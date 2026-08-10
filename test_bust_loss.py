#!/usr/bin/env python3
"""The bust test itself: a bust is a LOSS of cached prefix, not a big write.

WHAT THIS SUITE IS FOR. /_bust and the live /_status classifier used to call a
turn a bust when its cache WRITE was a large fraction of the priced window
(`write / (read + write + uncached) >= 0.15`). That is not what a bust is. A
bust is previously-cached prefix that stopped matching — and the receipt says so
directly, because `cache_read` IS the length of the prefix the API matched. So
the test is whether `cache_read` SHRANK from one turn to the next.

The write-fraction test was wrong in BOTH directions, and each direction has a
case here:

  * FALSE ALARM on growth. A window that merely grows writes a lot relative to
    its own size — worst on early/small turns, where one new tool result can be
    half the window. The prefix is fully intact. Measured on the live corpus:
    1,100 of 61.8k transitions flagged with zero prefix lost. This is what made
    a clean reviewer session report 4 busts (`test_reviewer_shape`).
  * SILENCE on a real lapse. A lapse re-reads a short static floor and writes
    little relative to the new window, so write_frac stays near 0 while the
    history evaporates. Measured: 2,046 real busts missed, the worst losing
    138k tokens at write_frac 0.00.

Both engines are fixed the same way and MUST agree, so this suite drives the
disk one (report.bust_series) and the live one (warmth._classify_bust) through
the same scenarios.

Two constants encode judgement rather than measurement, and are pinned here so
a change to them is deliberate:
  * BUST_MIN_LOST_TOKENS = 1024 — the minimum cacheable block. A shrink smaller
    than one block cannot be a lost block; it is framing jitter around an
    unchanged boundary. On the corpus this floor drops 152 of 3,214 flagged
    transitions carrying 96k of 173.6M lost tokens (0.06%).
  * BUST_FULL_REWRITE_FRAC = 0.9 — a REPORTING convention, not a fitted valley.
    The loss distribution is not bimodal (p25 0.08, p50 0.44, p75 0.88), so no
    threshold is "the" boundary; 0.9 means "essentially nothing survived".

The deferred-write case is the subtle one and has its own test: the tail a turn
writes past its last breakpoint is routinely not re-read by the very next
request (the CLI's rolling marker sweeps it in a turn later). Baselining loss on
`read + write` would charge that as damage; baselining on `read` alone does not.
Corpus: 1,127 such turns / 19.3M tokens, every one with read_k == read_{k-1}.

Mutation-checked when written: 9 of 10 seeded faults are caught here (floor
zeroed in either engine, the write-fraction gate restored, the loss baseline
moved back to read+write, `worst` ranked by write, the full-rewrite share moved,
the live gate removed, unread_write zeroed, lost_frac taken over the wrong
denominator). The survivor is warmth._classify_bust's explicit
`prior_read is None` guard, which is EQUIVALENT rather than untested: with no
baseline the following floor test computes `0 - read <= floor`, which is true
for any non-negative read, so control flow is identical either way. The guard is
kept because it states the intent — 'no baseline means DECLINE, not guess',
the same rule the warmth gates follow — where the arithmetic only implies it.
"""
import json, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logproxy as lp                                       # noqa: E402
from proxylab import core as core_mod, report as report_mod  # noqa: E402
from proxylab import warmth as warmth_mod                    # noqa: E402

PASS = FAIL = 0


def ck(desc, got, want):
    global PASS, FAIL
    if got == want:
        print(f"  ok   — {desc}"); PASS += 1
    else:
        print(f"  FAIL — {desc}\n         got {got!r}, want {want!r}"); FAIL += 1


