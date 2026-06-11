#!/usr/bin/env bash
# Wire the shipped client pieces into ~/.claude. Conservative by design:
# only the /warm-cache command is installed automatically (a symlink through
# releases/current, so it upgrades when a release is cut); settings.json
# changes are PRINTED for manual merge, with detection of what's already
# wired. Re-run anytime — idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LAB="$(cd "$HERE/.." && pwd)"

# Prefer the releases/current path (stable across releases). Fall back to the
# invoking copy when no release with client/ exists yet.
SRC="$LAB/releases/current/client"
[ -f "$SRC/warm-cache.md" ] || SRC="$HERE"

CMD_DIR="$HOME/.claude/commands"
TARGET="$CMD_DIR/warm-cache.md"
mkdir -p "$CMD_DIR"

if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "$SRC/warm-cache.md" ]; then
  echo "/warm-cache command: already linked -> $SRC/warm-cache.md"
else
  if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
    cp -p "$TARGET" "$TARGET.bak"
    echo "backed up existing $TARGET -> $TARGET.bak"
  fi
  ln -sfn "$SRC/warm-cache.md" "$TARGET"
  echo "/warm-cache command: linked $TARGET -> $SRC/warm-cache.md"
fi

echo
if grep -qs '/_end?session' "$HOME/.claude/settings.json" 2>/dev/null; then
  echo "user-level SessionEnd->/_end hook: already wired in ~/.claude/settings.json"
else
  echo "user-level SessionEnd hook NOT found in ~/.claude/settings.json —"
  echo "merge the snippet from $SRC/README.md (proxy hold/teardown needs it)."
fi
echo "per-project wiring (env/statusline/hooks): merge $SRC/settings.example.json"
echo "into the project's .claude/settings.json."
