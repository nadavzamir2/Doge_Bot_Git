#!/usr/bin/env bash
set -euo pipefail

cd /Users/yuvalzamir/doge_bot

mkdir -p logs
LOGFILE="logs/run_$(date +%F).log"

PY="/Users/yuvalzamir/doge_bot/venv/bin/python3"

# נטרול משתני סביבה ישנים שהפנו ל-venv הישן
unset SSL_CERT_FILE || true
unset REQUESTS_CA_BUNDLE || true

# שימוש ב-certifi של ה-venv החדש
CACERT="$("$PY" -c 'import certifi; print(certifi.where())')"
export SSL_CERT_FILE="$CACERT"
export REQUESTS_CA_BUNDLE="$CACERT"

{
  echo "---- $(date) starting bot ----"
  echo "[DEBUG] Using Python: $PY"
  echo "[DEBUG] CACERT: $CACERT"
} >> "$LOGFILE"

exec "$PY" main.py >> "$LOGFILE" 2>&1
