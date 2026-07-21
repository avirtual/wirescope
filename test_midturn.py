#!/usr/bin/env python3
"""Mid-turn strip + marker gate — the 2026-07-20 redesign (Bogdan's placement
rule, replacing the op-1 pin): doomed thinking is NEVER cached. Per request:
strip every consumed thinking block (everything but the last assistant
message); then, if the last assistant message still carries thinking, pull the
CLI's rolling tail marker back to the last stable message so the doomed block
is read once at 1x instead of written at premium and invalidated (the AB2
churn). The relocated marker KEEPS the CLI's own ttl (2026-07-20 correction:
no 5m of ours anywhere — in-turn stripping discards nothing, the covered span
is durable). Clean-tail rounds are byte-stock. Invariants: budget never
grows, no marker ever sits above the halt point on a doomed round, 1h anchors
are never touched, strip/gate cannot desync (gate predicate reads only the
last assistant message, which the strip never mutates)."""
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
check("relocated ttl inherits the CLI marker's (1h)", rec and rec.get("ttl") == "1h", str(rec))
mm = msg_markers_of(b)
check("single 1h marker at halt, tail gone", mm == [(prot - 1, "1h")], str(mm))

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
check("only halt marked, tail ttl inherited", mm == [(prot - 1, "1h")], str(mm))

print("== gate: 1h anchor below halt is never touched; homogeneous 1h layout ==")
msgs = turn("task", [True, True, True])
last = len(msgs) - 1
b = body(msgs, msg_markers=((0, "1h"), (last, "1h")))
rec = t._midturn_marker_gate(b)
check("relocated", rec and rec.get("mode") == "relocated", str(rec))
mm = msg_markers_of(b)
check("anchor 1h below, halt 1h above (no-strip layout, no 5m)",
      mm == [(0, "1h"), (last - 2, "1h")], str(mm))

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
      and halt["content"][-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"})

print("== gate: subagent-style 5m tail relocates as 5m (ttl travels) ==")
msgs = turn("task", [True, True])
b = body(msgs, msg_markers=((len(msgs) - 1, "5m"),))
rec = t._midturn_marker_gate(b)
check("relocated w/ 5m inherited", rec and rec.get("mode") == "relocated"
      and rec.get("ttl") == "5m", str(rec))

print("== gate: CLI OMITS its tail marker -> proxy places one at halt (seq092) ==")
# The bug: CLI ships no rolling message tail marker, pin_settled declines
# (single user-turn), only the msg0 bundle anchor survives -> cache floors at
# msg0 and the stable in-turn tail re-reads cold. Fallback plants our own marker
# on the last stable message below the live block.
msgs = turn("task", [True, True])            # last assistant (idx 3) carries thinking
b = body(msgs, msg_markers=((0, "1h"),))     # ONLY the bundle anchor, no tail
rec = t._midturn_marker_gate(b)
check("acted + tail_fallback", rec and rec.get("acted")
      and rec.get("mode") == "tail_fallback", str(rec))
check("halt just below the live block (idx 2)", rec and rec.get("halt_idx") == 2, str(rec))
check("ttl mirrors the deepest existing marker (1h)", rec and rec.get("ttl") == "1h", str(rec))
mm = msg_markers_of(b)
check("anchor kept + new tail at halt", mm == [(0, "1h"), (2, "1h")], str(mm))

print("== gate: CLI-omitted tail but halt == boundary -> decline (anchor covers) ==")
msgs = turn("task", [True])                   # halt (0) == boundary
b = body(msgs, msg_markers=((0, "1h"),))
rec = t._midturn_marker_gate(b)
check("declines, no fallback (anchor already covers stable)",
      rec and rec.get("acted") is False
      and rec.get("reason") == "no_inturn_tail_marker", str(rec))
check("no marker added", msg_markers_of(b) == [(0, "1h")], str(msg_markers_of(b)))

print("== gate: CLI-omitted tail, budget FULL, NO donor above boundary -> decline ==")
msgs = turn("task", [True, True])                          # single turn, boundary 0
b = body(msgs, sys_markers=3, msg_markers=((0, "1h"),))    # 3 sys + msg0 = 4; msg0 IS at boundary 0
rec = t._midturn_marker_gate(b)
check("declines (no message marker strictly above boundary to donate)",
      rec and rec.get("acted") is False
      and rec.get("reason") == "no_inturn_tail_marker", str(rec))
check("still 4 total markers (no 5th)", len(t._cache_markers(b)) == 4, str(len(t._cache_markers(b))))

print("== gate: CLEAN-tail CLI-omit at FULL budget -> migrate msg0 bundle to frontier ==")
# two tasks: boundary at the 2nd real user turn (idx3); clean (plain) rounds;
# markers = 2 sys + msg0 bundle + pin@boundary = 4 full, NO rolling tail.
msgs = [{"role": "user", "content": "task1"}, plain_msg(0), result_msg(0),
        {"role": "user", "content": "task2"}, plain_msg(1), result_msg(1)]
b = body(msgs, msg_markers=((0, "1h"), (3, "1h")))
rec = t._midturn_marker_gate(b)
check("clean_tail_fallback acts", rec and rec.get("acted")
      and rec.get("mode") == "clean_tail_fallback", str(rec))
check("donor = msg0 bundle", rec and rec.get("donor_idx") == 0, str(rec))
check("placed on frontier (idx5)", rec and rec.get("tail_idx") == 5, str(rec))
mm = msg_markers_of(b)
check("pin@3 kept + frontier@5, msg0 gone, still 4 total",
      mm == [(3, "1h"), (5, "1h")] and len(t._cache_markers(b)) == 4, str(mm))

print("== gate: LIVE-tail CLI-omit at FULL budget -> migrate msg0 bundle to halt ==")
# same two-task frame but the current round carries thinking (live) -> live branch;
# halt = just below the live block, donor = msg0.
msgs = [{"role": "user", "content": "task1"}, plain_msg(0), result_msg(0),
        {"role": "user", "content": "task2"}, plain_msg(1), result_msg(1),
        think_msg(2), result_msg(2)]
b = body(msgs, msg_markers=((0, "1h"), (3, "1h")))
rec = t._midturn_marker_gate(b)
check("tail_fallback acts (live)", rec and rec.get("acted")
      and rec.get("mode") == "tail_fallback", str(rec))
check("donor = msg0 bundle", rec and rec.get("donor_idx") == 0, str(rec))
check("halt = 5 (below the live thinking at 6)", rec and rec.get("halt_idx") == 5, str(rec))
mm = msg_markers_of(b)
check("pin@3 kept + halt@5, msg0 gone, still 4 total",
      mm == [(3, "1h"), (5, "1h")] and len(t._cache_markers(b)) == 4, str(mm))

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

print("== consumed error strip + live-error marker gate (2026-07-20) ==")
t.STRIP_PRIOR_TOOL_ERRORS = True


def fail_call_msg(n):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"e{n}", "name": "Edit",
         "input": {"old_string": "x" * 120, "new_string": "y" * 120}}]}


