#!/usr/bin/env python3
"""Mid-turn strip + marker gate — the 2026-07-20 redesign (Bogdan's placement
rule, replacing the op-1 pin): doomed thinking is NEVER cached. Per request:
strip every consumed thinking block (everything but the last assistant
message); then, if the last assistant message still carries thinking, pull the
CLI's rolling tail marker back to the last stable message so the doomed block
is read once at 1x instead of written at premium and invalidated (the AB2
churn). Clean-tail rounds are byte-stock. Invariants: budget never grows, no
marker ever sits above the halt point on a doomed round, 1h anchors are never
touched, strip/gate cannot desync (gate predicate reads only the last
assistant message, which the strip never mutates)."""
import copy
import json

import logproxy as lp
t = lp.transforms

CHECKS = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    CHECKS["pass" if cond else "fail"] += 1
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f"  ({detail})" if detail and not cond else ""))


def think_msg(n, chars=500):
    return {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "x" * chars, "signature": "s" * 8},
        {"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {"command": "true"}}]}


def plain_msg(n):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {"command": "true"}}]}


def result_msg(n):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{n}", "content": "ok"}]}


def turn(user_text, rounds):
    """rounds: list of bools — True = thinking round, False = plain round."""
    msgs = [{"role": "user", "content": user_text}]
    for k, th in enumerate(rounds):
        msgs.append(think_msg(k) if th else plain_msg(k))
        msgs.append(result_msg(k))
    return msgs


def body(msgs, sys_markers=2, msg_markers=()):
    """msg_markers: (idx, ttl) pairs -> cache_control on that message's last block."""
    b = {"model": "claude-opus-4-8",
         "system": [{"type": "text", "text": "sys0"}] +
                   [{"type": "text", "text": f"sys{i+1}",
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}}
                    for i in range(sys_markers)],
         "messages": copy.deepcopy(msgs)}
    for idx, ttl in msg_markers:
        m = b["messages"][idx]
        if isinstance(m.get("content"), str):
            m["content"] = [{"type": "text", "text": m["content"]}]
        m["content"][-1]["cache_control"] = {"type": "ephemeral", "ttl": ttl}
    return b


def n_thinking(msgs):
    return sum(1 for m in msgs for blk in (m.get("content") or [])
               if isinstance(blk, dict) and blk.get("type") == "thinking")


def msg_markers_of(b):
    return [(i, blk["cache_control"]["ttl"]) for i, m in enumerate(b["messages"])
            for blk in (m.get("content") if isinstance(m.get("content"), list) else [])
            if isinstance(blk, dict) and blk.get("cache_control")]


# force the L1 gate on for the suite (kill switches already default-on)
t.STRIP_PRIOR_THINKING = True
t.STRIP_MIDTURN_THINKING = True
t.MIDTURN_MARKER_GATE = True

print("== strip: dense turn — all consumed blocks go, last assistant protected ==")
msgs = turn("task", [True, True, True, True])   # thinking every round
b = body(msgs)
rec = t._strip_midturn_thinking(b)
check("dense strips", rec and rec.get("stripped") is True)
check("3 of 4 removed (last assistant protected)", rec and rec["removed_thinking_blocks"] == 3, str(rec))
check("protected block survives", n_thinking(b["messages"]) == 1)
check("protected_idx = last assistant", rec and rec.get("protected_idx") == len(msgs) - 2, str(rec))

print("== strip: clean tail strips EVERYTHING (consumed = deletable) ==")
msgs = turn("task", [True, True, True, False])  # final round plain
b = body(msgs)
rec = t._strip_midturn_thinking(b)
check("all 3 removed", rec and rec["removed_thinking_blocks"] == 3, str(rec))
check("no thinking left", n_thinking(b["messages"]) == 0)
check("protected_idx None (clean last assistant)", rec and rec.get("protected_idx") is None, str(rec))

print("== strip: sparse turn strips too (texture gate retired) ==")
msgs = turn("task", [True] + [False] * 9 + [True])   # old sparse_turn shape
b = body(msgs)
rec = t._strip_midturn_thinking(b)
check("sparse strips now", rec and rec.get("stripped") is True
      and rec["removed_thinking_blocks"] == 1, str(rec))
check("only the protected head remains", n_thinking(b["messages"]) == 1)

print("== strip: single thinking block at the head -> nothing consumed yet ==")
msgs = turn("task", [True])
b = body(msgs)
check("declines (None)", t._strip_midturn_thinking(b) is None)
check("body untouched", n_thinking(b["messages"]) == 1)

print("== strip: determinism + monotonicity across rounds ==")
msgs_k = turn("task", [True, True, True])
msgs_k1 = turn("task", [True, True, True, True])       # CLI resends everything + new round
bk, bk1 = body(msgs_k), body(msgs_k1)
t._strip_midturn_thinking(bk)
t._strip_midturn_thinking(bk1)
common = len(bk["messages"]) - 2   # everything before round k's head msg + result
same = all(json.dumps(bk["messages"][i], sort_keys=True) ==
           json.dumps(bk1["messages"][i], sort_keys=True) for i in range(common))
check("common prefix byte-stable", same)
check("k's head stripped in k+1 (monotone)",
      n_thinking(bk1["messages"]) == 1 and n_thinking(bk["messages"]) == 1)

