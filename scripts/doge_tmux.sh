#!/usr/bin/env bash
set -euo pipefail

SESSION="doge"
PROJDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="$PROJDIR/logs"
LOGFILE="$LOGDIR/run_$(date +%F).log"

need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ Missing '$1'"; return 1; }; }

ensure_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "❌ tmux לא מותקן."
    echo "התקנה במק עם Homebrew:  brew install tmux"
    exit 1
  fi
}

ensure_env() {
  [[ -f "$PROJDIR/venv/bin/activate" ]] || { echo "❌ לא נמצא venv. צור אחד:  python3 -m venv venv"; exit 1; }
  [[ -f "$PROJDIR/main.py" ]] || { echo "❌ main.py לא קיים ב-$PROJDIR"; exit 1; }
  mkdir -p "$LOGDIR"
}

start() {
  ensure_tmux
  ensure_env
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "ℹ️  סשן '$SESSION' כבר קיים. השתמש: $0 attach"
    exit 0
  fi

  echo "▶️  מפעיל סשן tmux '$SESSION' ומריץ את הבוט..."
  # מריץ בתיקיית הפרויקט; מפעיל venv; רושם לוגים מתגלגלים לפי תאריך
  tmux new-session -d -s "$SESSION" -c "$PROJDIR" \
    "bash -lc 'source venv/bin/activate && mkdir -p \"$LOGDIR\" && echo \"---- \$(date) starting bot ----\" >> \"$LOGFILE\" && exec python3 main.py >> \"$LOGFILE\" 2>&1'"

  echo "✅ הופעל. ראה לוגים: tail -f \"$LOGFILE\""
  echo "הצטרפות לסשן: $0 attach"
}

stop() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "⏹️  עוצר את הסשן '$SESSION'..."
    tmux kill-session -t "$SESSION"
    echo "✅ נעצר."
  else
    echo "ℹ️  אין סשן '$SESSION'. מנסה לעצור תהליך ריצה (pkill)..."
    pkill -f "python3 main.py" 2>/dev/null && echo "✅ הופסק תהליך python" || echo "ℹ️ לא נמצא תהליך פעיל."
  fi
}

restart() { stop; sleep 1; start; }

attach() {
  ensure_tmux
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux attach -t "$SESSION"
  else
    echo "ℹ️  אין סשן '$SESSION'. הפעלה: $0 start"
  fi
}

logs() {
  mkdir -p "$LOGDIR"
  echo "📜 עוקב אחרי לוג: $LOGFILE"
  tail -n 100 -f "$LOGFILE"
}

status() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "✅ tmux session '$SESSION' RUNNING"
  else
    echo "❌ tmux session '$SESSION' not running"
  fi
  echo "אחרוני הלוגים:"
  [[ -f "$LOGFILE" ]] && tail -n 30 "$LOGFILE" || echo "(אין עדיין קובץ לוג)"
}

usage() {
  cat <<USAGE
שימוש: $0 {start|stop|restart|attach|logs|status}

  start    – מפעיל סשן tmux ומריץ את הבוט עם לוגים לתוך: $LOGFILE
  stop     – עוצר את הסשן (או את התהליך אם אין סשן)
  restart  – מפסיק ומפעיל מחדש
  attach   – נכנס לסשן tmux (לצפייה בזמן אמת)
  logs     – tail על הלוג של היום
  status   – מצב ריצה + 30 שורות אחרונות מלוג

USAGE
}

cmd="${1:-status}"
case "$cmd" in
  start)   start   ;;
  stop)    stop    ;;
  restart) restart ;;
  attach)  attach  ;;
  logs)    logs    ;;
  status)  status  ;;
  *) usage; exit 1 ;;
esac
