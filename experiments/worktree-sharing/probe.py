"""Worktree cache-sharing probe — price the payoff the single-agent A/B can't.

Finding 3 (see ../README.md): the stock CLI bakes machine-local info — the
absolute cwd + git state in `# Environment`, the absolute CLAUDE.md path stamp —
INTO the cache-marked prefix. So two instances of the SAME agent on the SAME
project but in DIFFERENT git worktrees can't share the system-prose / CLAUDE.md
segments: each cold-writes its own byte-identical copy, purely because a cwd
string and a git flag differ. wirescope's relocate/pathstamp transforms peel
those volatile bits out to an uncached tail, so the segments become byte-
identical across worktrees and share as cheap reads.

Each REP is two worktrees of one project; A is run before B in each arm, so A
warms the prefix and B reads it IFF the segment is shareable. We show, per arm:

  * STRUCTURAL (deterministic, warmth-independent): is the cache-marked system
    segment byte-identical across A and B (hash match)? treatment yes, stock no.
  * ECONOMIC: instance-B's first-turn cache_read vs cache_write. Stock cold-
    writes the system prose; treatment reads it. Across N reps -> a distribution.

Usage (run.sh wires this up; lists are comma-separated, rep-aligned):
  python3 probe.py --treat-dir logs_ab_treat --ctrl-dir logs_ab_ctrl \
      --treat-a s1,s2,.. --treat-b s1,s2,.. --ctrl-a s1,s2,.. --ctrl-b s1,s2,..
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
    sysb = [b for b in sysb if not b.get("text", "").startswith(BILLING_PREFIX)]
    last_marked = max((i for i, b in enumerate(sysb) if b.get("cache_control")),
                      default=len(sysb) - 1)
    prefix = "".join(b.get("text", "") for b in sysb[:last_marked + 1])
    return hashlib.sha256(prefix.encode()).hexdigest()[:8], len(prefix)


def _env_in_system(body):
    for b in body.get("system", []):
        if isinstance(b, dict) and ENV_HEAD in b.get("text", ""):
            return True
    return False


def _pathstamp(body):
    for m in body.get("messages", []):
        content = m.get("content", "")
        blocks = content if isinstance(content, list) else [{"text": content}]
        for blk in blocks:
            t = blk.get("text", "") if isinstance(blk, dict) else str(blk)
            if PATHSTAMP_RE.search(t or ""):
                return True
    return False


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
        "env_in_system": _env_in_system(body),
        "pathstamp": _pathstamp(body),
        "seg_hash": seg_hash,
        "seg_chars": seg_len,
        "cache_read": tok.get("cache_read_input_tokens", 0) or 0,
        "cache_write": (tok.get("cache_write_1h_tokens", 0) or 0)
                       + (tok.get("cache_write_5m_tokens", 0) or 0)
                       + (tok.get("cache_write_flat_tokens", 0) or 0),
    }


def _stat(xs):
    return (sum(xs) / len(xs), min(xs), max(xs)) if xs else (0, 0, 0)


def _arm(label, base_dir, a_sids, b_sids):
    print(f"\n## {label}")
    b_reads, b_writes, shared_ok = [], [], 0
    for i, (sa, sb) in enumerate(zip(a_sids, b_sids), 1):
        A = _probe_one(f"{base_dir}/{sa}")
        B = _probe_one(f"{base_dir}/{sb}")
        shared = A["seg_hash"] == B["seg_hash"]
        shared_ok += 1 if shared else 0
        b_reads.append(B["cache_read"]); b_writes.append(B["cache_write"])
        env = "env-in-sys" if B["env_in_system"] else "env→tail  "
        ps = "abs-path" if B["pathstamp"] else "normalized"
        print(f"  rep{i}: A.sys={A['seg_hash']}({A['seg_chars']}c) B.sys={B['seg_hash']}({B['seg_chars']}c)"
              f"  A==B={str(shared):<5}  B[{env} {ps:<10}] read={B['cache_read']:>6} write={B['cache_write']:>6}")
    rm, rlo, rhi = _stat(b_reads); wm, wlo, whi = _stat(b_writes)
    print(f"  -- segment shared across worktrees: {shared_ok}/{len(a_sids)} reps")
    print(f"  -- instance-B cache_read : mean {rm:,.0f}  (min {rlo:,} / max {rhi:,})")
    print(f"  -- instance-B cold write : mean {wm:,.0f}  (min {wlo:,} / max {whi:,})")
    return {"reads": b_reads, "writes": b_writes, "shared_ok": shared_ok, "n": len(a_sids)}


def _split(s):
    return [x for x in s.split(",") if x]


def main():
    ap = argparse.ArgumentParser(description="worktree cache-sharing probe (multi-rep)")
    ap.add_argument("--treat-dir", required=True)
    ap.add_argument("--ctrl-dir", required=True)
    ap.add_argument("--treat-a", required=True, help="comma-separated, rep-aligned")
    ap.add_argument("--treat-b", required=True)
    ap.add_argument("--ctrl-a", required=True)
    ap.add_argument("--ctrl-b", required=True)
    args = ap.parse_args()

    T = _arm("TREATMENT (transforms on)", args.treat_dir, _split(args.treat_a), _split(args.treat_b))
    C = _arm("CONTROL (passthrough / stock bytes)", args.ctrl_dir, _split(args.ctrl_a), _split(args.ctrl_b))

    tr, _, _ = _stat(T["reads"]); tw, _, _ = _stat(T["writes"])
    cr, _, _ = _stat(C["reads"]); cw, _, _ = _stat(C["writes"])
    print(f"\n# HEADLINE — instance-B (the 2nd worktree), mean over reps")
    print(f"  CONTROL    B: cache_read {cr:>7,.0f}  cold_write {cw:>7,.0f}   "
          f"segment shared {C['shared_ok']}/{C['n']}")
    print(f"  TREATMENT  B: cache_read {tr:>7,.0f}  cold_write {tw:>7,.0f}   "
          f"segment shared {T['shared_ok']}/{T['n']}")
    print(f"\n  wirescope lets the 2nd worktree READ ~{tr - cr:,.0f} more tokens of warm prefix")
    print(f"  and cold-WRITE ~{cw - tw:,.0f} fewer — purely because a cwd string + git flag differ.")
    if T["shared_ok"] == T["n"] and C["shared_ok"] == 0:
        print("  VERDICT: finding 3 reproduced in every rep — treatment shares the system")
        print("           segment across worktrees, stock never does.")
    else:
        print(f"  NOTE: expected treat-shared in all reps / ctrl-shared in none; got "
              f"treat {T['shared_ok']}/{T['n']}, ctrl {C['shared_ok']}/{C['n']}.")


if __name__ == "__main__":
    main()
