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
con = lp.store.db()
with lp.store.LOCK:
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
with lp.store.LOCK:
    con.execute("DELETE FROM warmth")
    con.commit()
check("purged row reads 'absent'", lp.warmth_state(h_hist) == "absent")
res = lp._strip_compact_cache(compact_obj())
check("ABSENT strips identically (purge never changes the decision)",
      res is not None and res["condition_met"] is True)

# ledger off -> 'off' must DECLINE (can't judge != evidence of bust)
_saved = lp.WARMTH_LEDGER
lp.warmth.WARMTH_LEDGER = False
res = lp._strip_compact_cache(compact_obj())
check("ledger OFF declines the strip",
      res is not None and res["condition_met"] is False
      and res["warmth_state"] == "off")
lp.warmth.WARMTH_LEDGER = _saved

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

# --- cold-resume counter -------------------------------------------------------
# DISTINCT content so this session's content-addressed warmth rows don't collide
# with any sibling's (the /_end fork-shared check above relies on its row staying
# warm). Each entry advances this session's head to a distinct prefix.
cr = {"model": "claude-fable-5",
      "system": [{"type": "text", "text": "You are Claude Code, a CLI."}],
      "messages": [msg("user", "cold-resume probe alpha " * 20),
                   msg("assistant", "cold-resume reply " * 20),
                   msg("user", "cold-resume probe gamma " * 20)],
      "metadata": {"user_id": json.dumps({"session_id": "sess-cr-1"})}}
cr_t1 = {**cr, "messages": cr["messages"][:1]}
cr_t2 = {**cr, "messages": cr["messages"][:2]}
cr_t3 = {**cr, "messages": cr["messages"][:3]}

# Turn 1: a session's FIRST turn is an initial cold start, NOT a resume.
r1 = lp._record_warmth(cr_t1, {"cache_creation_input_tokens": 100})
check("first turn is not counted as a resume",
      r1["cold_resume"] is False and r1["cold_resumes"] == 0
      and lp.cold_resumes("sess-cr-1") == 0)

# Turn 2: prior head still warm (active session) -> no resume.
r2 = lp._record_warmth(cr_t2, {"cache_read_input_tokens": 100})
check("a turn on a still-warm prior head is not a resume",
      r2["cold_resume"] is False and lp.cold_resumes("sess-cr-1") == 0)

# Lapse this session's CURRENT head, then take a turn -> prior head is cold ->
# resume #1. (Scope the lapse to this head hash so sibling sessions' rows, which
# later checks rely on, stay warm. Resolve the hash BEFORE taking the lock —
# _session_head_hash acquires it and store.LOCK is non-reentrant.)
_cr_head = lp.warmth._session_head_hash("sess-cr-1")
with lp.store.LOCK:
    con.execute("UPDATE warmth SET expires_at=? WHERE hash=?",
                (time.time() - 30, _cr_head))
    con.commit()
r3 = lp._record_warmth(cr_t3, {"cache_creation_input_tokens": 200})
check("a turn on a lapsed prior head counts as a cold resume",
      r3["cold_resume"] is True and r3["cold_resumes"] == 1
      and lp.cold_resumes("sess-cr-1") == 1)

# Lapse again with a fresh, longer prefix -> resume #2 (counter accumulates).
cr_t4 = {**cr, "messages": cr["messages"] + [msg("user", "more " * 10)]}
_cr_head = lp.warmth._session_head_hash("sess-cr-1")
with lp.store.LOCK:
    con.execute("UPDATE warmth SET expires_at=? WHERE hash=?",
                (time.time() - 30, _cr_head))
    con.commit()
r4 = lp._record_warmth(cr_t4, {"cache_creation_input_tokens": 50})
check("the cold-resume counter accumulates across lapses",
      r4["cold_resumes"] == 2 and lp.cold_resumes("sess-cr-1") == 2)

check("cold_resumes() is 0 for an unknown session (no fiction)",
      lp.cold_resumes("sess-cr-ghost") == 0)

# /_status carries the per-session counter
lp._upsert_session_meta("sess-cr-1", cwd="/tmp/cr", model="claude-fable-5")
_cr_snap = [s for s in lp._status_snapshot(session="sess-cr-1")["sessions"]
            if s["session_id"] == "sess-cr-1"]
check("status snapshot exposes cold_resumes",
      len(_cr_snap) == 1 and _cr_snap[0]["cold_resumes"] == 2)

# /_end = a MARKER since 2026-06-11, not a delete: one-shot `claude -p` runs
# fire SessionEnd the instant their answer lands; the debug state must survive.
e = lp._end_session("sess-test-1", reason="clear")
check("/_end marks the session ended (resumable fact, not a delete)",
      e["ended"] is True and lp._ENDED["sess-test-1"]["reason"] == "clear")
check("warmth query still resolves after /_end (head retained)",
      lp.warmth_query(session="sess-test-1")["found"] is True)
check("the anonymous warmth row outlives /_end (fork-shared)",
      lp.warmth_state(lp._prefix_hashes(sobj)[2]) == "warm")
check("/_end on a session the proxy never saw invents nothing",
      lp._end_session("sess-ghost-1")["ended"] is False
      and "sess-ghost-1" not in lp._ENDED)
# a live turn on an ended session = resume -> the marker clears
lp._clear_session_ended("sess-test-1")
check("live turn clears the ended marker", "sess-test-1" not in lp._ENDED)

# --- leading-breakpoint segments (display-grade; 2026-06-12) -------------------
# Both markers live in system[] (the wire never marks tools[]): marker 1 caches
# tools + the "You are Claude" preamble, marker 2 adds the full system prompt.
# Content-addressed so sessions with byte-identical layouts share rows; a
# sibling's traffic keeps them warm after this session's own message tail
# lapses. Eye candy — no gate reads these.

def seg_obj(sid, question, ttl="1h", tool_desc="edit a file",
            with_billing_block=False):
    cc = ({"type": "ephemeral", "ttl": "1h"} if ttl == "1h"
          else {"type": "ephemeral"})
    # real shape: marker 1 on the preamble block, marker 2 on the system prompt
    sysb = [{"type": "text", "text": "You are Claude Code, a CLI.",
             "cache_control": dict(cc)},
            {"type": "text", "text": "# Environment\ncwd: /tmp",
             "cache_control": dict(cc)}]
    if with_billing_block:
        sysb.insert(0, {"type": "text",
                        "text": "x-anthropic-billing-header: cch=42"})
    return {"model": "claude-fable-5",
            "metadata": {"user_id": json.dumps({"session_id": sid})},
            "tools": [{"name": "Read", "description": "read a file",
                       "input_schema": {"type": "object"}},
                      {"name": "Edit", "description": tool_desc,
                       "input_schema": {"type": "object"}}],
            "system": sysb,
            "messages": [msg("user", question),
                         {"role": "user", "content": [
                             {"type": "text", "text": "go",
                              "cache_control": dict(cc)}]}]}

sa, sb = seg_obj("seg-A", "build the thing " * 60), seg_obj("seg-B", "fix the bug " * 60)
ga, gb = lp._segment_hashes(sa), lp._segment_hashes(sb)
check("identical layouts -> identical segment hashes (the sharing claim)",
      ga["tools"]["hash"] == gb["tools"]["hash"]
      and ga["system"]["hash"] == gb["system"]["hash"])
check("…while their message-tail prefixes differ",
      lp._prefix_hash(sa, 2) != lp._prefix_hash(sb, 2))
check("segment ttl read from its own marker",
      ga["tools"]["ttl"] == 3600
      and lp._segment_hashes(seg_obj("x", "q", ttl="5m"))["tools"]["ttl"] == 300)
check("marker ttl is an attribute, not identity (5m and 1h hash the same)",
      lp._segment_hashes(seg_obj("x", "q", ttl="5m"))["tools"]["hash"]
      == ga["tools"]["hash"])
check("a changed tool description changes BOTH segment hashes (byte-exact)",
      lp._segment_hashes(seg_obj("x", "q", tool_desc="edit a FILE"))["tools"]["hash"]
      != ga["tools"]["hash"]
      and lp._segment_hashes(seg_obj("x", "q", tool_desc="edit a FILE"))["system"]["hash"]
      != ga["system"]["hash"])
