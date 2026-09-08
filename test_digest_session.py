#!/usr/bin/env python3
"""digest_session.py: mechanical no-LLM session summarizer, behaviour tests.

Same style as test_tool_use_view.py: plain functions, a ck() helper,
PASS/FAIL counters, run directly (not pytest). Builds tiny synthetic
transcripts / capture dirs under the scratchpad and asserts on the
RENDERED digest text — never on internal call shapes — so these tests
catch a regression in what a human actually reads.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import digest_session as ds  # noqa: E402

PASS = FAIL = 0

SCRATCH_BASE = Path(
    "/private/tmp/claude-501/-Users-bogdan-projects-proxy-lab/"
    "12a5474d-5d7b-43f3-bddf-37f5303085ed/scratchpad"
)


def ck(name, got, want=True):
    global PASS, FAIL
    ok = (got == want)
    PASS += ok
    FAIL += (not ok)
    print(("  ok  " if ok else "  FAIL") + f" {name}" + ("" if ok else f"  got={got!r} want={want!r}"))


def workdir():
    base = SCRATCH_BASE if SCRATCH_BASE.parent.exists() else Path(tempfile.gettempdir())
    d = Path(tempfile.mkdtemp(prefix="digest_test_", dir=str(base)))
    return d


def user_msg(text, ts="2026-09-08T01:00:00.000Z", uuid_="u1", extra=None):
    obj = {"parentUuid": None, "type": "user", "message": {"role": "user", "content": text},
           "uuid": uuid_, "timestamp": ts, "sessionId": "s1"}
    if extra:
        obj.update(extra)
    return obj


def assistant_msg(content_blocks, ts="2026-09-08T01:00:05.000Z", uuid_="a1"):
    return {"parentUuid": None, "type": "assistant",
            "message": {"role": "assistant", "content": content_blocks},
            "uuid": uuid_, "timestamp": ts, "sessionId": "s1"}


def write_transcript(lines):
    d = workdir()
    p = d / "session.jsonl"
    with open(p, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return p


def run_digest(path, *extra_args):
    argv = ["digest_session.py", str(path)] + list(extra_args)
    out_path = Path(str(path)) if os.path.isdir(path) else path
    out_file = out_path.parent / "out.md" if hasattr(out_path, "parent") else Path("out.md")
    argv += ["-o", str(out_file)]
    rc = ds.main(argv)
    text = out_file.read_text() if out_file.exists() else ""
    return rc, text


# ------------------------------------------------------------------

def test_prompt_heading_appears_and_reminder_only_message_does_not():
    lines = [
        user_msg("<system-reminder>boot stuff</system-reminder>", uuid_="u0"),
        user_msg("Please fix the login bug", uuid_="u1"),
        assistant_msg([{"type": "text", "text": "Sure, looking into it."}], uuid_="a1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("exit code 0", rc, 0)
    ck("real prompt becomes a heading", "## Please fix the login bug" in text)
    ck("reminder-only message is not a heading",
        "## <system-reminder>boot stuff</system-reminder>" not in text)


def test_edit_shows_path_not_content():
    lines = [
        user_msg("edit the file please", uuid_="u1"),
        assistant_msg([
            {"type": "tool_use", "id": "t1", "name": "Edit",
             "input": {"file_path": "/a/b.py", "old_string": "x" * 5, "new_string": "y" * 12}},
        ], uuid_="a1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("edit line shows the path", "/a/b.py" in text)
    ck("edit line shows a size delta", "+12/-5" in text)
    ck("old_string content not leaked", "xxxxx" not in text)
    ck("new_string content not leaked", "y" * 12 not in text)


def test_write_shows_path_and_line_count_not_content():
    lines = [
        user_msg("write a file", uuid_="u1"),
        assistant_msg([
            {"type": "tool_use", "id": "t1", "name": "Write",
             "input": {"file_path": "/tmp/out.txt", "content": "line1\nline2\nline3\nSECRETVALUE"}},
        ], uuid_="a1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("write line shows path", "/tmp/out.txt" in text)
    ck("write line shows line count", "(4 lines)" in text)
    ck("write content not leaked", "SECRETVALUE" not in text)


def test_dm_to_and_from_lines():
    lines = [
        user_msg("[agent:from wirescope] Message (523 bytes) attached: @/x.txt", uuid_="u1"),
        assistant_msg([
            {"type": "text", "text": "Some analysis text.\n\n[agent:dm wirescope]\nHere is my reply body.\n[agent:end]"},
        ], uuid_="a1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("dm-from line rendered", "← dm from wirescope (523 bytes, attached file)" in text)
    ck("dm-to line rendered", "→ dm to wirescope: Here is my reply body." in text)
    ck("bare end marker is dropped as noise", "[agent:end]" not in text)
    ck("intent body text not duplicated as plain prose",
        text.count("Here is my reply body.") == 1)


def test_ticket_close_line():
    lines = [
        user_msg("close the ticket", uuid_="u1"),
        assistant_msg([
            {"type": "text", "text": "Done.\n\n[agent:task done t762]\n[agent:end]"},
        ], uuid_="a1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("ticket close line rendered", "✔ ticket t762 closed" in text)
    ck("ticket counted in header", "Tickets touched" in text and "t762" in text)


def test_ticket_rejected_from_user_line():
    lines = [
        user_msg("[agent:from clodex] [ticket t762 rejected] close with [agent:task done t762] "
                  "Message (900 bytes) attached: @/x.txt", uuid_="u1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("ticket rejected line rendered", "ticket t762 rejected" in text)
    ck("attached dm-from line also rendered", "← dm from clodex" in text)


def test_error_line_rendered():
    lines = [
        user_msg("run the tests", uuid_="u1"),
        assistant_msg([
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}},
        ], uuid_="a1"),
        user_msg([{"type": "tool_result", "tool_use_id": "t1",
                    "content": "ImportError: no module named foo", "is_error": True}], uuid_="u2"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("tool error rendered with warning marker", "⚠" in text and "ImportError" in text)


def test_large_result_flagged():
    big = "x" * 25000
    lines = [
        user_msg("grep everything", uuid_="u1"),
        assistant_msg([
            {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "foo"}},
        ], uuid_="a1"),
        user_msg([{"type": "tool_result", "tool_use_id": "t1", "content": big}], uuid_="u2"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("large result size noted", "large result" in text)
    ck("large result content not dumped", big not in text)


def test_elision_default_and_full():
    lines = [user_msg("do a lot of things", uuid_="u0")]
    for i in range(80):
        lines.append(assistant_msg([
            {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": f"/f/{i}.py"}},
        ], uuid_=f"a{i}"))
        lines.append(user_msg([{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}],
                               uuid_=f"u{i}"))
        # break up consecutive Read calls so they don't collapse into one run
        lines.append(assistant_msg([{"type": "text", "text": f"step {i} done"}], uuid_=f"b{i}"))

    path = write_transcript(lines)
    rc, text_default = run_digest(path)
    ck("default output elides", "events elided" in text_default)

    rc, text_full = run_digest(path, "--full")
    ck("--full disables elision", "events elided" not in text_full)
    ck("--full is longer than default", len(text_full) > len(text_default))


def test_thinking_hidden_by_default_and_shown_with_flag():
    lines = [
        user_msg("think about it", uuid_="u1"),
        assistant_msg([
            {"type": "thinking", "thinking": "This is my secret internal reasoning about the bug."},
            {"type": "text", "text": "Here is my answer."},
        ], uuid_="a1"),
    ]
    path = write_transcript(lines)
    rc, text_default = run_digest(path)
    ck("thinking hidden by default", "secret internal reasoning" not in text_default)

    rc, text_thinking = run_digest(path, "--thinking")
    ck("thinking shown with --thinking", "secret internal reasoning" in text_thinking)
    ck("thinking line is prefixed", "(thinking)" in text_thinking)


def test_capture_dir_format_detected_and_rendered():
    d = workdir()
    cap_dir = d / "capdir"
    cap_dir.mkdir()
    body = {
        "model": "claude-opus-5",
        "tools": [{"name": "Bash"}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Hello there, please help"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, on it."}]},
            {"role": "system", "content": "The user hasn't heard from you in a while"},
        ],
        "system": [{"type": "text", "text": "sys prompt"}],
    }
    req = {"seq": 1, "ts": "2026-09-08T01:00:00.000Z", "agent": "trader",
           "summary": {"role": "parent", "agent_id": None, "n_messages": 3}, "body": body}
    req_name = "001-trader-abc-parent-opus-5-010000.request.json"
    (cap_dir / req_name).write_text(json.dumps(req))

    fmt = ds.detect_format(str(cap_dir))
    ck("capture dir format detected", fmt, "capture_dir")

    rc, text = run_digest(cap_dir)
    ck("capture dir digest exit 0", rc, 0)
    ck("capture dir prompt heading rendered", "## Hello there, please help" in text)
    ck("capture dir system notice counted", "System notices" in text)
    ck("capture dir note about missing reply present or reply included",
        ("final reply not included" in text) or ("Sure, on it." in text))


def _write_capture_req(cap_dir, seq, ts, messages, role="parent", agent="clodex",
                        agent_id="abc", model="opus-5", hhmmss="010000"):
    body = {"model": model, "messages": messages, "system": [{"type": "text", "text": "sys"}],
            "tools": [{"name": "Bash"}]}
    req = {"seq": seq, "ts": ts, "agent": agent,
           "summary": {"role": role, "agent_id": None, "n_messages": len(messages)},
           "body": body}
    name = f"{seq:03d}-{agent}-{agent_id}-{role}-{model}-{hhmmss}.request.json"
    (cap_dir / name).write_text(json.dumps(req))
    return name


def test_capture_walk_tolerates_settled_history_rewrites():
    """The CLI/proxy rewrite settled history in ways that are NOT edits:
    attachment <system-reminder> blocks vanish, a trailing role:system tail
    hint is not persisted, and a tool-less side-call carries no history.
    None of these may read as a compact."""
    d = workdir()
    cap_dir = d / "capdir4"
    cap_dir.mkdir()
    rem = {"type": "text", "text": "<system-reminder>\nmemory store stuff\n</system-reminder>"}
    u1 = {"role": "user", "content": [{"type": "text", "text": "prompt one"}, rem]}
    a1 = {"role": "assistant", "content": [{"type": "text", "text": "reply one"}]}
    tail = {"role": "system", "content": "First privately list what you need next"}
    _write_capture_req(cap_dir, 1, "2026-09-08T01:00:00.000Z", [u1, tail], hhmmss="010000")
    u1s = {"role": "user", "content": "prompt one\n"}
    u2 = {"role": "user", "content": [{"type": "text", "text": "prompt two"}]}
    _write_capture_req(cap_dir, 2, "2026-09-08T01:05:00.000Z", [u1s, a1, u2], hhmmss="010500")
    # a title side-call: no tools, one message -- must be skipped, not walked
    name = _write_capture_req(cap_dir, 3, "2026-09-08T01:06:00.000Z",
                              [{"role": "user", "content": "title this"}], hhmmss="010600")
    req = json.loads((cap_dir / name).read_text())
    del req["body"]["tools"]
    (cap_dir / name).write_text(json.dumps(req))
    a2 = {"role": "assistant", "content": [{"type": "text", "text": "reply two"}]}
    _write_capture_req(cap_dir, 4, "2026-09-08T01:07:00.000Z", [u1s, a1, u2, a2], hhmmss="010700")

    rc, text = run_digest(cap_dir, "--utc", "--full")
    ck("tolerant walk exits 0", rc, 0)
    ck("no compact marker from reminder/tail/side-call churn", "context compacted" not in text)
    ck("side-call prompt never rendered", "title this" not in text)
    ck("prompt one heading once", text.count("## prompt one"), 1)
    ck("prompt two heading once", text.count("## prompt two"), 1)
    ck("reply two stamped by its debut request", "01:07  reply two" in text)


def test_inline_peer_message_body_is_not_a_prompt():
    lines = [
        user_msg("do the thing", uuid_="u1"),
        assistant_msg([{"type": "text", "text": "on it"}], uuid_="a1"),
        user_msg("[agent:from ticket-loop] [ticket t99 MERGED] branch -> master as abc\n"
                 "Review rounds: 1. Suite on master after the merge: green.\n"
                 "CHANGELOG.md was CHANGED by this merge.", uuid_="u2"),
        user_msg("<task-notification>\n<task-id>a41d0</task-id>\n<output-file>/x/y.md</output-file>\n"
                 "</task-notification>", uuid_="u3"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path, "--utc")
    ck("peer digest exits 0", rc, 0)
    ck("dm from line rendered", "← dm from ticket-loop" in text)
    ck("ticket merge event rendered", "ticket t99 MERGED" in text)
    ck("dm body is not a heading", "## Review rounds" not in text)
    ck("only the real prompt is a heading", text.count("\n## "), 1)
    ck("subagent notification is a line, not a heading",
       "← subagent finished (a41d0)" in text and "## <task-notification>" not in text)


def test_local_command_echo_and_teammate_notice_are_lines_not_headings():
    lines = [
        user_msg("real question", uuid_="u1"),
        user_msg("<local-command-caveat>Caveat: generated while running local commands</local-command-caveat>",
                 uuid_="u2"),
        user_msg("<command-name>/compact</command-name>\n<command-message>compact</command-message>", uuid_="u3"),
        user_msg("<local-command-stdout>Compacted</local-command-stdout>", uuid_="u4"),
        user_msg("/compact", uuid_="u5"),
        user_msg("Another Claude session sent a message:\n<teammate-message teammate_id=\"fixer\" color=\"blue\">\n"
                 "{\"type\":\"idle_notification\",\"idleReason\":\"failed\",\"failureReason\":\"Prompt is too long\"}"
                 "\n</teammate-message>", uuid_="u6"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path, "--utc")
    ck("echo digest exits 0", rc, 0)
    ck("only the real prompt is a heading", text.count("\n## "), 1)
    ck("slash command noted as a line", "local command /compact" in text)
    ck("teammate failure noted with its reason", "← subagent fixer failed: Prompt is too long" in text)


def test_capture_dir_chronological_walk_across_compact():
    d = workdir()
    cap_dir = d / "capdir3"
    cap_dir.mkdir()

    m1 = {"role": "user", "content": [{"type": "text", "text": "first prompt"}]}
    m2 = {"role": "assistant", "content": [{"type": "text", "text": "first reply"}]}
    m3 = {"role": "user", "content": [{"type": "text", "text": "second prompt"}]}
    m4 = {"role": "assistant", "content": [{"type": "text", "text": "second reply"}]}
    # request 1: history so far = [m1, m2]
    _write_capture_req(cap_dir, 1, "2026-09-08T01:00:00.000Z", [m1, m2], hhmmss="010000")
    # request 2: extends with [m3, m4] -- new messages timestamped at THIS request's ts
    _write_capture_req(cap_dir, 2, "2026-09-08T01:05:00.000Z", [m1, m2, m3, m4], hhmmss="010500")
    # request 3: history is SHORTER / diverges -- a compact/reset. Fresh start.
    m5 = {"role": "user", "content": [{"type": "text", "text": "post-compact prompt"}]}
    _write_capture_req(cap_dir, 3, "2026-09-08T05:00:00.000Z", [m5], hhmmss="050000")

    rc, text = run_digest(cap_dir)
    ck("capture dir chronological walk exits 0", rc, 0)
    ck("first prompt heading present", "## first prompt" in text)
    ck("second prompt heading present", "## second prompt" in text)
    ck("post-compact prompt heading present", "## post-compact prompt" in text)
    ck("compact marker rendered", "context compacted" in text)
    ck("span is measured in hours across the reset (full session, not just the tail)",
        "h" in text.split("(")[1].split(")")[0])


def test_capture_dir_noise_lines_dropped():
    d = workdir()
    cap_dir = d / "capdir4"
    cap_dir.mkdir()
    messages = [
        {"role": "user", "content": "do a thing"},
        {"role": "assistant", "content": "[agent:task list]\n\nchecking the board"},
        {"role": "user", "content": "[agent:task] tickets on clodex:\nt1 [open] hand 1d — spec…"},
        {"role": "assistant", "content": "[agent:remind list]"},
        {"role": "user", "content": "[agent:remind] 1 reminder(s):\n  abc123  in 5m — ping"},
        {"role": "assistant", "content": "wrapping up now\n\n[agent:end]"},
    ]
    _write_capture_req(cap_dir, 1, "2026-09-08T01:00:00.000Z", messages)
    rc, text = run_digest(cap_dir)
    ck("noise: bare task list dropped", "task list" not in text)
    ck("noise: tickets-on board dump dropped", "tickets on clodex" not in text)
    ck("noise: bare remind list dropped", "remind list" not in text)
    ck("noise: reminder-count header dropped", "reminder(s)" not in text)
    ck("noise: bare agent:end dropped", "[agent:end]" not in text)
    ck("real content survives", "checking the board" in text and "wrapping up now" in text)


def test_ticket_host_confirmation_not_duplicate_of_close():
    lines = [
        user_msg("close the ticket", uuid_="u1"),
        assistant_msg([
            {"type": "text", "text": "Done.\n\n[agent:task done t762] finished the work\n[agent:end]"},
        ], uuid_="a1"),
        user_msg("[agent:task] ticket t762 closed (done) — report delivered", uuid_="u2"),
        user_msg("[agent:task] ticket t900 respec'd → hand", uuid_="u3"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("agent's own close line rendered", "✔ ticket t762 closed" in text)
    ck("host confirmation rendered distinctly, not as a second close",
        "ticket t762 close confirmed" in text)
    ck("only one '✔ ticket t762 closed' line", text.count("✔ ticket t762 closed") == 1)
    ck("other host verb tagged as host", "ticket t900 respec (host)" in text)


def test_compact_marker_is_plain_line_not_heading():
    lines = [
        user_msg("do a thing", uuid_="u1"),
        assistant_msg([{"type": "text", "text": "working on it"}], uuid_="a1"),
        {"parentUuid": None, "type": "system", "subtype": "compact_boundary",
         "content": "Conversation compacted", "uuid": "s1",
         "timestamp": "2026-09-08T02:00:00.000Z", "sessionId": "s1"},
        user_msg("This session is being continued from a previous conversation. Summary: did stuff.",
                  uuid_="u2"),
        assistant_msg([{"type": "text", "text": "continuing"}], uuid_="a2"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("compact marker rendered as a plain line, not a heading",
        "## ── context compacted ──" not in text and "── context compacted ──" in text)
    ck("continuation summary becomes its own heading",
        "## This session is being continued" in text)


def test_local_time_default_vs_utc():
    lines = [
        user_msg("do a thing", uuid_="u1", ts="2026-09-08T12:00:00.000Z"),
        assistant_msg([{"type": "text", "text": "ok"}], uuid_="a1", ts="2026-09-08T12:00:05.000Z"),
    ]
    path = write_transcript(lines)
    rc, text_local = run_digest(path)
    rc, text_utc = run_digest(path, "--utc")
    ck("default digest runs fine", rc, 0)
    ck("utc digest runs fine", rc, 0)
    ck("span line names a zone (default)", "Span:" in text_local and "(" in text_local)
    ck("span line names UTC explicitly with --utc", "UTC" in text_utc)


def test_last_n_limits_rendered_sections():
    lines = [user_msg("prompt one", uuid_="u0")]
    for i in range(5):
        lines.append(user_msg(f"prompt {i}", uuid_=f"u{i}",
                               ts=f"2026-09-08T0{i+1}:00:00.000Z"))
        lines.append(assistant_msg([{"type": "text", "text": f"reply {i}"}], uuid_=f"a{i}",
                                    ts=f"2026-09-08T0{i+1}:00:05.000Z"))
    path = write_transcript(lines)
    rc, text_all = run_digest(path)
    rc, text_last2 = run_digest(path, "--last", "2")
    ck("full digest has all 5 prompts", all(f"## prompt {i}" in text_all for i in range(5)))
    ck("--last 2 drops earlier prompts", "## prompt 0" not in text_last2)
    ck("--last 2 keeps the most recent prompts",
        "## prompt 3" in text_last2 and "## prompt 4" in text_last2)
    ck("--last 2 header still counts the whole session",
        "User turns: 6" in text_all and "User turns: 6" in text_last2)


def test_malformed_line_does_not_crash():
    d = workdir()
    p = d / "session.jsonl"
    with open(p, "w") as f:
        f.write("{not valid json\n")
        f.write(json.dumps(user_msg("a real prompt here", uuid_="u1")) + "\n")
        f.write("\n")  # blank line
    rc, text = run_digest(p)
    ck("malformed-line file still exits 0", rc, 0)
    ck("real prompt after garbage line still rendered", "## a real prompt here" in text)


def test_empty_file_does_not_crash():
    d = workdir()
    p = d / "empty.jsonl"
    p.write_text("")
    rc, text = run_digest(p)
    ck("empty transcript exits 0", rc, 0)


def test_non_matching_input_exits_1():
    d = workdir()
    p = d / "notes.txt"
    p.write_text("just some prose, not a transcript or capture dir")
    rc = ds.main(["digest_session.py", str(p)])
    ck("non-matching file exits 1", rc, 1)


def test_compact_boundary_marker_rendered():
    lines = [
        user_msg("do a thing", uuid_="u1"),
        assistant_msg([{"type": "text", "text": "working on it"}], uuid_="a1"),
        {"parentUuid": None, "type": "system", "subtype": "compact_boundary",
         "content": "Conversation compacted", "uuid": "s1",
         "timestamp": "2026-09-08T02:00:00.000Z", "sessionId": "s1"},
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("compact boundary rendered", "context compacted" in text)


def test_consecutive_identical_tool_calls_collapse():
    lines = [user_msg("read four files", uuid_="u1")]
    blocks = [{"type": "tool_use", "id": f"t{i}", "name": "Read",
               "input": {"file_path": f"/x/{n}.py"}}
              for i, n in enumerate(["a", "b", "c", "d"])]
    lines.append(assistant_msg(blocks, uuid_="a1"))
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("collapsed run line present", "Read ×4:" in text)
    ck("collapsed run lists all files", all(n in text for n in ["a.py", "b.py", "c.py", "d.py"]))


def test_image_block_shown_as_placeholder():
    lines = [
        user_msg([{"type": "text", "text": "look at this"},
                  {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": "AAAA" * 1000}}], uuid_="u1"),
    ]
    path = write_transcript(lines)
    rc, text = run_digest(path)
    ck("image placeholder shown", "[image]" in text)
    ck("base64 image data not leaked", "AAAA" * 1000 not in text)


if __name__ == "__main__":
    tests = [
        test_prompt_heading_appears_and_reminder_only_message_does_not,
        test_edit_shows_path_not_content,
        test_write_shows_path_and_line_count_not_content,
        test_dm_to_and_from_lines,
        test_ticket_close_line,
        test_ticket_rejected_from_user_line,
        test_error_line_rendered,
        test_large_result_flagged,
        test_elision_default_and_full,
        test_thinking_hidden_by_default_and_shown_with_flag,
        test_capture_dir_format_detected_and_rendered,
        test_malformed_line_does_not_crash,
        test_empty_file_does_not_crash,
        test_non_matching_input_exits_1,
        test_compact_boundary_marker_rendered,
        test_consecutive_identical_tool_calls_collapse,
        test_image_block_shown_as_placeholder,
        test_capture_dir_chronological_walk_across_compact,
        test_capture_walk_tolerates_settled_history_rewrites,
        test_inline_peer_message_body_is_not_a_prompt,
        test_local_command_echo_and_teammate_notice_are_lines_not_headings,
        test_capture_dir_noise_lines_dropped,
        test_ticket_host_confirmation_not_duplicate_of_close,
        test_compact_marker_is_plain_line_not_heading,
        test_local_time_default_vs_utc,
        test_last_n_limits_rendered_sections,
    ]
    for t in tests:
        print(f"=== {t.__name__} ===")
        t()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
