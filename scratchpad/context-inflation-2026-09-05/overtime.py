"""Did the CLI's bash-over-Read/Edit instruction actually change behaviour, and
did the window follow? Bucketed by DATE (the user's axis), hand seats only.

Two series per week:
  file-op mix  -- what share of file operations went through Bash
  window/call  -- context carried per unit of work (length-controlled)
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
wk=defaultdict(lambda: dict(seats=0,native=0,bash=0,wpc=[],peak=[],callpay=[]))
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
    nat=bsh=ntot=0; pay=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            ntot+=1; pay+=len(json.dumps(blk.get("input")))
            k2=isfileop(blk.get("name"), blk.get("input",{}).get("command",""))
            if k2=="native": nat+=1
            elif k2=="bash": bsh+=1
    if ntot<80: continue
    d=datetime.date.fromtimestamp(mt); key=d - datetime.timedelta(days=d.weekday())
    e=wk[key]; e["seats"]+=1; e["native"]+=nat; e["bash"]+=bsh
    e["wpc"].append(win/ntot); e["peak"].append(win); e["callpay"].append(pay/CH)
print(f"{'week of':<12} {'seats':>6} {'fileops':>8} {'bash%':>6} {'medPeakWin':>11} {'win/call':>9} {'callPayTok':>11}")
for k in sorted(wk):
    e=wk[k]; tot=e["native"]+e["bash"]
    if e["seats"]<3: continue
    print(f"{str(k):<12} {e['seats']:>6} {tot:>8} {(e['bash']/tot*100 if tot else 0):>5.0f}% "
          f"{statistics.median(e['peak']):>11,.0f} {statistics.median(e['wpc']):>9,.0f} {statistics.median(e['callpay']):>11,.0f}")
