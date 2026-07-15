"""Regression checks for pot.py — the boiling-pot tier-2 redundant-read
aggregation. Exercises the FROZEN definition (>=80% overlap, edit-resets,
different-ranges, compact-resets-window) + restart-no-double-count, all against
synthetic request-body histories. Throwaway tmp store; no live ports.

Run: python3 test_pot.py
"""
import os
import sys
import tempfile

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="pottest_logs_")
os.environ["WARMTH_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="pottest_db_"), "warmth.sqlite")

import logproxy as lp  # noqa: E402

pot = lp.pot
_fails = []


def check(desc, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {desc}")
    if not cond:
        _fails.append(desc)


def _asst_read(tid, path, offset=None, limit=None):
    inp = {"file_path": path}
    if offset is not None:
        inp["offset"] = offset
    if limit is not None:
        inp["limit"] = limit
    return {"role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": "Read", "input": inp}]}


def _asst_edit(path, tool="Edit"):
    return {"role": "assistant",
            "content": [{"type": "tool_use", "id": "e-" + path, "name": tool,
                         "input": {"file_path": path}}]}


def _result(tid, chars):
    return {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tid,
                         "content": "x" * chars}]}


def _row(file):
    """The current aggregated snapshot row for `file` (or zeros)."""
    for f in pot.snapshot()["files"]:
        if f["file"] == file:
            return f
    return {"file": file, "reads": 0, "redundant_reads": 0, "redundant_tokens": 0}


# ---- scenario 1: basic redundancy (same full read twice, no edit) ------------
print("scenario 1: redundant full re-read")
h = []
h += [_asst_read("s1a", "/a.js"), _result("s1a", 4000)]
pot.ingest("sess-1", {"messages": h}, False)
h += [_asst_read("s1b", "/a.js"), _result("s1b", 4000)]
pot.ingest("sess-1", {"messages": h}, False)
r = _row("/a.js")
check("reads counted (2)", r["reads"] == 2)
check("second read redundant (1)", r["redundant_reads"] == 1)
check("redundant_tokens = 4000/4 = 1000", r["redundant_tokens"] == 1000)

# ---- scenario 2: an intervening edit refreshes -> not redundant --------------
print("scenario 2: read-after-own-edit is NOT redundant")
h = []
h += [_asst_read("s2a", "/b.js"), _result("s2a", 4000)]
pot.ingest("sess-2", {"messages": h}, False)
h += [_asst_edit("/b.js")]
h += [_asst_read("s2b", "/b.js"), _result("s2b", 4000)]
pot.ingest("sess-2", {"messages": h}, False)
r = _row("/b.js")
check("reads counted (2)", r["reads"] == 2)
check("post-edit read not redundant (0)", r["redundant_reads"] == 0)

# ---- scenario 3: different line ranges -> not redundant -----------------------
print("scenario 3: non-overlapping ranged reads are NOT redundant")
h = []
h += [_asst_read("s3a", "/c.js", offset=0, limit=100), _result("s3a", 4000)]
pot.ingest("sess-3", {"messages": h}, False)
h += [_asst_read("s3b", "/c.js", offset=2000, limit=100), _result("s3b", 4000)]
pot.ingest("sess-3", {"messages": h}, False)
r = _row("/c.js")
check("reads counted (2)", r["reads"] == 2)
check("disjoint ranges not redundant (0)", r["redundant_reads"] == 0)

# overlapping ranged re-read (same window) IS redundant
h += [_asst_read("s3c", "/c.js", offset=0, limit=100), _result("s3c", 4000)]
pot.ingest("sess-3", {"messages": h}, False)
r = _row("/c.js")
check("overlapping ranged re-read redundant (1)", r["redundant_reads"] == 1)

# ---- scenario 4: compact resets the window -----------------------------------
print("scenario 4: /compact resets the redundancy window")
h = []
h += [_asst_read("s4a", "/d.js"), _result("s4a", 4000)]
pot.ingest("sess-4", {"messages": h}, False)
h += [_asst_read("s4b", "/d.js"), _result("s4b", 4000)]
pot.ingest("sess-4", {"messages": h}, False)   # redundant
r = _row("/d.js")
check("pre-compact: 1 redundant", r["redundant_reads"] == 1)
# compact: history contracts to a summary; is_compact=True
h2 = [{"role": "user", "content": [{"type": "text", "text": "summary"}]}]
h2 += [_asst_read("s4c", "/d.js"), _result("s4c", 4000)]
pot.ingest("sess-4", {"messages": h2}, True)
r = _row("/d.js")
check("post-compact read is new, not redundant (still 1)", r["redundant_reads"] == 1)
check("post-compact read counted (reads now 3)", r["reads"] == 3)

# ---- scenario 5: restart resumes from cursor, no double-count -----------------
print("scenario 5: restart (lost in-memory state) does not double-count")
h = []
h += [_asst_read("s5a", "/e.js"), _result("s5a", 4000)]
h += [_asst_read("s5b", "/e.js"), _result("s5b", 4000)]
pot.ingest("sess-5", {"messages": h}, False)   # 2 reads, 1 redundant
before = _row("/e.js")
check("before restart: reads 2", before["reads"] == 2)
# simulate restart: drop in-memory state; the persisted cursor survives
pot._STATE.pop("sess-5", None)
pot.ingest("sess-5", {"messages": h}, False)   # same history re-presented
after = _row("/e.js")
check("no re-count of already-ingested history (reads still 2)",
      after["reads"] == 2)
check("no re-count of redundant (still 1)", after["redundant_reads"] == 1)
# a read after restart is still picked up, but conservatively: the in-memory
# span state was lost, so a re-read of a pre-restart path counts as NEW (never
# over-claims redundancy — the documented conservative post-restart under-count).
h += [_asst_read("s5c", "/e.js"), _result("s5c", 4000)]
pot.ingest("sess-5", {"messages": h}, False)
after2 = _row("/e.js")
check("post-restart read counted (reads 3)", after2["reads"] == 3)
check("post-restart read conservatively NOT redundant (still 1, span lost)",
      after2["redundant_reads"] == 1)

# ---- scenario 6: totals + ranking + window shape -----------------------------
print("scenario 6: snapshot shape / ranking / totals")
snap = pot.snapshot()
check("files ranked by redundant_tokens desc",
      all(snap["files"][i]["redundant_tokens"] >= snap["files"][i + 1]["redundant_tokens"]
          for i in range(len(snap["files"]) - 1)))
check("totals = sum of rows (reads)",
      snap["totals"]["reads"] == sum(f["reads"] for f in snap["files"]))
check("totals = sum of rows (redundant_tokens)",
      snap["totals"]["redundant_tokens"] == sum(f["redundant_tokens"] for f in snap["files"]))
check("window carries days + to", snap["window"]["days"] == 14 and snap["window"]["to"])
check("every row is a closed triple (never partial)",
      all({"file", "reads", "redundant_reads", "redundant_tokens"} <= set(f)
          for f in snap["files"]))
check("days=0 clamps to >=1", pot.snapshot(days=0)["window"]["days"] == 1)
check("bad days falls back to default", pot.snapshot(days="nope")["window"]["days"] == 14)

# ---- done --------------------------------------------------------------------
print()
if _fails:
    print(f"FAILED ({len(_fails)}): " + "; ".join(_fails))
    sys.exit(1)
print("all pot tests passed")
