"""Anatomy of a heredoc edit: how much of the payload is the EDIT vs the WRAPPER?

A native Edit ships old_string + new_string and nothing else -- the content is
irreducible, the framing is ~0. A heredoc ships the same two strings PLUS a
python program around them (cd, open, read, replace, write, verify prints).
Only the wrapper is reclaimable; the strings are the work.

So the question is not "bash vs Edit" in the abstract but: what fraction of
those 63,899 tokens is framing, and how much does the result side add back.

Classification of the edit body:
  replace-style : carries old+new (same information a native Edit carries)
  full-rewrite  : writes a whole file body (irreducible either way)
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)

# wrapper = everything outside the quoted string literals that carry content
STR = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
tot_in=0; tot_wrap=0; tot_res=0; n=0
kinds=defaultdict(lambda:[0,0,0])
per_seat=defaultdict(lambda:[0,0])
res_sizes=[]
edit_targets=defaultdict(lambda: defaultdict(int))
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    uses={}
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); inp=blk.get("input") or {}
            if nm!="Bash": continue
            cmd=inp.get("command","")
            if not ("<<" in cmd and ("python3 -" in cmd or "python -" in cmd or "cat >" in cmd)) and "sed -i" not in cmd:
                continue
            body_ch=len(cmd)
            content_ch=sum(len(m2.group(0)) for m2 in STR.finditer(cmd))
            wrap=max(body_ch-content_ch,0)
            k2 = "full-rewrite" if re.search(r"cat\s*>\s*['\"]?[\w./-]+['\"]?\s*<<", cmd) else "replace-style"
            uses[blk["id"]]=(k2,body_ch,wrap)
            tot_in+=body_ch; tot_wrap+=wrap; n+=1
            e=kinds[k2]; e[0]+=1; e[1]+=body_ch; e[2]+=wrap
            per_seat[tk][0]+=body_ch
            mp=re.search(r"p\s*=\s*['\"]([^'\"]+)['\"]", cmd)
            if mp: edit_targets[tk][mp.group(1)]+=1
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            u=uses.get(blk.get("tool_use_id"))
            if not u: continue
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            tot_res+=len(s); res_sizes.append(len(s)/CH)
            per_seat[u[0] if False else 0][1]+=0
print(f"heredoc/sed-i edits analysed: {n:,}\n")
print(f"  tool_use INPUT total   : {tot_in/CH:>10,.0f} tok")
print(f"    of which WRAPPER     : {tot_wrap/CH:>10,.0f} tok  ({tot_wrap/max(tot_in,1)*100:.0f}%)")
print(f"    of which content     : {(tot_in-tot_wrap)/CH:>10,.0f} tok  ({(tot_in-tot_wrap)/max(tot_in,1)*100:.0f}%)")
print(f"  tool_result total      : {tot_res/CH:>10,.0f} tok  (avg {tot_res/max(n,1)/CH:,.0f}/edit)")
print(f"  per edit: input {tot_in/max(n,1)/CH:,.0f} (wrapper {tot_wrap/max(n,1)/CH:,.0f}) + result {tot_res/max(n,1)/CH:,.0f}")
print("\n== by edit style ==")
print(f"{'style':<16} {'n':>6} {'inTok/edit':>11} {'wrapper/edit':>13} {'wrap%':>6}")
for k,(c,i,w) in sorted(kinds.items(), key=lambda kv:-kv[1][1]):
    print(f"{k:<16} {c:>6} {i/c/CH:>11,.0f} {w/c/CH:>13,.0f} {w/i*100:>5.0f}%")
rs=sorted(res_sizes)
if rs:
    print(f"\n== result size of an edit (the 'did it work' echo) ==")
    print(f"  p50 {rs[len(rs)//2]:,.0f}   p90 {rs[int(len(rs)*.9)]:,.0f}   p99 {rs[int(len(rs)*.99)]:,.0f}   max {rs[-1]:,.0f} tok")
    print(f"  native Edit result for comparison: 1 tok (p50/p90/p99)")
print("\n== churn: same file edited repeatedly in one ticket ==")
ch=[]
for tk,d in edit_targets.items():
    if not d: continue
    ch.append((tk,sum(d.values()),len(d),max(d.values()),max(d,key=d.get)))
ch.sort(key=lambda x:-x[3])
print(f"{'ticket':>7} {'edits':>6} {'files':>6} {'maxPerFile':>11}  hottest file")
for tk,tot,nf,mx,fn in ch[:10]:
    print(f"t{tk:>6} {tot:>6} {nf:>6} {mx:>11}  {fn[:44]}")
if ch:
    print(f"\n  median edits per file: {statistics.median([t/nf for _,t,nf,_,_ in ch]):.2f}")
