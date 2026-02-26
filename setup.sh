#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "=== Time Tracker Setup ==="
echo ""

# Uninstall mode
if [ "$1" = "uninstall" ]; then
    echo "Stopping and removing LaunchAgent..."
    launchctl unload "$HOME/Library/LaunchAgents/com.local.timetracker.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.local.timetracker.plist"
    echo "Done. The app will no longer autostart."
    exit 0
fi

# --- Python venv ---
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo "Installing dependencies..."
pip install -q -r requirements.txt

# --- Environment variables ---
if [ ! -f ".env" ]; then
    echo ""
    echo "--- API Keys ---"
    echo ""
    read -p "OpenAI API key (for GPT categorization): " OPENAI_KEY
    echo "OPENAI_API_KEY=$OPENAI_KEY" > .env

    echo ""
    echo "--- Turso (optional, for cloud dashboard) ---"
    read -p "Turso DB URL (leave blank to skip): " TURSO_URL
    if [ -n "$TURSO_URL" ]; then
        echo "TURSO_DB_URL=$TURSO_URL" >> .env
        read -p "Turso DB token: " TURSO_TOKEN
        echo "TURSO_DB_TOKEN=$TURSO_TOKEN" >> .env
    fi

    echo ""
    echo "--- Weekly email summary (optional) ---"
    read -p "Email for weekly summary (leave blank to skip): " EMAIL
    if [ -n "$EMAIL" ]; then
        sed -i '' "s/your-email@example.com/$EMAIL/g" n8n/weekly-summary-workflow.json 2>/dev/null || true
    fi
else
    echo ".env already exists, skipping credential setup."
fi

# --- Projects and tags ---
echo ""
echo "--- Projects & Tags ---"
echo "Default projects: Work, Personal, Side project"
echo "Default tags: coding, writing, reading, communications, planning, creative, meeting, admin"
echo ""
echo "You can customize these in config.py or in the web dashboard after setup."
echo ""

# --- LaunchAgent for autostart ---
echo "Setting up autostart..."
PLIST="$PROJECT_DIR/com.local.timetracker.plist"
cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.local.timetracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PROJECT_DIR/venv/bin/python</string>
        <string>$PROJECT_DIR/app/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LowPriorityIO</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/timetracker.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/timetracker.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
PLISTEOF

mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST" "$HOME/Library/LaunchAgents/com.local.timetracker.plist"
launchctl unload "$HOME/Library/LaunchAgents/com.local.timetracker.plist" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/com.local.timetracker.plist"

echo ""
echo "=== Setup complete ==="
echo ""
echo "  The time tracker is running and will autostart on login."
echo "  Dashboard: http://127.0.0.1:5123"
echo "  Stop:      launchctl unload ~/Library/LaunchAgents/com.local.timetracker.plist"
echo "  Uninstall: ./setup.sh uninstall"
echo ""
echo "  To deploy the dashboard to Vercel:"
echo "    1. Install Turso: brew install tursodatabase/tap/turso"
echo "    2. Create DB: turso auth signup && turso db create timetracker"
echo "    3. Add TURSO_DB_URL and TURSO_DB_TOKEN to .env"
echo "    4. Deploy: vercel --prod"
echo ""
