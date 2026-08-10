#!/usr/bin/env python3
"""report._bust_scan: the receipt-first spine of /_bust.

WHAT THIS SUITE IS FOR. /_bust used to json-load every `*.request.json` in a
session dir — each holding that turn's whole `messages` array — to answer a
question about the ~1% of turns that actually busted. Measured on a real
session: 7,507 turns, 2.77 GB of bodies, 15.4s in `_iter_pairs` alone, of which
the bust loci needed ~100 bodies. The fix reads the bill first and loads a body
only where a locus is computed.

That makes the endpoint fast by NOT READING most of its old inputs, so the risk
is no longer "is it slow" but "does it still say the same thing". Two cheap
reads carry the whole optimisation, and each is a structural claim about the
capture format that a future writer change could silently break:

  1. `summary.n_messages == len(body["messages"])` — the writer builds both from
     the same parsed object (server.py), so the sidecar value substitutes for
     counting the array. `prev_messages`/`cur_messages` ride on this, and they
     decide the `compact` vs `conversation`/`lapse` bust CLASS.
  2. `summary` present iff `body` is a dict — the writer only emits a summary
     after reading `obj["messages"]`, so a bodiless/unparseable capture has
     none. The transition chain BREAKS on a non-dict body, so the scan needs
     this to reproduce chain breaks without opening bodies.

Both were verified against the live corpus when the change shipped (invariant 1
on every comparable turn of the profiled session plus a 4,134-request sample
across all 1,039 dirs; invariant 2 on all 124,026 records). These tests pin them
against a fixture so a regression fails HERE rather than as a quietly wrong bust
class in someone's popover. `test_scan_matches_full_parse` is the end-to-end
guard: it runs the receipt-first path and a body-parsing reference over the same
fixture and asserts the payloads are equal.

Fixture shape: real capture-dir layout (`<stem>.request.json` +
`.response.json`), since the whole point is the on-disk format.

Mutation-checked when written: 9 of 10 seeded faults in the scan are caught here
(chain-break suppressed, message count constant-folded, ts ordering swapped for
seq, fallback parse removed, detail flag ignored, either read window shrunk,
prev/cur swapped, bust bodies never loaded). The survivor is `_tail_summary`'s
`isinstance(obj, dict)` guard, which is unreachable by construction — brace-
matched text starting with `{` either parses to a dict or raises — so it is kept
as cheap defence, not as behaviour a test could pin.
"""
import json, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logproxy as lp                                     # noqa: E402  (ordered boot)
from proxylab import core as core_mod, report as report_mod  # noqa: E402

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
    """A capture dir written the way the proxy writes one."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="bustscan-"))
        self.sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.dir = self.root / self.sid
        self.dir.mkdir(parents=True)
        self.seq = 0
        self._prev = core_mod.LOG_DIR
        core_mod.LOG_DIR = self.root

    def turn(self, ts, messages, read=50_000, write=1_000, role="parent",
             status=200, body=True, tools=None, system=None, pad_kb=0):
        """One captured turn. `ts` is an epoch float here and is written as the
        ISO-8601 string the writer actually emits (`time.strftime`, server.py) —
        the head read matches that format, so a float fixture would test a shape
        that never reaches disk. `pad_kb` inflates the body so tail/head reads are
        genuinely reading past it (a 4 KB tail on a 200-byte file proves nothing)."""
        import datetime as _dt
        ts = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
        self.seq += 1
        stem = f"{self.seq:05d}-a-{self.sid[:8]}-{role}-opus-5-{self.seq:06d}"
        msgs = list(messages)
        if pad_kb and msgs:
            msgs[-1] = dict(msgs[-1])
            msgs[-1]["content"] = (msgs[-1].get("content") or "") + "x" * (pad_kb * 1024)
        rec = {"seq": self.seq, "ts": ts, "agent": "a", "method": "POST",
               "path": "/v1/messages", "client": None, "request_headers": {}}
        if body:
            rec["body"] = {"model": "claude-opus-5", "messages": msgs,
                           "tools": tools or [], "system": system or []}
            # the writer emits `summary` LAST and only for a dict body
            rec["summary"] = {"model": "claude-opus-5", "session_id": self.sid,
                              "role": role, "system_chars": 0, "system_blocks": 0,
                              "n_messages": len(msgs), "messages_chars": 0,
                              "n_tools": len(tools or []), "tool_names": [],
                              "agent_id": None}
        else:
            rec["parse_error"] = "not json"
            rec["body_raw"] = "<<garbage>>"
        (self.dir / f"{stem}.request.json").write_text(json.dumps(rec))
        (self.dir / f"{stem}.response.json").write_text(json.dumps({
            "seq": self.seq, "agent": "a", "role": role, "model": "claude-opus-5",
            "session_id": self.sid, "endpoint": "messages", "status_code": status,
            "billing": {"model": "claude-opus-5", "tokens": {
                "cache_read_input_tokens": read, "cache_write_5m_tokens": write,
                "input_tokens": 0, "output_tokens": 100}},
            "usage": {}, "meta": {}}))
        return stem

    def close(self):
        core_mod.LOG_DIR = self._prev
        shutil.rmtree(self.root, ignore_errors=True)


def msgs(n, salt=""):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"m{i}{salt}"} for i in range(n)]


# ---------------------------------------------------------------- cheap reads

def test_head_ts_and_tail_summary_are_exact():
    """The two cheap reads must return what a full parse returns — on a body big
    enough that they are genuinely skipping it."""
    f = Fixture()
    try:
        stem = f.turn(1000.0, msgs(30), pad_kb=400)
        rf = f.dir / f"{stem}.request.json"
        full = json.loads(rf.read_text())
        if not pre("the fixture body is far larger than either cheap read",
                   rf.stat().st_size > 200_000):
            return
        if not pre("the fixture writes ts in the ISO form the proxy emits",
                   isinstance(full["ts"], str)):
            return
        ck("head read returns the record's ts",
           report_mod._head_ts(rf), full["ts"])
        ck("tail read returns the record's summary verbatim",
           report_mod._tail_summary(rf), full["summary"])
        ck("INVARIANT 1: summary.n_messages == len(body.messages)",
           report_mod._tail_summary(rf)["n_messages"],
           len(full["body"]["messages"]))
    finally:
        f.close()


def test_cheap_reads_degrade_to_none_not_to_garbage():
    """A format change must produce a MISS (caller falls back to a full parse),
    never a confident wrong answer."""
    f = Fixture()
    try:
        stem = f.turn(1000.0, msgs(4))
        rf = f.dir / f"{stem}.request.json"
        # a record with no summary at all (the bodiless shape)
        bad = f.dir / "nope.request.json"
        bad.write_text(json.dumps({"seq": 1, "no_ts_here": True, "body": "str"}))
        ck("tail read on a summary-less record misses",
           report_mod._tail_summary(bad), None)
        ck("head read on a ts-less record misses", report_mod._head_ts(bad), None)
        ck("head read on a missing file misses",
           report_mod._head_ts(f.dir / "absent.request.json"), None)
        ck("tail read on a missing file misses",
           report_mod._tail_summary(f.dir / "absent.request.json"), None)
        # and the good one still works, so the misses above aren't vacuous
        ck("the well-formed record still reads", bool(report_mod._head_ts(rf)), True)
    finally:
        f.close()


def test_scan_falls_back_when_cheap_reads_miss():
    """If the tail read cannot find a summary, the scan must PARSE that record
    rather than drop it or guess — otherwise a format drift silently loses turns."""
    f = Fixture()
    try:
        f.turn(1000.0, msgs(4))
        f.turn(1010.0, msgs(6))
        # rewrite one record with summary NOT last (tail read still finds it) and
        # one with the summary pushed out of tail range by a trailing key
        stems = sorted(p.name for p in f.dir.glob("*.request.json"))
        rf = f.dir / stems[1]
        rec = json.loads(rf.read_text())
        rec["zz_trailing"] = "z" * 20_000        # summary now >4 KB from the end
        rf.write_text(json.dumps(rec))
        if not pre("the trailing key really pushed summary out of tail range",
                   report_mod._tail_summary(rf) is None):
            return
        rows = report_mod._bust_scan(f.sid)
        ck("both turns survive the scan", len(rows), 2)
        ck("the fallback row still has its message count",
           [r["n_messages"] for r in rows], [4, 6])
        ck("the fallback row is still main-line",
           [r["line"] for r in rows], ["main", "main"])
    finally:
        f.close()


# ------------------------------------------------------------- the invariants

def test_bodiless_capture_breaks_the_chain():
    """INVARIANT 2. A record with no dict body has no summary, and the scan must
    mark it `has_body:False` so the transition chain breaks exactly where the
    body-parsing code broke it — a transition must never be computed ACROSS a
    gap, which would diff two non-adjacent turns."""
    f = Fixture()
    try:
        f.turn(1000.0, msgs(4))
        f.turn(1010.0, msgs(6), body=False)      # the gap
        f.turn(1020.0, msgs(8))
        f.turn(1030.0, msgs(10))
        rows = report_mod._bust_scan(f.sid)
        ck("scan sees all four captures", len(rows), 4)
        ck("has_body tracks whether a dict body exists",
           [r["has_body"] for r in rows], [True, False, True, True])
        res = report_mod.bust_series(f.sid, detail=True)
        # pairs: (1->2) skipped, (2->3) skipped (chain reset), (3->4) computed
        ck("only the one adjacent in-chain pair becomes a transition",
           res["count"], 1)
        ck("that transition is the 3->4 pair",
           (res["transitions"][0]["prev_messages"],
            res["transitions"][0]["cur_messages"]), (8, 10))
    finally:
        f.close()


def test_message_counts_come_from_the_sidecar():
    """prev/cur_messages decide the compact-vs-conversation class, so the sidecar
    value must equal what counting the array gives."""
    f = Fixture()
    try:
        f.turn(1000.0, msgs(40), pad_kb=50)
        f.turn(1010.0, msgs(6), read=1_000, write=60_000, pad_kb=50)   # contraction
        res = report_mod.bust_series(f.sid, detail=True)
        if not pre("the fixture produced a bust to classify", res["n_busts"] == 1):
            return
        t = res["transitions"][0]
        ck("prev_messages read from the sidecar", t["prev_messages"], 40)
        ck("cur_messages read from the sidecar", t["cur_messages"], 6)
        ck("a sharp contraction classifies as compact", t["class"], "compact")
    finally:
        f.close()


# ------------------------------------------------------------------ ordering

def test_order_is_by_timestamp_not_by_seq():
    """The capture seq RESETS on a proxy restart, so a session spanning one would
    sort its post-restart turns first. The scan orders by ts like _iter_pairs."""
    f = Fixture()
    try:
        f.turn(2000.0, msgs(20))      # seq 1, later in time
        f.turn(1000.0, msgs(4))       # seq 2, earlier in time (post-restart file)
        rows = report_mod._bust_scan(f.sid)
        ck("rows come back oldest-first by ts",
           [report_mod._epoch(r["ts"]) for r in rows], [1000.0, 2000.0])
        ck("which is NOT the seq order",
           [report_mod._seq_of(r["stem"]) for r in rows], [2, 1])
    finally:
        f.close()


def test_non_main_and_failed_turns_are_filtered():
    """bust_series looks at main-line 200s only; the scan must carry the fields
    that decide both."""
    f = Fixture()
    try:
        f.turn(1000.0, msgs(4))
        f.turn(1010.0, msgs(6), role="subagent")     # not main line
        f.turn(1020.0, msgs(8), status=500)          # not ok
        rows = {r["line"] for r in report_mod._bust_scan(f.sid)}
        ck("the subagent row is keyed off the main line", "main" in rows, True)
        ck("and is distinguishable from it", len(rows), 2)
        oks = [r["ok"] for r in report_mod._bust_scan(f.sid)]
        ck("the 500 is marked not-ok", sorted(oks), [False, True, True])
    finally:
        f.close()


# --------------------------------------------------------------- end-to-end

def _reference_series(session):
    """bust_series computed the OLD way: every field derived from a parsed body.
    Kept deliberately naive — it is the thing the fast path must agree with."""
    from proxylab import report as R
    d = core_mod._session_dir(session)
    pairs = []
    for rf in d.glob("*.request.json"):
        rec = R._load(rf)
        if rec is None:
            continue
        stem = rf.name[: -len(".request.json")]
        resp = R._load(rf.with_name(stem + ".response.json")) or {}
        pairs.append({"stem": stem, "req": rec, "resp": resp,
                      "line": R._line_key(rec.get("summary") or {}),
                      "ts": rec.get("ts"),
                      "tokens": (resp.get("billing") or {}).get("tokens") or {},
                      "ok": resp.get("status_code") == 200})
    pairs.sort(key=lambda p: (R._epoch(p["ts"]) or 0.0, R._seq_of(p["stem"])))
    pairs = [p for p in pairs if p["line"] == "main" and p["ok"]]
    out, prev = [], None
    for idx, p in enumerate(pairs):
        body = (p.get("req") or {}).get("body")
        if not isinstance(body, dict):
            prev = None
            continue
        if prev is None:
            prev = p
            continue
        a = (prev.get("req") or {}).get("body")
        cr, write, inp, _ = R._tokens_rw(p.get("tokens"))
        window = cr + write + inp
        wf = round(write / window, 3) if window else 0.0
        bust = wf >= 0.15
        out.append({
            "i": idx, "stem": p["stem"], "write_frac": wf, "bust": bust,
            "prev_messages": len(a.get("messages") or []),
            "cur_messages": len(body.get("messages") or []),
            "locus": R._first_divergence(a, body) if bust else None,
        })
        prev = p
    return out


def test_scan_matches_full_parse():
    """THE END-TO-END GUARD. Same fixture, both paths, same answer — including
    the loci, which are the one thing that still reads bodies."""
    f = Fixture()
    try:
        base = msgs(10)
        f.turn(1000.0, base, pad_kb=20)
        f.turn(1010.0, base + msgs(2, "b"), pad_kb=20)                  # append
        f.turn(1020.0, base + msgs(4, "c"), read=1_000, write=80_000,   # bust
               pad_kb=20)
        f.turn(1030.0, base + msgs(6, "d"), pad_kb=20,
               tools=[{"name": "Read"}])                                # tools bust
        f.turn(1040.0, msgs(3, "z"), read=500, write=90_000, pad_kb=20)  # compact
        fast = report_mod.bust_series(f.sid, detail=True)["transitions"]
        ref = _reference_series(f.sid)
        if not pre("the fixture generated transitions of both kinds",
                   len(ref) >= 3 and any(t["bust"] for t in ref)
                   and any(not t["bust"] for t in ref)):
            return
        ck("same number of transitions", len(fast), len(ref))
        for key in ("stem", "write_frac", "bust", "prev_messages", "cur_messages"):
            ck(f"{key} agrees on every transition",
               [t[key] for t in fast], [t[key] for t in ref])
        ck("the bust LOCI agree (the part that still reads bodies)",
           [json.dumps(t["locus"], sort_keys=True) for t in fast],
           [json.dumps(t["locus"], sort_keys=True) for t in ref])
    finally:
        f.close()


def test_detail_gates_transitions_only():
    """The payload split must drop `transitions` and change NOTHING else — the
    summary a consumer reads has to be identical either way."""
    f = Fixture()
    try:
        f.turn(1000.0, msgs(10))
        f.turn(1010.0, msgs(12))
        f.turn(1020.0, msgs(14), read=1_000, write=70_000)
        summ = report_mod.bust_series(f.sid, detail=False)
        det = report_mod.bust_series(f.sid, detail=True)
        ck("summary omits transitions", "transitions" in summ, False)
        ck("detail includes them", len(det["transitions"]), det["count"])
        ck("every other key is byte-identical",
           json.dumps(summ, sort_keys=True),
           json.dumps({k: v for k, v in det.items() if k != "transitions"},
                      sort_keys=True))
        if not pre("the fixture has a bust, so `busts` is not vacuously equal",
                   summ["n_busts"] > 0):
            return
        ck("busts survive in the summary", len(summ["busts"]), summ["n_busts"])
    finally:
        f.close()


def test_empty_and_missing_sessions():
    f = Fixture()
    try:
        ck("unknown session scans to nothing",
           report_mod._bust_scan("no-such-session-id"), [])
        res = report_mod.bust_series("no-such-session-id")
        ck("and yields an empty series, not an error",
           (res["count"], res["n_busts"], res["busts"]), (0, 0, []))
        ck("an empty capture dir behaves the same",
           report_mod.bust_series(f.sid)["count"], 0)
    finally:
        f.close()


def main():
    for fn in (test_head_ts_and_tail_summary_are_exact,
               test_cheap_reads_degrade_to_none_not_to_garbage,
               test_scan_falls_back_when_cheap_reads_miss,
               test_bodiless_capture_breaks_the_chain,
               test_message_counts_come_from_the_sidecar,
               test_order_is_by_timestamp_not_by_seq,
               test_non_main_and_failed_turns_are_filtered,
               test_scan_matches_full_parse,
               test_detail_gates_transitions_only,
               test_empty_and_missing_sessions):
        print(f"\n{fn.__name__}:")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
