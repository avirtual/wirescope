#!/usr/bin/env python3
"""views._turn_clock: turn-boundary timestamps for /_session.

WHAT THIS SUITE IS FOR. The feature shipped in v0.6.43 and rendered NOTHING on
any session that had compacted or /clear'ed — the exact sessions anyone looks
at. It had been validated by "0 inversions across 421 sessions", which the bug
satisfies perfectly: the map it produced was EMPTY of the current lineage, and
an empty map has no inversions. Monotonicity is a correlate of correctness that
throwing everything away MAXIMISES.

So the assertions here are about COVERAGE — does a rendered turn header actually
get a stamp — and every case states the count it expects, not merely that the
map is well-formed. `test_monotonic_alone_is_not_enough` is the standing guard:
it pins the vacuity by showing the old metric passes on a map that serves no
turn at all.

Fixture shape: _turn_clock reads ONLY *.warmth.json sidecars ({n_messages_hashed,
ts}), so a fixture is a directory of tiny JSON files — no request bodies, no
proxy, no network.
"""
import json, shutil, sys, tempfile, time
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
    """A capture dir of warmth sidecars. `lineage(start_ts, ns)` writes one
    run of turns; calling it again with a lower n models a compact//clear."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="turnclock-"))
        self.sid = "11111111-2222-3333-4444-555555555555"
        self.dir = self.root / self.sid
        self.dir.mkdir(parents=True)
        self.seq = 0
        self._prev_log_dir = core_mod.LOG_DIR
        core_mod.LOG_DIR = self.root

    def lineage(self, start_ts, ns):
        for k, n in enumerate(ns):
            self.seq += 1
            ts = start_ts + k * 10
            (self.dir / f"{self.seq:03d}-a-main-m-000000.warmth.json").write_text(
                json.dumps({"n_messages_hashed": n, "ts": ts}))
        return start_ts + len(ns) * 10

    def close(self):
        core_mod.LOG_DIR = self._prev_log_dir
        shutil.rmtree(self.root, ignore_errors=True)


def test_single_lineage_every_turn_stamped():
    """The base case: one lineage, every captured boundary is retrievable."""
    f = Fixture()
    try:
        ns = [2, 4, 6, 8, 10]
        f.lineage(1_000_000.0, ns)
        clock = views_mod._turn_clock(f.sid)
        ck("every captured boundary is in the map", sorted(clock), ns)
        ck("timestamps increase with message count",
           [clock[n] for n in sorted(clock)] ==
           sorted(clock[n] for n in clock), True)
    finally:
        f.close()


def test_after_compact_current_lineage_is_served():
    """THE REGRESSION. A compact leaves a LONGER old lineage behind a SHORTER
    live one. Anchoring on the largest n lands in the dead past and discards
    every live entry — which is what shipped, and why the page was blank."""
    f = Fixture()
    try:
        end = f.lineage(1_000_000.0, [2, 4, 6, 8, 100, 200, 286])   # pre-compact
        live = [2, 4, 6, 8]                                          # post-compact
        f.lineage(end + 3600, live)
        if not pre("the old lineage really is longer than the live one (else "
                   "there is no stale-anchor to be fooled by)", 286 > max(live)):
            return
        clock = views_mod._turn_clock(f.sid)
        ck("map serves the LIVE lineage's boundaries", sorted(clock), live)
        ck("stamps are the post-compact times, not the pre-compact ones",
           all(clock[n] >= end for n in clock), True)
        ck("the dead lineage's long tail is gone",
           [n for n in clock if n > max(live)], [])
    finally:
        f.close()


def test_repeated_compacts_serve_the_newest():
    """Three lineages, each shorter than the last: only the newest survives."""
    f = Fixture()
    try:
        end = f.lineage(1_000_000.0, [2, 4, 6, 8, 10, 12])
        end = f.lineage(end + 3600, [2, 4, 6, 8])
        f.lineage(end + 3600, [2, 4])
        clock = views_mod._turn_clock(f.sid)
        ck("only the newest lineage's counts remain", sorted(clock), [2, 4])
    finally:
        f.close()


def test_render_ts_clamps_to_the_rendered_request():
    """Viewing an OLD turn must not be stamped with times from later ones."""
    f = Fixture()
    try:
        end = f.lineage(1_000_000.0, [2, 4, 6])
        f.lineage(end + 3600, [8, 10, 12])
        cut = 1_000_000.0 + 15          # after n=2 (+0) and n=4 (+10), before n=6 (+20)
        clock = views_mod._turn_clock(f.sid, render_ts=cut)
        if not pre("the clamp excludes something (else the case tests nothing)",
                   len(clock) < 6):
            return
        ck("only boundaries at-or-before the rendered request", sorted(clock),
           [2, 4])
        ck("no stamp postdates the rendered request",
           all(clock[n] <= cut for n in clock), True)
    finally:
        f.close()


def test_monotonic_alone_is_not_enough():
    """STANDING GUARD over the metric that let the bug ship. The old validation
    asked only 'are the stamps monotonic?' — this shows an EMPTY map answers
    yes while serving no turn at all, so coverage must be asserted separately."""
    empty = {}
    ks = sorted(empty)
    inversions = sum(1 for a, b in zip(ks, ks[1:]) if empty[b] < empty[a])
    ck("an empty map scores a perfect 0 inversions", inversions, 0)
    ck("...while covering no turn whatsoever", len(empty), 0)

    f = Fixture()
    try:
        end = f.lineage(1_000_000.0, [2, 4, 6, 8, 100, 200, 286])
        f.lineage(end + 3600, [2, 4, 6, 8])
        clock = views_mod._turn_clock(f.sid)
        ck("the real map, by contrast, covers the live turns", len(clock) > 0,
           True)
    finally:
        f.close()


def test_absent_capture_dir_is_empty_not_an_error():
    f = Fixture()
    try:
        clock = views_mod._turn_clock("99999999-0000-0000-0000-000000000000")
        ck("missing capture dir returns {}", clock, {})
    finally:
        f.close()


def test_malformed_sidecars_are_skipped():
    f = Fixture()
    try:
        f.lineage(1_000_000.0, [2, 4])
        (f.dir / "900-x.warmth.json").write_text("{not json")
        (f.dir / "901-x.warmth.json").write_text(json.dumps({"ts": 1_000_500.0}))
        (f.dir / "902-x.warmth.json").write_text(json.dumps({"n_messages_hashed": 9}))
        clock = views_mod._turn_clock(f.sid)
        ck("unparseable and incomplete sidecars ignored", sorted(clock), [2, 4])
    finally:
        f.close()


if __name__ == "__main__":
    for t in (test_single_lineage_every_turn_stamped,
              test_after_compact_current_lineage_is_served,
              test_repeated_compacts_serve_the_newest,
              test_render_ts_clamps_to_the_rendered_request,
              test_monotonic_alone_is_not_enough,
              test_absent_capture_dir_is_empty_not_an_error,
              test_malformed_sidecars_are_skipped):
        print(f"=== {t.__name__} ===")
        t()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
