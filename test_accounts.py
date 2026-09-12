#!/usr/bin/env python3
"""Per-account credential stores (`proxylab/accounts.py`, `/_accounts`) and the
auth refresh / bootstrap running PER STORE.

WHY THIS SUITE EXISTS (2026-09-12). Clodex moved seats onto a second
subscription by launching them with CLAUDE_CONFIG_DIR=~/.clodex/accounts/opsguru,
and keep-warm died on every moved seat. The consumer-side half (its ping
re-read the bearer from ~/.claude, so an opsguru seat was pinged with the gmail
token: 30 pings, 30 429s on the OTHER org's quota wall, 0 warmed) is clodex's to
fix. This suite guards the proxy-side half: everything here that had to go to
the CLI's credential store — the proactive refresh's expiry read and the
bootstrap turn's spawn — assumed exactly one store at ~/.claude, so a second
account's token lapsed unrefreshed and its restored stash stayed awaiting_auth.

Behavioural assertions: a store registry is built from temp dirs, the reader
and the spawn are injected, and each case checks what the tick DID per store.
The keychain service-name rule is asserted against two values read off THIS
box's keychain (`security dump-keychain`), not derived from the code.
"""
import asyncio
import json
import os
import sys
import tempfile
import time

os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="accounts-logs-")
os.environ["WARMTH_DB"] = os.path.join(tempfile.mkdtemp(prefix="accounts-db-"), "w.sqlite")
os.environ["WARMTH_LEDGER"] = "1"
os.environ["WARMTH_AUTH_REFRESH"] = "1"
os.environ["WARMTH_AUTH_BOOTSTRAP"] = "1"

import logproxy as lp  # noqa: E402
from proxylab import accounts as acc  # noqa: E402
from proxylab import hold as hold_mod  # noqa: E402
from proxylab import pinger as pinger_mod  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


NOW = 1_800_000_000.0
HOME = os.path.expanduser("~")


def _mk_store(label, account_uuid, email, org="org-" + "x"):
    d = tempfile.mkdtemp(prefix=f"store-{label}-")
    with open(os.path.join(d, ".claude.json"), "w") as f:
        json.dump({"oauthAccount": {"accountUuid": account_uuid,
                                    "emailAddress": email,
                                    "organizationUuid": org}}, f)
    return d


print("[keychain service name = the CLI's rule, checked against this box's keychain]")
check("default store → bare service", acc.keychain_service(None) == "Claude Code-credentials")
check("~/.claude as an explicit CLAUDE_CONFIG_DIR → -2358b87d (observed item)",
      acc.keychain_service("/Users/bogdan/.claude") == "Claude Code-credentials-2358b87d",
      acc.keychain_service("/Users/bogdan/.claude"))
check("~/.clodex/accounts/opsguru → -6f0dcb4b (observed item)",
      acc.keychain_service("/Users/bogdan/.clodex/accounts/opsguru")
      == "Claude Code-credentials-6f0dcb4b")
check("a trailing slash changes the hash (so normalize first)",
      acc.keychain_service("/x/y/") != acc.keychain_service("/x/y"))

print("\n[normalize_dir]")
check("None → default", acc.normalize_dir(None) is None)
check("empty → default", acc.normalize_dir("  ") is None)
check("the literal default path → default (None)",
      acc.normalize_dir(os.path.join(HOME, ".claude")) is None)
check("~ expands", acc.normalize_dir("~/.clodex/accounts/x") == os.path.join(HOME, ".clodex/accounts/x"))
check("trailing slash stripped", acc.normalize_dir("/a/b/") == "/a/b")
try:
    acc.normalize_dir("relative/dir")
    check("relative path rejected", False)
except ValueError:
    check("relative path rejected", True)

print("\n[spawn_env: the bootstrap turn runs under the wanted store]")
base = {"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/inherited/from/a/seat"}
e0 = acc.spawn_env(None, base)
check("default store: an inherited CLAUDE_CONFIG_DIR is DROPPED", "CLAUDE_CONFIG_DIR" not in e0)
check("...other env kept", e0["PATH"] == "/bin")
e1 = acc.spawn_env("/s/opsguru", base)
check("named store: CLAUDE_CONFIG_DIR set to it", e1["CLAUDE_CONFIG_DIR"] == "/s/opsguru")

