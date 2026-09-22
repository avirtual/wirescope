"""Same question, controlled for session length.

The raw medians (95,210 vs 94,790) compare a 30-call seat to a 250-call seat and
so measure how long a ticket ran, not how the seat worked. The comparable unit is
WINDOW PER TOOL CALL -- how much context each unit of work costs to carry.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    b=os.path.basename(f)
    m=re.search(r"clodex-clodex\.(t\d+)\.(hand|review-r\d+)-", b)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
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
    cnt=defaultdict(int); pay=defaultdict(int); res=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            t2=blk.get("type")
            if t2=="tool_use":
                nm=blk.get("name"); cnt[nm]+=1; pay[nm]+=len(json.dumps(blk.get("input")))
            elif t2=="tool_result": res+=len(json.dumps(blk))
    tot=sum(cnt.values())
    if tot<80: continue        # only seats that did real work
    bash=cnt.get("Bash",0)
    rows.append(dict(tk=tk,kind=kind,win=win,calls=tot,bashpct=bash/tot,
                     wpc=win/tot, callpay=sum(pay.values())/2.98,
                     bashpay=pay.get("Bash",0)/2.98, res=res/2.98))
def med(v,k): return statistics.median([x[k] for x in v]) if v else 0
hi=[r for r in rows if r["bashpct"]>=0.90]
lo=[r for r in rows if r["bashpct"]<0.70]
print(f"seats with >=80 tool calls: {len(rows)}   (bash-heavy {len(hi)}, mixed {len(lo)})\n")
hdr=f"{'group':<14} {'n':>4} {'calls':>7} {'window':>9} {'win/call':>9} {'callPayload':>12} {'callPay%win':>11}"
print(hdr)
for nm,g in (("bash >=90%",hi),("mixed <70%",lo)):
    if not g: continue
    print(f"{nm:<14} {len(g):>4} {med(g,'calls'):>7,.0f} {med(g,'win'):>9,.0f} {med(g,'wpc'):>9,.0f} "
          f"{med(g,'callpay'):>12,.0f} {statistics.median([x['callpay']/x['win']*100 for x in g]):>10.1f}%")
print("\n-- tool_use INPUT payload as share of window, by group --")
for nm,g in (("bash >=90%",hi),("mixed <70%",lo)):
    sh=[x['callpay']/x['win']*100 for x in g]
    print(f"  {nm:<12} median {statistics.median(sh):>5.1f}%   p90 {sorted(sh)[int(len(sh)*.9)]:>5.1f}%")
