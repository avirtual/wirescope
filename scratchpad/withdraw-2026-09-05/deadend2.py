"""Extend the dead-end scan to SEARCH results (Grep/Glob/bash grep|rg|find) and
to WRONG-CHUNK reads (same file re-read within the next 2 assistant rounds with a
different offset/range = the model withdrew it in spirit). Token share of each
class, plus the size distribution of dead-end results (is the reclaim in a few
fat ones?)."""
import json,glob,os,re,statistics as st
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
PATH_RE=re.compile(r"(?:^|[\s'\"=])((?:/|\./|~/)?[\w.@-]+(?:/[\w.@-]+)+\.[A-Za-z0-9]{1,6}|[\w.-]+\.(?:js|ts|py|md|json|html|css|sh|mjs|cjs))(?=$|[\s'\":;)])")
def kind(name, inp):
    if name=="Read": return "read",[inp.get("file_path")] if inp.get("file_path") else []
    if name in("Grep","Glob"): return "search",[inp.get("pattern") or ""]
    if name=="Bash":
        c=inp.get("command") or ""
        if "sed -i" in c or "<<" in c: return None,[]
        if re.search(r"\b(grep|rg|find)\b",c):
            pats=re.findall(r"(?:grep|rg)\s+(?:-[\w]+\s+)*['\"]([^'\"]+)['\"]",c)
            return "search",pats or [c[:40]]
        if re.search(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s",c):
            return "read",[m.group(1) for m in PATH_RE.finditer(c)]
    return None,[]
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
C=defaultdict(lambda: defaultdict(int)); sizes=defaultdict(list); nseat=0; win=0; alltool=0
for tk,(mt,f) in sorted(seats.items()):
    try: b=json.load(open(f)); b=b.get("body",b)
    except Exception: continue
    msgs=b.get("messages") or []
    if len(msgs)<10: continue
    nseat+=1; win+=sum(len(json.dumps(m)) for m in msgs)
    asst=[]; 
    for msg in msgs:
        s=""
        if msg.get("role")=="assistant":
            c=msg.get("content")
            if isinstance(c,str): s=c
            else:
                for blk in c or []:
                    if blk.get("type")=="text": s+=blk.get("text") or ""
                    elif blk.get("type")=="tool_use": s+=json.dumps(blk.get("input"))
        asst.append(s)
    calls={}; results={}
    for i,msg in enumerate(msgs):
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")=="tool_use": calls[blk["id"]]=(i,blk.get("name"),blk.get("input") or {})
            elif blk.get("type")=="tool_result":
                body=blk.get("content"); s=body if isinstance(body,str) else json.dumps(body)
                results[blk["tool_use_id"]]=(i,len(s),bool(blk.get("is_error")),s)
    for tid,(ri,n,err,s) in results.items(): alltool+=n
    # reads by (idx,path,offset) for wrong-chunk detection
    readlog=[]
    for tid,(ci,name,inp) in calls.items():
        if name=="Read": readlog.append((ci,inp.get("file_path"),(inp.get("offset"),inp.get("limit"))))
        elif name=="Bash":
            c=inp.get("command") or ""
            m=re.search(r"sed -n\s+'?(\d+,\d+)p'?\s+(\S+)",c)
            if m: readlog.append((ci,m.group(2),m.group(1)))
    readlog.sort()
    for tid,(ci,name,inp) in calls.items():
        k,tg=kind(name,inp)
        if not k or tid not in results: continue
        ri,n,err,s=results[tid]
        if err: continue
        C[k]["n"]+=1; C[k]["tok"]+=n
        later="\n".join(asst[ri+1:])
        if k=="read":
            keys=[os.path.basename(p) for p in tg if p]
            dead = bool(keys) and not any(x in later for x in keys)
            # wrong chunk: same path re-read in next 2 assistant rounds w/ different range
            wrong=False
            if not dead:
                nxt=[r for r in readlog if r[0]>ci and r[1] and tg and os.path.basename(r[1])==os.path.basename(tg[0])]
                rounds=[i for i in range(ri+1,len(msgs)) if msgs[i].get("role")=="assistant"][:2]
                wrong=any(r[0] in rounds for r in nxt)
            if dead: C[k]["dead"]+=1; C[k]["deadtok"]+=n; sizes["read"].append(n)
            if wrong: C[k]["wrong"]+=1; C[k]["wrongtok"]+=n
        else:
            # search dead end: no path listed in the output is referenced later, and pattern not reused
            paths=set(os.path.basename(p) for p in PATH_RE.findall(s[:20000]))
            hit = any(p in later for p in paths) if paths else False
            pat_reused = any(p and p in later for p in tg)
            empty = ("No matches" in s or "No files found" in s or len(s.strip())==0)
            if empty: C[k]["empty"]+=1; C[k]["emptytok"]+=n
            elif not hit and not pat_reused: C[k]["dead"]+=1; C[k]["deadtok"]+=n; sizes["search"].append(n)
print(f"seats {nseat}; all tool_result bytes {alltool/CH:,.0f} tok; message bytes {win/CH:,.0f} tok")
for k in ("read","search"):
    d=C[k]
    print(f"{k:7} n={d['n']:5}  dead {d['dead']:4} ({d['dead']/max(1,d['n']):.1%})  dead tok {d['deadtok']/CH:,.0f} ({d['deadtok']/max(1,win):.2%} of msg bytes)"
          + (f"  wrong-chunk {d['wrong']} ({d['wrong']/max(1,d['n']):.1%}) tok {d['wrongtok']/CH:,.0f}" if k=="read" else f"  empty {d['empty']} tok {d['emptytok']/CH:,.0f}"))
    if sizes[k]:
        q=st.quantiles(sizes[k],n=10); print(f"        dead result size tok: p50 {st.median(sizes[k])/CH:,.0f} p90 {q[8]/CH:,.0f} max {max(sizes[k])/CH:,.0f}; share of dead tok in top-10% results {sum(sorted(sizes[k])[-len(sizes[k])//10:])/max(1,sum(sizes[k])):.0%}")
tot=sum(C[k]["deadtok"] for k in C)
print(f"TOTAL dead-end carriage {tot/CH:,.0f} tok = {tot/max(1,win):.2%} of message bytes; per seat {tot/CH/nseat:,.0f} tok")
