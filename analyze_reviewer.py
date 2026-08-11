#!/usr/bin/env python3
"""Corpus analyser for clodex-reviewer sessions: which tool calls could a bootstrap
have pre-served, and would pre-serving them have paid?

The question is NOT "how much context did the reviewer use" (that is /_context) but
"how much of the reviewer's TOOL LOOP is derivable before the session starts". Every
round trip re-reads the whole window, so a call a script could have made at bootstrap
is a round trip bought for nothing.

REVIEW SHAPES -- established by survey, not assumed. A reviewer session does NOT
reliably "receive a diff": only about half do.

  diff-file     a .diff/.patch artifact is Read from disk (t190.diff style)
  diff-inline   diff --git text arrives some other way (brief, git output in a result)
  git-bash      the reviewer GENERATES the diff itself via Bash git (needs Bash in
                the roster -- only a minority of sessions have it)
  no-diff       worktree or spec review: no diff anywhere. The reviewer Globs a tree
                or reads plan/spec docs. These are REAL reviews with up to 55 calls.

CALL CLASSIFICATION (only DERIVABLE and MANIFEST are bootstrap-servable):

  DERIVABLE   Read overlapping a hunk of a file the diff touches -- the diff named
              the file AND the line, and the reviewer went back to look. The
              "line 59 changed -> Read line 59" reflex, measured.
  MANIFEST    Glob / directory listing. The answer is a file list: tiny, static,
              and knowable before the session starts.
  EXPANSION   Read into a diff file but AWAY from every hunk -- predictable in file,
              not in location; pre-serving means shipping the whole file.
  OFF_DIFF    Read/Grep of a file no diff mentions: the reviewer chasing a seam into
              unchanged code. Not derivable.
  DISCOVERY   repo-wide Grep, no path. Answers "what else exists" -- structurally
              underivable from a diff, which shows only what changed.

ECONOMICS use the receipt anchor (cache_read + cache_creation IS the exact prefix
size at that request). Pre-serving pays iff

    preload_tokens * rounds_it_now_rides * READ  <  rounds_killed * mean_window * READ

which is why big-and-late loses even when perfectly derivable: a preload pays rent
every remaining round.

Usage:
    python3 analyze_reviewer.py [--logs DIR] [--session ID] [--shape S] [--json OUT]
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from glob import glob

READ, WRITE, OUT = 0.5e-6, 6.25e-6, 25e-6   # opus-5 USD/token
CPT = 2.98                                   # measured message-JSON density

DEFAULT_LOGS = os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs"
)

DIFF_RE = re.compile(r"diff --git a/(\S+)")
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
LINENO_RE = re.compile(r"^\s*\d+\t")         # Read prefixes lines with "  NNN\t"
DIFFISH = re.compile(r"\.(diff|patch)$")


def seq_of(path):
    return int(os.path.basename(path).split("-")[0])


def load_body(path):
    with open(path) as fh:
        blob = json.load(fh)
    return blob.get("body") or blob


def usage_of(req_path):
    resp = req_path.replace(".request.json", ".response.json")
    if not os.path.exists(resp):
        return {}
    try:
        with open(resp) as fh:
            return json.load(fh).get("usage") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        return "".join(x.get("text", "") for x in content if isinstance(x, dict))
    return content or ""


def parse_diff(text):
    """-> {path: [(start, n_lines)]}. Tolerates Read's line-number prefix."""
    hunks, cur = {}, None
    for ln in (LINENO_RE.sub("", x) for x in text.split("\n")):
        m = DIFF_RE.match(ln)
        if m:
            cur = m.group(1)
            hunks.setdefault(cur, [])
            continue
        m = HUNK_RE.match(ln)
        if m and cur:
            hunks[cur].append((int(m.group(1)), int(m.group(2) or 1)))
    return hunks


