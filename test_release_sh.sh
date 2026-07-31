#!/usr/bin/env bash
# Hermetic test for release.sh. NO network, NO real remote, NO real gh:
# origin is a local bare repo and `gh` is a stub on PATH that records its
# invocation. Nothing here can publish anything.
#
# NOT part of the ./release.sh gate: that gate globs test_*.py, and this suite
# is bash. Deliberate — it builds ~10 throwaway git repos, and having a release
# cut recursively exercise the release script is a knot rather than a check.
# Run it by hand when release.sh changes:  ./test_release_sh.sh
#
# ── READ BEFORE ADDING A CASE ───────────────────────────────────────────────
# THE TRAP THIS HARNESS SETS FOR ITS OWN AUTHOR (caught 3× while writing it):
# release.sh REFUSES a dirty tree when HEAD is the released commit. So a case
# that dirties the tree to prove something about the released bytes never runs
# the code it's about — the refusal fires first and the case passes having
# tested the refusal. **If your case makes the tree differ from what ships,
# commit ahead of origin/main first and assert it with pre().** The general
# form: the refusal you are testing AROUND fires before the regime you are
# testing IN.
#
# Two more shapes, each of which passed here before being caught:
#   - `not grep ... FILE` PASSES when FILE does not exist. A negative assertion
#     is exactly where you fail to notice the target is gone — assert existence
#     with pre() first (or count with `grep -c`, which cannot pass by absence).
#   - Do not assert a state that merely CORRELATES with the property named in
#     the description. "poison test was never run" was checked as "the untracked
#     file is absent from the release worktree" — entailed by it being untracked,
#     so it could not have come back false in ANY regime, and it passed under
#     both mutations. Assert the observable CONSEQUENCE instead.
#     The distinguishing question, worth asking of every check here:
#     **could this assertion have come back false?** If not, it is not evidence.
#
# The canary in case [10] is mutation-proven; if you change release.sh's control
# flow, re-run the mutations (break it deliberately, confirm a case fails BY
# NAME). A canary nobody has seen die is not known to be alive.
# ────────────────────────────────────────────────────────────────────────────
set -uo pipefail
# Resolve the script under test RELATIVE to this file, never by absolute path:
# a hardcoded dev-tree path would silently test the wrong bytes when this suite
# runs from a release worktree.
SRC="$(cd "$(dirname "$0")" && pwd)/release.sh"
LAB="$(mktemp -d)"
PASS=0; FAIL=0
ck() { # ck <desc> <cmd...>   — prefix cmd with `not` to invert
  local d="$1"; shift
  local want=0
  if [ "${1:-}" = "not" ]; then want=1; shift; fi
  if "$@"; then local got=0; else local got=1; fi
  if [ "$got" != "$want" ]; then echo "  FAIL — $d"; FAIL=$((FAIL+1))
  else echo "  ok   — $d"; PASS=$((PASS+1)); fi
}
# A case that cannot reach the regime it means to probe must SAY SO, not pass.
pre() { # pre <desc> <cmd...>
  local d="$1"; shift
  if ! "$@"; then echo "  BROKEN PRECONDITION — $d"; FAIL=$((FAIL+1)); return 1; fi
  return 0
}

# stub gh: records calls, never talks to GitHub
mkdir -p "$LAB/bin"
cat > "$LAB/bin/gh" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$GH_LOG"
# release notes arrive on STDIN (--notes-file -); capture them or an assertion
# about note CONTENT would be reading an empty file and passing vacuously.
if [ -n "${GH_NOTES:-}" ]; then cat > "$GH_NOTES"; fi
exit 0
EOF
chmod +x "$LAB/bin/gh"
export PATH="$LAB/bin:$PATH"