check("billing-header block excluded from the system segment (out-of-band)",
      lp._segment_hashes(seg_obj("x", "q", with_billing_block=True))["system"]["hash"]
      == ga["system"]["hash"])
check("tools segment (tools+preamble) differs from system segment (+system prompt)",
      ga["tools"]["hash"] != ga["system"]["hash"])
check("single system marker -> only the 'system' segment (can't separate the two)",
      "tools" not in lp._segment_hashes(
          {"model": "m", "tools": [],
           "system": [{"type": "text", "text": "all in one",
                       "cache_control": {"type": "ephemeral"}}]}))
check("no markers -> no segments", lp._segment_hashes(compact_obj()) == {})

lp._record_warmth(sa, {"cache_creation_input_tokens": 5000})
wsa = lp.warmth_segments("seg-A")
check("a confirmed turn stamps the segment rows warm",
      wsa is not None and wsa["tools"]["state"] == "warm"
      and wsa["system"]["state"] == "warm"
      and wsa["tools"]["hash"] == ga["tools"]["hash"])

# the eye-candy scenario itself: lapse EVERYTHING seg-A stamped, then let the
# sibling seg-B (same layout, different conversation) take a turn — seg-A's
# segments must read warm again off the shared rows while its own head is cold
con = lp.store.db()
with lp.store.LOCK:
    con.execute("UPDATE warmth SET expires_at=?", (time.time() - 30,))
    con.commit()
lp._record_warmth(sb, {"cache_read_input_tokens": 4000})
wsa = lp.warmth_segments("seg-A")
check("sibling traffic re-warms the shared segments…",
      wsa["tools"]["state"] == "warm" and wsa["system"]["state"] == "warm")
check("…while seg-A's own message tail stays cold (the display this buys)",
      lp.warmth_query(session="seg-A")["warm"] is False)
check("unknown session -> None (no row, no fiction)",
      lp.warmth_segments("seg-ghost") is None)

# /_status carries the readout
lp._upsert_session_meta("seg-A", cwd="/tmp/seg", model="claude-fable-5")
snap_s = [s for s in lp._status_snapshot(session="seg-A")["sessions"]
          if s["session_id"] == "seg-A"]
check("status snapshot exposes warmth.segments",
      snap_s and (snap_s[0]["warmth"]["segments"] or {}).get("tools", {})
      .get("state") == "warm")

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

# openai side (codex routes): API-equivalent estimate, longest prefix again
check("gpt-5.4-mini wins over the bare gpt-5.4 prefix",
      lp._price_for("gpt-5.4-mini", table=lp.PRICES_OPENAI)["in"] == 0.75)
ob = lp._billing_openai("gpt-5.4", {
    "input_tokens": 23502,
    "input_tokens_details": {"cached_tokens": 21376},
    "output_tokens": 114,
    "output_tokens_details": {"reasoning_tokens": 108}})
check("openai bill splits cached out of input (anthropic totals semantics)",
      ob["tokens"]["input_tokens"] == 2126
      and ob["tokens"]["cache_read_input_tokens"] == 21376
      and ob["tokens"]["thinking_tokens"] == 108)
check("openai est_usd: uncached*in + cached*cached_in + out*out",
      ob["est_usd"] == round(2126*2.5e-6 + 21376*0.25e-6 + 114*15e-6, 6))
check("unknown gpt model flagged unpriced (loud floor, not silent None)",
      lp._billing_openai("gpt-9-turbo", {"input_tokens": 1})["unpriced"] is True)
tot_o = lp._new_totals()
lp._bump(tot_o, ob, stop={"stop_reason": "completed", "is_turn": True})
check("openai bill feeds the shared totals (cache_read = cached tokens)",
      tot_o["cache_read_tokens"] == 21376 and tot_o["est_usd"] == ob["est_usd"]
      and tot_o["turns"] == 1)

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
check("full stop_details + model + epoch kept (the wire truth the CLI eats)",
      tot3["refusal_events"][0]["stop_details"] == {"category": "reasoning_extraction"}
      and tot3["refusal_events"][0]["model"] == "claude-fable-5"
      and tot3["refusal_events"][0]["at"] > 0)

# --- refusal surfacing: /_status + /_admin link + /_session banner ------------
lp._upsert_session_meta("sess-ref-1", cwd="/tmp/ref", model="claude-fable-5")
lp._bump(lp._SESSION_TOTALS["sess-ref-1"], bill2,
         stop={"stop_reason": "refusal",
               "stop_details": {"category": "reasoning_extraction",
                                "explanation": "ToS: duplicating model outputs"},
               "request_id": "req_ref456"})
st_ref = lp._status_snapshot(session="sess-ref-1")["sessions"][0]
check("status exposes refusal_events per session",
      st_ref["refusals"] == 1
      and st_ref["refusal_events"][0]["request_id"] == "req_ref456")
check("admin ref count links to the session refusal banner",
      "/_session?session=sess-ref-1#refusals"
      in lp._render_admin_html(lp._status_snapshot(session="sess-ref-1")))
_ref_entry = {"obj": compact_obj(sid="sess-ref-1"), "ts": time.time() - 60,
              "path": "/v1/messages"}
_rv = lp._render_session_html("sess-ref-1", _ref_entry,
                              lp._status_snapshot(session="sess-ref-1"))
check("session page renders the refusal banner (category + explanation)",
      'id="refusals"' in _rv and "reasoning_extraction" in _rv
      and "duplicating model outputs" in _rv)
check("refusal newer than the capture -> flags it as the blocked context",
      "IS the" in _rv)
_rv2 = lp._render_session_html(
    "sess-ref-1", {**_ref_entry, "ts": time.time() + 60},
    lp._status_snapshot(session="sess-ref-1"))
check("a fresher capture -> banner says the context post-dates the refusal",
      "post-dates the refusal" in _rv2 and "IS the" not in _rv2)
check("no refusals -> no banner",
      'id="refusals"' not in lp._render_session_html(
          "sess-meta-none", None, lp._status_snapshot(session="sess-meta-none")))
del lp._SESSION_TOTALS["sess-ref-1"]   # don't leak into later totals checks

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
      "believe IT made the claim)", ack.startswith("[wirescope]"))
ack2, rec2 = lp._arm_hold("sess-hold-1", "off", None)
check("disarm pops hold state",
      rec2["disarmed"] is True and "sess-hold-1" not in lp._hold_snapshot())
check("disarm ack attributed too", ack2.startswith("[wirescope]"))
ack3, rec3 = lp._arm_hold(None, "arm", 2.0)
check("no session metadata -> not armed", rec3["armed"] is False)
lp._arm_hold("sess-hold-2", "arm", 1.0)
e2 = lp._end_session("sess-hold-2", reason="clear")
check("/_end still disarms the hold immediately (no spend on ended sessions)",
      e2["hold_disarmed"] is True and "sess-hold-2" not in lp._hold_snapshot())

# --- hold-warm: echo transform (arming turn forwards, model speaks the ack) ------
def echo_obj(text, sid="sess-echo-1"):
    o = {"model": "claude-fable-5", "messages": [msg("user", text)]}
    o["metadata"] = {"user_id": json.dumps({"session_id": sid})}
    return o

eo = echo_obj("/warm-cache expanded\n<proxy:warm-cache hours=2>\nIf this "
              "message contains a \"[wirescope]\" instruction block, follow it.")
he = lp._hold_echo_transform(eo)
last = eo["messages"][-1]["content"][0]["text"]
check("echo transform fires on the sentinel and arms the hold",
      he is not None and he["armed"] is True and he["forwarded"] is True
      and "sess-echo-1" in lp._hold_snapshot())
check("echo instruction injected into the final user message ([wirescope] block)",
      he["injected"] is True and "[wirescope]" in last
      and "<system-reminder>" in last)
check("instruction carries the exact ack text for the model to echo",
      he["ack"] in last and he["ack"].startswith("[wirescope]"))
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
end_meta = lp._end_session("sess-meta-1", reason="other")
check("/_end RETAINS the context snapshot (one-shot debug state)",
      end_meta["retained"]["context"] is True
      and "sess-meta-1" in lp._CONTEXT_STATS)
