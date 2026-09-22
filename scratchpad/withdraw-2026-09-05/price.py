"""Price the dead-end ceiling in USD against the seats' real bill. For each hand
seat: sum the receipts' cache_read tokens across the whole session (what dead
carriage would have been billed at), and the total USD from _totals.json; then
the share the dead-end carriage represents if it had been stubbed the request
after it landed (carried at 0.1x for the rest of the session)."""
import json,glob,os,re
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
seats=defaultdict(list)
for f in glob.glob(os.path.join(LD,"*","*hand-*.response.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if m: seats[m.group(1)].append(f)
tot_usd=tot_read=0; n=0; tot_dirs=set()
for tk,fs in seats.items():
    for f in fs:
        try: r=json.load(open(f))
        except Exception: continue
        u=r.get("usage") or {}; tot_read+=u.get("cache_read_input_tokens") or 0
        b=r.get("billing") or r.get("bill") or {}
        tot_usd+=(b.get("est_usd") or 0); n+=1
    tot_dirs.add(os.path.dirname(fs[0]))
print(f"hand responses {n}; seats {len(seats)}; sum est_usd ${tot_usd:,.2f}; sum cache_read tok {tot_read:,.0f}")
# alt: from _totals.json per dir
alt=0
for d in tot_dirs:
    try: t=json.load(open(os.path.join(d,"_totals.json"))); alt+=t.get("est_usd") or t.get("usd") or 0
    except Exception: pass
print(f"_totals.json sum over those dirs ${alt:,.2f} (dirs shared with non-hand seats)")
