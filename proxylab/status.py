import asyncio
import atexit
import collections
import hashlib
import html
import itertools
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from proxylab import billing as billing_mod
from proxylab import codex as codex_mod
from proxylab import core as core_mod
from proxylab import hold as hold_mod
from proxylab import meta as meta_mod
from proxylab import pinger as pinger_mod
from proxylab import restore as restore_mod
from proxylab import subs as subs_mod
from proxylab import store as store_mod
from proxylab import transforms as transforms_mod
from proxylab import warmth as warmth_mod
from proxylab import writer as writer_mod

# Stable product marker. Many proxies can sit on ANTHROPIC_BASE_URL in front of
# the model backend; a subscriber needs a cheap, unauthenticated way to tell
# OURS apart from a generic forwarder before it tries to register, pull stats,
# or warm the cache. /_identity is that handshake: a consumer confirms
# `product == "wirescope"`, reads `protocols`/`capabilities` to decide what it
# may use, and `endpoints` for where. Additive-only (mirror SUBSCRIBERS.md's
# versioning rule): never remove a field, bump `protocols.<name>` on a break.
# (identity protocol 2: renamed product logproxy -> wirescope, 2026-06-13.)
PRODUCT = "wirescope"
IDENTITY_PROTOCOL = 2


def _identity():
    """The 'is this our proxy?' handshake — see PRODUCT above. Read-only,
    spends nothing; capabilities reflect LIVE flags so a consumer integrates
    conditionally (e.g. only attempt /_ping when ping is actually enabled)."""
    return {
        "wirescope": True,                # quick boolean for the lazy check
        "product": PRODUCT,               # the authoritative discriminator
        "vendor": "proxy-lab",
        "version": core_mod.VERSION,      # release tag or git-describe (dev tree)
        "protocols": {
            # protocol/contract versions a consumer can branch on
            "identity": IDENTITY_PROTOCOL,
            "subscribers": 1,             # SUBSCRIBERS.md envelope "v"
            "wirescope": 1,               # WIRESCOPE.md [wirescope:...] spec (v1:
            #                               renamed ws:->wirescope:, spawn + keep)
        },
        # what THIS process can actually do right now (env flags can disable
        # subsystems) — a subscriber should gate features on these, not assume
        "capabilities": {
            # A/B control arm: when true this port is a byte-verbatim forwarder
            # (whole mutation chain skipped) — the experiment's CONTROL. Analyzers
            # read this to label an arm without guessing from the env.
            "passthrough": transforms_mod.PASSTHROUGH,
            "subscribers": subs_mod.SUBSCRIBERS,
            "warmth": warmth_mod.WARMTH_LEDGER,
            "ping": pinger_mod.WARMTH_PINGER,
            "hold": hold_mod.WARMTH_HOLD,
            "stats": True,                # /_status is always served
            "session_view": True,         # /_session HTML
            "context_view": True,         # /_context tool-roster JSON
            "context_composition": True,  # /_context per-category token breakdown
            "context_utilization": True,  # /_context?...&utilization=1 used/deadweight
            "codex": True,                # /agent/<name>/openai routing
            # wirescope directives (WIRESCOPE.md): agent-name always honored,
            # omit/replace gated by WS_OMIT, keep always honored; `spawn` =
            # whether spawn-position (messages[0] head) directives are read at
            # all; `omit_default` = operator policy already stripped from every
            # subagent spawn (so a spawner needs no knowledge for that case)
            "wirescope": {"agent_name": True, "omit": transforms_mod.WS_OMIT,
                          "replace": transforms_mod.WS_OMIT, "keep": True,
                          "spawn": writer_mod.WS_SPAWN_DIRECTIVES,
                          "omit_default": transforms_mod.WS_OMIT_DEFAULT,
                          "spawner_hint": transforms_mod.WS_SPAWNER_HINT,
                          # tool-roster trim: `tools` (allowlist), `strip-tools`
                          # (denylist), `keep-tools` (override); gated by
                          # WS_STRIP_TOOLS, same spawn/body/sticky plumbing.
                          "strip_tools": transforms_mod.WS_STRIP_TOOLS},
        },
        "endpoints": {
            "identity": "/_identity",
            "status": "/_status",
            "subscribe": "/_subscribe",
            "warm": "/_warm",
            "ping": "/_ping",
            "hold": "/_hold",
            "end": "/_end",
            "admin": "/_admin",
            "session": "/_session",
            "context": "/_context",
        },
        "docs": "INTEGRATION.md",         # front-door contract; push deep-dive = SUBSCRIBERS.md
    }


