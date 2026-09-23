#!/usr/bin/env bash
# One-time setup on the Brev VM (Ubuntu). Run as the default user:  bash deploy/vm_setup.sh
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/ekt-assistant}"
echo "== installing uv =="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
echo "== python deps =="
cd "$APP_DIR/backend"
uv python install 3.12 >/dev/null 2>&1 || true
uv sync
echo "== building catalog index =="
if [ -d "$APP_DIR/backend/data/dump" ] && ls "$APP_DIR/backend/data/dump"/list_*.json >/dev/null 2>&1; then
  uv run python scripts/build_index.py --dump "$APP_DIR/backend/data/dump"
else
  echo "no dump found in backend/data/dump — run scripts/dump_catalog.py first (or rsync the dump from the laptop)"
fi
echo "== done. start with: bash deploy/vm_run.sh =="
