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
SERVICE_FILE="${BOT_DIR}/deploy/sabot.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🚀 Deploying SABot from ${BOT_DIR}...${NC}"

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

# Update systemd service file if it exists in repo
if [ -f "$SERVICE_FILE" ]; then
    echo -e "${YELLOW}⚙️ Updating systemd service...${NC}"
    cp "$SERVICE_FILE" "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
fi

# Restart service
echo -e "${YELLOW}🔄 Restarting ${SERVICE_NAME}...${NC}"
systemctl restart "$SERVICE_NAME"

# Wait and check status
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${GREEN}✅ Deployment successful!${SERVICE_NAME} is running.${NC}"
    systemctl status "$SERVICE_NAME" --no-pager
else
    echo -e "${RED}❌ Deployment failed! Service is not running.${NC}"
    echo ""
    echo -e "${YELLOW}📋 Last 50 lines of logs:${NC}"
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager
    echo ""
    echo -e "${YELLOW}💡 Tip: check that WorkingDirectory in the service file matches ${BOT_DIR}${NC}"
    exit 1
fi
