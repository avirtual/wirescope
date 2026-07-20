#!/usr/bin/env python3
"""Offline tests for the 4-marker budget discipline (the vendored-clodex
5-marker 400, 2026-07-19). HARD RULE: never forward more than 4 cache_control
markers — the API rejects a 5th and the user's turn dies.

Three cooperating pieces:
  - transforms._relocate_env_to_tail step 4 is BUDGET-AWARE: at <4 markers it
    ADDS the CLAUDE.md bundle marker (today's behavior); at 4 it MIGRATES the
    earliest non-tail client message marker onto the bundle; no donor -> SKIPS
    the marker (relocation itself still applies).
  - transforms._pin_settled_breakpoint already migrates at 4 (pre-existing).
  - transforms._enforce_marker_budget is the unconditional final clamp.

Invariants asserted:
  (a) 3-marker client (the normal wire): relocate ADDS -> 4 total, never 5.
  (b) 4-marker org-route client (2 sys + 2 msg): relocate MIGRATES the non-tail
      message marker -> still 4, bundle marked, tail + system untouched.
  (c) 4-marker client, no message donor (2 sys + tools + tail): relocate SKIPS
      -> still 4, relocation (env peel) still applied.
  (d) already-marked bundle: relocate leaves it, no add, no migrate.
  (e) clamp: 5-marker layout -> drops earliest non-tail message marker first;
      never the last system marker, never the rolling tail.
  (f) clamp preference order: message -> tools -> earlier system.
  (g) clamp no-op at <=4 (returns None, object untouched).
  (h) end-to-end: relocate + pin on a 4-marker client stays at 4.
"""
import copy
import json

from proxylab import transforms as t


def _count(obj):
    return len(t._cache_markers(obj))


def _cc(ttl="1h"):
    return {"type": "ephemeral", "ttl": ttl}


def _base(sys_markers=2, msg_markers=1, tools_marker=False, n_msgs=6):
    """A CLI-shaped request: system[0] billing header (unmarked), system[1]
    preamble + system[2] prompt (marked per sys_markers), a claudeMd bundle in
    messages[0], alternating user/assistant history, marker(s) on the tail
    (and optionally one mid-history) per msg_markers."""
    sysb = [{"type": "text", "text": "x-anthropic-billing-header: cc"},
            {"type": "text", "text": "You are Claude Code."},
            {"type": "text", "text": "Big prompt.\n# Environment\ncwd: /tmp\n# Other\nprose"}]
    if sys_markers >= 1:
        sysb[1]["cache_control"] = _cc()
    if sys_markers >= 2:
        sysb[2]["cache_control"] = _cc()
    tools = [{"name": "Bash", "description": "run", "input_schema": {}}]
    if tools_marker:
        tools[0]["cache_control"] = _cc()
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "<system-reminder>\n# claudeMd\nContents of "
                                 "/tmp/CLAUDE.md\nrules rules rules\n</system-reminder>"}]}]
    for i in range(1, n_msgs):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": [{"type": "text", "text": f"turn {i}"}]})
    # tail marker (the CLI's rolling one) on the LAST message
    if msg_markers >= 1:
        msgs[-1]["content"][-1]["cache_control"] = _cc()
    # a second, mid-history message marker (the org-route "two rolling markers")
    if msg_markers >= 2:
        msgs[2]["content"][-1]["cache_control"] = _cc()
    return {"model": "claude-sonnet-5", "system": sysb, "tools": tools,
            "messages": msgs}


def test_add_at_three():
    o = _base(sys_markers=2, msg_markers=1)
    assert _count(o) == 3
    log = t._relocate_env_to_tail(o)
    assert log and log["bundle_marker"] == "added", log
    assert _count(o) == 4, _count(o)
    assert o["messages"][0]["content"][0].get("cache_control"), "bundle unmarked"
    print("ok  3-marker client: bundle marker ADDED -> 4 total")


def test_migrate_at_four():
    o = _base(sys_markers=2, msg_markers=2)
    assert _count(o) == 4
    log = t._relocate_env_to_tail(o)
    assert log and log["bundle_marker"] == "migrated", log
    assert _count(o) == 4, f"budget violated: {_count(o)}"
    assert o["messages"][0]["content"][0].get("cache_control"), "bundle unmarked"
    assert not o["messages"][2]["content"][-1].get("cache_control"), "donor kept its marker"
    assert o["messages"][-1]["content"][-1].get("cache_control"), "tail was stolen"
    assert o["system"][1].get("cache_control") and o["system"][2].get("cache_control"), \
        "system marker was stolen"
    print("ok  4-marker client: donor message marker MIGRATED onto bundle, still 4")


def test_skip_no_donor():
    o = _base(sys_markers=2, msg_markers=1, tools_marker=True)
    assert _count(o) == 4
    log = t._relocate_env_to_tail(o)
    assert log and log["bundle_marker"] == "skipped_budget_full", log
    assert _count(o) == 4, f"budget violated: {_count(o)}"
    assert not o["messages"][0]["content"][0].get("cache_control"), "bundle got a 5th marker"
    assert "# Environment" not in o["system"][2]["text"], "env peel skipped too (should still apply)"
    print("ok  4-marker client, no donor: bundle marker SKIPPED, relocation still applied")


def test_already_marked_bundle():
    o = _base(sys_markers=2, msg_markers=1)
    o["messages"][0]["content"][0]["cache_control"] = _cc()
    before = _count(o)
    log = t._relocate_env_to_tail(o)
    assert log and log["bundle_marker"] == "already_marked", log
    assert _count(o) == before, "marker count changed on already-marked bundle"
    print("ok  already-marked bundle: left alone")


