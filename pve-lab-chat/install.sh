#!/usr/bin/env bash
# Install pve-lab-chat as a systemd service (run as root on the Proxmox host,
# an LXC, or ai01 itself). Re-running it upgrades the code and keeps your config.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/pve-lab-chat

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo $0)" >&2; exit 1; fi
command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }

install -d "$DEST/static"
install -m 0644 "$SRC/server.py" "$DEST/server.py"
install -m 0644 "$SRC/static/index.html" "$DEST/static/index.html"
install -m 0644 "$SRC/pve-lab-chat.service" /etc/systemd/system/pve-lab-chat.service

if [[ ! -f /etc/pve-lab-chat.env ]]; then
  install -m 0640 "$SRC/pve-lab-chat.env.example" /etc/pve-lab-chat.env
  echo "Created /etc/pve-lab-chat.env -- check BACKEND_URL and set ACCESS_TOKEN."
fi

systemctl daemon-reload
systemctl enable --now pve-lab-chat
systemctl restart pve-lab-chat
sleep 1
systemctl --no-pager --lines=5 status pve-lab-chat || true

PORT=$(grep -E '^PORT=' /etc/pve-lab-chat.env | cut -d= -f2); PORT=${PORT:-8080}
echo "Health: curl -s http://127.0.0.1:$PORT/healthz ; models: curl -s http://127.0.0.1:$PORT/api/models"
echo "Publish privately on the tailnet (HTTPS):  sudo tailscale serve --bg $PORT"
echo "Never use 'tailscale funnel' for this service."
