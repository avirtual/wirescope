"""Fleet scan: the CLI's bash_output_audience_note on the wire.

Counts copies in the LAST request of each session (accreted carriage), not
across all requests -- history re-ships every turn, so summing per-request
counts would measure session length, not how often the CLI emits the note.
"""
import json,os,sys,glob
from collections import defaultdict

LD=sys.argv[1]
N="Only you see that command's output"
rows=[]
sess=sorted(os.listdir(LD))
for s in sess:
    d=os.path.join(LD,s)
    if not os.path.isdir(d): continue
    reqs=glob.glob(os.path.join(d,"*.request.json"))
    if not reqs: continue
    # group by agent: each agent has its own lineage
    byagent=defaultdict(list)
    for r in reqs:
        base=os.path.basename(r)
        try: agent=base.split("-",1)[1].rsplit("-",1)[0]
        except Exception: continue
        byagent[agent].append(r)
    for agent,fs in byagent.items():
        fs.sort(key=lambda p: os.path.getmtime(p))
        last=fs[-1]
        try: b=json.load(open(last))
        except Exception: continue
        b=b.get("body",b)
        msgs=b.get("messages") or []
        cnt=0; chars=0
        for m in msgs:
            c=m.get("content")
            t=c if isinstance(c,str) else json.dumps(c)
            k=t.count(N)
            if k:
                cnt+=k; chars+=k*148
        rows.append((s,agent,b.get("model"),len(fs),cnt,chars))
rows.sort(key=lambda r:-r[4])
tot=sum(r[4] for r in rows)
print(f"sessions/agents scanned: {len(rows)}   total copies in final requests: {tot}")
print("\n--- top 25 by copies carried in the LAST request ---")
print(f"{'copies':>6} {'chars':>7} {'reqs':>5}  {'model':<22} agent")
for s,a,m,nr,c,ch in rows[:25]:
    if not c: break
    print(f"{c:>6} {ch:>7} {nr:>5}  {str(m):<22} {a[:60]}")

bymodel=defaultdict(lambda:[0,0,0])
for s,a,m,nr,c,ch in rows:
    e=bymodel[str(m)]; e[0]+=1; e[1]+=c; e[2]+= (1 if c else 0)
print("\n--- by model ---")
print(f"{'agents':>7} {'w/note':>7} {'copies':>7}  model")
for m,(n,c,wn) in sorted(bymodel.items(), key=lambda kv:-kv[1][1]):
    print(f"{n:>7} {wn:>7} {c:>7}  {m}")
