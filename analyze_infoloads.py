#!/usr/bin/env python3
"""What does a seat's own information-loading cost, and what would a subagent
doing the same reads cost instead?

MAIN LINE ONLY, one session at a time. Every tool call the seat makes is
classified INFO LOAD (Read/Grep/Glob, or a Bash whose every segment only looks)
vs ACTION (edit, append, run, spawn). For each info load we price what it really
costs, which is not the first ingress: a tool result enters the window once and
is then RE-SHIPPED by every later request until a compact/clear drops it, so the
bill is `first write + N carried reads` at the session's own model rates.

Carriage is COUNTED, never modelled: each block is keyed (tool_use id, or a hash
of its text) and we count the requests whose forwarded body actually contains it.
A compact therefore ends a block's carriage by itself, with no compact detector.

PRICING IS RECEIPT-ANCHORED AND POSITIONAL. For each request the receipt says
how many tokens were cache-read, written, and sent uncached. Those are three
CONTIGUOUS SPANS of the forwarded body, in that order, so we walk the body in
order (system+tools first, then messages), scale estimated tokens to the
receipt's total, and charge each block at the rate of the span it lands in. That
puts the write premium on the tail that actually caused it instead of smearing
it, and the per-block sum reconciles to the receipt by construction. Generation
is priced separately off each turn's own SSE (thinking vs text vs tool-call
JSON), which is the only place the emitted bytes of a stripped thinking block
still exist.

CITATION is the compressibility proxy, and it is measured by SYMBOL ECHO, not
by quoting. Verbatim-line matching was tried first and scored 0.3% of ingested
bytes across 3,803 loads -- it measures the detector, because a lead paraphrases
what it reads instead of quoting it. Instead: a token (identifier, path segment,
number, >= 5 chars) is a LEARNED symbol of a result if the seat had never
emitted it before that result and does emit it afterwards, in text or in a tool
input, which is where specs get written. A result LINE is used if it carries at
least one learned symbol; `cited_bytes` is the sum of those lines. Still a lower
bound -- reasoning that leaves no token trace is invisible -- but it bounds how
small a summary of that read could have been.

Usage:
  python3 analyze_infoloads.py --session ID [ID...] [--logs DIR] [--out J.json]
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT_LOGS = os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs")

CH_PER_TOK_MSG = 2.98      # tokest: message JSON density
CH_PER_TOK_SYS = 2.71      # tokest: tool schemas / system prose
READ_TOOLS = {"Read", "Grep", "Glob", "NotebookRead"}

# ---- read-only Bash detection -------------------------------------------------
# A command is an info load only if EVERY segment of it only looks.
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n)\s*")
READER_HEAD = re.compile(
    r"^(?:cat|head|tail|sed -n|ls|wc|stat|file|du|find|grep|rg|egrep|fgrep|"
    r"awk|cut|sort|uniq|jq|tree|which|pwd|echo|printf|date|basename|dirname|"
    r"realpath|readlink|column|xxd|strings|diff|comm|test|\[|node -e|python3? -c)\b")
GIT_READ = re.compile(
    r"^git(?: --no-pager| -C \S+)* (?:status|log|show|diff|branch|rev-parse|"
    r"describe|worktree list|stash list|remote|ls-files|blame|shortlog|tag)\b")
MUTATOR = re.compile(
    r"(?:^|\s)(?:rm|mv|cp|mkdir|touch|chmod|chown|ln|tee|sed -i|git (?:add|commit|"
    r"push|pull|checkout|merge|rebase|reset|stash|worktree add|branch -[dD]|tag )|"
    r"npm|npx|yarn|pnpm|node(?! -e)|python3?(?! -c)|pytest|cargo|go |make|curl|"
    r"open|kill|pkill|launchctl|defaults|brew|\./[\w.-]+\.sh|log\.sh)\b")
REDIR_WRITE = re.compile(r"(?<![0-9<>])>(?!&)|>>")


def bash_is_read(cmd):
    if not cmd:
        return False
    c = cmd.strip()
    if REDIR_WRITE.search(c) and not re.search(r">\s*/dev/null", c):
        return False
    if "<<" in c:                      # heredoc: a script, judge it as action
        return False
    for seg in _SPLIT.split(c):
        seg = seg.strip().lstrip("(").strip()
        if not seg or seg in ("fi", "done", "else", "then"):
            continue
        seg = re.sub(r"^(?:cd \S+|[A-Z_]+=\S+)\s+", "", seg)
        seg = seg.split("|")[0].strip()
        if MUTATOR.search(seg):
            return False
        if not (READER_HEAD.match(seg) or GIT_READ.match(seg)):
            return False
    return True


# ---- shape clustering ---------------------------------------------------------
CODE_EXT = re.compile(r"\.(?:js|mjs|cjs|ts|tsx|jsx|py|css|html|json|sh|yml|yaml|"
                      r"toml|c|h|go|rs|swift|java|rb)\b")
DOC_EXT = re.compile(r"\.(?:md|txt|rst)\b")


def _paths_in(text):
    return re.findall(r"[~/][\w./~-]{3,}", text or "")


def shape_of(name, inp):
    """Recurring SHAPE of one info load, from the tool and its arguments."""
    inp = inp or {}
    if name in ("Read", "NotebookRead"):
        return _path_shape(str(inp.get("file_path") or ""), "read")
    if name == "Glob":
        return "dir/file listing"
    if name == "Grep":
        return "code search (grep/glob)"
    if name != "Bash":
        return "other read"
    cmd = str(inp.get("command") or "")
    low = cmd.lower()
    if re.search(r"\bgit\b.*\b(status|branch|rev-parse|worktree|stash list|remote)", low):
        return "git worktree/branch state"
    if re.search(r"\bgit\b.*\b(log|show|diff|blame|shortlog)", low):
        return "git history/diff read"
    if re.match(r"^\s*(?:ls|find|tree|du)\b", low):
        return "dir/file listing"
    if re.search(r"\b(?:grep|rg|egrep)\b", low):
        return "code search (grep/glob)"
    for p in _paths_in(cmd):
        s = _path_shape(p, "bash")
        if s:
            return s
    if re.match(r"^\s*(?:stat|wc|cat|head|tail|sed -n)\b", low):
        return "dir/file listing"
    return "other read"


def _path_shape(p, how):
    if not p:
        return "other read" if how == "read" else None
    if "/.clodex/messages/" in p:
        return "read hand/peer report message file"
    if "/tasks/_lead/" in p:
        return "tail my decision log"
    if "/.clodex/projects/" in p and "/tasks/" in p:
        return "read ticket spec/verdict artifact"
    if "/.clodex/" in p and "/tasks/" not in p:
        return "clodex state (board/roster/config)"
    if re.search(r"(?:scratchpad|/tmp/|/private/tmp/)", p) and re.search(r"\.(?:out|log|txt|json)\b", p):
        return "read run/test output file"
    if re.search(r"(?:CHANGELOG|HANDOFF|CLAUDE|README|PLAN|OPERATIONS|\.md\b)", p) or DOC_EXT.search(p):
        return "read project doc/changelog"
    if CODE_EXT.search(p):
        return "read source file(s)"
    return "other read" if how == "read" else None


# ---- normalisation for the citation index -------------------------------------
_WS = re.compile(r"\s+")


# A SYMBOL is a token only a reader of this material would produce: an
# identifier (camelCase / snake_case / has a digit), a filename with an
# extension, a hash, or a 4+ digit number. Plain English words are excluded on
# purpose -- an earlier pass counted any 5+ char token and "because"/"function"
# matched everywhere, which scores the language, not the load.
SYM = re.compile(
    r"\b[\w./-]*[A-Za-z_][\w-]*\.[A-Za-z][A-Za-z0-9]{0,4}\b"   # foo/bar.js
    r"|\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]{2,}\b"            # snake_case
    r"|\b[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]{2,}\b"              # camelCase
    r"|\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]{2,}\b"                 # PascalCase
    r"|\b[A-Z]{3,}[A-Z0-9_]*\b"                                # SCREAMING
    r"|\b[A-Za-z]*[0-9][A-Za-z0-9]{3,}\b"                      # has a digit
    r"|\b[0-9a-f]{7,}\b|\b\d{4,}\b")
MAX_SYM_PER_LINE = 12


def _syms(s):
    return {m.group(0) for m in SYM.finditer(s) if len(m.group(0)) >= 5}


def _res_lines(text):
    """[(bytes, symbols)] per result line long enough to carry information."""
    out = []
    for ln in (text or "").splitlines():
        s = _WS.sub(" ", ln).strip()
        s = re.sub(r"^\d+\t", "", s)               # numbered Read output
        s = re.sub(r"^\d+[:\-]", "", s).strip()    # grep -n prefix
        if len(s) >= 20:
            sy = _syms(s)
            if len(sy) > MAX_SYM_PER_LINE:
                sy = set(sorted(sy, key=len, reverse=True)[:MAX_SYM_PER_LINE])
            out.append((len(ln) + 1, sy))
    return out


def _txt_of(block):
    t = block.get("type")
    if t == "text":
        return block.get("text") or ""
    if t == "thinking":
        return block.get("thinking") or ""
    if t == "tool_use":
        try:
            return json.dumps(block.get("input") or {}, ensure_ascii=False)
        except Exception:
            return ""
    if t == "tool_result":
        c = block.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(b.get("text") or "" for b in c if isinstance(b, dict))
    return ""


def _key(block):
    t = block.get("type")
    if t == "tool_use":
        return "U:" + str(block.get("id"))
    if t == "tool_result":
        return "R:" + str(block.get("tool_use_id"))
    h = hashlib.blake2b(_txt_of(block).encode("utf-8", "replace"), digest_size=10)
    return f"{t}:{h.hexdigest()}"


# ---- per-request pricing ------------------------------------------------------
def rates_for(receipt, billing):
    b = (receipt or {}).get("billing") or {}
    tok = b.get("tokens") or {}
    r = billing._price_for(b.get("model"), speed=tok.get("speed"))
    if not r:
        return None
    w1 = tok.get("cache_write_1h_tokens") or 0
    w5 = tok.get("cache_write_5m_tokens") or 0
    # `cache_write_flat_tokens` is the TOTAL (billing's fallback when the wire
    # omits the 5m/1h split), not a third tier — adding it double-counts writes.
    write = (w1 + w5) or (tok.get("cache_write_flat_tokens") or 0)
    return {
        "model": b.get("model"),
        "read_tok": tok.get("cache_read_input_tokens") or 0,
        "write_tok": write,
        "in_tok": tok.get("input_tokens") or 0,
        "out_tok": tok.get("output_tokens") or 0,
        "r_read": r["cache_read"] / 1e6,
        "r_write": r["cache_write_1h" if w1 >= w5 else "cache_write_5m"] / 1e6,
        "r_in": r["in"] / 1e6,
        "r_out": r["out"] / 1e6,
        "usd": b.get("est_usd") or 0.0,
    }


def _span_cost(start, ntok, rt):
    """Cost of a [start, start+ntok) token span under read|write|uncached order."""
    read_end = rt["read_tok"]
    write_end = read_end + rt["write_tok"]
    usd = 0.0
    a, b = start, start + ntok
    for lo, hi, rate in ((0, read_end, rt["r_read"]),
                         (read_end, write_end, rt["r_write"]),
                         (write_end, float("inf"), rt["r_in"])):
        o = max(0.0, min(b, hi) - max(a, lo))
        if o > 0:
            usd += o * rate
    return usd


# ---- SSE: what this turn actually generated -----------------------------------
_SSE_DATA = re.compile(rb"^data: (\{.*)$", re.M)


def sse_emissions(path):
    """[(kind, chars, tool_use_id|None)] for one turn's emitted blocks."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    cur = {}
    out = []
    for m in _SSE_DATA.finditer(raw):
        try:
            ev = json.loads(m.group(1))
        except Exception:
            continue
        t = ev.get("type")
        if t == "content_block_start":
            cb = ev.get("content_block") or {}
            cur = {"kind": cb.get("type"), "chars": len(cb.get("text") or ""),
                   "id": cb.get("id")}
        elif t == "content_block_delta":
            d = ev.get("delta") or {}
            cur["chars"] = cur.get("chars", 0) + len(
                d.get("text") or d.get("thinking") or d.get("partial_json") or "")
        elif t == "content_block_stop":
            if cur.get("kind"):
                out.append((cur["kind"], cur.get("chars", 0), cur.get("id")))
            cur = {}
    return out


