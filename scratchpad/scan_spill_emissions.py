#!/usr/bin/env python3
"""Count MODEL-EMITTED receipt STAND-INS on the wire — a harness rendering typed
by the model in place of the body it was supposed to write.

WHY THIS WORKS (the whole method in one paragraph):
the proxy captures the raw API response, which is UPSTREAM of clodex's spill tee.
So a stand-in found in a `.response.json` `meta.text` was typed by the model,
by construction — the tee's own rewrite can only ever appear later, in the
`.request.json` history of a subsequent turn. No placement heuristic is needed.
Measured 2026-09-21 over 12h: 20 distinct hashes in responses, 0 of them real files;
78 in requests, 51 real. The two populations partition cleanly.

COUNT THE CLASS, NOT THE SHAPE. This started as a pointer counter and was wrong
for it: clodex shipped two rewrites in one morning, and the fabrication followed
each into its new shape (pointer -> receipt sentence -> bare filler line). A scan
pinned to yesterday's shape scores a MIGRATION as a cure — the 11:09 build read
as 0/13 on pointers while two seats were fabricating fillers. See the shape table
at RECEIPT/FILLER below; every new rendering the harness ships needs a row there
BEFORE its arm is measured.

ENUMERATE THE SINKS, NOT JUST THE CHANNEL. The wire is where a stand-in is CHEAP
(clodex's `Not executed` bounces it, the turn is retried). The expensive sink is a
DURABLE one, and this scan was blind to it until 2026-09-21 18:40: two of this
seat's memory units have an ellipsis-plus-pointer as their whole body, saved
22:21 the night before, both dangling and provably never filed (retention covers
the window; neighbours survive either side). There the fabrication destroys the
fact AT SAVE TIME and ships a half-sentence to every later session, framed with
the confidence a memory unit carries. `--sinks` scans that store; add a sink here
whenever the harness grows a new durable one.

Four traps this avoids (cf. CLAUDE.md, the 611-hits/0-firings lesson):
  1. apparatus matching itself  -> scan ONLY meta.text, never request bodies.
  2. re-shipped history inflating -> dedupe by meta.message_id, count per response.
  3. harness vs model stand-ins -> the response/request split above, plus a
     resolvability cross-check against the spill dirs on disk.
  4. the fleet DISCUSSING the bug -> an id that names a real file is the seat
     quoting its own receipt, and the bare filler inside prose is a quote; only a
     dangling id, or the filler as the WHOLE reply, is a fabrication.

PER-SEAT TREATMENT GATE. After a prompt fix ships, the old and new wordings coexist
on the wire until every pre-reload process exits — a running seat keeps its booted
prompt, and a host on new code proves nothing about a seat started before it. So
treatment is read per REQUEST out of body.system, never from the host's git state,
and every event is tagged [NEW]/[OLD]. Counting a reloaded seat's post-fix turns
together with a stale seat's would measure the rollout, not the fix.

Usage:  scan_spill_emissions.py [window_hours] [--json OUT] [--arm | --control]
        --arm      count only TREATED turns (new receipt wording in the prompt)
        --control  count only UNTREATED turns (pre-fix wording)
        neither    count both; the per-event tag says which
        --sinks    audit the DURABLE sinks instead of the wire (memory store);
                   no window — a unit saved once is delivered forever
"""
import json, os, re, glob, sys, time, collections, email.utils

CAPTURE_ROOTS = sorted(glob.glob(
    os.path.expanduser("~/Library/Application Support/clodex/*/logs")))
SPILL_GLOB = os.path.expanduser("~/.clodex/spill/*/*.md")

POINTER = re.compile(r"@spill:([0-9a-fA-F]{8,})")  # a concrete pointer
SEAT_RE = re.compile(r"(\d+)-(.+?)-(parent|subagent|ext|unknown)-")

