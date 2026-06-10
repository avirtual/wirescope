"""Regression checks for the SQLite warmth store + TWO-STATE gates (2026-06-09).

Covers exactly the cases the live A/B can't cheaply reach:
  * warm declines compact-strip; lapsed ('cold') AND never-seen ('absent') strip
    — including the over-the-sweep-horizon case the old in-memory sweeper broke
  * receipt discipline (zero-usage responses never stamp)
  * the housekeeping purge never changes a gate decision (predicate-only expiry)
  * durability: warmth survives a simulated proxy restart (fresh connection)
  * pricing: longest-prefix match, fable/opus-4.5+ rates, unpriced accounting

Run: python3 test_warmth_store.py   (uses throwaway tmp dirs; no live ports)
"""
import json
import os
import sqlite3
import sys
import tempfile
import time

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="warmthtest_logs_")
os.environ["WARMTH_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="warmthtest_db_"), "warmth.sqlite")
os.environ["WARMTH_LEDGER"] = "1"
os.environ["STRIP_COMPACT_CACHE"] = "1"
os.environ.pop("STRIP_COMPACT_FORCE", None)

import logproxy as lp  # noqa: E402  (env must be set before import)

FAILS = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def msg(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


# >=2 anchors from _COMPACT_ANCHORS so _is_compact_request fires
COMPACT_PROMPT = (
    "Please create a detailed summary of the conversation so far. "
    "wrap your analysis in <analysis> tags, producing an <analysis> block "
    "followed by a <summary> block.")


def compact_obj(sid=None):
    o = {"model": "claude-fable-5",
         "system": [{"type": "text", "text": "You are Claude Code, a CLI."}],
         "messages": [
             msg("user", "please do the thing " * 50),
             msg("assistant", "did the thing " * 80),
             {"role": "user", "content": [{"type": "text", "text": COMPACT_PROMPT,
                                           "cache_control": {"type": "ephemeral"}}]},
         ]}
    if sid:
        o["metadata"] = {"user_id": json.dumps({"session_id": sid})}
    return o


# --- two-state basics --------------------------------------------------------
check("never-seen hash reads 'absent'", lp.warmth_state("ab" * 20) == "absent")

obj = compact_obj()
h_hist = lp._prefix_hashes(obj)[2]          # the history prefix a compact reuses
turn = {**obj, "messages": obj["messages"][:2]}

rec0 = lp._record_warmth(turn, {"cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0})
check("zero-usage response is NOT stamped (receipt discipline)", rec0 is None)
check("still absent after declined stamp", lp.warmth_state(h_hist) == "absent")

rec = lp._record_warmth(turn, {"cache_creation_input_tokens": 1234,
                               "cache_read_input_tokens": 0})
check("confirmed response stamps the history prefix",
      rec is not None and rec["hash"] == h_hist)
check("stamped prefix reads 'warm'", lp.warmth_state(h_hist) == "warm")

# --- compact gate ---------------------------------------------------------
st, hh, d = lp._compact_history_warmth(obj)
check("depth-scan finds the warm history prefix", (st, hh, d) == ("warm", h_hist, 2))
res = lp._strip_compact_cache(compact_obj())
check("WARM declines the strip", res is not None and res["condition_met"] is False)

# lapse the row in place -> 'cold' (row present, expired) must STRIP
con = lp._warmth_db()
with lp._DB_LOCK:
    con.execute("UPDATE warmth SET expires_at=? WHERE hash=?",
                (time.time() - 30, h_hist))
    con.commit()
check("lapsed row reads 'cold'", lp.warmth_state(h_hist) == "cold")
res = lp._strip_compact_cache(compact_obj())
check("COLD strips the discarded history marker",
      res is not None and res["condition_met"] is True
      and res["removed_message_markers"] == 1)

# the housekeeping purge removes the lapsed row -> 'absent' must STRIP THE SAME
# (this is the regression the old semantic sweeper failed: cold evidence reaped
# at bare ttl flipped the gate to decline)
with lp._DB_LOCK:
    con.execute("DELETE FROM warmth")
    con.commit()
check("purged row reads 'absent'", lp.warmth_state(h_hist) == "absent")
res = lp._strip_compact_cache(compact_obj())
check("ABSENT strips identically (purge never changes the decision)",
      res is not None and res["condition_met"] is True)

# ledger off -> 'off' must DECLINE (can't judge != evidence of bust)
_saved = lp.WARMTH_LEDGER
lp.WARMTH_LEDGER = False
res = lp._strip_compact_cache(compact_obj())
check("ledger OFF declines the strip",
      res is not None and res["condition_met"] is False
      and res["warmth_state"] == "off")
lp.WARMTH_LEDGER = _saved

# --- session head index + /_end + durability ----------------------------------
sobj = compact_obj(sid="sess-test-1")
turn_s = {**sobj, "messages": sobj["messages"][:2]}
lp._record_warmth(turn_s, {"cache_read_input_tokens": 999})
q = lp.warmth_query(session="sess-test-1")
check("warmth_query resolves session -> head -> warm",
      q["found"] and q["warm"] and q["remaining_s"] > 0)

# simulated proxy restart: a brand-new connection sees the same warmth
con2 = sqlite3.connect(os.environ["WARMTH_DB"])
n_rows = con2.execute("SELECT COUNT(*) FROM warmth").fetchone()[0]
n_heads = con2.execute("SELECT COUNT(*) FROM session_head").fetchone()[0]
con2.close()
check("warmth survives a restart (fresh connection sees rows)",
      n_rows >= 1 and n_heads == 1)

e = lp._end_session("sess-test-1", reason="clear")
check("/_end drops the session head", e["dropped"]["session_head"] is True)
check("query after /_end -> not found",
      lp.warmth_query(session="sess-test-1")["found"] is False)
check("the anonymous warmth row outlives /_end (fork-shared)",
      lp.warmth_state(lp._prefix_hashes(sobj)[2]) == "warm")

# --- pricing --------------------------------------------------------------
check("fable-5 priced (2x opus)",
      lp._price_for("claude-fable-5") == {"in": 10.0, "out": 50.0,
                                          "cache_write_5m": 12.5,
                                          "cache_write_1h": 20.0,
                                          "cache_read": 1.00})
check("longest prefix wins: opus-4-8 gets 4.5+ rates, not legacy",
      lp._price_for("claude-opus-4-8")["in"] == 5.0)
check("legacy opus-4-1 keeps $15 rates",
      lp._price_for("claude-opus-4-1-20250805")["in"] == 15.0)
check("unknown model -> None (not a silent default)",
      lp._price_for("claude-zonnet-9") is None)

bill = lp._billing("messages", model_resolved="claude-zonnet-9",
                   usage_final={"input_tokens": 10, "output_tokens": 5})
check("unknown model bill flagged unpriced",
      bill["unpriced"] is True and bill["est_usd"] is None)
tot = lp._new_totals()
lp._bump(tot, bill)
check("totals count unpriced traffic",
      tot["unpriced_requests"] == 1 and tot["unpriced_models"] == ["claude-zonnet-9"])

bill2 = lp._billing("messages", model_resolved="claude-fable-5",
                    usage_final={"input_tokens": 1_000_000, "output_tokens": 0,
                                 "cache_read_input_tokens": 0,
                                 "cache_creation_input_tokens": 100_000})
check("flat cache_creation (no ttl split) is priced, not dropped",
      bill2["est_usd"] == 10.0 + 1.25 and "flat total" in bill2["price_basis"])
tot2 = lp._new_totals()
lp._bump(tot2, bill2)
check("flat cache_creation lands in cache_write_tokens",
      tot2["cache_write_tokens"] == 100_000)

# --- refusal counter -----------------------------------------------------------
tot3 = lp._new_totals()
lp._bump(tot3, bill2, stop={"stop_reason": "refusal",
                            "stop_details": {"category": "reasoning_extraction"},
                            "request_id": "req_test123"})
lp._bump(tot3, bill2, stop={"stop_reason": "end_turn"})
check("refusal bumps the counter once (end_turn doesn't)",
      tot3["refusals"] == 1)
check("refusal evidence recorded (category + request_id)",
      tot3["refusal_events"][0]["category"] == "reasoning_extraction"
      and tot3["refusal_events"][0]["request_id"] == "req_test123")

# --- hold-warm: sentinel parse ---------------------------------------------------
def hold_obj(text):
    return {"messages": [msg("user", text)]}

check("sentinel parses hours", lp._hold_request(hold_obj(
    "<proxy:warm-cache hours=3>")) == ("arm", 3.0))
check("fractional hours", lp._hold_request(hold_obj(
    "<proxy:warm-cache hours=0.5>")) == ("arm", 0.5))
check("hours clamp to WARMTH_HOLD_MAX_HOURS", lp._hold_request(hold_obj(
    "<proxy:warm-cache hours=99>")) == ("arm", lp.WARMTH_HOLD_MAX_HOURS))
check("off disarms", lp._hold_request(hold_obj(
    "<proxy:warm-cache hours=off>")) == ("off", None))
check("zero disarms", lp._hold_request(hold_obj(
    "<proxy:warm-cache hours=0>")) == ("off", None))
check("no sentinel -> None (normal turn untouched)",
      lp._hold_request(hold_obj("please warm-cache my code")) is None)
check("sentinel survives surrounding command prose", lp._hold_request(hold_obj(
    "blah\n<proxy:warm-cache hours=2>\n(fallback line)")) == ("arm", 2.0))

# --- hold-warm: decision matrix (pure) -------------------------------------------
NOW = 1_000_000.0
HOLD = {"until": NOW + 3600, "pings": 0, "failures": 0}
warm_due = (NOW - 3400, 3600, NOW + 100)        # remaining 100s < margin 300
warm_high = (NOW - 100, 3600, NOW + 3500)       # remaining 3500s
cold_row = (NOW - 7200, 3600, NOW - 3600)

check("due warm prefix -> ping",
      lp._hold_decision(HOLD, True, warm_due, NOW)[0] == "ping")
check("warm but not yet due -> skip",
      lp._hold_decision(HOLD, True, warm_high, NOW)[0] == "skip")
check("cold prefix -> skip (NOT disarm: warmth can come back)",
      lp._hold_decision(HOLD, True, cold_row, NOW) == ("skip", "prefix already cold"))
check("no ledger row -> skip",
      lp._hold_decision(HOLD, True, None, NOW)[0] == "skip")
check("no replayable request -> skip",
      lp._hold_decision(HOLD, False, warm_due, NOW)[0] == "skip")
check("hold period over -> disarm",
      lp._hold_decision({**HOLD, "until": NOW - 1}, True, warm_due, NOW)[0] == "disarm")
check("max pings -> disarm",
      lp._hold_decision({**HOLD, "pings": lp.WARMTH_HOLD_MAX_PINGS},
                        True, warm_due, NOW)[0] == "disarm")
check("consecutive failures -> disarm",
      lp._hold_decision({**HOLD, "failures": 2}, True, warm_due, NOW)[0] == "disarm")

# --- hold-warm: arm/disarm bookkeeping -------------------------------------------
ack, rec = lp._arm_hold("sess-hold-1", "arm", 2.0)
check("arm registers hold state",
      rec["armed"] is True and "sess-hold-1" in lp._hold_snapshot())
check("arm ack warns when prefix is not warm",
      "not warm" in ack)
check("ack is attributed to the proxy (anti-ambush: a later model must not "
      "believe IT made the claim)", ack.startswith("[logproxy]"))
ack2, rec2 = lp._arm_hold("sess-hold-1", "off", None)
check("disarm pops hold state",
      rec2["disarmed"] is True and "sess-hold-1" not in lp._hold_snapshot())
check("disarm ack attributed too", ack2.startswith("[logproxy]"))
ack3, rec3 = lp._arm_hold(None, "arm", 2.0)
check("no session metadata -> not armed", rec3["armed"] is False)
lp._arm_hold("sess-hold-2", "arm", 1.0)
e2 = lp._end_session("sess-hold-2", reason="clear")
check("/_end also drops the hold", e2["dropped"]["hold"] is True)

# --- hold-warm: echo transform (arming turn forwards, model speaks the ack) ------
def echo_obj(text, sid="sess-echo-1"):
    o = {"model": "claude-fable-5", "messages": [msg("user", text)]}
    o["metadata"] = {"user_id": json.dumps({"session_id": sid})}
    return o

eo = echo_obj("/warm-cache expanded\n<proxy:warm-cache hours=2>\nIf this "
              "message contains a \"[logproxy]\" instruction block, follow it.")
he = lp._hold_echo_transform(eo)
last = eo["messages"][-1]["content"][0]["text"]
check("echo transform fires on the sentinel and arms the hold",
      he is not None and he["armed"] is True and he["forwarded"] is True
      and "sess-echo-1" in lp._hold_snapshot())
check("echo instruction injected into the final user message ([logproxy] block)",
      he["injected"] is True and "[logproxy]" in last
      and "<system-reminder>" in last)
check("instruction carries the exact ack text for the model to echo",
      he["ack"] in last and he["ack"].startswith("[logproxy]"))
check("original command text (sentinel + tripwire) is preserved, not replaced",
      "<proxy:warm-cache hours=2>" in last
      and last.index("<proxy:warm-cache") < last.index("<system-reminder>"))
eo_off = echo_obj("<proxy:warm-cache hours=off>")
he_off = lp._hold_echo_transform(eo_off)
check("disarm sentinel also forwards with an injected ack",
      he_off is not None and he_off.get("disarmed") is True
      and he_off["ack"] in eo_off["messages"][-1]["content"][0]["text"]
      and "sess-echo-1" not in lp._hold_snapshot())
eo_plain = echo_obj("please warm-cache my code")
before = json.dumps(eo_plain)
check("normal turn: no transform, message untouched",
      lp._hold_echo_transform(eo_plain) is None
      and json.dumps(eo_plain) == before)
check("cold-prefix arm ack reports self-establishment (forward semantics)",
      "re-establishes" in he["ack"] and "ping(s) expected" in he["ack"])

# --- session meta: upsert / COALESCE / durability --------------------------------
lp._upsert_session_meta("sess-meta-1", cwd="/tmp/projA", model="claude-fable-5")
lp._upsert_session_meta("sess-meta-1", title="Fix the frobnicator")
lp._upsert_session_meta("sess-meta-1", model="claude-fable-5")  # last_seen bump
con3 = sqlite3.connect(os.environ["WARMTH_DB"])
row = con3.execute("SELECT title, cwd, model, first_seen, last_seen "
                   "FROM session_meta WHERE session_id='sess-meta-1'").fetchone()
con3.close()
check("meta upserts merge (COALESCE keeps earlier fields) + survive restart",
      row == (row[0], "/tmp/projA", "claude-fable-5", row[3], row[4])
      and row[0] == "Fix the frobnicator" and row[4] >= row[3])

# --- turn accounting: request-derived context stats + receipt-counted turns ------
ts = lp._turn_stats({"messages": [
    msg("user", "first question"),
    msg("assistant", "answer"),
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "x" * 5000}]}]},
    msg("assistant", "after tool"),
    {"role": "user", "content": [
        {"type": "text", "text": "<command-message>foo</command-message>"}]},
    {"role": "user", "content": [
        {"type": "text", "text": "<system-reminder>bundle only</system-reminder>"}]},
    msg("user", "second real question"),
]})
check("turns_in_context counts only text-bearing prompts (tool_result-only, "
      "<command-*> and system-reminder-only excluded)",
      ts["turns_in_context"] == 2)
