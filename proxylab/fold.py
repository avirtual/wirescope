"""READ+EDIT FOLDING — the chunk-cache transform.

THE IDEA (proven on the wire, 2026-06-20). A file gets Read once, then Edited
in the SAME turn. Today the window carries, forever after, the raw Read buffer
(pre-edit, now STALE) PLUS the Edit's fat `old_string`/`new_string` input PLUS
its success-ack tool_result — and the model has to mentally replay the diff to
know the file's current shape. We instead apply the edit onto the Read buffer
deterministically and:

  * REPLACE the Read tool_result content with the file's POST-edit shape
    (re-numbered `cat -n`), so downstream turns read the final state directly;
  * STUB the now-redundant Edit `tool_use` input + its tool_result to tiny
    byte-stable constants (we do NOT drop the blocks — stubbing keeps
    tool_use/tool_result pairing and message structure trivially valid, the
    same conservative move the tool-error strip already makes).

WHY SAME-TURN ONLY (the load-bearing invariant). When the read and its edits
live in the same turn, the file's final shape is SETTLED the instant that turn
becomes history — no later edit can change what this read's folded content
should be. So a folded read_id's bytes are FINAL on first write and identical
every turn after -> the prefix stays warm. Folding a LATER-turn edit back into
an earlier read would rewrite already-settled (warm) history every time a new
edit landed -> a per-turn bust. We never reach back; later-turn edits (~19% in
clean coding) stay live. Measured feasibility (clean cohort): ~82% of edits
rebase cleanly; the rest just stay live (all-or-nothing per read chain).

WHY IT'S SAFE ON THE WIRE. The read-before-edit gate and the edit application
are CLIENT-side (`readFileState`) — the CLI already committed the real file
before we ever see the turn. Edit `tool_use`/`tool_result` blocks are NOT
signed (only `thinking` is), so rewriting them desyncs nothing the model
verifies. We never touch thinking/text blocks.

ECONOMICS / NO-FLAP. The fold is a DETERMINISTIC function of the CLI's
re-shipped original transcript, so the forwarded bytes for a settled pair are
identical every turn -> warm after the first send; it cannot oscillate the way
the old thinking-ratio guard did. The maps below are therefore PURE MEMO (skip
re-rebasing), not a correctness store: a restart drops them and the next turn
recomputes byte-identically. The ONE cost is the transition turn: when a pair
crosses from live (current turn) into history we fold it for the first time,
busting from that read's index. In steady state that index sits in the
just-settled turn at the TAIL -> cheap (the "~2x the settling turn" bargain we
already accepted for thinking-strip). First-enabling fold on a long ALREADY-warm
session eats one deeper re-cache — the accepted price of a deliberate per-session
opt-in (same stance as the L2 strips), not an automatic decision that needs the
cold-gate.

GATING — fold is part of STRIP LEVEL 2 (L2 = bust-riders + fold). It is NOT a
separate flag/table/directive: it rides the existing consumer-tiered strip
ladder (transforms.STRIP LEVELS), gated by `transforms._strip_l2_enabled`
(effective level >= 2). That intent already persists in `strip_override` and is
resolved EARLY in the chain (via `_strip_thinking_enabled`, before the directive
strip), so fold inherits the durable-across-restart override AND is immune to the
directive-before-strip race for free. Per-session: `[wirescope:strip-thinking
l2]` / `/_strip?level=2`; global default: STRIP_L2. (Fold moved L3->L2 2026-06-20
so it runs alongside the other L2 optimizations; L3 retired.) This module owns
NO table — only its in-memory MEMO maps (deterministically recomputable).

OWNERSHIP. Wired into the server transform chain after the strips, before the
hold echo. Gated by the L3 strip level.
"""
import json
import re

from proxylab import writer as writer_mod

# Only single-op Edit for v1. Write creates files (no prior read); MultiEdit /
# NotebookEdit carry a multi-op shape we deliberately leave live for now.
_FOLD_EDIT_NAMES = frozenset({"Edit"})

