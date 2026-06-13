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

# Siblings resolved LAZILY through the package object: writer is imported
# early (warmth's header pulls it in) and eager `from proxylab import
# meta/pinger/warmth` here would cascade their import while warmth is still
# partial (pinger reads warmth.WARMTH_LEDGER at module level). The references
# below run only on the writer thread, long after the package finished loading.
import proxylab

# --- async disk writer --------------------------------------------------------
# The proxy must add no visible overhead, so NOTHING on the request/response
# byte-path touches the disk. The handler only enqueues (an O(1) put); a single
# background daemon thread does the mkdir + json.dumps + write. One thread keeps
# writes serialized (stable file ordering) and avoids thread-explosion.
NO_SESSION = "_no-session"          # bucket for requests that carry no session_id
_WRITE_Q: "queue.Queue" = queue.Queue()


def _writer_loop():
    while True:
        item = _WRITE_Q.get()
        try:
            if item is None:
                return
            path, kind, data = item
            # path may legitimately be None (ledger with WARMTH_LOG_FILE=0,
            # session-meta upserts) — the old unconditional mkdir crashed there
            # and the bare except silently dropped the WHOLE item, ledger stamp
            # included.
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "meta":  # session_meta upsert (no file output)
                sid, fields = data
                proxylab.meta._upsert_session_meta(sid, **fields)
            elif kind == "lastreq":   # mirror a replayable request (no secrets)
                proxylab.pinger._persist_last_request_row(*data)
            elif kind == "lastreq_del":
                proxylab.pinger._delete_last_request_row(data)
            elif kind == "bytes":
                path.write_bytes(data)
            elif kind == "append":  # one JSON object per line (canary change-log)
                with path.open("a") as fh:
                    fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            elif kind == "ledger":  # hash+touch the prefix-warmth ledger off-thread
                obj, usage = data
                rec = proxylab.warmth._record_warmth(obj, usage)
                if rec is not None:
                    segs = rec.get("segments") or {}
                    seg_s = ("".join(f" {k}={v['hash'][:6]}"
                                     for k, v in segs.items())) if segs else ""
                    print(f"[warmth] {rec['hash'][:12]} ttl={rec['ttl']}s "
                          f"{'PING' if rec['ping'] else 'turn'} "
                          f"warm_on_arrival={rec['warm_on_arrival']} "
                          f"(ledger={rec['ledger_size']}){seg_s}", flush=True)
                    if path is not None:
                        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
            else:  # "json" — serialize off the event loop, in this thread
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass
        finally:
            _WRITE_Q.task_done()


_writer_thread = threading.Thread(target=_writer_loop, name="logwriter", daemon=True)
_writer_thread.start()


def _flush_writes():
    """Drain pending writes on shutdown so no captures are lost."""
    try:
        _WRITE_Q.join()
        _WRITE_Q.put(None)
        _writer_thread.join(timeout=5)
    except Exception:
        pass


atexit.register(_flush_writes)


def _enqueue_json(path: Path, obj):
    _WRITE_Q.put((path, "json", obj))


def _enqueue_bytes(path: Path, blob: bytes):
    _WRITE_Q.put((path, "bytes", blob))


def _enqueue_append(path: Path, obj):
    _WRITE_Q.put((path, "append", obj))


def _enqueue_ledger(path, obj, usage):
    """Hand the (post-transform) request body + response usage to the writer
    thread, which hashes the cacheable prefix and refreshes the warmth ledger.
    `path` (a <stem>.warmth.json) is written too when WARMTH_LOG_FILE is on."""
    _WRITE_Q.put((path, "ledger", (obj, usage)))


def _enqueue_meta(session_id, **fields):
    """Upsert session_meta (title/cwd/model/last_seen) on the writer thread —
    the SQLite write never runs on the byte hot path."""
    if session_id:
        _WRITE_Q.put((None, "meta", (session_id, fields)))


def _enqueue_last_request(session_id, account_uuid, path, ts, obj, safe_headers):
    """Mirror a session's replayable last request to SQLite on the writer thread
    (JSON serialization of a multi-hundred-KB body stays off the event loop).
    obj is not reused after its turn, so passing the ref is safe."""
    _WRITE_Q.put((None, "lastreq",
                  (session_id, account_uuid, path, ts, obj, safe_headers)))


def _enqueue_last_request_delete(session_id):
    _WRITE_Q.put((None, "lastreq_del", session_id))


def _session_ids(obj):
    """Parse session_id/account_uuid/device_id out of metadata.user_id.

    metadata.user_id is itself a JSON STRING, e.g.
    '{"device_id":"…","account_uuid":"…","session_id":"…"}'. Only `messages`
    requests carry it; count_tokens/probes do not (-> NO_SESSION bucket)."""
    uid = (obj.get("metadata") or {}).get("user_id")
    if not uid:
        return None, None, None
    try:
        d = json.loads(uid)
        return d.get("session_id"), d.get("account_uuid"), d.get("device_id")
    except Exception:
        return None, None, None


def _sys_text(obj):
    sys = obj.get("system")
    if isinstance(sys, list):
        return " ".join(b.get("text", "") for b in sys if isinstance(b, dict))
    return sys or ""


# Roles that _classify_role assigns to TASK-spawned subagents. They share the
# parent's session_id on the wire (one session dir holds parent + every sub), so
# anything keyed by session_id (the /_status row, the replayable last request,
# the hold anchor) must NOT be overwritten by a subagent turn — the main agent
# is the durable, pingable line; subagents are transient. "parent"/"unknown" are
# the main line. See server.py + meta._capture_session_meta.
SUBAGENT_ROLES = frozenset({"Plan", "verification", "general-purpose"})


def _is_subagent_role(role):
    return role in SUBAGENT_ROLES


def _classify_role(obj):
    """Infer the agent role from the system-prompt signature."""
    s = _sys_text(obj)
    if "software architect and planning" in s:
        return "Plan"
    if "verification specialist" in s:
        return "verification"
    if "agent for Claude Code" in s or "Searching for code" in s:
        return "general-purpose"
    if "Claude Code" in s:
        return "parent"
    return "unknown"


def _short_model(m):
    if not m:
        return "nomodel"
    return (m or "").replace("claude-", "").replace("[1m]", "").replace(".", "-")[:24]
