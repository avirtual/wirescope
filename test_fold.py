#!/usr/bin/env python3
"""Offline tests for proxylab.fold — the Read+Edit fold transform.

Two layers:
  1. UNIT — buffer parse/render/apply round-trips, discovery, stub apply.
  2. REPLAY — drive REAL captured sessions through fold turn-by-turn and assert
     the load-bearing invariants:
       (a) CORRECTNESS: a folded Read body == the file shape after replaying the
           same-turn edits onto the original buffer (independently recomputed).
       (b) PREFIX-STABILITY: for a settled pair, the forwarded bytes are
           identical across every later turn (the warmth guarantee).
       (c) DETERMINISM: fresh maps (a "restart") reproduce byte-identical output.
       (d) LIVE TAIL: the current (unsettled) turn is never folded.
       (e) BALANCE: every tool_use keeps a paired tool_result and vice-versa.
"""
import copy
import glob
import json
import os
import sys

from proxylab import fold


# ----------------------------------------------------------------- unit tests
def _mk_read(content):
    return content


def test_parse_render_roundtrip():
    body = "1\tfoo\n2\tbar\n3\tbaz\n"
    start, text = fold._parse_numbered(body)
    assert start == 1 and text == "foo\nbar\nbaz\n", (start, repr(text))
    assert fold._render_numbered(start, text) == body
    # offset read, no trailing newline
    body2 = "10\talpha\n11\tbeta"
    start2, text2 = fold._parse_numbered(body2)
    assert start2 == 10 and text2 == "alpha\nbeta", (start2, repr(text2))
    assert fold._render_numbered(start2, text2) == body2
    # non-numbered (error read) -> None
    assert fold._parse_numbered("File does not exist.") is None
    assert fold._parse_numbered("") is None
    print("ok  parse/render roundtrip")


def test_apply_edit():
    text = "line one\nline two\nline three\n"
    # clean single replace
    assert fold._apply_edit(text, "line two", "LINE 2", False) == "line one\nLINE 2\nline three\n"
    # missing old -> None
    assert fold._apply_edit(text, "nope", "x", False) is None
    # ambiguous without replace_all -> None
    t2 = "x\nx\n"
    assert fold._apply_edit(t2, "x", "y", False) is None
    # ambiguous WITH replace_all -> ok
    assert fold._apply_edit(t2, "x", "y", True) == "y\ny\n"
    # empty old -> None
    assert fold._apply_edit(text, "", "x", False) is None
    print("ok  apply_edit")


def test_render_renumbers_after_growth():
    # an edit that adds a line must renumber contiguously
    start, text = fold._parse_numbered("1\ta\n2\tb\n")
    text = fold._apply_edit(text, "b", "b\nc", False)
    out = fold._render_numbered(start, text)
    assert out == "1\ta\n2\tb\n3\tc\n", repr(out)
    print("ok  renumber after line growth")


def test_synthetic_fold_and_stability():
    """A hand-built session: turn 1 reads+edits a file, turn 2 is a new prompt.
    Assert turn-2 request folds the settled pair; turn-1 request (pair still
    live) does not."""
    read_body = "1\thello\n2\tworld\n"
    rid, eid = "toolu_R", "toolu_E"

    def turn1():
        # current turn = the read+edit just happened, no later user prompt yet
        return {"metadata": {"user_id": json.dumps({"session_id": "S-syn"})},
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "edit it"}]},
                    {"role": "assistant", "content": [
                        {"type": "tool_use", "id": rid, "name": "Read",
                         "input": {"file_path": "/f.py"}}]},
                    {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": rid, "content": read_body}]},
                    {"role": "assistant", "content": [
                        {"type": "tool_use", "id": eid, "name": "Edit",
                         "input": {"file_path": "/f.py", "old_string": "world",
                                   "new_string": "there"}}]},
                    {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": eid,
                         "content": "The file /f.py has been updated successfully"}]},
                ]}

    def turn2():
        o = turn1()
        o["messages"].append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})
        o["messages"].append({"role": "user", "content": [{"type": "text", "text": "now what"}]})
        return o

    fold._forget("S-syn")
    fold._FOLD_OVERRIDE["S-syn"] = True

    # turn 1: the pair is the CURRENT turn (after the last real-user msg "edit it"
    # all blocks are tool plumbing) -> NOT settled -> no fold
    o1 = turn1()
    r1 = fold.fold_read_edits(o1)
    assert r1 is None, f"turn1 should not fold (live tail): {r1}"
    # read body untouched, edit input intact
    assert o1["messages"][2]["content"][0]["content"] == read_body
    assert "old_string" in o1["messages"][3]["content"][0]["input"]

    # turn 2: a new user prompt settled turn 1 -> fold it
    o2 = turn2()
    r2 = fold.fold_read_edits(o2)
    assert r2 and r2["folded_read_bodies"] == 1 and r2["stubbed_edit_calls"] == 1, r2
    folded_read = o2["messages"][2]["content"][0]["content"]
    assert folded_read == "1\thello\n2\tthere\n", repr(folded_read)
    assert o2["messages"][3]["content"][0]["input"] == fold.FOLD_CALL_STUB
    assert o2["messages"][4]["content"][0]["content"] == fold.FOLD_RESULT_STUB

    # STABILITY: a third turn produces byte-identical folded bytes for the pair
    o3 = turn2()
    o3["messages"].append({"role": "assistant", "content": [{"type": "text", "text": "x"}]})
    o3["messages"].append({"role": "user", "content": [{"type": "text", "text": "y"}]})
    fold.fold_read_edits(o3)
    assert o3["messages"][2]["content"][0]["content"] == folded_read

    # DETERMINISM: wipe maps (simulate restart) -> recompute identical
    fold._FOLDED_READS.pop("S-syn", None)
    fold._FOLDED_EDITS.pop("S-syn", None)
    fold._PROCESSED_READS.pop("S-syn", None)
    o2b = turn2()
    fold.fold_read_edits(o2b)
    assert o2b["messages"][2]["content"][0]["content"] == folded_read
    fold._forget("S-syn")
    print("ok  synthetic fold + live-tail + stability + determinism")


