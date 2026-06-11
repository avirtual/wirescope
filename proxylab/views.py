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
from proxylab import hold as hold_mod
from proxylab import meta as meta_mod
from proxylab import warmth as warmth_mod
from proxylab import writer as writer_mod

# --- /_admin: the /_status snapshot rendered for humans ------------------------
# Same read-only data, as a self-refreshing HTML page — JSON is for tools, this
# is for eyeballs. Server-rendered, zero JS, escapes everything (titles are
# model output). Lab-grade like the other endpoints: localhost, unauthenticated.

def _fmt_ago(ts, now=None):
    if not ts:
        return "—"
    d = max(0.0, (now or time.time()) - ts)
    if d < 60:
        return f"{int(d)}s ago"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{d / 3600:.1f}h ago"
    return f"{d / 86400:.1f}d ago"


def _fmt_dur(s):
    if s is None:
        return "?"
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _fmt_tok(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


_ADMIN_CSS = """
body{background:#14161a;color:#cdd3dd;font:13px/1.5 ui-monospace,Menlo,monospace;
     margin:1.2em auto;max-width:1180px;padding:0 1em}
a{color:#6ab0de;text-decoration:none} a:hover{text-decoration:underline}
h1{font-size:16px;color:#e6ebf2} h1 small{color:#69707d;font-weight:normal}
table{border-collapse:collapse;width:100%;margin:.8em 0}
th{color:#8a93a3;text-align:left;font-weight:normal;border-bottom:1px solid #2a2e36}
th,td{padding:.32em .6em;vertical-align:top}
tr:nth-child(even) td{background:#191c21}
.kv span{margin-right:1.4em;white-space:nowrap}
.kv b{color:#e6ebf2;font-weight:600}
.warm{color:#7ec699}.cold{color:#6ab0de}.absent{color:#69707d}
.bad{color:#e06c75}.warn{color:#e5c07b}.dim{color:#69707d}
.badge{border:1px solid #2a2e36;border-radius:3px;padding:0 .35em;margin-right:.3em}
.on{color:#7ec699}.off{color:#69707d}
code{color:#9aa3b2}
"""


def _render_admin_html(snap, host=""):
    e = html.escape
    p = snap["proxy"]
    t = p["totals"]
    s0 = p.get("totals_since_start") or {}
    now = time.time()
    flags = " ".join(
        f'<span class="badge {"on" if v else "off"}">{e(k)}</span>'
        for k, v in (p.get("flags") or {}).items())
    ref = t.get("refusals") or 0
    unp = t.get("unpriced_requests") or 0
    head = (
        f'<h1>logproxy <small>· {e(p.get("version") or "?")} '
        f'· {e(p["log_dir"])} @ {e(host or "localhost")} '
        f'&rarr; {e(p["upstream"])}</small></h1>'
        f'<p class="kv">'
        f'<span>up <b>{e(_fmt_dur(p["uptime_s"]))}</b></span>'
        f'<span>{flags}</span>'
        f'<span>holds <b>{p["holds_armed"]}</b></span>'
        f'<span>replayable <b>{p["tracked_last_requests"]}</b></span></p>'
        f'<p class="kv">'
        f'<span>requests <b>{t["requests"]}</b></span>'
        f'<span>in <b>{e(_fmt_tok(t["input_tokens"]))}</b></span>'
        f'<span>out <b>{e(_fmt_tok(t["output_tokens"]))}</b></span>'
        f'<span>cache r/w <b>{e(_fmt_tok(t["cache_read_tokens"]))}/'
        f'{e(_fmt_tok(t["cache_write_tokens"]))}</b></span>'
        f'<span>est <b>${t["est_usd"]:.4f}</b>'
        f'{f" <span class=warn>(+{unp} unpriced)</span>" if unp else ""}</span>'
        f'<span class="{"bad" if ref else "dim"}">refusals <b>{ref}</b></span>'
        f'<span class="dim">since restart: {s0.get("requests", 0):g} req / '
        f'${s0.get("est_usd", 0):.4f}</span></p>')
    rows = []
    for s in snap["sessions"]:
        w = s["warmth"]
        if w["state"] == "warm":
            warmth = (f'<span class="warm">&#128293; '
                      f'{e(_fmt_dur(w["remaining_s"]))} left</span>'
                      f'<br><span class="dim">ttl {e(_fmt_dur(w["ttl_s"]))}</span>')
        elif w["state"] == "cold":
            warmth = '<span class="cold">&#10052;&#65039; cold</span>'
        else:
            warmth = '<span class="absent">&empty;</span>'
        h = s.get("hold")
        if h:
            hold = (f'until {time.strftime("%H:%M", time.localtime(h["until"]))} '
                    f'· {h["pings"]}/{h.get("expected_pings", hold_mod.WARMTH_HOLD_MAX_PINGS)} pings')
            if h.get("failures"):
                hold += f' <span class="bad">{h["failures"]} fail</span>'
            if h.get("last_result"):
                hold += f'<br><span class="dim">{e(str(h["last_result"]))}</span>'
        else:
            hold = '<span class="dim">—</span>'
        if s.get("pingable"):
            ping = '<span class="warm">yes</span>'
        elif s.get("awaiting_auth"):
            ping = '<span class="warn">awaiting auth</span>'
        else:
            ping = '<span class="dim">no</span>'
        c = s.get("cost")
        cost = (f'${c["est_usd"]:.4f}<br><span class="dim">{c["requests"]} req'
                f'</span>') if c else '<span class="dim">—</span>'
        sref = s.get("refusals") or 0
        sid = s["session_id"]
        kindb = (f' <span class="badge off">&#129302; {e(s["kind"])}</span>'
                 if s.get("kind") else "")
        en = s.get("ended")
        if en:   # ended but resumable; debug state stays until the sweep
            kindb += (f' <span class="badge off">&#127937; ended'
                      f'{" · " + e(str(en["reason"])) if en.get("reason") else ""}'
                      f'</span>')
        # title is [agent] when routed; keep the learned summary visible too
        summ = s.get("summary")
        summ = (f' <span class="dim">{e(summ)}</span>'
                if summ and summ != s.get("title") else "")
        rows.append(
            f'<tr><td>{warmth}</td>'
            f'<td><b><a href="/_session?session={e(sid)}">'
            f'{e(s.get("title") or "(untitled)")}</a></b>{summ}{kindb}<br>'
            f'<a href="/_status?session={e(sid)}"><code>{e(sid[:8])}…</code></a> '
            f'<span class="dim">{e(writer_mod._short_model(s.get("model")))}</span><br>'
            f'<span class="dim">{e(s.get("cwd") or "")}</span></td>'
            f'<td>{e(_fmt_ago(s.get("last_seen"), now))}</td>'
            f'<td>{hold}</td><td>{ping}</td><td>{cost}</td>'
            f'<td class="{"bad" if sref else "dim"}">{sref or "—"}</td></tr>')
    table = ('<table><tr><th>warmth</th><th>session</th><th>last seen</th>'
             '<th>hold</th><th>pingable</th><th>cost</th><th>ref</th></tr>'
             + "".join(rows) + "</table>") if rows else "<p class=dim>no sessions tracked</p>"
    foot = ('<p class="dim">auto-refresh 10s · <a href="/_admin">last 24h</a> · '
            '<a href="/_admin?all=1">all</a> · <a href="/_status">raw json</a></p>')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta http-equiv="refresh" content="10">'
            f'<title>logproxy · {e(p["log_dir"])}</title>'
            f'<style>{_ADMIN_CSS}</style></head><body>'
            + head + table + foot + "</body></html>")


