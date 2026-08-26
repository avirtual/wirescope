#!/usr/bin/env python3
"""The billing header is OUT-OF-BAND: excluded from every cache fingerprint,
and SAID to be excluded by the views that render it.

WHAT THIS SUITE GUARDS. `system[0]` on every CLI request is
`x-anthropic-billing-header: cc_version=...; cc_entrypoint=cli;`. It sits ahead
of every `cache_control` marker and does NOT participate in the prompt cache.
Anthropic ships a new CLI build most days, so if that block ever entered one of
our fingerprints, every session would read as busted on every upgrade — a
guaranteed miss, and a warmth ledger that lies in the expensive direction.

WIRE PROOF (2026-08-26, 8-week corpus, 113,306 tool-carrying captures): 24 turns
exist where cc_version changed and NOTHING else in the prefix did. Split by
whether the cache could still be alive:
    gap <  55min (TTL live)    17 turns -> 17/17 hit cache, mean read 92,704 tok
    gap >= 55min (TTL expired)  7 turns ->  1/7  hit, mean write 81,926 tok
17 of 17. The 7 misses are plain TTL expiry (447-1,119 min idle), not the
version. So the string is free, and pinning it would buy nothing while forging
a value Anthropic bills against.

THE DISPLAY DEFECT this also guards: rendering the block as a bare `system[0]`
implied it LED the cached prefix. That reading is what made "pin cc_version to
stop cache busts" look like a real lever. The block is now labelled out-of-band
in both renderers. This is the CLAUDE.md failure mode of a durable artifact
recording the wrong thing about a decision and then being trusted.

Assertions are on BEHAVIOUR — mutate only cc_version, assert no fingerprint
moves — plus a control proving each fingerprint DOES move on the change it
exists to detect (else "nothing moved" is vacuous).

Run: python3 test_billing_header_oob.py
"""
import copy
import re
import json
import sys

from proxylab import report, views, warmth

_fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(label)


_BILLING = "x-anthropic-billing-header: cc_version=2.1.245.ff6; cc_entrypoint=cli;"
_BUMPED = "x-anthropic-billing-header: cc_version=2.1.246.40e; cc_entrypoint=cli;"

BASE = {
    "model": "claude-opus-5",
    "tools": [{"name": "Read", "description": "read a file"},
              {"name": "Edit", "description": "edit a file"}],
    "system": [
        {"type": "text", "text": _BILLING},
        {"type": "text", "text": "You are Claude Code, Anthropic's official CLI.",
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": "# Harness\nthe long system prompt body",
         "cache_control": {"type": "ephemeral"}},
    ],
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "msg0 context bundle"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        {"role": "user", "content": [{"type": "text", "text": "next turn"}]},
    ],
}

# Every fingerprint that could plausibly fold system[] in. If a new one is added
# to warmth.py it belongs here too.
_FINGERPRINTS = {
    "_prefix_hash": lambda o: warmth._prefix_hash(o, len(o["messages"])),
    "_sys_full_hash": warmth._sys_full_hash,
    "_msg0_hash": warmth._msg0_hash,
    "_stable_sys_text": warmth._stable_sys_text,
    "_segment_hashes": lambda o: json.dumps(warmth._segment_hashes(o), sort_keys=True),
    "_sys_tools_fingerprint": warmth._sys_tools_fingerprint,
}


def _mut(fn):
    o = copy.deepcopy(BASE)
    fn(o)
    return o


# -- the defect: a cc_version bump must move NOTHING ---------------------------
print("\n[cc_version is excluded from every fingerprint]")
bumped = _mut(lambda o: o["system"][0].__setitem__("text", _BUMPED))
for name, fn in _FINGERPRINTS.items():
    check(f"{name} ignores a cc_version bump", fn(BASE) == fn(bumped),
          f"{str(fn(BASE))[:60]} != {str(fn(bumped))[:60]}")

# A whole-block swap of the header (entrypoint + subagent flag change too) is
# still out-of-band — the exclusion is by block identity, not by diffing text.
whole = _mut(lambda o: o["system"].__setitem__(0, {
    "type": "text",
    "text": "x-anthropic-billing-header: cc_version=9.9.9.zzz; "
            "cc_entrypoint=sdk; cc_is_subagent=true; cch=41;"}))