check("max_tool_result_chars finds the biggest single tool_result",
      ts["max_tool_result_chars"] == 5000)
check("n_messages is the raw message count", ts["n_messages"] == 7)
check("string-content user message counts as a turn",
      lp._turn_stats({"messages": [{"role": "user", "content": "hi"}]})
      ["turns_in_context"] == 1)
tot_t = lp._new_totals()
bill_t = {"endpoint": "messages", "tokens": {}, "est_usd": 0.0}
lp._bump(tot_t, bill_t, stop={"stop_reason": "tool_use", "is_turn": False})
lp._bump(tot_t, bill_t, stop={"stop_reason": "end_turn", "is_turn": True})
lp._bump(tot_t, bill_t, stop={"stop_reason": "refusal", "is_turn": True})
check("turns receipt-counts terminal responses only (tool_use hop doesn't; "
      "refusal still ends a turn)", tot_t["turns"] == 2)
lp._CONTEXT_STATS["sess-meta-1"] = {"turns_in_context": 5,
                                    "max_tool_result_chars": 123,
                                    "n_messages": 11, "ts": 0}
lp._bump(lp._SESSION_TOTALS["sess-meta-1"], bill_t,
         stop={"stop_reason": "end_turn", "is_turn": True})
st_turns = lp._status_snapshot(session="sess-meta-1")["sessions"][0]
check("/_status exposes turns_completed + context heaviness",
      st_turns["turns_completed"] == 1
      and st_turns["context"]["turns_in_context"] == 5)
