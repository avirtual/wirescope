#!/usr/bin/env bash
# Cut a RELEASE of the proxy: tag an EXPLICITLY NAMED commit and check it out
# as a frozen worktree under releases/<version>, then point releases/current
# at it.
#
#   ./release.sh v0.1.0              # releases origin/main (the reviewed, published state)
#   ./release.sh v0.1.0 HEAD         # releases your local HEAD, ancestors included
#   ./release.sh v0.1.0 <sha|ref>    # releases exactly that commit
#
# WHY the commit is named rather than inherited: this script publishes to a
# PUBLIC repo. It used to tag HEAD and `git push origin HEAD "$VERSION"`, so
# whatever happened to sit on local main — a half-finished commit of your own,
# anything a peer left in the tree — auto-published at the next cut. Defaulting
# to origin/main means the default release is bytes that are already public,
# and shipping anything else requires saying its name.
#
# Everything the cut inspects (tests, CHANGELOG entry, GitHub release notes) is
# read from the RELEASED COMMIT, never from the working tree. Reading them from
# the tree would validate bytes that aren't the ones shipping — green over the
# gap, and invisible precisely when the tree and the commit disagree.
#
# The OFFICIAL long-running proxy (the one workbench agents use) runs from
# releases/current via ./run_release.sh — so day-to-day development restarts
# in this working tree never touch it; agents only see changes when you cut a
# new release and run ./run_release.sh again.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

VERSION="${1:?usage: ./release.sh vX.Y.Z [commit-ish, default origin/main]}"
COMMITISH="${2:-origin/main}"

# Deliberately NO `git fetch`: what ships must not depend on a network call
# made mid-cut. origin/main is whatever you last fetched, and the resolved
# sha + subject are printed below so a stale ref is visible rather than silent.
COMMIT="$(git rev-parse -q --verify "$COMMITISH^{commit}")" || {
  echo "ERROR: cannot resolve '$COMMITISH' to a commit." >&2
  echo "       (if origin/main is missing, fetch once or name a commit explicitly)" >&2
  exit 1
}

if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
  echo "ERROR: tag $VERSION already exists." >&2
  exit 1
fi

# Say what is about to become public, before doing any of it.
echo "releasing $VERSION from $COMMITISH"
echo "  commit:  $(git log -1 --format='%h %s' "$COMMIT")"
echo "  authored: $(git log -1 --format='%ai' "$COMMIT")"

HEAD_SHA="$(git rev-parse HEAD)"
if [ "$COMMIT" = "$HEAD_SHA" ]; then
  # The released commit IS your HEAD, so uncommitted work is work you probably
  # meant to include. Refuse — same guarantee as before.
  if ! git diff-index --quiet HEAD --; then
    echo "ERROR: uncommitted changes — commit first, a release must equal a commit." >&2
    exit 1
  fi
else
  # A different commit ships, so the state of your tree cannot affect it. Not
  # an error — but say plainly what is being left behind, since this is exactly
  # the case the old HEAD-tagging behaviour got wrong.
  AHEAD="$(git rev-list --count "$COMMIT..HEAD" 2>/dev/null || echo '?')"
  echo "  NOTE: your HEAD ($(git rev-parse --short HEAD)) is $AHEAD commit(s) ahead of what ships;"
  echo "        those commits and any uncommitted changes are NOT published by this cut."
fi

# ── INVARIANT for everything below ──────────────────────────────────────────
# EVERY check from here on must read the RELEASED COMMIT, never the working
# tree: `git show "$COMMIT:path"`, or a path inside releases/$VERSION — never a
# bare relative path, which resolves against the dev tree.
#
# This became load-bearing the moment the tag stopped following HEAD. Before
# that, tree == HEAD == released commit by construction, so reading the tree was
# harmless and nothing had to say so. Now a tree-reading check validates bytes
# that aren't shipping, and it reads GREEN exactly when the tree and the commit
# disagree — i.e. in the very case this script exists to support. Adding one
# would not fail any existing test; test_release_sh.sh case [10] is the canary
# that catches it (it cuts with every tracked file mangled in the tree).
# ────────────────────────────────────────────────────────────────────────────

# A release should tell its story: warn (don't block) when the RELEASED
# COMMIT's CHANGELOG.md has no entry for the version being cut.
#
# NOT a pipeline, deliberately. `git show … | grep -q` under `set -o pipefail`
# reports 141 whenever the entry IS found: grep -q exits at the first match,
# git show dies of SIGPIPE writing the rest, and pipefail surfaces that as the
# pipeline's status. Inverted by `!`, the warning then fires in BOTH cases —
# the check could not come back clean on any CHANGELOG larger than the pipe
# buffer, which is every real one (ours: 62 KB). Live-fired on the v0.6.44 cut
# while the entry was present two lines from the top. Read the blob into a
# variable and match a herestring: no upstream process, so nothing to SIGPIPE.
CHANGELOG_AT_COMMIT="$(git show "$COMMIT:CHANGELOG.md" 2>/dev/null || true)"
if ! grep -q "^## $VERSION\b" <<<"$CHANGELOG_AT_COMMIT"; then
  echo "WARNING: CHANGELOG.md at $COMMITISH has no '## $VERSION' entry — add one (top of file) so the release is self-describing." >&2
