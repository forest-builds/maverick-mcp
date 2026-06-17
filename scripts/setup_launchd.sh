#!/bin/bash
# Install Maverick daily intelligence job into macOS launchd.
# Runs at 8:00am (pre-market), 12:00pm (midday), and 4:30pm (post-close) on weekdays.
# Computer must be on — it will NOT run if the machine is asleep.
#
# Schwab credentials are read from environment or ~/.env at install time and
# baked into the plist so the headless job can reach the Schwab API.
#
# Usage: bash scripts/setup_launchd.sh

set -euo pipefail

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.maverick.intelligence"
PLIST_PATH="$PLIST_DIR/$PLIST_NAME.plist"
UV_BIN="$(which uv || echo /Users/hunter/.local/bin/uv)"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$PROJECT/scripts/daily_intelligence_run.py"
LOG="$PROJECT/logs/daily_intelligence.log"
ERR_LOG="$PROJECT/logs/daily_intelligence_error.log"

mkdir -p "$PROJECT/logs"

# Load Schwab credentials — check project .env then ~/.env
if [ -f "$PROJECT/.env" ]; then
  # shellcheck disable=SC1090
  set -a; source "$PROJECT/.env"; set +a
fi
if [ -f "$HOME/.env" ]; then
  # shellcheck disable=SC1090
  set -a; source "$HOME/.env"; set +a
fi
SCHWAB_CLIENT_ID="${SCHWAB_CLIENT_ID:-}"
SCHWAB_CLIENT_SECRET="${SCHWAB_CLIENT_SECRET:-}"
SCHWAB_REDIRECT_URI="${SCHWAB_REDIRECT_URI:-https://127.0.0.1:8765/callback}"
SCHWAB_TOKEN_FILE="${SCHWAB_TOKEN_FILE:-$HOME/.local/schwab-token.json}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

if [ -z "$SCHWAB_CLIENT_ID" ] || [ -z "$SCHWAB_CLIENT_SECRET" ]; then
  echo "WARNING: SCHWAB_CLIENT_ID or SCHWAB_CLIENT_SECRET not set."
  echo "  The job will still run VC scoring but Schwab sync will be skipped."
  echo "  Set these in ~/.env and re-run this script to enable Schwab sync."
fi

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_NAME</string>

  <key>ProgramArguments</key>
  <array>
    <string>$UV_BIN</string>
    <string>run</string>
    <string>--project</string>
    <string>$PROJECT</string>
    <string>python</string>
    <string>$SCRIPT</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT</string>

  <!-- Run 5x daily on weekdays: pre-market, open settle, midday, late session, post-close -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
  </array>

  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$ERR_LOG</string>

  <key>RunAtLoad</key>
  <false/>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$UV_BIN")</string>
    <key>SCHWAB_CLIENT_ID</key>
    <string>$SCHWAB_CLIENT_ID</string>
    <key>SCHWAB_CLIENT_SECRET</key>
    <string>$SCHWAB_CLIENT_SECRET</string>
    <key>SCHWAB_REDIRECT_URI</key>
    <string>$SCHWAB_REDIRECT_URI</string>
    <key>SCHWAB_TOKEN_FILE</key>
    <string>$SCHWAB_TOKEN_FILE</string>
    <key>TELEGRAM_BOT_TOKEN</key>
    <string>$TELEGRAM_BOT_TOKEN</string>
    <key>TELEGRAM_CHAT_ID</key>
    <string>$TELEGRAM_CHAT_ID</string>
  </dict>
</dict>
</plist>
EOF

# Unload first if already loaded
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "✓ Installed: $PLIST_NAME"
echo "  Runs: weekdays 8:00am + 9:45am + 12:30pm + 2:30pm + 4:15pm"
echo "  Log:  $LOG"
echo "  Schwab client ID: ${SCHWAB_CLIENT_ID:-(not set)}"
echo "  Schwab token file: $SCHWAB_TOKEN_FILE"
echo ""
echo "To run now:      launchctl start $PLIST_NAME"
echo "To uninstall:    launchctl unload $PLIST_PATH && rm $PLIST_PATH"
echo "To check status: launchctl list | grep maverick"