st_ended = lp._status_snapshot(session="sess-meta-1")["sessions"][0]
check("/_status exposes ended + keeps turn info after /_end",
      st_ended["ended"] is not None
      and st_ended["ended"]["reason"] == "other"
      and st_ended["turns_completed"] == 1
      and st_ended["context"]["turns_in_context"] == 5)
check("ended marker is durable (survives into a fresh load)",
      (lp._ENDED.pop("sess-meta-1", None), lp._restore_ended(),
       lp._ENDED.get("sess-meta-1", {}).get("reason") == "other")[2])
check("admin page renders the ended badge",
      "ended" in lp._render_admin_html(
          lp._status_snapshot(session="sess-meta-1")))
check("proxy self-reports its code version in /_status",
      bool(lp._status_snapshot()["proxy"].get("version"))
      and lp._status_snapshot()["proxy"]["version"] != "unknown")
# resume: the per-turn meta hook clears the mark (memory + durable column)
lp._capture_session_meta("sess-meta-1", {"system": [], "messages": []}, "m")
check("a live turn through the meta hook clears ended everywhere",
      "sess-meta-1" not in lp._ENDED
      and (lp._restore_ended(), "sess-meta-1" not in lp._ENDED)[1])

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

# --- session meta: agent route name names the session ----------------------------
# The /agent/<name>/ route identity is the operator's own label — it WINS over
# a learned summary title (and SDK-driven sessions never make the title
# side-call anyway). The raw learned title stays available as `summary`.
lp._capture_session_meta("sess-agent-1", {"system": [], "messages": []},
                         "claude-sonnet-4-6", agent="executor-1")
lp._WRITE_Q.join()
st_ag = lp._status_snapshot(session="sess-agent-1")["sessions"][0]
check("agent-routed session is titled [agent]",
      st_ag["agent"] == "executor-1" and st_ag["title"] == "[executor-1]")
lp._upsert_session_meta("sess-agent-1", title="Run the test matrix")
st_ag2 = lp._status_snapshot(session="sess-agent-1")["sessions"][0]
check("agent name beats the learned title; summary keeps the raw title",
      st_ag2["title"] == "[executor-1]"
      and st_ag2["summary"] == "Run the test matrix"
      and st_ag2["agent"] == "executor-1")
lp._capture_session_meta("sess-agent-1", {"system": [], "messages": []},
                         "claude-sonnet-4-6")   # plain turn: agent=None
lp._WRITE_Q.join()
check("agent survives later agent-less upserts (COALESCE)",
      lp._status_snapshot(session="sess-agent-1")["sessions"][0]["agent"]
      == "executor-1")
check("plain (ext) sessions carry agent=None (learned title still shows)",
      st_ended["agent"] is None)
# Un-routed AND no learned title (e.g. a headless run straight at the port):
# title falls back to ~<session_id first segment> so it stays identifiable.
lp._capture_session_meta("3f8a1c2d-dead-beef-0000-000000000000",
                         {"system": [], "messages": []}, "claude-sonnet-4-6")
lp._WRITE_Q.join()
st_anon = lp._status_snapshot(
    session="3f8a1c2d-dead-beef-0000-000000000000")["sessions"][0]
check("un-routed, title-less session falls back to ~<uuid-prefix>",
      st_anon["agent"] is None and st_anon["title"] == "~3f8a1c2d"
      and st_anon["summary"] is None)
page_ag = lp._render_admin_html(lp._status_snapshot(session="sess-agent-1"))
check("admin renders [agent] as the name AND the learned summary beside it",
      "[executor-1]" in page_ag and "Run the test matrix" in page_ag)

# --- subagent per-role session view ----------------------------------------------
# Task-spawned subagents share the parent's session_id; /_session?role=<role>
# renders that role's latest captured turn, and the /_admin ↳ rows link to it.
psid = "5fb9eba7-1111-2222-3333-444444444444"
lp._capture_session_meta(psid,
                         {"system": [{"type": "text", "text": "You are Claude Code"}],
                          "messages": [{"role": "user", "content": "do it"}]},
                         "claude-opus-4-8", role="parent")
sub_obj = {"system": [{"type": "text", "text": "agent for Claude Code"}],
           "messages": [{"role": "user", "content": "SUBAGENT TASK MARKER"}]}
lp._capture_session_meta(psid, sub_obj, "claude-haiku-4-5", role="general-purpose")
lp._WRITE_Q.join()
check("subagent request is stashed per role; absent role -> None",
      (lp._subagent_request(psid, "general-purpose") or {}).get("obj") is sub_obj
      and lp._subagent_request(psid, "verification") is None)
snap_p = lp._status_snapshot(session=psid)
check("parent identity survives the subagent turn (model stays parent's opus)",
      snap_p["sessions"][0]["model"] == "claude-opus-4-8"
      and any(sa["role"] == "general-purpose"
              for sa in (snap_p["sessions"][0].get("sub_agents") or [])))
admin_p = lp._render_admin_html(snap_p, host="t:7800")
check("admin links the ↳ subagent row to the per-instance view (no agent-id -> key=role)",
      f"/_session?session={psid}&amp;sub=general-purpose" in admin_p)
subpage = lp._render_session_html(psid, lp._subagent_request(psid, "general-purpose"),
                                  snap_p, subrole="general-purpose")
check("subagent page shows the role, a back link, and the captured request",
      "&#8627; <b>general-purpose</b>" in subpage
      and f'href="/_session?session={psid}"' in subpage
      and "SUBAGENT TASK MARKER" in subpage and "haiku" in subpage)
empty_sub = lp._render_session_html(psid, lp._subagent_request(psid, "verification"),
                                    snap_p, subrole="verification")
check("missing-role subagent page renders gracefully (no crash, shows note)",
      "&#8627; <b>verification</b>" in empty_sub
      and "no replayable request" in empty_sub.lower())

# --- concurrent custom subagents keyed by x-claude-code-agent-id instance ---------
# Three customs spawned at once all classify as role "subagent"; the agent-id
# header keeps them three distinct rows/pages, and repeated turns of one instance
# merge (count bumps, not a new row).
_cbh = "x-anthropic-billing-header: cc_entrypoint=cli; cc_is_subagent=true; "
csid = "5fb9eba7-aaaa-bbbb-cccc-dddddddddddd"
for aid, marker in [("a6285af7ebb94c299", "PROBE-ALPHA-CTX"),
                    ("abeb7108bd7b6623a", "PROBE-BETA-CTX"),
                    ("a9184c50c47de95bf", "PROBE-GAMMA-CTX")]:
    lp._capture_session_meta(csid,
        {"system": [{"type": "text", "text": _cbh + "You are a probe"}],
         "messages": [{"role": "user", "content": marker}]},
        "claude-opus-4-8", role="subagent", agent_id=aid)
# alpha takes a second turn -> same instance, no new row
lp._capture_session_meta(csid,
    {"system": [{"type": "text", "text": _cbh + "You are a probe"}],
     "messages": [{"role": "user", "content": "PROBE-ALPHA-CTX-2"}]},
    "claude-opus-4-8", role="subagent", agent_id="a6285af7ebb94c299")
lp._WRITE_Q.join()
snap_c = lp._status_snapshot(session=csid)
csubs = snap_c["sessions"][0].get("sub_agents") or []
check("three concurrent customs stay three distinct instances (keyed by agent-id)",
      len(csubs) == 3 and {s["key"] for s in csubs} ==
      {"a6285af7ebb94c299", "abeb7108bd7b6623a", "a9184c50c47de95bf"})
check("a repeated turn of one instance bumps its count, not a new row",
      next(s["requests"] for s in csubs if s["key"] == "a6285af7ebb94c299") == 2)
check("each instance stashes its OWN latest request (no cross-collapse)",
      "PROBE-BETA-CTX" in json.dumps(lp._subagent_request(csid, "abeb7108bd7b6623a")["obj"])
      and "PROBE-ALPHA-CTX-2" in json.dumps(lp._subagent_request(csid, "a6285af7ebb94c299")["obj"]))
