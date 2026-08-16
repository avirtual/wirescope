#!/usr/bin/env python3
"""`_hold_tick` — the auth/failure SPLIT, and the bootstrap budget's reset.

WHY THIS SUITE EXISTS (2026-08-16). Clodex lost a keep-warm seat for ten hours:
its ping replays a request with the bearer captured at that turn, an OAuth token
lives ~8h, and on an idle seat it goes stale mid-hold. Two 401s a minute apart
spent a 2-strike disarm budget and the hold sat dead.

wirescope survives that case — but the property that saves it lives ENTIRELY in
`_hold_tick`, which had NO test. `_hold_decision` (pure, well covered) and
`_bootstrap_decision` (pure, well covered) both looked green while the branch
carrying the actual guarantee was unasserted. That is the gap this file closes:

  * A 401 IS NOT A FAILURE STRIKE. `failures` counts things that will not fix
    themselves; a stale bearer fixes itself the moment any traffic re-donates.
    Feed one into the other and a recoverable gap becomes a permanent disarm —
    exactly Clodex's bug, which was a PORT of this code minus this branch.
  * A CLEAN DECLINE IS NOT A FAILURE EITHER (racing to cold is normal).
  * A REAL failure still strikes, and two still disarm — the budget must keep
    working for what it was written for, or "don't strike on 401" is just a
    disabled safety net.

Plus the defect the same review found: the bootstrap budget is documented as
per-OUTAGE but `attempts` was only ever reset by a SUCCESSFUL donation, so a run
of FAILED spawns spent it for the process lifetime — the hold then never disarms
(no strikes, by the rule above) and never recovers, while /_status reports
`armed:true`. Silent, and worse than dying loudly.

Run: python3 test_hold_tick.py   (no live ports, no upstream, no credits)
"""
import asyncio
import os
import sys
import tempfile
import time

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="holdtick_logs_")
os.environ["WARMTH_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="holdtick_db_"), "warmth.sqlite")
os.environ["WARMTH_LEDGER"] = "1"

import logproxy as lp  # noqa: E402  (env must be set before import)
from proxylab import hold as hold_mod, pinger as pinger_mod, warmth as warmth_mod  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


SID = "sess-tick-1"
ACCT = "acct-tick"
BODY = {"model": "claude-sonnet-5", "max_tokens": 16,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}


def _arm(**over):
    """An armed hold that _hold_decision will resolve to 'ping'."""
    now = time.time()
    h = {"until": now + 3600, "armed_at": now, "hours": 1.0, "pings": 0,
         "failures": 0, "last_ping_ts": None, "last_result": None}
    h.update(over)
    with hold_mod._HOLD_LOCK:
        hold_mod._HOLD_STATE[SID] = h
    with pinger_mod._LAST_REQUEST_LOCK:
        pinger_mod._LAST_REQUEST[SID] = {
            "obj": dict(BODY), "headers": {"authorization": "Bearer X"},
            "path": "/v1/messages", "ts": now, "account": ACCT,
            "needs_auth": False}
    return h


def _state():
    return hold_mod._hold_snapshot().get(SID)


# A prefix that is warm and DUE (inside MARGIN) -> the decision says 'ping'.
def _warm_due(_hashes):
    now = time.time()
    return {h: (now - 60, 3600, now + hold_mod.WARMTH_HOLD_MARGIN - 30)
            for h in _hashes}


warmth_mod._warmth_rows = _warm_due          # no ledger I/O in this suite
_BOOTSTRAPS = []


async def _fake_bootstrap(account=None):
    _BOOTSTRAPS.append(account)

hold_mod._auth_bootstrap = _fake_bootstrap


def _run_tick(ping_result):
    """One tick with `_warm_session` stubbed to a given outcome."""
    async def _fake_warm(session_id, force=False):
        return ping_result
    pinger_mod._warm_session = _fake_warm

    async def _go():
        await hold_mod._hold_tick()
        await asyncio.sleep(0)      # let the created bootstrap task run
    asyncio.run(_go())


# -- THE PROPERTY CLODEX'S PORT LOST ------------------------------------------
print("\n[a 401 is not a failure strike]")
_BOOTSTRAPS.clear()
_arm()
_run_tick((401, {"ok": False, "warmed": False, "auth_stale": True,
                 "status_code": 401}))
s = _state()
check("a stale-auth ping leaves `failures` at 0 (recoverable != failed)",
      s["failures"] == 0, f"failures={s['failures']}")
check("...and is recorded distinctly, not as fail:<code>",
      s["last_result"] == "auth_stale", str(s["last_result"]))
check("...and the hold stays ARMED", SID in hold_mod._hold_snapshot())
check("...and a bootstrap is kicked off to close the gap",
      _BOOTSTRAPS == [ACCT], str(_BOOTSTRAPS))

# Clodex's exact sequence: two 401s a minute apart. Under their port this
# disarmed; here it must not.
_BOOTSTRAPS.clear()
_arm()
for _ in range(4):
    _run_tick((401, {"ok": False, "warmed": False, "auth_stale": True,
                     "status_code": 401}))
check("FOUR consecutive 401s still do not disarm (the ported-bug scenario)",
      SID in hold_mod._hold_snapshot() and _state()["failures"] == 0,
      str(_state()))

# -- ...but the budget must still work for what it was written for -------------
print("\n[a real failure still strikes]")
_arm()
_run_tick((502, {"ok": False, "warmed": False, "status_code": 502}))
check("a genuine failure DOES increment `failures`", _state()["failures"] == 1)
check("...and is labelled with its code", _state()["last_result"] == "fail:502")
_run_tick((502, {"ok": False, "warmed": False, "status_code": 502}))
check("a second real failure reaches the strike budget", _state()["failures"] == 2)
# The disarm is evaluated at the START of a tick (_hold_decision), so it lands on
# the FOLLOWING one — and must do so WITHOUT spending another ping, since the
# decision short-circuits before the ping branch.
_pings_before = _state()["pings"]
_run_tick((200, {"ok": True, "warmed": True}))   # would succeed if it ran at all
check("two consecutive real failures disarm on the next tick (budget intact)",
      SID not in hold_mod._hold_snapshot(), str(_state()))
check("...and the disarm spends no further ping",
      _pings_before == 2, str(_pings_before))

print("\n[a clean decline is not a failure]")
_arm()
_run_tick((200, {"ok": True, "warmed": False, "skipped": "cold"}))
check("a warm-only decline leaves `failures` at 0", _state()["failures"] == 0)
check("...and records what it declined on",
      _state()["last_result"] == "declined:cold", str(_state()["last_result"]))

print("\n[a success resets the strike count]")
_arm(failures=1)
_run_tick((200, {"ok": True, "warmed": True}))
check("a warmed ping clears prior strikes", _state()["failures"] == 0)
check("...and counts against the ping budget", _state()["pings"] == 1)
hold_mod._arm_hold(SID, "off", None)

# -- THE BUDGET DEFECT: per-OUTAGE, not per-PROCESS ---------------------------
print("\n[bootstrap budget resets after an outage window]")
NOW = time.time()
BST = {"attempts": hold_mod._AUTH_BOOTSTRAP_MAX, "last_ts": NOW,
       "inflight": False, "last_reason": None, "spawns": 0}
check("a spent budget declines DURING the outage",
      hold_mod._bootstrap_decision("acct-new", now=NOW, state=dict(BST))[0] is False)
stale_ts = NOW - hold_mod._AUTH_BOOTSTRAP_RESET - 1
check("a budget spent on a FINISHED outage is fresh again (the defect)",
      hold_mod._bootstrap_decision(
          "acct-new", now=NOW, state={**BST, "last_ts": stale_ts})[0] is True)
check("...and the reset window is longer than the cooldown (it must dominate)",
      hold_mod._AUTH_BOOTSTRAP_RESET > hold_mod._AUTH_BOOTSTRAP_COOLDOWN)
# CONTROL — could this come back True while the subject fails? The reset must
# not resurrect a budget merely because the process is old: with attempts=0 the
# staleness branch is irrelevant, and an account that HAS auth still declines.
with pinger_mod._LAST_REQUEST_LOCK:
    pinger_mod._ACCOUNT_AUTH["acct-has"] = {"authorization": "Bearer live"}
check("CONTROL: a stale timestamp does not override 'auth already present'",
      hold_mod._bootstrap_decision(
          "acct-has", now=NOW, state={**BST, "last_ts": stale_ts})
      == (False, "auth already present (resolve instead)"))
check("CONTROL: the kill switch still wins over a stale budget",
      (setattr(hold_mod, "WARMTH_AUTH_BOOTSTRAP", False),
       hold_mod._bootstrap_decision("acct-new", now=NOW,
                                    state={**BST, "last_ts": stale_ts})[0],
       setattr(hold_mod, "WARMTH_AUTH_BOOTSTRAP", True))[1] is False)

print("\n[the stuck hold is now VISIBLE]")
hold_mod._AUTH_BOOTSTRAP.update(attempts=hold_mod._AUTH_BOOTSTRAP_MAX,
                                last_ts=time.time(), spawns=3,
                                inflight=False, last_reason="go")
snap = hold_mod._bootstrap_snapshot()
check("a spent budget reports budget_spent=True (the silent half of the defect)",
      snap["budget_spent"] is True, str(snap))
check("...and carries the spawn count + age for diagnosis",
      snap["spawns"] == 3 and snap["age_s"] is not None)
hold_mod._AUTH_BOOTSTRAP.update(attempts=0, last_ts=0.0, spawns=0,
                                last_reason=None)
check("a fresh process reports budget_spent=False",
      hold_mod._bootstrap_snapshot()["budget_spent"] is False)
check("/_status surfaces it under proxy.auth_bootstrap",
      "budget_spent" in (lp.status._status_snapshot()
                         .get("proxy", {}).get("auth_bootstrap") or {}))

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("ALL PASS")
