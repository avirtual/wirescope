#!/usr/bin/env python3
"""Regression suite for receipt-calibrated token estimation (proxylab/tokest.py
+ views._prefix_tokens / _turn_weights / _turn_weight_html).

The defect this guards: /_session priced every cache breakpoint at a flat
chars//4, ~33% under the truth, so the last breakpoint implied a ~40k uncached
tail on a session whose real tail was 2 tokens.

GROUND TRUTH in group [4] is `/v1/messages/count_tokens` measured against real
captures on 2026-08-03 (session 144fffe8) — not self-generated. That is what
makes these assertions falsifiable rather than a restatement of the code.
"""
import json
import sys

from proxylab import tokest
from proxylab import views

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


# --------------------------------------------------------------------------
group("1] anchor_tokens — both receipt shapes, and what does NOT anchor")

check("raw wire shape (cache_creation_input_tokens)",
      tokest.anchor_tokens({"cache_read_input_tokens": 99872,
                            "cache_creation_input_tokens": 726}) == 100598)
check("normalized billing shape (5m+1h)",
      tokest.anchor_tokens({"cache_read_input_tokens": 99872,
                            "cache_write_5m_tokens": 0,
                            "cache_write_1h_tokens": 726}) == 100598)
check("falls back to flat when 5m+1h are both zero",
      tokest.anchor_tokens({"cache_read_input_tokens": 100,
                            "cache_write_5m_tokens": 0,
                            "cache_write_1h_tokens": 0,
                            "cache_write_flat_tokens": 55}) == 155)
# PRECONDITION: this assertion is only meaningful because input_tokens is
# nonzero here — with it at 0 the check would pass on any implementation.
_u = {"cache_read_input_tokens": 500, "cache_creation_input_tokens": 0,
      "input_tokens": 1234}
check("precondition: the uncached-tail field is actually populated",
      _u["input_tokens"] > 0)
check("input_tokens (uncached tail) is EXCLUDED from the anchor",
      tokest.anchor_tokens(_u) == 500)
check("no cache activity anchors nothing", tokest.anchor_tokens(
      {"input_tokens": 900, "cache_read_input_tokens": 0,
       "cache_creation_input_tokens": 0}) is None)
check("None/garbage usage anchors nothing",
      tokest.anchor_tokens(None) is None and tokest.anchor_tokens([]) is None)

# --------------------------------------------------------------------------
group("2] images price by PIXEL AREA, not base64 length")

# 2000x552 PNG header, the real shape from the capture that drove the 188% error
import base64
import struct


def _png_b64(w, h):
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", w, h)
    raw += b"\x00" * 4096
    return base64.b64encode(raw).decode()


_img = {"type": "image",
        "source": {"type": "base64", "media_type": "image/png",
                   "data": _png_b64(2000, 552)}}
# measured: this image counted 1,443 tok on the wire; w*h/750 = 1472
check("2000x552 PNG ~ w*h/750 (measured 1,443 on the wire)",
      1400 <= tokest.image_tokens(_img) <= 1500,
      f"got {tokest.image_tokens(_img)}")
check("image tokens are capped at 1600",
      tokest.image_tokens({"source": {"data": _png_b64(9000, 9000)}}) == 1600)

_msg = {"role": "user", "content": [_img, {"type": "text", "text": "hello"}]}
_ch, _fx = tokest.message_cost(_msg)
check("base64 payload is EXCLUDED from the char count",
      _ch < 500, f"chars={_ch} (payload is {len(_img['source']['data']):,})")
check("image contributes fixed tokens instead", _fx > 1000, f"fixed={_fx}")
# the same image nested in a tool_result (where screenshots actually arrive)
_tr = {"role": "user", "content": [
    {"type": "tool_result", "content": [_img]}]}
_ch2, _fx2 = tokest.message_cost(_tr)
check("recurses into tool_result content", _fx2 > 1000, f"fixed={_fx2}")
check("a message with no image has zero fixed tokens",
      tokest.message_cost({"role": "user", "content": "plain"})[1] == 0)

# --------------------------------------------------------------------------
group("3] calibrate — fixed image tokens are held OUT of the ratio")

