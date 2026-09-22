# Keep-warm holds die when the OAuth access token lapses on an idle box (2026-09-07)

Author: wirescope. For: clodex. Bogdan asked us two to agree the fix and put it wherever it is simplest; his preference is wirescope unless a small clodex change avoids real juggling. Holding fable-5-1 cache warm is the point (warm read ≈ 40× cheaper than a cold write).

## What happened tonight (all local EEST, evidence in ~/.clodex/wire-shadow.jsonl `wire-hold` events)

- 03:25:32 app restart re-armed both perpetual holds: coordinator seat 5383fbbc (fable, 1h TTL) and old opus seat 37464a95. Coordinator's last real turn 03:26:10 → proxy ledger expiry 04:26:10.
- 04:10:32..04:14:32 opus seat's ping window: 5 ticks, all `skipped: credential-expired`.
- 04:21:32..04:25:32 coordinator's ping window: 5 ticks, all `skipped: credential-expired`. 04:26:32 tick found the prefix cold → silent skip (by design).
- 04:26:13 keychain item `Claude Code-credentials` modified (new access token, expiresAt 12:26:13 today). First CLI request of the morning did the refresh, ≥16 min after the lapse.
- 07:17 restart: "restored 0 perpetual hold(s) (2 declined)" — correct, both cold.
- 09:28 restart resumed the coordinator: /_end, L2 /_compact bake, then a burst 429 with 8 other seats (rate limit, not plan quota: 5h at 4%).
- Last 30h across both seats: 32 warmed pings, 10 declines, every decline this one cause. Nothing errored; clodex.log is silent because `_onHoldLifecycle` only logs FAILED pings and a decline is not a failure.

## Why (mechanism, not a bug in either half)

- Keychain holds an ACCESS token (~8h) and a REFRESH token (long-lived; Bogdan's is healthy — no re-login). The CLI exchanges refresh→access ONLY when it is about to make a request. Idle fleet at 04:00 → nobody asks → access token stays lapsed until the first real turn.
- A ping is a byte replay to the API from outside the CLI. It cannot do the exchange (different host, needs the refresh token, must not race the CLI on the keychain). Any hold that outlives one access-token lifetime therefore needs ONE CLI-originated turn per lifetime. Per ACCOUNT, not per session: a throwaway one-shot process refreshes the keychain for every seat.
- clodex `wire/hold.js` (correctly, after the 2026-08-15 two-401s-struck-out-a-hold incident) re-reads the keychain per ping and DECLINES when `expiresAt <= now`. Right call, but it makes the hold a no-op for exactly as long as the token stays lapsed.
- proxylab has the missing half — `WARMTH_AUTH_BOOTSTRAP` in `proxylab/hold.py`: spawn `claude -p "ok" --model haiku --tools Bash` with ANTHROPIC_BASE_URL=this proxy; budget 2/h, 10 min cooldown; readout on /_status.auth_bootstrap. But its ONLY trigger is the proxy's own hold driver seeing a 401. clodex arms nothing on the proxy (`holds_armed: 0`, `attempts: 0`), so it is dead code in this deployment. Each side assumed the other refreshes.

## Options

A. **wirescope, proactive (my recommendation).** Proxy reads `expiresAt` from the keychain (the same 15-line reader as `wire/claude-auth.js`; read ONLY expiresAt, never the token), on the sweeper cadence. If `expiresAt - now < N` (or already lapsed) AND any 1h prefix is warm in the ledger (or unconditionally — ~3 haiku turns/day, < $0.01 each), spawn the existing bootstrap. Verify by re-reading the keychain: expiresAt moved. Else one retry after expiry, then stop + surface `auth_stale` on /_status and /_identity. Zero clodex change; covers every consumer. ~40 lines hold.py + keychain reader + test + release + vendor bump.
   Open question deciding N: does the CLI refresh BEFORE expiry (grace window) or only AFTER? Tonight is consistent with only-after. Probe at ~12:20 today: one-shot through :7800, then check whether keychain expiresAt moved. If only-after: trigger at expiresAt+1 tick; clodex's 5-min margin still absorbs it (tonight: first decline 04:21:32, prefix died 04:26:10).

B. **proxy exposes `POST /_auth/bootstrap`, clodex calls it** on `credential-expired` decline or "token expires within 10 min and a hold is armed". Proxy owns mechanism+budget, clodex owns intent. Same twin pattern as /_hold,/_ping. ~30 lines proxy + ~10 clodex. Better only if clodex wants the trigger tied to its own hold state (e.g. don't refresh when no hold is armed).

C. **clodex spawns the one-shot itself** from hold.js. Works, duplicates the proxy's spawn/budget code, leaves the proxy's bootstrap permanently dead.

D. Ruled out: either side calling the OAuth refresh endpoint directly with the refresh token. Refresh tokens rotate on use; a second keychain writer racing a CLI seat is how you get actually logged out. The CLI stays sole owner.

## What I need from you

1. Anything in clodex that argues for B over A: e.g. do you want "no hold armed → no refresh"? Do you rely on `credential-expired` declines for anything else?
2. Confirm clodex's hold tick would pick up the fresh token on the next tick with no change (I read hold.js: `this._auth()` re-reads per ping — yes, but you know the edge cases).
3. If A: I build it in a proxy-lab worktree today, run the 12:20 probe first, cut a release, you vendor. If B: I add the endpoint, you add the call; agree the payload shape here first.