check("/_end drops the context snapshot",
      (lp._end_session("sess-meta-1"),
       "sess-meta-1" not in lp._CONTEXT_STATS)[1])

# --- session meta: kind tag (proxy-spawned bootstrap sessions) -------------------
# The auth bootstrap pre-registers its spawn's session id with kind=bootstrap;
# later traffic upserts must not erase it, and the status/admin views keep it
# out of the human's default session list.
lp._upsert_session_meta("sess-boot-1", kind="bootstrap", cwd="/tmp",
                        model="claude-haiku-4-5-20251001")
lp._upsert_session_meta("sess-boot-1", title="Process user session request")
con4 = sqlite3.connect(os.environ["WARMTH_DB"])
krow = con4.execute("SELECT kind, title FROM session_meta "
                    "WHERE session_id='sess-boot-1'").fetchone()
con4.close()
check("kind survives traffic upserts (COALESCE)",
      krow == ("bootstrap", "Process user session request"))
st_all = lp._status_snapshot(all_sessions=True)
st_def = lp._status_snapshot()
in_all = [s for s in st_all["sessions"] if s["session_id"] == "sess-boot-1"]
check("bootstrap session visible under all=1 and carries kind",
      len(in_all) == 1 and in_all[0]["kind"] == "bootstrap")