print("\n[registry: register / stores / lookup / unregister / persistence]")
acc._reset_for_tests()
A = _mk_store("a", "acct-a", "a@example.com")
B = _mk_store("b", "acct-b", "b@example.com")
r = acc.register(A + "/")
check("register returns the store's readout", r["ok"] and r["registered"] and r["config_dir"] == A, r)
check("...with the account read off .claude.json", r["account_uuid"] == "acct-a" and r["email"] == "a@example.com")
r2 = acc.register(A)
check("re-register is idempotent (registered:false)", r2["registered"] is False)
acc.register(B)
st = acc.stores()
check("stores(): default first, then registered in order",
      [s["config_dir"] for s in st] == [None, A, B], [s["config_dir"] for s in st])
check("default store flagged", st[0]["default"] is True and st[0]["keychain_service"] == "Claude Code-credentials")
check("store_for_account resolves a registered account", acc.store_for_account("acct-b") == B)
check("store_for_account: unknown account → None (default-store fallback, today's behaviour)",
      acc.store_for_account("acct-nobody") is None)
check("label_for_account", acc.label_for_account("acct-a") == "a@example.com")
rd = acc.register(os.path.join(HOME, ".claude"))
check("registering the default store is a no-op", rd["registered"] is False and rd["default"] is True)
try:
    acc.register(tempfile.mktemp(prefix="nope-"))
    check("a non-directory is rejected", False)
except ValueError:
    check("a non-directory is rejected", True)
# a re-login under the same dir is reflected without re-registering
with open(os.path.join(B, ".claude.json"), "w") as f:
    json.dump({"oauthAccount": {"accountUuid": "acct-b2", "emailAddress": "b2@example.com"}}, f)
check("account fields re-read on every readout (re-login follows)",
      acc.store_for_account("acct-b2") == B and acc.store_for_account("acct-b") is None)
# persistence round trip
acc._reset_for_tests()
check("reset → default only", [s["config_dir"] for s in acc.stores()] == [None])
n = acc.load()
check("load() restores both registered dirs", n == 2 and {s["config_dir"] for s in acc.stores()} == {None, A, B}, n)
u = acc.unregister(B)
check("unregister removes", u["removed"] is True and B not in [s["config_dir"] for s in acc.stores()])
acc._reset_for_tests()
acc.load()
check("...and the removal persisted", [s["config_dir"] for s in acc.stores()] == [None, A])
import shutil
shutil.rmtree(A)
acc._reset_for_tests()
n = acc.load()
check("a vanished dir is dropped on load, not restored", n == 0 and acc.stores()[0]["default"] and len(acc.stores()) == 1, n)

print("\n[read_expiry: plaintext file, seconds out, token never returned]")
C = _mk_store("c", "acct-c", "c@example.com")
with open(os.path.join(C, ".credentials.json"), "w") as f:
    json.dump({"claudeAiOauth": {"accessToken": "SECRET", "expiresAt": 1_700_000_000_000}}, f)
exp = acc.read_expiry(C)
check("expiresAt in epoch seconds", exp == 1_700_000_000.0, exp)
with open(os.path.join(C, ".credentials.json"), "w") as f:
    json.dump({"other": 1}, f)
check("file without an oauth block → None", acc.read_expiry(C) is None)
os.remove(os.path.join(C, ".credentials.json"))
try:
    acc.read_expiry(C)      # no file; keychain item for this random dir cannot exist
    check("no file + no keychain item → RuntimeError", False)
except RuntimeError as e:
    check("no file + no keychain item → RuntimeError", "credential store" in str(e))

print("\n[refresh runs PER STORE: independent expiry, budget, and spawn env]")
acc._reset_for_tests()
D1 = _mk_store("d1", "acct-d1", "d1@example.com")
D2 = _mk_store("d2", "acct-d2", "d2@example.com")
acc.register(D1)
acc.register(D2)


def _reset_hold_state():
    for st in (hold_mod._AUTH_REFRESH_STORES, hold_mod._AUTH_BOOTSTRAP_STORES):
        for k in list(st):
            if k is not None:
                del st[k]
    hold_mod._AUTH_REFRESH.update({"expires_at": None, "checked_ts": 0.0, "read_error": None,
                                   "last_trigger_ts": 0.0, "last_outcome": None, "refreshed": 0})
    hold_mod._AUTH_BOOTSTRAP.update({"attempts": 0, "last_ts": 0.0, "inflight": False,
                                     "last_reason": None, "spawns": 0})


