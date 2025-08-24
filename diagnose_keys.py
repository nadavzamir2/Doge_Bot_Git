#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnose Binance API keys (region/IP/permissions) for doge_bot.

בדיקות:
- טעינת .env + הדפסת אזור/צמד/פריפיקס מפתחות (בלי לחשוף מלא)
- חיבור ציבורי: load_markets + fetch_ticker
- חיבור READ: fetch_balance
- חיבור TRADE: fetch_balance
- רמזים לפתרון לפי הודעת השגיאה (כולל -2015 ו-2008)
"""

import os, argparse
from dotenv import load_dotenv
import ccxt

try:
    import requests
except Exception:
    requests = None

def color(s, c):
    codes = {'ok':'32','warn':'33','err':'31','info':'36','bold':'1'}
    code = codes.get(c, '0')
    return f"\033[{code}m{s}\033[0m"

def prefix(s, n=6):
    if not s: return ''
    return s[:n] + '…'

def fetch_public_ip():
    if not requests:
        return None
    try:
        r = requests.get('https://api.ipify.org', timeout=5)
        if r.ok:
            return r.text.strip()
    except Exception:
        pass
    return None

def mk_client(Cls, key=None, secret=None):
    opts = {"defaultType":"spot", "adjustForTimeDifference": True, "fetchCurrencies": False}
    cfg = {"enableRateLimit": True, "options": opts}
    if key and secret:
        cfg.update({"apiKey": key, "secret": secret})
    return Cls(cfg)

def suggest(e, region, which):
    s = str(e)
    if 'Invalid API-key, IP, or permissions' in s or '-2015' in s:
        print(color(f"→ {which}: בדוק BINANCE_REGION={region}, ש־IP של המפתח מאושר, שיש הרשאת Spot Trading, ושהמפתח לא נמחק.", 'info'))
    elif 'Invalid Api-Key ID' in s or '-2008' in s:
        print(color(f"→ {which}: המפתח לא מתאים לאזור או שגוי. ודא binance.us מול binance.com והדבק מחדש ל-.env.", 'info'))
    elif 'recvWindow' in s:
        print(color("→ נסה להגדיל BINANCE_RECVWINDOW (למשל ל-50000).", 'info'))
    else:
        print(color("→ בדוק הרשאות (Read/Spot), אזור (com/us), ושעון מערכת. אם צריך — צור מפתח חדש עם IP מוגבל.", 'info'))

def run(args):
    env_path = os.path.expanduser(args.env)
    print(f"[INFO] Loading .env: {env_path} (exists={os.path.exists(env_path)})")
    load_dotenv(env_path)

    region = (args.region or os.getenv("BINANCE_REGION", "com")).strip().lower()
    Cls = ccxt.binanceus if region == "us" else ccxt.binance
    pair = args.pair or os.getenv("PAIR", "DOGE/USDT")
    recv = int(os.getenv("BINANCE_RECVWINDOW", "10000"))

    read_key    = os.getenv("BINANCE_READ_KEY")    or os.getenv("BINANCE_API_KEY")
    read_secret = os.getenv("BINANCE_READ_SECRET") or os.getenv("BINANCE_API_SECRET")
    trade_key    = os.getenv("BINANCE_TRADE_KEY")    or os.getenv("BINANCE_API_KEY")
    trade_secret = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")

    pub_ip = fetch_public_ip()
    if pub_ip:
        print(f"[INFO] Public IP (whitelist for TRADE): {pub_ip}")

    print(f"[INFO] Region={region} ({Cls.__name__}) | Pair={pair}")
    print(f"[INFO] READ  key present? {bool(read_key)}  (prefix: {prefix(read_key)})")
    print(f"[INFO] TRADE key present? {bool(trade_key)} (prefix: {prefix(trade_key)})")

    statuses = {'public': False, 'read': False, 'trade': False}

    # --- Public (no keys) ---
    pub = mk_client(Cls)
    try:
        pub.load_markets()  # ללא fetchCurrencies כדי לא לדרוש חתימה
        tk = pub.fetch_ticker(pair)
        print(color(f"[OK] Public markets/ticker ✔  last={tk.get('last')}", 'ok'))
        statuses['public'] = True
    except Exception as e:
        print(color(f"[ERR] Public error: {type(e).__name__}: {e}", 'err'))

    # --- READ (private signed) ---
    if read_key and read_secret:
        r = mk_client(Cls, read_key, read_secret)
        try:
            # חסכון בקריאות: משתמשים בשווקים שכבר נטענו
            r.markets, r.symbols = getattr(pub, 'markets', None), getattr(pub, 'symbols', None)
            bal = r.fetch_balance(params={"recvWindow": recv})
            usdt = bal.get('free', {}).get('USDT', 0.0)
            print(color(f"[OK] READ balance ✔  USDT free={usdt}", 'ok'))
            statuses['read'] = True
        except Exception as e:
            print(color(f"[ERR] READ error: {type(e).__name__}: {e}", 'err'))
            suggest(e, region, "READ")
    else:
        print(color("[WARN] חסרים BINANCE_READ_KEY/BINANCE_READ_SECRET ב-.env", 'warn'))

    # --- TRADE (private signed, needs IP restriction + Spot) ---
    if trade_key and trade_secret:
        t = mk_client(Cls, trade_key, trade_secret)
        try:
            t.markets, t.symbols = getattr(pub, 'markets', None), getattr(pub, 'symbols', None)
            bal = t.fetch_balance(params={"recvWindow": recv})
            usdt = bal.get('free', {}).get('USDT', 0.0)
            print(color(f"[OK] TRADE balance ✔  USDT free={usdt}", 'ok'))
            statuses['trade'] = True
        except Exception as e:
            print(color(f"[ERR] TRADE error: {type(e).__name__}: {e}", 'err'))
            suggest(e, region, "TRADE")
    else:
        print(color("[WARN] חסרים BINANCE_TRADE_KEY/BINANCE_TRADE_SECRET ב-.env", 'warn'))

    print("-"*70)
    print("Summary:", statuses)
    if not statuses['public']:
        print(color("× Public נכשל — בדוק רשת/אזור/סימבול.", 'err'))
    if statuses['public'] and not statuses['read']:
        print(color("! READ נכשל — מפתח קריאה/אזור/recvWindow/שעה/הרשאות.", 'warn'))
    if statuses['public'] and not statuses['trade']:
        print(color("! TRADE נכשל — לרוב IP restriction או חוסר הרשאת Spot או אזור לא תואם.", 'warn'))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Diagnose Binance keys (region/IP/permissions)")
    p.add_argument("--env", default="~/doge_bot/.env", help="נתיב לקובץ .env")
    p.add_argument("--pair", default=None, help="ברירת מחדל מ-.env או DOGE/USDT")
    p.add_argument("--region", default=None, choices=["com","us"], help="עקיפת BINANCE_REGION")
    run(p.parse_args())



# cd ~/doge_bot
# source venv/bin/activate
# python3 diagnose_keys.py
