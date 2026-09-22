# Main clodex seat: strip stack ON (actual receipts) vs OFF (counterfactual), per request, per model phase.
# OFF model: thinking + L2-stubbed bytes stay in the window; stock rolling tail marker => each request
# reads the previous window at cache_read and writes only the new tail at the 1h premium; a new user
# turn re-writes nothing (the prior turn was already cached by the rolling marker); cold/compaction
# requests keep the same hit prefix the API actually returned and write the rest.
import json,os,re,glob,sys,statistics as st,datetime as dt
sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
from proxylab import billing
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=3.0
P={}
def price(m):
    if m not in P: P[m]=billing._price_for(model=m)
    return P[m]
def W(t): return sum((t.get(k) or 0) for k in ('input_tokens','cache_read_input_tokens','cache_write_5m_tokens','cache_write_1h_tokens'))
def load(sid_prefix, since=None):
    d=glob.glob(LD+'/'+sid_prefix+'*')[0]
    fs=glob.glob(d+'/*-parent-*.request.json')
    fs.sort(key=lambda f:int(os.path.basename(f).split('-')[0]))
    out=[]
    for f in fs:
        rf=f.replace('.request.json','.response.json')
        if not os.path.exists(rf): continue
        try: r=json.load(open(f)); o=json.load(open(rf))
        except: continue
        if o.get('status_code')!=200: continue
        t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        if W(t)<2000 or not (r.get('summary') or {}).get('n_tools'): continue
        ts=os.stat(rf).st_mtime
        if since and ts<since: continue
        g=lambda k:(r.get(k) or {}).get('stripped_chars',0) or 0
        out.append(dict(seq=int(os.path.basename(f).split('-')[0]),ts=ts,model=o.get('model'),t=t,usd=(o.get('billing') or {}).get('est_usd') or 0,
            think_chars=g('strip_prior_thinking')+g('strip_midturn_thinking'), mid_think=g('strip_midturn_thinking'),
            l2_chars=g('strip_prior_tool_errors')+g('strip_prior_edit_acks')+g('fold_read_edits')+g('task_reminder_strip')+g('filemod_diff_strip'),
            boundary=(r.get('pin_settled_breakpoint') or {}).get('boundary_idx') or (r.get('strip_prior_thinking') or {}).get('boundary_idx') or (r.get('midturn_marker_gate') or {}).get('boundary_idx'),
            l2_delta=0,
            nmsg=(r.get('summary') or {}).get('n_messages')))
    return out
