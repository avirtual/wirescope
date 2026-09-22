"""Why does Read re-fetch 2.19x per file while bash re-fetches 1.31x?

Three candidate explanations, separated here:
 (a) RANGED reads -- Read with offset/limit fetching different slices of one
     file is not redundancy at all, it is paging.
 (b) THE READ-BEFORE-EDIT GATE -- native Edit requires readFileState, so an Edit
     forces a preceding Read of that path. A heredoc/sed -i edit bypasses the
     gate entirely and needs no read. If so, Read's extra fetches are a COST OF
     THE NATIVE EDIT PATH, not of reading.
 (c) genuine re-reading of the same bytes.

Also: WITHIN-SEAT comparison. Fleet totals mix seat populations; a seat that
uses both routes is its own control.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
BASH_READ=re.compile(r"(?:^|&&|\||;)\s*(?:cat|head|tail|sed\s+-n\s+[^ ]+)\s+(?:-[A-Za-z0-9]+\s+)*(['\"]?)([~/][^\s'\"|;&>]+)\1")
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)

ranged=0; fullr=0
repeat_full=0; repeat_ranged=0
gate_follows=0; repeat_reads_total=0
within=[]
edit_paths_native=defaultdict(int)
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    seq=[]   # ordered (kind, path, extra)
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); inp=blk.get("input") or {}
            if nm=="Read":
                p=inp.get("file_path")
                if p:
                    rg = ("offset" in inp) or ("limit" in inp)
                    seq.append(("Read",os.path.normpath(p),rg))
                    if rg: ranged+=1
                    else: fullr+=1
            elif nm in ("Edit","Write"):
                p=inp.get("file_path")
                if p: seq.append((nm,os.path.normpath(p),False))
            elif nm=="Bash":
                cmd=inp.get("command","")
                ps=BASH_READ.findall(cmd)
                if len(ps)==1: seq.append(("bash",os.path.normpath(ps[0][1]),False))
                if "<<" in cmd or "sed -i" in cmd: seq.append(("bashedit",None,False))
    if len(seq)<5: continue
    # (a)/(c): repeat classification
    seen=set()
    for i,(k2,p,rg) in enumerate(seq):
        if k2!="Read": continue
        if p in seen:
            repeat_reads_total+=1
            if rg: repeat_ranged+=1
            else: repeat_full+=1
            # (b): is the NEXT native op on this path an Edit/Write?
            for k3,p3,_ in seq[i+1:i+4]:
                if p3==p and k3 in ("Edit","Write"): gate_follows+=1; break
        seen.add(p)
    # within-seat: seats using both routes
    nR=sum(1 for k2,_,_ in seq if k2=="Read"); nB=sum(1 for k2,_,_ in seq if k2=="bash")
    if nR>=5 and nB>=5:
        dR=len({p for k2,p,_ in seq if k2=="Read"}); dB=len({p for k2,p,_ in seq if k2=="bash"})
        within.append((tk,nR,dR,nR/dR,nB,dB,nB/dB))

print("== (a) are Read repeats PAGING or re-reads of the same bytes? ==")
print(f"  Read calls: full-file {fullr:,}   ranged(offset/limit) {ranged:,}")
print(f"  repeat Reads: {repeat_reads_total:,}  ->  ranged {repeat_ranged:,} ({repeat_ranged/max(repeat_reads_total,1)*100:.0f}%)"
      f"   full {repeat_full:,} ({repeat_full/max(repeat_reads_total,1)*100:.0f}%)")
print("\n== (b) is the repeat Read feeding the read-before-edit gate? ==")
print(f"  repeat Reads followed within 3 ops by Edit/Write on the SAME path: "
      f"{gate_follows:,} / {repeat_reads_total:,} = {gate_follows/max(repeat_reads_total,1)*100:.0f}%")
print("\n== (c) WITHIN-SEAT control: seats using >=5 of BOTH routes ==")
if within:
    print(f"{'ticket':>7} {'Rfetch':>7} {'Rdist':>6} {'R/file':>7} {'Bfetch':>7} {'Bdist':>6} {'B/file':>7}")
    for tk,nR,dR,rr,nB,dB,br in sorted(within,key=lambda x:-x[3])[:12]:
        print(f"t{tk:>6} {nR:>7} {dR:>6} {rr:>7.2f} {nB:>7} {dB:>6} {br:>7.2f}")
    print(f"\n  n={len(within)} seats   median Read/file {statistics.median([x[3] for x in within]):.2f}"
          f"   median bash/file {statistics.median([x[6] for x in within]):.2f}")
else:
    print("  no seat used >=5 of both routes -- the fleet split is near-total,")
    print("  so the 2.19 vs 1.31 comparison is BETWEEN seat populations, not within.")
