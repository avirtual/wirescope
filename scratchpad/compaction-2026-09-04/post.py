#!/usr/bin/env python3
"""Item 4: what the FIRST request after a compaction weighs, and how many
requests until the next compaction in the same session."""
import json
from pathlib import Path
from collections import defaultdict
SC=Path("/private/tmp/claude-501/-Users-bogdan-projects-proxy-lab/5ac1ab3b-f6ac-4ace-ac03-d53240c425ab/scratchpad/compaction")
ev=[x for x in json.load(open(SC/"events.json")) if x["out"]>=200]
roots=[Path("/Users/bogdan/Library/Application Support/clodex/wirescope/logs"),
       Path("/Users/bogdan/projects/proxy-lab/logs_main")]
CONT="This session is being continued from a previous conversation"
def q(xs,p):
    xs=sorted(xs); return xs[min(len(xs)-1,int(round(p*(len(xs)-1))))] if xs else None
bysess=defaultdict(list)
for x in ev: bysess[x["session"]].append(x)

post=[]; gaps=[]; ratio=[]
for sid,rows in bysess.items():
    d=None
    for r in roots:
        if (r/sid).is_dir(): d=r/sid; break
    if not d: continue
    files=sorted(d.glob("*.request.json"), key=lambda f:f.stat().st_mtime)
    names={f.name:i for i,f in enumerate(files)}
    cidx={}
    for x in rows:
        i=names.get(Path(x["file"]).name)
        if i is not None: cidx[i]=x
    for i in sorted(cidx):
        x=cidx[i]
        # first following request that is a resumed (post-compact) turn
        for j in range(i+1, min(i+12, len(files))):
            try: raw=files[j].read_text(errors="replace")
            except Exception: continue
            if CONT not in raw: continue
            try: d2=json.loads(raw)
            except Exception: continue
            b=d2.get("body",d2)
            u=None
            rp=Path(str(files[j]).replace(".request.json",".response.json"))
            if rp.exists():
                try:
                    dd=json.load(open(rp)); u=(dd.get("usage") or (dd.get("body") or {}).get("usage"))
                except Exception: pass
            if u:
                ctx=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
                if ctx>0:
                    post.append(ctx); ratio.append(ctx/x["ctx"])
            break
        # requests until next compaction
        nxt=[k for k in sorted(cidx) if k>i]
        if nxt: gaps.append(nxt[0]-i)
print("=== post-compact FIRST request (summary + re-attached context) ===")
print(f"n={len(post)}  p10 {q(post,.1):,}  p50 {q(post,.5):,}  p90 {q(post,.9):,}  max {max(post):,}")
print(f"as fraction of the PRE-compact window: p10 {q(ratio,.1):.2f}  p50 {q(ratio,.5):.2f}  p90 {q(ratio,.9):.2f}")
print()
print("=== REQUESTS between one compaction and the next (same session) ===")
print(f"n={len(gaps)}  p10 {q(gaps,.1)}  p50 {q(gaps,.5)}  p90 {q(gaps,.9)}  max {max(gaps)}")
