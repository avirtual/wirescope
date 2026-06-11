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

from proxylab import core as core_mod
from proxylab import writer as writer_mod

# --- prefix-warmth ledger (SQLite-backed, TWO-STATE) ---------------------------
# Records, per cached message-prefix, WHEN it was last stamped and at what TTL,
# so a separate consumer (statusline / hook / pinger / compact-strip) can answer
# "is this conversation's prefix still warm?" — something a per-session JSONL
# can't know, because warmth lives on the CONTENT-ADDRESSED prefix the backend
# caches, which a forked keep-alive ping shares but never writes back to the
# original session's transcript. So the proxy LEARNS it from response receipts
# and stores it here.
#
# STORE: one shared SQLite file (WARMTH_DB, default <proxy dir>/warmth.sqlite),
# WAL mode — durable across proxy restarts and SHARED by every proxy port on the
# box (one ledger, not eight blind ones). Why SQLite over Redis (2026-06-09):
# stdlib + no daemon to babysit; durable per-commit by default (Redis RDB/AOF
# needs deliberate config to not re-create restart-amnesia in miniature); and no
# "store unreachable" runtime state to mishandle now that ABSENCE TRIGGERS
# ACTION. Credentials never land here — only anonymous prefix hashes +
# timestamps; _LAST_REQUEST (bodies + auth headers) stays in-process.
#
# TWO-STATE SEMANTICS (2026-06-09 decision; replaces warm/cold/unknown): the
# expiry predicate IS the answer. warm = row exists AND expires_at > now;
# everything else is not-warm. Because the store is durable and stamps every
# response-confirmed cache event, absence ≈ expiry, so the compact-strip gate
# may act on absence without the old third 'unknown' state. The gates:
#   * ping  IFF warm   (anything else declines — never higher cost)
#   * strip IFF NOT warm ('cold'/'absent'); store 'error' or ledger 'off'
#     decline (can't judge -> take that gate's no-action path)
# warmth_state() still reports 'cold' (lapsed row not yet purged) vs 'absent'
# (no row) for OBSERVABILITY only — no gate distinguishes them.
#
# EXPIRY IS THE GC: correctness lives in the read predicate (expires_at > now),
# never in row deletion. The background sweep only reclaims disk space, with a
# generous slack, and may run late or never without changing any gate decision.
# (This deletes the old semantic sweeper, whose eager reaping at bare ttl erased
# the very cold-evidence the three-state compact gate needed — the bug that
# motivated the two-state redesign.)
WARMTH_LEDGER = os.environ.get("WARMTH_LEDGER", "1") not in ("0", "no", "off", "false")
WARMTH_LOG_FILE = os.environ.get("WARMTH_LOG_FILE", "1") not in ("0", "no", "off", "false")
WARMTH_PING_SENTINEL = os.environ.get("WARMTH_PING_SENTINEL")  # tail-msg marker => keep-warm ping
# A keep-warm ping exists to REFRESH a still-warm prefix before it lapses. On any
# NOT-warm prefix (lapsed, never seen, store error), forwarding the ping would
# cold-WRITE the discarded prefix at the write premium — the precise event the
# ping was meant to forestall, with nothing recovered. So forward IFF warm;
# everything else short-circuits with a synthetic end_turn (0 tokens).
WARMTH_BLOCK_COLD_PING = os.environ.get("WARMTH_BLOCK_COLD_PING") in (
    "1", "yes", "on", "true")
WARMTH_DB = os.environ.get("WARMTH_DB",
                           # repo root (next to the logproxy.py shim), NOT
                           # inside the proxylab/ package — pre-split default
                           str(Path(__file__).resolve().parent.parent / "warmth.sqlite"))
# Per-proxy-INSTANCE scope for persisted runtime state (holds, last-requests).
# Warmth is global by design (it lives on the content-addressed prefix lineage,
# shared across ports), but a hold/replayable-request belongs to THE proxy that
# serves the session — scoping rows by LOG_DIR keeps a scratch port from
# resurrecting (and double-pinging) the main proxy's sessions after a restart.
_OWNER = str(core_mod.LOG_DIR.resolve())
_DB = None
_DB_LOCK = threading.Lock()


