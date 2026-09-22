"""FULL window reconciliation per hand seat, MEANS, split at the instruction cut
(2026-08-18) and by week. Every byte of the last request lands in exactly one
bucket so the buckets SUM to the estimate; the estimate is then scaled to the
receipt window so buckets are in receipt tokens. Answers: reads+edits explain
~6k of the +24k rise -- where are the other 18k?"""
import json,glob,os,re,datetime,statistics as st
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
INSTR="through the Bash tool wherever it can accomplish the job"
NOTE="Only you see that command's output"
def bkind(cmd):
    c=(cmd or "").strip()
    if ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c or "tee " in c)) or "sed -i" in c: return "edit:bash"
    if re.search(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s",c) and not re.search(r"\b(grep|rg)\b",c): return "read:bash"
    if re.search(r"\b(grep|rg|find|ls|wc)\b",c): return "search:bash"
    if re.search(r"\b(npm|npx|node|pytest|python3?|make|cargo|jest|vitest|curl)\b",c): return "run:bash"
    if re.search(r"\bgit\b",c): return "git:bash"
    return "other:bash"
def nkind(nm):
    return {"Read":"read:native","Edit":"edit:native","Write":"edit:native","Grep":"search:native","Glob":"search:native",
            "Task":"task","TodoWrite":"other:native","WebFetch":"web","WebSearch":"web"}.get(nm,"other:native")
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
rows=[]
for tk,(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    try:
        rj=json.load(open(f.replace(".request.json",".response.json")))
        u=rj.get("usage") or {}
        win=sum((u.get(x) or 0) for x in ("input_tokens","cache_read_input_tokens","cache_creation_input_tokens"))
    except Exception: continue
    if win<=0: continue
    msgs=b.get("messages") or []
    B=defaultdict(float); N=defaultdict(int)
    B["boot:system"]=len(json.dumps(b.get("system")))/CH
    B["boot:tools"]=len(json.dumps(b.get("tools")))/CH
    has=False; uses={}
    for i,msg in enumerate(msgs):
        role=msg.get("role"); c=msg.get("content")
        s=json.dumps(msg)
        if INSTR in s: has=True
        if role=="system":
            B["sys:audience-note" if NOTE in s else "sys:midconv"]+=len(s)/CH; N["sys:audience-note" if NOTE in s else "sys:midconv"]+=1; continue
        if i==0: B["boot:msg0"]+=len(s)/CH; continue
        if isinstance(c,str):
            B["user:text" if role=="user" else "asst:text"]+=len(c)/CH; continue
        for blk in c or []:
            t=blk.get("type"); n=len(json.dumps(blk))/CH
            if t=="text":
                if role=="user" and "<system-reminder>" in (blk.get("text") or ""):
                    B["sys:audience-note" if NOTE in blk["text"] else "sys:reminder"]+=n; N["sys:audience-note" if NOTE in blk["text"] else "sys:reminder"]+=1
                else: B["user:text" if role=="user" else "asst:text"]+=n
            elif t in ("thinking","redacted_thinking"): B["asst:thinking"]+=n
            elif t=="tool_use":
                nm=blk.get("name"); inp=blk.get("input") or {}
                k=bkind(inp.get("command")) if nm=="Bash" else nkind(nm)
                uses[blk["id"]]=k; B["in:"+k]+=n; N[k]+=1
            elif t=="tool_result":
                k=uses.get(blk.get("tool_use_id"),"orphan"); B["out:"+k]+=n
            else: B["other:"+t]+=n
    est=sum(B.values()); sc=win/est if est else 1
    rows.append(dict(t=tk,d=str(datetime.date.fromtimestamp(mt)),has=has,win=win,sc=sc,
                     B={k:v*sc for k,v in B.items()},N=dict(N),model=b.get("model"),
                     ccv=(re.search(r"cc_version=([\w.\-]+)",json.dumps(b.get("system"))) or [None,None])[1]))
json.dump(rows,open("recon3.json","w"))
def mean(xs): return st.mean(xs) if xs else 0
def table(groups):
    keys=sorted({k for g in groups.values() for r in g for k in r["B"]})
    order=lambda k:(0 if k.startswith("boot") else 1 if k.startswith("sys") else 2 if k.startswith("user") else 3 if k.startswith("asst") else 4 if k.startswith("in:") else 5, k)
    keys.sort(key=order)
    names=list(groups)
    print(f"  {'bucket':<22}"+"".join(f"{n:>13}" for n in names)+("        delta" if len(names)==2 else ""))
    for k in keys:
        vals=[mean([r['B'].get(k,0) for r in groups[n]]) for n in names]
        line=f"  {k:<22}"+"".join(f"{v:13,.0f}" for v in vals)
        if len(names)==2: line+=f"{vals[1]-vals[0]:13,.0f}"
        print(line)
    vals=[mean([r['win'] for r in groups[n]]) for n in names]
    print(f"  {'WINDOW (receipt)':<22}"+"".join(f"{v:13,.0f}" for v in vals)+(f"{vals[1]-vals[0]:13,.0f}" if len(names)==2 else ""))
    print(f"  {'n seats':<22}"+"".join(f"{len(groups[n]):13d}" for n in names))
    print(f"  {'scale est->receipt':<22}"+"".join(f"{mean([r['sc'] for r in groups[n]]):13.3f}" for n in names))
    print("  -- call counts per seat --")
    ck=sorted({k for g in groups.values() for r in g for k in r["N"]})
    for k in ck:
        print(f"  {k:<22}"+"".join(f"{mean([r['N'].get(k,0) for r in groups[n]]):13.1f}" for n in names))
print("=== PRE vs POST instruction cut (2026-08-18) ===")
table({"pre":[r for r in rows if not r["has"]],"post":[r for r in rows if r["has"]]})
wk=defaultdict(list)
for r in rows:
    d=datetime.date.fromisoformat(r["d"]); wk[str(d-datetime.timedelta(days=d.weekday()))].append(r)
print("\n=== by week ===")
table({w:wk[w] for w in sorted(wk)})
print("\n=== cc_version / model by day ===")
bd=defaultdict(lambda:defaultdict(int))
for r in rows: bd[r["d"]][(r["ccv"],r["model"])]+=1
for d in sorted(bd): print(" ",d,dict(bd[d]))
