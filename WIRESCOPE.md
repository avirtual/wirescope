# Wirescope Directive Protocol (`ws:`) — v0

The wire carries **no** settable identity or context-suppression controls for a Claude Code subagent: the CLI drops the agent's frontmatter and only built-in Explore/Plan can shed `# claudeMd` (nothing sheds `# userEmail`).
Wirescope reconstructs those missing knobs as **opt-in directives an agent author writes into the agent's prompt body**, which the proxy reads and acts on at the wire.

This is the canonical grammar. The proxy owns it; consumers adhere.
If you author `.claude/agents/*.md` files (or any system prompt that routes through the proxy), this is the whole contract.

## Why the body, and why it's safe

The agent definition's **frontmatter never reaches the wire** — verified in the CLI source: only the `.md` *body* is injected, verbatim, as a `system[]` block.
So the body is the *only* channel an author controls that the proxy can see.
A directive therefore lives in the body, lands in `system[2]`, and is **cache-constant per agent type** (every spawn of one agent hashes the same system prefix — zero within-type cache cost).

The proxy parses directives **only from the system prompt, never from message content** — so nothing a user (or a tool result, or a quoted transcript) types into the conversation can forge a directive.

## Grammar

One directive per line, anywhere in the body:

```
[ws:<directive> <value>]
```

- `<directive>` — lowercase, `[a-z][a-z0-9-]*`.
- `<value>` — everything up to the closing `]`, trimmed. Lists are comma-separated (whitespace around items ignored).
- Whole-line tokens. Put each on its own line; multiple directives are fine.
- **Unknown directives are ignored** (logged once). A newer directive on an older proxy degrades to a no-op — additive forever, never a hard break.
- Absent directive ⇒ unchanged behavior. Everything here is opt-in.

## v0 directives

### `[ws:agent-name <label>]`
A human display label for the agent, surfaced in `/_admin` and `/_session` (and anywhere the proxy shows a subagent).
The wire carries no agent name — this is how you give one.

- `<label>`: free text, ≤64 chars.
- **Display-grade only** — no gate, no billing, no transform reads it.
- Falls back to the role + the per-instance `x-claude-code-agent-id` when absent (concurrent same-role subagents already stay distinct via that header; this just adds the friendly name).
- Supersedes the `[agent: NAME]` prototype (which is removed).

```
[ws:agent-name probe-alpha]
```

### `[ws:omit <targets>]`
Strip the named context sections from the **first user message** (`messages[0]`) before forwarding upstream — the reconstruction of the CLI's internal `omitClaudeMd`, generalized.

- `<targets>`: comma-separated, from the registry below.
- Strips the matching ``# <Section>`` block out of the `<system-reminder>` in `messages[0]`, on **every** forwarded turn, deterministically.
- **No-op + logged if the section isn't found** — format drift or a wrong target name fails safe; it never over-strips.

```
[ws:omit claudemd,useremail]
```

**Omit target registry (v0):**

| target | strips | notes |
|---|---|---|
| `claudemd` | the `# claudeMd` system-reminder section | the project/agent CLAUDE.md attachment; usually the bulk of the tokens |
| `useremail` | the `# userEmail` system-reminder section | nothing native can remove this one |

New targets are added here; an unknown target is ignored (logged), not an error.

## Directives are consumed, not forwarded

The proxy reads and acts on directives, then **strips every `[ws:...]` line out of the system prompt before forwarding upstream**.
The model never sees them, and they cost **zero** prefix tokens — the forwarded system body is identical to one that never carried a directive.
The strip is unconditional (it runs even where a verb like `omit` is disabled — the proxy always consumes its own control lines) and deterministic per agent type, so the stripped system prefix stays cache-constant.

## Cache & correctness semantics

- **System prefix untouched (net).** The only system-body change is the *removal* of the directive lines; deterministic per type, so spawns of one agent still share a system prefix. `messages[0]` (where `omit` acts) sits *after* the system/tools cache breakpoints, so a strip there never busts the expensive prefix.
- **Within-instance message cache stays coherent.** The strip is deterministic, so an instance's `messages[0]` is byte-stable across its turns.
- **No transcript desync.** It's request-side and idempotent: the CLI rebuilds `messages[0]` locally from its own context each turn; the proxy simply re-applies the same strip in-flight. (Unlike response mutation, this leaves the client's transcript untouched.)
- **The one invariant:** the strip must fire on every forwarded turn — guaranteed, because the directive rides `system[2]` on every turn.
- **Behavior-affecting, by design.** Omitting `claudeMd`/`userEmail` removes real context the agent would otherwise see. That's the author's opt-in call. (Side note: it also tends to *reduce* fable refusal-classifier hits, since claudeMd content is a common trigger.)

## Scope

The proxy honors a directive **wherever it appears in a system prompt** routed through it.
In practice that means Task-spawned subagents (the `.claude/agents/*.md` body is author-controlled), which is the intended use; a main agent could strip its own project CLAUDE.md the same way if its system prompt carried the marker.

## Placement & precedence summary

- **Where:** the agent `.md` **body** (never frontmatter — it's dropped).
- **Read from:** the request's `system[]` only (never messages).
- **Applies to:** any request whose system body carries the directive.
- **Versioning:** additive. Unknown directives/targets are safe no-ops; this doc's version bumps only on a breaking change.

## Status

v0 — `agent-name` is live and unconditional (display-grade, no flag).
`omit` is honored by default: the per-agent `[ws:omit ...]` directive **is** the opt-in (no directive → no change), so the directive alone gates it.
`WS_OMIT` remains as a deployment kill-switch only — set `WS_OMIT=0` to make a deployment refuse omit directives entirely.
Its safety is the **fail-safe miss**: a requested section that isn't found (unknown token or `<system-reminder>` format drift) is logged and skipped, never over-stripped.
A proactive canary fingerprint of the reminder heading structure (alerting on drift even when no `omit` is requested) is a planned follow-up.
