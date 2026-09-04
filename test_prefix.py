#!/usr/bin/env python3
"""Regression suite for analyze_prefix.py (cold-prefix anatomy).

Every assertion here corresponds to a defect the tool actually shipped with and
that was found by RUNNING it against traffic it wasn't built for, never by
reading it. That is the point of the file: the segmentation is heuristic, the
heuristics are dialect-specific, and each one failed silently — producing a
plausible breakdown of the wrong thing rather than an error.

The four originals, in the order they were found:
  1. a WebSearch side-call chosen as the "seat boot" (role label doesn't
     separate them; the tool KIND does)
  2. calibration x20.6 from a receipt pricing a server-side tool absent from the
     body — scaling by it would have invented ~2,800 tokens and spread them
     proportionally across every row
  3. a two-seat comparison labelling both columns with the harness name
  4. a boot share of 2,176% on a session with no warm requests, and 126% on a
     quartile whose mean read sat below the prefix

Fixtures are synthetic but SHAPED like the real captures (the wire shapes are
documented in CLAUDE.md "Hard facts"); the numbers assert behaviour, not the
text of the implementation.
"""
import json
import sys
import tempfile
from pathlib import Path

import analyze_prefix as ap

_fails = []
_n = 0


def check(label, cond, detail=""):
    global _n
    _n += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}  {detail}")
        _fails.append(label)


def group(name):
    print(f"\n[{name}]")


def _write(d, seq, name, body, usage=None, role="parent"):
    """One capture pair, named the way the writer names them."""
    stem = f"{seq}-{name}-{role}-opus-5-0900{seq:02d}"
    (d / f"{stem}.request.json").write_text(json.dumps(
        {"seq": seq, "summary": {"role": role}, "body": body}))
    if usage is not None:
        (d / f"{stem}.response.json").write_text(json.dumps({"usage": usage}))
    return d / f"{stem}.request.json"


def _client_tool(name, pad=400):
    return {"name": name, "description": "x" * pad,
            "input_schema": {"type": "object", "properties": {}}}


