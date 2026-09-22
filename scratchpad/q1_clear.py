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
def parts(path):
    try: b=json.load(open(path)).get("body") or {}
    except Exception: return None
    h=lambda s: hashlib.sha256(s.encode()).hexdigest()[:10]
    sysb=b.get("system") or []
    st=[ (len(x.get("text","")), h(x.get("text",""))) for x in sysb if isinstance(x,dict)]
    return dict(tools=h(json.dumps(b.get("tools") or [],sort_keys=True)), ntools=len(b.get("tools") or []), sys=st)
# per AGENT, ordered by global seq: a clear shows as a new sess whose first real req carries /clear
byagent=defaultdict(list)
for sess,seq,agent,model,ts,path in idx:
    byagent[agent].append((seq,sess,ts,model,path))
out=[]
for agent,v in byagent.items():
    v.sort()
    for i,(seq,sess,ts,model,path) in enumerate(v):
        e=events.get((sess,seq))
        if not (e and e["clear"]): continue
        # previous request by this agent in a DIFFERENT session = last of old id
        prev=None
        for j in range(i-1,-1,-1):
            if v[j][1]!=sess: prev=v[j]; break
        if not prev: continue
        pu,cu=usage(prev[4]),usage(path)
        if not pu or not cu: continue
        # did the OLD session ever carry a compact summary?
        had_compact=any(events.get((prev[1],s))and(events[(prev[1],s)]["summary"] or events[(prev[1],s)]["compact"]) for s,ss,_,_,_ in v if ss==prev[1])
        pp,cp=parts(prev[4]),parts(path)
        out.append(dict(agent=agent,model=model,ts=ts,old=prev[1][:8],new=sess[:8],
            had_compact=had_compact,
            prev_read=pu.get("cache_read_input_tokens",0) or 0, prev_write=pu.get("cache_creation_input_tokens",0) or 0,
            read=cu.get("cache_read_input_tokens",0) or 0, write=cu.get("cache_creation_input_tokens",0) or 0,
            m0=e["m0"], nmsg=e["nmsg"],
            sys_same=(pp and cp and pp["sys"]==cp["sys"]), tools_same=(pp and cp and pp["tools"]==cp["tools"])))
json.dump(out,open("scratchpad/_q1.json","w"))
print("clear transitions with a linked predecessor:",len(out))
