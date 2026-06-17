"""report.py — `GET /_report?session=<id>`: the per-session cost/efficiency
report. The on-demand, disk-based answer to "where did this session's tokens
(and dollars) actually go, and is it optimal?"

OWNS the quantitative truth a consumer can't safely re-derive: pricing (real
TTL-correct cache dollars from billing.PRICES), the cost decomposition, the
cache-miss detector, the deadweight findings, and the verdict/score. A consumer
(clodex) pulls this lazily behind a "Full report →" link and renders the prose;
the coupling surface is the versioned schema (`report_version`), not my folder
layout — so I can rename a field on disk without breaking the render.

DISK-BASED on purpose (unlike /_context, which reads in-memory last-requests and
goes empty on a cold/ended session): this scans LOG_DIR/<session>/ directly, so
it works on ENDED and historical sessions. Heavy (hundreds of files) → off the
fast poll/admin path; on-demand only, exactly like /_context?utilization=1.

Two internally-consistent views, by design:
  * cost_decomposition — real billing dollars (per-request `billing` blocks,
    summed; NOT response.cumulative, which is GLOBAL across all sessions).
  * token_decomposition — a char-estimate (len/4, same basis as _tool_roster /
    _composition / analyze_tools.py) of where the *tokens* go, model-independent.
The reconciliation invariants (stated in the schema doc): cost buckets sum to
totals.est_usd (± rounding), and token_decomposition.preamble.unused_tokens ==
Σ of the additive deadweight findings' per-turn tokens (same estimate basis).
"""
import collections
import datetime
import json

from . import billing as billing_mod
from . import core as core_mod
from . import status as status_mod

REPORT_VERSION = 3          # v2: added `waste` section; cache_misses finding
#                             reclaimable is MARGINAL (net of the warm read), while
#                             cost_decomposition.cache_misses stays gross.
#                           v3: carriage multiplier is REQUESTS, not turns (each
#                             request re-sends the prefix; one user turn fans out
#                             into many). Renamed *_per_turn -> *_per_request,
#                             turns_resent -> requests_resent, finding `turns` ->
#                             `requests`; scope.turns now = user prompts (the human
#                             "turn"), scope.requests = wire requests (the multiplier).

# Verdict thresholds, driven by reclaimable_pct of HIGH+MEDIUM-confidence
# findings only (low-conf heuristics inform prose, never the rating — clodex's
# refinement #1). v1 cuts; tune from the real distribution later.
_RATING_OPTIMAL_MAX = 10.0          # < 10% reclaimable -> optimal
_RATING_WASTEFUL_MIN = 30.0         # > 30% reclaimable -> wasteful
# score is a 0-100 "how good" scale (100 = nothing to reclaim); reclaimable_pct
# is mapped linearly to 0 at this ceiling so the UI colours off score directly.
_SCORE_PCT_CEILING = 60.0

_CHARS_PER_TOK = 4

# The preamble = the static, re-sent-every-turn prefix. These composition
# categories make it up (everything that isn't live conversation / output).
_PREAMBLE_CATEGORIES = ("system", "claudemd", "useremail", "agents", "skills",
                        "tools")

# Low-confidence "a cheaper tool existed" heuristic: a Bash command whose first
# word is one of these has a first-class tool that does the same job (our own
# CLAUDE.md anti-pattern list). Flagged as quality signal only — non-additive
# (never counted in the reclaimable $ sum; it overlaps deadweight and is about
# tool choice, not carriage).
_CHEAPER_TOOL = {
    "cat": "Read", "head": "Read", "tail": "Read", "sed": "Read/Edit",
    "grep": "Grep", "rg": "Grep", "egrep": "Grep", "fgrep": "Grep",
    "find": "Glob", "ls": "Glob/Read", "awk": "Read/Grep",
}


