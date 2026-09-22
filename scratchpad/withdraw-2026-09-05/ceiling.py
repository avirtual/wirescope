"""Exact USD ceiling of withdraw: for each dead-end read/search result in a hand
seat's last request, saving = tokens x requests-remaining-after-it x read price
(0.1x of model input); cost = one-time re-write of the withdrawing round's
assistant message + stub at the 1.25x/2x write price. Also the SUPERSESSION
class (a read of a path that is read AGAIN later, any range) as the
no-cooperation alternative. Prices from proxylab.billing.PRICES."""
import json,glob,os,re,sys
from collections import defaultdict
sys.path.insert(0,"/Users/bogdan/projects/proxy-lab")
from proxylab import billing
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
PATH_RE=re.compile(r"(?:^|[\s'\"=])((?:/|\./|~/)?[\w.@-]+(?:/[\w.@-]+)+\.[A-Za-z0-9]{1,6}|[\w.-]+\.(?:js|ts|py|md|json|html|css|sh|mjs|cjs))(?=$|[\s'\":;)])")
def price(model):
    p=billing._price_for(model=model) if hasattr(billing,"_price_for") else None
    return p
def kind(name, inp):
    if name=="Read": return "read",[inp.get("file_path")] if inp.get("file_path") else []
    if name in("Grep","Glob"): return "search",[inp.get("pattern") or ""]
    if name=="Bash":
        c=inp.get("command") or ""
        if "sed -i" in c or "<<" in c: return None,[]
        if re.search(r"\b(grep|rg|find)\b",c):
            return "search",re.findall(r"(?:grep|rg)\s+(?:-[\w]+\s+)*['\"]([^'\"]+)['\"]",c) or [c[:40]]
        if re.search(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s",c):
            return "read",[m.group(1) for m in PATH_RE.finditer(c)]
    return None,[]
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
T=defaultdict(float); nseat=0; bill=0
for tk,(mt,f) in sorted(seats.items()):
    try:
        b=json.load(open(f)); b=b.get("body",b)
        rj=json.load(open(f.replace(".request.json",".response.json")))
    except Exception: continue
    msgs=b.get("messages") or []
    if len(msgs)<10: continue
    model=(rj.get("billing") or {}).get("model") or b.get("model")
    pr=price(model)
    if not pr: continue
    rd=pr["cache_read"]; wr=pr["cache_write_5m"]
    nseat+=1
    # per-seat total bill from all its responses
    d=os.path.dirname(f); stem_tag=re.search(r"(clodex-clodex\.t\d+\.hand-[0-9a-f]+)",f).group(1)
    for g in glob.glob(os.path.join(d,f"*{stem_tag}*.response.json")):
        try: bill+=(json.load(open(g)).get("billing") or {}).get("est_usd") or 0
        except Exception: pass
    asst_idx=[i for i,m in enumerate(msgs) if m.get("role")=="assistant"]; nreq=len(asst_idx)
    asst=[]
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
    readpaths=[]  # (idx, basename)
    for tid,(ci,name,inp_) in calls.items():
        k,tg=kind(name,inp_)
        if k=="read" and tg and tg[0]: readpaths.append((ci,os.path.basename(tg[0])))
    readpaths.sort()
    for tid,(ci,name,inp_) in calls.items():
        k,tg=kind(name,inp_)
        if not k or tid not in results: continue
        ri,n,err,s=results[tid]
        if err: continue
        tok=n/CH
        req_at=sum(1 for a in asst_idx if a<ri)     # requests already made when this landed
        remaining=max(0,nreq-req_at-1)               # requests that re-carry it after the withdraw round
        later="\n".join(asst[ri+1:])
        dead=False
        if k=="read":
            keys=[os.path.basename(p) for p in tg if p]
            dead=bool(keys) and not any(x in later for x in keys)
            # supersession: same basename read again later
            if keys and any(ci2>ci and bn==keys[0] for ci2,bn in readpaths):
                T["sup_tok"]+=tok; T["sup_n"]+=1; T["sup_usd"]+=tok*remaining*rd/1e6
        else:
            paths=set(os.path.basename(p) for p in PATH_RE.findall(s[:20000]))
            dead=not (any(p in later for p in paths) if paths else False) and not any(p and p in later for p in tg)
        if k=="read" and not dead and tg and tg[0]:
            bn=os.path.basename(tg[0])
            rounds=[i for i in range(ri+1,len(msgs)) if msgs[i].get("role")=="assistant"][:2]
            if any(ci2 in rounds and bn2==bn for ci2,bn2 in readpaths):
                T["wrong_n"]+=1; T["wrong_tok"]+=tok; T["wrong_usd"]+=tok*remaining*rd/1e6
        if dead:
            T["dead_n"]+=1; T["dead_tok"]+=tok
            T["save_usd"]+=tok*remaining*rd/1e6
            # bust cost: rewrite of the withdrawing round's assistant msg (the one right after ri) + stub
            nxt=next((a for a in asst_idx if a>ri),None)
            rew=(len(json.dumps(msgs[nxt]))/CH if nxt is not None else 0)+30
            T["cost_usd"]+=rew*wr/1e6
print(f"seats {nseat}; their total bill ${bill:,.2f}")
print(f"dead-end results {T['dead_n']:.0f}, {T['dead_tok']:,.0f} tok; PERFECT-withdraw saving ${T['save_usd']:,.2f} ({T['save_usd']/bill:.2%} of bill), bust cost ${T['cost_usd']:,.2f} -> net ${T['save_usd']-T['cost_usd']:,.2f} ({(T['save_usd']-T['cost_usd'])/bill:.2%})")
print(f"wrong-chunk reads (same file re-read within 2 rounds, other range): {T['wrong_n']:.0f}, {T['wrong_tok']:,.0f} tok, carriage ${T['wrong_usd']:,.2f} ({T['wrong_usd']/bill:.2%}) -> combined ceiling ${T['save_usd']+T['wrong_usd']-T['cost_usd']:,.2f} ({(T['save_usd']+T['wrong_usd']-T['cost_usd'])/bill:.2%})")
print(f"per seat net ${(T['save_usd']-T['cost_usd'])/nseat:.3f}")
print(f"superseded reads (same file read again later): {T['sup_n']:.0f}, {T['sup_tok']:,.0f} tok, carriage ${T['sup_usd']:,.2f} ({T['sup_usd']/bill:.2%} of bill) -- no-cooperation class, but a later read of a DIFFERENT range does not semantically supersede")
