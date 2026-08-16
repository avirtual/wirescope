#!/usr/bin/env python3
"""Response-usage parsing across BOTH wire dialects: SSE and bare JSON.

WHAT THIS SUITE GUARDS. `_parse_usage_from_sse` walked `data:` lines only, so
a `"stream": false` response — one bare JSON object, no `data:` prefixes — read
as all-None. Nothing crashed; the turn just silently became weightless:

  * THE LEDGER NEVER STAMPED. `warmth._record_warmth` refuses to stamp a turn
    whose usage shows no cache (`created <= 0 and read <= 0`) — correct receipt
    discipline fed a false receipt. A session kept warm by non-streaming pings
    therefore read 'cold' on /_admin while its prefix was demonstrably warm,
    and /_ping would decline it under WARMTH_BLOCK_COLD_PING.
  * THE TURN BILLED $0.00, so totals under-reported by real money.

Found 2026-08-16 on clodex session d48e988f (an external keep-warm pinger
sending `stream:false, max_tokens:1`): 190 pings over 12 days, 173 with a real
`cache_read > 0` upstream, and 0 of 190 stamped the ledger — against 14,521 of
14,717 streaming turns that did. The same dir also held THREE ordinary
main-line turns (max_tokens 64000, 133-182 messages) sent without `stream`, so
this was never only about pings.

The property under test is that the two dialects agree: the same facts, off
whichever shape the wire used. Assertions are on PARSED VALUES against the
response body's own numbers — never on the presence of a branch — and the
corpus pass re-parses real captures, checking the interesting case was
actually reached (a corpus with no non-streaming response would pass every
check vacuously, so that is asserted too).

Run: python3 test_nonstream_usage.py [capture-dir]
"""
import glob
import json
import os
import sys

from proxylab import billing

_fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(label)


# The two shapes carry IDENTICAL facts; only the framing differs. Numbers are
# the real ones off the d48e988f ping that exposed this.
_USAGE = {"input_tokens": 90, "output_tokens": 1,
          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 158490,
          "cache_creation": {"ephemeral_5m_input_tokens": 0,
                             "ephemeral_1h_input_tokens": 0},
          "service_tier": "standard"}

_JSON_BODY = json.dumps({
    "type": "message", "id": "msg_011Ce6RkCBs3NSHuubWfcv19",
    "model": "claude-opus-5", "role": "assistant",
    "content": [{"type": "text", "text": "hi"}],
    "stop_reason": "max_tokens", "stop_sequence": None, "stop_details": None,
    "usage": _USAGE}).encode()

_SSE_BODY = (
    b'event: message_start\ndata: ' + json.dumps({
        "type": "message_start",
        "message": {"id": "msg_011Ce6RkCBs3NSHuubWfcv19",
                    "model": "claude-opus-5", "role": "assistant",
                    "usage": _USAGE}}).encode() + b'\n\n'
    b'event: content_block_start\ndata: ' + json.dumps({
        "type": "content_block_start",
        "content_block": {"type": "text", "text": ""}}).encode() + b'\n\n'
    b'event: content_block_delta\ndata: ' + json.dumps({
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "hi"}}).encode() + b'\n\n'
    b'event: message_delta\ndata: ' + json.dumps({
        "type": "message_delta", "usage": _USAGE,
        "delta": {"stop_reason": "max_tokens"}}).encode() + b'\n\n')


# -- the defect itself: a non-streaming response is not weightless -------------
print("\n[non-streaming usage]")
u = billing._parse_usage_from_sse(_JSON_BODY)
check("cache_read is read off a bare JSON body",
      u["cache_read_input_tokens"] == 158490, str(u))
check("input_tokens too", u["input_tokens"] == 90, str(u))
check("output_tokens too", u["output_tokens"] == 1, str(u))
check("stop_reason comes off the message, not a delta",
      u["stop_reason"] == "max_tokens", str(u))

print("\n[both dialects agree]")
check("SSE and JSON framings of the same turn parse identically",
      billing._parse_usage_from_sse(_SSE_BODY) == u,
      f"sse={billing._parse_usage_from_sse(_SSE_BODY)} json={u}")

m_json, m_sse = (billing._parse_response_meta(_JSON_BODY),
                 billing._parse_response_meta(_SSE_BODY))
for f in ("message_id", "resolved_model", "role", "stop_reason", "text",
          "content_block_types", "usage_final"):
    check(f"meta.{f} matches across dialects",
          m_json[f] == m_sse[f], f"json={m_json[f]!r} sse={m_sse[f]!r}")


