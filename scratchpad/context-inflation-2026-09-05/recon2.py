"""Same reconciliation, fixed aggregator.

The median run reported 0 for most categories: more than half of seats use only
one route, so the median of a mostly-zero column is zero. For an ACCOUNTING
question the aggregator must be the mean (or the sum) -- it is the only one that
adds up to the total. Medians answer "what does a typical seat do"; they cannot
answer "where did the tokens go".
"""
import json,glob,os,re,statistics,datetime
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
BASH_READ=re.compile(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s+([^|;&]*)")
def bkind(cmd):
    c=cmd.strip()
    if ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c)) or "sed -i" in c: return "write:bash"
    m=BASH_READ.search(c)
    if m and not re.search(r"\b(grep|rg)\b",c): return "read:bash"
    if re.search(r"\b(grep|rg|find)\b",c): return "search:bash"
    if re.search(r"\b(npm|node|pytest|python3? -m|make|cargo|jest)\b",c): return "run/test"
    if re.search(r"\bgit\b",c): return "git"
    return "other:bash"
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
    uses={}; buck=defaultdict(float); ops=defaultdict(int)
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if isinstance(c,str): buck["prose"]+=len(c)/CH; continue
        for blk in c or []:
            t2=blk.get("type")
            if t2=="tool_use":
                nm=blk.get("name"); inp=blk.get("input") or {}
                n=len(json.dumps(inp))/CH
                k2 = bkind(inp.get("command","")) if nm=="Bash" else (
                     "read:native" if nm=="Read" else
                     "write:native" if nm in ("Edit","Write") else
                     "search:native" if nm in ("Grep","Glob") else "other:native")
                uses[blk["id"]]=k2; buck[k2]+=n; ops[k2]+=1
            elif t2=="thinking": buck["thinking"]+=len(json.dumps(blk))/CH
            elif t2=="text": buck["prose"]+=len(json.dumps(blk))/CH
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            buck[uses.get(blk.get("tool_use_id"),"other:native")]+=len(json.dumps(blk))/CH
    for t in (b.get("tools") or []): buck["tools_schema"]+=len(json.dumps(t))/CH
    sb=b.get("system")
    if isinstance(sb,list):
        for s in sb: buck["system"]+=len(json.dumps(s))/CH
    elif isinstance(sb,str): buck["system"]+=len(sb)/CH
    est=sum(buck.values()); scale=win/est if est else 1
    for k2 in buck: buck[k2]*=scale
    d=datetime.date.fromtimestamp(mt); wk=d-datetime.timedelta(days=d.weekday())
    rows.append((wk,win,buck,ops))
byw=defaultdict(list)
for r in rows: byw[r[0]].append(r)
weeks=[w for w in sorted(byw) if len(byw[w])>=8]
CATS=["read:native","read:bash","search:native","search:bash","write:native","write:bash",
      "run/test","git","other:bash","other:native","thinking","prose","system","tools_schema"]
def mean(v): return sum(v)/len(v) if v else 0
print("== MEAN TOKENS PER SEAT WINDOW (accounting view) ==")
hdr="category".ljust(16)+"".join(str(w)[5:].rjust(11) for w in weeks)+"      delta"
print(hdr); print("-"*len(hdr))
deltas=[]
for c in CATS:
    vals=[mean([x[2].get(c,0) for x in byw[w]]) for w in weeks]
    d=vals[-1]-vals[0]; deltas.append((c,d,vals[0],vals[-1]))
    print(c.ljust(16)+"".join(f"{v:>11,.0f}" for v in vals)+f"{d:>+11,.0f}")
tot=[mean([x[1] for x in byw[w]]) for w in weeks]
gr=tot[-1]-tot[0]
print("TOTAL".ljust(16)+"".join(f"{v:>11,.0f}" for v in tot)+f"{gr:>+11,.0f}")
print(f"\n  seats/week: "+"  ".join(f"{str(w)[5:]}={len(byw[w])}" for w in weeks))
print("\n== CONTRIBUTION TO THE RISE ==")
deltas.sort(key=lambda x:-abs(x[1]))
for c,d,a,b2 in deltas:
    if abs(d)<400: continue
    print(f"  {c:<16} {a:>9,.0f} -> {b2:>9,.0f}   {d:>+9,.0f}  ({d/gr*100:>+6.1f}%)")
print("\n== MEAN OP COUNTS PER SEAT ==")
print("category".ljust(16)+"".join(str(w)[5:].rjust(11) for w in weeks)+"      delta")
for c in ["read:native","read:bash","search:native","search:bash","write:native","write:bash","run/test","git"]:
    vals=[mean([x[3].get(c,0) for x in byw[w]]) for w in weeks]
    print(c.ljust(16)+"".join(f"{v:>11,.1f}" for v in vals)+f"{vals[-1]-vals[0]:>+11,.1f}")
tops=[mean([sum(x[3].values()) for x in byw[w]]) for w in weeks]
print("ALL OPS".ljust(16)+"".join(f"{v:>11,.1f}" for v in tops)+f"{tops[-1]-tops[0]:>+11,.1f}")
