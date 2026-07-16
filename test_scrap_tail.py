#!/usr/bin/env python3
"""Offline tests for transforms._downshift_scrap_tail — the 5m scrap-tail write.

On an L1 (prior-thinking-stripping) session, the current turn's frontier —
thinking + tool_results accreting PAST the settled boundary — is guaranteed to be
stripped and rewritten next turn, so caching it at the 1h premium buys durability
that is discarded before it is ever collected. The transform downshifts the
rolling tail marker to 5m in exactly that case, while PRESERVING 1h on:
  - the turn-start / at-boundary write (the durable anchor the pin reads back),
  - every non-strip session (frontier not doomed).

Invariants asserted:
  (a) GATE: fires only when the session's strip level >= 1.
  (b) SPLIT: tail_idx > boundary -> 5m; tail_idx <= boundary -> stays 1h.
  (c) ORDERING: after the downshift, every earlier marker stays 1h and only the
      single highest-index (tail) marker is 5m (API: longer-TTL markers first).
  (d) IDEMPOTENT: a second pass no-ops (already_5m).
"""
import json
import sys

from proxylab import transforms as t

t.SCRAP_TAIL_5M = True

SID = "sess-scrap-tail-test"


def _obj(frontier):
    """Build a request body carrying the test session id. messages:
      0 user  'bundle'        (1h, sys-bundle marker)
      1 asst  text
      2 user  'pin boundary'  (1h) <- last real user turn == settled boundary
    frontier=True appends:
      3 asst  thinking + tool_use
      4 user  tool_result     (1h tail marker, index 4 > boundary 2)
    """
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "bundle",
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
        {"role": "user", "content": [
            {"type": "text", "text": "pin boundary",
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}]},
    ]
    if frontier:
        msgs += [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "x"},
                {"type": "tool_use", "id": "u", "name": "Bash", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "u", "content": "r",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}}]},
        ]
    return {"metadata": {"user_id": json.dumps({"session_id": SID})},
            "messages": msgs}


def _markers(o):
    return [(i, b.get("type"), b["cache_control"].get("ttl"))
            for i, m in enumerate(o["messages"])
            for b in m["content"]
            if isinstance(b, dict) and b.get("cache_control")]


def _set_level(n):
    t._STRIP_OVERRIDE[SID] = n


def _cleanup():
    t._STRIP_OVERRIDE.pop(SID, None)
    t._STRIP_GUARD_LATCH.pop(SID, None)


def test_frontier_downshifted_on_l1():
    _set_level(1)
    o = _obj(frontier=True)
    log = t._downshift_scrap_tail(o)
    assert log and log["downshifted"], f"should downshift frontier tail: {log}"
    mk = _markers(o)
    assert mk[-1][2] == "5m", f"tail must be 5m: {mk}"
    assert all(x[2] == "1h" for x in mk[:-1]), f"earlier markers must stay 1h: {mk}"
    _cleanup()
    print("ok  frontier tail (tail_idx>boundary) -> 5m, ordering legal")


def test_boundary_write_stays_1h():
    # turn-start: no frontier, the tail marker is AT the settled boundary (msg 2).
    _set_level(1)
    o = _obj(frontier=False)
    log = t._downshift_scrap_tail(o)
    assert log and not log["downshifted"] and log["reason"] == "tail_at_durable_boundary", log
    assert _markers(o)[-1][2] == "1h", "durable boundary write must remain 1h"
    _cleanup()
    print("ok  at-boundary durable write stays 1h (eviction trap avoided)")


def test_non_strip_session_untouched():
    _set_level(0)                         # L0: not stripping
    o = _obj(frontier=True)
    log = t._downshift_scrap_tail(o)
    assert log is None, f"non-strip session must be a no-op: {log}"
    assert _markers(o)[-1][2] == "1h", "CLI's 1h tail must be preserved"
    _cleanup()
    print("ok  non-strip (L0) session untouched")


def test_idempotent():
    _set_level(1)
    o = _obj(frontier=True)
    t._downshift_scrap_tail(o)
    log2 = t._downshift_scrap_tail(o)
    assert log2 and log2["reason"] == "already_5m", log2
    _cleanup()
    print("ok  idempotent (second pass no-ops)")


def test_kill_switch():
    t.SCRAP_TAIL_5M = False
    _set_level(1)
    o = _obj(frontier=True)
    assert t._downshift_scrap_tail(o) is None, "kill-switch off must no-op"
    assert _markers(o)[-1][2] == "1h"
    t.SCRAP_TAIL_5M = True
    _cleanup()
    print("ok  SCRAP_TAIL_5M kill-switch")


if __name__ == "__main__":
    test_frontier_downshifted_on_l1()
    test_boundary_write_stays_1h()
    test_non_strip_session_untouched()
    test_idempotent()
    test_kill_switch()
    print("\nALL SCRAP-TAIL TESTS PASSED")
