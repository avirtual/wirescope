"""Price the bash_output_audience_note's RE-CARRIAGE, not its unique bytes.

The note is 148 chars once, but it bakes into history and re-ships on every
subsequent request of that agent's lineage. So the bill is sum-over-requests
of (copies present in THAT request), priced at each request's own cache
disposition (read 0.1x vs uncached 1x), not 148 chars x number of firings.
"""
import json,os,sys,glob
from collections import defaultdict

LD=sys.argv[1]
N="Only you see that command's output"
CH_PER_TOK=2.98   # message JSON density, tokest.py

PRICES={  # (in, cache_read) per MTok
 "claude-fable-5-1":(10.0,0.25), "claude-fable-5":(10.0,1.0),
 "claude-opus-5":(5.0,0.5), "claude-sonnet-5":(2.0,0.2),
 "claude-opus-4-8":(5.0,0.5), "claude-haiku-4-5":(1.0,0.1),
 "claude-haiku-4-5-20251001":(1.0,0.1),
}
def price(model):
    if not model: return None
    best=None
    for k,v in PRICES.items():
        if model.startswith(k) and (best is None or len(k)>len(best[0])): best=(k,v)
    return best[1] if best else None

tot_tok=0.0; tot_usd=0.0; fires=0
per_model=defaultdict(lambda:[0.0,0.0,0])
sess=sorted(os.listdir(LD))
for s in sess:
    d=os.path.join(LD,s)
    if not os.path.isdir(d): continue
    for r in glob.glob(os.path.join(d,"*.request.json")):
        try: b=json.load(open(r))
        except Exception: continue
        b=b.get("body",b)
        msgs=b.get("messages") or []
        cnt=0
        for m in msgs:
            c=m.get("content")
            t=c if isinstance(c,str) else json.dumps(c)
            cnt+=t.count(N)
        if not cnt: continue
        model=b.get("model")
        p=price(model)
        if not p: continue
        tok=cnt*148/CH_PER_TOK
        # disposition: read the receipt if present
        resp=r.replace(".request.json",".response.json")
        rate=p[1]   # assume cached read (dominant case)
        try:
            rb=json.load(open(resp))
            u=(rb.get("usage") or (rb.get("body") or {}).get("usage") or {})
            cr=u.get("cache_read_input_tokens") or 0
            cc=u.get("cache_creation_input_tokens") or 0
            if cc>cr: rate=p[0]
        except Exception: pass
        usd=tok/1e6*rate
        tot_tok+=tok; tot_usd+=usd; fires+=1
        e=per_model[str(model)]; e[0]+=tok; e[1]+=usd; e[2]+=1

print(f"requests carrying the note : {fires:,}")
print(f"total note carriage        : {tot_tok:,.0f} tok")
print(f"total cost                 : ${tot_usd:,.2f}")
print("\n--- by model ---")
print(f"{'reqs':>6} {'tok':>12} {'usd':>9}  model")
for m,(tk,us,n) in sorted(per_model.items(), key=lambda kv:-kv[1][1]):
    print(f"{n:>6} {tk:>12,.0f} ${us:>8.2f}  {m}")
