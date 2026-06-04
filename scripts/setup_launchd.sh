#!/bin/bash
# Install Maverick daily intelligence job into macOS launchd.
# Runs at 8:00am (pre-market) and 4:30pm (post-close) on weekdays.
# Computer must be on — it will NOT run if the machine is asleep.
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

  <!-- Run at 8:00am and 4:30pm weekdays -->
  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Weekday</key><integer>1</integer>
      <key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>1</integer>
      <key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>2</integer>
      <key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>2</integer>
      <key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>3</integer>
      <key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>3</integer>
      <key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>4</integer>
      <key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>4</integer>
      <key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>5</integer>
      <key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>
    </dict>
    <dict>
      <key>Weekday</key><integer>5</integer>
      <key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>
    </dict>
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
  </dict>
</dict>
</plist>
EOF

# Unload first if already loaded
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "✓ Installed: $PLIST_NAME"
echo "  Runs: weekdays 8:00am + 4:30pm"
echo "  Log:  $LOG"
echo ""
echo "To run now:     launchctl start $PLIST_NAME"
echo "To uninstall:   launchctl unload $PLIST_PATH && rm $PLIST_PATH"
echo "To check status: launchctl list | grep maverick"
