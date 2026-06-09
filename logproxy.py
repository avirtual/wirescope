"""Minimal standalone logging/dumper forward-proxy (throwaway, #2873/#2874).

Captures the EXACT outbound request bodies AND inbound response SSE streams a
`claude` CLI exchanges with the Anthropic API — ground truth for "what is in a
subagent's context" (#2873, request side) and "what usage/cost the API returns"
(#2874, response side). No model introspection.

NOT the production proxy: no intent dispatcher, no workbench contact whatsoever.
It only logs request + response and forwards bytes.

Output layout (one subdirectory per session):
  LOG_DIR/<session_id>/<seq>-<agent>-<role>-<model>-<ts>.request.json
  LOG_DIR/<session_id>/<seq>-...-.response.sse | .response.json
  LOG_DIR/<session_id>/_session.json   per-session running total
  LOG_DIR/_no-session/...              count_tokens + probes (carry no metadata)
  LOG_DIR/_totals.json                 global process-lifetime total
The session_id is parsed out of metadata.user_id (itself a JSON string). All
disk writes are handed to a background thread so the proxy byte-path never
blocks on I/O (see _writer_loop).

Run:
  LOG_DIR=/tmp/proxyclone/logs python3 -m uvicorn logproxy:app --host 127.0.0.1 --port 7799

Point a CLI at it either way:
  - bare:   ANTHROPIC_BASE_URL=http://127.0.0.1:7799            -> path /v1/messages
  - routed: ANTHROPIC_BASE_URL=http://127.0.0.1:7799/agent/<name>/anthropic
            (the /agent/<name>/anthropic prefix is stripped before forwarding;
             <name> is captured as the agent id in the dump filename)
"""
import atexit
import collections
import hashlib
import itertools
import json
import os
import queue
import re
import sqlite3
import threading
import time
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

