#!/usr/bin/env python3
"""Meta / Muse Code provider (`proxylab/muse.py`, `/agent/<name>/meta`, root
`/muse-code/*`).

WHY THIS SUITE EXISTS (2026-09-22). Muse speaks the same OpenAI Responses API
codex does, so the model call rides the codex handler with a provider switch.
Everything that DIFFERS was read off the real 1.3.0 binary — first fronted by a
stub, then live against Meta on 2026-09-22 (fixtures/muse/ = the live catalog +
subscription event) — and each difference is a way to be silently wrong rather
than loudly broken:

  * the model catalog is fetched at the ORIGIN with the --base-url prefix
    DROPPED, and a 4xx there aborts the run before any model call — so a proxy
    that only routes /agent/<name>/meta never sees a single turn;
  * the LIVE catalog carries NO prices (my stub had guessed a `cost` block
    from the MSP schema — the first real fetch caught it): the roster and
    limits are learned, the published list price is the table, and a cost
    block is parsed only if one ever appears — never assumed;
  * every stream carries the plan quota (`response.subscription_usage`);
  * one user prompt is 1 conversation call + N reminder-observer side-calls,
    each with its OWN x-tbh-session-id, linked to the parent only by the 8-char
    fragment in `prompt_cache_key` — file them under the parent, price them
    apart, and never let one count as a turn or overwrite the session's view;
  * tools arrive as ONE namespace wrapper — a roster that counts 1 tool is a
    roster that says nothing.

Behavioural: a stub upstream (http.server thread) serves the captured catalog
and SSE; the app is driven through starlette's TestClient; assertions read the
totals, receipts, /_status, /_context and the subscriber envelopes.
"""
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "muse"
CATALOG = (FIX / "catalog.json").read_bytes()
SSE = (FIX / "response.sse").read_bytes()
SAMPLE = json.loads((FIX / "request.json").read_text())

# ---- stub upstream: catalog at the origin, SSE on any …/responses ----------
STUB_SEEN = []


class _Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _seen(self, body=None):
        STUB_SEEN.append({"method": self.command, "path": self.path,
                          "headers": {k.lower(): v for k, v in self.headers.items()},
                          "body": body})

    def do_GET(self):
        self._seen()
        if self.path.startswith("/muse-code/models"):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(CATALOG)))
            self.end_headers()
            self.wfile.write(CATALOG)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n)
        self._seen(raw)
        if self.path.rstrip("/").endswith("/responses"):
            # the codex regression arm (session-id header) gets the same
            # stream under a codex model id, so it prices off PRICES_OPENAI
            sse = (SSE.replace(b"muse-spark-1.3", b"gpt-5.4")
                   if self.headers.get("session-id") else SSE)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(sse)))
            self.end_headers()
            self.wfile.write(sse)
            return
        self.send_response(404)
        self.end_headers()


with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
_srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), _Stub)
threading.Thread(target=_srv.serve_forever, daemon=True).start()

LOG_DIR = tempfile.mkdtemp(prefix="muse-logs-")
os.environ["LOG_DIR"] = LOG_DIR
os.environ["WARMTH_DB"] = os.path.join(tempfile.mkdtemp(prefix="muse-db-"), "w.sqlite")
os.environ["UPSTREAM_META"] = f"http://127.0.0.1:{PORT}/v1"
os.environ["UPSTREAM_OPENAI"] = f"http://127.0.0.1:{PORT}"     # codex regression arm
os.environ["SUBSCRIBERS"] = "1"

import logproxy as lp  # noqa: E402
from proxylab import billing as billing_mod  # noqa: E402
from proxylab import muse as muse_mod  # noqa: E402
from proxylab import status as status_mod  # noqa: E402
from proxylab import subs as subs_mod  # noqa: E402
from proxylab import writer as writer_mod  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def flush():
    writer_mod._WRITE_Q.join()


# subscriber envelopes are captured synchronously (delivery itself is
# fire-and-forget on the loop and not what is under test here)
ENVELOPES = []
_real_dispatch = subs_mod.dispatch


def _capture_dispatch(event, agent, session_id, request_id, data, subs=None):
    ENVELOPES.append({"event": event, "agent": agent, "session_id": session_id,
                      "data": data})
    return 1


subs_mod.dispatch = _capture_dispatch
code, _ = subs_mod.subscribe({"url": "http://127.0.0.1:1/cb", "agents": ["*"],
                              "events": ["text.delta", "turn.completed"]})
assert code == 200, code

c = TestClient(lp.app)
BODY = SAMPLE["body"]
SID = BODY["prompt_cache_key"].split(":")[-1]
HDRS = {k: v for k, v in SAMPLE["headers"].items()
        if k not in ("host", "content-length", "accept-encoding")}
