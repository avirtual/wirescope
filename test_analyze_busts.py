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


if __name__ == "__main__":
    for t in (test_marginal_is_paid_minus_read_at_the_receipt_ttl,
              test_pattern_key_folds_digits_and_restart,
              test_proxy_state_names_the_unanchored_shape):
        print(f"=== {t.__name__} ===")
        t()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
