#!/usr/bin/env bash
# Lay out this collection under ansible_collections/verdel/rancher for ansible-test.
# The tree lives outside the main checkout so ansible-test resolves the collection correctly.
# This script was obtained from the repository
# https://github.com/aioue/ansible-unifi-inventory
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SANITY_ROOT="${SANITY_ROOT:-$(mktemp -d)}"
COLLECTION_ROOT="$SANITY_ROOT/ansible_collections/verdel/rancher"

if [[ -e "$COLLECTION_ROOT" ]]; then
  echo "Sanity collection tree already exists: $COLLECTION_ROOT" >&2
  exit 1
fi

mkdir -p "$(dirname "$COLLECTION_ROOT")"

rsync -a \
  --exclude .git \
  --exclude .venv \
  --exclude .pytest_cache \
  --exclude .ruff_cache \
  --exclude .sanity-tree-path \
  --exclude .collections \
  --exclude dist \
  --exclude ansible_collections \
  --exclude 'inventory/local*' \
  --exclude 'inventory/*.crt' \
  --exclude 'inventory/*.pem' \
  --exclude '*.tar.gz' \
  "$ROOT/" "$COLLECTION_ROOT/"

git -C "$SANITY_ROOT" init -q
git -C "$SANITY_ROOT" add -A
git -C "$SANITY_ROOT" \
  -c user.email=ci@localhost \
  -c user.name=ci \
  -c commit.gpgSign=false \
  commit -q -m "sanity tree"

printf '%s' "$SANITY_ROOT" > "$ROOT/.sanity-tree-path"
