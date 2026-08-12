#!/usr/bin/env python3
"""views._call_ordinals: which API CALL first carried each message of a payload.

WHAT THIS SUITE IS FOR. The /_session block labels used to be the message's
INDEX in the payload array, which reads like a round-trip counter and is not
one — a dm and the SessionStart system message that arrived WITH it rendered as
`#0` and `#1`, i.e. as two API calls, when the wire carried them in one. These
cases assert the mapping index -> call, and each states the numbers it expects.

THE ASSERTION THAT MATTERS MOST is `test_equal_lengths_do_not_end_the_lineage`.
The walk runs newest-first and must stop when it crosses into a discarded
lineage (a /clear or compact restarts the message count). The tempting break is
"stop when the count stops decreasing" — and it is WRONG, because a retry or a
turn that grew nothing legitimately repeats a length. Measured on the live
corpus, that version truncated 5 of 53 sessions to a single call (one lost 73
calls) while still looking well-formed: every ordinal it DID emit was 1, which
is self-consistent and useless. Only a coverage assertion catches it, so every
case here pins counts, never just shape.

Fixture shape: _call_ordinals reads ONLY the tail `summary` of *.request.json
captures, so a fixture is a directory of tiny JSON files — no bodies, no proxy,
no network. mtime is set explicitly: the walk orders by it.
"""
import json, os, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logproxy as lp                                    # noqa: E402  (ordered boot)
from proxylab import core as core_mod, views as views_mod  # noqa: E402

PASS = FAIL = 0


def ck(desc, got, want):
    global PASS, FAIL
    if got == want:
        print(f"  ok   — {desc}"); PASS += 1
    else:
        print(f"  FAIL — {desc}\n         got {got!r}, want {want!r}"); FAIL += 1


def pre(desc, cond):
    """A case that cannot reach the regime it means to probe must SAY SO."""
    global FAIL
    if not cond:
        print(f"  BROKEN PRECONDITION — {desc}"); FAIL += 1
        return False
    return True


