import os,re,sys,json,time
from collections import defaultdict
D=os.path.expanduser("~/Library/Application Support/clodex/wirescope/logs")
now=time.time(); cut=now-10*86400
pat=re.compile(r'^(\d+)-clodex-(clodex\.t(\d+)\.review-r(\d+))-([0-9a-f]{8})-([a-z]+)-([a-z0-9.\-]+?)-(\d{6})\.(request|response)\.json$')
S=defaultdict(lambda:dict(req=[],resp=[]))
for sd in os.listdir(D):
    p=os.path.join(D,sd)
    if not os.path.isdir(p) or os.path.getmtime(p)<cut: continue
    for fn in os.listdir(p):
        m=pat.match(fn)
        if not m: continue
        seq,agent,ticket,rev,inst,role,model,hms,kind=m.groups()
        fp=os.path.join(p,fn); mt=os.path.getmtime(fp)
        if mt<cut: continue
        e=S[(sd,agent,inst)]
        e.update(sid=sd,agent=agent,ticket=int(ticket),rev=int(rev),inst=inst)
        e['req' if kind=='request' else 'resp'].append(dict(seq=int(seq),role=role,model=model,hms=hms,mtime=mt,size=os.path.getsize(fp),path=fp))
out=[]
for k,e in S.items():
    e['req'].sort(key=lambda r:r['seq']); e['resp'].sort(key=lambda r:r['seq'])
    models=sorted({r['model'] for r in e['req']}); roles=sorted({r['role'] for r in e['req']})
    out.append(dict(sid=e['sid'],agent=e['agent'],ticket=e['ticket'],rev=e['rev'],inst=e['inst'],n_req=len(e['req']),n_resp=len(e['resp']),
                    models=models,roles=roles,first=e['req'][0]['mtime'] if e['req'] else None,last=e['req'][-1]['mtime'] if e['req'] else None,
                    last_req=e['req'][-1]['path'] if e['req'] else None,last_size=e['req'][-1]['size'] if e['req'] else 0,
                    resp_paths=[r['path'] for r in e['resp']],req_paths=[r['path'] for r in e['req']]))
out.sort(key=lambda s:(s['ticket'],s['rev']))
json.dump(out,open(sys.argv[1],'w'))
print(len(out),"review sessions;",sum(s['n_req'] for s in out),"requests")
print(f"{'ticket':>6} {'rev':>3} {'sid':9} {'inst':9} {'req':>4} {'lastMB':>7} {'dur_min':>7} {'age_d':>5} models roles")
for s in out:
    dur=(s['last']-s['first'])/60 if s['first'] else 0
    print(f"{s['ticket']:6d} {s['rev']:3d} {s['sid'][:8]:9} {s['inst']:9} {s['n_req']:4d} {s['last_size']/1e6:7.2f} {dur:7.1f} {(now-s['last'])/86400:5.1f} {','.join(s['models'])} {','.join(s['roles'])}")
