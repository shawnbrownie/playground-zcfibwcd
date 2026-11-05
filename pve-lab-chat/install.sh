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
  echo "Created /etc/pve-lab-chat.env -- check BACKEND_URL points at ai01."
fi

systemctl daemon-reload
systemctl enable --now pve-lab-chat
systemctl restart pve-lab-chat
sleep 1
systemctl --no-pager --lines=5 status pve-lab-chat || true

PORT=$(grep -E '^PORT=' /etc/pve-lab-chat.env | cut -d= -f2); PORT=${PORT:-8080}
if command -v tailscale >/dev/null; then
  TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
  [[ -n "$TS_IP" ]] && echo "Open from any Tailscale device: http://$TS_IP:$PORT"
fi
echo "Local: http://$(hostname -I | awk '{print $1}'):$PORT"