# THE COUNTED THING IS "A STAND-IN INSTEAD OF A BODY", NOT ONE SHAPE OF IT.
# (2026-09-21, after clodex pointed out the pointer count missed the filler.)
# Each time the harness changed the rewrite, the fabrication followed it into the
# new shape, so a scan pinned to the old shape reads the migration as a cure:
#   t1047 and earlier  -> `@spill:<hex>`                    (POINTER)
#   t1047 receipt      -> `(I sent dm X — "…" in full, N B; …at <path>)`  (RECEIPT)
#   t1052 strip+filler -> the editorial filler line, alone  (FILLER)
#   t1055/t1056        -> back to `@spill:<id>` (POINTER), plus the Clodex-voiced
#                         `[clodex] … filed at <path>` ack, now typeable (ACK)
#   t1065              -> `@spill:` RETIRED from every agent-visible surface; the
#                         stub becomes `<head> — 5.2 KB filed at <path>` (FILED).
#                         The id survives only as the file basename, so the
#                         dangling discriminator is a path stat, not a token.
# Count all three under one class, and keep them separable so a MIGRATION is
# visible as a shape rotation rather than hidden inside one total.
RECEIPT = re.compile(r"\((?:I sent|Clodex: you sent)\b[^)]*?"
                     r"(?:in full, *[\d,]+ *B|delivered in full)[^)]*?"
                     r"(/[^\s)]+?/([0-9a-f]{16})\.md)", re.S)
FILLER = "[Runtime note: action text omitted from retained history.]"
# Fourth shape (t1055/t1056, 2026-09-21 15:17): the Clodex-voiced ACK, which rides
# the next prompt in the USER role — so a seat typing it is claiming, in its own
# voice, a filing only the harness can perform. Same dangling rule as the others.
ACK = re.compile(r"\[clodex\][^\n]*?\bfiled at\b[^\n]*?/([0-9a-f]{16})\.md")

# POSITION, for the one case the dangling rule cannot decide (t1055 onward).
# On the RESPONSE side every pointer is model-typed by construction — the tee is
# downstream — so dangling separates "invented an id" from "copied a real one",
# NOT harness from model. That was harmless while the tee wrote no pointers; since
# t1055 restored the pointer stand-in, a seat can copy a RESOLVABLE id out of its
# own transcript and the dangling rule would file that as prose. So a pointer
# standing where a body belongs — alone on an intent head line, or as the whole
# reply — is a stand-in whatever it resolves to.
STANDIN_POS = re.compile(r"^\[agent:[^\]\n]*\][^\n]{0,160}?@spill:([0-9a-fA-F]{8,})\s*$",
                         re.M)

# Fifth shape (t1065): same position rule, new vocabulary. With `@spill:` gone a
# typed stand-in reads `[agent:dm x] … — 858 B filed at /…/<hex>.md`, which
# STANDIN_POS cannot see. Anchor on the trailing `filed at <path>` instead, and
# keep the size clause optional — the untitled and prose-tail variants word it
# differently ("858 B filed at", "858 B of prose filed at") but all three end in
# the path. Both id-bearing groups stay separable so a migration still shows up
# as a shape rotation rather than a silent zero.
FILED = re.compile(r"\bfiled at\b\s+(/[^\s)]+?/([0-9a-f]{16})\.md)")
STANDIN_FILED = re.compile(
    r"^\[agent:[^\]\n]*\][^\n]{0,160}?\bfiled at\b\s+/[^\s)]+?/([0-9a-f]{16})\.md\s*$",
    re.M)


def spill_id_resolves(h):
    """True iff <h>.md exists under any seat's spill dir.

    A path stat, per seat dir — never `ls dir/*/h.md &&`: an unmatched zsh glob
    lists the cwd and exits 0, which reported RESOLVES for every hash once.
    """
    h = h.lower()
    return any(os.path.isfile(os.path.join(d, h + ".md"))
               for d in glob.glob(os.path.expanduser("~/.clodex/spill/*/")))

# THE PER-SEAT TREATMENT GATE (added 2026-09-21).
# A seat is only evidence about the fix if the fix was in the prompt it was served.
# Read that off the wire per request, never from the host's git state: the old and
# new prompts coexist for as long as any pre-reload process is still running.
# The NEW wording has already been revised once in place (t1047's "a receipt is
# something Clodex writes after delivery" -> t1053's "the confirmation is …"), so
# match the shortest substring COMMON to every post-fix revision. Pinning the full
# t1047 sentence would have silently re-labelled 119 treated requests as untreated.
OLD_LINE = "Never type `@spill:` yourself"
NEW_LINE = "is something Clodex writes after delivery"

args = [a for a in sys.argv[1:] if not a.startswith("--")]
WIN = float(args[0]) if args else 12.0
OUT = None
if "--json" in sys.argv:
    OUT = sys.argv[sys.argv.index("--json") + 1]