fi

mkdir -p releases
git worktree add --detach "releases/$VERSION" "$COMMIT" >/dev/null

# Gate on ALL the offline test suites (every test_*.py — a new suite is gated
# the day it lands, no list to forget to extend), run INSIDE the release
# worktree so they exercise the bytes that ship rather than the bytes you have.
# The capture corpus is gitignored, so it is absent from a fresh worktree; link
# it in for the run (suites resolve `logs_main` relatively) and take it back out
# so the frozen release keeps no dangling path into the dev tree.
echo "running test suites (against $COMMITISH, in releases/$VERSION)…"
release_gate_failed=0
if [ -d "$ROOT/logs_main" ]; then
  ln -sfn "$ROOT/logs_main" "releases/$VERSION/logs_main"
else
  echo "  NOTE: no logs_main corpus — replay-based suites will skip their real-capture cases." >&2
fi
for t in "releases/$VERSION"/test_*.py; do
  [ -e "$t" ] || { echo "ERROR: no test suites in the released commit — refusing to cut blind." >&2; release_gate_failed=1; break; }
  ( cd "releases/$VERSION" && python3 "$(basename "$t")" >/dev/null ) \
    || { echo "FAILED: $(basename "$t") (at $COMMITISH)" >&2; release_gate_failed=1; break; }
done
rm -f "releases/$VERSION/logs_main"
if [ "$release_gate_failed" = 1 ]; then
  git worktree remove --force "releases/$VERSION" 2>/dev/null || true
  echo "ERROR: gate failed — nothing tagged, nothing pushed." >&2
  exit 1
fi
echo "tests OK"

git tag -a "$VERSION" -m "proxy release $VERSION" "$COMMIT"
# Stamp the worktree so the proxy self-reports WHICH release serves a port
# (/_status proxy.version, /_admin header). Dev trees fall back to git describe.
printf '%s %s %s\n' "$VERSION" \
  "$(git rev-parse --short "$VERSION^{commit}")" "$(date +%F)" \
  > "releases/$VERSION/RELEASE"
ln -sfn "$VERSION" releases/current

# Publish so other machines building from GitHub can see the release
# (v0.6.14 once lived only on this machine — a remote clodex stayed on .13).
# Non-fatal: offline shouldn't block a local cut, but say so loudly.
#
# Push the TAG ONLY. The old form was `git push origin HEAD "$VERSION"`, which
# published local main as a side effect of cutting a release — the branch push
# was never the point, and it was the mechanism by which an unreviewed commit
# became public. The tag carries the released commit and all its ancestors, so
# anyone building from GitHub gets everything the release needs. Advancing the
# BRANCH is a separate decision, made deliberately (`git push origin main`),
# not smuggled into a cut.
tag_pushed=1
if ! git push origin "$VERSION"; then
  tag_pushed=0
  echo "WARNING: push failed — $VERSION exists only locally. Run: git push origin $VERSION" >&2
fi

# Also publish a GitHub RELEASE (the human-facing Releases page) — a separate
# step from pushing the tag, and the one that silently lapsed for
# v0.6.25–v0.6.36 (page sat at v0.6.24 for two weeks while the code was
# current). Notes = this version's CHANGELOG.md section AS OF THE RELEASED
# COMMIT (not the working tree — the notes must describe what shipped); title =
# its heading suffix when the entry carries one. Non-fatal like the push.
#
# Gated on the tag actually reaching the remote: `gh release create` on an
# unpushed tag would create the tag REMOTELY at the default branch's head —
# i.e. publish a release pointing at bytes this cut never chose. Skip instead.
if [ "$tag_pushed" = 0 ]; then
  echo "WARNING: tag not on remote — GitHub Release skipped (it would tag the remote default branch, not $COMMITISH)." >&2
elif command -v gh >/dev/null; then
  NOTES="$(git show "$COMMIT:CHANGELOG.md" 2>/dev/null | awk -v v="$VERSION" '$0 ~ "^## "v" " || $0 ~ "^## "v"$" {f=1; next} /^## v/{f=0} f')"
  if [ -n "$NOTES" ]; then
    if ! printf '%s\n' "$NOTES" | gh release create "$VERSION" --title "$VERSION" --notes-file -; then
      echo "WARNING: GitHub Release not created — run: gh release create $VERSION --notes-file <(changelog section)" >&2
    fi
  else
    echo "WARNING: no CHANGELOG.md section for $VERSION — GitHub Release skipped." >&2
  fi
else
  echo "WARNING: gh not installed — GitHub Release not created for $VERSION." >&2
fi

echo "release $VERSION cut -> releases/$VERSION (releases/current updated)"
echo "deploy it on :7800 with:  ./run_release.sh"
