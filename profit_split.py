#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Profit Splitting Module for DOGE Trading Bot.

This module handles profit splitting functionality, dividing trading profits
into chunks for BNB conversion and reinvestment. The module maintains state
persistence and supports configurable split ratios.

Features:
- Configurable chunk size for profit splitting (default 4 USD)
- Flexible split ratio between BNB and reinvestment (default 50/50)
- Automatic BNB market purchases when minimum cost is reached
- Thread-safe state management with file locking
- Comprehensive statistics tracking

Configuration via environment variables:
- SPLIT_CHUNK_USD: Chunk size for splitting (default 4.0)
- SPLIT_RATIO: Ratio for BNB allocation (default 0.5 = 50%)
- BNB_SYMBOL: BNB trading symbol (default "BNB/USDT")
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict


# Configuration constants
SPLIT_CHUNK_USD = float(os.getenv("SPLIT_CHUNK_USD", "4.0"))
SPLIT_RATIO = float(os.getenv("SPLIT_RATIO", "0.5"))  # 0.5 = 50% to BNB, 50% to reinvest
BNB_SYMBOL = os.getenv("BNB_SYMBOL", "BNB/USDT")

# File system paths
DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE_PATH = DATA_DIR / "split_state.json"
SCHEMA_VERSION = 1

# Cache settings
MIN_COST_CACHE_DURATION = 60.0  # seconds


@contextlib.contextmanager
def file_lock(file_path: pathlib.Path):
    """
    Context manager for file locking to ensure thread-safe operations.

    Args:
        file_path: Path to the file to lock

    Yields:
        None: Context for locked file operations

    Note:
        Uses fcntl on POSIX systems, gracefully degrades on other platforms.
    """
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")

    with open(lock_path, "a+") as lock_file:
        try:
            try:
                import fcntl  # POSIX only

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except ImportError:
                # Graceful degradation on non-POSIX systems
                yield
        finally:
            pass  # File handle closed automatically


@dataclass
class SplitState:
    """
    Data class representing the profit splitting state.

    Attributes:
        schema_version: State schema version for migrations
        split_accumulator_usd: Accumulated profit not yet split (< chunk size)
        bnb_pending_usd: USD amount pending BNB purchase
        reinvest_pool_usd: USD amount available for reinvestment
        total_sent_to_bnb_usd: Total USD converted to BNB (statistics)
        total_reinvested_usd: Total USD reinvested (statistics)
        last_update_ts: Timestamp of last state update
        last_action: Description of last action taken (for debugging)
        min_cost_cache_usd: Cached minimum cost for BNB purchase
        min_cost_cache_ts: Timestamp of cached minimum cost
    """

    schema_version: int = SCHEMA_VERSION
    split_accumulator_usd: float = 0.0
    bnb_pending_usd: float = 0.0
    reinvest_pool_usd: float = 0.0
    total_sent_to_bnb_usd: float = 0.0
    total_reinvested_usd: float = 0.0
    last_update_ts: float = 0.0
    last_action: str = ""
    min_cost_cache_usd: float = 0.0
    min_cost_cache_ts: float = 0.0


