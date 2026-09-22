"""CORRECTION. My 1.68x compared a heredoc CALL against a native Edit CALL.
A heredoc routinely performs several replaces in one call; a native Edit performs
exactly one. So the fair unit is the EDIT OPERATION, not the call.

Operations per heredoc = number of replace-ops, floored at 1 (a `cat > f <<EOF`
full write is one operation with zero .replace() calls).

Also checks: is MultiEdit in these seats' tool rosters? If the CLI offers a
batching native tool, the comparison changes again.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
STR=re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
REPL=re.compile(r"\.replace\s*\(|\bre\.sub\s*\(")
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
H=[0,0,0,0]   # calls, ops, in_ch, res_ch
E=[0,0,0]     # calls, in_ch, res_ch
rosters=defaultdict(int)
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    for t in (b.get("tools") or []): rosters[t.get("name")]+=1
    uses={}
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); inp=blk.get("input") or {}
            n=len(json.dumps(inp))
            if nm in ("Edit","Write"):
                uses[blk["id"]]=("native",1,n); E[0]+=1; E[1]+=n
            elif nm=="Bash":
                cmd=inp.get("command","")
                if not (("<<" in cmd and ("python3 -" in cmd or "python -" in cmd or "cat >" in cmd)) or "sed -i" in cmd): continue
                ops=max(len(REPL.findall(cmd)), 1)
                uses[blk["id"]]=("heredoc",ops,n)
                H[0]+=1; H[1]+=ops; H[2]+=n
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            u=uses.get(blk.get("tool_use_id"))
            if not u: continue
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            if u[0]=="heredoc": H[3]+=len(s)
            else: E[2]+=len(s)
print("== tool rosters: does a batching native tool exist on these seats? ==")
for nm,c in sorted(rosters.items(), key=lambda kv:-kv[1]):
    if nm in ("Edit","Write","Read","MultiEdit","NotebookEdit","Bash"):
        print(f"  {nm:<14} present on {c} seats")
print("\n== per CALL (what I reported before) ==")
print(f"  native Edit/Write : {(E[1]+E[2])/max(E[0],1)/CH:>7,.0f} tok/call   (n={E[0]:,})")
print(f"  heredoc           : {(H[2]+H[3])/max(H[0],1)/CH:>7,.0f} tok/call   (n={H[0]:,})")
print(f"  ratio {((H[2]+H[3])/max(H[0],1))/((E[1]+E[2])/max(E[0],1)):.2f}x")
print("\n== per EDIT OPERATION (the fair unit) ==")
print(f"  mean ops per heredoc call: {H[1]/max(H[0],1):.2f}")
ep=(E[1]+E[2])/max(E[0],1)/CH
hp=(H[2]+H[3])/max(H[1],1)/CH
print(f"  native Edit/Write : {ep:>7,.0f} tok/op")
print(f"  heredoc           : {hp:>7,.0f} tok/op")
print(f"  ratio {hp/ep:.2f}x  ->  {'heredoc COSTS MORE' if hp>ep else 'heredoc is CHEAPER'}")
print(f"\n  to perform {H[1]:,} edit operations:")
print(f"    via heredoc (actual)      : {(H[2]+H[3])/CH:>11,.0f} tok")
print(f"    via native Edit (modelled): {H[1]*ep:>11,.0f} tok")
d=H[1]*ep-(H[2]+H[3])/CH
print(f"    difference                : {d:>11,.0f} tok  ({'native would cost MORE' if d>0 else 'native would SAVE'})")