new_repo() { # new_repo <name> <tests-pass:1|0>
  local n="$1" ok="$2" d="$LAB/$1"
  rm -rf "$d" "$LAB/$1.git"
  git init -q --bare "$LAB/$1.git"
  mkdir -p "$d"; git -C "$d" init -q -b main
  git -C "$d" config user.email t@t; git -C "$d" config user.name t
  cp "$SRC" "$d/release.sh"; chmod +x "$d/release.sh"
  printf '## v1.0.0 first\n\nnotes body\n' > "$d/CHANGELOG.md"
  printf 'releases/\nlogs_*/\n' > "$d/.gitignore"
  if [ "$ok" = 1 ]; then printf 'print("ok")\n' > "$d/test_a.py"
  else printf 'raise SystemExit(1)\n' > "$d/test_a.py"; fi
  git -C "$d" add -A; git -C "$d" commit -qm "reviewed base"
  git -C "$d" remote add origin "$LAB/$1.git"
  git -C "$d" push -q origin main
  echo "$d"
}

echo "=== [1] default target is origin/main, NOT local HEAD ==="
D=$(new_repo r1 1)
echo "SECRET-UNREVIEWED" > "$D/leak.txt"
git -C "$D" add -A; git -C "$D" commit -qm "unreviewed local commit"
LOCAL_HEAD=$(git -C "$D" rev-parse HEAD)
ORIGIN_MAIN=$(git -C "$D" rev-parse origin/main)
export GH_LOG="$LAB/gh1.log"; : > "$GH_LOG"
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "cut succeeded" [ "$RC" = 0 ]
TAGGED=$(git -C "$D" rev-parse "v1.0.0^{commit}")
ck "tag points at origin/main, not local HEAD" [ "$TAGGED" = "$ORIGIN_MAIN" ]
ck "tag does NOT point at the unreviewed HEAD" [ "$TAGGED" != "$LOCAL_HEAD" ]
ck "unreviewed commit did NOT reach the remote" not git -C "$LAB/r1.git" merge-base --is-ancestor "$LOCAL_HEAD" HEAD 2>/dev/null
ck "remote branch main still at the reviewed commit" \
   [ "$(git -C "$LAB/r1.git" rev-parse main)" = "$ORIGIN_MAIN" ]
ck "tag IS on the remote" git -C "$LAB/r1.git" rev-parse -q --verify refs/tags/v1.0.0 >/dev/null
ck "leak.txt absent from the released worktree" [ ! -f "$D/releases/v1.0.0/leak.txt" ]
ck "gh release was created" grep -q "release create v1.0.0" "$GH_LOG"
ck "cut announced the commit it publishes" grep -q "releasing v1.0.0 from origin/main" <<<"$OUT"
ck "cut warned HEAD is ahead" grep -q "ahead of what ships" <<<"$OUT"

echo "=== [2] explicit HEAD releases HEAD (opt-in still works) ==="
D=$(new_repo r2 1)
echo x > "$D/new.txt"; git -C "$D" add -A; git -C "$D" commit -qm "local work"
LOCAL_HEAD=$(git -C "$D" rev-parse HEAD)
export GH_LOG="$LAB/gh2.log"; : > "$GH_LOG"
"$D/release.sh" v1.0.0 HEAD >/dev/null 2>&1
ck "explicit HEAD is honoured" [ "$(git -C "$D" rev-parse 'v1.0.0^{commit}')" = "$LOCAL_HEAD" ]
ck "remote main NOT advanced even when releasing HEAD" \
   [ "$(git -C "$LAB/r2.git" rev-parse main)" != "$LOCAL_HEAD" ]

echo "=== [3] dirty tree refused when HEAD is what ships ==="
D=$(new_repo r3 1)
echo dirty >> "$D/CHANGELOG.md"
OUT=$("$D/release.sh" v1.0.0 HEAD 2>&1); RC=$?
ck "refused" [ "$RC" != 0 ]
ck "said why" grep -q "uncommitted changes" <<<"$OUT"
ck "no tag created" not git -C "$D" rev-parse -q --verify refs/tags/v1.0.0 >/dev/null

echo "=== [4] dirty tree IRRELEVANT when a different commit ships ==="
D=$(new_repo r4 1)
echo "dirty-uncommitted" > "$D/scratch.txt"
export GH_LOG="$LAB/gh4.log"; : > "$GH_LOG"
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "cut proceeded" [ "$RC" = 0 ]
ck "dirty file not in release" [ ! -f "$D/releases/v1.0.0/scratch.txt" ]

