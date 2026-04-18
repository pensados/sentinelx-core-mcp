#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="/opt/sentinelx-core-mcp"
CONFIG_DIR="/etc/sentinelx-core-mcp"
LOG_DIR="/var/log/sentinelx-mcp"
SERVICE_NAME="sentinelx-core-mcp"
RUN_USER="sentinelx"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip curl ca-certificates

id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --home /var/lib/sentinelx --shell /usr/sbin/nologin "$RUN_USER"

mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"
cp -r "$SRC_DIR"/* "$INSTALL_DIR"/
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

if [ ! -f "$CONFIG_DIR/sentinelx-core-mcp.env" ]; then
  cp "$INSTALL_DIR/examples/sentinelx-core-mcp.env.example" "$CONFIG_DIR/sentinelx-core-mcp.env"
  chmod 640 "$CONFIG_DIR/sentinelx-core-mcp.env"
fi

cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=SentinelX Core MCP
After=network-online.target sentinelx.service
Wants=network-online.target

[Service]
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${CONFIG_DIR}/sentinelx-core-mcp.env
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

chown -R ${RUN_USER}:${RUN_USER} "$INSTALL_DIR" "$LOG_DIR"
chmod +x "$INSTALL_DIR/run.sh" "$INSTALL_DIR/install.sh"
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}
echo "Installed SentinelX Core MCP"
echo "Edit config: ${CONFIG_DIR}/sentinelx-core-mcp.env"
echo "Status: systemctl status ${SERVICE_NAME}"
