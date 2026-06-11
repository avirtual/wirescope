"""Offline checks for the subscriber push feed (SUBSCRIBERS.md, 2026-06-12).

Covers what a live drill can't cheaply pin down:
  * registration validation (loopback guard, glob/event checks), upsert-by-url
    semantics (id stability, suspension reset), unsubscribe by url/id
  * matching: agent globs, event filter, suspended exclusion, the structural
    'ext' (plain Claude Code traffic) exclusion
  * the SSE tee: framing, dialect decode, offset bookkeeping, coalescing,
    tail flush on close
  * delivery accounting: consecutive-failure suspension + reactivation
  * persistence: registrations (incl. token + suspended flag) survive a
    simulated proxy restart
  * turn.completed assembly: receipts pass through, totals subset, flags

Run: python3 test_subscribers.py   (uses throwaway tmp dirs; no live ports)
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="substest_logs_")
os.environ["WARMTH_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="substest_db_"), "warmth.sqlite")
os.environ["SUBSCRIBERS"] = "1"

import logproxy as lp  # noqa: E402  (env must be set before import)
from proxylab import subs  # noqa: E402

FAILS = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def reg(url="http://127.0.0.1:9000/events", agents=("wb-*",),
        events=("text.delta", "turn.completed", "session.ended"), **kw):
    return subs.subscribe({"url": url, "agents": list(agents),
                           "events": list(events), **kw})


# --- registration validation ---------------------------------------------------
code, body = subs.subscribe({"agents": ["x"], "events": ["text.delta"]})
check("missing url rejected", code == 400)
code, body = reg(url="ftp://127.0.0.1/x")
check("non-http url rejected", code == 400)
code, body = reg(url="http://10.0.0.5:9000/events")
check("non-loopback url rejected by default", code == 400 and "loopback" in body["error"])
code, body = reg(agents=[])
check("empty agents rejected", code == 400)
code, body = reg(events=["text.delta", "nope"])
check("unknown event type rejected", code == 400)
code, body = reg(name="wb", token="s3cret")
check("valid registration accepted", code == 200 and body["ok"])
check("token never echoed", "token" not in body and body["has_token"] is True)
sub_id = body["id"]

# --- upsert by url ---------------------------------------------------------------
code, body = reg(name="wb2", agents=("wb-*", "clodex-*"))
check("re-POST same url upserts (id stable)", code == 200 and body["id"] == sub_id)
check("upsert replaces fields", body["name"] == "wb2"
      and body["agents"] == ["wb-*", "clodex-*"])
check("registry holds one entry", len(subs.list_subscribers()) == 1)

# --- matching --------------------------------------------------------------------
check("glob matches agent", len(subs._match("wb-alice", "text.delta")) == 1)
check("second glob matches too", len(subs._match("clodex-bob", "turn.completed")) == 1)
check("non-matching agent ignored", subs._match("other-x", "text.delta") == [])
check("matching is case-sensitive on every platform",
      subs._match("WB-Alice", "text.delta") == [])
check("prefix alone does not match without the glob",
      subs._match("wb", "text.delta") == [])
check("ext (plain CC traffic) never matches even vs *",
      (reg(url="http://127.0.0.1:9001/all", agents=("*",))[0] == 200
       and subs._match("ext", "text.delta") == []))
check("None agent never matches", subs._match(None, "text.delta") == [])
subs.unsubscribe(url="http://127.0.0.1:9001/all")
code, body = reg(events=("turn.completed",))
check("event filter respected", subs._match("wb-alice", "text.delta") == []
      and len(subs._match("wb-alice", "turn.completed")) == 1)
code, body = reg()  # back to all three events

# --- unsubscribe -----------------------------------------------------------------
check("unsubscribe by id", subs.unsubscribe(sub_id=sub_id) is True
      and subs.list_subscribers() == [])
check("unsubscribe unknown is False", subs.unsubscribe(url="http://127.0.0.1:9/x") is False)
code, body = reg(name="wb", token="s3cret")
sub_id = body["id"]

# --- the SSE tee -----------------------------------------------------------------
SENT = []
_real_dispatch = subs.dispatch
subs.dispatch = lambda ev, agent, sid, rid, data, subs=None: SENT.append(
    (ev, agent, sid, rid, data)) or 1
subs.SUBSCRIBERS_DELTA_MS = 0          # flush on every feed that adds text


def sse(ev_obj):
    return f"data: {json.dumps(ev_obj)}\n\n".encode()


tee = subs._SubTee("wb-alice", "sess-1", "001-120000")
tee.feed(sse({"type": "message_start", "message": {"usage": {}}}))
check("non-text events emit nothing", SENT == [] and tee.text == "")
tee.feed(sse({"type": "content_block_delta",
              "delta": {"type": "text_delta", "text": "Hello "}}))
check("text delta flushed", len(SENT) == 1
      and SENT[0][4] == {"provider": "anthropic", "text": "Hello ", "offset": 0})
# split one SSE event across two feeds: no flush until the frame completes
half = sse({"type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "world"}})
tee.feed(half[:10])
check("partial SSE frame buffered", len(SENT) == 1)
tee.feed(half[10:])
check("completed frame flushed with offset", len(SENT) == 2
      and SENT[1][4] == {"provider": "anthropic", "text": "world", "offset": 6})
tee.close()
check("close with nothing pending emits nothing", len(SENT) == 2)
check("tee accumulated full text", tee.text == "Hello world")

# coalescing: a large window holds deltas until close
subs.SUBSCRIBERS_DELTA_MS = 10 ** 9
SENT.clear()
tee = subs._SubTee("wb-alice", "sess-1", "002-120001")
tee.feed(sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a"}}))
tee.feed(sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "b"}}))
check("coalescing window holds deltas", SENT == [])
tee.close()
check("close flushes the accumulated tail", SENT and SENT[0][4]["text"] == "ab"
      and SENT[0][4]["offset"] == 0)
tee.close()
check("double close emits once", len(SENT) == 1)
subs.SUBSCRIBERS_DELTA_MS = 0

# openai dialect
SENT.clear()
tee = subs._SubTee("wb-alice", "sess-2", "003-120002", wire="openai")
tee.feed(sse({"type": "response.output_text.delta", "delta": "codex says"}))
tee.feed(b"data: [DONE]\n\n")
tee.close()
check("openai dialect decoded", SENT and SENT[0][4]["provider"] == "openai"
      and SENT[0][4]["text"] == "codex says")

# --- _tee_for gating -------------------------------------------------------------
check("tee created for matching agent",
      subs._tee_for("wb-alice", "sess-1", "004-1") is not None)
check("no tee for unmatched agent",
      subs._tee_for("stranger", "sess-x", "005-1") is None)
check("no tee for ext", subs._tee_for("ext", "sess-y", "006-1") is None)
check("note_session recorded for unmatched agent too",
      subs._SESSION_AGENT.get("sess-x") == "stranger")

# --- turn.completed assembly -------------------------------------------------------
SENT.clear()
META = {"message_id": "msg_1", "resolved_model": "claude-fable-5",
        "tool_uses": ["Bash"], "text": "capped"}
BILL = {"model": "claude-fable-5", "est_usd": 0.031, "unpriced": False,
        "tokens": {"input_tokens": 12, "output_tokens": 480,
                   "cache_read_input_tokens": 154000,
                   "cache_write_5m_tokens": 2100, "cache_write_1h_tokens": None,
                   "cache_write_flat_tokens": None, "thinking_tokens": 7,
                   "service_tier": "standard"}}
STOP = {"stop_reason": "end_turn", "stop_details": None,
        "request_id": "req_abc", "is_turn": True}
TOTALS = {"requests": 31, "turns": 7, "refusals": 0, "input_tokens": 1,
          "output_tokens": 2, "cache_read_tokens": 3, "cache_write_tokens": 4,
          "est_usd": 1.27, "refusal_events": [{"private": "internal"}],
          "unpriced_models": []}
n = subs.emit_turn_completed_anthropic(
    "wb-alice", "sess-1", "007-120010", meta=META, bill=BILL, stop=STOP,
    status_code=200, text="full text", role="parent", title_call=False,
    session_totals=TOTALS,
    context={"turns_in_context": 7, "n_messages": 41,
             "max_tool_result_chars": 52000, "ts": 1.0})
d = SENT[0][4] if SENT else {}
check("turn.completed dispatched", n == 1 and SENT[0][0] == "turn.completed")
check("receipts pass through", d.get("usage") == BILL["tokens"]
      and d.get("cost") == {"est_usd": 0.031, "unpriced": False}
      and d.get("anthropic_request_id") == "req_abc"
      and d.get("model") == "claude-fable-5")
check("turn flags", d.get("turn_end") is True and d.get("refusal") is False
      and d.get("title_call") is False and d.get("text") == "full text")
check("session_totals is the documented subset",
      d.get("session_totals", {}).get("est_usd") == 1.27
      and "refusal_events" not in d.get("session_totals", {})
      and "unpriced_models" not in d.get("session_totals", {}))
check("context strips ts", d.get("context") == {"turns_in_context": 7,
      "n_messages": 41, "max_tool_result_chars": 52000})
check("warmth advisory present (absent session -> warm falsy)",
      isinstance(d.get("warmth"), dict) and not d["warmth"]["warm"])

SENT.clear()
STOP_R = {"stop_reason": "refusal", "stop_details": {"category": "x"},
          "request_id": "req_r", "is_turn": True}
subs.emit_turn_completed_anthropic(
    "wb-alice", "sess-1", "008-1", meta=META, bill=BILL, stop=STOP_R,
    status_code=200, text="", role="parent", title_call=False,
    session_totals=None, context=None)
check("refusal surfaced", SENT[0][4]["refusal"] is True
      and SENT[0][4]["stop_details"] == {"category": "x"})

SENT.clear()
subs.emit_turn_completed_openai(
    "wb-alice", "sess-2", "009-1", status_code=200, text="codex full",
    meta={"resolved_model": "gpt-5.1-codex", "response_id": "resp_1",
          "status": "completed",
          "usage": {"input_tokens": 9000, "output_tokens": 300,
                    "input_tokens_details": {"cached_tokens": 8700},
                    "output_tokens_details": {"reasoning_tokens": 120}}})
d = SENT[0][4]
check("openai turn.completed shape", d["provider"] == "openai"
      and d["usage"] == {"input_tokens": 9000, "output_tokens": 300,
                         "cached_tokens": 8700, "reasoning_tokens": 120}
      and d["cost"] is None and d["warmth"] is None)

# --- session.ended ----------------------------------------------------------------
SENT.clear()
subs.note_session("wb-alice", "sess-9")
check("session.ended routed via session->agent map",
      subs.emit_session_ended("sess-9", "clear") == 1
      and SENT[0][0] == "session.ended" and SENT[0][4] == {"reason": "clear"})
check("unknown session ended is silent", subs.emit_session_ended("nope", "x") == 0)

subs.dispatch = _real_dispatch

# --- dispatch without a running loop drops + counts --------------------------------
before = subs._STATS["dropped_no_loop"]
n = subs.dispatch("text.delta", "wb-alice", "s", "r", {"text": "x", "offset": 0})
check("off-loop dispatch drops and counts", n == 0
      and subs._STATS["dropped_no_loop"] == before + 1)

# --- delivery accounting: suspension + reactivation --------------------------------
class _StubResp:
    def __init__(self, code):
        self.status_code, self.text = code, "err"


class _StubClient:
    def __init__(self, codes):
        self.codes = list(codes)

    async def post(self, url, json=None, headers=None):
        c = self.codes.pop(0)
        if c == "boom":
            raise RuntimeError("connection refused")
        return _StubResp(c)


subs.SUBSCRIBERS_MAX_FAILURES = 3
_real_client = subs._sub_client
sub = next(s for s in subs._SUBS.values())
env = {"v": 1, "event": "text.delta", "agent": "wb-alice", "session_id": "s",
       "request_id": "r", "ts": "t", "data": {}}

subs._sub_client = _StubClient([500, "boom", 200, 500, "boom", 503])
asyncio.run(subs._deliver(sub, env))
asyncio.run(subs._deliver(sub, env))
check("failures accumulate", sub["failures"] == 2 and not sub["suspended"])
asyncio.run(subs._deliver(sub, env))
check("success resets the streak", sub["failures"] == 0 and sub["delivered"] == 1)
asyncio.run(subs._deliver(sub, env))
asyncio.run(subs._deliver(sub, env))
asyncio.run(subs._deliver(sub, env))
check("suspended at threshold", sub["suspended"] is True and sub["failures"] == 3)
check("suspended drops out of matching", subs._match("wb-alice", "text.delta") == [])
code, body = reg(name="wb", token="s3cret")
check("re-registration reactivates", body["suspended"] is False
      and len(subs._match("wb-alice", "text.delta")) == 1)
subs._sub_client = _real_client

# --- persistence across a simulated restart ----------------------------------------
con = sqlite3.connect(os.environ["WARMTH_DB"])
row = con.execute("SELECT id, name, token, suspended FROM subscribers "
                  "WHERE url=?", ("http://127.0.0.1:9000/events",)).fetchone()
check("registration persisted", row is not None and row[0] == sub_id
      and row[1] == "wb" and row[2] == "s3cret" and row[3] == 0)
con.close()

subs._SUBS.clear()
check("restore reloads registrations", subs._load_subscribers() >= 1
      and subs._SUBS["http://127.0.0.1:9000/events"]["token"] == "s3cret"
      and subs._SUBS["http://127.0.0.1:9000/events"]["id"] == sub_id)
check("restored registration matches again",
      len(subs._match("wb-alice", "turn.completed")) == 1)

subs.unsubscribe(url="http://127.0.0.1:9000/events")
con = sqlite3.connect(os.environ["WARMTH_DB"])
check("unsubscribe unpersists", con.execute(
    "SELECT COUNT(*) FROM subscribers").fetchone()[0] == 0)
con.close()

# --- summary ------------------------------------------------------------------------
print(f"\n{len(FAILS)} failure(s)" if FAILS else
      "\nALL CHECKS PASSED")
sys.exit(1 if FAILS else 0)