# Byte-stable stubs for a folded edit's two halves (envelope kept for pairing /
# API validity; only the heavy args/ack body is replaced). Constants => stable.
FOLD_CALL_STUB = {"_folded": "edit applied to the Read above"}
FOLD_RESULT_STUB = "ok"

# A `cat -n` line: optional leading pad, an integer, a TAB, then the content.
_NUM_LINE = re.compile(r"^\s*(\d+)\t(.*)$")

# session_id -> {read_tool_use_id: replacement_content_str}  (folded read bodies)
_FOLDED_READS = {}
# session_id -> {edit_tool_use_id: True}  (edits to stub, paired by id)
_FOLDED_EDITS = {}
# session_id -> set(read_tool_use_id) already examined (foldable OR not) — the
# memo that keeps discovery O(new settled pairs), not O(history) every turn.
_PROCESSED_READS = {}


def _forget(session_id):
    """Drop all per-session fold MEMO state. Called on sweep/end. (No override
    table to clear — the L3 intent lives in transforms' strip_override, reaped by
    _ws_forget / the strip machinery.)"""
    _FOLDED_READS.pop(session_id, None)
    _FOLDED_EDITS.pop(session_id, None)
    _PROCESSED_READS.pop(session_id, None)


# _clear_maps is an alias kept for the /compact path's intent (drop the memo so
# retired tool_ids don't linger); identical to _forget now that there's no
# separate override to preserve.
_clear_maps = _forget


# ---------------------------------------------------------------- buffer ops
def _result_text(blk):
    """The string body of a tool_result block across wire dialects (str, or a
    list of {type:text,text:…} blocks). None if not text-shaped."""
    c = blk.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            return "".join(parts)
    return None


def _parse_numbered(content):
    """Parse a `cat -n` Read body -> (start_lineno, file_text) or None if any
    non-empty line isn't numbered (an error read / non-file content -> skip).
    file_text is the raw file content with the trailing newline preserved."""
    if not isinstance(content, str) or "\t" not in content:
        return None
    lines = content.split("\n")
    trailing_nl = bool(lines) and lines[-1] == ""
    if trailing_nl:
        lines = lines[:-1]
    if not lines:
        return None
    start = None
    raws = []
    for ln in lines:
        m = _NUM_LINE.match(ln)
        if not m:
            return None
        if start is None:
            start = int(m.group(1))
        raws.append(m.group(2))
    text = "\n".join(raws) + ("\n" if trailing_nl else "")
    return start, text


def _render_numbered(start, text):
    """Re-emit file_text as `cat -n` lines numbered contiguously from `start`."""
    trailing_nl = text.endswith("\n")
    lines = text[:-1].split("\n") if trailing_nl else text.split("\n")
    body = "\n".join(f"{start + i}\t{ln}" for i, ln in enumerate(lines))
    return body + ("\n" if trailing_nl else "")


def _apply_edit(text, old, new, replace_all):
    """Apply one Edit's old->new onto file_text. Return the new text, or None if
    it can't fold cleanly (empty/absent old_string, or ambiguous without
    replace_all) -> caller aborts the whole read chain (all-or-nothing)."""
    if not old:
        return None
    cnt = text.count(old)
    if cnt == 0:
        return None
    if cnt > 1 and not replace_all:
        return None
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


# ---------------------------------------------------------------- discovery
def _error_edit_ids(msgs, last_user):
    """tool_use_ids (in the settled region) whose tool_result is is_error — a
    failed edit didn't change the real file, so we must NOT fold it."""
    ids = set()
    for i, m in enumerate(msgs):
        if i >= last_user or m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                ids.add(b.get("tool_use_id"))
    return ids