def _load(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _line_key(summ):
    """Same agent-line key _utilization/_context resolve: 'main' for the routed
    parent/unknown line, else the subagent INSTANCE's agent-id (fallback role)."""
    role = (summ or {}).get("role")
    if role in ("parent", "unknown", None):
        return "main"
    return (summ or {}).get("agent_id") or role


def _seq_of(stem):
    """The leading capture seq integer of a stem ('692-clodex-...' -> 692), used
    only as a sub-second tiebreaker (NOT the primary order — see _iter_pairs)."""
    head = stem.split("-", 1)[0]
    return int(head) if head.isdigit() else 0


def _iter_pairs(session):
    """Per-request {stem, req, resp, warmth, summ, line, model, ts, billing,
    tokens, ok} for a session dir, in **chronological (timestamp) order**. The
    single disk pass the rest of the report joins against.

    Ordered by `ts`, NOT by the capture seq/filename: seq is a global counter
    that RESETS to 0 on every proxy restart, so a session that spans a restart
    (e.g. a deploy mid-session) would otherwise have its post-restart turns sort
    *before* its pre-restart ones — which scrambles the cache-miss detector's
    chronological state (established/last_ts) and can hide a real idle-gap miss.
    Timestamps are restart-stable; seq is only a sub-second tiebreaker."""
    d = core_mod.LOG_DIR / session
    if not d.is_dir():
        return []
    out = []
    for rf in d.glob("*.request.json"):
        req = _load(rf)
        if req is None:
            continue
        stem = rf.name[: -len(".request.json")]
        resp = _load(rf.with_name(stem + ".response.json")) or {}
        warmth = _load(rf.with_name(stem + ".warmth.json")) or {}
        summ = req.get("summary") or {}
        billing = resp.get("billing") or {}
        ts = req.get("ts") or warmth.get("ts")
        out.append({
            "stem": stem,
            "req": req,
            "resp": resp,
            "warmth": warmth,
            "summ": summ,
            "line": _line_key(summ),
            "model": billing.get("model") or summ.get("model"),
            "ts": ts,
            "billing": billing,
            "tokens": billing.get("tokens") or {},
            "ok": resp.get("status_code") == 200,
        })
    out.sort(key=lambda p: (_epoch(p["ts"]) if _epoch(p["ts"]) is not None else 0.0,
                            _seq_of(p["stem"])))
    return out


def _usd(tokens, rate):
    return (tokens or 0) * rate / 1_000_000.0


def _epoch(ts):
    """Capture timestamps come two ways: request.json `ts` is an ISO-8601 string
    ('2026-06-13T19:01:56'), warmth.json `ts` is an epoch float. Normalise to a
    float epoch (None if unparseable) so idle-gap math works."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# cost_decomposition (Q2): real billing dollars, split into where they went,
# including the MISSED-CACHE bucket (a prefix that had been warm got re-written).
# ---------------------------------------------------------------------------
def _cost_decomposition(pairs):
    buckets = collections.Counter()      # bucket -> usd
    bucket_tokens = collections.Counter()
    # cache-miss = a prefix that had been warm came back COLD on a continuation
    # turn and paid to re-write it (a wasted write — it was already paid for).
    # Signal: main line, we've seen a warm read before (established), this turn
    # is warm_on_arrival False and still writes. (Head-hash recurrence is too
    # strict — the head hash changes every turn as the conversation grows; the
    # miss that matters is the stable PREFIX going cold, not a literal replay.)
    # Scoped to the MAIN line: one stable instance there, where keep-warm is the
    # actionable lever; role-collapsed subagent lines mix fresh instances whose
    # cold preamble write is an inherent spawn cost, not an expiry (that's the
    # cross-instance-sharing lever — a different story). `where` is localised by
    # the system-segment hash: if the preamble's own segment had been warm, the
    # re-write hit the preamble; else it's a conversation-tail rewrite.
    established = False                   # a warm main read has been seen
    warm_segs = set()                    # system-segment hashes seen warm (main)
    last_ts = None                       # ts of previous MAIN turn (idle gap)
    misses = []                          # [{where, suspected_cause, usd, tokens}]
    miss_by_cause = collections.Counter()

    for p in pairs:
        t, model = p["tokens"], p["model"]
        rates = billing_mod._price_for(model)
        if not rates:
            continue                     # unpriced (codex/unknown) — totals guard
        cr = t.get("cache_read_input_tokens") or 0
        w5 = t.get("cache_write_5m_tokens") or 0
        w1 = t.get("cache_write_1h_tokens") or 0
        flat = t.get("cache_write_flat_tokens") or 0
        if not w5 and not w1 and flat:   # no TTL split -> price flat at 5m
            w5 = flat
        inp = t.get("input_tokens") or 0
        out = t.get("output_tokens") or 0
        write_tok = w5 + w1
        write_usd = _usd(w5, rates["cache_write_5m"]) + _usd(w1, rates["cache_write_1h"])

        buckets["cache_read"] += _usd(cr, rates["cache_read"])
        bucket_tokens["cache_read"] += cr
        buckets["uncached_input"] += _usd(inp, rates["in"])
        bucket_tokens["uncached_input"] += inp
        buckets["output"] += _usd(out, rates["out"])
        bucket_tokens["output"] += out

        woa = p["warmth"].get("warm_on_arrival")
        if woa is None:
            woa = cr > 0
        sysseg = ((p["warmth"].get("segments") or {}).get("system") or {}).get("hash")
        ts = _epoch(p["ts"]) or _epoch(p["warmth"].get("ts"))
        gap = (ts - last_ts) if (ts is not None and last_ts is not None) else None

        is_miss = (p["line"] == "main" and bool(write_tok) and not woa and established)
        if is_miss:
            ttl = p["warmth"].get("ttl") or 0
            cause = ("idle_gap_gt_ttl" if (gap is not None and ttl and gap > ttl)
                     else "eviction")   # came back cold within TTL (mid-turn evict)
            where = "preamble" if (sysseg and sysseg in warm_segs) else "conversation"
            buckets["cache_write_rewrite"] += write_usd
            bucket_tokens["cache_write_rewrite"] += write_tok
            misses.append({"line": "main", "where": where, "usd": round(write_usd, 6),
                           "tokens": write_tok, "suspected_cause": cause,
                           "idle_gap_s": round(gap, 1) if gap is not None else None})
            miss_by_cause[cause] += 1
        else:
            buckets["cache_write_initial"] += write_usd
            bucket_tokens["cache_write_initial"] += write_tok

        if p["line"] == "main":
            if woa or cr > 0:
                established = True
                if sysseg:
                    warm_segs.add(sysseg)
            last_ts = ts

    total = sum(buckets.values())
    by_bucket = [{"bucket": b, "usd": round(u, 6), "tokens": int(bucket_tokens[b]),
                  "pct": round(100.0 * u / total, 1) if total else 0.0}
                 for b, u in buckets.items()]
    by_bucket.sort(key=lambda x: x["usd"], reverse=True)
    miss_summary = {
        "count": len(misses),
        "usd": round(buckets["cache_write_rewrite"], 6),
        "tokens": int(bucket_tokens["cache_write_rewrite"]),
        "by_cause": dict(miss_by_cause),
        "where": collections.Counter(m["where"] for m in misses).most_common(1)[0][0]
                 if misses else None,
        "events": misses[:20],            # localised, capped
    }
    return {"total_usd": round(total, 6), "by_bucket": by_bucket}, miss_summary


# ---------------------------------------------------------------------------
# representative body + token_decomposition (the char-estimate "where tokens go")
# ---------------------------------------------------------------------------
def _representative_body(pairs, line="main"):
    """The last tool-loaded request body on a line — the steady-state shape whose
    preamble (tools+system+CLAUDE.md+skills+agents) rides every turn. Returns the
    body dict or None."""
    chosen = None
    for p in pairs:
        if p["line"] != line:
            continue
        if not (p["summ"].get("n_tools") or 0):
            continue
        body = p["req"].get("body")
        if isinstance(body, dict):
            chosen = body
    return chosen


def _token_decomposition(pairs, util_by_line, skutil_by_line):
    """Char-estimate decomposition (len/4) of where the tokens go on the MAIN
    line, with the preamble split used vs unused. unused == Σ deadweight (tools +
    skills) so the reconciliation invariant holds against the findings."""
    body = _representative_body(pairs, "main")
    comp = status_mod._composition(body) if body else None   # estimate basis
    if not comp:
        return None, {}
    cats = {c["category"]: c["tokens"] for c in comp["by_category"]}
    preamble_per_request = sum(cats.get(k, 0) for k in _PREAMBLE_CATEGORIES)

    # deadweight (provably unused) on the main line, from the disk util scans
    tools = status_mod._tool_roster(body)
    skills = status_mod._skill_roster(body)
    t_roll = status_mod._apply_utilization(tools, util_by_line.get("main")) if tools else None
    s_roll = status_mod._apply_skill_utilization(skills, skutil_by_line.get("main")) if skills else None
    unused_per_request = ((t_roll or {}).get("deadweight_tokens", 0)
                          + (s_roll or {}).get("deadweight_tokens", 0))

    # the carriage multiplier is REQUESTS (each re-sends the prefix), not user turns
    main_requests = (util_by_line.get("main") or {}).get("evaluable_turns", 0)
    conversation = sum(cats.get(k, 0) for k in
                       ("user", "assistant", "thinking", "tool_calls", "tool_results"))
    decomp = {
        "basis": "estimate",          # char/4; the $ truth lives in cost_decomposition
        "preamble": {
            "tokens_per_request": preamble_per_request,
            "requests_resent": main_requests,
            "total_resent_tokens": preamble_per_request * main_requests,
            "used_tokens_per_request": max(0, preamble_per_request - unused_per_request),
            "unused_tokens_per_request": unused_per_request,
            "stable": True,           # byte-stable prefix => proven re-sent, not estimated
            "by_category": [{"category": k, "tokens_per_request": cats[k]}
                            for k in _PREAMBLE_CATEGORIES if cats.get(k)],
        },
        "conversation_tokens": conversation,
        "output_tokens": cats.get("output", 0),
    }
    return decomp, {"tools": tools, "skills": skills, "t_roll": t_roll, "s_roll": s_roll,
                    "comp": comp}


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------
def _line_rates(pairs, line):
    for p in pairs:
        if p["line"] == line and p["model"]:
            r = billing_mod._price_for(p["model"])
            if r:
                return r, p["model"]
    return None, None


def _reclaimable_carriage_usd(tokens_per_request, requests, rates):
    """A preamble token that rides the cache is paid as a cache_read on every warm
    REQUEST (not per user turn — one turn fans out into many requests, each
    re-sending the prefix), plus one cold write to establish it. Honest
    reclaimable $ = recurring reads × requests + one 5m write (NOT full input rate
    — it was never full-priced after the first write)."""
    if not rates or not tokens_per_request:
        return 0.0
    recurring = _usd(tokens_per_request, rates["cache_read"]) * max(requests, 1)
    one_write = _usd(tokens_per_request, rates["cache_write_5m"])
    return round(recurring + one_write, 6)


def _tool_result_attribution(pairs):
    """Q1 / refinement C: per (line, tool) {calls, result_tokens}, by joining each
    assistant tool_use id to its tool_result in the next request's history. Also
    collects cheaper-tool and redundant-read evidence. Returns
    {line -> {tool -> {calls, result_tokens}}} and a list of low-conf hints."""
    by_line = {}                         # line -> {tool -> {calls, result_tokens}}
    id_tool = {}                         # tool_use id -> (line, tool name)
    counted_result = set()
    read_targets = collections.Counter()  # (line, file) -> count
    cheaper = collections.Counter()      # (line, cmd, alt) -> count
    seen_use = set()

    for p in pairs:
        line = p["line"]
        slot = by_line.setdefault(line, {})
        for m in (p["req"].get("body") or {}).get("messages") or []:
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "tool_use":
                    bid = b.get("id")
                    name = b.get("name")
                    if bid and bid not in seen_use:
                        seen_use.add(bid)
                        id_tool[bid] = (line, name)
                        ts = slot.setdefault(name, {"calls": 0, "result_tokens": 0})
                        ts["calls"] += 1
                        inp = b.get("input") or {}
                        if name == "Read" and inp.get("file_path"):
                            read_targets[(line, inp["file_path"])] += 1
                        if name == "Bash" and isinstance(inp.get("command"), str):
                            w = inp["command"].strip().split()
                            cmd = w[0] if w else ""
                            cmd = cmd.split("/")[-1]
                            if cmd in _CHEAPER_TOOL:
                                cheaper[(line, cmd, _CHEAPER_TOOL[cmd])] += 1
                elif bt == "tool_result":
                    tid = b.get("tool_use_id")
                    if tid in id_tool and tid not in counted_result:
                        counted_result.add(tid)
                        bc = b.get("content")
                        ln = (len(bc) if isinstance(bc, str)
                              else sum(len(x.get("text") or "") for x in bc
                                       if isinstance(x, dict)) if isinstance(bc, list)
                              else len(json.dumps(bc, ensure_ascii=False)) if bc is not None
                              else 0)
                        ln_tok = ln // _CHARS_PER_TOK
                        lk, nm = id_tool[tid]
                        by_line[lk][nm]["result_tokens"] += ln_tok
    hints = []
    for (line, f), n in read_targets.items():
        if n >= 3:
            hints.append(("redundant_read", line, f, n))
    for (line, cmd, alt), n in cheaper.items():
        if n >= 2:
            hints.append(("cheaper_tool", line, cmd, alt, n))
    return by_line, hints


def _findings(pairs, util_by_line, skutil_by_line, td_extra, attribution, hints):
    out = []
    # per-line deadweight tools + skills (high confidence, additive)
    lines = set(util_by_line) | set(skutil_by_line)
    for line in sorted(lines):
        rates, model = _line_rates(pairs, line)
        is_sub = line != "main"
        body = _representative_body(pairs, line)
        if body is None:
            continue
        tools = status_mod._tool_roster(body)
        skills = status_mod._skill_roster(body)
        t_roll = status_mod._apply_utilization(tools, util_by_line.get(line)) if tools else None
        s_roll = status_mod._apply_skill_utilization(skills, skutil_by_line.get(line)) if skills else None
        attr = attribution.get(line, {})
        if t_roll and t_roll["deadweight_tokens"] > 0:
            reqs = t_roll["evaluable_turns"]      # tool-loaded requests (the multiplier)
            dead = [pp for pp in tools["per_tool"] if pp["used"] == 0]
            evid = [{"name": pp["name"], "est_tokens": pp["est_tokens"],
                     "calls": attr.get(pp["name"], {}).get("calls", 0)}
                    for pp in tools["per_tool"]]
            out.append({
                "id": f"deadweight_tools:{line}",
                "category": "deadweight_tools", "line": line,
                "title": f"{len(dead)} of {t_roll['loaded']} tools loaded, never called"
                         + (" (subagent)" if is_sub else ""),
                "detail": "Schemas re-sent every request, 0 calls over "
                          f"{reqs} requests: " + ", ".join(pp["name"] for pp in dead[:8]),
                "reclaimable_tokens_per_request": t_roll["deadweight_tokens"],
                "reclaimable_tokens": t_roll["deadweight_tokens"] * reqs,
                "reclaimable_usd": _reclaimable_carriage_usd(t_roll["deadweight_tokens"], reqs, rates),
                "requests": reqs,
                "evidence": {"loaded": t_roll["loaded"], "used": t_roll["used_distinct"],
                             "evaluable_requests": reqs, "per_tool": evid},
                "confidence": "high", "additive": True,
                "lever": ("[wirescope:strip-tools …] on the spawn prompt" if is_sub
                          else '--tools "Read Edit Write Bash Glob Grep"'),
            })
        if s_roll and s_roll["deadweight_tokens"] > 0:
            reqs = s_roll["evaluable_turns"]
            dead = [pp for pp in skills["per_skill"] if pp["used"] == 0]
            out.append({
                "id": f"deadweight_skills:{line}",
                "category": "deadweight_skills", "line": line,
                "title": f"{len(dead)} of {s_roll['loaded']} skills loaded, never invoked",
                "detail": "Skills list re-sent every request, 0 invocations over "
                          f"{reqs} requests: " + ", ".join(pp["name"] for pp in dead[:8]),
                "reclaimable_tokens_per_request": s_roll["deadweight_tokens"],
                "reclaimable_tokens": s_roll["deadweight_tokens"] * reqs,
                "reclaimable_usd": _reclaimable_carriage_usd(s_roll["deadweight_tokens"], reqs, rates),
                "requests": reqs,
                "evidence": {"loaded": s_roll["loaded"], "used": s_roll["used_distinct"],
                             "evaluable_requests": reqs},
                "confidence": "high", "additive": True,
                "lever": "skillOverrides:{\"<name>\":\"off\"} in settings (reclaims tokens; "
                         "permissions.deny only gates invocation)",
            })

    # claudemd / useremail carriage on the main line (medium: reclaimable via
    # omit, but we can't PROVE it went unread -> medium, additive).
    comp = (td_extra or {}).get("comp")
    if comp:
        cats = {c["category"]: c["tokens"] for c in comp["by_category"]}
        rates, _ = _line_rates(pairs, "main")
        reqs = (util_by_line.get("main") or {}).get("evaluable_turns", 0)
        for cat, lever in (("claudemd", "[wirescope:omit claudemd] on subagent spawns"),
                           ("useremail", "[wirescope:omit useremail] / WS_OMIT_DEFAULT")):
            tok = cats.get(cat, 0)
            if tok > 0 and reqs:
                out.append({
                    "id": f"reclaimable_{cat}", "category": f"reclaimable_{cat}",
                    "line": "main",
                    "title": f"{cat} re-sent every request ({tok} tok/request)",
                    "detail": f"{cat} rides the cached preamble on all {reqs} requests; "
                              "a file-reading subagent rarely needs it.",
                    "reclaimable_tokens_per_request": tok,
                    "reclaimable_tokens": tok * reqs,
                    "reclaimable_usd": _reclaimable_carriage_usd(tok, reqs, rates),
                    "requests": reqs,
                    "evidence": {"tokens_per_request": tok},
                    "confidence": "medium", "additive": True, "lever": lever,
                })

    # low-confidence heuristics (non-additive: inform prose, never the $ sum/score)
    for h in hints:
        if h[0] == "redundant_read":
            _, line, f, n = h
            out.append({
                "id": f"redundant_read:{line}:{f}", "category": "redundant_tool_calls",
                "line": line, "title": f"Read {f.split('/')[-1]} {n}× ",
                "detail": f"{f} was read {n} times on this line — re-reads ship the "
                          "full file each time.",
                "reclaimable_tokens": 0, "reclaimable_usd": 0.0,
                "occurrences": n, "evidence": {"file": f, "reads": n},
                "confidence": "low", "additive": False, "lever": "cache the read / @file"})
        elif h[0] == "cheaper_tool":
            _, line, cmd, alt, n = h
            out.append({
                "id": f"cheaper_tool:{line}:{cmd}", "category": "cheaper_tool_available",
                "line": line, "title": f"Bash ran `{cmd}` {n}× — {alt} tool exists",
                "detail": f"`{cmd}` was shelled out {n}× via Bash; the {alt} tool does "
                          "the same job without spawning a shell.",
                "reclaimable_tokens": 0, "reclaimable_usd": 0.0,
                "occurrences": n, "evidence": {"command": cmd, "alternative": alt, "count": n},
                "confidence": "low", "additive": False,
                "lever": f"use the {alt} tool instead of Bash {cmd}"})

    out.sort(key=lambda x: x.get("reclaimable_usd", 0.0), reverse=True)
    return out


def _verdict(findings, total_usd, preamble, totals_tokens):
    """Score (0-100, higher = better) + rating, driven by HIGH+MEDIUM additive
    reclaimable $ only. Plain factual headline as the render default."""
    reclaimable = sum(f["reclaimable_usd"] for f in findings
                      if f.get("additive") and f.get("confidence") in ("high", "medium"))
    pct = (100.0 * reclaimable / total_usd) if total_usd else 0.0
    if pct < _RATING_OPTIMAL_MAX:
        rating = "optimal"
    elif pct < _RATING_WASTEFUL_MIN:
        rating = "suboptimal"
    else:
        rating = "wasteful"
    score = round(max(0.0, 100.0 * (1.0 - min(pct, _SCORE_PCT_CEILING) / _SCORE_PCT_CEILING)))
    # factual headline: lead with the dominant cost bucket.
    pre = (preamble or {}).get("tokens_per_request", 0)
    reqs = (preamble or {}).get("requests_resent", 0)
    cr = next((b for b in totals_tokens.get("by_bucket", [])
               if b["bucket"] == "cache_read"), None)
    if cr and reqs:
        headline = (f"{cr['pct']:.0f}% of ${total_usd:.2f} is cache-read of a "
                    f"~{pre/1000:.0f}k-token preamble re-sent {reqs}× (requests)")
    else:
        headline = f"${total_usd:.2f} over {reqs} requests"
    return {"rating": rating, "score": score, "headline": headline,
            "reclaimable_usd_total": round(reclaimable, 6),
            "reclaimable_pct": round(pct, 1),
            "confidence": "high" if any(f.get("confidence") == "high" for f in findings)
                          else "medium"}


# Map each finding category to a waste TYPE (the consolidated "what could have
# gone better" grouping). Low-confidence heuristics (cheaper_tool/redundant_read)
# are additive:false and never enter the waste $ — they're quality hints.
_WASTE_TYPE = {
    "cache_misses": "cold_cache",
    "deadweight_tools": "deadweight_tools",
    "deadweight_skills": "deadweight_skills",
    "reclaimable_claudemd": "claudemd_carriage",
    "reclaimable_useremail": "useremail_carriage",
}


def _waste_section(findings, total_usd):
    """The consolidated WASTE view — 'what could have gone better', priced as the
    real saving (NET reclaimable), aggregated by type across all agent lines.
    Distinct from cost_decomposition (which is gross — every dollar actually paid):
    waste is the avoidable SUBSET. A cold-cache re-write enters at its marginal
    cost (write − the read you'd pay warm); unused tools/skills/claudemd enter at
    their carriage (cached-read rate × requests + one write). Only `additive`
    findings count; sorted by $ desc. total == verdict.reclaimable_usd_total."""
    agg = {}
    for f in findings:
        if not f.get("additive"):
            continue
        wt = _WASTE_TYPE.get(f["category"], f["category"])
        a = agg.setdefault(wt, {"type": wt, "usd": 0.0, "tokens": 0, "items": 0,
                                "confidence": f.get("confidence"),
                                "lever": f.get("lever")})
        a["usd"] += f.get("reclaimable_usd", 0.0)
        a["tokens"] += f.get("reclaimable_tokens", 0) or 0
        a["items"] += 1
        # a high-confidence contributor upgrades the group's confidence label
        if f.get("confidence") == "high":
            a["confidence"] = "high"
    by_type = sorted(agg.values(), key=lambda x: x["usd"], reverse=True)
    for a in by_type:
        a["usd"] = round(a["usd"], 6)
    total = round(sum(a["usd"] for a in by_type), 6)
    return {
        "total_usd": total,
        "pct_of_session": round(100.0 * total / total_usd, 1) if total_usd else 0.0,
        "by_type": by_type,
        "basis": "net reclaimable (the real saving), aggregated by type: cold-cache "
                 "at marginal cost (2× write − 0.1× read you'd pay warm); unused "
                 "tools/skills/claudemd at carriage (cached-read rate × requests + "
                 "one write). Subset of cost_decomposition; == verdict.reclaimable_usd_total.",
    }


def _user_turns(pairs):
    """Genuine conversation turns = user PROMPTS on the main line (what a human
    means by 'turn'), NOT wire requests. The full conversation lives in the last
    (largest) main request body; count user messages that are real prompts — not
    tool_result continuations and not harness-injected reminders. One user turn
    fans out into many requests (each tool-loop hop), which is why carriage is
    priced per-REQUEST, not per-turn."""
    last_main = None
    for p in pairs:
        if p["line"] == "main" and isinstance(p["req"].get("body"), dict):
            last_main = p["req"]["body"]
    if not last_main:
        return 0
    n = 0
    for m in last_main.get("messages") or []:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        blocks = (c if isinstance(c, list)
                  else [{"type": "text", "text": c}] if isinstance(c, str) else [])
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
            continue                      # tool-loop continuation, not a prompt
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        # count if ANY block is genuine prose — the harness bundles the injected
        # reminders alongside the real prompt as separate blocks in the first
        # message, so "exclude if any reminder" would drop that opening turn.
        if any(t.strip() and not status_mod._is_injected_reminder(t) for t in texts):
            n += 1
    return n


def _scope(pairs):
    reqs = billed = 0
    models = set()
    first = last = None
    lines = {}                           # key -> {line, role, agent_id, model, requests}
    for p in pairs:
        reqs += 1
        if p["billing"].get("billable"):
            billed += 1
        if p["model"]:
            models.add(p["model"])
        if p["ts"]:
            first = p["ts"] if first is None else min(first, p["ts"])
            last = p["ts"] if last is None else max(last, p["ts"])
        summ = p["summ"]
        k = p["line"]
        e = lines.setdefault(k, {"line": "main" if k == "main" else "subagent",
                                 "role": summ.get("role"),
                                 "agent_id": summ.get("agent_id"),
                                 "model": p["model"], "requests": 0})
        e["requests"] += 1
    return {"requests": reqs,             # wire requests (the carriage multiplier)
            "billed_requests": billed,
            "turns": _user_turns(pairs),  # user prompts = conversation turns (human)
            "first_ts": first, "last_ts": last, "models": sorted(models),
            "agents": list(lines.values())}


def _totals(pairs):
    agg = collections.Counter()
    usd = 0.0
    for p in pairs:
        t = p["tokens"]
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_write_5m_tokens", "cache_write_1h_tokens", "thinking_tokens"):
            agg[k] += t.get(k) or 0
        usd += p["billing"].get("est_usd") or 0
    return {
        "tokens": {
            "input": int(agg["input_tokens"]),
            "output": int(agg["output_tokens"]),
            "cache_read": int(agg["cache_read_input_tokens"]),
            "cache_write_5m": int(agg["cache_write_5m_tokens"]),
            "cache_write_1h": int(agg["cache_write_1h_tokens"]),
            "thinking": int(agg["thinking_tokens"]),
        },
        "est_usd": round(usd, 6),
        "basis": "summed per-request billing blocks (NOT response.cumulative, "
                 "which is global across all sessions)",
    }


def session_report(session, detail=False):
    """Build the full report_version=1 payload for a session, scanned from disk.
    `detail` is reserved for v1.1 per-turn series (documented, not yet emitted)."""
    pairs = list(_iter_pairs(session))
    if not pairs:
        return {"report_version": REPORT_VERSION, "session_id": session,
                "basis": "on-disk-capture", "note": "no capture on disk for this session",
                "scope": {"requests": 0}, "findings": [], "verdict": None}

    util = status_mod._utilization(session)
    skutil = status_mod._skill_utilization(session)
    scope = _scope(pairs)
    totals = _totals(pairs)
    cost, misses = _cost_decomposition(pairs)
    token_decomp, td_extra = _token_decomposition(pairs, util, skutil)
    attribution, hints = _tool_result_attribution(pairs)
    findings = _findings(pairs, util, skutil, td_extra, attribution, hints)

    # fold the cache-miss localisation into a finding (medium; additive — the $ a
    # keep-warm / hold would reclaim). Priced MARGINAL: a cold prefix is billed as
    # a 2× (1h) write, but even kept warm you'd pay the 0.1× read — so the true
    # saving is write − read, not the gross write (which stays in cost_decomposition
    # as what you actually PAID). At 1h that's ~95% of the write; at 5m less.
    if misses["count"]:
        cause = max(misses["by_cause"], key=misses["by_cause"].get)
        lever = ("/warm-cache N (or POST /_hold) — keep the prefix warm across idle gaps"
                 if cause == "idle_gap_gt_ttl"
                 else "stabilise the prefix (relocate volatile env to tail — on by default)")
        mrates, _ = _line_rates(pairs, "main")
        read_equiv = _usd(misses["tokens"], mrates["cache_read"]) if mrates else 0.0
        marginal = round(max(0.0, misses["usd"] - read_equiv), 6)
        findings.append({
            "id": "cache_misses", "category": "cache_misses", "line": "main",
            "title": f"Cache re-written {misses['count']}× after it had been warm",
            "detail": f"{misses['count']} turns paid a full prefix re-write "
                      f"({misses['tokens']} tok) — dominant cause: {cause}.",
            "reclaimable_tokens": misses["tokens"], "reclaimable_usd": marginal,
            "events": misses["count"], "where": misses["where"],
            "suspected_cause": cause, "by_cause": misses["by_cause"],
            "evidence": {"events": misses["events"], "gross_write_usd": misses["usd"],
                         "read_equiv_usd": round(read_equiv, 6),
                         "note": "reclaimable = gross write − the 0.1× read you'd pay "
                                 "even kept warm; gross is in cost_decomposition"},
            "confidence": "medium", "additive": True, "lever": lever,
        })
        findings.sort(key=lambda x: x.get("reclaimable_usd", 0.0), reverse=True)

    preamble = (token_decomp or {}).get("preamble", {})
    verdict = _verdict(findings, cost["total_usd"], preamble, cost)
    waste = _waste_section(findings, cost["total_usd"])

    return {
        "report_version": REPORT_VERSION,
        "session_id": session,
        "generated_at": __import__("time").time(),
        "basis": "on-disk-capture",
        "scope": scope,
        "totals": totals,
        "cost_decomposition": {**cost, "basis": "receipt", "cache_misses": misses},
        "token_decomposition": token_decomp,
        "findings": findings,
        "waste": waste,
        "verdict": verdict,
        "invariants": {
            "cost_buckets_sum_to_totals": "Σ by_bucket.usd == totals.est_usd (± rounding)",
            "preamble_unused_eq_deadweight": "token_decomposition.preamble."
                "unused_tokens_per_request == Σ findings[deadweight_*]."
                "reclaimable_tokens_per_request (main line; carriage is per-REQUEST, "
                "not per user turn — one turn fans out into many requests)",
            "cache_misses_subset_of_rewrite": "cost_decomposition.cache_misses.usd == "
                "the cache_write_rewrite by_bucket row (localised drill-down, ALREADY "
                "counted in by_bucket — NOT an additional addend; by_bucket stays the "
                "sum-to-totals set). cache_write_initial and cache_write_rewrite are "
                "disjoint and together equal total cache writes.",
            "waste_is_net_reclaimable": "waste.total_usd == verdict.reclaimable_usd_total "
                "== Σ additive findings.reclaimable_usd. waste is the AVOIDABLE SUBSET of "
                "cost_decomposition priced as the real saving (NET): cold-cache marginal "
                "(write − warm read), carriage at cached-read rate. cost_decomposition is "
                "GROSS (every dollar paid); waste is what better choices would reclaim.",
        },
    }