# ---- main scan ----------------------------------------------------------------
def scan_session(d, billing):
    reqs = sorted(d.glob("*.request.json"))
    blocks = {}
    order = 0
    n_main = 0
    usd_total = 0.0
    gen = Counter()          # kind -> usd
    gen_tok = Counter()
    call_gen = {}            # tool_use id -> usd generated emitting the call
    totals = Counter()
    for f in reqs:
        try:
            o = json.loads(f.read_bytes())
        except Exception:
            continue
        s = o.get("summary") or {}
        if s.get("agent_id") or s.get("role") not in ("parent", "unknown"):
            continue
        if s.get("keepwarm") or s.get("sidecall"):
            continue
        rp = f.with_name(f.name.replace(".request.json", ".response.json"))
        if not rp.exists():
            continue
        try:
            rt = rates_for(json.loads(rp.read_bytes()), billing)
        except Exception:
            rt = None
        if rt is None:
            continue
        n_main += 1
        usd_total += rt["usd"]
        for k in ("read_tok", "write_tok", "in_tok", "out_tok"):
            totals[k] += rt[k]

        body = o.get("body") or {}
        fixed_ch = (len(json.dumps(body.get("system") or "", ensure_ascii=False))
                    + len(json.dumps(body.get("tools") or [], ensure_ascii=False)))
        fixed_est = fixed_ch / CH_PER_TOK_SYS

        seq = []          # (key, est_tok) in body order
        seen_here = set()
        for m in body.get("messages") or []:
            role = m.get("role")
            c = m.get("content")
            bl = c if isinstance(c, list) else [{"type": "text", "text": c or ""}]
            for block in bl:
                if not isinstance(block, dict):
                    continue
                k = _key(block)
                r = blocks.get(k)
                if r is None:
                    txt = _txt_of(block)
                    nb = len(txt.encode("utf-8", "replace"))
                    r = blocks[k] = {
                        "kind": block.get("type"), "role": role, "order": order,
                        "bytes": nb, "tok": nb / CH_PER_TOK_MSG,
                        "n_req": 0, "usd": 0.0,
                        "name": block.get("name"),
                        "id": block.get("id") or block.get("tool_use_id"),
                        "input": block.get("input") if block.get("type") == "tool_use" else None,
                        "is_error": bool(block.get("is_error")),
                        "lines": (_res_lines(txt)
                                  if block.get("type") == "tool_result" else None),
                        "out_syms": (_syms(txt) if role == "assistant"
                                     and block.get("type") in ("text", "tool_use") else None),
                    }
                    order += 1
                if k in seen_here:
                    continue
                seen_here.add(k)
                r["n_req"] += 1
                seq.append((k, r["tok"]))

        est_total = fixed_est + sum(t for _, t in seq)
        recv_total = rt["read_tok"] + rt["write_tok"] + rt["in_tok"]
        scale = (recv_total / est_total) if est_total > 0 else 0.0
        pos = fixed_est * scale
        totals["fixed_usd"] += _span_cost(0.0, pos, rt)
        for k, t in seq:
            n = t * scale
            blocks[k]["usd"] += _span_cost(pos, n, rt)
            pos += n

        # --- generation side, off this turn's own SSE
        sse = f.with_name(f.name.replace(".request.json", ".response.sse"))
        em = sse_emissions(sse)
        tot_ch = sum(c for _, c, _ in em)
        for kind, ch, tid in em:
            share = (ch / tot_ch) if tot_ch else 0.0
            tok = rt["out_tok"] * share
            usd = tok * rt["r_out"]
            gen[kind] += usd
            gen_tok[kind] += tok
            if kind == "tool_use" and tid:
                call_gen[tid] = call_gen.get(tid, 0.0) + usd
        if not em:
            gen["unparsed"] += rt["out_tok"] * rt["r_out"]
            gen_tok["unparsed"] += rt["out_tok"]

    # symbol -> FIRST order the seat itself emitted it. A result teaches a symbol
    # only if that first emission comes after the result arrived.
    cite = {}
    for r in blocks.values():
        sy = r.pop("out_syms", None)
        if not sy:
            continue
        for s in sy:
            h = hashlib.blake2b(s.encode("utf-8", "replace"), digest_size=8).digest()
            if h not in cite or r["order"] < cite[h]:
                cite[h] = r["order"]
    return {"session": d.name, "n_main": n_main, "usd_total": usd_total,
            "totals": dict(totals), "blocks": blocks, "cite": cite,
            "gen": dict(gen), "gen_tok": dict(gen_tok), "call_gen": call_gen}