def _apply_to_buffers(bufs, old, new, replace_all):
    """Apply one Edit's old->new to EVERY live buffer of the file that contains
    old_string (the same file is often Read in several windows in one turn — the
    edited line can sit in more than one, e.g. overlapping reads). Keeping all
    containing windows in lock-step is what makes a multi-read file consistent
    after folding (one window post-edit, another pre-edit would mislead the
    model). Returns the number of buffers updated, or None if it CANNOT fold
    cleanly: an empty old_string, or an ambiguous match (old_string appears >1×
    in some window without replace_all). 0 hits is a clean "this edit's region
    isn't in any carried window" -> the caller treats that as unfoldable too
    (its diff would be lost), so only a positive count means folded."""
    if not old:
        return None
    hits = 0
    for buf in bufs:
        cnt = buf["text"].count(old)
        if cnt == 0:
            continue
        if cnt > 1 and not replace_all:
            return None                  # ambiguous in this window -> abort file
        buf["text"] = (buf["text"].replace(old, new) if replace_all
                       else buf["text"].replace(old, new, 1))
        hits += 1
    return hits


def _discover(msgs, last_user, folded_reads, folded_edits, processed, error_ids):
    """Walk the SETTLED region (msgs[:last_user]) turn by turn. For each file
    Read (one or MORE windows) and then Edited in the SAME turn, rebase every
    edit onto every window that contains it. ALL-OR-NOTHING PER FILE: the file's
    reads+edits fold as a unit only if every edit landed cleanly in >=1 window
    with no ambiguity and no failure — otherwise none of that file's blocks are
    touched (a partly-folded file could show inconsistent windows / a stubbed
    edit whose diff isn't reflected somewhere). Pure memo: a read examined once
    (in whichever pass first saw its turn settled) is never re-rebased."""
    cur = {}            # file_path -> {bufs:[{read_id,start,text}], edit_ids, ok}
    pending = {}        # read tool_use_id -> file_path (read awaiting its result)

    def commit():
        for g in cur.values():
            if g["ok"] and g["edit_ids"] and g["bufs"]:
                for buf in g["bufs"]:
                    folded_reads[buf["read_id"]] = _render_numbered(buf["start"], buf["text"])
                for eid in g["edit_ids"]:
                    folded_edits[eid] = True

    for i in range(last_user):
        m = msgs[i]
        if writer_mod_is_real_user_turn(m):
            commit()
            cur = {}
            pending = {}
        c = m.get("content")
        if not isinstance(c, list):
            continue
        role = m.get("role")
        if role == "assistant":
            for b in c:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                nm = b.get("name")
                fp = (b.get("input") or {}).get("file_path")
                if nm == "Read" and fp:
                    pending[b.get("id")] = fp
                elif nm in _FOLD_EDIT_NAMES and fp and fp in cur:
                    g = cur[fp]
                    if not g["ok"]:
                        continue
                    if b.get("id") in error_ids:     # failed edit -> abort file
                        g["ok"] = False
                        continue
                    inp = b.get("input") or {}
                    hits = _apply_to_buffers(g["bufs"], inp.get("old_string"),
                                             inp.get("new_string") or "",
                                             inp.get("replace_all"))
                    if not hits:                     # ambiguous (None) or no window
                        g["ok"] = False              # held its diff -> abort file
                    else:
                        g["edit_ids"].append(b.get("id"))
        elif role == "user":
            for b in c:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tid = b.get("tool_use_id")
                if tid not in pending:
                    continue
                fp = pending.pop(tid)
                if tid in processed:                 # already examined -> skip
                    continue
                processed.add(tid)
                parsed = _parse_numbered(_result_text(b))
                g = cur.get(fp)
                if parsed is None:
                    # unparseable window (error read) -> the file can't fold
                    # consistently; abort the whole file's cluster this turn.
                    if g is not None:
                        g["ok"] = False
                    else:
                        cur[fp] = {"bufs": [], "edit_ids": [], "ok": False}
                    continue
                start, text = parsed
                if g is None:
                    cur[fp] = {"bufs": [{"read_id": tid, "start": start, "text": text}],
                               "edit_ids": [], "ok": True}
                else:
                    g["bufs"].append({"read_id": tid, "start": start, "text": text})
    commit()


