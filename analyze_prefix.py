"""Cold-prefix anatomy: what a seat LOADS before it does any work.

`analyze_tools.py` prices one segment of the carried window (tool schemas, the
part measurable with zero judgment). This is the whole-prefix companion: for a
given session it takes the COLD turn off the wire and answers "the seat opened
at 43k tokens — 43k of WHAT?", broken down far enough to attach a trim lever to
each line.

Why a tool and not an ad-hoc dig: the answer is a segmentation of one captured
request body, and the segmentation is fiddly in ways that are easy to get subtly
wrong by hand (system prose splits on `# Heading`, the clodex wrapper splits on
ALL-CAPS labels, the SessionStart hook splits on neither). Doing it by hand
gives you a different breakdown every time, which is the thing you cannot
compare across seats or across weeks.

THE COLD TURN, NOT THE CURRENT ONE. The seat's first working request is the load:
everything present before the conversation exists. Later turns mix load with work
and cannot answer "is our boot too fat". We pick the fewest-messages request that
carries at least one CLIENT tool — see pick_cold_request for why that predicate
and not the role label or a tool count.

CHAR ESTIMATES ARE CALIBRATED, NOT TRUSTED. Densities (proxylab.tokest) put every
segment on one comparable scale, but they run low against a receipt (measured:
9-14% on two seats, same direction). So when the matching response carries usage
we compute `receipt / estimate` and report SCALED tokens. An uncalibrated run
says so in the header. The shares are the durable half; the absolute tokens move.

THE ROW ORDERING IS SOFTER THAN ITS DIGITS. Only the RECEIPT is measured; every
per-segment number is a char-density estimate, and calibrating them to sum to the
receipt makes the total right BY CONSTRUCTION while distributing each segment's
error across all the others. So a block that tokenizes worse than its assumed
density is silently subsidised by every other row, and the tool structurally
cannot surface that on its own.

Fitted against 420 cold boots in logs_main (solve `receipt ~ a*tools + b*rest`):
tool schemas come out at ~2.77 ch/tok against the 2.71 we assume — accurate to 2%
— while non-tool prose implies ~2.83 against our 3.11, i.e. prose tokenizes ~10%
DENSER than assumed. The bias therefore runs OPPOSITE to the intuition that
schema-shaped text is the underestimated kind: prose rows are understated and
tool rows carry the spread-out excess. Consequence for a reader: treat adjacent
rows within ~10% as tied, and never pick a trim target on row order alone.

WHAT THIS TOOL CANNOT TELL YOU. It anatomizes the BOOT LOAD, which is a constant.
Its share of a session's bill decays as the conversation grows (measured 83% -> 32%
across one session; 34% on a 4,311-request one), so on any long session the
accumulated window dominates and trimming a segment here moves the total very
little. Use it to answer "what did this seat load", never "where is the money" —
for the latter the questions are window size at each request, and cost per unit of
shipped work, neither of which live in a cold prefix.

PREFIX BYTES AND WINDOW BYTES ARE DIFFERENT GOODS; this prices only the first. A
prefix byte is written once and cache-read thereafter, so it amortizes and its
share DECAYS. A working-window byte — a file the seat reads at turn 10 — is carried
at full weight to compaction, re-read on every subsequent request, AND permanently
displaces budget that cannot be recovered. Every conclusion this tool supports
("constants don't matter on long sessions", "trimming a row moves little") applies
to the prefix ONLY, and inverts on the window. If the question is "what is crowding
this seat out", the instrument is the read stream, not the boot load.

IT CANNOT TELL YOU A SEGMENT IS DEAD. Utilization (`/_context?utilization=1`) shows
what was CALLED, and a zero there has at least three causes that look identical on
the wire: genuinely unused, used rarely by a granted skill, or unused BY POLICY
because the seat's instructions forbid the path. Worked example, 2026-09-04: two
clodex seats loaded SendMessage and called it zero times across 40 turns, which
read as pure deadweight. It is in fact the reply channel for subagents spawned via
the Agent tool — dropping it while keeping Agent yields spawn-with-no-reply. The
zero came from a role prompt that routes all delegation through a ticket protocol.
So a zero is a question, and answering it needs the seat's INSTRUCTIONS, which are
prose this tool only measures the size of.

LEAK SCAN. Two ways a prefix carries the same bytes twice, both invisible in a
category rollup: an identical block appearing at two paths (md5 over every string
>200 ch), and one prose body quoting another (identical sentences shared between
the two largest bodies). Both were NEGATIVE on the seats this was built against,
which is itself the finding worth being able to reproduce cheaply — a fat prefix
is usually fat honestly, and "no leak" is the answer you want to reach in one
command instead of an afternoon.

Usage:
  python3 analyze_prefix.py SESSION_ID [--log-dir DIR] [--top N] [--json]
  python3 analyze_prefix.py --file path/to/NNNN-*.request.json
  python3 analyze_prefix.py SESSION_A SESSION_B          # side-by-side compare
"""
import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

