#!/usr/bin/env bash
# Deploy exactly the committed repository plus local catalog data and .env to the user's Brev VM.
# Example: APP_PORT=8881 PUBLIC_BASE_URL=https://jupyter-uct8xo9mo.gobrev.dev bash deploy/push_to_vm.sh
set -euo pipefail

VM_HOST="${VM_HOST:-distinctive-orange-mammal}"
VM_PORT="${VM_PORT:-}"
APP_DIR="${APP_DIR:-ekt-assistant}"
APP_PORT="${APP_PORT:-8000}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! [[ "$APP_DIR" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
  echo "APP_DIR must be a simple directory name" >&2
  exit 1
fi
if [ -n "$VM_PORT" ] && ! [[ "$VM_PORT" =~ ^[0-9]+$ ]]; then
  echo "invalid VM_PORT: $VM_PORT" >&2
  exit 1
fi
if ! [[ "$APP_PORT" =~ ^[0-9]+$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  echo "invalid APP_PORT: $APP_PORT" >&2
  exit 1
fi
if [ -n "$PUBLIC_BASE_URL" ] && ! [[ "$PUBLIC_BASE_URL" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
  echo "PUBLIC_BASE_URL must be an http(s) origin without a path" >&2
  exit 1
fi

ssh_opts=(-o StrictHostKeyChecking=accept-new)
scp_opts=(-q -o StrictHostKeyChecking=accept-new)
ssh_transport="ssh -o StrictHostKeyChecking=accept-new"
if [ -n "$VM_PORT" ]; then
  ssh_opts+=(-p "$VM_PORT")
  scp_opts+=(-P "$VM_PORT")
  ssh_transport+=" -p $VM_PORT"
fi

echo "== committed code -> $VM_HOST:$APP_DIR =="
ssh "${ssh_opts[@]}" "$VM_HOST" "mkdir -p \$HOME/$APP_DIR/backend/data \$HOME/$APP_DIR/data"
git -C "$REPO_DIR" archive --format=tar HEAD |
  ssh "${ssh_opts[@]}" "$VM_HOST" "tar -xf - -C \$HOME/$APP_DIR"

if [ -f "$REPO_DIR/data/ekt_catalog.sqlite3" ]; then
  echo "== catalog audit DB =="
  rsync -az -e "$ssh_transport" "$REPO_DIR/data/ekt_catalog.sqlite3" "$VM_HOST:$APP_DIR/data/ekt_catalog.sqlite3"
fi
if [ -n "${DUMP_DIR:-}" ]; then
  echo "== catalog JSON dump =="
  ssh "${ssh_opts[@]}" "$VM_HOST" "mkdir -p \$HOME/$APP_DIR/backend/data/dump"
  rsync -az -e "$ssh_transport" "$DUMP_DIR/" "$VM_HOST:$APP_DIR/backend/data/dump/"
fi
# .env is uploaded only when the VM has none yet (or FORCE_ENV=1): the VM keeps its own PUBLIC_BASE_URL.
if [ -f "$REPO_DIR/backend/.env" ] && { [ "${FORCE_ENV:-0}" = "1" ] || ! ssh "${ssh_opts[@]}" "$VM_HOST" "test -f \$HOME/$APP_DIR/backend/.env"; }; then
  scp "${scp_opts[@]}" "$REPO_DIR/backend/.env" "$VM_HOST:$APP_DIR/backend/.env"
  ssh "${ssh_opts[@]}" "$VM_HOST" "chmod 600 \$HOME/$APP_DIR/backend/.env"
fi

remote_env="APP_DIR=\$HOME/$APP_DIR APP_PORT=$APP_PORT"
if [ -n "$PUBLIC_BASE_URL" ]; then
  remote_env+=" PUBLIC_BASE_URL=$PUBLIC_BASE_URL"
fi
echo "== setup and start :$APP_PORT =="
ssh "${ssh_opts[@]}" "$VM_HOST" "$remote_env bash \$HOME/$APP_DIR/deploy/vm_setup.sh && $remote_env bash \$HOME/$APP_DIR/deploy/vm_run.sh"
