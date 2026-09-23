#!/usr/bin/env bash
# (Re)start the backend on the VM in the background on port 8000. Logs: ~/ekt-assistant/server.log
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/ekt-assistant}"
export PATH="$HOME/.local/bin:$PATH"
cd "$APP_DIR/backend"
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 > "$APP_DIR/server.log" 2>&1 &
sleep 3
curl -s http://127.0.0.1:8000/api/health || (echo "server did not start, see $APP_DIR/server.log"; tail -30 "$APP_DIR/server.log"; exit 1)
echo
echo "running on :8000 (pid $(pgrep -f 'uvicorn app.main:app' | head -1))"
