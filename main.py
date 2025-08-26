#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DOGE Grid Trading Bot - Main Module.

This module implements a grid trading strategy for DOGE/USDT on Binance.
The bot places buy orders below current price and sells them with a fixed profit margin
when they fill, implementing a dollar-cost averaging grid strategy.

Features:
- Grid-based trading with configurable parameters
- Automatic profit splitting between BNB conversion and reinvestment
- Support for both live and paper trading modes
- Comprehensive error handling and logging
- State persistence for reliable operation
"""

import json
import logging
import math
import os
import signal
import sys
import time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Dict, Optional

import ccxt
from dotenv import load_dotenv

# Optional profit splitting module
try:
    import profit_split
except ImportError:
    profit_split = None


# ==================== CONFIGURATION ====================

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("doge_grid_bot")

# Environment file loading
ENV_PATH = os.path.expanduser("~/doge_bot/.env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
else:
    load_dotenv()  # fallback to default .env

# Trading mode and region configuration
MODE = os.getenv("MODE", "LIVE").upper()  # LIVE / PAPER
REGION = os.getenv("BINANCE_REGION", "com").lower()  # com / us
RECV_WINDOW = int(os.getenv("BINANCE_RECVWINDOW", "10000"))

# API Keys - supports separate TRADE/READ keys or legacy combined keys
API_KEY = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")

# Trading pair configuration
TRADING_PAIR = os.getenv("PAIR", "DOGE/USDT")

# Grid trading parameters
GRID_LOW_PRICE = Decimal(os.getenv("GRID_LOW", "0.13"))
GRID_HIGH_PRICE = Decimal(os.getenv("GRID_HIGH", "0.32"))
GRID_STEP_PERCENT = Decimal(os.getenv("STEP_PCT", "1.0"))  # Percentage between layers

# Order sizing and budget parameters
BASE_ORDER_USD = Decimal(os.getenv("BASE_ORDER_USD", "5.0"))
MAX_CYCLE_USD = Decimal(os.getenv("MAX_CYCLE_USD", "40.0"))

# Fee buffer to avoid MIN_NOTIONAL issues
FEE_BUFFER = Decimal(os.getenv("FEE_BUFFER", "0.001"))  # 0.1% default

# State file path
STATE_FILE_PATH = os.path.expanduser("~/doge_bot/state.json")


# ==================== UTILITY FUNCTIONS ====================


def to_decimal(value) -> Decimal:
    """
    Convert a value to Decimal for precise arithmetic.

    Args:
        value: Value to convert (Decimal, int, float, or string)

    Returns:
        Decimal: Converted value
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


def load_trading_state() -> Dict[str, Any]:
    """
    Load the bot's trading state from file.

    Returns:
        Dict[str, Any]: Trading state with default values if file doesn't exist
    """
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Failed to load state file: %s", e)

    return {
        "processed_buys": [],  # List of processed buy order IDs
        "child_sells": {},  # buy_order_id -> sell_order_id mapping
        "buy_fills": {},  # buy_order_id -> fill data
        "sell_fills": {},  # sell_order_id -> fill data
        "realized_profit_usd": 0.0,  # Cumulative realized profit
    }


def save_trading_state(state: Dict[str, Any]) -> None:
    """
    Save the bot's trading state to file atomically.

    Args:
        state: Trading state dictionary to save
    """
    temp_file = STATE_FILE_PATH + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        os.replace(temp_file, STATE_FILE_PATH)
    except (IOError, OSError) as e:
        log.error("Failed to save state file: %s", e)


def create_exchange_client() -> ccxt.Exchange:
    """
    Create and configure a CCXT exchange client.

    Returns:
        ccxt.Exchange: Configured exchange client

    Raises:
        ValueError: If required API credentials are missing
    """
    if not API_KEY or not API_SECRET:
        raise ValueError("API key and secret are required")

    exchange_class = ccxt.binanceus if REGION == "us" else ccxt.binance

    return exchange_class(
        {
            "apiKey": API_KEY,
            "secret": API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
                "fetchCurrencies": False,  # Avoid signature requirements
            },
        }
    )


