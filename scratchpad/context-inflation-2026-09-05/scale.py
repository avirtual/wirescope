"""Does superseded-edit weight GROW with ticket length? That is the claim that
would make it the explanation for "long tickets are context hogs" rather than a
flat tax that is equally present on short ones.

Binned by window size. A flat share across bins = ordinary tax, not the cause.
A rising share = the mechanism compounds, and long tickets pay disproportionately.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
PATH=re.compile(r"p\s*=\s*['\"]([^'\"]+)['\"]|(?:cat|tee)\s*>\s*['\"]?([\w./-]+)|sed\s+-i[^ ]*\s+[^ ]+\s+([\w./-]+)")
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
rows=[]
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    r=f.replace(".request.json",".response.json")
    try:
        u=json.load(open(r)).get("usage") or {}
        win=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
    except Exception: continue
    if win<=0: continue
    seq=[]
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); inp=blk.get("input") or {}
            n=len(json.dumps(inp)); p=None
            if nm in ("Edit","Write"): p=inp.get("file_path")
            elif nm=="Bash":
                cmd=inp.get("command","")
                if not (("<<" in cmd and ("python3 -" in cmd or "python -" in cmd or "cat >" in cmd)) or "sed -i" in cmd): continue
                m2=PATH.search(cmd); p=next((g for g in (m2.groups() if m2 else []) if g), None)
            else: continue
            if p: seq.append((os.path.normpath(p),n))
    if len(seq)<3: continue
    lastidx={}
    for i,(p,n) in enumerate(seq): lastidx[p]=i
    sup=sum(n for i,(p,n) in enumerate(seq) if lastidx[p]>i)/CH
    rows.append((win,sup,sup/win*100,len(seq),tk))
bins=[(0,60000),(60000,120000),(120000,180000),(180000,240000),(240000,10**9)]
print(f"{'window bin':<18} {'n':>4} {'medWin':>9} {'medEdits':>9} {'medSupTok':>10} {'medSup%win':>11}")
for lo,hi in bins:
    g=[r for r in rows if lo<=r[0]<hi]
    if len(g)<4: continue
    lbl=f"{lo//1000}k-{hi//1000}k" if hi<10**9 else f"{lo//1000}k+"
    print(f"{lbl:<18} {len(g):>4} {statistics.median([x[0] for x in g]):>9,.0f} "
          f"{statistics.median([x[3] for x in g]):>9,.0f} {statistics.median([x[1] for x in g]):>10,.0f} "
          f"{statistics.median([x[2] for x in g]):>10.1f}%")
big=[r for r in rows if r[0]>=180000]
print(f"\nseats >=180k window: n={len(big)}  median superseded {statistics.median([x[1] for x in big]):,.0f} tok"
      f" ({statistics.median([x[2] for x in big]):.1f}% of window)")