class Fixture:
    """A capture dir of request records carrying only the tail `summary` the
    walk reads. `calls(ns)` appends a run of main-line turns of those lengths;
    calling it again with lower counts models a /clear or compact."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="callord-"))
        self.sid = "11111111-2222-3333-4444-555555555555"
        self.dir = self.root / self.sid
        self.dir.mkdir(parents=True)
        self.seq = 0
        self.mtime = 1_000_000.0
        self._prev_log_dir = core_mod.LOG_DIR
        core_mod.LOG_DIR = self.root

    def _write(self, summary):
        self.seq += 1
        self.mtime += 10
        stem = f"{self.seq:04d}-a-x-m-000000"
        p = self.dir / f"{stem}.request.json"
        # `ts` first (the head-read), `summary` last (the tail-read) — the real
        # capture layout both cheap reads depend on.
        p.write_text(json.dumps({"ts": "2026-08-12T00:00:00", "seq": self.seq,
                                 "body": {"messages": []},
                                 "summary": summary}))
        os.utime(p, (self.mtime, self.mtime))
        return stem

    def calls(self, ns, role="parent", n_tools=5):
        return [self._write({"role": role, "n_messages": n, "n_tools": n_tools})
                for n in ns]

    def close(self):
        core_mod.LOG_DIR = self._prev_log_dir
        shutil.rmtree(self.root, ignore_errors=True)


def test_one_call_carrying_two_messages_is_one_call():
    """THE MOTIVATING BUG. A dm plus the SessionStart system message that came
    with it are ONE round trip; the old label made them look like two."""
    f = Fixture()
    try:
        f.calls([2, 4, 6])
        o = views_mod._call_ordinals(f.sid)
        ck("3 calls found", len(o["calls"]), 3)
        ck("messages 0 AND 1 both debut on call 1",
           [o["debut"][0], o["debut"][1]], [1, 1])
        ck("the pair added by call 2 maps to call 2",
           [o["debut"][2], o["debut"][3]], [2, 2])
        ck("every message of the final payload is attributed",
           sorted(o["debut"]), [0, 1, 2, 3, 4, 5])
    finally:
        f.close()


def test_equal_lengths_do_not_end_the_lineage():
    """THE REGRESSION GUARD. Retries and payload-neutral turns repeat a message
    count. Breaking the backward walk on 'stopped decreasing' truncates the map
    to a single call — self-consistent, fully monotonic, and wrong."""
    f = Fixture()
    try:
        ns = [2, 4, 4, 6, 8, 8, 10]
        f.calls(ns)
        if not pre("the fixture really does repeat a length (else this case "
                   "cannot probe the break)", len(set(ns)) < len(ns)):
            return
        o = views_mod._call_ordinals(f.sid)
        ck("all 7 calls survive the walk", len(o["calls"]), len(ns))
        ck("the last message debuts on the last call", o["debut"][9], 7)
        ck("no message is left unattributed", len(o["debut"]), 10)
    finally:
        f.close()


def test_after_compact_only_the_live_lineage_counts():
    """A compact leaves a LONGER dead lineage behind a SHORTER live one. Call
    numbers must restart with the live run, or turn 1 of the current
    conversation gets numbered in the hundreds."""
    f = Fixture()
    try:
        f.calls([2, 4, 6, 100, 200])            # pre-compact, discarded
        f.calls([2, 4, 6])                      # post-compact, live
        o = views_mod._call_ordinals(f.sid)
        ck("only the live lineage's calls are counted", len(o["calls"]), 3)
        ck("the live run starts at call 1", o["debut"][0], 1)
        ck("the dead lineage's length is gone",
           max(c["n"] for c in o["calls"]), 6)
    finally:
        f.close()


def test_subagent_captures_never_shift_the_numbering():
    """Subagents share the parent's session_id but carry their OWN message
    array. Counting them would renumber every main-line call after them."""
    f = Fixture()
    try:
        f.calls([2, 4])
        f.calls([3, 3, 3], role="general-purpose")   # a subagent's own history
        f.calls([6])
        o = views_mod._call_ordinals(f.sid)
        ck("only the 3 main-line calls count", len(o["calls"]), 3)
        ck("the message added by the 3rd main call says call 3", o["debut"][5], 3)
    finally:
        f.close()


def test_utility_side_calls_are_not_round_trips_of_this_conversation():
    """The CLI's title probe and the WebFetch/WebSearch summarizers are
    one-shot calls with their own tiny payload and no cache lineage. They are
    real API calls but not calls of THIS conversation, and counting them
    renumbers everything after them."""
    f = Fixture()
    try:
        f.calls([2, 4])
        f.calls([1], n_tools=0)                 # WebFetch summarize: 0 tools
        f.calls([1], n_tools=1)                 # WebSearch: 1 tool
        f.calls([6])
        o = views_mod._call_ordinals(f.sid)
        ck("side-calls excluded from the count", len(o["calls"]), 3)
        ck("the turn after them is call 3, not call 5", o["debut"][5], 3)
    finally:
        f.close()


def test_from_stem_clamps_to_the_rendered_turn():
    """Viewing an OLD turn in the navigator must not number it against calls
    that had not happened yet."""
    f = Fixture()
    try:
        stems = f.calls([2, 4, 6, 8, 10])
        o = views_mod._call_ordinals(f.sid, from_stem=stems[2])
        ck("only calls up to the rendered one", len(o["calls"]), 3)
        ck("the rendered turn's newest message belongs to call 3",
           o["debut"][5], 3)
        ck("nothing from the later calls leaks in", max(o["debut"]), 5)
    finally:
        f.close()


def test_carriage_multiplier_counts_the_calls_that_re_sent_it():
    """The label's `xN` is the carriage fact: a message debuting on call 1 of 5
    has been paid for on all 5; one debuting on call 5 only once."""
    ck("first message rides every call", views_mod._call_label(0, {0: 1}, 5),
       '<span title="first sent on API call 1 of 5 · payload index 0">call 1'
       '</span> <span class="dim" title="re-sent on 5 API calls since (every '
       'one paid to carry it)">&times;5</span>')
    ck("a message from the newest call shows no multiplier",
       "&times;" in views_mod._call_label(9, {9: 5}, 5), False)


def _visible(html):
    """The text a reader actually SEES — tags stripped, tooltips dropped. The
    tooltip legitimately names the carrying call ('ships back as input on call
    3'), so a raw substring check over the whole label cannot tell a correct
    tooltip from a wrong headline."""
    import re
    return re.sub(r"<[^>]*>", "", html).strip()


def _u(*kinds):
    """A user message whose content blocks are of the given types."""
    return {"role": "user", "content": [{"type": k} for k in kinds]}


def _a():
    return {"role": "assistant", "content": [{"type": "text", "text": "x"}]}


def test_assistant_blocks_are_dated_by_their_emitter_not_their_carriage():
    """Bogdan's catch. Every block first hits the wire in the REQUEST of some
    call, which is what `debut` measures — but an assistant message was
    PRODUCED as the response to the PREVIOUS call and only ships back as input
    here. Wire-proven: capture 058's response emitted 2 Bash tool_use at
    12:29:13, and 059's request carries that same message at index 6 at
    12:29:30. Labelling it `call 3` dates it 17 s late and credits the wrong
    round trip."""
    lab = views_mod._call_label(6, {6: 3}, 5, role="assistant")
    ck("an assistant block reads as the response that emitted it",
       _visible(lab).startswith("reply 2"), True)
    ck("...and NOT as the call that merely re-carried it",
       "call 3" in _visible(lab), False)
    ck("the carrying call stays in the tooltip — it is still a true fact",
       "on call 3 of 5" in lab, True)
    ck("the carriage multiplier survives — it was always per-call",
       "&times;3" in lab, True)
    ck("a user block on the same call still reads as that call",
       _visible(views_mod._call_label(7, {7: 3}, 5, role="user")
                ).startswith("call 3"), True)


def test_assistant_on_the_first_captured_call_has_no_emitter_to_name():
    """A resumed or swept session's payload opens with assistant turns whose
    emitting response predates the lineage. `reply 0` would be a lie; the label
    must fall back to the carriage fact and say which fact it is stating."""
    lab = views_mod._call_label(1, {1: 1}, 4, role="assistant")
    ck("no reply-0 is invented", "reply 0" in lab, False)
    ck("falls back to the carriage form", "call 1" in lab, True)
    ck("and the tooltip says the emitter is off-lineage",
       "predates the captured lineage" in lab, True)


def test_model_initiated_calls_are_distinguished_from_operator_ones():
    """THE DECOMPOSITION. One request shape, two causes: a call triggered by a
    real user turn is one the operator asked for; a call triggered by
    tool_result blocks is one the MODEL's own previous response demanded by
    emitting tool_use. Both re-carry the whole window at full price."""
    msgs = [_u("text"),                  # call 1 — operator
            _a(),
            _u("tool_result"),           # call 2 — model asked for it
            _a(),
            _u("tool_result", "tool_result"),   # call 3 — model again
            _a(),
            _u("text")]                  # call 4 — operator
    debut = {0: 1, 1: 2, 2: 2, 3: 3, 4: 3, 5: 4, 6: 4}
    c = views_mod._call_causes(msgs, debut, 4)["cause"]
    if not pre("the fixture really mixes both causes (else nothing is probed)",
               len(set(c.values())) == 2):
        return
    ck("every call is attributed", sorted(c), [1, 2, 3, 4])
    ck("the causes are read off the trigger message",
       [c[k] for k in (1, 2, 3, 4)],
       ["operator", "model", "model", "operator"])
    ck("2 of 4 round trips were the model's own idea",
       sum(1 for v in c.values() if v == "model"), 2)


def test_the_trigger_is_the_last_user_message_the_call_introduced():
    """A call can debut several messages. The one that TRIGGERED it is the last
    USER message — 'last user' and not 'last message', because the opus-4.8
    wire ships a trailing mid-conversation `role:"system"` roster that debuts
    after the trigger without being one."""
    msgs = [_u("text"),
            _a(),
            _u("tool_result"),
            {"role": "system", "content": "agent roster"}]
    debut = {0: 1, 1: 2, 2: 2, 3: 2}
    got = views_mod._call_causes(msgs, debut, 2)
    if not pre("the trailing system message really does debut on the call it "
               "must not be credited with", debut[3] == 2):
        return
    ck("the system roster does not become the trigger",
       got["trigger"].get(3), None)
    ck("the tool_result does, so the call reads as model-initiated",
       got["cause"][2], "model")
    ck("and it is the block that carries the cause mark",
       got["trigger"].get(2), 2)


def test_a_call_that_added_no_user_message_is_not_guessed_at():
    """A retry re-sends an identical payload and debuts nothing. Assigning it a
    cause anyway would invent an operator turn that never happened, so it is
    simply absent — and operator+model may total less than the call count."""
    msgs = [_u("text"), _a()]
    c = views_mod._call_causes(msgs, {0: 1, 1: 2}, 3)["cause"]
    ck("only the calls with a known trigger are classified", sorted(c), [1])
    ck("the unattributed call is omitted, not defaulted", c.get(3), None)


def test_tool_result_only_detection_is_not_fooled_by_mixed_content():
    """The CLI can attach a system-reminder alongside a tool_result. A message
    carrying ANY real text is something a human could have said, so it must not
    be silently reclassified as the model talking to itself."""
    ck("pure tool_result", views_mod._is_tool_result_only(_u("tool_result")),
       True)
    ck("tool_result plus text is NOT model-only",
       views_mod._is_tool_result_only(_u("tool_result", "text")), False)
    ck("plain text is not", views_mod._is_tool_result_only(_u("text")), False)
    ck("a string-content message is not",
       views_mod._is_tool_result_only({"role": "user", "content": "hi"}), False)
    ck("an empty message is not",
       views_mod._is_tool_result_only({"role": "user", "content": []}), False)


def test_cause_marks_only_the_triggering_block():
    """The cause is a fact about the CALL. Stamping it on every block would
    repeat it once per tool_result in a fan-out message."""
    trig = views_mod._call_label(4, {4: 3}, 5, role="user", cause="model")
    plain = views_mod._call_label(4, {4: 3}, 5, role="user", cause=None)
    ck("the trigger block carries the model-initiated mark",
       "&#8635;" in trig, True)
    ck("a non-trigger block on the same call does not",
       "&#8635;" in plain or "&#9679;" in plain, False)
    ck("an operator trigger is marked distinctly",
       "&#9679;" in views_mod._call_label(0, {0: 1}, 5, role="user",
                                          cause="operator"), True)


def test_unattributable_message_falls_back_to_the_index():
    """A swept capture dir must degrade the LABEL, never the page: the bare
    payload index is exactly what /_session rendered before."""
    f = Fixture()
    try:
        ck("absent capture dir yields an empty map",
           views_mod._call_ordinals("99999999-0000-0000-0000-000000000000"),
           {"debut": {}, "calls": []})
        ck("a label with no ordinal renders the index",
           "#7" in views_mod._call_label(7, {}, 0), True)
    finally:
        f.close()


def test_malformed_captures_are_skipped_not_fatal():
    f = Fixture()
    try:
        f.calls([2, 4])
        (f.dir / "9000-x.request.json").write_text("{not json")
        o = views_mod._call_ordinals(f.sid)
        ck("unparseable capture ignored, real calls still found",
           len(o["calls"]), 2)
    finally:
        f.close()


if __name__ == "__main__":
    for t in (test_one_call_carrying_two_messages_is_one_call,
              test_equal_lengths_do_not_end_the_lineage,
              test_after_compact_only_the_live_lineage_counts,
              test_subagent_captures_never_shift_the_numbering,
              test_utility_side_calls_are_not_round_trips_of_this_conversation,
              test_from_stem_clamps_to_the_rendered_turn,
              test_carriage_multiplier_counts_the_calls_that_re_sent_it,
              test_assistant_blocks_are_dated_by_their_emitter_not_their_carriage,
              test_assistant_on_the_first_captured_call_has_no_emitter_to_name,
              test_model_initiated_calls_are_distinguished_from_operator_ones,
              test_the_trigger_is_the_last_user_message_the_call_introduced,
              test_a_call_that_added_no_user_message_is_not_guessed_at,
              test_tool_result_only_detection_is_not_fooled_by_mixed_content,
              test_cause_marks_only_the_triggering_block,
              test_unattributable_message_falls_back_to_the_index,
              test_malformed_captures_are_skipped_not_fatal):
        print(f"=== {t.__name__} ===")
        t()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