for name, fn in _FINGERPRINTS.items():
    check(f"{name} ignores a whole-header rewrite", fn(BASE) == fn(whole))


# -- CONTROL: each fingerprint moves on what it exists to detect ---------------
# Without this, "nothing moved" would also pass for a fingerprint that is broken
# and moves for nothing at all.
print("\n[control: the fingerprints are not simply inert]")
_CONTROLS = {
    # mutation -> the fingerprints that MUST notice it
    "tools[] changed": (lambda o: o["tools"].append({"name": "Bash", "description": "run"}),
                        ["_prefix_hash", "_segment_hashes", "_sys_tools_fingerprint"]),
    "system prose changed": (lambda o: o["system"][2].__setitem__("text", "# DIFFERENT"),
                             ["_prefix_hash", "_sys_full_hash", "_stable_sys_text",
                              "_segment_hashes", "_sys_tools_fingerprint"]),
    "messages[0] changed": (lambda o: o["messages"][0]["content"][0].__setitem__(
                                "text", "a different bundle"),
                            ["_prefix_hash", "_msg0_hash"]),
    "tail message appended": (lambda o: o["messages"].append(
                                  {"role": "assistant", "content": [
                                      {"type": "text", "text": "more"}]}),
                              ["_prefix_hash"]),
}
for label, (mutate, must_move) in _CONTROLS.items():
    o = _mut(mutate)
    for name in must_move:
        fn = _FINGERPRINTS[name]
        check(f"{name} MOVES on: {label}", fn(BASE) != fn(o))


# -- the display must not imply the header leads the cached prefix -------------
print("\n[the renderers say out-of-band]")
sysb = BASE["system"]
lbl0 = report._system_label(sysb, 0)
lbl1 = report._system_label(sysb, 1)
check("report labels the billing block as not cached",
      "out-of-band" in lbl0 and "not cached" in lbl0, lbl0)
check("report leaves an ordinary system block's label alone",
      "out-of-band" not in lbl1, lbl1)
check("report._is_billing_block identifies block 0 only",
      report._is_billing_block(sysb[0]) and not report._is_billing_block(sysb[1]))

# The HTML row, asserted on the RENDERED PAGE (not on the source text — a check
# that greps views.py for a literal pins what the code SAYS, never what it does).
# `entry["obj"]` is the body key the renderer reads; passing the wrong one drops
# it into the no-entry branch where there are no system rows at all and every
# check below would pass vacuously. Hence the row-count assertion first.
_entry = {"obj": BASE, "ts": 1787000000.0, "agent": "t", "model": "claude-opus-5"}
_snap = {"sessions": [{"session_id": "s1", "model": "claude-opus-5"}]}
page = views._render_session_html("s1", _entry, _snap)
rows = re.findall(r'<div class="blk sysb[^"]*">.*?(?=<div class="blk|<div class="cmark)',
                  page, re.S)
check("the renderer actually emitted a row per system block (not the empty branch)",
      len(rows) == len(BASE["system"]), f"{len(rows)} rows for "
      f"{len(BASE['system'])} blocks")

oob_row = next((r for r in rows if "x-anthropic-billing-header" in r), None)
check("the billing block rendered at all", oob_row is not None)
if oob_row:
    check("its row is marked out-of-band / not cached",
          "out-of-band" in oob_row and "not cached" in oob_row, oob_row[:160])
    check("it does NOT get the green 'cache <ttl>' badge a breakpoint gets",
          'badge on' not in oob_row, oob_row[:160])
    check("it says a version bump does not bust",
          "does not bust" in oob_row, oob_row[:160])

# The real breakpoints must be untouched by this change.
cached_rows = [r for r in rows if "x-anthropic-billing-header" not in r]
check("every real cache_control block still shows its cache badge",
      cached_rows and all("badge on" in r for r in cached_rows),
      f"{sum('badge on' in r for r in cached_rows)}/{len(cached_rows)}")
check("no real block is mislabelled out-of-band",
      not any("out-of-band" in r for r in cached_rows))
# ...and the breakpoint dividers still number through the canonical order.
check("both cache breakpoints still render",
      page.count("cache breakpoint") >= 2, str(page.count("cache breakpoint")))


print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILURE(S): ' + str(_fails)}")
sys.exit(1 if _fails else 0)
