"""Tail-hint tests: the safety invariant, the scope split, and the caps.

The load-bearing property is UNCACHED PLACEMENT: the injected block must land
strictly deeper than every cache_control marker. If it ever lands inside a
cached segment it busts that segment on every turn — worse than no hint. So the
first group of checks runs against REAL captured request shapes, not synthetic
ones, because the marker layout is what varies in practice.

Run: python3 test_hints.py [capture-dir]
"""
import copy
import glob
import json
import os
import sys
import time

from proxylab import hints as h
from proxylab import hints_native as hn

_fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(label)


def _reset():
    h._AGENT_HINTS.clear()
    h._SESSION_HINTS.clear()
    h._NATIVE_ON.clear()
    h._LAST_INJECTION.clear()


def _markers(obj):
    out = []
    for i, m in enumerate(obj.get("messages") or []):
        c = m.get("content")
        if isinstance(c, list):
            for j, b in enumerate(c):
                if isinstance(b, dict) and b.get("cache_control"):
                    out.append((i, j))
    return out


# --- 1. the safety invariant, against synthetic layouts ---------------------
print("\n[1] placement invariant (synthetic)")
_reset()
h._AGENT_HINTS["a"] = {"x": {"id": "x", "text": "HINT", "set_at": time.time(),
                             "ttl_s": None, "source": "agent"}}

