import json,sys,os,re
S=json.load(open(sys.argv[1]))
out=[]
def txt(c):
    if isinstance(c,str): return c
    return '\n'.join(x.get('text','') for x in c if isinstance(x,dict) and x.get('type')=='text')
for s in S:
    if s['sid']=='_no-sess': continue
    j=json.load(open(s['last_req'])); b=j.get('body') or j
    ms=b.get('messages',[])
    sysb=b.get('system'); sys_txt=sysb if isinstance(sysb,str) else '\n'.join(x.get('text','') for x in sysb or [])
    tools=[t.get('name') for t in b.get('tools',[])]
    calls=[]; results={}
    # map tool_use id -> result size
    for m in ms:
        if m['role']=='user' and isinstance(m['content'],list):
            for x in m['content']:
                if x.get('type')=='tool_result':
                    c=x.get('content'); n=len(c) if isinstance(c,str) else sum(len(y.get('text','')) for y in c if isinstance(y,dict)) if isinstance(c,list) else 0
                    results[x.get('tool_use_id')]=(n,bool(x.get('is_error')))
    for i,m in enumerate(ms):
        if m['role']=='assistant' and isinstance(m['content'],list):
            for x in m['content']:
                if x.get('type')=='tool_use':
                    inp=x.get('input',{}); r=results.get(x.get('id'),(None,False))
                    calls.append(dict(msg=i,name=x.get('name'),inp=inp,res_chars=r[0],err=r[1]))
    first_user=txt(ms[0]['content']) if ms else ''
    # last assistant text
    last_txt=''
    for m in reversed(ms):
        if m['role']=='assistant':
            t=txt(m['content']); 
            if t.strip(): last_txt=t; break
    # billing across responses
    usd=0; inp=rd=w5=w1=outp=0; n=0; stops=[]
    for rp in s['resp_paths']:
        try: r=json.load(open(rp))
        except: continue
        bl=r.get('billing',{}); t=bl.get('tokens') or {}
        usd+=bl.get('est_usd') or 0; n+=1
        inp+=t.get('input_tokens') or 0; rd+=t.get('cache_read_input_tokens') or 0; w5+=t.get('cache_write_5m_tokens') or 0; w1+=t.get('cache_write_1h_tokens') or 0; outp+=t.get('output_tokens') or 0
        stops.append((r.get('meta') or {}).get('stop_reason'))
    out.append(dict(sid=s['sid'],ticket=s['ticket'],rev=s['rev'],agent=s['agent'],n_req=s['n_req'],dur=s['last']-s['first'],
        tools=tools,sys_len=len(sys_txt),sys_head=sys_txt[:300],first_user=first_user,calls=calls,last_txt=last_txt,
        usd=usd,tok=dict(input=inp,read=rd,w5=w5,w1h=w1,out=outp),n_resp=n,stops=stops,n_msgs=len(ms)))
json.dump(out,open(sys.argv[2],'w'))
print(len(out),"sessions extracted")