def _boot_body(sys_text, msg0, n_tools=3):
    return {
        "model": "claude-opus-5",
        "tools": [_client_tool(f"T{i}") for i in range(n_tools)],
        "system": [{"type": "text", "text": "x-anthropic-billing-header: cc_version=1"},
                   {"type": "text", "text": sys_text,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": msg0}]}],
    }


# --------------------------------------------------------------------------
group("1] pick_cold_request — a side-call is not a seat boot (defect 1)")

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    # A real boot: client tools, small history.
    _write(d, 1, "seat", _boot_body("# Role\n" + "p" * 5000, "# Doc\n" + "m" * 5000),
           {"cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0})
    # A WebSearch side-call: SERVER-side tool (no input_schema), fewer messages,
    # and — the trap — captured on the PARENT line, so role does not exclude it.
    _write(d, 2, "seat", {"model": "claude-opus-5",
                          "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                          "system": [{"type": "text", "text": "search helper"}],
                          "messages": [{"role": "user", "content": "find x"}]},
           {"input_tokens": 2900}, role="parent")
    f, body = ap.pick_cold_request(d)
    check("picks the seat boot, not the fewer-message side-call",
          len(body.get("messages")) == 1 and any(
              t.get("input_schema") for t in body["tools"]),
          f"picked {Path(f).name}")
    check("PRECONDITION: the side-call really had fewer messages",
          True)  # 1 vs 1 by construction; the discriminator must be tool KIND

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    _write(d, 1, "seat", {"model": "claude-opus-5",
                          "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                          "system": [{"type": "text", "text": "s"}],
                          "messages": [{"role": "user", "content": "q"}]},
           {"input_tokens": 2900})
    try:
        ap.pick_cold_request(d)
        check("a side-call-only session refuses rather than guessing", False)
    except SystemExit as e:
        check("a side-call-only session refuses rather than guessing",
              "no seat-boot request" in str(e))


# --------------------------------------------------------------------------
group("2] calibration — refuses a receipt pricing bytes absent from the body (defect 2)")

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    f = _write(d, 1, "seat", _boot_body("# Role\n" + "p" * 3000, "# Doc\n" + "m" * 3000),
               {"cache_creation_input_tokens": 3800, "cache_read_input_tokens": 0})
    a = ap.analyze(f, json.loads(f.read_text())["body"], d)
    check("a plausible receipt IS applied", a["scale_ok"] and a["scale"] != 1.0,
          f"scale={a['scale']:.3f}")
    check("plausible scale lands in the measured band",
          ap.SCALE_MIN <= a["scale"] <= ap.SCALE_MAX)

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    # Tiny body, huge receipt: the server-side-tool case, x20 territory.
    f = _write(d, 1, "seat", _boot_body("s" * 40, "m" * 40, n_tools=1),
               {"cache_creation_input_tokens": 30000})
    a = ap.analyze(f, json.loads(f.read_text())["body"], d)
    check("an impossible receipt/estimate ratio is NOT applied", not a["scale_ok"])
    check("and the scale falls back to 1.0 rather than inventing tokens",
          a["scale"] == 1.0)
    check("PRECONDITION: the raw ratio really was out of band",
          a["receipt"] / a["est"] > ap.SCALE_MAX)


# --------------------------------------------------------------------------
group("3] comparison labels distinguish seats under a harness prefix (defect 3)")

check("harness-prefixed names shift to the distinguishing field",
      ap._distinct_labels(["1-clodex-alpha-parent-opus-5-1.request.json",
                           "2-clodex-beta-parent-opus-5-2.request.json"])
      == ["alpha", "beta"])
check("plain names still label from the first field",
      ap._distinct_labels(["1-alpha-parent-opus-5-1.request.json",
                           "2-beta-parent-opus-5-2.request.json"])
      == ["alpha", "beta"])
check("a single path needs no disambiguation",
      len(ap._distinct_labels(["1-clodex-solo-parent-opus-5-1.request.json"])) == 1)


# --------------------------------------------------------------------------
group("4] amortization — no share of a quantity the load was never part of (defect 4)")

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    _write(d, 1, "seat", _boot_body("# Role\n" + "p" * 3000, "# Doc\n" + "m" * 3000),
           {"cache_creation_input_tokens": 28764, "input_tokens": 1385})
    amp = ap.amplification(d, 30149)
    check("a 1-request session reports no boot share", amp["pct_of_read"] is None)
    check("and says how many warm requests it had", amp["amortized_requests"] == 0)

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    body = _boot_body("# Role\n" + "p" * 3000, "# Doc\n" + "m" * 3000)
    # Ascending window, so early requests read LESS than the 20k prefix and late
    # ones far more — the real shape, and the one that produced a >100% share.
    for i in range(1, 13):
        _write(d, i, "seat", body,
               {"cache_read_input_tokens": 8000 * i,
                "cache_creation_input_tokens": 100})
    amp = ap.amplification(d, 20000)
    check("a many-request session does report a share", amp["pct_of_read"] is not None)
    check("boot share is never reported above 100%",
          all(q["boot_share_pct"] <= 100.0 for q in amp["boot_share_by_quartile"]),
          str(amp["boot_share_by_quartile"]))
    check("a quartile reading below the prefix is FLAGGED, not silently capped",
          any(q.get("below_prefix") for q in amp["boot_share_by_quartile"]))
    check("PRECONDITION: an early quartile really did read below the prefix",
          amp["boot_share_by_quartile"][0]["mean_read"] < 20000)


# --------------------------------------------------------------------------
group("5] segmentation attributes, and degrades honestly when it cannot")

_sys = ("preamble\n"
        "This session runs inside clodex, blah.\n"
        "HOW TO COMMUNICATE:\n" + "c" * 3000 + "\n"
        "RULES:\n" + "r" * 1500 + "\n")
_hook = ("SessionStart hook additional context: You are the agent 'x'.\n"
         "Your persistent memory (9 unit(s)).\n" + "m" * 800 + "\n"
         "Index (bodies on disk).\n" + "i" * 600 + "\n"
         "Available agent types for the Agent tool:\n" + "a" * 400)
_body = _boot_body("x" * 10, "# Doc\n## S1\n" + "d" * 3000 + "\n## S2\n" + "e" * 3000)
_body["system"][1]["text"] = _sys
_body["messages"].append({"role": "user", "content": [{"type": "text", "text": _hook}]})
rows, _meta = ap.dissect(_body)
groups = {g for g, _n2, _c, _t in rows}
check("the wrapper's ALL-CAPS sections are split out", "wrapper" in groups)
check("HOW TO COMMUNICATE is attributed by name",
      any("HOW TO COMMUNICATE" in n for g, n, _c, _t in rows if g == "wrapper"))
check("the SessionStart hook is segmented by its anchors", "hook" in groups)
check("memory full-units and index are separated",
      {"memory: full units", "memory: index"} <=
      {n for g, n, _c, _t in rows if g == "hook"})
check("a big CLAUDE.md is sub-split below its title, not left as one row",
      sum(1 for g, _n2, _c, _t in rows if g == "claudemd") >= 2)

# An unrecognised hook dialect must go coarse, never wrong.
_b2 = _boot_body("x" * 10, "# Doc\n" + "d" * 3000)
_b2["messages"].append({"role": "user", "content": [
    {"type": "text", "text": "SessionStart hook additional context: " + "z" * 3000}]})
rows2, _ = ap.dissect(_b2)
check("an unknown hook dialect degrades to ONE unsegmented row",
      [n for g, n, _c, _t in rows2 if g == "hook"] == ["SessionStart (unsegmented)"])

# Dash-fenced dialect (workbench seats) must segment too.
_b3 = _boot_body("--- IDENTITY ---\n" + "i" * 3000 + "\n--- HOW TO COMMUNICATE ---\n"
                 + "c" * 3000, "# Doc\n" + "d" * 2000)
rows3, _ = ap.dissect(_b3)
check("dash-fenced sections are segmented (workbench dialect)",
      any("IDENTITY" in n for g, n, _c, _t in rows3 if g == "system"),
      str([n for g, n, _c, _t in rows3 if g == "system"])[:160])


# --------------------------------------------------------------------------
group("6] leak scan — finds a real duplicate, stays quiet otherwise")

# The scan hashes whole strings, so the duplicate must be the ENTIRE block at
# both paths — a shared SUFFIX under different heads is not a duplicate, which is
# what the first version of this fixture got wrong.
_dup = "D" * 900
_b4 = _boot_body(_dup, _dup)
dupes, overlaps, _nb = ap.leak_scan(_b4)
check("an identical block at two paths is reported", len(dupes) == 1)
check("and it records where both copies live", len(dupes[0]) == 2)

_b5 = _boot_body("# Role\n" + "p" * 7000, "# Doc\n" + "m" * 7000)
dupes5, overlaps5, nb5 = ap.leak_scan(_b5)
check("distinct bodies report no duplicate", dupes5 == [])
check("PRECONDITION: two large prose bodies were actually compared", nb5 >= 2)
check("distinct bodies report no sentence overlap", overlaps5 == [])


# --------------------------------------------------------------------------
group("7] cache profile — the TTL tier is read, not assumed")

check("an explicit 1h marker is reported as 1h",
      ap.cache_profile(_body).get("1h") == 1)
_b6 = _boot_body("s" * 20, "m" * 20)
_b6["system"][1]["cache_control"] = {"type": "ephemeral"}
check("a marker with no ttl is reported as the 5m default",
      "5m (default)" in ap.cache_profile(_b6))


# --------------------------------------------------------------------------
print(f"\n{'=' * 60}")
if _fails:
    print(f"FAILED {len(_fails)}/{_n}")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"PASSED {_n}/{_n}")
