"""Order placement and fill handling logic."""
from __future__ import annotations
from decimal import Decimal
from typing import Any, Dict, Optional
import ccxt
import time
from config import (
    MODE, RECV_WINDOW, GRID_STEP_PERCENT, FEE_BUFFER, BASE_ORDER_USD, MAX_CYCLE_USD, TRADING_PAIR
)
from trading_utils import round_amount_down, round_price_down, to_decimal
from .state import save_state
from . import local_store
from .grid import compute_grid_levels

import logging
log = logging.getLogger("doge_grid_bot.orders")

def generate_client_order_id(prefix: str) -> str:
    ts = int(time.time() * 1000) % 10_000_000
    return f"{prefix}-{ts}"

def place_limit_buy(exchange: ccxt.Exchange, symbol: str, qty: Decimal, price: Decimal, client_id: Optional[str] = None) -> str:
    params = {"recvWindow": RECV_WINDOW}
    if client_id:
        params["newClientOrderId"] = client_id
    if MODE == "LIVE":
        o = exchange.create_order(symbol, "limit", "buy", float(qty), float(price), params)
        oid = str(o["id"])
        log.info("(LIVE) BUY %s @ %s id=%s", qty, price, oid)
        # Record locally for fallback display
        local_store.add_open_order(oid, "buy", float(price), float(qty))
        return oid
    else:
        oid = "PAPER-" + (client_id or "B")
        log.info("(PAPER) BUY %s @ %s id=%s", qty, price, oid)
        local_store.add_open_order(oid, "buy", float(price), float(qty))
        return oid

def place_limit_sell(exchange: ccxt.Exchange, symbol: str, qty: Decimal, price: Decimal, client_id: Optional[str] = None) -> str:
    params = {"recvWindow": RECV_WINDOW}
    if client_id:
        params["newClientOrderId"] = client_id
    if MODE == "LIVE":
        o = exchange.create_order(symbol, "limit", "sell", float(qty), float(price), params)
        oid = str(o["id"])
        log.info("(LIVE) SELL %s @ %s id=%s", qty, price, oid)
        local_store.add_open_order(oid, "sell", float(price), float(qty))
        return oid
    else:
        oid = "PAPER-" + (client_id or "S")
        log.info("(PAPER) SELL %s @ %s id=%s", qty, price, oid)
        local_store.add_open_order(oid, "sell", float(price), float(qty))
        return oid

def bootstrap_buys(exchange: ccxt.Exchange, market_info: Dict[str, Any], symbol: str) -> int:
    try:
        ticker = exchange.fetch_ticker(symbol)
        current_price = to_decimal(ticker.get("last"))
    except Exception as e:
        log.error("fetch_ticker failed: %s", e)
        return 0
    grid_levels = compute_grid_levels()
    buy_levels = [lvl for lvl in grid_levels if lvl <= current_price]
    buy_levels = list(reversed(buy_levels))
    budget_remaining = MAX_CYCLE_USD
    orders_placed = 0
    if MODE == "LIVE":
        try:
            balance = exchange.fetch_balance(params={"recvWindow": RECV_WINDOW})
            usdt_free = to_decimal(balance["free"].get("USDT", 0.0))
        except Exception as e:
            log.error("fetch_balance failed: %s", e)
            return 0
    else:
        usdt_free = MAX_CYCLE_USD
    est_need = min(len(buy_levels), 7) * BASE_ORDER_USD
    if usdt_free < min(est_need, budget_remaining):
        log.warning("Insufficient USDT %s need >= %s skipping", usdt_free, min(est_need, budget_remaining))
        return 0
    for lvl in buy_levels[:7]:
        if budget_remaining < BASE_ORDER_USD or usdt_free < BASE_ORDER_USD:
            break
        qty = round_amount_down(BASE_ORDER_USD / lvl, market_info["amount_step"])
        price = round_price_down(lvl, market_info["price_tick"])
        if qty * price < market_info["min_cost"]:
            req_qty = (market_info["min_cost"] / price) * (Decimal("1.0") + FEE_BUFFER)
            qty = round_amount_down(req_qty, market_info["amount_step"])
        if qty <= 0:
            continue
        cid = generate_client_order_id("B")
        try:
            place_limit_buy(exchange, symbol, qty, price, cid)
            orders_placed += 1
            budget_remaining -= BASE_ORDER_USD
            usdt_free -= qty * price
        except Exception as e:
            log.error("place buy fail: %s", e)
    log.info("Bootstrapped %d buys", orders_placed)
    return orders_placed