HDRS["authorization"] = "Bearer test-key"

# ---- 1. catalog: origin passthrough + prices learned off the wire ----------
print("[catalog]")
seed = dict(muse_mod.PRICES_META.get("muse-spark-1.3") or {})
r = c.get("/muse-code/models", headers={"authorization": "Bearer test-key",
                                        "x-client-id": "tbh:exec"})
check("root /muse-code/models proxied (200)", r.status_code == 200, r.status_code)
check("catalog body passes through verbatim",
      r.json().get("data", [{}])[0].get("id") == "muse-spark-1.3-contributor")
check("stub saw the catalog at the ORIGIN path (no /v1, no agent prefix)",
      STUB_SEEN[-1]["path"] == "/muse-code/models", STUB_SEEN[-1]["path"])
check("bearer forwarded to the origin",
      STUB_SEEN[-1]["headers"].get("authorization") == "Bearer test-key")
p = muse_mod.PRICES_META.get("muse-spark-1.3") or {}
# the LIVE catalog (fixture = the real 2026-09-22 body) carries NO cost rows:
# the roster + limits are learned, the published list price stays the table
check("catalog roster learned (4 live model ids)",
      muse_mod._MUSE_STATS["catalog_models"] == ["muse-spark-1.3-contributor", "muse-spark-1.3",
                                                 "muse-spark-1.2-contributor", "muse-spark-1.2"],
      muse_mod._MUSE_STATS["catalog_models"])
check("context/output limits learned off the catalog",
      muse_mod.CATALOG_LIMITS.get("muse-spark-1.3") == {"context": 1007997, "output": 128000})
check("no cost rows on the live catalog -> published seed row untouched", p == seed and p.get("source") is None)
check("every catalog model is priced by longest prefix (-contributor -> base row)",
      all(billing_mod._price_for(m, table=muse_mod.PRICES_META) for m in muse_mod._MUSE_STATS["catalog_models"]))
check("catalog persisted for restart reload",
      (Path(LOG_DIR) / "_meta_catalog.json").exists())
check("learn_catalog rejects an unknown shape without touching the table",
      muse_mod.learn_catalog({"models": [{"id": "x", "cost": {"input": "9"}}]}) == {"models": 0, "priced": 0}
      and "x" not in muse_mod.PRICES_META)
check("learn_catalog skips a cost block it cannot parse (row still on the roster)",
      muse_mod.learn_catalog({"data": [{"id": "y", "cost": {"input": "n/a", "output": "1"}}]}) == {"models": 1, "priced": 0}
      and "y" not in muse_mod.PRICES_META)
check("a catalog cost block, when one appears, overrides the seed (source=catalog)",
      muse_mod.learn_catalog({"data": [{"id": "muse-spark-9", "metadata": {"muse-code": {
          "cost": {"input": "2", "output": "8", "cached": "0.2"}}}}]}) == {"models": 1, "priced": 1}
      and muse_mod.PRICES_META["muse-spark-9"] == {"in": 2.0, "cached_in": 0.2, "out": 8.0,
                                                   "currency": "USD", "source": "catalog"})
muse_mod.PRICES_META.pop("muse-spark-9", None)
muse_mod.learn_catalog(json.loads(CATALOG))       # restore the live roster
check("stats count the product request", muse_mod._MUSE_STATS["product_requests"] == 1)
flush()
cat_files = list((Path(LOG_DIR) / writer_mod.NO_SESSION).glob("*-ext-muse-catalog-*.response.json"))
check("catalog response captured under the no-session bucket", len(cat_files) == 1)
if cat_files:
    rec = json.loads(cat_files[0].read_text())
    check("capture records what was learned", rec.get("catalog_learned") == {"models": 4, "priced": 0})
    check("capture redacts the bearer",
          rec["request_headers"].get("authorization") != "Bearer test-key")

# ---- 2. the conversation line -----------------------------------------------
print("[main line]")
r = c.post("/agent/probe/meta/responses", content=json.dumps(BODY).encode(),
           headers=HDRS)
check("POST /agent/<name>/meta/responses -> 200", r.status_code == 200, r.status_code)
check("SSE bytes reach the client verbatim", b"hi there" in r.content and b"response.completed" in r.content)
up = STUB_SEEN[-1]
check("forwarded to UPSTREAM_META + path (the /v1 the CLI dropped is restored)",
      up["path"] == "/v1/responses", up["path"])
check("bearer forwarded untouched (no chatgpt rewrite on this wire)",
      up["headers"].get("authorization") == "Bearer test-key")
