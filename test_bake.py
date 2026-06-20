#!/usr/bin/env python3
"""Offline tests for bake_session.py — the standalone JSONL transcript optimizer.

bake_session applies the proxy's prior-thinking strip directly to a Claude Code
session transcript on disk (proxy-free), so a `--resume` loads an already-lean
history. The load-bearing invariants:
  (a) thinking-only assistant lines are DELETED (one block per line in CC's
      JSONL -> deletion, never message-emptying);
  (b) the parentUuid chain stays intact after deletion (children of a deleted
      line re-point to its surviving ancestor; no dangling links);
  (c) NON-thinking content is preserved byte-identical (we only drop thinking);
  (d) determinism + idempotency (baking a baked file is a no-op);
  (e) the live resume itself is proven separately (manual --resume of the
      791426c1 guinea-pig session: RESUMED_OK, /context freed ~110k tok).

Plus a REPLAY layer against any real transcript backups present under
~/.claude/projects (skipped cleanly if none).
"""
import glob
import json
import os

import bake_session as B


# --------------------------------------------------------------- helpers
def _line(typ, uuid, parent, content=None, **extra):
    """A minimal CC-shaped JSONL line. `content` is a list of blocks for
    user/assistant; omitted for bookkeeping line types."""
    d = {"type": typ, "uuid": uuid, "parentUuid": parent,
         "sessionId": "S", "isSidechain": False}
    if content is not None:
        d["message"] = {"role": "assistant" if typ == "assistant" else "user",
                        "content": content}
    d.update(extra)
    return d


def _think(uuid, parent, text="reasoning", sig="SIG"):
    return _line("assistant", uuid, parent,
                 [{"type": "thinking", "thinking": text, "signature": sig}])


def _text(uuid, parent, text="hello"):
    return _line("assistant", uuid, parent, [{"type": "text", "text": text}])


def _user(uuid, parent, text="go"):
    return _line("user", uuid, parent, [{"type": "text", "text": text}])


# --------------------------------------------------------------- unit tests
def test_detect_thinking_only():
    assert B._is_thinking_only(_think("a", None))
    assert not B._is_thinking_only(_text("a", None))
    assert not B._is_thinking_only(_user("a", None))
    # mixed content (would never happen in CC, but must NOT be treated deletable)
    mixed = _line("assistant", "a", None,
                  [{"type": "thinking", "thinking": "t", "signature": "s"},
                   {"type": "text", "text": "x"}])
    assert not B._is_thinking_only(mixed), "mixed block must be preserved"
    # bookkeeping lines never match
    assert not B._is_thinking_only(_line("file-history-snapshot", "a", None))
    print("ok  thinking-only detection (incl. mixed-block safety)")


def test_basic_bake_and_chain():
    # u1 -> A(think) -> tr -> A(text) -> u2
    lines = [
        _user("u1", None),
        _think("t1", "u1"),
        _user("r1", "t1", "result"),
        _text("a1", "r1"),
        _user("u2", "a1"),
    ]
    baked, stats = B.bake(lines)
    assert stats["thinking_lines_deleted"] == 1
    assert stats["lines_out"] == 4
    assert stats["parent_links_rewired"] == 1
    # the child of the deleted think line (r1) must now point at u1
    r1 = next(l for l in baked if l["uuid"] == "r1")
    assert r1["parentUuid"] == "u1", f"chain not spliced: {r1['parentUuid']}"
    assert B.validate(baked) == [], "no dangling links allowed"
    print("ok  basic bake + single-link splice")


def test_consecutive_deletions_transitive_splice():
    # a run of thinking lines back-to-back -> child must skip ALL of them
    lines = [
        _user("u1", None),
        _think("t1", "u1"),
        _think("t2", "t1"),
        _think("t3", "t2"),
        _text("a1", "t3"),
    ]
    baked, stats = B.bake(lines)
    assert stats["thinking_lines_deleted"] == 3
    a1 = next(l for l in baked if l["uuid"] == "a1")
    assert a1["parentUuid"] == "u1", f"transitive splice failed: {a1['parentUuid']}"
    assert B.validate(baked) == []
    print("ok  transitive splice across consecutive deletions")


def test_nonthinking_preserved_byte_identical():
    lines = [_user("u1", None), _think("t1", "u1"), _text("a1", "t1"),
             _line("file-history-snapshot", "fh", "a1", foo={"bar": 1})]
    survivors_before = {l["uuid"]: json.dumps(l, sort_keys=True)
                        for l in lines if not B._is_thinking_only(l)
                        and "parentUuid" not in (("t1",))}  # all survivors
    baked, _ = B.bake(lines)
    for l in baked:
        # only parentUuid may change; everything else identical
        before = next(x for x in lines if x["uuid"] == l["uuid"])
        b2 = dict(before); b2.pop("parentUuid", None)
        l2 = dict(l); l2.pop("parentUuid", None)
        assert json.dumps(b2, sort_keys=True) == json.dumps(l2, sort_keys=True), \
            f"non-parent content mutated on {l['uuid']}"
    print("ok  non-thinking content preserved (only parentUuid touched)")


def test_idempotent():
    lines = [_user("u1", None), _think("t1", "u1"), _text("a1", "t1"),
             _user("u2", "a1")]
    once, s1 = B.bake(lines)
    twice, s2 = B.bake([dict(l) for l in once])
    assert s2["thinking_lines_deleted"] == 0, "second bake must be a no-op"
    assert json.dumps(once) == json.dumps(twice), "bake not idempotent"
    print("ok  idempotent (re-baking a baked file is a no-op)")


def test_root_preserved():
    lines = [_user("u1", None), _think("t1", "u1"), _text("a1", "t1")]
    baked, _ = B.bake(lines)
    assert baked[0]["parentUuid"] is None, "root must stay rooted"
    # if the FIRST line were thinking, its child becomes the new root (parent None)
    lines2 = [_think("t0", None), _user("u1", "t0")]
    baked2, _ = B.bake(lines2)
    assert baked2[0]["uuid"] == "u1" and baked2[0]["parentUuid"] is None, \
        "deleting the root thinking line must re-root its child"
    print("ok  root linkage preserved / re-rooted")


# --------------------------------------------------------------- replay layer
def test_replay_real_transcripts():
    """Bake every real transcript backup we can find; assert chain integrity and
    that no thinking survives. Skips cleanly when none are present."""
    roots = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl.full")) \
        + glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl.bak-*"))
    if not roots:
        print("skip replay (no real transcript backups found)")
        return
    checked = 0
    for path in roots:
        try:
            lines = [json.loads(l) for l in open(path) if l.strip()]
        except Exception:
            continue
        if not lines:
            continue
        baked, stats = B.bake(lines)
        assert B.validate(baked) == [], f"dangling chain in {path}"
        survivor_think = sum(1 for l in baked if B._is_thinking_only(l))
        assert survivor_think == 0, f"thinking survived in {path}"
        checked += 1
        print(f"   {os.path.basename(path)[:40]:40s} "
              f"{stats['lines_in']}->{stats['lines_out']} "
              f"(-{stats['thinking_lines_deleted']} think, "
              f"~{stats['carriage_tokens_removed_est']:,} tok)")
    print(f"ok  replay: {checked} real transcript(s), all chains intact")


if __name__ == "__main__":
    test_detect_thinking_only()
    test_basic_bake_and_chain()
    test_consecutive_deletions_transitive_splice()
    test_nonthinking_preserved_byte_identical()
    test_idempotent()
    test_root_preserved()
    test_replay_real_transcripts()
    print("\nALL BAKE TESTS PASSED")
