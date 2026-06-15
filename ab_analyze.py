"""A/B analyzer: price two captured corpora and show what wirescope bought.

The thesis (CLAUDE.md) is that cost is CONTEXT CARRIAGE, not the model thinking.
This script proves (or disproves) it on real wire captures by diffing two arms
of the SAME workflow:

  TREATMENT  — a normal wirescope port (transforms on: omit / tool-trim /
               relocate / strip / sort / ...).
  CONTROL    — the same binary with WIRESCOPE_PASSTHROUGH=1 (byte-verbatim
               forwarder; capture + billing still run). The honest baseline.

Run the identical task through each (see ab_run.py, or just two
`ANTHROPIC_BASE_URL=... claude -p "..."` invocations into two ports whose
LOG_DIRs differ), then point this at the two capture dirs.

It reads the per-request `billing` block the proxy already computes (real wire
tokens, TTL-correct est_$ — NOT the CLI's under-reporting total_cost_usd) from
every `*.response.json`, and the request-side transform records (`ws_omit`,
`ws_tools`, `env_relocate`, ...) to show WHAT the treatment did. Output is a
side-by-side with deltas, broken down by main-line vs subagent (the subagent
roster is the biggest lever), plus per-session means so unequal rep counts
still compare.

Scoping (each arm independently):
  ab_analyze.py DIR_A DIR_B                 # whole corpora
  ab_analyze.py DIR_A DIR_B --last N        # N most-recent session dirs/arm
  ab_analyze.py DIR_A DIR_B --since 1h      # sessions touched in the last 1h
  ab_analyze.py --manifest run.json         # exact sessions ab_run.py recorded

Usage:
  python3 ab_analyze.py [DIR_A DIR_B] [--manifest FILE]
      [--label-a NAME --label-b NAME] [--last N] [--since 30m|EPOCH|ISO]
      [--sessions-a a,b,.. --sessions-b a,b,..] [--verbose]
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

# Mirror of billing.PRICES (USD per 1M tok). Longest-prefix match. The proxy
# already stamped est_usd per request; we re-price only as a fallback when a
# capture is missing est_usd (e.g. an old/partial record) — and we FLAG it.
PRICES = {
    "claude-fable-5":  {"in": 10.0, "out": 50.0, "cw5": 12.5,  "cw1": 20.0, "cr": 1.00},
    "claude-opus-4-5": {"in": 5.0,  "out": 25.0, "cw5": 6.25,  "cw1": 10.0, "cr": 0.50},
    "claude-opus-4-6": {"in": 5.0,  "out": 25.0, "cw5": 6.25,  "cw1": 10.0, "cr": 0.50},
    "claude-opus-4-7": {"in": 5.0,  "out": 25.0, "cw5": 6.25,  "cw1": 10.0, "cr": 0.50},
    "claude-opus-4-8": {"in": 5.0,  "out": 25.0, "cw5": 6.25,  "cw1": 10.0, "cr": 0.50},
    "claude-opus-4":   {"in": 15.0, "out": 75.0, "cw5": 18.75, "cw1": 30.0, "cr": 1.50},
    "claude-sonnet-4": {"in": 3.0,  "out": 15.0, "cw5": 3.75,  "cw1": 6.0,  "cr": 0.30},
    "claude-haiku-4":  {"in": 1.0,  "out": 5.0,  "cw5": 1.25,  "cw1": 2.0,  "cr": 0.10},
}

# transform records on the request side -> a human label + how to size its effect
TRANSFORM_KEYS = {
    "ws_omit": "wirescope omit/replace",
    "ws_tools": "wirescope tool-trim",
    "ws_strip": "wirescope directive-strip",
    "ws_strip_spawn": "wirescope spawn-directive strip",
    "ws_spawner_hint": "wirescope spawner hint",
    "env_relocate": "relocate env->tail",
    "system_strip": "strip system sections",
    "rest_split": "split system rest",
    "tool_sort": "sort tools[]",
    "strip_compact_cache": "strip compact cache",
    "injection": "request injection",
    "syspatch": "shortcircuit syspatch",
}


def _price(model):
    best = None
    for pfx, p in PRICES.items():
        if (model or "").startswith(pfx) and (best is None or len(pfx) > len(best[0])):
            best = (pfx, p)
    return best[1] if best else None


def _est_from_tokens(model, t):
    """Fallback pricing when a capture lacks est_usd. Returns (usd, priced?)."""
    p = _price(model)
    if not p:
        return 0.0, False
    cw5 = t["cw5"] or 0
    cw1 = t["cw1"] or 0
    flat = t["cw_flat"] or 0
    if not cw5 and not cw1 and flat:      # no TTL split -> price flat at 5m
        cw5 = flat
    usd = ((t["in"] or 0) * p["in"] + (t["out"] or 0) * p["out"]
           + (t["cr"] or 0) * p["cr"] + cw5 * p["cw5"] + cw1 * p["cw1"]) / 1e6
    return usd, True


def _blank():
    return {"requests": 0, "turns": 0, "in": 0, "out": 0, "cr": 0, "cw": 0,
            "est_usd": 0.0, "unpriced": 0}


def _parse_since(s):
    if s is None:
        return None
    s = str(s).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s and s[-1] in units and s[:-1].replace(".", "", 1).isdigit():
        return time.time() - float(s[:-1]) * units[s[-1]]
    try:                                  # bare epoch
        return float(s)
    except ValueError:
        pass
    try:                                  # ISO-ish
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return time.mktime(time.strptime(s[:10], "%Y-%m-%d"))


def _session_dirs(root, *, last=None, since=None, only=None):
    """The session subdirectories under a LOG_DIR to include, after scoping.
    A session dir = a directory that holds at least one *.request.json."""
    root = Path(root)
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    dirs = [d for d in root.iterdir()
            if d.is_dir() and next(d.glob("*.request.json"), None)]
    if only:
        only = set(only)
        dirs = [d for d in dirs if d.name in only]
    if since is not None:
        dirs = [d for d in dirs if d.stat().st_mtime >= since]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    if last is not None:
        dirs = dirs[:last]
    return dirs


def _scan_arm(dirs):
    """Walk an arm's session dirs -> totals, per-role totals, transform tally."""
    tot = _blank()
    by_role = defaultdict(_blank)         # "main" | "subagent"
    transforms = defaultdict(lambda: {"reqs": 0, "chars": 0, "tools": 0})
    models = set()
    sessions = []

    for sd in dirs:
        sessions.append(sd.name)
        # request side: transform tally (what the proxy DID to the wire)
        for rf in sd.glob("*.request.json"):
            try:
                rec = json.load(open(rf))
            except Exception:
                continue
            for k, label in TRANSFORM_KEYS.items():
                v = rec.get(k)
                if not v:
                    continue
                e = transforms[label]
                e["reqs"] += 1
                if isinstance(v, dict):
                    e["chars"] += int(v.get("chars_removed") or v.get("removed_chars") or 0)
                    rm = v.get("removed")
                    if isinstance(rm, list):
                        e["tools"] += len(rm)
                    elif isinstance(rm, int):
                        e["tools"] += rm
        # response side: real billed tokens + est_$
        for rp in sd.glob("*.response.json"):
            try:
                resp = json.load(open(rp))
            except Exception:
                continue
            bill = resp.get("billing") or {}
            if not bill.get("billable") or bill.get("endpoint") != "messages":
                continue                  # skip count_tokens / non-billable
            tk = bill.get("tokens") or {}
            t = {
                "in": tk.get("input_tokens") or 0,
                "out": tk.get("output_tokens") or 0,
                "cr": tk.get("cache_read_input_tokens") or 0,
                "cw5": tk.get("cache_write_5m_tokens"),
                "cw1": tk.get("cache_write_1h_tokens"),
                "cw_flat": tk.get("cache_write_flat_tokens"),
            }
            cw = (t["cw5"] or 0) + (t["cw1"] or 0)
            if not cw:
                cw = t["cw_flat"] or 0
            model = bill.get("model") or resp.get("model")
            models.add(model)
            est = bill.get("est_usd")
            priced = est is not None and not bill.get("unpriced")
            if not priced:
                est, ok = _est_from_tokens(model, t)
                priced = ok
            meta = resp.get("meta") or {}
            is_turn = meta.get("stop_reason") == "end_turn"
            role = "subagent" if resp.get("role") in (
                "subagent", "Plan", "verification", "general-purpose") else "main"

            for bucket in (tot, by_role[role]):
                bucket["requests"] += 1
                bucket["turns"] += 1 if is_turn else 0
                bucket["in"] += t["in"]
                bucket["out"] += t["out"]
                bucket["cr"] += t["cr"]
                bucket["cw"] += cw
                bucket["est_usd"] += est or 0.0
                bucket["unpriced"] += 0 if priced else 1

    return {"tot": tot, "by_role": dict(by_role), "transforms": dict(transforms),
            "models": sorted(m for m in models if m), "sessions": sessions}