# --- /_session: simplified view of a session's captured context ----------------
# Renders the session's REPLAYABLE LAST REQUEST — the post-transform body the
# pinger would replay, i.e. the exact context the model saw on the session's
# last turn (in-memory entry; SQLite `last_request` row after a restart) — as a
# human-readable inventory: tools / system blocks / message timeline with
# previews. BODY ONLY: headers (incl. auth) are never rendered. Static snapshot,
# no auto-refresh (pages can be large). Zero JS — expansion is <details>.

_SESSION_CSS = _ADMIN_CSS + """
.blk{border:1px solid #2a2e36;border-left-width:3px;border-radius:4px;
     margin:.45em 0;padding:.3em .6em}
.blk .sz{float:right;color:#69707d;margin-left:1em}
.user{border-left-color:#6ab0de}.assistant{border-left-color:#7ec699}
.tooluse{border-left-color:#e5c07b}.toolres{border-left-color:#c678dd}
.sysb{border-left-color:#e06c75}
pre{white-space:pre-wrap;word-break:break-word;color:#aab2c0;margin:.3em 0;
    background:#101317;padding:.5em;border-radius:4px;max-height:30em;overflow:auto}
details>summary{cursor:pointer;color:#6ab0de}
.turnhdr{margin:1.2em 0 .35em;padding-bottom:.15em;color:#9aa3b2;
         font-weight:bold;border-bottom:1px solid #2a2e36}
"""

