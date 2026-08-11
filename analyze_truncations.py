#!/usr/bin/env python3
"""Mid-stream truncation analysis: how often does a forwarded stream never finish,
and can the cuts be attributed to an externally-recorded event (e.g. app reboots)?

Read-only over a capture dir. Two modes:

  python3 analyze_truncations.py <LOG_DIR>                    # baseline rate
  python3 analyze_truncations.py <LOG_DIR> --events ev.txt    # + join against events

`--events` takes one ISO-8601 UTC timestamp per line (blank lines and #comments
ignored) produced by a SEPARATE system — the point of the join is that the two
records are independently produced. See "WHY A JOIN" below.

THE PREDICATE (this is the whole analysis; everything else is bookkeeping).
A "genuine mid-stream cut" = the capture STARTED an SSE stream (`message_start`
in the head) and never finished it (no `message_stop` in the tail). Reaching
that took four passes, and EVERY wrong one INFLATED the number — a too-loose
predicate finds the thing you are looking for, which is exactly when nobody
audits it. The three false-positive classes it must exclude:

  1. API errors  — rate_limit_error / overloaded_error legitimately never
     complete a stream. Naive "no message_stop" counted these: 4.79%.
  2. The codex/OpenAI wire — terminates with `response.completed`, NOT
     `message_stop`, so every codex capture looked truncated. -> 0.392%.
  3. OUR OWN /_ping warmth replays — non-streaming JSON bodies (`content:[]`,
     `output_tokens:1`, `stop_reason:max_tokens`). They never had a stream to
     cut. Counting them attributed our own pings to someone else's bug.

Requiring `message_start` excludes (1) and (3) structurally rather than by
pattern-matching their symptoms, which is why it is the predicate that survived.
Result on the live corpus 2026-08-11: 173/76,709 = 0.226%.

WHY A JOIN, AND NOT A HARDER LOOK AT ONE CORPUS.
A single vantage point supports a plausible wrong story indefinitely. This
corpus knows when streams died and nothing about why; the consumer's log knew
when reboots fired and nothing about streams. Reasoning from either alone
produced a confident wrong answer from BOTH sides (we each tested for the
signature our own data could show). Crossing them settled it in one pass.
So: when a cause lives outside your data, get the other record and join.

NEGATIVE CONTROL — always run it, and especially when the result CONFIRMS the
hypothesis of whoever handed you the events. `--events` reshuffles the event
timestamps uniformly over the observed span N times and reports how many
matches chance alone produces. Live run: observed 28 vs chance median 0, p95 1.
Without this the association is an anecdote.

THE DENOMINATOR IS A CLAIM ABOUT WHO WAS ELIGIBLE, and it needs the same audit
as the predicate. Three ways to get it wrong, all met live:

  1. POPULATION MIXING — a numerator counted over app-managed seats only,
     divided by ALL cuts including standalone/cron seats the mechanism cannot
     touch. `--managed-prefix` filters the denominator to match the numerator;
     they must be the same population or the ratio means nothing.
  2. RIGHT-CENSORING — cuts outside the event log's coverage are UNOBSERVABLE,
     not absent. Quoting matches over the whole corpus silently assumes the
     censored region contains zero, false at any nonzero base rate. Hence the
     in-window rate is the point estimate and the whole-corpus rate is printed
     explicitly as a LOWER BOUND.
  3. INELIGIBILITY — the subtle one, and NOT the same as censoring. A period
     where the mechanism could not fire AT ALL (feature not yet shipped, cause
     disabled) contains no candidates, so including it dilutes the denominator
     with cases that were never at risk. Live: the consumer's log covered from
     07-08 and every cut in it was observed, but the reboot intent did not exist
     until 07-20 — 94 was "observed and unmatched" (defensible, wrong), 69 was
     "eligible" (right). `--eligible-from/--eligible-to` set this explicitly;
     it DEFAULTS to the first event, which is usually right and sometimes not.

Note what settled #3: a COMMIT DATE in the other system's repo. Eligibility is a
fact about the system under study, not about the measurement, so it lives in that
system's record and cannot be derived from capture data at any level of care.
Ask the other side "when could this first have happened?" — do not infer it.
"""
import argparse, datetime as dt, glob, os, random, statistics, sys, time

HEAD_BYTES, TAIL_BYTES = 400, 3000


def classify(path):
    """-> 'cut' | 'complete' | 'error' | 'non_stream' | 'other_wire' | None"""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return None
        name = os.path.basename(path)
        # the codex/OpenAI wire ends with response.completed, not message_stop
        if "-codex-" in name or "-ws-" in name:
            return "other_wire"
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES).decode("utf-8", "replace")
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
        if '"type":"error"' in head or "event: error" in head:
            return "error"
        if "message_start" not in head:
            return "non_stream"      # /_ping replay or other non-streaming body
        return "complete" if "message_stop" in tail else "cut"
    except OSError:
        return None


def scan(log_dir):
    counts, cuts = {}, []
    for path in glob.glob(os.path.join(log_dir, "*", "*.response.sse")):
        kind = classify(path)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "cut":
            cuts.append((os.path.getmtime(path), os.path.basename(path)))
    cuts.sort()
    return counts, cuts


def is_managed(basename, prefix):
    """Seats managed by the consumer app vs standalone/cron agents."""
    part = basename.split("-", 1)[1] if "-" in basename else basename
    return part.startswith(prefix)


