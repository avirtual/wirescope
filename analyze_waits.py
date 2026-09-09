#!/usr/bin/env python3
"""Scan wirescope captures for wait-polling loops: runs of >=3 consecutive
tool-only assistant turns that repeat a near-identical read-only command.

Usage: python3 analyze_waits.py [--logs DIR] [--out report.json] [--print-top N]
       [--since YYYY-MM-DD[THH:MM]] [--only SUBSTR] [--text-noise N]

--since keeps only sessions whose FIRST capture is at or after that instant,
so a re-run measures seats spawned after a prompt change, not old history.
First run 2026-09-09: 6/470 hand sessions, $28, all waiting on their own
run-tests digest; clodex moved the "end your turn" line under the exec at 11:54.
"""
import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_busts import _agent_of, _load_json, _norm  # noqa: E402

DEFAULT_LOGS = os.path.expanduser("~/Library/Application Support/clodex/wirescope/logs")
MAX_TOKENS_RE = re.compile(rb'"max_tokens":\s*(\d+)')
MIN_RUN = 3
TEXT_NOISE = 40  # an assistant "text" this short is still a tool-only turn (overridable)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# (class, regex on the NORMALISED command). First match wins.
RAW_IDENTITY = {"file-peek", "read", "glob", "grep"}  # paging through a file is not polling
RETRY_RE = re.compile(
    r"^(?:cd \S+ (?:&&|;) )?(?:npm (?:test|run)|npx (?:jest|vitest|tsc|mocha)|pytest|python3? -m pytest|cargo (?:test|build|check)|go (?:test|build|vet)|make\b|tsc\b|node --test|jest\b|vitest\b|mvn\b|gradle\b|rspec\b|ruff\b|eslint\b|flake8\b|mypy\b|black\b)")
POLL_SHAPES = [
    ("date", re.compile(r"^date\b")),
    ("sleep-loop", re.compile(r"^(?:while|until|for)\b.*\bsleep\b")),
    ("sleep+check", re.compile(r"^sleep #\s*(?:&&|;|\|\|)\s*\S")),
    ("sleep", re.compile(r"^sleep #\s*$")),
    ("jobs", re.compile(r"^jobs\b")),
    ("ps/pgrep", re.compile(r"^(?:ps|pgrep|pkill -#|kill -# |lsof)\b")),
    ("git-peek", re.compile(r"^git (?:status|log|diff|show|branch|rev-parse|stash list)\b")),
    ("curl", re.compile(r"^(?:curl|wget|http)\b")),
    ("file-peek", re.compile(r"^(?:ls|cat|tail|head|wc|stat|test -[fe]|\[ -[fe]|find|du|tree|sed -n|grep|rg|awk)\b")),
    ("inbox-peek", re.compile(r"clodex/messages")),
]
SELF_LAUNCH_RE = re.compile(r"\[agent:(?:exec|term)\b")
PEER_RE = re.compile(r"\[agent:dm\b")
MONITOR_RE = re.compile(r"\[agent:exec clodex-monitor\b")
REMIND_RE = re.compile(r"\[agent:remind\b")
BG_AMP_RE = re.compile(r"(?:&\s*$|&\s*(?:2>|>|;|\)))|\bnohup\b|\bsetsid\b|\bdisown\b")


def tail_summary(path, nbytes=4096):
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - nbytes))
            tail = fh.read()
    except OSError:
        return None
    k = tail.rfind(b'"summary"')
    if k < 0:
        return None
    start = tail.find(b"{", k)
    depth = 0
    for i in range(start, len(tail)):
        c = tail[i:i + 1]
        if c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(tail[start:i + 1])
                except ValueError:
                    return None
    return None


TS_RE = re.compile(rb'"ts":\s*"([^"]+)"')


def head_ts(path):
    try:
        with open(path, "rb") as fh:
            m = TS_RE.search(fh.read(512))
        return m.group(1).decode() if m else None
    except OSError:
        return None