def process_fills(exchange: ccxt.Exchange, market_info: Dict[str, Any], symbol: str, state: Dict[str, Any]) -> None:
    try:
        orders = exchange.fetch_orders(symbol, limit=50)
    except Exception as e:
        log.error("fetch_orders failed: %s", e)
        return
    # buys
    for o in orders:
        if o.get("symbol") != symbol or o.get("side") != "buy" or o.get("status") != "closed":
            continue
        bid = str(o.get("id"))
        if bid in state["processed_buys"]:
            continue
        filled_amount = to_decimal(o.get("filled") or o.get("amount") or 0)
        avg_price = to_decimal(o.get("average") or o.get("price") or 0)
        if filled_amount <= 0 or avg_price <= 0:
            continue
        target_price = round_price_down(avg_price * (Decimal("1.0") + (GRID_STEP_PERCENT / Decimal("100"))), market_info["price_tick"])
        sell_qty = round_amount_down(filled_amount * (Decimal("1.0") - FEE_BUFFER), market_info["amount_step"])
        if sell_qty * target_price < market_info["min_cost"]:
            req_qty = (market_info["min_cost"] / target_price) * (Decimal("1.0") + FEE_BUFFER)
            sell_qty = round_amount_down(req_qty, market_info["amount_step"])
        if sell_qty <= 0:
            continue
        scid = generate_client_order_id(f"S{bid[-4:]}")
        try:
            sid = place_limit_sell(exchange, symbol, sell_qty, target_price, scid)
            state["processed_buys"].append(bid)
            state["child_sells"][bid] = sid
            state["buy_fills"][bid] = {"price": float(avg_price), "amount": float(filled_amount)}
            save_state(state)
        except Exception as e:
            log.error("create sell fail: %s", e)
    # sells
    for o in orders:
        if o.get("symbol") != symbol or o.get("side") != "sell" or o.get("status") != "closed":
            continue
        sid = str(o.get("id"))
        if sid in state["sell_fills"]:
            continue
        filled_amount = to_decimal(o.get("filled") or o.get("amount") or 0)
        avg_price = to_decimal(o.get("average") or o.get("price") or 0)
        if filled_amount <= 0 or avg_price <= 0:
            continue
        parent = None
        for bid, csid in state["child_sells"].items():
            if csid == sid:
                parent = bid
                break
        profit_usd = Decimal("0")
        if parent and parent in state["buy_fills"]:
            bdata = state["buy_fills"][parent]
            bprice = Decimal(str(bdata["price"]))
            shared_qty = min(filled_amount, Decimal(str(bdata["amount"])))
            profit_usd = (avg_price - bprice) * shared_qty
        state["sell_fills"][sid] = {"price": float(avg_price), "amount": float(filled_amount)}
        if profit_usd > 0:
            prev = Decimal(str(state.get("realized_profit_usd", 0.0)))
            state["realized_profit_usd"] = float(prev + profit_usd)
            log.info("Realized profit +%.4f (total=%.4f)", float(profit_usd), float(prev + profit_usd))
        # record fill to local history
        local_store.record_fill(sid, "sell", float(avg_price), float(filled_amount))
        save_state(state)

__all__ = [
    "bootstrap_buys",
    "process_fills",
]
