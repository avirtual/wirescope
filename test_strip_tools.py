#!/usr/bin/env python3
"""Offline tests for transforms._strip_tools_global — the global exact-name
tool strip. Sibling of _strip_mcp_tools (which keys on the mcp__ prefix); this
one drops tools[] entries by EXACT name for every routed request, for tools the
CLI force-includes past every native filter (EndConversation: survives
`--tools bash`, and settings permissions.deny leaves it on the wire).

Invariants asserted:
  (a) GATE: fires only when STRIP_TOOLS_GLOBAL is non-empty.
  (b) EXACT-NAME + case-insensitive drop; other tools untouched.
  (c) FAIL-SAFE MISS: configured but absent -> logged miss, never over-strips.
  (d) RE-ADMIT: [wirescope:keep-tools <name>] cancels the drop for that agent.
  (e) IDEMPOTENT: a second pass no-ops (target already gone -> miss).
"""
from proxylab import transforms as t


def _obj(names):
    return {"tools": [{"name": n, "description": "x " * 50,
                       "input_schema": {"type": "object", "properties": {}}}
                      for n in names]}


def _names(o):
    return [x["name"] for x in o["tools"]]


def test_strips_exact_name():
    t.STRIP_TOOLS_GLOBAL = frozenset({"endconversation"})
    o = _obj(["Bash", "EndConversation", "Read"])
    log = t._strip_tools_global(o)
    assert log and log["removed"] == ["EndConversation"], log
    assert _names(o) == ["Bash", "Read"], _names(o)
    print("ok  strips EndConversation by exact name, keeps the rest")


def test_case_insensitive():
    t.STRIP_TOOLS_GLOBAL = frozenset({"endconversation"})
    o = _obj(["endconversation", "Bash"])          # differing case on the wire
    log = t._strip_tools_global(o)
    assert log and log["removed"] == ["endconversation"], log
    print("ok  case-insensitive match")


def test_miss_when_absent():
    t.STRIP_TOOLS_GLOBAL = frozenset({"endconversation"})
    o = _obj(["Bash", "Read"])
    log = t._strip_tools_global(o)
    assert log and log.get("miss") and log["removed"] == [], log
    assert _names(o) == ["Bash", "Read"]            # untouched
    print("ok  fail-safe miss (configured but absent -> no over-strip)")


def test_keep_tools_readmit():
    t.STRIP_TOOLS_GLOBAL = frozenset({"endconversation"})
    o = _obj(["Bash", "EndConversation"])
    o["messages"] = [{"role": "user", "content": [
        {"type": "text", "text": "[wirescope:keep-tools EndConversation]\nhi"}]}]
    log = t._strip_tools_global(o, agent_id="a1")
    assert log is None, ("re-admit should no-op (every target re-admitted)", log)
    assert "EndConversation" in _names(o)
    print("ok  [wirescope:keep-tools] re-admits, no strip")


def test_idempotent():
    t.STRIP_TOOLS_GLOBAL = frozenset({"endconversation"})
    o = _obj(["Bash", "EndConversation"])
    t._strip_tools_global(o)
    log2 = t._strip_tools_global(o)                 # target already gone
    assert log2 and log2.get("miss"), log2
    print("ok  idempotent (second pass is a miss)")


def test_kill_switch():
    t.STRIP_TOOLS_GLOBAL = frozenset()
    o = _obj(["Bash", "EndConversation"])
    assert t._strip_tools_global(o) is None, "empty set must no-op"
    assert "EndConversation" in _names(o)
    print("ok  kill switch (empty STRIP_TOOLS_GLOBAL) -> no-op")


if __name__ == "__main__":
    try:
        test_strips_exact_name()
        test_case_insensitive()
        test_miss_when_absent()
        test_keep_tools_readmit()
        test_idempotent()
        test_kill_switch()
        print("\nALL STRIP-TOOLS TESTS PASSED")
    finally:
        t.STRIP_TOOLS_GLOBAL = frozenset()          # leave the module clean
