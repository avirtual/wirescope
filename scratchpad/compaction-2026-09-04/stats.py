#!/usr/bin/env python3
import json, sys, statistics as st
from collections import Counter, defaultdict

PRICES = {  # in, out, w5m, w1h, read  ($/MTok)
 "claude-opus-5":        (5,25,6.25,10,0.50),
 "claude-opus-4-8":      (5,25,6.25,10,0.50),
 "claude-fable-5":       (10,50,12.5,20,1.00),
 "claude-fable-5-1":     (10,50,12.5,20,0.25),
 "claude-sonnet-5":      (2,10,2.50,4,0.20),
 "claude-haiku-4-5-20251001": (1,5,1.25,2,0.10),
}
def price(m):
    best=None
    for k,v in PRICES.items():
        if m and m.startswith(k) and (best is None or len(k)>len(best[0])): best=(k,v)
    return best[1] if best else None

def q(xs,p):
    xs=sorted(xs)
    if not xs: return None
    i=min(len(xs)-1,int(round(p*(len(xs)-1))))
    return xs[i]

def load(p):
    d=json.load(open(p))
    return [x for x in d if x["kind"]=="compact" and x.get("max_tokens")!=1]

recs=[]
for p in sys.argv[1:]:
    recs+=load(p)

# EXCLUDE my own A/B replay harness arms (agent names from the 2026-09-02 experiment)
AB={"ab-compact-sonnet","ab-compact-opus","ab-compact-fable"}
ab=[x for x in recs if str(x.get("agent")) in AB]
recs=[x for x in recs if str(x.get("agent")) not in AB]
print(f"excluded A/B replay arms: {len(ab)}")

good=[x for x in recs if x.get("usage") and x["usage"].get("output")]
print(f"compaction events: {len(recs)}  with usable receipt: {len(good)}")
print(f"distinct sessions: {len({x['session'] for x in good})}")
print()

for x in good:
    u=x["usage"]
    x["ctx"]=(u.get("input") or 0)+(u.get("cache_read") or 0)+(u.get("cache_write") or 0)
    x["out"]=u.get("output") or 0
    x["ratio"]=x["out"]/x["ctx"] if x["ctx"] else None

print("=== 1/2. SUMMARY SIZE vs CONTEXT SIZE ===")
def block(name,rows):
    if not rows: return
    ctx=[r["ctx"] for r in rows]; out=[r["out"] for r in rows]
    rat=[r["ratio"] for r in rows if r["ratio"]]
    print(f"\n-- {name}  (n={len(rows)})")
    print(f"   context in : min {min(ctx):>7,}  p50 {q(ctx,.5):>7,}  p90 {q(ctx,.9):>7,}  max {max(ctx):>7,}")
    print(f"   summary out: min {min(out):>7,}  p50 {q(out,.5):>7,}  p90 {q(out,.9):>7,}  max {max(out):>7,}")
    print(f"   ratio out/ctx: min {min(rat):.4f}  p50 {q(rat,.5):.4f}  p90 {q(rat,.9):.4f}  max {max(rat):.4f}")
block("ALL",good)
for m,n in Counter(x["model"] for x in good).most_common():
    if n>=5: block(m,[x for x in good if x["model"]==m])

print("\n=== 3. COST SHARE of one compaction ===")
rows=[]
for x in good:
    pr=price(x["model"])
    if not pr: continue
    i,o,w5,w1,rd=pr
    u=x["usage"]
    c_read=(u.get("cache_read") or 0)/1e6*rd
    c_in  =(u.get("input") or 0)/1e6*i
    c_w   =(u.get("cache_write") or 0)/1e6*w1
    c_out =x["out"]/1e6*o
    tot=c_read+c_in+c_w+c_out
    if tot>0: rows.append((x,c_read,c_in,c_w,c_out,tot))
print(f" n={len(rows)}")
sh=[r[4]/r[5] for r in rows]
print(f" OUTPUT share of compaction $: p50 {q(sh,.5)*100:.1f}%  p90 {q(sh,.9)*100:.1f}%  min {min(sh)*100:.1f}%  max {max(sh)*100:.1f}%")
shr=[(r[1])/r[5] for r in rows]
print(f" CACHE-READ share          : p50 {q(shr,.5)*100:.1f}%")
shi=[(r[2])/r[5] for r in rows]
print(f" UNCACHED-INPUT share      : p50 {q(shi,.5)*100:.1f}%")
shw=[(r[3])/r[5] for r in rows]
print(f" CACHE-WRITE share         : p50 {q(shw,.5)*100:.1f}%")
tot=[r[5] for r in rows]
print(f" $ per compaction: p50 ${q(tot,.5):.4f}  p90 ${q(tot,.9):.4f}  max ${max(tot):.4f}  SUM ${sum(tot):.2f}")

print("\n=== 4/amendment. WHERE COMPACTION FIRES (context size at trigger) ===")
ctx=[x["ctx"] for x in good]
for p in (0,.1,.25,.5,.75,.9,1.0):
    print(f"   p{int(p*100):<3} {q(ctx,p):>9,}")
buck=Counter()
for c in ctx:
    b=(c//50000)*50
    buck[b]+=1
for b in sorted(buck): print(f"   {b:>4}k-{b+50:<4}k : {buck[b]:>4}  {'#'*min(60,buck[b])}")

json.dump([{k:v for k,v in x.items() if k!='file'} for x in good],
          open("/private/tmp/claude-501/-Users-bogdan-projects-proxy-lab/5ac1ab3b-f6ac-4ace-ac03-d53240c425ab/scratchpad/compaction/events.json","w"))
