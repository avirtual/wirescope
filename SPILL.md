# Intent-body spill — wire format

Status: **built and shipped in v0.6.69 (`proxylab/spill.py`); DARK on every box we run, and expected to stay that way.**

Read this before touching `spill.py` or before implementing a spill rewrite anywhere else — the format is the contract, and the second consumer of it already exists.

> **⚠ THE TWO IMPLEMENTATIONS DIVERGED ON §4, 2026-09-21 — and the divergence is CORRECT.**
> clodex's tee (their `wire/spill.js`, ticket t1047) no longer writes `<head> @spill:<id>` into
> the transcript. It writes a past-tense receipt with **no pointer token and no `[agent:` head**:
> `(Clodex: you sent dm clodex "title" — delivered in full, 1234 B; your text is kept at <path>)`.
>
> **Why, measured on the wire (this repo, 12h window, 2026-09-21): the old §4 shape TAUGHT the
> failure it was reporting.** The placeholder lands in the model's OWN transcript record, so every
> successful spill became a worked example — in the model's own voice — of "this is how I write a
> long body". Result: 17 emission events / 19 fabricated pointers, **19/19 dangling**, 18 of 19
> reproducing the §4 shape ellipsis and all. Two lead seats, both holding a "never type it"
> instruction, one of them this seat. A prohibition in prose cannot beat few-shot evidence in the
> model's own voice; removing the reproducible token from BOTH the placeholder and the grammar line
> is the fix. Baseline instrument + numbers: `scratchpad/scan_spill_emissions.py`, HANDOFF entry
> 2026-09-21.
>
> **Consequence for THIS file.** §4 below specifies a wire shape no live implementation now emits,
> and `test_spill.py` asserts it. That is fine *as long as it is not mistaken for conformance*: our
> module is dark, so nothing observes it, and the id/path/threshold/failure-policy halves (§2, §3,
> §5) are unchanged and still shared. **If our module is ever ARMED on a headless node — the one
> case reason #2 below exists for — it must emit the receipt shape, not §4**, or it will teach the
> same defect to whatever seat it serves. Do not "re-align" clodex to §4; §4 is the stale half.
> Generalizes the CLAUDE.md lesson one turn further: a vendored consumer can be current on the CODE
> and stale on a TABLE it re-declared — and a SPEC can be the stale copy while both implementations
> have moved on.

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

**STALE as of 2026-09-21 — see the warning at the top of this file. This section records
what v0.6.69 emits and what `test_spill.py` asserts; it is NOT the shape to implement.**

    [agent:task add t42 start] @spill:1f4e9c07a2b35d68
    [agent:end]

The head line is preserved **byte-for-byte** including all modifiers; only the body
is replaced, by the bare pointer after a single space. `[agent:end]` is re-emitted
as-is.

### 4b. The shape to implement (current)

Emit a past-tense receipt that contains **no `[agent:` opener and no `@spill:` token** — nothing
a model can copy as a template for its own next intent. The head line is consumed, not preserved,
and the terminator is swallowed with it:

    (Clodex: you sent task add "T-B scratch-rewind: session-manager begin/cancel/end" — delivered in full, 4859 B; your text is kept at /Users/bogdan/.clodex/spill/clodex/1f4e9c07a2b35d68.md)

The id, the path and the write rules (§3) are unchanged — the file is still content-addressed and
still written before anything is emitted. What changes is only the *transcript-visible* rendering,
because that rendering is training data for the next turn. The intent scanner must therefore read
the **unspilled** stream (clodex does this in `wire/proxy.js`), so the receipt is never
load-bearing for dispatch: it is prose for the model and the operator, nothing parses it back.

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
