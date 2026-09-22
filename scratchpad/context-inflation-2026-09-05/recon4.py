"""Follow-ups on recon3, one pass over hand+review last requests:
 1. where the bash instruction rides (system section / msg0 reminder / trailing system msg), its size, copies per request
 2. system-prompt heading set + tool name set, pre vs post (what grew boot:system +1.6k / boot:tools +2k)
 3. sys:midconv breakdown by signature pre/post
 4. asst:text: per assistant message and last-message length pre/post
 5. reviewer seats: same read/edit call counts (is FINDINGS' 'native Read 10.2/seat' a REVIEWER number?)
 6. heredoc edit sample for target extraction
"""
import json,glob,os,re,random,statistics as st
from collections import defaultdict,Counter
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
CH=2.98
INSTR="through the Bash tool wherever it can accomplish the job"
seats={}
for f in glob.glob(os.path.join(LD,"*","*.request.json")):
    m=re.search(r"clodex-clodex\.t(\d+)\.(hand|review-r\d+)-", os.path.basename(f))
    if not m: continue
    k=(m.group(1),m.group(2)); t=os.path.getmtime(f)
    if k not in seats or t>seats[k][0]: seats[k]=(t,f)
data=[]
for (tk,role),(mt,f) in seats.items():
    try: b=json.load(open(f)); b=b.get("body",b)
    except Exception: continue
    msgs=b.get("messages") or []
    if not msgs: continue
    data.append((tk,role,b))
def isbashedit(c): c=c or ""; return ("<<" in c and ("python3 -" in c or "python -" in c or "cat >" in c or "tee " in c)) or "sed -i" in c
hands=[d for d in data if d[1]=="hand"]
def has(b): return INSTR in json.dumps(b)
pre=[d for d in hands if not has(d[2])]; post=[d for d in hands if has(d[2])]
print(f"hands pre={len(pre)} post={len(post)}  reviewers={sum(1 for d in data if d[1]!='hand')}")

print("\n=== 1. where the instruction rides ===")
loc=Counter(); sizes=[]; copies=[]
for tk,role,b in post:
    n=0
    if INSTR in json.dumps(b.get("system")): loc["system"]+=1; n+=1
    msgs=b["messages"]
    if INSTR in json.dumps(msgs[0]): loc["msg0"]+=1
    for i,m in enumerate(msgs):
        s=json.dumps(m)
        if INSTR in s:
            n+=s.count(INSTR)
            loc["trailing-system" if m.get("role")=="system" else ("msg0" if i==0 else f"user-reminder")]+=0 if i==0 else 1
    copies.append(n)
print("  location counts (seats):",dict(loc)); print("  copies/request: mean %.2f max %d"%(st.mean(copies),max(copies)))
tk,role,b=post[0]
for i,m in enumerate(b["messages"]):
    s=json.dumps(m)
    if INSTR in s:
        txt=m["content"] if isinstance(m["content"],str) else " ".join(x.get("text","") for x in m["content"] if x.get("type")=="text")
        j=txt.find("While bypass"); j=j if j>=0 else max(0,txt.find(INSTR)-300)
        print(f"  sample (msg idx {i}, role {m.get('role')}, {len(txt)/CH:.0f} tok whole msg):\n    "+txt[j:j+700].replace("\n","\n    ")); break

print("\n=== 2. system headings + tools, pre vs post ===")
def heads(b): return set(re.findall(r"^#+ .*$", "\n".join(x.get("text","") for x in (b.get("system") or []) if isinstance(x,dict)), re.M))
def tools(b): return set(t.get("name") for t in (b.get("tools") or []))
hp=Counter(); hq=Counter(); tp=Counter(); tq=Counter()
for _,_,b in pre: hp.update(heads(b)); tp.update(tools(b))
for _,_,b in post: hq.update(heads(b)); tq.update(tools(b))
def share(c,n): return {k:round(v/n,2) for k,v in c.items()}
sp,sq=share(hp,len(pre)),share(hq,len(post))
print("  headings changed share (pre -> post), |delta|>0.3:")
for k in sorted(set(sp)|set(sq)):
    if abs(sp.get(k,0)-sq.get(k,0))>0.3: print(f"    {sp.get(k,0):4.2f} -> {sq.get(k,0):4.2f}  {k[:90]}")
tp2,tq2=share(tp,len(pre)),share(tq,len(post))
print("  tools changed share:")
for k in sorted(set(tp2)|set(tq2)):
    if abs(tp2.get(k,0)-tq2.get(k,0))>0.3: print(f"    {tp2.get(k,0):4.2f} -> {tq2.get(k,0):4.2f}  {k}")
def tooltok(b): return {t.get("name"):len(json.dumps(t))/CH for t in (b.get("tools") or [])}
ap=defaultdict(list); aq=defaultdict(list)
for _,_,b in pre:
    for k,v in tooltok(b).items(): ap[k].append(v)