check("bootstrap session HIDDEN from the default session list",
      not any(s["session_id"] == "sess-boot-1" for s in st_def["sessions"]))
check("direct ?session= still shows a bootstrap session",
      any(s["session_id"] == "sess-boot-1"
          for s in lp._status_snapshot(session="sess-boot-1")["sessions"]))
check("real sessions carry kind=None",
      all(s["kind"] is None for s in st_def["sessions"]))
check("admin marks proxy-spawned sessions (robot badge)",
      "&#129302; bootstrap" in lp._render_admin_html(st_all, host="t:7800"))

# --- session meta: cwd extraction + title-call detection -------------------------
check("cwd from system text", lp._extract_cwd(
    {"system": [{"type": "text",
                 "text": "# Environment\nPrimary working directory: /Users/x/proj\n"}],
     "messages": []}) == "/Users/x/proj")
check("cwd from msg0 bundle (headless)", lp._extract_cwd(
    {"system": "You are an agent.",
     "messages": [msg("user", "<system-reminder>\n# Environment\n"
                              "Primary working directory: /tmp/headless\n")]})
      == "/tmp/headless")
check("no env block -> None", lp._extract_cwd(
    {"system": "custom", "messages": [msg("user", "2+2")]}) is None)
check("title call detected", lp._is_title_call(
    {"tools": [], "system": [
        {"type": "text", "text": "x-anthropic-billing-header: cch=1;"},
        {"type": "text", "text": "Generate a concise, sentence-case title (3-7 words)…"}],
     "messages": []}) is True)
check("main-agent turn (has tools) is NOT a title call", lp._is_title_call(
    {"tools": [{"name": "Bash"}],
     "system": [{"type": "text", "text": "Generate a concise, sentence-case title"}],
     "messages": []}) is False)

# --- restart-amnesia (item h): holds survive a restart ----------------------------
import asyncio  # noqa: E402

lp._arm_hold("sess-persist-1", "arm", 2.0)
with lp._HOLD_LOCK:
    lp._HOLD_STATE.clear()                  # simulate the restart: memory wiped
n = lp._restore_holds()
check("armed hold survives a simulated restart",
      n == 1 and "sess-persist-1" in lp._hold_snapshot())
lp._persist_hold_row("sess-persist-old",
                     {"until": time.time() - 5, "armed_at": time.time() - 7200,
                      "pings": 3, "failures": 0})