def err_result_msg(n):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"e{n}",
         "content": "boom " * 30, "is_error": True}]}


# [0]user [1]fail-call e0 [2]err e0 [3]assistant reaction [4]LIVE err e1
emsgs = [{"role": "user", "content": "task"},
         fail_call_msg(0), err_result_msg(0),
         plain_msg(1), err_result_msg(1)]
b = body(copy.deepcopy(emsgs))
rec = t._strip_consumed_tool_errors(b)
check("consumed strip fires", rec and rec.get("stripped") is True, str(rec))
check("consumed call stubbed (1)", rec and rec["stubbed_failed_calls"] == 1, str(rec))
check("consumed error result stubbed (1)", rec and rec["stubbed_error_results"] == 1, str(rec))
check("consumed call input replaced with stub",
      b["messages"][1]["content"][0]["input"] == t.ERROR_CALL_STUB)
check("consumed error body replaced with marker",
      b["messages"][2]["content"][0]["content"] == t.ERROR_ELIDED_MARKER)
check("LIVE frontier error UNTOUCHED (still the model's retry signal)",
      b["messages"][4]["content"][0]["content"] == "boom " * 30)

# determinism: re-running is a no-op (idempotent, byte-stable)
rec2 = t._strip_consumed_tool_errors(b)
check("consumed strip idempotent (second pass None)", rec2 is None, str(rec2))

