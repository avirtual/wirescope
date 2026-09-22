import os,re,json,sys,hashlib
D="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
NAME=re.compile(r"^(\d+)-(.+?)-([0-9a-f]{8})-(\w+)-(.+)-(\d{6})\.request\.json$")

def msg0_text(body):
    msgs=body.get("messages") or []
    if not msgs: return ""
    c=msgs[0].get("content")
    if isinstance(c,str): return c
    out=[]
    for b in c or []:
        if isinstance(b,dict) and b.get("type")=="text": out.append(b.get("text",""))
    return "\n".join(out)

def usage(rp):
    if not os.path.exists(rp): return {}
    try: d=json.load(open(rp))
    except Exception: return {}
    u=d.get("usage") or {}
    if not u:
        def f(o):
            if isinstance(o,dict):
                if "cache_read_input_tokens" in o: return o
                for v in o.values():
                    r=f(v)
                    if r: return r
            elif isinstance(o,list):
                for v in o:
                    r=f(v)
                    if r: return r
        u=f(d) or {}
    return u

rows=[]
sess_dirs=[s for s in os.listdir(D) if os.path.isdir(os.path.join(D,s)) and s!="_no-session"]
sess_dirs.sort(key=lambda s: os.path.getmtime(os.path.join(D,s)), reverse=True)
LIMIT=int(sys.argv[1]) if len(sys.argv)>1 else 400
for s in sess_dirs[:LIMIT]:
    p=os.path.join(D,s)
    fs=[]
    for f in os.listdir(p):
        m=NAME.match(f)
        if m: fs.append((int(m.group(1)),m.group(3),m.group(5),m.group(6),f))
    fs.sort()
    for seq,agent,model,ts,f in fs:
        rows.append((s,seq,agent,model,ts,os.path.join(p,f)))
print("scanned sessions:",len(sess_dirs[:LIMIT]),"requests:",len(rows))
json.dump([(r[0],r[1],r[2],r[3],r[4],r[5]) for r in rows],open("scratchpad/_idx.json","w"))
