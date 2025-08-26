"""
Utility functions for the DOGE trading bot.

This module provides utility functions for price and quantity rounding,
runtime statistics management, and other common operations needed by the bot.
"""

import json
import math
import pathlib
import threading
from typing import Optional


def round_down_qty(
    qty: float, amount_precision: Optional[int] = None, amount_step: Optional[float] = None
) -> float:
    """
    Round down a quantity to comply with exchange precision requirements.

    Args:
        qty: The quantity to round down
        amount_precision: Number of decimal places for precision-based rounding
        amount_step: Step size for step-based rounding (takes precedence)

    Returns:
        float: The rounded down quantity

    Note:
        If amount_step is provided, it takes precedence over amount_precision.
        Always rounds down to avoid exceeding available balance.
    """
    if amount_step:
        step = float(amount_step)
        if step > 0:
            return math.floor(qty / step) * step

    if amount_precision is not None:
        factor = 10 ** int(amount_precision)
        return math.floor(qty * factor) / factor

    return math.floor(qty)


def round_price(
    price: float, price_precision: Optional[int] = None, price_tick: Optional[float] = None
) -> float:
    """
    Round down a price to comply with exchange precision requirements.

    Args:
        price: The price to round down
        price_precision: Number of decimal places for precision-based rounding
        price_tick: Tick size for tick-based rounding (takes precedence)

    Returns:
        float: The rounded down price

    Note:
        If price_tick is provided, it takes precedence over price_precision.
        Always rounds down to avoid exceeding bid/ask constraints.
    """
    if price_tick:
        step = float(price_tick)
        if step > 0:
            return math.floor(price / step) * step

    if price_precision is not None:
        # Use floor to avoid rounding up which can exceed allowed price
        factor = 10 ** int(price_precision)
        return math.floor(price * factor) / factor

    return price


# Runtime statistics management
_STATS_LOCK = threading.Lock()
STATS_FILE = pathlib.Path.home() / "doge_bot" / "data" / "runtime_stats.json"


def _read_stats() -> dict:
    """
    Read runtime statistics from the stats file.

    Returns:
        dict: Statistics dictionary with default values if file doesn't exist
    """
    try:
        with STATS_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, IOError, OSError):
        pass

    return {
        "cumulative_profit_usd": 0.0,
        "splits_count": 0,
        "bnb_converted_usd": 0.0,
        "trade_count": 0,
        "trigger_amount_usd": 0.0,
    }


def _write_stats(stats_data: dict) -> None:
    """
    Write runtime statistics to the stats file atomically.

    Args:
        stats_data: Dictionary containing statistics to write
    """
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = STATS_FILE.with_suffix(".json.tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(stats_data, file, ensure_ascii=False, indent=2)

    temp_file.replace(STATS_FILE)


def add_realized_profit(
    profit_usd: float, *, inc_splits: int = 0, add_bnb_usd: float = 0.0
) -> None:
    """
    Add realized profit to the runtime statistics.

    Args:
        profit_usd: Profit amount in USD to add
        inc_splits: Number of profit splits to increment (keyword-only)
        add_bnb_usd: Amount of BNB converted in USD to add (keyword-only)

    Note:
        This function is thread-safe and uses atomic file operations.
    """
    with _STATS_LOCK:
        stats = _read_stats()

        current_profit = float(stats.get("cumulative_profit_usd", 0.0) or 0.0)
        current_splits = int(stats.get("splits_count", 0) or 0)
        current_bnb = float(stats.get("bnb_converted_usd", 0.0) or 0.0)

        stats["cumulative_profit_usd"] = current_profit + float(profit_usd)
        stats["splits_count"] = current_splits + int(inc_splits)
        stats["bnb_converted_usd"] = current_bnb + float(add_bnb_usd)

        _write_stats(stats)
