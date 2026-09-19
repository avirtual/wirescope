# Intent-body spill — wire format

Status: **built and shipped in v0.6.69 (`proxylab/spill.py`); DARK on every box we run, and expected to stay that way.**

Read this before touching `spill.py` or before implementing a spill rewrite anywhere else — the format is the contract, and the second consumer of it already exists.

**Why it is dark, which is not the same as why it would be deleted.** clodex signed this format off, then moved the rewrite into its own in-process wire tee (`wire/proxy.js`), which sits between each seat and our `/agent/<name>` route and implements this document verbatim. Bogdan's ruling (2026-09-19) settled the ownership question on principle rather than on which implementation worked: *no clodex intent grammar is to live in wirescope*, which is the same ruling CLAUDE.md already recorded when `WB_INTENT_DISPATCH` was retired in 2026-06 — "No app-specific protocol parsing remains in the proxy." Our four `WIRESCOPE_SPILL_*` knobs therefore stay unset in every deployment, and clodex explicitly does not depend on this code.

**What it is kept FOR.** Two things, and they are the standard to judge a deletion against:

1. **This file is the format's specification, and clodex's tee is an implementation of it.** Two implementations now compute the same sha over the same delimited bytes. A spec with an executable, tested reference is a different artifact from a spec alone — `test_spill.py` is the conformance suite for the format, not merely a test of our module.
2. **It is the drop-in for a box that runs wirescope WITHOUT clodex's tee** (headless nodes), which is the case the ownership ruling does not cover, because there is no tee there to own the rewrite.

**The cost of keeping it is bounded and was measured.** `enabled()` requires all four knobs, and `SpillTee.feed` returns its chunk before parsing anything when unarmed, so an unconfigured box takes one boolean per streamed chunk and no other path differs. The maintenance obligation is the honest cost: `spill` is registered in both module registries, so it rides the suite on every release.

**If a future session wants to delete this, the argument has to be about the maintenance cost or about the format being dead — not about it being unused.** Dark-and-maintained was chosen deliberately here, exactly as `fold` was.

## 1. Configuration (env, read at proxy launch)

The feature is OFF unless **all four** grammar/routing knobs are set. Absent/empty = today's behaviour exactly, no code path differs.

| var | meaning | default |
|---|---|---|
| `WIRESCOPE_SPILL_DIR` | absolute path to the spill ROOT. wirescope writes `<root>/<agent>/<id>.md`. | unset = off |
| `WIRESCOPE_SPILL_VERBS` | comma-separated verb keys that may spill. Empty = off. | unset = off |
| `WIRESCOPE_SPILL_OPEN` | the opener token, e.g. `[agent:` | unset = off |
| `WIRESCOPE_SPILL_END` | the terminator line, e.g. `[agent:end]` | unset = off |
| `WIRESCOPE_SPILL_MIN_BYTES` | body must EXCEED this to spill | `800` |
| `WIRESCOPE_SPILL_MAX_BYTES` | buffer cap; past it, flush held bytes and stream the rest untouched | `262144` (256 KiB) |

The dir is a parameter rather than a hardcoded `~/.clodex/spill` on purpose:
wirescope should not bake a consumer's private layout in, and a scratch/test root
becomes trivial.

**The grammar tokens are configuration too, and have NO default** (added at build
time, 2026-09-19). A durable ruling in CLAUDE.md retired the last app-specific
intent parser from this proxy on 2026-06-12 — *"No app-specific protocol parsing
remains in the proxy"* — and hardcoding `[agent:` / `[agent:end]` would have
quietly overturned it for one consumer. wirescope knows only the SHAPE (opener,
verb, `]`, body, terminator line); clodex supplies its own tokens, exactly as it
already supplies the verb vocabulary. Nothing changes on clodex's side beyond
passing two more env vars at launch, and the feature stays inert for anyone who
does not.

**Verb keys are DOTTED, never spaced** — `task.add`, not `task add`. A comma list
containing spaces invites trim bugs on both sides. Initial scope ruling:

    WIRESCOPE_SPILL_VERBS=task.add,task.respec,task.reject,context.compact,context.clear,context.reload