def _warmth_db():
    """Lazily open (and initialize) the shared warmth store. One connection per
    process, serialized by _DB_LOCK (write volume is a few rows/sec at peak);
    WAL + busy_timeout make the file safely shareable across proxy processes."""
    global _DB
    with _DB_LOCK:
        if _DB is None:
            con = sqlite3.connect(WARMTH_DB, check_same_thread=False, timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("CREATE TABLE IF NOT EXISTS warmth ("
                        "hash TEXT PRIMARY KEY, stamped_at REAL NOT NULL, "
                        "ttl INTEGER NOT NULL, expires_at REAL NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS session_head ("
                        "session_id TEXT PRIMARY KEY, hash TEXT NOT NULL, "
                        "updated_at REAL NOT NULL)")
            # human-useful session identity for /_status: the CLI's own session
            # title (harvested from its title-generator side-call) + cwd + model.
            # Durable so /_status is useful right after a restart, when the
            # in-memory _LAST_REQUEST is empty.
            con.execute("CREATE TABLE IF NOT EXISTS session_meta ("
                        "session_id TEXT PRIMARY KEY, title TEXT, cwd TEXT, "
                        "model TEXT, first_seen REAL NOT NULL, "
                        "last_seen REAL NOT NULL)")
            # `kind` tags sessions the PROXY ITSELF spawned (auth bootstrap),
            # so the status/admin views can keep them out of the human's
            # session list. NULL = a real user session.
            try:
                con.execute("ALTER TABLE session_meta ADD COLUMN kind TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            # `ended_at`/`end_reason` (2026-06-11): the SessionEnd MARKER.
            # /_end no longer deletes runtime state — a one-shot `claude -p`
            # fires SessionEnd the instant its answer lands, and instant
            # teardown destroyed exactly the post-mortem state (context
            # stats, /_session view, last answer) worth debugging. Ended is
            # a durable FACT about the session (resumable — a live turn
            # clears it); cleanup belongs to the staleness sweeper.
            # `agent` (2026-06-12): the /agent/<name>/ route identity, for any
            # wire. SDK-driven sessions never make the title side-call, so the
            # route name is the only human-readable label they'll ever have;
            # /_status falls back to it when title is NULL. Plain (un-routed)
            # traffic stays NULL.
            for ddl in ("ALTER TABLE session_meta ADD COLUMN ended_at REAL",
                        "ALTER TABLE session_meta ADD COLUMN end_reason TEXT",
                        "ALTER TABLE session_meta ADD COLUMN agent TEXT"):
                try:
                    con.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already exists
            # RESTART-AMNESIA (open item h): per-proxy runtime state, mirrored on
            # every change and reloaded at startup, so a restart recovers what
            # the process held. owner = LOG_DIR (see _OWNER).
            con.execute("CREATE TABLE IF NOT EXISTS hold_state ("
                        "owner TEXT NOT NULL, session_id TEXT NOT NULL, "
                        "until REAL NOT NULL, armed_at REAL NOT NULL, "
                        "pings INTEGER NOT NULL, failures INTEGER NOT NULL, "
                        "last_ping_ts REAL, last_result TEXT, "
                        "PRIMARY KEY (owner, session_id))")
            # `hours` = the hold's INSURANCE WINDOW (2026-06-10): `until` slides
            # to last-organic-turn + hours, so the original until/armed_at pair
            # no longer encodes the duration. Migrate in place; legacy rows
            # (NULL) derive hours from (until - armed_at).
            try:
                con.execute("ALTER TABLE hold_state ADD COLUMN hours REAL")
            except sqlite3.OperationalError:
                pass  # column already exists
            # The replayable last request: BODY + NON-SECRET headers only. The
            # body is no more secret than the LOG_DIR captures (same
            # post-transform bytes); auth headers NEVER land on disk (standing
            # rule) — they are re-attached at runtime from the first live
            # request of the same account (_ACCOUNT_AUTH).
            con.execute("CREATE TABLE IF NOT EXISTS last_request ("
                        "owner TEXT NOT NULL, session_id TEXT NOT NULL, "
                        "account_uuid TEXT, path TEXT NOT NULL, ts REAL NOT NULL, "
                        "body TEXT NOT NULL, headers TEXT NOT NULL, "
                        "PRIMARY KEY (owner, session_id))")
            con.commit()
            _DB = con
    return _DB


def _warmth_rows(hashes):
    """{hash: (stamped_at, ttl, expires_at)} for the given hashes (one query).
    Raises on store failure — each caller maps that to its gate's safe default."""
    hashes = [h for h in hashes if h]
    if not hashes:
        return {}
    con = _warmth_db()
    with _DB_LOCK:
        q = ",".join("?" * len(hashes))
        cur = con.execute("SELECT hash, stamped_at, ttl, expires_at FROM warmth "
                          f"WHERE hash IN ({q})", hashes)
        return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def _session_head_hash(session):
    con = _warmth_db()
    with _DB_LOCK:
        r = con.execute("SELECT hash FROM session_head WHERE session_id=?",
                        (session,)).fetchone()
    return r[0] if r else None

def _strip_cache_control(node):
    """Deep copy of a message/content node with every `cache_control` removed, so
    an unchanged message hashes IDENTICALLY turn-over-turn (the rolling marker
    hops onto the new last message each turn — if we hashed it the 'same' history
    would change and a returning session would never match)."""
    if isinstance(node, dict):
        return {k: _strip_cache_control(v) for k, v in node.items()
                if k != "cache_control"}
    if isinstance(node, list):
        return [_strip_cache_control(v) for v in node]
    return node


def _canon_message(m):
    return json.dumps(_strip_cache_control(m), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8", "replace")


def _stable_sys_text(obj):
    """System text for the warmth fingerprint, EXCLUDING the volatile per-turn
    attribution block (`x-anthropic-billing-header: ... cch=N ...`) — it changes
    every turn but is out-of-band and does NOT participate in the prompt cache, so
    folding it in would make every turn's fingerprint differ (a guaranteed miss)."""
    sys = obj.get("system")
    if isinstance(sys, list):
        return " ".join(b.get("text", "") for b in sys if isinstance(b, dict)
                        and not b.get("text", "").startswith("x-anthropic-billing-header"))
    return sys or ""


def _sys_tools_fingerprint(obj):
    """A constant lead-in standing in for the tools+system prefix. Folding it in
    means a silent model / tool-set / system-prompt change invalidates the key
    (reads cold) instead of colliding with a different real cache entry."""
    tools = obj.get("tools") or []
    parts = [obj.get("model") or "",
             ",".join(sorted(t.get("name", "") for t in tools if isinstance(t, dict))),
             _stable_sys_text(obj)]
    return ("\x1f".join(parts)).encode("utf-8", "replace")


def _prefix_hash(obj, upto):
    """Chain-hash of the cacheable prefix: tools/system fingerprint + messages
    [0:upto], each canonicalized without cache_control. Simple full recompute
    (fast — blake2b over a few hundred KB is sub-ms); runs on the writer thread."""
    h = hashlib.blake2b(digest_size=20)
    h.update(_sys_tools_fingerprint(obj))
    for m in (obj.get("messages") or [])[:upto]:
        h.update(b"\x1e")
        h.update(_canon_message(m))
    return h.hexdigest()


def _marker_ttl(obj):
    """TTL (seconds) of the message-tail cache breakpoint: 3600 for ttl:'1h',
    else 300 (bare ephemeral). Falls back to the system markers."""
    for m in reversed(obj.get("messages") or []):
        c = m.get("content")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("cache_control"):
                    return 3600 if blk["cache_control"].get("ttl") == "1h" else 300
    sys = obj.get("system")
    if isinstance(sys, list):
        for b in sys:
            if isinstance(b, dict) and b.get("cache_control"):
                return 3600 if b["cache_control"].get("ttl") == "1h" else 300
    return 300


def _is_warm_ping(obj):
    """A recognized keep-warm ping: the LAST user message carries the sentinel.
    Such a turn refreshes the shared prefix but its own tail is throwaway, so we
    hash UP TO (not including) it."""
    if not WARMTH_PING_SENTINEL:
        return False
    for m in reversed(obj.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(
            b.get("text", "") for b in c if isinstance(b, dict)) if isinstance(c, list) else ""
        return WARMTH_PING_SENTINEL in text
    return False


def warmth_warm(hash_hex):
    """Read side (for a statusline/hook/keep-warm decision): is this prefix still
    warm? Anything other than 'warm' is not-warm."""
    return warmth_state(hash_hex) == "warm"


def warmth_state(hash_hex):
    """TWO-STATE for decisions, four labels for logs: 'warm' (row exists,
    expires_at > now) vs not-warm, where not-warm is reported as 'cold' (lapsed
    row still on disk awaiting purge), 'absent' (no row), 'off' (ledger
    disabled), or 'error' (store failure). GATES test == 'warm' only; the
    compact-strip gate additionally requires 'cold'/'absent' to act (so
    'off'/'error' decline — absence is evidence, a broken store is not)."""
    if not WARMTH_LEDGER:
        return "off"
    if not hash_hex:
        return "absent"
    try:
        r = _warmth_rows([hash_hex]).get(hash_hex)
    except Exception:
        return "error"
    if not r:
        return "absent"
    return "warm" if r[2] > time.time() else "cold"


def warmth_query(hash_hex=None, session=None):
    """Resolve warmth for the GET /_warm endpoint. By hash (content-addressed,
    fork-proof) or by session_id (convenience: resolves to that session's latest
    head hash, which a fork's keep-warm ping refreshes under the hood). Head
    index is in the store too, so this survives a proxy restart."""
    try:
        h = hash_hex or (_session_head_hash(session) if session else None)
        if not h:
            return {"found": False, "warm": False, "session": session, "hash": hash_hex}
        r = _warmth_rows([h]).get(h)
    except Exception as e:
        return {"found": False, "warm": False, "session": session,
                "hash": hash_hex, "error": f"store: {e}"}
    if not r:
        return {"found": False, "warm": False, "session": session, "hash": h}
    ts, ttl, exp = r
    now = time.time()
    return {"found": True, "warm": now < exp, "session": session, "hash": h,
            "age_s": round(now - ts, 1), "ttl_s": ttl,
            "remaining_s": round(max(0.0, exp - now), 1)}


def _cold_ping_decision(obj):
    """If this request is a keep-warm ping whose target prefix is NOT warm, return
    a decline record (caller short-circuits, never forwards). A ping only ever pays
    off on a WARM prefix (a cheap read that slides the TTL); on anything else
    (cold, absent, store error), forwarding is a cache WRITE at the premium for
    no gain — the higher cost the pinger exists to avoid. So forward IFF warm. Hash
    on the SAME basis `_record_warmth` uses for a ping (history up to, not
    including, the throwaway sentinel tail)."""
    if not WARMTH_BLOCK_COLD_PING or not _is_warm_ping(obj):
        return None
    msgs = obj.get("messages") or []
    upto = len(msgs) - 1                  # same as _record_warmth's ping path
    if upto <= 0:
        return None
    h = _prefix_hash(obj, upto)
    state = warmth_state(h)
    if state == "warm":
        return None                       # only a warm prefix is worth pinging
    return {"ping": True, "blocked": True, "warmth_state": state, "hash": h,
            "n_messages_hashed": upto,
            "note": f"declined ping: prefix is '{state}', not warm; forwarding "
                    "would write the prefix at the premium for no gain"}


def _record_warmth(obj, usage):
    """Refresh the ledger for the prefix this response just (re)cached, and return
    a small log record. Regular turn -> hash includes the last message (the entry
    the backend cached); ping -> excludes its throwaway tail.

    The stamp is RESPONSE-CONFIRMED: a row exists ONLY because the backend told us
    a cache does. We stamp iff usage confirms caching actually happened this turn
    (`cache_creation > 0` = just written, or `cache_read > 0` = read & TTL slid).
    A response with both zero (e.g. a sub-min-cacheable prefix the backend declined
    to cache) is NOT stamped — marking it 'warm' would be a lie, and a later ping
    would write rather than read. The request is mere intent; the response is the
    receipt. (This receipt discipline is what makes the two-state 'absence ≈
    expiry' reading honest.)"""
    if not WARMTH_LEDGER:
        return None
    msgs = obj.get("messages") or []
    if not msgs:
        return None
    created = (usage or {}).get("cache_creation_input_tokens") or 0
    read = (usage or {}).get("cache_read_input_tokens") or 0
    if created <= 0 and read <= 0:
        return None                       # no cache confirmed -> nothing to stamp
    ping = _is_warm_ping(obj)
    upto = len(msgs) - 1 if ping else len(msgs)
    if upto <= 0:
        return None
    h = _prefix_hash(obj, upto)
    ttl = _marker_ttl(obj)
    now = time.time()
    try:
        sid = (writer_mod._session_ids(obj) or (None,))[0]
    except Exception:
        sid = None
    try:
        con = _warmth_db()
        with _DB_LOCK:
            con.execute("INSERT INTO warmth(hash, stamped_at, ttl, expires_at) "
                        "VALUES(?,?,?,?) ON CONFLICT(hash) DO UPDATE SET "
                        "stamped_at=excluded.stamped_at, ttl=excluded.ttl, "
                        "expires_at=excluded.expires_at", (h, now, ttl, now + ttl))
            # a real turn advances this session's head; a fork's ping only
            # refreshes the shared hash above (its fork-id head is irrelevant).
            if sid and not ping:
                con.execute("INSERT INTO session_head(session_id, hash, updated_at) "
                            "VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                            "hash=excluded.hash, updated_at=excluded.updated_at",
                            (sid, h, now))
            con.commit()
            size = con.execute("SELECT COUNT(*) FROM warmth").fetchone()[0]
    except Exception as e:
        # A failed stamp must be LOUD: it silently degrades a warm prefix to
        # 'absent', which the compact gate now acts on.
        print(f"[warmth] STORE WRITE FAILED {h[:12]}…: {e}", flush=True)
        return None
    return {"hash": h, "ttl": ttl, "ts": round(now, 3), "ping": ping,
            "n_messages_hashed": upto, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": created,
            "warm_on_arrival": read > 0, "ledger_size": size}