def is_ping(path, summ):
    if summ and "keepwarm" in summ:
        return bool(summ["keepwarm"])
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    k = head.find(b'"body"')
    m = MAX_TOKENS_RE.search(head, k if k >= 0 else 0)
    if m is not None:
        return int(m.group(1)) == 1 and bool((summ or {}).get("n_tools"))
    return False


def classify_cmd(cmd):
    """Return (class, norm) for a Bash command; class None = not a poll shape."""
    n = _norm(cmd)
    if not n:
        return None, n
    if RETRY_RE.search(n):
        return "retry", n
    for cls, rx in POLL_SHAPES:
        if rx.search(n):
            return cls, n
    return None, n


def turn_shape(msg):
    """For an assistant message, return (kind, key, cmds) where kind is
    'poll' | 'retry' | 'edit' | 'other' | 'text'. key identifies the repeated
    action (class + normalised command / file path)."""
    c = msg.get("content")
    if isinstance(c, str):
        return ("text" if len(c.strip()) > TEXT_NOISE else "other"), None, []
    text_len = 0
    tools = []
    for b in c or []:
        t = b.get("type")
        if t == "text":
            text_len += len((b.get("text") or "").strip())
        elif t == "tool_use":
            tools.append(b)
    if text_len > TEXT_NOISE or not tools:
        return ("text" if text_len > TEXT_NOISE else "other"), None, []
    keys, cmds = [], []
    kinds = set()
    for b in tools:
        name = b.get("name") or "?"
        inp = b.get("input") or {}
        if name in EDIT_TOOLS:
            return "edit", None, []
        if name == "Bash":
            cmd = inp.get("command") or ""
            cls, norm = classify_cmd(cmd)
            cmds.append(cmd)
            if cls == "retry":
                kinds.add("retry")
                keys.append(("retry", norm))
            elif cls:
                kinds.add("poll")
                keys.append((cls, re.sub(r"\s+", " ", cmd).strip() if cls in RAW_IDENTITY else norm))
            else:
                kinds.add("other")
        elif name == "Read":
            kinds.add("poll")
            keys.append(("read", f"{inp.get('file_path')}@{inp.get('offset')}+{inp.get('limit')}"))
            cmds.append(f"Read {inp.get('file_path')}")
        elif name in ("Glob", "Grep", "LS"):
            kinds.add("poll")
            keys.append((name.lower(), json.dumps(inp, sort_keys=True)[:200]))
            cmds.append(f"{name} {json.dumps(inp)[:120]}")
        elif name in ("TaskOutput", "BashOutput", "Monitor"):
            kinds.add("poll")
            keys.append((name.lower(), _norm(json.dumps(inp, sort_keys=True))[:200]))
            cmds.append(f"{name} {json.dumps(inp)[:120]}")
        else:
            kinds.add("other")
    if kinds == {"poll"}:
        return "poll", tuple(sorted(keys)), cmds
    if kinds == {"retry"}:
        return "retry", tuple(sorted(keys)), cmds
    return "other", None, cmds


def user_signal(msg):
    """What a user-role message between two assistant turns carries:
    'interrupt' | 'input' (real text from user/peer) | None (tool results /
    reminders only)."""
    c = msg.get("content")
    if isinstance(c, str):
        s = c.strip()
        if "[Request interrupted" in s or "Interrupted while running" in s:
            return "interrupt"
        return "input" if s and not s.startswith("<system-reminder>") else None
    sig = None
    for b in c or []:
        t = b.get("type")
        if t == "tool_result":
            cc = b.get("content")
            txt = cc if isinstance(cc, str) else "".join(
                (x.get("text") or "") for x in (cc or []) if isinstance(x, dict))
            if "Interrupted while running" in txt or "[Request interrupted" in txt:
                return "interrupt"
        elif t == "text":
            s = (b.get("text") or "").strip()
            if "[Request interrupted" in s:
                return "interrupt"
            if s and not s.startswith("<system-reminder>"):
                sig = "input"
    return sig


def asst_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    return "\n".join((b.get("text") or "") for b in c or [] if b.get("type") == "text")


