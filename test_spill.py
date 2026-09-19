"""Offline checks for intent-body spill (SPILL.md, 2026-09-19).

The wire format is a CONTRACT with clodex: it builds the `@spill:<id>` resolver
against these exact bytes, so anything here that drifts silently breaks a
consumer we cannot see from this repo. What that makes worth pinning:

  * IDENTITY — traffic that does not spill is byte-identical, at every chunk
    boundary. This is ~100% of real traffic; a needless rewrite would be a wire
    change bought for nothing.
  * ROUND TRIP — the file on disk equals the body EXACTLY (no normalisation),
    because clodex's resolver substitutes it verbatim into a ticket.
  * NEVER LOSE A SPEC — every failure mode forwards the original body. A
    truncated task spec is worse than a spammy transcript.
  * SIGNED THINKING — only `text_delta` is ever rewritten.

Mutation-tested 2026-09-19: dropping the agent-name validation, the strict `>`,
the `\\Z` anchor, the dot-only guard, the one-space rule, the close() flush, the
overflow flush, and the thinking guard each fail at least one check here.
(`os.fsync` in spill_body is deliberately NOT covered — no in-process test can
observe it; it only matters across power loss.)

Run: python3 test_spill.py   (throwaway tmp dirs; no live ports)
"""
import asyncio
import json
import os
import sys
import tempfile

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="spilltest_logs_")
os.environ["WARMTH_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="spilltest_db_"), "warmth.sqlite")
os.environ["WIRESCOPE_SPILL_DIR"] = tempfile.mkdtemp(prefix="spilltest_spill_")
os.environ["WIRESCOPE_SPILL_VERBS"] = "task.add,task.respec,context.compact"
# The grammar tokens are CONFIG, not code (CLAUDE.md: no app-specific protocol
# parsing in the proxy). There is no default, so a consumer must state them.
os.environ["WIRESCOPE_SPILL_OPEN"] = "[agent:"
os.environ["WIRESCOPE_SPILL_END"] = "[agent:end]"

import logproxy as lp  # noqa: E402  (env must be set before import)
from proxylab import spill  # noqa: E402

FAILS = []
BIG = "z" * 900          # over the 800 B default threshold
SMALL = "y" * 50


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def ev(t, d):
    return ("event: " + t + "\ndata: " + json.dumps(d) + "\n\n").encode("utf-8")


def td(i, text):
    return ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                      "delta": {"type": "text_delta", "text": text}})


def run_text(text, agent="wirescope", cs=1):
    """Drive the line filter at chunk size `cs`."""
    f = spill._SpillFilter(agent)
    out = [f.feed(text[i:i + cs]) for i in range(0, len(text), cs)]
    out.append(f.close())
    return "".join(out)


def text_of(blob, kind="text_delta", key="text"):
    s = ""
    for e in blob.decode("utf-8", "replace").split("\n\n"):
        for ln in e.split("\n"):
            if ln.startswith("data:"):
                try:
                    d = json.loads(ln[5:])
                except Exception:
                    continue
                if d.get("type") == "content_block_delta" \
                        and (d.get("delta") or {}).get("type") == kind:
                    s += d["delta"].get(key, "")
    return s


# --- identity: everything that must not change -------------------------------
# Chunk sizes 1 and 7 split the head line, the pointer and `[agent:end]` across
# deltas; a real stream does exactly this (~43 chars/delta measured).
for name, t in {
    "plain prose": "Hello world.\nNo intents here.\n",
    "prose with brackets": "See [1], [agent], [agentx:foo]\ndone\n",
    "unlisted verb (dm)": f"[agent:dm bob] {BIG}\n[agent:end]\n",
    "below threshold": f"[agent:task add t1] {SMALL}\n[agent:end]\n",
    "no trailing newline": "abc",
    "bare [agent:end]": "[agent:end]\n",
}.items():
    check(f"identity: {name}",
          all(run_text(t, cs=cs) == t for cs in (1, 7, 10 ** 6)))

