"""Does a seat's TOOL MIX predict its window size?

Hypothesis: bypass-permissions mode instructs seats to do file work through Bash
(cat/sed/heredoc) instead of Read/Edit/Write. A heredoc edit carries the old AND
new text inside the tool_use INPUT, which is history and re-ships forever; the
same edit via Edit carries the same strings but a ~20-token tool_result, whereas
a Bash edit's result is whatever the script printed.

Measured on each seat's LAST request (the accumulated window), so tool_use counts
are the whole session's, deduped by block identity within that one body.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    b=os.path.basename(f)
    m=re.search(r"clodex-clodex\.(t\d+)\.(hand|review-r\d+)-", b)
    if not m: continue
    k=(m.group(1),m.group(2))
    t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
rows=[]
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    r=f.replace(".request.json",".response.json")
    try:
        u=json.load(open(r)).get("usage") or {}
        win=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
    except Exception: continue
    if win<=0: continue
    cnt=defaultdict(int); pay=defaultdict(int)
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); cnt[nm]+=1; pay[nm]+=len(json.dumps(blk.get("input")))
    tot=sum(cnt.values())
    if tot<20: continue
    bash=cnt.get("Bash",0); edit=cnt.get("Edit",0)+cnt.get("Write",0)+cnt.get("Read",0)
    rows.append((tk,kind,win,tot,bash/tot,edit,pay.get("Bash",0)/2.98))
rows.sort(key=lambda r:-r[2])
print(f"{'ticket':>7} {'kind':<9} {'window':>9} {'calls':>6} {'bash%':>6} {'R/E/W':>6} {'bashPayloadTok':>14}")
for tk,kind,win,tot,bp,edit,bpay in rows[:22]:
    print(f"{tk:>7} {kind:<9} {win:>9,} {tot:>6} {bp*100:>5.0f}% {edit:>6} {bpay:>14,.0f}")
hi=[r for r in rows if r[4]>=0.9]; lo=[r for r in rows if r[4]<0.6]
def med(v,i): return statistics.median([x[i] for x in v]) if v else 0
print(f"\nbash-dominant (>=90% of calls): n={len(hi)}  median window {med(hi,2):,.0f}")
print(f"mixed        (<60% bash)      : n={len(lo)}  median window {med(lo,2):,.0f}")
