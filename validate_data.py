#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
validate_data.py
----------------
בדיקות עקביות למאגרי ה-data:
- runtime_stats.json
- profit_watcher_state.json
- split_state.json (לשעבר state.json)
- runtime_state.json (אם קיים)
- price_history.json (דגימה)

מדפיס אזהרות כשהשדות חסרים/לא הגיוניים.
"""

from __future__ import annotations
import json, pathlib, time

DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"

def _read(path: pathlib.Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] cannot read {path.name}: {e}")
        return None

def check_runtime_stats():
    p = DATA_DIR / "runtime_stats.json"
    j = _read(p)
    if not isinstance(j, dict):
        print("[ERR] runtime_stats.json missing or malformed")
        return
    ok = True
    for k in ["cumulative_profit_usd", "bnb_converted_usd", "splits_count", "trade_count", "trigger_amount_usd"]:
        if k not in j:
            ok = False
            print(f"[WARN] runtime_stats.json missing key: {k}")
    if ok:
        print(f"[OK] runtime_stats.json: profit={j['cumulative_profit_usd']:.4f} splits={j['splits_count']} bnb=${j['bnb_converted_usd']:.2f} trades={j['trade_count']}")

def check_profit_watcher_state():
    p = DATA_DIR / "profit_watcher_state.json"
    j = _read(p)
    if not isinstance(j, dict):
        print("[ERR] profit_watcher_state.json missing/malformed")
        return
    inv = j.get("inventory", [])
    if not isinstance(inv, list):
        print("[ERR] watcher.inventory not list")
    else:
        # הסר לוטים ריקים (אזהרה בלבד)
        zeros = [x for x in inv if float(x.get("qty", 0.0)) <= 1e-12]
        if zeros:
            print(f"[WARN] watcher.inventory contains {len(zeros)} empty lots (qty≈0)")

def check_split_state():
    p = DATA_DIR / "split_state.json"
    j = _read(p)
    if not isinstance(j, dict):
        print("[ERR] split_state.json missing/malformed (did you migrate state.json?)")
        return
    acc = float(j.get("split_accumulator_usd", 0.0))
    pend = float(j.get("bnb_pending_usd", 0.0))
    rein = float(j.get("reinvest_pool_usd", 0.0))
    totb = float(j.get("total_sent_to_bnb_usd", 0.0))
    totr = float(j.get("total_reinvested_usd", 0.0))
    print(f"[OK] split_state.json: acc={acc:.4f} bnb_pending=${pend:.2f} reinvest_pool=${rein:.2f} totalBNB=${totb:.2f} totalReinv=${totr:.2f}")

def check_runtime_state():
    p = DATA_DIR / "runtime_state.json"
    if not p.exists():
        print("[i] runtime_state.json not present (ok if your bot writes elsewhere)")
        return
    j = _read(p)
    if not isinstance(j, dict):
        print("[ERR] runtime_state.json malformed")
        return
    # הצע ניקוי היסטוריה אם גדל
    pf = j.get("pending_buys", {})
    if isinstance(pf, dict) and len(pf) > 200:
        print(f"[WARN] runtime_state.json pending_buys large: {len(pf)} (consider pruning)")

def check_price_history():
    p = DATA_DIR / "price_history.json"
    if not p.exists():
        print("[i] price_history.json missing (ok if dashboard is the only producer)")
        return
    j = _read(p)
    if not isinstance(j, list):
        print("[ERR] price_history.json not a list")
        return
    if len(j) > 120000:
        print(f"[WARN] price_history.json very large: {len(j)} points; consider rotation")

def main():
    print(f"[INFO] validating data dir: {DATA_DIR}")
    check_runtime_stats()
    check_profit_watcher_state()
    check_split_state()
    check_runtime_state()
    check_price_history()
    print("[DONE]")

if __name__ == "__main__":
    main()