class Fixture:
    """A capture dir written the way the proxy writes one (see test_bust_scan)."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="bustloss-"))
        self.sid = "11111111-2222-3333-4444-555555555555"
        self.dir = self.root / self.sid
        self.dir.mkdir(parents=True)
        self.seq = 0
        self._prev = core_mod.LOG_DIR
        core_mod.LOG_DIR = self.root

    def turn(self, ts, n_msgs, read, write, inp=0, tools=None, system=None):
        import datetime as _dt
        ts = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
        self.seq += 1
        stem = f"{self.seq:05d}-a-{self.sid[:8]}-parent-opus-5-{self.seq:06d}"
        ms = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
              for i in range(n_msgs)]
        rec = {"seq": self.seq, "ts": ts, "agent": "a", "method": "POST",
               "path": "/v1/messages", "client": None, "request_headers": {},
               "body": {"model": "claude-opus-5", "messages": ms,
                        "tools": tools or [], "system": system or []},
               "summary": {"model": "claude-opus-5", "session_id": self.sid,
                           "role": "parent", "system_chars": 0, "system_blocks": 0,
                           "n_messages": len(ms), "messages_chars": 0,
                           "n_tools": len(tools or []), "tool_names": [],
                           "agent_id": None}}
        (self.dir / f"{stem}.request.json").write_text(json.dumps(rec))
        (self.dir / f"{stem}.response.json").write_text(json.dumps({
            "seq": self.seq, "agent": "a", "role": "parent", "model": "claude-opus-5",
            "session_id": self.sid, "endpoint": "messages", "status_code": 200,
            "billing": {"model": "claude-opus-5", "tokens": {
                "cache_read_input_tokens": read, "cache_write_5m_tokens": write,
                "input_tokens": inp, "output_tokens": 100}},
            "usage": {}, "meta": {}}))
        return stem

    def series(self):
        return report_mod.bust_series(self.sid, detail=True)["transitions"]

    def close(self):
        core_mod.LOG_DIR = self._prev
        shutil.rmtree(self.root, ignore_errors=True)


# ------------------------------------------------------- the two failure modes

def test_growth_is_not_a_bust():
    """A big write on a small window is growth. The old rule called this a bust.

    Numbers are the real ones from the reviewer session that exposed the defect
    (turn 4): the prefix was fully hit at 16,746 while 19,797 was written to
    cache the 19,972 the previous turn had left uncached. write_frac = 0.36,
    which the old rule read as a `partial` bust.
    """
    f = Fixture()
    try:
        f.turn(1000.0, 4, read=16_746, write=0, inp=19_972)
        f.turn(1100.0, 6, read=16_746, write=19_797, inp=18_637)
        t = f.series()[0]
        ck("a fully-hit prefix is not a bust however much was written",
           (t["bust"], t["severity"]), (False, "append"))
        ck("...and the write fraction that used to fire is still REPORTED",
           t["write_frac"] >= 0.15, True)
        ck("...with zero loss recorded", t["lost_tokens"], 0)
    finally:
        f.close()


def test_silent_lapse_is_a_bust():
    """A lapse re-reads a static floor and writes little: write_frac ~0, and the
    old rule stayed silent while 138k tokens of prefix evaporated. Real numbers
    from corpus session a68b0455 (read 154,327 -> 16,218 at write_frac 0.00)."""
    f = Fixture()
    try:
        f.turn(1000.0, 257, read=154_327, write=0)
        f.turn(1100.0, 257, read=16_218, write=0, inp=140_000)
        t = f.series()[0]
        ck("a collapsed cache_read is a bust even when almost nothing was written",
           t["bust"], True)
        ck("...sized by what was LOST, not what was written",
           t["lost_tokens"], 138_109)
        ck("...and the write fraction that missed it is still ~0",
           t["write_frac"] < 0.15, True)
        ck("...classified as a lapse (bytes unchanged, prefix just died)",
           t["class"], "lapse")
    finally:
        f.close()


def test_deferred_write_is_not_loss():
    """The tail written past the last breakpoint is often not re-read next turn;
    the rolling marker sweeps it in later. Baselining on read+write would call
    that a loss. It is reported as `unread_write` instead."""
    f = Fixture()
    try:
        f.turn(1000.0, 10, read=50_000, write=9_000)
        f.turn(1100.0, 12, read=50_000, write=2_000)     # read held; write unread
        t = f.series()[0]
        ck("an unread prior write is not a loss", (t["bust"], t["lost_tokens"]),
           (False, 0))
        ck("...it is surfaced as deferred write, so it stays visible",
           t["unread_write"], 9_000)
    finally:
        f.close()


# ------------------------------------------------------------- the two constants

def test_sub_block_jitter_is_floored():
    """A shrink under one cacheable block cannot be a lost block."""
    f = Fixture()
    try:
        f.turn(1000.0, 10, read=80_000, write=500)
        f.turn(1100.0, 12, read=79_500, write=500)       # -500, under the floor
        ck("a sub-block shrink is not a bust", f.series()[0]["bust"], False)
    finally:
        f.close()
    f = Fixture()
    try:
        f.turn(1000.0, 10, read=80_000, write=500)
        f.turn(1100.0, 12, read=78_000, write=500)       # -2000, over the floor
        ck("a shrink of more than one block is", f.series()[0]["bust"], True)
    finally:
        f.close()
    ck("the floor is the minimum cacheable block",
       report_mod.BUST_MIN_LOST_TOKENS, 1024)
    ck("the disk and live engines use the SAME floor",
       report_mod.BUST_MIN_LOST_TOKENS, warmth_mod.BUST_MIN_LOST_TOKENS)


def test_severity_splits_on_share_lost():
    f = Fixture()
    try:
        f.turn(1000.0, 10, read=100_000, write=0)
        f.turn(1100.0, 12, read=2_000, write=0)          # 98% gone
        f.turn(1200.0, 14, read=60_000, write=0)         # grew back
        f.turn(1300.0, 16, read=40_000, write=0)         # 33% gone
        s = f.series()
        ck("losing essentially everything is a full-rewrite",
           s[0]["severity"], "full-rewrite")
        ck("...and is flagged quasi_full_rewrite for the consumer",
           s[0]["quasi_full_rewrite"], True)
        ck("losing part of the prefix is partial", s[2]["severity"], "partial")
        ck("...and is not a full rewrite", s[2]["quasi_full_rewrite"], False)
        ck("the reported share is loss over the PRIOR read",
           s[2]["lost_frac"], 0.333)
    finally:
        f.close()


def test_worst_ranks_by_loss_not_by_write():
    """`worst` must surface the most damage. A turn that wrote more because the
    conversation grew is not worse than one that lost more."""
    f = Fixture()
    try:
        f.turn(1000.0, 10, read=100_000, write=0)
        f.turn(1100.0, 12, read=60_000, write=90_000)    # lost 40k, wrote 90k
        f.turn(1200.0, 14, read=150_000, write=0)
        f.turn(1300.0, 16, read=50_000, write=5_000)     # lost 100k, wrote 5k
        w = report_mod.bust_series(f.sid, detail=False)["worst"]
        ck("worst is the biggest LOSS", w["lost_tokens"], 100_000)
        ck("...not the biggest write", w["write_tokens"], 5_000)
    finally:
        f.close()


# ------------------------------------------------ the shape that started this

def test_reviewer_shape():
    """The live reviewer session (5m TTL, opus, tool-heavy) that reported 4 busts
    while never losing a single cached token. Receipt sequence is the real one,
    turns 1-6; every read equals the prior read, i.e. the prefix only ever grew.

    The leading (0, 0, 2) is the session's genuine first request — it must be
    present for the fixture to produce the same TRANSITIONS as the live session
    (the first turn is only a baseline, so without it the cold-start transition
    that the old rule scored 1.00 never appears and the case would silently
    check 3 false alarms instead of 4)."""
    f = Fixture()
    try:
        seq = [(0, 0, 2),
               (0, 6_692, 2), (6_692, 10_054, 2), (16_746, 0, 19_972),
               (16_746, 19_797, 18_637), (36_543, 8_669, 2_732),
               (45_212, 2_061, 5_921)]
        for i, (r, w, u) in enumerate(seq):
            f.turn(1000.0 + i * 100, 2 + 2 * i, read=r, write=w, inp=u)
        s = f.series()
        ck("no turn of a session that never lost prefix is a bust",
           [t["bust"] for t in s], [False] * len(s))
        ck("...and the old rule would have fired on four of them",
           sum(1 for t in s if t["write_frac"] >= 0.15), 4)
    finally:
        f.close()


# -------------------------------------------------------- the live twin agrees

def _live(prior_read, read, created, inp, *, prior=None, msgs=(10, 12),
          lapsed=False):
    """warmth._classify_bust with an unchanged static prefix unless told else."""
    p = prior or ("t", "s", "sf", "m0", msgs[0])
    return warmth_mod._classify_bust(
        read, created, inp, prior=p, prior_read=prior_read,
        cur_tools="t", cur_sys="s", cur_sysfull="sf", cur_msg0="m0",
        cur_msgs=msgs[1], lapsed=lapsed)


def test_live_classifier_matches():
    ck("live: growth with an intact prefix is not a bust",
       _live(16_746, 16_746, 19_797, 18_637), None)
    ck("live: a collapsed read IS a bust even at write_frac ~0",
       _live(154_327, 16_218, 0, 140_000, msgs=(257, 257), lapsed=True), "lapse")
    ck("live: an unread prior write is not a loss",
       _live(50_000, 50_000, 2_000, 0), None)
    ck("live: sub-block jitter is floored", _live(80_000, 79_500, 500, 0), None)
    ck("live: a real shrink with a changed toolset files as `tools`",
       warmth_mod._classify_bust(
           2_000, 5_000, 0, prior=("OLD", "s", "sf", "m0", 10), prior_read=90_000,
           cur_tools="NEW", cur_sys="s", cur_sysfull="sf", cur_msg0="m0",
           cur_msgs=12, lapsed=False), "tools")


def test_live_declines_when_it_cannot_judge():
    """A head row written before the read_tokens column existed has no baseline.
    Declining is correct: 'can't judge' is not evidence of a bust (the same rule
    the warmth gates follow)."""
    ck("live: a pre-migration head declines rather than guessing",
       _live(None, 1_000, 90_000, 0), None)
    ck("live: the session's first turn is still not a bust",
       warmth_mod._classify_bust(0, 50_000, 0, prior=None, prior_read=None,
                                 cur_tools="t", cur_sys="s", cur_sysfull="sf",
                                 cur_msg0="m0", cur_msgs=2, lapsed=False), None)


def test_live_still_splits_the_classes():
    """The loss test replaced the GATE, not the WHERE — all five classes survive."""
    got = {
        "tools": warmth_mod._classify_bust(
            0, 9_000, 0, prior=("OLD", "s", "sf", "m0", 10), prior_read=90_000,
            cur_tools="NEW", cur_sys="s", cur_sysfull="sf", cur_msg0="m0",
            cur_msgs=12, lapsed=False),
        "system": warmth_mod._classify_bust(
            0, 9_000, 0, prior=("t", "OLD", "OLD", "m0", 10), prior_read=90_000,
            cur_tools="t", cur_sys="s", cur_sysfull="sf", cur_msg0="m0",
            cur_msgs=12, lapsed=False),
        "preamble": warmth_mod._classify_bust(
            0, 9_000, 0, prior=("t", "s", "sf", "OLD", 10), prior_read=90_000,
            cur_tools="t", cur_sys="s", cur_sysfull="sf", cur_msg0="m0",
            cur_msgs=12, lapsed=False),
        "compact": _live(90_000, 1_000, 9_000, 0, msgs=(300, 4)),
        "lapse": _live(90_000, 1_000, 9_000, 0, lapsed=True),
        "conversation": _live(90_000, 1_000, 9_000, 0, lapsed=False),
    }
    for want, got_cls in got.items():
        ck(f"live: `{want}` still classifies", got_cls, want)


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print(f"\n{fn.__name__}:")
        fn()

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
print("ALL PASS")
