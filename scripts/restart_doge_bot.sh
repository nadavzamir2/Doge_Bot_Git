#!/bin/bash

# הגדר נתיב לפרויקט
PROJECT_DIR="/Users/yuvalzamir/doge_bot"   # שנה לשביל הפרויקט שלך
PYTHON=python3                             # או python אם רלוונטי

# שמות sessions
BOT_SESSION="doge_bot"
WATCHER_SESSION="profit_watcher"
DASH_SESSION="dashboard"

# עצירת סשנים קיימים
tmux kill-session -t "$BOT_SESSION" 2>/dev/null
tmux kill-session -t "$WATCHER_SESSION" 2>/dev/null
tmux kill-session -t "$DASH_SESSION" 2>/dev/null

# הרצת הבוט
tmux new-session -d -s "$BOT_SESSION" "cd $PROJECT_DIR && $PYTHON main_original.py"

# הרצת profit_watcher
tmux new-session -d -s "$WATCHER_SESSION" "cd $PROJECT_DIR && $PYTHON profit_watcher.py"

# הרצת הדשבורד (אם יש dash_server.py, אחרת dasboardTry.py)
tmux new-session -d -s "$DASH_SESSION" "cd $PROJECT_DIR && $PYTHON dash_server.py"

echo "הבוט, profit_watcher והדשבורד הופעלו מחדש ב-tmux!"
echo "לצפייה בלוגים: tmux attach -t {session_name}"