ARM = "--arm" in sys.argv          # count only NEW-line (treated) seats
CTRL = "--control" in sys.argv     # count only OLD-line (untreated) seats

cutoff = time.time() - WIN * 3600
real = set(os.path.basename(f)[:-3] for f in glob.glob(SPILL_GLOB))

# --- the DURABLE sink -------------------------------------------------------
MEMORY_ROOT = os.path.expanduser("~/.clodex/library/memory")
# NOTE both globs: the per-seat dirs under library/memory are SYMLINKS into
# ~/.clodex/sessions/<seat>/memory, so `glob` on `*/*.md` traverses them but
# `grep -r` does NOT (it needs -R). A recursive search that silently returns
# nothing looks exactly like a clean audit — this cost a wrong "no hits" once.
MEMORY_GLOB = os.path.join(MEMORY_ROOT, "*", "*.md")


def audit_sinks():
    """A stand-in in a memory unit is strictly worse than one on the wire: the
    wire bounces and retries, a unit is saved DESTROYED and then delivered to
    every later session. A unit whose body ends in a pointer is a fabrication
    whether or not the id resolves — the tee never spills a memory.remember, so
    a real pointer cannot legitimately appear in one."""
    bad = []
    for f in sorted(glob.glob(MEMORY_GLOB)):
        try:
            txt = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        body = txt.split("\n---\n", 1)[-1].strip()
        for m in POINTER.finditer(body):
            h = m.group(1).lower()
            # `[ -f ]` per candidate dir, never `ls dir/*/h.md &&`: an unmatched
            # zsh glob lists the cwd and exits 0, which reported RESOLVES for
            # every hash the first time this was checked by hand.
            resolves = any(os.path.isfile(os.path.join(d, h + ".md"))
                           for d in glob.glob(os.path.expanduser(
                               "~/.clodex/spill/*/")))
            bad.append((f, h, resolves, len(body.encode()), body[:90]))
    print(f"DURABLE SINK: memory store ({MEMORY_ROOT})")
    print(f"  units scanned: {len(glob.glob(MEMORY_GLOB))}")
    if not bad:
        print("  no pointer-bodied units — clean")
        return
    print(f"  {len(bad)} FABRICATED unit(s) — the fact was destroyed at save time:")
    for f, h, res, n, head in bad:
        seat = os.path.basename(os.path.dirname(f))
        print(f"    {seat}/{os.path.basename(f)[:-3]}  {n:4d} B  "
              f"@spill:{h} {'resolves(!)' if res else 'dangling'}")
        print(f"      {head}…")
    print("  ACTION: recover the fact from the handoff/changelog, re-save in full,"
          " and forget the stub — its content is already gone.")


def grammar_served(req_path):
    """Which spill grammar line was in the SYSTEM prompt of this request.

    'NEW' / 'OLD' / None. Reads body.system — the capture nests the payload
    under `body`, and scanning the top level silently returns nothing.
    """
    try:
        d = json.load(open(req_path))
    except Exception:
        return None
    sysb = (d.get("body") or {}).get("system") or []
    if isinstance(sysb, str):
        sysb = [{"text": sysb}]
    t = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in sysb)
    if NEW_LINE in t:
        return "NEW"
    if OLD_LINE in t:
        return "OLD"
    return None


if "--sinks" in sys.argv:
    audit_sinks()
    sys.exit(0)

files = []
for root in CAPTURE_ROOTS:
    for dirp in os.scandir(root):
        if not dirp.is_dir():
            continue
        try:
            if dirp.stat().st_mtime < cutoff - 3600:
                continue
        except OSError:
            continue
        for f in os.scandir(dirp.path):
            if not f.name.endswith(".response.json"):
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    files.append(f.path)
            except OSError:
                pass

