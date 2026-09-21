#!/usr/bin/env python3
"""Count MODEL-EMITTED intent-pointer tokens (`@spill:<hex>`) on the wire.

WHY THIS WORKS (the whole method in one paragraph):
the proxy captures the raw API response, which is UPSTREAM of clodex's spill tee.
So a pointer token found in a `.response.json` `meta.text` was typed by the model,
by construction — the tee's own rewrite can only ever appear later, in the
`.request.json` history of a subsequent turn. No placement heuristic is needed.
Measured 2026-09-21 over 12h: 20 distinct hashes in responses, 0 of them real files;
78 in requests, 51 real. The two populations partition cleanly.

Three traps this avoids (cf. CLAUDE.md, the 611-hits/0-firings lesson):
  1. apparatus matching itself  -> scan ONLY meta.text, never request bodies.
  2. re-shipped history inflating -> dedupe by meta.message_id, count per response.
  3. harness vs model pointers  -> the response/request split above, plus a
     resolvability cross-check against the spill dirs on disk.
A bare `@spill` mention with NO hex is prose ABOUT the token (prompt drafts,
retractions, "never type it" memories) and is reported separately, never counted.

PER-SEAT TREATMENT GATE. After a prompt fix ships, the old and new wordings coexist
on the wire until every pre-reload process exits — a running seat keeps its booted
prompt, and a host on new code proves nothing about a seat started before it. So
treatment is read per REQUEST out of body.system, never from the host's git state,
and every event is tagged [NEW]/[OLD]. Counting a reloaded seat's post-fix turns
together with a stale seat's would measure the rollout, not the fix.

Usage:  scan_spill_emissions.py [window_hours] [--json OUT] [--arm | --control]
        --arm      count only TREATED turns (new receipt wording in the prompt)
        --control  count only UNTREATED turns (pre-fix wording)
        neither    count both; the per-event tag says which
"""
import json, os, re, glob, sys, time, collections, email.utils

CAPTURE_ROOTS = sorted(glob.glob(
    os.path.expanduser("~/Library/Application Support/clodex/*/logs")))
SPILL_GLOB = os.path.expanduser("~/.clodex/spill/*/*.md")

POINTER = re.compile(r"@spill:([0-9a-fA-F]{8,})")  # a concrete pointer
SEAT_RE = re.compile(r"(\d+)-(.+?)-(parent|subagent|ext|unknown)-")

# THE PER-SEAT TREATMENT GATE (added 2026-09-21).
# A seat is only evidence about the fix if the fix was in the prompt it was served.
# Read that off the wire per request, never from the host's git state: the old and
# new prompts coexist for as long as any pre-reload process is still running.
# The NEW wording has already been revised once in place (t1047's "a receipt is
# something Clodex writes after delivery" -> t1053's "the confirmation is …"), so
# match the shortest substring COMMON to every post-fix revision. Pinning the full
# t1047 sentence would have silently re-labelled 119 treated requests as untreated.
OLD_LINE = "Never type `@spill:` yourself"
NEW_LINE = "is something Clodex writes after delivery"

args = [a for a in sys.argv[1:] if not a.startswith("--")]
WIN = float(args[0]) if args else 12.0
OUT = None
if "--json" in sys.argv:
    OUT = sys.argv[sys.argv.index("--json") + 1]
ARM = "--arm" in sys.argv          # count only NEW-line (treated) seats
CTRL = "--control" in sys.argv     # count only OLD-line (untreated) seats

cutoff = time.time() - WIN * 3600
real = set(os.path.basename(f)[:-3] for f in glob.glob(SPILL_GLOB))


def grammar_served(req_path):
    """Which spill grammar line was in the SYSTEM prompt of this request.

    'NEW' / 'OLD' / None. Reads body.system — the capture nests the payload
    under `body`, and scanning the top level silently returns nothing.
    """
    try:
        d = json.load(open(req_path))
    except Exception:
        return None
    sysb = (d.get("body") or {}).get("system") or []
    if isinstance(sysb, str):
        sysb = [{"text": sysb}]
    t = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in sysb)
    if NEW_LINE in t:
        return "NEW"
    if OLD_LINE in t:
        return "OLD"
    return None


files = []
for root in CAPTURE_ROOTS:
    for dirp in os.scandir(root):
        if not dirp.is_dir():
            continue
        try:
            if dirp.stat().st_mtime < cutoff - 3600:
                continue
        except OSError:
            continue
        for f in os.scandir(dirp.path):
            if not f.name.endswith(".response.json"):
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    files.append(f.path)
            except OSError:
                pass