# marker gate relocates below a LIVE error even with NO thinking in last_asst
b = body(copy.deepcopy(emsgs), msg_markers=((4, "1h"),))
t._strip_consumed_tool_errors(b)          # in-turn strip runs first (as in server)
g = t._midturn_marker_gate(b)
check("gate acts on a live-error tail (no thinking)", g and g.get("acted") is True, str(g))
check("gate flags the live error", g and g.get("live_error") is True, str(g))
check("gate halt sits below the live error (idx 3)", g and g.get("halt_idx") == 3, str(g))
check("marker relocated off the live error (idx 4) onto idx 3",
      (4, "1h") not in msg_markers_of(b) and any(i == 3 for i, _ in msg_markers_of(b)),
      str(msg_markers_of(b)))

# a consumed-only error (no live frontier) is a clean tail -> gate stock
csmsgs = [{"role": "user", "content": "task"},
          fail_call_msg(0), err_result_msg(0), plain_msg(1), result_msg(1)]
b = body(copy.deepcopy(csmsgs), msg_markers=((4, "1h"),))
t._strip_consumed_tool_errors(b)
check("gate stock when the tail is a plain (non-error) result",
      t._midturn_marker_gate(b) is None)

t.STRIP_PRIOR_TOOL_ERRORS = False

print("== consumed edit-ack strip: bust-free at the transition (AB3 8.7k fix) ==")
t.STRIP_PRIOR_EDIT_ACKS = True
_UPD = "The file /repo/lib/slugify.js has been updated successfully. (file state is current in your context — no need to Read it back)"


def edit_call_msg(n):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"ed{n}", "name": "Edit",
         "input": {"file_path": "/repo/lib/slugify.js", "old_string": "a", "new_string": "b"}}]}


def ack_result_msg(n, body_text=_UPD):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"ed{n}", "content": body_text, "is_error": False}]}


# The exact AB3 seq-7→8 shape: task-1 edit ack settled in history, then a NEW
# assistant turn. [0]user [1]edit ed0 [2]ack ed0 [3]assistant reaction
# [4]user(task2) [5]edit ed1 [6]LIVE ack ed1
amsgs = [{"role": "user", "content": "task1"},
         edit_call_msg(0), ack_result_msg(0),
         plain_msg(9),
         {"role": "user", "content": "task2"},
         edit_call_msg(1), ack_result_msg(1)]
b = body(copy.deepcopy(amsgs))
rec = t._strip_consumed_edit_acks(b)
check("consumed ack collapses (1)", rec and rec["collapsed_edit_acks"] == 1, str(rec))
check("consumed ack body -> 'ok'", b["messages"][2]["content"][0]["content"] == "ok")
check("LIVE frontier ack UNTOUCHED (keeps the no-Read-back nudge)",
      b["messages"][6]["content"][0]["content"] == _UPD)
# BUST-FREE PROOF: the collapse is deterministic + idempotent, so a re-shipped
# history is byte-stable (never mutates-after-caching -> never busts a warm prefix)
rec2 = t._strip_consumed_edit_acks(b)
check("consumed ack strip idempotent (second pass None)", rec2 is None, str(rec2))
# the two-task history from cold produces the SAME bytes as the incremental one
# above (determinism across the transition = no self-inflicted bust)
b_cold = body(copy.deepcopy(amsgs))
t._strip_consumed_edit_acks(b_cold)
check("cold vs incremental produce identical collapsed bytes (bust-free)",
      json.dumps(b_cold["messages"][2], sort_keys=True) ==
      json.dumps(b["messages"][2], sort_keys=True))

# MARKER GATE must relocate below a LIVE frontier EDIT-ACK too (the seq10->11
# read-drop bust: the CLI's rolling marker lands on the fresh raw ack [idx6],
# next turn's collapse mutates that block -> the whole segment it anchors busts
# back to the preamble. Fix: the ack joins thinking+error in the doomed set, so
# the marker rides idx5 [one block before], leaving the doomed ack uncached.)
b = body(copy.deepcopy(amsgs), msg_markers=((6, "1h"),))
t._strip_consumed_edit_acks(b)            # in-turn strip runs first (as in server)
g = t._midturn_marker_gate(b)
check("gate acts on a live-edit-ack tail", g and g.get("acted") is True, str(g))
check("gate flags the live edit-ack, NOT an error",
      g and g.get("live_edit_ack") is True and g.get("live_error") is False, str(g))
