#!/usr/bin/env python3
"""Proactive OAuth refresh (`WARMTH_AUTH_REFRESH`) — the idle-night hold killer.

WHY THIS SUITE EXISTS (2026-09-07). Two perpetual clodex holds died between
04:10 and 04:26: every ping tick was declined `credential-expired` (10 declines,
0 sends, nothing errored), the CLI's OAuth ACCESS token having lapsed on an idle
box. The first real CLI turn refreshed the keychain at 04:26:13 — three seconds
after the coordinator's prefix expired. wirescope's reactive bootstrap could not
help: its only trigger is a proxy-side hold's replay 401ing, and clodex arms its
holds in its own port, never here. The property this file asserts is that the
proxy refreshes the token ON ITS OWN CADENCE, gated on the token's expiry alone,
never on any hold being armed — and that it verifies the refresh by re-reading
expiry rather than trusting the spawn's exit code.

Behavioural assertions only (never source shape): a reader/bootstrap pair is
injected, and each case checks what the tick DID (spawned or not, how many
times, what it reported) — not how it is written.
"""
import asyncio
import os
import sys
import tempfile

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="authrefresh-")
os.environ["WARMTH_LEDGER"] = "1"
os.environ["WARMTH_AUTH_REFRESH"] = "1"
os.environ["WARMTH_AUTH_BOOTSTRAP"] = "1"

import logproxy as lp  # noqa: E402  (env must be set before import)
from proxylab import hold as hold_mod  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


NOW = 1_800_000_000.0


def _reset():
    hold_mod._AUTH_REFRESH.update({"expires_at": None, "checked_ts": 0.0,
                                   "read_error": None, "last_trigger_ts": 0.0,
                                   "last_outcome": None, "refreshed": 0})
    hold_mod._AUTH_BOOTSTRAP.update({"attempts": 0, "last_ts": 0.0, "inflight": False,
                                     "last_reason": None, "spawns": 0})


class Fake:
    """A credential store + a CLI in one: `exp` is what the reader returns;
    a bootstrap moves it forward by `bump` (0 = the exchange failed)."""
    def __init__(self, exp, bump=8 * 3600, fail_read=False):
        self.exp, self.bump, self.fail_read = exp, bump, fail_read
        self.spawns = 0

    def reader(self):
        if self.fail_read:
            raise RuntimeError("credential store unreadable: locked")
        return self.exp

    async def bootstrap(self, account=None):
        # mirror the real _auth_bootstrap's budget mutation so the second
        # tick sees a spent attempt (the decision gate is the real one)
        hold_mod._AUTH_BOOTSTRAP["attempts"] += 1
        hold_mod._AUTH_BOOTSTRAP["spawns"] += 1
        hold_mod._AUTH_BOOTSTRAP["last_ts"] = NOW
        self.spawns += 1
        if self.bump and self.exp is not None:
            self.exp += self.bump


def tick(fake, now=NOW):
    return asyncio.run(hold_mod._auth_refresh_tick(
        now=now, reader=fake.reader, bootstrap=fake.bootstrap))


print("[decision is pure and keyed on expiry only]")
d = hold_mod._auth_refresh_decision
check("valid token → no", d(NOW + 3600, now=NOW, lead=0)[0] is False)
check("lapsed token → go", d(NOW - 1, now=NOW, lead=0)[0] is True)
check("exactly at expiry → go", d(NOW, now=NOW, lead=0)[0] is True)
check("inside lead → go", d(NOW + 100, now=NOW, lead=300)[0] is True)
check("outside lead → no", d(NOW + 600, now=NOW, lead=300)[0] is False)
check("unreadable expiry → no (never spawn blind)", d(None, now=NOW)[0] is False)

print("\n[a valid token never spends a turn]")
_reset()
f = Fake(exp=NOW + 5000)
r = tick(f)
check("no spawn", f.spawns == 0, r)
check("snapshot shows the expiry", hold_mod._auth_refresh_snapshot(NOW)["token_expires_in_s"] == 5000.0)
check("not lapsed, not stalled", not hold_mod._auth_refresh_snapshot(NOW)["stalled"])

