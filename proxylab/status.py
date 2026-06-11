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
from proxylab import warmth as warmth_mod
from proxylab import wb as wb_mod

def _status_snapshot(session=None, all_sessions=False):
    """Everything a human (or the statusline) wants to know about the sessions
    this proxy tracks, one read-only JSON. Universe = in-memory pingable
    sessions ∪ armed holds ∪ durable session_meta rows (last 24h unless all=1).
    Identity (title/cwd/model) is SQLite-durable; pingability/hold/cost are
    in-memory by design (nothing replayable survives a restart anyway)."""
    now = time.time()
    with pinger_mod._LAST_REQUEST_LOCK:
        last_real = {sid: (e["ts"], bool(e.get("needs_auth")),
                           codex_mod._is_openai_body(e.get("obj")))
                     for sid, e in pinger_mod._LAST_REQUEST.items()}
    holds = hold_mod._hold_snapshot()
    meta_rows, meta_err = {}, None
    try:
        con = warmth_mod._warmth_db()
        with warmth_mod._DB_LOCK:
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
        # title have the `summary` field.
        agent_name = r[9] if r else None
        sessions.append({
            "session_id": sid,
            "kind": kind,
            "ended": ended,
            "agent": agent_name,
            "summary": r[1] if r else None,
            "title": (f"[{agent_name}]" if agent_name else None) or
                     (r[1] if r else None),
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
                       "ttl_s": wq.get("ttl_s")},
            "hold": hold,
            "cost": ({"est_usd": tot["est_usd"], "requests": tot["requests"],
                      "unpriced_requests": tot["unpriced_requests"]}
                     if tot else None),
            "refusals": (tot or {}).get("refusals", 0),
            # receipt-counted completed turns + the latest request-derived
            # heaviness snapshot (turns_in_context resets at /compact)
            "turns_completed": (tot or {}).get("turns"),
            "context": meta_mod._CONTEXT_STATS.get(sid),
        })
    sessions.sort(key=lambda s: s.get("last_seen") or 0, reverse=True)
    res = {"proxy": {"version": core_mod.VERSION,
                     "log_dir": str(core_mod.LOG_DIR), "upstream": core_mod.UPSTREAM,
                     "upstream_openai": codex_mod.UPSTREAM_OPENAI,
                     "uptime_s": round(now - core_mod._START_TS, 1),
                     "flags": {"hold": hold_mod.WARMTH_HOLD, "pinger": pinger_mod.WARMTH_PINGER,
                               "ledger": warmth_mod.WARMTH_LEDGER,
                               "block_cold_ping": warmth_mod.WARMTH_BLOCK_COLD_PING,
                               "wb_intent_dispatch": bool(wb_mod._parse_intents)},
                     "wb_intents": dict(wb_mod._WB_STATS),
                     "subscribers": subs_mod._stats(),
                     "codex": dict(codex_mod._CODEX_STATS),
                     "hold_config": {"margin_s": hold_mod.WARMTH_HOLD_MARGIN,
                                     "interval_s": hold_mod.WARMTH_HOLD_INTERVAL,
                                     "max_hours": hold_mod.WARMTH_HOLD_MAX_HOURS,
                                     "max_pings": hold_mod.WARMTH_HOLD_MAX_PINGS},
                     "tracked_last_requests": len(last_real),
                     "holds_armed": len(holds),
                     "restored_at_start": dict(restore_mod._RESTORED),
                     "totals": dict(billing_mod._TOTALS),
                     "totals_since_start": billing_mod._since_start()},
           "sessions": sessions}
    if meta_err:
        res["proxy"]["session_meta_error"] = meta_err
    return res