check("gate halt sits below the live ack (idx 5)", g and g.get("halt_idx") == 5, str(g))
check("marker relocated off the doomed ack (idx 6) onto stable idx 5",
      (6, "1h") not in msg_markers_of(b) and any(i == 5 for i, _ in msg_markers_of(b)),
      str(msg_markers_of(b)))
# a settled (consumed) ack with a PLAIN result tail is a clean tail -> gate stock
b = body(copy.deepcopy(amsgs[:4]) + [result_msg(9)], msg_markers=((4, "1h"),))
t._strip_consumed_edit_acks(b)
check("gate stock when the frontier is a plain (non-ack) result",
      t._midturn_marker_gate(b) is None)
t.STRIP_PRIOR_EDIT_ACKS = False

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

print("== OWNED MOBILE MARKER (OWN_MOBILE_MARKER) ==")
t.OWN_MOBILE_MARKER = True
t.STRIP_MIDTURN_THINKING = True
_OWN = t.store_mod.OWNER
t._MARKER_STATE.clear()


def _omm(b, sid):
    return t._own_mobile_marker(b, session_id=sid)


def _state(sid):
    return t._MARKER_STATE.get((_OWN, sid))


# clean tail (no live block): marker planted on the LAST message, CLI tail stripped
msgs = turn("task", [True, True, False])          # last round plain -> clean tail
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))   # CLI rolling tail present
rec = _omm(b, "s-clean")
last = len(msgs) - 1
check("clean tail: placed", rec and rec.get("mode") == "placed", str(rec))
check("clean tail: frontier = last message", rec and rec.get("frontier_idx") == last, str(rec))
check("clean tail: our marker on last message", any(i == last for i, _ in msg_markers_of(b)))
check("clean tail: exactly one message marker (CLI tail absorbed, not doubled)",
      len(msg_markers_of(b)) == 1, str(msg_markers_of(b)))
check("clean tail: ttl preserves CLI tail (1h, no silent upgrade)",
      rec and rec.get("ttl") == "1h", str(rec))

# live thinking tail: marker must sit BELOW the live block, never on it
msgs = turn("task", [True, True, True])           # last assistant carries thinking
b = body(msgs, msg_markers=((len(msgs) - 1, "1h"),))
rec = _omm(b, "s-live")
live_idx = len(msgs) - 2                            # last assistant (thinking)
check("live tail: placed below live block", rec and rec.get("frontier_idx") == live_idx - 1, str(rec))
check("live tail: no marker at/after the live block",
      all(i < live_idx for i, _ in msg_markers_of(b)), str(msg_markers_of(b)))

# CLI OMITTED its rolling tail entirely -> we still plant one (the whole point)
msgs = turn("task", [True, True, False])
b = body(msgs)                                     # NO msg markers at all
rec = _omm(b, "s-omit")
check("omitted tail: we plant our own anyway", rec and rec.get("mode") == "placed"
      and len(msg_markers_of(b)) == 1, str(rec))

# floor_only: turn's first round, nothing consumed beyond the boundary
msgs = turn("task", [True])                        # single thinking head, no consumed
b = body(msgs)
rec = _omm(b, "s-floor")
check("floor_only when nothing consumed beyond boundary",
      rec and rec.get("mode") == "floor_only", str(rec))
check("floor_only plants no message marker", len(msg_markers_of(b)) == 0, str(msg_markers_of(b)))

# SELF BUDGET-AWARENESS (contrarian #5): 2 sys + msg0 bundle + pin@boundary +
# our mobile = 5 -> we demote msg0 OURSELVES, keeping pin (boundary) + mobile.
msgs = turn("task", [True, True, False])           # clean tail
# boundary is msg0 (the user turn); simulate pin already placed there + msg0 bundle.
# Here msg0 IS the boundary, so add a SECOND below-frontier CLI marker to force 5.
msgs2 = [{"role": "user", "content": "prior turn"},
         plain_msg(0), result_msg(0),
         {"role": "user", "content": "task"}] + turn("", [True, True, False])[1:]
