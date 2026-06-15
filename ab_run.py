"""Drive the SAME headless task through two proxy arms, N times, into a manifest.

This is the convenience harness behind the comparison ab_analyze.py prices. It
runs

    ANTHROPIC_BASE_URL=<A> claude -p "<prompt>" --output-format json
    ANTHROPIC_BASE_URL=<B> claude -p "<prompt>" --output-format json

once per rep (A then B, so neither arm always runs on a colder cache), parses
the session_id the CLI reports, and records it per arm in a manifest. Point
ab_analyze.py at that manifest for an exact, session-pinned comparison — no
guessing which capture dirs belong to the experiment.

Each `claude -p` starts a FRESH session (new session_id), so reps don't
contaminate each other. The CLI's own total_cost_usd/usage is stored too as a
cheap cross-check (it under-reports 1h writes — the wire numbers from the
proxy capture are authoritative; that's what the analyzer uses).

You still need TWO proxies up first — a normal one (treatment) and one started
with WIRESCOPE_PASSTHROUGH=1 (control), each writing to its OWN LOG_DIR:

    PORT=7800 LOG_DIR=logs_ab_treat ./start_proxy.sh
    PORT=7801 LOG_DIR=logs_ab_ctrl WIRESCOPE_PASSTHROUGH=1 ./start_proxy.sh

Usage:
  python3 ab_run.py "PROMPT"  --a-url http://127.0.0.1:7800 --a-dir logs_ab_treat \
                              --b-url http://127.0.0.1:7801 --b-dir logs_ab_ctrl \
                              -n 5 -o run.json
  python3 ab_run.py @task.txt ...        # prompt from a file
  python3 ab_analyze.py --manifest run.json
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _identity(url):
    """Probe /_identity so we can label arms and sanity-check passthrough."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/_identity", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def _run_one(prompt, base_url, extra):
    env = dict(os.environ, ANTHROPIC_BASE_URL=base_url)
    cmd = ["claude", "-p", prompt, "--output-format", "json", *extra]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        sys.exit("`claude` CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "wall_s": round(time.time() - t0, 1)}
    wall = round(time.time() - t0, 1)
    out = (p.stdout or "").strip()
    rec = {"wall_s": wall, "exit": p.returncode}
    try:
        j = json.loads(out)
        rec["session_id"] = j.get("session_id")
        rec["cli_cost_usd"] = j.get("total_cost_usd")
        rec["num_turns"] = j.get("num_turns")
        rec["usage"] = j.get("usage")
        rec["is_error"] = j.get("is_error")
    except Exception:
        rec["error"] = "could not parse --output-format json"
        rec["stderr"] = (p.stderr or "")[-500:]
        rec["stdout_head"] = out[:500]
    return rec


def main():
    ap = argparse.ArgumentParser(description="run a headless task through two proxy arms")
    ap.add_argument("prompt", help="arm-A prompt text, or @file (also arm B unless --b-prompt)")
    ap.add_argument("--b-prompt", help="separate prompt for arm B (text or @file). Use when "
                    "the arms solve the SAME problem but the treatment exploits wirescope "
                    "features (per-subagent tool-strip / omit) a vanilla run can't — comparing "
                    "the IDENTICAL prompt understates the real-world gain.")
    ap.add_argument("--a-url", required=True, help="base URL of arm A (treatment)")
    ap.add_argument("--b-url", required=True, help="base URL of arm B (control)")
    ap.add_argument("--a-dir", required=True, help="arm A LOG_DIR (for the analyzer)")
    ap.add_argument("--b-dir", required=True, help="arm B LOG_DIR (for the analyzer)")
    ap.add_argument("--a-label", default="TREATMENT")
    ap.add_argument("--b-label", default="CONTROL")
    ap.add_argument("-n", "--reps", type=int, default=1)
    ap.add_argument("-o", "--out", default="ab_run.json", help="manifest path")
    ap.add_argument("--claude-arg", action="append", default=[],
                    help="extra arg passed to every `claude` call (repeatable), "
                         "e.g. --claude-arg --tools --claude-arg 'Read Edit Bash'")
    args = ap.parse_args()

    def _load(p):
        return Path(p[1:]).read_text() if p and p.startswith("@") else p
    prompt = _load(args.prompt)
    b_prompt = _load(args.b_prompt) if args.b_prompt else prompt

    # sanity: label arms from /_identity, warn if the control isn't passthrough
    ida, idb = _identity(args.a_url), _identity(args.b_url)
    def _pt(idj):
        return bool((idj or {}).get("capabilities", {}).get("passthrough"))
    if ida and _pt(ida):
        print(f"note: arm A ({args.a_url}) reports passthrough=ON — that's the CONTROL shape.")
    if idb is not None and not _pt(idb):
        print(f"WARNING: arm B ({args.b_url}) is NOT passthrough — control is not a clean "
              f"verbatim forwarder. Restart it with WIRESCOPE_PASSTHROUGH=1.", file=sys.stderr)

    arms = {
        args.a_label: {"label": args.a_label, "base_url": args.a_url,
                       "log_dir": args.a_dir, "passthrough": _pt(ida),
                       "prompt": prompt, "sessions": [], "runs": []},
        args.b_label: {"label": args.b_label, "base_url": args.b_url,
                       "log_dir": args.b_dir, "passthrough": _pt(idb),
                       "prompt": b_prompt, "sessions": [], "runs": []},
    }
    order = [(args.a_label, args.a_url, prompt), (args.b_label, args.b_url, b_prompt)]

    for rep in range(1, args.reps + 1):
        for label, url, pr in order:
            print(f"[rep {rep}/{args.reps}] {label} -> {url} ...", flush=True)
            rec = _run_one(pr, url, args.claude_arg)
            rec["rep"] = rep
            arms[label]["runs"].append(rec)
            sid = rec.get("session_id")
            if sid:
                arms[label]["sessions"].append(sid)
                print(f"    session={sid}  cli_cost=${rec.get('cli_cost_usd')}  "
                      f"turns={rec.get('num_turns')}  {rec.get('wall_s')}s", flush=True)
            else:
                print(f"    FAILED: {rec.get('error')}  exit={rec.get('exit')}", flush=True)

    manifest = {"prompt": prompt, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "reps": args.reps, "arms": arms}
    Path(args.out).write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {args.out}  ->  python3 ab_analyze.py --manifest {args.out}")


if __name__ == "__main__":
    main()
