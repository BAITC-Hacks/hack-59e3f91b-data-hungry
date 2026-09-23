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
uv sync --locked
echo "== building catalog index =="
if [ -f "$APP_DIR/data/ekt_catalog.sqlite3" ]; then
  uv run --locked python scripts/build_index.py --audit-db "$APP_DIR/data/ekt_catalog.sqlite3"
  uv run --locked python scripts/import_details.py --audit-db "$APP_DIR/data/ekt_catalog.sqlite3"
elif [ -d "$APP_DIR/backend/data/dump" ] && ls "$APP_DIR/backend/data/dump"/list_*.json >/dev/null 2>&1; then
  uv run --locked python scripts/build_index.py --dump "$APP_DIR/backend/data/dump"
  if [ -f "$APP_DIR/backend/data/dump/details.jsonl" ]; then
    uv run --locked python scripts/import_details.py --file "$APP_DIR/backend/data/dump/details.jsonl"
  fi
elif [ -f "$APP_DIR/backend/data/catalog.sqlite" ]; then
  echo "no source dump found; keeping existing catalog index"
else
  echo "no catalog source found — copy data/ekt_catalog.sqlite3 or backend/data/dump first" >&2
  exit 1
fi
echo "== done. start with: bash deploy/vm_run.sh =="
