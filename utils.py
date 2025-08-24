import math
from typing import Optional

def round_down_qty(qty: float,
                   amount_precision: Optional[int] = None,
                   amount_step: Optional[float] = None) -> float:
    if amount_step:
        step = float(amount_step)
        if step > 0:
            return math.floor(qty / step) * step
    if amount_precision is not None:
        factor = 10 ** int(amount_precision)
        return math.floor(qty * factor) / factor
    return math.floor(qty)

def round_price(price: float,
                price_precision: Optional[int] = None,
                price_tick: Optional[float] = None) -> float:
    if price_tick:
        step = float(price_tick)
        if step > 0:
            return math.floor(price / step) * step
    if price_precision is not None:
        fmt = "{:0." + str(int(price_precision)) + "f}"
        return float(fmt.format(price))
    return price

# utils_stats.py  (או בכל קובץ משותף בבוט)
import json, pathlib, threading

_STATS_LOCK = threading.Lock()
STATS_FILE = pathlib.Path.home() / "doge_bot" / "data" / "runtime_stats.json"

def _read_stats():
    try:
        with STATS_FILE.open("r", encoding="utf-8") as f:
            j = json.load(f)
            if isinstance(j, dict):
                return j
    except Exception:
        pass
    return {"cumulative_profit_usd": 0.0, "splits_count": 0, "bnb_converted_usd": 0.0}

def _write_stats(j):
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False)
    tmp.replace(STATS_FILE)

def add_realized_profit(profit_usd: float, *, inc_splits: int = 0, add_bnb_usd: float = 0.0):
    with _STATS_LOCK:
        s = _read_stats()
        s["cumulative_profit_usd"] = float(s.get("cumulative_profit_usd", 0.0) or 0.0) + float(profit_usd)
        s["splits_count"] = int(s.get("splits_count", 0) or 0) + int(inc_splits)
        s["bnb_converted_usd"] = float(s.get("bnb_converted_usd", 0.0) or 0.0) + float(add_bnb_usd)
        _write_stats(s)
