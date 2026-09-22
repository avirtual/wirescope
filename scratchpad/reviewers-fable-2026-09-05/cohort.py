import json,re,glob,os,collections,statistics as st,datetime as dt
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
M={int(k):v for k,v in json.load(open('merges.json')).items()}
pat=re.compile(r'clodex\.t(\d+)\.hand-([0-9a-f]+)-(parent|[a-z-]+?)-(opus-5|fable-5-1|sonnet-5|haiku-[0-9-]+)-\d{6}\.response\.json$')
files=collections.defaultdict(list)
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if not os.path.isdir(p): continue
    for f in os.listdir(p):
        m=pat.search(f)
        if m and 660<=int(m.group(1))<=697: files[int(m.group(1))].append((os.path.join(p,f),m.group(3),m.group(4)))
def g(t,k): return t.get(k) or 0
rows={}
for t,fl in files.items():
    usd=0;req=0;wins=[];ts=[];model='';sub_usd=0;sub_req=0
    for f,role,mdl in sorted(fl):
        r=json.load(open(f)); b=r.get('billing',{}) or {}; tk=b.get('tokens',{}) or {}
        if role=='parent':
            usd+=b.get('est_usd') or 0; req+=1; ts.append(os.path.getmtime(f)); model=model or mdl
            wins.append(g(tk,'input_tokens')+g(tk,'cache_read_input_tokens')+g(tk,'cache_creation_input_tokens'))
        elif (b.get('est_usd') or 0)>0.02: sub_usd+=b['est_usd']; sub_req+=1
    if not ts: continue
    comp=sum(1 for i in range(1,len(wins)) if wins[i]<wins[i-1]*0.6 and wins[i-1]>50000)
    verd=[]
    for v in sorted(glob.glob(os.path.expanduser(f'~/.clodex/projects/wb-wrap-ui-5bc8ce0a/tasks/*/review-t{t}-r*.verdict.md'))):
        txt=open(v).read(); mm=re.search(r'VERDICT\W*([A-Z]+)',txt); verd.append(mm.group(1) if mm else '?')
    mg=M.get(t,{})
    rows[t]=dict(model=model,day=dt.datetime.fromtimestamp(min(ts)).strftime('%m-%d'),start=dt.datetime.fromtimestamp(min(ts)).strftime('%H:%M'),req=req,usd=usd,sub_usd=sub_usd,sub_req=sub_req,wall=(max(ts)-min(ts))/60,maxwin=max(wins)//1000,meanwin=sum(wins)/len(wins)//1000,comp=comp,verd=verd,lines=mg.get('ins',0)+mg.get('dele',0),files=mg.get('files',0))
json.dump({str(k):v for k,v in rows.items()},open('hands_660_697.json','w'))
print(f"{'t':>4} {'day':5} {'st':5} {'model':9} {'lines':>5} {'f':>3} {'req':>4} {'$':>6} {'sub$':>5} {'min':>4} {'maxW':>4} {'mW':>4} cmp verd")
for t,r in sorted(rows.items()):
    print(f"{t:>4} {r['day']:5} {r['start']:5} {r['model']:9} {r['lines']:>5} {r['files']:>3} {r['req']:>4} {r['usd']:>6.2f} {r['sub_usd']:>5.2f} {r['wall']:>4.0f} {r['maxwin']:>4} {r['meanwin']:>4.0f} {r['comp']:>3} {','.join(r['verd'])}")
def coh(name,ts):
    rs=[rows[t] for t in ts if t in rows and rows[t]['lines']>0]
    if not rs: return
    tot=lambda k:sum(r[k] for r in rs)
    perl=[(r['usd']+r['sub_usd'])/r['lines']*100 for r in rs]; reqL=[r['req']/r['lines']*100 for r in rs]
    rounds=[len(r['verd']) for r in rs]; rej=sum(1 for r in rs if any(v!='ACCEPT' for v in r['verd']))
    print(f"\n{name}: n={len(rs)} lines/tk med {st.median([r['lines'] for r in rs]):.0f} tot {tot('lines')} | $/100l med {st.median(perl):.2f} pooled {(tot('usd')+tot('sub_usd'))/tot('lines')*100:.2f} | req/100l med {st.median(reqL):.1f} pooled {tot('req')/tot('lines')*100:.1f} | meanwin med {st.median([r['meanwin'] for r in rs]):.0f}k maxwin med {st.median([r['maxwin'] for r in rs]):.0f}k | min/100l {tot('wall')/tot('lines')*100:.1f} | review rounds mean {st.mean(rounds):.2f}, non-accept {rej}/{len(rs)} | sub$ {tot('sub_usd'):.2f} ({tot('sub_usd')/(tot('usd')+tot('sub_usd'))*100:.1f}%) | compactions {tot('comp')} | total $ {tot('usd')+tot('sub_usd'):.2f}")
old=[t for t,r in rows.items() if r['model']=='opus-5' and r['day'] in('09-04','09-05')]
new=[t for t,r in rows.items() if r['model']=='opus-5' and r['day']=='09-06']
coh('OLD prompt opus 09-04..05',old); coh('OLD 09-05 only',[t for t in old if rows[t]['day']=='09-05'])
coh('NEW agentic prompt opus 09-06',new); coh('NEW excl t685',[t for t in new if t!=685])
