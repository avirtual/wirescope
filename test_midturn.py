#!/usr/bin/env python3
"""Mid-turn strip + op-1 pin — regression suite for the AB2 postmortem fixes
(2026-07-20): (a) donor never steals a 1h marker (the anchor-steal defect),
(b) sparse-texture gate (30x negative ROI on sparse turns), (c) ladder
invariant (never 5m-only message coverage). Plus determinism/monotonicity of
the gap gate across rounds."""
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


# force the L1 gate on for the suite (kill switches already default-on)
t.STRIP_PRIOR_THINKING = True
t.STRIP_MIDTURN_THINKING = True
t.PIN_MIDTURN_BREAKPOINT = True

print("== gap gate: dense turn strips, keep-window protected ==")
msgs = turn("task", [True, True, True, True])   # thinking every round, gap 2
b = body(msgs)
rec = t._strip_midturn_thinking(b)
check("dense strips", rec and rec.get("stripped") is True)
check("3 of 4 removed (keep=1)", rec and rec["removed_thinking_blocks"] == 3, str(rec))
check("head survives", n_thinking(b["messages"]) == 1)
check("no sparse skips", rec and rec.get("skipped_sparse_blocks") == 0)

print("== gap gate: sparse turn declines (the AB2 T4 texture) ==")
msgs = turn("task", [True] + [False] * 9 + [True])   # gap 20 > MAX_GAP
b = body(msgs)
rec = t._strip_midturn_thinking(b)
check("sparse declines", rec and rec.get("stripped") is False and rec.get("reason") == "sparse_turn", str(rec))
check("nothing deleted", n_thinking(b["messages"]) == 2)
pin = t._pin_midturn_breakpoint(body(msgs, msg_markers=((len(msgs) - 1, "5m"),)))
check("pin declines sparse too", pin and pin.get("pinned") is False and pin.get("reason") == "sparse_turn", str(pin))

print("== gap gate: mixed texture strips only the dense block ==")
msgs = turn("task", [True, True] + [False] * 9 + [True])  # gaps: 2 (dense), 20 (sparse)
b = body(msgs)
rec = t._strip_midturn_thinking(b)
check("mixed strips dense only", rec and rec["removed_thinking_blocks"] == 1
      and rec["skipped_sparse_blocks"] == 1, str(rec))
check("2 thinking remain (sparse + head)", n_thinking(b["messages"]) == 2)

print("== determinism: round k+1 resend reproduces round k's stripped prefix ==")
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

print("== donor: NEVER the 1h anchor; tail is the fallback donor ==")
msgs = turn("task", [True, True, True, True])
last = len(msgs) - 1
# budget full: 2 system + 1h anchor on msg2 + 5m tail = 4 markers
b = body(msgs, msg_markers=((2, "1h"), (last, "5m")))
pin = t._pin_midturn_breakpoint(b)
check("pin fired", pin and pin.get("pinned") is True, str(pin))
check("donor is the TAIL, not the anchor", pin and pin.get("mode") == "migrated_tail"
      and pin.get("donor_msg_idx") == last, str(pin))
anchor_cc = b["messages"][2]["content"][-1].get("cache_control")
check("1h anchor SURVIVES", anchor_cc == {"type": "ephemeral", "ttl": "1h"})
check("pin is 5m (1h coverage below)", pin and pin.get("ttl") == "5m")
tail_cc = b["messages"][last]["content"][-1].get("cache_control")
check("tail marker removed", tail_cc is None)

print("== donor: 5m marker below pin preferred over tail ==")
msgs = turn("task", [True, True, True, True])
last = len(msgs) - 1
b = body(msgs, msg_markers=((1, "5m"), (last, "5m")))   # msg1 = old 5m loop marker
pin = t._pin_midturn_breakpoint(b)
check("5m donor migrated", pin and pin.get("pinned") and pin.get("mode") == "migrated"
      and pin.get("donor_msg_idx") == 1, str(pin))
check("tail survives", b["messages"][last]["content"][-1].get("cache_control") is not None)

print("== donor: decline when only 1h markers available and tail not stealable ==")
old_scrap = t.SCRAP_TAIL_5M
t.SCRAP_TAIL_5M = False
msgs = turn("task", [True, True, True, True])
last = len(msgs) - 1
b = body(msgs, msg_markers=((2, "1h"), (last, "1h")))   # 1h tail, scrap off
pin = t._pin_midturn_breakpoint(b)
check("declines rather than steal 1h", pin and pin.get("pinned") is False
      and pin.get("reason") == "budget_full_no_donor", str(pin))
check("anchor untouched on decline",
      b["messages"][2]["content"][-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"})
check("tail untouched on decline",
      b["messages"][last]["content"][-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"})
t.SCRAP_TAIL_5M = old_scrap

print("== ladder invariant: no message marker below pin -> pin goes 1h ==")
msgs = turn("task", [True, True, True, True])
last = len(msgs) - 1
b = body(msgs, sys_markers=2, msg_markers=((last, "5m"),))  # 3 markers, budget free
pin = t._pin_midturn_breakpoint(b)
check("pin fired (added)", pin and pin.get("pinned") and pin.get("mode") == "added", str(pin))
check("pin upgraded to 1h", pin and pin.get("ttl") == "1h", str(pin))

print("== ladder invariant: surviving 5m below keeps pin 5m (ordering legality) ==")
msgs = turn("task", [True, True, True, True])
last = len(msgs) - 1
b = body(msgs, sys_markers=1, msg_markers=((1, "5m"), (last, "5m")))  # 3 markers
pin = t._pin_midturn_breakpoint(b)
check("pin stays 5m after 5m marker", pin and pin.get("pinned") and pin.get("ttl") == "5m", str(pin))

print(f"\n{CHECKS['pass']} passed, {CHECKS['fail']} failed")
raise SystemExit(1 if CHECKS["fail"] else 0)
