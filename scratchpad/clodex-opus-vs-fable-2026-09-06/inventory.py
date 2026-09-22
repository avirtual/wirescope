import os, re, json, collections, sys
ROOT = "/Users/bogdan/Library/Application Support/clodex/wirescope/logs/"
LO, HI = "2026-08-20", "2026-09-06"
name_re = re.compile(r"^(\d+)-(.+)-(\d{6})\.request\.json$")
ts_re = re.compile(r'"ts":\s*"([^"]+)"'); agent_re = re.compile(r'"agent":\s*"([^"]*)"')
role_re = re.compile(r'"role":\s*"([^"]*)"'); model_re = re.compile(r'"model":\s*"([^"]*)"')
sid_re = re.compile(r'"session_id":\s*"([^"]*)"')
def short(m): return m.replace("claude-","") if m else m
rows = []  # dict per request
noresp = 0
for sess in os.listdir(ROOT):
    d = os.path.join(ROOT, sess)
    if not os.path.isdir(d) or len(sess) != 36: continue
    try: names = os.listdir(d)
    except Exception: continue
    for n in names:
        m = name_re.match(n)
        if not m: continue
        try:
            with open(os.path.join(d, n), "rb") as f: head = f.read(600).decode("utf-8","replace")
        except Exception: continue
        tm = ts_re.search(head); ts = tm.group(1) if tm else None
        if ts is None or not (LO <= ts[:10] <= HI): continue
        am = agent_re.search(head); agent = am.group(1) if am else "?"
        rp = os.path.join(d, n[:-len(".request.json")] + ".response.json")
        role = model = sid = None
        try:
            with open(rp, "rb") as f: rh = f.read(400).decode("utf-8","replace")
            rm = role_re.search(rh); role = rm.group(1) if rm else "(none)"
            mm = model_re.search(rh); model = short(mm.group(1)) if mm else "?"
            sm = sid_re.search(rh); sid = sm.group(1) if sm else None
        except Exception:
            noresp += 1; role = "(no-response-file)"; model = "?"
        prefix = re.sub(r"-[0-9a-f]{8}$", "", agent)
        rows.append(dict(sess=sess, seq=int(m.group(1)), ts=ts, agent=agent, prefix=prefix, role=role, model=model, sid=sid))
rows.sort(key=lambda r: (r["ts"], r["seq"]))
out = []; P = out.append
P(f"# Inventory: clodex wirescope captures {LO}..{HI}\n")
P(f"Total request captures in window: {len(rows)} (requests lacking a .response.json: {noresp}). role/model read from `.response.json` header; ts/agent from `.request.json` header; session = directory name.\n")
def family(p):
    p = re.sub(r"^clodex-clodex\.t\d+\.(review-r\d+|hand|[a-z-]+)$", lambda m: "clodex-clodex.t*."+re.sub(r"\d+$","N",m.group(1)), p)
    p = re.sub(r"^crypto-(daily|macro)-\d{4}-\d\d-\d\d$", r"crypto-\1-<date>", p)
    p = re.sub(r"^crypto-[a-z_0-9]+$", "crypto-<token>", p)
    p = re.sub(r"^clodex-clodex-hand-[0-9a-f]+$", "clodex-clodex-hand-<hex>", p)
    p = re.sub(r"^clodex-clodex-designer-\d+$", "clodex-clodex-designer-<n>", p)
    p = re.sub(r"^auto-\d+-w\d+$", "auto-<n>-w<n>", p)
    return p
P("## Q1a. Agent FAMILIES (per-task prefixes collapsed): request count, sessions, models, roles\n")
P("| family | distinct prefixes | requests | session dirs | models | roles |\n|---|---|---|---|---|---|")
byf = collections.defaultdict(list)
for r in rows: byf[family(r["prefix"])].append(r)
for p, rs in sorted(byf.items(), key=lambda kv: -len(kv[1])):
    models = collections.Counter(r["model"] for r in rs); roles = collections.Counter(r["role"] for r in rs)
    P(f"| `{p}` | {len(set(r['prefix'] for r in rs))} | {len(rs)} | {len(set(r['sess'] for r in rs))} | {', '.join(f'{m}:{c}' for m,c in models.most_common())} | {', '.join(f'{k}:{v}' for k,v in roles.most_common())} |")
