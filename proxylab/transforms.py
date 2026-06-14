import asyncio
import atexit
import collections
import hashlib
import html
import itertools
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from proxylab import warmth as warmth_mod
from proxylab import writer as writer_mod

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


# ---- WIRESCOPE `[wirescope:omit ...]` — strip context sections from msgs[0] -
# Honors `[wirescope:omit claudemd,useremail]` (see WIRESCOPE.md): the proxy
# strips the named `# <Section>` blocks out of the <system-reminder> in the first
# user message before forwarding — the reconstruction of the CLI's internal
# omitClaudeMd, generalized (nothing native removes # userEmail). The directive
# may come from the agent BODY (per-type) or the SPAWN-prompt head (per-call,
# v1); a `keep` verb overrides per target (spawn > body). The strip rides
# system[2]/messages[0] cache-constant, fires every turn deterministically, and
# is idempotent. messages[0] sits AFTER the system/tools cache breakpoint, so the
# expensive prefix is untouched.
# Default ON: the `[wirescope:omit ...]` directive IS the opt-in (an author must
# write it; no directive -> no change), so the directive alone gates the
# behavior. WS_OMIT stays only as a deployment kill-switch (WS_OMIT=0 to refuse
# honoring omit directives entirely).
WS_OMIT = os.environ.get("WS_OMIT", "1") not in ("0", "no", "off", "false")
# Operator-level default OMIT policy (WIRESCOPE.md): a comma list of targets the
# operator wants stripped from EVERY subagent spawn with zero agent/spawner
# knowledge — the universal case (e.g. `WS_OMIT_DEFAULT=useremail` to keep the
# user's email out of every spawned helper). Applied as the LOWEST-precedence
# action layer (operator < body < spawn), so any `[wirescope:keep <t>]` directive
# overrides it. Empty/unset = off (no change). Still under the WS_OMIT master
# gate, and only on subagent turns (the main session is the user's own).
# UNCONDITIONAL-ONLY RULE (policy can be automated, strategy cannot): a target
# belongs here ONLY if no subagent would EVER want it kept — i.e. it's policy,
# not a task-dependent judgment. `useremail` qualifies; `claudemd` does NOT
# (whether a subagent needs project context is per-task = strategy → leave it to
# body/spawn directives). Rule of thumb: "if you'd ever want it kept, it doesn't
# belong in omit_default." (keep-override is the safety valve for rare misses,
# not a license to put strategic targets in the blanket default.)
WS_OMIT_DEFAULT = [t.lower() for t in
                   re.split(r"[,\s]+", os.environ.get("WS_OMIT_DEFAULT", "").strip())
                   if t]                          # liberal: commas and/or whitespace
# Spawner discovery hint (WIRESCOPE.md): the ONE place wirescope puts
# proxy-authored MODEL-VISIBLE text on the wire (everywhere else it strips its
# own directives). A small constant SELF-CONTAINED grammar block (the recipient
# lives in its own cwd and can't open the proxy-side WIRESCOPE.md, so the hint
# carries the usable syntax inline, not a file pointer), injected
# ONLY into a spawner's request — a main/parent line (not cc_is_subagent) that
# actually carries a subagent-spawn tool (Agent/Task) — so an agent that can't
# spawn never sees it and subagents stay pristine. Operator opt-in, default OFF.
WS_SPAWNER_HINT = os.environ.get("WS_SPAWNER_HINT", "") in (
    "1", "yes", "on", "true")
