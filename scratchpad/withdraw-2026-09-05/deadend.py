"""Dead-end rate of READ results on clodex hand seats: a read (native Read, or
bash cat/head/tail/sed -n on a path) whose target path NEVER recurs in any
LATER assistant message (text or tool_use input) or later tool_result of an
edit. Those are the results a `[wirescope:withdraw]` could have stubbed.
Reports count share and token share (of tool_result carriage and of window)."""
import json,glob,os,re,statistics as st
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
PATH_RE=re.compile(r"(?:^|[\s'\"=])((?:/|\./|~/)?[\w.@-]+(?:/[\w.@-]+)+\.[A-Za-z0-9]{1,6}|[\w.-]+\.(?:js|ts|py|md|json|html|css|sh|mjs|cjs))(?=$|[\s'\":;)])")
def read_targets(name, inp):
    if name=="Read": p=inp.get("file_path"); return [p] if p else []
    if name=="Bash":
        c=inp.get("command") or ""
        if re.search(r"\b(grep|rg)\b",c): return []
        if not re.search(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s",c): return []
        if "sed -i" in c or "<<" in c: return []
        return [m.group(1) for m in PATH_RE.finditer(c)]
    return []
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
tot=defaultdict(int); per=[]
for tk,(mt,f) in sorted(seats.items()):
    try: b=json.load(open(f)); b=b.get("body",b)
    except Exception: continue
    msgs=b.get("messages") or []
    if len(msgs)<10: continue
    # index: assistant-side text per message index (text + tool_use inputs)
    asst_txt=[]
    for msg in msgs:
        if msg.get("role")!="assistant": asst_txt.append(""); continue
        c=msg.get("content"); s=""
        if isinstance(c,str): s=c
        else:
            for blk in c or []:
                if blk.get("type")=="text": s+=blk.get("text") or ""
                elif blk.get("type")=="tool_use": s+=json.dumps(blk.get("input"))
        asst_txt.append(s)
    # tool_use id -> (idx, name, targets); results by id
    calls={}; results={}
    for i,msg in enumerate(msgs):
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")=="tool_use": calls[blk["id"]]=(i,blk.get("name"),blk.get("input") or {})
            elif blk.get("type")=="tool_result":
                body=blk.get("content"); s=body if isinstance(body,str) else json.dumps(body)
                results[blk["tool_use_id"]]=(i,len(s),bool(blk.get("is_error")))
    reads=dead=0; rtok=dtok=0; alltool=0
    for tid,(ri,n,err) in results.items(): alltool+=n
    for tid,(ci,name,inp) in calls.items():
        tg=read_targets(name,inp)
        if not tg or tid not in results: continue
        ri,n,err=results[tid]
        if err: continue
        reads+=1; rtok+=n
        later="\n".join(asst_txt[ri+1:])
        keys=[os.path.basename(p) for p in tg]
        if not any(k in later for k in keys):
            dead+=1; dtok+=n
    win=sum(len(json.dumps(m)) for m in msgs)
    per.append((tk,reads,dead,rtok,dtok,alltool,win))
    tot["reads"]+=reads; tot["dead"]+=dead; tot["rtok"]+=rtok; tot["dtok"]+=dtok; tot["alltool"]+=alltool; tot["win"]+=win
print(f"seats {len(per)}  reads {tot['reads']}  dead-end reads {tot['dead']} ({tot['dead']/max(1,tot['reads']):.1%})")
print(f"read carriage {tot['rtok']/CH:,.0f} tok; dead-end carriage {tot['dtok']/CH:,.0f} tok ({tot['dtok']/max(1,tot['rtok']):.1%} of reads, {tot['dtok']/max(1,tot['alltool']):.1%} of all tool_result bytes, {tot['dtok']/max(1,tot['win']):.1%} of message bytes)")
ds=[d/r for _,r,d,*_ in per if r>=5]
print(f"per-seat dead-end rate (seats with >=5 reads, n={len(ds)}): p25 {st.quantiles(ds,n=4)[0]:.0%} p50 {st.median(ds):.0%} p75 {st.quantiles(ds,n=4)[2]:.0%}")
print(f"per-seat dead-end tok mean {st.mean(d for *_,d,_,_ in [(p[0],p[1],p[2],p[3],p[4],p[5],p[6]) for p in per])/CH:,.0f}")
