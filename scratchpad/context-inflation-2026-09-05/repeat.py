"""Repeat fetches of the SAME FILE PATH -- the check my md5 pass could not do.

Why the earlier 0.0% was wrong-instrument: it hashed tool_result BODIES. A file
re-read after an edit differs by a few bytes, so exact-match dedup scores it as
"not redundant" while the window carries the whole file twice. Keying on PATH
counts what actually re-ships.

Native Read participates in readFileState (client-side, never on the wire), so
the CLI knows what the model has already seen. `cat`/`sed -n`/`head` do not --
nothing suppresses or shortens a re-fetch. That asymmetry is the hypothesis.

Path extraction is deliberately conservative: only unambiguous single-file
fetch forms. A miss undercounts bash repeats, so any bash>native result here is
a LOWER bound.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98

BASH_READ = re.compile(
    r"(?:^|&&|\||;)\s*(?:cat|head|tail|sed\s+-n\s+[^ ]+)\s+"
    r"(?:-[A-Za-z0-9]+\s+)*"          # flags like -n 50
    r"(['\"]?)([~/][^\s'\"|;&>]+)\1")

def bash_paths(cmd):
    return [m.group(2) for m in BASH_READ.finditer(cmd or "")]

seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
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

    uses={}   # tool_use id -> (via, path)
    ncalls=0; nbashfile=0; nnatfile=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            ncalls+=1
            nm=blk.get("name"); inp=blk.get("input") or {}
            if nm=="Read":
                p=inp.get("file_path")
                if p: uses[blk["id"]]=("Read",os.path.normpath(p)); nnatfile+=1
            elif nm in ("Edit","Write"):
                nnatfile+=1
            elif nm=="Bash":
                ps=bash_paths(inp.get("command",""))
                if len(ps)==1:
                    uses[blk["id"]]=("bash",os.path.normpath(ps[0])); nbashfile+=1
    if ncalls<80: continue

    # pair results, tally per (via,path)
    fetch=defaultdict(list)
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            u2=uses.get(blk.get("tool_use_id"))
            if not u2: continue
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            fetch[u2].append(len(s))

    agg={}
    for via in ("Read","bash"):
        items={p:v for (vv,p),v in fetch.items() if vv==via}
        n=sum(len(v) for v in items.values())
        distinct=len(items)
        repeat_tok=sum(sum(v[1:]) for v in items.values())/CH
        total_tok=sum(sum(v) for v in items.values())/CH
        agg[via]=(n,distinct,repeat_tok,total_tok)
    tot_file=nbashfile+nnatfile
    rows.append(dict(tk=tk,kind=kind,win=win,calls=ncalls,
                     bshare=(nbashfile/tot_file if tot_file else None),
                     R=agg["Read"], B=agg["bash"]))

def show(g,label):
    if len(g)<3: return
    def m(fn): return statistics.median([fn(x) for x in g])
    print(f"  {label:<16} n={len(g):<4} "
          f"Read {m(lambda x:x['R'][0]):>5.0f} fetch/{m(lambda x:x['R'][1]):>4.0f} distinct  "
          f"bash {m(lambda x:x['B'][0]):>5.0f}/{m(lambda x:x['B'][1]):>4.0f}")

print(f"seats analysed: {len(rows)}\n")
tR=[sum(x['R'][0] for x in rows), sum(x['R'][1] for x in rows), sum(x['R'][2] for x in rows), sum(x['R'][3] for x in rows)]
tB=[sum(x['B'][0] for x in rows), sum(x['B'][1] for x in rows), sum(x['B'][2] for x in rows), sum(x['B'][3] for x in rows)]
print("== FLEET TOTALS: file fetches by route ==")
print(f"{'route':<8} {'fetches':>8} {'distinct':>9} {'fetch/file':>11} {'repeatTok':>11} {'totalTok':>11} {'repeat%':>8}")
for nm,t in (("Read",tR),("bash",tB)):
    ratio=t[0]/t[1] if t[1] else 0
    pct=t[2]/t[3]*100 if t[3] else 0
    print(f"{nm:<8} {t[0]:>8,} {t[1]:>9,} {ratio:>11.2f} {t[2]:>11,.0f} {t[3]:>11,.0f} {pct:>7.1f}%")

print("\n== worst repeat offenders (single path, one window) ==")
allp=[]
for (tk,kind),(mt,f) in list(seats.items()):
    pass
# re-walk for detail on top seats
detail=[]
for x in sorted(rows,key=lambda r:-r['B'][2])[:6]:
    detail.append(x)
for x in detail:
    print(f"  t{x['tk']:<5} win {x['win']:>8,}  bash repeat {x['B'][2]:>7,.0f} tok  "
          f"({x['B'][0]} fetches / {x['B'][1]} distinct files)")