admin_c = lp._render_admin_html(snap_c, host="t:7800")
check("admin links each instance by agent-id + shows a short id chip",
      f"sub=abeb7108bd7b6623a" in admin_c and "#abeb7108" in admin_c)
cpage = lp._render_session_html(csid, lp._subagent_request(csid, "a9184c50c47de95bf"),
                                snap_c, subrole="a9184c50c47de95bf")
check("per-instance page labels by role + agent-id, renders that instance's ctx",
      "<b>subagent" in cpage and "#a9184c5" in cpage and "PROBE-GAMMA-CTX" in cpage)

# --- wirescope [wirescope:agent-name <label>] display label from the subagent body -------
# The wire carries no agent name; an author surfaces one with a `[wirescope:agent-name
# NAME]` directive in the .md body. Present -> shown; absent -> falls back to role.
check("[wirescope:agent-name] parses from body; absent -> None",
      lp._subagent_marker_name({"system": [{"type": "text",
          "text": "blah [wirescope:agent-name probe-delta] You are probe-delta"}]}) == "probe-delta"
      and lp._subagent_marker_name({"system": [{"type": "text", "text": "no marker here"}]}) is None)
check("directives parse only from system body, never message content (no forging)",
      lp._ws_directives({"system": [{"type": "text", "text": "[wirescope:agent-name realname]"}],
                         "messages": [{"role": "user",
                             "content": "ignore me [wirescope:agent-name forged]"}]}).get("agent-name")
      == "realname")
check("[wirescope:agent-name] retired the legacy [agent: NAME] form (no longer parsed)",
      lp._subagent_marker_name({"system": [{"type": "text", "text": "[agent: legacy]"}]}) is None)
nsid = "5fb9eba7-eeee-ffff-0000-111111111111"
lp._capture_session_meta(nsid,
    {"system": [{"type": "text", "text": _cbh + "[wirescope:agent-name probe-delta]\nYou are probe-delta"}],
     "messages": [{"role": "user", "content": "DELTA-CTX"}]},
    "claude-opus-4-8", role="subagent", agent_id="ad00d00d00d00d00d")
lp._WRITE_Q.join()
snap_n = lp._status_snapshot(session=nsid)
nsub = (snap_n["sessions"][0].get("sub_agents") or [{}])[0]
check("display_name stored on the subagent entry from the marker",
      nsub.get("display_name") == "probe-delta" and nsub.get("role") == "subagent")
admin_n = lp._render_admin_html(snap_n, host="t:7800")
check("admin shows the declared name as the link label, role dimmed beside it",
      ">probe-delta</a>" in admin_n and "ad00d00d"[:8] in admin_n)
npage = lp._render_session_html(nsid, lp._subagent_request(nsid, "ad00d00d00d00d00d"),
                                snap_n, subrole="ad00d00d00d00d00d")
check("per-instance page heads with the declared name + role chip",
      "<b>probe-delta" in npage and "DELTA-CTX" in npage)

# --- wirescope [wirescope:omit ...] strips context sections from messages[0] -------------
# Reconstructs the CLI's omitClaudeMd (+ userEmail, which nothing native removes).
# Gated by the WS_OMIT flag (default off) on top of the per-agent directive.
_reminder = ("<system-reminder>\nAs you answer, you can use the following context:\n"
             "# claudeMd\nContents of CLAUDE.md:\nMARKER-CLAUDEMD body line\n"
             "# userEmail\nThe user's email address is x@y.com\n</system-reminder>")
def _omit_obj():
    return {"system": [{"type": "text", "text": _cbh + "[wirescope:omit claudemd,useremail]\nYou are a probe"}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _reminder},
                {"type": "text", "text": "<system-reminder>\n# currentDate\nToday\n</system-reminder>"},
                {"type": "text", "text": "do the task"}]}]}
check("[wirescope:omit] parses the target list from the directive",
      (lp._ws_directives(_omit_obj()).get("omit") or "").split(",") == ["claudemd", "useremail"])
# REGRESSION (2026-06-14 wire leak): a claudeMd body that LEADS with markdown
# headings must be stripped IN FULL up to the NEXT reminder section. The old
# `^# ` boundary stopped at the first content heading (`# Spatiul lui Adam`),
# cutting only the preamble and leaving the whole project doc on the wire.
_rem_md = ("<system-reminder>\nAs you answer:\n"
           "# claudeMd\nContents of CLAUDE.md:\n\n# Project Title\nbody A\n"
           "## Subsection\nMARKER-DEEP body\n# Another H1\nmore\n"
           "# userEmail\nThe user's email is x@y.com\n</system-reminder>")
_stripped, _n = lp.transforms._ws_strip_reminder_section(_rem_md, "# claudeMd")
check("omit strips the WHOLE claudeMd section past internal markdown headings",
      "# claudeMd" not in _stripped and "Project Title" not in _stripped
      and "MARKER-DEEP" not in _stripped and "Another H1" not in _stripped)
check("omit claudeMd PRESERVES the sibling userEmail section in the same block",
      "# userEmail" in _stripped and "x@y.com" in _stripped)
check("strip of the LAST section (userEmail) stops at the closing tag, keeps claudeMd",
      (lambda s, _n: "</system-reminder>" in s and "x@y.com" not in s
       and "# claudeMd" in s)(
          *lp.transforms._ws_strip_reminder_section(_rem_md, "# userEmail")))
check("replace swaps the claudeMd body across internal headings, keeps userEmail",
      (lambda s, n: n == 1 and "# claudeMd" in s and "Project Title" not in s
       and "MARKER-DEEP" not in s and "LEAN-X" in s and "# userEmail" in s)(
          *lp.transforms._ws_replace_reminder_section(_rem_md, "# claudeMd", "LEAN-X")))
# flag OFF -> no-op
lp.transforms.WS_OMIT = False
check("omit is a no-op while the WS_OMIT flag is off", lp.transforms._ws_omit(_omit_obj()) is None)
# flag ON -> strips both; block[0] held ONLY claudeMd+userEmail, so it's emptied
# of all sections and DROPPED WHOLE (no dangling 'here's the context:' shell).
# currentDate (was content[1]) shifts up to content[0], the prompt to content[1].
lp.transforms.WS_OMIT = True
o1 = _omit_obj()
res = lp.transforms._ws_omit(o1)
_content = o1["messages"][0]["content"]
_alltext = " ".join(b.get("text", "") for b in _content if isinstance(b, dict))
check("omit strips # claudeMd + # userEmail and DROPS the now-empty reminder block",
      res and sorted(res["omitted"]) == ["claudemd", "useremail"]
      and res.get("dropped_blocks") == 1 and len(_content) == 2
      and "MARKER-CLAUDEMD" not in _alltext and "x@y.com" not in _alltext)
check("after the drop, the separate currentDate block + prompt remain (shifted up)",
      "# currentDate" in _content[0]["text"] and "do the task" in _content[1]["text"])
# a reminder that KEEPS a section (currentDate via keep) is NOT dropped; and the
# message-level cache breakpoint is re-anchored onto the new first block.
_ccobj = {"system": [{"type": "text", "text": _cbh + "[wirescope:omit claudemd,useremail]\nx"}],
          "messages": [{"role": "user", "content": [
              {"type": "text", "cache_control": {"type": "ephemeral"},
               "text": _reminder},
              {"type": "text", "text": "task"}]}]}
lp.transforms._ws_omit(_ccobj)
_cc = _ccobj["messages"][0]["content"]
check("emptied reminder dropped → its cache_control re-anchors on the new first block",
      len(_cc) == 1 and _cc[0]["text"] == "task"
      and _cc[0].get("cache_control") == {"type": "ephemeral"})
check("omit is idempotent (second pass finds nothing -> miss, no further change)",
      (lambda r: r is not None and r["omitted"] == [] and "claudemd" in r["missed"])(
          lp.transforms._ws_omit(o1)))
# unknown / absent target -> logged miss, never an over-strip
miss = lp.transforms._ws_omit({"system": [{"type": "text", "text": "[wirescope:omit bogus]"}],
                               "messages": [{"role": "user", "content": [
                                   {"type": "text", "text": _reminder}]}]})
check("unknown omit target is a safe logged miss (no strip)",
      miss is not None and miss["omitted"] == [] and miss["missed"] == ["bogus"])
