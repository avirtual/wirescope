#!/usr/bin/env python3
"""Account-quota parsing: the `anthropic-ratelimit-unified-*` headers.

WHAT THIS SUITE GUARDS. The numbers here go on a status bar, so the failure
mode is not a crash — it is a plausible WRONG PERCENTAGE, or a right one that
is silently hours old. Four properties carry that weight:

  * DIRECTION. The header is utilization = fraction CONSUMED. Rendering it as
    "remaining" inverts the number precisely when it matters (0.93 used is
    7% left, and the two readings differ by a factor of 13 near the edge).
  * A 429 CARRIES NO HEADERS. Measured: 102/102 rate-limit rejections in a 4k
    capture window had no ratelimit headers at all. So the one response that
    proves you hit the wall must NOT be allowed to blank the last good reading.
  * WINDOWS ARE DISCOVERED, NOT HARDCODED. A `7d_oi` meter appeared in 256 of
    those same captures with no announcement. A parser that knows only 5h/7d
    drops new meters silently; this one must keep them.
  * FRESHNESS IS PART OF THE READING. Nothing polls; the numbers move only when
    a turn is forwarded, so a snapshot without `age_s` overstates what it knows.

The header-shape checks run against REAL captured headers when a capture dir is
available (the shapes vary more than anyone guesses — see `overage`, which
arrives with a status and a disabled_reason but NO utilization at all).

Run: python3 test_quota.py [capture-dir]
"""
import glob
import json
import os
import sys
import time

from proxylab import quota as q

_fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(label)


def _reset():
    q._QUOTA.clear()
    q._LAST_ACCOUNT = None


P = "anthropic-ratelimit-unified-"


def _hdrs(**kw):
    """A realistic successful-response header set (shape copied from the wire)."""
    h = {"anthropic-organization-id": "org-1",
         "anthropic-workspace-id": "wrk-1",
         P + "status": "allowed_warning",
         P + "5h-status": "allowed",
         P + "5h-utilization": "0.23",
         P + "5h-reset": str(int(time.time()) + 3600),
         P + "7d-status": "allowed_warning",
         P + "7d-utilization": "0.93",
         P + "7d-reset": str(int(time.time()) + 86400),
         P + "7d-surpassed-threshold": "0.75",
         P + "representative-claim": "seven_day",
         P + "reset": str(int(time.time()) + 86400)}
    h.update(kw)
    return h


# -- DIRECTION: used vs remaining, the inversion that matters most -------------
print("\n[direction]")
_reset()
q.note(_hdrs())
s = q.snapshot()
check("utilization is carried through verbatim (fraction CONSUMED)",
      s["windows"]["7d"]["utilization"] == 0.93, str(s["windows"]["7d"]))
check("used_pct is the CONSUMED percentage, not the remaining one",
      s["windows"]["7d"]["used_pct"] == 93.0, str(s["windows"]["7d"]["used_pct"]))
check("remaining_pct is its complement",
      s["windows"]["7d"]["remaining_pct"] == 7.0,
      str(s["windows"]["7d"]["remaining_pct"]))
check("used + remaining == 100 for every window with a utilization",
      all(abs(w["used_pct"] + w["remaining_pct"] - 100) < 1e-6
          for w in s["windows"].values() if w["used_pct"] is not None))

# -- the binding window --------------------------------------------------------
print("\n[representative window]")
check("representative_claim maps to a real window key",
      s["representative_window"] == "7d", str(s["representative_window"]))
check("primary IS that window's view (the one number for a one-slot statusbar)",
      s["primary"]["window"] == "7d" and s["primary"]["used_pct"] == 93.0)
_reset()
q.note(_hdrs(**{P + "representative-claim": "some_future_claim"}))
s2 = q.snapshot()
check("an UNKNOWN representative claim degrades to no primary, never a wrong one",
      s2["representative_window"] is None and s2["primary"] is None,
      str(s2["representative_window"]))
check("...while the raw claim string is still reported for diagnosis",
      s2["representative_claim"] == "some_future_claim")
# REGRESSION (2026-08-15): every claim string the wire actually sends must map.
# The first version of the map invented `seven_day_oi` by analogy with the
# `7d_oi-*` HEADER prefix — the wire says `seven_day_overage_included`, so
# `primary` silently went null on the ~1% of turns carrying that claim. The
# header prefix and the claim word are different vocabularies.
for claim, want in (("five_hour", "5h"), ("seven_day", "7d"),
                    ("seven_day_overage_included", "7d_oi")):
    _reset()
    q.note(_hdrs(**{P + "representative-claim": claim,
                    P + "7d_oi-utilization": "0.24",
                    P + "7d_oi-status": "allowed"}))
    got = q.snapshot()
    check(f"observed claim {claim!r} maps to the {want} window",
          got["representative_window"] == want and got["primary"] is not None,
          str(got["representative_window"]))

# -- 429: the response that proves the wall, carrying no numbers ---------------
print("\n[429 carries no headers]")
_reset()
q.note(_hdrs())
before = q.snapshot()
q.note({"content-type": "application/json"}, status_code=429)   # real 429 shape
after = q.snapshot()
check("a 429 does NOT blank the last good reading",
      after is not None and after["windows"]["7d"]["used_pct"] == 93.0,
      str(after))
check("a 429 does not corrupt the reading's as_of",
      after["as_of"] == before["as_of"])
check("the 429 is recorded as its own fact",
      after.get("last_429") is not None and after.get("last_429_age_s") is not None,
      str(after.get("last_429")))
_reset()
q.note({"content-type": "application/json"}, status_code=429)
check("a 429 with no prior reading yields no snapshot (never a fabricated 0%)",
      q.snapshot() is None)

