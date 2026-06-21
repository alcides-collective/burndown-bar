#!/bin/sh
# Install (or refresh) the Burndown dashboard as a macOS LaunchAgent so it
# starts on login, restarts if it dies, and serves on :3838.
#
#   ./install.sh            install + start
#   ./install.sh uninstall  stop + remove
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.alcides.burndown-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/burndown-dashboard.log"
PORT="${BURNDOWN_DASHBOARD_PORT:-3838}"

if [ "$1" = "uninstall" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL."
  exit 0
fi

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 not found in PATH"; exit 1; }

# Build the SPA if it hasn't been built yet.
if [ ! -f "$DIR/web/dist/index.html" ]; then
  echo "Building the dashboard frontend (one-time)…"
  (cd "$DIR/web" && npm install && npm run build)
fi

mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"
sed -e "s#__PYTHON__#$PY#g" \
    -e "s#__SERVER__#$DIR/server.py#g" \
    -e "s#__WORKDIR__#$DIR#g" \
    -e "s#__LOG__#$LOG#g" \
    "$DIR/com.alcides.burndown-dashboard.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
sleep 1

echo "Burndown dashboard installed and running."
echo "  URL:       http://localhost:$PORT"
echo "  Logs:      $LOG"
echo "  Uninstall: $DIR/install.sh uninstall"
