"""Decompose the window rise: more WORK per ticket, or more CONTEXT per unit of work?

window ~ calls x (tokens carried per call). If calls/seat drove it, the bash
instruction is exonerated and the story is ticket scope. If tok/call drove it,
the instruction is implicated.

Also: the within-period control -- among seats in the SAME week, do the
bash-heavy ones carry more? That separates the instruction from the calendar.
"""
import json,glob,os,re,statistics,datetime
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
def isfileop(nm,cmd):
    if nm in ("Read","Edit","Write"): return "native"
    if nm!="Bash": return None
    c=cmd.strip()
    if "<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c): return "bash"
    if re.search(r"^\s*(cat|head|tail|sed -n|sed -i)\b", c) or re.search(r"&&\s*(cat|head|tail|sed -n|sed -i)\b", c): return "bash"
    return None
recs=[]
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
    nat=bsh=ntot=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            ntot+=1
            k2=isfileop(blk.get("name"), blk.get("input",{}).get("command",""))
            if k2=="native": nat+=1
            elif k2=="bash": bsh+=1
    if ntot<80: continue
    d=datetime.date.fromtimestamp(mt); wkk=d-datetime.timedelta(days=d.weekday())
    fo=nat+bsh
    recs.append(dict(wk=wkk,win=win,calls=ntot,wpc=win/ntot,bp=(bsh/fo if fo else None),ticket=tk))
byw=defaultdict(list)
for r in recs: byw[r["wk"]].append(r)
print("== decomposition: calls vs tokens-per-call ==")
print(f"{'week':<12} {'n':>3} {'medCalls':>9} {'medWin/call':>12} {'medWindow':>10}")
base=None
for k in sorted(byw):
    g=byw[k]
    if len(g)<3: continue
    mc=statistics.median([x['calls'] for x in g]); mw=statistics.median([x['wpc'] for x in g]); mp=statistics.median([x['win'] for x in g])
    if base is None: base=(mc,mw,mp)
    print(f"{str(k):<12} {len(g):>3} {mc:>9,.0f} {mw:>12,.0f} {mp:>10,.0f}"
          + (f"   calls x{mc/base[0]:.2f}  tok/call x{mw/base[1]:.2f}" if base else ""))
print("\n== within-week control: bash-heavy vs mixed, SAME week ==")
print(f"{'week':<12} {'grp':<12} {'n':>3} {'medWin/call':>12} {'medCalls':>9}")
for k in sorted(byw):
    g=[x for x in byw[k] if x['bp'] is not None]
    hi=[x for x in g if x['bp']>=0.85]; lo=[x for x in g if x['bp']<0.85]
    for nm,gg in (("bash>=85%",hi),("mixed",lo)):
        if len(gg)<3: continue
        print(f"{str(k):<12} {nm:<12} {len(gg):>3} {statistics.median([x['wpc'] for x in gg]):>12,.0f} {statistics.median([x['calls'] for x in gg]):>9,.0f}")
