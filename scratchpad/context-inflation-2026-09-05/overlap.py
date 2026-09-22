"""Do the ranged Read repeats OVERLAP, or are they disjoint pages?

100% of repeat Reads carry offset/limit, so "same path twice" is paging, not
naive re-reading. But paging can still re-fetch: read 1-500, then 400-900
re-carries 100 lines. Only overlapping ranges are waste.

Measured as line-interval union vs sum over each (seat, path).
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
tot_sum=0; tot_union=0; npaths=0; overlapping=0
per=[]
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    byp=defaultdict(list)
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use" or blk.get("name")!="Read": continue
            inp=blk.get("input") or {}
            p=inp.get("file_path")
            if not p: continue
            off=inp.get("offset") or 1
            lim=inp.get("limit") or 2000
            byp[os.path.normpath(p)].append((int(off),int(off)+int(lim)))
    for p,iv in byp.items():
        if len(iv)<2: continue
        npaths+=1
        s=sum(b2-a for a,b2 in iv)
        iv2=sorted(iv); merged=[]
        for a,b2 in iv2:
            if merged and a<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],b2)
            else: merged.append([a,b2])
        un=sum(b2-a for a,b2 in merged)
        tot_sum+=s; tot_union+=un
        if s>un: overlapping+=1
        per.append((tk,p,len(iv),s,un))
print(f"(seat,path) pairs read 2+ times : {npaths:,}")
print(f"  lines fetched (sum of ranges) : {tot_sum:,}")
print(f"  distinct lines (union)        : {tot_union:,}")
print(f"  OVERLAP (re-fetched lines)    : {tot_sum-tot_union:,}  = {(tot_sum-tot_union)/max(tot_sum,1)*100:.1f}%")
print(f"  pairs with any overlap        : {overlapping:,} / {npaths:,} ({overlapping/max(npaths,1)*100:.0f}%)")
print("\n== worst overlap (one seat, one path) ==")
per.sort(key=lambda x:-(x[3]-x[4]))
for tk,p,n,s,un in per[:10]:
    print(f"  t{tk:<5} {n:>3} reads  sum {s:>6,}  union {un:>6,}  waste {s-un:>6,} lines  {os.path.basename(p)[:40]}")
