#!/usr/bin/env bash
# Copy hooks from .githooks to .git/hooks (run once locally)
set -euo pipefail

HOOK_DIR=".git/hooks"
SOURCE_DIR=".githooks"

if [ ! -d "$HOOK_DIR" ]; then
  echo ".git/hooks not found — are you in a git repository?"
  exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
  echo ".githooks directory not found — nothing to install"
  exit 0
fi

for f in "$SOURCE_DIR"/*; do
  fname=$(basename "$f")
  dest="$HOOK_DIR/$fname"
  cp "$f" "$dest"
  chmod +x "$dest"
  echo "Installed hook: $dest"
done

echo "Hooks installed."
