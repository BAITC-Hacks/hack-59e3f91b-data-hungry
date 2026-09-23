#!/usr/bin/env bash
# From the laptop: sync the repo (and optionally the catalog dump) to the VM and restart the server.
#   VM_HOST=shared-azure-python bash deploy/push_to_vm.sh            # after `brev login` (ssh alias)
#   VM_HOST="ubuntu@global.prd.ga.run.brev.nvidia.com" VM_PORT=47026 bash deploy/push_to_vm.sh
set -euo pipefail
VM_HOST="${VM_HOST:-shared-azure-python}"
VM_PORT="${VM_PORT:-22}"
APP_DIR="${APP_DIR:-ekt-assistant}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH="ssh -p $VM_PORT -o StrictHostKeyChecking=accept-new"
echo "== rsync code -> $VM_HOST:$APP_DIR =="
rsync -az --delete -e "$SSH" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'backend/data/catalog.sqlite*' --exclude 'backend/data/uploads' --exclude 'backend/data/dump' \
  "$REPO_DIR/" "$VM_HOST:$APP_DIR/"
if [ -n "${DUMP_DIR:-}" ]; then
  echo "== rsync dump ($DUMP_DIR) =="
  $SSH "$VM_HOST" "mkdir -p $APP_DIR/backend/data/dump"
  rsync -az -e "$SSH" "$DUMP_DIR/" "$VM_HOST:$APP_DIR/backend/data/dump/"
fi
if [ -f "$REPO_DIR/backend/.env" ]; then
  scp -P "$VM_PORT" -q "$REPO_DIR/backend/.env" "$VM_HOST:$APP_DIR/backend/.env"
fi
echo "== setup + run on VM =="
$SSH "$VM_HOST" "APP_DIR=\$HOME/$APP_DIR bash \$HOME/$APP_DIR/deploy/vm_setup.sh && APP_DIR=\$HOME/$APP_DIR bash \$HOME/$APP_DIR/deploy/vm_run.sh"
