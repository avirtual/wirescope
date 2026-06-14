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
from proxylab import canary as canary_mod
from proxylab import codex as codex_mod
from proxylab import core as core_mod
from proxylab import hold as hold_mod
from proxylab import meta as meta_mod
from proxylab import pinger as pinger_mod
from proxylab import receipts as receipts_mod
from proxylab import restore as restore_mod
from proxylab import status as status_mod
from proxylab import subs as subs_mod
from proxylab import transforms as transforms_mod
from proxylab import views as views_mod
from proxylab import warmth as warmth_mod
from proxylab import writer as writer_mod

async def _handle_openai(request: Request, n, raw, agent, upstream_path, ts):
    """The /agent/<name>/openai/... path: forward to UPSTREAM_OPENAI with the
    chatgpt-backend rewrite, capture request+response, tee subscribers, price
    the receipts (API-equivalent). Deliberately NO transform/warmth/canary
    machinery — see the OPENAI/CODEX PROVIDER block up top."""
    base_path = upstream_path.split("?")[0]
    chatgpt_mode = codex_mod._is_chatgpt_backend(codex_mod.UPSTREAM_OPENAI)
    codex_mod._CODEX_STATS["requests"] += 1

    # ---- observer-side decode + parse (forward the ORIGINAL bytes) ----
    body_bytes, dec_err = codex_mod._content_decode(
        raw, request.headers.get("content-encoding"))
    obj = None
    try:
        obj = json.loads(body_bytes) if body_bytes else {}
    except Exception:
        pass
    model = (obj or {}).get("model")
    # codex carries session identity in HEADERS (plus prompt_cache_key in-body)
    session_id = (request.headers.get("session-id")
                  or request.headers.get("thread-id")
                  or (obj or {}).get("prompt_cache_key"))
    session_key = session_id or writer_mod.NO_SESSION
    out_dir = core_mod.LOG_DIR / session_key
    stem = f"{n:03d}-{agent}-codex-{writer_mod._short_model(model)}-{ts}"

    client = request.client
    record = {"seq": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "agent": agent, "provider": "openai",
              "method": request.method, "path": upstream_path,
              "client": {"host": client.host, "port": client.port} if client else None,
              "request_headers": core_mod._safe_headers(request.headers)}
    if dec_err:
        record["decode_error"] = dec_err
    if isinstance(obj, dict):
        record["body"] = obj
        inp = obj.get("input") or []
        record["summary"] = {
            "model": model, "session_id": session_id,
            "instructions_chars": len(obj.get("instructions") or ""),
            "n_input": len(inp) if isinstance(inp, list) else None,
            "n_tools": len(obj.get("tools") or []),
            "tool_names": [t.get("name") or t.get("type")
                           for t in (obj.get("tools") or [])
                           if isinstance(t, dict)],
            "reasoning": obj.get("reasoning"),
            "prompt_cache_key": obj.get("prompt_cache_key"),
            "store": obj.get("store"), "stream": obj.get("stream"),
        }
        # session identity for /_status//_admin: model + cwd (from the
        # environment_context input item) + first-prompt head as the title
        # (codex has no title side-call)
        if session_id and base_path.rstrip("/").endswith(("/responses",
                                                          "/chat/completions")):
            pinger_mod._clear_session_ended(session_id)   # live turn = resume
            # /_session context view (NOT replayable — pinger declines openai)
            pinger_mod._cache_last_request_openai(session_id, obj, upstream_path)
            fields = {"model": model, "agent": agent}
            try:
                texts = [c.get("text") or "" for it in inp if isinstance(it, dict)
                         for c in (it.get("content") or [])
                         if isinstance(c, dict)]
                joined = "\n".join(texts)
                mcwd = re.search(r"<cwd>([^<]+)</cwd>", joined)
                if mcwd:
                    fields["cwd"] = mcwd.group(1)
                prompts = [tx for it in inp if isinstance(it, dict)
                           and it.get("role") == "user"
                           for c in (it.get("content") or []) if isinstance(c, dict)
                           for tx in [c.get("text") or ""]
                           if tx and not tx.lstrip().startswith("<")]
                if prompts:
                    fields["title"] = prompts[0].strip().splitlines()[0][:80]
            except Exception:
                pass
            writer_mod._enqueue_meta(session_id, **fields)
    elif raw:
        record["body_raw"] = body_bytes.decode("utf-8", "replace")[:4000]
    writer_mod._enqueue_json(out_dir / f"{stem}.request.json", record)

    # ---- /v1/models stub (chatgpt backend has no platform model list) ----
    if chatgpt_mode and base_path.rstrip("/").endswith("/models"):
        out = {"models": [{"id": mid, "object": "model",
                           "created": int(time.time()), "owned_by": "openai"}
                          for mid in codex_mod.CODEX_MODELS_STUB],
               "object": "list"}
        writer_mod._enqueue_json(out_dir / f"{stem}.response.json",
                      {"seq": n, "agent": agent, "provider": "openai",
                       "endpoint": "models", "status_code": 200,
                       "stub": True, "body": out})
        return Response(json.dumps(out), media_type="application/json")

    # ---- forward (original bytes; auth rewritten for the chatgpt backend) ----
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in core_mod._HOP}
    fwd_headers["accept-encoding"] = "identity"   # readable SSE for the capture
    up_path = upstream_path
    if chatgpt_mode:
        up_path, fwd_headers = codex_mod._rewrite_chatgpt_request(up_path, fwd_headers)
    req = core_mod._client.build_request(request.method, codex_mod.UPSTREAM_OPENAI + up_path,
                                headers=fwd_headers, content=raw)
    up = await core_mod._client.send(req, stream=True)
    resp_headers = {k: v for k, v in up.headers.items()
                    if k.lower() not in {"connection", "transfer-encoding",
                                         "content-length", "keep-alive"}}
    # POST /responses|/chat/completions IS the model wire — capture + tee it
    # path-based, never content-type-based (success can ship with NO
    # content-type header at all on the chatgpt backend)
    is_model_call = (request.method == "POST"
                     and base_path.rstrip("/").endswith(("/responses",
                                                         "/chat/completions")))
    chunks = []
    # Generic subscriber tee (SUBSCRIBERS.md); every request here is /agent/-
    # routed by construction, so the agent identity gate is the route itself.
    sub_tee = (subs_mod._tee_for(agent, session_id, f"{n}-{ts}", wire="openai")
               if is_model_call else None)

    async def body_iter():
        try:
            async for chunk in up.aiter_raw():
                if is_model_call:
                    chunks.append(chunk)
                yield chunk
                if sub_tee is not None:  # after yield: client bytes come first
                    sub_tee.feed(chunk)
        finally:
            if sub_tee is not None:
                sub_tee.close()
            await up.aclose()
            if is_model_call and chunks:
                blob = b"".join(chunks)
                writer_mod._enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
                # everything derived from the finished response — billing,
                # view state, capture, subscriber receipt — lives in receipts
                receipts_mod.openai(
                    blob, n=n, ts=ts, agent=agent, model=model,
                    session_id=session_id, session_key=session_key,
                    out_dir=out_dir, stem=stem, status_code=up.status_code,
                    resp_headers=dict(up.headers),
                    tee_text=(sub_tee.text if sub_tee is not None else None))

    return StreamingResponse(body_iter(), status_code=up.status_code,
                             headers=resp_headers,
                             media_type=up.headers.get("content-type"))


