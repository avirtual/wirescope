"""What is occupying the window on a late request -- by category and by SOURCE.

Boot load is a constant (analyze_prefix.py prices it); this prices the ACCUMULATED
conversation, which is what actually grows. Categories follow proxylab's /_context
composition vocab. Then: which individual tool_result / user blocks are biggest,
since a window spike is usually a few fat payloads, not uniform growth.
"""
import json,sys,glob,os
from collections import defaultdict
CH=2.98
f=sys.argv[1]
b=json.load(open(f)); b=b.get("body",b)
msgs=b.get("messages") or []
cat=defaultdict(int); items=[]
sysb=b.get("system")
if isinstance(sysb,list):
    for s in sysb: cat["system"]+=len(json.dumps(s))
elif isinstance(sysb,str): cat["system"]+=len(sysb)
for t in (b.get("tools") or []): cat["tools"]+=len(json.dumps(t))
for i,m in enumerate(msgs):
    role=m.get("role"); c=m.get("content")
    if isinstance(c,str):
        cat[role]+=len(c); items.append((len(c),role,i,"str",c[:100])); continue
    for blk in (c or []):
        t=blk.get("type"); n=len(json.dumps(blk))
        if t=="tool_result":
            cat["tool_results"]+=n
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            items.append((n,"tool_result",i,blk.get("tool_use_id","")[:12],s[:100]))
        elif t=="tool_use":
            cat["tool_calls"]+=n
            items.append((n,"tool_use:"+str(blk.get("name")),i,"",json.dumps(blk.get("input"))[:100]))
        elif t=="thinking":
            cat["thinking"]+=n; items.append((n,"thinking",i,"",""))
        elif t=="text":
            cat[role]+=n; items.append((n,role+":text",i,"",blk.get("text","")[:100]))
        else:
            cat[str(t)]+=n
tot=sum(cat.values())
print(f"file: {os.path.basename(f)}")
r=f.replace(".request.json",".response.json")
try:
    u=json.load(open(r)).get("usage") or {}
    rec=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
    print(f"receipt window = {rec:,} tok   (est {tot/CH:,.0f}, calib x{rec/(tot/CH):.3f})")
    scale=rec/(tot/CH)
except Exception:
    scale=1.0; print("no receipt")
print(f"messages={len(msgs)}\n== BY CATEGORY ==")
for k,v in sorted(cat.items(), key=lambda kv:-kv[1]):
    print(f"  {k:<16} {v/CH*scale:>10,.0f} tok  {v/tot*100:>5.1f}%")
print("\n== TOP 25 INDIVIDUAL BLOCKS ==")
items.sort(key=lambda x:-x[0])
for n,kind,i,extra,prev in items[:25]:
    print(f"  {n/CH*scale:>8,.0f} tok  msg{i:<4} {kind:<26} {prev[:70]!r}")
