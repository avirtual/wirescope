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
#   t1067              -> a stub-only assistant message BEHIND A SYSTEM ROW is no
#                         longer dropped (`system -> user` is a 400) but kept as a
#                         head-line PLACEHOLDER, `[agent:verb args] <title>` or a
#                         bare `[agent]` (PLACEHOLDER). New rendering, reaches the
#                         model, so it gets a row here BEFORE the arm is measured.
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

# Fifth shape (t1065, merged 8c7e12db): same position rule, new vocabulary. With
# `@spill:` gone a typed stand-in reads `[agent:dm x] … — 858 B filed at
# /…/<hex>.md`, which STANDIN_POS cannot see. Anchor on `filed at <path>`.
#
# THE SIZE TOKEN IS PART OF THE GRAMMAR, NOT DECORATION. Merged shape is
# `<head> [<title> — ]<size> filed at <abs path>`, and clodex is explicit that a
# bare `filed at` is PROSE. My first cut made the size clause optional, which
# would have scored every sentence like "the body was filed at /…/x.md" as an
# emission — over-counting the arm with the fleet's own discussion, the same
# trap #4 family that already fired twice today. Require the size token.
SIZE = r"[\d.,]+ *(?:B|KB|MB)(?: +of +prose)?"
FILED = re.compile(r"(" + SIZE + r") +filed at\s+(/[^\s)]+?/([0-9a-f]{16})\.md)")
STANDIN_FILED = re.compile(
    r"^\[agent:[^\]\n]*\][^\n]{0,160}?" + SIZE +
    r" +filed at\s+/[^\s)]+?/([0-9a-f]{16})\.md\s*$",
    re.M)

# Sixth shape (t1067, merged f973c53a): the tee now arms on system-adjacent
# requests too, and a stub-only assistant message sitting behind a `role:system`
# row is kept as a HEAD-LINE PLACEHOLDER rather than dropped (dropping it would
# leave `system -> user`, a 400). Shape, from wire/spill-cut.js:
#   heads present -> the intent head lines, one per line, `[agent:verb args]`
#                    optionally ` <title>` (placeholderOf/headOf)
#   no head       -> the bare literal `[agent]` (PLACEHOLDER)
# It reaches the model in history, so it is imitable, and imitation is exactly
# the failure this scan counts.
#
# TWO DISCRIMINATORS, DIFFERENT STRENGTHS — keep them separate rather than
# averaging them into one number:
#   bare  : a line that is exactly `[agent]` is not a valid intent in any
#           grammar, so nothing but the placeholder can legitimately produce it.
#           Unfoolable.
#   head  : a body-taking intent head line that is the LAST non-empty line of the
#           response — i.e. a greedy body that was never written. Ambiguous by
#           construction: it is indistinguishable from the model emitting a plain
#           bodyless intent (which merely bounces). Counted, but tagged apart so
#           a reader can drop it.
#
# THE VERB SET IS THE WHOLE PRECISION OF THE HEAD RULE — get it from the grammar,
# not from "takes a body". First cut listed every body-taking verb and scored
# 21 hits over 30h, 0 of them real: 20 were `[agent:remind …]`, whose body is
# written INLINE on the head line by design (so a head line with no following
# body is the normal, correct shape), and 1 was a kv-only `[agent:team role-set …]`,
# which the grammar says takes no body at all. Both are legitimate traffic, and
# the count would have opened the arm at 21 phantom emissions. Excluded here:
#   remind              — body is inline on the head line
#   team role-add/-set  — kv head line takes no body
#   term, exec          — argument ends at its own line
#   who/name/list/…     — no body in the grammar
#   context compact/clear/reload — the handoff text is OPTIONAL and may be
#                         written on the head line itself ("on the same or
#                         following lines"), so a bare or inline head is the
#                         normal shape. Included at first; the one head-only
#                         hit of the +18.7h reading (2026-09-22 15:15) was a
#                         `[agent:context compact] <inline handoff>` line —
#                         and it only became the LAST line because the
#                         receipt's `meta.text` is capped (~4,000 ch) and the
#                         cut fell inside that line. Two false-positive
#                         mechanisms, one hit; both closed here (verb dropped,
#                         head rule reads the SSE when the receipt is at cap).
PLACEHOLDER_BARE = re.compile(r"^\[agent\]\s*$", re.M)
# t1087 (5c2b80c6, 2026-09-22 ~19:06): the no-head placeholder is no longer the
# bare `[agent]` but ONE prose line. Keyed on the EXACT bytes clodex dm'd (sha256
# d7d077a9…f963), never a paraphrase: a paraphrase would match the fleet
# discussing the rendering, the exact line only matches an imitation of it. BARE
# stays for histories cut before the swap; the swap time is a placeholder-CLASS
# boundary only (receipt-shape arm unaffected).
PLACEHOLDER_PROSE_BYTES = ("(This turn's text was delivered in full; "
                           "Clodex keeps it out of the request.)")
PLACEHOLDER_PROSE = re.compile(
    r"^" + re.escape(PLACEHOLDER_PROSE_BYTES) + r"\s*$", re.M)