class Box:
    """A box with several credential stores: `exp[dir]` is each store's token
    expiry; a bootstrap under a store moves that store's expiry by `bump`."""
    def __init__(self, exp, bump=8 * 3600):
        self.exp, self.bump = dict(exp), bump
        self.spawns = []       # config_dir per spawn

    def read_expiry(self, config_dir, timeout=5):
        if config_dir not in self.exp:
            raise RuntimeError("credential store unreadable: ItemNotFound")
        return self.exp[config_dir]

    async def bootstrap(self, account=None, config_dir=None):
        st = hold_mod._bootstrap_state(config_dir)
        st["attempts"] += 1
        st["spawns"] += 1
        st["last_ts"] = NOW
        self.spawns.append(config_dir)
        if self.bump and self.exp.get(config_dir) is not None:
            self.exp[config_dir] += self.bump


_real_read, _real_boot = acc.read_expiry, hold_mod._auth_bootstrap


def _with(box, fn):
    acc.read_expiry = box.read_expiry
    hold_mod._auth_bootstrap = box.bootstrap
    try:
        return fn()
    finally:
        acc.read_expiry, hold_mod._auth_bootstrap = _real_read, _real_boot


_reset_hold_state()
box = Box({None: NOW + 5000, D1: NOW - 60, D2: NOW + 9000})
res = _with(box, lambda: asyncio.run(hold_mod._auth_refresh_all(now=NOW)))
check("one tick covers every store", set(res) == {None, D1, D2}, res)
check("only the lapsed store spawned, under ITS dir", box.spawns == [D1], box.spawns)
check("...and reports refreshed", res[D1] == "refreshed", res)
check("default store untouched (valid)", res[None].startswith("token valid"), res)
snap = hold_mod._auth_refresh_snapshot(NOW)
check("snapshot top-level fields are the DEFAULT store's (shape unchanged)",
      snap["token_expires_in_s"] == 5000.0 and snap["refreshed"] == 0, snap)
check("snapshot lists every store with its account",
      [(s["config_dir"], s["email"]) for s in snap["stores"]]
      == [(None, acc.account_of(None)[1]), (D1, "d1@example.com"), (D2, "d2@example.com")],
      [(s["config_dir"], s["email"]) for s in snap["stores"]])
check("the refreshed store shows refreshed:1",
      next(s for s in snap["stores"] if s["config_dir"] == D1)["refreshed"] == 1)
check("nothing stalled", snap["stalled"] is False and snap["stalled_stores"] == [])

print("\n[a second store's dead refresh token stalls THAT store, not the box]")
_reset_hold_state()
box = Box({None: NOW + 5000, D1: NOW - 60, D2: NOW + 9000}, bump=0)
_with(box, lambda: asyncio.run(hold_mod._auth_refresh_all(now=NOW)))
_with(box, lambda: asyncio.run(hold_mod._auth_refresh_all(now=NOW + hold_mod._AUTH_BOOTSTRAP_COOLDOWN + 1)))
later = NOW + 2 * hold_mod._AUTH_BOOTSTRAP_COOLDOWN + 2
r3 = _with(box, lambda: asyncio.run(hold_mod._auth_refresh_all(now=later)))
check("two failed attempts, then the budget blocks", box.spawns == [D1, D1] and r3[D1].startswith("blocked"), (box.spawns, r3))
snap = hold_mod._auth_refresh_snapshot(later)
check("stalled overall (a login is owed somewhere)", snap["stalled"] is True)
check("...and it says WHERE", snap["stalled_stores"] == [D1], snap["stalled_stores"])
check("the default store's own view is not stalled",
      next(s for s in snap["stores"] if s["default"])["stalled"] is False)

print("\n[budgets are independent across stores]")
# D1's budget is spent (above). Now the DEFAULT store lapses: it must still spawn.
box.exp[None] = NOW - 1
box.bump = 8 * 3600
r4 = _with(box, lambda: asyncio.run(hold_mod._auth_refresh_all(now=later + 5)))
check("the default store spawns despite D1's spent budget",
      box.spawns[-1] is None and r4[None] == "refreshed", (box.spawns, r4))

print("\n[live credentials reset only THEIR store's budget]")
_reset_hold_state()
hold_mod._bootstrap_state(D1)["attempts"] = 2
hold_mod._bootstrap_state(None)["attempts"] = 2
with pinger_mod._LAST_REQUEST_LOCK:
    pinger_mod._LAST_REQUEST.pop("sid-acct-test", None)