# tools[] names that mean "this agent can spawn subagents" (clodex: Agent;
# vanilla Claude Code: Task) — the hint is pointless without one of these.
_WS_SPAWN_TOOLS = {"Agent", "Task"}
# Self-contained: the agent that receives this lives in its OWN cwd and cannot
# open the proxy-side WIRESCOPE.md, so the hint carries the usable grammar inline
# (not a file pointer). Still one constant block -> re-anchors once, then rides
# the cache. Must start with "[wirescope] " (the idempotency guard keys on it).
# MIXED REGISTER (the proxy holds no per-task intent, so it must not push a
# stripping strategy): *recommend* agent-name (needs no task knowledge, no
# downside), only *surface* omit/keep/replace (strategy — the spawner decides).
_WS_HINT_TEXT = (
    "[wirescope] This agent can spawn subagents through the wirescope proxy, "
    "which can shape what each subagent inherits on the wire. The choice is "
    "yours per spawn — wirescope only carries it onto the wire, it doesn't "
    "decide. Directives go at the head of the spawn's prompt (one per line, "
    "before the task text) and are stripped before forwarding, so the subagent "
    "never sees them.\n"
    "\n"
    "Recommended for every spawn — name the subagent:\n"
    "  [wirescope:agent-name <label>]   improves traceability in logs and "
    "dashboards; costs nothing.\n"
    "\n"
    "Optional, apply per your own strategy — shape inherited context "
    "(targets/names are comma- or space-separated):\n"
    "  [wirescope:omit claudemd,useremail]   drop inherited context sections\n"
    "  [wirescope:keep claudemd]             cancel an omit (e.g. an operator "
    "default)\n"
    "  [wirescope:replace claudemd <text>]   keep the section, swap in a "
    "one-line body\n"
    "  [wirescope:tools Read,Grep,Glob]      forward only these tools "
    "(allowlist)\n"
    "  [wirescope:strip-tools Bash,WebFetch] forward all but these tools "
    "(denylist)\n"
    "  [wirescope:keep-tools Bash]           cancel a strip-tools\n"
    "Context targets: claudemd, useremail (some may already be stripped by an "
    "operator default). Tool names match the subagent's roster.")
# directive target token -> the `# <Section>` heading it removes
_WS_OMIT_TARGETS = {"claudemd": "# claudeMd", "useremail": "# userEmail"}


# A reminder SECTION header is `# ` + a lowercase camelCase key (claudeMd,
# userEmail, currentDate, … — the CLI generates them from internal camelCase
# keys) on its own line. NOT an arbitrary markdown heading: CLAUDE.md CONTENT
# routinely leads with `# Title` / `## Sub` headings, and a naive `^# ` boundary
# stopped at the FIRST of those, leaving the whole doc un-stripped (the
# 2026-06-14 leak: a `# claudeMd` body began `# Spatiul lui Adam`, so omit cut
# only the ~280-char preamble and the project doc survived on the wire).
# Calibrated against 400 real captures: every CLI reminder header is lowercase
# camelCase (claudeMd/userEmail/currentDate); content headings are Title-Case or
# multi-word and never match — so a section ends only at the NEXT such header or
# the closing tag. (An unknown future section is still camelCase → respected, no
# over-strip; the rare lowercase-single-word CONTENT heading would under-strip,
# same fail-safe class as before, never a leak of MORE than asked.)
_WS_SECTION_HDR_RE = r"(?m)^# [a-z][A-Za-z0-9]*[ \t]*$"


def _ws_section_end(rest):
    """Offset into `rest` where the current reminder section ends: the next
    reminder-section header or </system-reminder>, whichever is first; len(rest)
    if neither (defensive — strips to end)."""
    bounds = [c.start() for c in (re.search(_WS_SECTION_HDR_RE, rest),
                                  re.search(r"</system-reminder>", rest)) if c]
    return min(bounds) if bounds else len(rest)


def _ws_strip_reminder_section(text, hdr):
    """Strip the `hdr` section from a <system-reminder> text — the heading and its
    whole body up to the next reminder-section header (see _WS_SECTION_HDR_RE) or
    the closing </system-reminder>, so internal markdown headings inside the body
    don't truncate it and a sibling section (e.g. userEmail) after it is kept.
    Returns (new_text, chars_removed)."""
    m = re.search(r"(?m)^[ \t]*" + re.escape(hdr) + r"[ \t]*$", text)
    if not m:
        return text, 0
    start = m.start()
    end = m.end() + _ws_section_end(text[m.end():])
    new = text[:start] + text[end:]
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new, end - start


def _ws_replace_reminder_section(text, hdr, new_body):
    """Replace the BODY of the `hdr` section (keep the `# <Section>` heading,
    swap its content) with `new_body`. Same section boundary as the strip helper
    (next reminder-section header or the closing </system-reminder>). Returns
    (new_text, 1) on a hit, (text, 0) on a miss (fail-safe, never invents a
    section)."""
    m = re.search(r"(?m)^[ \t]*" + re.escape(hdr) + r"[ \t]*$", text)
    if not m:
        return text, 0
    end = m.end() + _ws_section_end(text[m.end():])
    body = new_body.strip("\n")
    new = text[:m.end()] + "\n" + body + "\n" + text[end:]
    return new, 1


