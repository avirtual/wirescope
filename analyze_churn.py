"""Offline READ-CHURN ledger over captured proxy logs — the story of which
prior tool results were ever re-touched, and which just rode the window dead.

Companion to analyze_tools.py. That one prices dead TOOL SCHEMAS (loaded but
never called — a zero-judgment verdict). This one tackles the harder, juicier
segment: dead TOOL RESULTS, the Read/Bash bodies re-shipped every turn after
the model is done with them. The thesis (CLAUDE.md): stripping is loss-free iff
the stripped content is DEAD by construction; size is only the prize, status is
whether you can claim it.

THE STATUS LADDER for a Read result, derived purely from later tool activity in
the same session:

  overwritten  a later Write (full replace) hit the SAME path     -> the WHOLE
               read body is stale; re-consulting it is WRONG.  PROVABLY DEAD.
  exact-dup    the SAME (path, offset, limit) is read again later -> the later
               copy carries identical/fresher bytes.  PROVABLY DEAD (collapse).
  edited       a later Edit/MultiEdit hit the SAME path           -> only the
               edited hunk is stale; the Edit ack carries no full body, so the
               read still represents the UNEDITED regions and feeds further
               edits.  PARTIALLY live -> NOT a clean strip (downgraded from a
               naive "superseded == dead" read).
  paginated    same path re-read at a DIFFERENT window           -> complementary
               LIVE content (your 50%-of-re-reads finding).  KEEP.
  abandoned    never re-touched by any later tool.               -> PROBABLY dead,
               but a BET: the model may have consulted it silently from context
               (invisible to tool traces), so this is an UPPER BOUND, not a win.

So the reclaim splits into bounds, never one number:
  PROVABLE floor  = overwritten + exact-dup   (clean; legal anytime, free at cold)
  BET ceiling     = abandoned                 (only +EV if churn is genuinely high)
  GREY            = edited                     (partially live; needs a hunk-aware
                                                strip, not a blunt one)
  LIVE floor      = paginated + still-open     (re-reading these is a STRICT loss)

Honesty guard: tool traces cannot see a silent in-context consultation. We only
ever claim "never re-TOUCHED", never "never used".

Keys off file CONTENT, not directory layout. Reads the FULLEST request snapshot
per session (the one carrying the most messages = the complete ordered history).
Detects the STRIP_PRIOR_READS marker so a pre-stripped corpus is reported, not
silently miscounted.

Usage:
  python3 analyze_churn.py [LOG_DIR] [--by tool|session] [--top N] [--bash]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CHARS_PER_TOK = 4          # rough JSON chars->tokens; ranking robust to it
STRIP_MARKER = "[Old tool result content cleared]"   # STRIP_PRIOR_READS sentinel
OVERWRITE_TOOLS = {"Write"}                       # full replace -> whole read stale
EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}  # partial -> read partly live

# recurring cache_read rate is the honest per-turn carriage tax for a result that
# lives in the cached prefix; uncached "in" is the cold-write / re-read rate.
PRICES = {
    "claude-fable-5":  {"in": 10.0, "cache_read": 1.00},
    "claude-opus-4-5": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4-6": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4-7": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4-8": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4":   {"in": 15.0, "cache_read": 1.50},
    "claude-sonnet-4": {"in": 3.0,  "cache_read": 0.30},
    "claude-haiku-4":  {"in": 1.0,  "cache_read": 0.10},
}


def _price(model):
    best = None
    for pfx, p in PRICES.items():
        if (model or "").startswith(pfx) and (best is None or len(pfx) > len(best[0])):
            best = (pfx, p)
    return best[1] if best else {"in": 5.0, "cache_read": 0.50}  # default opus-ish


def _session_id(rec, body):
    sid = (rec.get("summary") or {}).get("session_id")
    if sid:
        return sid
    uid = (body.get("metadata") or {}).get("user_id")
    if uid:
        try:
            return json.loads(uid).get("session_id")
        except Exception:
            pass
    return None


EDIT_INPUT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}
EDIT_ACK_RE = re.compile(
    r"(has been updated successfully|File created successfully at:"
    r"|All occurrences were successfully replaced)")


def _deprefix(read_text):
    """Strip the Read result's 'N\\t' line-number prefixes so an Edit old_string
    (raw, no prefixes) can be matched against the carried file content."""
    return "\n".join(re.sub(r"^\s*\d+\t", "", ln) for ln in read_text.split("\n"))


def _edit_pairs(inp):
    """(old_string, new_string) pairs for an Edit or MultiEdit input."""
    if isinstance(inp.get("edits"), list):
        return [(e.get("old_string", "") or "", e.get("new_string", "") or "")
                for e in inp["edits"]]
    return [(inp.get("old_string", "") or "", inp.get("new_string", "") or "")]


def _result_text(content):
    """tool_result.content is a string OR a list of {type:text,text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text") or "")
            elif isinstance(b, str):
                out.append(b)
        return "".join(out)
    return ""


def _fullest_snapshot(req_files):
    """Per session, the request whose message history is longest = the complete
    ordered transcript. Returns {sid: (body, model)}."""
    best = {}
    for f in req_files:
        try:
            rec = json.load(open(f))
        except Exception:
            continue
        body = rec.get("body") or {}
        if not body.get("tools"):          # skip title/probe side-calls
            continue
        msgs = body.get("messages") or []
        sid = _session_id(rec, body) or f"_nosess::{f.parent.name}"
        if sid not in best or len(msgs) > len(best[sid][2]):
            best[sid] = (body, body.get("model"), msgs)
    return {sid: (msgs, model) for sid, (body, model, msgs) in best.items()}


def _walk(msgs):
    """Yield ordered events: ('use', idx, name, id, input) and
    ('result', idx, tool_use_id, text, is_error). idx = message position."""
    for i, m in enumerate(msgs):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for blk in c:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "tool_use":
                yield ("use", i, blk.get("name"), blk.get("id"), blk.get("input") or {})
            elif t == "tool_result":
                yield ("result", i, blk.get("tool_use_id"),
                       _result_text(blk.get("content")), bool(blk.get("is_error")))


# read-only / idempotent verbs: re-running is side-effect-free and ~deterministic,
# so a forced re-fetch is SAFE (only costs tokens, never corrupts state).
READONLY_VERBS = {
    "ls", "cat", "head", "tail", "grep", "rg", "egrep", "fgrep", "find", "fd",
    "pwd", "echo", "printf", "wc", "stat", "file", "du", "df", "date", "whoami",
    "which", "type", "tree", "env", "printenv", "ps", "top", "uname", "hostname",
    "sort", "uniq", "comm", "diff", "cut", "jq", "yq", "column", "basename",
    "dirname", "realpath", "readlink", "test", "true", "false", "sleep", "id",
    "lsof", "netstat", "history", "man", "help", "nl", "tac", "rev", "md5", "shasum",
}
GIT_SUBCMDS = {"git", "npm", "yarn", "pnpm", "pip", "pip3", "cargo", "docker",
               "kubectl", "go", "python", "python3", "pytest", "node"}


def _segments(cmd):
    """Split a shell line into pipeline/sequence segments on &&, ||, |, ;, newline
    — but only OUTSIDE quotes, so a regex alternation like grep "a\\|b" isn't
    chopped at its escaped pipe (that bug misfiled ~90% of greps as effectful)."""
    cmd = cmd or ""
    out, buf, q, i, n = [], [], None, 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if q:                                  # inside a quote: copy verbatim
            buf.append(ch)
            if ch == q:
                q = None
            i += 1
            continue
        if ch in ("'", '"'):
            q = ch; buf.append(ch); i += 1; continue
        if cmd[i:i + 2] in ("&&", "||"):
            out.append("".join(buf)); buf = []; i += 2; continue
        if ch in "|;\n":
            out.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def bash_verb(cmd):
    """Primary verb signature: first non-(cd/assignment) token of the first real
    segment, plus a subcommand for git-likes. e.g. 'cd x && git status' -> 'git status'."""
    for seg in _segments(cmd or ""):
        toks = seg.split()
        if not toks:
            continue
        t0 = toks[0]
        if t0 == "cd" or "=" in t0 and not t0.startswith("-"):   # skip cd / VAR=val
            continue
        if t0 in GIT_SUBCMDS and len(toks) > 1 and not toks[1].startswith("-"):
            return f"{t0} {toks[1]}"
        return t0
    return (cmd or "").split()[0] if (cmd or "").split() else "?"


READONLY_GIT_SUBS = {"status", "diff", "log", "show", "branch", "remote",
                     "ls-files", "rev-parse", "describe", "blame", "config",
                     "stash"}   # git read-only subcommands (stash list/show only,
                                # but bare 'git stash' is rare — accept)


def bash_is_readonly(cmd):
    """True iff EVERY pipeline/sequence segment's VERB is read-only and there is no
    output redirection. Verb-position only — argument tokens (e.g. a grep pattern
    'commit') never make a command effectful. Conservative: an unknown verb ->
    effectful (we'd rather under-claim strippable than over-claim)."""
    segs = _segments(cmd or "")
    if not segs:
        return False
    for seg in segs:
        # redirection to a file is a write; tolerate fd-dups like 2>&1
        compact = seg.replace("2>&1", "").replace("2>", "")
        if ">" in compact:
            return False
        toks = seg.split()
        if not toks:
            continue
        v = toks[0]
        if v == "cd" or ("=" in v and not v.startswith("-")):   # cd / VAR=val prefix
            # peek at the next token as the real verb
            v = toks[1] if len(toks) > 1 else v
        if v in GIT_SUBCMDS:
            sub = toks[1] if len(toks) > 1 else ""
            if v == "git" and sub in READONLY_GIT_SUBS:
                continue
            if v in {"pip", "pip3"} and sub in {"show", "list", "freeze"}:
                continue
            return False                       # build/install/run/test -> effectful
        if v not in READONLY_VERBS:
            return False
    return True


# a read-only command splits two ways that matter for stripping:
#  content-probe = pulls FILE CONTENT through the shell (cat/grep/sed/...) -> the
#    same reuse problem as the Read tool; re-consultation is likely, no clean win.
#  state-probe   = snapshots MUTABLE WORLD STATE (ls/git status/ps/...) -> value
#    decays (stale-by-change), the model re-runs rather than trust an old one, so
#    stripping it is BOTH cheap AND arguably correct. This is Bash's real edge.
CONTENT_VERBS = {
    "cat", "head", "tail", "sed", "grep", "rg", "egrep", "fgrep", "awk", "jq",
    "yq", "nl", "tac", "cut", "column", "sort", "uniq", "comm", "diff", "wc",
    "git diff", "git log", "git show", "git blame",
}


def readonly_kind(verb):
    return "content-probe" if verb in CONTENT_VERBS else "state-probe"


def classify_bash(uses, result_by_id):
    """Per-Bash-result status ladder, ordered. Returns rows of
    (klass, tokens, verb, is_error)."""
    bashes = [u for u in uses if u["name"] == "Bash"]
    cmds = [(u, (u["input"] or {}).get("command") or "") for u in bashes]
    rows = []
    for k, (u, cmd) in enumerate(cmds):
        r = result_by_id.get(u["id"]) or {}
        body = r.get("text", "") or ""
        tok = len(body) // CHARS_PER_TOK
        err = r.get("err", False)
        idx = u["idx"]
        verb = bash_verb(cmd)
        readonly = bash_is_readonly(cmd)

        later_exact = any(c2 == cmd and v2["idx"] > idx for v2, c2 in cmds)
        later_sameverb = any(bash_verb(c2) == verb and v2["idx"] > idx
                             for v2, c2 in cmds)
        # a failed command whose verb is retried later == dead failure noise
        if err and later_sameverb:
            klass = "stale-error"
        elif later_exact:
            klass = "rerun-exact"          # identical cmd re-run -> prior output stale
        elif readonly:
            klass = readonly_kind(verb)    # state-probe (clean) vs content-probe (Read-like)
        else:
            klass = "effectful"            # one-time record; re-run risks state
        rows.append((klass, tok, verb, err))
    return rows


def classify_session(msgs):
    """Return per-Read classification rows + raw mass tallies for a session."""
    uses = []                      # ordered tool_use records
    result_by_id = {}              # tool_use_id -> {text, err, idx}
    for ev in _walk(msgs):
        if ev[0] == "use":
            uses.append({"idx": ev[1], "name": ev[2], "id": ev[3], "input": ev[4]})
        else:
            result_by_id[ev[2]] = {"text": ev[3], "err": ev[4], "idx": ev[1]}

    # later-activity index by normalized path
    reads = []
    for u in uses:
        if u["name"] == "Read":
            reads.append(u)

    def pkey(inp):
        return (inp or {}).get("file_path")

    classes = []  # (klass, tokens, path, stripped_bool)
    for k, u in enumerate(reads):
        path = pkey(u["input"])
        body = (result_by_id.get(u["id"]) or {}).get("text", "")
        stripped = STRIP_MARKER in (body or "")
        tok = len(body) // CHARS_PER_TOK
        off = (u["input"] or {}).get("offset")
        lim = (u["input"] or {}).get("limit")
        idx = u["idx"]

        later_overwrite = any(
            v["idx"] > idx and v["name"] in OVERWRITE_TOOLS and pkey(v["input"]) == path
            for v in uses)
        later_edit = any(
            v["idx"] > idx and v["name"] in EDIT_TOOLS and pkey(v["input"]) == path
            for v in uses)
        later_reads = [v for v in reads
                       if v["idx"] > idx and pkey(v["input"]) == path]
        exact_dup = any(
            (v["input"] or {}).get("offset") == off and (v["input"] or {}).get("limit") == lim
            for v in later_reads)

        # priority: a clean overwrite/dup beats a partial edit beats pagination
        if later_overwrite:
            klass = "overwritten"
        elif exact_dup:
            klass = "exact-dup"
        elif later_edit:
            klass = "edited"
        elif later_reads:
            klass = "paginated"
        else:
            klass = "abandoned"
        classes.append((klass, tok, path, stripped))
    return classes, uses, result_by_id


def scan_edit_ack_carriage(req_files):
    """One pass over ALL snapshots (not just the fullest): count successful
    Edit/Write ack occurrences + token mass. Each ack rides every turn after it
    is created, so this is the RECURRING carriage — the real cost a collapse-to-
    'ok' transform reclaims. Returns (occurrences, tokens, snapshots_with_acks)."""
    occ = tok = snaps_hit = 0
    for f in req_files:
        try:
            rec = json.load(open(f))
        except Exception:
            continue
        body = rec.get("body") or {}
        if not body.get("tools"):
            continue
        hit = 0
        for m in body.get("messages") or []:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for blk in c:
                if (isinstance(blk, dict) and blk.get("type") == "tool_result"
                        and not blk.get("is_error")):
                    t = _result_text(blk.get("content"))
                    if EDIT_ACK_RE.search(t or ""):
                        occ += 1; tok += len(t) // CHARS_PER_TOK; hit += 1
        if hit:
            snaps_hit += 1
    return occ, tok, snaps_hit


def main():
    args = sys.argv[1:]
    by = "tool"
    top = 0
    show_bash = False
    root = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--by":
            by = args[i + 1]; i += 2
        elif a == "--top":
            top = int(args[i + 1]); i += 2
        elif a == "--bash":
            show_bash = True; i += 1
        else:
            root = a; i += 1
    root = Path(root or "logs_main")

    req_files = sorted(root.rglob("*.request.json"))
    snaps = _fullest_snapshot(req_files)
    if not snaps:
        print(f"no tool-loading sessions found under {root}/")
        return

    # aggregate tallies
    KLASS = ["overwritten", "exact-dup", "edited", "paginated", "abandoned"]
    BKLASS = ["stale-error", "rerun-exact", "state-probe", "content-probe", "effectful"]
    agg = {k: {"n": 0, "tok": 0} for k in KLASS}
    bagg = {k: {"n": 0, "tok": 0} for k in BKLASS}
    bverb = defaultdict(lambda: {"n": 0, "tok": 0})    # verb -> mass
    result_mass = defaultdict(int)     # tool name -> result tokens
    result_cnt = defaultdict(int)
    n_stripped = 0
    per_session = []
    bper_session = []
    models = set()
    # edit-input redundancy (fullest snapshots): old/new mass + old recoverability
    edit = {"n": 0, "old": 0, "new": 0, "old_recov": 0}
    edit_depth = defaultdict(int)      # (sid,path) -> edit count

    for sid, (msgs, model) in snaps.items():
        models.add(model)
        classes, uses, result_by_id = classify_session(msgs)

        # edit-input redundancy: is each old_string already in context (the
        # carried Read of that path, deprefixed, OR a prior new_string)?
        rnorm, prior_new = {}, {}
        for u in uses:
            if u["name"] == "Read":
                p = (u["input"] or {}).get("file_path")
                rnorm[p] = rnorm.get(p, "") + "\n" + _deprefix(
                    (result_by_id.get(u["id"]) or {}).get("text", ""))
        for u in uses:
            if u["name"] not in EDIT_INPUT_TOOLS:
                continue
            path = (u["input"] or {}).get("file_path")
            edit_depth[(sid, path)] += 1
            for old, new in _edit_pairs(u["input"] or {}):
                edit["n"] += 1
                edit["old"] += len(old) // CHARS_PER_TOK
                edit["new"] += len(new) // CHARS_PER_TOK
                oc = old.strip()
                if oc and (oc in rnorm.get(path, "") or oc in prior_new.get(path, "")):
                    edit["old_recov"] += len(old) // CHARS_PER_TOK
                prior_new[path] = prior_new.get(path, "") + "\n" + new
        # total result mass by tool (Read/Bash/...)
        id2name = {u["id"]: u["name"] for u in uses}
        for rid, r in result_by_id.items():
            nm = id2name.get(rid, "?")
            result_mass[nm] += len(r["text"]) // CHARS_PER_TOK
            result_cnt[nm] += 1
        sess_tok = {k: 0 for k in KLASS}
        for klass, tok, path, stripped in classes:
            agg[klass]["n"] += 1; agg[klass]["tok"] += tok
            sess_tok[klass] += tok
            if stripped:
                n_stripped += 1
        per_session.append((sum(sess_tok.values()), sid, model, len(classes), sess_tok))

        # Bash ladder
        brows = classify_bash(uses, result_by_id)
        bsess = {k: 0 for k in BKLASS}
        for klass, tok, verb, err in brows:
            bagg[klass]["n"] += 1; bagg[klass]["tok"] += tok
            bsess[klass] += tok
            bverb[verb]["n"] += 1; bverb[verb]["tok"] += tok
        bper_session.append((sum(bsess.values()), sid, model, len(brows), bsess))

    total_read_tok = sum(a["tok"] for a in agg.values())
    total_read_n = sum(a["n"] for a in agg.values())
    p = _price(next(iter(models)) if len(models) == 1 else None)
    multi_model = len(models) > 1

    print(f"# read-churn ledger  ({len(req_files)} request files, "
          f"{len(snaps)} sessions, models={sorted(m for m in models if m)})\n")
    if n_stripped:
        print(f"  NOTE: {n_stripped} Read results already carry the "
              f"STRIP_PRIOR_READS marker (pre-stripped corpus — their tokens "
              f"read as ~0; run on an UNstripped corpus for true mass)\n")

    # ---- 1. result mass by tool (the 'Read 54% / Bash 41%' picture) ----
    print("## tool-result mass carried in the fullest snapshots")
    print(f"   {'tool':12s} {'results':>8} {'tokens':>10} {'share':>7}  mean/result")
    total_mass = sum(result_mass.values()) or 1
    for nm, tok in sorted(result_mass.items(), key=lambda x: -x[1]):
        cnt = result_cnt[nm]
        print(f"   {nm:12s} {cnt:8d} {tok:10,} {100*tok/total_mass:6.1f}% "
              f"{tok//max(cnt,1):>7,} tok")
    print()

    # ---- 2. Read churn breakdown into the status ladder ----
    print("## Read status ladder  (which prior reads were ever re-touched)")
    print(f"   {'class':12s} {'reads':>7} {'share#':>7} {'tokens':>10} {'share$':>7}  meaning")
    meaning = {
        "overwritten": "PROVABLE dead (later Write -> whole body stale)",
        "exact-dup":   "PROVABLE dead (re-read identical window)",
        "edited":      "GREY (later Edit -> hunk stale, rest live)",
        "paginated":   "LIVE (re-read a different window)",
        "abandoned":   "BET (never re-touched; may be silent-consulted)",
    }
    for k in KLASS:
        n = agg[k]["n"]; tok = agg[k]["tok"]
        print(f"   {k:12s} {n:7d} {100*n/max(total_read_n,1):6.1f}% {tok:10,} "
              f"{100*tok/max(total_read_tok,1):6.1f}%  {meaning[k]}")
    print(f"   {'TOTAL':12s} {total_read_n:7d} {'':7} {total_read_tok:10,}\n")

    # ---- 3. the bounds, priced ----
    prov_tok = agg["overwritten"]["tok"] + agg["exact-dup"]["tok"]
    bet_tok = agg["abandoned"]["tok"]
    grey_tok = agg["edited"]["tok"]
    live_tok = agg["paginated"]["tok"]
    note = "  [MULTI-MODEL corpus: priced at opus-ish default]" if multi_model else ""
    print(f"## reclaim bounds (per-turn carriage these reads add while in-window){note}")

    def money(tok):
        return f"~${tok*p['cache_read']/1e6:.4f} cached .. ${tok*p['in']/1e6:.4f} uncached"

    print(f"   PROVABLE floor (overwrite+dup) : {prov_tok:>9,} tok  "
          f"= {money(prov_tok)}  per full re-carry")
    print(f"     -> clean, loss-free. Legal anytime; FREE at cold cache "
          f"(rides the cold write).")
    print(f"   GREY (edited, hunk-aware only) : {grey_tok:>9,} tok  "
          f"= {money(grey_tok)}")
    print(f"     -> only the edited hunk is dead; a blunt strip loses the live "
          f"remainder. Needs hunk-diff strip to claim safely.")
    print(f"   BET ceiling (abandoned)        : {bet_tok:>9,} tok  "
          f"= {money(bet_tok)}")
    print(f"     -> UPPER bound only; +EV iff most are truly never consulted. "
          f"Re-reading a consulted one is a strict loss.")
    print(f"   LIVE floor (paginated)         : {live_tok:>9,} tok  "
          f"= {money(live_tok)}")
    print(f"     -> KEEP. Stripping forces a strict re-read.")
    print()
    re_touched = (agg["overwritten"]["n"] + agg["exact-dup"]["n"]
                  + agg["edited"]["n"] + agg["paginated"]["n"])
    if re_touched:
        pag_share = 100 * agg["paginated"]["n"] / re_touched
        clean_share = 100 * (agg["overwritten"]["n"] + agg["exact-dup"]["n"]) / re_touched
        edit_share = 100 * agg["edited"]["n"] / re_touched
        print(f"   of {re_touched} re-touched reads: {pag_share:.0f}% pagination (live), "
              f"{edit_share:.0f}% edited (grey), {clean_share:.0f}% clean-dead "
              f"(overwrite/dup) — vs the corpus prior of ~50% pagination.\n")

    # ---- 4. optional per-session leaderboard ----
    if top:
        print(f"## top {top} sessions by total Read mass")
        per_session.sort(reverse=True)
        print(f"   {'tokens':>9} {'reads':>6}  {'ovw':>5} {'dup':>5} {'edit':>5} "
              f"{'pag':>5} {'aban':>5}  session")
        for tot, sid, model, nrd, st in per_session[:top]:
            print(f"   {tot:9,} {nrd:6d}  {st['overwritten']:5d} {st['exact-dup']:5d} "
                  f"{st['edited']:5d} {st['paginated']:5d} {st['abandoned']:5d}  "
                  f"{sid[:18]} ({model})")
        print()

    # ==== BASH ladder ========================================================
    total_bash_tok = sum(b["tok"] for b in bagg.values())
    total_bash_n = sum(b["n"] for b in bagg.values())
    if total_bash_n:
        print("=" * 72)
        print("## Bash status ladder  (a Bash result is acted-on, and re-runnable —")
        print("   so 'was it run again?' is a BACKWARD-looking provable signal, not a bet)")
        print(f"   {'class':14s} {'cmds':>6} {'share#':>7} {'tokens':>10} {'share$':>7}  meaning")
        bmeaning = {
            "stale-error":   "DEAD (failed; same verb retried later)",
            "rerun-exact":   "DEAD (identical cmd re-run -> prior output stale)",
            "state-probe":   "CLEAN-ish (ephemeral world snapshot; stale-by-change)",
            "content-probe": "HARD (file content via shell -> same bet as Read)",
            "effectful":     "KEEP-ish (one-time record; re-run risks state)",
        }
        for k in BKLASS:
            n = bagg[k]["n"]; tok = bagg[k]["tok"]
            print(f"   {k:14s} {n:6d} {100*n/max(total_bash_n,1):6.1f}% {tok:10,} "
                  f"{100*tok/max(total_bash_tok,1):6.1f}%  {bmeaning[k]}")
        print(f"   {'TOTAL':14s} {total_bash_n:6d} {'':7} {total_bash_tok:10,}\n")

        prov = bagg["stale-error"]["tok"] + bagg["rerun-exact"]["tok"]
        state = bagg["state-probe"]["tok"]
        content = bagg["content-probe"]["tok"]
        keep = bagg["effectful"]["tok"]
        note = "  [MULTI-MODEL: opus-ish default $]" if multi_model else ""
        print(f"## Bash reclaim bounds{note}")

        def bmoney(tok):
            return f"~${tok*p['cache_read']/1e6:.4f} cached .. ${tok*p['in']/1e6:.4f} uncached"

        print(f"   PROVABLE dead (stale-error + rerun-exact): {prov:>9,} tok = {bmoney(prov)}")
        print(f"     -> output superseded by a later run; loss-free, like dup-reads.")
        print(f"   STATE-PROBE (the real Bash win)          : {state:>9,} tok = {bmoney(state)}")
        print(f"     -> ephemeral world snapshot (ls/git status/ps). Value decays;")
        print(f"        the model re-runs rather than trust a stale one -> stripping")
        print(f"        is cheap AND arguably correct. Read had NO analog to this.")
        print(f"   CONTENT-PROBE (Read-like, hard)          : {content:>9,} tok = {bmoney(content)}")
        print(f"     -> cat/grep/sed pulling file content; inherits Read's reuse bet.")
        print(f"   KEEP-ish (effectful)                     : {keep:>9,} tok = {bmoney(keep)}")
        print(f"     -> one-time record (commit/build/install); re-run not reproducible.\n")

        # ---- biggest verbs by mass ----
        print("## top Bash verbs by carried result mass")
        print(f"   {'verb':22s} {'cmds':>6} {'tokens':>10} {'share':>7}  mean/result")
        for verb, s in sorted(bverb.items(), key=lambda x: -x[1]["tok"])[:18]:
            print(f"   {verb:22s} {s['n']:6d} {s['tok']:10,} "
                  f"{100*s['tok']/max(total_bash_tok,1):6.1f}% {s['tok']//max(s['n'],1):>7,} tok")
        print()

        if top:
            print(f"## top {top} sessions by total Bash mass")
            bper_session.sort(reverse=True)
            print(f"   {'tokens':>9} {'cmds':>6}  {'err':>5} {'rerun':>6} {'state':>6} "
                  f"{'cont':>6} {'eff':>6}  session")
            for tot, sid, model, nb, st in bper_session[:top]:
                print(f"   {tot:9,} {nb:6d}  {st['stale-error']:5d} {st['rerun-exact']:6d} "
                      f"{st['state-probe']:6d} {st['content-probe']:6d} {st['effectful']:6d}  "
                      f"{sid[:18]} ({model})")
            print()

    # ==== EDIT redundancy + ACK carriage =====================================
    print("=" * 72)
    print("## Edit input redundancy  (old_string is stale-after-apply; how much is")
    print("   ALSO a duplicate of content already in context?)")
    ein = edit["old"] + edit["new"]
    if ein:
        print(f"   edit-input mass (fullest snapshots): {ein:,} tok  "
              f"(old {edit['old']:,} / new {edit['new']:,})")
        print(f"   old_string recoverable from carried Read or prior new_string: "
              f"{edit['old_recov']:,} tok "
              f"({100*edit['old_recov']/max(edit['old'],1):.0f}% of old mass)")
        depths = sorted(edit_depth.values())
        multi = sum(1 for d in depths if d >= 2)
        print(f"   edited files: {len(depths)}; with >=2 edits: {multi} "
              f"({100*multi/max(len(depths),1):.0f}%); max {max(depths)} edits/file")
        print("   -> old_string strip is token-clean but loses the positional ANCHOR")
        print("      the model uses to reconstruct; full materialization is fragile")
        print("      (reads are paginated/non-overlapping, edits renumber lines).\n")

    occ, atok, hit = scan_edit_ack_carriage(req_files)
    print("## Edit/Write success-ACK carriage  (THE clean win: collapse prior acks")
    print("   to 'ok'; current turn kept. Pure boilerplate, one bit preserved.)")
    if occ:
        reclaim = atok - occ      # ~1 tok per "ok"
        print(f"   acks carried across ALL {len(req_files)} snapshots (recurring): "
              f"{occ:,} occurrences = {atok:,} tok")
        print(f"   collapse-to-'ok' reclaim: ~{reclaim:,} tok "
              f"({100*reclaim/max(atok,1):.0f}%)")
        print(f"   snapshots carrying >=1 ack: {hit:,}  "
              f"(avg {occ/max(hit,1):.1f} acks re-shipped per such turn)")
        print(f"   priced (recurring read tax .. cold re-carry): {money(atok)}")
    print()


if __name__ == "__main__":
    main()
