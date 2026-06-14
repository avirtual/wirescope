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
            "subscribers": subs_mod.SUBSCRIBERS,
            "warmth": warmth_mod.WARMTH_LEDGER,
            "ping": pinger_mod.WARMTH_PINGER,
            "hold": hold_mod.WARMTH_HOLD,
            "stats": True,                # /_status is always served
            "session_view": True,         # /_session HTML
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