lp.transforms.WS_OMIT = False        # restore default for any later checks

# --- directives are stripped from system before forwarding (no model exposure) ----
def _dir_obj():
    return {"system": [{"type": "text", "text": "hdr"},
                       {"type": "text", "text":
                        "[wirescope:agent-name probe-zeta]\n[wirescope:omit claudemd]\nYou are probe-zeta, a probe."}]}
d1 = _dir_obj()
sres = lp.transforms._ws_strip_directives(d1)
sys2 = d1["system"][1]["text"]
check("strip removes every [wirescope:...] line from system, leaves the prose",
      sres and sres["stripped"] == 2 and "[wirescope:" not in sys2
      and "You are probe-zeta" in sys2)
check("strip is deterministic (same input -> identical bytes; cache-constant)",
      (lambda a, b: (lp.transforms._ws_strip_directives(a), lp.transforms._ws_strip_directives(b),
       a["system"][1]["text"] == b["system"][1]["text"])[2])(_dir_obj(), _dir_obj()))
check("nothing to strip -> None (no-op on a directive-free body)",
      lp.transforms._ws_strip_directives({"system": [{"type": "text", "text": "plain"}]}) is None)
# the display name still lands even when the server passes it pre-strip and the
# obj meta sees is already stripped (simulates the real server ordering)
zsid = "5fb9eba7-2222-3333-4444-555555555555"
stripped_obj = _dir_obj(); lp.transforms._ws_strip_directives(stripped_obj)
lp._capture_session_meta(zsid, stripped_obj, "claude-opus-4-8", role="subagent",
                         agent_id="aZ00", display_name="probe-zeta")
lp._WRITE_Q.join()
zsub = (lp._status_snapshot(session=zsid)["sessions"][0].get("sub_agents") or [{}])[0]
check("display_name from the pre-strip server param survives a stripped obj",
      zsub.get("display_name") == "probe-zeta")

# --- wirescope v1: spawn-position directives (messages[0] head) ------------------
# A directive at the STRICT HEAD of the spawn-prompt block applies omit/keep to
# UNEDITABLE built-in subagents (lead the Task prompt with it). messages[0] is
# frozen at spawn, so it is NOT injectable by mid-conversation content; only the
# leading run of pure directive lines is honored. Gated by WS_SPAWN_DIRECTIVES.
def _spawn_obj(prompt, body=""):
    return {"system": [{"type": "text", "text": _cbh + body + "You are a probe"}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _reminder},
                {"type": "text", "text": "<system-reminder>\n# currentDate\nT\n</system-reminder>"},
                {"type": "text", "text": prompt}]}]}
lp.writer.WS_SPAWN_DIRECTIVES = True
check("spawn directive parses from the strict head of the prompt block",
      lp.writer._ws_spawn_directives(_spawn_obj("[wirescope:omit useremail]\nDo the task"))
      .get("omit") == "useremail")
check("prompt block = the first NON-system-reminder block (not the reminders)",
      lp.writer._ws_prompt_block(_spawn_obj("HELLO"))["text"] == "HELLO")
check("a directive NOT at the prompt head is ignored (no mid-prose match)",
      lp.writer._ws_spawn_directives(_spawn_obj("do this [wirescope:omit useremail]")) == {})
check("a directive in a later message is never read (messages[0] head only)",
      (lambda o: (o["messages"].append({"role": "user", "content": [
          {"type": "text", "text": "[wirescope:omit claudemd]"}]}),
          lp.writer._ws_spawn_directives(o))[1])(_spawn_obj("plain prompt")) == {})
check("spawn omit merges with body omit",
      lp.transforms._ws_effective_omit_targets(
          _spawn_obj("[wirescope:omit useremail]\nx", body="[wirescope:omit claudemd]\n"))
      == {"claudemd", "useremail"})
check("spawn keep overrides a body omit (precedence spawn > body)",
      lp.transforms._ws_effective_omit_targets(
          _spawn_obj("[wirescope:keep claudemd]\nx", body="[wirescope:omit claudemd,useremail]\n"))
      == {"useremail"})
lp.transforms.WS_OMIT = True
_so = _spawn_obj("[wirescope:omit claudemd,useremail]\nDo it")
_sres = lp.transforms._ws_omit(_so)
_sbody = _so["messages"][0]["content"][0]["text"]
check("spawn-only omit strips claudeMd + userEmail from messages[0]",
      _sres and sorted(_sres["omitted"]) == ["claudemd", "useremail"]
      and "MARKER-CLAUDEMD" not in _sbody and "x@y.com" not in _sbody)
lp.transforms.WS_OMIT = False
_sd = _spawn_obj("[wirescope:omit useremail]\n[wirescope:agent-name p]\nReal task here")
_st = lp.transforms._ws_strip_spawn_directives(_sd)
_ptxt = lp.writer._ws_prompt_block(_sd)["text"]
check("spawn strip removes the leading directive lines, leaves the task prose",
      _st and _st["stripped"] == 2 and "[wirescope:" not in _ptxt
      and _ptxt.startswith("Real task"))
check("spawn strip leaves a NON-leading [wirescope:...] in prose untouched",
      (lambda o: (lp.transforms._ws_strip_spawn_directives(o),
       "[wirescope:omit x]" in lp.writer._ws_prompt_block(o)["text"])[1])(
          _spawn_obj("keep this [wirescope:omit x] literal")))
check("spawn agent-name wins over body agent-name (display precedence)",
      lp.writer._subagent_marker_name(_spawn_obj(
          "[wirescope:agent-name spawnname]\nx", body="[wirescope:agent-name bodyname]\n"))
      == "spawnname")
lp.writer.WS_SPAWN_DIRECTIVES = False
check("WS_SPAWN_DIRECTIVES=off disables all spawn-position parsing",
      lp.writer._ws_spawn_directives(_spawn_obj("[wirescope:omit useremail]\nx")) == {}
      and lp.transforms._ws_strip_spawn_directives(_spawn_obj("[wirescope:omit u]\nx")) is None)
lp.writer.WS_SPAWN_DIRECTIVES = True   # restore default

# --- wirescope v1: replace verb (substitute a section body, inline one-liner) ----
# `[wirescope:replace <target> <text>]` keeps the # <Section> heading and swaps
# its body for the inline text. Gated by WS_OMIT (same section-rewrite family).
lp.transforms.WS_OMIT = True
_ro = _spawn_obj("[wirescope:replace claudemd Use only docs/LEAN.md]\nDo it")
_rres = lp.transforms._ws_omit(_ro)
_rbody = _ro["messages"][0]["content"][0]["text"]
check("replace swaps the # claudeMd body, keeps the heading + other sections",
      _rres and _rres["replaced"] == ["claudemd"]
      and "# claudeMd" in _rbody and "Use only docs/LEAN.md" in _rbody
      and "MARKER-CLAUDEMD" not in _rbody and "x@y.com" in _rbody
      and "</system-reminder>" in _rbody)
check("spawn replace overrides a body omit (precedence spawn > body)",
      (lambda o: (lp.transforms._ws_omit(o),
       o["messages"][0]["content"][0]["text"])[1])(
          _spawn_obj("[wirescope:replace claudemd KEPT-LEAN]\nx",
                     body="[wirescope:omit claudemd]\n")).count("KEPT-LEAN") == 1)
