#!/usr/bin/env python3
"""diff_sessions.py — side-by-side diff of two proxy sessions (an A/B pair).

Stop rebuilding this ad hoc. Outputs everything we keep eyeballing during an A/B:
turns / requests, total + per-request $, the three cost buckets (read / write /
output), token volumes, churn ratio, cold-read (read-retreat) count, and — the one
that silently invalidates a run — the TOOL ROSTER diff between the two arms.

Two data sources per side, tried in order:
  1. a live proxy's /_report  (pass --a-url / --b-url, or a bare port)
  2. the on-disk capture dir   (pass --a-dir / --b-dir)
The report gives receipt-exact $; the capture dir gives the per-request read curve
(retreats) and the tool rosters. Give BOTH when you can — you get the full picture.

Usage:
  # live reports (cheapest, receipt-exact $):
  ./diff_sessions.py --a 7802:SID_A --b 7800:SID_B

  # add capture dirs for roster + cold-read audit:
  ./diff_sessions.py --a 7802:SID_A --b 7800:SID_B \
      --a-dir logs_scratch_ab3/SID_A \
      --b-dir "~/Library/Application Support/clodex/wirescope/logs/SID_B"

  # capture-dir only (offline / proxy down):
  ./diff_sessions.py --a-dir DIR_A --b-dir DIR_B --a-label dev --b-label vendored
"""
import argparse, glob, json, os, sys, urllib.request

# ---------- data pulls ----------

def fetch_report(port, sid, timeout=10):
    """GET http://127.0.0.1:<port>/_report?session=<sid>&detail=1 -> dict or None."""
    url = f"http://127.0.0.1:{port}/_report?session={sid}&detail=1"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception as e:
        print(f"  ! report pull failed ({url}): {e}", file=sys.stderr)
        return None

def _body(d):
    b = d.get("body")
    return b if isinstance(b, dict) else d

def _usage(respf):
    try:
        d = json.load(open(respf))
    except Exception:
        return None
    def find(o):
        if isinstance(o, dict):
            if isinstance(o.get("usage"), dict):
                return o["usage"]
            for v in o.values():
                r = find(v)
                if r:
                    return r
        return None
    return find(d)

def scan_captures(cap_dir):
    """Walk a session's capture dir. Returns dict with tool roster (from the first
    tools-bearing request) and the per-request cache_read curve (for retreats)."""
    cap_dir = os.path.expanduser(cap_dir)
    reqs = sorted(glob.glob(os.path.join(cap_dir, "*parent*.request.json")))
    if not reqs:  # fall back to any request json (non-parent-tagged captures)
        reqs = sorted(glob.glob(os.path.join(cap_dir, "*.request.json")))
    roster = None
    reads = []
    client = {}
    for rf in reqs:
        try:
            d = json.load(open(rf))
        except Exception:
            continue
        body = _body(d)
        tools = body.get("tools")
        if roster is None and isinstance(tools, list) and tools:
            roster = sorted(t.get("name") for t in tools if isinstance(t, dict))
            hdr = d.get("headers") or {}
            hdr = {k.lower(): v for k, v in hdr.items()} if isinstance(hdr, dict) else {}
            client = {"user_agent": hdr.get("user-agent"),
                      "betas": hdr.get("anthropic-beta"),
                      "model": body.get("model")}
        u = _usage(rf[:-len(".request.json")] + ".response.json")
        if u:
            cr = u.get("cache_read_input_tokens", 0) or 0
            if cr > 0:
                reads.append(cr)
    # count read-retreats: a read strictly below a prior high-water = prefix/tail
    # collapse forcing a cold re-read.  tolerance skips tiny wobble.
    retreats, mx, drop = 0, 0, 0
    for r in reads:
        if r < mx - 500:
            retreats += 1
            drop += (mx - r)
        mx = max(mx, r)
    return {"dir": cap_dir, "roster": roster, "reads": reads,
            "retreats": retreats, "retreat_tokens": drop, "client": client}

# ---------- extraction from a report ----------

def from_report(rep):
    """Flatten the fields we compare out of a /_report dict."""
    if not rep:
        return {}
    scope = rep.get("scope", {})
    tot = rep.get("totals", {})
    tk = tot.get("tokens", {})
    buckets = {b["bucket"]: b for b in rep.get("cost_decomposition", {}).get("by_bucket", [])}
    usd = rep.get("cost_decomposition", {}).get("total_usd") or tot.get("est_usd")
    reqs = scope.get("requests")
    pre = rep.get("token_decomposition", {}).get("preamble", {})
    return {
        "requests": reqs,
        "turns": scope.get("turns"),
        "usd": usd,
        "usd_per_req": (usd / reqs) if usd and reqs else None,
        "read_usd": buckets.get("cache_read", {}).get("usd"),
        "write_usd": (buckets.get("cache_write_initial", {}).get("usd")
                      or buckets.get("cache_write", {}).get("usd")),
        "output_usd": buckets.get("output", {}).get("usd"),
        "uncached_usd": buckets.get("uncached_input", {}).get("usd"),
        "read_tok": tk.get("cache_read"),
        "write_tok": (tk.get("cache_write_1h", 0) or 0) + (tk.get("cache_write_5m", 0) or 0),
        "write_1h": tk.get("cache_write_1h"),
        "write_5m": tk.get("cache_write_5m"),
        "output_tok": tk.get("output"),
        "uncached_tok": tk.get("input"),
        "preamble_per_req": pre.get("tokens_per_request"),
        "preamble_unused_per_req": pre.get("unused_tokens_per_request"),
        "models": ",".join(scope.get("models", []) or []),
    }