b = body(msgs2, msg_markers=((0, "1h"), (2, "1h")))   # msg0 bundle + a stray CLI marker
# manufacture a pin marker at the settled boundary
bnd = t._settled_boundary(b["messages"])
if isinstance(b["messages"][bnd].get("content"), str):
    b["messages"][bnd]["content"] = [{"type": "text", "text": b["messages"][bnd]["content"]}]
b["messages"][bnd]["content"][-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
rec = _omm(b, "s-budget")
allm = t._cache_markers(b)
check("budget: <=4 markers after self-demote", len(allm) <= 4, str([(r, i) for r, i, _ in allm]))
check("budget: pin@boundary preserved", any(r == "messages" and i == bnd for r, i, _ in allm), str(bnd))
check("budget: our mobile@frontier preserved",
      any(r == "messages" and i == rec.get("frontier_idx") for r, i, _ in allm), str(rec))
check("budget: msg0 bundle demoted (idx 0 gone)",
      not any(r == "messages" and i == 0 for r, i, _ in allm)
      and rec.get("demoted_msg_markers") and 0 in rec["demoted_msg_markers"], str(rec))

# PERSISTENCE: remember anchor + prefix fingerprint; re-send classifies same lineage
t._MARKER_STATE.clear()
msgs = turn("task", [True, True, False])           # clean tail, frontier = last
b = body(msgs)
rec = _omm(b, "s-persist")
saved = _state("s-persist")
check("persist: anchor remembered", saved is not None and saved["idx"] == len(msgs) - 1, str(saved))
check("persist: sig matches the placed block",
      saved and saved["sig"] == t._msg_identity(b["messages"][len(msgs) - 1]))
check("persist: prefix fingerprint stored", saved and saved.get("pfp"))
b2 = body(msgs)                                    # identical prefix re-sent
rec2 = _omm(b2, "s-persist")
check("re-send: lineage classified SAME (prefix survived)",
      rec2 and rec2.get("lineage") == "same", str(rec2))

# FRONTIER ADVANCE must NOT false-alarm as changed (contrarian #2): the prefix
# grows legitimately; lineage stays SAME, advanced=True.
t._MARKER_STATE.clear()
msgs_r1 = turn("task", [True, False])              # frontier at last (idx 4)
b1 = body(msgs_r1)
rec1 = _omm(b1, "s-adv")
msgs_r2 = turn("task", [True, False, False])       # same prefix + a new consumed round
b2 = body(msgs_r2)
rec2 = _omm(b2, "s-adv")
check("advance: lineage SAME (not falsely changed)", rec2 and rec2.get("lineage") == "same", str(rec2))
check("advance: frontier moved deeper -> advanced flag",
      rec2 and rec2.get("advanced") is True and rec2["frontier_idx"] > rec1["frontier_idx"], str(rec2))

# LEGACY ROW (pre-pfp migration): stored row has pfp=None -> must NOT read as
# prefix_changed on the first post-migration request (contrarian msg-154 #1).
t._MARKER_STATE.clear()
msgs_l = turn("task", [True, False])
b0 = body(msgs_l)
# seed a legacy row whose sig matches the frontier we're about to compute, pfp None
_fr = len(msgs_l) - 1
t._marker_state_save("s-legacy", t._msg_identity(b0["messages"][_fr]), _fr, "1h",
                     len(msgs_l), None)
b = body(msgs_l)
rec = _omm(b, "s-legacy")
check("legacy row (pfp None) -> legacy_unknown, not prefix_changed",
      rec and rec.get("lineage") == "legacy_unknown", str(rec))
# and it self-heals: the save wrote a real pfp, so next request is same
rec2 = _omm(body(msgs_l), "s-legacy")
check("legacy self-heals to same after one request", rec2 and rec2.get("lineage") == "same", str(rec2))

# ANCHOR GONE (full compact / divergence): remembered anchor absent entirely
t._MARKER_STATE.clear()
t._marker_state_save("s-gone", "sig-not-present-anywhere", 99, "1h", 100, "oldpfp")
msgs = turn("totally different session", [True, False])
b = body(msgs)
rec = _omm(b, "s-gone")
check("anchor_gone when remembered anchor absent", rec and rec.get("lineage") == "anchor_gone", str(rec))

# REPRESENTATION STABILITY (contrarian #2): the SAME message as a bare string
# vs a singleton text block must NOT read as a lineage change.
sig_str = t._msg_identity({"role": "user", "content": "hello"})
sig_blk = t._msg_identity({"role": "user", "content": [{"type": "text", "text": "hello"}]})
check("identity: string == singleton text block", sig_str == sig_blk, f"{sig_str} vs {sig_blk}")

# LINEAGE CHANGE (partial compact): anchor SURVIVES but the prefix before it was
# rewritten -> lineage:changed (honest cold-read accounting, contrarian #3).
msgs_a = turn("task", [True, True, False])
b = body(msgs_a)
_omm(b, "s-lineage")
msgs_b = copy.deepcopy(msgs_a)
msgs_b[0] = {"role": "user", "content": "REWRITTEN earlier context (compact)"}
b2 = body(msgs_b)                                  # same tail anchor, changed prefix
rec = _omm(b2, "s-lineage")
check("partial compact: lineage:prefix_changed when prefix differs (anchor survives)",
      rec and rec.get("lineage") == "prefix_changed", str(rec))

# STALE ROW cleanup (contrarian #6): unmarkable frontier forgets the row
t._MARKER_STATE.clear()
t._marker_state_save("s-stale", "sig", 5, "1h", 10, "pfp")
check("stale: row present before", _state("s-stale") is not None)
# frontier unmarkable: last message content is an empty list
msgs = turn("task", [True, True, False])
b = body(msgs)
b["messages"][len(msgs) - 1]["content"] = []       # unmarkable frontier
rec = _omm(b, "s-stale")
check("stale: declined on unmarkable frontier",
      rec and rec.get("mode") == "declined", str(rec))
check("stale: row forgotten (no repeat dead lookup)", _state("s-stale") is None)

# lazy load / forget round-trip
t._marker_state_forget("s-persist")
check("forget clears memory", _state("s-persist") is None)
check("load after forget -> None", t._marker_state_load("s-persist") is None)

# SCHEMA MIGRATION (contrarian #1): an OLD marker_state table (no pfp column)
# must get pfp added, so load/save don't hit "no such column" forever.
import sqlite3 as _sq
import tempfile as _tf
_olddb = _tf.mktemp(suffix=".sqlite")
_oc = _sq.connect(_olddb)
_oc.execute("CREATE TABLE marker_state (owner TEXT NOT NULL, session_id TEXT NOT "
            "NULL, sig TEXT NOT NULL, idx INTEGER NOT NULL, ttl TEXT, msgs "
            "INTEGER NOT NULL, set_at REAL NOT NULL, PRIMARY KEY (owner, session_id))")
_oc.commit()
_oc.close()
_mig_ok = True
try:
    _mc = _sq.connect(_olddb)
    # apply exactly the statements transforms registers (CREATE IF NOT EXISTS no-ops,
    # ALTER adds the column)
    for _stmt in ("CREATE TABLE IF NOT EXISTS marker_state (owner TEXT NOT NULL, "
                  "session_id TEXT NOT NULL, sig TEXT NOT NULL, idx INTEGER NOT "
                  "NULL, ttl TEXT, msgs INTEGER NOT NULL, pfp TEXT, set_at REAL "
                  "NOT NULL, PRIMARY KEY (owner, session_id))",
                  "ALTER TABLE marker_state ADD COLUMN pfp TEXT"):
        try:
            _mc.execute(_stmt)
        except _sq.OperationalError:
            if not _stmt.lstrip().upper().startswith("ALTER "):
                raise
    _mc.commit()
    _cols = [r[1] for r in _mc.execute("PRAGMA table_info(marker_state)")]
    _mc.close()
except Exception as _e:
    _mig_ok = False
    _cols = [str(_e)]
check("migration: old marker_state table gains pfp column", _mig_ok and "pfp" in _cols, str(_cols))

# kill switch
t.OWN_MOBILE_MARKER = False
check("OWN_MOBILE_MARKER off -> None", _omm(body(turn("x", [True, False])), "s-off") is None)
t.OWN_MOBILE_MARKER = False   # leave default off for the rest of the process

print(f"\n{CHECKS['pass']} passed, {CHECKS['fail']} failed")
raise SystemExit(1 if CHECKS["fail"] else 0)
