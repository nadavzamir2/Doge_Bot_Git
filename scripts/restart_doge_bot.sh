#!/bin/bash

source /Users/yuvalzamir/doge_bot/venv/bin/activate

# Stop existing python processes (bot/dashboard)
pkill -f dash_server.py
pkill -f bot.py

sleep 2

# Run the bot in background (if bot.py exists)
if [ -f bot.py ]; then
  nohup python3 bot.py > bot.log 2>&1 &
  echo "bot.py running (log: bot.log)"
fi

sleep 1

# Run the dashboard (logs redirected)
if [ -f dash_server.py ]; then
  nohup python3 dash_server.py > dash.log 2>&1 &
  DASH_PID=$!
  echo "dash_server.py running (log: dash.log, pid $DASH_PID)"
else
  echo "dash_server.py not found!"
  exit 1
fi

sleep 2

# Open browser to dashboard (Chrome)
# Note: macOS -> open -a "Google Chrome" ...; Linux -> google-chrome / chromium-browser
if command -v google-chrome > /dev/null; then
  google-chrome http://127.0.0.1:8899 &
elif command -v chromium-browser > /dev/null; then
  chromium-browser http://127.0.0.1:8899 &
elif command -v open > /dev/null; then
  open -a "Google Chrome" http://127.0.0.1:8899 &
else
  echo "No browser auto-detected, open manually: http://127.0.0.1:8899"
fi

# Show live tail of the dashboard log
echo "Showing live dash_server.py log (Ctrl+C to stop):"
tail -f dash.log

# bash scripts/restart_doge_bot.sh