def _write_state_atomically(file_path: pathlib.Path, data: Dict[str, Any]) -> None:
    """
    Write state data to file atomically to prevent corruption.

    Args:
        file_path: Path to write to
        data: Data dictionary to write
    """
    temp_file = file_path.with_suffix(file_path.suffix + ".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    os.replace(temp_file, file_path)


def _load_state() -> SplitState:
    """
    Load the profit split state from file.

    Returns:
        SplitState: Current state or default state if file doesn't exist
    """
    if not STATE_FILE_PATH.exists():
        return SplitState()

    try:
        with file_lock(STATE_FILE_PATH):
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as file:
                data = json.load(file)

        if isinstance(data, dict):
            # Ensure schema version is current
            data["schema_version"] = SCHEMA_VERSION
            return SplitState(**data)
    except (json.JSONDecodeError, TypeError, OSError):
        pass

    return SplitState()


def _save_state(state: SplitState) -> None:
    """
    Save the profit split state to file.

    Args:
        state: State object to save
    """
    state.schema_version = SCHEMA_VERSION
    state.last_update_ts = time.time()

    with file_lock(STATE_FILE_PATH):
        _write_state_atomically(STATE_FILE_PATH, asdict(state))


def _get_minimum_cost(exchange_client, symbol: str, fallback: float = 10.0) -> float:
    """
    Get the minimum cost for trading a symbol, with caching.

    Args:
        exchange_client: CCXT exchange client
        symbol: Trading symbol to check
        fallback: Fallback value if lookup fails

    Returns:
        float: Minimum cost in USD

    Note:
        Caches the result for MIN_COST_CACHE_DURATION seconds to reduce API calls.
    """
    state = _load_state()
    current_time = time.time()

    # Use cached value if available and fresh
    if (
        state.min_cost_cache_usd > 0
        and current_time - state.min_cost_cache_ts < MIN_COST_CACHE_DURATION
    ):
        return state.min_cost_cache_usd

    try:
        exchange_client.load_markets()
        market_info = exchange_client.market(symbol)
        limits = market_info.get("limits", {}).get("cost", {})
        minimum_cost = limits.get("min")

        value = float(minimum_cost) if minimum_cost else fallback
    except Exception:
        value = fallback

    # Update cache
    state.min_cost_cache_usd = value
    state.min_cost_cache_ts = current_time
    _save_state(state)

    return value


def handle_realized_profit(profit_usd: float, exchange_client) -> Dict[str, Any]:
    """
    Process realized profit by splitting into chunks for BNB and reinvestment.

    Args:
        profit_usd: Profit amount in USD to process
        exchange_client: CCXT exchange client for BNB purchases

    Returns:
        Dict[str, Any]: Processing results containing:
            - chunks: Number of complete chunks processed
            - bnb_bought_usd: USD amount used to buy BNB
            - reinvest_added_usd: USD amount added to reinvestment pool
    """
    result = {"chunks": 0, "bnb_bought_usd": 0.0, "reinvest_added_usd": 0.0}

    if profit_usd <= 0:
        return result

    state = _load_state()
    state.split_accumulator_usd += float(profit_usd)

    # Calculate complete chunks
    chunk_size = SPLIT_CHUNK_USD
    complete_chunks = int(state.split_accumulator_usd // chunk_size)
    remainder = state.split_accumulator_usd - complete_chunks * chunk_size

    if complete_chunks > 0:
        # Split chunks between BNB and reinvestment
        bnb_per_chunk = chunk_size * SPLIT_RATIO
        reinvest_per_chunk = chunk_size - bnb_per_chunk

        total_to_bnb = bnb_per_chunk * complete_chunks
        total_to_reinvest = reinvest_per_chunk * complete_chunks

        # Update state
        state.bnb_pending_usd += total_to_bnb
        state.reinvest_pool_usd += total_to_reinvest
        state.split_accumulator_usd = remainder

        # Update result
        result["chunks"] = complete_chunks
        result["reinvest_added_usd"] = total_to_reinvest

        state.last_action = (
            f"chunked: +{complete_chunks} chunks "
            f"(bnb+={total_to_bnb:.2f}, reinv+={total_to_reinvest:.2f})"
        )
    else:
        state.last_action = f"accumulate: +{profit_usd:.4f} (pending chunk)"

    # Attempt to buy BNB if we have enough pending
    minimum_cost = _get_minimum_cost(exchange_client, BNB_SYMBOL, fallback=10.0)

    if state.bnb_pending_usd >= minimum_cost:
        usd_to_spend = state.bnb_pending_usd

        try:
            # Get current BNB price
            ticker = exchange_client.fetch_ticker(BNB_SYMBOL)
            bnb_price = float(ticker["last"])

            # Calculate quantity
            estimated_quantity = usd_to_spend / bnb_price
            precise_quantity = float(
                exchange_client.amount_to_precision(BNB_SYMBOL, estimated_quantity)
            )

            if precise_quantity > 0:
                # Place market buy order
                client_order_id = f"SPLITBNB-{int(time.time())}"
                exchange_client.create_order(
                    BNB_SYMBOL,
                    "market",
                    "buy",
                    precise_quantity,
                    None,
                    {"newClientOrderId": client_order_id},
                )

                # Update state
                state.total_sent_to_bnb_usd += usd_to_spend
                state.bnb_pending_usd = 0.0
                result["bnb_bought_usd"] = usd_to_spend

                state.last_action = f"BNB market buy ~${usd_to_spend:.2f} (qty≈{precise_quantity})"
        except Exception as e:
            state.last_action = f"BNB buy failed: {e}"

    _save_state(state)
    return result


def pull_reinvestment_funds(max_amount_usd: float) -> float:
    """
    Pull funds from reinvestment pool for use in new buy orders.

    Args:
        max_amount_usd: Maximum amount to pull from the pool

    Returns:
        float: Actual amount pulled (may be less than requested)

    Note:
        This function reduces the reinvestment pool by the returned amount.
    """
    if max_amount_usd <= 0:
        return 0.0

    state = _load_state()
    amount_to_pull = min(state.reinvest_pool_usd, float(max_amount_usd))

    if amount_to_pull > 0:
        state.reinvest_pool_usd -= amount_to_pull
        state.total_reinvested_usd += amount_to_pull
        state.last_action = f"reinvest pulled {amount_to_pull:.2f}"
        _save_state(state)

    return float(amount_to_pull)


def get_current_state() -> Dict[str, Any]:
    """
    Get the current profit split state as a dictionary.

    Returns:
        Dict[str, Any]: Current state data
    """
    return asdict(_load_state())


def main() -> None:
    """Command-line interface for checking split state."""
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "status"

    if command == "status":
        current_state = get_current_state()
        print(json.dumps(current_state, indent=2, ensure_ascii=False))
    else:
        print("Usage: python3 profit_split.py status")


if __name__ == "__main__":
    main()
