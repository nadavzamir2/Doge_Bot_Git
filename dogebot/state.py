"""Trading state load/save and mutation utilities."""
from __future__ import annotations
import json, os
from typing import Any, Dict
from config import STATE_FILE_PATH

def default_state() -> Dict[str, Any]:
    return {
        "processed_buys": [],
        "child_sells": {},
        "buy_fills": {},
        "sell_fills": {},
        "realized_profit_usd": 0.0,
    }

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    d = default_state()
                    d.update(data)
                    return d
        except Exception:
            pass
    return default_state()

def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_FILE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE_PATH)
    except Exception:
        pass

__all__ = ["load_state", "save_state", "default_state"]
