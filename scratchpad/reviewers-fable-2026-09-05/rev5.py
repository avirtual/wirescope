import json,os,re,glob
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
def L(x): 
    if isinstance(x,str): return len(x)
    if isinstance(x,list): return sum(L(b) for b in x)
    if isinstance(x,dict): return len(json.dumps(x))
    return 0
for sid in ['02640873','1a20bf67']:
    d=[x for x in os.listdir(LD) if x.startswith(sid)][0]
    fs=[f for f in sorted(glob.glob(LD+'/'+d+'/*.request.json')) if RE.search(f)]
    print("=========",sid,d)
    for f in fs[:7]:
        r=json.load(open(f)); body=r.get('body') or r
        if not isinstance(body,dict) or not body.get('messages'): continue
        o=json.load(open(f.replace('.request.json','.response.json'))); t=(o.get('billing') or {}).get('tokens') or {}
        print(f"--- {os.path.basename(f)[:40]} keys={[k for k in r.keys() if k!='body'][:12]}")
        print(f"    billing: in={t.get('input_tokens')} rd={t.get('cache_read_input_tokens')} w5={t.get('cache_write_5m_tokens')} w1h={t.get('cache_write_1h_tokens')} out={t.get('output_tokens')}")
        sm=r.get('summary') or {}
        for k in ('transforms','notes','pin_settled_breakpoint','strip','ws','directives','strip_prior_thinking'):
            if k in sm: print(f"    summary.{k}={json.dumps(sm[k])[:300]}")
        if 'transforms' in r: print(f"    transforms={json.dumps(r['transforms'])[:400]}")
        sysb=body.get('system'); 
        if isinstance(sysb,list):
            print("    system:",[(L(b.get('text','')),'CC' if 'cache_control' in b else '') for b in sysb])
        tl=body.get('tools') or []
        print("    tools:",[(x.get('name'),'CC' if 'cache_control' in x else '') for x in tl])
        for i,m in enumerate(body['messages']):
            c=m['content']
            if isinstance(c,str): desc=f"str[{len(c)}]"+(" CC?" if False else "")
            else:
                desc=' '.join(f"{b.get('type')}[{L(b.get('text') or b.get('content') or b.get('input') or b.get('thinking') or '')}]{'CC' if 'cache_control' in b else ''}" for b in c)
            print(f"    m{i} {m['role']:9} {desc[:200]}")