def find_runs(messages):
    """Yield run dicts over the message list."""
    runs = []
    n = len(messages)
    i = 0
    # pre-compute per-assistant shape
    shapes = {}
    for idx, m in enumerate(messages):
        if m.get("role") == "assistant":
            shapes[idx] = turn_shape(m)
    asst_idx = sorted(shapes)
    k = 0
    while k < len(asst_idx):
        idx = asst_idx[k]
        kind, key, cmds = shapes[idx]
        if kind not in ("poll", "retry"):
            k += 1
            continue
        # extend while consecutive assistant turns share kind+key and nothing
        # but tool_results/reminders sit between them
        j = k
        end_reason = "moved_on"
        while j + 1 < len(asst_idx):
            nxt = asst_idx[j + 1]
            kind2, key2, _ = shapes[nxt]
            between = [messages[x] for x in range(asst_idx[j] + 1, nxt) if messages[x].get("role") == "user"]
            sig = None
            for um in between:
                s = user_signal(um)
                if s == "interrupt":
                    sig = "interrupt"
                    break
                if s == "input":
                    sig = "input"
            if sig:
                end_reason = sig
                break
            if kind2 == kind and key2 == key:
                j += 1
                continue
            end_reason = "moved_on"
            break
        else:
            end_reason = "history_end"
        length = j - k + 1
        if length >= MIN_RUN:
            first, last = asst_idx[k], asst_idx[j]
            # if the run was the last assistant turn, check trailing user msgs
            if end_reason == "history_end":
                for x in range(last + 1, n):
                    if messages[x].get("role") == "user":
                        s = user_signal(messages[x])
                        if s:
                            end_reason = s
                            break
            # preceding assistant text (last text-bearing assistant msg)
            pre = ""
            launched = "unknown"
            for x in range(first - 1, -1, -1):
                m = messages[x]
                if m.get("role") != "assistant":
                    continue
                t = asst_text(m).strip()
                if len(t) > TEXT_NOISE and not pre:
                    pre = t
                if x < first - 20 and pre:
                    break
            # self-launch detection: the last 8 assistant msgs before the run
            win = [messages[x] for x in range(0, first) if messages[x].get("role") == "assistant"]
            for m in reversed(win):
                t = asst_text(m)
                c = m.get("content")
                blocks = c if isinstance(c, list) else []
                bg = any(b.get("type") == "tool_use" and b.get("name") == "Bash"
                         and ((b.get("input") or {}).get("run_in_background")
                              or BG_AMP_RE.search((b.get("input") or {}).get("command") or ""))
                         for b in blocks)
                sm = any(b.get("type") == "tool_use" and b.get("name") in ("SendMessage", "Agent", "Task") for b in blocks)
                if bg or SELF_LAUNCH_RE.search(t):
                    launched = "self"
                    break
                if sm or PEER_RE.search(t):
                    launched = "peer"
                    break
            runs.append({
                "kind": kind,
                "shape": key[0][0] if key else "?",
                "norm": " || ".join(x[1][:120] for x in key) if key else "",
                "example": (cmds[0] if cmds else "")[:160],
                "length": length,
                "first_idx": first,
                "last_idx": last,
                "end": end_reason,
                "launched": launched,
                "preceding": pre[:200],
            })
        k = j + 1
    return runs


def usage_facts(messages):
    """Counts of the 'correct alternative' signals over a history."""
    bg = 0
    monitor = 0
    exec_intent = 0
    inshell_wait = 0
    remind = 0
    for m in messages:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            t = c
            blocks = []
        else:
            blocks = c or []
            t = asst_text(m)
        monitor += len(MONITOR_RE.findall(t))
        remind += len(REMIND_RE.findall(t))
        exec_intent += len(SELF_LAUNCH_RE.findall(t))
        for b in blocks:
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                inp = b.get("input") or {}
                if inp.get("run_in_background"):
                    bg += 1
                cls, _ = classify_cmd(inp.get("command") or "")
                if cls == "sleep-loop":
                    inshell_wait += 1
    return {"run_in_background": bg, "monitor_intent": monitor, "remind_intent": remind,
            "exec_intent": exec_intent, "inshell_wait_calls": inshell_wait}


