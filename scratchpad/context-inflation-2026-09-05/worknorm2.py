"""worknorm.py with fixed path extraction (relative paths; heredoc targets via
open()/Path()/cat >/sed -i), unknown edit targets counted separately instead of
as fake-distinct, plus edit PAYLOAD tokens (the code actually written) as a
second routing-independent work unit."""
import json,glob,os,re,datetime,statistics as st
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
INSTR="through the Bash tool wherever it can accomplish the job"
PATH=r"(['\"]?)((?:~|\.{0,2}/)?[\w.@-]+(?:/[\w.@-]+)*\.[A-Za-z]{1,5})\1"
BASH_READ = re.compile(r"(?:^|&&|\|\||\||;)\s*(?:cat|head|tail|sed\s+-n\s+'?[\d,$p;]+'?)\s+(?:-[A-Za-z]+\s*\d*\s+)*"+PATH)
EDIT_T = re.compile(r"(?:cat\s*>>?\s*|sed\s+-i(?:\s+'')?\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\")\s+|open\(\s*|Path\(\s*|tee\s+(?:-a\s+)?)"+PATH)
def bash_reads(c): return [m.group(2) for m in BASH_READ.finditer(c or "")]
def is_bash_edit(c):
    c=c or ""
    return ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c or "tee " in c)) or "sed -i" in c
def bash_edits(c):
    out=set()
    for m in EDIT_T.finditer(c or ""):
        p=m.group(2)
        if not p.endswith((".py",".sh")) or "cat >" in c or "tee" in c: out.add(p)
    return out

seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
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
    msgs=b.get("messages") or []
    has=INSTR in json.dumps(b.get("system")) or any(INSTR in json.dumps(m) for m in msgs if m.get("role")=="system" or (msgs and m is msgs[0]))
    fetched=set(); edited=set(); calls=0; reads=0; rb=0; edits=0; eb=0; eunk=0
    read_tok=defaultdict(float); edit_in=0.0; edit_out=0.0; uses={}
    for msg in msgs:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            calls+=1; nm=blk.get("name"); inp=blk.get("input") or {}
            if nm=="Read":
                fetched.add(inp.get("file_path")); reads+=1; uses[blk["id"]]="nr"
            elif nm in ("Edit","Write"):
                edited.add(inp.get("file_path")); edits+=1; edit_in+=len(json.dumps(inp))/CH; uses[blk["id"]]="ne"
            elif nm=="Bash":
                cmd=inp.get("command","")
                if not re.search(r"\b(grep|rg)\b",cmd):
                    p=bash_reads(cmd)
                    if p: fetched.update(p); reads+=1; rb+=1; uses[blk["id"]]="br"
                if is_bash_edit(cmd):
                    edits+=1; eb+=1; edit_in+=len(cmd)/CH; uses[blk["id"]]="be"
                    t=bash_edits(cmd)
                    if t: edited.update(t)
                    else: eunk+=1
        for blk in c:
            if blk.get("type")!="tool_result": continue
            k2=uses.get(blk.get("tool_use_id"))
            if not k2: continue
            n=len(json.dumps(blk.get("content")))/CH
            if k2 in ("nr","br"): read_tok[k2]+=n
            else: edit_out+=n
    fetched.discard(None); edited.discard(None)
    rows.append(dict(t=tk,d=str(datetime.date.fromtimestamp(mt)),has=has,win=win,calls=calls,nf=len(fetched),ne=len(edited),eunk=eunk,
        reads=reads,rb=rb,edits=edits,eb=eb,nrt=read_tok["nr"],brt=read_tok["br"],ein=edit_in,eout=edit_out))
def mean(xs): return st.mean(xs) if xs else 0
def show(label,g):
    if not g: return
    print(f"  {label:<15} n={len(g):3d} win={mean([r['win'] for r in g]):8,.0f} calls={mean([r['calls'] for r in g]):5.1f}"
          f" fetchedF={mean([r['nf'] for r in g]):5.1f} editedF={mean([r['ne'] for r in g]):5.1f}(unk {mean([r['eunk'] for r in g]):3.1f})"
          f" reads/F={mean([r['reads']/r['nf'] for r in g if r['nf']]):4.2f} bash%={100*mean([r['rb']/r['reads'] for r in g if r['reads']]):3.0f}"
          f" readTok={mean([r['nrt']+r['brt'] for r in g]):7,.0f} editIn={mean([r['ein'] for r in g]):7,.0f} editOut={mean([r['eout'] for r in g]):6,.0f}"
          f" win/F={mean([r['win']/r['nf'] for r in g if r['nf']]):7,.0f} win/editedF={mean([r['win']/r['ne'] for r in g if r['ne']]):7,.0f}"
          f" win/editKtok={mean([r['win']/r['ein']*1000 for r in g if r['ein']>200]):6,.0f}")
wk=defaultdict(list)
for r in rows:
    d=datetime.date.fromisoformat(r["d"]); wk[str(d-datetime.timedelta(days=d.weekday()))].append(r)
print("--- by week ---")
for w in sorted(wk): show(w,wk[w])
print("--- by instruction ---")
show("no-instr",[r for r in rows if not r["has"]]); show("instr",[r for r in rows if r["has"]])
tr=[r for r in rows if r["has"] and r["reads"]]
print("--- treated, by bash-read share ---")
show("instr bash<50%",[r for r in tr if r["rb"]/r["reads"]<0.5]); show("instr bash>=50%",[r for r in tr if r["rb"]/r["reads"]>=0.5])
print("--- pre-cut (no-instr) by bash share, as a check the split isn't just 'bash-preferring seats are weird' ---")
un=[r for r in rows if not r["has"] and r["reads"]]
show("noinstr bash<50%",[r for r in un if r["rb"]/r["reads"]<0.5]); show("noinstr bash>=50%",[r for r in un if r["rb"]/r["reads"]>=0.5])
json.dump(rows,open("worknorm2.json","w"))