_MD_HEADING_RE = re.compile(r"(?m)^#{1,3} .+$")


def _load_last_request_row(session_id):
    """Read this proxy's persisted replayable request straight from SQLite —
    fallback for entries not in memory (e.g. evicted past the cap)."""
    try:
        con = warmth_mod._warmth_db()
        with warmth_mod._DB_LOCK:
            r = con.execute("SELECT path, ts, body FROM last_request "
                            "WHERE owner=? AND session_id=?",
                            (warmth_mod._OWNER, session_id)).fetchone()
        if r:
            body = json.loads(r[2])
            # openai rows never need auth — they exist for the view only
            return {"obj": body, "path": r[0], "ts": r[1],
                    "needs_auth": not codex_mod._is_openai_body(body)}
    except Exception as e:
        print(f"[session] last_request read failed for {session_id[:12]}…: {e}",
              flush=True)
    return None


def _flat_text(content):
    """All human-readable text under a content value (str | block list)."""
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                out.append(_flat_text(b.get("content")))
    return "\n".join(x for x in out if x)


def _prevu(text, cap=350, full_cap=60000):
    """Escaped preview <pre> + a <details> with the (capped) full text."""
    t = text or ""
    if not t:
        return ""
    if len(t) <= cap:
        return f"<pre>{html.escape(t)}</pre>"
    more = "" if len(t) <= full_cap else f"\n… (+{len(t) - full_cap:,} more ch)"
    return (f"<pre>{html.escape(t[:cap])}…</pre>"
            f"<details><summary>show all {len(t):,} ch</summary>"
            f"<pre>{html.escape(t[:full_cap])}{html.escape(more)}</pre></details>")


