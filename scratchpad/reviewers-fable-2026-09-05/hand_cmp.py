import json,os,re,glob,sys,collections,statistics as st
sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
from proxylab.billing import _price_for
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
M={int(k):v for k,v in json.load(open('merges.json')).items()}
M[680]=dict(day='09-05',files=25,ins=789,dele=282)
TEST_RE=re.compile(r'\b(npm (test|run)|npx (vitest|jest|mocha|tsc)|node --test|pytest)\b')
WRITE_RE=re.compile(r"(<<\s*'?\"?[A-Z_]+|sed -i|tee |> ?[\w./-]+\.(js|ts|py|md|json|mjs|cjs|html|css)\b)")
FAIL_RE=re.compile(r'(^not ok|✖|AssertionError|FAIL\b|Error:)',re.M)
def profile(t):
    fs=[]
    for d in os.listdir(LD):
        p=os.path.join(LD,d)
        if d.startswith('_') or not os.path.isdir(p): continue
        fs+= [f for f in glob.glob(p+f'/*clodex.t{t}.hand-*-parent-*.request.json')]
    fs.sort(key=lambda f:int(os.path.basename(f).split('-')[0]))
    if not fs: return None
    model=None; n=0; usd=0; tok=collections.Counter(); ts=[]; stops=collections.Counter(); think_blocks=[]
    for f in fs:
        r=f.replace('.request.json','.response.json')
        try: o=json.load(open(r))
        except: continue
        if o.get('status_code')!=200: continue
        tk={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        W=tk['input_tokens']+tk['cache_read_input_tokens']+tk['cache_write_5m_tokens']+tk['cache_write_1h_tokens']
        if W<2000: continue
        model=o.get('model') or model; n+=1; usd+=(o['billing'].get('est_usd') or 0)
        for k,v in tk.items():
            if isinstance(v,(int,float)): tok[k]+=v
        tok['W']+=W; ts.append(json.load(open(f))['ts'] if False else os.stat(f).st_mtime)
        stops[(o.get('meta') or {}).get('stop_reason')]+=1
    # final body
    fs2=[f for f in fs]; b=json.load(open(fs2[-1]))['body']
    # compaction segments: pick largest bodies... simpler: walk all bodies, dedupe tool_use ids
    seen=set(); tu=[]; results={}; done_at=None; ntu=0; per_req=[]; asst_text=0; last_n=0
    for f in fs2:
        try: bb=json.load(open(f))['body']
        except: continue
        cur=0
        for msg in bb['messages']:
            c=msg['content']
            if msg['role']=='user' and isinstance(c,list):
                for x in c:
                    if x.get('type')=='tool_result':
                        cc=x.get('content'); results[x.get('tool_use_id')]=(cc if isinstance(cc,str) else ' '.join(y.get('text','') for y in cc if isinstance(y,dict)),bool(x.get('is_error')))
            if msg['role']=='assistant' and isinstance(c,list):
                for x in c:
                    if x.get('type')=='text' and '[agent:task done' in x.get('text','') and done_at is None: done_at=len(tu)
                    if x.get('type')=='tool_use' and x['id'] not in seen: seen.add(x['id']); tu.append(x)
        if len(bb['messages'])>=2:
            last=bb['messages'][-2] if bb['messages'][-1]['role']=='user' else bb['messages'][-1]
            if last['role']=='assistant' and isinstance(last['content'],list):
                per_req.append(sum(1 for x in last['content'] if x.get('type')=='tool_use'))
                asst_text+=sum(len(x.get('text','')) for x in last['content'] if x.get('type')=='text')
    cnt=collections.Counter(x['name'] for x in tu); bash=collections.Counter(); tests=0; tfail=0; errs=0; res=collections.Counter(); files=set(); rereads=collections.Counter()
    for x in tu:
        inp=x.get('input') or {}; r=results.get(x['id'],('',False))
        if r[1]: errs+=1
        k=x['name']
        if k=='Bash':
            cmd=inp.get('command','')
            if TEST_RE.search(cmd): k='Bash:test'; tests+=1; tfail+= bool(FAIL_RE.search(r[0]) or r[1])
            elif WRITE_RE.search(cmd): k='Bash:write'
            elif re.match(r'\s*(cat|sed -n|grep|rg|head|tail|ls|find|wc|git (diff|log|show|status|grep))\b',re.sub(r'^cd \S+ && ','',cmd)): k='Bash:read'
            else: k='Bash:other'
            bash[k]+=1
        if x['name'] in('Edit','Write'): files.add(inp.get('file_path'))
        if x['name']=='Read': rereads[inp.get('file_path')]+=1
        res[k]+=len(r[0])
    m=M.get(t,{}); ch=m.get('ins',0)+m.get('dele',0)
    pr=_price_for(model=model) if model else {}
    return dict(t=t,model=model,day=m.get('day'),n=n,usd=usd,ch=ch,files_merged=m.get('files'),ins=m.get('ins'),dele=m.get('dele'),
        dur_min=(max(ts)-min(ts))/60 if ts else 0,tu=len(tu),tools_per_req=len(tu)/max(n,1),multi=sum(1 for v in per_req if v>1)/max(len(per_req),1),
        cnt=dict(cnt),bash=dict(bash),tests=tests,tfail=tfail,errs=errs,edited_files=len(files-{None}),rereads=sum(v-1 for v in rereads.values() if v>1),
        res_k={k:round(v/1000) for k,v in res.items()},res_total_k=round(sum(res.values())/1000),asst_text_k=round(asst_text/1000),
        out=tok['output_tokens'],think=tok['thinking_tokens'],read=tok['cache_read_input_tokens'],w5=tok['cache_write_5m_tokens'],w1=tok['cache_write_1h_tokens'],in1x=tok['input_tokens'],W=tok['W'],
        done_frac=(done_at/len(tu)) if done_at is not None and tu else None,stops=dict(stops),
        usd_read=tok['cache_read_input_tokens']*pr.get('cache_read',0)/1e6,usd_write=(tok['cache_write_5m_tokens']*pr.get('cache_write_5m',0)+tok['cache_write_1h_tokens']*pr.get('cache_write_1h',0))/1e6,usd_out=tok['output_tokens']*pr.get('out',0)/1e6,usd_in=tok['input_tokens']*pr.get('in',0)/1e6)
if __name__=='__main__':
    ts=[int(a) for a in sys.argv[1:]]
    P=[p for p in (profile(t) for t in ts) if p]
    json.dump(P,open('hand_cmp.json','w'),indent=1)
    cols=['t','model','day','ch','files_merged','n','usd','dur_min','tu','tools_per_req','multi','edited_files','tests','tfail','errs','rereads','res_total_k','asst_text_k','out','think','W','read','w5','w1','in1x','usd_read','usd_write','usd_out','usd_in','done_frac']
    for p in P:
        print(f"t{p['t']} {p['model']} {p['day']} | changed {p['ch']} ({p['ins']}+/{p['dele']}-, {p['files_merged']}f) | req {p['n']} ${p['usd']:.2f} {p['dur_min']:.0f}min | tools {p['tu']} ({p['tools_per_req']:.2f}/req, multi {p['multi']:.0%}) | edited {p['edited_files']} tests {p['tests']} fail {p['tfail']} errs {p['errs']} rereads {p['rereads']} | results {p['res_total_k']}k asst-text {p['asst_text_k']}k | out {p['out']} think {p['think']} | W {p['W']/1e6:.1f}M read {p['read']/1e6:.1f}M w5 {p['w5']/1e3:.0f}k w1 {p['w1']/1e3:.0f}k in1x {p['in1x']/1e3:.0f}k | $ read {p['usd_read']:.2f} write {p['usd_write']:.2f} out {p['usd_out']:.2f} in {p['usd_in']:.2f} | done@{p['done_frac']}")
        print('    tools',p['cnt'],'\n    bash',p['bash'],'\n    res_k',p['res_k'],'\n    stops',p['stops'])
        print(f"    per 100 changed lines: req {100*p['n']/max(p['ch'],1):.1f}  $ {100*p['usd']/max(p['ch'],1):.2f}  tools {100*p['tu']/max(p['ch'],1):.1f}")