print("\n[a lapsed token spends exactly one turn and is verified by re-read]")
_reset()
f = Fake(exp=NOW - 60)
r = tick(f)
check("spawned once", f.spawns == 1, r)
check("outcome = refreshed (expiry moved)", r == "refreshed", r)
snap = hold_mod._auth_refresh_snapshot(NOW)
check("refreshed counter", snap["refreshed"] == 1)
check("new expiry visible", snap["token_expires_in_s"] > 0, snap["token_expires_in_s"])
r2 = tick(f, now=NOW + 60)
check("next tick: token valid → no second spawn", f.spawns == 1, r2)

print("\n[NOTHING here depends on a proxy-side hold being armed]")
check("no holds armed", not hold_mod._hold_snapshot())
check("...and the lapsed-token tick spawned anyway (see above)", f.spawns == 1)

print("\n[failed exchange: retry within budget, then STALLED]")
_reset()
f = Fake(exp=NOW - 60, bump=0)
r1 = tick(f, now=NOW)
check("first attempt reports not refreshed", r1 == "not refreshed", r1)
r2 = tick(f, now=NOW + 60)
check("second tick inside cooldown does not spawn", f.spawns == 1, r2)
r3 = tick(f, now=NOW + hold_mod._AUTH_BOOTSTRAP_COOLDOWN + 1)
check("after cooldown: second attempt", f.spawns == 2, r3)
r4 = tick(f, now=NOW + 2 * hold_mod._AUTH_BOOTSTRAP_COOLDOWN + 2)
check("budget spent: no third spawn", f.spawns == 2, r4)
snap = hold_mod._auth_refresh_snapshot(NOW + 2 * hold_mod._AUTH_BOOTSTRAP_COOLDOWN + 2)
check("snapshot says STALLED (human login owed)", snap["stalled"] is True, snap)
check("token still reported lapsed", snap["token_lapsed"] is True)

print("\n[budget reset after the outage window lets a later lapse refresh again]")
later = NOW + hold_mod._AUTH_BOOTSTRAP_RESET + 10
f.bump = 8 * 3600
r5 = tick(f, now=later)
check("fresh outage: spawns again", f.spawns == 3, r5)
check("and succeeds", r5 == "refreshed", r5)

print("\n[unreadable store: decline, report, never spawn]")
_reset()
f = Fake(exp=NOW - 60, fail_read=True)
r = tick(f)
check("no spawn on read failure", f.spawns == 0, r)
snap = hold_mod._auth_refresh_snapshot(NOW)
check("read_error surfaced", bool(snap["read_error"]), snap)
check("not stalled (nothing was tried)", snap["stalled"] is False)

print("\n[kill switch]")
_reset()
hold_mod.WARMTH_AUTH_REFRESH = False
f = Fake(exp=NOW - 60)
r = tick(f)
check("disabled → no spawn", f.spawns == 0, r)
hold_mod.WARMTH_AUTH_REFRESH = True

print("\n[/_status + /_identity expose it]")
_reset()
from proxylab import status as status_mod  # noqa: E402
st = status_mod._status_payload() if hasattr(status_mod, "_status_payload") else None
if st is None:
    import json
    from starlette.testclient import TestClient
    c = TestClient(lp.app)
    st = c.get("/_status").json()
    ident = c.get("/_identity").json()
else:
    ident = None
check("/_status proxy.auth_refresh present",
      isinstance((st.get("proxy") or {}).get("auth_refresh"), dict), list((st.get("proxy") or {}).keys())[:12])
if ident is not None:
    check("/_identity capabilities.auth_refresh true",
          ident.get("capabilities", {}).get("auth_refresh") is True)

print("\n[real credential reader returns an expiry, not a token]")
try:
    exp = hold_mod._cli_token_expiry()
    check("reader returns a float epoch or None", exp is None or isinstance(exp, float), type(exp).__name__)
except RuntimeError as e:
    check("reader raises a typed error when the store is absent", "credential store" in str(e), str(e))

if FAILS:
    print(f"\n{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL PASS")
