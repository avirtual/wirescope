"""If the tool choice is a wash, where IS the reclaimable weight in edit traffic?

Three candidates, priced separately:
 (A) VERIFICATION ECHO -- the heredoc printing the file/diff back to prove it
     worked. Native Edit's result is 1 tok; anything above that is the seat
     choosing to re-read its own output.
 (B) SUPERSESSION -- edit #1 to a file is dead once edit #7 lands, but both
     re-ship on every later request forever. This is CLAUDE.md open item (c)
     (Tier-2 supersession stubs), measured here rather than assumed.
 (C) WRAPPER framing (already measured: 40% of input, but batching amortizes it).

(B) is the interesting one because it GROWS with ticket length, which is exactly
the symptom -- long tickets being context hogs.
"""
import json,glob,os,re,statistics
from collections import defaultdict
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
REPL=re.compile(r"\.replace\s*\(|\bre\.sub\s*\(")
PATH=re.compile(r"p\s*=\s*['\"]([^'\"]+)['\"]|(?:cat|tee)\s*>\s*['\"]?([\w./-]+)|sed\s+-i[^ ]*\s+[^ ]+\s+([\w./-]+)")
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    bn=os.path.basename(f)
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", bn)
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
echo_tok=0; echo_n=0; echo_big=0
sup_tok=0; sup_n=0; live_tok=0; live_n=0
per_seat=[]
for (tk,kind),(mt,f) in seats.items():
    try: b=json.load(open(f))
    except Exception: continue
    b=b.get("body",b)
    r=f.replace(".request.json",".response.json")
    try:
        u=json.load(open(r)).get("usage") or {}
        win=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
    except Exception: win=0
    uses={}; seq=[]
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_use": continue
            nm=blk.get("name"); inp=blk.get("input") or {}
            n=len(json.dumps(inp))
            p=None
            if nm in ("Edit","Write"): p=inp.get("file_path")
            elif nm=="Bash":
                cmd=inp.get("command","")
                if not (("<<" in cmd and ("python3 -" in cmd or "python -" in cmd or "cat >" in cmd)) or "sed -i" in cmd): continue
                m2=PATH.search(cmd)
                p=next((g for g in (m2.groups() if m2 else []) if g), None)
            else: continue
            if p: p=os.path.normpath(p)
            uses[blk["id"]]=(p,n,nm)
            seq.append((blk["id"],p,n))
    # (A) echo
    for msg in b.get("messages") or []:
        c=msg.get("content")
        if not isinstance(c,list): continue
        for blk in c:
            if blk.get("type")!="tool_result": continue
            u2=uses.get(blk.get("tool_use_id"))
            if not u2 or u2[2] in ("Edit","Write"): continue
            tc=blk.get("content"); s=tc if isinstance(tc,str) else json.dumps(tc)
            echo_tok+=len(s)/CH; echo_n+=1
            if len(s)/CH>200: echo_big+=1
    # (B) supersession: an edit to path p is superseded if a LATER edit hits p
    lastidx={}
    for i,(bid,p,n) in enumerate(seq):
        if p: lastidx[p]=i
    s_t=0
    for i,(bid,p,n) in enumerate(seq):
        if p and lastidx.get(p,i)>i: sup_tok+=n/CH; sup_n+=1; s_t+=n/CH
        else: live_tok+=n/CH; live_n+=1
    if win>2000 and s_t: per_seat.append((tk,win,s_t,s_t/win*100))
print("== (A) verification echo on bash edits ==")
print(f"  {echo_n:,} edit results   {echo_tok:,.0f} tok   avg {echo_tok/max(echo_n,1):,.0f}")
print(f"  native Edit result = 1 tok  ->  echo overhead ~= {echo_tok-echo_n:,.0f} tok")
print(f"  results >200 tok: {echo_big:,} ({echo_big/max(echo_n,1)*100:.0f}%)")
print("\n== (B) superseded edits still riding in the window ==")
print(f"  superseded edit blocks : {sup_n:,}  = {sup_tok:,.0f} tok")
print(f"  live (last per file)   : {live_n:,}  = {live_tok:,.0f} tok")
print(f"  superseded share of all edit payload: {sup_tok/max(sup_tok+live_tok,1)*100:.1f}%")
per_seat.sort(key=lambda x:-x[2])
print("\n== seats where superseded edits weigh most ==")
print(f"{'ticket':>7} {'window':>9} {'supersededTok':>14} {'%win':>6}")
for tk,win,s,p in per_seat[:12]:
    print(f"t{tk:>6} {win:>9,} {s:>14,.0f} {p:>5.1f}%")
if per_seat:
    print(f"\n  median share of window that is superseded edits: {statistics.median([x[3] for x in per_seat]):.1f}%")
