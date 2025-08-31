"""Runtime stats merging and writing."""
from __future__ import annotations
import json, time, os
from typing import Any, Dict
from config import STATS_FILE_PATH, MAX_CYCLE_USD
from .exchange import MODE

def load_existing() -> Dict[str, Any]:
    if STATS_FILE_PATH.exists():
        try:
            with open(STATS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            return {}
    return {}

def build_stats(state: Dict[str, Any], realized: float, open_orders_count: int, existing: Dict[str, Any]) -> Dict[str, Any]:
    stats = dict(existing)
    stats["realized_profit_usd"] = realized
    cumulative = float(stats.get("cumulative_profit_usd", realized) or realized)
    if cumulative < realized:
        cumulative = realized
    stats["cumulative_profit_usd"] = cumulative
    stats.setdefault("grid_profit_usd", realized)
    total_profit = float(stats.get("total_profit_usd", cumulative))
    if total_profit < realized:
        total_profit = realized
    stats["total_profit_usd"] = total_profit
    stats.setdefault("unrealized_profit_usd", 0.0)
    stats.setdefault("fees_usd", 0.0)
    sell_trades_count = len(state.get("sell_fills", {}))
    if sell_trades_count:
        stats["sell_trades_count"] = sell_trades_count
        stats.setdefault("splits_count", sell_trades_count)
    else:
        stats.setdefault("sell_trades_count", int(stats.get("splits_count", 0)))
    stats["open_orders_count"] = open_orders_count
    base_invest = float(MAX_CYCLE_USD) if MAX_CYCLE_USD else 0.0
    if base_invest > 0:
        stats["profit_pct"] = (stats.get("total_profit_usd", 0.0) / base_invest) * 100.0
    else:
        stats.setdefault("profit_pct", 0.0)
    stats["last_update_ts"] = time.time()
    return stats

def write_stats(stats: Dict[str, Any]) -> None:
    tmp = str(STATS_FILE_PATH) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATS_FILE_PATH)
    except Exception:
        pass

__all__ = ["load_existing", "build_stats", "write_stats"]