check("no anchor -> no calibration", tokest.calibrate(None, 1000) is None)
check("degenerate raw -> no calibration", tokest.calibrate(100, 0) is None)
check("exact fit gives k=1.0", abs(tokest.calibrate(1000, 1000) - 1.0) < 1e-9)
# 500 of the 1000 raw are exact image tokens; only the other 500 may be scaled.
# Chosen to land INSIDE the clamp, so this measures the exclusion and not the
# clamp: (1250-500)/(1000-500) = 1.5, whereas a naive 1250/1000 would be 1.25.
check("image tokens excluded from both sides of the ratio",
      abs(tokest.calibrate(1250, 1000, 500) - 1.5) < 1e-9,
      f"got {tokest.calibrate(1250, 1000, 500)}")
check("precondition: that factor is inside the clamp, so the clamp isn't "
      "what produced it", 0.6 < 1.5 < 1.6)
check("absurd factors are clamped, not propagated",
      tokest.calibrate(100000, 100) == 1.6 and tokest.calibrate(1, 100000) == 0.6)
check("apply() does not scale the exact image part",
      tokest.apply(1000, 500, 2.0) == 1500)
check("apply() with no factor returns the raw estimate",
      tokest.apply(1234.4, 0, None) == 1234)

# --------------------------------------------------------------------------
group("4] GROUND TRUTH — count_tokens, real capture (session 144fffe8)")

# Reconstructed to the real segment sizes of that request. count_tokens values
# were measured against api.anthropic.com on 2026-08-03.
TRUTH = {1: 5962, 2: 13873, 3: 48666, 4: 100639}
OLD_CH4 = {1: 3930, 2: 9853, 3: 32296, 4: 65314}