async def handler(request: Request) -> Response:
    # ---- identity: "is this our proxy?" handshake for subscribers -------------
    # GET /_identity — read-only, unauthenticated, spends nothing. Lets a
    # consumer confirm product == "wirescope" + read capabilities/protocols
    # before it registers / pulls stats / warms cache (see SUBSCRIBERS.md).
    if request.method == "GET" and request.url.path.rstrip("/") == "/_identity":
        res = status_mod._identity()
        return Response(json.dumps(res, indent=2),
                        media_type="application/json",
                        headers={"X-Wirescope-Version": core_mod.VERSION})

    # ---- status: what sessions are tracked + warmth/hold/identity/cost --------
    # GET /_status[?session=<id>][&all=1] — read-only, spends nothing.
    if request.method == "GET" and request.url.path == "/_status":
        q = request.query_params
        res = status_mod._status_snapshot(session=q.get("session"),
                               all_sessions=q.get("all") in ("1", "yes", "true"))
        return Response(json.dumps(res, indent=2), media_type="application/json")

    # ---- admin page: the same snapshot for humans ------------------------------
    # GET /_admin[?session=<id>][&all=1] — read-only HTML view of /_status.
    if request.method == "GET" and request.url.path.rstrip("/") == "/_admin":
        q = request.query_params
        all_s = q.get("all") in ("1", "yes", "true")
        sess = q.get("session")
        try:
            show = int(q.get("show") or 60)
        except (TypeError, ValueError):
            show = 60
        show = max(10, min(show, 2000))
        res = status_mod._status_snapshot(
            session=sess, all_sessions=all_s,
            limit=(None if (all_s or sess) else show))
        return Response(views_mod._render_admin_html(
                            res, host=request.headers.get("host", ""), show=show),
                        media_type="text/html; charset=utf-8")

    # ---- session context view: the replayable last request, for humans --------
    # GET /_session?session=<id> — read-only HTML rendering of the session's
    # captured context (body only, never headers).
    if request.method == "GET" and request.url.path.rstrip("/") == "/_session":
        sess = request.query_params.get("session")
        if not sess:
            return Response("missing ?session=", status_code=400,
                            media_type="text/plain")
        # ?sub=<instance-key> (or legacy ?role=<role>) -> the per-subagent view
        # (shares the parent's session_id; latest captured turn, never the
        # parent's pingable request nor the parent's response/usage receipts).
        # The key is the x-claude-code-agent-id when the spawn had one, else the
        # role — so concurrent same-role subagents each get their own page.
        subkey = request.query_params.get("sub") or request.query_params.get("role")
        if subkey:
            return Response(views_mod._render_session_html(
                                sess, meta_mod._subagent_request(sess, subkey),
                                status_mod._status_snapshot(session=sess),
                                subrole=subkey),
                            media_type="text/html; charset=utf-8")
        with pinger_mod._LAST_REQUEST_LOCK:
            entry = pinger_mod._LAST_REQUEST.get(sess)
        if entry is None:
            entry = views_mod._load_last_request_row(sess)
        return Response(views_mod._render_session_html(sess, entry,
                                             status_mod._status_snapshot(session=sess),
                                             resp=meta_mod._LAST_RESPONSE.get(sess),
                                             usage=meta_mod._LAST_USAGE.get(sess)),
                        media_type="text/html; charset=utf-8")

    # ---- warmth read endpoint (local consumers: statusline / hook / pinger) ---
    # GET /_warm?h=<prefix-hash>  or  /_warm?session=<session_id>
    if request.method == "GET" and request.url.path == "/_warm":
        q = request.query_params
        res = warmth_mod.warmth_query(hash_hex=q.get("h"), session=q.get("session"))
        return Response(json.dumps(res), media_type="application/json")

    # ---- keep-warm HOLD: arm/disarm idle insurance for a session --------------
    # GET  /_hold?session=<id>                  -> current hold (read-only, free)
    # POST /_hold?session=<id>&hours=<n>        -> arm n hours of idle insurance
    # POST /_hold?session=<id>&hours=0  (|&action=off) -> disarm
    # The programmatic twin of the in-band `/warm-cache` command (which arms by
    # injecting a <proxy:warm-cache hours=N> sentinel into a forwarded turn).
    # Unlike that path this does NOT forward a turn, so the pinger can only keep
    # the cache warm if the session already has a replayable last request + live
    # auth (see `pingable`/`awaiting_auth` in the reply); otherwise the hold is
    # recorded and the session's next real turn re-anchors + donates them.
    if request.url.path.rstrip("/") == "/_hold":
        q = request.query_params
        sess = q.get("session")
        if not sess:
            return Response(json.dumps({"ok": False, "reason": "missing ?session="}),
                            status_code=400, media_type="application/json")
        if request.method == "GET":
            hold = hold_mod._hold_snapshot().get(sess)
            return Response(json.dumps({"ok": True, "session": sess, "hold": hold}),
                            media_type="application/json")
        raw_h, act = q.get("hours"), q.get("action")
        if act == "off" or raw_h in ("0", "off"):
            arm_action, hours = "off", None
        else:
            try:
                hours = float(raw_h)
            except (TypeError, ValueError):
                return Response(json.dumps({"ok": False, "reason": "missing/invalid ?hours="}),
                                status_code=400, media_type="application/json")
            if hours <= 0:
                arm_action, hours = "off", None
            else:
                # match the in-band path's clamp to the configured ceiling
                arm_action = "arm"
                hours = min(hours, hold_mod.WARMTH_HOLD_MAX_HOURS)
        # Same discipline as /_ping: we never warm a non-warm prefix. Arming
        # over HTTP does NOT forward a turn, so a hold on a cold/absent prefix
        # has nothing to keep warm — it would be a no-op until a real turn
        # re-establishes the cache. Decline it (force=1 to arm anyway, e.g. when
        # the caller knows a turn is imminent). Disarm is never gated.
        # Convention (matches /_ping): a deliberate decline is a SUCCESSFUL
        # request with a structured outcome (200, ok:true, armed:false,
        # skipped:<state>) — NOT an HTTP error. 4xx is reserved for malformed
        # requests (missing session / bad hours). Branch on `armed`, not status.
        if arm_action == "arm" and q.get("force") not in ("1", "yes", "on", "true"):
            wq = warmth_mod.warmth_query(session=sess)
            state = ("warm" if wq.get("warm")
                     else "cold" if wq.get("found") else "absent")
            if state != "warm":
                return Response(json.dumps(
                    {"ok": True, "armed": False, "skipped": state, "session": sess,
                     "warmth_state": state, "warmth": wq,
                     "reason": f"prefix is '{state}', not warm; arming over HTTP does "
                               "not forward a turn, so there is nothing to keep warm — "
                               "it would be a no-op until a real turn re-establishes "
                               "the cache. Declined (force=1 to arm anyway, or send a "
                               "turn through the proxy first)."}),
                    media_type="application/json")
        ack, rec = hold_mod._arm_hold(sess, arm_action, hours)
        print(f"[hold] session={sess[:12]}… HTTP {arm_action} -> "
              f"armed={rec.get('armed')} reason={rec.get('reason')}", flush=True)
        return Response(json.dumps({"ok": True, "ack": ack, **rec}),
                        media_type="application/json")

    # ---- keep-warm pinger: replay a session's cached last request -------------
    # POST/GET /_ping?session=<id>[&force=1] — intercepted, never forwarded as a
    # normal turn. Locates the session's cached last request and replays it as a
    # thinking-off, max_tokens:1 cache-read to slide the TTL. (force=1 re-warms a
    # provably-cold prefix instead of declining.)
    if request.url.path == "/_ping":
        q = request.query_params
        sess = q.get("session")
        if not sess:
            return Response(json.dumps({"ok": False, "reason": "missing ?session="}),
                            status_code=400, media_type="application/json")
        force = q.get("force") in ("1", "yes", "on", "true")
        code, res = await pinger_mod._warm_session(sess, force=force)
        print(f"[ping] session={sess[:12]}… -> {res.get('warmed') and 'WARMED' or res.get('skipped') or 'FAIL'} "
              f"prior={res.get('prior_warmth')} read={res.get('cache_read_input_tokens')} "
              f"remaining={res.get('remaining_s')}", flush=True)
        return Response(json.dumps(res), status_code=code,
                        media_type="application/json")

    # ---- session teardown: stop caching a finished session --------------------
    # GET/POST /_end?session=<id>[&reason=clear] — wire to the CLI's SessionEnd
    # hook so a /clear or exit forgets the session's cached request immediately;
    # the background sweeper is the backstop for crashes/kills the hook misses.
    if request.url.path == "/_end":
        sess = request.query_params.get("session")
        if not sess:
            return Response(json.dumps({"ok": False, "reason": "missing ?session="}),
                            status_code=400, media_type="application/json")
        res = pinger_mod._end_session(sess, reason=request.query_params.get("reason", "unspecified"))
        subs_mod.emit_session_ended(sess, res["reason"])
        print(f"[end] session={sess[:12]}… reason={res['reason']} "
              f"ended={res['ended']} hold_disarmed={res['hold_disarmed']} "
              f"retained={res['retained']} (sweeper reaps later)", flush=True)
        return Response(json.dumps(res), media_type="application/json")

    # ---- subscriber registry: app-agnostic push feed (see SUBSCRIBERS.md) -----
    # GET/POST/DELETE /_subscribe — consumers register an endpoint + agent globs
    # and receive text.delta / turn.completed / session.ended for their sessions.
    if request.url.path.rstrip("/") == "/_subscribe":
        return await subs_mod.handle_subscribe(request)

    n = next(core_mod._counter)
    raw = await request.body()
    ts = time.strftime("%H%M%S")

    # ---- route: strip /agent/<name>/<provider> prefix, capture agent name ----
    path = request.url.path
    m = core_mod._ROUTE.match(path)
    if m:
        agent = m.group("name")
        upstream_path = m.group("rest") or "/"
    else:
        mo = codex_mod._ROUTE_OPENAI.match(path)
        if mo:
            up_rest = mo.group("rest") or "/"
            if request.url.query:
                up_rest += "?" + request.url.query
            return await _handle_openai(request, n, raw,
                                        mo.group("name"), up_rest, ts)
        agent = "ext"
        upstream_path = path
    if request.url.query:
        upstream_path += "?" + request.url.query

    # ---- parse + summarize the request body ----
    role, model = "unknown", None
    session_id = account_uuid = None
    title_call = False
    obj = None      # stays None on an unparseable body -> every transform/gate is
                    # skipped and the ORIGINAL bytes forward verbatim (fail-open:
                    # a parse failure must degrade to passthrough, never to a 500)
    client = request.client
    record = {"seq": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "agent": agent, "method": request.method, "path": upstream_path,
              # inbound transport identity — the "who" behind no-session calls
              "client": {"host": client.host, "port": client.port} if client else None,
              "request_headers": core_mod._safe_headers(request.headers)}
    try:
        obj = json.loads(raw) if raw else {}
        record["body"] = obj
        # VERSION-DRIFT CANARY (read-only): fingerprint the ORIGINAL CLI request
        # shape BEFORE any of our transforms, so we detect CLI/wire changes (incl.
        # a new 4th cache_control marker), not our own mutations.
        if obj and upstream_path.split("?")[0].endswith("/v1/messages"):
            cres = canary_mod._canary_check(obj, request.headers, n)
            if cres is not None:
                record["canary"] = cres
        # EXPERIMENTAL piggyback: mutate the outbound payload, forward modified bytes
        if obj:
            changed = False
            appended, reason = transforms_mod._decide_injection(obj)
            if appended:
                orig = transforms_mod._inject_into_last_user(obj, appended, transforms_mod.INJECT_SEP)
                if orig is not None:
                    record["injection"] = {"appended": appended, "reason": reason,
                                           "marker": transforms_mod.INJECT_MARKER,
                                           "original_last_user": orig,
                                           "final_last_user": transforms_mod._last_user_text(obj)}
                    changed = True
            # Invisible standing protocol instruction (UX): append to the user's
            # prompt on genuine prompt turns only (skip tool_result continuations).
            if transforms_mod.SHORTCIRCUIT_INSTRUCT and transforms_mod._last_user_text(obj):
                orig2 = transforms_mod._inject_into_last_user(obj, transforms_mod.SHORTCIRCUIT_INSTRUCT, "\n\n")
                if orig2 is not None:
                    record["injection_shortcircuit"] = {"appended": transforms_mod.SHORTCIRCUIT_INSTRUCT}
                    changed = True
            # Best placement: patch the terminal tools' own descriptions in tools[]
            patched = transforms_mod._patch_tool_descriptions(obj)
            if patched:
                record["toolpatch"] = {"tools": patched}
                changed = True
            # System-prompt delivery (stable-position, cache-riding, invisible).
            if transforms_mod._patch_system(obj):
                record["syspatch"] = True
                changed = True
            # Proxy-side `rest` split: relocate static prose to an env-independent
            # cache prefix (byte-identical model-visible text; cache boundary only).
            sp = transforms_mod._split_system_rest(obj)
            if sp:
                record["rest_split"] = sp
                changed = True
            # Design-2: relocate env+date to a tail block, mark CLAUDE.md (model-visible).
            rel = transforms_mod._relocate_env_to_tail(obj)
            if rel:
                record["env_relocate"] = rel
                changed = True
            # System-section strip: drop configured `# Heading` sections (model-visible).
            strp = transforms_mod._strip_system_sections(obj)
            if strp:
                record["system_strip"] = strp
                changed = True
            # Tool sort: alphabetize tools[] for a byte-stable first cache segment.
            srt = transforms_mod._sort_tools(obj)
            if srt:
                record["tool_sort"] = srt
                changed = True
            # Strip the discarded history cache_control on a (busted) compact req.
            scc = transforms_mod._strip_compact_cache(obj)
            if scc:
                record["strip_compact_cache"] = scc
                if scc.get("removed_message_markers"):
                    changed = True
            # HOLD-WARM: /warm-cache sentinel turn -> arm/disarm + inject the
            # echo instruction; the turn then forwards like any other (the
            # model speaks the ack; this request becomes the replayable,
            # warm, auth-donating last request). LAST in the chain so the
            # instruction is the final text the model reads.
            if upstream_path.split("?")[0].endswith("/v1/messages"):
                he = hold_mod._hold_echo_transform(obj)
                if he:
                    record["hold_echo"] = he
                    changed = True
                    print(f"[hold] #{n} {he['action']} -> "
                          f"{'ARMED ' + str(he.get('hours') or '') if he.get('armed') else 'not armed'}"
                          f" (forwarding; model echoes the ack)", flush=True)
            if changed:
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        role = writer_mod._classify_role(obj)
        model = obj.get("model")
        session_id, account_uuid, device_id = writer_mod._session_ids(obj)
        sysf = obj.get("system")
        sys_chars = len(writer_mod._sys_text(obj))
        msgs = obj.get("messages", []) or []
        msg_chars = len(json.dumps(msgs))
        record["summary"] = {
            "model": model,
            "session_id": session_id,
            "account_uuid": account_uuid,
            "device_id": device_id,
            "role": role,
            "system_chars": sys_chars,
            "system_blocks": len(sysf) if isinstance(sysf, list) else (1 if sysf else 0),
            "n_messages": len(msgs),
            "messages_chars": msg_chars,
            "n_tools": len(obj.get("tools", []) or []),
            "tool_names": [t.get("name") for t in (obj.get("tools") or []) if isinstance(t, dict)],
        }
        # session identity for /_status: bump last_seen/model; hunt the cwd
        # until found; flag the title side-call so the response capture can
        # harvest the session title the CLI generates anyway.
        if upstream_path.split("?")[0].endswith("/v1/messages"):
            title_call = meta_mod._is_title_call(obj)
            # subagents (Task-spawned) share the parent's session_id; pass role
            # so a sub turn is logged distinctly and never overwrites the parent
            # agent's identity/model on the /_status row. The agent-id header
            # (present iff subagent, distinct per spawn) keys concurrent subs apart.
            agent_id = request.headers.get("x-claude-code-agent-id")
            meta_mod._capture_session_meta(session_id, obj, model,
                                           agent=(agent if m else None),
                                           role=role, title_call=title_call,
                                           agent_id=agent_id)
            # heaviness snapshot from the model-visible history (main line
            # only: a subagent's small history must not clobber the parent's)
            if session_id and not title_call and role in ("parent", "unknown"):
                meta_mod._CONTEXT_STATS[session_id] = {**meta_mod._turn_stats(obj),
                                              "ts": time.time()}
    except Exception as e:
        record["parse_error"] = str(e)
        record["body_raw"] = raw.decode("utf-8", "replace")

    # one subdirectory per session; count_tokens/probes (no metadata) -> NO_SESSION
    session_key = session_id or writer_mod.NO_SESSION
    out_dir = core_mod.LOG_DIR / session_key
    stem = f"{n:03d}-{agent}-{role}-{writer_mod._short_model(model)}-{ts}"
    writer_mod._enqueue_json(out_dir / f"{stem}.request.json", record)

    # (HOLD-WARM arming now happens in the transform chain above — the
    # sentinel turn is forwarded with an injected echo instruction, so it
    # flows through the normal capture/billing/warmth path like any turn.)

    # ---- WARMTH: decline a provably-COLD keep-warm ping ----------------------
    # A keep-warm ping for a prefix the ledger knows is already busted has lost its
    # meaning: forwarding would only cold-WRITE the discarded prefix at the write
    # premium. Synthesize an end_turn here and skip upstream entirely (0 tokens).
    if isinstance(obj, dict) and upstream_path.split("?")[0].endswith("/v1/messages"):
        cp = warmth_mod._cold_ping_decision(obj)
        if cp:
            msg_id = f"msg_coldping_{n:06d}"
            ack = "Keep-warm ping declined: cache already expired."
            blob = transforms_mod._synth_end_turn_sse(model, ack, msg_id)
            writer_mod._enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
            writer_mod._enqueue_json(out_dir / f"{stem}.response.json",
                {"seq": n, "agent": agent, "role": role, "model": model,
                 "session_id": session_id, "endpoint": "messages",
                 "status_code": 200, "billing": None, "usage": {}, "meta": {},
                 "cold_ping_block": {**cp, "upstream_called": False,
                                     "synthetic_message_id": msg_id,
                                     "ack": ack}})
            print(f"[warmth] #{n} {agent}/{role} declined COLD keep-warm ping "
                  f"({cp['hash'][:12]}…); upstream skipped, 0 tokens", flush=True)
            return StreamingResponse(iter([blob]), status_code=200,
                                     media_type="text/event-stream")

    # ---- SHORTCIRCUIT (experimental): answer the wrap-up turn locally --------
    # If this request is the tool_result continuation of a model-declared
    # terminal edit, synthesize "Done." here and SKIP the upstream call entirely:
    # the ~one-turn context carriage is never shipped, never billed. The file was
    # already modified by the CLI before it sent this request, so nothing about
    # the edit is lost — only the redundant round trip to hear the model stop.
    sc = None
    if isinstance(obj, dict) and upstream_path.split("?")[0].endswith("/v1/messages"):
        # RELAY (model's own prose, matched by tool_use_id) takes precedence; in
        # relay mode the sentinel is stripped from history so the canned path
        # won't fire. Without relay, fall back to the history-sentinel decision.
        sc = transforms_mod._shortcircuit_relay_decision(obj) or transforms_mod._shortcircuit_decision(obj)
    if sc:
        msg_id = f"msg_shortcircuit_{n:06d}"
        blob = transforms_mod._synth_end_turn_sse(model, sc["ack"], msg_id)
        writer_mod._enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
        writer_mod._enqueue_json(out_dir / f"{stem}.response.json",
            {"seq": n, "agent": agent, "role": role, "model": model,
             "session_id": session_id, "endpoint": "messages",
             "status_code": 200, "billing": None, "usage": {}, "meta": {},
             "shortcircuit": {**sc, "upstream_called": False,
                              "synthetic_message_id": msg_id,
                              "note": "wrap-up turn answered locally; upstream "
                                      "NOT called; 0 tokens billed"}})
        _tools = sc.get("tools") or sc.get("tool") or sc.get("tool_use_ids") or []
        print(f"[shortcircuit] #{n} {agent}/{role} elided wrap-up after "
              f"{','.join(_tools) if isinstance(_tools, list) else _tools} -> "
              f"{sc['ack']!r} (upstream skipped, 0 tokens)", flush=True)
        return StreamingResponse(iter([blob]), status_code=200,
                                 media_type="text/event-stream")

    # ---- forward upstream; tee the response stream to a .sse file ----
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in core_mod._HOP}
    fwd_headers["accept-encoding"] = "identity"  # force uncompressed so we can read the SSE
    # Stash this (post-transform) request so POST /_ping?session= can replay it.
    # Only the MAIN LINE (parent agent) is the session's durable, pingable
    # request: a subagent or title side-call shares the session_id but is
    # transient — it must not replace what /_ping replays nor re-anchor the
    # keep-warm hold (else we'd keep a finished subagent's context warm instead
    # of the main agent's).
    if upstream_path.split("?")[0].endswith("/v1/messages"):
        if not title_call and not writer_mod._is_subagent_role(role):
            pinger_mod._cache_last_request(session_id, obj, fwd_headers, upstream_path,
                                account_uuid)
            if "hold_echo" not in record:      # the arming turn itself isn't
                hold_mod._hold_note_real_turn(session_id)   # organic; real turns restart
                                                   # the ping budget + window
    req = core_mod._client.build_request(request.method, core_mod.UPSTREAM + upstream_path,
                                headers=fwd_headers, content=raw)
    up = await core_mod._client.send(req, stream=True)
    resp_headers = {k: v for k, v in up.headers.items()
                    if k.lower() not in {"connection", "transfer-encoding",
                                         "content-length", "keep-alive"}}
    base_path = upstream_path.split("?")[0]
    is_messages = base_path.endswith("/v1/messages")
    is_count = base_path.endswith("/count_tokens")
    capture = is_messages or is_count
    chunks = []

    mutate = is_messages and transforms_mod._resp_mutating()
    relay = is_messages and transforms_mod._relay_active()
    buffer_resp = mutate or relay    # both need the full SSE before we can rewrite

    # Generic subscriber tee (SUBSCRIBERS.md): agent-identified routes (m)
    # only, never plain Claude Code traffic; None when no registered
    # subscriber matches this agent, so plain traffic pays nothing.
    sub_tee = (subs_mod._tee_for(agent, session_id, f"{n}-{ts}")
               if m is not None and is_messages else None)

    async def body_iter():
        out_blob = None
        try:
            async for chunk in up.aiter_raw():
                if capture:
                    chunks.append(chunk)
                if not buffer_resp:     # stream verbatim; when buffering we hold
                    yield chunk
                if sub_tee is not None:  # after yield: client bytes come first
                    sub_tee.feed(chunk)
            if buffer_resp and chunks:
                full = b"".join(chunks)
                # relay stashes prose + blanks it as a side effect; compute ONCE
                out_blob = transforms_mod._relay_capture_and_strip(full) if relay else transforms_mod._mutate_sse(full)
                yield out_blob          # emit rewritten response once
        finally:
            if sub_tee is not None:
                sub_tee.close()     # flush the tail text.delta, if any
            await up.aclose()
            if capture and chunks:
                blob = b"".join(chunks)
                if is_messages:
                    writer_mod._enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
                    if mutate:
                        writer_mod._enqueue_bytes(out_dir / f"{stem}.response.mutated.sse",
                                       transforms_mod._mutate_sse(blob))
                    if relay and out_blob is not None:
                        writer_mod._enqueue_bytes(out_dir / f"{stem}.response.relayed.sse", out_blob)
                # everything derived from the finished response — billing, view
                # state, ledger stamp, capture, subscriber receipt — lives in
                # receipts; the closure only owns bytes and routing identity
                receipts_mod.anthropic(
                    blob, n=n, ts=ts, agent=agent, role=role, model=model,
                    session_id=session_id, session_key=session_key, obj=obj,
                    title_call=title_call, is_messages=is_messages,
                    routed=(m is not None), out_dir=out_dir, stem=stem,
                    status_code=up.status_code, resp_headers=dict(up.headers),
                    tee_text=(sub_tee.text if sub_tee is not None else None),
                    response_injection=({"append": transforms_mod.RESP_APPEND,
                                         "replace": transforms_mod.RESP_REPLACE}
                                        if mutate else None))

    return StreamingResponse(body_iter(), status_code=up.status_code,
                             headers=resp_headers,
                             media_type=up.headers.get("content-type"))


# Reload persisted state BEFORE serving: armed holds resume (skipping until
# auth is re-donated), totals continue instead of zeroing, the cwd hunt skips
# known sessions. Runs at import so the offline tests exercise it too.
restore_mod._restore_state()

app = Starlette(routes=[Route("/{path:path}", handler,
                              methods=["GET", "POST", "PUT", "DELETE"])],
                # the hold-warm driver must live on the event loop (it awaits
                # _warm_session on the shared _client)
                on_startup=[hold_mod._start_hold_loop])
