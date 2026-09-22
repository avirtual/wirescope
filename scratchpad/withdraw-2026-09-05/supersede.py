"""EXACT supersession: a successful read whose (path, range) is read AGAIN later
in the session with the SAME range (or both whole-file), and no edit to that path
happened between (so the later read returns the same or newer content = the
earlier body is dead weight by construction). Price at read rate x remaining
requests, minus the tail bust. Needs zero model cooperation."""
import json,glob,os,re,sys
from collections import defaultdict
sys.path.insert(0,"/Users/bogdan/projects/proxy-lab")
from proxylab import billing
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
def rkey(name,inp):
    if name=="Read": return (inp.get("file_path"), inp.get("offset") or 0, inp.get("limit") or 0)
    if name=="Bash":
        c=(inp.get("command") or "").strip()
        m=re.fullmatch(r"cat\s+(?:-n\s+)?(\S+)",c)
        if m: return (m.group(1),0,0)
        m=re.fullmatch(r"sed\s+-n\s+'?(\d+),(\d+)p'?\s+(\S+)",c)
        if m: return (m.group(3),int(m.group(1)),int(m.group(2)))
    return None
def edits_path(name,inp):
    if name in ("Edit","Write"): return inp.get("file_path")
    if name=="Bash":
        c=inp.get("command") or ""
        if "sed -i" in c or "<<" in c or " > " in c: return "*"
    return None
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
T=defaultdict(float); nseat=0; bill=0; sizes=[]
for tk,(mt,f) in sorted(seats.items()):
    try:
        b=json.load(open(f)); b=b.get("body",b)
        rj=json.load(open(f.replace(".request.json",".response.json")))
    except Exception: continue
    msgs=b.get("messages") or []
    if len(msgs)<10: continue
    pr=billing._price_for(model=(rj.get("billing") or {}).get("model") or b.get("model"))
    if not pr: continue
    rd=pr["cache_read"]; wr=pr["cache_write_5m"]; nseat+=1
    d=os.path.dirname(f); tag=re.search(r"(clodex-clodex\.t\d+\.hand-[0-9a-f]+)",f).group(1)
    for g in glob.glob(os.path.join(d,f"*{tag}*.response.json")):
        try: bill+=(json.load(open(g)).get("billing") or {}).get("est_usd") or 0
        except Exception: pass
    asst_idx=[i for i,m in enumerate(msgs) if m.get("role")=="assistant"]; nreq=len(asst_idx)
    events=[]; results={}
    for i,msg in enumerate(msgs):
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")=="tool_result":
                body=blk.get("content"); s=body if isinstance(body,str) else json.dumps(body)
                results[blk["tool_use_id"]]=(i,len(s)/CH,bool(blk.get("is_error")))
    for i,msg in enumerate(msgs):
        c=msg.get("content")
        if msg.get("role")!="assistant" or not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            name=blk.get("name"); inp=blk.get("input") or {}
            k=rkey(name,inp)
            if k and blk["id"] in results and not results[blk["id"]][2]:
                events.append((results[blk["id"]][0],"read",k,results[blk["id"]][1]))
            ep=edits_path(name,inp)
            if ep: events.append((i,"edit",ep,0))
    events.sort()
    for a,(ri,kind,key,tok) in enumerate(events):
        if kind!="read": continue
        T["reads"]+=1
        for rj_,kind2,key2,tok2 in events[a+1:]:
            if kind2=="edit" and (key2=="*" or key2==key[0]): break
            if kind2=="read" and key2==key:
                req_sup=sum(1 for x in asst_idx if x<rj_)
                remaining=max(0,nreq-req_sup)
                T["sup"]+=1; T["sup_tok"]+=tok; sizes.append(tok)
                T["save"]+=tok*remaining*rd/1e6
                span=sum(len(json.dumps(m)) for m in msgs[ri:rj_+1])/CH
                T["cost"]+=span*wr/1e6; T["span_tok"]+=span
                break
print(f"seats {nseat}; bill ${bill:,.2f}; keyed reads {T['reads']:.0f}")
print(f"exactly-superseded reads {T['sup']:.0f} ({T['sup']/max(1,T['reads']):.1%}), {T['sup_tok']:,.0f} tok")
print(f"saving at read rate ${T['save']:,.2f} ({T['save']/bill:.2%}); re-write cost of the busted span ${T['cost']:,.2f} (mean span {T['span_tok']/max(1,T['sup']):,.0f} tok) -> net ${T['save']-T['cost']:,.2f} ({(T['save']-T['cost'])/bill:.2%} of bill)")
import statistics as st
if sizes: print(f"superseded body tok p50 {st.median(sizes):,.0f} p90 {st.quantiles(sizes,n=10)[8]:,.0f}")
