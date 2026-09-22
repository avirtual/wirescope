"""Does the bash regime generate RETRIES and VERIFICATION reads that native Edit did not?
Per source edit (native Edit/Write vs bash heredoc/sed -i), look at the next 3 tool calls:
  - same-file re-edit (retry), same-file read (verify), failure marker in the edit's own result.
Hands only, pre/post 2.1.234."""
import json,glob,os,re,statistics as st
from collections import defaultdict,Counter
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
INSTR="through the Bash tool wherever it can accomplish the job"
PATHRE=re.compile(r"""(?:\bp\s*=\s*|open\(\s*|Path\(\s*|cat\s*>>?\s*|tee\s+(?:-a\s+)?|sed\s+-i(?:\s+'')?\s+(?:-e\s+)?(?:'[^']*'|"[^"]*")\s+)(['"]?)([~\w./@-]+\.[A-Za-z]{1,5})\1""")
READRE=re.compile(r"(?:^|&&|\|\||\||;)\s*(?:cat|head|tail|sed\s+-n\s+'?[\d,$p;]+'?|grep\s+-n[^|]*?)\s+(?:-[A-Za-z]+\s*\d*\s+)*(['\"]?)([~\w./@-]+\.[A-Za-z]{1,5})\1", re.M)
FAIL=re.compile(r"Traceback|AssertionError|not found|No such file|0 replacements|nothing to replace|has not been read|File has been modified|Error:|error:", re.I)
def isbashedit(c): c=c or ""; return ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c or "tee " in c)) or "sed -i" in c
def base(p): return os.path.basename(p) if p else None
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.hand-", os.path.basename(f))
    if not m: continue
    k=m.group(1); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
S=defaultdict(lambda:defaultdict(list))   # (period,kind) -> metric -> list
for tk,(mt,f) in seats.items():
    try: b=json.load(open(f)); b=b.get("body",b)
    except Exception: continue
    msgs=b.get("messages") or []
    per="post" if any(INSTR in json.dumps(m) for m in msgs[:3]) else "pre"
    seq=[]; res={}
    for m in msgs:
        c=m.get("content")
        if not isinstance(c,list): continue
        for x in c:
            if x.get("type")=="tool_use":
                nm=x["name"]; inp=x.get("input") or {}
                if nm in("Edit","Write"): seq.append(("edit:native",{base(inp.get("file_path"))},x["id"]))
                elif nm=="Read": seq.append(("read",{base(inp.get("file_path"))},x["id"]))
                elif nm=="Bash":
                    cmd=inp.get("command","")
                    if re.search(r"\.clodex/(projects|messages)|JOURNAL\.md",cmd): seq.append(("journal",set(),x["id"])); continue
                    if isbashedit(cmd): seq.append(("edit:bash",{base(p) for _,p in PATHRE.findall(cmd)},x["id"]))
                    elif re.search(r"\b(node|npm|npx|pytest|python3? |make|jest|vitest)\b",cmd) and "<<" not in cmd: seq.append(("run",set(),x["id"]))
                    else: seq.append(("read",{base(p) for _,p in READRE.findall(cmd)},x["id"]))
                else: seq.append(("other",set(),x["id"]))
            elif x.get("type")=="tool_result":
                res[x.get("tool_use_id")]=json.dumps(x.get("content"))
    for i,(k,files,uid) in enumerate(seq):
        if not k.startswith("edit:") or not files: continue
        nxt=seq[i+1:i+4]
        retry=any(k2.startswith("edit:") and files&f2 for k2,f2,_ in nxt)
        verify=any(k2=="read" and files&f2 for k2,f2,_ in nxt)
        run=any(k2=="run" for k2,f2,_ in nxt)
        fail=bool(FAIL.search(res.get(uid,"")))
        S[(per,k)]["retry"].append(retry); S[(per,k)]["verify"].append(verify); S[(per,k)]["run"].append(run); S[(per,k)]["fail"].append(fail)
        S[(per,k)]["restok"].append(len(res.get(uid,""))/CH)
for key in sorted(S):
    d=S[key]; n=len(d["retry"])
    print(f"{key[0]:<4} {key[1]:<12} n={n:5d}  retry(same file ≤3 calls) {100*st.mean(d['retry']):4.1f}%  verify-read {100*st.mean(d['verify']):4.1f}%  run/test next {100*st.mean(d['run']):4.1f}%  fail-marker {100*st.mean(d['fail']):4.1f}%  result tok p50 {st.median(d['restok']):4.0f}")