# Measured densities, ch/tok (proxylab/tokest.py; CLAUDE.md "Hard facts").
D_TOOL, D_SYS, D_MSG = 2.71, 3.11, 2.98

# Plausible receipt/estimate calibration. The densities are measured, so a real
# gap is tens of percent (observed 1.10-1.16); anything outside this band means
# the receipt is pricing something the body does not contain, not that our
# characters were mis-weighed.
SCALE_MIN, SCALE_MAX = 0.7, 1.6

# Where clodex's vendored wirescope writes, so the common case needs no --log-dir.
DEFAULT_LOG_DIRS = [
    Path.home() / "Library/Application Support/clodex/wirescope/logs",
    Path("logs_main"),
]

# Anchors inside the SessionStart hook message. THIS LIST IS ONE HARNESS'S DIALECT
# (clodex's), not a universal grammar: the block is flat text with no heading
# syntax, so leading substrings are the only handle. A harness that words its hook
# differently matches nothing and degrades to a single "SessionStart (unsegmented)"
# row — correct but coarse. Extend the list to teach it a new dialect; never assume
# a seat has no memory/roster because the anchors missed.
HOOK_ANCHORS = [
    ("Your persistent memory", "memory: full units"),
    ("Index (bodies on disk", "memory: index"),
    ("Your team", "team roster"),
    ("Dispatch: TWO steps", "dispatch note"),
    ("Called the Read tool", "briefing replay (synthetic tool call)"),
    ("Available agent types", "agent roster"),
    ("The following skills", "skills roster"),
    ("While bypass permissions", "bypass-mode note"),
]

# Section markers seen in the wild on big system blocks. Neither is universal, so
# both are tried and the one that finds more sections wins — a block that matches
# neither falls through to a single unsegmented row rather than a wrong split.
#   CAPS_LABEL  bare ALL-CAPS line          (clodex wrapper: `HOW TO COMMUNICATE:`)
#   DASH_LABEL  dash-fenced ALL-CAPS line   (workbench seats: `--- IDENTITY ---`)
CAPS_LABEL = re.compile(r"^[A-Z][A-Z /-]{3,}:?$", re.M)
DASH_LABEL = re.compile(r"^-{2,}\s*[A-Z][A-Z /-]{2,}\s*-{2,}$", re.M)
MD_H1 = re.compile(r"^# (.+)$")
MD_H2 = re.compile(r"^#{2,4} (.+)$", re.M)
WRAPPER_START = "This session runs inside clodex"


def tok(s, density):
    return round(len(s) / density)