with lp._HOLD_LOCK:
    lp._HOLD_STATE.clear()
lp._restore_holds()
check("expired hold is NOT restored (row reaped)",
      "sess-persist-old" not in lp._hold_snapshot()
      and "sess-persist-1" in lp._hold_snapshot())
lp._arm_hold("sess-persist-1", "off", None)
with lp._HOLD_LOCK:
    lp._HOLD_STATE.clear()
check("disarm also deletes the persisted row", lp._restore_holds() == 0)

# --- restart-amnesia: last_request bodies persist, auth does NOT ------------------
lobj = compact_obj(sid="sess-lr-1")
lturn = {**lobj, "messages": lobj["messages"][:2]}
lp._record_warmth(lturn, {"cache_creation_input_tokens": 50})   # warm -> restorable
lp._cache_last_request("sess-lr-1", lturn,
                       {"authorization": "Bearer SECRET", "x-api-key": "sk-SECRET",
                        "anthropic-beta": "beta-1", "content-type": "application/json"},
                       "/v1/messages", account_uuid="acct-1")
lp._WRITE_Q.join()                          # the mirror write rides the writer thread
con6 = sqlite3.connect(os.environ["WARMTH_DB"])
lrow = con6.execute("SELECT headers, account_uuid, body FROM last_request "
                    "WHERE session_id='sess-lr-1'").fetchone()
con6.close()
check("last_request row persisted with account_uuid",
      lrow is not None and lrow[1] == "acct-1")
check("persisted headers exclude every secret (standing rule)",
      "SECRET" not in lrow[0] and "authorization" not in lrow[0].lower()
      and "anthropic-beta" in lrow[0])
with lp._LAST_REQUEST_LOCK:
    lp._LAST_REQUEST.clear()
    lp._ACCOUNT_AUTH.clear()                # restart: auth registry gone too
check("restore loads the body auth-less",
      lp._restore_last_requests() == 1
      and lp._LAST_REQUEST["sess-lr-1"]["needs_auth"] is True
      and lp._LAST_REQUEST["sess-lr-1"]["headers"].get("anthropic-beta") == "beta-1")
code_na, res_na = asyncio.run(lp._warm_session("sess-lr-1"))
check("ping declines cleanly (not a failure) while auth is missing",
      code_na == 200 and res_na.get("skipped") == "no_auth")
check("hold decision skips an auth-less entry (no ping slot burned)",
      lp._hold_decision(HOLD, True, warm_due, NOW, has_auth=False)[0] == "skip")
with lp._LAST_REQUEST_LOCK:                 # the account's next live turn donates
    lp._ACCOUNT_AUTH["acct-1"] = {"authorization": "Bearer FRESH"}
ent = lp._resolve_auth("sess-lr-1")
check("live traffic re-donates account-level auth to the restored entry",
      ent["needs_auth"] is False
      and ent["headers"]["authorization"] == "Bearer FRESH"
      and ent["headers"].get("anthropic-beta") == "beta-1")
lp._end_session("sess-lr-1", reason="clear")
con7 = sqlite3.connect(os.environ["WARMTH_DB"])
left = con7.execute("SELECT COUNT(*) FROM last_request "
                    "WHERE session_id='sess-lr-1'").fetchone()[0]
con7.close()
check("/_end drops the persisted last_request row too", left == 0)

# stale rows (cold past the grace) are reaped at restore, not resurrected
lp._persist_last_request_row("sess-lr-stale", "acct-1", "/v1/messages",
                             time.time() - 7200,
                             {"model": "m", "messages": [msg("user", "hi")]}, {})
with lp._LAST_REQUEST_LOCK:
    lp._LAST_REQUEST.clear()
lp._restore_last_requests()
check("stale last_request row is reaped at restore",
      "sess-lr-stale" not in lp._LAST_REQUEST)

# --- restart-amnesia: totals reload + since_start delta ---------------------------
import pathlib  # noqa: E402

(pathlib.Path(os.environ["LOG_DIR"]) / "sess-tot-1").mkdir(exist_ok=True)
(pathlib.Path(os.environ["LOG_DIR"]) / "_totals.json").write_text(
    json.dumps({**lp._new_totals(), "requests": 7, "est_usd": 1.25}))
(pathlib.Path(os.environ["LOG_DIR"]) / "sess-tot-1" / "_session.json").write_text(
    json.dumps({**lp._new_totals(), "requests": 3}))
restored_t, nsess = lp._restore_totals()
check("totals reload from the on-disk snapshots",
      restored_t and lp._TOTALS["requests"] == 7
      and lp._TOTALS["est_usd"] == 1.25 and nsess == 1
      and lp._SESSION_TOTALS["sess-tot-1"]["requests"] == 3)
lp._bump(lp._TOTALS, bill2)
check("since_start tracks only post-restart deltas",
      lp._since_start()["requests"] == 1 and lp._TOTALS["requests"] == 8)

# --- restart-amnesia: cwd hunt resumes only where needed --------------------------
lp._META_CWD_DONE.clear()
lp._restore_cwd_done()
check("sessions with a stored cwd skip the hunt after restart",
      "sess-meta-1" in lp._META_CWD_DONE)

