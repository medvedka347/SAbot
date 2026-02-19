#!/bin/bash

# =============================================================================
# Setup script for SABot on Ubuntu Server
# Run this on your server as root
# =============================================================================

echo "🚀 Setting up SABot on server..."

# Update system
echo "📦 Updating system packages..."
apt update && apt upgrade -y

# Install required packages
echo "📦 Installing Python and Git..."
apt install -y python3 python3-pip python3-venv git

# Create bot directory
echo "📁 Creating bot directory..."
mkdir -p /root/SABot
cd /root/SABot

# Clone repository (you'll need to do this manually with your credentials)
echo "📥 Please clone your repository manually:"
echo "   git clone https://github.com/medvedka347/SAbot.git ."
echo "   OR if using SSH:"
echo "   git clone git@github.com:medvedka347/SAbot.git ."
echo ""
echo "Then create .env file with your BOT_TOKEN and INITIAL_ADMIN_ID"
echo ""

# Create virtual environment
echo "🐍 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Clone your repository into /root/SABot"
echo "2. Create .env file with your settings"
echo "3. Run: sudo systemctl enable sabot && sudo systemctl start sabot"
