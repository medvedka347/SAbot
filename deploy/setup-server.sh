#!/bin/bash
set -e

# =============================================================================
# Setup script for SABot on Ubuntu/Debian Server
# Run this on your server as root (one-time setup)
# Usage: bash setup-server.sh
# =============================================================================

BOT_DIR="/opt/sabot"
REPO_URL="https://github.com/medvedka347/SAbot.git"
USER="sabot"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Setting up SABot on server...${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Please run as root${NC}"
    exit 1
fi

# Update system
echo -e "${YELLOW}📦 Updating system packages...${NC}"
apt update && apt upgrade -y

# Install required packages
echo -e "${YELLOW}📦 Installing dependencies...${NC}"
apt install -y python3 python3-venv python3-pip git

# Check Python version (need 3.10+)
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo -e "${RED}❌ Python 3.10+ is required. Found: ${PY_MAJOR}.${PY_MINOR}${NC}"
    exit 1
fi

# Create dedicated user
echo -e "${YELLOW}👤 Creating user '$USER'...${NC}"
if ! id "$USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$BOT_DIR" "$USER"
fi

# Create directory
echo -e "${YELLOW}📁 Setting up bot directory...${NC}"
mkdir -p "$BOT_DIR"
mkdir -p "$BOT_DIR/data"

# Clone repository
echo -e "${YELLOW}📥 Cloning repository...${NC}"
cd "$BOT_DIR"
if [ -d ".git" ]; then
    git pull origin main
else
    git clone "$REPO_URL" .
fi

# Set permissions
echo -e "${YELLOW}🔐 Setting permissions...${NC}"
chown -R "$USER:$USER" "$BOT_DIR"
chmod 750 "$BOT_DIR"
chmod 770 "$BOT_DIR/data"

# Create virtual environment
echo -e "${YELLOW}🐍 Creating virtual environment...${NC}"
sudo -u "$USER" python3 -m venv "$BOT_DIR/.venv"
sudo -u "$USER" "$BOT_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$USER" "$BOT_DIR/.venv/bin/pip" install -r "$BOT_DIR/requirements.txt"

# Create .env from example if doesn't exist
if [ ! -f "$BOT_DIR/.env" ]; then
    if [ -f "$BOT_DIR/.env.example" ]; then
        cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
        chown "$USER:$USER" "$BOT_DIR/.env"
        chmod 600 "$BOT_DIR/.env"
        echo ""
        echo -e "${YELLOW}⚠️  Created .env from template. Edit it:${NC}"
        echo "    nano $BOT_DIR/.env"
        echo ""
        echo "    Required variables:"
        echo "      BOT_TOKEN=<your bot token from @BotFather>"
        echo "      INITIAL_ADMIN_ID=<your Telegram ID from @userinfobot>"
        echo ""
    fi
fi

# Install systemd service
echo -e "${YELLOW}⚙️ Installing systemd service...${NC}"
cp "$BOT_DIR/deploy/sabot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable sabot

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "1. Edit .env file:     nano $BOT_DIR/.env"
echo "2. Start the bot:      systemctl start sabot"
echo "3. Check status:       systemctl status sabot"
echo "4. View logs:          journalctl -u sabot -f"
echo "5. Manual deploy:      bash $BOT_DIR/deploy/deploy.sh"
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT: Edit .env before starting!${NC}"