`memory.remember` needs no special case: it is excluded by not being in the list.
An unlisted or unknown verb is never touched.

## 2. What counts as a body

Given a greedy intent:

    [agent:task add t42 start] <body…>
    [agent:end]

* Body STARTS immediately after the head line's `]`, skipping **exactly one**
  space if present.
* Body ENDS immediately before the newline preceding the `[agent:end]` line.
* No other normalisation. No trailing-newline insertion, no trimming, no
  re-encoding. The file gets exactly these bytes, so what the model wrote is what
  a recipient reads.

Threshold is measured on **these bytes, UTF-8**, and is strict `>`.

## 3. The id and the file

    id       = lowercase hex, sha256(body bytes)[:16]     # 16 chars, 64 bits
    path     = <WIRESCOPE_SPILL_DIR>/<agent>/<id>.md
    pointer  = @spill:<id>

Content-addressed, so a re-emitted identical body resolves to the same file:
idempotent across retries and `--resume`. Write is atomic (temp + `os.replace`,
same rule as `/_compact`); if the file already exists it is left alone rather than
rewritten, which also removes any torn-read window.

16 hex is a deliberate pick: at 8 the collision floor is uncomfortably close for a
directory that accumulates for months, and the id is never typed by a human.

**`<agent>` is a path component and is validated before use**, against the exact rule
clodex mints seat names with: `^(?!\.+\Z)[a-zA-Z0-9._-]{1,64}\Z`. A name failing it
does not spill — the body forwards untouched. No `..`, no separators, no absolute
paths, ever.

Three things about that regex are load-bearing, and each was a live defect at some
point in the build:

* **Dots are LEGAL**, and only an all-dots name is rejected. `.hidden` is a
  deliberately accepted seat name on clodex's side (pinned there by
  `test/name-dot-only.test.js`), so a narrower charset would have silently denied
  the feature to a seat called `t42.fix` rather than failing loudly.
* **The anchor is `\Z`, never `$`.** Python's `$` also matches before a trailing
  newline, so `$` would accept `"ok\n"` as a directory component.
* **The validator is what keeps `..` out of a path, not the router.** `core._ROUTE`
  accepts dots in the agent name, so `/agent/../anthropic/v1/messages` parses with
  `name='..'` (verified by probe). Any feature turning a route name into a
  filesystem path must validate separately; this one fails closed.

## 4. What goes on the wire

    [agent:task add t42 start] @spill:1f4e9c07a2b35d68
    [agent:end]

The head line is preserved **byte-for-byte** including all modifiers; only the body
is replaced, by the bare pointer after a single space. `[agent:end]` is re-emitted
as-is.

## 5. Failure policy — never lose a spec

Every failure mode forwards the ORIGINAL body untouched:

* spill dir missing / unwritable / not a directory
* agent name fails the charset
* body ≤ threshold (not a failure — just no pointer)
* body exceeds `MAX_BYTES`: flush what is held, stream the remainder through
* no `[agent:end]` before end-of-stream: flush held bytes at end-of-stream

A truncated task spec is far worse than a spammy transcript, so there is no path
where a pointer is emitted for a body that was not fully and durably written
first. Write-then-rewrite, never rewrite-then-write.

## 6. Resolver contract (clodex side)

* Accept `@spill:<id>` only where `id` matches `^[0-9a-f]{16}$`.
* Resolve strictly to `<root>/<agent>/<id>.md` — path built from the agent's own
  name, never from anything in the message.
* Do not follow symlinks; do not resolve a pointer that arrived in a RECEIVED
  message (per the scope ruling, only the sender's own transcript pointers are
  resolved, by the sender, at intent time).
* A pointer that does not resolve is an ERROR to surface, never a silent empty
  body — an empty ticket spec is the one outcome worse than a stall.

## 7. Property worth stating once

Content-addressing is **idempotent, not opaque**. A body is recoverable by anyone
who can guess it. Everything here is same-user under `0700`, so this is not a
vulnerability — but the file name must never be described as a secret, or someone
will later treat it as an access control.