def _ws_omit_target_list(value):
    """Parse an `omit`/`keep` value into a lowercased token list. LIBERAL
    separator (Postel's law): commas and/or whitespace, so `claudemd,useremail`,
    `claudemd, useremail`, and `claudemd useremail` are all equivalent. An agent
    that discovered the syntax from the spawner hint tends to write
    space-separated targets — the most natural naive form must parse, or a
    correctly-intentioned omit silently no-ops (real catch, 2026-06-14)."""
    return [t.lower() for t in re.split(r"[,\s]+", (value or "").strip()) if t]


def _ws_resolve_actions(pairs):
    """Resolve ordered [(directive, value)] into a per-target action map
    {target: ('omit', None) | ('replace', text)}. `omit` adds each listed target;
    `replace <target> <text>` sets one target to substitute that text; `keep`
    cancels a target. Later directives override earlier for the same target, so
    feeding body pairs THEN spawn pairs makes spawn win (precedence spawn > body),
    in either direction. The verb vocabulary lives here — new section verbs slot
    in as one more branch."""
    actions = {}
    for name, value in pairs:
        if name == "omit":
            for t in _ws_omit_target_list(value):
                actions[t] = ("omit", None)
        elif name == "replace":
            parts = value.split(None, 1)            # "<target> <inline text>"
            if parts:
                actions[parts[0].lower()] = (
                    "replace", parts[1] if len(parts) > 1 else "")
        elif name == "keep":
            for t in _ws_omit_target_list(value):
                actions.pop(t, None)
    return actions


# STICKY PER-INSTANCE SPAWN MEMORY: spawn-position directives only sit at the
# strict HEAD of messages[0] on a subagent's FIRST turn; on any continuation turn
# that block is a follow-up / <local-command-caveat> / compaction summary, so
# _ws_spawn_pairs sees nothing and the omitted sections (esp. # claudeMd) RETURN.
# We remember the resolved spawn pairs by the stable per-instance key
# (x-claude-code-agent-id, present iff subagent) and RE-APPLY them on later turns
# of the same instance. A later directive-bearing turn UPDATES the memory (a
# fresh `keep` can still cancel). Keyed session_id -> {agent_id: pairs} so the
# pinger sweep drops it with the session's other instance state; transforms OWNS
# + mutates this (no gate but omit reads it). See _ws_forget.
_WS_SPAWN_MEMORY = {}


def _ws_forget(session_id):
    """Drop the sticky spawn memory for a session (called by the pinger sweep
    alongside the other per-session instance state). No-op if absent."""
    _WS_SPAWN_MEMORY.pop(session_id, None)


def _ws_merged_pairs(obj, agent_id=None):
    """Ordered (directive, value) pairs after merging the operator default policy
    + body + spawn layers (precedence operator < body < spawn, by feed order),
    with the spawn layer made STICKY per instance. BOTH verb families consume this
    one stream — section verbs via _ws_resolve_actions, tool verbs via
    _ws_resolve_tools — so precedence + stickiness live in exactly one place.

    `agent_id` (the x-claude-code-agent-id request header) makes the spawn layer
    sticky: on a directive-bearing turn we remember the spawn pairs under that id;
    on a later directive-less turn of the same instance we re-feed the remembered
    pairs so the directives persist past turn 1. Only ever remembered/replayed for
    a real subagent instance (the main line has no agent_id, so it is never
    sticky; a non-subagent is never touched even if some header leaked through)."""
    pairs = []
    is_sub = writer_mod._billing_is_subagent(obj)
    if WS_OMIT_DEFAULT and is_sub:
        pairs.append(("omit", ",".join(WS_OMIT_DEFAULT)))   # lowest precedence
    pairs += writer_mod._ws_body_pairs(obj)
    spawn = writer_mod._ws_spawn_pairs(obj)
    if agent_id and is_sub:
        sid = writer_mod._session_ids(obj)[0]
        if spawn:                                  # turn-1 (or any directive turn)
            _WS_SPAWN_MEMORY.setdefault(sid, {})[agent_id] = spawn
        else:                                      # continuation turn: replay
            spawn = (_WS_SPAWN_MEMORY.get(sid) or {}).get(agent_id, [])
    pairs += spawn                                 # highest precedence
    return pairs


