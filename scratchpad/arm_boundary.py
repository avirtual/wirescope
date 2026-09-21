#!/usr/bin/env python3
"""Find the ARM's exact opening boundary: the first request on the wire served
the post-restart (t1065 + t1067) rendering.

Read the treatment off body.system, never off a wall clock — a host restart is
asynchronous and any process that survived it keeps serving the prompt it booted
with (the same reason the per-request OLD/NEW gate exists in the scan). The wall
clock is only used here to BOUND the scan, never to decide treatment.

Usage: arm_boundary.py <since_epoch> [--per-seat]
"""
import os, json, glob, sys, datetime as dt

ROOTS = sorted(glob.glob(
    os.path.expanduser("~/Library/Application Support/clodex/*/logs")))
OLD_TOKEN = "@spill:"          # retired from every agent-visible surface by t1065
NEW_LINE = "is something Clodex writes after delivery"   # survives t1053's reword

since = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
per_seat = "--per-seat" in sys.argv

rows = []
for root in ROOTS:
    for sess in os.listdir(root):
        p = os.path.join(root, sess)
        if not os.path.isdir(p):
            continue
        # cheap dir-level bound: a session dir untouched since `since` holds
        # nothing in the window.
        try:
            if os.path.getmtime(p) < since:
                continue
        except OSError:
            continue
        for f in os.listdir(p):
            if not f.endswith(".request.json"):
                continue
            fp = os.path.join(p, f)
            try:
                mt = os.path.getmtime(fp)
            except OSError:
                continue
            if mt < since:
                continue
            try:
                d = json.load(open(fp))
            except Exception:
                continue
            body = d.get("body") or {}
            if not (body.get("tools") or []):
                continue                      # tools-less title side-call
            sysb = body.get("system") or []
            if isinstance(sysb, str):
                sysb = [{"text": sysb}]
            t = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b)
                          for b in sysb)
            if not t:
                continue
            seat = f.split("-", 1)[1].rsplit("-parent-", 1)[0] \
                if "-parent-" in f else f.split("-", 1)[1][:40]
            rows.append((mt, seat, OLD_TOKEN in t, NEW_LINE in t, sess, f))

rows.sort()
old = [r for r in rows if r[2]]
new = [r for r in rows if not r[2]]
print(f"window opens  : {dt.datetime.fromtimestamp(since)}")
print(f"requests (tool-carrying, with a system prompt): {len(rows)}")
print(f"  still carrying '@spill:'      (OLD, stale process): {len(old)}")
print(f"  no '@spill:'                  (post-t1065)        : {len(new)}")
print(f"  carrying the t1047/t1053 spill grammar line       : "
      f"{sum(1 for r in rows if r[3])}")
if new:
    print(f"\nFIRST post-t1065 request: {dt.datetime.fromtimestamp(new[0][0])}  "
          f"{new[0][1]}  {new[0][5][:48]}")
if old:
    print(f"LAST '@spill:' request  : {dt.datetime.fromtimestamp(old[-1][0])}  "
          f"{old[-1][1]}  {old[-1][5][:48]}")

if per_seat:
    import collections
    agg = collections.defaultdict(lambda: [0, 0, None])
    for mt, seat, has_old, has_line, sess, f in rows:
        a = agg[seat]
        a[0 if has_old else 1] += 1
        if a[2] is None:
            a[2] = mt
    print("\nper seat: OLD / NEW / first request in window")
    for seat, (o, n, first) in sorted(agg.items(), key=lambda kv: kv[1][2]):
        print(f"  {seat:<44} {o:>4} / {n:>4}   "
              f"{dt.datetime.fromtimestamp(first).strftime('%H:%M:%S')}")