check("x-tbh-session-id forwarded", up["headers"].get("x-tbh-session-id") == SID)
check("body forwarded byte-identical", up["body"] == json.dumps(BODY).encode())
flush()
tot = billing_mod._SESSION_TOTALS.get(SID) or {}
check("session keyed by x-tbh-session-id", tot.get("requests") == 1, tot.get("requests"))
check("completed response with text = one turn", tot.get("turns") == 1)
# usage in the fixture: 100 in (50 cached), 5 out; catalog 1.00 / 0.10 / 5.00
check("priced off the list-price table: 50*1.25 + 50*0.15 + 5*4.25 per M",
      abs((tot.get("est_usd") or 0) - 0.000092) < 1.5e-6, tot.get("est_usd"))
check("uncached input split from cached (anthropic totals semantics)",
      tot.get("input_tokens") == 50 and tot.get("cache_read_tokens") == 50)
check("no unpriced request", not tot.get("unpriced_requests"))
check("by_line bucket main present (cost_by_line works on this wire)",
      (tot.get("by_line") or {}).get("main", {}).get("requests") == 1)
sess_dir = Path(LOG_DIR) / SID
reqs = list(sess_dir.glob("*-probe-muse-*.request.json"))
resps = list(sess_dir.glob("*-probe-muse-*.response.json"))
check("request captured with the muse stem tag", len(reqs) == 1, [p.name for p in sess_dir.glob("*")])
check("response receipt captured", len(resps) == 1)
if reqs and resps:
    rq = json.loads(reqs[0].read_text())
    rs = json.loads(resps[0].read_text())
    check("request record provider=meta", rq.get("provider") == "meta")
    check("request summary: sidecall null on the conversation line",
          rq["summary"].get("sidecall") is None)
    check("request summary counts the UNWRAPPED functions",
          rq["summary"].get("n_functions") == 29 and rq["summary"].get("n_tools") == 1,
          rq["summary"].get("n_functions"))
    check("request capture redacts the bearer",
          rq["request_headers"].get("authorization") != "Bearer test-key")
    check("receipt provider=meta", rs.get("provider") == "meta")
    check("receipt price_basis says list price and why (catalog has no cost)",
          "list price" in (rs.get("billing") or {}).get("price_basis", ""))
    check("receipt carries the stream's subscription snapshot",
          (rs.get("subscription") or {}).get("window", {}).get("window_duration_mins") == 300
          and "at" in rs["subscription"], rs.get("subscription"))
    check("receipt usage carries cached_tokens",
          (rs.get("usage") or {}).get("input_tokens_details", {}).get("cached_tokens") == 50)
check("session sse captured", len(list(sess_dir.glob("*-probe-muse-*.response.sse"))) == 1)
check("muse stats: 1 request / 1 response / 0 sidecalls",
      (muse_mod._MUSE_STATS["requests"], muse_mod._MUSE_STATS["responses"],
       muse_mod._MUSE_STATS["sidecalls"]) == (1, 1, 0))
check("codex stats untouched", lp.codex._CODEX_STATS["requests"] == 0)

# ---- 3. subscriber feed -------------------------------------------------------
print("[subscribers]")
turns = [e for e in ENVELOPES if e["event"] == "turn.completed"]
deltas = [e for e in ENVELOPES if e["event"] == "text.delta"]
check("turn.completed emitted", len(turns) == 1, len(turns))
if turns:
    d = turns[0]["data"]
    check("turn.completed provider=meta", d.get("provider") == "meta")
    check("turn.completed sidecall null", d.get("sidecall") is None)
    check("turn.completed text = the full turn", d.get("text") == "hi there", d.get("text"))
    check("turn.completed cost", abs((d.get("cost") or {}).get("est_usd", 0) - 0.000092) < 1.5e-6)
    check("turn.completed subscription = the plan quota off the stream",
          (d.get("subscription") or {}).get("weekly", {}).get("used_percent") == 0
          and d["subscription"].get("tier") == "27681527378179523")
    check("turn.completed usage.cached_tokens", d["usage"].get("cached_tokens") == 50)
    check("turn.completed routed to the agent", turns[0]["agent"] == "probe" and turns[0]["session_id"] == SID)
check("text.delta decoded from response.output_text.delta on wire=meta",
      deltas and deltas[0]["data"].get("provider") == "meta"
      and "".join(e["data"]["text"] for e in deltas) == "hi there")

# ---- 4. reminder-observer side-call filed under the parent ------------------
print("[side-calls]")
side = dict(BODY)
side["prompt_cache_key"] = f"tbh:goal-reminder:{SID[:8]}:cb6542df:18214b5c"
side["instructions"] = "observer"
side_hdrs = dict(HDRS)
side_hdrs["x-tbh-session-id"] = "1d3b42aa-af3a-4973-b547-eb55712a31c8"
side_hdrs["x-meta-ai-gateway-session-id"] = side_hdrs["x-tbh-session-id"]
ENVELOPES.clear()
r = c.post("/agent/probe/meta/responses", content=json.dumps(side).encode(),
           headers=side_hdrs)
