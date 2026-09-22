"""Same axis as strip-thinking: OCCUPANCY of the window. For each hand seat's
last request: window (receipt), prior-thinking bytes (what strip-thinking
reclaims), dead-end read+search bytes, wrong-chunk read bytes. Then compaction
pressure: how many hand seats compacted, at what window, growth per request,
and how many requests a withdraw-sized reclaim would delay the compaction."""
import json,glob,os,re,statistics as st
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
PATH_RE=re.compile(r"(?:^|[\s'\"=])((?:/|\./|~/)?[\w.@-]+(?:/[\w.@-]+)+\.[A-Za-z0-9]{1,6}|[\w.-]+\.(?:js|ts|py|md|json|html|css|sh|mjs|cjs))(?=$|[\s'\":;)])")
COMPACT="Your task is to create a detailed summary of the conversation so far"
def kind(name, inp):
    if name=="Read": return "read",[inp.get("file_path")] if inp.get("file_path") else []
    if name in("Grep","Glob"): return "search",[inp.get("pattern") or ""]
    if name=="Bash":
        c=inp.get("command") or ""
        if "sed -i" in c or "<<" in c: return None,[]
        if re.search(r"\b(grep|rg|find)\b",c): return "search",re.findall(r"(?:grep|rg)\s+(?:-[\w]+\s+)*['\"]([^'\"]+)['\"]",c) or [c[:40]]
        if re.search(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s",c): return "read",[m.group(1) for m in PATH_RE.finditer(c)]
    return None,[]
seats=defaultdict(list)
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if m: seats[m.group(1)].append(f)
rows=[]; comp=[]; growth=[]
for tk,fs in seats.items():
    fs.sort(key=os.path.getmtime); f=fs[-1]
    try:
        b=json.load(open(f)); b=b.get("body",b); rj=json.load(open(f.replace(".request.json",".response.json")))
    except Exception: continue
    msgs=b.get("messages") or []
    if len(msgs)<10: continue
    u=rj.get("usage") or {}; win=sum((u.get(x) or 0) for x in ("input_tokens","cache_read_input_tokens","cache_creation_input_tokens"))
    if win<=0: continue
    msgbytes=sum(len(json.dumps(m)) for m in msgs)
    scale=win/(msgbytes/CH+len(json.dumps(b.get("system")))/CH+len(json.dumps(b.get("tools")))/CH)
    asst=[]; think=0; toolres=0
    for msg in msgs:
        s=""
        if msg.get("role")=="assistant":
            c=msg.get("content")
            if isinstance(c,str): s=c
            else:
                for blk in c or []:
                    if blk.get("type")=="text": s+=blk.get("text") or ""
                    elif blk.get("type")=="tool_use": s+=json.dumps(blk.get("input"))
                    elif blk.get("type") in("thinking","redacted_thinking"): think+=len(json.dumps(blk))
        asst.append(s)
    calls={}; results={}
    for i,msg in enumerate(msgs):
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")=="tool_use": calls[blk["id"]]=(i,blk.get("name"),blk.get("input") or {})
            elif blk.get("type")=="tool_result":
                body=blk.get("content"); s=body if isinstance(body,str) else json.dumps(body)
                results[blk["tool_use_id"]]=(i,len(s),bool(blk.get("is_error")),s); toolres+=len(s)
    readpaths=sorted((ci,os.path.basename(tg[0])) for ci,name,inp in calls.values() for k,tg in [kind(name,inp)] if k=="read" and tg and tg[0])
    dead=wrong=0
    for tid,(ci,name,inp) in calls.items():
        k,tg=kind(name,inp)
        if not k or tid not in results: continue
        ri,n,err,s=results[tid]
        if err: continue
        later="\n".join(asst[ri+1:])
        if k=="read":
            keys=[os.path.basename(p) for p in tg if p]
            if keys and not any(x in later for x in keys): dead+=n
            elif keys:
                rounds=[i for i in range(ri+1,len(msgs)) if msgs[i].get("role")=="assistant"][:2]
                if any(ci2 in rounds and bn==keys[0] for ci2,bn in readpaths): wrong+=n
        else:
            paths=set(os.path.basename(p) for p in PATH_RE.findall(s[:20000]))
            if not (any(p in later for p in paths) if paths else False) and not any(p and p in later for p in tg): dead+=n
    nreq=sum(1 for m in msgs if m.get("role")=="assistant")
    rows.append(dict(win=win,think=think/CH*scale,dead=dead/CH*scale,wrong=wrong/CH*scale,toolres=toolres/CH*scale,nreq=nreq))
    # compaction events for this seat: any request whose last real user text carries the compact prompt
    for g in fs:
        try: bb=json.load(open(g)); bb=bb.get("body",bb)
        except Exception: continue
        mm=bb.get("messages") or []
        for msg in reversed(mm):
            if msg.get("role")=="user":
                s=json.dumps(msg.get("content"))
                if COMPACT in s:
                    try:
                        uu=json.load(open(g.replace(".request.json",".response.json"))).get("usage") or {}
                        w=sum((uu.get(x) or 0) for x in ("input_tokens","cache_read_input_tokens","cache_creation_input_tokens"))
                        if w>0: comp.append((tk,w,len(mm)))
                    except Exception: pass
                break
    if nreq>5: growth.append(win/nreq)
n=len(rows); W=st.mean(r["win"] for r in rows)
print(f"seats {n}; mean window {W:,.0f}")
for k,lab in (("toolres","all tool_result"),("think","prior thinking (strip-thinking's class)"),("dead","dead-end read+search"),("wrong","wrong-chunk reads")):
    v=st.mean(r[k] for r in rows); print(f"  {lab:42} mean {v:8,.0f} tok = {v/W:5.1%} of window")
v=st.mean(r["dead"]+r["wrong"] for r in rows); print(f"  {'dead + wrong-chunk':42} mean {v:8,.0f} tok = {v/W:5.1%} of window")
cs=set(t for t,_,_ in comp)
print(f"\ncompactions on hand seats: {len(comp)} events across {len(cs)} of {n} seats ({len(cs)/n:.0%}); window at compaction p50 {st.median(w for _,w,_ in comp):,.0f}" if comp else "\nno compactions found on hand seats")
g=st.median(growth); print(f"window growth per request p50 {g:,.0f} tok/request; mean requests/seat {st.mean(r['nreq'] for r in rows):.0f}")
for lab,v in (("dead-end only",st.mean(r['dead'] for r in rows)),("dead + wrong-chunk",st.mean(r['dead']+r['wrong'] for r in rows))):
    print(f"  a {lab} reclaim ({v:,.0f} tok) delays a compaction by ~{v/g:.1f} requests")
