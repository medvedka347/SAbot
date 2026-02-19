#!/bin/bash
set -e

# =============================================================================
# Setup script for SABot on Ubuntu Server
# Run this on your server as root
# Usage: bash setup-server.sh
# =============================================================================

BOT_DIR="/root/SABot"
REPO_URL="https://github.com/medvedka347/SAbot.git"

echo "🚀 Setting up SABot on server..."

# Update system
echo "📦 Updating system packages..."
apt update && apt upgrade -y

# Install required packages
echo "📦 Installing Python and Git..."
apt install -y python3 python3-pip python3-venv git

# Clone repository
echo "📁 Creating bot directory..."
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

if [ -d ".git" ]; then
    echo "📥 Repository already exists, pulling latest..."
    git pull origin main
else
    echo "📥 Cloning repository..."
    git clone "$REPO_URL" .
fi

# Create virtual environment and install dependencies
echo "🐍 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Created .env from template. Edit it with your settings:"
    echo "    nano $BOT_DIR/.env"
    echo ""
    echo "    Required variables:"
    echo "      BOT_TOKEN=<your bot token from @BotFather>"
    echo "      INITIAL_ADMIN_ID=<your Telegram ID from @userinfobot>"
    echo ""
fi

# Install systemd service
echo "⚙️ Installing systemd service..."
cp deploy/sabot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable sabot

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file: nano $BOT_DIR/.env"
echo "2. Start the bot: sudo systemctl start sabot"
echo "3. Check status:   sudo systemctl status sabot"
echo "4. View logs:      journalctl -u sabot -f"
