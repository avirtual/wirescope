"""The read-before-edit gate as a COST, not just a constraint.

Native Edit needs readFileState, so the first edit to a file must be preceded by
a Read of that file. That Read is part of the native edit path's price and my
659 tok/op figure EXCLUDED it. A heredoc bypasses the gate entirely.

Measured: for each native Edit/Write, was there a preceding Read of the same path
in this window, and what did that Read cost? Attribute a Read to the edit path
only if the Read is not otherwise explained (i.e. it is the FIRST touch of that
path and an edit follows).
"""
import json,glob,os,re,statistics
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

n_edit=0; n_gated=0; gate_read_tok=0.0
edit_in=0.0; edit_res=0.0
files_edited_native=set(); files_edited_bash=set()
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    seq=[]; results={}
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            t=blk.get("type")
            if t=="tool_use":
                nm=blk.get("name"); inp=blk.get("input") or {}
                n=len(json.dumps(inp))
                if nm=="Read" and inp.get("file_path"):
                    seq.append(("Read",os.path.normpath(inp["file_path"]),blk["id"],n))
                elif nm in ("Edit","Write") and inp.get("file_path"):
                    seq.append((nm,os.path.normpath(inp["file_path"]),blk["id"],n))
            elif t=="tool_result":
                tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
                results[blk.get("tool_use_id")]=len(s)
    # for each path, find first native edit and whether a Read preceded it
    first_edit={}; reads_before=defaultdict(list)
    for i,(k2,p,bid,n) in enumerate(seq):
        if k2 in ("Edit","Write") and p not in first_edit: first_edit[p]=i
    for i,(k2,p,bid,n) in enumerate(seq):
        if k2=="Read" and p in first_edit and i<first_edit[p]:
            reads_before[p].append((bid,n))
    for p,fi in first_edit.items():
        n_edit+=1
        if reads_before.get(p):
            n_gated+=1
            bid,n=reads_before[p][-1]     # the read that satisfied the gate
            gate_read_tok += (n + results.get(bid,0))/CH
    for k2,p,bid,n in seq:
        if k2 in ("Edit","Write"):
            edit_in+=n/CH; edit_res+=results.get(bid,0)/CH
            files_edited_native.add((tk,p))
print("== the read-before-edit gate, priced ==")
print(f"  distinct files first-edited natively : {n_edit:,}")
print(f"  preceded by a Read of that file      : {n_gated:,} ({n_gated/max(n_edit,1)*100:.0f}%)")
print(f"  cost of those gate Reads             : {gate_read_tok:,.0f} tok")
print(f"  = {gate_read_tok/max(n_gated,1):,.0f} tok per file unlocked for native editing")
print(f"\n  native Edit/Write payload alone      : {edit_in+edit_res:,.0f} tok")
print(f"  native path TOTAL (edits + gate reads): {edit_in+edit_res+gate_read_tok:,.0f} tok")
print(f"\n== true per-file cost of each route ==")
print(f"  native : gate Read {gate_read_tok/max(n_gated,1):,.0f} + edits")
print(f"  heredoc: 0 gate    + edits")
