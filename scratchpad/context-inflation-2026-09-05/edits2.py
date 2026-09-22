"""Price the edit-path overhead, and check the one defence of the heredoc:
BATCHING. If one heredoc does 4 replaces, its wrapper amortizes 4:1 and the
comparison against a single native Edit is unfair. Count replaces per call.

Overhead is priced as re-CARRIAGE, not unique bytes: an edit block bakes into
history and re-ships on every later request of that seat, so the bill is
(overhead tokens) x (requests remaining in the session), at the read rate.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
STR=re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
REPL=re.compile(r"\.replace\s*\(|\bold\s*=|\bs\s*=\s*s\.")
# per-seat: overhead tokens and how many requests they will re-ship across
sessions=defaultdict(list)
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    sessions[(m.group(1),m.group(2))].append(f)

nrep=defaultdict(int); tot_calls=0
carriage_tok=0.0; usd=0.0
PRICE_READ={"claude-opus-5":0.5,"claude-fable-5-1":0.25,"claude-fable-5":1.0,"claude-sonnet-5":0.2}
for k,fs in sessions.items():
    fs.sort(key=os.path.getmtime)
    if len(fs)<5: continue
    # overhead introduced at request i re-ships on the (len-i) later requests
    last=fs[-1]
    try: b=json.load(open(last))
    except Exception: continue
    b=b.get("body",b)
    model=b.get("model") or ""
    rate=None
    for kk,v in PRICE_READ.items():
        if model.startswith(kk): rate=v
    if rate is None: continue
    # count overhead present in the final window
    ov=0
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use" or blk.get("name")!="Bash": continue
            cmd=(blk.get("input") or {}).get("command","")
            if not (("<<" in cmd and ("python3 -" in cmd or "python -" in cmd or "cat >" in cmd)) or "sed -i" in cmd): continue
            tot_calls+=1
            nrep[len(REPL.findall(cmd))]+=1
            wrap=len(cmd)-sum(len(m2.group(0)) for m2 in STR.finditer(cmd))
            ov+=max(wrap,0)
    # crude: overhead sits in history for ~half the session's requests on average
    reqs=len(fs)
    carriage_tok += ov/CH * reqs*0.5
    usd += ov/CH * reqs*0.5 /1e6 * rate
print(f"heredoc/sed-i edit calls in final windows: {tot_calls:,}")
print("\n== replaces per heredoc call (the batching defence) ==")
tot=sum(nrep.values())
for k in sorted(nrep):
    if nrep[k]<10: continue
    print(f"  {k if k<6 else '6+':>3} replace-ops : {nrep[k]:>5} calls  {nrep[k]/tot*100:>5.1f}%")
one=sum(v for k,v in nrep.items() if k<=1); multi=sum(v for k,v in nrep.items() if k>1)
print(f"\n  single-op calls {one:,} ({one/max(tot,1)*100:.0f}%)   multi-op {multi:,} ({multi/max(tot,1)*100:.0f}%)")
print(f"  mean replaces/call: {sum(k*v for k,v in nrep.items())/max(tot,1):.2f}")
print(f"\n== priced re-carriage of the WRAPPER (reclaimable framing only) ==")
print(f"  ~{carriage_tok:,.0f} tok  ~= ${usd:,.2f} across the analysed sessions")
