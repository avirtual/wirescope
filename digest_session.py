#!/usr/bin/env python3
"""digest_session.py: mechanical, no-LLM summarizer for a Claude Code session.

Reads either a Claude Code transcript JSONL file or a wirescope capture
directory and writes a compact markdown digest: what prompts were given,
what tools were called, what clodex agent-to-agent traffic happened, and
what errors/notices occurred — without dumping any large payload (no tool
result bodies, no edit contents, no write contents, no images).

Two loaders (`load_transcript`, `load_capture_dir`) both produce a common
list of `(timestamp, role, blocks)` messages, where `blocks` is the same
list-of-content-block shape the Anthropic wire uses (`text`, `thinking`,
`tool_use`, `tool_result`, `image`), plus a synthetic `compact_boundary`
block kind for transcript compaction markers. Everything else (event
extraction, clodex intent parsing, rendering) is shared.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_TEXT_DEFAULT = 300
MAX_THINKING = 200
ELIDE_THRESHOLD = 60
ELIDE_HEAD = 40
ELIDE_TAIL = 15

SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INTENT_LINE_RE = re.compile(r"^\s*\[agent:")
TICKET_RE = re.compile(r"ticket (t\d+) (\w+)")


# ------------------------------------------------------------------
# loaders
# ------------------------------------------------------------------

def _parse_ts(raw):
    """Best-effort timestamp parse -> float epoch seconds, or None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # heuristics: ms vs s vs already datetime-like
        return float(raw) / 1000.0 if raw > 1e12 else float(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def load_transcript(path):
    """Load a Claude Code session JSONL transcript.

    Returns a list of (ts, role, blocks) tuples in file order. `blocks` is
    a list of content-block dicts (text/thinking/tool_use/tool_result/image)
    or, for a compaction marker, a single synthetic block
    `{"type": "compact_boundary", "text": <continuation summary>}`.
    """
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            ts = _parse_ts(obj.get("timestamp"))
            if t == "system":
                subtype = obj.get("subtype")
                if subtype == "compact_boundary":
                    content = obj.get("content") or "Conversation compacted"
                    out.append((ts, "system", [{"type": "compact_boundary", "text": str(content)}]))
                continue
            if t not in ("user", "assistant"):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or t
            content = msg.get("content")
            blocks = _normalize_content(content)
            if blocks is None:
                continue
            out.append((ts, role, blocks))
    return out


_LEADING_CTRL_RE = re.compile(r"^[\x00-\x08\x0b\x0c\x0e-\x1f]+")


def _clean_text(text):
    """Strip stray leading control bytes (seen in some transcript text
    blocks, e.g. a leading \\x15) that would otherwise defeat the
    `[agent:...]`-at-line-start intent match."""
    if not isinstance(text, str):
        return text
    return _LEADING_CTRL_RE.sub("", text)


def _normalize_content(content):
    """String or list wire content -> a list of block dicts, or None."""
    if content is None:
        return None
    if isinstance(content, str):
        return [{"type": "text", "text": _clean_text(content)}]
    if isinstance(content, list):
        blocks = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    b = dict(b)
                    b["text"] = _clean_text(b["text"])
                blocks.append(b)
            elif isinstance(b, str):
                blocks.append({"type": "text", "text": _clean_text(b)})
        return blocks
    return None


def _block_identity(b):
    """Identity token for one content block, chosen to survive documented
    settled-history mutation rather than the block's raw bytes.

    `tool_result`/`tool_use` blocks key on their `tool_use_id`/`id` (the
    wire's own pairing identity) rather than their content/input -- a
    settled tool_result's CONTENT is routinely mutated in place by a later
    proxy fold (measured live: an Edit-confirmation shrank from the full
    "file ... updated successfully" sentence to a bare "ok" 12s later on
    the SAME `tool_use_id`; a failed tool_use's `input` was replaced with
    an `_elided` stub) while the id never changes -- so keying on content
    there would read routine proxy housekeeping as a compact. Returns None
    for a `thinking`/`redacted_thinking` block (filtered by the caller).
    """
    if isinstance(b, str):
        return ("tx", b[:200])
    if not isinstance(b, dict):
        return ("raw", json.dumps(b, default=str)[:200])
    btype = b.get("type")
    if btype in ("thinking", "redacted_thinking"):
        return None
    if "tool_use_id" in b:  # tool_result (its `type` isn't always present)
        return ("tr", b.get("tool_use_id"))
    if btype == "tool_use":
        return ("tu", b.get("id"), b.get("name"))
    if btype == "text":
        text = (b.get("text") or "").strip()
        # attachment reminders (memory store, claudeMd, hook context) are
        # dropped by the CLI once the message settles; the settled tail also
        # gains/loses a trailing "\n" -- neither is a history edit
        if text.startswith("<system-reminder>"):
            return None
        return ("tx", text[:200])
    d = {k: v for k, v in b.items() if k != "cache_control"}
    return ("raw", json.dumps(d, sort_keys=True, default=str)[:200])


def _msg_key(m):
    """Structural identity for one capture-dir message: `(role, blocks)`
    where `blocks` is a tuple of `_block_identity` tokens.

    Two wire-shape changes are normalized away here because they are NOT
    semantic edits: the CLI collapsing a single-text-block list to a bare
    string once history settles (both forms produce the same one-block
    tuple), and `PIN_SETTLED_BREAKPOINT` migrating the `cache_control`
    breakpoint marker between messages turn-to-turn (placement metadata,
    dropped in `_block_identity`, not hashed/billed content). Everything
    else about `_block_identity`'s tolerance lives there, not here.
    """
    role = m.get("role")
    content = m.get("content")
    if isinstance(content, str):
        blocks = tuple(tok for tok in (_block_identity({"type": "text", "text": content}),)
                       if tok is not None)
    elif isinstance(content, list):
        blocks = tuple(tok for tok in (_block_identity(b) for b in content) if tok is not None)
    else:
        blocks = (("raw", json.dumps(content, default=str)[:200] if content is not None else ""),)
    return (role, blocks)


def _keys_extend(prev_keys, new_keys):
    """True iff `new_keys` is `prev_keys` with zero or more messages
    appended -- tolerating the LAST shared message growing extra trailing
    blocks in place.

    That tolerance matters for one concrete case seen live: the CLI's
    auto-compact trigger appends its "CRITICAL: Respond with TEXT ONLY ...
    summarize the conversation" instruction as an EXTRA block onto the
    final existing message (a tool_result) rather than as a new message,
    so the message COUNT is unchanged but the last message's block count
    grows. Without this, that turn misreads as a full reset.
    """
    # a trailing role:system message is a proxy/CLI tail hint (uncached,
    # never persisted into history) -- the next request legitimately lacks it
    while prev_keys and prev_keys[-1][0] == "system":
        prev_keys = prev_keys[:-1]
    if len(new_keys) < len(prev_keys):
        return False
    if not prev_keys:
        return True
    if new_keys[: len(prev_keys) - 1] != prev_keys[:-1]:
        return False
    prev_role, prev_blocks = prev_keys[-1]
    new_role, new_blocks = new_keys[len(prev_keys) - 1]
    n = min(len(prev_blocks), len(new_blocks))
    return prev_role == new_role and new_blocks[:n] == prev_blocks[:n]


def load_capture_dir(path):
    """Load a wirescope capture directory as a chronological walk.

    Each `<seq>-...-<role>-...request.json` file is one forwarded request;
    the main agent's own successive requests each carry the FULL message
    history up to that turn (the wire is stateless), so walking them in
    order and diffing consecutive `body.messages` lists recovers real
    per-message timestamps and the FULL session (not just the tail after
    the last compact) without ever holding more than one request body in
    memory at a time.

    Main-line = `summary.role == "parent"` (not a subagent/general-purpose
    side-call) -- same definition the single-request picker used before.

    Ordering key is TIMESTAMP, not `seq`: `seq` is a per-process-boot
    counter that resets whenever the proxy restarts, so two different
    boots reuse the same seq range (measured live on a 5,409-file capture
    dir: three agent-id boots' seq ranges overlap by thousands while their
    timestamps span different days) -- sorting by seq alone would
    interleave unrelated boots and produce nonsense ordering. `ts` is
    wall-clock and always advances; `seq` only breaks ties within the same
    second.

    For each request in order, messages beyond the previous request's
    message count are NEW (the wire carries no per-message timestamp, so a
    message's debut request supplies its timestamp -- see `_msg_key`). If
    the new list is not an extension of the previous one (shorter, or the
    shared prefix diverges), that is a compact or a fresh `--resume`/clear:
    a synthetic `compact_boundary` block is emitted and the walk restarts
    from the new list, all of it stamped with this request's timestamp.

    Memory: only the PREVIOUS request's list of structural keys is held
    across iterations, never the bodies themselves -- two passes over the
    directory (first a light pass for `ts`+`summary.role` per file, then a
    second pass that opens one parent-role body at a time in ts order).
    """
    entries = [n for n in os.listdir(path) if n.endswith(".request.json")]
    if not entries:
        return []

    def seq_of(name):
        try:
            return int(name.split("-", 1)[0])
        except ValueError:
            return -1

    # pass 1: cheap metadata only (ts, role) -- decide the walk order
    # without holding any request body around. Prefer role == "parent"
    # (the main agent line); if a capture dir has none at all, fall back
    # to everything that isn't an obvious subagent/side-call role, same
    # fallback the old single-request picker used.
    all_meta = []
    parent_meta = []
    for name in entries:
        full = os.path.join(path, name)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except (ValueError, OSError):
            continue
        summary = d.get("summary") or {}
        role = summary.get("role")
        if not (d.get("body") or {}).get("tools"):
            continue  # tool-less side-call (title, summarize) -- not the main line
        ts = _parse_ts(d.get("ts"))
        entry = (ts if ts is not None else 0.0, seq_of(name), name)
        all_meta.append((role, entry))
        if role == "parent":
            parent_meta.append(entry)

    if parent_meta:
        meta = parent_meta
    else:
        meta = [e for role, e in all_meta if role not in ("subagent", "general-purpose")] \
            or [e for _, e in all_meta]
    if not meta:
        return []

    meta.sort(key=lambda t: (t[0], t[1]))

    # pass 2: walk in ts order, one body at a time.
    out = []
    prev_keys = None
    last_name = None
    last_ts = None
    for ts, seq, name in meta:
        full = os.path.join(path, name)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except (ValueError, OSError):
            continue
        body = d.get("body") or {}
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        req_ts = _parse_ts(d.get("ts"))
        if req_ts is None:
            req_ts = ts
        keys = [_msg_key(m) for m in messages]

        if prev_keys is None:
            new_msgs = messages
        elif _keys_extend(prev_keys, keys):
            shared = len(prev_keys)
            while shared and prev_keys[shared - 1][0] == "system" and \
                    (shared > len(keys) or keys[shared - 1] != prev_keys[shared - 1]):
                shared -= 1
            new_msgs = messages[shared:]
        else:
            out.append((req_ts, "system",
                        [{"type": "compact_boundary",
                          "text": "Conversation compacted (or session restarted)"}]))
            new_msgs = messages

        for m in new_msgs:
            role = m.get("role") or "user"
            blocks = _normalize_content(m.get("content"))
            if blocks is None:
                continue
            out.append((req_ts, role, blocks))

        prev_keys = keys
        last_name = name
        last_ts = req_ts
        # d/body/messages go out of scope on the next loop iteration --
        # only `prev_keys` (a list of small tuples) survives.

    if last_name is None:
        return out

    # try to append the final assistant reply from the matching .response.sse
    stem = last_name[: -len(".request.json")]
    sse_path = os.path.join(path, stem + ".response.sse")
    reply_blocks = _parse_sse_reply(sse_path)
    if reply_blocks:
        out.append((last_ts, "assistant", reply_blocks))
    else:
        out.append((last_ts, "system", [{"type": "text", "text": "(final reply not included)"}]))
    return out


def _parse_sse_reply(sse_path):
    """Parse an Anthropic SSE stream into a list of content blocks.

    Handles content_block_start/content_block_delta for text_delta and
    input_json_delta (tool input). Returns [] on any trouble.
    """
    if not os.path.isfile(sse_path):
        return []
    blocks = {}
    order = []
    try:
        with open(sse_path, "r", encoding="utf-8", errors="replace") as f:
            data_line = None
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("data:"):
                    data_line = line[len("data:"):].strip()
                elif line == "" and data_line is not None:
                    _apply_sse_event(data_line, blocks, order)
                    data_line = None
            if data_line is not None:
                _apply_sse_event(data_line, blocks, order)
    except OSError:
        return []
    out = []
    for idx in order:
        b = blocks.get(idx)
        if not b:
            continue
        if b.get("type") == "tool_use":
            raw = b.pop("_input_raw", "")
            try:
                b["input"] = json.loads(raw) if raw else {}
            except ValueError:
                b["input"] = {}
        out.append(b)
    return out


def _apply_sse_event(data_line, blocks, order):
    try:
        ev = json.loads(data_line)
    except ValueError:
        return
    etype = ev.get("type")
    if etype == "content_block_start":
        idx = ev.get("index")
        cb = ev.get("content_block") or {}
        block = dict(cb)
        if block.get("type") == "tool_use":
            block["_input_raw"] = ""
        blocks[idx] = block
        order.append(idx)
    elif etype == "content_block_delta":
        idx = ev.get("index")
        delta = ev.get("delta") or {}
        block = blocks.get(idx)
        if block is None:
            return
        dtype = delta.get("type")
        if dtype == "text_delta":
            block["text"] = block.get("text", "") + delta.get("text", "")
        elif dtype == "input_json_delta":
            block["_input_raw"] = block.get("_input_raw", "") + delta.get("partial_json", "")
        elif dtype == "thinking_delta":
            block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")


def detect_format(path):
    """Return 'transcript', 'capture_dir', or None."""
    if os.path.isdir(path):
        for name in os.listdir(path):
            if name.endswith(".request.json"):
                return "capture_dir"
        return None
    if os.path.isfile(path):
        if path.endswith(".jsonl"):
            return "transcript"
        # sniff: a JSONL file with "type" keys
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                first = f.readline()
            obj = json.loads(first)
            if isinstance(obj, dict) and "type" in obj:
                return "transcript"
        except (ValueError, OSError):
            pass
    return None


# ------------------------------------------------------------------
# text helpers
# ------------------------------------------------------------------

def strip_reminders(text):
    return SYSTEM_REMINDER_RE.sub("", text or "")


def strip_code_fences(text):
    def repl(m):
        n = m.group(0).count("\n") + 1
        return f"[code block, {n} lines]"
    return CODE_FENCE_RE.sub(repl, text or "")


def trim(text, n):
    text = text or ""
    text = " ".join(text.split())
    if len(text) > n:
        return text[: n].rstrip() + "…"
    return text


def fmt_hhmm(ts, utc=False):
    if ts is None:
        return "??:??"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if not utc:
            dt = dt.astimezone()
        return dt.strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "??:??"


def _tz_label(utc):
    if utc:
        return "UTC"
    return datetime.now().astimezone().tzname() or "local"


def is_prompt_text(text):
    """A user message counts as a real prompt if it has visible text left
    after stripping system-reminders and clodex intent-only wrapper lines."""
    stripped = strip_reminders(text).strip()
    return bool(stripped)


# ------------------------------------------------------------------
# clodex intent parsing
# ------------------------------------------------------------------

def parse_intents(text, is_assistant):
    """Parse `[agent:...]` intent lines (and, for a dm, the body lines that
    belong to it) out of a text block.

    Returns (events, remainder, matched) where `events` is a list of
    rendered one-line strings (no timestamp), `remainder` is the input text
    with every consumed intent line/block removed (i.e. what's left over
    as ordinary prose), and `matched` is True iff at least one `[agent:...]`
    intent line was recognized -- even one that renders ZERO events because
    it's pure noise (`[agent:end]`, `[agent:task list]`, etc). Callers must
    branch on `matched`, not on `bool(events)`: an intent-bearing line with
    every intent noise-filtered has `events == []` but `matched == True`,
    and must still render `remainder` rather than the raw original text (a
    bare `events` check would otherwise re-surface the very intent markers
    the filtering was supposed to drop). `is_assistant` is accepted for
    symmetry (assistant text emits dm/end/who/etc; user text only relays
    from/exec/task/remind/etc) but both are parsed by the same table today.
    """
    lines = text.split("\n")
    events = []
    matched = False
    remainder_lines = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not INTENT_LINE_RE.match(stripped):
            remainder_lines.append(line)
            i += 1
            continue
        matched = True
        consumed, evs = _parse_one_intent(lines, i)
        events.extend(evs)
        i += consumed
    remainder = "\n".join(remainder_lines).strip()
    return events, remainder, matched


def _intent_head(stripped):
    """Return (tag, rest) where tag is the bracketed head e.g. 'agent:dm X'."""
    if not stripped.startswith("[") :
        return None, stripped
    end = stripped.find("]")
    if end == -1:
        return None, stripped
    return stripped[1:end], stripped[end + 1:].strip()


def _parse_one_intent(lines, i):
    """Parse the intent block starting at lines[i]. Returns (n_consumed, [event_str, ...])."""
    stripped = lines[i].strip()
    tag, rest = _intent_head(stripped)
    if tag is None:
        return 1, []
    parts = tag.split(None, 1)
    kind = parts[0] if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if kind == "agent:dm":
        target = arg or "?"
        body_lines = []
        j = i + 1
        while j < len(lines):
            ls = lines[j].strip()
            if ls == "[agent:end]" or INTENT_LINE_RE.match(ls):
                break
            body_lines.append(lines[j])
            j += 1
        body = " ".join(l.strip() for l in body_lines if l.strip())
        consumed = j - i
        return consumed, [f"→ dm to {target}: {trim(body, 120)}"]

    if kind == "agent:from":
        # tag = "agent:from wirescope" -> arg = "wirescope";
        # rest = "Message (523 bytes) attached: @/path ..." OR
        # "[ticket t762 rejected] close with ... Message (N bytes) attached: @/path"
        target = arg or "?"
        events = []
        tm = TICKET_RE.search(rest)
        if tm:
            events.append(f"ticket {tm.group(1)} {tm.group(2)}")
        # an inline peer message runs to a bare [agent:end], the next intent
        # line, or the end of the block -- its body is not a user prompt
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if nxt == "[agent:end]":
                j += 1
                break
            if INTENT_LINE_RE.match(nxt):
                break
            j += 1
        attach_m = re.search(r"Message \((\d+) bytes\) attached", rest)
        if attach_m:
            events.append(f"← dm from {target} ({attach_m.group(1)} bytes, attached file)")
        else:
            events.append(f"← dm from {target}: {trim(rest, 120)}")
        return j - i, events

    if kind == "agent:task":
        # "[agent:task done ID]" -> the agent's OWN close intent
        m = re.match(r"done\s+(\S+)", arg)
        if m:
            return 1, [f"✔ ticket {m.group(1)} closed: {trim(rest, 100)}".rstrip(": ")]
        # "[agent:task list...]" -> a board query; noise, drop
        if re.match(r"^list\b", arg):
            return 1, []
        # "[agent:task] tickets on ..." -> the board-listing reply to that
        # query; noise, drop (it's a full-board dump, not an event)
        if not arg and rest.strip().startswith("tickets on"):
            return 1, []
        # "[agent:task] ticket tNNN <verb> ..." -> the HOST's reply about a
        # single ticket. A "closed" verb here would duplicate the agent's
        # own "✔ ticket ... closed" intent line above, so render it as a
        # confirmation instead; any other verb is the host's side of an
        # action the agent didn't itself emit (accepted/respec'd/reopened/
        # etc), tagged "(host)" to distinguish it from the agent's own line.
        if not arg:
            tm = TICKET_RE.search(rest)
            if tm:
                tid, verb = tm.group(1), tm.group(2)
                if verb == "closed":
                    return 1, [f"ticket {tid} close confirmed"]
                return 1, [f"ticket {tid} {verb} (host)"]
        tm = TICKET_RE.search(rest) or TICKET_RE.search(arg)
        if tm:
            return 1, [f"ticket {tm.group(1)} {tm.group(2)}"]
        return 1, [f"task {trim(arg or rest, 100)}"]

    if kind == "agent:exec":
        # "[agent:exec name] {...}" (outgoing) or "[agent:exec] name: result" (return)
        if arg:
            payload = trim(rest, 80)
            return 1, [f"exec {arg} {payload}".rstrip()]
        m = re.match(r"^([^:]+):\s*(.*)$", rest)
        if m:
            return 1, [f"exec {m.group(1).strip()} → {trim(m.group(2), 100)}"]
        return 1, [f"exec {trim(rest, 100)}"]

    if kind == "agent:remind":
        # "[agent:remind list]" -> a query; noise, drop
        if re.match(r"^list\b", arg):
            return 1, []
        # "[agent:remind] N reminder(s):" -> the reply header to that
        # query; noise, drop (the per-reminder detail lines that may
        # follow on subsequent lines aren't `[agent:` intent lines, so
        # they're untouched -- only the header line itself is noise)
        if not arg and re.match(r"^\d+\s+reminder\(s\)", rest.strip()):
            return 1, []
        return 1, [f"remind {trim(arg + ' ' + rest, 120).strip()}"]

    if kind == "agent:memory":
        return 1, [f"memory {arg} {trim(rest, 100)}".rstrip()]

    if kind == "agent:notify-user":
        return 1, [f"notify-user: {trim(rest, 120)}"]

    if kind == "agent:spawn":
        return 1, [f"spawn {trim(arg, 100)}"]

    if kind == "agent:context":
        verb = arg.strip() or trim(rest, 40)
        return 1, [f"context {verb}"]

    if kind == "agent:file":
        return 1, [f"file view {trim(arg, 150)}"]

    if kind == "term" or kind == "agent:term":
        return 1, [f"term exec: {trim(rest or arg, 100)}"]

    if kind in ("agent:end", "agent:who", "agent:name"):
        # bare turn-bookkeeping markers; noise, drop
        return 1, []

    # unknown intents: one-word note
    label = kind.split(":", 1)[-1] if ":" in kind else kind
    ev = f"[{kind}]" if not rest and not arg else f"{label}: {trim(arg + ' ' + rest, 80).strip()}"
    return 1, [ev]


# ------------------------------------------------------------------
# tool call rendering
# ------------------------------------------------------------------

def _str_len(v):
    return len(v) if isinstance(v, str) else 0


def render_tool_call(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        desc = inp.get("description")
        cmd = inp.get("command", "")
        first_line = cmd.split("\n", 1)[0] if isinstance(cmd, str) else ""
        gist = desc if desc else first_line
        if not gist and inp.get("_elided"):
            gist = str(inp["_elided"])
        return f"Bash: {trim(gist, 100)}" if gist else "Bash (no command captured)"
    if name in ("Read", "Edit", "Write"):
        path = inp.get("file_path") or inp.get("path") or "?"
        if name == "Edit":
            old = _str_len(inp.get("old_string"))
            new = _str_len(inp.get("new_string"))
            return f"Edit {path} (+{new}/-{old} chars)"
        if name == "Write":
            content = inp.get("content", "")
            nlines = content.count("\n") + 1 if isinstance(content, str) and content else 0
            return f"Write {path} ({nlines} lines)"
        return f"Read {path}"
    if name in ("Glob", "Grep"):
        pattern = inp.get("pattern", "?")
        return f"{name} {trim(pattern, 100)}"
    if name == "Agent":
        desc = inp.get("description", "")
        model = inp.get("model", "")
        suffix = f" ({model})" if model else ""
        return f"Agent: {trim(desc, 100)}{suffix}"
    if name == "WebFetch":
        return f"WebFetch {trim(inp.get('url', '?'), 150)}"
    if name == "Skill":
        return f"Skill {trim(inp.get('skill', inp.get('name', '?')), 100)}"
    # unknown tool: name + first string value
    first_val = None
    for v in inp.values():
        if isinstance(v, str) and v:
            first_val = v
            break
    if first_val:
        return f"{name}: {trim(first_val, 80)}"
    return f"{name}"


# ------------------------------------------------------------------
# system-notice classification
# ------------------------------------------------------------------

def classify_notice(text):
    t = text or ""
    if "hasn't heard from you" in t or "haven't heard from you" in t:
        return "nudge: idle"
    if "task tools haven't been used" in t or "Task tool" in t and "recently" in t:
        return "nudge: task tools"
    if "was modified, either by the user or by a linter" in t:
        return "file-modified note"
    return "other"


# ------------------------------------------------------------------
# event extraction
# ------------------------------------------------------------------

class Event:
    __slots__ = ("ts", "kind", "text")

    def __init__(self, ts, kind, text):
        self.ts = ts
        self.kind = kind  # 'prompt_heading' | 'line' | 'compact'
        self.text = text


def events_from_messages(msgs, opts):
    """Turn the common (ts, role, blocks) message list into a flat list of
    sections: [(heading_text_or_None, ts, [Event, ...]), ...].

    Each section starts at a real user prompt (or at the very start / after
    a compact boundary if no heading applies yet).
    """
    sections = []
    current = None  # (heading, ts, events)
    counts = {
        "user_turns": 0,
        "assistant_msgs": 0,
        "tool_calls": {},
        "peer_messages": {},
        "tickets": set(),
        "notices": {},
    }

    def ensure_section(heading, ts):
        nonlocal current
        if current is None or heading is not None:
            current = [heading, ts, []]
            sections.append(current)
        return current

    # start an initial anonymous section so pre-prompt events have a home
    ensure_section(None, msgs[0][0] if msgs else None)

    pending_tool_run = []  # collapsing consecutive identical-tool calls

    def flush_tool_run(sect):
        if not pending_tool_run:
            return
        ts0 = pending_tool_run[0][0]
        name = pending_tool_run[0][1]
        if len(pending_tool_run) == 1:
            sect[2].append(Event(ts0, "line", pending_tool_run[0][2]))
        else:
            args = [p[3] for p in pending_tool_run]
            sect[2].append(Event(ts0, "line",
                                  f"{name} ×{len(pending_tool_run)}: {', '.join(args)}"))
        pending_tool_run.clear()

    def add_tool_call(sect, ts, name, inp, suffix=""):
        arg = _tool_arg_gist(name, inp)
        line = render_tool_call(name, inp) + suffix
        if pending_tool_run and pending_tool_run[-1][1] == name and not suffix:
            pending_tool_run.append((ts, name, line, arg))
        else:
            flush_tool_run(sect)
            if suffix:
                sect[2].append(Event(ts, "line", line))
            else:
                pending_tool_run.append((ts, name, line, arg))
        counts["tool_calls"][name] = counts["tool_calls"].get(name, 0) + 1

    # boilerplate compact_boundary `content` values that carry no actual
    # continuation-summary information (the real summary, if any, arrives
    # as the NEXT user message) -- suppress these so the marker doesn't
    # get a redundant, uninformative line right under it.
    _COMPACT_BOILERPLATE = {"Conversation compacted",
                             "Conversation compacted (or session restarted)"}

    for ts, role, blocks in msgs:
        # compact boundary marker: a plain line, not a heading (the
        # CONTINUATION SUMMARY that follows, if any, becomes the next
        # heading via the ordinary prompt-heading path below).
        if role == "system" and len(blocks) == 1 and blocks[0].get("type") == "compact_boundary":
            flush_tool_run(current)
            summary_text = trim(blocks[0].get("text", ""), 300)
            current[2].append(Event(ts, "compact", "── context compacted ──"))
            if summary_text and summary_text not in _COMPACT_BOILERPLATE:
                current[2].append(Event(ts, "line", summary_text))
            continue

        if role == "system":
            # wire-shape system notice (string content already normalized to text block)
            for b in blocks:
                if b.get("type") == "text":
                    label = classify_notice(b.get("text", ""))
                    counts["notices"][label] = counts["notices"].get(label, 0) + 1
            continue

        if role == "user":
            counts["user_turns"] += 1
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            joined = "\n".join(text_parts)

            # system-reminder-only or tool_result-only messages aren't prompts
            has_tool_result = any(b.get("type") == "tool_result" for b in blocks)
            visible = is_prompt_text(joined)

            # clodex intent lines from user text (from/exec/task/remind/spawn/file/terminal)
            intent_lines, intent_remainder, _ = parse_intents(joined, is_assistant=False) if joined else ([], "", False)
            for ev_text in intent_lines:
                flush_tool_run(current)
                current[2].append(Event(ts, "line", ev_text))
                if ev_text.startswith("← dm from") or ev_text.startswith("→ dm to"):
                    peer_m = re.search(r"dm (?:to|from) (\S+)", ev_text)
                    if peer_m:
                        peer = peer_m.group(1).rstrip(":")
                        counts["peer_messages"][peer] = counts["peer_messages"].get(peer, 0) + 1
                tm = re.search(r"ticket (t\d+)", ev_text)
                if tm:
                    counts["tickets"].add(tm.group(1))

            # tool_result blocks: only errors / large results, attached to current section
            for b in blocks:
                if b.get("type") == "image":
                    flush_tool_run(current)
                    current[2].append(Event(ts, "line", "[image]"))
                    continue
                if b.get("type") != "tool_result":
                    continue
                content = b.get("content")
                content_text = content if isinstance(content, str) else json.dumps(content) if content else ""
                size = len(content_text)
                if b.get("is_error"):
                    flush_tool_run(current)
                    current[2].append(Event(ts, "line", f"⚠ tool failed: {trim(content_text, 120)}"))
                elif size > 20000:
                    flush_tool_run(current)
                    current[2].append(Event(ts, "line", f"(large result, {size:,} chars)"))

            # does this open a new prompt heading? (visible text that isn't
            # entirely consumed by intent lines and isn't riding a tool_result)
            stripped_reminder_removed = strip_reminders(intent_remainder).strip()
            # CLI echoes of a local slash command (`/compact` and its caveat,
            # command-name and stdout wrappers) are bookkeeping, not prompts
            if re.match(r"^(<local-command-caveat>|<command-name>|<local-command-stdout>|/[a-z-]+\s*$)",
                        stripped_reminder_removed):
                cmd = re.search(r"<command-name>([^<]+)</command-name>", stripped_reminder_removed)
                if cmd or re.match(r"^/[a-z-]+\s*$", stripped_reminder_removed):
                    flush_tool_run(current)
                    name = cmd.group(1) if cmd else stripped_reminder_removed.strip()
                    current[2].append(Event(ts, "line", f"local command {name}"))
                stripped_reminder_removed = ""
            if stripped_reminder_removed.startswith("Another Claude session sent a message:"):
                flush_tool_run(current)
                who = re.search(r'teammate_id="([^"]+)"', stripped_reminder_removed)
                why = re.search(r'"idleReason":\s*"([^"]+)"', stripped_reminder_removed)
                fail = re.search(r'"failureReason":\s*"([^"]+)"', stripped_reminder_removed)
                label = f"← subagent {who.group(1) if who else '?'} {why.group(1) if why else 'message'}"
                if fail:
                    label += f": {fail.group(1)}"
                current[2].append(Event(ts, "line", trim(label, 160)))
                stripped_reminder_removed = ""
            if stripped_reminder_removed.startswith("<task-notification>"):
                flush_tool_run(current)
                tid = re.search(r"<task-id>([^<]+)</task-id>", stripped_reminder_removed)
                current[2].append(Event(ts, "line", f"← subagent finished ({tid.group(1) if tid else '?'})"))
                stripped_reminder_removed = ""
            if visible and stripped_reminder_removed and not has_tool_result:
                flush_tool_run(current)
                if stripped_reminder_removed.startswith(
                        "This session is being continued from a previous conversation"):
                    # the CLI's synthetic continuation prompt is the compact
                    # itself, not something the user typed: fold it into the
                    # marker (once -- a transcript's compact_boundary line
                    # usually precedes it and already emitted one)
                    if not (current[2] and current[2][-1].kind == "compact"):
                        current[2].append(Event(ts, "compact", "── context compacted ──"))
                else:
                    ensure_section(trim(stripped_reminder_removed, 200), ts)
            continue

        if role == "assistant":
            counts["assistant_msgs"] += 1
            for b in blocks:
                btype = b.get("type")
                if btype == "text":
                    text = b.get("text", "")
                    intent_lines, remainder, matched = parse_intents(text, is_assistant=True)
                    if matched:
                        if remainder:
                            flush_tool_run(current)
                            shown = strip_code_fences(remainder)
                            current[2].append(Event(ts, "line", trim(shown, opts.max_text)))
                        for ev_text in intent_lines:
                            flush_tool_run(current)
                            current[2].append(Event(ts, "line", ev_text))
                            peer_m = re.search(r"dm (?:to|from) (\S+)", ev_text)
                            if peer_m:
                                peer = peer_m.group(1).rstrip(":")
                                counts["peer_messages"][peer] = counts["peer_messages"].get(peer, 0) + 1
                            tm = re.search(r"ticket (t\d+)", ev_text)
                            if tm:
                                counts["tickets"].add(tm.group(1))
                    else:
                        shown = strip_code_fences(text)
                        flush_tool_run(current)
                        current[2].append(Event(ts, "line", trim(shown, opts.max_text)))
                elif btype == "thinking":
                    if opts.thinking:
                        flush_tool_run(current)
                        current[2].append(Event(ts, "line",
                                                 "(thinking) " + trim(b.get("thinking", ""), MAX_THINKING)))
                elif btype == "tool_use":
                    add_tool_call(current, ts, b.get("name", "?"), b.get("input"))
                elif btype == "image":
                    flush_tool_run(current)
                    current[2].append(Event(ts, "line", "[image]"))
            continue

    flush_tool_run(current)
    return sections, counts


def _tool_arg_gist(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Read", "Edit", "Write"):
        return os.path.basename(inp.get("file_path") or inp.get("path") or "?")
    if name in ("Glob", "Grep"):
        return trim(inp.get("pattern", "?"), 40)
    return trim(str(next((v for v in inp.values() if isinstance(v, str)), "")), 40)


# ------------------------------------------------------------------
# rendering
# ------------------------------------------------------------------

def render(sections, header, counts, opts):
    lines = []
    lines.append(f"# Session digest: {header['title']}")
    lines.append("")
    lines.append(f"- Source: `{header['source']}`")
    if header.get("session_id"):
        lines.append(f"- Session id: `{header['session_id']}`")
    if header.get("start") or header.get("end"):
        lines.append(f"- Span: {header.get('start', '?')} → {header.get('end', '?')}"
                      f" ({header.get('duration', '?')})")
    lines.append(f"- User turns: {counts['user_turns']}  ·  Assistant messages: {counts['assistant_msgs']}")
    if counts["tool_calls"]:
        tool_str = ", ".join(f"{k} ×{v}" for k, v in
                              sorted(counts["tool_calls"].items(), key=lambda kv: -kv[1]))
        lines.append(f"- Tool calls: {tool_str}")
    if counts["peer_messages"]:
        ranked_peers = sorted(counts["peer_messages"].items(), key=lambda kv: -kv[1])
        shown, rest = ranked_peers[:15], ranked_peers[15:]
        peer_str = ", ".join(f"{k} ×{v}" for k, v in shown)
        if rest:
            peer_str += f", +{len(rest)} more"
        lines.append(f"- Messages exchanged per peer: {peer_str}")
    if counts["tickets"]:
        def ticket_key(t):
            m = re.match(r"t(\d+)", t)
            return int(m.group(1)) if m else 0
        ranked_tickets = sorted(counts["tickets"], key=ticket_key)
        shown, rest = ranked_tickets[:20], ranked_tickets[20:]
        ticket_str = ", ".join(shown)
        if rest:
            ticket_str += f", +{len(rest)} more"
        lines.append(f"- Tickets touched ({len(counts['tickets'])}): {ticket_str}")
    if counts["notices"]:
        notice_str = ", ".join(f"{k} ×{v}" for k, v in counts["notices"].items())
        lines.append(f"- System notices: {notice_str}")
    if header.get("note"):
        lines.append(f"- Note: {header['note']}")
    lines.append("")

    render_sections = sections
    if opts.last is not None and opts.last >= 0:
        render_sections = sections[-opts.last:] if opts.last else []
        if len(render_sections) < len(sections):
            lines.append(f"(showing last {len(render_sections)} of {len(sections)} sections)")
            lines.append("")

    for heading, ts, events in render_sections:
        if heading:
            lines.append(f"## {heading}")
        elif not events:
            continue
        else:
            lines.append("## (session start)")
        events_to_render = events
        elided_note = None
        if not opts.full and len(events) > ELIDE_THRESHOLD:
            elided_count = len(events) - ELIDE_HEAD - ELIDE_TAIL
            events_to_render = events[:ELIDE_HEAD]
            elided_note = elided_count
            tail_events = events[-ELIDE_TAIL:]
        else:
            tail_events = []

        for ev in events_to_render:
            if ev.kind == "compact":
                lines.append(ev.text)
            else:
                lines.append(f"{fmt_hhmm(ev.ts, opts.utc)}  {ev.text}")
        if elided_note is not None:
            lines.append(f"… {elided_note} events elided …")
            for ev in tail_events:
                if ev.kind == "compact":
                    lines.append(ev.text)
                else:
                    lines.append(f"{fmt_hhmm(ev.ts, opts.utc)}  {ev.text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

class Opts:
    def __init__(self, thinking=False, full=False, max_text=MAX_TEXT_DEFAULT, utc=False, last=None):
        self.thinking = thinking
        self.full = full
        self.max_text = max_text
        self.utc = utc
        self.last = last


def build_header(path, fmt, msgs, utc=False):
    timestamps = [m[0] for m in msgs if m[0] is not None]
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None
    duration = None
    if start is not None and end is not None:
        secs = int(end - start)
        h, rem = divmod(secs, 3600)
        mn, sec = divmod(rem, 60)
        duration = f"{h}h{mn:02d}m" if h else f"{mn}m{sec:02d}s"

    tz_label = _tz_label(utc)

    def fmt_ts(ts):
        if ts is None:
            return None
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if not utc:
            dt = dt.astimezone()
        return dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_label}")

    session_id = None
    if fmt == "transcript":
        session_id = os.path.splitext(os.path.basename(path))[0]
    else:
        session_id = os.path.basename(path.rstrip("/"))

    return {
        "title": os.path.basename(path.rstrip("/")),
        "source": path,
        "session_id": session_id,
        "start": fmt_ts(start),
        "end": fmt_ts(end),
        "duration": duration,
        "note": "final reply not included" if fmt == "capture_dir" and _no_final_reply(msgs) else None,
    }


def _no_final_reply(msgs):
    if not msgs:
        return False
    last = msgs[-1]
    return (last[1] == "system" and len(last[2]) == 1
            and last[2][0].get("text") == "(final reply not included)")


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("usage: digest_session.py <transcript.jsonl | capture_dir> "
              "[-o out.md] [--thinking] [--full] [--max-text N] [--utc] [--last N]",
              file=sys.stderr)
        return 0 if len(argv) >= 2 else 1
    path = argv[1]
    out_file = None
    thinking = False
    full = False
    max_text = MAX_TEXT_DEFAULT
    utc = False
    last = None

    args = argv[2:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-o":
            i += 1
            out_file = args[i] if i < len(args) else None
        elif a == "--thinking":
            thinking = True
        elif a == "--full":
            full = True
        elif a == "--max-text":
            i += 1
            try:
                max_text = int(args[i]) if i < len(args) else MAX_TEXT_DEFAULT
            except (ValueError, IndexError):
                pass
        elif a == "--utc":
            utc = True
        elif a == "--last":
            i += 1
            try:
                last = int(args[i]) if i < len(args) else None
            except (ValueError, IndexError):
                last = None
        i += 1

    fmt = detect_format(path)
    if fmt is None:
        print(f"error: {path!r} is neither a Claude Code transcript JSONL file "
              f"nor a wirescope capture directory", file=sys.stderr)
        return 1

    if fmt == "transcript":
        msgs = load_transcript(path)
    else:
        msgs = load_capture_dir(path)

    opts = Opts(thinking=thinking, full=full, max_text=max_text, utc=utc, last=last)
    sections, counts = events_from_messages(msgs, opts)
    header = build_header(path, fmt, msgs, utc=utc)
    text = render(sections, header, counts, opts)

    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
