"""Minimal standalone logging/dumper forward-proxy (throwaway, #2873/#2874).

Captures the EXACT outbound request bodies AND inbound response SSE streams a
`claude` CLI exchanges with the Anthropic API — ground truth for "what is in a
subagent's context" (#2873, request side) and "what usage/cost the API returns"
(#2874, response side). No model introspection.

NOT the production proxy: no intent dispatcher, no workbench contact whatsoever.
It only logs request + response and forwards bytes.

Output layout (one subdirectory per session):
  LOG_DIR/<session_id>/<seq>-<agent>-<role>-<model>-<ts>.request.json
  LOG_DIR/<session_id>/<seq>-...-.response.sse | .response.json
  LOG_DIR/<session_id>/_session.json   per-session running total
  LOG_DIR/_no-session/...              count_tokens + probes (carry no metadata)
  LOG_DIR/_totals.json                 global process-lifetime total
The session_id is parsed out of metadata.user_id (itself a JSON string). All
disk writes are handed to a background thread so the proxy byte-path never
blocks on I/O (see _writer_loop).

Run:
  LOG_DIR=/tmp/proxyclone/logs python3 -m uvicorn logproxy:app --host 127.0.0.1 --port 7799

Point a CLI at it either way:
  - bare:   ANTHROPIC_BASE_URL=http://127.0.0.1:7799            -> path /v1/messages
  - routed: ANTHROPIC_BASE_URL=http://127.0.0.1:7799/agent/<name>/anthropic
            (the /agent/<name>/anthropic prefix is stripped before forwarding;
             <name> is captured as the agent id in the dump filename)
"""
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

UPSTREAM = "https://api.anthropic.com"
LOG_DIR = Path(os.environ.get("LOG_DIR", "/tmp/proxyclone/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# hop-by-hop + accept-encoding (we want an uncompressed SSE stream we can read)
_HOP = {"host", "content-length", "connection", "transfer-encoding",
        "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
        "trailers", "upgrade", "accept-encoding"}

_counter = itertools.count(1)
_START_TS = time.time()


def _detect_version():
    """Self-identify the running code so /_status//_admin say WHICH release
    serves a port (the handoff-notes way went stale within a day). Release
    worktrees carry a RELEASE stamp written by release.sh; a dev tree falls
    back to git describe and says so."""
    root = Path(__file__).resolve().parent.parent
    try:
        stamp = (root / "RELEASE").read_text().strip()
        if stamp:
            return stamp
    except OSError:
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=3)
        desc = out.stdout.strip()
        if out.returncode == 0 and desc:
            # an exact clean tag (old release worktrees predate the RELEASE
            # stamp) is the release itself; anything else is a dev state
            exact = "-g" not in desc and not desc.endswith("-dirty")
            return desc if exact else desc + " (dev tree)"
    except Exception:
        pass
    return "unknown"


VERSION = _detect_version()
print(f"[proxy] code version: {VERSION}", flush=True)
_client = httpx.AsyncClient(timeout=httpx.Timeout(600.0), follow_redirects=False)

# /agent/<name>/anthropic/<rest>  ->  (name, /<rest>)
_ROUTE = re.compile(r"^/agent/(?P<name>[A-Za-z0-9_.-]+)/anthropic(?P<rest>/.*)?$")

# Inbound transport identity. The body's session_id is absent on count_tokens
# pre-flights, but the CLI stamps these on EVERY request, and they identify the
# calling CLI process/build/account (the "who sent this" the metadata omits).
# Secrets are redacted — never write the caller's API key to disk.
_SECRET_HEADERS = {"authorization", "x-api-key", "cookie", "proxy-authorization",
                   "chatgpt-account-id", "openai-api-key"}


def _safe_headers(headers):
    return {k: ("<redacted>" if k.lower() in _SECRET_HEADERS else v)
            for k, v in headers.items()}
