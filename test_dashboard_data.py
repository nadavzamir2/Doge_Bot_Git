#!/usr/bin/env python3
"""Dashboard data integrity tests.

These tests exercise the Flask view functions in dash_server directly (without
starting an HTTP server) to validate that required fields are present and have
expected types / value domains. They rely on a temporary runtime_stats.json
written into the same location dash_server expects.
"""
from __future__ import annotations

import json
import pathlib
import types
import time
import importlib

# Ensure module import path
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

RUNTIME_STATS_PATH = pathlib.Path('data/runtime_stats.json')
RUNTIME_STATS_PATH.parent.mkdir(exist_ok=True)

# A representative stats payload the bot would write
_FAKE_STATS = {
    "cumulative_profit_usd": 12.34,
    "total_profit_usd": 12.34,
    "splits_count": 5,
    "sell_trades_count": 5,
    "actual_splits_count": 4,
    "bnb_converted_usd": 1.11,
    "realized_profit_usd": 10.0,
    "unrealized_profit_usd": 2.0,
    "grid_profit_usd": 8.0,
    "fees_usd": 0.66,
    "profit_pct": 3.21,
    "split_trigger_usd": 4.0,
}

# Write fake runtime stats file before importing dash_server so its first read finds data
with RUNTIME_STATS_PATH.open('w', encoding='utf-8') as f:
    json.dump(_FAKE_STATS, f)

# Import the dashboard module
import dash_server  # type: ignore

# Patch out network exchange interactions to make deterministic tests
class _DummyExchange:
    def fetch_open_orders(self, *a, **k):
        return [
            {"timestamp": int(time.time()*1000)-5000, "side": "buy", "price": 0.23, "amount": 100},
            {"timestamp": int(time.time()*1000)-3000, "side": "sell", "price": 0.245, "amount": 80},
        ]
    def fetch_orders(self, *a, **k):
        # Return closed/filled orders to populate history
        return [
            {"timestamp": int(time.time()*1000)-10000, "lastTradeTimestamp": int(time.time()*1000)-9500, "side": "buy", "status": "closed", "price": 0.22, "amount": 120},
            {"timestamp": int(time.time()*1000)-8000, "lastTradeTimestamp": int(time.time()*1000)-7500, "side": "sell", "status": "filled", "price": 0.24, "amount": 60},
        ]
    def fetch_ticker(self, *a, **k):
        return {"last": 0.235}
    def load_markets(self):
        return {}

# Force auth available so /api/open_orders & /api/order_history paths execute
import types as _types

dash_server.API_KEY = 'X'
dash_server.API_SECRET = 'Y'
dash_server.CLIENT = _DummyExchange()

# Provide deterministic current price for api_stats
# (simulate that SSE poller updated it)
dash_server._current_price = 0.235

def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)

REQUIRED_STATS_KEYS = {
    'profit_usd', 'sell_trades_count', 'splits_count', 'total_profit_usd', 'profit_pct'
}

OPTIONAL_STATS_KEYS = {
    'realized_profit_usd', 'unrealized_profit_usd', 'grid_profit_usd', 'fees_usd'
}

def test_api_stats_structure():
    data = dash_server.api_stats()
    missing = REQUIRED_STATS_KEYS - data.keys()
    assert not missing, f"Missing required keys: {missing} in {data}"
    # Basic sanity: numeric non-negative where expected
    assert _is_number(data['profit_usd'])
    assert _is_number(data['total_profit_usd'])
    assert data['sell_trades_count'] >= 0
    assert data['splits_count'] >= 0
    assert _is_number(data['profit_pct'])

    # Optional numeric fields if present
    for k in OPTIONAL_STATS_KEYS:
        if k in data:
            assert _is_number(data[k])


def test_api_open_orders():
    payload = dash_server.api_open_orders()
    assert payload['ok'] is True
    assert isinstance(payload['orders'], list) and payload['orders'], 'Expected at least one open order'
    sample = payload['orders'][0]
    for key in ('time', 'side', 'price', 'amount', 'value_usdt'):
        assert key in sample, f'Missing {key} in open order sample {sample}'


def test_api_order_history():
    payload = dash_server.api_order_history()
    assert payload['ok'] is True
    assert isinstance(payload['orders'], list) and payload['orders'], 'Expected at least one history order'
    sample = payload['orders'][0]
    for key in ('time', 'execution_time', 'side', 'status', 'price', 'amount', 'value_usdt'):
        assert key in sample, f'Missing {key} in history order sample {sample}'


def test_history_endpoint_minimum_structure():
    # Ensure we can call history endpoint and it returns dict with 'data' list
    resp = dash_server.history_endpoint()
    assert isinstance(resp, dict) and 'data' in resp
    assert isinstance(resp['data'], list)

if __name__ == '__main__':  # Manual run
    for fn in [test_api_stats_structure, test_api_open_orders, test_api_order_history, test_history_endpoint_minimum_structure]:
        fn()
    print('All dashboard data tests passed.')