print("== gate: doomed round RELOCATES the tail marker below the block ==")
msgs = turn("task", [True, True])
last = len(msgs) - 1                     # tool_result tail
prot = last - 1                          # thinking-bearing last assistant
b = body(msgs, msg_markers=((last, "1h"),))
rec = t._midturn_marker_gate(b)
check("acted + relocated", rec and rec.get("acted") and rec.get("mode") == "relocated", str(rec))
check("halt just below the doomed block", rec and rec.get("halt_idx") == prot - 1, str(rec))
mm = msg_markers_of(b)
check("single 5m marker at halt, tail gone", mm == [(prot - 1, "5m")], str(mm))

print("== gate: clean tail is byte-stock (None, marker untouched) ==")
msgs = turn("task", [True, False])
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
snap = json.dumps(b, sort_keys=True)
check("gate None", t._midturn_marker_gate(b) is None)
check("body untouched", json.dumps(b, sort_keys=True) == snap)

print("== gate: first thinking round -> marker DROPPED (anchor covers stable) ==")
msgs = turn("task", [True])              # halt == boundary
b = body(msgs, msg_markers=((0, "1h"), (len(msgs) - 1, "1h")))   # anchor + tail
rec = t._midturn_marker_gate(b)
check("dropped mode", rec and rec.get("acted") and rec.get("mode") == "dropped", str(rec))
mm = msg_markers_of(b)
check("only the boundary anchor remains", mm == [(0, "1h")], str(mm))

print("== gate: transition request (no assistant past boundary) -> stock ==")
msgs = turn("task", [True, True]) + [{"role": "user", "content": "next task"}]
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
check("gate None at transition", t._midturn_marker_gate(b) is None)

print("== gate: every marker above the halt is removed (no doomed bytes cached) ==")
msgs = turn("task", [True, True, True])
last = len(msgs) - 1
prot = last - 1
b = body(msgs, msg_markers=((prot, "5m"), (last, "1h")))   # stray marker ON the doomed msg
rec = t._midturn_marker_gate(b)
check("both dropped, one placed", rec and rec.get("markers_dropped") == 2, str(rec))
mm = msg_markers_of(b)
check("only halt marked", mm == [(prot - 1, "5m")], str(mm))

print("== gate: 1h anchor below halt is never touched; ordering stays legal ==")
msgs = turn("task", [True, True, True])
last = len(msgs) - 1
b = body(msgs, msg_markers=((0, "1h"), (last, "1h")))
rec = t._midturn_marker_gate(b)
check("relocated", rec and rec.get("mode") == "relocated", str(rec))
mm = msg_markers_of(b)
check("anchor 1h below, halt 5m above (legal order)",
      mm == [(0, "1h"), (last - 2, "5m")], str(mm))

print("== gate: halt already marked -> tail dropped, no duplicate ==")
msgs = turn("task", [True, True])
last = len(msgs) - 1
prot = last - 1
b = body(msgs, msg_markers=((prot - 1, "5m"), (last, "1h")))
rec = t._midturn_marker_gate(b)
check("dropped w/ reason", rec and rec.get("mode") == "dropped"
      and rec.get("reason") == "halt_already_marked", str(rec))
mm = msg_markers_of(b)
check("single marker at halt", mm == [(prot - 1, "5m")], str(mm))

print("== gate: budget never grows ==")
for rounds, markers in (([True, True], ((3, "1h"), (4, "1h"))),
                        ([True, True, True], ((0, "1h"), (6, "1h"))),
                        ([True], ((2, "1h"),))):
    msgs = turn("task", rounds)
    b = body(msgs, msg_markers=markers)
    before = len(msg_markers_of(b))
    t._midturn_marker_gate(b)
    check(f"markers {before} -> {len(msg_markers_of(b))} (<=)",
          len(msg_markers_of(b)) <= before)

print("== gate: string-content halt converts to a text block ==")
msgs = [{"role": "user", "content": "task"},
        think_msg(0), result_msg(0),
        {"role": "assistant", "content": "interim answer"},   # string content
        think_msg(1), result_msg(1)]
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
rec = t._midturn_marker_gate(b)
check("relocated w/ conversion", rec and rec.get("mode") == "relocated"
      and rec.get("converted_string") is True, str(rec))
halt = b["messages"][rec["halt_idx"]]
check("converted shape", isinstance(halt["content"], list)
      and halt["content"][0]["type"] == "text"
      and halt["content"][-1].get("cache_control") == {"type": "ephemeral", "ttl": "5m"})

print("== live order: strip mutates first, gate unaffected (4a0521e class dead) ==")
msgs = turn("task", [True, True, True, True])
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
rec = t._strip_midturn_thinking(b)
check("strip fired first", rec and rec.get("stripped") is True)
g = t._midturn_marker_gate(b)
check("gate still fires on the stripped body", g and g.get("acted")
      and g.get("mode") == "relocated", str(g))
msgs = turn("task", [True, True, False])
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
rec = t._strip_midturn_thinking(b)
check("clean-tail strip removed all", rec and rec["removed_thinking_blocks"] == 2)
check("gate stock on clean tail post-strip", t._midturn_marker_gate(b) is None)

print("== kill switches ==")
msgs = turn("task", [True, True])
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
t.MIDTURN_MARKER_GATE = False
check("gate off -> None", t._midturn_marker_gate(b) is None)
t.MIDTURN_MARKER_GATE = True
t.STRIP_MIDTURN_THINKING = False
check("strip kill also silences the gate", t._midturn_marker_gate(b) is None)
check("strip off -> None", t._strip_midturn_thinking(b) is None)
t.STRIP_MIDTURN_THINKING = True

print(f"\n{CHECKS['pass']} passed, {CHECKS['fail']} failed")
raise SystemExit(1 if CHECKS["fail"] else 0)