check("side-call forwarded (200)", r.status_code == 200)
flush()
tot = billing_mod._SESSION_TOTALS.get(SID) or {}
check("side-call filed under the PARENT session (by cache-key prefix)",
      tot.get("requests") == 2, tot.get("requests"))
check("side-call's own throwaway session id created no session",
      "1d3b42aa-af3a-4973-b547-eb55712a31c8" not in billing_mod._SESSION_TOTALS)
check("side-call is NOT a turn", tot.get("turns") == 1, tot.get("turns"))
check("side-call priced apart under sidecalls.goal-reminder",
      ((tot.get("sidecalls") or {}).get("goal-reminder") or {}).get("requests") == 1
      and abs(tot["sidecalls"]["goal-reminder"]["est_usd"] - 0.000092) < 1.5e-6)
check("side-call inside the session total (decomposition, not a second count)",
      abs((tot.get("est_usd") or 0) - 0.000184) < 2e-6, tot.get("est_usd"))
check("by_line gains a goal-reminder line, main unchanged",
      (tot.get("by_line") or {}).get("goal-reminder", {}).get("requests") == 1
      and tot["by_line"]["main"]["requests"] == 1)
check("stats count the sidecall", muse_mod._MUSE_STATS["sidecalls"] == 1)
turns = [e for e in ENVELOPES if e["event"] == "turn.completed"]
check("side-call turn.completed carries sidecall=goal-reminder under the parent",
      turns and turns[0]["data"].get("sidecall") == "goal-reminder"
      and turns[0]["session_id"] == SID)
side_reqs = [json.loads(p.read_text()) for p in sess_dir.glob("*-probe-muse-*.request.json")]
check("side-call request summary tagged", any(q["summary"].get("sidecall") == "goal-reminder" for q in side_reqs))
side_resps = [json.loads(p.read_text()) for p in sess_dir.glob("*-probe-muse-*.response.json")]
check("side-call receipt tagged", any(q.get("sidecall") == "goal-reminder" for q in side_resps))
# the /_session view state stays the conversation's, not the observer's
r = c.get(f"/_status?session={SID}")
st = r.json()
check("/_status session row exists", st.get("session_id") == SID or (st.get("sessions") or [{}])[0].get("session_id") == SID)

# LIVE ORDER (2026-09-22, seq 2 vs 3 in the same second): the skill-reminder
# observer fires BEFORE the main call of the same prompt. So a side-call whose
# parent is unknown at request time must re-resolve at receipt time, once the
# main call has had the stream's duration to register its prefix. Simulate:
# the side-call's request is read (identity decided) before the main call
# registers, and the main call lands before the side-call's stream ends.
import threading as _th
SID2 = "01a0c9ff-0000-4000-8000-000000000002"
main2 = dict(BODY)
main2["prompt_cache_key"] = f"tbh:main:{SID2}"
m2_hdrs = dict(HDRS)
m2_hdrs["x-tbh-session-id"] = SID2
m2_hdrs["x-meta-ai-gateway-session-id"] = SID2
early = dict(side)
early["prompt_cache_key"] = f"tbh:skill-reminder:{SID2[:8]}:f350a679:e9a9bf35"
e_hdrs = dict(side_hdrs)
e_hdrs["x-tbh-session-id"] = "343b43f2-7738-48d3-8671-77d720bdfe35"
e_hdrs["x-meta-ai-gateway-session-id"] = e_hdrs["x-tbh-session-id"]
_gate = _th.Event()
_orig_stub_post = _Stub.do_POST


def _slow_post(self):
    # hold the FIRST early side-call's upstream stream until the main call has
    # been through the proxy (its identity registered), then release it
    if not _gate.is_set() and b"skill-reminder:01a0c9ff" in (self.headers.get("x-probe") or "").encode():
        _gate.wait(10)
    _orig_stub_post(self)


_Stub.do_POST = _slow_post
e_hdrs["x-probe"] = early["prompt_cache_key"]
res = {}


def _fire_early():
    res["early"] = c.post("/agent/probe/meta/responses", content=json.dumps(early).encode(), headers=e_hdrs)


t = _th.Thread(target=_fire_early)
t.start()
time.sleep(0.4)                                  # side-call request is in flight
r = c.post("/agent/probe/meta/responses", content=json.dumps(main2).encode(), headers=m2_hdrs)
check("main call of the new session -> 200", r.status_code == 200)
_gate.set()
t.join(15)
_Stub.do_POST = _orig_stub_post
flush()
t2 = billing_mod._SESSION_TOTALS.get(SID2) or {}
check("early side-call resolved to the parent AT RECEIPT time (2 requests on the parent)",
      t2.get("requests") == 2, t2.get("requests"))