# --- hold display: expected pings for THIS hold (duration/ttl, cap-bounded) -------
lp._arm_hold("sess-exp-1", "arm", 2.0)
st_exp = lp._status_snapshot(session="sess-exp-1")
check("hold reports expected_pings = hours/ttl (2h @ 1h default -> 2)",
      st_exp["sessions"][0]["hold"]["expected_pings"] == 2)

# the hold is IDLE INSURANCE: an organic turn re-anchors the WHOLE window
# (until = turn + armed hours), not just the ping counter — "/warm-cache 2"
# means "keep me warm until 2h after my LAST interaction, whenever that is".
with lp._HOLD_LOCK:
    lp._HOLD_STATE["sess-exp-1"]["pings"] = 2
    lp._HOLD_STATE["sess-exp-1"]["failures"] = 1
    _h = dict(lp._HOLD_STATE["sess-exp-1"])
_turn_ts = _h["armed_at"] + 3600              # organic turn 1h into the 2h hold
with lp._LAST_REQUEST_LOCK:
    lp._LAST_REQUEST["sess-exp-1"] = {
        "obj": {"messages": []}, "headers": {}, "path": "/v1/messages",
        "ts": _turn_ts, "account": None, "needs_auth": False}
lp._hold_note_real_turn("sess-exp-1", now=_turn_ts)
st_exp2 = lp._status_snapshot(session="sess-exp-1")
_h2 = st_exp2["sessions"][0]["hold"]
check("organic turn resets pings/failures",
      _h2["pings"] == 0 and _h2["failures"] == 0)
check("organic turn SLIDES the window (until = last turn + armed hours)",
      abs(_h2["until"] - (_turn_ts + 2 * 3600)) < 1)
check("expected_pings is the full window again after the slide (2h @ 1h -> 2)",
      _h2["expected_pings"] == 2)
with lp._HOLD_LOCK:           # the slide+reset was mirrored: a restart reloads it
    lp._HOLD_STATE.clear()
lp._restore_holds()
_h3 = lp._hold_snapshot()["sess-exp-1"]
check("slid window + counter reset + hours survive a restart",
      _h3["pings"] == 0 and abs(_h3["until"] - (_turn_ts + 2 * 3600)) < 1
      and _h3["hours"] == 2.0)
# legacy row (pre-`hours` column): duration derives from until - armed_at
lp._persist_hold_row("sess-legacy-1",
                     {"until": time.time() + 5400, "armed_at": time.time() - 1800,
                      "pings": 0, "failures": 0})
with lp._HOLD_LOCK:
    lp._HOLD_STATE.clear()
lp._restore_holds()
check("legacy hold row derives hours from its original span (2h)",
      abs(lp._hold_snapshot()["sess-legacy-1"]["hours"] - 2.0) < 0.01)
lp._arm_hold("sess-legacy-1", "off", None)
with lp._LAST_REQUEST_LOCK:
    lp._LAST_REQUEST.pop("sess-exp-1", None)
lp._arm_hold("sess-exp-1", "off", None)

# --- auth self-bootstrap: bounded spend decision -----------------------------------
BST = {"attempts": 0, "last_ts": 0.0, "inflight": False}
check("bootstrap fires for an unknown account",
      lp._bootstrap_decision("acct-z", now=NOW, state=dict(BST))[0] is True)
check("bootstrap declines while one is in flight",
      lp._bootstrap_decision("acct-z", now=NOW,
                             state={**BST, "inflight": True})[0] is False)
check("bootstrap declines past max attempts",
      lp._bootstrap_decision("acct-z", now=NOW,
                             state={**BST, "attempts": lp._AUTH_BOOTSTRAP_MAX})[0] is False)
check("bootstrap respects the cooldown",
      lp._bootstrap_decision("acct-z", now=NOW,
                             state={**BST, "attempts": 1, "last_ts": NOW - 5})[0] is False)
with lp._LAST_REQUEST_LOCK:
    lp._ACCOUNT_AUTH["acct-have"] = {"authorization": "Bearer x"}
check("bootstrap declines when the account's auth is already present",
      lp._bootstrap_decision("acct-have", now=NOW, state=dict(BST))
      == (False, "auth already present (resolve instead)"))
_sb = lp.WARMTH_AUTH_BOOTSTRAP
lp.WARMTH_AUTH_BOOTSTRAP = False
check("bootstrap respects the kill switch",
      lp._bootstrap_decision("acct-z", now=NOW, state=dict(BST))[0] is False)
lp.WARMTH_AUTH_BOOTSTRAP = _sb

# --- stale-auth (ping 401) self-heal -----------------------------------------------
# A 401 replay invalidates the dead bearer everywhere: account registry dropped,
# entry back to needs_auth -> the no-auth skip + bootstrap path takes over.
with lp._LAST_REQUEST_LOCK:
    lp._ACCOUNT_AUTH["acct-stale"] = {"authorization": "Bearer EXPIRED"}
    lp._LAST_REQUEST["sess-stale-1"] = {
        "obj": dict(lturn), "headers": {"authorization": "Bearer EXPIRED"},
        "path": "/v1/messages", "ts": NOW, "account": "acct-stale",
        "needs_auth": False}