def churn(m):
    r, w = m.get("read_tok") or 0, m.get("write_tok") or 0
    return (w / (r + w)) if (r + w) else None

# ---------- rendering ----------

def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) < 1:
            return f"{v:.4f}"
        return f"{v:,.2f}" if v != int(v) else f"{int(v):,}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)

def _delta(a, b):
    """B relative to A: absolute + pct. (A is the reference/experimental arm.)"""
    if a is None or b is None or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return ""
    d = a - b
    pct = (d / b * 100) if b else float("inf")
    sign = "+" if d >= 0 else ""
    return f"{sign}{_fmt(d)}  ({sign}{pct:.1f}% vs B)"

def row(label, a, b, note=""):
    print(f"  {label:26} {_fmt(a):>16} {_fmt(b):>16}   {_delta(a,b):<24} {note}")

def main():
    ap = argparse.ArgumentParser(description="Diff two proxy sessions (A/B pair).")
    ap.add_argument("--a", help="A side as PORT:SESSION_ID (live /_report)")
    ap.add_argument("--b", help="B side as PORT:SESSION_ID (live /_report)")
    ap.add_argument("--a-dir", help="A capture dir (roster + cold-read curve)")
    ap.add_argument("--b-dir", help="B capture dir")
    ap.add_argument("--a-label", default="A", help="A arm label")
    ap.add_argument("--b-label", default="B", help="B arm label")
    args = ap.parse_args()

    def side(spec, cap_dir):
        rep = None
        if spec:
            port, _, sid = spec.partition(":")
            rep = fetch_report(port, sid)
        m = from_report(rep)
        m["churn"] = churn(m)
        if cap_dir:
            m["cap"] = scan_captures(cap_dir)
        return m

    A = side(args.a, args.a_dir)
    B = side(args.b, args.b_dir)
    la, lb = args.a_label, args.b_label

    print(f"\n{'='*80}\n  SESSION DIFF   A={la}   B={lb}   (delta column = A minus B)\n{'='*80}")
    print(f"  {'metric':26} {'A='+la:>16} {'B='+lb:>16}   {'Δ (A vs B)':<24}")
    print("  " + "-"*78)

    print("\n  — volume —")
    row("requests", A.get("requests"), B.get("requests"))
    row("turns", A.get("turns"), B.get("turns"))

    print("\n  — cost ($) —")
    row("TOTAL usd", A.get("usd"), B.get("usd"), "<-- the headline")
    row("per-request usd", A.get("usd_per_req"), B.get("usd_per_req"), "<-- fair axis (turns diverge)")
    row("read usd", A.get("read_usd"), B.get("read_usd"))
    row("write usd", A.get("write_usd"), B.get("write_usd"))
    row("output usd", A.get("output_usd"), B.get("output_usd"))
    row("uncached-in usd", A.get("uncached_usd"), B.get("uncached_usd"))

    print("\n  — tokens —")
    row("read (cache_read)", A.get("read_tok"), B.get("read_tok"), "<-- usually the dominant bucket")
    row("write (1h+5m)", A.get("write_tok"), B.get("write_tok"))
    row("  write 1h", A.get("write_1h"), B.get("write_1h"))
    row("  write 5m", A.get("write_5m"), B.get("write_5m"))
    row("output", A.get("output_tok"), B.get("output_tok"))
    row("uncached input", A.get("uncached_tok"), B.get("uncached_tok"))
    row("churn w/(r+w)", A.get("churn"), B.get("churn"), "lower=better")
    row("preamble/req", A.get("preamble_per_req"), B.get("preamble_per_req"))
    row("  unused/req", A.get("preamble_unused_per_req"), B.get("preamble_unused_per_req"), "deadweight schema")

    # cold-read curve (needs capture dirs)
    ca, cb = A.get("cap"), B.get("cap")
    if ca or cb:
        print("\n  — cold reads (read-retreats: cached prefix dropped & re-read) —")
        row("read-retreats", (ca or {}).get("retreats"), (cb or {}).get("retreats"), "0=marker held tail")
        row("retreat tokens", (ca or {}).get("retreat_tokens"), (cb or {}).get("retreat_tokens"))

    # TOOL ROSTER — the confound that silently invalidates an A/B
    if ca and cb and ca.get("roster") and cb.get("roster"):
        ra, rb = set(ca["roster"]), set(cb["roster"])
        print("\n  — TOOL ROSTER (must match for a valid A/B) —")
        row("tool count", len(ra), len(rb))
        only_a = sorted(ra - rb)
        only_b = sorted(rb - ra)
        if only_a or only_b:
            print(f"\n  !! ROSTER MISMATCH — the arms are NOT the same client, run is CONFOUNDED:")
            if only_a:
                print(f"     only in A={la}: {only_a}")
            if only_b:
                print(f"     only in B={lb}: {only_b}")
            print(f"     tools are re-read every turn (top read-cost bucket) -> a cost")
            print(f"     delta here is contaminated by the roster gap, not the proxy build.")
        else:
            print(f"     rosters identical ({len(ra)} tools) — clean on this axis. ✓")
        # client sanity
        cca, ccb = ca.get("client", {}), cb.get("client", {})
        if cca.get("user_agent") != ccb.get("user_agent") or cca.get("betas") != ccb.get("betas"):
            print("\n  !! CLIENT MISMATCH (CLI version / betas differ):")
            print(f"     A ua={cca.get('user_agent')}")
            print(f"     B ua={ccb.get('user_agent')}")
    elif (args.a_dir or args.b_dir):
        print("\n  (tool-roster diff needs BOTH --a-dir and --b-dir with tools-bearing captures)")

    print()

if __name__ == "__main__":
    main()
