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

from proxylab import codex as codex_mod

# --- WORKBENCH INTENT DISPATCH (on by default; active only on /agent/ routes) --
# Absorbs the standalone wb proxy's response-side job (agent-workbench
# components/proxy/proxy.py): watch the assistant's SSE text for workbench
# intents (`[wb:action target] body…`) and POST each one to the workbench's
# /api/intent endpoint as soon as its body boundary is final. Parsing is
# delegated to the CANONICAL intent_parser.parse_intents from the workbench
# repo (single source of truth — proxy- and JSONL-sourced intents can never
# drift), loaded by file path so the repos stay decoupled.
#
# Scope guard: only requests that arrived via /agent/<name>/anthropic/… get a
# tee — intents must carry an agent identity, and plain Claude Code sessions
# (agent="ext") never dispatch. During an 8999→7800 chain phase the wb proxy
# strips the /agent prefix before forwarding, so chained traffic reads "ext"
# here and the wb proxy remains the single dispatch path (no double fire).
#
#   WB_INTENT_DISPATCH  default on; 0/empty disables
#   WB_URL              workbench API base (default http://127.0.0.1:9000)
#   WB_PARSER_TOKEN     bearer for /api/intent (same token the wb proxy uses)
#   WB_INTENT_PARSER    path to the workbench repo's intent_parser.py
WB_INTENT_DISPATCH = os.environ.get("WB_INTENT_DISPATCH", "1") not in ("", "0")
WB_URL = os.environ.get("WB_URL", "http://127.0.0.1:9000").rstrip("/")
WB_PARSER_TOKEN = os.environ.get("WB_PARSER_TOKEN", "")
WB_INTENT_PARSER = os.environ.get(
    "WB_INTENT_PARSER",
    str(Path.home() / "projects" / "agent-workbench" / "intent_parser.py"))
_WB_STATS = {"intents_dispatched": 0, "intent_dispatch_failures": 0}
# Short-timeout client isolated from the upstream pool so a slow /api/intent
# POST can never stall a streaming forward (same isolation the wb proxy used).
_wb_client = httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=False)


def _load_intent_parser(path):
    """Load parse_intents from the workbench checkout. Returns None on any
    failure (loud print when the feature is on) — the proxy must come up and
    forward traffic regardless of whether the workbench repo is present."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("wb_intent_parser", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.parse_intents
    except Exception as e:
        if WB_INTENT_DISPATCH:
            print(f"[wb] intent dispatch UNAVAILABLE — cannot load "
                  f"parse_intents from {path}: {e}", flush=True)
        return None


_parse_intents = _load_intent_parser(WB_INTENT_PARSER) if WB_INTENT_DISPATCH else None


async def _wb_post_intent(payload):
    headers = {"Content-Type": "application/json"}
    if WB_PARSER_TOKEN:
        headers["Authorization"] = f"Bearer {WB_PARSER_TOKEN}"
    try:
        resp = await _wb_client.post(WB_URL + "/api/intent",
                                     json=payload, headers=headers)
        if resp.status_code >= 400:
            _WB_STATS["intent_dispatch_failures"] += 1
            print(f"[wb] intent dispatch {payload['agent']}:{payload['action']} "
                  f"-> {resp.status_code} {resp.text[:200]}", flush=True)
        else:
            _WB_STATS["intents_dispatched"] += 1
    except Exception as e:
        _WB_STATS["intent_dispatch_failures"] += 1
        print(f"[wb] intent dispatch failed: {e}", flush=True)


def _wb_dispatch(payload):
    # Fire-and-forget: dispatcher failures log + count but never block (or
    # fail) client-bound response bytes.
    asyncio.create_task(_wb_post_intent(payload))


class _WbIntentTee:
    """Incremental SSE → intent dispatcher: port of the wb proxy's _SSETee +
    _IntentDispatcher as one class (logproxy forces identity encoding upstream,
    so there is no decompressor side here).

    Boundary discipline (canonical parser semantics): intent N's body runs to
    intent N+1's bracket or EOF, so an intent dispatches as soon as a LATER
    intent appears in the re-parse; the last one flushes on close(). Re-parsing
    the whole buffer per feed is O(N²) in turn size — fine for kB of text.
    """

    def __init__(self, agent, req_id, parse_fn=None, dispatch_fn=None,
                 wire="anthropic"):
        self.agent = agent
        self.req_id = req_id
        self.parse = parse_fn if parse_fn is not None else _parse_intents
        self.dispatch_fn = dispatch_fn if dispatch_fn is not None else _wb_dispatch
        self.wire = wire            # "anthropic" | "openai" delta dialect
        self.buf = bytearray()      # undecoded SSE bytes
        self.text = ""              # accumulated assistant text
        self.dispatched = 0
        self._closed = False

    def feed(self, chunk):
        if not chunk or self._closed:
            return
        self.buf.extend(chunk)
        got_text = False
        # SSE event framing is a blank-line separator: \n\n or \r\n\r\n.
        while True:
            i_lf = self.buf.find(b"\n\n")
            i_crlf = self.buf.find(b"\r\n\r\n")
            if i_crlf != -1 and (i_lf == -1 or i_crlf < i_lf):
                cut, blen = i_crlf, 4
            elif i_lf != -1:
                cut, blen = i_lf, 2
            else:
                break
            raw = bytes(self.buf[:cut]).decode("utf-8", "replace")
            del self.buf[:cut + blen]
            data_lines = [ln[5:].lstrip() for ln in raw.split("\n")
                          if ln.startswith("data:")]
            if not data_lines:
                continue
            try:
                obj = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue                 # incl. openai's bare "data: [DONE]"
            t = codex_mod._sse_text_delta(obj, self.wire)
            if t:
                self.text += t
                got_text = True
        if got_text:
            intents = self.parse(self.text)
            # All but the tail — the tail's body may still be growing.
            if len(intents) > self.dispatched + 1:
                for i in range(self.dispatched, len(intents) - 1):
                    self._send(i, *intents[i])
                self.dispatched = len(intents) - 1

    def _send(self, index, action, target, body):
        self.dispatch_fn({
            "agent": self.agent,
            "action": action,
            "target": target or "_",
            "body": body,
            "intent_id": f"proxy-{self.req_id}-{index:04d}-{uuid.uuid4().hex[:8]}",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def close(self):
        """Stream end — flush held intent(s). Runs from body_iter's finally,
        so it fires even when the upstream connection drops mid-stream."""
        if self._closed:
            return
        self._closed = True
        intents = self.parse(self.text)
        for i in range(self.dispatched, len(intents)):
            self._send(i, *intents[i])
        self.dispatched = len(intents)
