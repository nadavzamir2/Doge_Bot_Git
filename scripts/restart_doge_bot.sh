#!/bin/bash

# עצור תהליכי python קיימים (של הבוט/דשבורד)
pkill -f dash_server.py
pkill -f bot.py

sleep 2

# הרץ את הבוט ברקע (אם יש bot.py)
if [ -f bot.py ]; then
  nohup python3 bot.py > bot.log 2>&1 &
  echo "bot.py running (log: bot.log)"
fi

sleep 1

# הרץ את הדשבורד (מציג לוגים בטרמינל)
if [ -f dash_server.py ]; then
  nohup python3 dash_server.py > dash.log 2>&1 &
  DASH_PID=$!
  echo "dash_server.py running (log: dash.log, pid $DASH_PID)"
else
  echo "dash_server.py not found!"
  exit 1
fi

sleep 2

# פתח את הדפדפן לכתובת הדשבורד (Chrome)
# שים לב: אם אתה על Mac, הפקודה היא open -a "Google Chrome" ...
# אם אתה על Linux/Ubuntu השתמש google-chrome או chromium-browser
if command -v google-chrome > /dev/null; then
  google-chrome http://127.0.0.1:8899 &
elif command -v chromium-browser > /dev/null; then
  chromium-browser http://127.0.0.1:8899 &
elif command -v open > /dev/null; then
  open -a "Google Chrome" http://127.0.0.1:8899 &
else
  echo "לא נמצא דפדפן אוטומטי, פתח ידנית: http://127.0.0.1:8899"
fi

# הצג tail חי של הלוג של הדשבורד
echo "מציג לייב את הלוג של dash_server.py (Ctrl+C לעצירה):"
tail -f dash.log

# bash scripts/restart_doge_bot.sh
