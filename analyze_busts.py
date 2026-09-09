#!/usr/bin/env python3
"""Corpus-wide cache-bust ledger: what busts, how often, what it cost, and whether
it was a compact, a lapse, or a genuine self-inflicted rewrite.

`report.bust_series` already answers this for ONE session (it is what /_bust
renders): receipt-first detection (a bust is a SHRINKING cache_read), a class
from the byte locus (tools / system / preamble / conversation / compact / lapse),
and a fault owner per class. This tool runs that engine over every session in a
capture root and AGGREGATES, which is the view a single session cannot give:

  * the same locus recurring across sessions is a PATTERN (a date rollover in
    messages[0], a tool roster that flaps when an MCP attaches, a transform
    editing a settled turn) — one session shows an incident, the corpus shows
    the habit;
  * the bill per class separates what is worth fixing from what is expected:
    a compact re-writes a small summary by design, a lapse is idle > TTL
    (keep-warm territory), a preamble/system/tools bust is a defect in
    something we control.

PRICING IS MARGINAL, NOT GROSS. A busted turn pays the write premium on tokens
it would otherwise have READ; the damage is `(write + uncached_input) x (paid
rate - read rate)` at the model's own rates, with the write TTL taken from the
receipt (1h vs 5m tokens), never assumed. The gross write is reported alongside
so nobody has to re-derive it, but the marginal number is the one that says
"this class cost us $X more than a warm run would have".

WHAT A ROW IS. One bust = one main-line transition where bust_series says
`bust: true` (lost > BUST_MIN_LOST_TOKENS). The pattern key is
`class | locus label with digits blanked | old->new snippet with digits blanked`,
so `messages[0].user changed  "Today's date is 2026-09-07" -> "...09-08"` and
the same on another day fold into one line. `restart_between` (the proxy
restarted between the two turns) is kept as its own bucket because it is a
deploy tax, not a defect of the prefix.

WHAT IT CANNOT SEE. A cold session start is not a bust (nothing was warm), so
boot cost is out of scope — that is analyze_prefix.py. The N-1 diff explains a
bust only when the lineage IS the previous forwarded turn; on a cold resume the
locus can point at a legitimate tail while the receipt says the head lapsed,
which is why the class comes from BOTH signals (report._transition_class).

KEEP-WARM PINGS get their own ledger at the end. A ping is a seat's request
replayed at `max_tokens: 1` (both pingers build that shape); it is billed like
a request and it is not a turn, so until v0.6.65 it hid inside the turn totals.
The ping section prices them per seat (cached read vs uncached tail vs any
write), flags DIRTY pings (a write, or an uncached span larger than the tail —
a ping is supposed to be a pure cache read), and names LOOPS: runs of pings
spaced at the hold tick (<= 2 min) with no organic turn between, which is what a
5m-TTL stash under a perpetual hold looks like (152 pings / $7.53 in one night
on one seat, 2026-09-09, keeping a pre-compact history warm).

Usage:
  python3 analyze_busts.py [--logs DIR] [--since DAYS] [--session ID] [--top N]
                           [--json OUT] [--min-usd X] [--no-pings] [--pings-json OUT]
Defaults: the live clodex capture root, last 14 days, top 15 per table.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_LOGS = os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs")


def _boot(logs):
    """Import the lab with LOG_DIR pointed at the corpus (core reads it at import)."""
    os.environ["LOG_DIR"] = str(logs)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import logproxy  # noqa: F401  (ordered boot; sets up the package)
    from proxylab import report, billing
    return report, billing


def _norm(s):
    """Digits and whitespace blanked so two instances of one habit share a key."""
    if not s:
        return ""
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _receipt(session_dir, stem):
    try:
        with open(session_dir / f"{stem}.response.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _request_record(session_dir, stem):
    """The full capture record (transform logs ride at the top level next to
    `body`); parsed once per bust only, never on the scan path."""
    try:
        with open(session_dir / f"{stem}.request.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _proxy_state(rec):
    """What the proxy DID on the busted request — the join that turns a locus
    into a cause. Returns (tag, note) where tag is a short stable key for
    grouping and note is the human line. Order matters: the first matching
    condition wins, and each one is a defect class measured on the corpus."""
    body = rec.get("body") or {}
    msgs = body.get("messages") or []
    msg_markers = sum(1 for m in msgs if isinstance(m, dict)
                      and isinstance(m.get("content"), list)
                      and any(isinstance(b, dict) and b.get("cache_control") for b in m["content"]))
    gate = rec.get("midturn_marker_gate") or {}
    pin = rec.get("pin_settled_breakpoint") or {}
    scc = rec.get("strip_compact_cache") or {}
    if scc.get("condition_met"):
        return ("compact_strip_fired",
                f"strip_compact_cache removed the history marker (ledger said "
                f"{scc.get('warmth_state')}) — retired hard-off in v0.6.58; a firing "
                "means the capture predates the vendored deploy of that build")
    if gate.get("mode") == "dropped" and msg_markers == 0:
        return ("gate_left_no_marker",
                f"marker gate dropped the tail and nothing else anchored messages "
                f"(pin: {pin.get('reason') or 'n/a'}); history re-read at 1x")
    if gate.get("mode") == "dropped_rebased":
        return ("gate_rebased", "marker gate dropped the tail and re-anchored the boundary (fixed shape)")
    if msg_markers == 0 and msgs:
        return ("no_message_marker",
                "request forwarded with no message-level cache marker at all")
    if rec.get("transform_error_likely_bust"):
        return ("transform_error", "a transform failed open; verbatim forward on a strip-latched session")
    return (None, None)


_ROLE_SEP_RE = re.compile(r"-(parent|subagent|unknown|codex|ext)-")


def _agent_of(stem):
    # <seq>-<agent...>-<role>-<model>-<hhmmss>; the route name itself carries
    # dashes (clodex-wirescope-597e9059), so cut at the role token, not the
    # first dash
    head = stem.split("-", 1)[1] if "-" in stem else "?"
    m = _ROLE_SEP_RE.search("-" + head + "-")
    return head[: m.start() - 1] if m and m.start() > 1 else head.split("-")[0]


def price_bust(t, receipt, billing):
    """(marginal_usd, gross_write_usd, write_ttl) for one bust transition."""
    b = receipt.get("billing") or {}
    model = b.get("model")
    tok = b.get("tokens") or {}
    rates = billing._price_for(model, speed=tok.get("speed"))
    if not rates:
        return None, None, None
    w1 = tok.get("cache_write_1h_tokens") or 0
    w5 = tok.get("cache_write_5m_tokens") or 0
    ttl = "1h" if w1 >= w5 else "5m"
    wrate = rates["cache_write_1h" if ttl == "1h" else "cache_write_5m"]
    write = t["write_tokens"] or 0
    inp = t["uncached_input"] or 0
    gross = (write * wrate + inp * rates["in"]) / 1e6
    marginal = (write * (wrate - rates["cache_read"]) + inp * (rates["in"] - rates["cache_read"])) / 1e6
    return round(marginal, 4), round(gross, 4), ttl


def pattern_key(t):
    loc = t.get("locus") or {}
    label = _norm(loc.get("label") or "(no byte diff: clean extension)")
    snip = ""
    if loc.get("old") is not None or loc.get("new") is not None:
        snip = f'{_norm(loc.get("old"))!r}->{_norm(loc.get("new"))!r}'
    cls = t.get("class") or "?"
    if t.get("restart_between"):
        cls = f"{cls}+restart"
    return cls, label, snip


def scan(logs, since_days, only_session, report, billing, min_usd=0.0):
    root = Path(logs)
    cutoff = time.time() - since_days * 86400 if since_days else 0
    rows = []
    sessions = 0
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if only_session and not d.name.startswith(only_session):
            continue
        try:
            if d.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        try:
            res = report.bust_series(d.name, detail=False)
        except Exception as e:  # one broken dir must not kill the ledger
            print(f"[skip] {d.name}: {e}", file=sys.stderr)
            continue
        sessions += 1
        if sessions % 100 == 0:
            print(f"[scan] {sessions} sessions, {len(rows)} busts", file=sys.stderr)
        for t in res.get("busts") or []:
            rc = _receipt(d, t["stem"])
            marginal, gross, ttl = price_bust(t, rc, billing)
            if marginal is not None and marginal < min_usd:
                continue
            cls, label, snip = pattern_key(t)
            ptag, pnote = _proxy_state(_request_record(d, t["stem"]))
            prev_ts = report._head_ts(d / f'{t["from_stem"]}.request.json')
            gap = None
            e1, e0 = report._epoch(t["ts"]), report._epoch(prev_ts)
            if e1 is not None and e0 is not None:
                gap = round(e1 - e0)
            rows.append({
                "session": d.name, "agent": _agent_of(t["stem"]), "stem": t["stem"],
                "ts": t["ts"], "gap_s": gap,
                "class": cls, "fault": t.get("fault"), "severity": t["severity"],
                "label": label, "snippet": snip,
                "lost_tokens": t["lost_tokens"], "write_tokens": t["write_tokens"],
                "uncached_input": t["uncached_input"], "read_tokens": t["read_tokens"],
                "prev_messages": t["prev_messages"], "cur_messages": t["cur_messages"],
                "model": (rc.get("billing") or {}).get("model"), "ttl": ttl,
                "marginal_usd": marginal, "gross_write_usd": gross,
                "fix_hint": t.get("fix_hint"),
                "proxy_state": ptag, "proxy_note": pnote,
                "locus": t.get("locus"),
            })
    return sessions, rows


def _usd(x):
    return f"${x:,.2f}"


def _table(title, groups, top, key_fmt):
    print(f"\n== {title}")
    print(f"{'busts':>6} {'sessions':>8} {'marginal':>10} {'gross':>10} {'lost tok':>11}  key")
    ranked = sorted(groups.items(), key=lambda kv: -kv[1]["usd"])
    for k, g in ranked[:top]:
        print(f"{g['n']:>6} {len(g['sessions']):>8} {_usd(g['usd']):>10} {_usd(g['gross']):>10} "
              f"{g['lost']:>11,}  {key_fmt(k)}")
    if len(ranked) > top:
        rest = ranked[top:]
        print(f"{sum(g['n'] for _, g in rest):>6} {'':>8} {_usd(sum(g['usd'] for _, g in rest)):>10}"
              f" {'':>10} {'':>11}  … {len(rest)} more")


def _group(rows, keyf):
    groups = defaultdict(lambda: {"n": 0, "usd": 0.0, "gross": 0.0, "lost": 0,
                                  "sessions": set(), "example": None})
    for r in rows:
        g = groups[keyf(r)]
        g["n"] += 1
        g["usd"] += r["marginal_usd"] or 0.0
        g["gross"] += r["gross_write_usd"] or 0.0
        g["lost"] += r["lost_tokens"] or 0
        g["sessions"].add(r["session"])
        if g["example"] is None or (r["marginal_usd"] or 0) > (g["example"]["marginal_usd"] or 0):
            g["example"] = r
    return groups


_MAX_TOKENS_RE = re.compile(rb'"max_tokens":\s*(\d+)')
PING_LOOP_GAP_S = 120        # two hold ticks: pings this close with no turn between = a loop
PING_LOOP_MIN = 5


def _is_ping_capture(path, report):
    """Cheap keep-warm test for one request capture: the summary flag when the
    capture carries one (v0.6.65+), else a head read for `max_tokens` — the CLI
    and both pingers put it before `messages`, so 8 KB reaches it; a miss
    falls through to a full parse (correct slow path)."""
    summ = report._tail_summary(path)
    if summ and "keepwarm" in summ:
        return bool(summ["keepwarm"]), summ
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False, summ
    k = head.find(b'"body"')
    m = _MAX_TOKENS_RE.search(head, k if k >= 0 else 0)
    if m is not None and summ is not None:
        return (int(m.group(1)) == 1 and bool(summ.get("n_tools"))), summ
    rec = _request_record(path.parent, path.name[: -len(".request.json")])
    body = rec.get("body") if isinstance(rec, dict) else None
    ok = isinstance(body, dict) and bool(body.get("tools")) and body.get("max_tokens") == 1
    return ok, (rec.get("summary") if isinstance(rec, dict) else summ)


def scan_pings(logs, since_days, only_session, report, billing):
    """Every keep-warm ping in the window, priced off its receipt, with the
    organic-turn context needed to call a run of them a loop."""
    root = Path(logs)
    cutoff = time.time() - since_days * 86400 if since_days else 0
    rows = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if only_session and not d.name.startswith(only_session):
            continue
        try:
            if d.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        # chronological walk of the dir: a ping's "previous request" decides
        # whether it extends a loop or follows an organic turn
        files = []
        for rf in d.glob("*.request.json"):
            try:
                mt = rf.stat().st_mtime
            except OSError:
                continue
            if mt < cutoff:
                continue
            files.append((mt, rf))
        files.sort()
        prev_kind = {}          # agent -> ("ping"|"turn", mtime)
        for mt, rf in files:
            ping, summ = _is_ping_capture(rf, report)
            agent = _agent_of(rf.name)
            if not ping:
                if summ and summ.get("n_tools"):
                    prev_kind[agent] = ("turn", mt)
                continue
            stem = rf.name[: -len(".request.json")]
            rc = _receipt(d, stem)
            bill = rc.get("billing") or {}
            t = bill.get("tokens") or {}
            model = bill.get("model") or (summ or {}).get("model")
            rates = billing._price_for(model) if model else None
            rd = t.get("cache_read_input_tokens") or 0
            inp = t.get("input_tokens") or 0
            w5 = t.get("cache_write_5m_tokens") or 0
            w1 = t.get("cache_write_1h_tokens") or 0
            wr = (w5 + w1) or (t.get("cache_write_flat_tokens") or 0)
            wj = _load_json(d / f"{stem}.warmth.json")
            pk = prev_kind.get(agent)
            rows.append({
                "session": d.name, "agent": agent, "stem": stem, "mtime": mt,
                "status": rc.get("status_code"), "model": model,
                "ttl": (wj or {}).get("ttl"),
                "read_tokens": rd, "uncached_input": inp, "write_tokens": wr,
                "read_usd": (_usd_of(rd, rates["cache_read"]) if rates else None),
                "uncached_usd": (_usd_of(inp, rates["in"]) if rates else None),
                "write_usd": ((_usd_of(w5, rates["cache_write_5m"])
                               + _usd_of(w1, rates["cache_write_1h"])) if rates else None),
                "est_usd": bill.get("est_usd"),
                # dirty = not a pure cache read: it wrote, or it re-read more
                # than a tail's worth uncached (the tail is ~2k tok on a seat)
                "dirty": bool(wr) or inp > 4096,
                "after": pk[0] if pk else None,
                "gap_s": round(mt - pk[1]) if pk else None,
            })
            prev_kind[agent] = ("ping", mt)
    return rows


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _usd_of(tokens, rate):
    return round((tokens or 0) * rate / 1e6, 6)


def ping_loops(rows):
    """Runs of >= PING_LOOP_MIN pings on one seat, each within PING_LOOP_GAP_S
    of the previous PING (no organic turn between). Returns [(agent, session,
    [rows])], costliest first."""
    by_seat = defaultdict(list)
    for r in rows:
        by_seat[(r["agent"], r["session"])].append(r)
    loops = []
    for (agent, sess), L in by_seat.items():
        L.sort(key=lambda r: r["mtime"])
        run = []
        for r in L:
            if run and r["after"] == "ping" and r["gap_s"] is not None and r["gap_s"] <= PING_LOOP_GAP_S:
                run.append(r)
            else:
                if len(run) >= PING_LOOP_MIN:
                    loops.append((agent, sess, run))
                run = [r]
        if len(run) >= PING_LOOP_MIN:
            loops.append((agent, sess, run))
    loops.sort(key=lambda x: -sum(r["est_usd"] or 0 for r in x[2]))
    return loops


def render_pings(rows, top, since_days):
    print(f"\n# Keep-warm pings — last {since_days} days · {len(rows)} pings"
          f" · {_usd(sum(r['est_usd'] or 0 for r in rows))}")
    if not rows:
        return
    ok = [r for r in rows if r["status"] == 200]
    dirty = [r for r in ok if r["dirty"]]
    print(f"clean (200, pure cache read): {len(ok) - len(dirty)} · dirty (wrote, or >4k uncached):"
          f" {len(dirty)} · failed (non-200): {len(rows) - len(ok)}")
    by = defaultdict(lambda: {"n": 0, "usd": 0.0, "read": 0.0, "unc": 0.0, "wr": 0.0,
                              "dirty": 0, "ttl": set(), "gaps": []})
    for r in rows:
        g = by[(r["agent"], r["session"][:8])]
        g["n"] += 1
        g["usd"] += r["est_usd"] or 0
        g["read"] += r["read_usd"] or 0
        g["unc"] += r["uncached_usd"] or 0
        g["wr"] += r["write_usd"] or 0
        g["dirty"] += 1 if r["dirty"] else 0
        if r["ttl"]:
            g["ttl"].add(r["ttl"])
        if r["after"] == "ping" and r["gap_s"] is not None:
            g["gaps"].append(r["gap_s"])
    print(f"\n{'seat':28} {'sess':8} {'pings':>5} {'usd':>7} {'read$':>6} {'unc$':>6} {'write$':>6}"
          f" {'dirty':>5} {'ttl':>9} {'ping→ping p50':>13}")
    for (agent, sess), g in sorted(by.items(), key=lambda kv: -kv[1]["usd"])[:top]:
        gaps = sorted(g["gaps"])
        p50 = f"{gaps[len(gaps) // 2]}s" if gaps else "-"
        ttl = "/".join(str(x) for x in sorted(g["ttl"])) or "-"
        print(f"{agent[:28]:28} {sess:8} {g['n']:5} {g['usd']:7.2f} {g['read']:6.2f} {g['unc']:6.2f}"
              f" {g['wr']:6.2f} {g['dirty']:5} {ttl:>9} {p50:>13}")
    loops = ping_loops(rows)
    if loops:
        print(f"\n== Ping loops (>= {PING_LOOP_MIN} pings <= {PING_LOOP_GAP_S}s apart, no organic"
              " turn between): a hold whose stash TTL is inside the ping margin")
        for agent, sess, run in loops[:top]:
            cost = sum(r["est_usd"] or 0 for r in run)
            hrs = (run[-1]["mtime"] - run[0]["mtime"]) / 3600
            t0 = time.strftime("%m-%d %H:%M", time.localtime(run[0]["mtime"]))
            t1 = time.strftime("%H:%M", time.localtime(run[-1]["mtime"]))
            ttl = run[0]["ttl"]
            rd = run[0]["read_tokens"]
            print(f"  {agent[:28]:28} {sess[:8]} {t0}→{t1} {len(run):4} pings {hrs:4.1f}h"
                  f" {_usd(cost):>8}  ttl={ttl}  read/ping={rd:,} tok")
    if dirty:
        print("\n== Dirty pings (a ping should be a pure cache read)")
        for r in sorted(dirty, key=lambda r: -(r["write_tokens"] + r["uncached_input"]))[:top]:
            print(f"  {r['session'][:8]} {r['stem'][:44]} write={r['write_tokens']:,}"
                  f" uncached={r['uncached_input']:,} read={r['read_tokens']:,} ttl={r['ttl']}")


def render(sessions, rows, top, since_days, logs):
    total = sum(r["marginal_usd"] or 0 for r in rows)
    gross = sum(r["gross_write_usd"] or 0 for r in rows)
    unpriced = sum(1 for r in rows if r["marginal_usd"] is None)
    print(f"# Bust ledger — {logs}")
    print(f"window: last {since_days} days · sessions scanned: {sessions} · busts: {len(rows)}"
          f" · marginal cost {_usd(total)} (gross write {_usd(gross)})"
          + (f" · {unpriced} unpriced" if unpriced else ""))

    by_fault = _group(rows, lambda r: r["fault"] or "?")
    _table("By fault owner (content = our prefix changed; self = our transform flapped; "
           "environment = TTL lapse or compact)", by_fault, top, str)

    by_class = _group(rows, lambda r: r["class"])
    _table("By class", by_class, top, str)

    by_proxy = _group(rows, lambda r: r["proxy_state"] or "(no proxy-side signal)")
    print("\n== By proxy action on the busted request (the join that names OUR defects)")
    print(f"{'busts':>6} {'sessions':>8} {'marginal':>10}  proxy state")
    for k, g in sorted(by_proxy.items(), key=lambda kv: -kv[1]["usd"])[:top]:
        print(f"{g['n']:>6} {len(g['sessions']):>8} {_usd(g['usd']):>10}  {k}")
        if g["example"] and g["example"].get("proxy_note"):
            print(f"{'':>27}{g['example']['proxy_note']}")

    by_pat = _group(rows, lambda r: (r["class"], r["label"], r["snippet"]))
    print("\n== Recurring patterns (class | locus | old->new snippet, digits blanked)")
    print(f"{'busts':>6} {'sessions':>8} {'marginal':>10}  pattern")
    for k, g in sorted(by_pat.items(), key=lambda kv: -kv[1]["usd"])[:top]:
        cls, label, snip = k
        ex = g["example"]
        print(f"{g['n']:>6} {len(g['sessions']):>8} {_usd(g['usd']):>10}  [{cls}] {label}"
              + (f"  {snip}" if snip else ""))
        print(f"{'':>27}e.g. {ex['session'][:8]} {ex['stem'][:40]} "
              f"lost {ex['lost_tokens']:,} tok, gap {ex['gap_s']}s, "
              f"{ex['prev_messages']}->{ex['cur_messages']} msgs")
        if ex.get("fix_hint"):
            print(f"{'':>27}fix: {ex['fix_hint']}")

    by_agent = _group(rows, lambda r: r["agent"])
    _table("By agent (route name)", by_agent, top, str)

    by_sess = _group(rows, lambda r: r["session"])
    _table("By session", by_sess, top, lambda s: s)

    # idle-gap texture for lapses: is it TTL expiry or something faster?
    lapses = [r for r in rows if r["class"].startswith("lapse") and r["gap_s"] is not None]
    if lapses:
        gaps = sorted(r["gap_s"] for r in lapses)
        q = lambda p: gaps[min(len(gaps) - 1, int(p * len(gaps)))]
        under = sum(1 for g in gaps if g < 300)
        print(f"\n== Lapse idle gaps: n={len(gaps)} p10={q(.1)}s p50={q(.5)}s p90={q(.9)}s"
              f" · {under} under 5 min (a 'lapse' inside the TTL is NOT a lapse — look at those)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--since", type=float, default=14, help="days (0 = all)")
    ap.add_argument("--session", default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-usd", type=float, default=0.0)
    ap.add_argument("--json", default=None, help="write the per-bust rows here")
    ap.add_argument("--no-pings", action="store_true", help="skip the keep-warm ping ledger")
    ap.add_argument("--pings-json", default=None, help="write the per-ping rows here")
    a = ap.parse_args()
    report, billing = _boot(a.logs)
    sessions, rows = scan(a.logs, a.since, a.session, report, billing, a.min_usd)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1, default=str)
        print(f"[json] {len(rows)} rows -> {a.json}", file=sys.stderr)
    render(sessions, rows, a.top, a.since, a.logs)
    if not a.no_pings:
        prow = scan_pings(a.logs, a.since, a.session, report, billing)
        if a.pings_json:
            with open(a.pings_json, "w", encoding="utf-8") as f:
                json.dump(prow, f, indent=1, default=str)
            print(f"[json] {len(prow)} ping rows -> {a.pings_json}", file=sys.stderr)
        render_pings(prow, a.top, a.since)


if __name__ == "__main__":
    main()
