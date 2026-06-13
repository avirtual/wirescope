"""Offline tool-utilization ledger over captured proxy logs.

The proxy captures faithfully and cheaply on the hot path; the detective work
happens HERE, after the fact, against the stored files. This pass answers the
original subagent question — "of the context we ship every turn, how much is
ever exercised?" — for the one segment that's measurable with zero judgment:
the TOOL SCHEMAS.

The proxy sees both sides of the loop:
  - request  body.tools          -> what was LOADED (and re-shipped every turn)
  - response meta.tool_uses      -> what was actually CALLED

A tool loaded but never called across a role's whole lifetime is deadweight:
its schema is re-sent on every turn of every session, billed every time even
when cached (cache_read is a recurring tax, not free). We price that.

Correctness guard: absence of a call is only evidence of waste if the turn
*ran*. Only a 200 response counts as a real "chance to use"; error/throttled
turns (429s, etc.) are carried but excluded from the deadweight verdict.

Keys off file CONTENT (session_id/role/tools parsed from each record), not the
directory layout, so it works over the new per-session dirs AND flat history.

Usage:
  python3 analyze_tools.py [LOG_DIR] [--by role|session] [--min-turns N]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

CHARS_PER_TOK = 4          # rough JSON chars->tokens; ranking is robust to it
DEFAULT_MIN_TURNS = 3      # successful turns needed before we call a tool DEAD

# approx public list USD/1M (mirror of logproxy.PRICES); cache_read is the
# realistic recurring rate for tool schemas (they live in the cached prefix).
# Longest-prefix match; opus repriced at 4.5 ($15->$5 in), hence the split.
PRICES = {
    "claude-fable-5":  {"in": 10.0, "cache_read": 1.00},
    "claude-opus-4-5": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4-6": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4-7": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4-8": {"in": 5.0,  "cache_read": 0.50},
    "claude-opus-4":   {"in": 15.0, "cache_read": 1.50},   # legacy 4.0/4.1
    "claude-sonnet-4": {"in": 3.0,  "cache_read": 0.30},
    "claude-haiku-4":  {"in": 1.0,  "cache_read": 0.10},
}


def _price(model):
    """Longest-prefix match. None for an unknown model — the caller FLAGS it
    instead of silently pricing at assumed rates (pricing-blindness guard)."""
    best = None
    for pfx, p in PRICES.items():
        if (model or "").startswith(pfx) and (best is None or len(pfx) > len(best[0])):
            best = (pfx, p)
    return best[1] if best else None


def _session_id(rec, body):
    sid = (rec.get("summary") or {}).get("session_id")
    if sid:
        return sid
    uid = (body.get("metadata") or {}).get("user_id")
    if uid:
        try:
            return json.loads(uid).get("session_id")
        except Exception:
            pass
    return None


def _load_response(req_path):
    rp = req_path.with_name(req_path.name.replace(".request.json", ".response.json"))
    try:
        return json.load(open(rp))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def main():
    args = sys.argv[1:]
    by = "role"
    min_turns = DEFAULT_MIN_TURNS
    root = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--by":
            by = args[i + 1]; i += 2
        elif a == "--min-turns":
            min_turns = int(args[i + 1]); i += 2
        else:
            root = a; i += 1
    root = Path(root or "logs")

    # group -> tool -> stats
    G = defaultdict(lambda: defaultdict(lambda: {
        "loaded_turns": 0, "evaluable_turns": 0, "called_turns": 0,
        "schema_chars": 0, "model": None}))
    group_meta = defaultdict(lambda: {"sessions": set(), "models": set(),
                                      "loaded_turns": 0, "evaluable_turns": 0})

    req_files = sorted(root.rglob("*.request.json"))
    for f in req_files:
        try:
            rec = json.load(open(f))
        except Exception:
            continue
        body = rec.get("body") or {}
        tools = body.get("tools") or []
        if not tools:
            continue
        role = (rec.get("summary") or {}).get("role") or "unknown"
        sid = _session_id(rec, body) or "_no-session"
        model = body.get("model")
        key = role if by == "role" else sid

        resp = _load_response(f)
        ok = bool(resp) and resp.get("status_code") == 200
        called = set((resp.get("meta") or {}).get("tool_uses") or []) if resp else set()

        gm = group_meta[key]
        gm["sessions"].add(sid); gm["models"].add(model)
        gm["loaded_turns"] += 1
        gm["evaluable_turns"] += 1 if ok else 0

        for t in tools:
            if not isinstance(t, dict):
                continue
            name = t.get("name") or "?"
            s = G[key][name]
            s["loaded_turns"] += 1
            s["schema_chars"] = max(s["schema_chars"], len(json.dumps(t)))
            s["model"] = s["model"] or model
            if ok:
                s["evaluable_turns"] += 1
                if name in called:
                    s["called_turns"] += 1

    if not G:
        print(f"no tool-loading requests found under {root}/")
        return

    print(f"# tool-utilization ledger  ({len(req_files)} request files, grouped by {by})\n")
    grand_dead = grand_total = 0.0
    for key in sorted(G, key=lambda k: -sum(
            s["schema_chars"] // CHARS_PER_TOK * s["loaded_turns"] for s in G[k].values()
            if s["called_turns"] == 0 and s["evaluable_turns"] >= min_turns)):
        gm = group_meta[key]
        rows = []
        dead_tok = total_tok = 0
        for name, s in G[key].items():
            tok = s["schema_chars"] // CHARS_PER_TOK
            carried = tok * s["loaded_turns"]          # re-shipped every turn
            total_tok += carried
            if s["called_turns"] > 0:
                verdict = f"used {s['called_turns']}/{s['evaluable_turns']}"
            elif s["evaluable_turns"] >= min_turns:
                verdict = "DEAD"; dead_tok += carried
            else:
                verdict = f"unknown ({s['evaluable_turns']} ok turns)"
            rows.append((carried, name, tok, s["loaded_turns"], s["called_turns"],
                         s["evaluable_turns"], verdict))
        rows.sort(reverse=True)
        model_key = next(iter(gm["models"])) if gm["models"] else None
        p = _price(model_key)
        assumed = p is None
        if assumed:                       # keep the math, but never silently
            p = {"in": 3.0, "cache_read": 0.30}
        dead_lo = dead_tok * p["cache_read"] / 1e6     # recurring even if cached
        dead_hi = dead_tok * p["in"] / 1e6
        grand_dead += dead_lo; grand_total += total_tok * p["cache_read"] / 1e6

        flag = (f"   [UNPRICED model {model_key!r}: $ below ASSUME sonnet rates "
                "— add it to PRICES]" if assumed else "")
        print(f"## {key}   ({len(gm['sessions'])} sessions, "
              f"{gm['loaded_turns']} tool-loading turns, {gm['evaluable_turns']} ran ok)"
              f"{flag}")
        print(f"   {'tool':24s} {'tok':>6} {'loaded':>7} {'called':>7} {'ok':>4}  verdict")
        for carried, name, tok, loaded, called, okn, verdict in rows:
            flag = "  <-- deadweight" if verdict == "DEAD" else ""
            print(f"   {name:24s} {tok:6d} {loaded:7d} {called:7d} {okn:4d}  {verdict}{flag}")
        print(f"   -> deadweight: {dead_tok:,} tokens re-shipped & never exercised "
              f"= ~${dead_lo:.4f}-${dead_hi:.4f} recurring (cache_read..uncached) "
              f"over captured turns\n")

    print(f"# total est. recurring tool-schema spend (cache_read basis): "
          f"~${grand_total:.4f}; of which deadweight ~${grand_dead:.4f}")


if __name__ == "__main__":
    main()
