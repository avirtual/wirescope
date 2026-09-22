"""Is a bash file-read BOUNDED more often than a native Read?

Read defaults to 2000 lines when no limit is given -- the agent gets a big slab
without asking for one. A bash read is whatever the agent typed, and `head -50`
/ `sed -n '1,120p'` are bounded by construction. This is the mechanism that
would explain bash:read costing 1,435 tok/op against Read's 2,705.

Confound checked: maybe bash is simply pointed at SMALLER files. Without the
filesystem we cannot know true file size, but we CAN compare the two routes on
paths that exist in the repo today.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
BASH_READ=re.compile(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s+([^|;&]*)")
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
read_bounded=0; read_default=0; read_b_tok=[]; read_d_tok=[]
bash_bounded=0; bash_full=0; bb_tok=[]; bf_tok=[]
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
            if nm=="Read" and inp.get("file_path"):
                uses[blk["id"]]=("Read_bounded" if ("limit" in inp or "offset" in inp) else "Read_default")
            elif nm=="Bash":
                cmd=inp.get("command","")
                m2=BASH_READ.search(cmd)
                if not m2: continue
                verb,rest=m2.group(1),m2.group(2)
                if verb=="cat" and not re.search(r"\|\s*(head|tail|sed)", cmd):
                    uses[blk["id"]]="bash_full"
                elif verb in ("head","tail") or (verb=="sed" and "-n" in rest) or re.search(r"\|\s*(head|tail)", cmd):
                    uses[blk["id"]]="bash_bounded"
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            k2=uses.get(blk.get("tool_use_id"))
            if not k2: continue
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            n=len(s)/CH
            if k2=="Read_bounded": read_bounded+=1; read_b_tok.append(n)
            elif k2=="Read_default": read_default+=1; read_d_tok.append(n)
            elif k2=="bash_bounded": bash_bounded+=1; bb_tok.append(n)
            elif k2=="bash_full": bash_full+=1; bf_tok.append(n)
def st(v):
    if not v: return "  --"
    v=sorted(v)
    return f"{statistics.median(v):>8,.0f} {v[int(len(v)*.9)]:>8,.0f} {max(v):>9,.0f} {sum(v):>11,.0f}"
print(f"{'route':<22} {'n':>6} {'p50':>8} {'p90':>8} {'max':>9} {'totalTok':>11}")
for nm,cnt,v in (("Read (offset/limit)",read_bounded,read_b_tok),
                 ("Read (no limit=2000)",read_default,read_d_tok),
                 ("bash bounded",bash_bounded,bb_tok),
                 ("bash full cat",bash_full,bf_tok)):
    print(f"{nm:<22} {cnt:>6} {st(v)}")
tb=sum(read_b_tok)+sum(read_d_tok); tn=sum(bb_tok)+sum(bf_tok)
print(f"\n  Read total {tb:,.0f} tok over {read_bounded+read_default} fetches")
print(f"  bash total {tn:,.0f} tok over {bash_bounded+bash_full} fetches")
print(f"\n  share of each route that is BOUNDED:")
print(f"    Read {read_bounded/max(read_bounded+read_default,1)*100:.0f}%   bash {bash_bounded/max(bash_bounded+bash_full,1)*100:.0f}%")