echo "=== [5] failing tests: nothing tagged, nothing pushed, no gh ==="
D=$(new_repo r5 0)
export GH_LOG="$LAB/gh5.log"; : > "$GH_LOG"
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "cut failed" [ "$RC" != 0 ]
ck "no local tag" not git -C "$D" rev-parse -q --verify refs/tags/v1.0.0 >/dev/null
ck "no remote tag" not git -C "$LAB/r5.git" rev-parse -q --verify refs/tags/v1.0.0 >/dev/null
ck "no gh release" [ ! -s "$GH_LOG" ]
ck "release worktree cleaned up" [ ! -d "$D/releases/v1.0.0" ]

echo "=== [6] gate runs the RELEASED bytes, not the working tree ==="
# tests pass at origin/main; working tree has a BROKEN test. If the gate read
# the tree it would fail. Positive control below proves the gate is live.
D=$(new_repo r6 1)
printf 'raise SystemExit(1)\n' > "$D/test_a.py"
git -C "$D" add -A; git -C "$D" commit -qm "break the test LOCALLY (not pushed)"
pre "released commit differs from HEAD (else the dirty/HEAD path fires instead)" \
   [ "$(git -C "$D" rev-parse origin/main)" != "$(git -C "$D" rev-parse HEAD)" ]
head_test_fails() { ( cd "$D" && ! python3 test_a.py >/dev/null 2>&1 ); }
pre "the test really is broken at HEAD" head_test_fails
export GH_LOG="$LAB/gh6.log"; : > "$GH_LOG"
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "test broken at HEAD does not fail a cut of origin/main" [ "$RC" = 0 ]
# positive control: same setup, but broken at the RELEASED COMMIT -> must fail
D=$(new_repo r6c 1)
printf 'raise SystemExit(1)\n' > "$D/test_a.py"
git -C "$D" add -A; git -C "$D" commit -qm "break the test"; git -C "$D" push -q origin main
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "POSITIVE CONTROL: broken RELEASED test does fail the cut" [ "$RC" != 0 ]

echo "=== [7] release notes come from the released commit ==="
D=$(new_repo r7 1)
printf '## v1.0.0 first\n\nHEAD-ONLY-NOTES\n' > "$D/CHANGELOG.md"
git -C "$D" add -A; git -C "$D" commit -qm "notes only at HEAD, never pushed"
pre "released commit differs from HEAD (else both sources agree and the case proves nothing)" \
   [ "$(git -C "$D" rev-parse origin/main)" != "$(git -C "$D" rev-parse HEAD)" ]
changelogs_differ() { ! git -C "$D" diff --quiet origin/main HEAD -- CHANGELOG.md; }
pre "the two CHANGELOGs actually differ" changelogs_differ
export GH_LOG="$LAB/gh7.log"; : > "$GH_LOG"
export GH_NOTES="$LAB/notes7.txt"; : > "$GH_NOTES"
"$D/release.sh" v1.0.0 >/dev/null 2>&1
ck "gh invoked" grep -q "release create" "$GH_LOG"
pre "notes were actually captured (else the content checks read an empty file)" \
   [ -s "$GH_NOTES" ]
ck "notes NOT taken from HEAD" not grep -q "HEAD-ONLY-NOTES" "$LAB/notes7.txt"
ck "notes taken from the released commit" grep -q "notes body" "$LAB/notes7.txt"

echo "=== [8] unpushed tag => GitHub release SKIPPED (no remote-side tagging) ==="
D=$(new_repo r8 1)
git -C "$D" remote set-url origin "$LAB/does-not-exist.git"
export GH_LOG="$LAB/gh8.log"; : > "$GH_LOG"
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "local cut still succeeds offline" [ "$RC" = 0 ]
ck "push failure reported" grep -q "push failed" <<<"$OUT"
ck "gh NOT invoked (would tag remote default branch)" [ ! -s "$GH_LOG" ]
ck "skip was explained" grep -q "GitHub Release skipped" <<<"$OUT"

