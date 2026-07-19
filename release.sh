#!/usr/bin/env bash
# Cut a RELEASE of the proxy: tag the current commit and check it out as a
# frozen worktree under releases/<version>, then point releases/current at it.
#
#   ./release.sh v0.1.0
#
# The OFFICIAL long-running proxy (the one workbench agents use) runs from
# releases/current via ./run_release.sh — so day-to-day development restarts
# in this working tree never touch it; agents only see changes when you cut a
# new release and run ./run_release.sh again.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:?usage: ./release.sh vX.Y.Z}"

# Refuse to release from a dirty tree — the tag must describe what ships.
if ! git diff-index --quiet HEAD --; then
  echo "ERROR: uncommitted changes — commit first, a release must equal a commit." >&2
  exit 1
fi
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
  echo "ERROR: tag $VERSION already exists." >&2
  exit 1
fi

# A release should tell its story: warn (don't block) when CHANGELOG.md has
# no entry for the version being cut.
if ! grep -q "^## $VERSION\b" CHANGELOG.md 2>/dev/null; then
  echo "WARNING: CHANGELOG.md has no '## $VERSION' entry — add one (top of file) so the release is self-describing." >&2
fi

# Gate on ALL the offline test suites (every test_*.py — a new suite is gated
# the day it lands, no list to forget to extend).
echo "running test suites…"
for t in test_*.py; do
  python3 "$t" >/dev/null || { echo "FAILED: $t" >&2; exit 1; }
done
echo "tests OK"

git tag -a "$VERSION" -m "proxy release $VERSION"
mkdir -p releases
git worktree add --detach "releases/$VERSION" "$VERSION" >/dev/null
# Stamp the worktree so the proxy self-reports WHICH release serves a port
# (/_status proxy.version, /_admin header). Dev trees fall back to git describe.
printf '%s %s %s\n' "$VERSION" \
  "$(git rev-parse --short "$VERSION^{commit}")" "$(date +%F)" \
  > "releases/$VERSION/RELEASE"
ln -sfn "$VERSION" releases/current

# Publish so other machines building from GitHub can see the release
# (v0.6.14 once lived only on this machine — a remote clodex stayed on .13).
# Non-fatal: offline shouldn't block a local cut, but say so loudly.
if ! git push origin HEAD "$VERSION"; then
  echo "WARNING: push failed — $VERSION exists only locally. Run: git push origin main $VERSION" >&2
fi

echo "release $VERSION cut -> releases/$VERSION (releases/current updated)"
echo "deploy it on :7800 with:  ./run_release.sh"
