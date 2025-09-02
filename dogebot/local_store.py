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
    return _read(OPEN_ORDERS_FILE, [])

def list_history() -> List[Dict[str, Any]]:
    return _read(ORDER_HISTORY_FILE, [])

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
        # Deduplicate by (id,time,side,price,amount,status)
        seen = set()
        cleaned = []
        for r in rows:
            key = (
                r.get('id'), r.get('time'), r.get('side'),
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
    # Build key set for fast dedup
    keys = {
        (
            r.get('id'), r.get('time'), r.get('side'),
            round(float(r.get('price',0.0)),10),
            round(float(r.get('amount',0.0)),10),
            r.get('status')
        ) for r in existing
    }
    added = 0
    for r in new_rows:
        k = (
            r.get('id'), r.get('time'), r.get('side'),
            round(float(r.get('price',0.0)),10),
            round(float(r.get('amount',0.0)),10),
            r.get('status')
        )
        if k not in keys:
            existing.append(r)
            keys.add(k)
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