seen, events, mention_only, n200 = set(), [], [], 0
exposure = collections.defaultdict(lambda: collections.Counter())  # seat -> {NEW,OLD}
for p in files:
    try:
        d = json.load(open(p))
    except Exception:
        continue
    if d.get("status_code") != 200:
        continue
    n200 += 1
    meta = d.get("meta") or {}
    txt = meta.get("text") or ""
    mid = meta.get("message_id")

    # Treatment is per REQUEST, so record exposure for every turn, not just hits —
    # denominators come from here. The paired request carries the prompt.
    req = p[:-len(".response.json")] + ".request.json"
    served = grammar_served(req) if os.path.exists(req) else None
    if served:
        exposure[d.get("agent")][served] += 1

    # Each shape needs its OWN discriminator against the fleet discussing it —
    # the mention-only trap applies to all three, not just the pointer.
    #   pointer : the 8+ hex group (a bare `@spill` is prose about the token)
    #   receipt : the id in its path resolves to NO file (a real receipt's does)
    #   filler  : it is the WHOLE reply (quoting it inside prose is discussion)
    # Dangling is the discriminator for BOTH id-carrying shapes, not just the
    # pointer: a model quoting its own real receipt id back in prose (both seats
    # did this while debugging the bug) names a file that EXISTS, and counting it
    # would have turned two explanations into two fabrications.
    positional = set(STANDIN_POS.findall(txt))
    if txt.strip().startswith("@spill:") and len(txt.strip().split()) == 1:
        positional |= set(POINTER.findall(txt))
    ptrs = [h for h in POINTER.findall(txt)
            if h.lower() not in real or h in positional]
    quoted = [h for h in POINTER.findall(txt)
              if h.lower() in real and h not in positional]
    receipts = [(m.group(1), m.group(2)) for m in RECEIPT.finditer(txt)
                if m.group(2) not in real]
    acks = [m.group(1) for m in ACK.finditer(txt) if m.group(1) not in real]
    quoted += [m.group(1) for m in ACK.finditer(txt) if m.group(1) in real]
    # t1065 shape. ACK already covers the Clodex-voiced `[clodex] … filed at`,
    # so only count a FILED hit that ACK did not claim — else the rendering
    # change would double every ack.
    ack_ids = {m.group(1) for m in ACK.finditer(txt)}
    positional_filed = set(STANDIN_FILED.findall(txt))
    filed = [h for (_, h) in
             ((m.group(1), m.group(2)) for m in FILED.finditer(txt))
             if h not in ack_ids
             and (not spill_id_resolves(h) or h in positional_filed)]
    quoted += [m.group(2) for m in FILED.finditer(txt)
               if m.group(2) not in ack_ids and spill_id_resolves(m.group(2))
               and m.group(2) not in positional_filed]
    filler = txt.strip() == FILLER
    if not (ptrs or receipts or acks or filed or filler or quoted
            or "@spill" in txt or FILLER in txt or "filed at" in txt):
        continue
    if mid in seen:
        continue
    seen.add(mid)
    if ARM and served != "NEW":
        continue
    if CTRL and served != "OLD":
        continue
    dt = email.utils.parsedate_to_datetime((d.get("response_headers") or {}).get("date"))
    rec = {"ts": dt.astimezone(), "agent": d.get("agent"), "model": d.get("model"),
           "session": d.get("session_id"), "mid": mid, "file": p,
           "served": served, "ptrs": ptrs, "quoted": quoted, "acks": acks,
           "receipts": receipts, "filler": filler, "filed": filed, "text": txt}
    rec["shapes"] = ([f"pointer:{h}" for h in ptrs]
                     + [f"receipt:{i}" for _, i in receipts]
                     + [f"ack:{i}" for i in acks]
                     + [f"filed:{i}" for i in filed]
                     + (["filler"] if filler else []))
    (events if rec["shapes"] else mention_only).append(rec)

events.sort(key=lambda r: r["ts"])
ptr_total = sum(len(e["ptrs"]) for e in events)
n_quoted = sum(len(e["quoted"]) for e in mention_only) + sum(len(e["quoted"]) for e in events)
n_recv = sum(len(e["receipts"]) for e in events)
n_ack = sum(len(e["acks"]) for e in events)
n_fill = sum(1 for e in events if e["filler"])
n_filed = sum(len(e["filed"]) for e in events)

arm = "  [--arm: TREATED seats only]" if ARM else ("  [--control: UNTREATED only]" if CTRL else "")
print(f"WINDOW: last {WIN:g}h over {len(CAPTURE_ROOTS)} capture root(s){arm}")
print(f"200-responses scanned: {n200} | responses mentioning a spill shape: {len(seen)}")
print(f"STAND-IN EVENTS (a receipt-shape emitted in place of a body): {len(events)}")
print(f"  by shape — pointer:{ptr_total}  receipt:{n_recv}  ack:{n_ack}  filler:{n_fill}"
      f"  filed:{n_filed}"
      f"   (id-carrying shapes: dangling, or standing where a body belongs)")