def _apply(msgs, folded_reads, folded_edits):
    """Uniform per-turn wire pass: replace folded read bodies, stub folded edit
    calls + their acks. O(blocks); time-agnostic (current-turn ids aren't in the
    maps, so the live tail is untouched). Returns (n_reads, n_calls, n_acks,
    chars_saved)."""
    n_reads = n_calls = n_acks = 0
    saved = 0
    for m in msgs:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            ty = b.get("type")
            if ty == "tool_result":
                tid = b.get("tool_use_id")
                if tid in folded_reads:
                    newc = folded_reads[tid]
                    old = b.get("content")
                    oldlen = len(old) if isinstance(old, str) else len(json.dumps(old, default=str))
                    if old != newc:
                        b["content"] = newc
                        n_reads += 1
                        saved += oldlen - len(newc)
                elif tid in folded_edits:
                    old = b.get("content")
                    if old != FOLD_RESULT_STUB:
                        oldlen = len(old) if isinstance(old, str) else len(json.dumps(old, default=str))
                        b["content"] = FOLD_RESULT_STUB
                        n_acks += 1
                        saved += oldlen - len(FOLD_RESULT_STUB)
            elif ty == "tool_use" and b.get("id") in folded_edits:
                old = b.get("input")
                if old != FOLD_CALL_STUB:
                    saved += len(json.dumps(old or {})) - len(json.dumps(FOLD_CALL_STUB))
                    b["input"] = dict(FOLD_CALL_STUB)
                    n_calls += 1
    return n_reads, n_calls, n_acks, saved


# `_is_real_user_turn` lives in transforms; import lazily to avoid a heavy
# import cycle at module load (transforms pulls in warmth/etc.). Bound once.
def writer_mod_is_real_user_turn(m):
    global _IRUT
    try:
        return _IRUT(m)
    except NameError:
        from proxylab import transforms as _t
        _IRUT = _t._is_real_user_turn
        return _IRUT(m)


def fold_read_edits(obj, agent_id=None):
    """Fold settled same-turn Read+Edit chains. Returns a log dict, or None when
    disabled (strip level < 2) / nothing to do. Mutates obj["messages"] in place.
    Gate = transforms._strip_l2_enabled (fold is part of strip level 2)."""
    if not isinstance(obj, dict):
        return None
    from proxylab import transforms as _t
    if not _t._strip_l2_enabled(obj, agent_id):
        return None
    sid = (writer_mod._session_ids(obj) or [None])[0]
    msgs = obj.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None

    # /compact retires the old tool_ids -> drop the memo maps.
    try:
        if _t._is_compact_request(obj):
            _clear_maps(sid)
    except Exception:
        pass

    folded_reads = _FOLDED_READS.setdefault(sid, {})
    folded_edits = _FOLDED_EDITS.setdefault(sid, {})
    processed = _PROCESSED_READS.setdefault(sid, set())

    last_user = max((i for i, m in enumerate(msgs)
                     if writer_mod_is_real_user_turn(m)), default=-1)
    if last_user > 0:
        error_ids = _error_edit_ids(msgs, last_user)
        _discover(msgs, last_user, folded_reads, folded_edits, processed, error_ids)

    if not folded_reads and not folded_edits:
        return None
    n_reads, n_calls, n_acks, saved = _apply(msgs, folded_reads, folded_edits)
    if not (n_reads or n_calls or n_acks):
        return None
    return {"folded": True, "folded_read_bodies": n_reads,
            "stubbed_edit_calls": n_calls, "stubbed_edit_acks": n_acks,
            "chars_saved": saved, "tracked_reads": len(folded_reads),
            "tracked_edits": len(folded_edits), "boundary_idx": last_user,
            "total_messages": len(msgs)}
