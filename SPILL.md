# Intent-body spill — wire format

Status: **built and shipped in v0.6.69 (`proxylab/spill.py`); DARK on every box we run, and expected to stay that way.**

Read this before touching `spill.py` or before implementing a spill rewrite anywhere else — the format is the contract, and the second consumer of it already exists.

> **⚠ §4 IS SOUND AS A RENDERING AND INSUFFICIENT AS A DEFENCE — read this before "fixing" it.**
> The stand-in rendering rotated three times on 2026-09-21 (t1047 receipt → t1052 strip+filler →
> t1055/t1056) and landed **back on the §4 pointer shape**, so §4 again describes what clodex's tee
> writes into a transcript. What changed is not the shape but where the defence lives: since
> `635aee45` clodex runs a **request-side editor** (`wire/spill-cut.js`) that removes every stand-in
> line — pointer, receipt, filler alike — from assistant messages in each OUTGOING request. The model
> never sees the rendering at all, so the rendering no longer has to be hard to copy.
>
> **The problem §4 was accused of is real; the rendering rewrite was NOT the cure, and the cure claim
> is retracted.** The stand-in lands in the model's own transcript record, so every successful spill
> becomes a worked example, in the model's own voice, of "this is how I write a long body" —
> measured here over 12h: 17 emission events / 19 fabricated pointers, 19/19 dangling, 18 of 19
> reproducing the §4 ellipsis and all, on two lead seats that both held a "never type it"
> instruction. But an all-shapes recount across the three renderings says the behaviour **migrated
> rather than stopped**: control 13 stand-ins / 31 long bodies (41.9%), t1047 arm 1/8 (12.5%), t1052
> arm 2/5 (40.0%), one-sided Fisher pooled p = 0.31. Each shape was harder to forge than the last
> and each was forged. **Do not record "removing the reproducible token is the fix" as a finding —
> it is an untested hypothesis with a null measurement behind it** (numbers + method:
> `scratchpad/scan_spill_emissions.py`, HANDOFF 2026-09-21 12:30). The durable half is the
> measurement lesson: *count the class ("a harness rendering typed in place of a body"), never the
> shape*, and give every new rendering a matcher BEFORE reading its arm, or a migration scores as a
> cure.
>
> **Consequence for THIS file.** §4 below stands as both the spec and what `test_spill.py` asserts;
> §4b records the t1047 receipt shape as history, not as the shape to implement. **But if our module
> is ever ARMED on a headless node — the one case reason #2 below exists for — emitting §4 alone
> reproduces exactly the configuration measured at 41.9%,** because there is no clodex tee out there
> to cut the stand-in back out of the request. An armed headless node therefore owes the *pair*: the
> §4 rendering for the operator and the transcript, plus removal of that line from every forwarded
> request. One without the other is the defect.
>
> **Pricing that cut, if we ever build it** (measured off clodex's, 2026-09-21): removing lines from
> assistant messages edits a CACHED PREFIX, so it costs one full cold write per seat on its first
> post-deploy turn — **$4.07 across 3 live seats**, scaling with window size — then rides byte-stable
> (15 consecutive requests, `changed_prefix_idx=[]`). The `[user, user]` adjacency it creates is
> accepted; `[user, system, user]` 400s, so an assistant message preceded by a `role:"system"` one
> must be left uncut. And per CLAUDE.md's rider-latch lesson, the cut must be UNCONDITIONAL: once it
> is live, a window where it does not run is what originates the bust — two cold writes per flap.

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

This is what v0.6.69 emits, what `test_spill.py` asserts, and — since `635aee45` (2026-09-21) —
what clodex's tee writes again. **It is the whole of the rendering contract and only half of the
obligation: see the warning at the top for the request-side cut that must accompany it.**

    [agent:task add t42 start] @spill:1f4e9c07a2b35d68
    [agent:end]

The head line is preserved **byte-for-byte** including all modifiers; only the body
is replaced, by the bare pointer after a single space. `[agent:end]` is re-emitted
as-is. Tail prose past the threshold spills to a bare `@spill:<id>` on its own line.

The id, the path and the write rules (§3) are unchanged by any rendering decision — the file is
content-addressed and written before anything is emitted. The intent scanner must read the
**unspilled** stream (clodex does this in `wire/proxy.js`), so the rendering is never load-bearing
for dispatch: it is prose for the model and the operator, and nothing parses it back.

### 4b. The t1047 receipt shape — history, not the shape to implement

For one day (t1047 → t1052) clodex's tee emitted a past-tense receipt instead, with no `[agent:`
opener and no `@spill:` token — nothing a model could copy as a template:

    (Clodex: you sent task add "T-B scratch-rewind: session-manager begin/cancel/end" — delivered in full, 4859 B; your text is kept at /Users/bogdan/.clodex/spill/clodex/1f4e9c07a2b35d68.md)

It was reverted in t1055/t1056 once the request-side cut made the rendering invisible to the model.
Recorded here because **it did not work**: seats fabricated the receipt sentence — quoted prefix,
byte count, absolute path, dangling id — as readily as they had fabricated the pointer, and the
`[Runtime note: action text omitted from retained history.]` filler that followed it was typed as a
whole reply twice within an hour. Reach for a harder-to-forge rendering only with that on the
record.

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
