#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
profit_watcher.py
-----------------
Sidecar computing realized profit (FIFO) from Binance trades and updating
~/doge_bot/data/runtime_stats.json. Persists state in
~/doge_bot/data/profit_watcher_state.json to avoid double counting.

Features:
- Optional historical backfill
- Live loop fetching new trades
- Realized profit computation (including fees on both sides)
- Dashboard update via utils_stats.add_realized_profit
- Profit split mechanism update (BNB / reinvest) via profit_split.handle_profit
- NEW: Synchronize bnb_converted_usd on dashboard from profit_split.state.json

Example run:
    cd ~/doge_bot
    source venv/bin/activate
    python3 -u profit_watcher.py --backfill --since-days 7
"""

from __future__ import annotations

import os
import time
import json
import math
import pathlib
import argparse
import threading
from typing import List, Dict, Any, Optional, Tuple

from dotenv import load_dotenv

# === Load project ENV ===
ENV_FILE = os.path.expanduser("~/doge_bot/.env")
load_dotenv(ENV_FILE)

import ccxt  # noqa: E402
from utils_stats import add_realized_profit  # noqa: E402
from profit_split import handle_profit, read_state as split_read_state  # also used for reading total_sent_to_bnb_usd

# === General configuration ===
BINANCE_REGION = os.getenv("BINANCE_REGION", "com").strip().lower()
API_KEY = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY") or ""
API_SECRET = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET") or ""
RECV_WINDOW = int(os.getenv("BINANCE_RECVWINDOW", "10000"))
PAIR = os.getenv("PAIR", "DOGE/USDT").strip()
FEE_RATE_EACH_SIDE = float(os.getenv("FEE_RATE_EACH_SIDE", "0.001"))

DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "profit_watcher_state.json"

# === Path of status file read by the dashboard ===
STATS_FILE = DATA_DIR / "runtime_stats.json"
_STATS_LOCK = threading.Lock()


def _load_stats() -> Dict[str, Any]:
    try:
        if STATS_FILE.exists():
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    # Default structure matching /api/stats in the dashboard
    return {
        "cumulative_profit_usd": 0.0,
        "splits_count": 0,
        "bnb_converted_usd": 0.0,
    }


def _set_bnb_converted_usd(abs_total_usd: float) -> None:
    """Update bnb_converted_usd field in status file to an absolute value (not delta)."""
    with _STATS_LOCK:
        st = _load_stats()
        st["bnb_converted_usd"] = float(abs_total_usd)
        STATS_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def make_client():
    """Create CCXT client for region (com/us) with rate limit & time sync."""
    Cls = ccxt.binanceus if BINANCE_REGION == "us" else ccxt.binance
    kwargs = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,
        },
    }
    if API_KEY and API_SECRET:
        kwargs["apiKey"] = API_KEY
        kwargs["secret"] = API_SECRET
    ex = Cls(kwargs)
    try:
        ex.load_markets()
    except Exception as e:
        print(f"[WARN] load_markets failed: {e}")
    return ex


# Global client used across functions
ex = make_client()


# === FIFO state management ===
def _init_state() -> Dict[str, Any]:
    return {"last_trade_id": None, "inventory": []}


def read_state() -> Dict[str, Any]:
    try:
        if STATE_FILE.exists():
            with STATE_FILE.open("r", encoding="utf-8") as f:
                j = json.load(f)
            if isinstance(j, dict):
                if "inventory" not in j or not isinstance(j["inventory"], list):
                    j["inventory"] = []
                return j
    except Exception as e:
        print(f"[WARN] failed reading state: {e}")
    return _init_state()


def write_state(s: Dict[str, Any]) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    tmp.replace(STATE_FILE)


# === Realized profit computation (including fees on both sides) ===
def realized_profit_on_match(buy_price: float, sell_price: float, qty: float,
                             fee_rate_each_side: float) -> float:
    gross = (sell_price - buy_price) * qty
    fees = (buy_price * qty + sell_price * qty) * fee_rate_each_side
    return float(gross - fees)


def fifo_match_sell(inventory: List[Dict[str, float]], sell_price: float, qty: float,
                    fee_rate_each_side: float) -> Tuple[float, float]:
    """
    Match a SELL against existing inventory (FIFO). Returns:
    - realized (USD)
    - matched qty (actual quantity sold from inventory)
    """
    remaining = qty
    realized = 0.0
    while remaining > 1e-12 and inventory:
        lot = inventory[0]
        take = min(remaining, lot["qty"])
        realized += realized_profit_on_match(lot["price"], sell_price, take, fee_rate_each_side)
        lot["qty"] -= take
        remaining -= take
        if lot["qty"] <= 1e-12:
            inventory.pop(0)
    matched = qty - remaining
    return realized, matched


# === Fetch trades from exchange (normalize/sort) ===
def normalize_trades(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(t):
        return (int(t.get("timestamp") or 0), str(t.get("id") or ""))
    trades = [t for t in trades if t and t.get("symbol") == PAIR]
    trades.sort(key=key)
    return trades


def fetch_trades_window(pair: str, since_ms: Optional[int] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    params = {"recvWindow": RECV_WINDOW}
    if since_ms is not None:
        params["startTime"] = since_ms
    try:
        ts0 = ex.milliseconds()
        trades = ex.fetch_my_trades(pair, limit=limit, params=params)
        trades = normalize_trades(trades)
        print(f"[FETCH] got {len(trades)} trades (since={since_ms}) in {ex.milliseconds()-ts0}ms")
        return trades
    except Exception as e:
        print(f"[ERROR] fetch_trades_window: {e}")
        return []


# === Process trade sequence to compute realized profit and "splits" ===
def process_trades_sequence(trades: List[Dict[str, Any]], st: Dict[str, Any],
                            fee_rate_each_side: float) -> Tuple[float, int, Optional[str]]:
    inv = st.get("inventory", [])
    realized_total = 0.0
    inc_sell_trades = 0  # Renamed from inc_splits for clarity - this counts sell trades, not actual profit splits
    last_id = st.get("last_trade_id")

    for t in trades:
        tid = str(t.get("id") or "")
        side = (t.get("side") or "").lower()
        price = float(t.get("price") or 0.0)
        amount = float(t.get("amount") or 0.0)
        if amount <= 0 or price <= 0:
            last_id = tid
            continue

        if side == "buy":
            inv.append({"qty": amount, "price": price})
        elif side == "sell":
            pnl, matched = fifo_match_sell(inv, price, amount, fee_rate_each_side)
            realized_total += pnl
            # Count all sell trades, not just those with matched inventory
            inc_sell_trades += 1

        last_id = tid

    st["inventory"] = inv
    return realized_total, inc_sell_trades, last_id


# === Helper: absolute sync of bnb_converted_usd from profit_split ===
def _sync_bnb_converted_from_split_state():
    try:
        split_state = split_read_state()  # reads profit_split state.json
        total_to_bnb = float(split_state.get("total_sent_to_bnb_usd", 0.0) or 0.0)
        _set_bnb_converted_usd(total_to_bnb)
        print(f"[SYNC] dashboard.bnb_converted_usd := {total_to_bnb:.2f}")
    except Exception as e:
        print(f"[SYNC][WARN] failed syncing bnb_converted_usd: {e}")


# === Backfill phase (optional) ===
def do_backfill(st: Dict[str, Any], since_ms: int) -> Dict[str, Any]:
    print(f"[BACKFILL] starting backfill from since_ms={since_ms}")
    trades = fetch_trades_window(PAIR, since_ms=since_ms, limit=1000)
    if not trades:
        print("[BACKFILL] no trades returned for backfill window.")
        return st

    realized, sell_trades, last_id = process_trades_sequence(trades, st, FEE_RATE_EACH_SIDE)
    print(f"[BACKFILL] processed {len(trades)} trades | realized={realized:.6f} | sell_trades={sell_trades}")

    # === Status + profit-split updates ===
    if abs(realized) > 1e-12 or sell_trades > 0:
    # Update dashboard (cumulative / count)
        add_realized_profit(realized, inc_sell_trades=sell_trades)
    # Update profit split mechanism (state.json + BNB purchase if needed)
        if realized > 0:
            try:
                handle_profit(realized, ex)
                print(f"[SPLIT] handle_profit(realized={realized:.6f}) done (backfill).")
            except Exception as e:
                print(f"[SPLIT][WARN] handle_profit failed (backfill): {e}")
            # Always sync accumulated BNB conversion to dashboard (even if no purchase executed)
            _sync_bnb_converted_from_split_state()

    st["last_trade_id"] = last_id
    write_state(st)
    return st


# === Live loop (polling) ===
def live_tail_loop(st: Dict[str, Any], interval_sec: int):
    # Bootstrap last_trade_id if missing
    if st.get("last_trade_id") is None:
        recent = fetch_trades_window(PAIR, since_ms=None, limit=1)
        if recent:
            st["last_trade_id"] = str(recent[-1].get("id") or "")
            write_state(st)
            print(f"[INIT] bootstrap last_trade_id={st['last_trade_id']}")
        else:
            print("[INIT] no trades found to bootstrap; waiting for first trade...")

    while True:
        try:
            all_trades = fetch_trades_window(PAIR, since_ms=None, limit=500)
            if not all_trades:
                print("[LIVE] no trades returned; sleeping...")
                time.sleep(interval_sec)
                continue

            last_id = st.get("last_trade_id")
            new_trades = []
            for t in all_trades:
                tid = str(t.get("id") or "")
                if last_id is None or tid > str(last_id):
                    new_trades.append(t)

            if not new_trades:
                print(f"[LIVE] no new trades after id={last_id}; sleeping...")
                time.sleep(interval_sec)
                continue

            realized, sell_trades, new_last_id = process_trades_sequence(new_trades, st, FEE_RATE_EACH_SIDE)
            print(f"[LIVE] processed {len(new_trades)} new trades | realized={realized:.6f} | sell_trades={sell_trades}")

            # === Status + profit-split updates ===
            if abs(realized) > 1e-12 or sell_trades > 0:
                # Update dashboard (cumulative / count)
                add_realized_profit(realized, inc_sell_trades=sell_trades)
                # Update profit split mechanism (state.json + buy BNB if needed)
                if realized > 0:
                    try:
                        handle_profit(realized, ex)
                        print(f"[SPLIT] handle_profit(realized={realized:.6f}) done (live).")
                    except Exception as e:
                        print(f"[SPLIT][WARN] handle_profit failed (live): {e}")
                # Always sync cumulative amount converted to BNB to dashboard
                _sync_bnb_converted_from_split_state()

            st["last_trade_id"] = new_last_id or last_id
            write_state(st)

        except ccxt.AuthenticationError as e:
            print(f"[ERROR] Authentication failed: {e}; check API keys/permissions/region.")
            time.sleep(max(15, interval_sec))
        except Exception as e:
            print(f"[WARN] loop error: {e}")
            time.sleep(interval_sec)


# === CLI ===
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="process historical trades first")
    ap.add_argument("--since-ms", type=int, default=None, help="backfill since ms (UNIX ms)")
    ap.add_argument("--since-days", type=int, default=None, help="backfill last N days (if --backfill given)")
    ap.add_argument("--loop-interval", type=int, default=5, help="seconds between live polls")
    return ap.parse_args()


def main():
    if not API_KEY or not API_SECRET:
        raise SystemExit("Missing API keys. Set BINANCE_API_KEY / BINANCE_API_SECRET in ~/.env")

    args = parse_args()
    print(f"[INFO] profit_watcher: PAIR={PAIR} region=binance.{BINANCE_REGION} fee_each_side={FEE_RATE_EACH_SIDE}")

    st = read_state()

    if args.backfill:
        if args.since_ms is not None:
            since_ms = int(args.since_ms)
        else:
            days = args.since_days if args.since_days is not None else 3
            since_ms = int(ex.milliseconds() - days * 24 * 60 * 60 * 1000)
        st = do_backfill(st, since_ms)

    live_tail_loop(st, interval_sec=args.loop_interval)


if __name__ == "__main__":
    main()
