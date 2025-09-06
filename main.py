#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean orchestrator for DOGE grid trading bot (refactored)."""

import logging
import signal
import sys
import time
from typing import Dict, Any

from config import (
    POLL_SECONDS, TRADING_PAIR, get_config_summary,
    validate_required_config, MODE
)
from dogebot.exchange import create_client
from dogebot.grid import load_market_precision
from dogebot.state import load_state
from dogebot.orders import bootstrap_buys, process_fills
from dogebot.stats import write_stats, load_existing, build_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("doge_grid_bot")


def _create_exchange():
    missing = validate_required_config()
    if missing:
        raise ValueError(f"Missing config: {', '.join(missing)}")
    ex = create_client()
    log.info("Exchange client ready (mode=%s)", MODE)
    return ex


def setup_trading_environment():
    """
    Set up the trading environment by creating exchange client and loading market info.
    
    Returns:
        Tuple of (exchange client, market info dictionary)
        
    Raises:
        ValueError: If setup fails
    """
    # Create exchange client
    try:
        exchange = _create_exchange()
        market_info = load_market_precision(exchange, TRADING_PAIR)
    except Exception as e:
        log.error("Environment setup failed: %s", e)
        raise ValueError(str(e))
    
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
    
    return exchange, market_info


def setup_signal_handlers() -> callable:
    """
    Set up signal handlers for graceful shutdown.
    
    Returns:
        Function that returns True when shutdown is requested
    """
    stop_flag = False
    
    def signal_handler(signum, frame):
        nonlocal stop_flag
        log.info("Received signal %s, shutting down gracefully...", signum)
        stop_flag = True
    
    def should_stop():
        return stop_flag
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    return should_stop


def log_startup_info():
    """Log startup information and configuration summary."""
    config_summary = get_config_summary()
    
    log.info("היי! ברוכים הבאים לבוט הדוגקוין - Hi! Welcome to the DOGE Bot")
    log.info("Starting DOGE Grid Trading Bot")
    log.info("Mode: %s", config_summary["mode"])
    log.info("Environment: %s", config_summary["env_path"])
    log.info("Region: %s (class=%s)", config_summary["region"], config_summary["exchange_class"])
    log.info(
        "Pair=%s | Grid=%s (step=%.3f%%) | base_order_usd=%.2f | max_cycle=%.2f",
        config_summary["pair"],
        config_summary["grid_range"],
        config_summary["grid_step_pct"],
        config_summary["base_order_usd"],
        config_summary["max_cycle_usd"],
    )


def run_trading_bot() -> None:
    """
    Main trading bot execution function.

    Initializes the bot, sets up grid trading, and runs the main trading loop.
    """
    log_startup_info()
    
    # Set up trading environment
    try:
        exchange, market_info = setup_trading_environment()
    except ValueError:
        return

    # Load trading state
    state = load_state()

    # Bootstrap initial buy orders
    log.info("Bootstrapping initial orders (if conditions met)")
    bootstrap_buys(exchange, market_info, TRADING_PAIR)

    # Initial stats file
    existing = load_existing()
    stats = build_stats(state, state.get("realized_profit_usd", 0.0), 0, existing)
    write_stats(stats)

    # Set up signal handlers for graceful shutdown
    should_stop = setup_signal_handlers()

    # Main trading loop
    log.info("Starting main trading loop (poll interval: %d seconds)", POLL_SECONDS)

    while not should_stop():
        try:
            process_fills(exchange, market_info, TRADING_PAIR, state)
            # Stats update (open orders count recalculated inside build)
            try:
                open_orders = []
                try:
                    if MODE == "LIVE":
                        open_orders = exchange.fetch_open_orders(TRADING_PAIR)
                except Exception:
                    open_orders = []
                existing = load_existing()
                stats = build_stats(state, state.get("realized_profit_usd", 0.0), len(open_orders), existing)
                write_stats(stats)
            except Exception as e2:
                log.debug("Stats update failed: %s", e2)
        except Exception as e:
            log.error("Error in trading loop: %s", e)

        time.sleep(POLL_SECONDS)

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