def run(reqs,label):
    phases={}
    prev=None; prevWoff=None
    for i,q in enumerate(reqs):
        t=q['t']; m=q['model']; pr=price(m)
        Won=W(t); Woff=Won+q['think_chars']/CH+q['l2_chars']/CH
        rd_on=t.get('cache_read_input_tokens',0); in_on=t.get('input_tokens',0); w_on=t.get('cache_write_5m_tokens',0)+t.get('cache_write_1h_tokens',0)
        ttl='1h' if t.get('cache_write_1h_tokens',0)>=t.get('cache_write_5m_tokens',0) else '5m'
        wp=pr['cache_write_'+ttl]
        gap=(q['ts']-prev['ts']) if prev else 1e9
        Won_prev=W(prev['t']) if prev else 0
        cold = prev is None or gap>3600 or (rd_on<0.3*Won_prev and (Won<0.6*Won_prev or gap>1800))
        newturn = prev is not None and q['boundary'] is not None and prev['boundary'] is not None and q['boundary']!=prev['boundary']
        if cold:
            rd_off=rd_on; w_off=max(0,Woff-rd_off); kind='cold'
        else:
            # natural (non-strip) bust: big write on a same-turn request beyond last round's uncached tail + new tail
            expected_w = (prev['t'].get('input_tokens',0) if prev else 0) + max(0,Won-Won_prev)
            if not newturn and w_on > expected_w + 3000 and rd_on < 0.9*Won_prev:
                scale=(prevWoff/Won_prev) if Won_prev else 1
                rd_off=min(Woff,rd_on*scale); w_off=max(0,Woff-rd_off); kind='natbust'
            else:
                rd_off=min(Woff,prevWoff); w_off=max(0,Woff-prevWoff); kind='turn' if newturn else 'warm'
        out=t.get('output_tokens',0)
        q['kind']=kind; q['expected_w']=(prev['t'].get('input_tokens',0) if prev else 0)+max(0,Won-Won_prev) if prev else 0
        usd_off = rd_off*pr['cache_read']/1e6 + w_off*wp/1e6 + out*pr['out']/1e6 + 200*pr['in']/1e6
        ph=phases.setdefault(m,dict(unexpl=0,unexpl_l2=0,xp={},n=0,on=0,off=0,Won=[],Woff=[],think=0,l2=0,on_in=0,on_rd=0,on_w=0,on_out=0,off_rd=0,off_w=0,kinds={},first=q['ts'],last=q['ts'],busts_on=0))
        ph['n']+=1; ph['on']+=q['usd']; ph['off']+=usd_off; ph['Won'].append(Won); ph['Woff'].append(Woff)
        ph['think']+=q['think_chars']/CH; ph['l2']+=q['l2_chars']/CH
        ph.setdefault('tail',0); ph['tail']+=max(0,Won-Won_prev) if (prev and kind=='warm') else 0; ph.setdefault('mt',0); ph['mt']+=(q.get('mid_think') or 0)/CH
        ph['on_in']+=in_on; ph['on_rd']+=rd_on; ph['on_w']+=w_on; ph['on_out']+=out; ph['off_rd']+=rd_off; ph['off_w']+=w_off
        ph['kinds'][kind]=ph['kinds'].get(kind,0)+1; ph['last']=q['ts']
        if prev is not None and kind in('warm','turn') and w_on>q['expected_w']+3000 and not newturn:
            ph['unexpl']+=1
            if q['l2_chars']>prev['l2_chars']: ph['unexpl_l2']+=1
        for om in ('claude-opus-5','claude-fable-5-1'):
            opr=price(om); owp=opr['cache_write_'+ttl]
            x=ph['xp'].setdefault(om,[0,0])
            x[0]+=in_on*opr['in']/1e6+rd_on*opr['cache_read']/1e6+w_on*owp/1e6+out*opr['out']/1e6
            x[1]+=rd_off*opr['cache_read']/1e6+w_off*owp/1e6+out*opr['out']/1e6+200*opr['in']/1e6
        prev=q; prevWoff=Woff
    print(f"== {label}")
    for m,ph in phases.items():
        pr=price(m); n=ph['n']
        print(f"  {m:18} {dt.datetime.fromtimestamp(ph['first']):%m-%d %H:%M}→{dt.datetime.fromtimestamp(ph['last']):%m-%d %H:%M} req={n:5d} | ON ${ph['on']:8.2f}  OFF ${ph['off']:8.2f}  ON/OFF {100*(ph['on']/ph['off']-1):+5.1f}%"
              f" | window ON p50 {st.median(ph['Won']):7.0f} OFF p50 {st.median(ph['Woff']):7.0f} (+{100*(st.median(ph['Woff'])/st.median(ph['Won'])-1):.0f}%) | extra/req: think {ph['think']/n:6.0f} l2 {ph['l2']/n:5.0f}"
              f" | ON tok/req: in1x {ph['on_in']/n:6.0f} rd {ph['on_rd']/n:7.0f} w {ph['on_w']/n:6.0f} out {ph['on_out']/n:5.0f} | OFF: rd {ph['off_rd']/n:7.0f} w {ph['off_w']/n:6.0f} | {ph['kinds']}")
        # cross-price this phase's behaviour under the other table
        for om in ('claude-opus-5','claude-fable-5-1'):
            if om==m: continue
        ph['model']=m
        print(f"      cross-price ON/OFF: "+' | '.join(f"{om[7:]} table {100*(x[0]/x[1]-1):+5.1f}%" for om,x in ph['xp'].items())+f" | midturn-think/tail per warm round {ph['mt']/max(1,ph['kinds'].get('warm',1)):.0f}/{ph['tail']/max(1,ph['kinds'].get('warm',1)):.0f} | unexplained warm writes: {ph['unexpl']} (of which l2 grew: {ph['unexpl_l2']})")
    return phases
import time
since=time.time()-4*86400
allph=[]
for sid,lab in (('5383fbbc','clodex main 5383fbbc (opus→fable→opus-4.8→fable, same session)'),('37464a95','clodex main 37464a95 (opus)'),('4d74dc16','clodex 4d74dc16 (opus)'),('893a6ec8','893a6ec8 (fable short)'),('30155aba','30155aba (fable short)')):
    try: allph.append(run(load(sid,since),lab))
    except Exception as e: print(sid,'ERR',e)
