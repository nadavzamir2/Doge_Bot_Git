#!/usr/bin/env bash
set -e

cd ~/doge_bot
source venv/bin/activate

# עצירה של ריצות ישנות אם יש
pkill -f profit_watcher.py || true
pkill -f dash_server.py || true

# יצירת תיקיית לוגים אם אין
mkdir -p logs

# הרצת profit_watcher עם backfill
nohup python3 -u profit_watcher.py --backfill --since-days 7 > logs/profit_watcher.out 2>&1 &

# בחירת פורט לדשבורד
PORT=8063

# הרצת dash_server (נפתח דפדפן אוטומטית)
PORT=$PORT OPEN_DASHBOARD=1 python3 dash_server.py
