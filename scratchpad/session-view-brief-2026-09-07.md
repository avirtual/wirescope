# Briefing after context clear — 2026-09-07 ~20:50 (wirescope, fable 5.1)

## Carry-over state (nothing pending on these)
- v0.6.59 (proactive OAuth refresh, 3c54f6c) and v0.6.60 (probe 429s → `last_429_probe`, 9f4d4ca) are cut, pushed, vendored by clodex; **:7800 is LIVE on v0.6.60** (GUI-restarted ~20:07, pid changed). HANDOFF.local.md top two entries have the full record.
- 12:26 token lapse was handled live (refresh fired 19s after expiry, bootstrap $0.012, clodex holds pinged warmed). The 20:26 lapse never happened: a real turn at 20:21:40 refreshed early (CLI refreshes when a real request lands inside ~5 min of expiresAt). **Next real (idle-night) exercise = 04:21:40 tomorrow**; a reminder is armed for 04:35 with the exact checks. Silent unless it diverges.
- Standing constraints: never restart :7800 (Bogdan's call); never edit CLAUDE.md mid-session; scratch-port teardown = listener pid only; release = `./release.sh vX.Y.Z HEAD` then `git push origin main`.

## THE NEW TASK (Bogdan's words, paraphrased closely)
Look into whether the session pages (the HTML views: `/_session?session=`, also `/_admin`, `/_timeline`) can be improved — "they look a bit dated". He wants to understand how we generate that HTML output.
Core idea: a normal session keeps growing; every API request re-ships the whole payload (all prior blocks + the new block). Because the view is always regenerated from a whole request body, it is hard to say WHEN a given block/request happened — it is part of every following request (unless the cache is busted). His thought: hold the per-request additions as BLOCKS. A new request is technically the previous blocks plus a new delta, so if we tracked deltas we could show when each block appeared (a timestamp per block).
His guardrail: "if it is a dumb idea that will kill the server, we don't do it — only if you think this adds any value." So: assess honestly first (cost on the request path, memory, disk), then propose/implement only if it pays.

## Where to start (facts I already know)
- HTML rendering lives in `proxylab/views.py` (~28k: /_admin + /_session + /_timeline). `/_session` renders the REPLAYABLE LAST REQUEST from `pinger._LAST_REQUEST` (in-memory, main-line only) as a turn-grouped timeline + last answer. So today it is a snapshot of one body, no per-message timing.
- Disk captures already exist per request: `<LOG_DIR>/<session>/<seq>-<agent>-<role>-<model>-<HHMMSS>.request.json` (has `ts`, `body`, `summary`) and `.response.json`/`.response.sse`. So the "when did block N first appear" answer is DERIVABLE OFFLINE from the capture sequence without holding anything new in memory: the first request whose messages[] contains a message is that message's birth time. `/_report?detail=1` and `/_timeline` already do disk-based per-request series.
- Warmth module already hashes prefixes (`warmth._canon_message`, prefix hashing) — a per-message canonical hash is the natural block identity; cache identity is prefix-based and byte-exact, string ≡ block form.
- Design principle from CLAUDE.md: the handler only parses + enqueues; disk I/O on the writer thread; stay non-intrusive. Any per-request delta tracking must be O(new messages), not O(history) on the hot path — or better, computed lazily at view time from captures (the `/_context?utilization=1` precedent: opt-in disk scan off the fast path).
- Prior-turn thinking strip / folds / relocations mean the forwarded body differs from the received one; the captured `body` is the received (pre-transform) one — check which one views render.

## Suggested plan
1. Read `proxylab/views.py` (render path for /_session) + how `pinger._LAST_REQUEST` and `status._last_assistant_activity` feed it; skim `report.py` series for the per-request disk walk.
2. Prototype an offline "block birth" derivation on one real session dir in `~/Library/Application Support/clodex/wirescope/logs/<sid>` (e.g. clodex coordinator 5383fbbc or a hand seat): for each request in seq order, hash each message (canonical), record first-seen ts; measure cost (time per session, number of files) and how often a message's content is REWRITTEN (CLI block→string rewrites, strips) so identity would need to be robust.
3. Decide: (a) view-time derivation from captures (zero hot-path cost, works for cold sessions) vs (b) an in-memory per-session `block_birth` map updated on the request path (O(new tail) per request if keyed by index+hash). Recommend, then implement the modest version + a visual refresh of the page if worthwhile.
4. Report to Bogdan with the assessment before any big rewrite; small improvements can just ship.