def scan_dir(sd):
    """Group captures by seat (route, role, agent_id); return per-group
    {'best': path, 'n': n_messages, 'caps': [(n_messages, est_usd)]}."""
    groups = {}
    for p in sd.glob("*.request.json"):
        stem = p.name[: -len(".request.json")]
        summ = tail_summary(p)
        if summ is None:
            continue
        if not summ.get("n_tools"):
            continue
        if is_ping(p, summ):
            continue
        route = _agent_of(stem)
        role = summ.get("role") or "?"
        gid = (route, role, summ.get("agent_id"))
        rec = _load_json(sd / f"{stem}.response.json") or {}
        usd = ((rec.get("billing") or {}).get("est_usd")) or 0.0
        toks = (rec.get("billing") or {}).get("tokens") or {}
        ctx = (toks.get("input_tokens") or 0) + (toks.get("cache_read_input_tokens") or 0) + \
              (toks.get("cache_write_flat_tokens") or 0)
        g = groups.setdefault(gid, {"best": None, "n": -1, "caps": []})
        n = summ.get("n_messages") or 0
        g["caps"].append((n, usd, stem, head_ts(p), ctx))
        if n > g["n"]:
            g["n"], g["best"] = n, p
    return groups


def price_run(run, caps):
    """Sum receipts whose n_messages lies in the run's request range; fall
    back to length x nearest per-request cost when receipts are missing."""
    lo, hi = run["first_idx"], run["last_idx"] + 1
    inrange = sorted(c for c in caps if lo <= c[0] <= hi)
    tss = [c[3] for c in inrange if c[3]]
    gaps = []
    for x, y in zip(tss, tss[1:]):
        try:
            gaps.append((_dt(y) - _dt(x)).total_seconds())
        except Exception:
            pass
    run["median_gap_s"] = round(statistics.median(gaps), 1) if gaps else None
    run["ctx_tokens"] = max((c[4] for c in inrange), default=0)
    run["tokens_reread"] = sum(c[4] for c in inrange)
    if len(inrange) >= run["length"] - 1:
        return round(sum(c[1] for c in inrange), 4), "receipts", len(inrange)
    near = sorted(caps, key=lambda c: abs(c[0] - lo))[:3]
    per = statistics.median(c[1] for c in near) if near else 0.0
    return round(per * run["length"], 4), "estimate", len(inrange)


def _first_capture_ts(sd):
    """Session start = mtime of its lowest-seq capture (sequence, not name order)."""
    best = None
    for p in sd.glob("*.request.json"):
        m = re.match(r"(\d+)-", p.name)
        if not m:
            continue
        k = (int(m.group(1)), p.stat().st_mtime)
        if best is None or k < best:
            best = k
    return best[1] if best else float("inf")