# --- the spill fires ----------------------------------------------------------
T = f"before\n[agent:task add t42 start] {BIG}\n[agent:end]\nafter\n"
outs = [run_text(T, cs=cs) for cs in (1, 3, 17, 10 ** 6)]
check("spill: fires at every chunk size", all("@spill:" in o for o in outs))
check("spill: identical output regardless of chunking", len(set(outs)) == 1)
check("spill: body removed from the wire", all(BIG not in o for o in outs))
check("spill: head line preserved byte-for-byte",
      all("[agent:task add t42 start] @spill:" in o for o in outs))
check("spill: surrounding prose untouched",
      all(o.startswith("before\n") and o.endswith("after\n") for o in outs))
check("spill: [agent:end] re-emitted", all("\n[agent:end]\n" in o for o in outs))

# --- round trip: disk content == body, byte for byte --------------------------
BODY = "line one\n\n  indented  \n" + BIG + "\nlast"     # blank lines + spaces
out = run_text(f"[agent:task add t7] {BODY}\n[agent:end]\n")
sid = out.split("@spill:")[1].split("\n")[0]
disk = open(os.path.join(os.environ["WIRESCOPE_SPILL_DIR"], "wirescope",
                         f"{sid}.md"), encoding="utf-8").read()
check("round trip: file content == body exactly (no normalisation)", disk == BODY)
check("round trip: id is sha256(body)[:16]",
      sid == __import__("hashlib").sha256(BODY.encode()).hexdigest()[:16])
check("round trip: pointer id is 16 lowercase hex",
      len(sid) == 16 and all(c in "0123456789abcdef" for c in sid))

# EXACTLY one space is skipped after ']' — the rest of the body is the body.
for lead, expect in [("   ", "  "), (" ", ""), ("\t", "\t")]:
    o = run_text(f"[agent:task add t]{lead}{BIG}\n[agent:end]\n")
    s = o.split("@spill:")[1].split("\n")[0]
    d = open(os.path.join(os.environ["WIRESCOPE_SPILL_DIR"], "wirescope",
                          f"{s}.md"), encoding="utf-8").read()
    check(f"round trip: exactly one space skipped (lead={lead!r})", d == expect + BIG)

# --- never lose a spec: every failure forwards the ORIGINAL --------------------
for bad in ["..", "...", ".", "", "a/b", "x" * 65, "ok\n"]:
    o = run_text(T.replace("before\n", ""), agent=bad)
    check(f"failure: invalid agent {bad!r} -> original body forwarded",
          BIG in o and "@spill" not in o)

_dir = spill.SPILL_DIR
spill.SPILL_DIR = "/proc/cannot-create-here"
check("failure: unwritable dir -> original body forwarded",
      BIG in run_text(T) and "@spill" not in run_text(T))
spill.SPILL_DIR = _dir

check("failure: missing [agent:end] -> original flushed at close",
      BIG in run_text(f"[agent:task add t9] {BIG}\n") )

_max = spill.SPILL_MAX_BYTES
spill.SPILL_MAX_BYTES = 200
# A body is usually ONE long line, so the cap has to be enforced on HELD bytes,
# not on completed lines — a per-line check never fires mid-line and the
# oversized body spills anyway. The strongest statement of "forward the
# original" is byte-equality with the input, which also catches a spurious
# newline inserted at a mid-line bail-out.
for cs in (1, 7, 64, 10 ** 6):
    check(f"failure: over MAX_BYTES -> stream byte-identical (cs={cs})",
          run_text(T, cs=cs) == T)
multi = f"[agent:task add t] {BIG}\nsecond line\nthird\n[agent:end]\nafter\n"
for cs in (1, 64, 10 ** 6):
    check(f"failure: over MAX_BYTES multi-line -> byte-identical (cs={cs})",
          run_text(multi, cs=cs) == multi)
