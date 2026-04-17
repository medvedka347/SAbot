#!/bin/bash
set -e

# =============================================================================
# Manual deploy script for SABot.
# Run this on your server as root (or a user with passwordless sudo).
# Usage: bash /opt/sabot/deploy/deploy.sh
# =============================================================================

# Auto-detect bot directory (where this script lives: deploy/deploy.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="sabot"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Detect if we need sudo
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

# Detect owner of the bot directory (service will run as this user)
SERVICE_USER=$(stat -c '%U' "$BOT_DIR" 2>/dev/null || echo "")
if [ -z "$SERVICE_USER" ] || [ "$SERVICE_USER" = "UNKNOWN" ]; then
    SERVICE_USER=$(whoami)
fi

echo -e "${GREEN}🚀 Deploying SABot from ${BOT_DIR}...${NC}"
echo -e "${GREEN}👤 Service user: ${SERVICE_USER}${NC}"

cd "$BOT_DIR"

# Backup database before any changes
if [ -f "user_roles.db" ]; then
    cp user_roles.db "user_roles.db.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${GREEN}💾 Database backed up${NC}"
fi
if [ -f "data/user_roles.db" ]; then
    cp data/user_roles.db "data/user_roles.db.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${GREEN}💾 Database backed up (data/)${NC}"
fi

# Pull latest code
echo -e "${YELLOW}📥 Pulling latest changes...${NC}"
git fetch origin
git reset --hard origin/main

# Update dependencies
echo -e "${YELLOW}📦 Updating dependencies...${NC}"
if [ -f ".venv/bin/pip" ]; then
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
else
    echo -e "${RED}❌ Virtual environment not found at ${BOT_DIR}/.venv${NC}"
    echo "Run setup first: bash deploy/setup-server.sh"
    exit 1
fi

# Generate systemd service dynamically with correct paths
echo -e "${YELLOW}⚙️ Generating systemd service...${NC}"
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=SABot Telegram Bot
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=${BOT_DIR}/.env
Environment=PYTHONPATH=${BOT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStartPre=${BOT_DIR}/.venv/bin/python -m py_compile ${BOT_DIR}/main.py
ExecStart=${BOT_DIR}/.venv/bin/python ${BOT_DIR}/main.py
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3
KillMode=process
TimeoutSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sabot
ProtectSystem=full
ProtectHome=true
ReadWritePaths=${BOT_DIR}
NoNewPrivileges=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
if ! $SUDO systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    $SUDO systemctl enable "$SERVICE_NAME"
fi

# Restart service
echo -e "${YELLOW}🔄 Restarting ${SERVICE_NAME}...${NC}"
$SUDO systemctl restart "$SERVICE_NAME"

# Wait and check status
sleep 3
if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${GREEN}✅ Deployment successful! ${SERVICE_NAME} is running.${NC}"
    $SUDO systemctl status "$SERVICE_NAME" --no-pager
else
    echo -e "${RED}❌ Deployment failed! Service is not running.${NC}"
    echo ""
    echo -e "${YELLOW}📋 Last 50 lines of logs:${NC}"
    $SUDO journalctl -u "$SERVICE_NAME" -n 50 --no-pager
    echo ""
    echo -e "${YELLOW}💡 Tip: check that .env exists and BOT_TOKEN is valid${NC}"
    exit 1
fi
