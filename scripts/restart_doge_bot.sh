#!/bin/bash

# Change to the project directory
cd /Users/yuvalzamir/doge_bot

# Activate virtual environment
source /Users/yuvalzamir/doge_bot/venv/bin/activate

# Stop existing python processes (bot/dashboard)
pkill -f dash_server.py
pkill -f main.py
pkill -f bot.py

sleep 2

# Run the bot in background (check for main.py first, then bot.py)
if [ -f main.py ]; then
  nohup python3 main.py > bot.log 2>&1 &
  echo "main.py running (log: bot.log)"
elif [ -f bot.py ]; then
  nohup python3 bot.py > bot.log 2>&1 &
  echo "bot.py running (log: bot.log)"
else
  echo "No bot file found (main.py or bot.py)"
fi

sleep 1

# Run the dashboard (logs redirected)
if [ -f dash_server.py ]; then
  nohup python3 dash_server.py --no-browser --host 127.0.0.1 --port 5001 > dash.log 2>&1 &
  DASH_PID=$!
  echo "dash_server.py running (log: dash.log, pid $DASH_PID)"
else
  echo "dash_server.py not found!"
  exit 1
fi

sleep 2

# Open browser to dashboard (Chrome) - Fixed port to 5001
# Note: macOS -> open -a "Google Chrome" ...; Linux -> google-chrome / chromium-browser
if command -v google-chrome > /dev/null; then
  google-chrome http://127.0.0.1:5001 &
elif command -v chromium-browser > /dev/null; then
  chromium-browser http://127.0.0.1:5001 &
elif command -v open > /dev/null; then
  open -a "Google Chrome" http://127.0.0.1:5001 &
else
  echo "No browser auto-detected, open manually: http://127.0.0.1:5001"
fi

# Show live tail of the dashboard log
echo "Showing live dash_server.py log (Ctrl+C to stop):"
tail -f dash.log
