"""Wire cost of ONE file operation: Read/Edit/Write vs the Bash equivalent.

Pairs each tool_use with its tool_result by id, so the unit is a whole
round-trip (input payload + result), which is what actually lands in history
and re-ships. Bash calls are sub-classified by what they DO, since `cat file`
and `npm test` are not the same animal.

Deduped by tool_use id within a session's last request; only file-touching
bash is compared against the file tools.
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

def classify(cmd):
    c=cmd.strip()
    if "<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c or "tee " in c): return "bash:write/edit(heredoc)"
    if re.search(r"\b(sed -i|perl -pi)", c): return "bash:write/edit(sed -i)"
    if re.search(r"^\s*(cat|head|tail|sed -n)\b", c) or re.search(r"&&\s*(cat|head|tail|sed -n)\b", c): return "bash:read"
    if re.search(r"\b(grep|rg|ag)\b", c): return "bash:search"
    if re.search(r"\b(npm|yarn|node|pytest|python3? -m|make|cargo)\b", c): return "bash:run/test"
    if re.search(r"\bgit\b", c): return "bash:git"
    return "bash:other"

agg=defaultdict(lambda:[0,0,0])   # n, input_ch, result_ch
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    uses={}; 
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")=="tool_use":
                nm=blk.get("name")
                key = classify(blk.get("input",{}).get("command","")) if nm=="Bash" else nm
                uses[blk.get("id")]=(key,len(json.dumps(blk.get("input"))))
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            u=uses.get(blk.get("tool_use_id"))
            if not u: continue
            e=agg[u[0]]; e[0]+=1; e[1]+=u[1]; e[2]+=len(json.dumps(blk))
print(f"{'operation':<26} {'n':>6} {'inTok':>8} {'resTok':>8} {'TOTAL/op':>9}")
for k,(n,i,r) in sorted(agg.items(), key=lambda kv:-(kv[1][1]+kv[1][2])):
    if n<20: continue
    print(f"{k:<26} {n:>6} {i/n/CH:>8,.0f} {r/n/CH:>8,.0f} {(i+r)/n/CH:>9,.0f}")
print("\n== the comparison that matters ==")
def g(k): 
    n,i,r=agg.get(k,[0,0,0]); return (i+r)/n/CH if n else 0, n
for a,bb in (("Read","bash:read"),("Edit","bash:write/edit(heredoc)"),("Grep","bash:search")):
    va,na=g(a); vb,nb=g(bb)
    if va and vb:
        print(f"  {a:<6} {va:>7,.0f} tok/op (n={na:<5})   vs  {bb:<26} {vb:>7,.0f} tok/op (n={nb:<5})  = {vb/va:.2f}x")