def load_market_precision(exchange: ccxt.Exchange, symbol: str) -> Dict[str, Any]:
    """
    Load market precision and trading limits for a symbol.

    Args:
        exchange: CCXT exchange client
        symbol: Trading symbol (e.g., 'DOGE/USDT')

    Returns:
        Dict[str, Any]: Market precision and limits information
    """
    markets = exchange.load_markets()
    market_info = markets[symbol]

    # Extract precision from market info
    price_precision = None
    amount_precision = None
    if "precision" in market_info:
        if "price" in market_info["precision"]:
            price_precision = Decimal(str(market_info["precision"]["price"]))
        if "amount" in market_info["precision"]:
            amount_precision = Decimal(str(market_info["precision"]["amount"]))

    # Extract precision from filters (more accurate)
    filters = market_info.get("info", {}).get("filters", [])
    price_tick = None
    amount_step = None
    min_notional = None

    for filter_info in filters:
        filter_type = filter_info.get("filterType")

        if filter_type == "PRICE_FILTER":
            tick_size = filter_info.get("tickSize")
            if tick_size:
                price_tick = Decimal(tick_size)
        elif filter_type == "LOT_SIZE":
            step_size = filter_info.get("stepSize")
            if step_size:
                amount_step = Decimal(step_size)
        elif filter_type in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = Decimal(str(filter_info.get("minNotional", "1")))

    # Use filter values if available, otherwise fall back to precision
    if price_tick:
        price_precision = price_tick
    if amount_step:
        amount_precision = amount_step
    if not min_notional:
        min_notional = Decimal("1.0")

    return {
        "price_tick": price_precision or Decimal("0.00001"),
        "amount_step": amount_precision or Decimal("1"),
        "min_cost": min_notional,
        "price_precision": price_precision or Decimal("0.00001"),
        "amount_precision": amount_precision or Decimal("1"),
    }


def round_price_down(price: Decimal, tick_size: Decimal) -> Decimal:
    """
    Round price down to the nearest tick size.

    Args:
        price: Price to round
        tick_size: Minimum price increment

    Returns:
        Decimal: Rounded price
    """
    if tick_size <= 0:
        return price
    quotient = (price / tick_size).to_integral_value(rounding=ROUND_FLOOR)
    return (quotient * tick_size).quantize(tick_size, rounding=ROUND_HALF_UP)


def round_amount_down(amount: Decimal, step_size: Decimal) -> Decimal:
    """
    Round amount down to the nearest step size.

    Args:
        amount: Amount to round
        step_size: Minimum amount increment

    Returns:
        Decimal: Rounded amount
    """
    if step_size <= 0:
        return amount
    quotient = (amount / step_size).to_integral_value(rounding=ROUND_FLOOR)
    return (quotient * step_size).quantize(step_size, rounding=ROUND_HALF_UP)


def generate_client_order_id(prefix: str) -> str:
    """
    Generate a unique client order ID.

    Args:
        prefix: Prefix for the order ID

    Returns:
        str: Unique client order ID
    """
    timestamp = int(time.time() * 1000) % 10_000_000
    return f"{prefix}-{timestamp}"


# ==================== ORDER EXECUTION ====================


def place_limit_buy_order(
    exchange: ccxt.Exchange,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    client_order_id: Optional[str] = None,
) -> str:
    """
    Place a limit buy order.

    Args:
        exchange: CCXT exchange client
        symbol: Trading symbol
        quantity: Quantity to buy
        price: Limit price
        client_order_id: Optional client order ID

    Returns:
        str: Order ID
    """
    params = {"recvWindow": RECV_WINDOW}
    if client_order_id:
        params["newClientOrderId"] = client_order_id

    if MODE == "LIVE":
        order = exchange.create_order(symbol, "limit", "buy", float(quantity), float(price), params)
        order_id = str(order["id"])
        log.info("(LIVE) Opened BUY %s @ %s | id=%s", quantity, price, order_id)
        return order_id
    else:
        order_id = "PAPER-" + (client_order_id or "B")
        log.info("(PAPER) BUY %s @ %s | id=%s", quantity, price, order_id)
        return order_id


