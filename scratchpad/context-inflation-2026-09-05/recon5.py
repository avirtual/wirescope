"""Work-normalized edits + clodex-side causes, hands only, pre/post the 2.1.234 cut."""
import json,glob,os,re,statistics as st
from collections import defaultdict,Counter
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
INSTR="through the Bash tool wherever it can accomplish the job"
PATHRE=re.compile(r"""(?:\bp\s*=\s*|open\(\s*|Path\(\s*|cat\s*>>?\s*|tee\s+(?:-a\s+)?|sed\s+-i(?:\s+'')?\s+(?:-e\s+)?(?:'[^']*'|"[^"]*")\s+)(['"]?)([~\w./@-]+\.[A-Za-z]{1,5})\1""")
OPS=re.compile(r"\.replace\s*\(|\bre\.sub\s*\(|^\s*old\d*\s*=|sed\s+-i|cat\s*>>?\s*[~\w./]", re.M)
def isbashedit(c): c=c or ""; return ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c or "tee " in c)) or "sed -i" in c
def cls(cmd):
    if re.search(r"\.clodex/(projects|messages)|JOURNAL\.md",cmd): return "journal"
    if re.search(r"(?:cat\s*>>?\s*|p=['\"])/?(?:tmp|private/tmp|\.t\d+probe)",cmd): return "scratch"
    return "source"
def targets(cmd):
    out=set()
    for m in PATHRE.finditer(cmd): out.add(m.group(2))
    return out
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
rows=[]
for tk,(mt,f) in seats.items():
    try: b=json.load(open(f)); b=b.get("body",b)
    except Exception: continue
    try:
        u=json.load(open(f.replace(".request.json",".response.json"))).get("usage") or {}
        win=sum((u.get(x) or 0) for x in ("input_tokens","cache_read_input_tokens","cache_creation_input_tokens"))
    except Exception: continue
    if win<=0: continue
    msgs=b.get("messages") or []
    has=any(INSTR in json.dumps(m) for m in msgs[:3])
    r=dict(t=tk,has=has,win=win,calls=0,ops=0,nat_ops=0,bash_calls=0,srcfiles=set(),jour_tok=0.0,jour_calls=0,scr_tok=0.0,
           edit_tok=defaultdict(float),edit_ops=defaultdict(int),am_text_only=0,am_text_and_tool=0,am_tool_only=0,hook_tok=0.0,hook_n=0,bash_total=0)
    uses={}
    for i,m in enumerate(msgs):
        c=m.get("content")
        if m.get("role")=="system" and i>0:
            s=json.dumps(m)
            if "SessionStart hook additional context" in s: r["hook_tok"]+=len(s)/CH; r["hook_n"]+=1
            continue
        if m.get("role")=="assistant":
            if isinstance(c,list):
                has_t=any(x.get("type")=="text" and x.get("text","").strip() for x in c); has_u=any(x.get("type")=="tool_use" for x in c)
                r["am_text_only"]+=has_t and not has_u; r["am_text_and_tool"]+=has_t and has_u; r["am_tool_only"]+=has_u and not has_t
            else: r["am_text_only"]+=1
        if not isinstance(c,list): continue
        for x in c:
            if x.get("type")=="tool_use":
                r["calls"]+=1; nm=x["name"]; inp=x.get("input") or {}; n=len(json.dumps(inp))/CH
                if nm=="Bash": r["bash_total"]+=1
                if nm in ("Edit","Write"):
                    k="native"; r["edit_ops"][k]+=1; r["edit_tok"][k]+=n; r["srcfiles"].add(inp.get("file_path")); uses[x["id"]]=k
                elif nm=="Bash":
                    cmd=inp.get("command","")
                    if re.search(r"\.clodex/(projects|messages)|JOURNAL\.md",cmd) and not isbashedit(cmd):
                        r["jour_tok"]+=n; r["jour_calls"]+=1; uses[x["id"]]="journal"; continue
                    if not isbashedit(cmd): continue
                    k=cls(cmd); ops=max(1,len(OPS.findall(cmd)))
                    if k=="journal": r["jour_tok"]+=n; r["jour_calls"]+=1
                    else:
                        r["edit_ops"][k]+=ops; r["edit_tok"][k]+=n; r["bash_calls"]+=1
                        if k=="source": r["srcfiles"].update(targets(cmd))
                    uses[x["id"]]=k
            elif x.get("type")=="tool_result":
                k=uses.get(x.get("tool_use_id"))
                if not k: continue
                n=len(json.dumps(x.get("content")))/CH
                if k=="journal": r["jour_tok"]+=n
                else: r["edit_tok"][k]+=n
    r["srcfiles"].discard(None); r["nsrc"]=len(r["srcfiles"]); del r["srcfiles"]
    rows.append(r)
pre=[r for r in rows if not r["has"]]; post=[r for r in rows if r["has"]]
def mean(xs): xs=list(xs); return st.mean(xs) if xs else 0
for lab,g in (("PRE",pre),("POST",post)):
    print(f"=== {lab} n={len(g)}  window {mean(r['win'] for r in g):,.0f}  calls {mean(r['calls'] for r in g):.1f}  bash calls {mean(r['bash_total'] for r in g):.1f} ===")
    for k in ("native","source","scratch"):
        ops=mean(r["edit_ops"][k] for r in g); tok=mean(r["edit_tok"][k] for r in g)
        calls=mean((r["bash_calls"] if k!="native" else r["edit_ops"]["native"]) for r in g)
        print(f"  edit:{k:<8} ops/seat {ops:5.1f}  tok/seat {tok:7,.0f}  tok/op {tok/ops if ops else 0:6,.0f}")
    srcops=mean(r["edit_ops"]["native"]+r["edit_ops"]["source"] for r in g)
    print(f"  SOURCE edit ops/seat (native+bash source) {srcops:5.1f}   distinct source files edited/seat {mean(r['nsrc'] for r in g):4.1f}   ops/file {srcops/mean(r['nsrc'] for r in g):.2f}")
    print(f"  window / source-edit-op {mean(r['win']/(r['edit_ops']['native']+r['edit_ops']['source']) for r in g if r['edit_ops']['native']+r['edit_ops']['source']):7,.0f}   window / source file {mean(r['win']/r['nsrc'] for r in g if r['nsrc']):7,.0f}")
    print(f"  JOURNAL/clodex-msgs: calls/seat {mean(r['jour_calls'] for r in g):4.1f}  tok/seat {mean(r['jour_tok'] for r in g):6,.0f}")
    print(f"  SessionStart hook msg: n/seat {mean(r['hook_n'] for r in g):3.1f}  tok/seat {mean(r['hook_tok'] for r in g):6,.0f}")
    print(f"  assistant msgs/seat: text-only {mean(r['am_text_only'] for r in g):4.1f}  text+tool {mean(r['am_text_and_tool'] for r in g):4.1f}  tool-only {mean(r['am_tool_only'] for r in g):4.1f}")
# ops-per-call distribution for bash source edits post
print("\nheredoc ops/call check (post, source): ", end="")
d=Counter()
for r in post: pass
