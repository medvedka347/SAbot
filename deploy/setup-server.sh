#!/bin/bash
set -e

# =============================================================================
# Setup script for SABot on Ubuntu Server
# Run this on your server as root
# Usage: bash setup-server.sh
# =============================================================================

BOT_DIR="/root/SABot"
REPO_URL="https://github.com/medvedka347/SAbot.git"

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

# Check Python version
echo -e "${YELLOW}🐍 Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.9"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then 
    echo -e "${RED}❌ Python $REQUIRED_VERSION or higher is required (found $PYTHON_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python $PYTHON_VERSION is OK${NC}"

# Update system
echo -e "${YELLOW}📦 Updating system packages...${NC}"
apt update && apt upgrade -y

# Install required packages
echo -e "${YELLOW}📦 Installing Python, Git and other dependencies...${NC}"
apt install -y python3 python3-pip python3-venv git

# Backup existing .env if exists
if [ -f "$BOT_DIR/.env" ]; then
    BACKUP_FILE="$BOT_DIR/.env.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${YELLOW}💾 Backing up existing .env to $BACKUP_FILE${NC}"
    cp "$BOT_DIR/.env" "$BACKUP_FILE"
fi

# Create or update repository
echo -e "${YELLOW}📁 Setting up bot directory...${NC}"
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

if [ -d ".git" ]; then
    echo -e "${YELLOW}📥 Repository exists, pulling latest changes...${NC}"
    git pull origin main
else
    echo -e "${YELLOW}📥 Cloning repository...${NC}"
    # Remove any existing files (except backups)
    find . -maxdepth 1 ! -name '*.backup.*' ! -name '.' ! -name '..' -exec rm -rf {} + 2>/dev/null || true
    git clone "$REPO_URL" .
fi

# Restore .env from backup if exists
if [ -f "$BACKUP_FILE" ]; then
    echo -e "${YELLOW}🔄 Restoring .env from backup...${NC}"
    cp "$BACKUP_FILE" .env
fi

# Create virtual environment and install dependencies
echo -e "${YELLOW}🐍 Creating virtual environment...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo ""
        echo -e "${YELLOW}⚠️  Created .env from template. Edit it with your settings:${NC}"
        echo "    nano $BOT_DIR/.env"
        echo ""
        echo "    Required variables:"
        echo "      BOT_TOKEN=<your bot token from @BotFather>"
        echo "      INITIAL_ADMIN_ID=<your Telegram ID from @userinfobot>"
        echo ""
    else
        echo -e "${RED}❌ .env.example not found! Creating empty .env file...${NC}"
        touch .env
    fi
fi

# Install systemd service
echo -e "${YELLOW}⚙️ Installing systemd service...${NC}"
cp deploy/sabot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable sabot

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "1. Edit .env file:     nano $BOT_DIR/.env"
echo "2. Start the bot:      sudo systemctl start sabot"
echo "3. Check status:       sudo systemctl status sabot"
echo "4. View logs:          journalctl -u sabot -f"
echo "5. Enable on boot:     sudo systemctl enable sabot"
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT: Make sure to edit .env before starting the bot!${NC}"
