#!/usr/bin/env python3
"""What do clodex REVIEWER seats actually do with Read/Glob/Grep, and how much of
it is a round trip that did not have to happen?

A reviewer seat ships exactly three tools (Glob, Grep, Read) and spends its whole
life in a tool loop. Every round trip re-carries the entire window, so the round
COUNT is the bill -- not the size of any single result. This asks, over a corpus
of recent reviewer sessions:

  1. VOLUME      rounds, calls, and how many calls ride in one round (batching).
  2. REPETITION  is the same file read more than once, and WHY. Four kinds, and
                 they have completely different fixes:

       CONTINUATION  a Read resuming where the previous Read of that file stopped
                     (offset == previous end + 1). The reviewer did not choose to
                     come back -- the tool handed back a partial file and it
                     paged. FIX: serve the file whole.
       OVERLAP       a re-read whose range intersects one already seen. The bytes
                     were already in the window; the model went back anyway.
                     FIX: none available to a bootstrap -- this is model behaviour.
       DISJOINT      a later read of a different region of a known-relevant file.
                     FIX: serve the file whole (same as CONTINUATION, weaker claim
                     -- the second region might never have been wanted).
       EXACT         byte-identical arguments issued twice. Pure waste.

  3. PATTERN     which files does EVERY reviewer open? A file opened by most
                 sessions of a repo is knowable at bootstrap -- that is the
                 "front it in one gulp" candidate list, ranked by how many rounds
                 it would have removed.
  4. FORCED      truncation the tools inflict: Grep's `[Showing results with
                 pagination = limit: N]` and Read's partial returns. These are
                 follow-ups the reviewer was FORCED into.

METHOD. One row per session, read from that session's DEEPEST captured request:
the CLI re-ships the whole conversation every turn, so the last request contains
every tool_use and every tool_result the session ever had. Nothing is counted
twice and no response bodies are needed.

Worktree paths are normalised: `wb-wrap-ui-t403-agents-off-cmdline-...` and
`wb-wrap-ui` are the SAME file for cross-session counting, otherwise every
ticket's worktree looks like a distinct file and no pattern can appear.

CAVEAT on savings: rounds_saved counts rounds whose every call was avoidable
(a round with one avoidable call among three saves nothing -- the round still
flies). That is the honest floor.

Usage:
    python3 analyze_review_tools.py [--logs DIR] [--days 3] [--session ID]
                                    [--repo NAME] [--top N] [--json OUT]
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from glob import glob

DEFAULT_LOGS = os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs"
)

# A reviewer seat's tag: legacy `...-reviewer-...` and current `...tNNN.review-rN-...`.
REVIEW_TAG = re.compile(r"(?:^|[-.])review(?:er)?(?:-r\d+)?(?:[-.]|$)", re.I)

# `<repo>-t<ticket>-<slug>` worktree -> `<repo>`. The ticket id anchors it, so a
# real directory that merely contains a dash (wb-wrap-ui) is never mangled.
# The slug is OPTIONAL: seats use both `<repo>-t403-move-agents-json-off` and a
# bare `<repo>-t330`. Requiring the slug left the bare form unnormalised, so one
# file counted as two (test/ticket-loop-verify.test.js showed up twice).
WORKTREE = re.compile(r"^(?P<repo>.+?)-t\d+(?:-[a-z0-9-]+)?$")

GREP_PAGINATED = re.compile(r"\[Showing results with pagination[^\]]*\]")
READ_LINE = re.compile(r"^(\d+)\t", re.M)

# Read's own line-count default. A result that stops short of the file's end
# without the reviewer asking for a limit is the tool truncating, not a choice.
DEFAULT_READ_LIMIT = 2000


def seq_of(path):
    return int(os.path.basename(path).split("-")[0])


def load_body(path):
    with open(path) as fh:
        blob = json.load(fh)
    return blob.get("body") or blob


def result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        return "".join(x.get("text", "") for x in content if isinstance(x, dict))
    return content or ""


def canon(path, repo_hint=None):
    """Collapse a worktree checkout onto its repo so cross-session counts mean
    something. Returns (canonical_path, repo)."""
    if not path:
        return "", ""
    parts = path.split("/")
    repo = ""
    for i, seg in enumerate(parts):
        m = WORKTREE.match(seg)
        if m:
            parts[i] = m.group("repo")
            repo = m.group("repo")
            break
        # non-worktree checkout of the same repo
        if repo_hint and seg == repo_hint:
            repo = seg
    return "/".join(parts), repo


def rel(path, repo):
    """Repo-relative display path (falls back to a short tail)."""
    if repo and f"/{repo}/" in path:
        return path.split(f"/{repo}/", 1)[1]
    parts = path.split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else path


class Call:
    __slots__ = ("name", "inp", "res", "is_error", "round", "target", "canon",
                 "repo", "start", "end", "truncated", "kind")

    def __init__(self, tu, res, is_error, rnd):
        self.name = tu.get("name", "?")
        self.inp = tu.get("input") or {}
        self.res = res
        self.is_error = is_error
        self.round = rnd
        self.target = self.inp.get("file_path") or self.inp.get("path") or ""
        self.canon, self.repo = canon(self.target)
        self.start = self.end = None
        self.truncated = False
        self.kind = ""
        if self.name == "Read":
            self._read_extent()
        elif self.name == "Grep":
            self.truncated = bool(GREP_PAGINATED.search(res))

    def _read_extent(self):
        """Actual line span RETURNED (from Read's own `NNN\\t` prefixes), not the
        span requested -- a Read that asked for 2000 lines and got 883 covers 883.

        Truncation is deliberately NOT inferred here. "Span == limit" means the
        model got what it ASKED for, which is not the tool cutting it off; scoring
        that as truncation counted 76% of Reads as forced when the provable figure
        is ~4%. The honest signal is external and lives in _classify_reads: a later
        Read that resumes at end+1 proves this one stopped early."""
        nums = READ_LINE.findall(self.res)
        if nums:
            self.start, self.end = int(nums[0]), int(nums[-1])
        else:
            self.start = self.inp.get("offset") or 1
            self.end = None


class Session:
    def __init__(self, sid, tag, req_paths):
        self.sid = sid
        self.tag = tag
        self.reqs = req_paths
        self.ok = False
        self._build()

    def _build(self):
        body = load_body(self.reqs[-1])
        self.roster = sorted(t.get("name", "") for t in body.get("tools", []))
        msgs = body.get("messages", [])
        if not msgs:
            return

        results, errors = {}, {}
        for m in msgs:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for bl in c:
                if bl.get("type") == "tool_result":
                    results[bl.get("tool_use_id")] = result_text(bl)
                    errors[bl.get("tool_use_id")] = bool(bl.get("is_error"))

        self.calls, self.rounds = [], 0
        for m in msgs:
            if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
                continue
            tus = [b for b in m["content"] if b.get("type") == "tool_use"]
            if not tus:
                continue
            self.rounds += 1
            for tu in tus:
                self.calls.append(
                    Call(tu, results.get(tu["id"], ""), errors.get(tu["id"], False),
                         self.rounds)
                )
        if not self.calls:
            return

        self.repo = Counter(c.repo for c in self.calls if c.repo).most_common(1)
        self.repo = self.repo[0][0] if self.repo else ""
        self._classify_reads()
        self.ok = True

    def _classify_reads(self):
        """Label every Read after the first of its file. Order is chronological,
        which is what makes CONTINUATION distinguishable from a plain re-read."""
        seen = defaultdict(list)          # canon path -> [(start, end), ...]
        exact = set()
        for c in self.calls:
            if c.name != "Read":
                continue
            key = (c.canon, json.dumps(c.inp, sort_keys=True))
            prior = seen[c.canon]
            if not prior:
                c.kind = "FIRST"
            elif key in exact:
                c.kind = "EXACT"
            else:
                s, e = c.start or 1, c.end or (c.start or 1)
                # continuation: starts within a few lines of where a prior read ended
                if any(0 <= s - pe <= 2 for _, pe in prior if pe):
                    c.kind = "CONTINUATION"
                elif any(s <= pe and e >= ps for ps, pe in prior if pe):
                    c.kind = "OVERLAP"
                else:
                    c.kind = "DISJOINT"
            exact.add(key)
            prior.append((c.start or 1, c.end))
            # Retro-mark the read this one resumes: THAT read was truncated by the
            # tool (proof by continuation, the only evidence on the wire).
            if c.kind == "CONTINUATION":
                for p in self.calls:
                    if (p.name == "Read" and p.canon == c.canon and p.end
                            and 0 <= (c.start or 1) - p.end <= 2):
                        p.truncated = True

    # ---------- metrics ----------

    @property
    def reads(self):
        return [c for c in self.calls if c.name == "Read"]

    def row(self):
        by_tool = Counter(c.name for c in self.calls)
        kinds = Counter(c.kind for c in self.reads)
        files = Counter(c.canon for c in self.reads if c.canon)
        repeated = {f: n for f, n in files.items() if n > 1}
        # rounds whose EVERY call was a repeat read (would vanish if files were
        # served whole) -- the honest floor on round savings.
        by_round = defaultdict(list)
        for c in self.calls:
            by_round[c.round].append(c)
        saveable = [
            r for r, cs in by_round.items()
            if cs and all(c.name == "Read" and c.kind in
                          ("CONTINUATION", "DISJOINT", "EXACT", "OVERLAP")
                          for c in cs)
        ]
        return {
            "session": self.sid,
            "tag": self.tag,
            "repo": self.repo,
            "rounds": self.rounds,
            "calls": len(self.calls),
            "calls_per_round": len(self.calls) / self.rounds if self.rounds else 0,
            "by_tool": dict(by_tool),
            "read_kinds": dict(kinds),
            "distinct_files": len(files),
            "repeated_files": len(repeated),
            "repeat_reads": sum(n - 1 for n in repeated.values()),
            "grep_truncated": sum(1 for c in self.calls
                                  if c.name == "Grep" and c.truncated),
            "read_truncated": sum(1 for c in self.reads if c.truncated),
            "errors": sum(1 for c in self.calls if c.is_error),
            "rounds_saveable": len(saveable),
            "files": {c.canon for c in self.reads if c.canon},
            "first_round_files": {c.canon for c in self.reads
                                  if c.round == 1 and c.canon},
            "coverage": self.coverage(),
            "motifs": self.motifs(),
            "grep_directed": self.grep_directed(),
            "reread_spans": [c.end - c.start + 1 for c in self.reads
                             if c.kind in ("DISJOINT", "OVERLAP") and c.end and c.start],
        }

    def grep_directed(self):
        """Re-reads whose line an EARLIER Grep already reported (`file:NNN:`).
        These are the round trips a `-C` on that Grep would have folded away:
        the search already found the spot, the extra round only fetched the
        surrounding lines."""
        n = 0
        for c in self.reads:
            if c.kind not in ("DISJOINT", "OVERLAP") or not c.start:
                continue
            base = c.target.split("/")[-1]
            if not base:
                continue
            near = re.compile(re.escape(base) + r":(\d+)")
            for p in self.calls:
                if p.name != "Grep" or p.round >= c.round or base not in p.res:
                    continue
                if any(abs(int(m.group(1)) - c.start) <= 60
                       for m in near.finditer(p.res)):
                    n += 1
                    break
        return n

    def coverage(self):
        """Per file: which LINES this session ended up seeing, and in how many
        pieces. The line set is what a preload would have to contain."""
        out = {}
        for c in self.reads:
            if not c.canon or not c.end:
                continue
            d = out.setdefault(c.canon, {"lines": set(), "reads": 0, "chars": 0})
            d["lines"].update(range(c.start, c.end + 1))
            d["reads"] += 1
            d["chars"] += len(c.res)
        return out

    def motifs(self):
        """Adjacent round-shape transitions (Grep -> Read etc.) -- the loop's
        grammar, which is what tells you whether a different INPUT could
        short-circuit it."""
        shapes = defaultdict(set)
        for c in self.calls:
            shapes[c.round].add(c.name)
        seq = ["+".join(sorted(shapes[r])) for r in sorted(shapes)]
        return Counter(f"{a} -> {b}" for a, b in zip(seq, seq[1:]))


def discover(logs, days, session=None, last=None):
    """-> {(session_id, tag): [request paths]}, chronological within a group.

    `last=N` keeps the N most RECENT seats (by newest request mtime) after the
    day window and the tool-loop filter, so "the last 10 reviewers" means ten
    real reviews -- not ten title side-calls, which outnumber them."""
    cut = time.time() - days * 86400
    groups = defaultdict(list)
    for p in glob(os.path.join(logs, "*", "*.request.json")):
        base = os.path.basename(p)
        if not REVIEW_TAG.search(base):
            continue
        if os.path.getmtime(p) < cut:
            continue
        sid = os.path.basename(os.path.dirname(p))
        if session and sid != session:
            continue
        # tag = everything between the leading seq and the 8-char instance hash
        m = re.match(r"\d+-(.+?)-[0-9a-f]{8}-", base)
        tag = m.group(1) if m else "?"
        groups[(sid, tag)].append(p)
    out = {k: sorted(v, key=seq_of) for k, v in groups.items()}
    if last:
        # Keep only seats that actually ran a tool loop. Counting requests is not
        # enough: the CLI's side-calls land in a shared `_no-session` bucket that
        # accumulates several per seat, so a >1 test lets them through. A real
        # review always forwards tools[].
        real = {}
        for k, v in out.items():
            for p in v:
                try:
                    with open(p) as fh:
                        body = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                if (body.get("body") or body).get("tools"):
                    real[k] = v
                    break
        newest = sorted(real, key=lambda k: max(os.path.getmtime(p) for p in real[k]),
                        reverse=True)[:last]
        out = {k: real[k] for k in newest}
    return out


def pct(n, d):
    return f"{100.0 * n / d:5.1f}%" if d else "    -"


def dist(name, xs, fmt="{:.0f}"):
    if not xs:
        return
    s = sorted(xs)
    f = fmt.format
    q = lambda p: s[max(0, min(len(s) - 1, int(round(p / 100 * len(s) + .5)) - 1))]
    print(f"  {name:22} n={len(s):<4} min {f(s[0]):>7}  med {f(statistics.median(s)):>7}"
          f"  p75 {f(q(75)):>7}  p90 {f(q(90)):>7}  max {f(s[-1]):>7}"
          f"  mean {f(statistics.mean(s)):>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--last", type=int,
                    help="only the N most recent reviewer seats")
    ap.add_argument("--session")
    ap.add_argument("--repo", help="restrict to sessions whose calls live in REPO")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json")
    args = ap.parse_args()

    groups = discover(args.logs, args.days, args.session, last=args.last)
    if not groups:
        print("no reviewer sessions found", file=sys.stderr)
        return 1

    rows, skipped = [], 0
    for (sid, tag), paths in groups.items():
        try:
            s = Session(sid, tag, paths)
        except (json.JSONDecodeError, OSError, KeyError):
            skipped += 1
            continue
        if not s.ok:
            skipped += 1
            continue
        if args.repo and s.repo != args.repo:
            continue
        rows.append(s.row())

    if not rows:
        print("no analysable reviewer sessions", file=sys.stderr)
        return 1

    n = len(rows)
    tot_calls = sum(r["calls"] for r in rows)
    tot_rounds = sum(r["rounds"] for r in rows)
    # Skips are overwhelmingly the CLI's 1-message side-calls (title generation,
    # captured under `_no-session` with role `unknown`): no assistant turn, so no
    # tool loop to analyse. Not parse failures -- calling them that once made the
    # corpus look 37% lossy when nothing was lost.
    print(f"\n{'='*78}\nREVIEWER TOOL-LOOP ANALYSIS — {n} sessions, last {args.days:g} days"
          f"  ({tot_rounds} rounds, {tot_calls} calls;"
          f" {skipped} side-calls/empty skipped)\n{'='*78}")

    print("\n-- 1. VOLUME (per session) " + "-" * 51)
    dist("rounds", [r["rounds"] for r in rows])
    dist("tool calls", [r["calls"] for r in rows])
    dist("calls per round", [r["calls_per_round"] for r in rows], "{:.2f}")
    dist("distinct files read", [r["distinct_files"] for r in rows])
    mix = Counter()
    for r in rows:
        mix.update(r["by_tool"])
    print("\n  tool mix:", "  ".join(
        f"{k} {v} ({pct(v, tot_calls).strip()})" for k, v in mix.most_common()))
    solo = sum(1 for r in rows if r["calls_per_round"] < 1.5)
    print(f"  sessions averaging <1.5 calls/round (barely batching): {solo}/{n}"
          f"  {pct(solo, n)}")

    print("\n-- 2. REPETITION (are they re-reading the same file?) " + "-" * 24)
    kinds = Counter()
    for r in rows:
        kinds.update(r["read_kinds"])
    tot_reads = sum(kinds.values())
    for k in ("FIRST", "CONTINUATION", "DISJOINT", "OVERLAP", "EXACT"):
        v = kinds.get(k, 0)
        print(f"  {k:14} {v:6}  {pct(v, tot_reads)} of all Reads")
    repeat = tot_reads - kinds.get("FIRST", 0)
    print(f"  {'-'*46}\n  REPEAT reads (any kind) {repeat:6}  {pct(repeat, tot_reads)}"
          f"  in {sum(1 for r in rows if r['repeat_reads'])}/{n} sessions")
    dist("repeated files/session", [r["repeated_files"] for r in rows])
    dist("repeat reads/session", [r["repeat_reads"] for r in rows])

    print("\n-- 3. FORCED FOLLOW-UPS (the tools truncated) " + "-" * 32)
    gt = sum(r["grep_truncated"] for r in rows)
    rt = sum(r["read_truncated"] for r in rows)
    print(f"  Grep hit its head_limit / pagination cap : {gt:5}"
          f"  ({pct(gt, mix.get('Grep', 0))} of Greps, "
          f"{sum(1 for r in rows if r['grep_truncated'])}/{n} sessions)")
    print(f"  Read provably cut short (a later read    : {rt:5}"
          f"  ({pct(rt, mix.get('Read', 0))} of Reads, "
          f"{sum(1 for r in rows if r['read_truncated'])}/{n} sessions)")
    print(f"    resumed at its end+1)")
    print(f"  failed / denied calls                    : {sum(r['errors'] for r in rows):5}")

    print("\n-- 4. CROSS-SESSION PATTERN (front-load candidates) " + "-" * 26)
    by_repo = defaultdict(list)
    for r in rows:
        by_repo[r["repo"] or "(none)"].append(r)
    for repo, rs in sorted(by_repo.items(), key=lambda kv: -len(kv[1]))[:3]:
        freq = Counter()
        late = Counter()
        for r in rs:
            freq.update(r["files"])
            late.update(r["files"] - r["first_round_files"])
        print(f"\n  repo {repo!r} — {len(rs)} sessions")
        print(f"  {'sessions':>9} {'%':>6} {'late':>6}  file")
        for f, c in freq.most_common(args.top):
            if c < 2:
                break
            print(f"  {c:>9} {pct(c, len(rs))} {late.get(f, 0):>6}  {rel(f, repo)}")

    print("\n-- 5. ROUND-TRIP FLOOR " + "-" * 55)
    sv = sum(r["rounds_saveable"] for r in rows)
    print(f"  rounds whose every call was a repeat read: {sv}"
          f"  = {pct(sv, tot_rounds)} of all rounds")
    print(f"  (floor: a round mixing a repeat with new work still has to fly)")

    print("\n-- 6. LOOP GRAMMAR (what follows what) " + "-" * 39)
    mot = Counter()
    for r in rows:
        mot.update(r["motifs"])
    tot_mot = sum(mot.values())
    for k, v in mot.most_common(8):
        print(f"  {v:5}  {pct(v, tot_mot)}  {k}")

    print("\n-- 7. WOULD A PRELOAD HIT? (per-file cross-session overlap) " + "-" * 18)
    print("  A file is only worth fronting if sessions want the SAME lines.")
    percov = defaultdict(list)
    for r in rows:
        for f, d in r["coverage"].items():
            percov[f].append(d)
    print(f"  {'reads':>6} {'sess':>5} {'med lines':>10} {'med chars':>10} {'ovl':>6}  file")
    for f, ds in sorted(percov.items(), key=lambda kv: -sum(d["reads"] for d in kv[1]))[:12]:
        if len(ds) < 2:
            continue
        sets = [d["lines"] for d in ds]
        pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]][:400]
        ov = statistics.median(
            [len(a & b) / len(a | b) if (a | b) else 0 for a, b in pairs]) if pairs else 0
        print(f"  {sum(d['reads'] for d in ds):>6} {len(ds):>5}"
              f" {statistics.median([len(d['lines']) for d in ds]):>10.0f}"
              f" {statistics.median([d['chars'] for d in ds]):>10.0f}"
              f" {ov:>6.3f}  {rel(f, next((r['repo'] for r in rows if r['repo']), ''))}")
    print("  ovl = median pairwise Jaccard of the line sets two sessions read.")
    print("  Near 0 => every session wants a different slice; a fixed preload misses.")

    print("\n-- 8. GREP-DIRECTED RE-READS (the -C lever) " + "-" * 34)
    gd = sum(r["grep_directed"] for r in rows)
    dj = sum(r["read_kinds"].get(k, 0) for r in rows for k in ("DISJOINT", "OVERLAP"))
    print(f"  re-reads whose line a PRIOR Grep already reported: {gd}"
          f"  {pct(gd, dj)} of DISJOINT+OVERLAP")
    print(f"  median span fetched: {statistics.median(sum((r['reread_spans'] for r in rows), [])) if any(r['reread_spans'] for r in rows) else 0:.0f} lines")
    print("  => Grep found it, then a whole round was spent fetching context around")
    print("     it. `-C` on the original Grep returns that context in the SAME round.")

    if args.json:
        def plain(v):
            """Sets (incl. the line sets nested in `coverage`) -> counts/lists."""
            if isinstance(v, set):
                return sorted(v)
            if isinstance(v, Counter):
                return dict(v)
            if isinstance(v, dict):
                return {k: (len(x) if k == "lines" else plain(x))
                        for k, x in v.items()}
            return v
        with open(args.json, "w") as fh:
            json.dump([{k: plain(v) for k, v in r.items()} for r in rows],
                      fh, indent=1)
        print(f"\nwrote {args.json}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