UPSTREAM = "https://api.anthropic.com"
LOG_DIR = Path(os.environ.get("LOG_DIR", "/tmp/proxyclone/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# hop-by-hop + accept-encoding (we want an uncompressed SSE stream we can read)
_HOP = {"host", "content-length", "connection", "transfer-encoding",
        "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
        "trailers", "upgrade", "accept-encoding"}

_counter = itertools.count(1)
_client = httpx.AsyncClient(timeout=httpx.Timeout(600.0), follow_redirects=False)

# /agent/<name>/anthropic/<rest>  ->  (name, /<rest>)
_ROUTE = re.compile(r"^/agent/(?P<name>[A-Za-z0-9_.-]+)/anthropic(?P<rest>/.*)?$")

# Inbound transport identity. The body's session_id is absent on count_tokens
# pre-flights, but the CLI stamps these on EVERY request, and they identify the
# calling CLI process/build/account (the "who sent this" the metadata omits).
# Secrets are redacted — never write the caller's API key to disk.
_SECRET_HEADERS = {"authorization", "x-api-key", "cookie", "proxy-authorization"}


def _safe_headers(headers):
    return {k: ("<redacted>" if k.lower() in _SECRET_HEADERS else v)
            for k, v in headers.items()}


# --- EXPERIMENTAL: payload injection (OFF by default; observer mode is default) -
# Two modes, both mutate the LAST user message of /v1/messages and forward the
# MODIFIED bytes (tail-only edit => the cached prefix still hits; we re-encode
# only when we actually change something):
#
#   1. UNCONDITIONAL (legacy): INJECT set -> append INJECT to every turn. The
#      original "piggyback" testbed (the 2+2 -> "and 3+3" probe).
#
#   2. MARKER-GATED (new): INJECT_MARKER set -> append INJECT_TEXT ONLY when the
#      user's prompt contains the marker substring (e.g. "Math:"). This lets the
#      HUMAN opt a turn into enhancement by typing a natural keyword, while
#      mundane turns pass through untouched. The injected text is phrased as a
#      natural continuation of the user's own message (NOT an "injection
#      protocol" banner) so the model complies without suspicion.
#
#      Env:
#        INJECT_MARKER  trigger substring, e.g. "Math:"  (case-sensitive)
#        INJECT_TEXT    what to append when the marker fires (default below)
#        INJECT_SEP     separator between original and injected text (default "\n\n")
INJECT = os.environ.get("INJECT")
INJECT_MARKER = os.environ.get("INJECT_MARKER")
# A natural-sounding second question; answer is 23*19 = 437 (easy to detect in
# the response, and clearly distinct from any plausible first-question answer).
_DEFAULT_INJECT_TEXT = "Also, what is 23 × 19?"
INJECT_TEXT = os.environ.get("INJECT_TEXT", _DEFAULT_INJECT_TEXT)
INJECT_SEP = os.environ.get("INJECT_SEP", "\n\n")

# "Volunteer context" mode: when INJECT_FILE points at a file, the proxy appends
# its CURRENT contents (read fresh from disk each turn) as an authoritative
# <system-reminder> on the last user message — the channel the model already
# trusts as ground truth. No marker, nothing for the agent to know about: it's
# the proxy proactively handing over context the agent would otherwise have to
# fetch with a Read tool call. The experiment then measures whether the agent
# skips its own Read (round trip collapsed) or fetches anyway (double ingestion).
INJECT_FILE = os.environ.get("INJECT_FILE")
_MAX_VOLUNTEER_BYTES = int(os.environ.get("INJECT_FILE_MAX_BYTES", "20000"))
# Optional operating instruction folded into the volunteered system-reminder.
# e.g. tell the agent it already has the exact bytes (so it needn't Read) and to
# apply changes via Bash/Write instead of Edit — routing AROUND FileEditTool's
# read-before-edit gate, which Bash/Write don't enforce. "Tools are just tools."
INJECT_FILE_NOTE = os.environ.get("INJECT_FILE_NOTE")

# --- RESPONSE-side mutation (experiment) --------------------------------------
# Everything above edits the REQUEST. This edits what comes BACK: can we alter
# what the model "said" before the CLI sees it, and does the client push back?
# The response is a streamed SSE; when either knob is set we buffer the full
# upstream response, rewrite it, and emit once (we lose streaming — fine for a
# test). Usage/billing is still parsed from the ORIGINAL bytes.
#   RESP_APPEND   — add a text_delta to the assistant's first text block.
#   RESP_REPLACE  — "old\x1fnew": swap text inside every text_delta.
RESP_APPEND = os.environ.get("RESP_APPEND")
RESP_REPLACE = os.environ.get("RESP_REPLACE")


def _resp_mutating():
    return bool(RESP_APPEND or RESP_REPLACE)


# --- SHORTCIRCUIT: elide the post-tool "wrap-up" round trip -------------------
# The Messages protocol forces a round trip after every tool_use: a response
# containing a tool_use always carries stop_reason "tool_use" (= "run this, I'll
# continue"), so even a SUCCESSFUL, TERMINAL edit still costs a whole extra turn
# just to hear the model say "Done." There is no way for the model to say "do
# this edit AND I'm finished" in one message — the protocol has no "last action"
# flag. That trailing turn re-ships the entire context (~one full cache_read
# carriage) to produce a ~20-token acknowledgment.
#
# We supply the missing affordance WITHOUT guessing: teach the model to mark a
# task-completing message with a sentinel (SHORTCIRCUIT_DONE, e.g. "<sc_done>") in
# the SAME message as its final tool call. Then, when the NEXT request is the
# tool_result continuation of a single SUCCESSFUL terminal tool_use whose
# assistant message carried the sentinel, the proxy SYNTHESIZES the end_turn
# response locally and NEVER forwards it upstream — saving that carriage. The
# terminality decision stays with the model (the only party that knows its plan);
# the proxy merely honors the signal the wire format can't otherwise carry.
#
# Safety: we refuse to short-circuit an ERROR tool_result (there the model must
# react), require exactly ONE tool_use of a known terminal tool, and require the
# result to be for THAT tool_use. Anything else falls through to a normal turn.
#   SHORTCIRCUIT_DONE   sentinel substring; its PRESENCE enables the mode
#   SHORTCIRCUIT_ACK    synthetic reply text (default "Done.")
#   SHORTCIRCUIT_TOOLS  comma-list of terminal tool names that may be elided
SHORTCIRCUIT_DONE = os.environ.get("SHORTCIRCUIT_DONE")
SHORTCIRCUIT_ACK = os.environ.get("SHORTCIRCUIT_ACK", "Done.")
# Default = the NATIVE authored-mutation tools only (their results are
# information-free: the model already knows the post-edit bytes, so the wrap-up is
# pure ceremony). To short-circuit a CUSTOM/MCP edit tool (e.g. the mutate-tool
# experiment's mcp__update__edit), opt it in explicitly:
#   SHORTCIRCUIT_TOOLS="Edit,Write,NotebookEdit,MultiEdit,mcp__update__edit"
# Don't bake experiment-specific names into the default — the syspatch prompt
# ENUMERATES this set into every session's system prompt, so stray names would
# reference tools not present in tools[].
SHORTCIRCUIT_TOOLS = set(filter(None, (os.environ.get(
    "SHORTCIRCUIT_TOOLS",
    "Edit,Write,NotebookEdit,MultiEdit"
).split(","))))

# RELAY mode: instead of a canned "Done.", the model pre-writes (in the SAME
# message as its terminal Edit) the summary it WOULD give after success. This is
# exact, not a guess: for an authored mutation like Edit the success result is
# information-free — it only confirms old_string matched; the model already knows
# the post-edit file byte-for-byte. We stash that prose keyed by tool_use_id at
# the edit turn AND blank it from the stream (so the success message isn't shown
# before the edit is confirmed), then REPLAY it as the synthetic wrap-up — but
# ONLY on a SUCCESS tool_result; on error we discard it and forward normally.
# This also lets us STRIP the sentinel cleanly (detection moves from "read it out
# of replayed history" to "match the tool_use_id we stashed"), so nothing leaks
# to the user. SHORTCIRCUIT_RELAY=1 enables it; without it we use SHORTCIRCUIT_ACK.
SHORTCIRCUIT_RELAY = os.environ.get("SHORTCIRCUIT_RELAY")
_PENDING_RELAY = collections.OrderedDict()   # tool_use_id -> pre-written prose
_PENDING_RELAY_CAP = 256

# IDEMPOTENCY GUARD for canned-ack SC. The gate is otherwise STATELESS: it
# re-evaluates history every request, so if the headless CLI ever RE-SENDS a
# continuation (it occasionally rejects/retries a synthetic end_turn in a long
# resumed session), a stateless gate would re-fire forever -> infinite loop
# (observed: 10x re-fire on one tf-build turn). We fire AT MOST ONCE per set of
# tool_use_ids; a re-send finds them already fired and falls through to a real
# upstream wrap-up (one extra turn, worst case — same bounded cost the relay
# path gets from its pop). Caps the blast radius of any CLI retry to 1, not N.
_SC_FIRED = collections.OrderedDict()         # frozenset(tool_use_ids) -> True
_SC_FIRED_CAP = 512


def _relay_active():
    return bool(SHORTCIRCUIT_DONE and SHORTCIRCUIT_RELAY)


# STANDING PROTOCOL INSTRUCTION (UX): so the user types a NORMAL prompt and never
# sees the protocol. When set, the proxy tail-appends this to the last user
# message on genuine prompt turns (cache-safe; skipped on tool_result hops). This
# is the "client bakes it in" simulation — a real client would ship it in the
# system prompt / tool descriptions. SHORTCIRCUIT_INSTRUCT=1 uses the default
# below (sentinel kept in sync with SHORTCIRCUIT_DONE); or set custom text.
_DEFAULT_SC_INSTRUCT = (
    "Efficiency protocol: do NOT spend a separate follow-up message just to "
    "report that your final tool call succeeded. Instead, in the SAME message as "
    "your final tool call this turn, FIRST write the after-the-fact summary you "
    "would normally give once it succeeds — say what you changed, in your own "
    "words and in the past tense (e.g. \"Added ...\") — then end that text with "
    f"the token {SHORTCIRCUIT_DONE or '<sc_done>'}. Assume the tool call will "
    "succeed; do not wait for its result to write the summary.")
_sc_instruct_env = os.environ.get("SHORTCIRCUIT_INSTRUCT")
SHORTCIRCUIT_INSTRUCT = (_DEFAULT_SC_INSTRUCT
                         if _sc_instruct_env in ("1", "default", "yes", "on")
                         else _sc_instruct_env)

# BEST-PLACEMENT delivery: patch the TERMINAL TOOLS' own descriptions in the
# request's tools[]. A tool description is a prompt the client ships in the
# cached prefix; appending the protocol THERE binds it to the exact action and is
# read precisely when the model chooses to use the tool — the most authoritative
# spot for a wavering model. Cache-stable (same text every turn → re-caches once).
# SHORTCIRCUIT_TOOLPATCH=1 uses the default below; or set custom text.
# NOTE: phrasing is DISPATCH-IMPERATIVE, modeled on the CLI's own
# getPreReadInstruction ("- You must use your `Read` tool ... before editing.") —
# an unconditional precondition bullet the model reliably obeys. The earlier
# default used POST-CONDITIONAL phrasing ("when a call to this tool completes …")
# which the signal-timing finding showed makes the model DEFER the sentinel to the
# wrap-up turn. This version frames the summary as a same-message output rule, not
# a reaction to the tool returning.
_DEFAULT_SC_TOOLPATCH = (
    "\n- Whenever you use this tool, you MUST, in the SAME message as the tool "
    "call, also write a one-line past-tense summary of the change you are making "
    "(e.g. \"Added ...\") and end that text with the token "
    f"{SHORTCIRCUIT_DONE or '<sc_done>'}. Write this summary now, as you make the "
    "call; do NOT wait for the tool's result and do NOT put it in a later "
    "message. Assume the call will succeed.")
_sc_toolpatch_env = os.environ.get("SHORTCIRCUIT_TOOLPATCH")
SHORTCIRCUIT_TOOLPATCH = (_DEFAULT_SC_TOOLPATCH
                          if _sc_toolpatch_env in ("1", "default", "yes", "on")
                          else _sc_toolpatch_env)


# SYSTEM-PROMPT delivery: append the protocol to the system block, enumerating
# the terminal tools it applies to. The point (per the cache thesis): inject the
# SAME text in the SAME position every turn so it joins the cached prefix — one
# cold write, then `cache_read` forever, like all the other re-shipped carriage.
# Front-of-prefix and INVISIBLE to the user (unlike INSTRUCT, which pollutes the
# visible user msg). Open question it tests: does a STANDING system rule fire the
# same-message behavior, or defer like the tool description? SHORTCIRCUIT_SYSPATCH=1
# uses the default below; or set custom text.
_sc_done_tok = SHORTCIRCUIT_DONE or '<sc_done>'
_DEFAULT_SC_SYSPATCH = (
    "\n\nOUTPUT RULE for the tools " + ", ".join(sorted(SHORTCIRCUIT_TOOLS)) + ": "
    "every time you call one of these tools, the SAME assistant message MUST ALSO "
    "contain a text block with a one-line PAST-TENSE summary of the change you are "
    f"making, ending with the token {_sc_done_tok}. Write that text block in the "
    "same message as the tool call. NEVER send one of these tool calls in a message "
    "by itself, and NEVER put the summary in a later message. Do NOT wait for the "
    "tool result before writing it: these tools return only success or failure, "
    "which tells you nothing you don't already know, and a failure will surface as "
    "an error you can handle on your next turn. Do NOT write forward-looking "
    "narration like \"I'll add...\" or \"Now writing...\"; write ONLY the past-tense "
    "summary as if the change is already done, then make the call in the same "
    "message. Assume the call succeeds.\n"
    "Example of a correct assistant message (text block + tool call together):\n"
    f"  text: \"Added a module-level docstring to sample.py. {_sc_done_tok}\"\n"
    "  tool_use: Edit(file_path=\"sample.py\", ...)")
_sc_syspatch_env = os.environ.get("SHORTCIRCUIT_SYSPATCH")
SHORTCIRCUIT_SYSPATCH = (_DEFAULT_SC_SYSPATCH
                         if _sc_syspatch_env in ("1", "default", "yes", "on")
                         else _sc_syspatch_env)


def _patch_system(obj):
    """Append the shortcircuit protocol to the system prompt in a STABLE position
    (the last text block / end of the string), so it's identical every turn and
    rides the prefix cache (one cold write, then cache_read). Idempotent. Returns
    True if it patched."""
    if not SHORTCIRCUIT_SYSPATCH:
        return False
    sys = obj.get("system")
    if isinstance(sys, list) and sys:
        # append to the LAST text block so we stay under its cache_control breakpoint
        for b in reversed(sys):
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                if SHORTCIRCUIT_SYSPATCH not in b["text"]:
                    b["text"] += SHORTCIRCUIT_SYSPATCH
                    return True
                return False
        return False
    if isinstance(sys, str):
        if SHORTCIRCUIT_SYSPATCH not in sys:
            obj["system"] = sys + SHORTCIRCUIT_SYSPATCH
            return True
    return False


# ---- PROXY-SIDE `rest` SPLIT (experimental, off by default) ---------------
# Under org/proxy scope the CLI welds ALL static system prose + ALL dynamic
# `# Environment` (cwd/git/dirs/platform) into ONE cached block (sys[-1], the
# "rest" block). Any env change busts that block, so the ~2.9k-tok static prose
# is re-WRITTEN (1.25x/2x) every change instead of READ (0.10x). This relocates
# the static head (everything BEFORE `\n# Environment`) onto the END of the
# preceding MARKED preamble block (sys[-2], "You are Claude Code..."), so it
# rides a DURABLE, env-independent cache prefix. SAFE: the concatenated system
# TEXT the model sees is byte-IDENTICAL — only a cache_control boundary moves
# (no reorder, no behavioural change, breakpoint count unchanged). FRAGILE: the
# split point is the `# Environment` header heuristic — version-pin + monitor
# hit-rate per CLI bump. Fleet-local (non-vanilla layout shares only with our
# own proxied sessions). DON'T touch block0 (attribution).
SPLIT_SYSTEM_REST = os.environ.get("SPLIT_SYSTEM_REST") in ("1", "yes", "on", "true")
_REST_SPLIT_MARKER = os.environ.get("SPLIT_SYSTEM_REST_MARKER", "\n# Environment")


def _split_system_rest(obj):
    """Move the static prose at the head of the env-bearing `rest` block onto the
    end of the preceding marked block. Byte-identical model-visible text; only a
    cache boundary shifts. Idempotent (once split, the marker sits at offset 0 of
    the rest block → no-op). Returns a log dict, or None if it didn't apply."""
    if not SPLIT_SYSTEM_REST:
        return None
    sys = obj.get("system")
    if not isinstance(sys, list) or len(sys) < 2:
        return None
    # the `rest` block is the one carrying the env header; host = the block before
    ri = next((i for i, b in enumerate(sys)
               if isinstance(b, dict) and isinstance(b.get("text"), str)
               and _REST_SPLIT_MARKER in b["text"]), None)
    if not ri:  # None, or 0 (no preceding block to host the static prose)
        return None
    rest, prev = sys[ri], sys[ri - 1]
    pt = prev.get("text")
    rt = rest["text"]
    if not isinstance(pt, str):
        return None
    idx = rt.find(_REST_SPLIT_MARKER)
    if idx <= 0:                      # marker at very start → already split / nothing to move
        return None
    static = rt[:idx]
    prev["text"] = pt + static        # host block (keeps its cache_control marker)
    rest["text"] = rt[idx:]           # rest block now starts at "\n# Environment"
    return {"host_block": ri - 1, "rest_block": ri, "moved_chars": len(static),
            "static_tail": static[-48:], "dynamic_head": rest["text"][:48]}


# ---- DESIGN-2: relocate volatile bits to the tail + mark CLAUDE.md (experimental)
# The CLI assembles a per-request context bundle in messages[0] (the <system-reminder>
# wrapping the on-disk CLAUDE.md, plus CLI-injected # userEmail / # currentDate) and
# ships the volatile `# Environment` block (cwd/git-branch/commits) in `system`. Because
# the cache prefix is cumulative (tools -> system -> messages), env sits UPSTREAM of
# CLAUDE.md and POISONS it: a branch/commit/worktree change re-WRITES the (often large)
# CLAUDE.md segment every session. This transform moves the two volatile, header-
# delimited pieces (`# Environment`, `# currentDate`) DOWN to a tail block right before
# the prompt, and gives the now-static CLAUDE.md bundle its OWN cache_control marker
# (the 4th breakpoint). Resulting layering:
#   M1 tools+preamble+static | M2 contextmgmt+append | M4 CLAUDE.md | M3 env+date+prompt
# so CLAUDE.md becomes an env-independent, project-shared cache segment.
# MODEL-VISIBLE (env now reads AFTER the project rules) -> behaviorally validate; this is
# NOT byte-identical like the rest-split. RELOCATE_CLAUDEMD_PATHSTAMP also strips the
# absolute "Contents of <path>/CLAUDE.md" stamp (cwd already lives in the relocated env)
# so the segment shares across WORKTREES too — that's dedupe, not forging a false path.
# ON BY DEFAULT (disable with RELOCATE_ENV_TO_TAIL=0 / RELOCATE_CLAUDEMD_PATHSTAMP=0).
# GENERALIZED: also fires when there is NO CLAUDE.md — it falls back to the
# userEmail/currentDate <system-reminder> bundle as the anchor, so env still leaves
# `system` (the big win: static system prefix becomes env-independent/shareable). A
# dedicated cache marker is only added for the LARGE claudeMd segment; without it we
# just relocate env and spend no extra marker. The injected marker mirrors the
# prevailing ttl (1h/5m) — a bare 5m marker before the CLI's 1h markers is a hard 400.
RELOCATE_ENV_TO_TAIL = os.environ.get("RELOCATE_ENV_TO_TAIL", "1") not in ("0", "no", "off", "false")
RELOCATE_CLAUDEMD_PATHSTAMP = os.environ.get("RELOCATE_CLAUDEMD_PATHSTAMP", "1") not in ("0", "no", "off", "false")
_ENV_SECTION_HDR = "\n# Environment"
_DATE_SECTION_HDR = "# currentDate"
_PATHSTAMP_RE = re.compile(r"Contents of /[^\n]*?CLAUDE\.md")


def _find_context_bundle(msgs):
    """(msg_index, block_index, block, has_claudemd) of the CLI's context bundle —
    a user text block carrying the <system-reminder> preamble. Prefers the block
    containing '# claudeMd' (the big static project segment); falls back to one
    with '# currentDate'/'# userEmail' so env-relocation ALSO works in repos with
    NO CLAUDE.md. (None, None, None, False) if no bundle block exists."""
    fallback = None
    for mi, m in enumerate(msgs):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for bi, b in enumerate(c):
            if not (isinstance(b, dict) and b.get("type") == "text"):
                continue
            t = b.get("text") or ""
            if "# claudeMd" in t:
                return mi, bi, b, True
            if fallback is None and ("# currentDate" in t or "# userEmail" in t):
                fallback = (mi, bi, b, False)
    return fallback if fallback else (None, None, None, False)


def _relocate_env_to_tail(obj):
    """Design-2 transform (see comment above). Returns a log dict, or None if it didn't
    apply (no CLAUDE.md bundle to protect / nothing volatile to move)."""
    if not RELOCATE_ENV_TO_TAIL:
        return None
    sysb = obj.get("system")
    msgs = obj.get("messages")
    if not isinstance(sysb, list) or not isinstance(msgs, list) or not msgs:
        return None
    mi, bi, cmd, has_claudemd = _find_context_bundle(msgs)
    if cmd is None:
        return None                          # no bundle block to anchor / pull date from
    moved = []
    # 1) pull the `# Environment` section out of whichever system block carries it
    rest = next((b for b in sysb if isinstance(b, dict)
                 and isinstance(b.get("text"), str)
                 and _ENV_SECTION_HDR in b["text"]), None)
    moved_env = ""
    if rest is not None:
        rt = rest["text"]
        i = rt.find(_ENV_SECTION_HDR)
        j = rt.find("\n# ", i + len(_ENV_SECTION_HDR))   # next top-level header (e.g. # Context management)
        moved_env = (rt[i:j] if j != -1 else rt[i:]).strip()
        rest["text"] = rt[:i] + (rt[j:] if j != -1 else "")
        moved.append("# Environment")
    # 2) pull the `# currentDate` section out of the claudeMd bundle (keep # userEmail)
    ct = cmd["text"]
    moved_date = ""
    di = ct.find(_DATE_SECTION_HDR)
    if di != -1:
        de = ct.find("\n\n", di)
        if de == -1:
            de = len(ct)
        moved_date = ct[di:de].strip()
        cmd["text"] = ct[:di].rstrip("\n") + "\n" + ct[de:].lstrip("\n")
        moved.append("# currentDate")
    # 2b) optional: dedupe the worktree-volatile absolute path stamp (only the
    #     claudeMd bundle carries a "Contents of <abspath>/CLAUDE.md" stamp)
    if RELOCATE_CLAUDEMD_PATHSTAMP and has_claudemd:
        new, n = _PATHSTAMP_RE.subn("Contents of CLAUDE.md", cmd["text"])
        if n:
            cmd["text"] = new
            moved.append("pathstamp")
    pieces = [p for p in (moved_env, moved_date) if p]
    if not pieces:
        return None
    # 3) assemble the relocated tail and insert it right AFTER the bundle block
    #    (so it lands between the bundle and the prompt marker)
    tail = "<system-reminder>\n" + "\n\n".join(pieces) + "\n</system-reminder>"
    msgs[mi]["content"].insert(bi + 1, {"type": "text", "text": tail})
    # 4) give the bundle its OWN cache_control breakpoint ONLY when it's the large
    #    static CLAUDE.md segment worth protecting as a shareable unit. For a tiny
    #    userEmail-only bundle (no CLAUDE.md) a marker buys nothing and would just
    #    spend our 4-marker budget — skip it; the win there is purely env leaving
    #    `system` (now fully static/shareable). When we DO mark, it MUST mirror the
    #    prevailing ttl: cache order is tools->system->messages and the API forbids
    #    a ttl='1h' block AFTER a ttl='5m' one, so a bare {ephemeral}=5m marker here
    #    sitting before the CLI's 1h prompt/system markers -> 400. Copy the ttl from
    #    the nearest preceding (last system) marker.
    claudemd_ttl = None
    if has_claudemd:
        cc = {"type": "ephemeral"}
        last_sys_ttl = next((b["cache_control"].get("ttl")
                             for b in reversed(sysb)
                             if isinstance(b, dict) and isinstance(b.get("cache_control"), dict)
                             and b["cache_control"].get("ttl")), None)
        if last_sys_ttl:
            cc["ttl"] = last_sys_ttl
        cmd["cache_control"] = cc
        claudemd_ttl = cc.get("ttl")
    return {"moved": moved, "has_claudemd": has_claudemd, "tail_chars": len(tail),
            "bundle_chars_after": len(cmd["text"]), "claudemd_ttl": claudemd_ttl}


# ---- SYSTEM-SECTION STRIP (experimental, off by default) ------------------
# Remove whole top-level `# Heading` sections from the system prompt by header.
# Unlike the rest-split (byte-identical, cache-only), this DELETES model-visible
# text — pure carriage reduction at the cost of dropping that instruction from
# the model's context. Use only for sections that are demonstrably irrelevant to
# the workload (e.g. `# Session-specific guidance` ultrareview prose ~520 chars).
# A section runs from its header line to the next column-0 `# ` header (or end of
# block). MODEL-VISIBLE + busts the system prefix once (the block's bytes change),
# then stable. ON BY DEFAULT, stripping the irrelevant `# Session-specific guidance`
# (ultrareview) prose. Config: STRIP_SYSTEM_SECTIONS = headers separated by `\x1f`.
#   - unset      -> default (strip `# Session-specific guidance`)
#   - custom     -> STRIP_SYSTEM_SECTIONS='# Foo\x1f# Bar'
#   - DISABLE    -> STRIP_SYSTEM_SECTIONS='' (empty)
_strip_env = os.environ.get("STRIP_SYSTEM_SECTIONS")
if _strip_env is None:
    _strip_env = "# Session-specific guidance"        # default-on
STRIP_SYSTEM_SECTIONS = [h for h in _strip_env.split("\x1f") if h.strip()]


def _strip_section_from_text(text, hdr):
    """Remove the `hdr` top-level section from text. Returns (new_text, chars_removed)."""
    m = re.search(r"(?m)^[ \t]*" + re.escape(hdr) + r"[ \t]*$", text)
    if not m:
        return text, 0
    start = m.start()
    nxt = re.search(r"(?m)^# ", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    new = text[:start] + text[end:]
    # collapse a seam of 3+ newlines left behind to a single blank line
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new, end - start


def _strip_system_sections(obj):
    """Delete configured `# Heading` sections from system text blocks. Returns a
    log dict, or None if nothing matched. Idempotent (gone → no further match)."""
    if not STRIP_SYSTEM_SECTIONS:
        return None
    sys = obj.get("system")
    removed = []
    if isinstance(sys, list):
        for bi, b in enumerate(sys):
            if not (isinstance(b, dict) and isinstance(b.get("text"), str)):
                continue
            for hdr in STRIP_SYSTEM_SECTIONS:
                new, n = _strip_section_from_text(b["text"], hdr)
                if n:
                    b["text"] = new
                    removed.append({"block": bi, "header": hdr, "chars": n})
    elif isinstance(sys, str):
        for hdr in STRIP_SYSTEM_SECTIONS:
            new, n = _strip_section_from_text(sys, hdr)
            if n:
                sys = new
                removed.append({"block": 0, "header": hdr, "chars": n})
        obj["system"] = sys
    return {"removed": removed} if removed else None


# ---- TOOL SORT (experimental, off by default) -----------------------------
# Alphabetically sort body.tools by name. Tools are logically FIRST in the cache
# order (cached under MARKER 1), so a STABLE order makes that segment byte-stable
# if the CLI ever emits tools in nondeterministic (readdir) order. Idempotent: if
# already sorted it's a no-op (no cache bust). The first re-ordering busts marker1
# once, then stable. Value is purely predictability. ON BY DEFAULT; disable with
# SORT_TOOLS=0. (Note we usually TRIM tools via native --tools rather than rely on
# a sorted full roster.)
SORT_TOOLS = os.environ.get("SORT_TOOLS", "1") not in ("0", "no", "off", "false")


def _sort_tools(obj):
    """Sort obj['tools'] by name. Returns log dict or None (no-op / already sorted)."""
    if not SORT_TOOLS:
        return None
    tools = obj.get("tools")
    if not isinstance(tools, list) or len(tools) < 2:
        return None
    names = [t.get("name") if isinstance(t, dict) else None for t in tools]
    if any(n is None for n in names):
        return None                      # can't safely sort an unnamed entry
    after = sorted(tools, key=lambda t: t.get("name", ""))
    after_names = [t.get("name") for t in after]
    if after_names == names:
        return None                      # already sorted → don't bust the cache
    obj["tools"] = after
    return {"before": names, "after": after_names}


# ---- STRIP COMPACT CACHE MARKER (experimental; off by default) -------------
# A `/compact` request re-ships the ENTIRE conversation history so the model can
# summarize it, and the CLI stamps its usual ROLLING message-level cache_control
# breakpoint on that history. But compaction REPLACES the history with the
# summary, so the cache written for that history is DISCARDED — never read again
# (measured: the next turn read 0 of it). On a BUSTED cache that marker therefore
# only forces a wasteful COLD WRITE at the 1.25x/2x premium; dropping it ships the
# history as plain 1.0x input instead, reclaiming the write premium (~25% of that
# chunk) for zero downside (the write was orphaned anyway).
#
# *** SAFE ONLY WHEN THE CACHE IS NOT WARM. *** On a WARM cache that same history
# is served as a 0.10x cache_read; stripping the marker would force a 1.0x input
# re-ship (~10x WORSE on that chunk). The strip is gated on the WARMTH LEDGER —
# now DURABLE (SQLite) and TWO-STATE (2026-06-09): 'warm' keeps the marker;
# NOT-warm ('cold' lapsed row, or 'absent') strips. With a durable store that
# receipt-stamps every confirmed cache event, absence ≈ expiry, so acting on it
# is sound: the residual loss case (absent-but-actually-warm: pre-store sessions,
# bypassed traffic) is one bounded ~0.9x overpay on a one-shot compact. Ledger
# 'off' or store 'error' still DECLINE — can't judge. A fork keep-warm ping keeps
# the entry warm, so an actively-pinged session won't get its compact stripped.
#
# We strip ONLY the MESSAGE-level marker(s) (the discarded history breakpoint) and
# KEEP the system markers (tools+system is legitimately reused by the post-compact
# turns and the fleet). Enable with STRIP_COMPACT_CACHE=1; force the decision either
# way with STRIP_COMPACT_FORCE=0/1 (experiments / the warm decline-to-strip control).
STRIP_COMPACT_CACHE = os.environ.get("STRIP_COMPACT_CACHE") in ("1", "yes", "on", "true")

# Stable anchors from the Claude Code compaction prompt (require >=2 -> ~0 FPs).
# Version-fragile by nature; the canary tracks wire shape, but if the CLI rewords
# this prompt the match silently stops — re-verify per CLI bump.
_COMPACT_ANCHORS = (
    "create a detailed summary of the conversation so far",
    "wrap your analysis in <analysis> tags",
    "an <analysis> block followed by a <summary> block",
    "Please provide your summary based on the conversation so far",
    "Primary Request and Intent",
)


def _is_compact_request(obj):
    """True iff the last user message is the Claude Code compaction prompt."""
    txt = _last_user_text(obj) or ""
    return sum(1 for a in _COMPACT_ANCHORS if a in txt) >= 2


def _prefix_hashes(obj):
    """Cumulative prefix hash at every message boundary. Returns {depth: hash}
    where depth = number of messages included (1..len). One forward pass; the
    hasher is copied at each boundary so old message bodies are hashed once."""
    h = hashlib.blake2b(digest_size=20)
    h.update(_sys_tools_fingerprint(obj))
    out = {}
    for i, m in enumerate(obj.get("messages") or []):
        h.update(b"\x1e")
        h.update(_canon_message(m))
        out[i + 1] = h.copy().hexdigest()
    return out


def _compact_history_warmth(obj):
    """(state, hash, depth) for the HISTORY prefix a /compact would read-or-rewrite.
    The reused cache segment is the LAST MARKED breakpoint, which sits some messages
    back from the tail (the compaction prompt, plus the assistant reply that grew the
    history since the previous request, are NOT yet a recorded breakpoint). So we
    check EVERY cumulative prefix below the compaction prompt against the store in
    one batched query: any WARM depth -> 'warm' (the backend can still serve that
    prefix as a 0.10x read; keep the marker). No warm depth -> not-warm, reported
    as 'cold' (deepest lapsed row, observability) or 'absent'. 'off'/'error' when
    the ledger can't judge (gates decline)."""
    if not WARMTH_LEDGER:
        return "off", None, 0
    msgs = obj.get("messages") or []
    if len(msgs) < 2:
        return "absent", None, 0
    hashes = _prefix_hashes(obj)
    depths = list(range(len(msgs) - 1, 0, -1))   # exclude the trailing compact prompt
    try:
        rows = _warmth_rows([hashes[d] for d in depths])
    except Exception:
        return "error", hashes.get(len(msgs) - 1), len(msgs) - 1
    now = time.time()
    lapsed = None
    for d in depths:
        r = rows.get(hashes[d])
        if r:
            if r[2] > now:
                return "warm", hashes[d], d
            if lapsed is None:
                lapsed = (hashes[d], d)
    if lapsed:
        return "cold", lapsed[0], lapsed[1]
    return "absent", hashes.get(len(msgs) - 1), len(msgs) - 1


def _compact_condition_met(obj):
    """Is it SAFE to strip the discarded history marker? TWO-STATE: strip iff the
    history prefix is NOT warm. On a warm cache that history is a 0.10x cache READ
    and stripping forces a 1.0x re-ship (~10x worse on that chunk) — decline. On
    'cold'/'absent' the marker only buys an orphaned write at the premium — strip
    (with a durable receipt-stamped store, absence ≈ expiry; the residual loss
    case is one bounded overpay on a one-shot compact). 'off'/'error' decline:
    absence is evidence, a disabled or broken store is not.
    Override for experiments: STRIP_COMPACT_FORCE=0/1."""
    force = os.environ.get("STRIP_COMPACT_FORCE")
    if force is not None:
        return force in ("1", "yes", "on", "true")
    return _compact_history_warmth(obj)[0] in ("cold", "absent")


def _strip_compact_cache(obj):
    """If this is a compaction request AND the history prefix is NOT warm, remove
    cache_control from MESSAGE blocks only (keep system markers). Returns a log dict
    or None (not a compact request / declined / nothing to strip). Two-state gate:
    'warm' keeps the marker; 'cold'/'absent' strip; 'off'/'error' decline."""
    if not STRIP_COMPACT_CACHE or not isinstance(obj, dict):
        return None
    if not _is_compact_request(obj):
        return None
    state, hhash, depth = _compact_history_warmth(obj)
    force = os.environ.get("STRIP_COMPACT_FORCE")
    condition = ((force in ("1", "yes", "on", "true")) if force is not None
                 else state in ("cold", "absent"))
    if not condition:
        return {"compact": True, "condition_met": False, "removed": 0,
                "warmth_state": state, "history_hash": hhash, "history_depth": depth,
                "forced": force is not None,
                "note": "declined to strip (strip only when the history prefix is "
                        f"not warm and the store can judge; history is {state})"}
    removed = []
    for mi, m in enumerate(obj.get("messages") or []):
        c = m.get("content")
        if isinstance(c, list):
            for bi, blk in enumerate(c):
                if isinstance(blk, dict) and blk.get("cache_control"):
                    cc = blk.pop("cache_control")
                    removed.append({"msg": mi, "block": bi, "type": blk.get("type"),
                                    "cache_control": cc})
    sys_markers = sum(1 for b in (obj.get("system") or [])
                      if isinstance(b, dict) and b.get("cache_control"))
    return {"compact": True, "condition_met": True,
            "warmth_state": state, "history_hash": hhash, "history_depth": depth,
            "forced": force is not None,
            "removed_message_markers": len(removed), "removed": removed,
            "kept_system_markers": sys_markers}


# ---- VERSION-DRIFT CANARY (read-only; on by default) ----------------------
# Borrowed from claude-code-cache-fix's upstream-change-detection: every lever in
# this proxy is version-fragile (split byte-offsets, the `# Environment` header,
# tool names, cache_control marker COUNT/positions). A silent CLI wire-shape
# change makes a transform no-op with zero signal. This builds a content-light
# STRUCTURAL fingerprint per (model, anthropic-beta) namespace, persists a
# baseline, and logs+prints a `structural_change` whenever the shape drifts.
# It is the early-warning system for "Anthropic shipped a CLI update that may have
# broken our transforms" — and, specifically, it tracks the total cache_control
# MARKER COUNT, so the day the CLI starts emitting a 4th marker (which it does NOT
# today) the canary fires immediately. Read-only: never mutates the request, runs
# on the ORIGINAL body before our transforms. Disable with CANARY=0.
CANARY_ENABLED = os.environ.get("CANARY", "1") not in ("0", "no", "off", "false")
_CANARY_DIR = Path(os.environ.get("CANARY_DIR", str(LOG_DIR / "_canary")))
_CANARY_BASELINES = {}                    # namespace -> compared-fingerprint dict
_CANARY_LOCK = threading.Lock()
_CANARY_LOADED = False


def _size_bucket(n):
    """Coarse log2 bucket so benign size jitter doesn't fire the canary."""
    b = 0
    while n > 1:
        n >>= 1
        b += 1
    return b


def _count_markers(blocks):
    return sum(1 for b in blocks
               if isinstance(b, dict) and b.get("cache_control")) \
        if isinstance(blocks, list) else 0


def _request_fingerprint(obj, headers):
    """Content-light structural shape of a /v1/messages request. Stable across
    normal conversation growth (message COUNT/content excluded from the diff);
    fires on tool-set, system-block-shape, or cache_control-marker changes."""
    tools = obj.get("tools") or []
    sys = obj.get("system")
    sys_blocks = sys if isinstance(sys, list) else ([{"text": sys}] if sys else [])
    msgs = obj.get("messages") or []
    msg_markers = sum(_count_markers(m.get("content")) for m in msgs
                      if isinstance(m, dict))
    tool_markers = _count_markers(tools)
    sys_markers = _count_markers(sys_blocks)
    sys_sig = []
    for b in sys_blocks:
        t = b.get("text", "") if isinstance(b, dict) else (b if isinstance(b, str) else "")
        cc = b.get("cache_control") if isinstance(b, dict) else None
        sys_sig.append({
            "hdr": (t or "")[:48],
            "size_bucket": _size_bucket(len(t or "")),
            "cc": cc.get("type") if isinstance(cc, dict) else None,
            "ttl": cc.get("ttl") if isinstance(cc, dict) else None,
        })
    beta = sorted(h.strip() for h in (headers.get("anthropic-beta", "") or "").split(",") if h.strip())
    return {
        "model": obj.get("model"),
        "beta": beta,
        "n_tools": len(tools),
        "tool_names": sorted(t.get("name") for t in tools
                             if isinstance(t, dict) and t.get("name")),
        "n_sys_blocks": len(sys_blocks),
        "sys_sig": sys_sig,
        "markers": {"tools": tool_markers, "system": sys_markers,
                    "messages": msg_markers,
                    "total": tool_markers + sys_markers + msg_markers},
    }


def _fp_diff(old, new):
    """Human-readable list of structural differences between two fingerprints."""
    diffs = []
    for k in ("n_tools", "n_sys_blocks"):
        if old.get(k) != new.get(k):
            diffs.append(f"{k}: {old.get(k)} -> {new.get(k)}")
    if old.get("tool_names") != new.get("tool_names"):
        o, n = set(old.get("tool_names") or []), set(new.get("tool_names") or [])
        if n - o:
            diffs.append(f"tools added: {sorted(n - o)}")
        if o - n:
            diffs.append(f"tools removed: {sorted(o - n)}")
    if old.get("beta") != new.get("beta"):
        diffs.append(f"beta: {old.get('beta')} -> {new.get('beta')}")
    om, nm = old.get("markers") or {}, new.get("markers") or {}
    for k in ("tools", "system", "messages", "total"):
        if om.get(k) != nm.get(k):
            diffs.append(f"markers.{k}: {om.get(k)} -> {nm.get(k)}")
    if old.get("sys_sig") != new.get("sys_sig"):
        os_, ns = old.get("sys_sig") or [], new.get("sys_sig") or []
        for i in range(max(len(os_), len(ns))):
            a = os_[i] if i < len(os_) else None
            b = ns[i] if i < len(ns) else None
            if a != b:
                diffs.append(f"sys_block[{i}]: {a} -> {b}")
    return diffs


def _canary_check(obj, headers, seq):
    """Compare this request's structural fingerprint to the persisted baseline for
    its (model, beta) namespace; on drift, log + print a structural_change. Returns
    a small dict for the request record. Read-only, fail-open."""
    if not CANARY_ENABLED:
        return None
    global _CANARY_LOADED
    try:
        fp = _request_fingerprint(obj, headers)
    except Exception:
        return None
    ns = f"{fp.get('model')}|{','.join(fp.get('beta') or [])}"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ns)[:120] or "default"
    with _CANARY_LOCK:
        if not _CANARY_LOADED:
            try:
                for p in _CANARY_DIR.glob("baseline-*.json"):
                    d = json.loads(p.read_text())
                    _CANARY_BASELINES[d.get("_ns", p.stem)] = d.get("fingerprint", d)
            except Exception:
                pass
            _CANARY_LOADED = True
        old = _CANARY_BASELINES.get(ns)
        if old is None:
            _CANARY_BASELINES[ns] = fp
            _enqueue_json(_CANARY_DIR / f"baseline-{safe}.json",
                          {"_ns": ns, "first_seq": seq, "fingerprint": fp})
            print(f"[canary] #{seq} new namespace baseline {ns!r}: "
                  f"{fp['n_tools']} tools, {fp['n_sys_blocks']} sys-blocks, "
                  f"{fp['markers']['total']} markers", flush=True)
            return {"namespace": ns, "event": "baseline", "markers": fp["markers"]}
        diffs = _fp_diff(old, fp)
        if not diffs:
            return {"namespace": ns, "event": "match", "markers": fp["markers"]}
        # drift: record, persist the new baseline, shout
        _CANARY_BASELINES[ns] = fp
        event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "seq": seq,
                 "namespace": ns, "diffs": diffs, "old": old, "new": fp}
        _enqueue_append(_CANARY_DIR / "changes.jsonl", event)
        _enqueue_json(_CANARY_DIR / f"baseline-{safe}.json",
                      {"_ns": ns, "first_seq": seq, "fingerprint": fp})
        marker_moved = old.get("markers", {}).get("total") != fp["markers"]["total"]
        bang = "  *** CACHE-MARKER COUNT CHANGED ***" if marker_moved else ""
        print(f"[canary] #{seq} STRUCTURAL CHANGE {ns!r}: {'; '.join(diffs)}{bang}",
              flush=True)
        return {"namespace": ns, "event": "structural_change", "diffs": diffs,
                "marker_count_changed": marker_moved, "markers": fp["markers"]}


def _patch_tool_descriptions(obj):
    """Append the shortcircuit protocol to each terminal tool's description in
    the request's tools[] (idempotent + cache-stable). Returns the list of tool
    names patched (empty if none)."""
    if not SHORTCIRCUIT_TOOLPATCH:
        return []
    tools = obj.get("tools")
    if not isinstance(tools, list):
        return []
    patched = []
    for t in tools:
        if isinstance(t, dict) and t.get("name") in SHORTCIRCUIT_TOOLS:
            d = t.get("description")
            if isinstance(d, str) and SHORTCIRCUIT_TOOLPATCH not in d:
                t["description"] = d + SHORTCIRCUIT_TOOLPATCH
                patched.append(t.get("name"))
    return patched


def _last_assistant_block(obj):
    """The most-recent role==assistant message dict, or None."""
    msgs = obj.get("messages") or []
    return next((m for m in reversed(msgs) if m.get("role") == "assistant"), None)


def _shortcircuit_decision(obj):
    """Return a dict describing why the wrap-up turn can be elided, or None.

    Fires only when ALL hold:
      * the last message is a USER turn carrying tool_result(s), none an error;
      * the most-recent ASSISTANT message contains the SHORTCIRCUIT_DONE sentinel
        in its text AND >=1 tool_use, where EVERY tool_use is a known terminal/
        info-free tool (SHORTCIRCUIT_TOOLS) whose id matches a tool_result here.
    Count is NOT the criterion — TOOL TYPE is. An authored mutation's result is
    information-free (the model already knows the post-edit bytes; the result is
    just a pass/fail bit we gate on), so N parallel Writes are as elidable as one.
    But if ANY tool in the batch returns information the model would act on (a
    Read/Bash/Grep mixed in), we must NOT elide — the model needs to see it — so
    the all-in-allowlist check is the real safety boundary, not the cardinality.
    The continuation already carries the REAL results (the CLI ran the tools
    before sending), so we are not assuming success — we verify every result and
    bail on any error."""
    if not SHORTCIRCUIT_DONE:
        return None
    msgs = obj.get("messages") or []
    if not msgs:
        return None
    last = msgs[-1]
    if last.get("role") != "user":
        return None
    lc = last.get("content")
    if not isinstance(lc, list):
        return None
    results = [b for b in lc if isinstance(b, dict) and b.get("type") == "tool_result"]
    if not results or any(b.get("is_error") for b in results):
        return None  # need success result(s); never elide an error
    asst = _last_assistant_block(obj)
    ac = asst.get("content") if asst else None
    if not isinstance(ac, list):
        return None
    text = " ".join(b.get("text", "") for b in ac
                    if isinstance(b, dict) and b.get("type") == "text")
    if SHORTCIRCUIT_DONE not in text:
        return None
    tool_uses = [b for b in ac if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not tool_uses:
        return None
    # EVERY tool_use must be an info-free terminal mutation (so its result carries
    # nothing the model would act on) AND have a matching successful result here.
    if any(tu.get("name") not in SHORTCIRCUIT_TOOLS for tu in tool_uses):
        return None
    result_ids = {b.get("tool_use_id") for b in results}
    if any(tu.get("id") not in result_ids for tu in tool_uses):
        return None
    ids = [tu.get("id") for tu in tool_uses]
    key = frozenset(ids)
    if key in _SC_FIRED:
        return None  # already short-circuited this exact turn — a CLI retry; let it go upstream
    if len(_SC_FIRED) >= _SC_FIRED_CAP:
        _SC_FIRED.clear()
    _SC_FIRED[key] = True
    return {"tools": [tu.get("name") for tu in tool_uses], "tool_use_ids": ids,
            "sentinel": SHORTCIRCUIT_DONE, "ack": SHORTCIRCUIT_ACK}


def _synth_end_turn_sse(model, ack, msg_id):
    """Build a minimal, VALID Anthropic streaming response: one text block = ack,
    stop_reason end_turn, zeroed usage (we ran no inference). Same event grammar
    the CLI parses from a real stream, so it's accepted transparently."""
    def ev(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"
    return "".join([
        ev("message_start", {"type": "message_start", "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "model": model or "claude", "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}}}),
        ev("content_block_start", {"type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": ack}}),
        ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ev("message_delta", {"type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 0}}),
        ev("message_stop", {"type": "message_stop"}),
    ]).encode("utf-8")


def _relay_capture_and_strip(blob: bytes) -> bytes:
    """RELAY edit-turn handler. If this response carries exactly one terminal
    tool_use plus a text block containing the sentinel, STASH the cleaned prose
    keyed by the tool_use_id and BLANK that text block in the stream (so the
    pre-written success message is NOT shown before the edit is confirmed).
    Returns the rewritten SSE, or the blob unchanged if it doesn't qualify.
    Every other event (incl. thinking + its signature) is byte-preserved."""
    events = blob.decode("utf-8", "replace").split("\n\n")
    tool_uses = []                                  # (index, id, name)
    text_by_idx = collections.defaultdict(list)     # index -> [text...]
    for ev in events:
        d = _data_of(ev)
        if not d:
            continue
        t = d.get("type")
        if t == "content_block_start":
            cb = d.get("content_block") or {}
            if cb.get("type") == "tool_use":
                tool_uses.append((d.get("index"), cb.get("id"), cb.get("name")))
        elif t == "content_block_delta" and (d.get("delta") or {}).get("type") == "text_delta":
            text_by_idx[d.get("index")].append(d["delta"].get("text", ""))
    if not tool_uses:
        return blob
    # Same info-free criterion as _shortcircuit_decision: EVERY tool_use must be a
    # terminal mutation; count is irrelevant (N parallel Writes are still elidable).
    if any((not tid) or tname not in SHORTCIRCUIT_TOOLS
           for _idx, tid, tname in tool_uses):
        return blob
    sent_idx = next((i for i, parts in text_by_idx.items()
                     if SHORTCIRCUIT_DONE in "".join(parts)), None)
    if sent_idx is None:
        return blob
    prose = "".join(text_by_idx[sent_idx]).replace(SHORTCIRCUIT_DONE, "").strip()
    if len(_PENDING_RELAY) >= _PENDING_RELAY_CAP:
        _PENDING_RELAY.clear()
    # Stash the one combined summary under EVERY tool_use_id in the batch; the
    # wrap-up handler pops them all and replays the prose once.
    for _idx, tid, _tname in tool_uses:
        _PENDING_RELAY[tid] = prose or SHORTCIRCUIT_ACK
    # REMOVE the sentinel-bearing text block entirely (an EMPTY text block is
    # rejected by the API when the message is replayed in history) and shift
    # later blocks down so indices stay contiguous. Thinking blocks and their
    # signatures (before sent_idx) are byte-preserved.
    rebuilt = []
    for ev in events:
        d = _data_of(ev)
        if d is None:
            rebuilt.append(ev)                       # blank separators / non-JSON
            continue
        idx = d.get("index")
        if idx == sent_idx:
            continue                                 # drop every event of that block
        if isinstance(idx, int) and idx > sent_idx:
            d["index"] = idx - 1                     # keep block indices contiguous
            rebuilt.append(f"event: {d.get('type')}\ndata: " + json.dumps(d))
        else:
            rebuilt.append(ev)                       # unchanged (byte-preserved)
    return "\n\n".join(rebuilt).encode("utf-8")


def _shortcircuit_relay_decision(obj):
    """RELAY wrap-up handler: fire when the request's last user message carries a
    SUCCESS tool_result whose tool_use_id we stashed prose for at the edit turn.
    The ack is the model's own pre-written summary (popped from _PENDING_RELAY)."""
    if not _relay_active():
        return None
    msgs = obj.get("messages") or []
    if not msgs or msgs[-1].get("role") != "user":
        return None
    lc = msgs[-1].get("content")
    if not isinstance(lc, list):
        return None
    if any(isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")
           for b in lc):
        return None  # any error in the batch -> let the model react, don't relay
    matched = [b.get("tool_use_id") for b in lc
               if isinstance(b, dict) and b.get("type") == "tool_result"
               and b.get("tool_use_id") in _PENDING_RELAY]
    if not matched:
        return None
    ack = _PENDING_RELAY.pop(matched[0])          # the one combined summary
    for tid in matched[1:]:
        _PENDING_RELAY.pop(tid, None)             # drain the rest of the batch
    return {"tool_use_ids": matched, "ack": ack,
            "sentinel": SHORTCIRCUIT_DONE, "relayed": True}


def _data_of(ev_text):
    """The parsed `data:` JSON of one SSE event block, or None."""
    for ln in ev_text.split("\n"):
        if ln.startswith("data:"):
            try:
                return json.loads(ln[5:].strip())
            except Exception:
                return None
    return None


def _mutate_sse(blob: bytes) -> bytes:
    """Rewrite a captured SSE stream (event-granular, events split on blank line):
      * RESP_REPLACE swaps text inside every text_delta.
      * RESP_APPEND adds a text_delta into the LAST text block (so it concatenates
        onto the model's visible answer; we target a text block, never thinking).
    Every other event is preserved."""
    old = new = None
    if RESP_REPLACE and "\x1f" in RESP_REPLACE:
        old, new = RESP_REPLACE.split("\x1f", 1)
    events = blob.decode("utf-8", "replace").split("\n\n")

    if old is not None:
        for i, ev in enumerate(events):
            d = _data_of(ev)
            if d and d.get("type") == "content_block_delta" \
                    and (d.get("delta") or {}).get("type") == "text_delta":
                d["delta"]["text"] = d["delta"].get("text", "").replace(old, new)
                events[i] = "event: content_block_delta\ndata: " + json.dumps(d)

    if RESP_APPEND:
        text_idx = None
        for ev in events:          # last content_block_start whose block is text
            d = _data_of(ev)
            if d and d.get("type") == "content_block_start" \
                    and (d.get("content_block") or {}).get("type") == "text":
                text_idx = d.get("index")
        if text_idx is not None:
            for i, ev in enumerate(events):   # insert before that block's stop
                d = _data_of(ev)
                if d and d.get("type") == "content_block_stop" and d.get("index") == text_idx:
                    inj = "event: content_block_delta\ndata: " + json.dumps(
                        {"type": "content_block_delta", "index": text_idx,
                         "delta": {"type": "text_delta", "text": RESP_APPEND}})
                    events.insert(i, inj)
                    break
    return "\n\n".join(events).encode("utf-8")


def _guess_lang(path):
    return {"py": "python", "js": "javascript", "ts": "typescript", "json": "json",
            "sh": "bash", "go": "go", "rs": "rust", "md": "markdown"}.get(
        path.rsplit(".", 1)[-1].lower(), "")


def _file_volunteer_text(path):
    """Read `path` fresh and wrap it as an authoritative system-reminder. Returns
    None if unreadable (so we forward the request untouched)."""
    try:
        data = open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    if len(data) > _MAX_VOLUNTEER_BYTES:
        data = data[:_MAX_VOLUNTEER_BYTES] + "\n…(truncated)…"
    lang = _guess_lang(path)
    note = f"\n\n{INJECT_FILE_NOTE}" if INJECT_FILE_NOTE else ""
    return (f"<system-reminder>\nFor reference, the current contents of {path} "
            f"are shown below.\n\n```{lang}\n{data}\n```{note}\n</system-reminder>")


def _last_user_block(obj):
    """Return the last role==user message dict, or None.

    Scans backward: the CLI appends a trailing role==system catalog block after
    the user's turn, so messages[-1] is often NOT the user's prompt."""
    msgs = obj.get("messages")
    if not msgs:
        return None
    return next((m for m in reversed(msgs) if m.get("role") == "user"), None)


def _last_user_text(obj):
    """Flatten the last user message's text (str content or text blocks)."""
    last = _last_user_block(obj)
    if last is None:
        return None
    c = last.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return None


def _inject_into_last_user(obj, text, sep="\n\n"):
    """Append `text` to the last USER message's text. Returns the original text
    if a change was made, else None (so the caller can skip re-encoding)."""
    last = _last_user_block(obj)
    if last is None:
        return None
    c = last.get("content")
    if isinstance(c, str):
        last["content"] = c + sep + text
        return c
    if isinstance(c, list):
        for blk in reversed(c):
            if isinstance(blk, dict) and blk.get("type") == "text":
                orig = blk.get("text", "")
                blk["text"] = orig + sep + text
                return orig
        c.append({"type": "text", "text": text})
        return ""
    return None


def _decide_injection(obj):
    """Return (text_to_append, reason) for this request, or (None, None).

    Priority: file-volunteer > marker-gated > unconditional.
      * INJECT_FILE   — append the file's current contents as a system-reminder,
        but ONLY on a genuine prompt turn (the last user message has text). We
        skip tool_result continuations inside a tool loop (their last 'user'
        message carries no prompt text), so we volunteer the context once per
        user turn rather than on every hop.
      * INJECT_MARKER — append INJECT_TEXT only when the prompt contains the marker.
      * INJECT        — unconditional append."""
    if INJECT_FILE:
        if _last_user_text(obj):  # genuine prompt turn, not a tool_result hop
            txt = _file_volunteer_text(INJECT_FILE)
            if txt:
                return (txt, f"file_volunteer:{INJECT_FILE}")
        return (None, None)
    if INJECT_MARKER:
        lut = _last_user_text(obj) or ""
        if INJECT_MARKER in lut:
            return (INJECT_TEXT, f"marker:{INJECT_MARKER!r}")
        return (None, None)
    if INJECT:
        return (INJECT, "unconditional")
    return (None, None)

# --- async disk writer --------------------------------------------------------
# The proxy must add no visible overhead, so NOTHING on the request/response
# byte-path touches the disk. The handler only enqueues (an O(1) put); a single
# background daemon thread does the mkdir + json.dumps + write. One thread keeps
# writes serialized (stable file ordering) and avoids thread-explosion.
NO_SESSION = "_no-session"          # bucket for requests that carry no session_id
_WRITE_Q: "queue.Queue" = queue.Queue()


def _writer_loop():
    while True:
        item = _WRITE_Q.get()
        try:
            if item is None:
                return
            path, kind, data = item
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "bytes":
                path.write_bytes(data)
            elif kind == "append":  # one JSON object per line (canary change-log)
                with path.open("a") as fh:
                    fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            elif kind == "ledger":  # hash+touch the prefix-warmth ledger off-thread
                obj, usage = data
                rec = _record_warmth(obj, usage)
                if rec is not None:
                    print(f"[warmth] {rec['hash'][:12]} ttl={rec['ttl']}s "
                          f"{'PING' if rec['ping'] else 'turn'} "
                          f"warm_on_arrival={rec['warm_on_arrival']} "
                          f"(ledger={rec['ledger_size']})", flush=True)
                    if path is not None:
                        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
            else:  # "json" — serialize off the event loop, in this thread
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass
        finally:
            _WRITE_Q.task_done()


_writer_thread = threading.Thread(target=_writer_loop, name="logwriter", daemon=True)
_writer_thread.start()


def _flush_writes():
    """Drain pending writes on shutdown so no captures are lost."""
    try:
        _WRITE_Q.join()
        _WRITE_Q.put(None)
        _writer_thread.join(timeout=5)
    except Exception:
        pass


atexit.register(_flush_writes)


def _enqueue_json(path: Path, obj):
    _WRITE_Q.put((path, "json", obj))


def _enqueue_bytes(path: Path, blob: bytes):
    _WRITE_Q.put((path, "bytes", blob))


def _enqueue_append(path: Path, obj):
    _WRITE_Q.put((path, "append", obj))


def _enqueue_ledger(path, obj, usage):
    """Hand the (post-transform) request body + response usage to the writer
    thread, which hashes the cacheable prefix and refreshes the warmth ledger.
    `path` (a <stem>.warmth.json) is written too when WARMTH_LOG_FILE is on."""
    _WRITE_Q.put((path, "ledger", (obj, usage)))


def _session_ids(obj):
    """Parse session_id/account_uuid/device_id out of metadata.user_id.

    metadata.user_id is itself a JSON STRING, e.g.
    '{"device_id":"…","account_uuid":"…","session_id":"…"}'. Only `messages`
    requests carry it; count_tokens/probes do not (-> NO_SESSION bucket)."""
    uid = (obj.get("metadata") or {}).get("user_id")
    if not uid:
        return None, None, None
    try:
        d = json.loads(uid)
        return d.get("session_id"), d.get("account_uuid"), d.get("device_id")
    except Exception:
        return None, None, None


def _sys_text(obj):
    sys = obj.get("system")
    if isinstance(sys, list):
        return " ".join(b.get("text", "") for b in sys if isinstance(b, dict))
    return sys or ""


def _classify_role(obj):
    """Infer the agent role from the system-prompt signature."""
    s = _sys_text(obj)
    if "software architect and planning" in s:
        return "Plan"
    if "verification specialist" in s:
        return "verification"
    if "agent for Claude Code" in s or "Searching for code" in s:
        return "general-purpose"
    if "Claude Code" in s:
        return "parent"
    return "unknown"


def _short_model(m):
    if not m:
        return "nomodel"
    return (m or "").replace("claude-", "").replace("[1m]", "").replace(".", "-")[:24]


# --- prefix-warmth ledger (SQLite-backed, TWO-STATE) ---------------------------
# Records, per cached message-prefix, WHEN it was last stamped and at what TTL,
# so a separate consumer (statusline / hook / pinger / compact-strip) can answer
# "is this conversation's prefix still warm?" — something a per-session JSONL
# can't know, because warmth lives on the CONTENT-ADDRESSED prefix the backend
# caches, which a forked keep-alive ping shares but never writes back to the
# original session's transcript. So the proxy LEARNS it from response receipts
# and stores it here.
#
# STORE: one shared SQLite file (WARMTH_DB, default <proxy dir>/warmth.sqlite),
# WAL mode — durable across proxy restarts and SHARED by every proxy port on the
# box (one ledger, not eight blind ones). Why SQLite over Redis (2026-06-09):
# stdlib + no daemon to babysit; durable per-commit by default (Redis RDB/AOF
# needs deliberate config to not re-create restart-amnesia in miniature); and no
# "store unreachable" runtime state to mishandle now that ABSENCE TRIGGERS
# ACTION. Credentials never land here — only anonymous prefix hashes +
# timestamps; _LAST_REQUEST (bodies + auth headers) stays in-process.
#
# TWO-STATE SEMANTICS (2026-06-09 decision; replaces warm/cold/unknown): the
# expiry predicate IS the answer. warm = row exists AND expires_at > now;
# everything else is not-warm. Because the store is durable and stamps every
# response-confirmed cache event, absence ≈ expiry, so the compact-strip gate
# may act on absence without the old third 'unknown' state. The gates:
#   * ping  IFF warm   (anything else declines — never higher cost)
#   * strip IFF NOT warm ('cold'/'absent'); store 'error' or ledger 'off'
#     decline (can't judge -> take that gate's no-action path)
# warmth_state() still reports 'cold' (lapsed row not yet purged) vs 'absent'
# (no row) for OBSERVABILITY only — no gate distinguishes them.
#
# EXPIRY IS THE GC: correctness lives in the read predicate (expires_at > now),
# never in row deletion. The background sweep only reclaims disk space, with a
# generous slack, and may run late or never without changing any gate decision.
# (This deletes the old semantic sweeper, whose eager reaping at bare ttl erased
# the very cold-evidence the three-state compact gate needed — the bug that
# motivated the two-state redesign.)
WARMTH_LEDGER = os.environ.get("WARMTH_LEDGER", "1") not in ("0", "no", "off", "false")
WARMTH_LOG_FILE = os.environ.get("WARMTH_LOG_FILE", "1") not in ("0", "no", "off", "false")
WARMTH_PING_SENTINEL = os.environ.get("WARMTH_PING_SENTINEL")  # tail-msg marker => keep-warm ping
# A keep-warm ping exists to REFRESH a still-warm prefix before it lapses. On any
# NOT-warm prefix (lapsed, never seen, store error), forwarding the ping would
# cold-WRITE the discarded prefix at the write premium — the precise event the
# ping was meant to forestall, with nothing recovered. So forward IFF warm;
# everything else short-circuits with a synthetic end_turn (0 tokens).
WARMTH_BLOCK_COLD_PING = os.environ.get("WARMTH_BLOCK_COLD_PING") in (
    "1", "yes", "on", "true")
WARMTH_DB = os.environ.get("WARMTH_DB",
                           str(Path(__file__).resolve().parent / "warmth.sqlite"))
_DB = None
_DB_LOCK = threading.Lock()


def _warmth_db():
    """Lazily open (and initialize) the shared warmth store. One connection per
    process, serialized by _DB_LOCK (write volume is a few rows/sec at peak);
    WAL + busy_timeout make the file safely shareable across proxy processes."""
    global _DB
    with _DB_LOCK:
        if _DB is None:
            con = sqlite3.connect(WARMTH_DB, check_same_thread=False, timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("CREATE TABLE IF NOT EXISTS warmth ("
                        "hash TEXT PRIMARY KEY, stamped_at REAL NOT NULL, "
                        "ttl INTEGER NOT NULL, expires_at REAL NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS session_head ("
                        "session_id TEXT PRIMARY KEY, hash TEXT NOT NULL, "
                        "updated_at REAL NOT NULL)")
            con.commit()
            _DB = con
    return _DB


def _warmth_rows(hashes):
    """{hash: (stamped_at, ttl, expires_at)} for the given hashes (one query).
    Raises on store failure — each caller maps that to its gate's safe default."""
    hashes = [h for h in hashes if h]
    if not hashes:
        return {}
    con = _warmth_db()
    with _DB_LOCK:
        q = ",".join("?" * len(hashes))
        cur = con.execute("SELECT hash, stamped_at, ttl, expires_at FROM warmth "
                          f"WHERE hash IN ({q})", hashes)
        return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def _session_head_hash(session):
    con = _warmth_db()
    with _DB_LOCK:
        r = con.execute("SELECT hash FROM session_head WHERE session_id=?",
                        (session,)).fetchone()
    return r[0] if r else None

# --- PINGER: keep a prefix warm by REPLAYING a session's last request ---------
# The old keep-warm path made a caller reconstruct an entire `--resume
# --fork-session` payload (tools, cwd, system, history) just to smuggle a
# sentinel turn past the proxy. But the proxy ALREADY sees the exact, fully
# transformed last request of every session — the precise bytes the backend
# content-addressed. So the dance collapses to: cache that last request in
# memory, and let `POST /_ping?session=<id>` replay it with thinking off and
# `max_tokens:1`. Identical cacheable prefix => a cache READ that slides the TTL,
# for ~1 output token. The caller only needs the session_id.
#
# The cache holds auth/version headers too (so the replay matches the original's
# beta namespace + credentials) — IN MEMORY ONLY, never written to disk.
WARMTH_PINGER = os.environ.get("WARMTH_PINGER", "1") not in ("0", "no", "off", "false")
_LAST_REQUEST_MAX = int(os.environ.get("WARMTH_PINGER_MAX", "2000"))
_LAST_REQUEST = {}                 # session_id -> {"obj","headers","path","ts"}
_LAST_REQUEST_LOCK = threading.Lock()


def _cache_last_request(session_id, obj, fwd_headers, upstream_path):
    """Stash the just-forwarded (post-transform) messages request so a later
    /_ping can replay it. obj is not reused after this turn, so we keep the ref;
    headers are kept whole (incl. auth + anthropic-beta) so the replay rides the
    same cache namespace — in-memory only, evicted oldest-first past the cap."""
    if not (WARMTH_PINGER and session_id and isinstance(obj, dict)):
        return
    with _LAST_REQUEST_LOCK:
        _LAST_REQUEST[session_id] = {"obj": obj, "headers": dict(fwd_headers),
                                     "path": upstream_path, "ts": time.time()}
        if len(_LAST_REQUEST) > _LAST_REQUEST_MAX:
            oldest = min(_LAST_REQUEST.items(), key=lambda kv: kv[1]["ts"])[0]
            _LAST_REQUEST.pop(oldest, None)


def _strip_cache_control(node):
    """Deep copy of a message/content node with every `cache_control` removed, so
    an unchanged message hashes IDENTICALLY turn-over-turn (the rolling marker
    hops onto the new last message each turn — if we hashed it the 'same' history
    would change and a returning session would never match)."""
    if isinstance(node, dict):
        return {k: _strip_cache_control(v) for k, v in node.items()
                if k != "cache_control"}
    if isinstance(node, list):
        return [_strip_cache_control(v) for v in node]
    return node


def _canon_message(m):
    return json.dumps(_strip_cache_control(m), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8", "replace")


def _stable_sys_text(obj):
    """System text for the warmth fingerprint, EXCLUDING the volatile per-turn
    attribution block (`x-anthropic-billing-header: ... cch=N ...`) — it changes
    every turn but is out-of-band and does NOT participate in the prompt cache, so
    folding it in would make every turn's fingerprint differ (a guaranteed miss)."""
    sys = obj.get("system")
    if isinstance(sys, list):
        return " ".join(b.get("text", "") for b in sys if isinstance(b, dict)
                        and not b.get("text", "").startswith("x-anthropic-billing-header"))
    return sys or ""


def _sys_tools_fingerprint(obj):
    """A constant lead-in standing in for the tools+system prefix. Folding it in
    means a silent model / tool-set / system-prompt change invalidates the key
    (reads cold) instead of colliding with a different real cache entry."""
    tools = obj.get("tools") or []
    parts = [obj.get("model") or "",
             ",".join(sorted(t.get("name", "") for t in tools if isinstance(t, dict))),
             _stable_sys_text(obj)]
    return ("\x1f".join(parts)).encode("utf-8", "replace")


def _prefix_hash(obj, upto):
    """Chain-hash of the cacheable prefix: tools/system fingerprint + messages
    [0:upto], each canonicalized without cache_control. Simple full recompute
    (fast — blake2b over a few hundred KB is sub-ms); runs on the writer thread."""
    h = hashlib.blake2b(digest_size=20)
    h.update(_sys_tools_fingerprint(obj))
    for m in (obj.get("messages") or [])[:upto]:
        h.update(b"\x1e")
        h.update(_canon_message(m))
    return h.hexdigest()


def _marker_ttl(obj):
    """TTL (seconds) of the message-tail cache breakpoint: 3600 for ttl:'1h',
    else 300 (bare ephemeral). Falls back to the system markers."""
    for m in reversed(obj.get("messages") or []):
        c = m.get("content")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("cache_control"):
                    return 3600 if blk["cache_control"].get("ttl") == "1h" else 300
    sys = obj.get("system")
    if isinstance(sys, list):
        for b in sys:
            if isinstance(b, dict) and b.get("cache_control"):
                return 3600 if b["cache_control"].get("ttl") == "1h" else 300
    return 300


def _is_warm_ping(obj):
    """A recognized keep-warm ping: the LAST user message carries the sentinel.
    Such a turn refreshes the shared prefix but its own tail is throwaway, so we
    hash UP TO (not including) it."""
    if not WARMTH_PING_SENTINEL:
        return False
    for m in reversed(obj.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(
            b.get("text", "") for b in c if isinstance(b, dict)) if isinstance(c, list) else ""
        return WARMTH_PING_SENTINEL in text
    return False


def warmth_warm(hash_hex):
    """Read side (for a statusline/hook/keep-warm decision): is this prefix still
    warm? Anything other than 'warm' is not-warm."""
    return warmth_state(hash_hex) == "warm"


def warmth_state(hash_hex):
    """TWO-STATE for decisions, four labels for logs: 'warm' (row exists,
    expires_at > now) vs not-warm, where not-warm is reported as 'cold' (lapsed
    row still on disk awaiting purge), 'absent' (no row), 'off' (ledger
    disabled), or 'error' (store failure). GATES test == 'warm' only; the
    compact-strip gate additionally requires 'cold'/'absent' to act (so
    'off'/'error' decline — absence is evidence, a broken store is not)."""
    if not WARMTH_LEDGER:
        return "off"
    if not hash_hex:
        return "absent"
    try:
        r = _warmth_rows([hash_hex]).get(hash_hex)
    except Exception:
        return "error"
    if not r:
        return "absent"
    return "warm" if r[2] > time.time() else "cold"


def warmth_query(hash_hex=None, session=None):
    """Resolve warmth for the GET /_warm endpoint. By hash (content-addressed,
    fork-proof) or by session_id (convenience: resolves to that session's latest
    head hash, which a fork's keep-warm ping refreshes under the hood). Head
    index is in the store too, so this survives a proxy restart."""
    try:
        h = hash_hex or (_session_head_hash(session) if session else None)
        if not h:
            return {"found": False, "warm": False, "session": session, "hash": hash_hex}
        r = _warmth_rows([h]).get(h)
    except Exception as e:
        return {"found": False, "warm": False, "session": session,
                "hash": hash_hex, "error": f"store: {e}"}
    if not r:
        return {"found": False, "warm": False, "session": session, "hash": h}
    ts, ttl, exp = r
    now = time.time()
    return {"found": True, "warm": now < exp, "session": session, "hash": h,
            "age_s": round(now - ts, 1), "ttl_s": ttl,
            "remaining_s": round(max(0.0, exp - now), 1)}


def _cold_ping_decision(obj):
    """If this request is a keep-warm ping whose target prefix is NOT warm, return
    a decline record (caller short-circuits, never forwards). A ping only ever pays
    off on a WARM prefix (a cheap read that slides the TTL); on anything else
    (cold, absent, store error), forwarding is a cache WRITE at the premium for
    no gain — the higher cost the pinger exists to avoid. So forward IFF warm. Hash
    on the SAME basis `_record_warmth` uses for a ping (history up to, not
    including, the throwaway sentinel tail)."""
    if not WARMTH_BLOCK_COLD_PING or not _is_warm_ping(obj):
        return None
    msgs = obj.get("messages") or []
    upto = len(msgs) - 1                  # same as _record_warmth's ping path
    if upto <= 0:
        return None
    h = _prefix_hash(obj, upto)
    state = warmth_state(h)
    if state == "warm":
        return None                       # only a warm prefix is worth pinging
    return {"ping": True, "blocked": True, "warmth_state": state, "hash": h,
            "n_messages_hashed": upto,
            "note": f"declined ping: prefix is '{state}', not warm; forwarding "
                    "would write the prefix at the premium for no gain"}


def _record_warmth(obj, usage):
    """Refresh the ledger for the prefix this response just (re)cached, and return
    a small log record. Regular turn -> hash includes the last message (the entry
    the backend cached); ping -> excludes its throwaway tail.

    The stamp is RESPONSE-CONFIRMED: a row exists ONLY because the backend told us
    a cache does. We stamp iff usage confirms caching actually happened this turn
    (`cache_creation > 0` = just written, or `cache_read > 0` = read & TTL slid).
    A response with both zero (e.g. a sub-min-cacheable prefix the backend declined
    to cache) is NOT stamped — marking it 'warm' would be a lie, and a later ping
    would write rather than read. The request is mere intent; the response is the
    receipt. (This receipt discipline is what makes the two-state 'absence ≈
    expiry' reading honest.)"""
    if not WARMTH_LEDGER:
        return None
    msgs = obj.get("messages") or []
    if not msgs:
        return None
    created = (usage or {}).get("cache_creation_input_tokens") or 0
    read = (usage or {}).get("cache_read_input_tokens") or 0
    if created <= 0 and read <= 0:
        return None                       # no cache confirmed -> nothing to stamp
    ping = _is_warm_ping(obj)
    upto = len(msgs) - 1 if ping else len(msgs)
    if upto <= 0:
        return None
    h = _prefix_hash(obj, upto)
    ttl = _marker_ttl(obj)
    now = time.time()
    try:
        sid = (_session_ids(obj) or (None,))[0]
    except Exception:
        sid = None
    try:
        con = _warmth_db()
        with _DB_LOCK:
            con.execute("INSERT INTO warmth(hash, stamped_at, ttl, expires_at) "
                        "VALUES(?,?,?,?) ON CONFLICT(hash) DO UPDATE SET "
                        "stamped_at=excluded.stamped_at, ttl=excluded.ttl, "
                        "expires_at=excluded.expires_at", (h, now, ttl, now + ttl))
            # a real turn advances this session's head; a fork's ping only
            # refreshes the shared hash above (its fork-id head is irrelevant).
            if sid and not ping:
                con.execute("INSERT INTO session_head(session_id, hash, updated_at) "
                            "VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                            "hash=excluded.hash, updated_at=excluded.updated_at",
                            (sid, h, now))
            con.commit()
            size = con.execute("SELECT COUNT(*) FROM warmth").fetchone()[0]
    except Exception as e:
        # A failed stamp must be LOUD: it silently degrades a warm prefix to
        # 'absent', which the compact gate now acts on.
        print(f"[warmth] STORE WRITE FAILED {h[:12]}…: {e}", flush=True)
        return None
    return {"hash": h, "ttl": ttl, "ts": round(now, 3), "ping": ping,
            "n_messages_hashed": upto, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": created,
            "warm_on_arrival": read > 0, "ledger_size": size}


async def _warm_session(session_id, force=False):
    """Replay a session's cached last request as a minimal keep-warm ping. Returns
    (http_status, json_result). Identical cacheable prefix => the backend serves a
    cache READ and slides the TTL; thinking off + max_tokens:1 keeps output to one
    token. Pings IFF the prefix is warm — skips anything else (a non-warm replay
    would be a cold-write at the write premium) unless force=1."""
    if not WARMTH_PINGER:
        return 404, {"ok": False, "reason": "pinger disabled (WARMTH_PINGER=0)"}
    with _LAST_REQUEST_LOCK:
        entry = _LAST_REQUEST.get(session_id)
        entry = dict(entry) if entry else None
    if not entry:
        return 404, {"ok": False, "session": session_id,
                     "reason": "no cached request for this session yet "
                               "(it must have made >=1 messages call through "
                               "this proxy since start)"}
    src = entry["obj"]
    msgs = src.get("messages") or []
    if not msgs:
        return 400, {"ok": False, "session": session_id,
                     "reason": "cached request has no messages"}
    # A ping is ONLY ever a win on a WARM prefix: a 0.10x cache READ that slides
    # the TTL, buying a future write. On anything else — cold, absent, store
    # error — replaying is a cache WRITE at the premium "for the sake of the
    # ping": exactly the higher cost the pinger exists to avoid. So ping IFF
    # warm; everything else declines. force=1 is the only override (deliberately
    # (re)establish a cache). Goal: never higher cost.
    h_full = _prefix_hash(src, len(msgs))
    prior = warmth_state(h_full)
    if prior != "warm" and not force:
        return 200, {"ok": True, "warmed": False, "skipped": prior,
                     "session": session_id, "hash": h_full, "prior_warmth": prior,
                     "note": f"prefix is '{prior}', not warm; a ping only refreshes "
                             "a warm cache — replaying would be a cold-write at the "
                             "write premium. Declined (force=1 to establish it)."}
    # Minimal warming variant: identical cacheable prefix (tools/system/messages
    # untouched -> same content hash), one output token, non-streaming. We turn
    # thinking OFF so max_tokens can be 1 (an enabled thinking budget forces
    # max_tokens > budget => real output cost); but a `context_management`
    # thinking-clearing strategy (e.g. clear_thinking_*) then 400s "requires
    # thinking to be enabled", so drop it too. Neither field is part of the cached
    # prefix, so the cache READ is preserved. `tools` MUST stay (it's IN the prefix).
    warm = dict(src)
    warm.pop("thinking", None)
    warm.pop("context_management", None)
    warm["max_tokens"] = 1
    warm["stream"] = False
    body = json.dumps(warm, ensure_ascii=False).encode("utf-8")
    headers = {k: v for k, v in entry["headers"].items()
               if k.lower() != "content-length"}
    headers["content-type"] = "application/json"
    headers["accept-encoding"] = "identity"
    try:
        r = await _client.post(UPSTREAM + entry["path"], headers=headers,
                               content=body)
    except Exception as e:
        return 502, {"ok": False, "session": session_id,
                     "reason": f"upstream error: {e}"}
    try:
        data = r.json()
    except Exception:
        data = {}
    u = data.get("usage") or {}
    usage = {"input_tokens": u.get("input_tokens"),
             "output_tokens": u.get("output_tokens"),
             "cache_read_input_tokens": u.get("cache_read_input_tokens"),
             "cache_creation_input_tokens": u.get("cache_creation_input_tokens")}
    ok = r.status_code == 200
    res = {"ok": ok, "warmed": ok, "session": session_id,
           "status_code": r.status_code, "prior_warmth": prior, "hash": h_full,
           "usage": usage, "request_id": r.headers.get("request-id")}
    if ok:
        rec = _record_warmth(warm, usage)   # refresh the ledger off this replay
        if rec:
            res["ttl_s"] = rec["ttl"]
            res["remaining_s"] = float(rec["ttl"])   # just stamped: full ttl left
        res["cache_read_input_tokens"] = usage.get("cache_read_input_tokens")
        res["cache_hit"] = bool((usage.get("cache_read_input_tokens") or 0) > 0)
    else:
        res["error"] = data or r.text[:500]
    return (200 if ok else r.status_code), res


# --- session teardown + housekeeping sweep ------------------------------------
# Two complementary ways to stop persisting a finished session's cached state:
#   (1) EXPLICIT signal — `GET/POST /_end?session=<id>[&reason=clear]`, driven by
#       the CLI's SessionEnd hook (reason=clear / logout / exit / other). Precise,
#       but unreliable: a crash / `kill -9` / sleep never fires it.
#   (2) HOUSEKEEPING sweep — with the SQLite ledger, EXPIRY IS ENFORCED BY THE
#       READ PREDICATE, so this thread is hygiene only: drop in-memory cached
#       last-requests whose prefix lapsed past the grace (memory + credential
#       lifetime), purge long-expired warmth rows (disk space), prune stale
#       session heads. It may run late or never without changing ANY gate
#       decision — unlike the old in-memory sweeper, whose deletions were
#       semantic and erased cold evidence at bare ttl.
WARMTH_SWEEP_INTERVAL = int(os.environ.get("WARMTH_SWEEP_INTERVAL", "300"))
_LAST_REQUEST_GRACE = int(os.environ.get("WARMTH_LAST_REQUEST_GRACE", "600"))
# How long an EXPIRED row stays on disk before the purge removes it. Pure
# observability slack (lets logs/tests still see 'cold' vs 'absent'); decisions
# never depend on it.
_WARMTH_PURGE_SLACK = int(os.environ.get("WARMTH_PURGE_SLACK", str(7 * 86400)))


def _end_session(session_id, reason="unspecified"):
    """Forget a finished session's replayable last request and its head index.
    Idempotent. We leave the (anonymous, ttl-bounded, possibly fork-shared) warmth
    row to expire on its own rather than risk blinding a concurrent sibling."""
    with _LAST_REQUEST_LOCK:
        dropped_lr = _LAST_REQUEST.pop(session_id, None) is not None
    dropped_head = False
    try:
        con = _warmth_db()
        with _DB_LOCK:
            cur = con.execute("DELETE FROM session_head WHERE session_id=?",
                              (session_id,))
            con.commit()
            dropped_head = cur.rowcount > 0
    except Exception:
        pass
    return {"ok": True, "session": session_id, "reason": reason,
            "dropped": {"last_request": dropped_lr, "session_head": dropped_head},
            "remaining_sessions": len(_LAST_REQUEST)}


def _prefix_age_ttl(entry, now):
    """(seconds since the prefix was last cached, ttl) for a cached request,
    consulting the warmth store — which a /_ping REFRESHES — so an actively
    kept-warm session is judged by its last ping, not its original turn. Falls
    back to the entry's own timestamp + 1h when the store has no record."""
    obj = entry["obj"]
    msgs = obj.get("messages") or []
    if msgs:
        try:
            h = _prefix_hash(obj, len(msgs))
            r = _warmth_rows([h]).get(h)
            if r:
                return now - r[0], r[1]
        except Exception:
            pass
    return now - entry["ts"], 3600


def _sweep_state(now=None):
    """Housekeeping only (see section comment): correctness lives in the read
    predicate, never in these deletions. Lock order LAST_REQUEST -> DB."""
    now = now or time.time()
    with _LAST_REQUEST_LOCK:
        stale = [sid for sid, e in _LAST_REQUEST.items()
                 if (lambda a, t: a > t + _LAST_REQUEST_GRACE)(*_prefix_age_ttl(e, now))]
        for sid in stale:
            _LAST_REQUEST.pop(sid, None)
    purged = heads = 0
    try:
        con = _warmth_db()
        with _DB_LOCK:
            purged = con.execute("DELETE FROM warmth WHERE expires_at < ?",
                                 (now - _WARMTH_PURGE_SLACK,)).rowcount
            heads = con.execute("DELETE FROM session_head WHERE updated_at < ?",
                                (now - _WARMTH_PURGE_SLACK,)).rowcount
            con.commit()
    except Exception:
        pass
    return {"last_request_dropped": len(stale), "warmth_purged": purged,
            "session_heads_dropped": heads,
            "last_request_size": len(_LAST_REQUEST)}


def _sweeper_loop():
    while True:
        time.sleep(max(30, WARMTH_SWEEP_INTERVAL))
        try:
            res = _sweep_state()
            if res["last_request_dropped"] or res["warmth_purged"] or res["session_heads_dropped"]:
                print(f"[sweep] dropped lr={res['last_request_dropped']} "
                      f"purged={res['warmth_purged']} heads={res['session_heads_dropped']} "
                      f"(lr={res['last_request_size']})", flush=True)
        except Exception:
            pass


if WARMTH_PINGER or WARMTH_LEDGER:
    threading.Thread(target=_sweeper_loop, name="warmthsweeper", daemon=True).start()


def _parse_usage_from_sse(raw_bytes):
    """Pull usage out of the captured SSE stream (message_start + message_delta)."""
    usage = {"input_tokens": None, "output_tokens": None,
             "cache_creation_input_tokens": None, "cache_read_input_tokens": None,
             "stop_reason": None}
    try:
        text = raw_bytes.decode("utf-8", "replace")
    except Exception:
        return usage
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        t = ev.get("type")
        if t == "message_start":
            u = (ev.get("message") or {}).get("usage") or {}
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                if u.get(k) is not None:
                    usage[k] = u[k]
            if u.get("output_tokens") is not None:
                usage["output_tokens"] = u["output_tokens"]
        elif t == "message_delta":
            u = ev.get("usage") or {}
            if u.get("output_tokens") is not None:
                usage["output_tokens"] = u["output_tokens"]
            d = ev.get("delta") or {}
            if d.get("stop_reason"):
                usage["stop_reason"] = d["stop_reason"]
    return usage


def _parse_response_meta(raw_bytes):
    """Capture the FULL metadata Anthropic returns, beyond the flat token counts.

    The flat `_parse_usage_from_sse` collapses cache tiers and drops everything
    else. This keeps the raw usage objects (cache_creation 5m/1h split,
    service_tier, inference_geo, output_tokens_details.thinking_tokens,
    iterations[]) plus message id, resolved model, stop details, content shape,
    and any error body. This is the "extra info from the Anthropic servers" the
    response carries that we were previously discarding.
    """
    meta = {"message_id": None, "resolved_model": None, "role": None,
            "stop_reason": None, "stop_sequence": None, "stop_details": None,
            "usage_start": None, "usage_final": None,
            "content_block_types": [], "tool_uses": [], "error": None}
    try:
        text = raw_bytes.decode("utf-8", "replace")
    except Exception:
        return meta
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        t = ev.get("type")
        if t == "message_start":
            m = ev.get("message") or {}
            meta["message_id"] = m.get("id")
            meta["resolved_model"] = m.get("model")
            meta["role"] = m.get("role")
            meta["usage_start"] = m.get("usage")          # full obj (cache TTL split, service_tier, geo)
        elif t == "content_block_start":
            cb = ev.get("content_block") or {}
            meta["content_block_types"].append(cb.get("type"))
            if cb.get("type") == "tool_use":
                meta["tool_uses"].append(cb.get("name"))
        elif t == "message_delta":
            if ev.get("usage") is not None:
                meta["usage_final"] = ev.get("usage")     # full obj (output_tokens_details, iterations[])
            d = ev.get("delta") or {}
            meta["stop_reason"] = d.get("stop_reason") or meta["stop_reason"]
            if d.get("stop_sequence") is not None:
                meta["stop_sequence"] = d.get("stop_sequence")
            meta["stop_details"] = d.get("stop_details") or meta["stop_details"]
        elif t in ("error", "rate_limit_error"):
            meta["error"] = ev.get("error") or ev
    return meta


# --- billing -----------------------------------------------------------------
# Approximate public list prices, USD per 1M tokens. EDIT as rates change.
# Matched by LONGEST model-name prefix (so "claude-opus-4-8" beats the legacy
# bare "claude-opus-4" entry). est_usd is a DERIVED estimate; the authoritative
# billing signal is the token breakdown itself. Write premiums: 5m=1.25x,
# 1h=2x; reads=0.10x of input. Verified against the API reference 2026-06-09.
# NOTE: opus REPRICED at 4.5 — $15/$75 is 4.0/4.1 ONLY; 4.5+ is $5/$25. Until
# this split, all opus-4.5+ captures (logs_opus) were over-priced ~3x.
PRICES = {
    "claude-fable-5":  {"in": 10.0, "out": 50.0, "cache_write_5m": 12.5,  "cache_write_1h": 20.0, "cache_read": 1.00},
    "claude-opus-4-5": {"in": 5.0,  "out": 25.0, "cache_write_5m": 6.25,  "cache_write_1h": 10.0, "cache_read": 0.50},
    "claude-opus-4-6": {"in": 5.0,  "out": 25.0, "cache_write_5m": 6.25,  "cache_write_1h": 10.0, "cache_read": 0.50},
    "claude-opus-4-7": {"in": 5.0,  "out": 25.0, "cache_write_5m": 6.25,  "cache_write_1h": 10.0, "cache_read": 0.50},
    "claude-opus-4-8": {"in": 5.0,  "out": 25.0, "cache_write_5m": 6.25,  "cache_write_1h": 10.0, "cache_read": 0.50},
    # legacy opus 4.0 / 4.1 (also catches their dated full ids)
    "claude-opus-4":   {"in": 15.0, "out": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.50},
    "claude-sonnet-4": {"in": 3.0,  "out": 15.0, "cache_write_5m": 3.75,  "cache_write_1h": 6.0,  "cache_read": 0.30},
    "claude-haiku-4":  {"in": 1.0,  "out": 5.0,  "cache_write_5m": 1.25,  "cache_write_1h": 2.0,  "cache_read": 0.10},
}

def _new_totals():
    return {"requests": 0, "billed_requests": 0, "count_tokens_requests": 0,
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "est_usd": 0.0,
            # PRICING-BLINDNESS guard (open item f): est_usd EXCLUDES these.
            # A nonzero unpriced_requests means the cumulative $ is a floor,
            # not a total — the mission is to price waste, so say so loudly.
            "unpriced_requests": 0, "unpriced_models": []}


_TOTALS = _new_totals()                                   # process-lifetime, all sessions
_SESSION_TOTALS = collections.defaultdict(_new_totals)    # per-session running totals


_UNPRICED_WARNED = set()


def _price_for(model):
    """Longest-prefix match (the old first-dict-hit walk silently shadowed
    "claude-opus-4-8" with the legacy "claude-opus-4" entry). None = unpriced."""
    if not model:
        return None
    best = None
    for pfx, p in PRICES.items():
        if model.startswith(pfx) and (best is None or len(pfx) > len(best[0])):
            best = (pfx, p)
    return best[1] if best else None


def _usd(tokens, rate_per_m):
    return round((tokens or 0) * rate_per_m / 1_000_000, 6)


def _billing(kind, model_resolved=None, usage_final=None, usage_start=None, count_tokens=None):
    """Formatted per-request billing. count_tokens is NOT billed for tokens
    (returns only an input count) — it spends request-rate-limit budget only."""
    if kind == "count_tokens":
        ct = count_tokens or {}
        return {"endpoint": "count_tokens", "billable": False,
                "note": "count_tokens not billed for tokens; consumes request-rate-limit only",
                "counted_input_tokens": ct.get("input_tokens"), "est_usd": 0.0}
    uf = usage_final or {}
    us = usage_start or {}
    cc = uf.get("cache_creation") or us.get("cache_creation") or {}
    tokens = {
        "input_tokens": uf.get("input_tokens", us.get("input_tokens")),
        "output_tokens": uf.get("output_tokens"),
        "cache_read_input_tokens": uf.get("cache_read_input_tokens", us.get("cache_read_input_tokens")),
        "cache_write_5m_tokens": cc.get("ephemeral_5m_input_tokens"),
        "cache_write_1h_tokens": cc.get("ephemeral_1h_input_tokens"),
        # flat total — fallback when the 5m/1h split is absent from the response
        "cache_write_flat_tokens": uf.get("cache_creation_input_tokens",
                                          us.get("cache_creation_input_tokens")),
        "thinking_tokens": (uf.get("output_tokens_details") or {}).get("thinking_tokens"),
        "service_tier": us.get("service_tier"),
    }
    p = _price_for(model_resolved)
    est = None
    unpriced = False
    basis = "approx public list USD/1M; edit PRICES"
    if p:
        w5, w1 = tokens["cache_write_5m_tokens"], tokens["cache_write_1h_tokens"]
        if w5 is None and w1 is None and tokens["cache_write_flat_tokens"]:
            # no TTL split returned: don't silently drop the write cost — price
            # the flat total at the cheaper 5m premium and say so in the basis.
            w5 = tokens["cache_write_flat_tokens"]
            basis += "; cache_creation split absent, flat total priced at 5m rate"
        est = round(_usd(tokens["input_tokens"], p["in"])
                    + _usd(tokens["output_tokens"], p["out"])
                    + _usd(tokens["cache_read_input_tokens"], p["cache_read"])
                    + _usd(w5, p["cache_write_5m"])
                    + _usd(w1, p["cache_write_1h"]), 6)
    elif model_resolved:
        # PRICING BLINDNESS guard: an unmatched model must be LOUD, not a silent
        # None that lets _totals.json keep reporting a confident under-count.
        unpriced = True
        if model_resolved not in _UNPRICED_WARNED:
            _UNPRICED_WARNED.add(model_resolved)
            print(f"[pricing] WARNING: no PRICES entry matches {model_resolved!r} — "
                  "est_usd=None for its traffic; cumulative est_usd is now a FLOOR. "
                  "Tracked in totals.unpriced_requests/unpriced_models; add rates "
                  "to PRICES.", flush=True)
    return {"endpoint": "messages", "billable": True, "model": model_resolved,
            "tokens": tokens, "est_usd": est, "unpriced": unpriced,
            "price_basis": basis}


def _bump(totals, bill):
    totals["requests"] += 1
    if bill.get("endpoint") == "count_tokens":
        totals["count_tokens_requests"] += 1
    else:
        totals["billed_requests"] += 1
        t = bill.get("tokens") or {}
        totals["input_tokens"] += t.get("input_tokens") or 0
        totals["output_tokens"] += t.get("output_tokens") or 0
        totals["cache_read_tokens"] += t.get("cache_read_input_tokens") or 0
        w = (t.get("cache_write_5m_tokens") or 0) + (t.get("cache_write_1h_tokens") or 0)
        totals["cache_write_tokens"] += w or (t.get("cache_write_flat_tokens") or 0)
        totals["est_usd"] = round(totals["est_usd"] + (bill.get("est_usd") or 0), 6)
        if bill.get("unpriced"):
            totals["unpriced_requests"] = totals.get("unpriced_requests", 0) + 1
            m = bill.get("model")
            models = totals.setdefault("unpriced_models", [])
            if m and m not in models:
                models.append(m)


def _accumulate(bill, session_key):
    """Update the global + per-session running totals (the API never returns
    one) and enqueue both snapshots. Math runs on the event loop (cheap dict
    ops); the disk writes are handed to the background writer."""
    _bump(_TOTALS, bill)
    _bump(_SESSION_TOTALS[session_key], bill)
    snap = dict(_TOTALS)
    _enqueue_json(LOG_DIR / "_totals.json", snap)
    _enqueue_json(LOG_DIR / session_key / "_session.json", dict(_SESSION_TOTALS[session_key]))
    return snap


async def handler(request: Request) -> Response:
    # ---- warmth read endpoint (local consumers: statusline / hook / pinger) ---
    # GET /_warm?h=<prefix-hash>  or  /_warm?session=<session_id>
    if request.method == "GET" and request.url.path == "/_warm":
        q = request.query_params
        res = warmth_query(hash_hex=q.get("h"), session=q.get("session"))
        return Response(json.dumps(res), media_type="application/json")

    # ---- keep-warm pinger: replay a session's cached last request -------------
    # POST/GET /_ping?session=<id>[&force=1] — intercepted, never forwarded as a
    # normal turn. Locates the session's cached last request and replays it as a
    # thinking-off, max_tokens:1 cache-read to slide the TTL. (force=1 re-warms a
    # provably-cold prefix instead of declining.)
    if request.url.path == "/_ping":
        q = request.query_params
        sess = q.get("session")
        if not sess:
            return Response(json.dumps({"ok": False, "reason": "missing ?session="}),
                            status_code=400, media_type="application/json")
        force = q.get("force") in ("1", "yes", "on", "true")
        code, res = await _warm_session(sess, force=force)
        print(f"[ping] session={sess[:12]}… -> {res.get('warmed') and 'WARMED' or res.get('skipped') or 'FAIL'} "
              f"prior={res.get('prior_warmth')} read={res.get('cache_read_input_tokens')} "
              f"remaining={res.get('remaining_s')}", flush=True)
        return Response(json.dumps(res), status_code=code,
                        media_type="application/json")

    # ---- session teardown: stop caching a finished session --------------------
    # GET/POST /_end?session=<id>[&reason=clear] — wire to the CLI's SessionEnd
    # hook so a /clear or exit forgets the session's cached request immediately;
    # the background sweeper is the backstop for crashes/kills the hook misses.
    if request.url.path == "/_end":
        sess = request.query_params.get("session")
        if not sess:
            return Response(json.dumps({"ok": False, "reason": "missing ?session="}),
                            status_code=400, media_type="application/json")
        res = _end_session(sess, reason=request.query_params.get("reason", "unspecified"))
        print(f"[end] session={sess[:12]}… reason={res['reason']} "
              f"dropped={res['dropped']} remaining={res['remaining_sessions']}",
              flush=True)
        return Response(json.dumps(res), media_type="application/json")

    n = next(_counter)
    raw = await request.body()
    ts = time.strftime("%H%M%S")

    # ---- route: strip /agent/<name>/anthropic prefix, capture agent name ----
    path = request.url.path
    m = _ROUTE.match(path)
    if m:
        agent = m.group("name")
        upstream_path = m.group("rest") or "/"
    else:
        agent = "ext"
        upstream_path = path
    if request.url.query:
        upstream_path += "?" + request.url.query

    # ---- parse + summarize the request body ----
    role, model = "unknown", None
    session_id = None
    obj = None      # stays None on an unparseable body -> every transform/gate is
                    # skipped and the ORIGINAL bytes forward verbatim (fail-open:
                    # a parse failure must degrade to passthrough, never to a 500)
    client = request.client
    record = {"seq": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "agent": agent, "method": request.method, "path": upstream_path,
              # inbound transport identity — the "who" behind no-session calls
              "client": {"host": client.host, "port": client.port} if client else None,
              "request_headers": _safe_headers(request.headers)}
    try:
        obj = json.loads(raw) if raw else {}
        record["body"] = obj
        # VERSION-DRIFT CANARY (read-only): fingerprint the ORIGINAL CLI request
        # shape BEFORE any of our transforms, so we detect CLI/wire changes (incl.
        # a new 4th cache_control marker), not our own mutations.
        if obj and upstream_path.split("?")[0].endswith("/v1/messages"):
            cres = _canary_check(obj, request.headers, n)
            if cres is not None:
                record["canary"] = cres
        # EXPERIMENTAL piggyback: mutate the outbound payload, forward modified bytes
        if obj:
            changed = False
            appended, reason = _decide_injection(obj)
            if appended:
                orig = _inject_into_last_user(obj, appended, INJECT_SEP)
                if orig is not None:
                    record["injection"] = {"appended": appended, "reason": reason,
                                           "marker": INJECT_MARKER,
                                           "original_last_user": orig,
                                           "final_last_user": _last_user_text(obj)}
                    changed = True
            # Invisible standing protocol instruction (UX): append to the user's
            # prompt on genuine prompt turns only (skip tool_result continuations).
            if SHORTCIRCUIT_INSTRUCT and _last_user_text(obj):
                orig2 = _inject_into_last_user(obj, SHORTCIRCUIT_INSTRUCT, "\n\n")
                if orig2 is not None:
                    record["injection_shortcircuit"] = {"appended": SHORTCIRCUIT_INSTRUCT}
                    changed = True
            # Best placement: patch the terminal tools' own descriptions in tools[]
            patched = _patch_tool_descriptions(obj)
            if patched:
                record["toolpatch"] = {"tools": patched}
                changed = True
            # System-prompt delivery (stable-position, cache-riding, invisible).
            if _patch_system(obj):
                record["syspatch"] = True
                changed = True
            # Proxy-side `rest` split: relocate static prose to an env-independent
            # cache prefix (byte-identical model-visible text; cache boundary only).
            sp = _split_system_rest(obj)
            if sp:
                record["rest_split"] = sp
                changed = True
            # Design-2: relocate env+date to a tail block, mark CLAUDE.md (model-visible).
            rel = _relocate_env_to_tail(obj)
            if rel:
                record["env_relocate"] = rel
                changed = True
            # System-section strip: drop configured `# Heading` sections (model-visible).
            strp = _strip_system_sections(obj)
            if strp:
                record["system_strip"] = strp
                changed = True
            # Tool sort: alphabetize tools[] for a byte-stable first cache segment.
            srt = _sort_tools(obj)
            if srt:
                record["tool_sort"] = srt
                changed = True
            # Strip the discarded history cache_control on a (busted) compact req.
            scc = _strip_compact_cache(obj)
            if scc:
                record["strip_compact_cache"] = scc
                if scc.get("removed_message_markers"):
                    changed = True
            if changed:
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        role = _classify_role(obj)
        model = obj.get("model")
        session_id, account_uuid, device_id = _session_ids(obj)
        sysf = obj.get("system")
        sys_chars = len(_sys_text(obj))
        msgs = obj.get("messages", []) or []
        msg_chars = len(json.dumps(msgs))
        record["summary"] = {
            "model": model,
            "session_id": session_id,
            "account_uuid": account_uuid,
            "device_id": device_id,
            "role": role,
            "system_chars": sys_chars,
            "system_blocks": len(sysf) if isinstance(sysf, list) else (1 if sysf else 0),
            "n_messages": len(msgs),
            "messages_chars": msg_chars,
            "n_tools": len(obj.get("tools", []) or []),
            "tool_names": [t.get("name") for t in (obj.get("tools") or []) if isinstance(t, dict)],
        }
    except Exception as e:
        record["parse_error"] = str(e)
        record["body_raw"] = raw.decode("utf-8", "replace")

    # one subdirectory per session; count_tokens/probes (no metadata) -> NO_SESSION
    session_key = session_id or NO_SESSION
    out_dir = LOG_DIR / session_key
    stem = f"{n:03d}-{agent}-{role}-{_short_model(model)}-{ts}"
    _enqueue_json(out_dir / f"{stem}.request.json", record)

    # ---- WARMTH: decline a provably-COLD keep-warm ping ----------------------
    # A keep-warm ping for a prefix the ledger knows is already busted has lost its
    # meaning: forwarding would only cold-WRITE the discarded prefix at the write
    # premium. Synthesize an end_turn here and skip upstream entirely (0 tokens).
    if isinstance(obj, dict) and upstream_path.split("?")[0].endswith("/v1/messages"):
        cp = _cold_ping_decision(obj)
        if cp:
            msg_id = f"msg_coldping_{n:06d}"
            ack = "Keep-warm ping declined: cache already expired."
            blob = _synth_end_turn_sse(model, ack, msg_id)
            _enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
            _enqueue_json(out_dir / f"{stem}.response.json",
                {"seq": n, "agent": agent, "role": role, "model": model,
                 "session_id": session_id, "endpoint": "messages",
                 "status_code": 200, "billing": None, "usage": {}, "meta": {},
                 "cold_ping_block": {**cp, "upstream_called": False,
                                     "synthetic_message_id": msg_id,
                                     "ack": ack}})
            print(f"[warmth] #{n} {agent}/{role} declined COLD keep-warm ping "
                  f"({cp['hash'][:12]}…); upstream skipped, 0 tokens", flush=True)
            return StreamingResponse(iter([blob]), status_code=200,
                                     media_type="text/event-stream")

    # ---- SHORTCIRCUIT (experimental): answer the wrap-up turn locally --------
    # If this request is the tool_result continuation of a model-declared
    # terminal edit, synthesize "Done." here and SKIP the upstream call entirely:
    # the ~one-turn context carriage is never shipped, never billed. The file was
    # already modified by the CLI before it sent this request, so nothing about
    # the edit is lost — only the redundant round trip to hear the model stop.
    sc = None
    if isinstance(obj, dict) and upstream_path.split("?")[0].endswith("/v1/messages"):
        # RELAY (model's own prose, matched by tool_use_id) takes precedence; in
        # relay mode the sentinel is stripped from history so the canned path
        # won't fire. Without relay, fall back to the history-sentinel decision.
        sc = _shortcircuit_relay_decision(obj) or _shortcircuit_decision(obj)
    if sc:
        msg_id = f"msg_shortcircuit_{n:06d}"
        blob = _synth_end_turn_sse(model, sc["ack"], msg_id)
        _enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
        _enqueue_json(out_dir / f"{stem}.response.json",
            {"seq": n, "agent": agent, "role": role, "model": model,
             "session_id": session_id, "endpoint": "messages",
             "status_code": 200, "billing": None, "usage": {}, "meta": {},
             "shortcircuit": {**sc, "upstream_called": False,
                              "synthetic_message_id": msg_id,
                              "note": "wrap-up turn answered locally; upstream "
                                      "NOT called; 0 tokens billed"}})
        _tools = sc.get("tools") or sc.get("tool") or sc.get("tool_use_ids") or []
        print(f"[shortcircuit] #{n} {agent}/{role} elided wrap-up after "
              f"{','.join(_tools) if isinstance(_tools, list) else _tools} -> "
              f"{sc['ack']!r} (upstream skipped, 0 tokens)", flush=True)
        return StreamingResponse(iter([blob]), status_code=200,
                                 media_type="text/event-stream")

    # ---- forward upstream; tee the response stream to a .sse file ----
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    fwd_headers["accept-encoding"] = "identity"  # force uncompressed so we can read the SSE
    # Stash this (post-transform) request so POST /_ping?session= can replay it.
    if upstream_path.split("?")[0].endswith("/v1/messages"):
        _cache_last_request(session_id, obj, fwd_headers, upstream_path)
    req = _client.build_request(request.method, UPSTREAM + upstream_path,
                                headers=fwd_headers, content=raw)
    up = await _client.send(req, stream=True)
    resp_headers = {k: v for k, v in up.headers.items()
                    if k.lower() not in {"connection", "transfer-encoding",
                                         "content-length", "keep-alive"}}
    base_path = upstream_path.split("?")[0]
    is_messages = base_path.endswith("/v1/messages")
    is_count = base_path.endswith("/count_tokens")
    capture = is_messages or is_count
    chunks = []

    mutate = is_messages and _resp_mutating()
    relay = is_messages and _relay_active()
    buffer_resp = mutate or relay    # both need the full SSE before we can rewrite

    async def body_iter():
        out_blob = None
        try:
            async for chunk in up.aiter_raw():
                if capture:
                    chunks.append(chunk)
                if not buffer_resp:     # stream verbatim; when buffering we hold
                    yield chunk
            if buffer_resp and chunks:
                full = b"".join(chunks)
                # relay stashes prose + blanks it as a side effect; compute ONCE
                out_blob = _relay_capture_and_strip(full) if relay else _mutate_sse(full)
                yield out_blob          # emit rewritten response once
        finally:
            await up.aclose()
            if capture and chunks:
                blob = b"".join(chunks)
                if is_messages:
                    _enqueue_bytes(out_dir / f"{stem}.response.sse", blob)
                    if mutate:
                        _enqueue_bytes(out_dir / f"{stem}.response.mutated.sse",
                                       _mutate_sse(blob))
                    if relay and out_blob is not None:
                        _enqueue_bytes(out_dir / f"{stem}.response.relayed.sse", out_blob)
                    usage = _parse_usage_from_sse(blob)
                    meta = _parse_response_meta(blob)
                    bill = _billing("messages",
                                    model_resolved=meta.get("resolved_model") or model,
                                    usage_final=meta.get("usage_final"),
                                    usage_start=meta.get("usage_start"))
                    # Refresh the prefix-warmth ledger off-thread (hash the prefix
                    # this response cached + stamp now/ttl). obj is the forwarded
                    # (post-transform) body = exactly what the backend addressed.
                    if WARMTH_LEDGER and isinstance(obj, dict):
                        _enqueue_ledger(
                            (out_dir / f"{stem}.warmth.json") if WARMTH_LOG_FILE else None,
                            obj, usage)
                else:  # count_tokens — plain JSON, not SSE
                    try:
                        ct = json.loads(blob.decode("utf-8", "replace"))
                    except Exception:
                        ct = {"parse_error": blob.decode("utf-8", "replace")[:500]}
                    usage = {}
                    meta = {"count_tokens_result": ct}
                    bill = _billing("count_tokens", model_resolved=model, count_tokens=ct)
                cum = _accumulate(bill, session_key)
                _enqueue_json(out_dir / f"{stem}.response.json",
                    {"seq": n, "agent": agent, "role": role, "model": model,
                     "session_id": session_id,
                     "endpoint": "messages" if is_messages else "count_tokens",
                     "status_code": up.status_code,
                     # full headers Anthropic returned — request-id,
                     # anthropic-ratelimit-*, billing/tier hints, etc.
                     "response_headers": dict(up.headers),
                     "billing": bill,        # formatted per-request billing
                     "cumulative": cum,      # process-lifetime running total
                     "usage": usage,         # flat back-compat view (messages only)
                     "meta": meta,           # full usage objects + ids + shape
                     "response_injection": ({"append": RESP_APPEND,
                                             "replace": RESP_REPLACE}
                                            if mutate else None)})
                if is_messages:
                    t = bill.get("tokens") or {}
                    print(f"[dump] #{n} {agent}/{role} {bill.get('model') or model} "
                          f"-> {up.status_code} in={t.get('input_tokens')} "
                          f"out={t.get('output_tokens')} "
                          f"cache_r={t.get('cache_read_input_tokens')} "
                          f"cw5m={t.get('cache_write_5m_tokens')} cw1h={t.get('cache_write_1h_tokens')} "
                          f"think={t.get('thinking_tokens')} tier={t.get('service_tier')} "
                          f"${bill.get('est_usd')}{' UNPRICED' if bill.get('unpriced') else ''} "
                          f"| cum ${cum.get('est_usd')}"
                          f"{' (+' + str(cum.get('unpriced_requests')) + ' unpriced)' if cum.get('unpriced_requests') else ''} "
                          f"reqid={up.headers.get('request-id')}", flush=True)
                else:
                    print(f"[count] #{n} {agent} -> {up.status_code} "
                          f"counted_in={(meta['count_tokens_result'] or {}).get('input_tokens')} "
                          f"(not billed) | cum reqs={cum.get('requests')} "
                          f"ct_reqs={cum.get('count_tokens_requests')} "
                          f"reqid={up.headers.get('request-id')}", flush=True)

    return StreamingResponse(body_iter(), status_code=up.status_code,
                             headers=resp_headers,
                             media_type=up.headers.get("content-type"))


app = Starlette(routes=[Route("/{path:path}", handler,
                              methods=["GET", "POST", "PUT", "DELETE"])])