def test_failed_edit_not_folded():
    """An is_error edit result means the real file didn't change -> don't fold."""
    rid, eid = "toolu_R2", "toolu_E2"
    o = {"metadata": {"user_id": json.dumps({"session_id": "S-err"})},
         "messages": [
             {"role": "user", "content": [{"type": "text", "text": "go"}]},
             {"role": "assistant", "content": [
                 {"type": "tool_use", "id": rid, "name": "Read", "input": {"file_path": "/f"}}]},
             {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": rid, "content": "1\taaa\n"}]},
             {"role": "assistant", "content": [
                 {"type": "tool_use", "id": eid, "name": "Edit",
                  "input": {"file_path": "/f", "old_string": "aaa", "new_string": "bbb"}}]},
             {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": eid, "is_error": True,
                  "content": "String not found"}]},
             {"role": "assistant", "content": [{"type": "text", "text": "hm"}]},
             {"role": "user", "content": [{"type": "text", "text": "next"}]},
         ]}
    fold._forget("S-err")
    fold._FOLD_OVERRIDE["S-err"] = True
    r = fold.fold_read_edits(o)
    assert r is None, f"failed edit must not fold: {r}"
    assert o["messages"][2]["content"][0]["content"] == "1\taaa\n"
    fold._forget("S-err")
    print("ok  failed edit not folded")