check("replace on an absent/unknown section is a safe logged miss",
      (lambda r: r is not None and r["replaced"] == [] and "bogus" in r["missed"])(
          lp.transforms._ws_omit({"system": [{"type": "text",
              "text": _cbh + "[wirescope:replace bogus hi]\nYou are a probe"}],
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": _reminder}]}]})))
check("action resolver: replace beats omit, keep cancels (per source order)",
      lp.transforms._ws_resolve_actions(
          [("omit", "claudemd"), ("replace", "claudemd LEAN"), ("keep", "useremail")])
      == {"claudemd": ("replace", "LEAN")})
# Liberal separator (Postel's law): a hint-discovered agent naturally writes
# SPACE-separated targets; comma / space / comma+space must all parse the same
# (real catch 2026-06-14: space form silently no-op'd a correct omit).
check("omit target list: space-separated parses (the naive form)",
      lp.transforms._ws_omit_target_list("claudemd useremail") == ["claudemd", "useremail"])
check("omit target list: comma, comma+space, and mixed whitespace all equivalent",
      lp.transforms._ws_omit_target_list("claudemd,useremail")
      == lp.transforms._ws_omit_target_list("claudemd, useremail")
      == lp.transforms._ws_omit_target_list("  claudemd   useremail ")
      == ["claudemd", "useremail"])
check("space-separated omit actually strips both sections (end to end)",
      (lambda r: r is not None and set(r["omitted"]) == {"claudemd", "useremail"})(
          lp.transforms._ws_omit({"system": [{"type": "text",
              "text": _cbh + "[wirescope:omit claudemd useremail]\nYou are a probe"}],
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": _reminder}]}]})))
lp.transforms.WS_OMIT = False
check("replace is gated by WS_OMIT too (off -> no-op)",
      lp.transforms._ws_omit(_spawn_obj("[wirescope:replace claudemd X]\nx")) is None)

# --- wirescope: operator default omit policy (WS_OMIT_DEFAULT) -------------------
# An operator can strip targets from EVERY subagent spawn with zero agent/spawner
# knowledge (lowest-precedence layer, subagent-only, keep-overridable).
lp.transforms.WS_OMIT = True
_save_omit_default = lp.transforms.WS_OMIT_DEFAULT
lp.transforms.WS_OMIT_DEFAULT = ["useremail"]
def _sub_obj(prompt="do it", sysflag="cc_is_subagent=true"):
    return {"system": [{"type": "text", "text": sysflag + "\nYou are a probe"}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _reminder},
                {"type": "text", "text": prompt}]}]}
_od = _sub_obj()
_odres = lp.transforms._ws_omit(_od)
check("WS_OMIT_DEFAULT strips useremail from a subagent spawn with no directive",
      _odres and _odres["omitted"] == ["useremail"]
      and "x@y.com" not in _od["messages"][0]["content"][0]["text"]
      and "MARKER-CLAUDEMD" in _od["messages"][0]["content"][0]["text"])
check("operator default does NOT touch a main-agent turn (no cc_is_subagent)",
      lp.transforms._ws_omit(_sub_obj(sysflag="You are Claude Code")) is None)
_ko = _sub_obj(prompt="[wirescope:keep useremail]\ndo it")
check("a [wirescope:keep] cancels the operator default (precedence keep > operator)",
      lp.transforms._ws_omit(_ko) is None
      and "x@y.com" in _ko["messages"][0]["content"][0]["text"])
lp.transforms.WS_OMIT_DEFAULT = _save_omit_default
lp.transforms.WS_OMIT = False

# --- wirescope: STICKY per-instance spawn directives (turn-1-only -> persists) ---
# Spawn-position directives sit at the head of messages[0] only on a subagent's
# FIRST turn; a continuation turn swaps that block for a follow-up, so without
# memory the omit is LOST and # claudeMd RETURNS (proven on the wire: session
# 1aa29620 agent-id af7427970be15b5f3, capture 027 omit -> 029 claudemd back).
# We remember the spawn pairs by the per-instance x-claude-code-agent-id and
# re-apply them on later turns of the same instance.
lp.transforms.WS_OMIT = True
_save_sticky_default = lp.transforms.WS_OMIT_DEFAULT
lp.transforms.WS_OMIT_DEFAULT = []          # isolate from the operator floor
lp.transforms._WS_SPAWN_MEMORY.clear()
def _inst_obj(prompt, sid="sess-sticky"):
    # a subagent turn (cc_is_subagent via _cbh) with a fresh reminder + the given
    # first prompt block; metadata supplies the real session_id for the memory key
    return {"system": [{"type": "text", "text": _cbh + "You are a probe"}],
            "metadata": {"user_id": json.dumps({"session_id": sid})},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _reminder},
                {"type": "text", "text": prompt}]}]}
# turn 1: spawn directive present + agent_id A1 -> strips claudeMd AND remembers
_t1 = _inst_obj("[wirescope:omit claudemd]\nDo the task")
_r1 = lp.transforms._ws_omit(_t1, agent_id="A1")
check("sticky turn1: spawn omit strips claudeMd and is remembered for the instance",
      _r1 and _r1["omitted"] == ["claudemd"]
      and "MARKER-CLAUDEMD" not in _t1["messages"][0]["content"][0]["text"]
      and lp.transforms._WS_SPAWN_MEMORY.get("sess-sticky", {}).get("A1"))
# turn 2: SAME agent_id, NO directive at head, claudeMd present -> stripped via memory
_t2 = _inst_obj("just a follow-up question")
_r2 = lp.transforms._ws_omit(_t2, agent_id="A1")
check("sticky turn2: same instance with NO directive still strips claudeMd (memory)",
      _r2 and _r2["omitted"] == ["claudemd"]
      and "MARKER-CLAUDEMD" not in _t2["messages"][0]["content"][0]["text"])
# turn 2 for a DIFFERENT (unknown) instance -> no memory -> untouched
_t2b = _inst_obj("a follow-up from another sub")
_r2b = lp.transforms._ws_omit(_t2b, agent_id="B2")
check("sticky: an unknown agent_id has no memory -> claudeMd untouched",
      _r2b is None and "MARKER-CLAUDEMD" in _t2b["messages"][0]["content"][0]["text"])
# a later `keep` turn UPDATES the memory and cancels the omit
_t3 = _inst_obj("[wirescope:keep claudemd]\ncarry on")
_r3 = lp.transforms._ws_omit(_t3, agent_id="A1")
check("sticky: a later [wirescope:keep] updates the memory and cancels the omit",
      (_r3 is None or _r3["omitted"] == [])
      and "MARKER-CLAUDEMD" in _t3["messages"][0]["content"][0]["text"])
# and the cancel persists: a subsequent directive-less turn no longer strips
_t4 = _inst_obj("another follow-up")
_r4 = lp.transforms._ws_omit(_t4, agent_id="A1")
check("sticky: the keep persists -> a later bare turn no longer strips",
      _r4 is None and "MARKER-CLAUDEMD" in _t4["messages"][0]["content"][0]["text"])
# main line (no agent_id) is never sticky even with memory present for the session
_t5 = _inst_obj("plain turn")
_r5 = lp.transforms._ws_omit(_t5, agent_id=None)
check("sticky: no agent_id (main line) never replays remembered actions",
      _r5 is None and "MARKER-CLAUDEMD" in _t5["messages"][0]["content"][0]["text"])
# _ws_forget drops the session memory (the pinger sweep hook)
lp.transforms._ws_forget("sess-sticky")
check("_ws_forget clears the session's sticky spawn memory",
      "sess-sticky" not in lp.transforms._WS_SPAWN_MEMORY)
lp.transforms._WS_SPAWN_MEMORY.clear()
lp.transforms.WS_OMIT_DEFAULT = _save_sticky_default
lp.transforms.WS_OMIT = False

# --- wirescope: spawner discovery hint (WS_SPAWNER_HINT, opt-in, model-visible) --
# The one place wirescope adds proxy-authored visible text: a constant
# SELF-CONTAINED grammar block (the recipient is in its own cwd and can't open the
# proxy-side WIRESCOPE.md, so the syntax is inline, not a file pointer), only for a
# main agent that can actually spawn (Agent/Task tool), never a subagent, off by default.
_save_hint = lp.transforms.WS_SPAWNER_HINT
lp.transforms.WS_SPAWNER_HINT = True
def _spawner_obj(tools=("Agent", "Read"), sysflag="You are Claude Code"):
    return {"system": [{"type": "text", "text": sysflag}],
            "tools": [{"name": n} for n in tools],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
_hobj = _spawner_obj()
_hres = lp.transforms._ws_spawner_hint(_hobj)
check("spawner hint appends a trailing system block for a main agent with a spawn tool",
      _hres and _hres["injected"] and "[wirescope]" in _hobj["system"][-1]["text"]
      and len(_hobj["system"]) == 2)
check("spawner hint is idempotent (second pass no-ops, no duplicate block)",
      lp.transforms._ws_spawner_hint(_hobj) is None and len(_hobj["system"]) == 2)
check("spawner hint is self-contained grammar (carries usable verbs inline, not just a file pointer)",
      all(tok in _hobj["system"][-1]["text"]
          for tok in ("[wirescope:omit", "[wirescope:keep", "[wirescope:replace",
                      "[wirescope:agent-name"))
      and "WIRESCOPE.md" not in _hobj["system"][-1]["text"])
check("spawn tool 'Task' (vanilla Claude Code) also triggers the hint",
      lp.transforms._ws_spawner_hint(_spawner_obj(tools=("Task", "Read"))) is not None)
check("no hint for a main agent WITHOUT a spawn tool (Agent/Task absent)",
      lp.transforms._ws_spawner_hint(_spawner_obj(tools=("Read", "Bash"))) is None)
check("no hint for a subagent (cc_is_subagent), even with a spawn tool",
      lp.transforms._ws_spawner_hint(_spawner_obj(sysflag="cc_is_subagent=true")) is None)
lp.transforms.WS_SPAWNER_HINT = False
check("spawner hint is off by default -> no-op",
      lp.transforms._ws_spawner_hint(_spawner_obj()) is None)
lp.transforms.WS_SPAWNER_HINT = _save_hint

# --- _classify_role: billing-header cc_is_subagent backstop ----------------------
# A CUSTOM .claude/agents subagent matches no signature and its prose says
# "Claude Code" — without the header flag it used to be mislabeled "parent" and
# clobbered the durable main line. The cc_is_subagent=true header is ground truth.
_bh = "x-anthropic-billing-header: cc_entrypoint=cli; cc_is_subagent=true; "
custom_sub = {"system": [{"type": "text", "text": _cbh + "You are Claude Code, custom probe agent."}]}
check("custom subagent (header-flagged, no signature) -> 'subagent', not 'parent'",
      lp._classify_role(custom_sub) == "subagent"
      and lp._is_subagent_role("subagent"))
check("named builtin subagent still wins by signature over generic bucket",
      lp._classify_role({"system": [{"type": "text", "text": _cbh + "agent for Claude Code"}]})
      == "general-purpose")
check("routed MAIN agent (no header flag) stays 'parent'",
      lp._classify_role({"system": [{"type": "text",
          "text": "x-anthropic-billing-header: cc_entrypoint=cli; You are Claude Code"}]})
      == "parent")

# --- context.input_tokens: wire-measured context size on /_status ----------------
# The number /_session renders as "context = X tok" (cache_read + cache_write +
# uncached input of the last turn) must also be in the polled /_status context.
check("_input_token_total sums read + write(5m+1h) + input",
      lp._input_token_total({"cache_read_input_tokens": 150000,
                             "cache_write_5m_tokens": 8000,
                             "cache_write_1h_tokens": 2000,
                             "input_tokens": 1800}) == 161800)
check("_input_token_total uses flat write when 5m/1h absent",
      lp._input_token_total({"cache_read_input_tokens": 100,
                             "cache_write_flat_tokens": 50,
                             "input_tokens": 10}) == 160)
check("_input_token_total is None with no usage", lp._input_token_total(None) is None)
ctxsid = "c0ffee00-0000-0000-0000-000000000000"
lp._capture_session_meta(ctxsid,
                         {"system": [{"type": "text", "text": "You are Claude Code"}],
                          "messages": [{"role": "user", "content": "go"}]},
                         "claude-opus-4-8", role="parent")
lp._WRITE_Q.join()
lp._CONTEXT_STATS[ctxsid] = {"turns_in_context": 30, "n_messages": 238,
                            "max_tool_result_chars": 7123, "ts": 1.0}
lp._LAST_USAGE[ctxsid] = {"cache_read_input_tokens": 150000, "cache_write_5m_tokens": 8000,
                         "cache_write_1h_tokens": 2000, "input_tokens": 1800, "ts": 2.0}
ctx = lp._status_snapshot(session=ctxsid)["sessions"][0]["context"]
check("/_status context carries input_tokens AND keeps the heaviness fields",
      ctx["input_tokens"] == 161800 and ctx["turns_in_context"] == 30
      and ctx["n_messages"] == 238)
# /_session header and /_status report the SAME number (shared helper)
spg = lp._render_session_html(ctxsid, {"obj": {"messages": []}, "ts": 2.0},
                              lp._status_snapshot(session=ctxsid),
                              usage=lp._LAST_USAGE[ctxsid])
check("/_session 'context = X tok' matches /_status input_tokens (161.8k)",
      "context = 161.8k tok" in spg)

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
check("/_end retains the replayable entry + its mirror row (sweeper's job now)",
      left == 1 and "sess-lr-1" in lp._LAST_REQUEST)
# the staleness sweep reaps the ended session's whole debug bundle in step
# (make the prefix REALLY stale: drop its warmth row so the predicate falls
# back to the entry ts, which we backdate past ttl+grace)
with lp._LAST_REQUEST_LOCK:
    lp._LAST_REQUEST["sess-lr-1"]["ts"] = time.time() - 7200
    _h_lr = lp._prefix_hash(lp._LAST_REQUEST["sess-lr-1"]["obj"],
                            len(lp._LAST_REQUEST["sess-lr-1"]["obj"]["messages"]))
con7b = sqlite3.connect(os.environ["WARMTH_DB"])
con7b.execute("DELETE FROM warmth WHERE hash=?", (_h_lr,))
con7b.commit(); con7b.close()
lp._CONTEXT_STATS["sess-lr-1"] = {"turns_in_context": 1}
lp._LAST_RESPONSE["sess-lr-1"] = {"text": "bye"}
lp._sweep_state()
con8 = sqlite3.connect(os.environ["WARMTH_DB"])
left8 = con8.execute("SELECT COUNT(*) FROM last_request "
                     "WHERE session_id='sess-lr-1'").fetchone()[0]
con8.close()
check("sweep reaps stale entry + mirror + context + last answer together",
      left8 == 0 and "sess-lr-1" not in lp._LAST_REQUEST
      and "sess-lr-1" not in lp._CONTEXT_STATS
      and "sess-lr-1" not in lp._LAST_RESPONSE)
check("the ended marker outlives the sweep (durable identity, not runtime state)",
      lp._ENDED.get("sess-lr-1", {}).get("reason") == "clear")

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
lp.hold.WARMTH_AUTH_BOOTSTRAP = False
check("bootstrap respects the kill switch",
      lp._bootstrap_decision("acct-z", now=NOW, state=dict(BST))[0] is False)
lp.hold.WARMTH_AUTH_BOOTSTRAP = _sb

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
# /_session affordances (2026-06-12): cache-boundary dividers, expand-without-
# duplication previews, slim tool lines, last-turn token receipts in the header
check("a marked system block draws a cache-boundary divider",
      "cache breakpoint 1 · ttl 1h" in _sv and "prefix above" in _sv)
check("tool churn renders as slim collapsed lines, not full blocks",
      'class="tline tooluse"' in _sv and 'class="tline toolres"' in _sv)
_pv = lp._prevu("HEAD!" + "y" * 100, cap=5)
check("preview expansion continues the text instead of duplicating it",
      _pv.count("HEAD!") == 1 and "show remaining 100 of 105 ch" in _pv
      and "y" * 100 in _pv)
_mk_entry = {
    "obj": {"model": "claude-fable-5",
            "tools": [{"name": "Bash", "input_schema": {},
                       "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            "system": [],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "hi",
                 "cache_control": {"type": "ephemeral"}}]}]},
    "path": "/v1/messages", "ts": time.time(), "needs_auth": False}