check("early side-call never created a session under its own header id",
      "343b43f2-7738-48d3-8671-77d720bdfe35" not in billing_mod._SESSION_TOTALS)
check("early side-call priced apart under the parent",
      ((t2.get("sidecalls") or {}).get("skill-reminder") or {}).get("requests") == 1)
check("stats count the deferred resolution", muse_mod._MUSE_STATS["sidecalls_deferred"] == 1)
d2 = Path(LOG_DIR) / SID2
e_reqs = [json.loads(p.read_text()) for p in d2.glob("*-probe-muse-*.request.json")
          if "skill-reminder" in p.read_text()]
check("early side-call's request capture filed in the PARENT dir, flagged deferred",
      len(e_reqs) == 1 and e_reqs[0]["summary"].get("deferred_parent") is True
      and e_reqs[0]["summary"]["session_id"] == SID2, [q["summary"].get("session_id") for q in e_reqs])
check("no stray capture under the side-call's own id",
      not (Path(LOG_DIR) / "343b43f2-7738-48d3-8671-77d720bdfe35").exists())

# a TRUE orphan (parent never seen: proxy restarted mid-session) keeps its own
# header id rather than landing in the no-session bucket
orphan = dict(side)
orphan["prompt_cache_key"] = "tbh:verify-reminder:deadbeef:ab8c6967:ead0b997"
o_hdrs = dict(side_hdrs)
o_hdrs["x-tbh-session-id"] = "caf21793-87b4-417c-998f-9415f56d612c"
r = c.post("/agent/probe/meta/responses", content=json.dumps(orphan).encode(), headers=o_hdrs)
flush()
check("orphan side-call keyed by its own session id",
      (billing_mod._SESSION_TOTALS.get("caf21793-87b4-417c-998f-9415f56d612c") or {}).get("requests") == 1)
check("orphan side-call still priced apart",
      ((billing_mod._SESSION_TOTALS["caf21793-87b4-417c-998f-9415f56d612c"].get("sidecalls") or {})
       .get("verify-reminder") or {}).get("requests") == 1)
check("orphan's request capture still written (under its own id)",
      len(list((Path(LOG_DIR) / "caf21793-87b4-417c-998f-9415f56d612c").glob("*.request.json"))) == 1)

# ---- 4b. the TUI's IDLE side-calls ride the MAIN key ---------------------------
# Live 2026-09-22 (session 01a0ca63, 133 captures): after every turn the TUI
# sends a "tip-picker" call and, when the user returns, an "away-recap" call —
# both under `tbh:main:<session>` + the session's own x-tbh-session-id, so the
# key vocabulary cannot separate them. They were the main line: the idle body
# became the session's last request (/_context tools:null whenever idle),
# counted as a turn, and priced under main_est_usd. Fixtures are those
# captures (seq 084 / 113) byte-for-byte, re-keyed to this test's session.
print("[idle side-calls]")
from proxylab import pinger as pinger_mod  # noqa: E402


def _idle_fixture(name):
    fx = json.loads((FIX / f"{name}.request.json").read_text())
    body = fx["body"]
    body["prompt_cache_key"] = f"tbh:main:{SID}"
    hdrs = dict(HDRS)
    hdrs["x-tbh-session-id"] = SID
    hdrs["x-meta-ai-gateway-session-id"] = SID
    return body, hdrs


TIP, tip_hdrs = _idle_fixture("tip-picker")
AWAY, _ = _idle_fixture("away-recap")
check("fixture shape: main key, no tools, no instructions, last item a user message",
      "tools" not in TIP and "instructions" not in TIP
      and TIP["input"][-1].get("role") == "user")
check("_idle_sidecall_kind: tip-picker off the captured opener",
      muse_mod._idle_sidecall_kind(TIP) == "tip-picker")
check("_idle_sidecall_kind: away-recap off the captured opener",
      muse_mod._idle_sidecall_kind(AWAY) == "away-recap")
check("_idle_sidecall_kind: the conversation body (tools + instructions) is NOT idle",
      muse_mod._idle_sidecall_kind(BODY) is None)
check("_idle_sidecall_kind: an unlisted prompt of the idle SHAPE files as `idle`",
      muse_mod._idle_sidecall_kind({"input": [{"type": "message", "role": "user",
                                              "content": "Summarize this session."}]}) == "idle")
check("_idle_sidecall_kind: a tool-less body whose last item is a tool output is NOT idle",
      muse_mod._idle_sidecall_kind({"input": [{"type": "function_call_output", "output": "x"}]}) is None)
