"""Is there a TREND: are recent hand seats carrying more context than older ones?

The user's claim is about NEW hands, so ticket number (a proxy for time) is the
axis, not the cross-section. Peak window per seat, bucketed by ticket range.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
seats=defaultdict(lambda:[0,0,None])   # peak, calls, mtime
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", bn)
    if not m: continue
    r=f.replace(".request.json",".response.json")
    try:
        u=json.load(open(r)).get("usage") or {}
    except Exception: continue
    win=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
    if win<=0: continue
    t=int(m.group(1)); e=seats[t]
    if win>e[0]: e[0]=win
    e[1]+=1
    mt=os.path.getmtime(f)
    if e[2] is None or mt>e[2]: e[2]=mt
rows=sorted(seats.items())
print(f"hand seats with receipts: {len(rows)}\n")
buckets=[(0,400),(400,500),(500,570),(570,610),(610,640),(640,700)]
print(f"{'tickets':<12} {'n':>4} {'medPeak':>9} {'p90Peak':>9} {'medReqs':>8}")
for lo,hi in buckets:
    g=[(t,v) for t,v in rows if lo<=t<hi]
    if not g: continue
    pk=sorted(v[0] for t,v in g); rq=sorted(v[1] for t,v in g)
    print(f"t{lo}-{hi:<7} {len(g):>4} {statistics.median(pk):>9,.0f} {pk[int(len(pk)*.9)]:>9,.0f} {statistics.median(rq):>8,.0f}")
print("\n-- last 20 hand seats by ticket number --")
print(f"{'ticket':>7} {'peakWin':>9} {'reqs':>6}")
for t,v in rows[-20:]:
    print(f"t{t:>6} {v[0]:>9,} {v[1]:>6}")
