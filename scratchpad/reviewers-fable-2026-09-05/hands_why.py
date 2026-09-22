import json,os,re,glob,time,statistics as st,collections,datetime as dt,hashlib,sys
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-14*86400
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
trend={ (h['t'],round(h['first'])):h for h in json.load(open(os.path.dirname(os.path.abspath(__file__))+'/hands_trend.json'))}
tbyt=collections.defaultdict(list)
for h in trend.values(): tbyt[h['t']].append(h)
READ_RE=re.compile(r'^\s*(cat|sed -n|head|tail|grep|rg|find|ls|wc|git (log|show|diff|status|grep|blame)|node -e|python3? -c|jq|awk|tree|stat)\b')
WRITE_RE=re.compile(r"(<<\s*'?\"?[A-Z_]+|sed -i|tee |> ?[\w./-]+\.(js|ts|py|md|json|mjs|cjs|html|css)\b|cp |mv |mkdir|rm -)")
TEST_RE=re.compile(r'\b(npm (test|run)|npx (vitest|jest|mocha|tsc|eslint)|node --test|pytest|python3? -m (pytest|unittest)|make |cargo test|go test)\b')
GIT_RE=re.compile(r'\bgit (add|commit|push|checkout|stash|rebase|merge|worktree)\b')
def cls_bash(cmd):
    if TEST_RE.search(cmd): return 'test'
    if WRITE_RE.search(cmd): return 'write'
    if GIT_RE.search(cmd): return 'git'
    if READ_RE.search(cmd): return 'read'
    return 'other'
def files_from_bash(cmd):
    out=set()
    for m in re.finditer(r"(?:<<\s*'?\"?[A-Z_]+'?\"?\s*>\s*|sed -i(?:\s+'')?\s+.*?\s|tee\s+(?:-a\s+)?|>\s*)([\w./-]+\.(?:js|ts|py|md|json|mjs|cjs|html|css|sh|txt))",cmd): out.add(m.group(1))
    return out
rows=[]
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f) and '-parent-' in f]
    if len(fs)<3: continue
    t_=int(RE.search(fs[0]).group(1))
    # segments by size drop (compaction)
    segs=[];prev=None;prevsz=0
    for f in fs:
        sz=os.stat(f).st_size
        if prev and sz<prevsz*0.5 and prevsz>200000: segs.append(prev)
        prev,prevsz=f,sz
    segs.append(prev)
    seen=set();tu=[];results={};specs=[];lead=0;replay=False;errs=0;res_chars=collections.Counter()
    hdr={}
    for si,f in enumerate(segs):
        try: b=json.load(open(f))['body']
        except Exception as e: continue
        if si==len(segs)-1:
            sysb=b.get('system') or []
            hdr['cc']=re.search(r'cc_version=([\d.]+)',sysb[0].get('text','')).group(1) if sysb else '?'
            role=[s.get('text','') for s in sysb if s.get('text','').startswith('# Team hand')]
            hdr['role_chars']=len(role[0]) if role else 0
            hdr['role_hash']=hashlib.md5(role[0].encode()).hexdigest()[:6] if role else '-'
            hdr['tools']=','.join(sorted(t['name'] for t in b.get('tools',[])))
            hdr['bashfirst']=any(m['role']=='system' and isinstance(m['content'],str) and 'Bash tool' in m['content'] for m in b['messages'])
            hdr['bashfirst_relaxed']=any(m['role']=='system' and isinstance(m['content'],str) and 'fragile' in m['content'] for m in b['messages'])
            hdr['n_msgs']=len(b['messages'])
        for mi,m in enumerate(b['messages']):
            c=m['content']
            if m['role']=='user':
                if isinstance(c,str): c=[{'type':'text','text':c}]
                for x in c:
                    if x.get('type')=='text' and '[agent:from' in x.get('text',''):
                        lead+=1
                        if 'ticket t' in x['text'] and si==0 and not specs: specs.append(len(x['text'])); replay='REPLAY' in x['text']
                    if x.get('type')=='tool_result':
                        cc=x.get('content'); n=len(cc) if isinstance(cc,str) else sum(len(y.get('text','')) for y in cc if isinstance(y,dict))
                        results[x.get('tool_use_id')]=(n,bool(x.get('is_error')))
            elif m['role']=='assistant' and isinstance(c,list):
                for x in c:
                    if x.get('type')=='tool_use' and x['id'] not in seen:
                        seen.add(x['id']); tu.append(x)
    cnt=collections.Counter(); bash=collections.Counter(); files=set(); edit_files=set(); readfiles=collections.Counter()
    for x in tu:
        n=x['name']; cnt[n]+=1
        inp=x.get('input') or {}
        if n=='Bash':
            k=cls_bash(inp.get('command','')); bash[k]+=1
            if k=='write': files|=files_from_bash(inp.get('command',''))
        elif n in('Edit','Write'):
            files.add(inp.get('file_path','')); edit_files.add(inp.get('file_path',''))
        elif n=='Read': readfiles[inp.get('file_path','')]+=1
        r=results.get(x['id'])
        if r:
            res_chars[n if n!='Bash' else 'Bash:'+cls_bash(inp.get('command',''))]+=r[0]
            if r[1]: errs+=1
    first=min(os.stat(f).st_mtime for f in fs)
    th=min(tbyt[t_],key=lambda h:abs(h['first']-first)) if tbyt[t_] else None
    rows.append(dict(t=t_,day=dt.datetime.fromtimestamp(first).strftime('%m-%d'),first=first,usd=th['usd'] if th else 0,n=th['n'] if th else len(tu),
        segs=len(segs),tu=len(tu),spec=specs[0] if specs else 0,replay=replay,lead=lead,errs=errs,files=len(files-{''}),edit_files=len(edit_files-{''}),
        rereads=sum(v-1 for v in readfiles.values() if v>1),cnt=dict(cnt),bash=dict(bash),res=dict(res_chars),**hdr))
rows.sort(key=lambda r:r['first'])
json.dump(rows,open(os.path.dirname(os.path.abspath(__file__))+'/hands_why.json','w'))
print(len(rows),'hands')
