import os,json,hashlib
from collections import defaultdict
idx=json.load(open("scratchpad/_idx.json"))
events={(e["sess"],e["seq"]):e for e in json.load(open("scratchpad/_events.json"))}
def usage(path):
    rp=path.replace(".request.json",".response.json")
    if not os.path.exists(rp): return {}
    try: d=json.load(open(rp))
    except Exception: return {}
    u=d.get("usage") or {}
    if not u:
        def f(o):
            if isinstance(o,dict):
                if "cache_read_input_tokens" in o: return o
                for v in o.values():
                    r=f(v)
                    if r: return r
            elif isinstance(o,list):
                for v in o:
                    r=f(v)
                    if r: return r
        u=f(d) or {}
    return u
h=lambda s: hashlib.sha256(s.encode()).hexdigest()[:10]
def parts(path):
    try: b=json.load(open(path)).get("body") or {}
    except Exception: return None
    sysb=[x for x in (b.get("system") or []) if isinstance(x,dict)]
    # EXCLUDE sys[0] = cc_version billing header (out-of-band, not in cache key)
    cache_sys=[(len(x.get("text","")),h(x.get("text",""))) for x in sysb
               if not x.get("text","").startswith("x-anthropic-billing-header")]
    return dict(tools=h(json.dumps(b.get("tools") or [],sort_keys=True)),
                sys=cache_sys, m0=b)
byagent=defaultdict(list)
for sess,seq,agent,model,ts,path in idx:
    byagent[agent].append((seq,sess,ts,model,path))
out=[]
for agent,v in byagent.items():
    v.sort()                       # SEQ order, not ts
    for i,(seq,sess,ts,model,path) in enumerate(v):
        e=events.get((sess,seq))
        if not (e and e["clear"]): continue
        if e["nmsg"]>3: continue   # /clear marker persists; only the FIRST turn has nmsg<=3
        prev=None
        for j in range(i-1,-1,-1):
            if v[j][1]!=sess: prev=v[j]; break
        if not prev: continue
        pu,cu=usage(prev[4]),usage(path)
        if not pu or not cu: continue
        had=any(events.get((prev[1],s)) and (events[(prev[1],s)]["summary"] or events[(prev[1],s)]["compact"])
                for s,ss,_,_,_ in v if ss==prev[1])
        pp,cp=parts(prev[4]),parts(path)
        out.append(dict(agent=agent,model=model,ts=ts,seq=seq,old=prev[1][:8],new=sess[:8],had_compact=had,
            prev_read=pu.get("cache_read_input_tokens",0) or 0,prev_write=pu.get("cache_creation_input_tokens",0) or 0,
            read=cu.get("cache_read_input_tokens",0) or 0,write=cu.get("cache_creation_input_tokens",0) or 0,
            m0=e["m0"],nmsg=e["nmsg"],
            sys_same=bool(pp and cp and pp["sys"]==cp["sys"]),tools_same=bool(pp and cp and pp["tools"]==cp["tools"])))
json.dump(out,open("scratchpad/_q1b.json","w"))
print(f"{'time':>7} {'model':>10} {'compacted':>9} {'prev_read':>9} | {'read':>7} {'write':>7}  sys= tools=")
for x in sorted(out,key=lambda z:z["seq"]):
    print(f"{x['ts']:>7} {x['model']:>10} {str(x['had_compact']):>9} {x['prev_read']:>9,} | {x['read']:>7,} {x['write']:>7,}  {x['sys_same']!s:>5} {x['tools_same']!s:>5}  {x['old']}->{x['new']}")