def _ws_effective_actions(obj, agent_id=None):
    """The per-target SECTION action map (omit/keep/replace) after merging the
    operator default + body + spawn directives (see _ws_merged_pairs for the
    precedence + stickiness). See _ws_resolve_actions. {} when nothing applies."""
    return _ws_resolve_actions(_ws_merged_pairs(obj, agent_id))


def _ws_effective_omit_targets(obj):
    """Just the targets resolved to a strip (`omit`) — convenience for callers /
    tests that only care about deletions, not replacements."""
    return {t for t, (act, _) in _ws_effective_actions(obj).items()
            if act == "omit"}


# ---- WIRESCOPE tool-set trim (`tools` / `strip-tools` / `keep-tools`) -------
# Let a SPAWNER trim a subagent's tool roster on the wire, customizing a
# predefined agent (whose toolset is frozen in its `.claude/agents/<name>.md`
# frontmatter) WITHOUT editing its file. This is the biggest token lever we have
# (default ~33 tools ≈ 24k tok every turn, typical use ~4); native `--tools`
# trims only the MAIN agent, so per-spawn subagent trimming is a real gap.
#   `[wirescope:tools Read,Edit,Grep]` — ALLOWLIST: keep ONLY these (mirrors
#       native --tools; last one wins so spawn overrides body).
#   `[wirescope:strip-tools Bash,WebFetch]` — DENYLIST: remove these, keep the
#       rest (safe surgical removal; no need to know the agent's full roster).
#   `[wirescope:keep-tools Bash]` — cancel a drop / re-admit to the allowlist
#       (precedence override, e.g. a spawn keep over a body strip).
# Matching is case-insensitive + liberal-separator (a naive agent writes
# `strip-tools bash`). tools[] sits IN FRONT of the first cache breakpoint, so a
# consistent per-instance trim (sticky via _ws_merged_pairs) reshapes the cached
# prefix to the SMALLER set once, then rides it — a net cache WIN, not a per-turn
# bust. Forgeable only by system body / spawn-prompt head (never message content,
# same as omit). Sharp edge (spawner's call, like --tools): if the agent's prompt
# expects a stripped tool and the model emits it, upstream 400s.
WS_STRIP_TOOLS = os.environ.get("WS_STRIP_TOOLS", "1") not in (
    "0", "no", "off", "false")


def _ws_resolve_tools(pairs):
    """Resolve ordered pairs into a tool filter spec {'allow': set|None,
    'drop': set} (lowercased names). `tools` SETS the allowlist (last wins);
    `strip-tools` adds to the drop set; `keep-tools` removes from drop AND
    re-admits to an active allowlist. Non-tool verbs ignored."""
    allow = None
    drop = set()
    for name, value in pairs:
        if name == "tools":
            allow = set(_ws_omit_target_list(value))
        elif name == "strip-tools":
            drop.update(_ws_omit_target_list(value))
        elif name == "keep-tools":
            for t in _ws_omit_target_list(value):
                drop.discard(t)
                if allow is not None:
                    allow.add(t)
    return {"allow": allow, "drop": drop}


def _ws_strip_tools(obj, agent_id=None):
    """Apply the wirescope tool-trim directives to obj['tools']. Returns a log
    dict {removed, kept, allow, drop[, miss]} or None (gate off / no tools / no
    directive). A directive that matches NOTHING is a fail-safe MISS (logged,
    never over-strips). WS_STRIP_TOOLS=0 is the deployment kill-switch."""
    if not WS_STRIP_TOOLS:
        return None
    tools = obj.get("tools")
    if not isinstance(tools, list) or not tools:
        return None
    spec = _ws_resolve_tools(_ws_merged_pairs(obj, agent_id))
    allow, drop = spec["allow"], spec["drop"]
    if allow is None and not drop:
        return None                          # no tool directive in play
    kept, removed = [], []
    for t in tools:
        nm = t.get("name") if isinstance(t, dict) else None
        low = (nm or "").lower()
        if allow is not None and low not in allow:
            removed.append(nm)
        elif low in drop:
            removed.append(nm)
        else:
            kept.append(t)
    log = {"allow": sorted(allow) if allow is not None else None,
           "drop": sorted(drop)}
    if not removed:
        log["removed"] = []
        log["miss"] = True                   # directive present, matched nothing
        return log
    obj["tools"] = kept
    log["removed"] = removed
    log["kept"] = [t.get("name") for t in kept]
    return log