check("_session_identity: tip-picker = side-call on the session ITSELF (no prefix hop)",
      muse_mod._session_identity(tip_hdrs, TIP) == (SID, "tip-picker", None))
check("_session_identity: conversation body still main", muse_mod._session_identity(HDRS, BODY) == (SID, None, None))
before = dict(billing_mod._SESSION_TOTALS.get(SID) or {})
last_before = (pinger_mod._LAST_REQUEST.get(SID) or {}).get("obj")
ENVELOPES.clear()
r = c.post("/agent/probe/meta/responses", content=json.dumps(TIP).encode(), headers=tip_hdrs)
check("tip-picker forwarded (200)", r.status_code == 200, r.status_code)
r = c.post("/agent/probe/meta/responses", content=json.dumps(AWAY).encode(), headers=tip_hdrs)
check("away-recap forwarded (200)", r.status_code == 200, r.status_code)
flush()
tot = billing_mod._SESSION_TOTALS.get(SID) or {}
check("both filed under the session (requests +2)", tot.get("requests") == before.get("requests", 0) + 2,
      (before.get("requests"), tot.get("requests")))
check("neither is a turn", tot.get("turns") == before.get("turns"), (before.get("turns"), tot.get("turns")))
check("priced apart under sidecalls.tip-picker / sidecalls.away-recap",
      ((tot.get("sidecalls") or {}).get("tip-picker") or {}).get("requests") == 1
      and ((tot.get("sidecalls") or {}).get("away-recap") or {}).get("requests") == 1,
      sorted((tot.get("sidecalls") or {}).keys()))
check("main line's own share unchanged (main_est_usd / by_line.main)",
      (tot.get("by_line") or {}).get("main", {}).get("requests")
      == (before.get("by_line") or {}).get("main", {}).get("requests"),
      ((before.get("by_line") or {}).get("main"), (tot.get("by_line") or {}).get("main")))
last_after = (pinger_mod._LAST_REQUEST.get(SID) or {}).get("obj")
check("the idle body did NOT become the session's last request (tools still on it)",
      last_after is last_before and bool((last_after or {}).get("tools")),
      ("tools" in (last_after or {}), (last_after or {}).get("prompt_cache_key")))
ctx = c.get(f"/_context?session={SID}").json()
m = next((a for a in ctx.get("agents") or [] if a.get("line") == "main"), None)
check("/_context after an idle call still shows the conversation's 29-function roster",
      m is not None and (m.get("tools") or {}).get("count") == 29, (m or {}).get("tools"))
turns = [e for e in ENVELOPES if e["event"] == "turn.completed"]
check("turn.completed carries sidecall=tip-picker / away-recap",
      sorted(e["data"].get("sidecall") for e in turns) == ["away-recap", "tip-picker"],
      [e["data"].get("sidecall") for e in turns])
idle_reqs = [json.loads(p.read_text()) for p in sess_dir.glob("*-probe-muse-*.request.json")]
check("request captures tagged sidecall=tip-picker / away-recap",
      {q["summary"].get("sidecall") for q in idle_reqs} >= {"tip-picker", "away-recap"})
idle_resps = [json.loads(p.read_text()) for p in sess_dir.glob("*-probe-muse-*.response.json")]
check("receipts tagged", {q.get("sidecall") for q in idle_resps} >= {"tip-picker", "away-recap"})

# ---- 4c. bare-string content renders + counts as turns (views, codex predicate) ---
# muse ships `content` as a bare string on every user/assistant item; codex as
# [{type,text}] blocks. The /_session view and the turn predicate iterated the
# list form only, so a muse session rendered no user/assistant text and 0 turns.
print("[string content]")
from proxylab import codex as codex_mod  # noqa: E402
from proxylab import views as views_mod  # noqa: E402
check("_is_prompt_item_openai counts a bare-string user item as a turn",
      codex_mod._is_prompt_item_openai({"type": "message", "role": "user", "content": "hi"}))
check("… and still the codex block dialect", codex_mod._is_prompt_item_openai(
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}))
check("… machine context (<…>) is not a turn in either dialect",
      not codex_mod._is_prompt_item_openai({"type": "message", "role": "user", "content": "<environment_context>x"})
      and not codex_mod._is_prompt_item_openai({"type": "message", "role": "user",
                                                "content": [{"type": "input_text", "text": " <permissions>"}]}))
check("tip-picker capture: 2 prompt turns counted (the real prompt + the idle prompt)",
      sum(1 for it in TIP["input"] if codex_mod._is_prompt_item_openai(it)) == 2)
html = views_mod._render_session_openai_body({"obj": TIP, "ts": time.time()})
check("/_session renders the bare-string USER text",
      "does that file allow disabling some of the internal tools?" in html)