def _status_snapshot(session=None, all_sessions=False, limit=None):
    """Everything a human (or the statusline) wants to know about the sessions
    this proxy tracks, one read-only JSON. Universe = in-memory pingable
    sessions ∪ armed holds ∪ durable session_meta rows (last 24h unless all=1).
    Identity (title/cwd/model) is SQLite-durable; pingability/hold/cost are
    in-memory by design (nothing replayable survives a restart anyway).

    `limit` (used by /_admin) caps the most-recently-active N sessions BEFORE
    the per-session warmth/segment enrichment, so a 24h window with hundreds/
    thousands of sessions doesn't query + render them all every refresh. Armed
    holds are never dropped; `session`/`all_sessions` bypass the cap. The cut is
    by last_seen, and a warm prefix implies recent activity, so warm sessions
    survive the cap in practice. Reports proxy.sessions_total/truncated."""
    now = time.time()
    with pinger_mod._LAST_REQUEST_LOCK:
        last_real = {sid: (e["ts"], bool(e.get("needs_auth")),
                           codex_mod._is_openai_body(e.get("obj")))
                     for sid, e in pinger_mod._LAST_REQUEST.items()}
    holds = hold_mod._hold_snapshot()
    meta_rows, meta_err = {}, None
    try:
        con = store_mod.db()
        with store_mod.LOCK:
            q = ("SELECT session_id, title, cwd, model, first_seen, last_seen, "
                 "kind, ended_at, end_reason, agent FROM session_meta")
            if session:
                cur = con.execute(q + " WHERE session_id=?", (session,))
            elif all_sessions:
                cur = con.execute(q)
            else:
                cur = con.execute(q + " WHERE last_seen > ?", (now - 86400,))
            meta_rows = {r[0]: r for r in cur.fetchall()}
    except Exception as e:
        meta_err = f"store: {e}"
    sids = set(meta_rows) | set(last_real) | set(holds)
    if session:
        sids &= {session}
    sessions_total = len(sids)
    truncated = False
    if (limit is not None and not session and not all_sessions
            and sessions_total > limit):
        # rank by best-known last activity (meta last_seen ∪ last real turn),
        # keep the top `limit`, but never drop an armed hold.
        def _activity(sid):
            r = meta_rows.get(sid)
            lr = last_real.get(sid)
            return max((r[5] if r else 0) or 0, lr[0] if lr else 0)
        keep = set(holds)
        ranked = sorted(sids - keep, key=_activity, reverse=True)
        sids = keep | set(ranked[:max(0, limit - len(keep))])
        truncated = True
    sessions = []
    for sid in sids:
        r = meta_rows.get(sid)
        kind = r[6] if r else None
        if kind and not (session or all_sessions):
            continue  # proxy-spawned utility session (auth bootstrap) — not
                      # the human's; visible via ?all=1 or direct ?session=
        wq = warmth_mod.warmth_query(session=sid)
        tot = billing_mod._SESSION_TOTALS.get(sid)        # .get: never create via defaultdict
        lr = last_real.get(sid)               # (ts, needs_auth) | None
        hold = holds.get(sid)
        if hold:
            # what THIS hold should still need: idle span / ttl, anchored at
            # the LAST REAL TURN (an organic turn re-warms for free and resets
            # the ping counter, so both sides of n/expected restart together).
            # The global ping cap is only the safety bound.
            hold = dict(hold)
            ttl = wq.get("ttl_s") or 3600
            ref = max(hold["armed_at"], (lr[0] if lr else 0))
            hold["expected_pings"] = min(
                hold_mod.WARMTH_HOLD_MAX_PINGS,
                max(1, int((hold["until"] - ref) // ttl)))
        ended = meta_mod._ENDED.get(sid)
        if not ended and r and r[7]:
            ended = {"ts": r[7], "reason": r[8] or "unspecified"}
        # The route agent name IS the session name when present — it's the
        # operator's own label, stabler than a generated summary (and SDK/
        # headless sessions never make the title side-call at all). Bracketed
        # so a label reads as a label. Consumers wanting the raw learned
        # title have the `summary` field. Last resort, for an un-routed session
        # that never made the title side-call either (e.g. a headless run
        # pointed straight at the port): name it after the session_id's first
        # segment (~8 hex) so it's still uniquely identifiable rather than
        # nameless. `~`-marked to read distinctly from a [route] label, and it
        # ranks BELOW the learned title so a plain interactive CLI keeps its
        # real title.
        agent_name = r[9] if r else None
        sessions.append({
            "session_id": sid,
            "kind": kind,
            "ended": ended,
            "agent": agent_name,
            "summary": r[1] if r else None,
            "title": (f"[{agent_name}]" if agent_name else None) or
                     (r[1] if r else None) or
                     (f"~{sid.split('-')[0]}" if sid else None),
            "cwd": r[2] if r else None,
            "model": r[3] if r else None,
            "first_seen": r[4] if r else None,
            "last_seen": (r[5] if r else None) or (lr[0] if lr else None),
            "last_real_turn_ts": lr[0] if lr else None,
            # openai-wire entries are view-only — never pingable, never
            # awaiting auth (nothing is ever replayed on that wire)
            "pingable": bool(lr and not lr[1] and not lr[2]),
            "awaiting_auth": bool(lr and lr[1] and not lr[2]),
            "warmth": {"state": ("warm" if wq.get("warm")
                                 else "cold" if wq.get("found") else "absent"),
                       "remaining_s": wq.get("remaining_s"),
                       "ttl_s": wq.get("ttl_s"),
                       # leading-breakpoint segments (tools / tools+system),
                       # content-addressed → shared across sessions with
                       # identical layouts; display-grade only
                       "segments": warmth_mod.warmth_segments(sid)},
            # lifetime count of resumes from a COLD cache (each = a full prefix
            # re-write at the write premium); 0 = never lapsed between turns
            "cold_resumes": warmth_mod.cold_resumes(sid),
            "hold": hold,
            "cost": ({"est_usd": tot["est_usd"], "requests": tot["requests"],
                      "unpriced_requests": tot["unpriced_requests"]}
                     if tot else None),
            "refusals": (tot or {}).get("refusals", 0),
            # last ≤20 classifier hits, wire-truth detail (full stop_details);
            # the CLI only ever showed a generic toast
            "refusal_events": (tot or {}).get("refusal_events") or None,
            # receipt-counted completed turns + the latest request-derived
            # heaviness snapshot (turns_in_context resets at /compact)
            "turns_completed": (tot or {}).get("turns"),
            "context": meta_mod._context_stats(sid),
            # Task-spawned subagents that share this session_id (each with its
            # own model + request count) — shown under the main agent so the two
            # are obviously distinct and neither overwrites the other.
            "sub_agents": meta_mod._subagents_snapshot(sid),
        })
    sessions.sort(key=lambda s: s.get("last_seen") or 0, reverse=True)
    res = {"proxy": {"version": core_mod.VERSION,
                     "log_dir": str(core_mod.LOG_DIR), "upstream": core_mod.UPSTREAM,
                     "upstream_openai": codex_mod.UPSTREAM_OPENAI,
                     "uptime_s": round(now - core_mod._START_TS, 1),
                     "flags": {"hold": hold_mod.WARMTH_HOLD, "pinger": pinger_mod.WARMTH_PINGER,
                               "ledger": warmth_mod.WARMTH_LEDGER,
                               "block_cold_ping": warmth_mod.WARMTH_BLOCK_COLD_PING},
                     "subscribers": subs_mod._stats(),
                     "codex": dict(codex_mod._CODEX_STATS),
                     "hold_config": {"margin_s": hold_mod.WARMTH_HOLD_MARGIN,
                                     "interval_s": hold_mod.WARMTH_HOLD_INTERVAL,
                                     "max_hours": hold_mod.WARMTH_HOLD_MAX_HOURS,
                                     "max_pings": hold_mod.WARMTH_HOLD_MAX_PINGS},
                     "tracked_last_requests": len(last_real),
                     "holds_armed": len(holds),
                     "sessions_total": sessions_total,
                     "sessions_shown": len(sessions),
                     "sessions_truncated": truncated,
                     "restored_at_start": dict(restore_mod._RESTORED),
                     "totals": dict(billing_mod._TOTALS),
                     "totals_since_start": billing_mod._since_start()},
           "sessions": sessions}
    if meta_err:
        res["proxy"]["session_meta_error"] = meta_err
    return res


# Rough JSON-chars -> tokens divisor for tool schema sizing. Mirrors
# analyze_tools.py's CHARS_PER_TOK so the live endpoint and the offline ledger
# price a tool the same way; the ranking (what to trim) is robust to the exact
# ratio.
_CHARS_PER_TOK = 4


def _tool_roster(obj):
    """The tool composition of ONE forwarded request body (the post-transform
    obj — i.e. what actually reached the model, reflecting any wirescope
    tool-trim/sort). Returns {count, names, total_schema_chars, est_tokens,
    per_tool:[{name, schema_chars, est_tokens}]} with per_tool biggest-first
    (the 'what to trim' view). None for an openai/codex body (different wire,
    server-side caching, no anthropic tools[]) or when there are no tools."""
    if not isinstance(obj, dict) or codex_mod._is_openai_body(obj):
        return None
    tools = obj.get("tools")
    if not isinstance(tools, list) or not tools:
        return None
    per = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        chars = len(json.dumps(t, ensure_ascii=False))
        per.append({"name": t.get("name"), "schema_chars": chars,
                    "est_tokens": chars // _CHARS_PER_TOK})
    per.sort(key=lambda x: x["schema_chars"], reverse=True)
    total = sum(p["schema_chars"] for p in per)
    return {"count": len(per), "names": [p["name"] for p in per],
            "total_schema_chars": total, "est_tokens": total // _CHARS_PER_TOK,
            "per_tool": per}


def _is_injected_reminder(text):
    """A user-role text block that is harness-injected context, NOT genuine user
    prompt: the `<system-reminder>` bundle (carries # claudeMd / # userEmail /
    # currentDate, plus our relocated env tail) and <command-*>/<local-command-*>
    expansions. Bucketed apart from real `user` text so the composition reflects
    what's actually a person's words vs auto-loaded context."""
    t = (text or "").lstrip()
    return (t.startswith("<system-reminder>") or t.startswith("<command-")
            or t.startswith("<local-command-"))


# The CLI injects the agent roster and the skills list as fixed-opener blocks,
# detected by their canonical lead line (tool word is Agent in clodex / Task in
# vanilla CC, hence the \w+). TWO wire shapes seen, BOTH must match:
#   (old/sonnet) a <system-reminder>-wrapped block inside messages[0]
#   (opus-4-8 mid-conversation-system beta) a trailing role:"system" message,
#     UNWRAPPED — text starts directly with the opener, no <system-reminder>.
# Hence the optional wrapper prefix. The match is ANCHORED to the block start
# (^) on purpose: these exact strings also appear mid-text in tool-results and
# assistant turns when an agent is *discussing* them — a substring match would
# false-positive, the anchor won't. Detecting them lets composition attribute
# their carriage (~1k tok/turn) apart from the agent system prompt, and a
# consumer can attach a trim lever (deny built-in agents / skillOverrides off).
# Whole-block categories: the entire block is the roster/list.
_RE_REMINDER_AGENTS = re.compile(
    r"^(?:<system-reminder>\s*)?Available agent types for the \w+ tool:")
_RE_REMINDER_SKILLS = re.compile(
    r"^(?:<system-reminder>\s*)?The following skills are available for use with the \w+ tool:")
# The opus-4-8 wire CONCATENATES the roster + skills list into ONE role:"system"
# message (agents first, then the skills opener on its own line). This finds that
# inner boundary so a combined block splits agents|skills (line-anchored: only a
# real list opener, never a mid-sentence mention).
_RE_SKILLS_LINE = re.compile(
    r"(?m)^The following skills are available for use with the \w+ tool:")


def _reminder_kind(text):
    """Classify a context block by its anchored opener: 'agents' (CLI agent
    roster), 'skills' (CLI skills list), or None (the # claudeMd/# userEmail
    context bundle, any other reminder/command expansion, or genuine
    conversation — all handled elsewhere). Matches both the <system-reminder>-
    wrapped form (old wire, messages[0]) and the unwrapped trailing role:"system"
    form (opus-4-8 mid-conversation-system beta)."""
    t = (text or "").lstrip()
    if _RE_REMINDER_AGENTS.match(t):
        return "agents"
    if _RE_REMINDER_SKILLS.match(t):
        return "skills"
    return None


def _composition(obj, total_tokens=None):
    """Token composition of ONE forwarded body, by category — 'what is taking up
    the context window'. Generic vocabulary any consumer can render:
    system / claudemd / useremail / agents / skills / tools / user / assistant /
    thinking / tool_calls / tool_results (file reads & command output land in
    tool_results, usually the bulk). claudemd & useremail are split out of the
    system-reminder so a consumer can attach a real trim lever (the wirescope
    omit directives); agents (the CLI agent roster) & skills (the CLI skills
    list) are likewise split out of their own system-reminder blocks — each
    ~hundreds of tok/turn that would otherwise hide inside 'system', and each
    has its own trim lever (deny built-in agents / deny skills in settings).

    Sizing is char-based (len, /4 — same basis as _tool_roster/analyze_tools.py);
    this is a READ-only endpoint computation, never on the forward path. When a
    real receipt `total_tokens` is given (main line), categories are scaled to
    sum to it (basis 'receipt') so the breakdown agrees with the wire-measured
    window total; otherwise raw char-estimate (basis 'estimate'). None for an
    openai/codex body or an empty one."""
    if not isinstance(obj, dict) or codex_mod._is_openai_body(obj):
        return None
    chars = collections.defaultdict(int)
    sysf = obj.get("system")
    if isinstance(sysf, list):
        for b in sysf:
            if isinstance(b, dict):
                chars["system"] += len(b.get("text") or "")
    elif isinstance(sysf, str):
        chars["system"] += len(sysf)
    for t in (obj.get("tools") or []):
        if isinstance(t, dict):
            chars["tools"] += len(json.dumps(t, ensure_ascii=False))
    for m in (obj.get("messages") or []):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        c = m.get("content")
        blocks = (c if isinstance(c, list)
                  else [{"type": "text", "text": c}] if isinstance(c, str) else [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                text = b.get("text") or ""
                kind = _reminder_kind(text)        # agents/skills, any wire shape
                if kind == "agents":               # checked FIRST: the opus-4-8
                    #  roster arrives unwrapped (not an injected-reminder) AND may
                    #  carry the skills list appended in the same block -> split.
                    m = _RE_SKILLS_LINE.search(text)
                    if m:
                        chars["agents"] += m.start()
                        chars["skills"] += len(text) - m.start()
                    else:
                        chars["agents"] += len(text)
                elif kind == "skills":
                    chars["skills"] += len(text)
                elif _is_injected_reminder(text):
                    cm = transforms_mod._ws_strip_reminder_section(text, "# claudeMd")[1]
                    ue = transforms_mod._ws_strip_reminder_section(text, "# userEmail")[1]
                    chars["claudemd"] += cm
                    chars["useremail"] += ue
                    chars["system"] += max(0, len(text) - cm - ue)
                else:
                    chars["assistant" if role == "assistant" else "user"] += len(text)
            elif bt in ("thinking", "redacted_thinking"):
                chars["thinking"] += len(b.get("thinking") or b.get("data")
                                         or json.dumps(b, ensure_ascii=False))
            elif bt == "tool_use":
                chars["tool_calls"] += len(json.dumps(b, ensure_ascii=False))
            elif bt == "tool_result":
                bc = b.get("content")
                ln = (len(bc) if isinstance(bc, str)
                      else sum(len(x.get("text") or "") for x in bc
                               if isinstance(x, dict)) if isinstance(bc, list)
                      else len(json.dumps(bc, ensure_ascii=False)) if bc is not None
                      else 0)
                chars["tool_results"] += ln
    raw = {k: v // _CHARS_PER_TOK for k, v in chars.items() if v > 0}
    raw_total = sum(raw.values())
    if not raw_total:
        return None
    if total_tokens and total_tokens > 0:
        basis, total = "receipt", total_tokens
        cats = [{"category": k, "tokens": round(v * total_tokens / raw_total)}
                for k, v in raw.items()]
    else:
        basis, total = "estimate", raw_total
        cats = [{"category": k, "tokens": v} for k, v in raw.items()]
    for c in cats:
        c["pct"] = round(100.0 * c["tokens"] / total, 1) if total else 0.0
    cats.sort(key=lambda x: x["tokens"], reverse=True)
    return {"total_tokens": total, "basis": basis, "by_category": cats}


def _utilization(session):
    """Lifetime tool-USE tally for a session, scanned from its capture dir
    (LOG_DIR/<session>/). Answers 'of the tools loaded every turn, which ever
    got exercised?' — the deadweight question, made per-session and live.

    On-demand only (a disk scan): callers gate it behind
    `/_context?...&utilization=1` so the 10s poll / admin path never pays for it.
    Scoped to the ONE session dir => naturally bounded to the live session_id (a
    /clear mints a fresh id => fresh dir => the tally never spans the boundary).

    Mirrors analyze_tools.py's accounting, joined request<->response by file
    stem: only turns that LOADED tools AND actually RAN (200) count as a 'chance
    to use' (a no-tools title side-call or an errored turn is not evidence of
    waste). `used` is the RAW invocation count (3 Reads in one turn = 3; from
    response meta.tool_uses), per clodex's contract.

    Returns {key -> {evaluable_turns, by_tool: {name -> used_count}}} keyed by
    agent line: 'main' for the routed parent/unknown-role turns, else the
    subagent INSTANCE's x-claude-code-agent-id (fallback role) — the same key
    _context_snapshot resolves per agent, so the merge lines up. Empty map for a
    cold/absent dir."""
    out = {}
    d = core_mod.LOG_DIR / session
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.request.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        summ = rec.get("summary") or {}
        if not (summ.get("n_tools") or 0):
            continue                       # no tools loaded => not a use-chance
        role = summ.get("role")
        key = "main" if role in ("parent", "unknown", None) \
            else (summ.get("agent_id") or role)
        rp = f.with_name(f.name.replace(".request.json", ".response.json"))
        ok, called = False, []
        try:
            resp = json.loads(rp.read_text())
            ok = resp.get("status_code") == 200
            called = (resp.get("meta") or {}).get("tool_uses") or []
        except Exception:
            pass
        g = out.setdefault(key, {"evaluable_turns": 0,
                                 "by_tool": collections.Counter()})
        if ok:
            g["evaluable_turns"] += 1
            for name in called:
                if name:
                    g["by_tool"][name] += 1
    return out


def _apply_utilization(tools, ustats):
    """Fold one agent line's lifetime tally (from _utilization) into its tools
    roster IN PLACE: stamp per_tool[].used, re-sort deadweight-first (never-used
    first, then biggest schema = the 'trim me' order clodex renders), and return
    a rollup {basis, evaluable_turns, loaded, used_distinct, deadweight_tokens}.
    deadweight_tokens = per-turn schema carriage of the currently-loaded tools
    that were never called — the concrete 'free up ~N tokens' payoff. None when
    there is no roster (codex / no tools)."""
    if not tools:
        return None
    by_tool = (ustats or {}).get("by_tool") or {}
    evaluable = (ustats or {}).get("evaluable_turns", 0)
    for p in tools["per_tool"]:
        p["used"] = int(by_tool.get(p["name"], 0))
    tools["per_tool"].sort(key=lambda x: (x["used"] > 0, -x["est_tokens"]))
    used_distinct = sum(1 for p in tools["per_tool"] if p["used"] > 0)
    deadweight = sum(p["est_tokens"] for p in tools["per_tool"] if p["used"] == 0)
    return {"basis": "capture-scan", "evaluable_turns": evaluable,
            "loaded": tools["count"], "used_distinct": used_distinct,
            "deadweight_tokens": deadweight}


def _context_snapshot(session, utilization=False):
    """`GET /_context?session=<id>`: the tool rosters loaded for a session,
    main/parent line and each subagent INSTANCE reported separately (they carry
    distinct, possibly wirescope-trimmed sets). Read-only over the in-memory
    last forwarded request bodies (parent in pinger._LAST_REQUEST, subagents in
    meta._SUBAGENT_LAST_REQ) — so it answers 'what tools are enabled for session
    X right now'. No disk lookup: an ended/restored/cold session with nothing in
    memory returns agents=[] plus an explanatory note.

    When `utilization=True` each agent's tools roster is additionally enriched
    with per-tool `used` counts + a `utilization` rollup (deadweight pricing)
    via a one-time disk scan of the session's capture dir (_utilization) — the
    'did the loaded tools pay off?' view. Off by default so the cheap in-memory
    path is unchanged for the poll/admin callers."""
    agents = []
    util = _utilization(session) if utilization else {}
    with pinger_mod._LAST_REQUEST_LOCK:
        main = pinger_mod._LAST_REQUEST.get(session)
        main = dict(main) if main else None     # shallow copy; obj read outside lock
    if main:
        obj = main.get("obj")
        # main line carries a real usage receipt -> anchor the composition total
        # to the wire-measured window size so it agrees with /_status.input_tokens
        total = meta_mod._input_token_total(meta_mod._LAST_USAGE.get(session))
        roster = _tool_roster(obj)
        entry = {
            "line": "main", "role": "parent", "agent_id": None,
            "display_name": None,
            "model": (obj or {}).get("model") if isinstance(obj, dict) else None,
            "wire": "openai" if codex_mod._is_openai_body(obj) else "anthropic",
            "last_seen": main.get("ts"),
            "tools": roster,
            "composition": _composition(obj, total)}
        if utilization:
            entry["utilization"] = _apply_utilization(roster, util.get("main"))
        agents.append(entry)
    for s in meta_mod._subagent_request_objs(session):
        obj = s.get("obj")
        roster = _tool_roster(obj)
        entry = {
            "line": "subagent", "role": s.get("role"),
            "agent_id": s.get("agent_id"), "display_name": s.get("display_name"),
            "model": s.get("model"),
            "wire": "openai" if codex_mod._is_openai_body(obj) else "anthropic",
            "last_seen": s.get("last_seen"),
            "tools": roster,
            "composition": _composition(obj)}    # no sub receipt -> estimate
        if utilization:
            ukey = s.get("agent_id") or s.get("role")
            entry["utilization"] = _apply_utilization(roster, util.get(ukey))
        agents.append(entry)
    note = None
    if not agents:
        note = ("no in-memory request for this session "
                "(cold/restored/ended); query while it is active")
    return {"session_id": session, "agents": agents, "note": note}
