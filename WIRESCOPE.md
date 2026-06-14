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
- `<value>` — everything up to the closing `]`, trimmed. Lists are comma-separated (whitespace around items ignored).
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

**Why this is safe.** `messages[0]` is fixed at spawn and resent verbatim every turn; later content (tool results, fetched pages, conversation) only ever appends to `messages[1..]`.
Nothing downstream can inject into the spawn-prompt head, and a `[wirescope:...]` that appears *later* in the prompt (or anywhere in message content) is **not** a directive and is left untouched.
**Residual trust:** the spawner must not place untrusted data as the literal leading token of the prompt — the same trust already given to a body-directive author, moved to dispatch time.

Spawn directives are **capability-gated** (`WS_SPAWN_DIRECTIVES`, default on; `=0` disables all message-content directive parsing). Body directives don't read message content and are unaffected.

### Precedence
A spawn directive **overrides** the body for the same target (spawn > body), in either direction:
the effective `omit` set is *body omit − body keep + spawn omit − spawn keep*, applied in that order, so a spawn's `omit`/`keep` always wins per target.
Body directives become an optional "baked-in lean default"; spawn directives are the universal override.

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

## Directives are consumed, not forwarded

The proxy reads and acts on directives, then **strips them before forwarding upstream** — body directives from the system prompt (every `[wirescope:...]` line), and spawn directives from the prompt head (only the consumed leading lines).
The model never sees them, and they cost **zero** prefix tokens.
The strip of body directives is unconditional (the proxy always consumes its own control lines); the spawn strip is gated with the spawn read.
Both are deterministic, so the forwarded prefix stays cache-constant.

## Cache & correctness semantics

- **System prefix untouched (net).** The only system-body change is the *removal* of body directive lines; deterministic per type, so spawns of one agent still share a system prefix. `messages[0]` (where `omit` and the spawn strip act) sits *after* the system/tools cache breakpoints, so a change there never busts the expensive prefix.
- **Within-instance message cache stays coherent.** All strips are deterministic, so an instance's `messages[0]` is byte-stable across its turns.
- **No transcript desync.** It's request-side and idempotent: the CLI rebuilds context locally each turn; the proxy re-applies the same strip in-flight, leaving the client's transcript untouched.
- **The one invariant:** the strip must fire on every forwarded turn — guaranteed, because both the body directive (`system[]`) and the spawn prompt (`messages[0]`) ride every turn.
- **Behavior-affecting, by design.** Omitting `claudeMd`/`userEmail` removes real context the agent would otherwise see. That's the author's/spawner's opt-in call. (Side note: it also tends to *reduce* fable refusal-classifier hits, since claudeMd content is a common trigger.)

## Placement & precedence summary

- **Where:** the agent `.md` **body** (type-wide), or the **strict head of a spawn's prompt** (per-call). Never frontmatter — it's dropped.
- **Read from:** the request's `system[]` (body) and the head of `messages[0]`'s spawn-prompt block (spawn) only — never elsewhere in message content.
- **Precedence:** spawn > body, per target.
- **Versioning:** additive. Unknown directives/targets are safe no-ops; this doc's version bumps only on a breaking change. (v0's `ws:` prefix was removed in v1.)

## Status

v1.
- `agent-name` — live, unconditional, display-grade.
- `omit` — honored by default; the directive **is** the opt-in. `WS_OMIT=0` is a deployment kill-switch only.
- `replace` — live; substitutes a section body inline. Same `WS_OMIT` gate (the section-rewrite family).
- `keep` — live, the per-target override verb.
- **spawn-position directives** — capability-gated by `WS_SPAWN_DIRECTIVES` (default on; `=0` disables message-content parsing entirely).

Safety is the **fail-safe miss**: a requested section that isn't found (unknown token or `<system-reminder>` format drift) is logged and skipped, never over-stripped.
A proactive canary fingerprint of the reminder heading structure (alerting on drift even when no `omit` is requested) is a planned follow-up.

`/_identity` advertises `protocols.wirescope = 1` and `capabilities.wirescope = {agent_name, omit, replace, keep, spawn}` so a consumer can feature-detect before relying on any of it.