# -- the consequence: the ledger stamps, and the turn bills -------------------
# This is the BEHAVIOUR the parse exists to enable, asserted through the same
# predicate warmth._record_warmth uses rather than by re-reading the fields.
print("\n[downstream consequence]")


def _would_stamp(usage):
    return ((usage or {}).get("cache_creation_input_tokens") or 0) > 0 \
        or ((usage or {}).get("cache_read_input_tokens") or 0) > 0


check("a cache-reading non-streaming turn passes the ledger's receipt guard",
      _would_stamp(u))
bill = billing._billing("messages", model_resolved=m_json["resolved_model"],
                        usage_final=m_json["usage_final"],
                        usage_start=m_json["usage_start"])
check("and it bills a real number, not $0.00",
      bill["est_usd"] and bill["est_usd"] > 0, str(bill["est_usd"]))
check("the cache read is what it's billed for",
      bill["tokens"]["cache_read_input_tokens"] == 158490, str(bill["tokens"]))

# A turn that genuinely cached NOTHING must still be refused — the guard is
# receipt discipline, and this fix must not turn it into a rubber stamp.
_nocache = json.dumps({"type": "message", "id": "m", "model": "claude-opus-5",
                       "role": "assistant", "content": [], "stop_reason": "end_turn",
                       "usage": {"input_tokens": 5, "output_tokens": 1,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 0}}).encode()
check("a non-streaming turn that cached nothing is still NOT stamped",
      not _would_stamp(billing._parse_usage_from_sse(_nocache)))


# -- shapes that are not a completed message ----------------------------------
print("\n[non-message shapes]")
err = json.dumps({"type": "error",
                  "error": {"type": "rate_limit_error", "message": "Error"},
                  "request_id": "req_1"}).encode()
check("a bare JSON error envelope surfaces its error",
      billing._parse_response_meta(err)["error"]
      == {"type": "rate_limit_error", "message": "Error"})
check("an error carries no usage (must not fake a cache receipt)",
      not _would_stamp(billing._parse_usage_from_sse(err)))
check("garbage bytes parse to empty usage, not an exception",
      billing._parse_usage_from_sse(b"\x00\xff not json") ==
      {"input_tokens": None, "output_tokens": None,
       "cache_creation_input_tokens": None, "cache_read_input_tokens": None,
       "stop_reason": None})
check("an empty body is handled",
      billing._parse_response_meta(b"")["message_id"] is None)
# count_tokens answers a bare JSON object too, but it is NOT type:message —
# it must not be mistaken for a turn with usage.
check("a count_tokens body is not read as a message",
      billing._json_message(json.dumps({"input_tokens": 12}).encode()) is None)


# -- real captures -------------------------------------------------------------
_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CAPTURE_DIR")
if _dir and os.path.isdir(_dir):
    print(f"\n[real captures: {_dir}]")
    seen_nonstream = seen_stream = agreed = mismatched = 0
    stamped = 0
    for p in glob.glob(os.path.join(_dir, "**", "*.request.json"), recursive=True):
        stem = p[:-len(".request.json")]
        try:
            req = json.load(open(p))
            blob = open(stem + ".response.sse", "rb").read()
        except Exception:
            continue
        whole = billing._json_message(blob)
        if whole is None:
            seen_stream += 1
            continue
        if whole.get("type") != "message":
            continue
        seen_nonstream += 1
        truth = whole.get("usage") or {}
        got = billing._parse_usage_from_sse(blob)
        if all(got[k] == truth.get(k) for k in
               ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens")):
            agreed += 1
        else:
            mismatched += 1
        if _would_stamp(got):
            stamped += 1
    # ASSERT THE INTERESTING CASE WAS REACHED: without a non-streaming response
    # in the corpus every check above it is vacuous.
    check("the corpus actually contains non-streaming responses",
          seen_nonstream > 0, f"{seen_nonstream} found")
    check("every non-streaming response's usage matches its own body",
          mismatched == 0, f"{mismatched} mismatched of {seen_nonstream}")
    if seen_nonstream:
        check("cache-reading ones would stamp the ledger",
              stamped > 0, f"{stamped} of {seen_nonstream}")
    print(f"  ({seen_nonstream} non-streaming, {seen_stream} streaming, "
          f"{agreed} agreed, {stamped} stampable)")
else:
    print("\n[real captures] skipped (pass a capture dir to enable)")

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILURE(S): ' + str(_fails)}")
sys.exit(1 if _fails else 0)
