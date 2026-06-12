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

from proxylab import pinger as pinger_mod
from proxylab import warmth as warmth_mod
from proxylab import writer as writer_mod

# --- session metadata (title / cwd / model) for /_status ----------------------
# The CLI already tells us everything a session list needs; we just stop
# discarding it: the per-session TITLE-GENERATOR side-call (0 tools, system
# "Generate a concise, sentence-case title…", same wire session_id) answers with
# the session title, and the `# Environment` section (system block interactive,
# msg0 bundle headless, tail block after RELOCATE_ENV_TO_TAIL) carries the cwd.
_CWD_RE = re.compile(r"Primary working directory:\s*(.+)")
_TITLE_SYS_PREFIX = "Generate a concise, sentence-case title"
_META_CWD_TRIES = collections.defaultdict(int)  # sid -> scans attempted
_META_CWD_DONE = set()                          # sids whose cwd is stored
_ENDED = {}     # sid -> {"ts","reason"}: SessionEnd markers (mirror of
                # session_meta.ended_at; a live turn = resume, clears both)
_META_CWD_MAX_TRIES = 5    # env block shows up in the first turns or never


def _upsert_session_meta(session_id, title=None, cwd=None, model=None, now=None,
                         kind=None, agent=None):
    """Durable identity row; COALESCE keeps existing values when a field isn't
    supplied, so the per-request last_seen bump never erases title/cwd/kind."""
    if not session_id:
        return
    now = now or time.time()
    try:
        con = warmth_mod._warmth_db()
        with warmth_mod._DB_LOCK:
            con.execute(
                "INSERT INTO session_meta(session_id, title, cwd, model, "
                "first_seen, last_seen, kind, agent) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "title=COALESCE(excluded.title, session_meta.title), "
                "cwd=COALESCE(excluded.cwd, session_meta.cwd), "
                "model=COALESCE(excluded.model, session_meta.model), "
                "kind=COALESCE(excluded.kind, session_meta.kind), "
                "agent=COALESCE(excluded.agent, session_meta.agent), "
                "last_seen=excluded.last_seen",
                (session_id, title, cwd, model, now, now, kind, agent))
            con.commit()
    except Exception as e:
        print(f"[meta] session_meta upsert failed for {session_id[:12]}…: {e}",
              flush=True)


# Heaviness signals derived from the REQUEST BODY — the model-visible history
# every request re-ships (the wire IS the transcript). Statelessly recomputed
# per turn, so it is resume/fork/restart-proof and resets at /compact for free
# (the summary replaces history; the summary message itself counts as 1).
# Replaces the statusline's JSONL heuristics with the bytes actually billed.
# Latest snapshot per session, in-memory only (next request repopulates).
_CONTEXT_STATS = {}
# Latest main-line response text per session (capped at _META_TEXT_CAP), so
# /_session can show the answer to the final user message — it exists only in
# the response until the next turn re-ships it. In-memory only; /_end drops it.
_LAST_RESPONSE = {}
# Latest main-line token receipts per session (the billing `tokens` dict +
# est_usd + ts), straight from the response: what was cache-read vs (re)written
# vs shipped uncached on the last turn. /_session's header bar. In-memory only.
_LAST_USAGE = {}


def _is_prompt_msg(m):
    """The shared 'a turn starts here' predicate: a user message carrying real
    prompt text (tool_result-only continuations, <command-*> expansions and
    system-reminder bundles don't count). Used by _turn_stats and the
    /_session turn grouping — one definition, so they can never disagree."""
    if not isinstance(m, dict) or m.get("role") != "user":
        return False
    c = m.get("content")
    blocks = (c if isinstance(c, list)
              else [{"type": "text", "text": c}] if isinstance(c, str) else [])
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            t = (b.get("text") or "").lstrip()
            if t and not t.startswith("<command-") \
                    and not t.startswith("<local-command-") \
                    and not t.startswith("<system-reminder>"):
                return True
    return False


def _turn_stats(obj):
    """{turns_in_context, max_tool_result_chars, n_messages} for a messages
    request (turn definition: _is_prompt_msg)."""
    msgs = obj.get("messages") or []
    turns, big = 0, 0
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        if _is_prompt_msg(m):
            turns += 1
        c = m.get("content")
        for b in (c if isinstance(c, list) else []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                bc = b.get("content")
                ln = (len(bc) if isinstance(bc, str)
                      else sum(len(x.get("text") or "") for x in bc
                               if isinstance(x, dict)) if isinstance(bc, list)
                      else 0)
                big = max(big, ln)
    return {"turns_in_context": turns, "max_tool_result_chars": big,
            "n_messages": len(msgs)}


def _extract_cwd(obj):
    """Find 'Primary working directory: …' wherever this CLI build put it:
    system text, the msg0 context bundle, or the relocated tail block. Scans
    only first user msg + last 3 messages (env never lives mid-history)."""
    m = _CWD_RE.search(writer_mod._sys_text(obj))
    if m:
        return m.group(1).strip()
    msgs = obj.get("messages") or []
    scan = msgs[:1] + msgs[-3:]
    for mm in scan:
        if mm.get("role") != "user":
            continue
        c = mm.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                m = _CWD_RE.search(b.get("text") or "")
                if m:
                    return m.group(1).strip()
    return None


def _title_from_text(text):
    """The title call answers plain text on some builds, structured-outputs
    JSON ('{"title": …}', beta structured-outputs-2025-12-15) on others —
    unwrap the latter."""
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            if isinstance(d, dict) and d.get("title"):
                return str(d["title"]).strip()
        except Exception:
            pass
    return t


def _is_title_call(obj):
    """The CLI's per-session title-generator side-call: zero tools + the title
    system prompt. Its response text IS the session title."""
    if obj.get("tools"):
        return False
    sys = obj.get("system")
    texts = ([b.get("text", "") for b in sys if isinstance(b, dict)]
             if isinstance(sys, list) else [sys or ""])
    return any(t.startswith(_TITLE_SYS_PREFIX) for t in texts)


def _capture_session_meta(session_id, obj, model, agent=None):
    """Per-request meta hook (handler, post-parse): bump last_seen/model every
    turn; hunt for the cwd only until found (capped attempts — sessions with a
    custom system prompt may simply not carry an env block). `agent` = the
    /agent/<name>/ route identity (None for plain traffic)."""
    if not session_id:
        return
    pinger_mod._clear_session_ended(session_id)    # live turn on an ended session = resume
    cwd = None
    if session_id not in _META_CWD_DONE and _META_CWD_TRIES[session_id] < _META_CWD_MAX_TRIES:
        _META_CWD_TRIES[session_id] += 1
        cwd = _extract_cwd(obj)
        if cwd:
            _META_CWD_DONE.add(session_id)
            _META_CWD_TRIES.pop(session_id, None)
    writer_mod._enqueue_meta(session_id, cwd=cwd, model=model, agent=agent)