_mkv = lp._render_session_html("sess-meta-1", _mk_entry,
                               lp._status_snapshot(session="sess-meta-1"))
check("breakpoints number through canonical order: tools first, then messages "
      "(message markers default to 5m)",
      "cache breakpoint 1 · ttl 1h" in _mkv
      and "cache breakpoint 2 · ttl 5m" in _mkv)
_uv = lp._render_session_html(
    "sess-meta-1", _sv_entry, lp._status_snapshot(session="sess-meta-1"),
    usage={"cache_read_input_tokens": 12000, "cache_write_1h_tokens": 300,
           "input_tokens": 7, "output_tokens": 42, "est_usd": 0.0123,
           "ts": time.time()})
check("token receipts from the last response render in the header",
      "cache read" in _uv and "12.0k" in _uv and "cache written" in _uv
      and "$0.0123" in _uv)
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

# --- OPENAI / CODEX provider (2026-06-11) -------------------------------------
_m = lp._ROUTE_OPENAI.match("/agent/codex/openai/v1/responses")
check("openai route matches /agent/<name>/openai/...",
      _m is not None and _m.group("name") == "codex"
      and _m.group("rest") == "/v1/responses")
check("openai route ignores anthropic + bare paths",
      lp._ROUTE_OPENAI.match("/agent/x/anthropic/v1/messages") is None
      and lp._ROUTE_OPENAI.match("/v1/responses") is None)
