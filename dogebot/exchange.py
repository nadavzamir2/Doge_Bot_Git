"""Exchange client creation and helpers."""
from __future__ import annotations
import ccxt
from typing import Any, Dict
from config import (
    API_KEY, API_SECRET, REGION, MODE, RECV_WINDOW
)

EXCHANGE_CLASS = ccxt.binanceus if REGION == "us" else ccxt.binance

def create_client() -> ccxt.Exchange:
    return EXCHANGE_CLASS({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,
        },
    })

__all__ = ["create_client", "RECV_WINDOW", "MODE"]
