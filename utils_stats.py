#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils_stats.py
--------------
אחראי בלעדית על קובץ הסטטיסטיקות data/runtime_stats.json (SSOT):
- cumulative_profit_usd
- bnb_converted_usd
- splits_count
- trade_count
- trigger_amount_usd (מידע עזר לדשבורד)
- last_update_ts
- schema_version

כולל כתיבה אטומית ונעילה בין-תהליכית (fcntl).
"""

from __future__ import annotations
import os, json, time, pathlib, contextlib

# נתיב בסיסי
DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATS_FILE = DATA_DIR / "runtime_stats.json"

SCHEMA_VERSION = 1
DEFAULT_TRIGGER = float(os.getenv("SPLIT_CHUNK_USD", "4.0"))

# --------------------------
# נעילה (fcntl) בין-תהליכית
# --------------------------
@contextlib.contextmanager
def file_lock(path: pathlib.Path):
    """
    נעילת קובץ פשוטה (POSIX). מייצרת קובץ .lock צמוד. על macOS/לינוקס זה יעבוד.
    אם fcntl לא קיים (ווינדוס) – נילון no-op (עדיין כתיבה אטומית עם os.replace).
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        try:
            import fcntl  # type: ignore
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            # no-op lock
            yield
    finally:
        fh.close()

def _atomic_write_json(path: pathlib.Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _defaults() -> dict:
    now = time.time()
    return {
        "schema_version": SCHEMA_VERSION,
        "cumulative_profit_usd": 0.0,
        "bnb_converted_usd": 0.0,
        "sell_trades_count": 0,  # Renamed from splits_count for clarity
        "actual_splits_count": 0,  # New field for actual profit chunks processed
        "trade_count": 0,
        "trigger_amount_usd": DEFAULT_TRIGGER,
        "last_update_ts": now,
    }

def _hydrate(d: dict) -> dict:
    base = _defaults()
    if isinstance(d, dict):
        base.update({k: v for k, v in d.items() if k in base})
        # שמירה על schema_version עדכני
        base["schema_version"] = SCHEMA_VERSION
    return base

def read_stats() -> dict:
    try:
        if STATS_FILE.exists():
            with file_lock(STATS_FILE):
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    return _hydrate(json.load(f))
    except Exception:
        pass
    return _defaults()

def write_stats(d: dict) -> None:
    d = _hydrate(d)
    d["last_update_ts"] = time.time()
    with file_lock(STATS_FILE):
        _atomic_write_json(STATS_FILE, d)

# --------------------------
# עדכונים פומביים
# --------------------------

def add_realized_profit(delta_usd: float, inc_sell_trades: int = 0, inc_trades: int = 0) -> dict:
    """
    מוסיף רווח/הפסד ממומש; מגדיל מונה של טריידי מכירה/טריידים כלליים (אם נמסרו).
    מחזיר מצב מעודכן (dict).
    
    Args:
        delta_usd: Profit/loss amount to add
        inc_sell_trades: Number of sell trades that matched inventory to add
        inc_trades: Number of general trades to add
    """
    st = read_stats()
    if delta_usd:
        st["cumulative_profit_usd"] = float(st.get("cumulative_profit_usd", 0.0) + float(delta_usd))
    if inc_sell_trades:
        st["sell_trades_count"] = int(st.get("sell_trades_count", 0)) + int(inc_sell_trades)
    if inc_trades:
        st["trade_count"] = int(st.get("trade_count", 0)) + int(inc_trades)
    write_stats(st)
    return st

def add_actual_splits(inc_actual_splits: int) -> dict:
    """
    מגדיל מונה של ספליטים ממשיים (chunks מעובדים ב-profit_split.py).
    מחזיר מצב מעודכן (dict).
    
    Args:
        inc_actual_splits: Number of actual profit chunks processed to add
    """
    st = read_stats()
    if inc_actual_splits:
        st["actual_splits_count"] = int(st.get("actual_splits_count", 0)) + int(inc_actual_splits)
    write_stats(st)
    return st

def add_bnb_converted_usd(delta_usd: float) -> dict:
    st = read_stats()
    st["bnb_converted_usd"] = float(st.get("bnb_converted_usd", 0.0) + float(delta_usd))
    write_stats(st)
    return st

def set_trigger_amount_usd(v: float) -> dict:
    st = read_stats()
    st["trigger_amount_usd"] = float(v)
    write_stats(st)
    return st
