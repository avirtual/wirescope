import os,json
from collections import defaultdict
idx=json.load(open("scratchpad/_idx.json"))
ev={ (e["sess"],e["seq"]):e for e in json.load(open("scratchpad/_events.json")) }
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
# group by (sess, agent) chronologically
byk=defaultdict(list)
for sess,seq,agent,model,ts,path in idx:
    byk[(sess,agent)].append((seq,ts,model,path))
res=[]
for k,v in byk.items():
    v.sort()
    seen=False
    for i,(seq,ts,model,path) in enumerate(v):
        e=ev.get((k[0],seq))
        has_sum = bool(e and e["summary"])
        if has_sum and not seen:
            seen=True
            if i==0: continue          # session STARTS compacted (resume) -> not a mid-session compact
            pu=usage(v[i-1][3]); cu=usage(path)
            if not cu or not pu: continue
            res.append(dict(sess=k[0],agent=k[1],model=model,ts=ts,
                prev_read=pu.get("cache_read_input_tokens",0) or 0,
                prev_write=pu.get("cache_creation_input_tokens",0) or 0,
                read=cu.get("cache_read_input_tokens",0) or 0,
                write=cu.get("cache_creation_input_tokens",0) or 0,
                inp=cu.get("input_tokens",0) or 0,
                m0=e["m0"], nmsg=e["nmsg"]))
json.dump(res,open("scratchpad/_q2.json","w"))
print("mid-session compacts found:",len(res))
import statistics as st
for mdl in ("opus-5","fable-5-1","sonnet-5"):
    r=[x for x in res if x["model"]==mdl]
    if not r: continue
    w=[x["write"] for x in r]; rd=[x["read"] for x in r]
    print(f"\n{mdl}: n={len(r)}  write median={st.median(w):,.0f} mean={st.mean(w):,.0f}  read median={st.median(rd):,.0f}")
    print(f"   prev-req write median={st.median([x['prev_write'] for x in r]):,.0f}  read median={st.median([x['prev_read'] for x in r]):,.0f}")