seen, events, mention_only, n200 = set(), [], [], 0
exposure = collections.defaultdict(lambda: collections.Counter())  # seat -> {NEW,OLD}
for p in files:
    try:
        d = json.load(open(p))
    except Exception:
        continue
    if d.get("status_code") != 200:
        continue
    n200 += 1
    meta = d.get("meta") or {}
    txt = meta.get("text") or ""
    mid = meta.get("message_id")

    # Treatment is per REQUEST, so record exposure for every turn, not just hits —
    # denominators come from here. The paired request carries the prompt.
    req = p[:-len(".response.json")] + ".request.json"
    served = grammar_served(req) if os.path.exists(req) else None
    if served:
        exposure[d.get("agent")][served] += 1

    if "@spill" not in txt:
        continue
    if mid in seen:
        continue
    seen.add(mid)
    if ARM and served != "NEW":
        continue
    if CTRL and served != "OLD":
        continue
    dt = email.utils.parsedate_to_datetime((d.get("response_headers") or {}).get("date"))
    rec = {"ts": dt.astimezone(), "agent": d.get("agent"), "model": d.get("model"),
           "session": d.get("session_id"), "mid": mid, "file": p,
           "served": served, "ptrs": POINTER.findall(txt), "text": txt}
    (events if rec["ptrs"] else mention_only).append(rec)

events.sort(key=lambda r: r["ts"])
ptr_total = sum(len(e["ptrs"]) for e in events)
dang = sum(1 for e in events for h in e["ptrs"] if h not in real)
res = ptr_total - dang

arm = "  [--arm: TREATED seats only]" if ARM else ("  [--control: UNTREATED only]" if CTRL else "")
print(f"WINDOW: last {WIN:g}h over {len(CAPTURE_ROOTS)} capture root(s){arm}")
print(f"200-responses scanned: {n200} | distinct responses containing '@spill': {len(seen)}")
print(f"EMISSION EVENTS (response emitted >=1 concrete @spill:<hex>): {len(events)}")
print(f"  pointer tokens in those: {ptr_total}   DANGLING: {dang}   RESOLVABLE: {res}")
print(f"MENTION-ONLY responses (prose about the token, no hex): {len(mention_only)}  <- NOT emissions")
print()
print("TREATMENT EXPOSURE (which grammar line each seat was SERVED, per request):")
print("  a seat is evidence about the fix only for its NEW-line turns; the two")
print("  prompts coexist until every pre-reload process has exited.")
for a in sorted(exposure, key=lambda a: -sum(exposure[a].values())):
    c = exposure[a]
    tag = "TREATED" if c["NEW"] and not c["OLD"] else ("untreated" if c["OLD"] and not c["NEW"] else "MIXED")
    print(f"  {a:40s} NEW={c['NEW']:4d} OLD={c['OLD']:4d}  {tag}")
print()
# THE DENOMINATOR. Zero emissions from a seat that emitted no long bodies is not
# evidence of a fix, it is an empty denominator — the 2026-09-21 arm nearly got
# read that way. A spill FILE is one long body that actually went out, so count
# files by mtime in the same window and report the rate, never the bare count.
print("LONG BODIES ACTUALLY EMITTED in the window (spill files by mtime) — the DENOMINATOR:")
spills = collections.Counter()
for f in glob.glob(SPILL_GLOB):
    try:
        if os.path.getmtime(f) >= cutoff:
            spills[os.path.basename(os.path.dirname(f))] += 1
    except OSError:
        pass
if spills:
    for s, n in spills.most_common():
        print(f"  {s:40s} {n:4d}")
    print(f"  {'TOTAL':40s} {sum(spills.values()):4d}")
else:
    print("  none — ANY emission count over this window is unnormalised, report it as such")
print()
print("BY AGENT (events / pointers / dangling):")
by = collections.defaultdict(lambda: [0, 0, 0])
for e in events:
    a = e["agent"]
    by[a][0] += 1
    by[a][1] += len(e["ptrs"])
    by[a][2] += sum(1 for h in e["ptrs"] if h not in real)
for a, (ev, pt, dg) in sorted(by.items(), key=lambda kv: -kv[1][0]):
    sub = [e for e in events if e["agent"] == a]
    served = collections.Counter(e["served"] for e in sub)
    sv = "/".join(f"{k or '?'}:{v}" for k, v in served.items())
    print(f"  {a:34s} model={sub[0]['model']:16s} events={ev:3d} ptrs={pt:3d} "
          f"dangling={dg:3d} served={sv:10s} {sub[0]['ts']:%m-%d %H:%M} -> {sub[-1]['ts']:%m-%d %H:%M}")
print()
print("BY HOUR (local, events / pointers):")
hr = collections.defaultdict(lambda: [0, 0])
for e in events:
    k = e["ts"].strftime("%m-%d %H")
    hr[k][0] += 1
    hr[k][1] += len(e["ptrs"])
for k in sorted(hr):
    print(f"  {k}:00   events={hr[k][0]:2d}  ptrs={hr[k][1]:2d}")
print()
print("ALL EMISSION EVENTS:")
for e in events:
    print(f"  {e['ts']:%m-%d %H:%M:%S}  [{e['served'] or '?'}]  {e['agent']:34s} {','.join(e['ptrs'])}")

if OUT:
    json.dump([{**e, "ts": e["ts"].isoformat()} for e in events],
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")
