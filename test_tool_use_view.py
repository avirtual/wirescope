#!/usr/bin/env python3
"""views._tool_use_html: a tool_use block on /_session renders as FIELDS.

Before this the row was `json.dumps(input)` — a `node -e` script arrived as one
line of `\\n` escapes, and the only thing a reader could grasp from the collapsed
summary was the first 90 chars of `{"command": "...`. The assertions here are
about what a human sees: the summary names the call (Bash's description, a file
path, a pattern), the body shows the argument with its real newlines, and the
JSON-escape form is gone from the rendered page.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logproxy as lp                                    # noqa: E402  (ordered boot)
from proxylab import views as views_mod                  # noqa: E402

PASS = FAIL = 0


def ck(name, got, want=True):
    global PASS, FAIL
    ok = (got == want)
    PASS += ok
    FAIL += (not ok)
    print(("  ok  " if ok else "  FAIL") + f" {name}" + ("" if ok else f"  got={got!r} want={want!r}"))


def summary_of(h):
    return h[h.find("<summary>") + 9:h.find("</summary>")]


def test_bash_summary_is_the_description_and_body_keeps_newlines():
    inp = {"command": "node -e '\nconst x = 1;\nconsole.log(x);\n'",
           "description": "Print one"}
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", "Bash", inp)
    ck("summary carries the description", "Print one" in summary_of(h))
    ck("summary does not show raw JSON", '{&quot;command&quot;' not in summary_of(h))
    ck("body has one field per argument", h.count('class="kv"'), 2)
    ck("script newlines are real newlines", "const x = 1;\nconsole.log(x);" in h)
    ck("no JSON-escaped newline survives", "\\n" not in h)
    ck("size badge is the wire size", f"{len(json.dumps(inp, ensure_ascii=False)):,} ch" in h)


def test_bash_without_description_falls_back_to_command_first_line():
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", "Bash",
                                 {"command": "ls -la\necho done"})
    ck("first line of the command", "ls -la" in summary_of(h))
    ck("second line not in summary", "echo done" not in summary_of(h))


def test_file_tools_name_the_path():
    for name in ("Read", "Edit", "Write"):
        h = views_mod._tool_use_html("tooluse", "L", "tool_use", name,
                                     {"file_path": "/a/b.py", "old_string": "x", "new_string": "y"})
        ck(f"{name} summary names the path", "/a/b.py" in summary_of(h))


def test_unknown_tool_uses_first_string_value():
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", "mcp__x__thing",
                                 {"n": 3, "q": "hello world"})
    ck("first string value is the gist", "hello world" in summary_of(h))


def test_nested_values_render_as_indented_json():
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", "Agent",
                                 {"description": "d", "opts": {"a": [1, 2]}})
    ck("nested value pretty-printed", '&quot;a&quot;: [\n    1,' in h)


def test_empty_and_non_dict_input_do_not_crash():
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", "Foo", {})
    ck("empty input stays a one-liner", "<details>" not in h and "<b>Foo</b>" in h)
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", "Foo", "junk")
    ck("string input stays a one-liner", "<details>" not in h)
    h = views_mod._tool_use_html("tooluse", "L", "tool_use", None, {"x": "<y>"})
    ck("unknown name and html are escaped", "<b>?</b>" in h and "&lt;y&gt;" in h)


if __name__ == "__main__":
    for t in (test_bash_summary_is_the_description_and_body_keeps_newlines,
              test_bash_without_description_falls_back_to_command_first_line,
              test_file_tools_name_the_path,
              test_unknown_tool_uses_first_string_value,
              test_nested_values_render_as_indented_json,
              test_empty_and_non_dict_input_do_not_crash):
        print(f"=== {t.__name__} ===")
        t()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
