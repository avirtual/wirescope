#!/usr/bin/env python3
"""analyze_busts.py: the corpus bust ledger prices what bust_series finds and
names the proxy action behind it.

Guards three things a reader of the ledger relies on: (1) marginal pricing is
(paid - read) at the receipt's own TTL, not the gross write; (2) the pattern
key folds digits so two instances of one habit share a row; (3) the proxy-state
join reads the capture's transform logs and a request that shipped with no
message marker is named as such, because that is the class the ledger exists
to surface.
"""
import json, os, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_busts as ab  # noqa: E402

PASS = FAIL = 0


def ck(name, got, want=True):
    global PASS, FAIL
    ok = (got == want)
    PASS += ok
    FAIL += (not ok)
    print(("  ok  " if ok else "  FAIL") + f" {name}" + ("" if ok else f"  got={got!r} want={want!r}"))


class _Billing:
    def _price_for(self, model, speed=None):
        return {"in": 10.0, "out": 50.0, "cache_write_5m": 12.5, "cache_write_1h": 20.0,
                "cache_read": 0.25} if model else None


def test_marginal_is_paid_minus_read_at_the_receipt_ttl():
    t = {"write_tokens": 100_000, "uncached_input": 0}
    rc = {"billing": {"model": "claude-fable-5-1", "tokens": {"cache_write_1h_tokens": 100_000}}}
    m, g, ttl = ab.price_bust(t, rc, _Billing())
    ck("1h ttl read off the receipt", ttl, "1h")
    ck("gross = write at the 1h rate", g, 2.0)
    ck("marginal = write x (1h - read)", m, round(100_000 * (20.0 - 0.25) / 1e6, 4))
    t2 = {"write_tokens": 0, "uncached_input": 100_000}
    m2, _, _ = ab.price_bust(t2, rc, _Billing())
    ck("uncached re-read priced at (in - read)", m2, round(100_000 * (10.0 - 0.25) / 1e6, 4))
    ck("unpriced model -> None, never 0", ab.price_bust(t, {"billing": {}}, _Billing())[0], None)


def test_pattern_key_folds_digits_and_restart():
    a = {"class": "preamble", "restart_between": False,
         "locus": {"label": "messages[0].user text", "old": "date is 2026-09-07", "new": "date is 2026-09-08"}}
    b = {"class": "preamble", "restart_between": False,
         "locus": {"label": "messages[0].user text", "old": "date is 2026-09-08", "new": "date is 2026-09-09"}}
    ck("two days of the same habit share a key", ab.pattern_key(a), ab.pattern_key(b))
    c = dict(a, restart_between=True)
    ck("a restart-straddling bust is its own bucket", ab.pattern_key(c)[0], "preamble+restart")


