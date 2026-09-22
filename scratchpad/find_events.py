import os,json,sys
idx=json.load(open("scratchpad/_idx.json"))
def msg0(body):
    msgs=body.get("messages") or []
    if not msgs: return ""
    c=msgs[0].get("content")
    if isinstance(c,str): return c
    return "\n".join(b.get("text","") for b in (c or []) if isinstance(b,dict) and b.get("type")=="text")
CLR="<command-name>/clear</command-name>"
CMP="<command-name>/compact</command-name>"
SUM="This session is being continued from a previous conversation"
out=[]
for i,(sess,seq,agent,model,ts,path) in enumerate(idx):
    if i%4000==0: print("  ..",i,file=sys.stderr)
    try: body=json.load(open(path)).get("body") or {}
    except Exception: continue
    if not (body.get("tools")): continue   # skip title/probe side-calls
    t=msg0(body)
    if not t: continue
    flags=(CLR in t, CMP in t, SUM in t)
    if any(flags):
        out.append(dict(sess=sess,seq=seq,agent=agent,model=model,ts=ts,path=path,
                        clear=flags[0],compact=flags[1],summary=flags[2],m0=len(t),
                        nmsg=len(body.get("messages") or [])))
json.dump(out,open("scratchpad/_events.json","w"))
print("events:",len(out))
from collections import Counter
print(Counter((e["clear"],e["compact"],e["summary"]) for e in out))
