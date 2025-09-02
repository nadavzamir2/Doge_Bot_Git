# notifier.py
import os, time

# Try to use requests; if unavailable, disable Telegram and warn once
_requests_ok = True
try:
    import requests
except Exception:
    _requests_ok = False

_TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or ""
_TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID") or ""
_TG_ENABLED     = bool(_TELEGRAM_TOKEN and _TELEGRAM_CHAT and _requests_ok)

_last_warn_no_tg = 0.0

def _send_telegram(text: str):
    global _last_warn_no_tg
    if not _TG_ENABLED:
        # Warn at most once every 60 seconds to avoid flooding
        now = time.time()
        if now - _last_warn_no_tg > 60:
            if not _requests_ok:
                print("[WARN] requests package not available; Telegram alerts disabled. Run: pip install requests")
            elif not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT:
                print("[WARN] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env; Telegram alerts disabled.")
            _last_warn_no_tg = now
        return
    try:
        url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": _TELEGRAM_CHAT,
            "text": text,
            "disable_web_page_preview": True,
            "parse_mode": "Markdown"
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[WARN] Telegram send failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[WARN] Telegram exception: {e}")

def info(msg: str):
    print("[INFO]", msg)

def warn(msg: str):
    print("[WARN]", msg)

def err(msg: str):
    print("[ERROR]", msg)

def alert(msg: str):
    """Send a Telegram message (if enabled) and print to console."""
    print("[ALERT]", msg)
    _send_telegram(msg)
