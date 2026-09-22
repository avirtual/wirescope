"""Two failure modes bash file-work has that the native tools do not.

(1) REDUNDANCY. Read participates in readFileState: the CLI knows what the model
    has already seen. `cat`/`sed -n` do not, so nothing suppresses a re-fetch of
    bytes already in the window. Measured as: identical tool_result bodies
    (md5, >400 ch) appearing 2+ times in ONE request body.
(2) NO TRUNCATION CEILING. Read caps output; `cat` dumps whole files. Measured as
    the upper tail of result size.

Both counted per seat on its last (largest) request.
"""
import json,glob,os,re,hashlib,statistics
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
def classify(cmd):
    c=cmd.strip()
    if "<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c): return "bash:write/edit"
    if re.search(r"^\s*(cat|head|tail|sed -n)\b", c) or re.search(r"&&\s*(cat|head|tail|sed -n)\b", c): return "bash:read"
    if re.search(r"\b(grep|rg)\b", c): return "bash:search"
    return "bash:other"
sizes=defaultdict(list)
red_by_group={"bash-heavy":[], "mixed":[]}
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    uses={}; nbash=0; ntot=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")=="tool_use":
                nm=blk.get("name"); ntot+=1
                if nm=="Bash": nbash+=1
                key=classify(blk.get("input",{}).get("command","")) if nm=="Bash" else nm
                uses[blk.get("id")]=key
    if ntot<80: continue
    seen=defaultdict(int); dup_ch=0; tot_ch=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            g=uses.get(blk.get("tool_use_id"))
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            if g: sizes[g].append(len(s))
            tot_ch+=len(s)
            if len(s)>400:
                h=hashlib.md5(s.encode()).hexdigest()
                seen[h]+=1
                if seen[h]>1: dup_ch+=len(s)
    grp="bash-heavy" if nbash/ntot>=0.9 else ("mixed" if nbash/ntot<0.7 else None)
    if grp and tot_ch: red_by_group[grp].append(dup_ch/tot_ch*100)
print("== result size distribution (tok) ==")
print(f"{'op':<18} {'n':>6} {'p50':>7} {'p90':>8} {'p99':>8} {'max':>9}")
for k,v in sorted(sizes.items(), key=lambda kv:-len(kv[1])):
    if len(v)<50: continue
    v=sorted(v)
    q=lambda p: v[min(int(len(v)*p),len(v)-1)]/CH
    print(f"{k:<18} {len(v):>6} {q(.5):>7,.0f} {q(.9):>8,.0f} {q(.99):>8,.0f} {max(v)/CH:>9,.0f}")
print("\n== redundant tool_result bytes (identical body seen 2+ times in one window) ==")
for g,v in red_by_group.items():
    if not v: continue
    print(f"  {g:<12} n={len(v):<4} median {statistics.median(v):>5.1f}%   p90 {sorted(v)[int(len(v)*.9)]:>5.1f}%")
