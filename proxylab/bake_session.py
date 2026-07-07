#!/usr/bin/env python3
"""bake_session.py — offline transcript optimizer (proxy-free carriage strip).

Applies the proxy's prior-thinking strip directly to a Claude Code session
JSONL, in place, so a `--resume` of the SAME session id loads an already-lean
history — no live wire-stripping needed, and it works for users who don't run
the proxy at all.

KEY STRUCTURAL FACT (verified on real transcripts): Claude Code writes ONE
content block per JSONL line. A thinking block therefore lives on its OWN
assistant line (content == [{"type":"thinking",...}]). Stripping it = DELETING
that line, not emptying a message -> no empty-content-message hazard. The only
repair needed is the parentUuid chain: every line that pointed at a deleted
line must be re-pointed at the deleted line's parent (transitively, in case of
runs of deletions).

SAFETY:
  - whole-block delete only; we never edit a thinking block's bytes (signatures
    won't re-validate, same rule as the wire).
  - backs up the original to <file>.bak-<ts> before overwriting.
  - keeps the session id and every surviving line's uuid unchanged.
  - dry-run by default; --apply to actually overwrite.

USAGE:
  python3 bake_session.py SESSION.jsonl              # dry-run, prints plan
  python3 bake_session.py SESSION.jsonl --apply      # backup + rewrite in place
  python3 bake_session.py SESSION.jsonl --apply -o OUT.jsonl   # write elsewhere
"""
import json, sys, os, time, argparse, shutil

THINK = ("thinking", "redacted_thinking")


def _is_thinking_only(line):
    """True if this is an assistant line whose content is solely thinking
    block(s) -> the deletable unit."""
    if line.get("type") != "assistant":
        return False
    cont = (line.get("message") or {}).get("content")
    if not isinstance(cont, list) or not cont:
        return False
    return all(isinstance(b, dict) and b.get("type") in THINK for b in cont)


def bake(lines):
    """Return (new_lines, stats). Deletes thinking-only assistant lines and
    splices the parentUuid chain so survivors stay linked."""
    # 1) decide deletions
    delete_uuid = {}            # uuid -> parentUuid (for chain splice)
    deleted = []
    kept = []
    text_chars = 0          # thinking prose only
    block_chars = 0         # full serialized block (text + signature + wrapper)
    for ln in lines:
        if _is_thinking_only(ln):
            u = ln.get("uuid")
            delete_uuid[u] = ln.get("parentUuid")
            for b in (ln.get("message") or {}).get("content", []):
                text_chars += len(b.get("thinking", "") or "")
                block_chars += len(json.dumps(b))   # the real carriage removed
            deleted.append(ln)
        else:
            kept.append(ln)

    # 2) transitive resolver: walk up past any chain of deleted parents
    def survivor_parent(p):
        seen = set()
        while p in delete_uuid and p not in seen:
            seen.add(p)
            p = delete_uuid[p]
        return p

    # 3) rewire survivors that pointed at a deleted line
    rewired = 0
    for ln in kept:
        if "parentUuid" in ln and ln["parentUuid"] in delete_uuid:
            ln["parentUuid"] = survivor_parent(ln["parentUuid"])
            rewired += 1

    # 4) PURE-THINKING TURNS = the ONLY place bake diverges from the LIVE strip.
    # Live-strip removes thinking blocks only when the coalesced wire message keeps
    # OTHER content (transforms.py: `if msg_removed and kept:` — it can't emit an
    # empty content array, the API 400s). So a turn that is ENTIRELY thinking (all
    # its consecutive assistant lines are thinking-only) survives live-strip intact
    # on the wire, but bake DELETES it. That's a benign/beneficial divergence (bake
    # safely out-strips what live-strip structurally can't), but it means a one-time
    # re-cache at that point even on a strip-ACTIVE session — so we COUNT it as the
    # divergence predictor: 0 => compact output is byte-identical to the live wire
    # (pure cache recycle); >0 => that many turns will re-cache once (then slimmer
    # forever). A run = maximal block of consecutive assistant lines.
    pure_turns = 0
    run_all_think = None
    for ln in lines:
        if ln.get("type") == "assistant":
            it = _is_thinking_only(ln)
            run_all_think = it if run_all_think is None else (run_all_think and it)
        else:
            if run_all_think:
                pure_turns += 1
            run_all_think = None
    if run_all_think:
        pure_turns += 1

    stats = {
        "lines_in": len(lines),
        "lines_out": len(kept),
        "thinking_lines_deleted": len(deleted),
        "thinking_text_chars": text_chars,
        "carriage_chars_removed": block_chars,          # incl. signatures (the big part)
        "carriage_tokens_removed_est": block_chars // 4,
        "parent_links_rewired": rewired,
        # wire-divergence predictor (see above): 0 = byte-identical to live-strip
        "pure_thinking_turns": pure_turns,
    }
    return kept, stats


def validate(lines):
    """Chain-integrity check: every non-None parentUuid must reference a uuid
    that still exists. Returns list of dangling (uuid, parentUuid)."""
    uuids = {ln.get("uuid") for ln in lines if "uuid" in ln}
    dangling = []
    for ln in lines:
        p = ln.get("parentUuid")
        if p is not None and p not in uuids:
            dangling.append((ln.get("uuid"), p))
    return dangling