def classify(sc):
    """Pair tool_use with its result, split info loads from actions, price both."""
    blocks = sc["blocks"]
    cite = sc["cite"]
    call_gen = sc["call_gen"]
    uses = {r["id"]: r for r in blocks.values() if r["kind"] == "tool_use"}
    results = {r["id"]: r for r in blocks.values() if r["kind"] == "tool_result"}
    rows = []
    for tid, u in uses.items():
        name = u["name"]
        inp = u["input"] or {}
        if name in READ_TOOLS:
            info = True
        elif name == "Bash":
            info = bash_is_read(str(inp.get("command") or ""))
        else:
            info = False
        res = results.get(tid)
        cited_b = cited_l = 0
        if res and res["lines"]:
            for nb, syms in res["lines"]:
                for s in syms:
                    h = hashlib.blake2b(s.encode("utf-8", "replace"), digest_size=8).digest()
                    if cite.get(h, -1) > res["order"]:
                        cited_b += nb
                        cited_l += 1
                        break
        rows.append({
            "tool": name, "info": info, "id": tid, "order": u["order"],
            "shape": shape_of(name, inp) if info else None,
            "desc": (str(inp.get("command") or inp.get("pattern")
                         or inp.get("file_path") or "")[:120] if info else None),
            "call_bytes": u["bytes"], "call_req": u["n_req"],
            "call_carry_usd": u["usd"], "call_gen_usd": call_gen.get(tid, 0.0),
            "res_bytes": res["bytes"] if res else 0,
            "res_req": res["n_req"] if res else 0,
            "res_usd": res["usd"] if res else 0.0,
            "res_lines": len(res["lines"]) if res and res["lines"] else 0,
            "cited_bytes": cited_b, "cited_lines": cited_l,
            "is_error": bool(res and res["is_error"]),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--session", nargs="+", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    os.environ["LOG_DIR"] = a.logs
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import logproxy  # noqa: F401
    from proxylab import billing

    L = Path(a.logs)
    out = []
    for sid in a.session:
        d = L / sid
        if not d.is_dir():
            print(f"!! missing {sid}", file=sys.stderr)
            continue
        sc = scan_session(d, billing)
        rows = classify(sc)
        info = [r for r in rows if r["info"]]
        usd_i = sum(r["res_usd"] + r["call_carry_usd"] + r["call_gen_usd"] for r in info)
        modeled = (sum(r["usd"] for r in sc["blocks"].values())
                   + sc["totals"].get("fixed_usd", 0.0) + sum(sc["gen"].values()))
        print(f"{sid[:8]} req={sc['n_main']:5d} ${sc['usd_total']:9.2f} "
              f"(modeled ${modeled:9.2f}) calls={len(rows):5d} info={len(info):5d} "
              f"info_usd=${usd_i:8.2f} ({100*usd_i/max(sc['usd_total'],1e-9):4.1f}%)",
              flush=True)
        out.append({"session": sid, "n_main": sc["n_main"],
                    "usd_total": sc["usd_total"], "modeled_usd": modeled,
                    "totals": sc["totals"], "gen": sc["gen"], "gen_tok": sc["gen_tok"],
                    "rows": rows})
    if a.out:
        Path(a.out).write_text(json.dumps(out))
        print("wrote", a.out)


if __name__ == "__main__":
    main()