check("anthropic route unaffected by openai paths",
      lp._ROUTE.match("/agent/codex/openai/v1/responses") is None)

check("chatgpt-backend detect", lp._is_chatgpt_backend(
      "https://chatgpt.com/backend-api/codex")
      and not lp._is_chatgpt_backend("https://api.openai.com"))

from pathlib import Path as _P
_authf = _P(tempfile.mkdtemp(prefix="codexauth_")) / "auth.json"
_authf.write_text(json.dumps(
    {"tokens": {"access_token": "tok123", "account_id": "acct456"}}))
_hdrs = {"Authorization": "Bearer client-sent", "originator": "codex_exec"}
_p2, _h2 = lp._rewrite_chatgpt_request("/v1/responses", dict(_hdrs),
                                       auth_path=_authf)
check("chatgpt rewrite strips /v1 + swaps auth + keeps client originator",
      _p2 == "/responses" and _h2["authorization"] == "Bearer tok123"
      and _h2["chatgpt-account-id"] == "acct456"
      and "Authorization" not in _h2 and _h2["originator"] == "codex_exec")
_p3, _h3 = lp._rewrite_chatgpt_request(
    "/v1/responses", {"authorization": "Bearer x"},
    auth_path=_P("/nonexistent/auth.json"))
check("unreadable auth.json leaves the request untouched (clean upstream 401)",
      _p3 == "/v1/responses" and _h3 == {"authorization": "Bearer x"})

check("codex auth headers are redacted in captures",
      lp._safe_headers({"chatgpt-account-id": "a", "Authorization": "b",
                        "session-id": "s"})
      == {"chatgpt-account-id": "<redacted>", "Authorization": "<redacted>",
          "session-id": "s"})

if lp._zstd is not None:
    _blob = json.dumps({"model": "gpt-5.4", "input": []}).encode()
    _dec, _err = lp._content_decode(lp._zstd.compress(_blob), "zstd")
    check("zstd request body decodes for capture", _dec == _blob and _err is None)
else:
    print("SKIP  zstd decode (py<3.14, no stdlib compression.zstd)")
_dec2, _err2 = lp._content_decode(b"\x00garbage", "zstd")
check("corrupt body degrades to raw + error, never raises",
      _dec2 == b"\x00garbage" and _err2 is not None)
check("identity/empty encodings pass through",
      lp._content_decode(b"x", None) == (b"x", None)
      and lp._content_decode(b"x", "identity") == (b"x", None))

check("openai delta extraction (Responses API + chat completions + DONE-safe)",
      lp._sse_text_delta({"type": "response.output_text.delta", "delta": "hi"},
                         "openai") == "hi"
      and lp._sse_text_delta({"choices": [{"delta": {"content": "yo"}}]},
                             "openai") == "yo"
      and lp._sse_text_delta({"type": "response.completed"}, "openai") is None)
check("anthropic delta extraction unchanged through the shared helper",
      lp._sse_text_delta({"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": "t"}},
                         "anthropic") == "t"
      and lp._sse_text_delta({"type": "message_delta", "delta": {}},
                             "anthropic") is None)

_sse = (b'event: response.output_text.delta\n'
        b'data: {"type":"response.output_text.delta","delta":"4"}\n\n'
        b'event: response.completed\n'
        b'data: {"type":"response.completed","response":{"id":"resp_1",'
        b'"model":"gpt-5.4","status":"completed","usage":{"input_tokens":100,'
        b'"input_tokens_details":{"cached_tokens":80},"output_tokens":5,'
        b'"output_tokens_details":{"reasoning_tokens":2},"total_tokens":105}}}\n\n')
_meta = lp._parse_openai_response(_sse)
check("openai response parse: usage + model + text from response.completed",
      _meta["text"] == "4" and _meta["resolved_model"] == "gpt-5.4"
      and _meta["usage"]["input_tokens"] == 100
      and _meta["usage"]["input_tokens_details"]["cached_tokens"] == 80
      and _meta["status"] == "completed" and _meta["error"] is None)
_meta_err = lp._parse_openai_response(b'{"detail":"The model is not supported"}')
check("openai response parse: unframed JSON error body lands in meta.error",
      _meta_err["error"] == {"detail": "The model is not supported"})

check("codex stats surfaced in /_status",
      lp._status_snapshot()["proxy"].get("codex", {}).get("requests") == 0
      and "upstream_openai" in lp._status_snapshot()["proxy"])

# --- codex /_session view: entry cached for the VIEW, never for replay --------
_cbody = {"model": "gpt-5.4", "instructions": "# Personality\nYou are codex.",
          "prompt_cache_key": "pck-1",
          "tools": [{"type": "function", "name": "exec_command",
                     "description": "run a command"},
                    {"type": "web_search"}],
          "input": [
              {"type": "message", "role": "developer",
               "content": [{"type": "input_text",
                            "text": "<permissions instructions>x</permissions>"}]},
              {"type": "message", "role": "user",
               "content": [{"type": "input_text",
                            "text": "<environment_context>cwd</environment_context>"}]},
              {"type": "message", "role": "user",
               "content": [{"type": "input_text", "text": "What is 6*7?"}]},
              {"type": "function_call", "name": "exec_command",
               "arguments": '{"cmd":"echo 42"}', "call_id": "c1"},
              {"type": "function_call_output", "call_id": "c1", "output": "42"},
              {"type": "reasoning", "encrypted_content": "ZZZ",
               "summary": [{"type": "summary_text", "text": "thought"}]}]}
check("_is_openai_body discriminates the wires",
      lp._is_openai_body(_cbody) and not lp._is_openai_body(compact_obj()))
check("prompt-item predicate skips machine <…> context",
      [lp._is_prompt_item_openai(it) for it in _cbody["input"][:3]]
      == [False, False, True])
lp._cache_last_request_openai("sess-cdx-1", _cbody, "/v1/responses")
lp._WRITE_Q.join()
st_cdx = lp._status_snapshot(session="sess-cdx-1")["sessions"][0]
check("codex entry is view-only: never pingable, never awaiting auth",
      st_cdx["pingable"] is False and st_cdx["awaiting_auth"] is False)
code_cdx, res_cdx = asyncio.run(lp._warm_session("sess-cdx-1", force=True))
check("pinger declines openai entries even with force=1",
      code_cdx == 200 and res_cdx.get("skipped") == "openai_wire")
row_cdx = lp._load_last_request_row("sess-cdx-1")
check("restored codex row needs no auth (view-only)",
      row_cdx is not None and row_cdx["needs_auth"] is False)
lp._LAST_RESPONSE["sess-cdx-1"] = {"text": "42", "truncated": False,
                                   "stop_reason": "completed",
                                   "ts": time.time() + 1}
page_cdx = lp._render_session_html(
    "sess-cdx-1", lp._LAST_REQUEST["sess-cdx-1"],
    lp._status_snapshot(session="sess-cdx-1"),
    resp=lp._LAST_RESPONSE["sess-cdx-1"])
check("/_session renders the codex payload end to end",
      "openai wire" in page_cdx and "Personality" in page_cdx
      and "exec_command" in page_cdx and "What is 6*7?" in page_cdx
      and "function_call_output" in page_cdx and 'id="turn-1"' in page_cdx
      and "assistant (response)" in page_cdx and "reasoning" in page_cdx
      and "awaiting auth" not in page_cdx)

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("ALL PASS")