def _dt(s):
    from datetime import datetime
    return datetime.fromisoformat(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--out", default=None)
    ap.add_argument("--print-top", type=int, default=0)
    ap.add_argument("--only", default=None, help="substring filter on session dir")
    ap.add_argument("--since", default=None,
                    help="keep sessions whose first capture is >= this local time (YYYY-MM-DD[THH:MM])")
    ap.add_argument("--text-noise", type=int, default=40,
                    help="assistant text up to this many chars still counts as a poll turn")
    a = ap.parse_args()
    globals()["TEXT_NOISE"] = a.text_noise
    logs = Path(a.logs)
    cohorts = {"hand": defaultdict(list), "nonhand": defaultdict(list)}
    sessions = {"hand": 0, "nonhand": 0}
    seats = {"hand": 0, "nonhand": 0}
    usage = {"hand": Counter(), "nonhand": Counter()}
    usage_sessions = {"hand": Counter(), "nonhand": Counter()}
    retry_runs = {"hand": 0, "nonhand": 0}
    all_runs = []
    dirs = [d for d in logs.iterdir() if d.is_dir() and (not a.only or a.only in d.name)]
    if a.since:
        import datetime as _dt
        fmt = "%Y-%m-%dT%H:%M" if "T" in a.since else "%Y-%m-%d"
        since_ts = _dt.datetime.strptime(a.since, fmt).timestamp()
        dirs = [d for d in dirs if _first_capture_ts(d) >= since_ts]
    for di, sd in enumerate(sorted(dirs)):
        groups = scan_dir(sd)
        if not groups:
            continue
        seen_cohort = set()
        for (route, role, aid), g in groups.items():
            cohort = "hand" if ".hand-" in route else "nonhand"
            rec = _load_json(g["best"])
            body = (rec or {}).get("body") or {}
            msgs = body.get("messages") or []
            if not msgs:
                continue
            seats[cohort] += 1
            seen_cohort.add(cohort)
            uf = usage_facts(msgs)
            for kk, v in uf.items():
                usage[cohort][kk] += v
                if v:
                    usage_sessions[cohort][kk] += 1
            runs = find_runs(msgs)
            for r in runs:
                if r["kind"] == "retry":
                    retry_runs[cohort] += 1
                    continue
                usd, method, nrec = price_run(r, g["caps"])
                r.update({
                    "session": sd.name, "route": route, "role": role,
                    "ticket": (re.search(r"\.t(\d+)\.", route) or [None, None])[1],
                    "usd": usd, "price_method": method, "receipts_matched": nrec,
                    "cohort": cohort, "history_len": len(msgs),
                    "capture": g["best"].name,
                })
                cohorts[cohort][sd.name].append(r)
                all_runs.append(r)
        for c in seen_cohort:
            sessions[c] += 1
        if di % 300 == 0:
            print(f"..{di}/{len(dirs)} dirs", file=sys.stderr)

    def summarize(cohort):
        runs = [r for r in all_runs if r["cohort"] == cohort]
        lens = Counter(r["length"] for r in runs)
        usd = [r["usd"] for r in runs]
        return {
            "sessions_scanned": sessions[cohort],
            "seats_scanned": seats[cohort],
            "sessions_with_run": len(cohorts[cohort]),
            "prevalence": round(len(cohorts[cohort]) / max(1, sessions[cohort]), 4),
            "runs": len(runs),
            "retry_runs_excluded": retry_runs[cohort],
            "run_length_dist": dict(sorted(lens.items())),
            "run_length_max": max(lens) if lens else 0,
            "top_shapes": Counter(r["shape"] for r in runs).most_common(12),
            "top_norms": Counter(r["norm"][:80] for r in runs).most_common(15),
            "usd_total": round(sum(usd), 2),
            "tokens_reread_total": sum(r.get("tokens_reread", 0) for r in runs),
            "median_gap_s_dist": Counter(
                ("<10s" if g < 10 else "<60s" if g < 60 else ">=60s") for g in
                (r.get("median_gap_s") for r in runs) if g is not None),
            "usd_median_per_run": round(statistics.median(usd), 4) if usd else 0,
            "usd_mean_per_run": round(statistics.mean(usd), 4) if usd else 0,
            "price_methods": Counter(r["price_method"] for r in runs),
            "end_reasons": Counter(r["end"] for r in runs),
            "launched_by": Counter(r["launched"] for r in runs),
            "alternative_usage_totals": dict(usage[cohort]),
            "alternative_usage_seats": dict(usage_sessions[cohort]),
            "top10": sorted(runs, key=lambda r: -r["usd"])[:10],
            "per_session": sorted(({"session": k, "ticket": v[0]["ticket"], "runs": len(v),
                                    "poll_turns": sum(r["length"] for r in v),
                                    "usd": round(sum(r["usd"] for r in v), 2),
                                    "tokens_reread": sum(r.get("tokens_reread", 0) for r in v)}
                                   for k, v in cohorts[cohort].items()), key=lambda x: -x["usd"]),
        }

    out = {"hand": summarize("hand"), "nonhand": summarize("nonhand"),
           "runs": sorted(all_runs, key=lambda r: -r["usd"])}
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)
    for c in ("hand", "nonhand"):
        s = dict(out[c]); s.pop("top10")
        print(c, json.dumps(s, indent=1, default=str))
    if a.print_top:
        for r in out["runs"][: a.print_top]:
            print(json.dumps(r, default=str)[:600])


if __name__ == "__main__":
    main()