check("/_session renders the bare-string ASSISTANT text", "Checked active settings" in html)
check("/_session renders turn headers for the string dialect", 'id="turn-1"' in html and 'id="turn-2"' in html)
html = views_mod._render_session_openai_body({"obj": BODY, "ts": time.time()})
check("/_session renders the conversation fixture's developer + user strings",
      "Workspace root" in html and "say hi" in html)
comp = status_mod._composition(TIP)
cc = {x["category"]: x["tokens"] for x in (comp or {}).get("by_category") or []}
check("_composition_openai on the tip-picker body: user/assistant/reasoning/tool_calls/tool_results, no tools/system",
      set(cc) == {"user", "assistant", "reasoning", "tool_calls", "tool_results"}, sorted(cc))
check("_composition_openai basis estimate without a receipt", (comp or {}).get("basis") == "estimate")

# ---- 5. /_identity, /_status, /_context ---------------------------------------
print("[endpoints]")
ident = c.get("/_identity").json()
check("capabilities.muse advertised", ident["capabilities"].get("muse") is True)
check("capabilities.codex still advertised", ident["capabilities"].get("codex") is True)
st = c.get("/_status").json()
check("/_status proxy.upstream_meta", st["proxy"].get("upstream_meta") == os.environ["UPSTREAM_META"])
check("/_status proxy.muse stats", (st["proxy"].get("muse") or {}).get("responses") == 7,
      (st["proxy"].get("muse") or {}).get("responses"))
check("/_status proxy.muse.subscription = newest plan reading",
      ((st["proxy"].get("muse") or {}).get("subscription") or {}).get("tier") == "27681527378179523")
check("/_status proxy.muse.catalog_models", len((st["proxy"].get("muse") or {}).get("catalog_models") or []) == 4)
row = next((s for s in st.get("sessions") or [] if s.get("session_id") == SID), None)
check("session row present", row is not None)
if row:
    check("muse session never pingable / never awaiting auth",
          not row.get("pingable") and not row.get("awaiting_auth"))
    check("/_status row says which wire the session speaks (wire=meta)",
          row.get("wire") == "meta", row.get("wire"))
    check("warmth block structurally absent on this wire (server-side cache, no TTL)",
          (row.get("warmth") or {}).get("state") == "absent", row.get("warmth"))
    check("cwd learned from the developer message (Workspace root)",
          (row.get("cwd") or "").endswith("/echo-ws"), row.get("cwd"))
    # the row title is the wirescope agent label when one is routed (here
    # "[probe]"); the prompt-derived title is the fallback for unlabeled seats
    check("title = agent label or first user prompt",
          row.get("title") in ("[probe]",) or (row.get("title") or "").endswith("say hi"),
          row.get("title"))
# /_admin files a server-side-cached session by RECENCY, not by the (absent)
# warmth row: a muse seat that turned seconds ago sat under "cold / expired"
# (Bogdan, 2026-09-22). Active in the last hour -> the warm table.
from proxylab import views as views_mod  # noqa: E402
snap = status_mod._status_snapshot()
page = views_mod._render_admin_html(snap)
warm_part = page.split("cold / expired")[0]
check("/_admin: the recently-active muse session renders in the WARM table",
      f"session={SID}" in warm_part and "server · active" in warm_part)
aged = json.loads(json.dumps(snap))
for s_ in aged["sessions"]:
    if s_["session_id"] == SID:
        s_["last_seen"] = time.time() - 2 * 3600
page = views_mod._render_admin_html(aged)
check("/_admin: the same session idle 2h renders in the COLD table as server · idle",
      f"session={SID}" not in page.split("cold / expired")[0]
      and "server · idle" in page.split("cold / expired")[1])
check("anthropic rows keep the warmth-state split (wire=anthropic default)",
      not views_mod._server_cached({"warmth": {"state": "cold"}})
      and views_mod._server_cached({"wire": "openai"}))
ctx = c.get(f"/_context?session={SID}").json()
main = next((a for a in ctx.get("agents") or [] if a.get("line") == "main"), None)
check("/_context main line present", main is not None, ctx.get("note"))
if main:
    check("/_context wire=meta", main.get("wire") == "meta", main.get("wire"))
    tools = main.get("tools") or {}
    check("/_context tools roster UNWRAPS the namespace (29 functions, not 1)",
          tools.get("count") == 29, tools.get("count"))
    check("roster names are the nested functions",
          "bash" in (tools.get("names") or []) or "read_file" in (tools.get("names") or []),
          (tools.get("names") or [])[:5])
    comp = main.get("composition") or {}
    cats = {c["category"]: c for c in comp.get("by_category") or []}
    check("/_context composition present on this wire (Responses-API vocabulary)",
          set(cats) >= {"tools", "system", "developer", "user"}, sorted(cats))
    check("composition scaled to the receipt's window (basis receipt, sums to 100)",
          comp.get("basis") == "receipt" and comp.get("total_tokens") == 100
          and sum(c["tokens"] for c in cats.values()) == 100, comp)
    check("composition: the namespace tool schema is the biggest category",
          (comp.get("by_category") or [{}])[0].get("category") == "tools")
    check("/_context model", main.get("model") == "muse-spark-1.3")
