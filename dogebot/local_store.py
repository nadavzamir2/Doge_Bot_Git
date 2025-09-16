"""Local fallback storage for open orders and order history (PAPER mode or auth failure).

Writes lightweight JSON arrays so the dashboard can still render tables when
API keys are missing/invalid. Safe (best-effort) and never raises.
"""
from __future__ import annotations
import json, time
from typing import Any, Dict, List
from pathlib import Path
from config import DATA_DIR

OPEN_ORDERS_FILE = DATA_DIR / "open_orders_local.json"
ORDER_HISTORY_FILE = DATA_DIR / "order_history_local.json"

def _read(path: Path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else default
    except Exception:
        return default
    return default

def _write(path: Path, data: Any) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception:
        pass

def list_open_orders() -> List[Dict[str, Any]]:
    orders = _read(OPEN_ORDERS_FILE, [])
    # In PAPER mode, include PAPER orders; otherwise filter them out
    from config import MODE
    if MODE == 'PAPER':
        return orders
    else:
        # Remove PAPER mode orders (id starts with 'PAPER-')
        return [o for o in orders if not str(o.get('id','')).startswith('PAPER-')]

def list_history() -> List[Dict[str, Any]]:
    hist = _read(ORDER_HISTORY_FILE, [])
    # In PAPER mode, include PAPER orders; otherwise filter them out
    from config import MODE
    if MODE == 'PAPER':
        return hist
    else:
        # Remove PAPER mode orders (id starts with 'PAPER-')
        return [o for o in hist if not str(o.get('id','')).startswith('PAPER-')]

def add_open_order(order_id: str, side: str, price: float, amount: float) -> None:
    orders = list_open_orders()
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
    orders.append({
        "id": order_id,
        "time": ts_iso,
        "side": side,
        "price": price,
        "amount": amount,
        "value_usdt": price * amount,
    })
    _write(OPEN_ORDERS_FILE, orders[-200:])  # cap size

def _remove_open(order_id: str) -> Dict[str, Any] | None:
    orders = list_open_orders()
    remaining = []
    removed = None
    for o in orders:
        if o.get("id") == order_id and removed is None:
            removed = o
            continue
        remaining.append(o)
    if removed is not None:
        _write(OPEN_ORDERS_FILE, remaining)
    return removed

def record_fill(order_id: str, side: str, price: float, amount: float, status: str = "filled") -> None:
    removed = _remove_open(order_id)
    hist = list_history()
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
    base = removed or {
        "id": order_id,
        "time": ts_iso,
        "side": side,
        "price": price,
        "amount": amount,
        "value_usdt": price * amount,
    }
    base["execution_time"] = ts_iso
    base["status"] = status
    hist.append(base)
    _write(ORDER_HISTORY_FILE, hist[-500:])  # cap

def set_history(rows: list[dict]) -> None:
    """Overwrite history file safely with provided rows (dedup + cap)."""
    try:
        # Deduplicate by (id,timestamp,side,price,amount,status)
        # Prefer execution_time when available to avoid mismatches between trade rows
        def _time_key(item):
            et = item.get('execution_time')
            if et and et != '—':
                return et
            return item.get('time')

        seen = set()
        cleaned = []
        for r in rows:
            key = (
                r.get('id'), _time_key(r), r.get('side'),
                round(float(r.get('price',0.0)), 10),
                round(float(r.get('amount',0.0)), 10),
                r.get('status'),
            )
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(r)
        _write(ORDER_HISTORY_FILE, cleaned[-5000:])  # raise cap for extended history
    except Exception:
        pass

def merge_history(new_rows: list[dict]) -> list[dict]:
    """Merge new rows into existing history, persist, and return merged list."""
    existing = list_history()
    # Build key set for fast dedup; prefer execution_time when present
    def _time_key(item):
        et = item.get('execution_time')
        if et and et != '—':
            return et
        return item.get('time')

    # Enhanced deduplication: detect duplicates by trade characteristics
    # Primary key: trade characteristics (detects same trade from different sources)
    trade_keys = {
        (
            _time_key(r), r.get('side'),
            round(float(r.get('price',0.0)),10),
            round(float(r.get('amount',0.0)),10)
        ) for r in existing
    }
    
    # Secondary key: exact record (for true duplicates)
    exact_keys = {
        (
            r.get('id'), _time_key(r), r.get('side'),
            round(float(r.get('price',0.0)),10),
            round(float(r.get('amount',0.0)),10),
            r.get('status')
        ) for r in existing
    }
    
    added = 0
    for r in new_rows:
        trade_key = (
            _time_key(r), r.get('side'),
            round(float(r.get('price',0.0)),10),
            round(float(r.get('amount',0.0)),10)
        )
        exact_key = (
            r.get('id'), _time_key(r), r.get('side'),
            round(float(r.get('price',0.0)),10),
            round(float(r.get('amount',0.0)),10),
            r.get('status')
        )
        
        # Skip if this trade already exists (same execution_time + price + amount + side)
        # or if it's an exact duplicate record
        if trade_key not in trade_keys and exact_key not in exact_keys:
            existing.append(r)
            trade_keys.add(trade_key)
            exact_keys.add(exact_key)
            added += 1
    if added:
        set_history(existing)
    return existing

__all__ = [
    "add_open_order",
    "record_fill",
    "list_open_orders",
    "list_history",
    "set_history",
    "merge_history",
]
