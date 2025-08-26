#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, json, math, signal, logging
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

# צד שלישי
import ccxt
from dotenv import load_dotenv

# מודולים אופציונליים שכבר קיימים אצלך בפרויקט
try:
    import profit_split   # מודול קיים אצלך: פיצול רווח -> BNB + reinvest
except Exception:
    profit_split = None

# ---------- קונפיג לוגים ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("doge_grid_bot")

# ---------- ENV / פרמטרים ----------
ENV_PATH = os.path.expanduser("~/doge_bot/.env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
else:
    load_dotenv()  # fallback

MODE = os.getenv("MODE", "LIVE").upper()   # LIVE / PAPER
REGION = os.getenv("BINANCE_REGION", "com").lower()  # com / us
RECVWINDOW = int(os.getenv("BINANCE_RECVWINDOW", "10000"))

# מפתחות – תומך בפיצול מפתחות (TRADE/READ) או בזוג הישן
API_KEY  = os.getenv("BINANCE_TRADE_KEY")   or os.getenv("BINANCE_API_KEY")
API_SEC  = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")

PAIR = os.getenv("PAIR", "DOGE/USDT")

# פרמטרי הגריד ברירת־מחדל (כפי שהוגדרו מולך)
GRID_LOW  = Decimal(os.getenv("GRID_LOW",  "0.13"))
GRID_HIGH = Decimal(os.getenv("GRID_HIGH", "0.32"))
STEP_PCT  = Decimal(os.getenv("STEP_PCT",  "1.0"))  # אחוז בין שכבות

# גודל הזמנה בסיסי בדולר + תקרת מחזור לחפיסה
BASE_ORDER_USD = Decimal(os.getenv("BASE_ORDER_USD", "5.0"))
MAX_CYCLE_USD  = Decimal(os.getenv("MAX_CYCLE_USD", "40.0"))

# באפר עמלות כדי לא להיתקע על “MIN_NOTIONAL”
FEE_BUFFER = Decimal(os.getenv("FEE_BUFFER", "0.001"))  # 0.1% כברירת מחדל

STATE_PATH = os.path.expanduser("~/doge_bot/state.json")

# ---------- Utilities ----------

def d(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("state.json read failed: %s", e)
    return {
        "processed_buys": [],          # רשימת buy orderIds שכבר פתחנו להם SELL
        "child_sells": {},             # buyOrderId -> sellOrderId
        "buy_fills": {},               # buyOrderId -> {"price":..., "amount":...}
        "sell_fills": {},              # sellOrderId -> {"price":..., "amount":...}
        "realized_profit_usd": 0.0,    # מצטבר
    }

def save_state(st: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)

def mk_exchange() -> ccxt.Exchange:
    Cls = ccxt.binanceus if REGION == "us" else ccxt.binance
    client = Cls({
        "apiKey": API_KEY,
        "secret": API_SEC,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            # חשוב: אל תבקש fetchCurrencies כדי לא ליפול על הרשאות
            "fetchCurrencies": False,
        }
    })
    return client

def load_precisions(exchange: ccxt.Exchange, symbol: str) -> Dict[str, Any]:
    markets = exchange.load_markets()
    m = markets[symbol]
    # precision & limits
    price_tick  = Decimal(str(m["precision"]["price"])) if "precision" in m and "price" in m["precision"] else None
    amount_step = Decimal(str(m["precision"]["amount"])) if "precision" in m and "amount" in m["precision"] else None

    # ננסה לחלץ tick/step אמיתיים מהפילטרים (עדיף)
    filters = m.get("info", {}).get("filters", [])
    _price_tick = None
    _amount_step = None
    min_notional = None
    for f in filters:
        if f.get("filterType") == "PRICE_FILTER":
            tick = f.get("tickSize")
            if tick:
                _price_tick = Decimal(tick)
        if f.get("filterType") == "LOT_SIZE":
            step = f.get("stepSize")
            if step:
                _amount_step = Decimal(step)
        if f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = Decimal(str(f.get("minNotional", "1")))

    if _price_tick:  # עדיף
        price_tick = _price_tick
    if _amount_step:
        amount_step = _amount_step
    if not min_notional:
        min_notional = Decimal("1.0")

    # price_precision/amount_precision לוגיים לתצוגה בלבד
    price_precision = price_tick
    amount_precision = amount_step

    info = {
        "price_tick": price_tick or Decimal("0.00001"),
        "amount_step": amount_step or Decimal("1"),
        "min_cost": min_notional,
        "price_precision": price_precision or Decimal("0.00001"),
        "amount_precision": amount_precision or Decimal("1"),
    }
    return info

def round_price(price: Decimal, tick: Decimal) -> Decimal:
    # מעגל מטה למכפלה הקרובה של tick
    if tick <= 0:
        return price
    q = (price / tick).to_integral_value(rounding=ROUND_FLOOR)
    return (q * tick).quantize(tick, rounding=ROUND_HALF_UP)

def round_amount(amount: Decimal, step: Decimal) -> Decimal:
    # מעגל מטה לגודל לוט (step)
    if step <= 0:
        return amount
    q = (amount / step).to_integral_value(rounding=ROUND_FLOOR)
    return (q * step).quantize(step, rounding=ROUND_HALF_UP)

def cid(prefix: str) -> str:
    # ClientOrderId קצר
    return f"{prefix}-{int(time.time()*1000)%10_000_000}"

# ---------- הרצת הזמנות ----------

def place_limit_buy(ex: ccxt.Exchange, symbol: str, qty: Decimal, price: Decimal, client_id: Optional[str]=None) -> str:
    params = {"recvWindow": RECVWINDOW}
    if client_id:
        params["newClientOrderId"] = client_id
    if MODE == "LIVE":
        o = ex.create_order(symbol, "limit", "buy", float(qty), float(price), params)
        oid = str(o["id"])
        log.info("(LIVE) Opened BUY %s @ %s | id=%s", qty, price, oid)
        return oid
    else:
        log.info("(PAPER) BUY %s @ %s | id=%s", qty, price, "PAPER-"+client_id if client_id else "PAPER")
        return "PAPER-"+(client_id or "B")

def place_limit_sell(ex: ccxt.Exchange, symbol: str, qty: Decimal, price: Decimal, client_id: Optional[str]=None) -> str:
    params = {"recvWindow": RECVWINDOW}
    if client_id:
        params["newClientOrderId"] = client_id
    if MODE == "LIVE":
        o = ex.create_order(symbol, "limit", "sell", float(qty), float(price), params)
        oid = str(o["id"])
        log.info("(LIVE) Opened SELL %s @ %s | id=%s", qty, price, oid)
        return oid
    else:
        log.info("(PAPER) SELL %s @ %s | id=%s", qty, price, "PAPER-"+client_id if client_id else "PAPER")
        return "PAPER-"+(client_id or "S")

# ---------- לוגיקת גריד בסיסית + פאץ' SELL-אחרי-BUY ----------

def compute_levels(lo: Decimal, hi: Decimal, step_pct: Decimal):
    """יוצר רשימת רמות בין low..high במרווח גיאומטרי של step%"""
    if lo <= 0 or hi <= 0 or hi <= lo:
        return []
    r = (Decimal("1.0") + (step_pct / Decimal("100")))
    levels = []
    p = lo
    while p <= hi + Decimal("1e-18"):
        levels.append(p)
        p = p * r
    # לוודא שהגבול העליון בפנים
    if levels[-1] < hi:
        levels.append(hi)
    return levels

def bootstrap_buys(ex: ccxt.Exchange, info: Dict[str,Any], symbol: str, base_order_usd: Decimal, max_cycle_usd: Decimal):
    """מניח מספר BUY (אם צריך) מתחת למחיר תוך שמירה על תקציב max_cycle_usd"""
    try:
        ticker = ex.fetch_ticker(symbol)
        last = d(ticker["last"])
    except Exception as e:
        log.error("Ticker fetch failed: %s", e)
        return 0

    levels = compute_levels(GRID_LOW, GRID_HIGH, STEP_PCT)
    # תיעדוף רמות שהן <= המחיר הנוכחי (קניות תחת המחיר)
    levels = [L for L in levels if L <= last]
    levels = list(reversed(levels))  # קרובות קודם

    budget = max_cycle_usd
    placed = 0
    bal = ex.fetch_balance(params={"recvWindow": RECVWINDOW}) if MODE=="LIVE" else {"free": {"USDT": float(max_cycle_usd)}}
    usdt_free = d(bal["free"].get("USDT", 0.0))

    # אם אין מספיק, נוותר
    est_need = min(len(levels), 7) * base_order_usd  # מגבלה רכה
    if usdt_free < min(est_need, budget):
        log.warning("Not enough free USDT: %s. Need >= %s. Skipping placements.", usdt_free, min(est_need, budget))

    for L in levels[:7]:  # לא להציף הזמנות
        if budget < base_order_usd:
            break
        if usdt_free < base_order_usd:
            break

        qty = round_amount(base_order_usd / L, info["amount_step"])
        price = round_price(L, info["price_tick"])

        # מינימום שווי
        if qty * price < info["min_cost"]:
            # נסה לעלות כמות מעט
            need_qty = (info["min_cost"] / price) * (Decimal("1.0") + FEE_BUFFER)
            qty = round_amount(need_qty, info["amount_step"])

        if qty <= 0:
            continue

        client_id = cid("B")
        try:
            place_limit_buy(ex, symbol, qty, price, client_id=client_id)
            placed += 1
            budget -= base_order_usd
            usdt_free -= (qty * price)
        except Exception as e:
            log.error("place BUY failed: %s", e)

    log.info("Bootstrapped %d open buys.", placed)
    return placed

def handle_fills_and_post_sells(ex: ccxt.Exchange, info: Dict[str,Any], symbol: str, state: Dict[str,Any]):
    """
    הפאץ' המינימלי:
    - סורק הזמנות אחרונות.
    - עבור כל BUY שנסגר ולא טופל: פותח SELL תואם במחיר יעד buy*(1+step%).
    - עבור כל SELL שנסגר: מחשב רווח ממומש ומעדכן state + profit_split אם יש.
    """
    # הזמנות אחרונות (כולל סגורות/מבוטלות)
    try:
        orders = ex.fetch_orders(symbol, limit=50)
    except Exception as e:
        log.error("fetch_orders failed: %s", e)
        return

    # אינדקס מהיר לפי id
    by_id = {str(o["id"]): o for o in orders}

    # 1) מצא BUY שנסגרו ועדיין אין להם SELL
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if o["side"] != "buy" or o["status"] != "closed":
            continue

        buy_id = str(o["id"])
        if buy_id in state["processed_buys"]:
            continue  # כבר טיפלנו

        filled = d(o.get("filled") or o.get("amount") or 0)
        avg    = d(o.get("average") or o.get("price") or 0)
        if filled <= 0 or avg <= 0:
            continue

        # יעד SELL: 1 + STEP_PCT%
        target = round_price(avg * (Decimal("1.0") + (STEP_PCT/Decimal("100"))), info["price_tick"])
        qty_s  = round_amount(filled * (Decimal("1.0") - FEE_BUFFER), info["amount_step"])

        if qty_s * target < info["min_cost"]:
            # הגבר מעט כדי לא לעבור על MIN_NOTIONAL
            need_qty = (info["min_cost"] / target) * (Decimal("1.0") + FEE_BUFFER)
            qty_s = round_amount(need_qty, info["amount_step"])

        if qty_s <= 0:
            continue

        sell_cid = cid(f"S{buy_id[-4:]}")  # client id עם זיהוי מהיר

        try:
            sell_id = place_limit_sell(ex, symbol, qty_s, target, client_id=sell_cid)
            # עדכן state
            state["processed_buys"].append(buy_id)
            state["child_sells"][buy_id] = sell_id
            state["buy_fills"][buy_id] = {"price": float(avg), "amount": float(filled)}
            save_state(state)
        except Exception as e:
            log.error("open SELL for BUY %s failed: %s", buy_id, e)

    # 2) בדוק SELL שסגור — חשב רווח
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if o["side"] != "sell" or o["status"] != "closed":
            continue

        sell_id = str(o["id"])
        if sell_id in state["sell_fills"]:
            continue  # כבר נרשם

        s_filled = d(o.get("filled") or o.get("amount") or 0)
        s_avg    = d(o.get("average") or o.get("price") or 0)
        if s_filled <= 0 or s_avg <= 0:
            continue

        # מצא buy ההורה (לפי state.child_sells)
        parent_buy = None
        for b, s in state["child_sells"].items():
            if s == sell_id:
                parent_buy = b
                break

        profit_usd = Decimal("0")
        if parent_buy and parent_buy in state["buy_fills"]:
            bdat = state["buy_fills"][parent_buy]
            b_avg = d(bdat["price"])
            # התאמת כמות: נחשב על המינימום המשותף (זהירות מביטולים חלקיים)
            qty_base = min(s_filled, d(bdat["amount"]))
            profit_usd = (s_avg - b_avg) * qty_base

        state["sell_fills"][sell_id] = {"price": float(s_avg), "amount": float(s_filled)}
        if profit_usd > 0:
            prev = Decimal(str(state.get("realized_profit_usd", 0.0)))
            newv = prev + profit_usd
            state["realized_profit_usd"] = float(newv)
            log.info("Realized profit: +%.4f USDT (total=%.4f)", float(profit_usd), float(newv))

            # קריאה למודול הפיצול (אם קיים)
            try:
                if profit_split and hasattr(profit_split, "on_realized_profit"):
                    profit_split.on_realized_profit(ex, float(profit_usd))
            except Exception as e:
                log.warning("profit_split.on_realized_profit failed: %s", e)

        save_state(state)

# ---------- MAIN ----------

def run():
    log.info("Mode: %s", MODE)
    log.info("ENV: %s", ENV_PATH if os.path.exists(ENV_PATH) else "(default)")
    log.info("Region: %s  (class=%s)", REGION, "binanceus" if REGION=="us" else "binance")
    log.info("Trade key prefix: %s…  secret prefix: %s…",
             (API_KEY or "")[:6], (API_SEC or "")[:6])
    log.info("Pair=%s | Grid=%.6f..%.6f (step=%.3f%%) | base_order_usd=%.2f | max_cycle=%.2f",
             PAIR, float(GRID_LOW), float(GRID_HIGH), float(STEP_PCT),
             float(BASE_ORDER_USD), float(MAX_CYCLE_USD))

    ex = mk_exchange()

    # ניסיון לטעון שווקים — לטפל באימות שגוי מראש
    try:
        info = load_precisions(ex, PAIR)
    except ccxt.AuthenticationError as e:
        log.error("Auth error in load_markets(): %s", e)
        return
    except Exception as e:
        log.error("load_precisions failed: %s", e)
        return

    log.info("Exchange info: %s", {
        "amount_precision": float(info["amount_precision"]),
        "price_precision":  float(info["price_precision"]),
        "amount_step":      float(info["amount_step"]),
        "price_tick":       float(info["price_tick"]),
        "min_cost":         float(info["min_cost"]),
    })

    state = load_state()

    # Bootstrap רכישות מתחת למחיר כדי לתת מהן SELL אחר כך
    log.info("Starting base_order_usd = %.1f", float(BASE_ORDER_USD))
    bootstrap_buys(ex, info, PAIR, BASE_ORDER_USD, MAX_CYCLE_USD)

    # לולאת הרצה
    stop = False
    def _sig(_a, _b):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    poll_sec = int(os.getenv("POLL_SECONDS", "7"))
    while not stop:
        # הפאץ' המינימלי: טפל במילויי BUY -> פתח SELL; ומכור -> חשב רווח
        handle_fills_and_post_sells(ex, info, PAIR, state)

        # אפשר להוסיף כאן לוגיקות נוספות (recenter, חידוש BUY אם מחסור, וכו’) – לא נגענו
        time.sleep(poll_sec)

    log.info("Exiting main loop.")

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception("Fatal error: %s", e)
