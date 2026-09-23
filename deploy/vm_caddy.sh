#!/usr/bin/env bash
# Run ON THE VM (needs sudo): installs Caddy and reverse-proxies the domain(s) to the backend on :8000 with automatic HTTPS.
#   ssh shared-azure-python 'bash ~/ekt-assistant/deploy/vm_caddy.sh'
# Prereqs: DNS A-record of the domain -> VM public IP (34.93.3.248); ports 80 and 443 opened in Brev "Cloud Firewall Ports".
set -euo pipefail
DOMAINS="${DOMAINS:-ekt-assistant.orau.kz, etc-assitant.orau.kz}"
export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null 2>&1; then
  sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq >/dev/null
  sudo apt-get install -y -qq caddy >/dev/null
fi
caddy version
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$DOMAINS {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
:80 {
    reverse_proxy 127.0.0.1:8000
}
CADDY
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy >/dev/null 2>&1 || true
sudo systemctl restart caddy
sleep 2
systemctl is-active caddy
echo "Caddy is proxying $DOMAINS -> 127.0.0.1:8000 (certs are issued automatically once DNS points here and 80/443 are open)"