def test_disabled_by_default():
    o = {"metadata": {"user_id": json.dumps({"session_id": "S-off"})},
         "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]}
    fold._forget("S-off")
    assert fold.fold_read_edits(o) is None
    print("ok  disabled by default")


# --------------------------------------------------------------- replay tests
def _largest_snapshot(session_dir):
    best = None
    for f in glob.glob(os.path.join(session_dir, "*.request.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        m = (d.get("body") or {}).get("messages")
        if m and (best is None or len(m) > len(best[1])):
            best = (f, m, d.get("body"))
    return best


def _independent_expected_folds(msgs, last_user):
    """Recompute, independently of fold.py's discovery, what each settled
    same-turn read should fold to (final file shape) + which edits stub. Returns
    {read_id: expected_content, ...}, set(edit_ids). All-or-nothing per read."""
    expected_reads, expected_edits = {}, set()
    # gather error ids
    err = set()
    for i in range(last_user):
        m = msgs[i]
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                    err.add(b.get("tool_use_id"))
    cur = {}
    pending = {}

    def commit():
        for ri in cur.values():
            if ri["ok"] and ri["eids"]:
                expected_reads[ri["rid"]] = fold._render_numbered(ri["start"], ri["text"])
                expected_edits.update(ri["eids"])

    from proxylab import transforms as t
    for i in range(last_user):
        m = msgs[i]
        if t._is_real_user_turn(m):
            commit(); cur = {}; pending = {}
        c = m.get("content")
        if not isinstance(c, list):
            continue
        if m.get("role") == "assistant":
            for b in c:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                fp = (b.get("input") or {}).get("file_path")
                if b.get("name") == "Read" and fp:
                    pending[b.get("id")] = fp
                elif b.get("name") == "Edit" and fp and fp in cur:
                    ri = cur[fp]
                    if not ri["ok"]:
                        continue
                    if b.get("id") in err:
                        ri["ok"] = False; continue
                    inp = b.get("input") or {}
                    nt = fold._apply_edit(ri["text"], inp.get("old_string"),
                                          inp.get("new_string") or "", inp.get("replace_all"))
                    if nt is None:
                        ri["ok"] = False
                    else:
                        ri["text"] = nt; ri["eids"].append(b.get("id"))
        elif m.get("role") == "user":
            for b in c:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tid = b.get("tool_use_id")
                if tid not in pending:
                    continue
                fp = pending.pop(tid)
                parsed = fold._parse_numbered(fold._result_text(b))
                if parsed is None:
                    cur.pop(fp, None); continue
                cur[fp] = {"rid": tid, "start": parsed[0], "text": parsed[1], "eids": [], "ok": True}
    commit()
    return expected_reads, expected_edits


def _assert_balanced(msgs):
    use_ids, res_ids = set(), set()
    for m in msgs:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                use_ids.add(b.get("id"))
            elif b.get("type") == "tool_result":
                res_ids.add(b.get("tool_use_id"))
    # every result references a use; every use that had a result still has one
    assert res_ids <= use_ids, f"orphan tool_results: {res_ids - use_ids}"


def test_replay_real_sessions(root="logs_main", limit=120):
    sessions = sorted(d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)
                      and not os.path.basename(d).startswith("_"))
    from proxylab import transforms as t
    checked = folded_sessions = total_folded_reads = 0
    stability_checks = correctness_checks = 0
    for s in sessions[:limit]:
        snap = _largest_snapshot(s)
        if not snap:
            continue
        _, msgs, _ = snap
        # need >=1 settled same-turn read+edit to be interesting
        last_user = max((i for i, m in enumerate(msgs) if t._is_real_user_turn(m)), default=-1)
        if last_user <= 0:
            continue
        exp_reads, exp_edits = _independent_expected_folds(msgs, last_user)
        sid = f"REPLAY::{os.path.basename(s)}"
        fold._forget(sid)
        fold._FOLD_OVERRIDE[sid] = True
        checked += 1

        # Build incremental snapshots: replay the transcript turn-by-turn by
        # truncating at each real-user boundary, mimicking how the CLI grows it.
        boundaries = [i for i, m in enumerate(msgs) if t._is_real_user_turn(m)]
        prev_folded = {}            # read_id -> bytes seen in a PRIOR turn
        for bi in boundaries[1:] + [len(msgs)]:
            obj = {"metadata": {"user_id": json.dumps({"session_id": sid})},
                   "messages": copy.deepcopy(msgs[:bi])}
            fold.fold_read_edits(obj)
            # collect folded read bodies present in this snapshot
            cur_lu = max((i for i, m in enumerate(obj["messages"])
                          if t._is_real_user_turn(m)), default=-1)
            for idx, m in enumerate(obj["messages"]):
                c = m.get("content")
                if not isinstance(c, list):
                    continue
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id")
                        if tid in exp_reads and idx < cur_lu:
                            # (a) correctness vs independent recompute
                            assert b["content"] == exp_reads[tid], \
                                f"{sid}: folded body mismatch for {tid}"
                            correctness_checks += 1
                            # (b) stability across turns
                            if tid in prev_folded:
                                assert prev_folded[tid] == b["content"], \
                                    f"{sid}: folded body changed across turns for {tid}"
                                stability_checks += 1
                            prev_folded[tid] = b["content"]
            # (e) structural balance after fold
            _assert_balanced(obj["messages"])

        # (c) determinism: fresh maps reproduce the final snapshot byte-identically
        o_final1 = {"metadata": {"user_id": json.dumps({"session_id": sid})},
                    "messages": copy.deepcopy(msgs)}
        fold.fold_read_edits(o_final1)
        fold._FOLDED_READS.pop(sid, None); fold._FOLDED_EDITS.pop(sid, None)
        fold._PROCESSED_READS.pop(sid, None)
        o_final2 = {"metadata": {"user_id": json.dumps({"session_id": sid})},
                    "messages": copy.deepcopy(msgs)}
        fold.fold_read_edits(o_final2)
        assert json.dumps(o_final1["messages"]) == json.dumps(o_final2["messages"]), \
            f"{sid}: non-deterministic across map wipe"

        if exp_reads:
            folded_sessions += 1
            total_folded_reads += len(exp_reads)
        fold._forget(sid)

    print(f"ok  replay: {checked} sessions, {folded_sessions} with folds, "
          f"{total_folded_reads} folded reads; "
          f"{correctness_checks} correctness + {stability_checks} stability checks")


if __name__ == "__main__":
    test_parse_render_roundtrip()
    test_apply_edit()
    test_render_renumbers_after_growth()
    test_synthetic_fold_and_stability()
    test_failed_edit_not_folded()
    test_disabled_by_default()
    root = sys.argv[1] if len(sys.argv) > 1 else "logs_main"
    if os.path.isdir(root):
        test_replay_real_sessions(root)
    else:
        print(f"(skip replay — {root} not present)")
    print("\nALL FOLD TESTS PASSED")
