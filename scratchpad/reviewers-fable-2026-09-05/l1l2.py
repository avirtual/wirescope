# Per reviewer request: what did the wire actually charge, and what does each layer explain?
# Gate pattern per warm round r: in_r = new tail_r (uncached), w_r = tail_{r-1} (written now, at halt),
# rd_r = everything below. Excess write = w_r - in_{r-1} - small -> a bust NOT explained by the gate.
import json,os,re,glob,time,sys,collections,statistics as st
sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-40*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
CH=3.0
def W(t): return sum(t.get(k,0) for k in ('input_tokens','cache_read_input_tokens','cache_write_5m_tokens','cache_write_1h_tokens'))
agg=collections.defaultdict(lambda:dict(n=0,rounds=0,in_tail=0,in_extra=0,w_expected=0,w_excess=0,excess_reqs=0,excess_l2=0,l2fires=0,l2chars=0,think_live=0,think_blocks=0,tail=0,asst_think=[]))
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f)]
    prev=None
    for f in fs:
        rf=f.replace('.request.json','.response.json')
        if not os.path.exists(rf): continue
        r=json.load(open(f)); o=json.load(open(rf))
        if o.get('status_code')!=200: continue
        t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        b=r.get('body') or {}
        if not b.get('tools') or W(t)<2000: continue
        m=o.get('model').replace('claude-',''); a=agg[m]; a['n']+=1
        smt=r.get('strip_midturn_thinking') or {}; spe=r.get('strip_prior_tool_errors') or {}
        if spe.get('stripped'): a['l2fires']+=1; a['l2chars']+=spe.get('stripped_chars',0)
        # live thinking present in the forwarded body's last assistant message
        msgs=b['messages']
        la=[x for x in msgs if x.get('role')=='assistant']
        if la and isinstance(la[-1].get('content'),list):
            tk=sum(len(bl.get('thinking','')) for bl in la[-1]['content'] if bl.get('type')=='thinking')
            if tk: a['think_live']+=tk/CH; a['think_blocks']+=1; a['asst_think'].append(tk/CH)
        if prev is not None:
            pt=prev['t']
            newtail=W(t)-W(pt)+ (smt.get('stripped_chars',0)/CH)  # window growth + what we just deleted
            a['rounds']+=1; a['tail']+=max(0,newtail)
            a['in_tail']+=min(t['input_tokens'],max(0,newtail)); a['in_extra']+=max(0,t['input_tokens']-max(0,newtail)-500)
            wexp=pt['input_tokens']; w=t.get('cache_write_5m_tokens',0)+t.get('cache_write_1h_tokens',0)
            a['w_expected']+=min(w,wexp+500); ex=max(0,w-wexp-500); a['w_excess']+=ex
            if ex>1000:
                a['excess_reqs']+=1
                if spe.get('stripped') and spe.get('stripped_chars',0)>(prev.get('spe') or {}).get('stripped_chars',0): a['excess_l2']+=1
        prev={'t':t,'spe':spe}
for m,a in agg.items():
    R=max(1,a['rounds'])
    print(f"{m:10} req={a['n']} rounds={a['rounds']} | per round: tail {a['tail']/R:6.0f} tok, uncached-in explained by tail {a['in_tail']/R:6.0f}, unexplained {a['in_extra']/R:5.0f} | write explained by gate {a['w_expected']/R:6.0f}, EXCESS {a['w_excess']/R:5.0f} (reqs with excess>1k: {a['excess_reqs']}, coinciding with new L2 stub: {a['excess_l2']})"
          f" | L2 tool-error stubs: {a['l2fires']} fires, {a['l2chars']/CH/max(1,a['n']):.0f} tok/req | live thinking blocks in last asst msg: {a['think_blocks']}/{a['n']} reqs, p50 {st.median(a['asst_think']) if a['asst_think'] else 0:.0f} tok, mean {a['think_live']/max(1,a['think_blocks']):.0f} tok; thinking/tail = {a['think_live']/max(1,a['tail']):.2f}")