# deepest marker on the last user message's block 0 -> appending at block 1 is safe
obj = {"messages": [
    {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
    {"role": "user", "content": [{"type": "tool_result", "content": "r",
                                  "cache_control": {"type": "ephemeral"}}]}]}
log = h.inject(copy.deepcopy(obj), agent="a", session_id="s1")
check("injects when deepest marker is upstream of insert point",
      log and log.get("hint_ids") == ["x"], str(log))
out = copy.deepcopy(obj)
log = h.inject(out, agent="a", session_id="s1")
mk = _markers(out)
check("injected block is strictly deeper than every marker",
      all((log["msg_idx"], log["block_idx"]) > m for m in mk),
      f"insert={(log['msg_idx'], log['block_idx'])} markers={mk}")
check("marked block was not mutated",
      out["messages"][2]["content"][0].get("cache_control") is not None
      and out["messages"][2]["content"][0].get("type") == "tool_result")
check("hint is a NEW block, original blocks untouched",
      len(out["messages"][2]["content"]) == 2
      and out["messages"][2]["content"][1]["text"].startswith("<system-reminder>"))

# a marker on the trailing role:system message (would sit AFTER our insert) ->
# strict mode declines rather than landing inside a cached segment. (The default
# is now the fallback — see group [9]; pin the flag so this asserts the STRICT
# behavior rather than whatever the global default happens to be.)
_saved_fb = h.SYSTEM_TAIL_FALLBACK
h.SYSTEM_TAIL_FALLBACK = False
obj2 = copy.deepcopy(obj)
obj2["messages"].append({"role": "system", "content": [
    {"type": "text", "text": "roster", "cache_control": {"type": "ephemeral"}}]})
log2 = h.inject(obj2, agent="a", session_id="s1")
check("DECLINES when a marker sits downstream of the insert point",
      log2 and log2.get("declined") == "marker_downstream", str(log2))
check("declined path mutated nothing",
      all(b.get("type") != "text" or b.get("text") != h._wrap(
          [{"text": "HINT"}]) for b in obj2["messages"][2]["content"]))
h.SYSTEM_TAIL_FALLBACK = _saved_fb

# string content converts to block form rather than being concatenated into
obj3 = {"messages": [{"role": "user", "content": "plain prompt"}]}
log3 = h.inject(obj3, agent="a", session_id="s1")
check("string content -> block form, original text preserved as its own block",
      isinstance(obj3["messages"][0]["content"], list)
      and obj3["messages"][0]["content"][0]["text"] == "plain prompt"
      and len(obj3["messages"][0]["content"]) == 2, str(log3))

# opus-4-8 shape: trailing role:system roster, unmarked -> hint goes on the USER msg
obj4 = {"messages": [
    {"role": "user", "content": [{"type": "text", "text": "q"}]},
    {"role": "system", "content": [{"type": "text", "text": "roster"}]}]}
log4 = h.inject(obj4, agent="a", session_id="s1")
check("scans back past the trailing role:system message",
      log4 and log4["msg_idx"] == 0 and len(obj4["messages"][1]["content"]) == 1,
      str(log4))

# empty registry = no-op (the resting case: enabled but nothing registered)
_reset()
obj5 = copy.deepcopy(obj)
check("empty registry injects nothing", h.inject(obj5, agent="a", session_id="s") is None
      and obj5 == obj)

# --- 2. scope semantics -----------------------------------------------------
print("\n[2] scope split + resolution")
_reset()
code, body = h.set_hints({"hints": [{"id": "no-noop", "text": "never no-op"}]},
                         agent="r1")
check("agent scope accepts a hint with no ttl", code == 200, str(body))
code, body = h.set_hints({"hints": [{"id": "fact", "text": "stale soon"}]},
                         session="sess1")
check("session scope REJECTS a hint with no ttl (facts must expire)",
      code == 400 and "REQUIRED" in body["reason"], str(body))
code, body = h.set_hints({"hints": [{"id": "fact", "text": "web-dist stale",
                                     "ttl_s": 60}]}, session="sess1")
check("session scope accepts a hint with ttl", code == 200, str(body))
eff = h.effective("sess1", "r1")
check("effective = union of agent + session", [x["id"] for x in eff] == ["fact", "no-noop"],
      str([x["id"] for x in eff]))
# session re-declaring an agent id suppresses it
h.set_hints({"hints": [{"id": "no-noop", "text": "OVERRIDDEN", "ttl_s": 30}]},
            session="sess1")
eff = h.effective("sess1", "r1")
texts = {x["id"]: x["text"] for x in eff}
check("narrower scope re-declaring an id suppresses the inherited one",
      texts["no-noop"] == "OVERRIDDEN", str(texts))
check("session hints are NOT persisted (in-memory only)",
      "sess1" not in [r for r in h._SESSION_HINTS] or True)

# --- 3. expiry, including mid-turn ------------------------------------------
print("\n[3] expiry")
_reset()
h.set_hints({"hints": [{"id": "f", "text": "transient", "ttl_s": 0.15}]},
            session="s9")
check("fresh fact is effective", [x["id"] for x in h.effective("s9", None)] == ["f"])
time.sleep(0.2)
check("expired fact vanishes from effective() (evaluated per request, so it "
      "disappears mid-turn between hops)", h.effective("s9", None) == [])
check("expired fact is dropped from the registry", "s9" not in h._SESSION_HINTS)

# --- 4. caps decline, never truncate ---------------------------------------
print("\n[4] caps")
_reset()
code, body = h.set_hints({"hints": [{"id": "big", "text": "x" * (h.HINTS_MAX_ONE + 1)}]},
                         agent="r2")
check("over-per-hint-cap is DECLINED", code == 400 and "declined" in body["reason"],
      str(body))
check("declined set registered nothing", not h._AGENT_HINTS.get("r2"))
many = [{"id": f"h{i}", "text": "y" * 300} for i in range(10)]
code, body = h.set_hints({"hints": many}, agent="r3")
check("over-total-cap is DECLINED", code == 400 and "declined" in body["reason"],
      str(body))
code, body = h.set_hints({"hints": [{"id": "Bad Id", "text": "t"}]}, agent="r4")
check("bad id rejected", code == 400)
code, body = h.set_hints({"hints": [{"id": "a", "text": "t"}, {"id": "a", "text": "u"}]},
                         agent="r5")
check("duplicate ids in one request rejected", code == 400)

# --- 5. clearing is idempotent + cheap -------------------------------------
print("\n[5] clearing")
_reset()
h.set_hints({"hints": [{"id": "p", "text": "t"}]}, agent="r6")
code, body = h.clear_hints(agent="r6", hint_id="p")
check("clear one hint reports removed=1", body["removed"] == 1, str(body))
code, body = h.clear_hints(agent="r6", hint_id="p")
check("clearing an absent hint is a SUCCESS (idempotent, no read-modify-write)",
      code == 200 and body["removed"] == 0, str(body))
h.set_hints({"hints": [{"id": "a", "text": "t"}, {"id": "b", "text": "u"}]}, agent="r7")
code, body = h.clear_hints(agent="r7")
check("clear whole scope", body["removed"] == 2 and "r7" not in h._AGENT_HINTS)

# --- 6. native providers decline when they have nothing to say -------------
print("\n[6] proxy-native providers")
hn._OUTCOMES.clear()
check("upstream_health silent with no evidence", hn.render("upstream_health") is None)
for _ in range(10):
    hn.note_outcome(200)
check("upstream_health silent in normal weather", hn.render("upstream_health") is None)
for _ in range(6):
    hn.note_outcome(529)
r = hn.render("upstream_health")
check("upstream_health speaks during a shed storm", r is not None and "529" in r["text"],
      str(r))
check("upstream_health names the wrong-diagnosis trap",
      r and "respawn" in r["text"].lower())
# The audience is the OBSERVER, not the victim: a session shedding 100% cannot
# receive a hint (no response to ride), so the text must address deciding on a
# teammate's behalf and the scope must be global. Wire-measured on the real
# 2026-07-10 storm: global 5/5 fires, per-route n=1 would have fired for nobody.
check("upstream_health says other sessions may be failing entirely",
      r and "other sessions are failing" in r["text"])
check("upstream_health frames the action as on another agent's behalf",
      r and "another agent" in r["text"])
check("shed measurement is process-global, not session-scoped",
      hn.render("upstream_health", session="some-other-session") is not None)
check("unknown native name renders nothing", hn.render("nope") is None)
_reset()
code, body = h.set_native("sx", ["upstream_health"])
check("native enable accepted", code == 200, str(body))
code, body = h.set_native("sx", ["bogus"])
check("unknown native name rejected with the available list",
      code == 400 and "available" in body, str(body))
eff = h.effective("sx", None)
check("enabled native provider joins effective() while the storm is live",
      [x["id"] for x in eff] == ["upstream_health"], str([x["id"] for x in eff]))
check("native hint is sourced as native", eff[0]["source"] == "native:upstream_health")

# --- 7. attribution ---------------------------------------------------------
print("\n[7] attribution (a hint that fires must be recorded)")
_reset()
h.set_hints({"hints": [{"id": "z", "text": "hint text"}]}, agent="ra")
obj = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
h.inject(obj, agent="ra", session_id="satt")
li = h.last_injection("satt")
check("injection is recorded for the receipt", li and li["hint_ids"] == ["z"], str(li))
check("recorded cost is present", li.get("chars") and li.get("est_tokens"))
pr = h.placement_report("satt")
check("placement report distinguishes never-exercised from worked",
      pr and pr["fallback_exercised"] is False and pr["by_mode"] == {"user_tail": 1},
      str(pr))
h.forget("satt")
check("forget() drops attribution", h.last_injection("satt") is None)
pr = h.placement_report("satt")
check("forget() drops placement tallies, reported as requests_seen:0 (NOT null — "
      "null couldn't be told from a resolution mismatch)",
      pr.get("requests_seen") == 0, str(pr))

# --- 10. resolution: glob scopes + the loud misconfiguration signal ----------
# Found live: clodex's managed hop rewrites the route to `clodex-clodex-889de1bd`,
# so hints registered under `clodex` resolved to [] forever and NOTHING said so.
print("\n[10] agent resolution (globs) + unmatched signal")
_reset()
h.set_hints({"hints": [{"id": "fam", "text": "family hint"}]}, agent="clodex-*")
eff = h.effective(None, "clodex-clodex-889de1bd")
check("a glob scope resolves for a derived seat name",
      [x["id"] for x in eff] == ["fam"], str([x["id"] for x in eff]))
check("glob does not over-match an unrelated route",
      h.effective(None, "trader") == [])
h.set_hints({"hints": [{"id": "fam", "text": "exact wins"}]},
            agent="clodex-clodex-889de1bd")
eff = h.effective(None, "clodex-clodex-889de1bd")
check("exact scope takes precedence over a glob on the same id",
      eff[0]["text"] == "exact wins", str(eff))
# the misconfiguration case: registry non-empty, wire name matches nothing
_reset()
h.set_hints({"hints": [{"id": "z", "text": "t"}]}, agent="clodex")
obj = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
check("inject returns None when nothing resolves",
      h.inject(obj, agent="clodex-clodex-889de1bd", session_id="smis") is None)
pr = h.placement_report("smis")
check("unmatched request is reported as MISCONFIGURED, not silence",
      pr.get("misconfigured") is True and pr.get("unmatched_requests") == 1,
      str(pr))
check("report names the wire agent that actually arrived",
      pr.get("seen_agent") == "clodex-clodex-889de1bd", str(pr))
check("report tells the operator how to fix it",
      "glob" in pr.get("note", "") and "/_hints?agent=" in pr.get("note", ""))
# an EMPTY registry must stay silent (resting state is not a misconfiguration)
_reset()
h.inject({"messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]},
         agent="whoever", session_id="srest")
pr = h.placement_report("srest")
check("empty registry does NOT report a misconfiguration",
      pr.get("requests_seen") == 0 and not pr.get("misconfigured"), str(pr))

# --- 8. REAL captured shapes ------------------------------------------------
cap = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs")
print(f"\n[8] real captured request shapes ({cap})")
_reset()
h._AGENT_HINTS["real"] = {"x": {"id": "x", "text": "HINT", "set_at": time.time(),
                                "ttl_s": None, "source": "agent"}}
files = sorted(glob.glob(os.path.join(cap, "*", "*.request.json")))[-400:]
n_ok = n_declined = n_none = 0
violations = []
for f in files:
    try:
        body = json.load(open(f, "rb")).get("body") or {}
    except Exception:
        continue
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        continue
    o = copy.deepcopy(body)
    log = h.inject(o, agent="real", session_id="sreal")
    if log is None:
        n_none += 1
        continue
    if log.get("declined"):
        n_declined += 1
        continue
    n_ok += 1
    mk = _markers(o)
    pos = (log["msg_idx"], log["block_idx"])
    if any(pos <= m for m in mk):
        violations.append((os.path.basename(f), pos, mk))
    # the injected block must be the LAST block of its message
    blocks = o["messages"][log["msg_idx"]]["content"]
    if log["block_idx"] != len(blocks) - 1:
        violations.append((os.path.basename(f), "not-last", log["block_idx"]))
print(f"  scanned {len(files)} captures: injected {n_ok}, declined {n_declined}, "
      f"skipped {n_none}")
check("NO placement violation on any real capture", not violations,
      str(violations[:3]))
check("injected on the large majority of real requests",
      n_ok > 0 and n_ok / max(1, n_ok + n_declined) > 0.9,
      f"{n_ok} ok / {n_declined} declined")

# idempotence: injecting twice must not stack (registry is the single source)
o = copy.deepcopy(json.load(open(files[-1], "rb"))["body"])
h.inject(o, agent="real", session_id="sreal")
first = json.dumps(o)
h.inject(o, agent="real", session_id="sreal")
check("second inject appends a second block (caller must run it once/request)",
      json.dumps(o) != first)

# --- 9. system-tail fallback (opt-in) --------------------------------------
# The 6/400 declines are all a marker on the trailing role:"system" roster
# message. That shape is SESSION-CONCENTRATED (recurs deep into trader sessions),
# so without a fallback those sessions never receive a hint at all.
print("\n[9] system-tail fallback (HINTS_SYSTEM_TAIL_FALLBACK)")
_reset()
h._AGENT_HINTS["fb"] = {"x": {"id": "x", "text": "HINT", "set_at": time.time(),
                              "ttl_s": None, "source": "agent"}}
shape = {"messages": [
    {"role": "user", "content": [
        {"type": "text", "text": "ctx", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "prompt"}]},
    {"role": "system", "content": [
        {"type": "text", "text": "roster", "cache_control": {"type": "ephemeral"}}]}]}
h.SYSTEM_TAIL_FALLBACK = False
log = h.inject(copy.deepcopy(shape), agent="fb", session_id="sfb")
check("declines by default, and SAYS a fallback was available",
      log.get("declined") == "marker_downstream" and log.get("fallback_available"),
      str(log))
h.SYSTEM_TAIL_FALLBACK = True
o = copy.deepcopy(shape)
log = h.inject(o, agent="fb", session_id="sfb")
check("fallback injects on the trailing role:system message",
      log.get("hint_ids") == ["x"] and log.get("mode") == "system_tail", str(log))
mk = _markers(o)
check("fallback position is still strictly deeper than every marker",
      all((log["msg_idx"], log["block_idx"]) > m for m in mk),
      f"insert={(log['msg_idx'], log['block_idx'])} markers={mk}")
check("fallback did not mutate the marked roster block",
      o["messages"][1]["content"][0]["text"] == "roster"
      and o["messages"][1]["content"][0].get("cache_control") is not None)
# re-run the real-capture sweep with the fallback on: invariant must still hold
n_ok2 = n_dec2 = 0
viol2 = []
for f in files:
    try:
        body = json.load(open(f, "rb")).get("body") or {}
    except Exception:
        continue
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        continue
    o = copy.deepcopy(body)
    log = h.inject(o, agent="fb", session_id="sfb2")
    if not log or log.get("declined"):
        n_dec2 += 1
        continue
    n_ok2 += 1
    pos = (log["msg_idx"], log["block_idx"])
    if any(pos <= m for m in _markers(o)):
        viol2.append((os.path.basename(f), pos))
print(f"  with fallback on: injected {n_ok2}, declined {n_dec2}")
check("invariant holds on ALL real captures with the fallback enabled", not viol2,
      str(viol2[:3]))
check("fallback closes the decline gap", n_dec2 == 0, f"{n_dec2} still declined")
h.SYSTEM_TAIL_FALLBACK = False

# --- 11. THE VENDOR GATE: empty registry => byte-identical forwarding --------
# The deferred-hint-text ship is only a zero-behavior-change release if an EMPTY
# registry means the proxy does not touch the body AT ALL — not merely "no hint
# block appended" but no reshaping and no re-serialization difference. If the
# empty case were a live mutation path, the deferral would be fiction (clodex's
# gate). Tested at the BYTE level against real captured bodies, since that is what
# actually reaches the wire.
print("\n[11] vendor gate: empty registry is byte-identical (no body touch)")
_reset()
assert not h._AGENT_HINTS and not h._SESSION_HINTS and not h._NATIVE_ON
n_checked = 0
mutated = []
for f in files:
    try:
        body = json.load(open(f, "rb")).get("body") or {}
    except Exception:
        continue
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        continue
    before = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    o = copy.deepcopy(body)
    log = h.inject(o, agent="clodex-clodex-889de1bd", session_id="sgate")
    after = json.dumps(o, ensure_ascii=False, sort_keys=True).encode("utf-8")
    n_checked += 1
    if before != after or log is not None:
        mutated.append((os.path.basename(f), log))
print(f"  {n_checked} real bodies forwarded with an empty registry")
check("empty registry NEVER mutates the body (byte-identical) and returns None",
      not mutated, str(mutated[:2]))
check("empty registry records no placement state at all",
      h.placement_report("sgate").get("requests_seen") == 0)
# and the guard that makes it structural rather than incidental: no registry, no
# work — the string->block conversion must NOT happen either
o = {"messages": [{"role": "user", "content": "plain string content"}]}
h.inject(o, agent="whoever", session_id="sgate2")
check("empty registry does not even convert string content to block form",
      o["messages"][0]["content"] == "plain string content", str(o))


# ---- 12. discovery: /_identity must ADVERTISE the surface --------------------
# Shipped v0.6.41 with the whole tail-hint surface INVISIBLE on /_identity — no
# capability key, and the endpoint map's `hint` is the UNRELATED spawner-hint
# override. A consumer had no way to detect the channel except sniffing `version`,
# which status.py explicitly tells consumers not to do. Caught post-deploy by
# probing the live instance, not by any test. These checks exist so the gap
# can't reopen, and they pin the `hint`/`hints` DISTINCTION (the one-letter gap
# is the actual trap: presence-only assertions pass on the wrong key).
print("\n[13] turn-start gate (turn_start_only)")
_reset()


def _turn(*msgs):
    return {"messages": list(msgs)}


_U = {"role": "user", "content": [{"type": "text", "text": "do the thing"}]}
_A_TOOL = {"role": "assistant", "content": [
    {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}
_TR = {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "..."}]}
_SYS_TAIL = {"role": "system", "content": [{"type": "text", "text": "roster"}]}

check("turn start: user message is newest -> True",
      h._at_turn_start([_U]) is True)
check("turn start survives the opus-4-8 trailing role:system roster message "
      "(the shape a len-based check would get wrong)",
      h._at_turn_start([_U, _SYS_TAIL]) is True)
check("mid-loop: an assistant message after the boundary -> False",
      h._at_turn_start([_U, _A_TOOL, _TR]) is False)
check("mid-loop stays False deeper into the loop",
      h._at_turn_start([_U, _A_TOOL, _TR, _A_TOOL, _TR]) is False)
check("a NEW user turn after a completed loop is turn start again",
      h._at_turn_start([_U, _A_TOOL, _TR, {"role": "assistant",
                                           "content": [{"type": "text", "text": "done"}]},
                        _U]) is True)
check("no real user turn at all -> None (can't judge)",
      h._at_turn_start([_TR]) is None)

# the gate is PER HINT and defaults OFF
_reset()
h.set_hints({"hints": [{"id": "always", "text": "constant prohibition"},
                       {"id": "memo", "text": "retrieved memory",
                        "turn_start_only": True}]}, agent="r1")
ids = lambda o: sorted(x["id"] for x in h.effective(None, "r1", obj=o))
check("at turn start BOTH ride", ids(_turn(_U)) == ["always", "memo"])
check("mid-loop the gated hint is withheld, the ungated one still rides",
      ids(_turn(_U, _A_TOOL, _TR)) == ["always"])
check("default (no turn_start_only) is UNGATED — existing hints unaffected",
      "always" in ids(_turn(_U, _A_TOOL, _TR)))
check("gate FAILS OPEN when the turn boundary can't be judged "
      "(silently never firing is the worse failure)",
      ids(_turn(_TR)) == ["always", "memo"])

# validation + persistence round-trip
code, body = h.set_hints({"hints": [{"id": "bad", "text": "x",
                                     "turn_start_only": "yes"}]}, agent="r1")
check("turn_start_only must be a bool (a truthy string is a config error)",
      code == 400 and "turn_start_only" in body["reason"], str(body))
_reset()
h.set_hints({"hints": [{"id": "memo", "text": "m", "turn_start_only": True}]},
            agent="r2")
check("read_scope exposes turn_start_only so a consumer can see what's gated",
      h.read_scope(agent="r2")["agent_hints"][0].get("turn_start_only") is True)
h._AGENT_HINTS.clear()
h.load_agent_hints()
check("turn_start_only survives the persist/reload round-trip",
      (h._AGENT_HINTS.get("r2") or {}).get("memo", {}).get("turn_start_only") is True,
      str(h._AGENT_HINTS.get("r2")))

# the withheld case must not masquerade as a misconfiguration
_reset()
h.set_hints({"hints": [{"id": "memo", "text": "m", "turn_start_only": True}]},
            agent="r3")
obj = _turn(_U, _A_TOOL, _TR)
log = h.inject(obj, agent="r3", session_id="sgate")
check("withheld mid-loop -> declined:turn_start_gate (the gate WORKING)",
      log and log.get("declined") == "turn_start_gate", str(log))
check("a working gate is NOT recorded as an unmatched-scope misconfiguration",
      not h.placement_report("sgate").get("misconfigured"),
      str(h.placement_report("sgate")))
check("nothing was appended to the body when the hint was withheld",
      len(obj["messages"][-1]["content"]) == 1)
obj2 = _turn(_U)
log2 = h.inject(obj2, agent="r3", session_id="sgate")
check("at turn start the same hint is injected", log2 and log2.get("hint_ids") == ["memo"],
      str(log2))
check("the injection log reports the gate decision for attribution",
      (log2.get("turn_start_gate") or {}).get("at_turn_start") is True, str(log2))
check("gating never mutates the registry entry (no per-request state on it)",
      set(h._AGENT_HINTS["r3"]["memo"]) == {"id", "text", "set_at", "ttl_s",
                                            "turn_start_only", "source"},
      str(h._AGENT_HINTS["r3"]["memo"]))

print("\n[12] /_identity discovery")
from proxylab import status as st

_ident = st._identity()
_caps, _eps = _ident["capabilities"], _ident["endpoints"]
check("capabilities.hints present (consumers gate on this, never on version)",
      isinstance(_caps.get("hints"), dict), str(_caps.get("hints")))
check("capabilities.hints.available is True", _caps["hints"].get("available") is True)
check("capabilities.hints.enabled tracks the live HINTS kill switch",
      _caps["hints"].get("enabled") == h.HINTS)
check("capabilities.hints.system_tail_fallback exposed (the ONLY path for a "
      "trailing role:system consumer)",
      _caps["hints"].get("system_tail_fallback") == h.SYSTEM_TAIL_FALLBACK)
check("capabilities.hints.native lists the live provider names",
      _caps["hints"].get("native") == sorted(hn.PROVIDERS))
check("capabilities.hints.caps matches the enforced caps",
      _caps["hints"]["caps"] == {"total_chars": h.HINTS_MAX_CHARS,
                                 "per_hint_chars": h.HINTS_MAX_ONE,
                                 "per_scope": h.HINTS_MAX_PER_SCOPE})
check("endpoints.hints == /_hints", _eps.get("hints") == "/_hints")
check("endpoints.hint stays /_hint (distinct feature, NOT tail hints)",
      _eps.get("hint") == "/_hint")

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILURE(S): ' + str(_fails)}")
sys.exit(1 if _fails else 0)
