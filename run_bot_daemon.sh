#!/bin/bash
set -euo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

APP_DIR="/Users/yuvalzamir/doge_bot"
LOG_DIR="$APP_DIR/logs"
PY="$APP_DIR/venv/bin/python3"

mkdir -p "$LOG_DIR"
cd "$APP_DIR"

if [[ ! -x "$PY" ]]; then
  echo "$(date) [FATAL] Python venv missing at $PY" >> "$LOG_DIR/launchd.err.log"
  exit 99
fi

LOGFILE="$LOG_DIR/run_$(date +%F).log"
exec "$PY" main.py >> "$LOGFILE" 2>> "$LOG_DIR/launchd.err.log"