for _,_,b in post:
    for k,v in tooltok(b).items(): aq[k].append(v)
print("  per-tool schema tok pre -> post (delta>150):")
for k in sorted(set(ap)|set(aq)):
    a=st.mean(ap[k]) if ap[k] else 0; c=st.mean(aq[k]) if aq[k] else 0
    if abs(c-a)>150: print(f"    {a:6.0f} -> {c:6.0f}  {k}")
print("  tools count mean: pre %.1f post %.1f"%(st.mean(len(tools(b)) for _,_,b in pre),st.mean(len(tools(b)) for _,_,b in post)))

print("\n=== 3. sys:midconv + reminders by signature (mean tok/seat) ===")
def sig(t):
    t=re.sub(r"\s+"," ",t.strip())[:70]; return re.sub(r"\d+","N",t)
def midconv(b):
    out=defaultdict(float)
    for i,m in enumerate(b["messages"]):
        if i==0: continue
        if m.get("role")=="system":
            t=m["content"] if isinstance(m["content"],str) else " ".join(x.get("text","") for x in m["content"] if x.get("type")=="text")
            out["S:"+sig(t)]+=len(json.dumps(m))/CH
        elif m.get("role")=="user" and isinstance(m.get("content"),list):
            for x in m["content"]:
                if x.get("type")=="text" and "<system-reminder>" in (x.get("text") or ""):
                    out["R:"+sig(x["text"].replace("<system-reminder>",""))]+=len(json.dumps(x))/CH
    return out
mp=defaultdict(float); mq=defaultdict(float)
for _,_,b in pre:
    for k,v in midconv(b).items(): mp[k]+=v/len(pre)
for _,_,b in post:
    for k,v in midconv(b).items(): mq[k]+=v/len(post)
for k in sorted(set(mp)|set(mq), key=lambda k:-(mq.get(k,0)+mp.get(k,0)))[:18]:
    print(f"    {mp.get(k,0):7,.0f} -> {mq.get(k,0):7,.0f}  {k}")

print("\n=== 4. asst:text per message ===")
def asst(b):
    per=[]; last=0
    for m in b["messages"]:
        if m.get("role")!="assistant": continue
        c=m["content"]; t=c if isinstance(c,str) else "".join(x.get("text","") for x in c if x.get("type")=="text")
        if t.strip(): per.append(len(t)/CH); last=len(t)/CH
    return per,last
for lab,g in (("pre",pre),("post",post)):
    P=[];L=[];NM=[]
    for _,_,b in g:
        per,last=asst(b); P+=per; L.append(last); NM.append(len(per))
    print(f"  {lab}: text-msgs/seat {st.mean(NM):5.1f}  tok/text-msg {st.mean(P):5.0f} (p50 {st.median(P):4.0f})  last-msg tok {st.mean(L):6.0f}")

print("\n=== 5. reviewer seats: read/edit call counts, by instruction ===")
rv=[d for d in data if d[1]!="hand"]
for lab,g in (("rev no-instr",[d for d in rv if not has(d[2])]),("rev instr",[d for d in rv if has(d[2])])):
    if not g: print(" ",lab,"n=0"); continue
    R=[];RB=[];E=[]
    for _,_,b in g:
        r=rb=e=0
        for m in b["messages"]:
            if not isinstance(m.get("content"),list): continue
            for x in m["content"]:
                if x.get("type")!="tool_use": continue
                if x["name"]=="Read": r+=1
                elif x["name"] in("Edit","Write"): e+=1
                elif x["name"]=="Bash":
                    c=x["input"].get("command","")
                    if re.search(r"(?:^|&&|\||;)\s*(cat|head|tail|sed)\s",c) and not re.search(r"\b(grep|rg)\b",c): rb+=1
        R.append(r);RB.append(rb);E.append(e)
    print(f"  {lab:<13} n={len(g):3d}  Read/seat {st.mean(R):5.1f}  bash-read/seat {st.mean(RB):5.1f}  Edit+Write/seat {st.mean(E):4.1f}")

print("\n=== 6. heredoc edit sample (post) ===")
cmds=[]
for _,_,b in post:
    for m in b["messages"]:
        if not isinstance(m.get("content"),list): continue
        for x in m["content"]:
            if x.get("type")=="tool_use" and x["name"]=="Bash" and isbashedit(x["input"].get("command","")): cmds.append(x["input"]["command"])
random.seed(1)
for c in random.sample(cmds,12): print("  ---\n  "+c[:260].replace("\n","\n  "))
first=Counter(re.sub(r"\s+"," ",c.strip())[:28] for c in cmds)
print("  first-28-chars shapes:",first.most_common(12))