pinger_mod._cache_last_request("sid-acct-test", {"model": "m", "messages": [{"role": "user", "content": "x"}]},
                               {"authorization": "Bearer live"}, "/v1/messages", account_uuid="acct-d1")
check("acct-d1 traffic resets D1's budget", hold_mod._bootstrap_state(D1)["attempts"] == 0)
check("...and leaves the default store's budget alone", hold_mod._bootstrap_state(None)["attempts"] == 2)
pinger_mod._cache_last_request("sid-acct-test2", {"model": "m", "messages": [{"role": "user", "content": "x"}]},
                               {"authorization": "Bearer live"}, "/v1/messages", account_uuid="acct-unknown")
check("an unregistered account resets the DEFAULT store (today's behaviour)",
      hold_mod._bootstrap_state(None)["attempts"] == 0)
_reset_hold_state()

print("\n[bootstrap resolves the store from the account it is asked for]")
# _auth_bootstrap spawns a real CLI when it decides 'go'; pre-spend the budget so
# it declines, and read which store's state it touched.
_reset_hold_state()
hold_mod._bootstrap_state(D2).update(attempts=hold_mod._AUTH_BOOTSTRAP_MAX, last_ts=time.time())
asyncio.run(hold_mod._auth_bootstrap("acct-d2"))
check("asked for acct-d2, it consulted D2's budget (last_reason set there)",
      hold_mod._bootstrap_state(D2)["last_reason"] is not None
      and hold_mod._bootstrap_state(None)["last_reason"] is None,
      (hold_mod._bootstrap_state(D2)["last_reason"], hold_mod._bootstrap_state(None)["last_reason"]))
_reset_hold_state()

print("\n[/_accounts, /_identity, /_status]")
from starlette.testclient import TestClient  # noqa: E402
c = TestClient(lp.app)
acc._reset_for_tests()
E = _mk_store("e", "acct-e", "e@example.com")
r = c.post(f"/_accounts?config_dir={E}/")
check("POST registers (trailing slash normalized)", r.status_code == 200 and r.json()["registered"] and r.json()["config_dir"] == E, r.text[:200])
r = c.get("/_accounts")
check("GET lists default + registered", [s["config_dir"] for s in r.json()["stores"]] == [None, E], r.text[:200])
check("GET carries the account + keychain item", r.json()["stores"][1]["email"] == "e@example.com"
      and r.json()["stores"][1]["keychain_service"] == acc.keychain_service(E))
r = c.post("/_accounts?config_dir=relative/x")
check("relative path → 400", r.status_code == 400, r.text[:120])
r = c.post("/_accounts")
check("missing config_dir → 400", r.status_code == 400)
r = c.post(f"/_accounts?config_dir={tempfile.mktemp(prefix='nodir-')}")
check("non-directory → 400", r.status_code == 400, r.text[:120])
ident = c.get("/_identity").json()
check("/_identity capabilities.accounts", ident["capabilities"].get("accounts") is True)
check("/_identity endpoints.accounts", ident["endpoints"].get("accounts") == "/_accounts")
with pinger_mod._LAST_REQUEST_LOCK:
    pinger_mod._LAST_REQUEST.pop("sid-acct-test", None)
pinger_mod._cache_last_request("sid-acct-status", {"model": "m", "messages": [{"role": "user", "content": "x"}]},
                               {"authorization": "Bearer live"}, "/v1/messages", account_uuid="acct-e")
st = c.get("/_status?session=sid-acct-status").json()
sess = st["sessions"][0] if isinstance(st["sessions"], list) else st["sessions"]["sid-acct-status"]
check("/_status session carries its account (uuid + email via the registry)",
      sess.get("account") == {"uuid": "acct-e", "email": "e@example.com"}, sess.get("account"))
check("/_status proxy.auth_refresh.stores present",
      isinstance(st["proxy"]["auth_refresh"].get("stores"), list)
      and "stalled_stores" in st["proxy"]["auth_refresh"])
r = c.delete(f"/_accounts?config_dir={E}")
check("DELETE unregisters", r.json()["removed"] is True and [s["config_dir"] for s in acc.stores()] == [None])
acc._reset_for_tests()

if FAILS:
    print(f"\n{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL PASS")
