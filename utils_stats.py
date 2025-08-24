# utils_stats.py
# -*- coding: utf-8 -*-
"""
Utilities for reading/updating the runtime stats file used by the dashboard.
Writes to: ~/doge_bot/data/runtime_stats.json

Schema expected by dash_server.py:
{
  "cumulative_profit_usd": float,
  "splits_count": int,
  "bnb_converted_usd": float
}
"""

from __future__ import annotations
import json
import pathlib
import threading
from typing import Dict, Any

STATS_FILE = pathlib.Path.home() / "doge_bot" / "data" / "runtime_stats.json"
STATS_FILE.parent.mkdir(parents=True, exist_ok=True)

_STATS_LOCK = threading.Lock()

_DEFAULT = {
    "cumulative_profit_usd": 0.0,
    "splits_count": 0,
    "bnb_converted_usd": 0.0,
}

def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)

def _safe_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)

def read_stats() -> Dict[str, Any]:
    """Read stats JSON; if missing/invalid, return defaults."""
    try:
        if STATS_FILE.exists():
            with STATS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "cumulative_profit_usd": _safe_float(data.get("cumulative_profit_usd", 0.0)),
                    "splits_count": _safe_int(data.get("splits_count", 0)),
                    "bnb_converted_usd": _safe_float(data.get("bnb_converted_usd", 0.0)),
                }
    except Exception:
        pass
    return dict(_DEFAULT)

def write_stats(data: Dict[str, Any]) -> None:
    """Write stats atomically."""
    norm = {
        "cumulative_profit_usd": _safe_float(data.get("cumulative_profit_usd", 0.0)),
        "splits_count": _safe_int(data.get("splits_count", 0)),
        "bnb_converted_usd": _safe_float(data.get("bnb_converted_usd", 0.0)),
    }
    tmp = STATS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False)
    tmp.replace(STATS_FILE)

def add_realized_profit(profit_usd: float, *, inc_splits: int = 0, add_bnb_usd: float = 0.0) -> None:
    """
    Thread-safe additive update to cumulative stats.
    Use this after you compute realized PnL (e.g., after SELL that closes BUY inventory).
    """
    with _STATS_LOCK:
        s = read_stats()
        s["cumulative_profit_usd"] = _safe_float(s.get("cumulative_profit_usd", 0.0)) + _safe_float(profit_usd)
        s["splits_count"] = _safe_int(s.get("splits_count", 0)) + _safe_int(inc_splits)
        s["bnb_converted_usd"] = _safe_float(s.get("bnb_converted_usd", 0.0)) + _safe_float(add_bnb_usd)
        write_stats(s)