def test_proxy_state_names_the_unanchored_shape():
    rec = {"body": {"messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]},
           "midturn_marker_gate": {"acted": True, "mode": "dropped"},
           "pin_settled_breakpoint": {"pinned": False, "reason": "budget_full_no_donor"}}
    tag, note = ab._proxy_state(rec)
    ck("gate drop that leaves no marker is named", tag, "gate_left_no_marker")
    ck("the note carries the pin reason", "budget_full_no_donor" in note)
    rec2 = {"body": {"messages": [{"role": "user", "content": [{"type": "text", "text": "x",
                                                                 "cache_control": {"type": "ephemeral"}}]}]},
            "midturn_marker_gate": {"acted": True, "mode": "dropped"}}
    ck("a drop that still leaves an anchor is not flagged", ab._proxy_state(rec2)[0], None)
    rec3 = {"body": {"messages": [{"role": "user", "content": "x"}]},
            "strip_compact_cache": {"compact": True, "condition_met": True, "warmth_state": "absent"}}
    ck("a compact-strip firing is named first", ab._proxy_state(rec3)[0], "compact_strip_fired")
    rec4 = {"body": {"messages": [{"role": "user", "content": "x"}]},
            "midturn_marker_gate": {"acted": True, "mode": "dropped_rebased"}}
    ck("the rebased shape is recognised", ab._proxy_state(rec4)[0], "gate_rebased")


def _ping_row(agent, mtime, after, gap, usd=0.1, **kw):
    return {"agent": agent, "session": "s1", "stem": f"{int(mtime)}-x", "mtime": mtime,
            "after": after, "gap_s": gap, "est_usd": usd, "ttl": 300, "read_tokens": 100_000,
            "uncached_input": 2000, "write_tokens": 0, "dirty": False, "status": 200,
            "model": "claude-fable-5-1", "read_usd": 0.1, "uncached_usd": 0.02,
            "write_usd": 0.0, **kw}


def test_agent_of_keeps_the_dashed_route_name():
    ck("route name with dashes survives", ab._agent_of("4646-clodex-wirescope-597e9059-parent-fable-5-1-011932"),
       "clodex-wirescope-597e9059")
    ck("a subagent stem too", ab._agent_of("12-clodex-clodex.t735.review-r2-560cda28-subagent-opus-5-212842"),
       "clodex-clodex.t735.review-r2-560cda28")
    ck("no role token: first segment", ab._agent_of("7-a-x"), "a")


def test_ping_loops_need_tick_spacing_and_no_turn_between():
    t0 = 1_000_000.0
    # six pings 60s apart, each after a ping -> one loop
    rows = [_ping_row("a", t0, "turn", 3000)] + [
        _ping_row("a", t0 + 60 * i, "ping", 60) for i in range(1, 6)]
    loops = ab.ping_loops(rows)
    ck("a 60s run of six pings is one loop", len(loops), 1)
    ck("the loop holds all six", len(loops[0][2]), 6)
    # hourly pings (a healthy 1h hold) are not a loop
    hourly = [_ping_row("b", t0 + 3360 * i, "ping" if i else "turn", 3360) for i in range(8)]
    ck("56-minute spacing is a hold doing its job, not a loop", ab.ping_loops(hourly), [])
    # a real turn in the middle splits the run below the minimum
    split = [_ping_row("c", t0 + 60 * i, "ping" if i not in (0, 3) else "turn", 60)
             for i in range(6)]
    ck("an organic turn between pings breaks the run", ab.ping_loops(split), [])


def test_ping_capture_detection_reads_flag_then_shape():
    d = Path(tempfile.mkdtemp())
    try:
        flagged = d / "1-seat-x.request.json"
        flagged.write_text(json.dumps({"seq": 1, "body": {"max_tokens": 1, "tools": [{"name": "Bash"}],
                                                          "messages": []},
                                       "summary": {"keepwarm": True, "n_tools": 1}}))
        legacy = d / "2-seat-x.request.json"
        legacy.write_text(json.dumps({"seq": 2, "body": {"max_tokens": 1, "tools": [{"name": "Bash"}],
                                                         "messages": []},
                                      "summary": {"n_tools": 1}}))
        turn = d / "3-seat-x.request.json"
        turn.write_text(json.dumps({"seq": 3, "body": {"max_tokens": 32000, "tools": [{"name": "Bash"}],
                                                       "messages": []},
                                    "summary": {"n_tools": 1}}))
        probe = d / "4-seat-x.request.json"
        probe.write_text(json.dumps({"seq": 4, "body": {"max_tokens": 1, "messages": [{"role": "user", "content": "quota"}]},
                                     "summary": {"n_tools": 0}}))
        from proxylab import report
        ck("v0.6.65 capture: the summary flag decides", ab._is_ping_capture(flagged, report)[0], True)
        ck("pre-flag capture: max_tokens:1 + tools off the head", ab._is_ping_capture(legacy, report)[0], True)
        ck("a real turn is not a ping", ab._is_ping_capture(turn, report)[0], False)
        ck("the tool-less quota probe is not a ping", ab._is_ping_capture(probe, report)[0], False)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_marginal_is_paid_minus_read_at_the_receipt_ttl,
              test_pattern_key_folds_digits_and_restart,
              test_proxy_state_names_the_unanchored_shape,
              test_agent_of_keeps_the_dashed_route_name,
              test_ping_loops_need_tick_spacing_and_no_turn_between,
              test_ping_capture_detection_reads_flag_then_shape):
        print(f"=== {t.__name__} ===")
        t()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
