#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance API Key Diagnostic Tool for DOGE Trading Bot.

This module diagnoses Binance API keys for region, IP, and permission issues.
It performs comprehensive tests for public API access, read permissions,
and trading permissions while providing detailed error analysis and solutions.

Tests performed:
- Public API: market data and ticker retrieval (no authentication)
- Read API: balance fetching with authentication
- Trade API: balance fetching with trading permissions
- Error analysis with specific solution suggestions
"""

import argparse
import os
from typing import Optional

import ccxt
from dotenv import load_dotenv

try:
    import requests
except ImportError:
    requests = None


# Color codes for terminal output
COLOR_CODES = {
    "ok": "32",  # Green
    "warn": "33",  # Yellow
    "err": "31",  # Red
    "info": "36",  # Cyan
    "bold": "1",  # Bold
}


def colorize_text(text: str, color_type: str) -> str:
    """
    Apply ANSI color codes to text for terminal output.

    Args:
        text: Text to colorize
        color_type: Color type from COLOR_CODES keys

    Returns:
        str: Colorized text with ANSI escape codes
    """
    code = COLOR_CODES.get(color_type, "0")
    return f"\033[{code}m{text}\033[0m"


def get_key_prefix(api_key: Optional[str], length: int = 6) -> str:
    """
    Get a safe prefix of an API key for display purposes.

    Args:
        api_key: The API key to get prefix from
        length: Number of characters to include in prefix

    Returns:
        str: Safe prefix with ellipsis, empty string if no key
    """
    if not api_key:
        return ""
    return api_key[:length] + "…"


def fetch_public_ip() -> Optional[str]:
    """
    Fetch the public IP address for whitelist verification.

    Returns:
        Optional[str]: Public IP address or None if unavailable

    Note:
        Requires requests library. Returns None if not available or on error.
    """
    if not requests:
        return None

    try:
        response = requests.get("https://api.ipify.org", timeout=5)
        if response.ok:
            return response.text.strip()
    except Exception:
        pass

    return None


def create_exchange_client(
    exchange_class: type, api_key: Optional[str] = None, api_secret: Optional[str] = None
) -> ccxt.Exchange:
    """
    Create a CCXT exchange client with appropriate configuration.

    Args:
        exchange_class: CCXT exchange class (binance or binanceus)
        api_key: API key for authentication (optional for public access)
        api_secret: API secret for authentication (optional for public access)

    Returns:
        ccxt.Exchange: Configured exchange client
    """
    config = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,  # Avoid signature requirements
        },
    }

    if api_key and api_secret:
        config.update({"apiKey": api_key, "secret": api_secret})

    return exchange_class(config)


def suggest_solution(error: Exception, region: str, api_type: str) -> None:
    """
    Provide specific solution suggestions based on error type.

    Args:
        error: The exception that occurred
        region: Binance region (com/us)
        api_type: Type of API operation (READ/TRADE)
    """
    error_message = str(error)

    if "Invalid API-key, IP, or permissions" in error_message or "-2015" in error_message:
        suggestion = (
            f"→ {api_type}: Check BINANCE_REGION={region}, verify IP whitelist, "
            f"ensure Spot Trading permissions, and confirm key is not deleted."
        )
        print(colorize_text(suggestion, "info"))

    elif "Invalid Api-Key ID" in error_message or "-2008" in error_message:
        suggestion = (
            f"→ {api_type}: Key doesn't match region or is incorrect. "
            f"Verify binance.us vs binance.com and re-paste key to .env file."
        )
        print(colorize_text(suggestion, "info"))

    elif "recvWindow" in error_message:
        suggestion = "→ Try increasing BINANCE_RECVWINDOW (e.g., to 50000)."
        print(colorize_text(suggestion, "info"))

    else:
        suggestion = (
            "→ Check permissions (Read/Spot), region (com/us), and system clock. "
            "If needed, create new key with IP restrictions."
        )
        print(colorize_text(suggestion, "info"))


def run_diagnostics(args: argparse.Namespace) -> None:
    """
    Run comprehensive Binance API diagnostics.

    Args:
        args: Parsed command line arguments
    """
    # Load environment configuration
    env_path = os.path.expanduser(args.env)
    print(f"[INFO] Loading .env: {env_path} (exists={os.path.exists(env_path)})")
    load_dotenv(env_path)

    # Get configuration
    region = (args.region or os.getenv("BINANCE_REGION", "com")).strip().lower()
    exchange_class = ccxt.binanceus if region == "us" else ccxt.binance
    pair = args.pair or os.getenv("PAIR", "DOGE/USDT")
    recv_window = int(os.getenv("BINANCE_RECVWINDOW", "10000"))

    # Get API keys with fallback to legacy environment variables
    read_key = os.getenv("BINANCE_READ_KEY") or os.getenv("BINANCE_API_KEY")
    read_secret = os.getenv("BINANCE_READ_SECRET") or os.getenv("BINANCE_API_SECRET")
    trade_key = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY")
    trade_secret = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")

    # Display configuration info
    public_ip = fetch_public_ip()
    if public_ip:
        print(f"[INFO] Public IP (whitelist for TRADE): {public_ip}")

    print(f"[INFO] Region={region} ({exchange_class.__name__}) | Pair={pair}")
    print(f"[INFO] READ  key present? {bool(read_key)}  (prefix: {get_key_prefix(read_key)})")
    print(f"[INFO] TRADE key present? {bool(trade_key)} (prefix: {get_key_prefix(trade_key)})")

    test_results = {"public": False, "read": False, "trade": False}

    # Test 1: Public API access (no authentication required)
    print("\n--- Testing Public API Access ---")
    public_client = create_exchange_client(exchange_class)
    try:
        public_client.load_markets()
        ticker = public_client.fetch_ticker(pair)
        last_price = ticker.get("last")
        print(colorize_text(f"[OK] Public markets/ticker ✔  last={last_price}", "ok"))
        test_results["public"] = True
    except Exception as e:
        print(colorize_text(f"[ERR] Public error: {type(e).__name__}: {e}", "err"))

    # Test 2: Read API access (authentication required)
    print("\n--- Testing Read API Access ---")
    if read_key and read_secret:
        read_client = create_exchange_client(exchange_class, read_key, read_secret)
        try:
            # Reuse loaded markets to save API calls
            if hasattr(public_client, "markets"):
                read_client.markets = public_client.markets
                read_client.symbols = public_client.symbols

            balance = read_client.fetch_balance(params={"recvWindow": recv_window})
            usdt_free = balance.get("free", {}).get("USDT", 0.0)
            print(colorize_text(f"[OK] READ balance ✔  USDT free={usdt_free}", "ok"))
            test_results["read"] = True
        except Exception as e:
            print(colorize_text(f"[ERR] READ error: {type(e).__name__}: {e}", "err"))
            suggest_solution(e, region, "READ")
    else:
        print(colorize_text("[WARN] Missing BINANCE_READ_KEY/BINANCE_READ_SECRET in .env", "warn"))

    # Test 3: Trade API access (requires IP restriction + Spot permissions)
    print("\n--- Testing Trade API Access ---")
    if trade_key and trade_secret:
        trade_client = create_exchange_client(exchange_class, trade_key, trade_secret)
        try:
            # Reuse loaded markets to save API calls
            if hasattr(public_client, "markets"):
                trade_client.markets = public_client.markets
                trade_client.symbols = public_client.symbols

            balance = trade_client.fetch_balance(params={"recvWindow": recv_window})
            usdt_free = balance.get("free", {}).get("USDT", 0.0)
            print(colorize_text(f"[OK] TRADE balance ✔  USDT free={usdt_free}", "ok"))
            test_results["trade"] = True
        except Exception as e:
            print(colorize_text(f"[ERR] TRADE error: {type(e).__name__}: {e}", "err"))
            suggest_solution(e, region, "TRADE")
    else:
        print(
            colorize_text("[WARN] Missing BINANCE_TRADE_KEY/BINANCE_TRADE_SECRET in .env", "warn")
        )

    # Display summary and recommendations
    print("\n" + "-" * 70)
    print("Summary:", test_results)

    if not test_results["public"]:
        print(colorize_text("× Public API failed — check network/region/symbol.", "err"))
    if test_results["public"] and not test_results["read"]:
        print(
            colorize_text(
                "! READ failed — check read key/region/recvWindow/time/permissions.", "warn"
            )
        )
    if test_results["public"] and not test_results["trade"]:
        print(
            colorize_text(
                "! TRADE failed — usually IP restriction or missing Spot permissions or region mismatch.",
                "warn",
            )
        )


def main() -> None:
    """Main entry point for the diagnostic tool."""
    parser = argparse.ArgumentParser(
        description="Diagnose Binance API keys (region/IP/permissions)"
    )
    parser.add_argument("--env", default="~/doge_bot/.env", help="Path to .env file")
    parser.add_argument(
        "--pair", default=None, help="Trading pair (default from .env or DOGE/USDT)"
    )
    parser.add_argument(
        "--region", default=None, choices=["com", "us"], help="Override BINANCE_REGION"
    )

    args = parser.parse_args()
    run_diagnostics(args)


if __name__ == "__main__":
    main()