def _ws_reminder_is_empty(text):
    """True if `text` is a <system-reminder> whose every `# Section` was stripped,
    leaving only the wrapper + the "you can use the following context:" intro with
    NOTHING after it. Detected as: a system-reminder with no remaining column-0
    `# ` heading of any kind (a kept section — currentDate, Environment, … — keeps
    a heading, so this is conservative: never drops a block that still has real
    content)."""
    return ("<system-reminder>" in text
            and re.search(r"(?m)^# ", text) is None)


def _ws_omit(obj, agent_id=None):
    """Apply the effective wirescope context-section actions (omit / replace,
    body + spawn, with the `keep` override) to messages[0]. Returns a log dict
    {omitted, replaced, missed, chars_removed, requested, dropped_blocks} or None
    when nothing was requested / the flag is off. A requested target not found
    (unknown token or format drift) is a logged MISS, never an over-strip
    (fail-safe). A reminder block emptied of ALL its sections is dropped whole
    rather than forwarded as a dangling 'here's the context:' shell.

    `agent_id` threads the per-instance key so a continuation turn re-applies the
    spawn directive remembered from turn 1 (see _ws_effective_actions)."""
    if not WS_OMIT:
        return None
    actions = _ws_effective_actions(obj, agent_id=agent_id)
    if not actions:
        return None
    requested = sorted(actions)
    msgs = obj.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    omitted, replaced, chars, dropped = set(), set(), 0, 0
    for m in msgs:                       # in practice only messages[0] carries it
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        drop_idx = []
        for bi, b in enumerate(c):
            if not (isinstance(b, dict) and b.get("type") == "text"
                    and isinstance(b.get("text"), str)):
                continue
            touched = False
            for tgt, (act, payload) in actions.items():
                hdr = _WS_OMIT_TARGETS.get(tgt)
                if not hdr or hdr not in b["text"]:
                    continue
                if act == "replace":
                    new, n = _ws_replace_reminder_section(b["text"], hdr, payload)
                    if n:
                        b["text"] = new
                        replaced.add(tgt)
                        touched = True
                else:                                # "omit"
                    new, n = _ws_strip_reminder_section(b["text"], hdr)
                    if n:
                        b["text"] = new
                        omitted.add(tgt)
                        chars += n
                        touched = True
            if touched and _ws_reminder_is_empty(b["text"]):
                drop_idx.append(bi)
        if drop_idx:
            drop = set(drop_idx)
            kept = [b for i, b in enumerate(c) if i not in drop]
            if not kept:
                continue                     # never nuke a message's whole content
            # If a dropped block carried the message-level cache breakpoint,
            # re-anchor it on the new first block so we don't lose the breakpoint.
            lost_cc = next((c[i]["cache_control"] for i in drop
                            if isinstance(c[i], dict) and c[i].get("cache_control")),
                           None)
            if (lost_cc and isinstance(kept[0], dict)
                    and not any(isinstance(b, dict) and b.get("cache_control")
                                for b in kept)):
                kept[0]["cache_control"] = lost_cc
            m["content"] = kept
            dropped += len(drop)
    done = omitted | replaced
    missed = [t for t in requested if t not in done]
    if not done and not missed:
        return None
    return {"omitted": sorted(omitted), "replaced": sorted(replaced),
            "missed": missed, "chars_removed": chars, "requested": requested,
            "dropped_blocks": dropped}


