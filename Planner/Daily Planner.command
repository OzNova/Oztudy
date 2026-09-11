#!/bin/bash
# Double-click to open the Daily Planner as a desktop app.
cd "$(dirname "$0")"
osascript -e 'tell application "Terminal" to activate' 2>/dev/null
PYTHON="/opt/homebrew/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi
exec "$PYTHON" run_desktop.py