# -- header-less responses (models stub / count_tokens / codex) ----------------
print("\n[responses without quota headers]")
_reset()
q.note(_hdrs())
q.note({"content-type": "application/json", "request-id": "req_x"})  # models stub
check("a 200 with no ratelimit headers leaves the reading untouched",
      q.snapshot()["windows"]["7d"]["used_pct"] == 93.0)
check("empty/None headers are survivable", (q.note(None), q.note({}), True)[-1])

# -- unknown meters are kept, not dropped -------------------------------------
print("\n[forward compatibility]")
_reset()
q.note(_hdrs(**{P + "7d_oi-status": "allowed",
                P + "7d_oi-utilization": "0.24",
                P + "7d_oi-reset": str(int(time.time()) + 400)}))
s = q.snapshot()
check("an UNANNOUNCED window (7d_oi) is discovered from the header name",
      "7d_oi" in s["windows"], str(list(s["windows"])))
check("...and priced like any other window",
      s["windows"]["7d_oi"]["used_pct"] == 24.0)
_reset()
q.note(_hdrs(**{P + "brand-new-thing": "42"}))
s = q.snapshot()
kept = json.dumps(s.get("unmapped") or s.get("windows") or {})
check("a header matching NO known shape is surfaced, never silently dropped",
      "brand-new-thing" in kept or "brand-new" in kept, kept)

# -- partial windows: overage has a status but no utilization ------------------
print("\n[partial windows]")
_reset()
q.note(_hdrs(**{P + "overage-status": "rejected",
                P + "overage-disabled-reason": "org_level_disabled"}))
s = q.snapshot()
check("a window with a status but NO utilization still appears",
      "overage" in s["windows"], str(list(s["windows"])))
check("...with null percentages rather than a fabricated 0",
      s["windows"]["overage"]["used_pct"] is None
      and s["windows"]["overage"]["utilization"] is None,
      str(s["windows"]["overage"]))
check("...and keeps its disabled_reason",
      s["windows"]["overage"].get("disabled_reason") == "org_level_disabled")

# -- freshness -----------------------------------------------------------------
print("\n[freshness]")
_reset()
q.note(_hdrs(), now=time.time() - 7200)
s = q.snapshot()
check("age_s reports how old the reading is (it moves only on forwarded turns)",
      6000 < s["age_s"] < 9000, str(s["age_s"]))
check("resets_in_s counts DOWN to the reset epoch",
      s["windows"]["7d"]["resets_in_s"] <= 86400
      and s["windows"]["7d"]["resets_in_s"] > 0,
      str(s["windows"]["7d"]["resets_in_s"]))
_reset()
q.note(_hdrs(**{P + "5h-reset": str(int(time.time()) - 500)}))
check("an ALREADY-PASSED reset clamps to 0, never goes negative",
      q.snapshot()["windows"]["5h"]["resets_in_s"] == 0)

# -- the receipt view ----------------------------------------------------------
print("\n[receipt view]")
_reset()
check("no reading -> no receipt block (absent, not empty-shaped)",
      q.receipt_view() is None)
q.note(_hdrs())
r = q.receipt_view()
check("receipt carries the binding window's full view",
      r["primary"]["used_pct"] == 93.0 and r["representative_window"] == "7d")
check("receipt carries every window's used_pct",
      r["used_pct"]["5h"] == 23.0 and r["used_pct"]["7d"] == 93.0,
      str(r["used_pct"]))
check("receipt carries its own age (a receipt can outlive its freshness)",
      "age_s" in r)
check("receipt stays small (rides EVERY turn.completed)",
      len(json.dumps(r)) < 600, str(len(json.dumps(r))))

# -- kill switch ---------------------------------------------------------------
print("\n[kill switch]")
_reset()
_saved = q.QUOTA_TRACK
q.QUOTA_TRACK = False
q.note(_hdrs())
check("QUOTA_TRACK=0 records nothing", q.snapshot() is None and not q._QUOTA)
q.QUOTA_TRACK = _saved

# -- REAL captured headers -----------------------------------------------------
print("\n[real captures]")
_dir = (sys.argv[1] if len(sys.argv) > 1 else
        os.path.expanduser("~/Library/Application Support/clodex/wirescope/logs"))
files = sorted(glob.glob(os.path.join(_dir, "*", "*.response.json")),
               key=os.path.getmtime)[-400:] if os.path.isdir(_dir) else []
if not files:
    print(f"  SKIP  no capture dir at {_dir}")
else:
    _reset()
    seen_windows, parsed_n, with_hdrs = set(), 0, 0
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        h = d.get("response_headers") or {}
        if any(k.startswith(P) for k in h):
            with_hdrs += 1
        p = q._parse(h)
        if p:
            parsed_n += 1
            seen_windows |= set(p["windows"])
        q.note(h, status_code=d.get("status_code"))
    check("every capture with unified-ratelimit headers parses",
          parsed_n == with_hdrs, f"parsed {parsed_n} of {with_hdrs}")
    check("the real 5h and 7d windows are both found",
          {"5h", "7d"} <= seen_windows, str(sorted(seen_windows)))
    s = q.snapshot()
    check("a snapshot is produced from real traffic", s is not None)
    if s:
        check("every real window's used_pct is a sane percentage",
              all(w["used_pct"] is None or 0 <= w["used_pct"] <= 100
                  for w in s["windows"].values()), str(s["windows"]))
        check("real traffic yields exactly one account (org-scoped, not session)",
              s["accounts"] == 1, str(s["accounts"]))

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILURE(S): ' + str(_fails)}")
sys.exit(1 if _fails else 0)
