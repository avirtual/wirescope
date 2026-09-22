"""ACCOUNTING RECONCILIATION, not ratios.

The user's point is an identity: if per-op read cost fell AND per-op write cost
fell, yet the window grew 28%, then the tokens went somewhere. Find where.

Method: for each seat's final window, bucket EVERY token by what it is, then
compare the buckets across weeks in ABSOLUTE tokens. A per-op improvement that
is swamped by op-count growth shows up here as a bucket that grew despite its
unit price falling. No ratios, no normalisation -- just where the bytes are.
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
                uses[blk["id"]]=k2
                buck[k2]+=n; ops[k2]+=1
            elif t2=="tool_result":
                pass
            elif t2=="thinking": buck["thinking"]+=len(json.dumps(blk))/CH
            elif t2=="text": buck["prose"]+=len(json.dumps(blk))/CH
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            k2=uses.get(blk.get("tool_use_id"),"other:native")
            buck[k2]+=len(json.dumps(blk))/CH
    for t in (b.get("tools") or []): buck["tools_schema"]+=len(json.dumps(t))/CH
    sb=b.get("system")
    if isinstance(sb,list):
        for s in sb: buck["system"]+=len(json.dumps(s))/CH
    elif isinstance(sb,str): buck["system"]+=len(sb)/CH
    est=sum(buck.values())
    scale=win/est if est else 1
    for k2 in buck: buck[k2]*=scale
    d=datetime.date.fromtimestamp(mt); wk=d-datetime.timedelta(days=d.weekday())
    rows.append((wk,win,buck,ops))
byw=defaultdict(list)
for r in rows: byw[r[0]].append(r)
CATS=["read:bash","read:native","write:bash","write:native","search:bash","search:native",
      "run/test","git","other:bash","other:native","thinking","prose","system","tools_schema"]
weeks=[w for w in sorted(byw) if len(byw[w])>=8]
print("== MEDIAN ABSOLUTE TOKENS PER SEAT WINDOW, by category and week ==")
hdr="category".ljust(16)+"".join(str(w)[5:].rjust(11) for w in weeks)+"      delta"
print(hdr); print("-"*len(hdr))
first,last=weeks[0],weeks[-1]
deltas=[]
for c in CATS:
    vals=[statistics.median([x[2].get(c,0) for x in byw[w]]) for w in weeks]
    d=vals[-1]-vals[0]
    deltas.append((c,d,vals[0],vals[-1]))
    print(c.ljust(16)+"".join(f"{v:>11,.0f}" for v in vals)+f"{d:>+11,.0f}")
tot=[statistics.median([x[1] for x in byw[w]]) for w in weeks]
print("TOTAL".ljust(16)+"".join(f"{v:>11,.0f}" for v in tot)+f"{tot[-1]-tot[0]:>+11,.0f}")
print("\n== WHO GREW: contribution to the rise ==")
deltas.sort(key=lambda x:-x[1])
gr=tot[-1]-tot[0]
for c,d,a,b2 in deltas:
    if abs(d)<300: continue
    print(f"  {c:<16} {a:>9,.0f} -> {b2:>9,.0f}   {d:>+9,.0f}  ({d/gr*100:>+6.1f}% of the rise)")
print("\n== OP COUNTS (median per seat) ==")
print("category".ljust(16)+"".join(str(w)[5:].rjust(11) for w in weeks))
for c in ["read:bash","read:native","write:bash","write:native","search:bash","search:native","run/test"]:
    vals=[statistics.median([x[3].get(c,0) for x in byw[w]]) for w in weeks]
    print(c.ljust(16)+"".join(f"{v:>11,.0f}" for v in vals))