def parse_ts(tok):
    """ISO-8601 UTC -> epoch seconds, or None. Accepts a bare date."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return (dt.datetime.strptime(tok, fmt)
                    .replace(tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def load_events(path):
    out = []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for tok in line.replace(",", " ").split():
            ts = parse_ts(tok)
            if ts is not None:
                out.append(ts)
    return sorted(out)


def join(cuts, events, window, trials=2000, seed=7, elig_from=None, elig_to=None):
    """Cuts landing within `window` seconds AFTER an event, vs chance.

    The ELIGIBLE window bounds the denominator: only cuts that could possibly
    have been caused by the mechanism count. Defaults to the event span, which
    assumes the mechanism existed exactly as long as the events do — override
    when the other system can tell you otherwise (see module docstring #3).
    """
    lo = elig_from if elig_from is not None else events[0] - 3600
    hi = elig_to if elig_to is not None else events[-1] + 86400
    in_window = [c for c in cuts if lo <= c[0] <= hi]
    matched = []
    for ts, name in in_window:
        for ev in events:
            if 0 <= ts - ev <= window:
                matched.append((ts - ev, name, ev))
                break
    # negative control: same test against randomly placed events
    if in_window:
        span_lo = min(c[0] for c in in_window)
        span_hi = max(c[0] for c in in_window)
        rng = random.Random(seed)
        draws = []
        for _ in range(trials):
            fake = [rng.uniform(span_lo, span_hi) for _ in events]
            draws.append(sum(1 for ts, _ in in_window
                             if any(0 <= ts - f <= window for f in fake)))
        draws.sort()
        control = (statistics.median(draws), draws[int(0.95 * len(draws))], draws[-1])
    else:
        control = (0, 0, 0)
    return in_window, matched, control


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log_dir")
    ap.add_argument("--events", help="file of ISO-8601 UTC timestamps to join against")
    ap.add_argument("--window", type=float, default=5.0)
    ap.add_argument("--managed-prefix", default="clodex-",
                    help="basename prefix marking app-managed seats")
    ap.add_argument("--eligible-from", metavar="ISO",
                    help="start of the window where the mechanism COULD fire "
                         "(e.g. the date the feature shipped). Default: first event. "
                         "Not the same as the event log's coverage — see docstring.")
    ap.add_argument("--eligible-to", metavar="ISO", help="end of that window")
    args = ap.parse_args()

    counts, cuts = scan(args.log_dir)
    started = counts.get("cut", 0) + counts.get("complete", 0)
    print(f"streams started (message_start): {started}")
    print(f"  excluded — api errors: {counts.get('error',0)}  "
          f"non-streaming bodies: {counts.get('non_stream',0)}  "
          f"other wire: {counts.get('other_wire',0)}")
    if not started:
        return 1
    print(f"GENUINE mid-stream cuts: {len(cuts)} ({100*len(cuts)/started:.3f}%)")

    managed = [c for c in cuts if is_managed(c[1], args.managed_prefix)]
    print(f"  app-managed seats: {len(managed)}   other agents: {len(cuts)-len(managed)}")

    if not args.events:
        return 0

    events = load_events(args.events)
    if not events:
        print("no parseable events", file=sys.stderr)
        return 1
    ef = parse_ts(args.eligible_from) if args.eligible_from else None
    et = parse_ts(args.eligible_to) if args.eligible_to else None
    in_window, matched, (med, p95, mx) = join(managed, events, args.window,
                                              elig_from=ef, elig_to=et)

    print(f"\n--- join: {len(events)} events, {args.window}s window ---")
    print(f"event log covers {time.strftime('%Y-%m-%d', time.localtime(events[0]))}"
          f" .. {time.strftime('%Y-%m-%d', time.localtime(events[-1]))}")
    basis = "explicit --eligible-*" if (ef or et) else "DEFAULTED to event span"
    print(f"eligibility basis: {basis}"
          + ("" if (ef or et) else "  (state it explicitly if the mechanism"
                                   " shipped later than your log starts)"))
    print(f"managed cuts INSIDE the eligible window: {len(in_window)}"
          f"  (ineligible/censored outside: {len(managed)-len(in_window)})")
    print(f"cuts attributable to an event: {len(matched)}")
    if in_window:
        # THE HONEST PAIR: in-window rate is the estimate; whole-corpus is a BOUND.
        print(f"  eligible-window : {len(matched)}/{len(in_window)} = "
              f"{100*len(matched)/len(in_window):.1f}%   <- point estimate")
        print(f"  whole-corpus    : {len(matched)}/{len(managed)} = "
              f"{100*len(matched)/len(managed):.1f}%   <- LOWER BOUND ONLY "
              f"(assumes 0 in the censored {len(managed)-len(in_window)})")
    if matched:
        lags = sorted(m[0] for m in matched)
        print(f"  lag after event : min {lags[0]:.1f}s  median "
              f"{lags[len(lags)//2]:.1f}s  max {lags[-1]:.1f}s")
        hit = len({m[2] for m in matched})
        print(f"  events with >=1 cut: {hit}/{len(events)}")
    print(f"NEGATIVE CONTROL ({args.window}s, randomized events): "
          f"median {med:.0f}, p95 {p95:.0f}, max {mx}")
    if len(matched) <= p95:
        print("  ⚠️  observed is within chance — NOT evidence of association")
    return 0


if __name__ == "__main__":
    sys.exit(main())