spill.SPILL_MAX_BYTES = _max
# DELIBERATE, not a bug: once a body blows the cap the filter stops touching the
# rest of THAT response, so a later in-cap intent forwards whole. Nothing is
# lost; only the saving is. Pinned so the choice is visible if it ever changes.
spill.SPILL_MAX_BYTES = 2000
two = (f"[agent:task add t1] {'q' * 2500}\n[agent:end]\nprose\n"
       f"[agent:task add t2] {BIG}\n[agent:end]\n")
o = run_text(two, cs=64)
check("overflow: latches for the rest of the response (documented trade-off)",
      o == two and "@spill" not in o)
spill.SPILL_MAX_BYTES = _max
check("recovery: a fresh stream spills normally after an overflow elsewhere",
      "@spill:" in run_text(T))

_min = spill.SPILL_MIN_BYTES
spill.SPILL_MIN_BYTES = 3
check("threshold: strict '>' (equal does NOT spill)",
      "@spill" not in run_text("[agent:task add t] abc\n[agent:end]\n"))
check("threshold: one byte over DOES spill",
      "@spill" in run_text("[agent:task add t] abcd\n[agent:end]\n"))
spill.SPILL_MIN_BYTES = _min

# --- agent charset mirrors clodex's seat-minting rule -------------------------
# `^(?!\.+$)[a-zA-Z0-9._-]{1,64}$` — dots legal (`.hidden` deliberate), only
# all-dots rejected. core._ROUTE permits dots, so `/agent/../anthropic/...`
# parses with name='..': this validation is what keeps it out of a path.
check("agent: names clodex can mint are accepted",
      all(spill.valid_agent(g) for g in
          ["wirescope", "t42.fix", ".hidden", "a..b", "-rf", "x" * 64]))
check("agent: traversal / separators / overlong rejected",
      not any(spill.valid_agent(b) for b in
              ["..", "...", ".", "", "a/b", "../x", "x" * 65]))
check("agent: trailing newline rejected (\\Z, not $)",
      not spill.valid_agent("wirescope\n"))
check("agent: the route regex really does yield '..'",
      lp.core._ROUTE.match("/agent/../anthropic/v1/messages").group("name") == "..")

# --- SSE level: signed thinking is never touched ------------------------------
stream = b"".join([
    ev("message_start", {"type": "message_start"}),
    ev("content_block_start", {"type": "content_block_start", "index": 0,
                               "content_block": {"type": "thinking"}}),
    ev("content_block_delta", {"type": "content_block_delta", "index": 0,
       "delta": {"type": "thinking_delta",
                 "thinking": f"[agent:task add t1] {BIG}\n[agent:end]\n"}}),
    ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ev("content_block_start", {"type": "content_block_start", "index": 1,
                               "content_block": {"type": "text"}}),
    td(1, "Here you go.\n[agent:task add t42] "), td(1, BIG[:400]),
    td(1, BIG[400:]), td(1, "\n[agent:end]\nDone.\n"),
    ev("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ev("message_stop", {"type": "message_stop"}),
])
for cs in (1, 13, 997, len(stream)):
    t = spill.SpillTee("wirescope")
    o = b"".join(t.feed(stream[i:i + cs]) for i in range(0, len(stream), cs)) + t.close()
    check(f"sse: thinking_delta untouched (cs={cs})", BIG in text_of(o, "thinking_delta", "thinking"))
    check(f"sse: text_delta rewritten (cs={cs})",
          "@spill:" in text_of(o) and BIG not in text_of(o))
    check(f"sse: envelope events preserved (cs={cs})",
          o.count(b"event: content_block_start") == 2
          and o.count(b"event: content_block_stop") == 2
          and o.count(b"event: message_stop") == 1)
    check(f"sse: fired counter (cs={cs})", t.fired == 1)

# The guard is the delta TYPE, not the key name: a thinking_delta carrying a
# 'text' key must still pass through untouched.
sneaky = ev("content_block_delta", {"type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "x",
                      "text": f"[agent:task add t] {BIG}\n[agent:end]\n"}})
t = spill.SpillTee("wirescope")
check("sse: thinking_delta with a 'text' key still untouched",
      t.feed(sneaky) + t.close() == sneaky and t.fired == 0)

