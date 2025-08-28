#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data Validation Module for DOGE Trading Bot.

This module provides validation functions for all data storage files used by the bot,
including runtime statistics, profit tracking, split state, and price history.
It performs consistency checks and reports warnings for missing or invalid data.

Validated files:
- runtime_stats.json: Cumulative trading statistics
- profit_watcher_state.json: Profit tracking and inventory data
- split_state.json: Profit splitting configuration and state
- runtime_state.json: Runtime operational state (optional)
- price_history.json: Historical price data (optional)
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, Optional


# Data directory configuration
DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"

# Expected keys for runtime statistics
REQUIRED_STATS_KEYS = [
    "cumulative_profit_usd",
    "bnb_converted_usd",
    "sell_trades_count",  # Renamed from splits_count for clarity
    "actual_splits_count",  # New field for actual profit chunks
    "trade_count",
    "trigger_amount_usd",
]

# Warning thresholds
MAX_INVENTORY_EMPTY_LOTS = 50
MAX_PENDING_BUYS = 200
MAX_PRICE_HISTORY_POINTS = 120000
EMPTY_QTY_THRESHOLD = 1e-12


def read_json_file(file_path: pathlib.Path) -> Optional[Dict[str, Any]]:
    """
    Safely read and parse a JSON file.

    Args:
        file_path: Path to the JSON file to read

    Returns:
        Optional[Dict[str, Any]]: Parsed JSON data or None on error

    Note:
        Prints warning message on read/parse errors.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, IOError, OSError) as e:
        print(f"[WARN] Cannot read {file_path.name}: {e}")
        return None


def validate_runtime_stats() -> None:
    """
    Validate the runtime statistics file.

    Checks for required keys and reports current statistics.
    Reports errors for missing or malformed files.
    """
    stats_path = DATA_DIR / "runtime_stats.json"
    stats_data = read_json_file(stats_path)

    if not isinstance(stats_data, dict):
        print("[ERR] runtime_stats.json missing or malformed")
        return

    missing_keys = []
    for key in REQUIRED_STATS_KEYS:
        if key not in stats_data:
            missing_keys.append(key)

    if missing_keys:
        for key in missing_keys:
            print(f"[WARN] runtime_stats.json missing key: {key}")
    else:
        profit = stats_data["cumulative_profit_usd"]
        sell_trades = stats_data["sell_trades_count"]
        actual_splits = stats_data["actual_splits_count"]
        bnb = stats_data["bnb_converted_usd"]
        trades = stats_data["trade_count"]

        print(
            f"[OK] runtime_stats.json: profit={profit:.4f} sell_trades={sell_trades} "
            f"actual_splits={actual_splits} bnb=${bnb:.2f} trades={trades}"
        )


def validate_profit_watcher_state() -> None:
    """
    Validate the profit watcher state file.

    Checks inventory structure and reports warnings for empty lots.
    """
    watcher_path = DATA_DIR / "profit_watcher_state.json"
    watcher_data = read_json_file(watcher_path)

    if not isinstance(watcher_data, dict):
        print("[ERR] profit_watcher_state.json missing/malformed")
        return

    inventory = watcher_data.get("inventory", [])
    if not isinstance(inventory, list):
        print("[ERR] watcher.inventory not a list")
        return

    # Check for empty lots (quantity approximately zero)
    empty_lots = [lot for lot in inventory if float(lot.get("qty", 0.0)) <= EMPTY_QTY_THRESHOLD]

    if empty_lots:
        empty_count = len(empty_lots)
        if empty_count > MAX_INVENTORY_EMPTY_LOTS:
            print(
                f"[WARN] watcher.inventory contains {empty_count} empty lots "
                f"(qty≈0) - consider cleanup"
            )
        else:
            print(f"[INFO] watcher.inventory contains {empty_count} empty lots (qty≈0)")


def validate_split_state() -> None:
    """
    Validate the profit split state file.

    Checks for required fields and reports current split statistics.
    """
    split_path = DATA_DIR / "split_state.json"
    split_data = read_json_file(split_path)

    if not isinstance(split_data, dict):
        print("[ERR] split_state.json missing/malformed (migration from state.json needed?)")
        return

    # Extract split state values with safe defaults
    accumulator = float(split_data.get("split_accumulator_usd", 0.0))
    bnb_pending = float(split_data.get("bnb_pending_usd", 0.0))
    reinvest_pool = float(split_data.get("reinvest_pool_usd", 0.0))
    total_bnb = float(split_data.get("total_sent_to_bnb_usd", 0.0))
    total_reinvest = float(split_data.get("total_reinvested_usd", 0.0))

    print(
        f"[OK] split_state.json: acc={accumulator:.4f} bnb_pending=${bnb_pending:.2f} "
        f"reinvest_pool=${reinvest_pool:.2f} totalBNB=${total_bnb:.2f} "
        f"totalReinv=${total_reinvest:.2f}"
    )


def validate_runtime_state() -> None:
    """
    Validate the runtime state file (optional).

    Checks for large pending_buys collections that may need pruning.
    """
    runtime_path = DATA_DIR / "runtime_state.json"

    if not runtime_path.exists():
        print("[INFO] runtime_state.json not present (OK if bot writes elsewhere)")
        return

    runtime_data = read_json_file(runtime_path)
    if not isinstance(runtime_data, dict):
        print("[ERR] runtime_state.json malformed")
        return

    # Check for large pending_buys collection
    pending_buys = runtime_data.get("pending_buys", {})
    if isinstance(pending_buys, dict) and len(pending_buys) > MAX_PENDING_BUYS:
        print(
            f"[WARN] runtime_state.json pending_buys large: {len(pending_buys)} "
            f"(consider pruning)"
        )


def validate_price_history() -> None:
    """
    Validate the price history file (optional).

    Checks for excessively large history files that may need rotation.
    """
    history_path = DATA_DIR / "price_history.json"

    if not history_path.exists():
        print("[INFO] price_history.json missing (OK if dashboard is the only producer)")
        return

    history_data = read_json_file(history_path)
    if not isinstance(history_data, list):
        print("[ERR] price_history.json not a list")
        return

    if len(history_data) > MAX_PRICE_HISTORY_POINTS:
        print(
            f"[WARN] price_history.json very large: {len(history_data)} points; "
            f"consider rotation"
        )


def main() -> None:
    """
    Run all data validation checks.

    Performs comprehensive validation of all bot data files and reports
    any issues found.
    """
    print(f"[INFO] Validating data directory: {DATA_DIR}")

    validate_runtime_stats()
    validate_profit_watcher_state()
    validate_split_state()
    validate_runtime_state()
    validate_price_history()

    print("[DONE] Data validation complete")


if __name__ == "__main__":
    main()
