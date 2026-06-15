"""Worktree cache-sharing probe — price the payoff the single-agent A/B can't.

Finding 3 (see ../README.md): the stock CLI bakes machine-local info — the
absolute cwd + git state in `# Environment`, the absolute CLAUDE.md path stamp —
INTO the cache-marked prefix. So two instances of the SAME agent on the SAME
project but in DIFFERENT git worktrees can't share the system-prose / CLAUDE.md
segments: each cold-writes its own byte-identical copy, purely because a cwd
string and a git flag differ. wirescope's relocate/pathstamp transforms peel
those volatile bits out to an uncached tail, so the segments become byte-
identical across worktrees and share as cheap reads.

This script reads the captures of FOUR first-turn sessions:

    treatment: instance-A (worktree A)   instance-B (worktree B)
    control:   instance-A (worktree A)   instance-B (worktree B)

A is run before B in each arm, so A warms the prefix and B reads it IFF the
segment is shareable. We then show, per arm:

  * whether the cache-marked SYSTEM segment is byte-identical across A and B
    (hash match) — the STRUCTURAL claim, independent of warmth/$;
  * instance-B's first-turn cache_read vs cache_write — the ECONOMIC claim:
    control cold-writes the system prose, treatment reads it.

Usage (run.sh wires this up; you can also call it by hand):
  python3 probe.py --treat-dir logs_ab_treat --ctrl-dir logs_ab_ctrl \
      --treat-a SID --treat-b SID --ctrl-a SID --ctrl-b SID
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

BILLING_PREFIX = "x-anthropic-billing-header"
ENV_HEAD = "# Environment"
PATHSTAMP_RE = re.compile(r"Contents of (/[^\s]+/CLAUDE\.md)")


def _main_request(session_dir):
    """The first-turn main request in a session dir: tool-bearing, biggest
    system (skips the tools=0 title side-call)."""
    best, best_sys = None, -1
    for req in sorted(Path(session_dir).glob("*.request.json")):
        body = json.load(open(req)).get("body", {})
        if len(body.get("tools", []) or []) <= 0:
            continue                      # title side-call / non-agent
        syslen = sum(len(b.get("text", "")) for b in body.get("system", [])
                     if isinstance(b, dict))
        if syslen > best_sys:
            best, best_sys = req, syslen
    return best


def _system_segment_hash(body):
    """Hash the cache-marked system prefix (all system blocks except the
    billing header, up to & including the last cache_control), cache_control
    stripped. This is what the upstream cache keys on for the system breakpoint.
    Identical hash across two worktrees => the segment SHARES as a read."""
    sysb = [b for b in body.get("system", []) if isinstance(b, dict)]
    # drop the billing-header block (volatile fingerprint, never cached)
    sysb = [b for b in sysb if not b.get("text", "").startswith(BILLING_PREFIX)]
    last_marked = max((i for i, b in enumerate(sysb) if b.get("cache_control")),
                      default=len(sysb) - 1)
    prefix = "".join(b.get("text", "") for b in sysb[:last_marked + 1])
    return hashlib.sha256(prefix.encode()).hexdigest()[:8], len(prefix)


def _env_in_system(body):
    """True if `# Environment` still rides inside a cache-marked system block
    (stock CLI) vs relocated to an uncached tail (wirescope)."""
    for b in body.get("system", []):
        if isinstance(b, dict) and ENV_HEAD in b.get("text", ""):
            return True
    return False


def _pathstamp(body):
    """The absolute CLAUDE.md path stamp in messages[0], if present (stock) /
    normalized away (wirescope). Returns the matched abs path or None."""
    for m in body.get("messages", []):
        content = m.get("content", "")
        blocks = content if isinstance(content, list) else [{"text": content}]
        for blk in blocks:
            t = blk.get("text", "") if isinstance(blk, dict) else str(blk)
            hit = PATHSTAMP_RE.search(t or "")
            if hit:
                return hit.group(1)
    return None


def _cwd(body):
    for b in body.get("system", []):
        t = b.get("text", "") if isinstance(b, dict) else ""
        m = re.search(r"Primary working directory:\s*(\S+)", t)
        if m:
            return m.group(1)
    # relocated env lands in a tail message
    for m in body.get("messages", []):
        c = m.get("content", "")
        blocks = c if isinstance(c, list) else [{"text": c}]
        for blk in blocks:
            t = blk.get("text", "") if isinstance(blk, dict) else str(blk)
            mm = re.search(r"Primary working directory:\s*(\S+)", t or "")
            if mm:
                return mm.group(1)
    return "?"


def _billing(req_path):
    resp = Path(str(req_path).replace(".request.json", ".response.json"))
    if not resp.exists():
        return {}
    return json.load(open(resp)).get("billing", {}).get("tokens", {}) or {}


def _probe_one(session_dir):
    req = _main_request(session_dir)
    if not req:
        sys.exit(f"no tool-bearing main request found under {session_dir}")
    body = json.load(open(req)).get("body", {})
    seg_hash, seg_len = _system_segment_hash(body)
    tok = _billing(req)
    return {
        "sid": Path(session_dir).name[:8],
        "cwd": _cwd(body),
        "env_in_system": _env_in_system(body),
        "pathstamp": _pathstamp(body),
        "seg_hash": seg_hash,
        "seg_chars": seg_len,
        "cache_read": tok.get("cache_read_input_tokens", 0) or 0,
        "cache_write": (tok.get("cache_write_1h_tokens", 0) or 0)
                       + (tok.get("cache_write_5m_tokens", 0) or 0)
                       + (tok.get("cache_write_flat_tokens", 0) or 0),
        "input": tok.get("input_tokens", 0) or 0,
    }


def _fmt(p):
    env = "in system[]  " if p["env_in_system"] else "relocated→tail"
    ps = "abs-path" if p["pathstamp"] else "normalized"
    return (f"  {p['sid']}  cwd={p['cwd']:<28} env={env}  claudemd-stamp={ps:<10} "
            f"sysseg={p['seg_hash']}({p['seg_chars']}c)  "
            f"read={p['cache_read']:>6}  write={p['cache_write']:>6}  in={p['input']:>5}")


def _arm(label, a_dir, b_dir):
    A, B = _probe_one(a_dir), _probe_one(b_dir)
    print(f"\n## {label}")
    print("  instance-A (warms the prefix):")
    print(_fmt(A))
    print("  instance-B (different worktree — does it SHARE A's segment?):")
    print(_fmt(B))
    shared = A["seg_hash"] == B["seg_hash"]
    print(f"  -> system segment hash A==B ? {shared}   "
          f"({'SHARED as a read' if shared else 'DIFFERS — B must cold-write it'})")
    return A, B, shared


def main():
    ap = argparse.ArgumentParser(description="worktree cache-sharing probe")
    ap.add_argument("--treat-dir", required=True)
    ap.add_argument("--ctrl-dir", required=True)
    ap.add_argument("--treat-a", required=True)
    ap.add_argument("--treat-b", required=True)
    ap.add_argument("--ctrl-a", required=True)
    ap.add_argument("--ctrl-b", required=True)
    args = ap.parse_args()

    tA, tB, t_shared = _arm("TREATMENT (transforms on)",
                            f"{args.treat_dir}/{args.treat_a}",
                            f"{args.treat_dir}/{args.treat_b}")
    cA, cB, c_shared = _arm("CONTROL (passthrough / stock bytes)",
                            f"{args.ctrl_dir}/{args.ctrl_a}",
                            f"{args.ctrl_dir}/{args.ctrl_b}")

    print("\n# HEADLINE — instance-B (the 2nd worktree) first turn")
    print(f"  CONTROL    B: cache_read={cB['cache_read']:>6}  cache_write={cB['cache_write']:>6}"
          f"   (system segment {'SHARED' if c_shared else 'COLD-WRITTEN'})")
    print(f"  TREATMENT  B: cache_read={tB['cache_read']:>6}  cache_write={tB['cache_write']:>6}"
          f"   (system segment {'SHARED' if t_shared else 'COLD-WRITTEN'})")
    gained = tB["cache_read"] - cB["cache_read"]
    print(f"\n  wirescope lets the 2nd worktree READ ~{gained:,} more tokens of warm prefix")
    print("  that the stock CLI cold-writes — purely because a cwd string + git flag differ.")
    if t_shared and not c_shared:
        print("  VERDICT: finding 3 reproduced — treatment shares the system segment across")
        print("           worktrees, stock does not.")
    else:
        print(f"  NOTE: structural expectation was treat-shared/ctrl-not; got "
              f"treat={t_shared} ctrl={c_shared}. Check warmth window / rerun A then B.")


if __name__ == "__main__":
    main()
