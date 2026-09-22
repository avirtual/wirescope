"""Split the rise into PRICE and VOLUME (Oaxaca-style).

For each category: tokens = ops x price_per_op.
  delta_tokens = (delta_ops x price_0) + (ops_0 x delta_price) + (delta_ops x delta_price)
                  ^volume effect        ^price effect          ^interaction

This directly answers the user's point: per-op prices fell, so if the total rose,
the volume term must dominate. Quantify by how much, per category.

Also the counterfactual the user is really asking: what WOULD the window be if
the fleet had kept week-1 tool ROUTING but done week-4 volume of work?
"""
import json,glob,os,re,datetime
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
BASH_READ=re.compile(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s+([^|;&]*)")
def bkind(cmd):
    c=cmd.strip()
    if ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c)) or "sed -i" in c: return "write:bash"
    m=BASH_READ.search(c)
    if m and not re.search(r"\b(grep|rg)\b",c): return "read:bash"
    if re.search(r"\b(grep|rg|find)\b",c): return "search:bash"
    if re.search(r"\b(npm|node|pytest|python3? -m|make|cargo|jest)\b",c): return "run/test"
    if re.search(r"\bgit\b",c): return "git"
    return "other:bash"
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
W=defaultdict(lambda: defaultdict(lambda:[0.0,0]))   # week -> cat -> [tok, ops]
seatn=defaultdict(int)
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
    uses={}; buck=defaultdict(float); ops=defaultdict(int)
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); inp=blk.get("input") or {}
            n=len(json.dumps(inp))/CH
            k2 = bkind(inp.get("command","")) if nm=="Bash" else (
                 "read:native" if nm=="Read" else "write:native" if nm in ("Edit","Write") else
                 "search:native" if nm in ("Grep","Glob") else "other")
            uses[blk["id"]]=k2; buck[k2]+=n; ops[k2]+=1
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            k2=uses.get(blk.get("tool_use_id"))
            if k2: buck[k2]+=len(json.dumps(blk))/CH
    d=datetime.date.fromtimestamp(mt); wk=d-datetime.timedelta(days=d.weekday())
    seatn[wk]+=1
    for k2 in set(list(buck)+list(ops)):
        e=W[wk][k2]; e[0]+=buck.get(k2,0); e[1]+=ops.get(k2,0)
weeks=[w for w in sorted(W) if seatn[w]>=8]
w0,w1=weeks[0],weeks[-1]
CATS=["read:native","read:bash","search:native","search:bash","write:native","write:bash","run/test","git"]
print(f"per-seat means: week {w0} (n={seatn[w0]}) vs {w1} (n={seatn[w1]})\n")
print(f"{'category':<15} {'ops0':>6} {'ops1':>6} {'price0':>8} {'price1':>8} {'VOLUME':>9} {'PRICE':>9} {'net':>9}")
TV=TP=TI=0
for c in CATS:
    o0=W[w0][c][1]/seatn[w0]; o1=W[w1][c][1]/seatn[w1]
    t0=W[w0][c][0]/seatn[w0]; t1=W[w1][c][0]/seatn[w1]
    p0=t0/o0 if o0 else 0; p1=t1/o1 if o1 else 0
    vol=(o1-o0)*p0; pri=o0*(p1-p0); inter=(o1-o0)*(p1-p0)
    TV+=vol; TP+=pri; TI+=inter
    print(f"{c:<15} {o0:>6.1f} {o1:>6.1f} {p0:>8,.0f} {p1:>8,.0f} {vol:>+9,.0f} {pri:>+9,.0f} {t1-t0:>+9,.0f}")
print(f"\n  VOLUME effect (more ops)      : {TV:>+10,.0f} tok/seat")
print(f"  PRICE effect (cost per op)    : {TP:>+10,.0f} tok/seat")
print(f"  interaction                   : {TI:>+10,.0f} tok/seat")
print(f"  net tool-traffic change       : {TV+TP+TI:>+10,.0f} tok/seat")
print("\n== SUBSTITUTION CHECK: did bash REPLACE native, or ADD to it? ==")
for pair,label in ((("read:native","read:bash"),"reads"),(("write:native","write:bash"),"writes"),
                   (("search:native","search:bash"),"searches")):
    a0=W[w0][pair[0]][1]/seatn[w0]; b0=W[w0][pair[1]][1]/seatn[w0]
    a1=W[w1][pair[0]][1]/seatn[w1]; b1=W[w1][pair[1]][1]/seatn[w1]
    print(f"  {label:<10} native {a0:>5.1f} -> {a1:>5.1f} ({a1-a0:+.1f})   bash {b0:>5.1f} -> {b1:>5.1f} ({b1-b0:+.1f})"
          f"   TOTAL {a0+b0:>5.1f} -> {a1+b1:>5.1f} ({a1+b1-a0-b0:+.1f})")
print("\n== COUNTERFACTUAL: week-4 volume of work at week-1 ROUTING ==")
# route week-4 total ops of each family through week-1's route mix at week-1 prices
for fam,(nat,bsh) in (("reads",("read:native","read:bash")),("writes",("write:native","write:bash")),
                      ("searches",("search:native","search:bash"))):
    o0n=W[w0][nat][1]/seatn[w0]; o0b=W[w0][bsh][1]/seatn[w0]
    o1n=W[w1][nat][1]/seatn[w1]; o1b=W[w1][bsh][1]/seatn[w1]
    p1n=(W[w1][nat][0]/seatn[w1])/o1n if o1n else 0
    p1b=(W[w1][bsh][0]/seatn[w1])/o1b if o1b else 0
    tot1=o1n+o1b; share0=o0n/(o0n+o0b) if (o0n+o0b) else 0
    actual=o1n*p1n+o1b*p1b
    cf=tot1*share0*p1n + tot1*(1-share0)*p1b
    print(f"  {fam:<10} actual {actual:>8,.0f} tok   if week-1 route mix ({share0*100:.0f}% native): {cf:>8,.0f} tok"
          f"   -> routing saved {cf-actual:>+8,.0f}")
