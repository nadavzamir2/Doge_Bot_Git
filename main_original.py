#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, json, math, signal, logging
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

# Third-party
import ccxt
from dotenv import load_dotenv

# Optional modules that may already exist in your project
try:
    import profit_split   # Module in project: profit split -> BNB + reinvest
except Exception:
    profit_split = None

# ---------- Logging Config ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("doge_grid_bot")

# ---------- ENV / Parameters ----------
ENV_PATH = os.path.expanduser("~/doge_bot/.env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
else:
    load_dotenv()  # fallback

MODE = os.getenv("MODE", "LIVE").upper()   # LIVE / PAPER
REGION = os.getenv("BINANCE_REGION", "com").lower()  # com / us
RECVWINDOW = int(os.getenv("BINANCE_RECVWINDOW", "10000"))

# API keys – supports split keys (TRADE/READ) or legacy pair
API_KEY  = os.getenv("BINANCE_TRADE_KEY")   or os.getenv("BINANCE_API_KEY")
API_SEC  = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")

PAIR = os.getenv("PAIR", "DOGE/USDT")

# Default grid parameters
GRID_LOW  = Decimal(os.getenv("GRID_LOW",  "0.13"))
GRID_HIGH = Decimal(os.getenv("GRID_HIGH", "0.32"))
STEP_PCT  = Decimal(os.getenv("STEP_PCT",  "1.0"))  # percent between layers

# Base order size in USD + cycle cap
BASE_ORDER_USD = Decimal(os.getenv("BASE_ORDER_USD", "5.0"))
MAX_CYCLE_USD  = Decimal(os.getenv("MAX_CYCLE_USD", "40.0"))

# Fee buffer to avoid MIN_NOTIONAL
FEE_BUFFER = Decimal(os.getenv("FEE_BUFFER", "0.001"))  # 0.1% default

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
        "processed_buys": [],          # BUY orderIds already processed (SELL opened)
        "child_sells": {},             # buyOrderId -> sellOrderId
        "buy_fills": {},               # buyOrderId -> {"price":..., "amount":...}
        "sell_fills": {},              # sellOrderId -> {"price":..., "amount":...}
        "realized_profit_usd": 0.0,    # Cumulative realized profit
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
            # Important: do not request fetchCurrencies to avoid permission issues
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

    # Try to extract real tick/step from filters (preferred)
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

    if _price_tick:  # preferred
        price_tick = _price_tick
    if _amount_step:
        amount_step = _amount_step
    if not min_notional:
        min_notional = Decimal("1.0")

    # price_precision/amount_precision logical; display only
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
    # Round down to nearest multiple of tick
    if tick <= 0:
        return price
    q = (price / tick).to_integral_value(rounding=ROUND_FLOOR)
    return (q * tick).quantize(tick, rounding=ROUND_HALF_UP)

def round_amount(amount: Decimal, step: Decimal) -> Decimal:
    # Round down to lot size (step)
    if step <= 0:
        return amount
    q = (amount / step).to_integral_value(rounding=ROUND_FLOOR)
    return (q * step).quantize(step, rounding=ROUND_HALF_UP)

def cid(prefix: str) -> str:
    # Short ClientOrderId
    return f"{prefix}-{int(time.time()*1000)%10_000_000}"

# ---------- Order Placement ----------

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

# ---------- Basic Grid Logic + SELL-after-BUY Patch ----------

def compute_levels(lo: Decimal, hi: Decimal, step_pct: Decimal):
    """Create list of levels between low..high with geometric spacing step%"""
    if lo <= 0 or hi <= 0 or hi <= lo:
        return []
    r = (Decimal("1.0") + (step_pct / Decimal("100")))
    levels = []
    p = lo
    while p <= hi + Decimal("1e-18"):
        levels.append(p)
        p = p * r
    # Ensure upper bound included
    if levels[-1] < hi:
        levels.append(hi)
    return levels

def bootstrap_buys(ex: ccxt.Exchange, info: Dict[str,Any], symbol: str, base_order_usd: Decimal, max_cycle_usd: Decimal):
    """Place required BUY orders below price while respecting max_cycle_usd budget"""
    try:
        ticker = ex.fetch_ticker(symbol)
        last = d(ticker["last"])
    except Exception as e:
        log.error("Ticker fetch failed: %s", e)
        return 0

    levels = compute_levels(GRID_LOW, GRID_HIGH, STEP_PCT)
    # Keep levels <= current price (buys below current)
    levels = [L for L in levels if L <= last]
    levels = list(reversed(levels))  # nearest first

    budget = max_cycle_usd
    placed = 0
    bal = ex.fetch_balance(params={"recvWindow": RECVWINDOW}) if MODE=="LIVE" else {"free": {"USDT": float(max_cycle_usd)}}
    usdt_free = d(bal["free"].get("USDT", 0.0))

    # If insufficient balance, skip
    est_need = min(len(levels), 7) * base_order_usd  # soft cap
    if usdt_free < min(est_need, budget):
        log.warning("Not enough free USDT: %s. Need >= %s. Skipping placements.", usdt_free, min(est_need, budget))

    for L in levels[:7]:  # avoid flooding orders
        if budget < base_order_usd:
            break
        if usdt_free < base_order_usd:
            break

        qty = round_amount(base_order_usd / L, info["amount_step"])
        price = round_price(L, info["price_tick"])

        # Minimum notional check
        if qty * price < info["min_cost"]:
            # Try to raise quantity slightly
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
    Minimal patch:
    - Scan recent orders.
    - For each filled BUY not yet processed: open corresponding SELL at buy*(1+step%).
    - For each filled SELL: compute realized profit and update state + profit_split if applicable.
    """
    # Recent orders (including closed/canceled)
    try:
        orders = ex.fetch_orders(symbol, limit=50)
    except Exception as e:
        log.error("fetch_orders failed: %s", e)
        return

    # Quick index by id
    by_id = {str(o["id"]): o for o in orders}

    # 1) Find BUY orders that closed and still have no SELL
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if o["side"] != "buy" or o["status"] != "closed":
            continue

        buy_id = str(o["id"])
        if buy_id in state["processed_buys"]:
            continue  # already processed

        filled = d(o.get("filled") or o.get("amount") or 0)
        avg    = d(o.get("average") or o.get("price") or 0)
        if filled <= 0 or avg <= 0:
            continue

        # SELL target: 1 + STEP_PCT%
        target = round_price(avg * (Decimal("1.0") + (STEP_PCT/Decimal("100"))), info["price_tick"])
        qty_s  = round_amount(filled * (Decimal("1.0") - FEE_BUFFER), info["amount_step"])

        if qty_s * target < info["min_cost"]:
            # Increase quantity slightly to satisfy MIN_NOTIONAL
            need_qty = (info["min_cost"] / target) * (Decimal("1.0") + FEE_BUFFER)
            qty_s = round_amount(need_qty, info["amount_step"])

        if qty_s <= 0:
            continue

        sell_cid = cid(f"S{buy_id[-4:]}")  # client id with quick reference

        try:
            sell_id = place_limit_sell(ex, symbol, qty_s, target, client_id=sell_cid)
            # Update state
            state["processed_buys"].append(buy_id)
            state["child_sells"][buy_id] = sell_id
            state["buy_fills"][buy_id] = {"price": float(avg), "amount": float(filled)}
            save_state(state)
        except Exception as e:
            log.error("open SELL for BUY %s failed: %s", buy_id, e)

    # 2) For each closed SELL compute profit
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if o["side"] != "sell" or o["status"] != "closed":
            continue

        sell_id = str(o["id"])
        if sell_id in state["sell_fills"]:
            continue  # already recorded

        s_filled = d(o.get("filled") or o.get("amount") or 0)
        s_avg    = d(o.get("average") or o.get("price") or 0)
        if s_filled <= 0 or s_avg <= 0:
            continue

        # Find parent BUY (via state.child_sells)
        parent_buy = None
        for b, s in state["child_sells"].items():
            if s == sell_id:
                parent_buy = b
                break

        profit_usd = Decimal("0")
        if parent_buy and parent_buy in state["buy_fills"]:
            bdat = state["buy_fills"][parent_buy]
            b_avg = d(bdat["price"])
            # Quantity adjustment: min common amount (handle partial cancellations)
            qty_base = min(s_filled, d(bdat["amount"]))
            profit_usd = (s_avg - b_avg) * qty_base

        state["sell_fills"][sell_id] = {"price": float(s_avg), "amount": float(s_filled)}
        if profit_usd > 0:
            prev = Decimal(str(state.get("realized_profit_usd", 0.0)))
            newv = prev + profit_usd
            state["realized_profit_usd"] = float(newv)
            log.info("Realized profit: +%.4f USDT (total=%.4f)", float(profit_usd), float(newv))

            # Call profit split module (if present)
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

    # Attempt to load markets — fail fast on auth errors
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

    # Bootstrap buys below current price to later create SELL orders
    log.info("Starting base_order_usd = %.1f", float(BASE_ORDER_USD))
    bootstrap_buys(ex, info, PAIR, BASE_ORDER_USD, MAX_CYCLE_USD)

    # Main loop
    stop = False
    def _sig(_a, _b):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    poll_sec = int(os.getenv("POLL_SECONDS", "7"))
    while not stop:
    # Minimal patch: process filled BUY -> open SELL; on SELL fill compute profit
        handle_fills_and_post_sells(ex, info, PAIR, state)

    # Additional logic (recenter, replenish BUY if shortage, etc.) can be added here – untouched
        time.sleep(poll_sec)

    log.info("Exiting main loop.")

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception("Fatal error: %s", e)