class Session:
    def __init__(self, sid, req_paths):
        self.sid = sid
        self.reqs = req_paths
        self.ok = False
        self.shape = "unknown"
        self._build()

    # ---------- construction ----------

    def _build(self):
        body = load_body(self.reqs[-1])
        msgs = body.get("messages", [])
        self.roster = sorted(t["name"] for t in body.get("tools", []))
        if len(msgs) < 3:
            self.shape = "aborted"
            return

        self.results, self.errors = {}, {}
        for m in msgs:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for bl in c:
                if bl.get("type") == "tool_result":
                    self.results[bl.get("tool_use_id")] = result_text(bl)
                    self.errors[bl.get("tool_use_id")] = bool(bl.get("is_error"))

        self.rounds = []
        for m in msgs:
            if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
                continue
            tus = [b for b in m["content"] if b.get("type") == "tool_use"]
            if tus:
                self.rounds.append(tus)
        self.calls = [t for r in self.rounds for t in r]
        if not self.calls:
            self.shape = "aborted"
            return

        self._find_diff(msgs)
        self._roots()
        self._classify()
        self._economics()
        self.ok = True

    def _find_diff(self, msgs):
        """Locate the diff whatever route it arrived by, and name the shape."""
        parts, src, from_artifact = [], None, False
        for tu in self.calls:
            txt = self.results.get(tu["id"], "")
            if txt.count("diff --git") < 2:
                continue
            inp = tu.get("input") or {}
            tgt = inp.get("file_path") or ""
            if DIFFISH.search(tgt):                      # a .diff artifact on disk
                if src is None or tgt == src:
                    parts.append(txt)
                    src, from_artifact = tgt, True
            elif not from_artifact:
                parts.append(txt)

        # A Bash call the roster does not grant comes back is_error "No such tool
        # available" -- the reviewer REACHED for git and was refused. That is a
        # burned round, not a review shape, so it must not make a session
        # "git-bash": require the call to have actually succeeded.
        self.failed_calls = sum(1 for c in self.calls if self.errors.get(c["id"]))
        self.denied_calls = sum(
            1 for c in self.calls
            if self.errors.get(c["id"])
            and "No such tool available" in self.results.get(c["id"], "")
        )
        git_bash = sum(
            1 for c in self.calls
            if c["name"] == "Bash" and not self.errors.get(c["id"])
            and "git " in json.dumps(c.get("input") or {})
        )
        blob = "\n".join(parts)
        # a diff may also ride in the brief itself
        if not blob:
            m0 = json.dumps(msgs[0]) if msgs else ""
            if m0.count("diff --git") >= 2:
                blob = m0

        self.hunks = parse_diff(blob) if blob else {}
        self.diff_files = set(self.hunks)
        self.diff_tokens = round(len(blob) / CPT)

        if from_artifact and self.diff_files:
            self.shape = "diff-file"
        elif git_bash and self.diff_files:
            self.shape = "git-bash"
        elif self.diff_files:
            self.shape = "diff-inline"
        elif git_bash:
            self.shape = "git-bash"
        else:
            self.shape = "no-diff"

    def _roots(self):
        """Project roots seen on the wire, so wire paths compare to diff paths."""
        roots = set()
        for tu in self.calls:
            inp = tu.get("input") or {}
            p = inp.get("file_path") or inp.get("path") or ""
            for f in self.diff_files:
                if p.endswith(f):
                    roots.add(p[: -len(f)])
        self.rootlist = sorted(roots, key=len, reverse=True)

    def _rel(self, path):
        if not path:
            return None
        for r in self.rootlist:
            if r in path:
                return path.split(r, 1)[1]
        return path

    # ---------- classification ----------

    def _classify(self):
        self.tagged = []
        for tu in self.calls:
            inp = tu.get("input") or {}
            name = tu["name"]
            path = self._rel(inp.get("file_path") or inp.get("path"))
            chars = len(self.results.get(tu["id"], ""))
            hunks = self.hunks.get(path or "", [])

            if name == "Glob":
                tag = "MANIFEST"
            elif name == "Read" and path in self.diff_files:
                off, lim = inp.get("offset"), inp.get("limit")
                if off is None:
                    tag = "EXPANSION"
                else:
                    lo, hi = off, off + (lim or 2000)
                    hit = any(h[0] < hi and h[0] + h[1] > lo for h in hunks)
                    tag = "DERIVABLE" if hit else "EXPANSION"
            elif name == "Grep" and path is None:
                tag = "DISCOVERY"
            else:
                tag = "OFF_DIFF"

            self.tagged.append({"tool": name, "path": path, "tag": tag,
                                "chars": chars})

        # a round dies only if EVERY call in it is servable
        self.servable_rounds = 0
        self.manifest_rounds = 0
        i = 0
        for r in self.rounds:
            tags = [self.tagged[i + k]["tag"] for k in range(len(r))]
            i += len(r)
            if tags and all(t in ("DERIVABLE", "MANIFEST") for t in tags):
                self.servable_rounds += 1
            if tags and all(t == "MANIFEST" for t in tags):
                self.manifest_rounds += 1

    # ---------- economics ----------

    def _economics(self):
        self.prefixes, self.out_tokens, self.unpriced = [], 0, 0
        read = write = 0
        for p in self.reqs:
            if len(load_body(p).get("messages", [])) < 2:
                continue
            u = usage_of(p)
            r = u.get("cache_read_input_tokens") or 0
            w = u.get("cache_creation_input_tokens") or 0
            if r + w == 0:
                # No response receipt: in-flight at capture time, or a failed
                # request. There is no anchor, so this is not a priced round --
                # counting it as a 0-token round would deflate mean_window and
                # invent a free round trip.
                self.unpriced += 1
                continue
            self.prefixes.append(r + w)      # receipt anchor = exact prefix size
            read += r
            write += w
            self.out_tokens += u.get("output_tokens") or 0
        self.read, self.write = read, write
        self.cost = read * READ + write * WRITE + self.out_tokens * OUT
        self.n_rounds = len(self.prefixes)
        self.mean_window = sum(self.prefixes) / max(len(self.prefixes), 1)

    def preserve(self, tags=("DERIVABLE", "MANIFEST")):
        pre = sum(t["chars"] for t in self.tagged if t["tag"] in tags) / CPT
        killed = self.servable_rounds if len(tags) > 1 else self.manifest_rounds
        rides = max(self.n_rounds - killed, 1)
        cost = pre * rides * READ
        save = killed * self.mean_window * READ
        return {"preload_tokens": round(pre), "rounds_killed": killed,
                "carriage_usd": round(cost, 4), "saved_usd": round(save, 4),
                "net_usd": round(save - cost, 4), "wins": save > cost}

    def batch_gain(self):
        """Pair up single-call rounds; compounding model on real deltas."""
        if len(self.prefixes) < 3:
            return 0.0
        d = [self.prefixes[0]] + [b - a for a, b in
                                  zip(self.prefixes, self.prefixes[1:])]

        def run(seq):
            win, rd = seq[0], 0
            for x in seq[1:]:
                rd += win
                win += x
            return (rd + win) * READ + win * WRITE + self.out_tokens * OUT

        base = run(d)
        k = max(len(d) - self.solo // 2, 2)
        per = sum(d[1:]) / (k - 1)
        return base - run([d[0]] + [per] * (k - 1))

    @property
    def solo(self):
        return sum(1 for r in self.rounds if len(r) == 1)

    def row(self):
        c = Counter(t["tag"] for t in self.tagged)
        return {"session": self.sid, "shape": self.shape, "rounds": self.n_rounds,
                "prefixes": self.prefixes,
                "tools": dict(Counter(t["tool"] for t in self.tagged)),
                "round_sizes": [len(r) for r in self.rounds],
                "result_chars": sum(t["chars"] for t in self.tagged),
                "unpriced_rounds": self.unpriced,
                "failed_calls": self.failed_calls,
                "denied_calls": self.denied_calls,
                "out_tokens": self.out_tokens, "write_tokens": self.write,
                "calls": len(self.calls), "solo_rounds": self.solo,
                "roster": len(self.roster), "diff_tokens": self.diff_tokens,
                "derivable": c["DERIVABLE"], "manifest": c["MANIFEST"],
                "expansion": c["EXPANSION"], "off_diff": c["OFF_DIFF"],
                "discovery": c["DISCOVERY"], "servable_rounds": self.servable_rounds,
                "manifest_rounds": self.manifest_rounds, "read_tokens": self.read,
                "final_window": self.prefixes[-1] if self.prefixes else 0,
                "mean_window": round(self.mean_window),
                "cost_usd": round(self.cost, 3), "preserve": self.preserve(),
                "batch_usd": round(self.batch_gain(), 3)}


def discover(logs, pattern="*reviewer*.request.json"):
    by = defaultdict(list)
    for p in glob(os.path.join(logs, "*", pattern)):
        sid = os.path.basename(os.path.dirname(p))
        if not sid.startswith("_"):
            by[sid].append(p)
    return {s: sorted(v, key=seq_of) for s, v in by.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--session")
    ap.add_argument("--shape", help="filter: diff-file|diff-inline|git-bash|no-diff")
    ap.add_argument("--json")
    args = ap.parse_args()

    sessions = discover(args.logs)
    if args.session:
        sessions = {k: v for k, v in sessions.items() if k.startswith(args.session)}

    rows, aborted = [], 0
    for sid, paths in sorted(sessions.items(), key=lambda kv: -len(kv[1])):
        try:
            s = Session(sid, paths)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            print(f"  ! {sid[:8]}: {type(e).__name__} {e}", file=sys.stderr)
            continue
        if not s.ok:
            aborted += 1
            continue
        if args.shape and s.shape != args.shape:
            continue
        rows.append(s.row())

    if not rows:
        print("no analysable reviewer sessions", file=sys.stderr)
        return 1

    print(f"analysed {len(rows)} reviewer sessions "
          f"({aborted} aborted: no tool calls at all)\n")

    hdr = (f"{'session':10}{'shape':13}{'rds':>4}{'calls':>6}{'solo':>5}"
           f"{'deriv':>6}{'manif':>6}{'exp':>5}{'off':>5}{'disc':>5}"
           f"{'window':>8}{'$':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda r: -r["cost_usd"]):
        print(f"{r['session'][:8]:10}{r['shape']:13}{r['rounds']:4}{r['calls']:6}"
              f"{r['solo_rounds']:5}{r['derivable']:6}{r['manifest']:6}"
              f"{r['expansion']:5}{r['off_diff']:5}{r['discovery']:5}"
              f"{r['final_window']:8}{r['cost_usd']:7.2f}")

    print(f"\n=== by shape ===")
    print(f"{'shape':14}{'n':>3}{'calls':>7}{'$':>8}{'$/sess':>8}"
          f"{'deriv%':>8}{'manif%':>8}{'off%':>7}{'disc%':>7}")
    for shape in ("diff-file", "diff-inline", "git-bash", "no-diff"):
        g = [r for r in rows if r["shape"] == shape]
        if not g:
            continue
        n = sum(r["calls"] for r in g)
        cost = sum(r["cost_usd"] for r in g)
        print(f"{shape:14}{len(g):3}{n:7}{cost:8.2f}{cost/len(g):8.2f}"
              f"{sum(r['derivable'] for r in g)/n*100:8.1f}"
              f"{sum(r['manifest'] for r in g)/n*100:8.1f}"
              f"{sum(r['off_diff'] for r in g)/n*100:7.1f}"
              f"{sum(r['discovery'] for r in g)/n*100:7.1f}")

    tot_calls = sum(r["calls"] for r in rows)
    tot_cost = sum(r["cost_usd"] for r in rows)
    tot_rounds = sum(r["rounds"] for r in rows)
    solo = sum(r["solo_rounds"] for r in rows)
    agg = Counter()
    for r in rows:
        for k in ("derivable", "manifest", "expansion", "off_diff", "discovery"):
            agg[k] += r[k]

    print(f"\n=== corpus: {tot_calls} calls, {tot_rounds} rounds, ${tot_cost:.2f} ===")
    for k in ("derivable", "manifest", "expansion", "off_diff", "discovery"):
        print(f"  {k:10} {agg[k]:5}  {agg[k]/tot_calls*100:5.1f}%")
    print(f"\n  single-call rounds: {solo}/{tot_rounds} ({solo/tot_rounds*100:.1f}%)")
    print(f"  read carriage: {sum(r['read_tokens'] for r in rows):,} tok = "
          f"${sum(r['read_tokens'] for r in rows)*READ:.2f}")
    mw = statistics.median(r["mean_window"] for r in rows)
    print(f"  median mean-window {mw:,.0f} -> one round trip ${mw*READ:.4f}")

    print(f"\n=== LEVER 1: pre-serve DERIVABLE reads + MANIFEST globs ===")
    wins = [r for r in rows if r["preserve"]["wins"]]
    net = sum(r["preserve"]["net_usd"] for r in rows)
    print(f"  pays in {len(wins)}/{len(rows)} sessions; net ${net:+.2f} "
          f"({net/tot_cost*100:+.1f}% of spend)")
    print(f"  rounds it could kill: {sum(r['servable_rounds'] for r in rows)}"
          f"/{tot_rounds}")

    print(f"\n=== LEVER 2: pair up single-call rounds (no information lost) ===")
    b = sum(r["batch_usd"] for r in rows)
    print(f"  ${b:.2f} of ${tot_cost:.2f} ({b/tot_cost*100:.1f}%)")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=1)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