echo "=== [9] unresolvable target refuses before doing anything ==="
D=$(new_repo r9 1)
OUT=$("$D/release.sh" v1.0.0 no/such/ref 2>&1); RC=$?
ck "refused" [ "$RC" != 0 ]
ck "no tag" not git -C "$D" rev-parse -q --verify refs/tags/v1.0.0 >/dev/null
ck "no worktree" [ ! -d "$D/releases/v1.0.0" ]

echo "=== [10] CANARY: no step may read the working tree ==="
# The invariant stated in release.sh above the CHANGELOG check. A future check
# added with a bare relative path (`grep ... CHANGELOG.md`, `python3 test_x.py`)
# would pass every other case here — tree and released commit agree in most of
# them — and would read GREEN precisely when they disagree. So: mangle EVERY
# tracked file in the tree, plant a poison test, and cut anyway. Anything that
# reaches for the tree now fails, loudly, instead of shipping unvalidated bytes.
D=$(new_repo r10 1)
git -C "$D" ls-files | while read -r f; do
  case "$f" in
    release.sh) : ;;                       # the script under test must stay runnable
    *) printf 'MANGLED-TREE-CONTENT\n' > "$D/$f" ;;
  esac
done
printf 'raise SystemExit(1)\n' > "$D/test_poison.py"   # untracked: must never run
# HEAD must be AHEAD of the released commit, or the dirty-tree refusal fires
# first and this case never reaches the regime it is about (it did, on the
# first run — the same trap as [6]/[7]).
git -C "$D" -c user.email=t@t -c user.name=t commit -qam "local commit, never pushed"
pre "tree really is mangled (else this case proves nothing)" \
   grep -q "MANGLED-TREE-CONTENT" "$D/CHANGELOG.md"
pre "released commit differs from HEAD (else the dirty-tree refusal fires)" \
   [ "$(git -C "$D" rev-parse origin/main)" != "$(git -C "$D" rev-parse HEAD)" ]
export GH_LOG="$LAB/gh10.log"; : > "$GH_LOG"
export GH_NOTES="$LAB/notes10.txt"; : > "$GH_NOTES"
OUT=$("$D/release.sh" v1.0.0 2>&1); RC=$?
ck "cut succeeds with a fully mangled working tree" [ "$RC" = 0 ]
# The poison test EXITS 1, so "the gate never ran it" is carried by the cut
# succeeding at all — asserting only that the file is absent from the worktree
# would pass whether or not it ran (it is untracked; it is never in there).
# Assert the observable consequence instead, and that the poison is real.
pre "poison test is present in the tree and does fail" \
   [ -f "$D/test_poison.py" ]
poison_fails() { ( cd "$D" && ! python3 test_poison.py >/dev/null 2>&1 ); }
pre "poison test really exits nonzero" poison_fails
ck "gate did not run the tree's poison test (cut would have failed)" [ "$RC" = 0 ]
ck "released worktree has the COMMITTED test, not the mangled one" \
   grep -q 'print("ok")' "$D/releases/v1.0.0/test_a.py"
# A `not grep` against a MISSING file also "passes" — the absent-file vacuity.
# Assert existence first so this cannot pass by the worktree not being there.
pre "released CHANGELOG exists (else the next check passes on a missing file)" \
   [ -f "$D/releases/v1.0.0/CHANGELOG.md" ]
ck "released worktree has the COMMITTED changelog" \
   not grep -q "MANGLED-TREE-CONTENT" "$D/releases/v1.0.0/CHANGELOG.md"
ck "no CHANGELOG warning (the check read the commit, not the mangled tree)" \
   not grep -q "has no '## v1.0.0' entry" <<<"$OUT"
pre "notes were captured (else both note checks read an empty file)" [ -s "$GH_NOTES" ]
ck "release notes free of tree content" not grep -q "MANGLED-TREE-CONTENT" "$GH_NOTES"
ck "release notes came from the commit" grep -q "notes body" "$GH_NOTES"
ck "corpus symlink removed from the frozen release" \
   [ ! -e "$D/releases/v1.0.0/logs_main" ]

echo
echo "PASS=$PASS FAIL=$FAIL"
rm -rf "$LAB"
[ "$FAIL" = 0 ]
