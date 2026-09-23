#!/usr/bin/env bash
# (Re)start this project's backend on the VM. Logs: ~/ekt-assistant/server.log
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/ekt-assistant}"
APP_PORT="${APP_PORT:-8000}"
PID_FILE="$APP_DIR/server.pid"
if ! [[ "$APP_PORT" =~ ^[0-9]+$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  echo "invalid APP_PORT: $APP_PORT" >&2
  exit 1
fi
export PATH="$HOME/.local/bin:$PATH"
# The laptop's .env may enable /lab for loopback testing; keep it disabled on a shared/public VM by default.
export ENABLE_FILE_LAB="${ENABLE_FILE_LAB:-0}"
cd "$APP_DIR/backend"
if [ -f "$PID_FILE" ]; then
  old_pid="$(<"$PID_FILE")"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && ps -p "$old_pid" -o args= 2>/dev/null | grep -q 'uvicorn app.main:app'; then
    kill -TERM "$old_pid"
    for _ in {1..20}; do
      if ! kill -0 "$old_pid" 2>/dev/null; then break; fi
      sleep 0.5
    done
  fi
fi
if ss -ltnH "( sport = :$APP_PORT )" | grep -q .; then
  echo "port $APP_PORT is already in use; will not stop an unrelated service" >&2
  exit 1
fi
nohup uv run --locked uvicorn app.main:app --host 0.0.0.0 --port "$APP_PORT" --workers 1 > "$APP_DIR/server.log" 2>&1 < /dev/null &
new_pid=$!
printf '%s\n' "$new_pid" > "$PID_FILE"
for _ in {1..20}; do
  if curl -fsS "http://127.0.0.1:$APP_PORT/api/health"; then
    echo
    echo "running on :$APP_PORT (pid $new_pid)"
    exit 0
  fi
  sleep 1
done
echo "server did not start, see $APP_DIR/server.log" >&2
tail -30 "$APP_DIR/server.log" >&2
exit 1
