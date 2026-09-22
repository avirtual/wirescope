import json,os,re,glob,time
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-30*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
want={'10be21b3','9697f2a9','a6c44a2e','ad9ce8cc','ffba5897','02640873','1a20bf67','29c9d9e1','b84193e1'}
for d in sorted(os.listdir(LD)):
    if d[:8] not in want: continue
    p=os.path.join(LD,d)
    fs=sorted(glob.glob(p+'/*.response.json'))
    print("=====",d[:8])
    last_body=None
    for f in fs:
        if not RE.search(f): continue
        o=json.load(open(f)); t=(o.get('billing') or {}).get('tokens') or {}; me=o.get('meta') or {}
        u=me.get('usage_final') or me.get('usage_start') or {}
        cc=u.get('cache_creation') or {}
        rq=f.replace('.response.json','.request.json')
        nm=0; ttl=set(); nmsg=0; ntools=0
        try:
            r=json.load(open(rq)); body=r.get('body') or r
            if isinstance(body,dict) and body.get('messages'):
                nmsg=len(body['messages']); ntools=len(body.get('tools') or [])
                def walk(x):
                    global nm
                    if isinstance(x,dict):
                        if 'cache_control' in x: nm+=1; ttl.add((x['cache_control'] or {}).get('ttl','5m'))
                        for v in x.values(): walk(v)
                    elif isinstance(x,list):
                        for v in x: walk(v)
                walk(body.get('system')); walk(body.get('tools')); walk(body.get('messages'))
                last_body=body
        except Exception as e: pass
        print(f"  {o.get('model','')[-9:]:9} st={o.get('status_code')} in={u.get('input_tokens',0):6d} rd={u.get('cache_read_input_tokens',0):7d} w5={cc.get('ephemeral_5m_input_tokens',0):6d} w1h={cc.get('ephemeral_1h_input_tokens',0):6d} out={u.get('output_tokens',0):5d} msgs={nmsg:3d} tools={ntools} markers={nm} ttl={sorted(ttl)} stop={me.get('stop_reason')} ntu={len(me.get('tool_uses') or [])} usd={(o.get('billing') or {}).get('est_usd',0):.3f}")
    if last_body:
        files=set(); names={}
        for m in last_body['messages']:
            if m.get('role')!='assistant' or not isinstance(m.get('content'),list): continue
            for b in m['content']:
                if b.get('type')=='tool_use':
                    names[b['name']]=names.get(b['name'],0)+1
                    i=b.get('input') or {}
                    if b['name']=='Read': files.add(i.get('file_path'))
                    if b['name']=='Bash':
                        for x in re.findall(r'(?:cat|sed -n [^ ]+|head [^ ]*|tail [^ ]*)\s+([\w./-]+)',i.get('command','')): files.add(x)
        print(f"  tools={names} distinct_files_read={len(files)}")
