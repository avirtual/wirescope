import json,sys,re,os,statistics as st
from collections import Counter,defaultdict
E=[e for e in json.load(open(sys.argv[1])) if e['n_req']>=3]
def q(v):
    v=sorted(v); 
    if not v: return "n/a"
    return f"n={len(v)} p25 {v[len(v)//4]:.0f} med {v[len(v)//2]:.0f} p75 {v[3*len(v)//4]:.0f} max {v[-1]:.0f}"
G=[c for e in E for c in e['calls'] if c['name']=='Grep']
R=[c for e in E for c in e['calls'] if c['name']=='Read']
print("=== GREP shape (n=%d)"%len(G))
print(" output_mode:",Counter(c['inp'].get('output_mode','<default=files>') for c in G).most_common())
print(" has path:",Counter(bool(c['inp'].get('path')) for c in G).most_common(), " path is a FILE (has ext):",sum(1 for c in G if re.search(r'\.\w{1,4}$',c['inp'].get('path',''))))
print(" glob filter:",Counter(bool(c['inp'].get('glob') or c['inp'].get('type')) for c in G).most_common())
print(" -n:",Counter(c['inp'].get('-n') for c in G).most_common(3)," context A/B/C used:",sum(1 for c in G if any(k in c['inp'] for k in ('-A','-B','-C'))))
print(" multiline:",sum(1 for c in G if c['inp'].get('multiline')), " head_limit:",sum(1 for c in G if c['inp'].get('head_limit')))
pats=[c['inp'].get('pattern','') for c in G]
def pshape(p):
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',p): return 'bare identifier'
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*(\(|\s*=)?',p): return 'dotted/call ident'
    if '|' in p: return 'alternation'
    if any(ch in p for ch in '\\[]()*+?^$'): return 'regex'
    return 'literal phrase'
print(" pattern shape:",Counter(pshape(p) for p in pats).most_common())
print(" alternation branch count:",Counter(min(p.count('|')+1,8) for p in pats if '|' in p).most_common())
print(" pattern len:",q([len(p) for p in pats]))
zero=[c for c in G if (c['res_chars'] or 0)<60]
print(" greps with ~empty result (<60ch):",len(zero),"(%.0f%%)"%(100*len(zero)/len(G)))
print(" sample empty-result patterns:",[str(c['inp'].get('pattern'))[:50] for c in zero[:12]])
# repeated greps within a session
rep=0; repn=0
for e in E:
    cc=Counter((c['inp'].get('pattern'),c['inp'].get('path')) for c in e['calls'] if c['name']=='Grep')
    rep+=sum(v-1 for v in cc.values() if v>1)
print(" exact-repeat greps (same pattern+path, same session):",rep)
# greps whose pattern appeared in an earlier grep of the session as substring
print("=== GREP errors sample")
errs=Counter()
for e in E:
    for c in e['calls']:
        if c['name']=='Grep' and c['err']: errs[str(c['inp'].get('pattern'))[:40]]+=1
print(errs.most_common(8))
print("=== READ shape (n=%d)"%len(R))
print(" offset/limit used:",Counter((bool(c['inp'].get('offset')),bool(c['inp'].get('limit'))) for c in R).most_common())
print(" limit values:",q([c['inp']['limit'] for c in R if c['inp'].get('limit')]))
files=Counter(os.path.basename(c['inp'].get('file_path','?')) for c in R)
print(" top files:",files.most_common(15))
ext=Counter(os.path.splitext(c['inp'].get('file_path','?'))[1] for c in R); print(" by ext:",ext.most_common(8))
# per-session: distinct files read, reads per file, re-reads of same file
dist=[]; rereads=[]; chunked=[]
for e in E:
    fs=Counter(c['inp'].get('file_path') for c in e['calls'] if c['name']=='Read')
    dist.append(len(fs)); rereads.append(sum(v-1 for v in fs.values()))
    chunked.append(sum(1 for v in fs.values() if v>=3))
print(" distinct files/session:",q(dist)," re-reads of same file/session:",q(rereads)," files read >=3x/session:",q(chunked))
# does the session read the .diff first? SPEC.md?
first=Counter(); readdiff=0; readspec=0; diffchunks=[]
for e in E:
    rs=[c for c in e['calls'] if c['name']=='Read']
    if rs: first[os.path.basename(rs[0]['inp'].get('file_path','?'))]+=1
    d=[c for c in rs if c['inp'].get('file_path','').endswith('.diff')]
    if d: readdiff+=1; diffchunks.append(len(d))
    if any('SPEC' in c['inp'].get('file_path','') for c in rs): readspec+=1
print(" first Read is:",first.most_common(6))
print(" sessions reading the .diff:",readdiff,"| reads of .diff per session:",q(diffchunks),"| read SPEC.md:",readspec)
# Read result truncated? big reads
print(" read res chars:",q([c['res_chars'] or 0 for c in R]))
# thinking split from responses
