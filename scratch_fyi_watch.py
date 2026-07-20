#!/usr/bin/env python3
"""Auto-inject a mid-loop FYI the instant a genuine tool_result continuation
request is seen on the wire. Fire-once. Scratch-only (:7810)."""
import glob, json, os, sys, time, urllib.request

SESS = "f066575b-2e75-4044-bc29-7e63c01271d7"
DIR = f"logs_deliver_scratch/{SESS}/"
TEXT = "[agent:from reviewer] heads up — CI is green on the wirescope branch, nothing needed from you"
DID = "cfrm-fyi-001"
URL = f"http://localhost:7810/_deliver?session={SESS}"

def last_user_is_toolresult(path):
    try:
        o = json.load(open(path)); b = o.get("body")
        if isinstance(b, str): b = json.loads(b)
        us = [m for m in b["messages"] if m.get("role") == "user"]
        if not us: return False
        c = us[-1].get("content")
        if not isinstance(c, list): return False
        kinds = [x.get("type") for x in c]
        # genuine mid-loop: ends in tool_result, no text-drain appended yet
        return "tool_result" in kinds and "text" not in kinds
    except Exception:
        return False

def newest():
    g = sorted(glob.glob(DIR + "*.request.json"))
    return g[-1] if g else None

seen = set()
deadline = time.time() + 90
print(f"[watch] arming on {SESS[:12]}… fire-once, 90s window", flush=True)
while time.time() < deadline:
    n = newest()
    if n and n not in seen:
        seen.add(n)
        ml = last_user_is_toolresult(n)
        base = os.path.basename(n)[:3]
        print(f"[watch] req {base} mid-loop={ml}", flush=True)
        if ml:
            body = json.dumps({"delivery_id": DID, "text": TEXT}).encode()
            req = urllib.request.Request(URL, data=body,
                                         headers={"Content-Type": "application/json"})
            try:
                r = urllib.request.urlopen(req, timeout=5)
                print(f"[watch] POSTED after req {base}: {r.read().decode()}", flush=True)
                print(f"[watch] FIRED — rides the NEXT request. Done.", flush=True)
                sys.exit(0)
            except Exception as e:
                print(f"[watch] POST failed: {e}", flush=True)
                sys.exit(1)
    time.sleep(0.3)
print("[watch] window expired, no mid-loop request seen (agent idle?)", flush=True)
