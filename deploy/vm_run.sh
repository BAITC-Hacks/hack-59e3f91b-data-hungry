#!/usr/bin/env bash
# (Re)start the backend on the VM. If the systemd unit ekt-assistant.service exists, restart it (auto-restart on
# failure/reboot); otherwise fall back to a nohup process on port 8000. Logs: journalctl -u ekt-assistant / server.log
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/ekt-assistant}"
export PATH="$HOME/.local/bin:$PATH"
if systemctl list-unit-files 2>/dev/null | grep -q '^ekt-assistant.service'; then
  sudo systemctl restart ekt-assistant
  sleep 5
else
  cd "$APP_DIR/backend"
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  sleep 1
  nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 > "$APP_DIR/server.log" 2>&1 &
  sleep 3
fi
curl -s http://127.0.0.1:8000/api/health || (echo "server did not start"; sudo journalctl -u ekt-assistant -n 20 --no-pager 2>/dev/null || tail -30 "$APP_DIR/server.log"; exit 1)
echo
echo "running on :8000"
