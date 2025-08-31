"""Grid computation and market precision utilities."""
from __future__ import annotations
from decimal import Decimal
from typing import Any, Dict
import ccxt
from config import GRID_LOW_PRICE, GRID_HIGH_PRICE, GRID_STEP_PERCENT


def load_market_precision(exchange: ccxt.Exchange, symbol: str) -> Dict[str, Any]:
    markets = exchange.load_markets()
    market_info = markets[symbol]

    price_precision = None
    amount_precision = None
    if "precision" in market_info:
        prec = market_info["precision"]
        price_precision = Decimal(str(prec.get("price", "0.00001"))) if prec.get("price") is not None else None
        amount_precision = Decimal(str(prec.get("amount", "1"))) if prec.get("amount") is not None else None

    filters = market_info.get("info", {}).get("filters", [])
    price_tick = None
    amount_step = None
    min_notional = None
    for f in filters:
        t = f.get("filterType")
        if t == "PRICE_FILTER":
            ts = f.get("tickSize")
            if ts:
                price_tick = Decimal(ts)
        elif t == "LOT_SIZE":
            ss = f.get("stepSize")
            if ss:
                amount_step = Decimal(ss)
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            mn = f.get("minNotional")
            if mn:
                min_notional = Decimal(str(mn))

    if price_tick:
        price_precision = price_tick
    if amount_step:
        amount_precision = amount_step
    if not min_notional:
        min_notional = Decimal("1.0")

    return {
        "price_tick": price_precision or Decimal("0.00001"),
        "amount_step": amount_precision or Decimal("1"),
        "min_cost": min_notional,
        "price_precision": price_precision or Decimal("0.00001"),
        "amount_precision": amount_precision or Decimal("1"),
    }


def compute_grid_levels(low_price: Decimal = GRID_LOW_PRICE, high_price: Decimal = GRID_HIGH_PRICE, step_percent: Decimal = GRID_STEP_PERCENT) -> list[Decimal]:
    if low_price <= 0 or high_price <= 0 or high_price <= low_price:
        return []
    multiplier = Decimal("1.0") + (step_percent / Decimal("100"))
    levels: list[Decimal] = []
    current = low_price
    while current <= high_price + Decimal("1e-18"):
        levels.append(current)
        current = current * multiplier
    if levels and levels[-1] < high_price:
        levels.append(high_price)
    return levels

__all__ = ["load_market_precision", "compute_grid_levels"]
