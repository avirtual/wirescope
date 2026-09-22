import json,os,re,glob,time,statistics as st,collections,datetime as dt
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-14*86400
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
def W(t): return sum((t.get(k) or 0) for k in ('input_tokens','cache_read_input_tokens','cache_write_5m_tokens','cache_write_1h_tokens'))
hands=[]
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.response.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f)]
    if not fs: continue
    t_=int(RE.search(fs[0]).group(1))
    h=dict(t=t_,n=0,usd=0,first=None,last=None,win=[],out=0,agent_calls=0,sendmsg=0,sub_req=0,sub_usd=0,tools=collections.Counter(),subs=set())
    for f in fs:
        try: o=json.load(open(f))
        except: continue
        if o.get('status_code')!=200: continue
        t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        if W(t)<2000: continue
        usd=(o.get('billing') or {}).get('est_usd') or 0
        role=o.get('role')
        if '-parent-' in f or role=='parent':
            h['n']+=1; h['usd']+=usd; h['win'].append(W(t)); h['out']+=t.get('output_tokens',0)
            ts=os.stat(f).st_mtime; h['first']=ts if h['first'] is None else min(h['first'],ts); h['last']=ts if h['last'] is None else max(h['last'],ts)
            for tu in (o.get('meta') or {}).get('tool_uses') or []:
                if isinstance(tu,dict):
                    h['tools'][tu.get('name')]+=1
                    if tu.get('name')=='Agent': h['agent_calls']+=1
                    if tu.get('name')=='SendMessage': h['sendmsg']+=1
        else:
            h['sub_req']+=1; h['sub_usd']+=usd; h['subs'].add(re.search(r'hand-([0-9a-f]{8})',f).group(1) if re.search(r'hand-([0-9a-f]{8})',f) else f)
    if h['n']>=3: hands.append(h)
hands.sort(key=lambda h:h['first'])
# first request bodies: tool roster + schema chars for Agent/SendMessage
roster=collections.Counter(); sch={}
for h in hands[-30:]:
    pass
days=collections.defaultdict(list)
for h in hands: days[dt.datetime.fromtimestamp(h['first']).strftime('%m-%d')].append(h)
print("day    hands  mean$   p50$   mean req  mean min  mean last-win  $/req   out/req  | agent calls  sendmsg  hands w/ subagent traffic  sub req  sub $")
for day in sorted(days):
    L=days[day]; n=len(L)
    print(f"{day}  {n:4d}  {st.mean(h['usd'] for h in L):6.2f} {st.median(h['usd'] for h in L):6.2f}  {st.mean(h['n'] for h in L):7.0f}  {st.mean((h['last']-h['first'])/60 for h in L):7.0f}  {st.mean(h['win'][-1] for h in L):11.0f}  {sum(h['usd'] for h in L)/sum(h['n'] for h in L):.3f}  {sum(h['out'] for h in L)/sum(h['n'] for h in L):6.0f}  | {sum(h['agent_calls'] for h in L):6d} {sum(h['sendmsg'] for h in L):8d} {sum(1 for h in L if h['sub_req']):8d} {sum(h['sub_req'] for h in L):12d} {sum(h['sub_usd'] for h in L):6.2f}")
print()
print("hands total:",len(hands),"| Agent calls:",sum(h['agent_calls'] for h in hands),"| SendMessage calls:",sum(h['sendmsg'] for h in hands),"| hands with any subagent traffic:",sum(1 for h in hands if h['sub_req']),"| subagent $:",round(sum(h['sub_usd'] for h in hands),2))
print("hands that used Agent:",[(f"t{h['t']}",h['agent_calls'],h['sub_req'],round(h['sub_usd'],2)) for h in hands if h['agent_calls'] or h['sub_req']])
tools=collections.Counter()
for h in hands: tools.update(h['tools'])
print("tool calls across all hands:",dict(tools.most_common()))
json.dump([{k:(v if k not in('tools','subs','win') else (dict(v) if k=='tools' else list(v) if k=='subs' else v[-1])) for k,v in h.items()} for h in hands],open('/Users/bogdan/projects/proxy-lab/scratchpad/reviewers-fable-2026-09-05/hands_trend.json','w'))