def test_clamp_drops_message_first():
    o = _base(sys_markers=2, msg_markers=2)
    o["messages"][0]["content"][0]["cache_control"] = _cc()   # simulate a blind 5th
    assert _count(o) == 5
    log = t._enforce_marker_budget(o)
    assert log and log["markers_before"] == 5, log
    assert _count(o) == 4, f"clamp failed: {_count(o)}"
    assert log["dropped"][0]["region"] == "messages", log
    assert o["messages"][-1]["content"][-1].get("cache_control"), "clamp dropped the tail"
    assert o["system"][1].get("cache_control") and o["system"][2].get("cache_control"), \
        "clamp dropped a system marker while messages were available"
    print("ok  clamp: 5 -> 4, earliest non-tail message marker dropped first")


def test_clamp_preference_order():
    # 2 sys + tools + 2 msg (tail + mid) = 5: drop must take the mid-history
    # message marker, not tools, not system, not the tail.
    o = _base(sys_markers=2, msg_markers=2, tools_marker=True)
    assert _count(o) == 5
    t._enforce_marker_budget(o)
    assert _count(o) == 4
    assert o["tools"][0].get("cache_control"), "tools dropped before message"
    assert not o["messages"][2]["content"][-1].get("cache_control"), "mid msg marker survived"
    # now 2 sys + tools + tail = 4 plus a forced extra tools marker = 5:
    # with no droppable message marker, tools goes next
    o["tools"].append({"name": "Read", "description": "r", "input_schema": {},
                      "cache_control": _cc()})
    assert _count(o) == 5
    t._enforce_marker_budget(o)
    assert _count(o) == 4
    assert o["system"][1].get("cache_control") and o["system"][2].get("cache_control"), \
        "system dropped before tools"
    print("ok  clamp preference: messages -> tools -> system, tail sacred")


def test_clamp_noop_within_budget():
    o = _base(sys_markers=2, msg_markers=1)
    snap = json.dumps(o, sort_keys=True)
    assert t._enforce_marker_budget(o) is None
    assert json.dumps(o, sort_keys=True) == snap, "clamp mutated a within-budget request"
    print("ok  clamp: no-op at <=4, object untouched")


def test_end_to_end_four_marker_client():
    # the exact vendored-clodex scenario: org-route client at 4, full message
    # transform sequence (relocate then pin) must land on exactly 4.
    o = _base(sys_markers=2, msg_markers=2, n_msgs=8)
    assert _count(o) == 4
    rel = t._relocate_env_to_tail(o)
    assert rel and rel["bundle_marker"] in ("migrated", "skipped_budget_full"), rel
    assert _count(o) <= 4
    t._pin_settled_breakpoint(o)
    assert _count(o) <= 4, f"pin overflowed: {_count(o)}"
    assert t._enforce_marker_budget(o) is None, "chain relied on the clamp"
    print("ok  end-to-end (relocate + pin) on 4-marker client: exactly <=4, clamp idle")


def test_pin_advances_in_turn():
    # mid-turn shape (assistant past the boundary, boundary unmarked): the pin
    # anchors the CURRENT turn's opening, not the penultimate boundary
    # (anchor-advance, 2026-07-20 — closes the full-turn 1h-coverage lag).
    o = _base(sys_markers=2, msg_markers=1, n_msgs=6)   # last=assistant+marker
    log = t._pin_settled_breakpoint(o)
    assert log and log["pinned"] and log["advanced"] is True, log
    assert log["anchor_idx"] == log["boundary_idx"] == 4, log
    assert o["messages"][4]["content"][-1].get("cache_control"), "boundary unpinned"
    print("ok  pin ADVANCES to the current turn's opening on request 2+")


def test_pin_penultimate_at_transition():
    # turn-first shape: the boundary IS the marked tail -> penultimate anchor
    # (the exact-match transition mechanism, unchanged).
    o = _base(sys_markers=2, msg_markers=1, n_msgs=7)   # last=user+marker
    log = t._pin_settled_breakpoint(o)
    assert log and log["pinned"] and log["advanced"] is False, log
    assert log["boundary_idx"] == 6 and log["anchor_idx"] == 4, log
    print("ok  pin stays PENULTIMATE at the turn transition (boundary is the tail)")


def test_pin_ttl_mirrors_prior_not_tail():
    # scrap-tail world: rolling tail at 5m must NOT drag the anchor to 5m —
    # the pin mirrors the last marker BEFORE it (1h system) instead.
    o = _base(sys_markers=2, msg_markers=1, n_msgs=6)
    o["messages"][-1]["content"][-1]["cache_control"] = _cc("5m")
    log = t._pin_settled_breakpoint(o)
    assert log and log["pinned"] and log["advanced"], log
    assert log["ttl"] == "1h", log
    assert _count(o) == 4
    print("ok  advanced pin ttl mirrors prior 1h marker, not the scrapped 5m tail")


if __name__ == "__main__":
    test_add_at_three()
    test_migrate_at_four()
    test_skip_no_donor()
    test_already_marked_bundle()
    test_clamp_drops_message_first()
    test_clamp_preference_order()
    test_clamp_noop_within_budget()
    test_end_to_end_four_marker_client()
    test_pin_advances_in_turn()
    test_pin_penultimate_at_transition()
    test_pin_ttl_mirrors_prior_not_tail()
    print("ALL MARKER-BUDGET TESTS PASSED")