def _carried(b):
    return b["in"] + b["cr"] + b["cw"]


def _fmt_int(n):
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.1f}"
    return f"{int(n):,}"


def _pct(treat, ctrl):
    if not ctrl:
        return "  n/a"
    return f"{(treat - ctrl) / ctrl * 100:+.1f}%"


def _row(name, b, width=11):
    return (f"  {name:<{width}} {_fmt_int(b['requests']):>5} {_fmt_int(b['turns']):>5} "
            f"{_fmt_int(b['in']):>12} {_fmt_int(b['out']):>10} "
            f"{_fmt_int(b['cr']):>13} {_fmt_int(b['cw']):>13} "
            f"{_fmt_int(_carried(b)):>14} {('$%.4f' % b['est_usd']):>11}")


def _table(title, a_label, a, b_label, b):
    hdr = (f"  {'arm':<11} {'reqs':>5} {'turns':>5} {'uncached_in':>12} "
           f"{'output':>10} {'cache_read':>13} {'cache_write':>13} "
           f"{'carried':>14} {'est_$':>11}")
    print(f"\n{title}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    print(_row(a_label, a))
    print(_row(b_label, b))
    # delta row (treatment - control) with %
    print(f"  {'Δ tok/$':<11} {'':>5} {'':>5} "
          f"{_fmt_int(a['in'] - b['in']):>12} {_fmt_int(a['out'] - b['out']):>10} "
          f"{_fmt_int(a['cr'] - b['cr']):>13} {_fmt_int(a['cw'] - b['cw']):>13} "
          f"{_fmt_int(_carried(a) - _carried(b)):>14} "
          f"{('$%+.4f' % (a['est_usd'] - b['est_usd'])):>11}")
    print(f"  {'Δ %':<11} {'':>5} {'':>5} "
          f"{_pct(a['in'], b['in']):>12} {_pct(a['out'], b['out']):>10} "
          f"{_pct(a['cr'], b['cr']):>13} {_pct(a['cw'], b['cw']):>13} "
          f"{_pct(_carried(a), _carried(b)):>14} "
          f"{_pct(a['est_usd'], b['est_usd']):>11}")


def _per_session(b, n):
    if not n:
        return _blank()
    return {k: (v / n if isinstance(v, float) else v / n)
            for k, v in b.items()}


def main():
    ap = argparse.ArgumentParser(description="A/B: price two captured corpora")
    ap.add_argument("dirs", nargs="*", help="DIR_A DIR_B (treatment then control)")
    ap.add_argument("--manifest", help="run manifest from ab_run.py (overrides dirs)")
    ap.add_argument("--label-a", default="TREATMENT")
    ap.add_argument("--label-b", default="CONTROL")
    ap.add_argument("--last", type=int, help="only the N most-recent session dirs/arm")
    ap.add_argument("--since", help="only sessions touched since (30m|2h|EPOCH|ISO)")
    ap.add_argument("--sessions-a", help="comma list of session ids for arm A")
    ap.add_argument("--sessions-b", help="comma list of session ids for arm B")
    ap.add_argument("--verbose", action="store_true", help="list per-session ids")
    args = ap.parse_args()

    since = _parse_since(args.since)
    if args.manifest:
        man = json.load(open(args.manifest))
        arms = man.get("arms") or {}
        names = list(arms)
        if len(names) != 2:
            sys.exit("manifest must define exactly 2 arms")
        (an, a), (bn, b) = (names[0], arms[names[0]]), (names[1], arms[names[1]])
        a_dir, b_dir = a["log_dir"], b["log_dir"]
        a_label = args.label_a if args.label_a != "TREATMENT" else (a.get("label") or an)
        b_label = args.label_b if args.label_b != "CONTROL" else (b.get("label") or bn)
        a_only, b_only = a.get("sessions"), b.get("sessions")
        print(f"# manifest: {args.manifest}")
        if man.get("prompt"):
            print(f"# prompt:   {man['prompt'][:100]!r}")
    else:
        if len(args.dirs) != 2:
            ap.error("give DIR_A DIR_B (or --manifest)")
        a_dir, b_dir = args.dirs
        a_label, b_label = args.label_a, args.label_b
        a_only = args.sessions_a.split(",") if args.sessions_a else None
        b_only = args.sessions_b.split(",") if args.sessions_b else None

    a_dirs = _session_dirs(a_dir, last=args.last, since=since, only=a_only)
    b_dirs = _session_dirs(b_dir, last=args.last, since=since, only=b_only)
    A = _scan_arm(a_dirs)
    B = _scan_arm(b_dirs)

    na, nb = len(A["sessions"]), len(B["sessions"])
    print(f"\n# wirescope A/B  —  {a_label} vs {b_label}")
    print(f"#   {a_label}: {a_dir}  ({na} sessions, models {A['models']})")
    print(f"#   {b_label}: {b_dir}  ({nb} sessions, models {B['models']})")
    if A["tot"]["unpriced"] or B["tot"]["unpriced"]:
        print(f"#   ! UNPRICED requests: {a_label}={A['tot']['unpriced']} "
              f"{b_label}={B['tot']['unpriced']} (est_$ is a FLOOR — add the model to PRICES)")
    if na != nb:
        print(f"#   ! arms have different session counts ({na} vs {nb}); "
              f"see the per-session means below for a fair compare")

    _table("## TOTAL (whole corpus)", a_label, A["tot"], b_label, B["tot"])

    if na and nb:
        pa, pb = _per_session(A["tot"], na), _per_session(B["tot"], nb)
        _table(f"## PER SESSION (mean over {na} vs {nb} runs)", a_label, pa, b_label, pb)

    # role split — the subagent line is the headline lever
    for role in ("main", "subagent"):
        ra = A["by_role"].get(role)
        rb = B["by_role"].get(role)
        if ra or rb:
            _table(f"## {role.upper()} line", a_label, ra or _blank(),
                   b_label, rb or _blank())

    # what the treatment arm actually did to the wire
    print(f"\n## {a_label} transforms that fired (request side)")
    if A["transforms"]:
        for label, e in sorted(A["transforms"].items(), key=lambda kv: -kv[1]["reqs"]):
            extra = []
            if e["chars"]:
                extra.append(f"~{_fmt_int(e['chars'])} chars stripped")
            if e["tools"]:
                extra.append(f"{e['tools']} tool-schemas removed")
            print(f"  {label:<32} {e['reqs']:>4} reqs" +
                  (f"   ({', '.join(extra)})" if extra else ""))
    else:
        print("  (none — is this arm actually running with transforms on?)")
    if B["transforms"]:
        print(f"\n## ! {b_label} ALSO shows transform records "
              f"({sum(e['reqs'] for e in B['transforms'].values())} reqs) — "
              f"the control is NOT a clean passthrough. Restart it with "
              f"WIRESCOPE_PASSTHROUGH=1.")

    if args.verbose:
        print(f"\n## sessions\n  {a_label}: {A['sessions']}\n  {b_label}: {B['sessions']}")

    # headline
    ca, cb = _carried(A["tot"]), _carried(B["tot"])
    print(f"\n# HEADLINE: {a_label} carried {_pct(ca, cb)} context "
          f"({_fmt_int(ca)} vs {_fmt_int(cb)} tok) "
          f"and cost {_pct(A['tot']['est_usd'], B['tot']['est_usd'])} "
          f"(${A['tot']['est_usd']:.4f} vs ${B['tot']['est_usd']:.4f}) vs the passthrough control.")


if __name__ == "__main__":
    main()
