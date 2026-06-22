# Wirescope Directive Protocol (`wirescope:`) — v1

The wire carries **no** settable identity or context-suppression controls for a Claude Code subagent: the CLI drops the agent's frontmatter and only built-in Explore/Plan can shed `# claudeMd` (nothing sheds `# userEmail`).
Wirescope reconstructs those missing knobs as **opt-in directives**, which the proxy reads and acts on at the wire.

A directive can be authored in two places:
- in the **agent prompt body** (a property of the agent *type* — every spawn of that agent gets it), or
- at the **head of a spawn's prompt** (a property of the *call* — applies to that one spawn, including uneditable built-in agents).

This is the canonical grammar. The proxy owns it; consumers adhere.
If you author `.claude/agents/*.md` files (or spawn agents through the proxy), this is the whole contract.

> **v1 changes from v0:** the directive prefix was renamed `ws:` → `wirescope:` (a distinctive prefix so an incidental bracketed token can't be mistaken for a directive and silently deleted), and **spawn-position directives**, a **`keep`** verb, and a **`replace`** verb were added. v0's `[ws:...]` is no longer recognized.

## Grammar

```
[wirescope:<directive> <value>]
```

- `<directive>` — lowercase, `[a-z][a-z0-9-]*`.
- `<value>` — everything up to the closing `]`, trimmed. Lists (e.g. `omit`/`keep` targets) are **comma- and/or whitespace-separated** — `claudemd,useremail`, `claudemd, useremail`, and `claudemd useremail` are all equivalent (liberal parse, so the most natural naive syntax works).
- One directive per line.
- **Unknown directives are ignored** (logged once). A newer directive on an older proxy degrades to a no-op — additive forever, never a hard break.
- Absent directive ⇒ unchanged behavior. Everything here is opt-in.

### Why the distinctive prefix
The proxy **strips its own directive lines before forwarding** (see below), so a false match would *silently delete real content*.
The long, distinctive `wirescope:` token makes an incidental collision in agent docs, code snippets, or a quoted transcript vanishingly unlikely.
(The strip is free regardless of length — directives never reach the model — so there is no cost to the longer token.)

## Two placements

### Body directives — a property of the agent type
Written **anywhere in the agent `.md` body**.
The agent definition's **frontmatter never reaches the wire** — verified in the CLI source: only the `.md` *body* is injected, verbatim, as a `system[]` block.
A body directive therefore lands in `system[]`, is **parsed only from the system prompt** (never message content), and is **cache-constant per agent type** (every spawn of one agent hashes the same system prefix → zero within-type cache cost).
Nothing a user, tool result, or quoted transcript types into the conversation can forge a body directive.

### Spawn directives — a property of the call
Written at the **strict head of the spawn's prompt** — i.e. as the leading line(s) of the prompt text you hand to a spawn.
This makes behavior a property of the *call*, so you can apply `omit`/`keep` to **any agent unmodified, including built-ins** (Plan/Explore/general-purpose) you cannot edit — avoiding the override-trap where overriding a built-in turns it custom and re-adds `CLAUDE.md`.

The proxy reads spawn directives from `messages[0]` only, and only at the strict head of the **spawn-prompt block** — the first `messages[0]` text block that is *not* a `<system-reminder>`.
(In practice `messages[0]` is: `<system-reminder>` (claudeMd/userEmail), `<system-reminder>` (env/date), then the prompt — so "the prompt" is the third block, not the first.)
Only a leading run of pure `[wirescope:...]` lines is honored; parsing stops at the first non-directive line.
So lead the prompt with the directive:

```
[wirescope:omit claudemd,useremail]
<the actual task text…>
```

**Why this is safe.** The spawn-prompt head is fixed at dispatch; nothing downstream can inject into it, and a `[wirescope:...]` that appears *later* in the prompt (or anywhere in message content) is **not** a directive and is left untouched.
**Residual trust:** the spawner must not place untrusted data as the literal leading token of the prompt — the same trust already given to a body-directive author, moved to dispatch time.

**Persistence (sticky per instance).** The spawn directive is at the head of `messages[0]` only on the subagent's **first** turn; on a continuation turn `messages[0]` is a follow-up / `<local-command-caveat>` / compaction summary, so the directive is no longer visible there.
The proxy therefore **remembers** the resolved spawn directives per instance — keyed by the `x-claude-code-agent-id` header (present iff subagent, stable across that instance's turns) — and **re-applies** them on every later turn of the same instance, so an `omit`/`replace`/`keep` declared at spawn holds for the life of the subagent (not just turn 1).
A later turn that carries a fresh directive at the head **updates** the memory (a new `[wirescope:keep …]` cancels an earlier `omit`). The memory is in-memory, session-scoped, and swept with the session's other per-instance state. The main line carries no `agent-id`, so it is never made sticky.

Spawn directives are **capability-gated** (`WS_SPAWN_DIRECTIVES`, default on; `=0` disables all message-content directive parsing). Body directives don't read message content and are unaffected.

### Operator default policy (`WS_OMIT_DEFAULT`)
An **operator** can set a deployment-wide default so the universal case needs *zero* agent or spawner knowledge.
`WS_OMIT_DEFAULT=useremail` (a comma list of omit targets) strips those sections from **every subagent spawn** — no directive required on any agent.
This is the lowest-precedence layer (see below): any `[wirescope:keep <target>]` in a body or spawn overrides it, and it never touches a main-agent turn (the main session is the user's own; the policy is scoped to subagents via the `cc_is_subagent` billing-header flag).

**Unconditional-only rule — policy can be automated, strategy cannot.**
A target belongs in `WS_OMIT_DEFAULT` only if **no subagent would ever want it kept** — i.e. it's a blanket *policy*, not a task-dependent judgment.
`useremail` qualifies (no spawned helper ever needs the account email). `claudemd` does **not** — whether a subagent wants project context depends on its task, which is *strategy* and belongs to a body or spawn directive, not the operator floor.
Rule of thumb: **"if you'd ever want it kept, it doesn't belong in `omit_default`."** (Keep-override is the safety valve for a rare miss, not a license to put strategic targets in the blanket default.)
It rides under the `WS_OMIT` master gate. Default empty = off.
A consumer can read the active list from `capabilities.wirescope.omit_default` on `/_identity` (e.g. a spawner skill can tell the user "your operator already strips `useremail`").

### Precedence
Three layers, lowest to highest: **operator default `WS_OMIT_DEFAULT` < body directive < spawn directive.**
They're resolved in that order (a later layer's `omit`/`replace`/`keep` wins per target, and a `keep` cancels a lower layer's action), so:
a spawn overrides the body, the body overrides the operator default, and a `keep` anywhere can cancel a lower layer.
Body directives are an optional "baked-in lean default" per agent type; spawn directives are the universal per-call override; the operator policy is the zero-knowledge floor.

## v1 directives

### `[wirescope:agent-name <label>]`
A human display label for the agent, surfaced in `/_admin` and `/_session` (and anywhere the proxy shows a subagent).
The wire carries no agent name — this is how you give one.

- `<label>`: free text, ≤64 chars.
- **Display-grade only** — no gate, no billing, no transform reads it.
- Falls back to the role + the per-instance `x-claude-code-agent-id` when absent.
- A spawn `agent-name` overrides a body one (precedence above).

### `[wirescope:omit <targets>]`
Strip the named context sections from the **first user message** (`messages[0]`) before forwarding upstream — the reconstruction of the CLI's internal `omitClaudeMd`, generalized.

- `<targets>`: comma-separated, from the registry below.
- Strips the matching ``# <Section>`` block out of the `<system-reminder>` in `messages[0]`, on **every** forwarded turn, deterministically.
- **No-op + logged if the section isn't found** — format drift or a wrong target name fails safe; it never over-strips.

### `[wirescope:replace <target> <inline text>]`
Keep the `# <Section>` heading but **swap its body** for the inline text — substitute different content where `omit` would have deleted it.
The model still sees the section in its `# claudeMd` slot (framed as project context), just with your content.

- One target, then the replacement text (everything after the first space, up to the closing `]`).
- **Single line** — a directive can't hold `]` or newlines. For a multi-paragraph replacement, `omit` the section and author your lean content in the body/prompt instead (the model sees it either way).
- **No-op + logged if the section isn't found** — same fail-safe as `omit`.
- Resolves per target against `omit`/`keep` (precedence below): a `replace` for a target supersedes an `omit` for it; a `keep` cancels it.

```
[wirescope:replace claudemd Follow only the conventions in docs/LEAN.md]
```

### `[wirescope:keep <targets>]`
Remove the named targets from the effective action set — the override verb.
Use it on a **spawn** to keep a section a body default would have omitted/replaced, or on a body to neutralize an earlier body `omit`/`replace`.

```
# agent body: lean default
[wirescope:omit claudemd,useremail]

# one spawn that needs CLAUDE.md after all:
[wirescope:keep claudemd]
<task…>
```

**Omit/keep target registry (v1):**

| target | strips | notes |
|---|---|---|
| `claudemd` | the `# claudeMd` system-reminder section | the project/agent CLAUDE.md attachment; usually the bulk of the tokens |
| `useremail` | the `# userEmail` system-reminder section | nothing native can remove this one |

New targets are added here; an unknown target is ignored (logged), not an error.

### `[wirescope:tools <names>]` / `[wirescope:strip-tools <names>]` / `[wirescope:keep-tools <names>]`

Trim the subagent's **tool roster** on the wire, so a spawner can customize a predefined agent — whose toolset is otherwise frozen in its `.claude/agents/<name>.md` frontmatter — **without editing its file**.
This is the largest token lever wirescope offers: the default roster is ~33 tools (≈24k tokens *every turn*) while a typical task uses ~4. Native `--tools` trims only the *main* agent; there is no per-spawn override for a subagent, which is the gap these verbs fill.

| verb | semantics |
|---|---|
| `tools <names>` | **Allowlist** — keep ONLY the named tools, drop the rest (mirrors native `--tools`). Last `tools` directive wins, so a spawn allowlist overrides a body one. |
| `strip-tools <names>` | **Denylist** — remove the named tools, keep everything else. Safe surgical removal: no need to know the agent's full roster. |
| `keep-tools <names>` | **Override** — cancel a lower layer's `strip-tools` and re-admit a name to an active allowlist (e.g. a spawn `keep-tools` over a body `strip-tools`). |

```
# a research subagent's body: never let it touch the shell
[wirescope:strip-tools Bash]

# a spawn that wants a minimal editing agent:
[wirescope:tools Read,Edit,Grep]
<task…>
```

Names are matched **case-insensitively** with the same liberal separator as `omit` (comma and/or whitespace), so `strip-tools bash webfetch` parses.
Like `omit`, the directive is read only from the system **body** or the **spawn-prompt head** (never message content → unforgeable), and is **sticky** per instance (persists past turn 1, see *Persistence*).
Gated by `WS_STRIP_TOOLS` (default on; `=0` is the deployment kill-switch).
Fail-safe: a name that matches no tool in the roster is a logged **miss**, never an over-strip.

> **Sharp edge (the spawner's call, same as `--tools`):** if the agent's prompt expects a tool you removed and the model emits a call for it, the upstream API rejects the turn. Trim to a set the agent's task actually needs.

### `[wirescope:keep-mcp <servers>]`

Re-admit (un-strip) a whole **MCP server's** tool family for this agent, cancelling the deployment-level `STRIP_MCP_SERVERS` filter. That filter (off in code, on for the canonical proxy via `start_proxy.sh`) surgically drops every `mcp__<server>__*` tool — its motivating case is the claude.ai `claude_design` connector, 20 auto-injected tools (~3.5k tok schema/turn) a coding agent never calls and that late-attaches on GUI restart, busting the tools segment. Unlike `--strict-mcp-config` (all-or-nothing), it removes exactly the named server and leaves every real project/user MCP intact, for any CLI routed through the proxy.

```
# this agent genuinely does design work — keep its tools:
[wirescope:keep-mcp claude_design]
```

Server **names** (not tool names) are matched with the same liberal separator. Same forge-safety + stickiness as the other verbs. The deployment toggle is `STRIP_MCP_SERVERS` (comma/space list of servers; empty = off); `keep-mcp` is the per-agent escape hatch.

## Directives are consumed, not forwarded

The proxy reads and acts on directives, then **strips them before forwarding upstream** — body directives from the system prompt (every `[wirescope:...]` line), and spawn directives from the prompt head (only the consumed leading lines).
The model never sees them, and they cost **zero** prefix tokens.
The strip of body directives is unconditional (the proxy always consumes its own control lines); the spawn strip is gated with the spawn read.
Both are deterministic, so the forwarded prefix stays cache-constant.

## Discovery — the spawner hint (`WS_SPAWNER_HINT`, the one visible exception)

Everywhere above, wirescope is invisible: the proxy reads directives and strips them so the model never sees them.
There is **one** opt-in exception, for discoverability — so a spawner learns the syntax with no skill or config.

When `WS_SPAWNER_HINT` is enabled, the proxy appends a **single constant block** of self-contained grammar — but only to a **spawner's** request:
- a **main/parent** line (not a subagent — gated on the `cc_is_subagent` billing-header flag), and
- one that **actually carries a subagent-spawn tool** (`Agent` / `Task`) — an agent that can't spawn never sees it.

So the hint reaches exactly the agents that can use it, and **never a subagent** (they stay pristine).
It is **self-contained, not a file pointer** — the spawner that receives it lives in its own cwd and cannot open this proxy-side doc, so the hint carries the usable verbs (`omit`/`keep`/`replace`/`agent-name`) and targets (`claudemd`/`useremail`) inline, enough to use without any fetch.
It's a **constant string at a stable position** (a trailing system block, after the cache breakpoint), so it re-anchors the prefix once and then rides the cache — it busts nothing before it.

**Mixed register — the proxy holds no per-task intent, so the hint must not push a strategy.**
It *recommends* `agent-name` (naming needs no task knowledge, has no downside, and is orthogonal to any stripping decision — the proxy can suggest it without presuming intent) but only *surfaces* `omit`/`keep`/`replace` as available capability ("here's how, if your strategy calls for it"). It never says "minimize context" or "strip X" — that would be the proxy presuming a per-task decision that belongs to the spawner. Recommend the no-downside practice; merely inform the judgment calls.

This is the lone place wirescope puts proxy-authored, model-visible text on the wire.
Note the direction: it's proxy→model *output* (a capability advertisement), not author→proxy *input*, so it doesn't expose any directive — but it does add visible text + a little cost, which is why it's **operator opt-in and default OFF**.
A consumer sees whether it's on via `capabilities.wirescope.spawner_hint`.

## Cache & correctness semantics

- **System prefix untouched (net).** The only system-body change is the *removal* of body directive lines; deterministic per type, so spawns of one agent still share a system prefix. `messages[0]` (where `omit` and the spawn strip act) sits *after* the system/tools cache breakpoints, so a change there never busts the expensive prefix.
- **Within-instance message cache stays coherent.** All strips are deterministic, so an instance's `messages[0]` is byte-stable across its turns.
- **No transcript desync.** It's request-side and idempotent: the CLI rebuilds context locally each turn; the proxy re-applies the same strip in-flight, leaving the client's transcript untouched.
- **The one invariant:** the strip must fire on every forwarded turn. A **body** directive rides `system[]` every turn, so it's automatic. A **spawn** directive is only at the `messages[0]` head on turn 1, so the proxy makes it sticky — it remembers the resolved spawn directives per `x-claude-code-agent-id` and re-applies them on every later turn of that instance (see *Persistence* above). Deterministic re-application keeps `messages[0]` byte-stable across the instance's turns.
- **Behavior-affecting, by design.** Omitting `claudeMd`/`userEmail` removes real context the agent would otherwise see. That's the author's/spawner's opt-in call. (Side note: it also tends to *reduce* fable refusal-classifier hits, since claudeMd content is a common trigger.)
- **Tool-trim reshapes the prefix — but to a net win.** Unlike the section verbs, `tools[]` sits *in front of* the first cache breakpoint, so trimming it changes the cached prefix. Because the trim is deterministic and sticky (the same roster every turn of the instance), the *smaller* set just becomes the stable cached prefix: turn 1 writes a shorter prefix, every later turn reads it. The win is the recurring per-turn carriage of the dropped tools, not a one-off. (Inconsistent trimming would bust the cache each turn — which is exactly why stickiness is load-bearing here.)

## Placement & precedence summary

- **Where:** the agent `.md` **body** (type-wide), or the **strict head of a spawn's prompt** (per-call). Never frontmatter — it's dropped. Plus the operator's `WS_OMIT_DEFAULT` floor (subagent spawns).
- **Read from:** the request's `system[]` (body) and the head of `messages[0]`'s spawn-prompt block (spawn) only — never elsewhere in message content.
- **Precedence:** spawn > body > operator default, per target (`keep` cancels a lower layer).
- **Versioning:** additive. Unknown directives/targets are safe no-ops; this doc's version bumps only on a breaking change. (v0's `ws:` prefix was removed in v1.)

## Status

v1.
- `agent-name` — live, unconditional, display-grade.
- `omit` — honored by default; the directive **is** the opt-in. `WS_OMIT=0` is a deployment kill-switch only.
- `replace` — live; substitutes a section body inline. Same `WS_OMIT` gate (the section-rewrite family).
- `keep` — live, the per-target override verb.
- `tools` / `strip-tools` / `keep-tools` — live; trim the tool roster (allowlist / denylist / override). Gated by `WS_STRIP_TOOLS` (default on; `=0` kill-switch). Sticky per instance like the section verbs.
- `keep-mcp` — live, per-agent override of the `STRIP_MCP_SERVERS` deployment filter (re-admit a whole `mcp__<server>__*` family). Server names, not tool names.
- **spawn-position directives** — capability-gated by `WS_SPAWN_DIRECTIVES` (default on; `=0` disables message-content parsing entirely).
- **operator default policy** — `WS_OMIT_DEFAULT` (comma list; default empty/off); strips those targets from every subagent spawn, keep-overridable.
- **spawner discovery hint** — `WS_SPAWNER_HINT` (default off); the one model-visible block, self-contained inline grammar, injected only into spawn-capable main agents, never subagents.

Safety is the **fail-safe miss**: a requested section that isn't found (unknown token or `<system-reminder>` format drift) is logged and skipped, never over-stripped.
A proactive canary fingerprint of the reminder heading structure (alerting on drift even when no `omit` is requested) is a planned follow-up.

`/_identity` advertises `protocols.wirescope = 1` and `capabilities.wirescope = {agent_name, omit, replace, keep, spawn, omit_default, spawner_hint, strip_tools}` so a consumer can feature-detect before relying on any of it (`omit_default` is the operator's active list — what's already stripped for you; `spawner_hint` is whether the discovery line is on; `strip_tools` is whether the tool-roster verbs are enabled).