# Non-firing streams must be byte-identical, not merely content-equal.
prose = b"".join([
    ev("message_start", {"type": "message_start"}),
    ev("content_block_start", {"type": "content_block_start", "index": 0,
                               "content_block": {"type": "text"}}),
    td(0, "A normal answer. "), td(0, "With [1] a citation, "),
    td(0, "an [agent] bracket, "), td(0, "and `[agent:end]` inline.\n"),
    ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ev("message_stop", {"type": "message_stop"}),
])
for cs in (1, 5, 64, 10 ** 6):
    t = spill.SpillTee("wirescope")
    o = b"".join(t.feed(prose[i:i + cs]) for i in range(0, len(prose), cs)) + t.close()
    check(f"sse: non-firing stream byte-identical (cs={cs})", o == prose)

# REAL wire bytes, not json.dumps output. The Anthropic SSE uses compact
# separators AND pads events with trailing spaces, so a re-serialized delta is
# a different line even when the text is identical — an unchanged delta must be
# forwarded as the ORIGINAL bytes, never re-encoded. A fixture built with
# json.dumps cannot catch this (it matches our own spacing by construction).
real = (b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":1,'
        b'"delta":{"type":"text_delta","text":"4"}               }\n\n')
t = spill.SpillTee("wirescope")
check("sse: real wire delta (compact + padded) forwarded byte-identical",
      t.feed(real) + t.close() == real)

# Disabled = the filter never runs at all.
_verbs = spill.SPILL_VERBS
spill.SPILL_VERBS = frozenset()
t = spill.SpillTee("wirescope")
check("sse: disabled -> byte-identical",
      t.feed(stream) + t.close() == stream)
spill.SPILL_VERBS = _verbs


# --- end to end: through the server's real streaming closure ------------------
# A helper tested in isolation says nothing about whether its CALLER uses it
# (a mutation survived exactly that way earlier this session), so drive the
# tee the way server.body_iter does and assert on what the CLIENT receives.
def _drive_like_server(blob, agent, cs=64):
    tee = (spill.SpillTee(agent)
           if spill.enabled() and spill.valid_agent(agent) else None)
    out = bytearray()
    for i in range(0, len(blob), cs):
        chunk = blob[i:i + cs]
        out.extend(tee.feed(chunk) if tee is not None else chunk)
    if tee is not None:
        out.extend(tee.close())
    return bytes(out)


client_got = _drive_like_server(stream, "wirescope")
check("e2e: client receives the pointer, not the body",
      "@spill:" in text_of(client_got) and BIG not in text_of(client_got))
check("e2e: unrouted-style invalid agent streams verbatim",
      _drive_like_server(stream, "..") == stream)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
    sys.exit(1)
print("ALL PASS")

# --- the grammar is CONFIG: no tokens, no parsing -----------------------------
# Guards against the whole suite passing vacuously: if the tokens failed to
# load, enabled() would be False and the "identity" checks above would pass for
# the wrong reason. Also pins the durable ruling (CLAUDE.md, WB_INTENT_DISPATCH
# retired 2026-06-12): the proxy must not carry a consumer's protocol in code.
check("config: armed only when all four knobs are set", spill.enabled())
_open, _end = spill.SPILL_OPEN, spill.SPILL_END
spill.SPILL_OPEN = None
check("config: no opener token -> disarmed", not spill.enabled())
check("config: no opener token -> stream untouched", run_text(T) == T)
spill.SPILL_OPEN = _open

# A DIFFERENT grammar must work exactly as well — proof the tokens are data.
spill.SPILL_OPEN, spill.SPILL_END = "<<", ">>END"
alt = f"<<task add t1] {BIG}\n>>END\n"
o = run_text(alt)
check("config: an unrelated grammar spills identically",
      "@spill:" in o and BIG not in o and o.startswith("<<task add t1] @spill:"))
check("config: clodex's grammar is inert under the alternate config",
      run_text(T) == T)
spill.SPILL_OPEN, spill.SPILL_END = _open, _end
