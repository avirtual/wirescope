"""Per-hand-seat window profile: is t654 actually anomalous vs its peers?

Window size per request = input + cache_read + cache_creation (the true prefix
token count off the receipt). We report the PEAK and the median, per ticket seat,
because a mean over a growing window describes no actual request.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
seats=defaultdict(list)
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    b=os.path.basename(f)
    m=re.search(r"clodex-clodex\.(t\d+)\.(hand|review-r\d+)-", b)
    if not m: continue
    r=f.replace(".request.json",".response.json")
    try:
        u=json.load(open(r)).get("usage") or {}
    except Exception: continue
    tot=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
    if tot<=0: continue
    seats[(m.group(1),m.group(2))].append((os.path.getmtime(f),tot))
rows=[]
for (t,kind),v in seats.items():
    v.sort()
    w=[x[1] for x in v]
    rows.append((t,kind,len(w),statistics.median(w),max(w)))
rows.sort(key=lambda r:-r[4])
print(f"{'ticket':>7} {'kind':<10} {'reqs':>5} {'medianWin':>10} {'peakWin':>10}")
for t,k,n,med,mx in rows[:30]:
    print(f"{t:>7} {k:<10} {n:>5} {med:>10,.0f} {mx:>10,}")
hands=[r for r in rows if r[1]=="hand"]
print(f"\nhand seats: {len(hands)}   median of peaks: {statistics.median([r[4] for r in hands]):,.0f}")
