#!/usr/bin/env bash
set -euo pipefail

# --- CONFIG ---
APP_DIR="${HOME}/doge_bot"         # שנה אם צריך
PYMAIN="main.py"                   # קובץ הבוט
PYDASH="dash_server.py"      # קובץ הדשבורד (אם יש)
DEFAULT_PORT="${PORT:-8000}"       # פורט דשבורד ברירת מחדל
OPEN_DASHBOARD="${OPEN_DASHBOARD:-1}"  # 1=פתח דפדפן אוטומטית
LOG_DIR="${APP_DIR}/logs"
ENV_FILE="${APP_DIR}/.env"

# --- helpers ---
open_url () {
  local url="$1"
  if [[ "${OPEN_DASHBOARD}" == "1" ]]; then
    if command -v open >/dev/null 2>&1; then open "${url}"; \
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "${url}"; \
    else echo "[i] Browse: ${url}"; fi
  fi
}

find_free_port () {
  local start="${1:-8000}"
  local p="$start"
  for i in {0..20}; do
    if ! lsof -i TCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "$p"; return 0
    fi
    p=$((p+1))
  done
  echo "0"
}

ensure_env () {
  [[ -f "${ENV_FILE}" ]] || { echo "[CONFIG] Missing ${ENV_FILE}"; exit 1; }
  set -a; source "${ENV_FILE}"; set +a

  local missing=()
  for k in BINANCE_API_KEY BINANCE_API_SECRET BINANCE_REGION PAIR; do
    [[ -n "${!k-}" ]] || missing+=("$k")
  done
  if (( ${#missing[@]} > 0 )); then
    echo "[CONFIG] Missing env vars: ${missing[*]}"; exit 1
  fi
  if [[ "${BINANCE_REGION}" != "com" && "${BINANCE_REGION}" != "us" ]]; then
    echo "[CONFIG] BINANCE_REGION must be 'com' or 'us' (got: ${BINANCE_REGION})"; exit 1
  fi
}

ensure_venv () {
  cd "${APP_DIR}"
  if [[ ! -d "venv" ]]; then
    echo "[venv] creating…"
    python3 -m venv venv
  fi
  source venv/bin/activate
  if [[ -f "requirements.txt" ]]; then
    pip -q install --upgrade pip >/dev/null
    pip -q install -r requirements.txt >/dev/null
  fi
}

ensure_logs () {
  mkdir -p "${LOG_DIR}"
}

run_bot () {
  ensure_env
  ensure_venv
  ensure_logs
  echo "[RUN] Bot starting: ${PYMAIN}"
  python3 "${PYMAIN}" 2>&1 | tee -a "${LOG_DIR}/bot_$(date +%Y%m%d).log"
}

run_dashboard () {
  ensure_env
  ensure_venv
  ensure_logs

  local port
  port="$(find_free_port "${DEFAULT_PORT}")"
  if [[ "${port}" == "0" ]]; then
    echo "[SERVER] No free port found near ${DEFAULT_PORT}"; exit 1
  fi

  export PORT="${port}"
  export OPEN_DASHBOARD="${OPEN_DASHBOARD}"

  local url="http://127.0.0.1:${port}"
  echo "[RUN] Dashboard on ${url} (PORT=${PORT})"
  open_url "${url}"

  # waitress אם אתה משתמש, אחרת flask רגיל
  if python3 -c "import waitress" >/dev/null 2>&1; then
    python3 - <<PY
from waitress import serve
from importlib import import_module
app = import_module("${PYDASH%.*}").app  # מניח שיש app
serve(app, host="0.0.0.0", port=int("${port}"))
PY
  else
    # נפוץ אצלך: קובץ הדשבורד מריץ את השרת בעצמו
    python3 "${PYDASH}"
  fi
}

usage () {
  cat <<USAGE
Usage: $(basename "$0") [bot|dashboard|both]
  bot        - מריץ את הבוט (main.py)
  dashboard  - מריץ רק את הדשבורד ופותח דפדפן (אם OPEN_DASHBOARD=1)
  both       - מריץ בוט + דשבורד במקביל (טב אחד למסחר, טב אחד לדשבורד)
Vars:
  APP_DIR=${APP_DIR}
  PORT=${DEFAULT_PORT} (יחפש פורט פנוי אם תפוס)
  OPEN_DASHBOARD=${OPEN_DASHBOARD}
USAGE
}

run_both () {
  ensure_env
  ensure_venv
  ensure_logs

  # Dashboard
  local port
  port="$(find_free_port "${DEFAULT_PORT}")"
  export PORT="${port}"
  export OPEN_DASHBOARD="${OPEN_DASHBOARD}"
  local url="http://127.0.0.1:${port}"
  echo "[RUN] Dashboard → ${url}"
  ( python3 "${PYDASH}" 2>&1 | tee -a "${LOG_DIR}/dashboard_$(date +%Y%m%d).log" ) &

  sleep 2
  open_url "${url}"

  # Bot
  echo "[RUN] Bot → ${PYMAIN}"
  python3 "${PYMAIN}" 2>&1 | tee -a "${LOG_DIR}/bot_$(date +%Y%m%d).log"
}

# --- entry ---
cd "${APP_DIR}" || { echo "[ERR] APP_DIR not found: ${APP_DIR}"; exit 1; }

case "${1-}" in
  bot)        run_bot ;;
  dashboard)  run_dashboard ;;
  both)       run_both ;;
  *)          usage ;;
esac
