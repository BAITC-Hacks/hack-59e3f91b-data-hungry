#!/usr/bin/env bash
# From the laptop: sync the repo (and optionally the catalog dump) to the VM and restart the server.
#   VM_HOST=distinctive-orange-mammal bash deploy/push_to_vm.sh      # user's configured SSH alias
#   VM_HOST="ubuntu@global.prd.ga.run.brev.nvidia.com" VM_PORT=47026 bash deploy/push_to_vm.sh
set -euo pipefail
VM_HOST="${VM_HOST:-distinctive-orange-mammal}"
VM_PORT="${VM_PORT:-}"   # empty = take the port from ~/.ssh/config (Brev alias)
APP_DIR="${APP_DIR:-ekt-assistant}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH="ssh ${VM_PORT:+-p $VM_PORT} -o StrictHostKeyChecking=accept-new"
echo "== rsync code -> $VM_HOST:$APP_DIR =="
rsync -az --delete -e "$SSH" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'backend/.env' --exclude 'server.log' \
  --exclude 'backend/data/catalog.sqlite*' --exclude 'backend/data/attachments.sqlite3*' \
  --exclude 'backend/data/uploads' --exclude 'backend/data/dump' \
  "$REPO_DIR/" "$VM_HOST:$APP_DIR/"
if [ -n "${DUMP_DIR:-}" ]; then
  echo "== rsync dump ($DUMP_DIR) =="
  $SSH "$VM_HOST" "mkdir -p $APP_DIR/backend/data/dump"
  rsync -az -e "$SSH" "$DUMP_DIR/" "$VM_HOST:$APP_DIR/backend/data/dump/"
fi
# .env is uploaded only when the VM has none yet (or FORCE_ENV=1): the VM keeps its own PUBLIC_BASE_URL.
if [ -f "$REPO_DIR/backend/.env" ] && { [ "${FORCE_ENV:-0}" = "1" ] || ! $SSH "$VM_HOST" "test -f $APP_DIR/backend/.env"; }; then
  scp ${VM_PORT:+-P $VM_PORT} -q "$REPO_DIR/backend/.env" "$VM_HOST:$APP_DIR/backend/.env"
fi
echo "== setup + run on VM =="
$SSH "$VM_HOST" "APP_DIR=\$HOME/$APP_DIR bash \$HOME/$APP_DIR/deploy/vm_setup.sh && APP_DIR=\$HOME/$APP_DIR bash \$HOME/$APP_DIR/deploy/vm_run.sh"