def _render_session_openai_body(entry, resp=None):
    """The /_session body for a codex/openai-wire entry: Responses-API shape —
    instructions (one system-like block), tools (name OR built-in type), input
    items (message / function_call / function_call_output / reasoning). No
    cache badges: caching is server-side (prompt_cache_key shown instead)."""
    e = html.escape
    obj = entry.get("obj") or {}
    tools = obj.get("tools") or []
    instr = obj.get("instructions") or ""
    inp = [it for it in (obj.get("input") or []) if isinstance(it, dict)]
    t_ch = len(json.dumps(tools)) if tools else 0
    i_ch = len(json.dumps(inp)) if inp else 0
    n_turns = sum(1 for it in inp if codex_mod._is_prompt_item_openai(it))
    pck = obj.get("prompt_cache_key") or ""
    bar = (f'<p class="kv"><span>captured <b>{e(_fmt_ago(entry.get("ts")))}</b> '
           f'<span class="badge on">openai wire</span></span>'
           f'<span>tools <b>{len(tools)}</b> &approx;{e(_fmt_tok(t_ch // 4))} tok</span>'
           f'<span>instructions <b>{len(instr):,}</b> ch</span>'
           f'<span>input <b>{len(inp)}</b> items &approx;{e(_fmt_tok(i_ch // 4))} tok</span>'
           f'{f"<span>turns <b><a href=#turn-{n_turns}>{n_turns}</a></b></span>" if n_turns else ""}'
           f'<span class="dim">server-side cache'
           f'{" · key " + e(pck[:13]) + "…" if pck else ""}</span></p>')
    if tools:
        trs = "".join(
            f'<tr><td><b>{e(t.get("name") or t.get("type") or "?")}</b></td>'
            f'<td class="dim">{len(json.dumps(t)):,} ch</td>'
            f'<td class="dim">{e((t.get("description") or "")[:120])}</td></tr>'
            for t in sorted(tools, key=lambda t: -len(json.dumps(t))))
        tools_html = (f'<details><summary>tools · {len(tools)} · '
                      f'&approx;{e(_fmt_tok(t_ch // 4))} tok</summary>'
                      f'<table>{trs}</table></details>')
    else:
        tools_html = '<p class="dim">no tools</p>'
    sb = ""
    if instr:
        heads = _MD_HEADING_RE.findall(instr)
        hl = " · ".join(e(h.lstrip("# ")) for h in heads[:12])
        sb = (f'<div class="blk sysb"><span class="sz">{len(instr):,} ch</span>'
              f'<span class="role">instructions</span> '
              f'<span class="dim">{hl}</span>{_prevu(instr, cap=160)}</div>')
    rows = []
    turn = 0
    for i, it in enumerate(inp):
        t = it.get("type")
        if codex_mod._is_prompt_item_openai(it):
            turn += 1
            cur = (' · <span class="warm">current</span>'
                   if turn == n_turns else '')
            rows.append(f'<div class="turnhdr" id="turn-{turn}">'
                        f'turn {turn}{cur}</div>')
        if t == "message":
            role = it.get("role", "?")
            for c in (it.get("content") or []):
                if not isinstance(c, dict):
                    continue
                txt = c.get("text") or ""
                machine = (' <span class="warn">[context]</span>'
                           if txt.lstrip().startswith("<") else "")
                cls = "assistant" if role == "assistant" else e(role)
                rows.append(f'<div class="blk {cls}">'
                            f'<span class="sz">{len(txt):,} ch</span>'
                            f'<span class="role">#{i} {e(role)}</span>'
                            f'{machine}{_prevu(txt)}</div>')
        elif t == "function_call":
            args = it.get("arguments") or ""
            rows.append(f'<div class="blk tooluse">'
                        f'<span class="sz">{len(args):,} ch</span>'
                        f'<span class="role">#{i} assistant</span> '
                        f'function_call <b>{e(it.get("name") or "?")}</b>'
                        f'{_prevu(args, cap=200)}</div>')
        elif t == "function_call_output":
            out = it.get("output")
            txt = out if isinstance(out, str) else json.dumps(out or "")
            rows.append(f'<div class="blk toolres">'
                        f'<span class="sz">{len(txt):,} ch</span>'
                        f'<span class="role">#{i} tool</span> '
                        f'function_call_output{_prevu(txt, cap=200)}</div>')
        elif t == "reasoning":
            enc = it.get("encrypted_content") or ""
            summ = "\n".join(s.get("text") or "" for s in (it.get("summary") or [])
                             if isinstance(s, dict))
            rows.append(f'<div class="blk assistant">'
                        f'<span class="sz">{len(enc):,} ch</span>'
                        f'<span class="role">#{i} assistant</span> reasoning '
                        f'<span class="dim">(encrypted)</span>'
                        f'{_prevu(summ, cap=120)}</div>')
        else:
            rows.append(f'<div class="blk"><span class="role">#{i}</span> '
                        f'<span class="dim">{e(str(t))}</span></div>')
    if (resp and resp.get("text")
            and (resp.get("ts") or 0) >= (entry.get("ts") or 0)):
        rtxt = resp["text"]
        trunc = (' <span class="dim">(preview capped)</span>'
                 if resp.get("truncated") else '')
        rows.append(f'<div class="turnhdr">answer · turn {turn or "?"} '
                    f'<span class="dim">from the wire response — not yet '
                    f'part of the next request</span></div>')
        rows.append(f'<div class="blk assistant">'
                    f'<span class="sz">{len(rtxt):,} ch</span>'
                    f'<span class="role">assistant (response)</span> '
                    f'<span class="dim">{e(str(resp.get("stop_reason") or ""))}'
                    f'</span>{trunc}{_prevu(rtxt)}</div>')
    return bar + tools_html + sb + "".join(rows)


