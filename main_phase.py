# Same seat, opus phase vs fable phase: per-request tool behaviour on real agentic work.
import json,os,glob,collections,statistics as st
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
d=glob.glob(LD+'/5383fbbc*')[0]
fs=sorted(glob.glob(d+'/*-parent-*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0]))
ph=collections.defaultdict(lambda:dict(n=0,turns=0,tools=collections.Counter(),tu=0,multi=0,errs=0,edit_files=set(),out=0,think=0,usd=0,bash_ed=0,txt_only=0))
prev_nu=None; prev_model=None
import re
for f in fs:
    rf=f.replace('.request.json','.response.json')
    if not os.path.exists(rf): continue
    r=json.load(open(f)); o=json.load(open(rf))
    if o.get('status_code')!=200: continue
    b=r.get('body') or {}; msgs=b.get('messages') or []
    if not b.get('tools'): continue
    m=o.get('model'); p=ph[m]; p['n']+=1
    t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
    p['out']+=t.get('output_tokens',0); p['think']+=t.get('thinking_tokens',0); p['usd']+=(o.get('billing') or {}).get('est_usd') or 0
    nu=sum(1 for x in msgs if x.get('role')=='user' and (isinstance(x.get('content'),str) or any(y.get('type')=='text' for y in x['content'] if isinstance(y,dict))))
    if nu!=prev_nu or m!=prev_model: p['turns']+=1
    prev_nu=nu; prev_model=m
    # the request's LAST assistant message = what the model did in the previous response
    la=[x for x in msgs if x.get('role')=='assistant']
    if la and isinstance(la[-1].get('content'),list):
        tus=[x for x in la[-1]['content'] if x.get('type')=='tool_use']
        p['tu']+=len(tus); p['multi']+=1 if len(tus)>1 else 0
        if not tus: p['txt_only']+=1
        for x in tus:
            p['tools'][x.get('name')]+=1; inp=x.get('input') or {}
            if x.get('name') in('Edit','Write','MultiEdit'): p['edit_files'].add(inp.get('file_path'))
            if x.get('name')=='Bash' and re.search(r"sed -i|cat >|python3? - <<|>\s*[\w./-]+\.(js|py|md|json|ts)\b",inp.get('command','')): p['bash_ed']+=1
    lu=msgs[-1] if msgs else {}
    # walk back to the last user message with tool results
    for x in reversed(msgs):
        if x.get('role')=='user' and isinstance(x.get('content'),list):
            p['errs']+=sum(1 for y in x['content'] if isinstance(y,dict) and y.get('type')=='tool_result' and y.get('is_error')); break
for m,p in ph.items():
    n=p['n']; tl=p['tools']
    print(f"{m:18} req={n:4d} turns={p['turns']:3d} req/turn={n/max(1,p['turns']):4.1f} | tool calls/req={p['tu']/n:.2f} multi-tool rounds={100*p['multi']/n:.0f}% text-only responses={100*p['txt_only']/n:.0f}% | mix: "+', '.join(f"{k} {v}" for k,v in tl.most_common(8))+f" | files edited={len(p['edit_files'])} bash-edits={p['bash_ed']} | tool errors/req={p['errs']/n:.3f} | out/req={p['out']/n:.0f} think/req={p['think']/n:.0f} | $/req={p['usd']/n:.3f} $/turn={p['usd']/max(1,p['turns']):.2f}")