print(f"RESOLVABLE ids quoted in prose: {n_quoted}  <- the seat discussing its own real receipt, NOT a fabrication")
print(f"MENTION-ONLY responses (prose ABOUT a shape, no fabrication): {len(mention_only)}  <- NOT events")
print()
print("TREATMENT EXPOSURE (which grammar line each seat was SERVED, per request):")
print("  a seat is evidence about the fix only for its NEW-line turns; the two")
print("  prompts coexist until every pre-reload process has exited.")
for a in sorted(exposure, key=lambda a: -sum(exposure[a].values())):
    c = exposure[a]
    tag = "TREATED" if c["NEW"] and not c["OLD"] else ("untreated" if c["OLD"] and not c["NEW"] else "MIXED")
    print(f"  {a:40s} NEW={c['NEW']:4d} OLD={c['OLD']:4d}  {tag}")
print()
# THE DENOMINATOR. Zero emissions from a seat that emitted no long bodies is not
# evidence of a fix, it is an empty denominator — the 2026-09-21 arm nearly got
# read that way. A spill FILE is one long body that actually went out, so count
# files by mtime in the same window and report the rate, never the bare count.
#
# The denominator therefore tracks the consumer's SPILL VERB SET, and it should:
# a verb the tee stopped spilling produces no stand-in to fabricate, so it belongs
# out of both sides of the rate. t1061 dropped context.compact/clear/reload — so
# from 2026-09-21 a long `[agent:context compact]` body rides the wire in FULL,
# once, on the request that carries the compact call, and is gone with the summary.
# That is the tee working as specced; do NOT read it as an unspilled body, and do
# not hand-add it to the denominator to "correct" for it.
print("LONG BODIES ACTUALLY EMITTED in the window (spill files by mtime) — the DENOMINATOR:")
spills = collections.Counter()
for f in glob.glob(SPILL_GLOB):
    try:
        if os.path.getmtime(f) >= cutoff:
            spills[os.path.basename(os.path.dirname(f))] += 1
    except OSError:
        pass
if spills:
    for s, n in spills.most_common():
        print(f"  {s:40s} {n:4d}")
    print(f"  {'TOTAL':40s} {sum(spills.values()):4d}")
else:
    print("  none — ANY emission count over this window is unnormalised, report it as such")
print()
print("BY AGENT (events, then the shape split):")
for a in sorted({e["agent"] for e in events},
                key=lambda a: -sum(1 for e in events if e["agent"] == a)):
    sub = [e for e in events if e["agent"] == a]
    sv = "/".join(f"{k or '?'}:{v}" for k, v in
                  collections.Counter(e["served"] for e in sub).items())
    print(f"  {a:34s} model={sub[0]['model']:16s} events={len(sub):3d} "
          f"ptr={sum(len(e['ptrs']) for e in sub):3d} "
          f"receipt={sum(len(e['receipts']) for e in sub):3d} "
          f"ack={sum(len(e['acks']) for e in sub):3d} "
          f"filler={sum(1 for e in sub if e['filler']):3d} "
          f"served={sv:10s} {sub[0]['ts']:%m-%d %H:%M} -> {sub[-1]['ts']:%m-%d %H:%M}")
print()
# Shape-by-hour, not events-by-hour: a fix that only removes ONE shape shows up
# here as the count moving to the next column, which a single total would hide.
print("BY HOUR (local; pointer / receipt / ack / filler):")
hr = collections.defaultdict(lambda: [0, 0, 0, 0])
for e in events:
    k = e["ts"].strftime("%m-%d %H")
    hr[k][0] += len(e["ptrs"])
    hr[k][1] += len(e["receipts"])
    hr[k][2] += len(e["acks"])
    hr[k][3] += 1 if e["filler"] else 0
for k in sorted(hr):
    print(f"  {k}:00   ptr={hr[k][0]:2d}  receipt={hr[k][1]:2d}  "
          f"ack={hr[k][2]:2d}  filler={hr[k][3]:2d}")
print()
print("ALL STAND-IN EVENTS:")
for e in events:
    print(f"  {e['ts']:%m-%d %H:%M:%S}  [{e['served'] or '?'}]  {e['agent']:34s} "
          f"{','.join(e['shapes'])}")

if OUT:
    json.dump([{**e, "ts": e["ts"].isoformat()} for e in events],
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")