BODY_VERBS = (r"dm|shout|memory remember|"
              r"task (?:add|done|reject|respec|cancel|accept)|"
              r"team (?:template-save|prompt-save)")
PLACEHOLDER_HEAD = re.compile(
    r"^\[agent:(?:" + BODY_VERBS + r")\b[^\]\n]*\][^\n]{0,200}\s*\Z", re.M)


def placeholder_hits(txt):
    """(bare, prose, head) counts of placeholder renderings emitted by the model.

    bare  = the t1067 literal `[agent]`; prose = the t1087 one-liner. Both are
    the no-head class, split so a reader can see which rendering was imitated.

    Typography guard, same as the pointer and filed shapes: a placeholder shown
    inside a code span, a fence or an indented block is being QUOTED — and this
    shape will be quoted a lot, since it is the thing the fleet is specifying.
    """
    def quoted(m):
        before = txt[:m.start()]
        line = txt[txt.rfind("\n", 0, m.start()) + 1: m.start()]
        return (before.count("`") % 2 == 1 or before.count("```") % 2 == 1
                or line.startswith("    ") or line.startswith("\t"))
    bare = sum(1 for m in PLACEHOLDER_BARE.finditer(txt) if not quoted(m))
    # The prose line is what the fleet SPECIFIES, so it gets pasted bare inside
    # dm bodies (first hit, 19:06:22, was t1087's own hand dm'ing me the bytes —
    # trap #4). An imitation is an EMPTY reply: the line as the whole response,
    # or on top of it with nothing but intents after. A response that also
    # talks about the rendering is discussion.
    discussing = re.search(r"placeholder|spill-cut", txt, re.I) is not None
    prose = sum(1 for m in PLACEHOLDER_PROSE.finditer(txt)
                if not quoted(m) and (txt.strip() == PLACEHOLDER_PROSE_BYTES
                                      or not discussing))
    head = sum(1 for m in PLACEHOLDER_HEAD.finditer(txt.rstrip())
               if not quoted(m))
    return bare, prose, head


def sse_text(path):
    """The response text reassembled from a captured .response.sse (uncapped)."""
    out = []
    try:
        raw = open(path, "rb").read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            d = json.loads(line[5:].strip())
        except Exception:
            continue
        if d.get("type") == "content_block_delta" and (d.get("delta") or {}).get("type") == "text_delta":
            out.append(d["delta"]["text"])
    return "".join(out)


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
    # Trap #4, second-order (2026-09-21 19:1x). The dangling rule assumed a
    # dangling id could only have been invented. Once the fleet started FIXING
    # this bug, the fabricated ids themselves became quotable: they now appear
    # verbatim in tickets, test pins and postmortems, where they are dangling BY
    # CONSTRUCTION (that is what makes them the example). All 3 "events" in the
    # first 6h run were this — two seats writing t1065's spec, quoting the two
    # memory-unit stubs and a literal `0123456789abcdef` test fixture.
    # Discriminator is TYPOGRAPHY, not resolvability: an id inside a code span,
    # a fence or an indented block is being SHOWN, never emitted. Cheap to check
    # and it cannot be fooled by the id resolving or not.
    quoted_span = set()
    for m in POINTER.finditer(txt):
        line = txt[txt.rfind("\n", 0, m.start()) + 1: m.start()]
        before, after = txt[:m.start()], txt[m.end():]
        in_span = before.count("`") % 2 == 1           # open backtick span
        in_fence = before.count("```") % 2 == 1        # inside a fence
        indented = line.startswith("    ") or line.startswith("\t")
        if in_span or in_fence or indented:
            quoted_span.add(m.group(1))
    ptrs = [h for h in POINTER.findall(txt)
            if (h.lower() not in real or h in positional)
            and not (h in quoted_span and h not in positional)]
    quoted = [h for h in POINTER.findall(txt)
              if (h.lower() in real or h in quoted_span) and h not in positional]
    receipts = [(m.group(1), m.group(2)) for m in RECEIPT.finditer(txt)
                if m.group(2) not in real]
    acks = [m.group(1) for m in ACK.finditer(txt) if m.group(1) not in real]
    quoted += [m.group(1) for m in ACK.finditer(txt) if m.group(1) in real]
    # t1065 shape. ACK already covers the Clodex-voiced `[clodex] … filed at`,
    # so only count a FILED hit that ACK did not claim — else the rendering
    # change would double every ack.
    ack_ids = {m.group(1) for m in ACK.finditer(txt)}
    positional_filed = set(STANDIN_FILED.findall(txt))
    # FILED groups: 1=size token, 2=path, 3=id. Same typography guard as the
    # pointer: an id shown inside a code span, fence or indented block is being
    # QUOTED (spec text, test pin), never emitted.
    filed_hits = [(m.group(3), m) for m in FILED.finditer(txt)]
    filed_quoted_span = set()
    for h, m in filed_hits:
        line = txt[txt.rfind("\n", 0, m.start()) + 1: m.start()]
        before = txt[:m.start()]
        if (before.count("`") % 2 == 1 or before.count("```") % 2 == 1
                or line.startswith("    ") or line.startswith("\t")):
            filed_quoted_span.add(h)
    filed = [h for h, _ in filed_hits
             if h not in ack_ids
             and (not spill_id_resolves(h) or h in positional_filed)
             and not (h in filed_quoted_span and h not in positional_filed)]
    quoted += [h for h, _ in filed_hits
               if h not in ack_ids and h not in positional_filed
               and (spill_id_resolves(h) or h in filed_quoted_span)]
    filler = txt.strip() == FILLER
    ph_bare, ph_prose, ph_head = placeholder_hits(txt)
    if ph_head and len(txt) >= 3900:
        # The head rule keys on the LAST line; a receipt text truncated at its
        # cap has an artificial last line. Re-read the full text off the SSE.
        sse = p[:-len(".response.json")] + ".response.sse"
        if os.path.exists(sse):
            full = sse_text(sse)
            if full:
                ph_head = placeholder_hits(full)[2]
    if not (ptrs or receipts or acks or filed or filler or quoted
            or ph_bare or ph_prose or ph_head
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
           "receipts": receipts, "filler": filler, "filed": filed,
           "ph_bare": ph_bare, "ph_prose": ph_prose, "ph_head": ph_head,
           "text": txt}
    rec["shapes"] = ([f"pointer:{h}" for h in ptrs]
                     + [f"receipt:{i}" for _, i in receipts]
                     + [f"ack:{i}" for i in acks]
                     + [f"filed:{i}" for i in filed]
                     + (["filler"] if filler else [])
                     + ([f"placeholder-bare:{ph_bare}"] if ph_bare else [])
                     + ([f"placeholder-prose:{ph_prose}"] if ph_prose else [])
                     + ([f"placeholder-head:{ph_head}"] if ph_head else []))
    (events if rec["shapes"] else mention_only).append(rec)