def _render_session_html(sid, entry, snap, resp=None):
    e = html.escape
    s = (snap.get("sessions") or [{}])[0]
    w = s.get("warmth") or {}
    warmth = {"warm": (f'<span class="warm">&#128293; '
                       f'{e(_fmt_dur(w.get("remaining_s")))} left</span>'),
              "cold": '<span class="cold">&#10052;&#65039; cold</span>'
              }.get(w.get("state"), '<span class="absent">&empty;</span>')
    head = (f'<h1>{e(s.get("title") or "(untitled)")} '
            f'<small>· <code>{e(sid)}</code></small></h1>'
            f'<p class="kv"><span>{warmth}</span>'
            f'<span>model <b>{e(writer_mod._short_model(s.get("model")))}</b></span>'
            f'<span>cwd <b>{e(s.get("cwd") or "?")}</b></span>'
            f'<span class="dim">last seen {e(_fmt_ago(s.get("last_seen")))}</span></p>')
    if entry and codex_mod._is_openai_body(entry.get("obj") or {}):
        body = _render_session_openai_body(entry, resp=resp)
        foot = (f'<p class="dim"><a href="/_admin">&larr; sessions</a> · '
                f'<a href="/_status?session={e(sid)}">raw json</a> · '
                f'static snapshot (reload to refresh)</p>')
        return ('<!doctype html><html><head><meta charset="utf-8">'
                f'<title>logproxy · {e((s.get("title") or sid)[:60])}</title>'
                f'<style>{_SESSION_CSS}</style></head><body>'
                + head + body + foot + "</body></html>")
    if not entry:
        body = ('<p class="warn">no replayable request tracked for this session '
                '— nothing captured yet, evicted, or ended via /_end.</p>')
        obj = {}
    else:
        obj = entry.get("obj") or {}
        tools = obj.get("tools") or []
        sysv = obj.get("system")
        sysb = ([{"type": "text", "text": sysv}] if isinstance(sysv, str)
                else [b for b in (sysv or []) if isinstance(b, dict)])
        msgs = obj.get("messages") or []
        t_ch = len(json.dumps(tools)) if tools else 0
        s_ch = sum(len(b.get("text") or "") for b in sysb)
        m_ch = len(json.dumps(msgs)) if msgs else 0
        auth_badge = (' <span class="warn">awaiting auth (restored)</span>'
                      if entry.get("needs_auth") else "")
        n_turns = meta_mod._turn_stats(obj)["turns_in_context"]
        turns_link = (f'<span>turns <b><a href="#turn-{n_turns}">{n_turns}'
                      f'</a></b></span>' if n_turns else '')
        bar = (f'<p class="kv"><span>captured <b>{e(_fmt_ago(entry.get("ts")))}'
               f'</b>{auth_badge}</span>'
               f'<span>tools <b>{len(tools)}</b> &approx;{e(_fmt_tok(t_ch // 4))} tok</span>'
               f'<span>system <b>{len(sysb)}</b> blocks &approx;{e(_fmt_tok(s_ch // 4))} tok</span>'
               f'<span>messages <b>{len(msgs)}</b> &approx;{e(_fmt_tok(m_ch // 4))} tok</span>'
               f'{turns_link}'
               f'<span class="dim">sizes are chars; tok &approx; ch/4</span></p>')
        if tools:
            trs = "".join(
                f'<tr><td><b>{e(t.get("name", "?"))}</b></td>'
                f'<td class="dim">{len(json.dumps(t)):,} ch</td>'
                f'<td class="dim">{e((t.get("description") or "")[:120])}</td></tr>'
                for t in sorted(tools, key=lambda t: -len(json.dumps(t))))
            tools_html = (f'<details><summary>tools · {len(tools)} · '
                          f'&approx;{e(_fmt_tok(t_ch // 4))} tok</summary>'
                          f'<table>{trs}</table></details>')
        else:
            tools_html = '<p class="dim">no tools</p>'
        sb = []
        for i, b in enumerate(sysb):
            txt = b.get("text") or ""
            cc = b.get("cache_control")
            badge = (f'<span class="badge on">cache '
                     f'{e((cc.get("ttl") or "5m") if isinstance(cc, dict) else "5m")}'
                     f'</span>' if cc else "")
            heads = _MD_HEADING_RE.findall(txt)
            hl = " · ".join(e(h.lstrip("# ")) for h in heads[:12])
            sb.append(f'<div class="blk sysb"><span class="sz">{len(txt):,} ch</span>'
                      f'<span class="role">system[{i}]</span> {badge} '
                      f'<span class="dim">{hl}</span>{_prevu(txt, cap=160)}</div>')
        rows = []
        turn = 0
        for i, mm in enumerate(msgs):
            # group the timeline by turn: a divider before each prompt-bearing
            # user message (same predicate as turns_in_context — one source)
            if meta_mod._is_prompt_msg(mm):
                turn += 1
                cur = (' · <span class="warm">current</span>'
                       if turn == n_turns else '')
                rows.append(f'<div class="turnhdr" id="turn-{turn}">'
                            f'turn {turn}{cur}</div>')
            role = mm.get("role", "?")
            content = mm.get("content")
            blocks = (content if isinstance(content, list)
                      else [{"type": "text", "text": content or ""}])
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                pin = " &#128204;" if b.get("cache_control") else ""
                lbl = f'<span class="role">#{i} {e(role)}{pin}</span>'
                if bt == "text":
                    txt = b.get("text") or ""
                    rem = (' <span class="warn">[system-reminder]</span>'
                           if "<system-reminder" in txt else "")
                    rows.append(f'<div class="blk {e(role)}">'
                                f'<span class="sz">{len(txt):,} ch</span>'
                                f'{lbl}{rem}{_prevu(txt)}</div>')
                elif bt == "tool_use":
                    args = json.dumps(b.get("input") or {}, ensure_ascii=False)
                    rows.append(f'<div class="blk tooluse">'
                                f'<span class="sz">{len(args):,} ch</span>'
                                f'{lbl} tool_use <b>{e(b.get("name") or "?")}</b>'
                                f'{_prevu(args, cap=200)}</div>')
                elif bt == "tool_result":
                    txt = _flat_text(b.get("content"))
                    err = (' <span class="bad">ERROR</span>'
                           if b.get("is_error") else "")
                    rows.append(f'<div class="blk toolres">'
                                f'<span class="sz">{len(txt):,} ch</span>'
                                f'{lbl} tool_result{err}{_prevu(txt, cap=200)}</div>')
                elif bt == "thinking":
                    txt = b.get("thinking") or ""
                    rows.append(f'<div class="blk assistant">'
                                f'<span class="sz">{len(txt):,} ch</span>'
                                f'{lbl} thinking{_prevu(txt, cap=120)}</div>')
                else:
                    rows.append(f'<div class="blk">{lbl} '
                                f'<span class="dim">{e(str(bt))}</span></div>')
        # The answer to the FINAL user message lives only in the response until
        # the next turn re-ships it as input — a request-only view always
        # lagged one answer. Append it when fresher than the captured request.
        if (resp and resp.get("text")
                and (resp.get("ts") or 0) >= (entry.get("ts") or 0)):
            rtxt = resp["text"]
            trunc = (' <span class="dim">(preview capped)</span>'
                     if resp.get("truncated") else '')
            rows.append(f'<div class="turnhdr">answer · turn {turn or "?"} '
                        f'<span class="dim">from the wire response — not yet '
                        f'part of the cached prefix</span></div>')
            rows.append(f'<div class="blk assistant">'
                        f'<span class="sz">{len(rtxt):,} ch</span>'
                        f'<span class="role">assistant (response)</span> '
                        f'<span class="dim">{e(str(resp.get("stop_reason") or ""))}'
                        f'</span>{trunc}{_prevu(rtxt)}</div>')
        body = (bar + tools_html + "".join(sb) + "".join(rows))
    foot = (f'<p class="dim"><a href="/_admin">&larr; sessions</a> · '
            f'<a href="/_status?session={e(sid)}">raw json</a> · '
            f'static snapshot (reload to refresh)</p>')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            f'<title>logproxy · {e((s.get("title") or sid)[:60])}</title>'
            f'<style>{_SESSION_CSS}</style></head><body>'
            + head + body + foot + "</body></html>")