def _obj():
    """A body reproducing the REAL capture's segment sizes and marker layout,
    measured from it directly: tools[] 15,591 ch and UNMARKED (so bp1 is the
    system marker); system blocks 70/57*/22347/1217* ch; 119 messages totalling
    88,920 ch through msgs[:14] and 220,306 ch through msgs[:119], marked at
    indices 13 and 118. Those char counts are what make TRUTH applicable."""
    tools = [{"name": f"t{i}", "description": "d" * 1707,
              "input_schema": {"type": "object"}} for i in range(9)]
    sysb = [{"type": "text", "text": "s" * 70},
            {"type": "text", "text": "s" * 57,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": "s" * 22347},
            {"type": "text", "text": "s" * 1217,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    # per-message padding chosen so the two marked prefixes hit the measured
    # cumulative char counts (each message costs its text + ~60 ch of JSON)
    msgs = []
    for i in range(119):
        pad = 6291 if i < 14 else 1194
        msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                     "content": [{"type": "text", "text": "m" * pad}]})
    msgs[13]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    msgs[118]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return {"tools": tools, "system": sysb, "messages": msgs}


_usage = {"cache_read_input_tokens": 99872, "cache_write_1h_tokens": 726,
          "cache_write_5m_tokens": 0, "input_tokens": 2}
cal = views._prefix_tokens(_obj(), _usage)

check("the final marker is the MEASURED anchor, not an estimate",
      cal["cum"][cal["last_marker"]] == 100598,
      f"got {cal['cum'].get(cal['last_marker'])}")
check("last_marker identifies the final breakpoint", cal["last_marker"] == 4)
# The headline regression: the old estimate implied a ~35k uncached tail.
_tail_old = 100600 - OLD_CH4[4]
_tail_new = 100600 - cal["cum"][4]
check("precondition: the OLD math really did imply a huge phantom tail",
      _tail_old > 30000, f"old tail was {_tail_old}")
check("new math leaves a tail consistent with input_tokens=2",
      abs(_tail_new) < 500, f"new tail = {_tail_new}")
check("segment totals sum to the calibrated window",
      abs((cal["tools"] + cal["system"] + cal["messages"]) - 100598) <= 2,
      f"sum={cal['tools'] + cal['system'] + cal['messages']}")
check("no segment is negative",
      min(cal["tools"], cal["system"], cal["messages"]) >= 0)

# Every breakpoint must beat the old estimator against measured truth.
for n in sorted(TRUTH):
    new_err = abs(cal["cum"][n] - TRUTH[n]) / TRUTH[n]
    old_err = abs(OLD_CH4[n] - TRUTH[n]) / TRUTH[n]
    check(f"bp{n}: closer to count_tokens than ch/4 "
          f"({new_err * 100:.1f}% vs {old_err * 100:.1f}%)",
          new_err < old_err)
    check(f"bp{n}: within 20% of measured truth", new_err < 0.20,
          f"err {new_err * 100:.1f}%")

# --------------------------------------------------------------------------
group("5] uncalibrated fallback — a cold session with no receipt")

cold = views._prefix_tokens(_obj(), None)
check("no receipt -> anchor is None", cold["anchor"] is None)
check("no receipt still produces usable numbers", cold["cum"][4] > 0)
check("uncalibrated estimate still beats ch/4 on truth",
      abs(cold["cum"][4] - TRUTH[4]) < abs(OLD_CH4[4] - TRUTH[4]),
      f"uncal={cold['cum'][4]} old={OLD_CH4[4]} truth={TRUTH[4]}")

# --------------------------------------------------------------------------
group("6] structural edge cases")

check("empty body doesn't explode",
      views._prefix_tokens({}, None)["cum"] == {})
check("body with no markers at all reports last_marker None",
      views._prefix_tokens(
          {"messages": [{"role": "user", "content": "hi"}]}, None
      )["last_marker"] is None)
check("string system is accepted (not just block form)",
      views._prefix_tokens(
          {"system": "abc" * 100,
           "messages": [{"role": "user", "content": "hi"}]}, None)["system"] > 0)
# The fixed tool preamble is only OBSERVABLE where calibration can't absorb it:
# a small tools-only prefix with no receipt. (Mutation-checked: setting
# TOOL_PREAMBLE_TOKENS=0 survives every other assertion in this file, because a
# constant offset is exactly what the anchor fit cancels.)
_only_tools = {"tools": [{"name": "x", "description": "d" * 400,
                          "cache_control": {"type": "ephemeral"}}],
               "messages": []}
_bp = views._prefix_tokens(_only_tools, None)["cum"][1]
_chars_only = _bp - tokest.TOOL_PREAMBLE_TOKENS
check("precondition: the preamble is a large share here, so its absence "
      "would be visible", tokest.TOOL_PREAMBLE_TOKENS > _chars_only * 0.5,
      f"preamble={tokest.TOOL_PREAMBLE_TOKENS} chars_part={_chars_only}")
check("tools-only marker prices the fixed tool preamble",
      _bp >= tokest.TOOL_PREAMBLE_TOKENS + _chars_only and _bp > 350,
      f"got {_bp}")

# --------------------------------------------------------------------------
group("7] turn weights + chips")


def _is_start(m):
    return m.get("role") == "user"


_items = [{"role": "user", "content": "a" * 100},
          {"role": "assistant", "content": "b" * 200},
          {"role": "user", "content": "c" * 300}]
w = views._turn_weights(_items, _is_start)
check("turn weights carry the last message index", w[1]["i"] == 1 and w[2]["i"] == 2)
check("turn 1 owns its prompt + the reply", w[1]["ch"] > 250)
_html = views._turn_weight_html(w[2], peak=w[2]["ch"], msg_cum={2: 42000})
check("chip reads Sigma off the calibrated curve when given one",
      "42.0k" in _html or "42k" in _html, _html)
check("chip no longer claims tok = ch/4", "ch/4" not in _html)
_html_nc = views._turn_weight_html(w[2], peak=w[2]["ch"])
check("chip still renders with no curve (fallback)", "&Sigma;" in _html_nc)
_html_codex = views._turn_weight_html(w[2], peak=w[2]["ch"], cpt=4.0)
check("codex override uses its own divisor (different result)",
      _html_codex != _html_nc)
check("empty weight renders nothing", views._turn_weight_html(None, 0) == "")

# --------------------------------------------------------------------------
group("8] _cmark rendering")

_m = views._cmark(3, {"ttl": "1h"}, 48666)
check("estimated marker keeps the approx sign", "&approx;" in _m)
check("estimated marker shows the ttl", "ttl 1h" in _m)
_me = views._cmark(4, {"ttl": "1h"}, 100598, exact=True)
check("measured marker drops the approx sign", "&approx;" not in _me)
check("measured marker says so", "measured" in _me)

# --------------------------------------------------------------------------
print(f"\n{'=' * 60}")
if _fails:
    print(f"FAILED {len(_fails)}/{_n}")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"PASSED {_n}/{_n}")