lp._invalidate_stale_auth("sess-stale-1", "acct-stale")
check("401 invalidation drops the account's stale auth",
      "acct-stale" not in lp._ACCOUNT_AUTH)
check("401 invalidation flips the entry back to needs_auth",
      lp._LAST_REQUEST["sess-stale-1"]["needs_auth"] is True)
check("resolve declines (no re-attach) after stale invalidation",
      lp._resolve_auth("sess-stale-1").get("needs_auth") is True)
check("bootstrap may fire again for the invalidated account",
      lp._bootstrap_decision("acct-stale", now=NOW, state=dict(BST))[0] is True)
# a fresh donation (live turn or bootstrap's donor) resets the spend budget:
# bounded per auth OUTAGE, not per process lifetime
lp._AUTH_BOOTSTRAP["attempts"] = lp._AUTH_BOOTSTRAP_MAX
lp._cache_last_request("sess-stale-1", dict(lturn),
                       {"authorization": "Bearer FRESH2",
                        "content-type": "application/json"},
                       "/v1/messages", account_uuid="acct-stale")
check("fresh auth donation resets the bootstrap attempt budget",
      lp._AUTH_BOOTSTRAP["attempts"] == 0
      and lp._ACCOUNT_AUTH["acct-stale"]["authorization"] == "Bearer FRESH2")
check("donated entry is pingable again (needs_auth cleared)",
      lp._LAST_REQUEST["sess-stale-1"]["needs_auth"] is False)
lp._WRITE_Q.join()
lp._end_session("sess-stale-1")

# --- /_admin HTML page -------------------------------------------------------------
page = lp._render_admin_html(lp._status_snapshot(all_sessions=True), host="t:7800")
check("admin page renders the session inventory",
      page.startswith("<!doctype html") and "Fix the frobnicator" in page
      and "/_status" in page)
lp._upsert_session_meta("sess-xss", title='<script>alert("x")</script>')
page2 = lp._render_admin_html(lp._status_snapshot(session="sess-xss"))
check("admin page escapes model-authored titles",
      "<script>alert" not in page2 and "&lt;script&gt;" in page2)

# --- /_status shape ---------------------------------------------------------------
st = lp._status_snapshot(session="sess-meta-1")
check("/_status lists the session with its meta",
      len(st["sessions"]) == 1
      and st["sessions"][0]["title"] == "Fix the frobnicator"
      and st["sessions"][0]["cwd"] == "/tmp/projA"
      and st["sessions"][0]["warmth"]["state"] in ("warm", "cold", "absent"))
check("/_status proxy block carries flags + totals",
      st["proxy"]["flags"]["ledger"] in (True, False)
      and "refusals" in st["proxy"]["totals"])

# --- /_session context view --------------------------------------------------------
_sv_entry = {
    "obj": {"model": "claude-fable-5",
            "tools": [{"name": "Bash", "description": "Run a command",
                       "input_schema": {"type": "object"}}],
            "system": [{"type": "text", "text": "# Harness\nrules here",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            "messages": [
                {"role": "user", "content": [{"type": "text",
                 "text": "<system-reminder>ctx</system-reminder>hello <b>world"}]},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                 "name": "Bash", "input": {"command": "ls"}}]},
                {"role": "user", "content": [{"type": "tool_result",
                 "tool_use_id": "t1", "content": "ok", "is_error": False}]}]},
    "path": "/v1/messages", "ts": time.time(), "needs_auth": False}
_sv = lp._render_session_html("sess-meta-1", _sv_entry,
                              lp._status_snapshot(session="sess-meta-1"))
check("session view renders tools/system/messages segments",
      "Bash" in _sv and "# Harness" in _sv and "tool_result" in _sv
      and "cache 1h" in _sv and "[system-reminder]" in _sv)
check("session view escapes message content",
      "<b>world" not in _sv and "&lt;b&gt;world" in _sv)
check("session view never renders headers",
      "authorization" not in _sv.lower() and "x-api-key" not in _sv.lower())
check("session view handles a missing entry",
      "no replayable request" in lp._render_session_html(
          "nope", None, lp._status_snapshot(session="nope")))
_tv_entry = {
    "obj": {"model": "claude-fable-5", "tools": [], "system": "sys",
            "messages": [
                msg("user", "first question"),
                msg("assistant", "answer one"),
                {"role": "user", "content": [{"type": "tool_result",
                 "tool_use_id": "t1", "content": "hop"}]},
                msg("assistant", "after the hop"),
                msg("user", "second question"),
                msg("assistant", "answer two"),
            ]},
    "path": "/v1/messages", "ts": time.time(), "needs_auth": False}
_tv = lp._render_session_html("sess-meta-1", _tv_entry,
                              lp._status_snapshot(session="sess-meta-1"))
check("session view groups the timeline by turn (divider per prompt msg, "
      "tool hop doesn't start one)",
      _tv.count('class="turnhdr"') == 2
      and 'id="turn-1"' in _tv and 'id="turn-2"' in _tv)
check("last turn is marked current and the bar links to it",
      'turn 2 · <span class="warm">current</span>' in _tv
      and 'href="#turn-2"' in _tv
      and _tv.count("current</span>") == 1)