def place_limit_sell_order(
    exchange: ccxt.Exchange,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    client_order_id: Optional[str] = None,
) -> str:
    """
    Place a limit sell order.

    Args:
        exchange: CCXT exchange client
        symbol: Trading symbol
        quantity: Quantity to sell
        price: Limit price
        client_order_id: Optional client order ID

    Returns:
        str: Order ID
    """
    params = {"recvWindow": RECV_WINDOW}
    if client_order_id:
        params["newClientOrderId"] = client_order_id

    if MODE == "LIVE":
        order = exchange.create_order(
            symbol, "limit", "sell", float(quantity), float(price), params
        )
        order_id = str(order["id"])
        log.info("(LIVE) Opened SELL %s @ %s | id=%s", quantity, price, order_id)
        return order_id
    else:
        order_id = "PAPER-" + (client_order_id or "S")
        log.info("(PAPER) SELL %s @ %s | id=%s", quantity, price, order_id)
        return order_id


# ==================== GRID TRADING LOGIC ====================


def compute_grid_levels(
    low_price: Decimal, high_price: Decimal, step_percent: Decimal
) -> list[Decimal]:
    """
    Compute grid trading levels between low and high prices.

    Args:
        low_price: Lower bound of grid
        high_price: Upper bound of grid
        step_percent: Percentage step between levels

    Returns:
        list[Decimal]: List of price levels
    """
    if low_price <= 0 or high_price <= 0 or high_price <= low_price:
        return []

    multiplier = Decimal("1.0") + (step_percent / Decimal("100"))
    levels = []
    current_price = low_price

    while current_price <= high_price + Decimal("1e-18"):
        levels.append(current_price)
        current_price = current_price * multiplier

    # Ensure upper bound is included
    if levels[-1] < high_price:
        levels.append(high_price)

    return levels


def bootstrap_buy_orders(
    exchange: ccxt.Exchange,
    market_info: Dict[str, Any],
    symbol: str,
    base_order_usd: Decimal,
    max_cycle_usd: Decimal,
) -> int:
    """
    Bootstrap initial buy orders below current market price.

    Args:
        exchange: CCXT exchange client
        market_info: Market precision and limits information
        symbol: Trading symbol
        base_order_usd: Base order size in USD
        max_cycle_usd: Maximum cycle budget in USD

    Returns:
        int: Number of orders placed
    """
    try:
        ticker = exchange.fetch_ticker(symbol)
        current_price = to_decimal(ticker["last"])
    except Exception as e:
        log.error("Failed to fetch ticker: %s", e)
        return 0

    grid_levels = compute_grid_levels(GRID_LOW_PRICE, GRID_HIGH_PRICE, GRID_STEP_PERCENT)

    # Filter levels below current price for buy orders
    buy_levels = [level for level in grid_levels if level <= current_price]
    buy_levels = list(reversed(buy_levels))  # Start with closest to current price

    budget_remaining = max_cycle_usd
    orders_placed = 0

    # Get available balance
    if MODE == "LIVE":
        try:
            balance = exchange.fetch_balance(params={"recvWindow": RECV_WINDOW})
            usdt_free = to_decimal(balance["free"].get("USDT", 0.0))
        except Exception as e:
            log.error("Failed to fetch balance: %s", e)
            return 0
    else:
        usdt_free = max_cycle_usd  # Unlimited for paper trading

    # Check if we have enough funds
    estimated_need = min(len(buy_levels), 7) * base_order_usd
    if usdt_free < min(estimated_need, budget_remaining):
        log.warning(
            "Insufficient USDT balance: %s. Need >= %s. Skipping order placement.",
            usdt_free,
            min(estimated_need, budget_remaining),
        )
        return 0

    # Place buy orders at grid levels
    for level in buy_levels[:7]:  # Limit to 7 orders to avoid flooding
        if budget_remaining < base_order_usd or usdt_free < base_order_usd:
            break

        quantity = round_amount_down(base_order_usd / level, market_info["amount_step"])
        price = round_price_down(level, market_info["price_tick"])

        # Ensure minimum notional value
        order_value = quantity * price
        if order_value < market_info["min_cost"]:
            # Increase quantity slightly to meet minimum
            required_quantity = (market_info["min_cost"] / price) * (Decimal("1.0") + FEE_BUFFER)
            quantity = round_amount_down(required_quantity, market_info["amount_step"])

        if quantity <= 0:
            continue

        client_order_id = generate_client_order_id("B")
        try:
            place_limit_buy_order(exchange, symbol, quantity, price, client_order_id)
            orders_placed += 1
            budget_remaining -= base_order_usd
            usdt_free -= quantity * price
        except Exception as e:
            log.error("Failed to place buy order: %s", e)

    log.info("Bootstrapped %d buy orders", orders_placed)
    return orders_placed