events.sort(key=lambda r: r["ts"])
ptr_total = sum(len(e["ptrs"]) for e in events)
n_quoted = sum(len(e["quoted"]) for e in mention_only) + sum(len(e["quoted"]) for e in events)
n_recv = sum(len(e["receipts"]) for e in events)
n_ack = sum(len(e["acks"]) for e in events)
n_fill = sum(1 for e in events if e["filler"])
n_filed = sum(len(e["filed"]) for e in events)
n_ph_bare = sum(e["ph_bare"] for e in events)
n_ph_prose = sum(e["ph_prose"] for e in events)
n_ph_head = sum(e["ph_head"] for e in events)

arm = "  [--arm: TREATED seats only]" if ARM else ("  [--control: UNTREATED only]" if CTRL else "")
print(f"WINDOW: last {WIN:g}h over {len(CAPTURE_ROOTS)} capture root(s){arm}")
print(f"200-responses scanned: {n200} | responses mentioning a spill shape: {len(seen)}")
print(f"STAND-IN EVENTS (a receipt-shape emitted in place of a body): {len(events)}")
print(f"  by shape — pointer:{ptr_total}  receipt:{n_recv}  ack:{n_ack}  filler:{n_fill}"
      f"  filed:{n_filed}"
      f"   (id-carrying shapes: dangling, or standing where a body belongs)")
print(f"  placeholder — bare:{n_ph_bare} (t1067 literal `[agent]`, unfoolable)"
      f"  prose:{n_ph_prose} (t1087 one-liner, exact bytes)"
      f"  head-only:{n_ph_head} (a body-taking head line with no body; AMBIGUOUS"
      f" — same class, but indistinguishable from a plain bodyless intent)")
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

# The near-misses are the part of this scan that CANNOT be automated: every
# reading so far has been 0 events and N mention-only, and the only way that
# zero stays honest is hand-reading what the discriminators threw out. `--near`
# dumps each one with the matching line in context so a reading takes a minute
# instead of a grep session over the capture dirs.
if "--near" in sys.argv:
    print()
    print(f"MENTION-ONLY RESPONSES ({len(mention_only)}) — hand-read these; a "
          f"mislabelled one is a missed event:")
    for e in sorted(mention_only, key=lambda r: r["ts"]):
        t = e["text"]
        hits = []
        for pat in ("@spill", "filed at", FILLER[:24]):
            i = t.find(pat)
            while i != -1 and len(hits) < 3:
                line = t[t.rfind("\n", 0, i) + 1:
                         (t.find("\n", i) if t.find("\n", i) != -1 else len(t))]
                hits.append(line.strip()[:160])
                i = t.find(pat, i + 1)
        print(f"\n  {e['ts']:%m-%d %H:%M:%S}  [{e['served'] or '?'}]  "
              f"{e['agent']}  {len(t)} ch  {os.path.basename(e['file'])}")
        for h in dict.fromkeys(hits):
            print(f"      | {h}")

if OUT:
    json.dump([{**e, "ts": e["ts"].isoformat()} for e in events],
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")
