#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration module for the DOGE Grid Trading Bot.

This module centralizes all configuration management including:
- Environment variable loading
- Trading parameters
- API configuration
- File paths
- Default values

All configuration values are loaded once at import time and can be 
imported by other modules as needed.
"""

import os
import pathlib
from decimal import Decimal
from typing import Optional

from dotenv import load_dotenv


# ==================== ENVIRONMENT LOADING ====================

def _load_environment() -> str:
    """Load environment variables from .env file."""
    env_path = os.path.expanduser("~/doge_bot/.env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        return env_path
    else:
        load_dotenv()  # fallback to default .env
        return "(default)"


def _get_env_decimal(name: str, default: str) -> Decimal:
    """Get environment variable as Decimal with default."""
    return Decimal(os.getenv(name, default))


def _get_env_int(name: str, default: str) -> int:
    """Get environment variable as int with default."""
    return int(os.getenv(name, default))


def _get_env_float(name: str, default: Optional[str] = None) -> Optional[float]:
    """Get environment variable as float with optional default."""
    value = os.getenv(name)
    if value is None:
        return float(default) if default is not None else None
    try:
        return float(value)
    except (ValueError, TypeError):
        return float(default) if default is not None else None


# ==================== CONFIGURATION CONSTANTS ====================

# Load environment variables
ENV_PATH = _load_environment()

# Trading mode and region configuration
MODE = os.getenv("MODE", "LIVE").upper()  # LIVE / PAPER
REGION = os.getenv("BINANCE_REGION", "com").lower()  # com / us
RECV_WINDOW = _get_env_int("BINANCE_RECVWINDOW", "10000")

# API Keys - supports separate TRADE/READ keys or legacy combined keys
API_KEY = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")

# Trading pair configuration
TRADING_PAIR = os.getenv("PAIR", "DOGE/USDT")

# Grid trading parameters
GRID_LOW_PRICE = _get_env_decimal("GRID_LOW", "0.13")
GRID_HIGH_PRICE = _get_env_decimal("GRID_HIGH", "0.32")
GRID_STEP_PERCENT = _get_env_decimal("STEP_PCT", "1.0")  # Percentage between layers

# Dashboard grid parameters (for compatibility with dash_server.py)
GRID_MIN = _get_env_float("GRID_MIN") or _get_env_float("GRID_LOW")
GRID_MAX = _get_env_float("GRID_MAX") or _get_env_float("GRID_HIGH")
GRID_STEP_PCT = _get_env_float("GRID_STEP_PCT") or _get_env_float("STEP_PCT")

# Feature toggles
DISABLE_REGRID = os.getenv("DISABLE_REGRID", "0").strip() in ("1", "true", "True", "YES", "yes")

# Force dashboard tables to always use local JSON data instead of live exchange
FORCE_LOCAL_DATA = os.getenv("FORCE_LOCAL_DATA", "0").strip() in ("1","true","True","YES","yes")

# Order sizing and budget parameters
BASE_ORDER_USD = _get_env_decimal("BASE_ORDER_USD", "5.0")
MAX_CYCLE_USD = _get_env_decimal("MAX_CYCLE_USD", "40.0")
MAX_USD_FOR_CYCLE = _get_env_float("MAX_USD_FOR_CYCLE") or float(MAX_CYCLE_USD)

# Profit splitting configuration
SPLIT_CHUNK_USD = _get_env_float("SPLIT_CHUNK_USD", "4.0")
PROFIT_SPLIT_TRIGGER_USD = (
    _get_env_float("PROFIT_SPLIT_TRIGGER_USD")
    or _get_env_float("SPLIT_TRIGGER_USD")
    or _get_env_float("PROFIT_TRIGGER_USD")
    or 0.0
)

# Fee buffer to avoid MIN_NOTIONAL issues
FEE_BUFFER = _get_env_decimal("FEE_BUFFER", "0.001")  # 0.1% default

# Trading loop configuration
POLL_SECONDS = _get_env_int("POLL_SECONDS", "7")

# File paths
DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE_PATH = os.path.expanduser("~/doge_bot/state.json")
STATS_FILE_PATH = DATA_DIR / "runtime_stats.json"
HISTORY_FILE_PATH = DATA_DIR / "price_history.json"


# ==================== UTILITY FUNCTIONS ====================

def get_exchange_class() -> str:
    """Get the appropriate exchange class name based on region."""
    return "binanceus" if REGION == "us" else "binance"


def validate_required_config() -> list[str]:
    """Validate that required configuration is present.
    
    Returns:
        List of missing configuration items, empty if all required config is present.
    """
    missing = []
    
    if not API_KEY:
        missing.append("API_KEY (BINANCE_TRADE_KEY or BINANCE_API_KEY)")
    
    if not API_SECRET:
        missing.append("API_SECRET (BINANCE_TRADE_SECRET or BINANCE_API_SECRET)")
    
    if GRID_LOW_PRICE >= GRID_HIGH_PRICE:
        missing.append("GRID_LOW must be less than GRID_HIGH")
    
    if GRID_STEP_PERCENT <= 0:
        missing.append("STEP_PCT must be greater than 0")
    
    if BASE_ORDER_USD <= 0:
        missing.append("BASE_ORDER_USD must be greater than 0")
    
    if MAX_CYCLE_USD <= 0:
        missing.append("MAX_CYCLE_USD must be greater than 0")
    
    return missing


def get_config_summary() -> dict:
    """Get a summary of current configuration for logging."""
    return {
        "mode": MODE,
        "region": REGION,
        "pair": TRADING_PAIR,
        "grid_range": f"{float(GRID_LOW_PRICE):.6f} - {float(GRID_HIGH_PRICE):.6f}",
        "grid_step_pct": float(GRID_STEP_PERCENT),
        "base_order_usd": float(BASE_ORDER_USD),
        "max_cycle_usd": float(MAX_CYCLE_USD),
        "poll_seconds": POLL_SECONDS,
        "exchange_class": get_exchange_class(),
        "env_path": ENV_PATH,
    }