def handle_order_fills_and_create_sells(
    exchange: ccxt.Exchange, market_info: Dict[str, Any], symbol: str, state: Dict[str, Any]
) -> None:
    """
    Handle filled buy orders and create corresponding sell orders.
    Also handle filled sell orders to calculate realized profit.

    Args:
        exchange: CCXT exchange client
        market_info: Market precision and limits information
        symbol: Trading symbol
        state: Trading state dictionary
    """
    try:
        # Fetch recent orders (including filled/cancelled)
        orders = exchange.fetch_orders(symbol, limit=50)
    except Exception as e:
        log.error("Failed to fetch orders: %s", e)
        return

    # Process filled buy orders
    _process_filled_buy_orders(exchange, market_info, symbol, state, orders)

    # Process filled sell orders
    _process_filled_sell_orders(exchange, symbol, state, orders)


def _process_filled_buy_orders(
    exchange: ccxt.Exchange,
    market_info: Dict[str, Any],
    symbol: str,
    state: Dict[str, Any],
    orders: list,
) -> None:
    """Process filled buy orders and create corresponding sell orders."""
    for order in orders:
        if order.get("symbol") != symbol or order["side"] != "buy" or order["status"] != "closed":
            continue

        buy_order_id = str(order["id"])
        if buy_order_id in state["processed_buys"]:
            continue  # Already processed

        filled_amount = to_decimal(order.get("filled") or order.get("amount") or 0)
        average_price = to_decimal(order.get("average") or order.get("price") or 0)

        if filled_amount <= 0 or average_price <= 0:
            continue

        # Calculate sell target price
        target_price = round_price_down(
            average_price * (Decimal("1.0") + (GRID_STEP_PERCENT / Decimal("100"))),
            market_info["price_tick"],
        )

        # Calculate sell quantity (reduce slightly for fees)
        sell_quantity = round_amount_down(
            filled_amount * (Decimal("1.0") - FEE_BUFFER), market_info["amount_step"]
        )

        # Ensure minimum notional value
        order_value = sell_quantity * target_price
        if order_value < market_info["min_cost"]:
            required_quantity = (market_info["min_cost"] / target_price) * (
                Decimal("1.0") + FEE_BUFFER
            )
            sell_quantity = round_amount_down(required_quantity, market_info["amount_step"])

        if sell_quantity <= 0:
            continue

        sell_client_id = generate_client_order_id(f"S{buy_order_id[-4:]}")

        try:
            sell_order_id = place_limit_sell_order(
                exchange, symbol, sell_quantity, target_price, sell_client_id
            )

            # Update state
            state["processed_buys"].append(buy_order_id)
            state["child_sells"][buy_order_id] = sell_order_id
            state["buy_fills"][buy_order_id] = {
                "price": float(average_price),
                "amount": float(filled_amount),
            }
            save_trading_state(state)
        except Exception as e:
            log.error("Failed to create sell order for buy %s: %s", buy_order_id, e)