_tv_resp = lp._render_session_html(
    "sess-meta-1", _tv_entry, lp._status_snapshot(session="sess-meta-1"),
    resp={"text": "final answer <i>here", "stop_reason": "end_turn",
          "truncated": False, "ts": _tv_entry["ts"] + 1})
check("session view appends the last ANSWER from the response "
      "(the reply a request-only view always missed)",
      "answer · turn 2" in _tv_resp and "assistant (response)" in _tv_resp
      and "&lt;i&gt;here" in _tv_resp and "final answer <i>" not in _tv_resp)
check("a response OLDER than the captured request is not shown (stale)",
      "assistant (response)" not in lp._render_session_html(
          "sess-meta-1", _tv_entry, lp._status_snapshot(session="sess-meta-1"),
          resp={"text": "old", "ts": _tv_entry["ts"] - 99}))
check("admin page links each session to its context view",
      "/_session?session=" in lp._render_admin_html(
          lp._status_snapshot(all_sessions=True)))

# --- WB intent tee (workbench integration: WB_INTENT_DISPATCH) ----------------
# The tee is tested with an injected parse stub (boundary discipline is OURS;
# intent grammar is the workbench's) and a collector in place of the async
# dispatcher. One conditional check loads the REAL parse_intents when the
# workbench checkout is present.

def _sse_text_delta(t, crlf=False):
    sep = "\r\n" if crlf else "\n"
    data = json.dumps({"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": t}})
    return (f"event: content_block_delta{sep}data: {data}{sep}{sep}").encode()


def _fake_parse(text):
    # Stand-in for intent_parser.parse_intents: every "[wb:" occurrence is one
    # accepted intent, in order. Grammar fidelity is irrelevant to the tee.
    return [("post", f"t{i}", f"b{i}") for i in range(text.count("[wb:"))]


_sent = []
_tee = lp._WbIntentTee("agent-x", "7-120000",
                       parse_fn=_fake_parse, dispatch_fn=_sent.append)
_tee.feed(_sse_text_delta("hello [wb:post note] first body"))
check("wb tee holds the tail intent while its body may still grow",
      _sent == [] and _tee.dispatched == 0)
_tee.feed(_sse_text_delta(" more body\n[wb:dm bob] second"))
check("wb tee dispatches intent N once intent N+1 locks its boundary",
      len(_sent) == 1 and _sent[0]["action"] == "post"
      and _sent[0]["target"] == "t0" and _sent[0]["body"] == "b0")
_tee.close()
check("wb tee close() flushes the held tail intent",
      len(_sent) == 2 and _sent[1]["target"] == "t1")
check("wb intent payloads carry agent identity + proxy-scoped intent_id + ts",
      _sent[0]["agent"] == "agent-x"
      and _sent[0]["intent_id"].startswith("proxy-7-120000-0000-")
      and "ts" in _sent[0])
_tee.feed(_sse_text_delta("[wb:post late] after close"))
_tee.close()
check("wb tee ignores feeds after close; double close is idempotent",
      len(_sent) == 2)

_sent2 = []
_tee2 = lp._WbIntentTee("a", "1-1", parse_fn=_fake_parse,
                        dispatch_fn=_sent2.append)
_blob = _sse_text_delta("[wb:post x] split across") \
    + _sse_text_delta(" chunks [wb:post y] tail")
for i in range(0, len(_blob), 7):     # mid-event, mid-JSON chunk boundaries
    _tee2.feed(_blob[i:i + 7])
_tee2.close()
check("wb tee reassembles SSE events split across arbitrary chunk boundaries",
      len(_sent2) == 2)

_sent3 = []
_tee3 = lp._WbIntentTee("a", "1-2", parse_fn=_fake_parse,
                        dispatch_fn=_sent3.append)
_tee3.feed(_sse_text_delta("[wb:post crlf] body", crlf=True))
_tee3.close()
check("wb tee handles CRLF-framed SSE", len(_sent3) == 1)

_sent4 = []
_tee4 = lp._WbIntentTee("a", "1-3", parse_fn=_fake_parse,
                        dispatch_fn=_sent4.append)
_tee4.feed(b'event: content_block_delta\ndata: {"type": "content_block_delta",'
           b' "delta": {"type": "input_json_delta", "partial_json": "[wb:"}}\n\n')
_tee4.feed(b'event: message_delta\ndata: {"type": "message_delta",'
           b' "usage": {"output_tokens": 5}}\n\n')
_tee4.close()
check("wb tee ignores non-text deltas (tool_use args never parse as intents)",
      _sent4 == [])

check("wb parser loader returns None on a missing file (proxy still comes up)",
      lp._load_intent_parser("/nonexistent/intent_parser.py") is None)
_real_pi_path = os.path.expanduser("~/projects/agent-workbench/intent_parser.py")
if os.path.exists(_real_pi_path):
    _real_pi = lp._load_intent_parser(_real_pi_path)
    check("wb parser loader imports the real workbench parse_intents",
          callable(_real_pi) and isinstance(_real_pi("plain prose"), list))
else:
    print("SKIP  wb real-parser load (workbench checkout not present)")

check("wb intent dispatch flag is surfaced in /_status",
      "wb_intent_dispatch" in lp._status_snapshot()["proxy"]["flags"]
      and "wb_intents" in lp._status_snapshot()["proxy"])

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("ALL PASS")