def text_of(block):
    """Flatten one content block to the text that is actually carried."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return json.dumps(block)
    t = block.get("type")
    if t == "text":
        return block.get("text") or ""
    if t == "thinking":
        return block.get("thinking") or ""
    if t == "redacted_thinking":
        return block.get("data") or ""
    if t == "tool_use":
        return json.dumps(block.get("input") or {})
    if t == "tool_result":
        c = block.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(text_of(x) for x in c)
        return json.dumps(c or "")
    return json.dumps(block)


def split_headings(s, pattern=MD_H1):
    """Split a blob on top-level markdown headings, keeping the heading with its body."""
    parts, cur, name = [], [], "(preamble)"
    for line in s.split("\n"):
        m = pattern.match(line) if pattern is MD_H1 else None
        if m:
            if cur:
                parts.append((name, "\n".join(cur)))
            name, cur = m.group(1).strip(), [line]
        else:
            cur.append(line)
    if cur:
        parts.append((name, "\n".join(cur)))
    return parts


def split_labels(s, regex):
    """Split on bare label lines (the clodex wrapper's ALL-CAPS sections)."""
    marks = [(m.start(), m.group(0).strip()) for m in regex.finditer(s)]
    marks.append((len(s), None))
    out, prev_pos, prev_name = [], 0, "(head)"
    for pos, name in marks:
        seg = s[prev_pos:pos]
        if seg.strip():
            out.append((prev_name, seg))
        prev_pos, prev_name = pos, name
    return out


def split_anchors(s, anchors):
    """Attribute a flat text block by ordered leading-substring anchors."""
    found = []
    for needle, label in anchors:
        i = s.find(needle)
        if i >= 0:
            found.append((i, label))
    if not found:
        return None
    found.sort()
    out = []
    if found[0][0] > 200:
        out.append(("(head)", s[:found[0][0]]))
    for k, (pos, label) in enumerate(found):
        end = found[k + 1][0] if k + 1 < len(found) else len(s)
        out.append((label, s[pos:end]))
    return out


def _best_label_re(s):
    """Whichever label dialect actually segments this block, or None."""
    best, n_best = None, 1
    for rx in (CAPS_LABEL, DASH_LABEL):
        n = len(rx.findall(s))
        if n > n_best:
            best, n_best = rx, n
    return best


def dissect(body):
    """-> (rows, meta). rows = (group, name, chars, est_tokens)."""
    rows = []

    for t in body.get("tools") or []:
        blob = json.dumps(t)
        rows.append(("tools", t.get("name") or t.get("type") or "?",
                     len(blob), tok(blob, D_TOOL)))

    sysb = body.get("system")
    if isinstance(sysb, str):
        sysb = [{"type": "text", "text": sysb}]
    for i, b in enumerate(sysb or []):
        s = text_of(b)
        if len(s) < 4000:
            rows.append(("system", f"sys[{i}] {s[:44].strip()!r}", len(s), tok(s, D_SYS)))
            continue
        # Big system block: peel the clodex wrapper (label-split), leave the rest
        # to heading-split. Both halves matter and they split differently.
        cut = s.find(WRAPPER_START)
        head, wrapper = (s[:cut], s[cut:]) if cut > 0 else (s, "")
        for name, sec in split_headings(head):
            if not sec.strip():
                continue
            # A heading-less block (no `# H1` at all) lands here whole; try the
            # label splitters before giving up, or a 11k-char seat prompt reports
            # as one opaque `# (preamble)` row with no lever attached to it.
            lab = _best_label_re(sec)
            if name == "(preamble)" and len(sec) > 4000 and lab:
                for lname, lsec in split_labels(sec, lab):
                    rows.append(("system", f"sys[{i}] {lname}", len(lsec), tok(lsec, D_SYS)))
            else:
                rows.append(("system", f"sys[{i}] # {name}", len(sec), tok(sec, D_SYS)))
        if wrapper:
            # A role block (`# Team lead`) can live INSIDE the wrapper region and is
            # usually the biggest single thing there, so heading-split it out first.
            role_at = wrapper.find("\n# ")
            body_part = wrapper[:role_at] if role_at > 0 else wrapper
            for name, sec in split_labels(body_part, _best_label_re(body_part)):
                rows.append(("wrapper", f"{name}", len(sec), tok(sec, D_SYS)))
            if role_at > 0:
                for name, sec in split_headings(wrapper[role_at + 1:]):
                    for sub, ssec in ([(name, sec)] if len(sec) < 6000
                                      else _subsplit(name, sec)):
                        rows.append(("role", sub, len(ssec), tok(ssec, D_SYS)))

    msgs = body.get("messages") or []
    for mi, m in enumerate(msgs):
        blocks = m["content"] if isinstance(m.get("content"), list) else [m.get("content")]
        for bi, b in enumerate(blocks):
            s = text_of(b)
            if not s:
                continue
            hook = mi == 1 and "SessionStart hook" in s[:80]
            if mi == 0 and len(s) > 4000:
                for name, sec in split_headings(s):
                    # A CLAUDE.md arrives as ONE `# Title` section whose real
                    # structure is `##` below it — sub-split or the whole file
                    # lands as a single unattributable row.
                    for sub, ssec in ([(f"# {name}", sec)] if len(sec) < 6000
                                      else _subsplit(name, sec)):
                        rows.append(("claudemd", sub, len(ssec), tok(ssec, D_MSG)))
            elif hook:
                parts = split_anchors(s, HOOK_ANCHORS)
                if parts:
                    for name, sec in parts:
                        rows.append(("hook", name, len(sec), tok(sec, D_MSG)))
                else:
                    rows.append(("hook", "SessionStart (unsegmented)", len(s), tok(s, D_MSG)))
            else:
                label = f"m{mi}[{bi}]"
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    label += f" {b.get('name')}"
                label += f" {s[:52].strip()!r}"
                group = "reminders" if "<system-reminder>" in s[:40] else m.get("role") or "?"
                rows.append((group, label, len(s), tok(s, D_MSG)))

    meta = {"n_messages": len(msgs), "n_tools": len(body.get("tools") or []),
            "model": body.get("model")}
    return rows, meta


def _subsplit(name, sec):
    """A role section big enough to hide its own structure gets one more level."""
    marks = [(m.start(), m.group(1).strip()) for m in MD_H2.finditer(sec)]
    if len(marks) < 2:
        return [(name, sec)]
    marks.append((len(sec), None))
    out = []
    if marks[0][0] > 200:
        out.append((f"# {name} (head)", sec[:marks[0][0]]))
    # Rows are read as a ranked list, so the sub-heading must lead: repeating the
    # parent title on every row pushes the distinguishing part off the width.
    short = name if len(name) <= 22 else name[:20] + "…"
    for k, (pos, sub) in enumerate(marks[:-1]):
        out.append((f"{sub}   [{short}]", sec[pos:marks[k + 1][0]]))
    return out


def _by_seq(paths):
    """Chronological order, by the capture's leading sequence number.

    Lexicographic filename sort is WRONG here and silently so: `10-` sorts before
    `2-`, so any session crossing a digit boundary gets its request order
    scrambled. That does not perturb totals, but the decay curve is defined by
    ORDER, so it would quietly report a shuffled trend as the finding.
    """
    def key(p):
        head = Path(p).name.split("-", 1)[0]
        return (0, int(head)) if head.isdigit() else (1, Path(p).name)
    return sorted(paths, key=key)


def amplification(session_dir, cold_tokens):
    """How many times the boot load is re-read, and what share of reads that is.

    The cold number alone invites the wrong conclusion in both directions. It is
    ONE write, so it looks cheap next to a bust; but it is re-read on every
    REQUEST, and a seat issues ~20-34 API requests per operator turn (measured
    here; a "turn" is one human prompt to one delivered answer, and every tool
    round inside it is its own billed request). Multiplying by the request count
    is what turns "the seat opens at 43k" into "the boot load is half the read
    bill" — which is a different claim, and the actionable one.

    `turns` is approximate: we count requests whose last message is genuine user
    text rather than a tool_result. Capture pruning and side-calls can move it,
    so it is reported as context for the ratio, never as a billing fact.
    """
    reqs = turns = 0
    read = write = 0
    per_req = []
    for f in _by_seq(session_dir.glob("*.request.json")):
        try:
            body = json.load(open(f)).get("body") or {}
        except (json.JSONDecodeError, OSError):
            continue
        if not any(isinstance(t, dict) and t.get("input_schema")
                   for t in (body.get("tools") or [])):
            continue
        reqs += 1
        msgs = body.get("messages") or []
        if msgs and msgs[-1].get("role") == "user":
            c = msgs[-1].get("content")
            blocks = c if isinstance(c, list) else [c]
            if not any(isinstance(x, dict) and x.get("type") == "tool_result"
                       for x in blocks):
                turns += 1
    for f in _by_seq(session_dir.glob("*.response.json")):
        try:
            u = json.load(open(f)).get("usage") or {}
        except (json.JSONDecodeError, OSError):
            continue
        rd = (u.get("cache_read_input_tokens") or 0) + (u.get("input_tokens") or 0)
        read += rd
        if rd:
            per_req.append(rd)
        write += u.get("cache_creation_input_tokens") or 0
    # The boot load is WRITTEN on the cold request and only RE-READ afterwards, so
    # the amortization claim needs at least a few warm requests to mean anything.
    # On a 1-request session `carried/read` printed 2,176% — the prefix was paid as
    # a write while `read` held only the tail. Suppress rather than emit a share of
    # a quantity the load was never part of.
    amortized = max(reqs - 1, 0)
    carried = cold_tokens * amortized
    # The aggregate share is a session AVERAGE and hides its own trend: the boot
    # load is a CONSTANT while the conversation grows, so its share decays as the
    # window fills (measured: 83% -> 32% across one session's quartiles, and a
    # 4,311-request session sat at 34%). Quoting the average alone invites
    # "the boot load is the dominant term", which is true only for SHORT sessions.
    # So report the curve, not just the number.
    quartiles = []
    if len(per_req) >= 8:
        q = len(per_req) // 4
        for i in range(4):
            seg = per_req[i * q:(i + 1) * q] if i < 3 else per_req[3 * q:]
            mean = sum(seg) // len(seg)
            # Cap at 100: a request can read LESS than the cold prefix (the cold
            # turn itself splits across write+read, and warm-up requests hit only
            # part of it), and an uncapped ratio then prints >100% of a quantity
            # it is a share OF. Clamp, and flag it, rather than emit an
            # impossible percentage that reads as a bug in the receipt.
            share = 100.0 * cold_tokens / mean
            quartiles.append({"quartile": i + 1, "mean_read": mean,
                              "boot_share_pct": round(min(share, 100.0), 1),
                              "below_prefix": share > 100.0})
    return {"requests": reqs, "turns": turns, "carried_tokens": carried,
            "read_tokens": read, "write_tokens": write,
            "mean_read_per_request": (sum(per_req) // len(per_req)) if per_req else None,
            "boot_share_by_quartile": quartiles,
            "amortized_requests": amortized,
            "pct_of_read": (round(100 * carried / read, 1)
                            if read and amortized >= 3 and carried <= read else None)}


def cache_profile(body):
    """The prefix's cache markers: how many, and at which TTL tier.

    This tool reports TOKENS, never dollars, so no price constant can make it
    wrong. But the reader's next step is always to price the number, and the
    write premium differs 1.25x (5m) vs 2x (1h) — a 60% swing on the write side
    that is invisible in a token count and easy to assume wrong in either
    direction. It is free to read off the wire, so read it rather than let the
    reader guess: `ttl` is absent on a 5m marker (the default), explicit on 1h.
    """
    tiers = defaultdict(int)

    def scan(n):
        if isinstance(n, dict):
            cc = n.get("cache_control")
            if isinstance(cc, dict):
                tiers[cc.get("ttl") or "5m (default)"] += 1
            for v in n.values():
                scan(v)
        elif isinstance(n, list):
            for v in n:
                scan(v)

    for key in ("system", "messages", "tools"):
        scan(body.get(key))
    return dict(tiers)


def leak_scan(body):
    """Duplicate blocks, and one prose body quoting another."""
    chunks = defaultdict(list)

    def walk(node, where):
        if isinstance(node, str):
            if len(node) > 200:
                chunks[hashlib.md5(node.encode()).hexdigest()].append((where, len(node), node))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{where}.{k}")

    walk(body.get("system"), "system")
    walk(body.get("messages"), "messages")
    dupes = [v for v in chunks.values() if len(v) > 1]

    def sentences(s):
        return {x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", s) if len(x.strip()) > 60}

    bodies = []
    sysb = body.get("system") or []
    if isinstance(sysb, list):
        for b in sysb:
            t = text_of(b)
            if len(t) > 6000:
                bodies.append(("system prose", t))
    for m in (body.get("messages") or [])[:2]:
        blocks = m["content"] if isinstance(m.get("content"), list) else [m.get("content")]
        for b in blocks:
            t = text_of(b)
            if len(t) > 6000:
                bodies.append(("msg prose", t))
    overlaps = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            a, b = sentences(bodies[i][1]), sentences(bodies[j][1])
            shared = a & b
            if shared:
                overlaps.append((bodies[i][0], bodies[j][0], len(shared),
                                 sum(len(x) for x in shared)))
    return dupes, overlaps, len(bodies)


def receipt_for(req_path):
    """Prefix tokens the API actually billed for this request, if captured."""
    resp = Path(str(req_path).replace(".request.json", ".response.json"))
    if not resp.exists():
        return None
    try:
        u = json.load(open(resp)).get("usage") or {}
    except (json.JSONDecodeError, OSError):
        return None
    tot = ((u.get("cache_creation_input_tokens") or 0)
           + (u.get("cache_read_input_tokens") or 0)
           + (u.get("input_tokens") or 0))
    return tot or None


def find_session_dir(session_id, log_dir):
    roots = [Path(log_dir)] if log_dir else DEFAULT_LOG_DIRS
    for r in roots:
        d = r / session_id
        if d.is_dir():
            return d
    raise SystemExit(f"no capture dir for {session_id} under "
                     + ", ".join(str(r) for r in roots))


def pick_cold_request(session_dir):
    """The MAIN LINE's first working turn = the load, pre-work.

    Two traps, both hit on real capture dirs:
      - subagents and one-shot side-calls (WebSearch/WebFetch) share the session's
        capture dir and carry tools too, and their bodies are tiny, so "fewest
        messages" hands back a 1-tool web-search prompt as if it were the seat's
        boot. The role label does NOT separate them — a WebSearch side-call is
        issued on the parent line and captures as role=parent. What does separate
        them is the tool KIND: a server-side tool is `{type: web_search_*}` with no
        `input_schema`, and a seat boot always carries client tools. So require at
        least one client tool rather than filtering on role or a count threshold.
      - the seat's very first request is the title side-call; it carries no tools,
        which is what excludes it.
    """
    best = None
    for f in sorted(session_dir.glob("*.request.json")):
        try:
            rec = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        body = rec.get("body") or {}
        tools = body.get("tools") or []
        if not any(isinstance(t, dict) and t.get("input_schema") for t in tools):
            continue
        n = len(body.get("messages") or [])
        if best is None or n < best[0]:
            best = (n, f, body)
    if best is None:
        raise SystemExit(
            f"no seat-boot request captured in {session_dir}\n"
            "(only side-calls/server-tool requests here; a codex or side-call-only\n"
            " session has no seat boot to anatomize)")
    return best[1], best[2]


def analyze(req_path, body, session_dir=None):
    rows, meta = dissect(body)
    est = sum(r[3] for r in rows)
    receipt = receipt_for(req_path)
    scale = (receipt / est) if (receipt and est) else 1.0
    # A receipt can bill tokens that are NOT in the body we can see: server-side
    # tools (web_search) carry their real definition upstream, and a tiny body
    # then yields an absurd factor (measured x20.6 on a web-search side-call).
    # Scaling by that would silently invent tokens and spread them across every
    # segment proportionally, which reads as a plausible breakdown. Refuse.
    scale_ok = SCALE_MIN <= scale <= SCALE_MAX
    if not scale_ok:
        scale = 1.0
    dupes, overlaps, n_bodies = leak_scan(body)
    return {"path": str(req_path), "rows": rows, "meta": meta, "est": est,
            "receipt": receipt, "scale": scale, "scale_ok": scale_ok,
            "dupes": dupes, "overlaps": overlaps, "n_prose_bodies": n_bodies,
            "cache": cache_profile(body),
            "amp": amplification(session_dir, receipt or round(est * scale))
                   if session_dir else None}


def rollup(rows, scale):
    agg = defaultdict(lambda: [0, 0])
    for g, _n, c, t in rows:
        agg[g][0] += c
        agg[g][1] += round(t * scale)
    return agg


def report(a, top):
    m, scale = a["meta"], a["scale"]
    total = round(a["est"] * scale)
    print(f"# {Path(a['path']).name}")
    tiers = ", ".join(f"{n} @ {t}" for t, n in sorted(a["cache"].items()))
    print(f"  model={m['model']}  tools={m['n_tools']}  messages={m['n_messages']}")
    print(f"  cache markers: {tiers or 'none'}"
          + ("   [1h = 2x write premium, not 1.25x]" if any("1h" in t for t in a["cache"]) else ""))
    if a["receipt"] and a["scale_ok"]:
        print(f"  COLD PREFIX (receipt) = {a['receipt']:,} tok   "
              f"[char estimate {a['est']:,}, calibration x{scale:.3f}]")
    elif a["receipt"]:
        print(f"  COLD PREFIX (ESTIMATE) = {total:,} tok   "
              f"[receipt says {a['receipt']:,} — ratio x{a['receipt']/a['est']:.1f} is "
              "outside the plausible band, NOT applied; the receipt is pricing "
              "something absent from the body (server-side tool?)]")
    else:
        print(f"  COLD PREFIX (ESTIMATE ONLY, no usage captured) = {total:,} tok")
    print()

    agg = rollup(a["rows"], scale)
    print("  == BY GROUP ==")
    for g, (c, t) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print(f"    {g:>12}  {t:>7,} tok  {100*t/total:5.1f}%   ({c:,} ch)")

    print(f"\n  == TOP {top} SEGMENTS ==")
    for g, n, _c, t in sorted(a["rows"], key=lambda r: -r[3])[:top]:
        print(f"    {round(t*scale):>7,} tok  [{g}] {n[:96]}")

    amp = a.get("amp")
    if amp and amp["requests"]:
        print(f"\n  == WHAT THE LOAD ACTUALLY COSTS ==")
        print(f"    re-read on {amp['requests']} API requests "
              f"(~{amp['requests']//max(amp['turns'],1)}x per operator turn, {amp['turns']} turns)")
        if amp["pct_of_read"]:
            print(f"    re-read {amp['carried_tokens']:,} tok = {amp['pct_of_read']}% "
                  f"of this session's {amp['read_tokens']:,} read tokens")
        else:
            print(f"    too few warm requests ({amp['amortized_requests']}) to amortize — "
                  "the load was written, not yet re-read")
        print(f"    one cold write bought it: {amp['write_tokens']:,} tok written all session")
        qs = amp.get("boot_share_by_quartile") or []
        if qs:
            curve = "  ".join(
                f"Q{q['quartile']} {q['boot_share_pct']:.0f}%"
                + ("*" if q.get("below_prefix") else "") for q in qs)
            print(f"    boot share DECAYS as the window fills: {curve}"
                  f"   (mean read/request {amp['mean_read_per_request']:,})")
            if any(q.get("below_prefix") for q in qs):
                print("       (* mean read below the cold prefix — those requests did not "
                      "carry all of it)")
            print("    -> the average above is a short-session artefact; on a long "
                  "session the accumulated\n       conversation dominates and trimming "
                  "the boot load moves the total very little.")

    print("\n  == LEAK SCAN ==")
    if a["dupes"]:
        print(f"    {len(a['dupes'])} DUPLICATED block(s) >200 ch:")
        for v in sorted(a["dupes"], key=lambda v: -v[0][1])[:6]:
            print(f"      x{len(v)}  {v[0][1]:,} ch  {v[0][2][:60]!r}")
            for w, _l, _s in v[:4]:
                print(f"          at {w}")
    else:
        print("    no duplicated blocks >200 ch")
    if a["overlaps"]:
        for x, y, n, ch in a["overlaps"]:
            print(f"    {x} / {y}: {n} identical sentences ({ch:,} ch) — one quotes the other")
    else:
        print(f"    no shared sentences between the {a['n_prose_bodies']} large prose bodies")


def _seat_label(path):
    """Seat name out of `NNNN-<seat>-<role>-<model>-<ts>.request.json`.

    Capture names start with the seat, but a harness may prefix its own name first
    (clodex writes `NNNN-clodex-<seat>-...`), which would label every column the
    same and make a comparison read as one seat twice. So drop a leading field that
    is common to ALL the files being compared rather than special-casing a vendor.
    """
    parts = Path(path).name.split("-")
    return parts[1][:14] if len(parts) > 1 else Path(path).stem[:14]


def _distinct_labels(paths):
    """Seat labels that actually distinguish the columns.

    If the first field is identical across every path it is a harness prefix, not a
    seat name — shift right until the labels differ (or we run out of fields).
    """
    for field in (1, 2, 3):
        labels = [(Path(p).name.split("-") + [""] * 4)[field][:14] for p in paths]
        if len(set(labels)) == len(labels) or len(paths) == 1:
            return labels
    return [_seat_label(p) for p in paths]


def compare(analyses):
    print("\n# COMPARISON")
    groups = sorted({g for a in analyses for g in rollup(a["rows"], a["scale"])})
    names = _distinct_labels([a["path"] for a in analyses])
    print(f"  {'group':>12} " + "".join(f"{n:>16}" for n in names))
    aggs = [rollup(a["rows"], a["scale"]) for a in analyses]
    for g in groups:
        cells = "".join(f"{ag.get(g, [0, 0])[1]:>16,}" for ag in aggs)
        print(f"  {g:>12} {cells}")
    print(f"  {'TOTAL':>12} "
          + "".join(f"{(a['receipt'] or round(a['est']*a['scale'])):>16,}" for a in analyses))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="*", help="session id(s); 2+ prints a comparison")
    ap.add_argument("--file", help="dissect one captured *.request.json directly")
    ap.add_argument("--log-dir", default=os.environ.get("LOG_DIR"))
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", action="store_true", help="machine-readable rows")
    args = ap.parse_args()

    targets = []
    if args.file:
        p = Path(args.file)
        rec = json.load(open(p))
        targets.append((p, rec.get("body") or rec, None))
    for sid in args.sessions:
        d = find_session_dir(sid, args.log_dir)
        f, b = pick_cold_request(d)
        targets.append((f, b, d))
    if not targets:
        ap.error("give a session id or --file")

    analyses = [analyze(p, b, d) for p, b, d in targets]

    if args.json:
        print(json.dumps([{
            "path": a["path"], "receipt": a["receipt"], "estimate": a["est"],
            "scale": round(a["scale"], 4), "calibrated": a["scale_ok"],
            "by_group": {g: v[1] for g, v in rollup(a["rows"], a["scale"]).items()},
            "segments": [{"group": g, "name": n, "chars": c,
                          "tokens": round(t * a["scale"])} for g, n, c, t in a["rows"]],
            "cache_markers": a["cache"], "amplification": a.get("amp"),
            "duplicate_blocks": len(a["dupes"]),
            "prose_overlaps": [{"a": x, "b": y, "sentences": n, "chars": ch}
                               for x, y, n, ch in a["overlaps"]],
        } for a in analyses], indent=1))
        return

    for i, a in enumerate(analyses):
        if i:
            print()
        report(a, args.top)
    if len(analyses) > 1:
        compare(analyses)


if __name__ == "__main__":
    main()
