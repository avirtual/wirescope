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

# Gate on the offline test suites.
echo "running test suites…"
python3 test_warmth_store.py >/dev/null
python3 test_subscribers.py >/dev/null
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

echo "release $VERSION cut -> releases/$VERSION (releases/current updated)"
echo "deploy it on :7800 with:  ./run_release.sh"