P("\n## Q1b. Every distinct agent prefix (full list)\n")
P("| prefix | requests | session dirs | models | roles |\n|---|---|---|---|---|")
byp = collections.defaultdict(list)
for r in rows: byp[r["prefix"]].append(r)
for p, rs in sorted(byp.items(), key=lambda kv: -len(kv[1])):
    models = collections.Counter(r["model"] for r in rs); roles = collections.Counter(r["role"] for r in rs)
    P(f"| `{p}` | {len(rs)} | {len(set(r['sess'] for r in rs))} | {', '.join(f'{m}:{c}' for m,c in models.most_common())} | {', '.join(f'{k}:{v}' for k,v in roles.most_common())} |")
main = [r for r in rows if r["prefix"] == "clodex-clodex"]
P("\n## Q2. `clodex-clodex-*` sessions (all roles in the dir that carry the clodex-clodex agent name)\n")
P("| session | agent hash(es) | first ts | last ts | requests | parent reqs | opus-5 | fable-5-1 | other |\n|---|---|---|---|---|---|---|---|---|")
bys = collections.defaultdict(list)
for r in main: bys[r["sess"]].append(r)
for s, rs in sorted(bys.items(), key=lambda kv: kv[1][0]["ts"]):
    mc = collections.Counter(r["model"] for r in rs)
    other = ", ".join(f"{k}:{v}" for k,v in mc.items() if k not in ("opus-5","fable-5-1"))
    agents = sorted(set(r["agent"][-8:] for r in rs))
    P(f"| `{s}` | {', '.join(agents)} | {rs[0]['ts']} | {rs[-1]['ts']} | {len(rs)} | {sum(r['role']=='parent' for r in rs)} | {mc.get('opus-5',0)} | {mc.get('fable-5-1',0)} | {other} |")
P("\n### Model switches, main seat, role=parent only (consecutive parent requests, chronological across sessions)\n")
P("| first request on new model | session | from | to | previous parent request ts (session) |\n|---|---|---|---|---|")
prev = None
for r in [r for r in main if r["role"]=="parent"]:
    if prev and prev["model"] != r["model"]:
        P(f"| {r['ts']} | `{r['sess']}` | {prev['model']} | {r['model']} | {prev['ts']} (`{prev['sess'][:8]}`) |")
    prev = r
P("\n### Per-session parent-model timeline (first/last parent ts per model within each session)\n")
P("| session | model | first parent ts | last parent ts | parent reqs |\n|---|---|---|---|---|")
for s, rs in sorted(bys.items(), key=lambda kv: kv[1][0]["ts"]):
    pm = collections.defaultdict(list)
    for r in rs:
        if r["role"]=="parent": pm[r["model"]].append(r["ts"])
    for mdl, tss in sorted(pm.items(), key=lambda kv: kv[1][0]):
        P(f"| `{s[:8]}` | {mdl} | {tss[0]} | {tss[-1]} | {len(tss)} |")
P("\n## Q3. Main seat per calendar day (all roles / parent only)\n")
P("| day | requests | parent reqs | opus-5 (all/parent) | fable-5-1 (all/parent) | other (all) | sessions |\n|---|---|---|---|---|---|---|")
byd = collections.defaultdict(list)
for r in main: byd[r["ts"][:10]].append(r)
for d, rs in sorted(byd.items()):
    mc = collections.Counter(r["model"] for r in rs); pc = collections.Counter(r["model"] for r in rs if r["role"]=="parent")
    other = ", ".join(f"{k}:{v}" for k,v in mc.items() if k not in ("opus-5","fable-5-1"))
    P(f"| {d} | {len(rs)} | {sum(pc.values())} | {mc.get('opus-5',0)}/{pc.get('opus-5',0)} | {mc.get('fable-5-1',0)}/{pc.get('fable-5-1',0)} | {other} | {len(set(r['sess'] for r in rs))} |")
P("\n## Q4. Roles on the main seat\n")
rc = collections.Counter(r["role"] for r in main)
P(f"Role counts for agent prefix `clodex-clodex` (from `.response.json` `role`): {dict(rc)}\n")
for role in rc:
    mc = collections.Counter(r["model"] for r in main if r["role"]==role)
    P(f"- {role}: {dict(mc)}")
main_sess = set(r["sess"] for r in main)
shared = collections.Counter((r["prefix"], r["role"]) for r in rows if r["sess"] in main_sess and r["prefix"] != "clodex-clodex")
P(f"\nOther agent prefixes/roles whose captures live in a main-seat session dir: {dict(shared) or 'none'}\n")
mism = sum(1 for r in main if r["sid"] and r["sid"] != r["sess"])
P(f"Main-seat rows whose response `session_id` differs from the directory name: {mism}\n")
text = "\n".join(out)
open(sys.argv[1], "w").write(text + "\n\n## Script\n\n```python\n" + open(__file__).read() + "\n```\n")
print(f"rows={len(rows)} main={len(main)} lines={len(out)}")