def _ws_strip_directives(obj):
    """Remove every `[wirescope:...]` directive from the system text blocks before
    forwarding. The proxy has already READ and ACTED on them (agent-name captured
    for display, omit applied to messages[0]); they are proxy control lines, so
    the MODEL must never see them and they shouldn't cost prefix tokens. Always
    runs (not WS_OMIT-gated) — the proxy consumes its own directives regardless of
    whether a given verb is honored. Deterministic per agent type, so the stripped
    system prefix stays cache-constant (and equals the no-directive body). Returns
    {stripped, blocks} or None. Whitespace left behind is lightly tidied."""
    sys = obj.get("system")
    total, blocks = 0, []
    if isinstance(sys, list):
        for bi, b in enumerate(sys):
            if not (isinstance(b, dict) and isinstance(b.get("text"), str)
                    and "[wirescope:" in b["text"]):
                continue
            new, n = writer_mod._WS_DIRECTIVE_RE.subn("", b["text"])
            if n:
                b["text"] = re.sub(r"[ \t]*\n{3,}", "\n\n", new)
                total += n
                blocks.append(bi)
    elif isinstance(sys, str) and "[wirescope:" in sys:
        new, n = writer_mod._WS_DIRECTIVE_RE.subn("", sys)
        if n:
            obj["system"] = re.sub(r"[ \t]*\n{3,}", "\n\n", new)
            total, blocks = n, [0]
    return {"stripped": total, "blocks": blocks} if total else None


def _ws_strip_spawn_directives(obj):
    """Strip the strict-head spawn directives from messages[0]'s prompt block
    before forwarding — the proxy has already READ and ACTED on them (omit/keep
    merged into the effective target set, agent-name captured for display), so
    the model must never see our control lines and they cost zero tokens. Gated
    by WS_SPAWN_DIRECTIVES. Unlike the system strip, this removes ONLY the leading
    consumed directive lines — never a `[wirescope:...]` that appears later in
    prompt prose or a quoted transcript (which was never a directive). Returns
    {stripped} or None. Deterministic per spawn, so messages[0] stays byte-stable
    across the instance's turns (cache-coherent, no transcript desync)."""
    if not writer_mod.WS_SPAWN_DIRECTIVES:
        return None
    b = writer_mod._ws_prompt_block(obj)
    if b is None:
        return None
    new, n = writer_mod._ws_strip_leading_directives(b["text"])
    if not n:
        return None
    b["text"] = new
    return {"stripped": n}


def _ws_spawner_hint(obj):
    """Inject the constant spawner discovery hint (WS_SPAWNER_HINT, see
    above) as a TRAILING system block — appended after the last system block, so
    it lands past the system cache breakpoint: stable position, busts nothing
    before it, ~tiny uncached tail. Gated to spawner requests only (not a
    subagent; carries a spawn tool). Idempotent (won't double-inject). Returns
    {injected:True} or None. Default OFF — this is the lone wire-visible
    proxy-authored text in the whole protocol."""
    if not WS_SPAWNER_HINT:
        return None
    if writer_mod._billing_is_subagent(obj):       # never teach a subagent
        return None
    tools = obj.get("tools")
    if not isinstance(tools, list):
        return None
    names = {t.get("name") for t in tools if isinstance(t, dict)}
    if not (names & _WS_SPAWN_TOOLS):              # can't spawn -> hint is noise
        return None
    sys = obj.get("system")
    if not isinstance(sys, list):                  # only the list-form system
        return None
    if any(isinstance(b, dict) and isinstance(b.get("text"), str)
           and "[wirescope] " in b["text"] for b in sys):
        return None                                # already present (idempotent)
    sys.append({"type": "text", "text": _WS_HINT_TEXT})
    return {"injected": True}


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
    h.update(warmth_mod._sys_tools_fingerprint(obj))
    out = {}
    for i, m in enumerate(obj.get("messages") or []):
        h.update(b"\x1e")
        h.update(warmth_mod._canon_message(m))
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
    if not warmth_mod.WARMTH_LEDGER:
        return "off", None, 0
    msgs = obj.get("messages") or []
    if len(msgs) < 2:
        return "absent", None, 0
    hashes = _prefix_hashes(obj)
    depths = list(range(len(msgs) - 1, 0, -1))   # exclude the trailing compact prompt
    try:
        rows = warmth_mod._warmth_rows([hashes[d] for d in depths])
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