check("_wire_of vocabulary", status_mod._wire_of(BODY) == "meta"
      and status_mod._wire_of({"input": [], "instructions": "x"}) == "openai"
      and status_mod._wire_of({"messages": []}) == "anthropic")
r = c.get(f"/_session?session={SID}")
check("/_session renders the meta badge", r.status_code == 200 and "meta wire" in r.text)
r = c.post(f"/_ping?session={SID}")
check("/_ping declines the wire (server-side cache, nothing to slide)",
      r.status_code == 200 and r.json().get("skipped") == "openai_wire", r.text[:120])

# ---- 6. codex regression: same handler, other provider ------------------------
print("[codex regression]")
codex_body = {"model": "gpt-5.4", "instructions": "x", "stream": True,
              "prompt_cache_key": "thread-1",
              "input": [{"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "hello"}]}],
              "tools": [{"type": "function", "name": "shell", "parameters": {}}]}
r = c.post("/agent/cx/openai/v1/responses", content=json.dumps(codex_body).encode(),
           headers={"content-type": "application/json", "session-id": "codex-sess-1",
                    "authorization": "Bearer codex-key"})
check("codex route still 200", r.status_code == 200, r.status_code)
flush()
ct = billing_mod._SESSION_TOTALS.get("codex-sess-1") or {}
check("codex session keyed by session-id header", ct.get("requests") == 1)
check("codex priced off PRICES_OPENAI (gpt-5.4: 50*2.5 + 50*0.25 + 5*15 per M)",
      abs((ct.get("est_usd") or 0) - 0.000213) < 2e-6, ct.get("est_usd"))
check("codex stem tag unchanged",
      len(list((Path(LOG_DIR) / "codex-sess-1").glob("*-cx-codex-*.request.json"))) == 1)
check("codex stats bumped, muse stats not",
      lp.codex._CODEX_STATS["requests"] == 1 and muse_mod._MUSE_STATS["requests"] == 7)
check("codex body is NOT a muse body", not muse_mod._is_muse_body(codex_body))
check("codex tools roster stays null", status_mod._tool_roster(codex_body) is None)
cx_turn = [e for e in ENVELOPES if e["event"] == "turn.completed" and e["agent"] == "cx"]
check("codex turn.completed provider=openai, sidecall null",
      cx_turn and cx_turn[0]["data"].get("provider") == "openai"
      and cx_turn[0]["data"].get("sidecall") is None)

# ---- 7. unit: cache-key parsing + restart reload -----------------------------
print("[units]")
check("_parse_cache_key main", muse_mod._parse_cache_key(f"tbh:main:{SID}") == {"kind": "main", "session": SID})
check("_parse_cache_key side-call", muse_mod._parse_cache_key("tbh:skill-reminder:01a0c87d:f350a679:7859c717")
      == {"kind": "skill-reminder", "session": "01a0c87d"})
check("_parse_cache_key rejects codex keys", muse_mod._parse_cache_key("thread-1") is None)
check("longest-prefix pricing covers the -contributor variant (the live default model)",
      billing_mod._price_for("muse-spark-1.3-contributor", table=muse_mod.PRICES_META) is not None)
check("_subscription_usage parses the live event and stamps `at`",
      (muse_mod._subscription_usage(SSE) or {}).get("window", {}).get("used_percent") == 0
      and muse_mod._subscription_usage(b"event: response.completed\ndata: {}\n\n") is None)
check("unknown muse model is unpriced, not mispriced",
      billing_mod._billing_openai("muse-spark-9", {"input_tokens": 10, "output_tokens": 1},
                                  provider="meta").get("unpriced") is True)
muse_mod.PRICES_META.pop("muse-spark-1.3", None)
muse_mod.CATALOG_LIMITS.clear()
check("_load_catalog restores the table + limits + roster from disk",
      muse_mod._load_catalog() >= 1 and muse_mod.PRICES_META["muse-spark-1.3"]["in"] == 1.25
      and muse_mod.CATALOG_LIMITS.get("muse-spark-1.2", {}).get("context") == 1007997)

_srv.shutdown()
print()
if FAILS:
    print(f"FAILED ({len(FAILS)}): " + "; ".join(FAILS))
    sys.exit(1)
print("ALL PASS")