def compact_file(path, expect_session=None):
    """Programmatic twin of the CLI (the proxy's POST /_compact calls this). Reads
    a Claude Code transcript JSONL, bakes out the maximal SAFE-to-drop set (today:
    thinking-only — whole-block deletes, signatures never edited), validates chain
    integrity BEFORE replacing, backs up + ATOMICALLY rewrites. Never raises for an
    expected condition — always returns {ok: bool, ...} so the caller can resume
    the ORIGINAL transcript on any problem rather than risk a broken one.

    Guarantees: NO partial writes. The file on `path` is only ever replaced via
    os.replace of a fully-written temp, and only after a .bak-<ts> copy exists and
    the baked chain validated clean. Idempotent: an already-slim file is a no-op
    (no write, no backup). Outcomes (all dicts):
      not_found / unreadable / not_a_transcript -> ok:False (no write)
      session_mismatch (file isn't this session) -> ok:False (no write)
      chain_integrity (a delete would dangle a parentUuid) -> ok:False (no write)
      slim already -> ok:True, noop:True (no write)
      success -> ok:True, noop:False, + stats + backup path
    """
    if not path or not os.path.isfile(path):
        return {"ok": False, "reason": "not_found", "path": path}
    try:
        raw = [l for l in open(path) if l.strip()]
        lines = [json.loads(l) for l in raw]
    except Exception as e:
        return {"ok": False, "reason": "unreadable", "error": str(e), "path": path}
    if not lines:
        return {"ok": False, "reason": "not_a_transcript", "path": path}
    # sanity: a real CC transcript is uuid/parentUuid-linked (same gate as the CLI)
    linked = sum(1 for l in lines if "uuid" in l and "parentUuid" in l)
    if linked < len(lines) // 2:
        return {"ok": False, "reason": "not_a_transcript", "path": path,
                "linked": linked, "lines": len(lines)}
    # safety: refuse to compact a file that isn't the named session (prevents a
    # wrong-path request silently rewriting someone else's transcript).
    if expect_session:
        sids = {l.get("sessionId") for l in lines if l.get("sessionId")}
        if sids and expect_session not in sids:
            return {"ok": False, "reason": "session_mismatch", "path": path,
                    "found_sessions": sorted(s for s in sids if s)[:3]}

    baked, stats = bake(lines)
    dangling = validate(baked)
    if dangling:
        return {"ok": False, "reason": "chain_integrity",
                "dangling": len(dangling), "path": path}
    if stats["thinking_lines_deleted"] == 0:        # already slim -> idempotent no-op
        return {"ok": True, "noop": True, "path": path, **stats}

    bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, bak)
    tmp = f"{path}.tmp-{os.getpid()}-{int(time.time())}"
    try:
        with open(tmp, "w") as fh:
            for ln in baked:
                fh.write(json.dumps(ln, ensure_ascii=False) + "\n")
        os.replace(tmp, path)                       # atomic swap into place
    except Exception as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return {"ok": False, "reason": "write_failed", "error": str(e),
                "backup": bak, "path": path}
    return {"ok": True, "noop": False, "path": path, "backup": bak, **stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--apply", action="store_true", help="overwrite in place (else dry-run)")
    ap.add_argument("-o", "--out", help="write to this path instead of in place")
    args = ap.parse_args()

    raw = [l for l in open(args.jsonl) if l.strip()]
    lines = [json.loads(l) for l in raw]
    orig_bytes = sum(len(l) for l in raw)

    # sanity: looks like a CC transcript? (uuid/parentUuid linkage present)
    linked = sum(1 for l in lines if "uuid" in l and "parentUuid" in l)
    if not lines or linked < len(lines) // 2:
        sys.exit(f"refusing: {args.jsonl} does not look like a Claude Code "
                 f"transcript ({linked}/{len(lines)} lines have uuid linkage)")

    baked, stats = bake(lines)
    dangling = validate(baked)
    new_bytes = sum(len(json.dumps(l)) for l in baked)

    print(f"== bake plan: {args.jsonl}")
    for k, v in stats.items():
        print(f"   {k:24s} {v:,}" if isinstance(v, int) else f"   {k:24s} {v}")
    print(f"   bytes (orig serialized)  {orig_bytes:,}")
    print(f"   bytes (baked serialized) {new_bytes:,}  ({100*new_bytes/orig_bytes:.1f}% of orig)")
    print(f"   chain integrity          {'OK' if not dangling else f'BROKEN: {len(dangling)} dangling'}")
    if dangling:
        for u, p in dangling[:5]:
            print(f"      dangling uuid={u} -> missing parent {p}")
        sys.exit(1)

    if not args.apply:
        print("\n   (dry-run; pass --apply to write)")
        return

    out = args.out or args.jsonl
    if not args.out:
        bak = f"{args.jsonl}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        os.rename(args.jsonl, bak)
        print(f"\n   backed up original -> {bak}")
    with open(out, "w") as fh:
        for ln in baked:
            fh.write(json.dumps(ln, ensure_ascii=False) + "\n")
    print(f"   wrote baked transcript -> {out}  ({len(baked)} lines)")


if __name__ == "__main__":
    main()
