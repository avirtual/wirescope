# clodex seat `clodex`: append-prompt.md edit not on the wire (2026-09-21)

Session `8f2353a5-0fd4-41bd-a117-a52736f29e6b`, agent `clodex-clodex-6e8596da`, model fable-5-1.

## 1. Diff: byte-exact, and it is NOT a "nearly the same size" coincidence

System block 2 = 61,437 ch. The CLI's own prose is the first **5,117 ch**; the Clodex-appended
segment is the remaining **56,320 ch / 56,684 bytes**.

    wire appended segment   56,684 B
  + 3 scratch rows          +1,697 B
  = 58,381 B                == append-prompt.md on disk, exactly

**Zero unexplained bytes. Zero reverse hunks** — nothing on the wire is absent from the file.
The ONLY hunk is an insertion after line 29 (`[agent:context reload]`): the three rows
`[agent:scratch begin]`, `[agent:scratch end] <summary>`, `[agent:scratch cancel]`.

So the wire is exactly the PREVIOUS revision of that file. Not a different file, not a
truncation, not a proxy edit.

## 2. The respawn did NOT pick it up — this is the load-bearing correction

Block-2 hash `dd8d95c63c` (61,437 ch) is byte-identical across **all 81 requests** from
23:43:39 to 00:19:31. Captures straddle the respawn:

  - last BEFORE: 23:44:58  dd8d95c63c  61,437 ch
  - respawn pid 60091 + file mtime: **23:45:28** (same second)
  - first AFTER: 23:45:31  dd8d95c63c  61,437 ch   <- unchanged

(The session's very first capture at 23:43:39 is a different 3,059-ch block: that is the
CLI's tool-less title side-call, not a prompt revision.)

An earlier message from me said "it needs `[agent:context reload]`". **That was wrong** — a
full `create()` with `--resume` already happened at 23:45:28 and did not pick the rows up.

## 3. Two hypotheses, one cheap discriminating test

  (a) write/read RACE — respawn read the file in the same second it was rewritten;
  (b) the `--resume` path never re-reads append-prompt.md at all.

Test: `touch` the file (or add a marker line), respawn WITHOUT any concurrent write, then
re-check the wire. Rows appear => (a), a race, and the fix is ordering the write before the
spawn. Rows still absent => (b), and the resume path itself is the defect.

## 4. Does wirescope pin or replay a system block?

**No.** The proxy never caches, pins or replays a system prompt. It forwards what the CLI
sends and edits only per-request (relocate/strip transforms, all deterministic). The
frozen hash is the CLI serving its boot-time copy, not wirescope replaying anything.
