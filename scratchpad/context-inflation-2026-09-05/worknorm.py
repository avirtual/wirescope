"""Work-normalized view. Bogdan's objection: the volume/price split treats ops as
routing-independent, but a bounded bash read fetches ~1/3 of a ranged Read per
call, so the SAME reading job takes ~3x the calls -> extra calls got booked as
"volume". Normalize by routing-independent work units instead:
  - distinct files FETCHED (Read path U bash read path)
  - distinct files EDITED (Edit/Write path U heredoc/sed -i target when parseable)
And date the treatment: first appearance of the bash-preference instruction on
the wire, adoption by day.
"""
import json,glob,os,re,datetime,statistics as st
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
INSTR="through the Bash tool wherever it can accomplish the job"
BASH_READ = re.compile(
    r"(?:^|&&|\||;)\s*(?:cat|head|tail|sed\s+-n\s+[^ ]+)\s+(?:-[A-Za-z0-9]+\s+)*(['\"]?)([~/][^\s'\"|;&>]+)\1")
EDIT_T = re.compile(r"(?:cat\s*>\s*|sed\s+-i(?:\s+'')?\s+(?:-e\s+)?'[^']*'\s+|open\(\s*['\"])(['\"]?)([~/][^\s'\"|;&>)]+)\1")
def bash_reads(c): return [m.group(2) for m in BASH_READ.finditer(c or "")]
def bash_edits(c):
    c=c or ""
    if not ("<<" in c or "sed -i" in c): return []
    return [m.group(2) for m in EDIT_T.finditer(c)]
def is_bash_edit(c):
    c=c or ""
    return ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c)) or "sed -i" in c

seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", bn)
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)

rows=[]
for tk,(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    try:
        u=json.load(open(f.replace(".request.json",".response.json"))).get("usage") or {}
        win=sum((u.get(x) or 0) for x in ("input_tokens","cache_read_input_tokens","cache_creation_input_tokens"))
    except Exception: continue
    if win<=0: continue
    blob=json.dumps(b.get("system"))+json.dumps(b.get("messages")[:1] if b.get("messages") else "")
    # instruction may ride as a trailing system msg too
    has=INSTR in blob or any(INSTR in json.dumps(m) for m in (b.get("messages") or []) if m.get("role")=="system")
    fetched=set(); edited=set(); calls=0; reads_n=0; reads_bash=0; edits_n=0; edits_bash=0
    nat_read_tok=0; bash_read_tok=0; uses={}
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            calls+=1; nm=blk.get("name"); inp=blk.get("input") or {}
            if nm=="Read":
                fetched.add(inp.get("file_path")); reads_n+=1; uses[blk["id"]]="nr"
            elif nm in ("Edit","Write"):
                edited.add(inp.get("file_path")); edits_n+=1
            elif nm=="Bash":
                cmd=inp.get("command","")
                p=bash_reads(cmd)
                if p and not re.search(r"\b(grep|rg)\b",cmd):
                    fetched.update(p); reads_n+=1; reads_bash+=1; uses[blk["id"]]="br"
                if is_bash_edit(cmd):
                    edits_n+=1; edits_bash+=1; edited.update(bash_edits(cmd) or [f"?{tk}:{edits_bash}"])
        for blk in c:
            if blk.get("type")!="tool_result": continue
            k2=uses.get(blk.get("tool_use_id"))
            if not k2: continue
            n=len(json.dumps(blk.get("content")))/CH
            if k2=="nr": nat_read_tok+=n
            else: bash_read_tok+=n
    fetched.discard(None); edited.discard(None)
    rows.append(dict(t=tk,d=datetime.date.fromtimestamp(mt),has=has,win=win,calls=calls,
        nf=len(fetched),ne=len(edited),reads=reads_n,rb=reads_bash,edits=edits_n,eb=edits_bash,
        nrt=nat_read_tok,brt=bash_read_tok))

rows.sort(key=lambda r:r["d"])
print("seats:",len(rows))
# --- cut date
byday=defaultdict(lambda:[0,0])
for r in rows: byday[r["d"]][0]+=1; byday[r["d"]][1]+=r["has"]
print("\n--- instruction adoption by day (seats, with-instruction) ---")
for d in sorted(byday):
    n,h=byday[d]; print(f"  {d}  {n:4d}  {h:4d}  {'#'*int(20*h/n)}")

def mean(xs): return st.mean(xs) if xs else 0
def show(label,grp):
    if not grp: return
    print(f"  {label:<14} n={len(grp):3d}  win={mean([r['win'] for r in grp]):8,.0f}  calls={mean([r['calls'] for r in grp]):5.1f}"
          f"  files_fetched={mean([r['nf'] for r in grp]):5.1f}  files_edited={mean([r['ne'] for r in grp]):4.1f}"
          f"  reads/file={mean([r['reads']/r['nf'] for r in grp if r['nf']]):4.2f}"
          f"  bashread%={100*mean([r['rb']/r['reads'] for r in grp if r['reads']]):3.0f}"
          f"  win/file={mean([r['win']/r['nf'] for r in grp if r['nf']]):7,.0f}"
          f"  win/edited={mean([r['win']/r['ne'] for r in grp if r['ne']]):7,.0f}")
print("\n--- by week ---")
wk=defaultdict(list)
for r in rows: wk[r["d"]-datetime.timedelta(days=r["d"].weekday())].append(r)
for w in sorted(wk): show(str(w),wk[w])
print("\n--- by instruction present ---")
show("no-instr",[r for r in rows if not r["has"]]); show("instr",[r for r in rows if r["has"]])
print("\n--- instruction present, split by bash-read share (within treated) ---")
tr=[r for r in rows if r["has"] and r["reads"]]
show("instr bash<50%",[r for r in tr if r["rb"]/r["reads"]<0.5]); show("instr bash>=50%",[r for r in tr if r["rb"]/r["reads"]>=0.5])
print("\n--- window binned, files fetched & calls (does big window = more files, or more calls per file?) ---")
for lo,hi in [(0,60e3),(60e3,120e3),(120e3,180e3),(180e3,240e3),(240e3,9e9)]:
    g=[r for r in rows if lo<=r["win"]<hi]
    show(f"{int(lo/1e3)}k-{int(hi/1e3) if hi<1e9 else '+'}",g)
json.dump([{**r,"d":str(r["d"])} for r in rows],open("worknorm.json","w"))
