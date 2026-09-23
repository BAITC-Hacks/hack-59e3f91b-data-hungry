#!/usr/bin/env bash
# Run on the VM only after ekt.orau.kz has a DNS A record and TCP 80/443 are open.
# Serves only ekt.orau.kz; other orau.kz hosts are deliberately untouched.
set -euo pipefail

DOMAIN=ekt.orau.kz
APP_PORT="${APP_PORT:-8881}"
EXPECTED_IP="${EXPECTED_IP:-}"

if [[ ! "$APP_PORT" =~ ^[0-9]+$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  echo "Invalid APP_PORT: $APP_PORT" >&2
  exit 1
fi
if ! curl -fsS --max-time 5 "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null; then
  echo "Backend is not healthy on 127.0.0.1:${APP_PORT}" >&2
  exit 1
fi

DNS_IP="$(getent ahostsv4 "$DOMAIN" | awk 'NR == 1 { print $1 }' || true)"
if [[ -z "$DNS_IP" ]]; then
  echo "No A record found for $DOMAIN; configure DNS before enabling HTTPS" >&2
  exit 1
fi
if [[ -n "$EXPECTED_IP" && "$DNS_IP" != "$EXPECTED_IP" ]]; then
  echo "$DOMAIN resolves to $DNS_IP, expected $EXPECTED_IP" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null 2>&1; then
  sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq >/dev/null
  sudo apt-get install -y -qq caddy >/dev/null
fi
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:$APP_PORT
}
CADDY
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl restart caddy
systemctl is-active caddy
echo "Caddy is proxying $DOMAIN -> 127.0.0.1:$APP_PORT; verify HTTPS from outside the VM"