def _process_filled_sell_orders(
    exchange: ccxt.Exchange, symbol: str, state: Dict[str, Any], orders: list
) -> None:
    """Process filled sell orders and calculate realized profit."""
    for order in orders:
        if order.get("symbol") != symbol or order["side"] != "sell" or order["status"] != "closed":
            continue

        sell_order_id = str(order["id"])
        if sell_order_id in state["sell_fills"]:
            continue  # Already processed

        filled_amount = to_decimal(order.get("filled") or order.get("amount") or 0)
        average_price = to_decimal(order.get("average") or order.get("price") or 0)

        if filled_amount <= 0 or average_price <= 0:
            continue

        # Find parent buy order
        parent_buy_id = None
        for buy_id, sell_id in state["child_sells"].items():
            if sell_id == sell_order_id:
                parent_buy_id = buy_id
                break

        profit_usd = Decimal("0")
        if parent_buy_id and parent_buy_id in state["buy_fills"]:
            buy_data = state["buy_fills"][parent_buy_id]
            buy_price = to_decimal(buy_data["price"])

            # Calculate profit on the minimum shared quantity
            shared_quantity = min(filled_amount, to_decimal(buy_data["amount"]))
            profit_usd = (average_price - buy_price) * shared_quantity

        # Update state
        state["sell_fills"][sell_order_id] = {
            "price": float(average_price),
            "amount": float(filled_amount),
        }

        if profit_usd > 0:
            previous_profit = Decimal(str(state.get("realized_profit_usd", 0.0)))
            new_profit = previous_profit + profit_usd
            state["realized_profit_usd"] = float(new_profit)

            log.info(
                "Realized profit: +%.4f USDT (total=%.4f)", float(profit_usd), float(new_profit)
            )

            # Call profit splitting module if available
            try:
                if profit_split and hasattr(profit_split, "on_realized_profit"):
                    profit_split.on_realized_profit(exchange, float(profit_usd))
            except Exception as e:
                log.warning("Profit splitting failed: %s", e)

        save_trading_state(state)


# ==================== MAIN EXECUTION ====================


def run_trading_bot() -> None:
    """
    Main trading bot execution function.

    Initializes the bot, sets up grid trading, and runs the main trading loop.
    """
    log.info("Starting DOGE Grid Trading Bot")
    log.info("Mode: %s", MODE)
    log.info("Environment: %s", ENV_PATH if os.path.exists(ENV_PATH) else "(default)")
    log.info("Region: %s (class=%s)", REGION, "binanceus" if REGION == "us" else "binance")
    log.info(
        "Trade key prefix: %s…  secret prefix: %s…", (API_KEY or "")[:6], (API_SECRET or "")[:6]
    )
    log.info(
        "Pair=%s | Grid=%.6f..%.6f (step=%.3f%%) | base_order_usd=%.2f | max_cycle=%.2f",
        TRADING_PAIR,
        float(GRID_LOW_PRICE),
        float(GRID_HIGH_PRICE),
        float(GRID_STEP_PERCENT),
        float(BASE_ORDER_USD),
        float(MAX_CYCLE_USD),
    )

    try:
        exchange = create_exchange_client()
    except ValueError as e:
        log.error("Exchange client creation failed: %s", e)
        return

    # Load market precision and limits
    try:
        market_info = load_market_precision(exchange, TRADING_PAIR)
    except ccxt.AuthenticationError as e:
        log.error("Authentication error while loading markets: %s", e)
        return
    except Exception as e:
        log.error("Failed to load market precision: %s", e)
        return

    log.info(
        "Exchange info: %s",
        {
            "amount_precision": float(market_info["amount_precision"]),
            "price_precision": float(market_info["price_precision"]),
            "amount_step": float(market_info["amount_step"]),
            "price_tick": float(market_info["price_tick"]),
            "min_cost": float(market_info["min_cost"]),
        },
    )

    # Load trading state
    state = load_trading_state()

    # Bootstrap initial buy orders
    log.info("Starting with base_order_usd = %.1f", float(BASE_ORDER_USD))
    bootstrap_buy_orders(exchange, market_info, TRADING_PAIR, BASE_ORDER_USD, MAX_CYCLE_USD)

    # Set up signal handlers for graceful shutdown
    stop_flag = False

    def signal_handler(signum, frame):
        nonlocal stop_flag
        log.info("Received signal %s, shutting down gracefully...", signum)
        stop_flag = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Main trading loop
    poll_interval = int(os.getenv("POLL_SECONDS", "7"))
    log.info("Starting main trading loop (poll interval: %d seconds)", poll_interval)

    while not stop_flag:
        try:
            handle_order_fills_and_create_sells(exchange, market_info, TRADING_PAIR, state)
        except Exception as e:
            log.error("Error in trading loop: %s", e)

        time.sleep(poll_interval)

    log.info("Trading bot shutdown complete")


def main() -> None:
    """Main entry point."""
    try:
        run_trading_bot()
    except KeyboardInterrupt:
        log.info("Bot interrupted by user")
    except Exception as e:
        log.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
