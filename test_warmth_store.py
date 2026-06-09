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

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("ALL PASS")
