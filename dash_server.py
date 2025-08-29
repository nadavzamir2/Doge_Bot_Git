#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DOGE Grid Monitor Dashboard (single file)
- Light theme (LTR)
- Info cards (Bot Range + Layer spacing, Current Price, Total Profit, Splits, Converted to BNB)
- EXTRA profits cards (Realized/Unrealized/Grid/Fees/Profit %) — shown if present in runtime_stats.json
- Collapsible sections with small arrows; clicking the whole summary toggles
- Chart with grid layers (BUY=light green, SELL=light orange), dashed; nearest layers emphasized dynamically vs. current price
- Y axis ticks follow grid layer prices (full price, not shortened)
- Live price via SSE (/stream) and *live stats via SSE on file-change* of runtime_stats.json
- Persistent history via /history (saved to ~/doge_bot/data/price_history.json)
- /api/open_orders  ו-/api/order_history עם מיון/סינון בצד לקוח
- Local state for “Show grid layers” checkbox (localStorage)
"""

import os
import json
import time
import argparse
import webbrowser
import pathlib
import threading
from collections import deque
from datetime import datetime
from typing import Optional

from flask import Flask, Response, jsonify, request, render_template_string, make_response
from dotenv import load_dotenv
import ccxt

# =========================================================
# ENV & CONSTANTS
# =========================================================

ENV_FILE = os.path.expanduser("~/doge_bot/.env")
# Also try loading from current directory if the primary location doesn't exist
if not os.path.exists(ENV_FILE):
    ENV_FILE = ".env"
load_dotenv(ENV_FILE)

BINANCE_REGION = os.getenv("BINANCE_REGION", "com").strip().lower()  # 'com' or 'us'
API_KEY = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY") or ""
API_SECRET = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET") or ""
RECV_WINDOW = int(os.getenv("BINANCE_RECVWINDOW", "10000"))
PAIR = os.getenv("PAIR", "DOGE/USDT").strip()

def _env_float(name: str):
    v = os.getenv(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None

# Grid info for UI card (optional)
GRID_MIN = _env_float("GRID_MIN")
GRID_MAX = _env_float("GRID_MAX")
GRID_STEP_PCT = _env_float("GRID_STEP_PCT")

# Initial investment amounts
BASE_ORDER_USD = _env_float("BASE_ORDER_USD") or 0.0
MAX_USD_FOR_CYCLE = _env_float("MAX_USD_FOR_CYCLE") or 0.0
SPLIT_CHUNK_USD = _env_float("SPLIT_CHUNK_USD") or 4.0

# Profit split trigger fallback from env (if not in stats file)
SPLIT_TRIGGER_ENV = (
    _env_float("PROFIT_SPLIT_TRIGGER_USD")
    or _env_float("SPLIT_TRIGGER_USD")
    or _env_float("PROFIT_TRIGGER_USD")
    or 0.0
)

DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "price_history.json"
STATS_FILE = DATA_DIR / "runtime_stats.json"

MAX_HISTORY = int(os.getenv("DASH_MAX_HISTORY", "10000"))  # max points kept in RAM/UI
PRICE_WINDOW = deque([], maxlen=MAX_HISTORY)
HISTORY_LOCK = threading.Lock()

# =========================================================
# CCXT CLIENT (public for price, private only if keys exist)
# =========================================================

def make_client():
    if BINANCE_REGION == "us":
        Cls = ccxt.binanceus
    else:
        Cls = ccxt.binance
    kwargs = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            # חשוב: לא למשוך SAPI של מטבעות בעת load_markets (דורש הרשאות ומפיל חלק מהמשתמשים)
            "fetchCurrencies": False,
        },
    }
    if API_KEY and API_SECRET:
        kwargs["apiKey"] = API_KEY
        kwargs["secret"] = API_SECRET
    ex = Cls(kwargs)
    try:
        ex.load_markets()  # בלי פרמטרים (מונע -1104)
    except Exception as e:
        print(f"[WARN] load_markets failed: {e}")
    return ex

CLIENT = make_client()

# =========================================================
# HISTORY LOAD/SAVE
# =========================================================

def _load_history_file():
    try:
        if HISTORY_FILE.exists():
            with HISTORY_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for p in data[-MAX_HISTORY:]:
                    if isinstance(p, dict) and "t" in p and "p" in p:
                        PRICE_WINDOW.append({"t": int(p["t"]), "p": float(p["p"])})
    except Exception as e:
        print(f"[WARN] failed loading history file: {e}")

def _save_history_file():
    try:
        with HISTORY_FILE.open("w", encoding="utf-8") as f:
            json.dump(list(PRICE_WINDOW), f, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] failed saving history file: {e}")

def _read_stats_file():
    # אם הבוט שלך כותב לכאן, הדשבורד יציג; אחרת יוצגו אפסים.
    try:
        if STATS_FILE.exists():
            with STATS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[WARN] read stats failed: {e}")
    return {
        "cumulative_profit_usd": 0.0,
        "splits_count": 0,
        "bnb_converted_usd": 0.0,
        # ערכים אופציונליים לרווחים נוספים:
        "realized_profit_usd": 0.0,
        "unrealized_profit_usd": 0.0,
        "grid_profit_usd": 0.0,
        "fees_usd": 0.0,
        "profit_pct": 0.0,
        # אפשרות שגם הטריגר ייכתב ע"י הבוט:
        "split_trigger_usd": SPLIT_TRIGGER_ENV,
        # לחלופין גם total_profit_usd אם קיים:
        "total_profit_usd": 0.0,
    }

_load_history_file()

# =========================================================
# LIVE PRICE & LIVE STATS (SSE)
# =========================================================

_current_price = None
_current_ts_ms = None
_sse_stop = threading.Event()

_stats_mtime = None
_stats_cache = None

def record_price_point(price: float, ts_ms: Optional[int] = None):
    """Append price point to history (memory + disk) and update current."""
    global _current_price, _current_ts_ms
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    pt = {"t": int(ts_ms), "p": float(price)}
    with HISTORY_LOCK:
        PRICE_WINDOW.append(pt)
        _save_history_file()
    _current_price = float(price)
    _current_ts_ms = int(ts_ms)

def _price_poller():
    """Fetch latest price every few seconds to keep chart moving (even without bot)."""
    while not _sse_stop.is_set():
        try:
            t = CLIENT.fetch_ticker(PAIR)
            price = t.get("last") or t.get("close") or t.get("bid") or t.get("ask")
            if price:
                record_price_point(price)
        except Exception:
            pass
        _sse_stop.wait(3.0)

def _load_stats_safely():
    global _stats_mtime, _stats_cache
    try:
        if not STATS_FILE.exists():
            return None
        m = STATS_FILE.stat().st_mtime
        if _stats_mtime is None or m != _stats_mtime:
            _stats_mtime = m
            _stats_cache = _read_stats_file()
        return _stats_cache
    except Exception:
        return None

def _sse_generator():
    """Server-Sent Events generator pushing the most recent price periodically + live stats on change."""
    last_sent_tick = None
    last_sent_stats_ver = None
    while not _sse_stop.is_set():
        # price ticks (every ~2s or when changes)
        if _current_price is not None:
            payload = {"t": _current_ts_ms or int(time.time() * 1000), "p": _current_price}
            js = json.dumps(payload, ensure_ascii=False)
            if js != last_sent_tick:
                yield f"event: tick\ndata: {js}\n\n"
                last_sent_tick = js

        # stats change event (after each trade the bot should update runtime_stats.json)
        stats = _load_stats_safely()
        if stats is not None:
            ver = f"{_stats_mtime}"
            if ver != last_sent_stats_ver:
                try:
                    split_trigger = float(stats.get("split_trigger_usd", SPLIT_TRIGGER_ENV) or 0.0)
                except Exception:
                    split_trigger = SPLIT_TRIGGER_ENV
                # קבע מדיניות רווח להצגה: total_profit_usd אם קיים, אחרת cumulative_profit_usd
                profit_live = stats.get("total_profit_usd", None)
                if profit_live is None:
                    profit_live = stats.get("cumulative_profit_usd", 0.0)
                sse_stats = {
                    "profit_usd": float(profit_live or 0.0),
                    "split_trigger_usd": float(split_trigger or 0.0),
                    "splits_count": int(stats.get("splits_count", 0) or 0),
                    "realized_profit_usd": float(stats.get("realized_profit_usd", 0.0) or 0.0),
                    "unrealized_profit_usd": float(stats.get("unrealized_profit_usd", 0.0) or 0.0),
                    "grid_profit_usd": float(stats.get("grid_profit_usd", 0.0) or 0.0),
                    "fees_usd": float(stats.get("fees_usd", 0.0) or 0.0),
                    "profit_pct": float(stats.get("profit_pct", 0.0) or 0.0),
                }
                yield f"event: stats\ndata: {json.dumps(sse_stats, ensure_ascii=False)}\n\n"
                last_sent_stats_ver = ver

        time.sleep(2)

# Start background poller
threading.Thread(target=_price_poller, name="price_poller", daemon=True).start()

# =========================================================
# FLASK APP + API
# =========================================================

app = Flask(__name__)

@app.get("/stream")
def stream():
    return Response(_sse_generator(), mimetype="text/event-stream")

@app.get("/history")
def history_endpoint():
    with HISTORY_LOCK:
        # If no real data, provide some test data for demonstration
        if not PRICE_WINDOW:
            import time
            now = int(time.time() * 1000)
            test_data = []
            # Generate test data around the grid boundaries (0.215000 and 0.250000)
            base_prices = [0.220000, 0.225000, 0.230000, 0.235000, 0.240000, 0.245000]
            for i, price in enumerate(base_prices):
                test_data.append({
                    "t": now - (len(base_prices) - i) * 60000,  # 1 minute intervals
                    "p": price
                })
            return {"data": test_data}
        return {"data": list(PRICE_WINDOW)}

@app.get("/api/initial_investments")
def api_initial_investments():
    """Get initial investment amounts from state.json and environment variables."""
    try:
        # Try multiple possible locations for state.json
        state_paths = [
            pathlib.Path("state.json"),  # Current directory
            pathlib.Path.home() / "doge_bot" / "data" / "state.json",  # Data directory
            DATA_DIR / "state.json"  # DATA_DIR location
        ]
        
        initial_doge = 0.0
        total_doge_usdt_value = 0.0  # USDT value at time of investment
        
        for state_file in state_paths:
            if state_file.exists():
                try:
                    with open(state_file, 'r') as f:
                        state = json.load(f)
                        # Calculate total DOGE from buy fills and their USDT value
                        buy_fills = state.get("buy_fills", {})
                        for fill in buy_fills.values():
                            amount = float(fill.get("amount", 0))
                            price = float(fill.get("price", 0))
                            initial_doge += amount
                            total_doge_usdt_value += amount * price
                    break  # Found and processed the file
                except Exception as e:
                    print(f"[WARN] Failed to read {state_file}: {e}")
                    continue
        
        return {
            "initial_usdt": float(MAX_USD_FOR_CYCLE or 0.0),
            "initial_doge": float(initial_doge),
            "initial_doge_usdt_value": float(total_doge_usdt_value),
        }
    except Exception as e:
        return {
            "initial_usdt": float(MAX_USD_FOR_CYCLE or 0.0),
            "initial_doge": 0.0,
            "initial_doge_usdt_value": 0.0,
            "error": str(e)
        }

@app.get("/api/stats")
def api_stats():
    stats = _read_stats_file()
    # החזר גם את כל סוגי הרווחים אם קיימים
    split_trigger = stats.get("split_trigger_usd", SPLIT_TRIGGER_ENV)
    return {
        "price": _current_price,
        "profit_usd": float(stats.get("total_profit_usd", stats.get("cumulative_profit_usd", 0.0)) or 0.0),
        "sell_trades_count": int(stats.get("sell_trades_count", stats.get("splits_count", 0)) or 0),  # Backward compatibility
        "actual_splits_count": int(stats.get("actual_splits_count", 0) or 0),
        "splits_count": int(stats.get("sell_trades_count", stats.get("splits_count", 0)) or 0),  # Legacy field for compatibility
        "bnb_converted_usd": float(stats.get("bnb_converted_usd", 0.0) or 0.0),

        "realized_profit_usd": float(stats.get("realized_profit_usd", 0.0) or 0.0),
        "unrealized_profit_usd": float(stats.get("unrealized_profit_usd", 0.0) or 0.0),
        "grid_profit_usd": float(stats.get("grid_profit_usd", 0.0) or 0.0),
        "fees_usd": float(stats.get("fees_usd", 0.0) or 0.0),
        "profit_pct": float(stats.get("profit_pct", 0.0) or 0.0),
        "total_profit_usd": float(stats.get("total_profit_usd", stats.get("cumulative_profit_usd", 0.0)) or 0.0),
        "split_trigger_usd": float(split_trigger or 0.0),
    }

def _auth_available():
    return bool(API_KEY and API_SECRET)

@app.get("/api/open_orders")
def api_open_orders():
    if not _auth_available():
        return {"ok": False, "error": "No API key/secret configured", "orders": []}
    try:
        orders = CLIENT.fetch_open_orders(PAIR, params={"recvWindow": RECV_WINDOW})
        out = []
        for o in orders:
            ts = o.get("timestamp") or o.get("datetime")
            if isinstance(ts, (int, float)):
                ts_iso = datetime.utcfromtimestamp(ts / 1000.0).isoformat() + "Z"
            else:
                ts_iso = str(ts)
            price = float(o.get("price") or 0)
            amount = float(o.get("amount") or 0)
            out.append({
                "time": ts_iso,
                "side": o.get("side"),
                "price": price,
                "amount": amount,
                "value_usdt": price * amount,
            })
        return {"ok": True, "orders": out}
    except Exception as e:
        return {"ok": False, "error": str(e), "orders": []}

@app.get("/api/order_history")
def api_order_history():
    if not _auth_available():
        return {"ok": False, "error": "No API key/secret configured", "orders": []}
    out = []
    try:
        orders = CLIENT.fetch_orders(PAIR, limit=50, params={"recvWindow": RECV_WINDOW})
        for o in orders:
            status = (o.get("status") or "").lower()
            if status not in ("closed", "filled", "canceled"):
                continue
            
            # Placement timestamp (when order was created)
            placement_ts = o.get("timestamp") or o.get("datetime")
            if isinstance(placement_ts, (int, float)):
                placement_ts_iso = datetime.utcfromtimestamp(placement_ts / 1000.0).isoformat() + "Z"
            else:
                placement_ts_iso = str(placement_ts)
            
            # Execution timestamp (when order was filled)
            # Try multiple fields: lastTradeTimestamp, info.updateTime, or fallback to placement time
            execution_ts = o.get("lastTradeTimestamp")
            if not execution_ts and o.get("info"):
                execution_ts = o.get("info", {}).get("updateTime")
            if not execution_ts:
                execution_ts = placement_ts  # Fallback to placement time
            
            if isinstance(execution_ts, (int, float)):
                execution_ts_iso = datetime.utcfromtimestamp(execution_ts / 1000.0).isoformat() + "Z"
            else:
                execution_ts_iso = str(execution_ts)
            
            price = float(o.get("price") or o.get("average") or 0)
            amount = float(o.get("amount") or o.get("filled") or 0)
            out.append({
                "time": placement_ts_iso,
                "execution_time": execution_ts_iso,
                "side": o.get("side"),
                "price": price,
                "amount": amount,
                "value_usdt": price * amount,
                "status": status,
            })
        return {"ok": True, "orders": out}
    except Exception:
        # fallback: trades
        try:
            trades = CLIENT.fetch_my_trades(PAIR, limit=50, params={"recvWindow": RECV_WINDOW})
            for t in trades:
                # For trades, timestamp is execution time, placement time is unknown
                execution_ts = t.get("timestamp") or t.get("datetime")
                if isinstance(execution_ts, (int, float)):
                    execution_ts_iso = datetime.utcfromtimestamp(execution_ts / 1000.0).isoformat() + "Z"
                else:
                    execution_ts_iso = str(execution_ts)
                
                # For trades, we don't have placement time, so mark as unavailable
                placement_ts_iso = "—"  # Unavailable for trades
                
                price = float(t.get("price") or 0)
                amount = float(t.get("amount") or 0)
                out.append({
                    "time": placement_ts_iso,
                    "execution_time": execution_ts_iso,
                    "side": t.get("side"),
                    "price": price,
                    "amount": amount,
                    "value_usdt": price * amount,
                    "status": "done",
                })
            return {"ok": True, "orders": out}
        except Exception as e2:
            return {"ok": False, "error": str(e2), "orders": []}

@app.post("/api/stop_bot")
def api_stop_bot():
    print("[API] stop bot requested")
    return {"ok": True}

@app.post("/api/resume_bot")
def api_resume_bot():
    print("[API] resume bot requested")
    return {"ok": True}

@app.post("/api/cancel_all_orders")
def api_cancel_all_orders():
    if not _auth_available():
        return {"ok": False, "error": "No API key/secret configured"}
    try:
        orders = CLIENT.fetch_open_orders(PAIR, params={"recvWindow": RECV_WINDOW})
        for o in orders:
            oid = o.get("id") or o.get("orderId") or o.get("order_id")
            if not oid:
                continue
            try:
                CLIENT.cancel_order(oid, PAIR, params={"recvWindow": RECV_WINDOW})
            except Exception as e:
                print(f"[WARN] cancel {oid} failed: {e}")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# =========================================================
# FULL UI (HTML) — LTR, LIGHT THEME, COLLAPSIBLE, GRID CHART
# =========================================================

HTML = r"""<!doctype html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>DOGE Grid Monitor</title>
<style>
  :root {
    --bg: #f7fafc;
    --fg: #1a202c;
    --muted: #4a5568;
    --card: #ffffff;
    --accent: #2b6cb0;
    --green: #2f855a;
    --red: #c53030;
    --grid: #e2e8f0;
  }
  body { margin:0; font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background: var(--bg); color: var(--fg); }
  .wrap { width: 90vw; margin: 24px auto; padding: 0 16px; height:90vh; }
  .topbar { display:flex; align-items:center; justify-content:space-between; }
  h1 { margin: 4px 0 16px; font-size: 22px; display:flex; align-items:center; gap:12px; }
  .last-update { font-size:14px; color:var(--muted); }
  .top-actions { display:flex; gap:6px; }
  .icon-btn { border:1px solid var(--grid); background:var(--card); border-radius:8px; padding:4px 6px; cursor:pointer; position: relative; }
  .icon-btn:hover { background:#f5f5f5; }
  /* Enhanced tooltip styling for buttons */
  .icon-btn[title]:hover::after {
    content: attr(title);
    position: absolute;
    bottom: -35px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(0, 0, 0, 0.8);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 11px;
    white-space: nowrap;
    z-index: 1000;
    pointer-events: none;
  }
  .cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--grid); border-radius:12px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); position: relative; }
  .card h3 { margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; }
  .card .v { font-size:20px; font-weight:700; }
  
  /* Tooltip styles for info boxes */
  .card[data-tooltip]:hover::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(0, 0, 0, 0.8);
    color: white;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
    white-space: nowrap;
    z-index: 1000;
    pointer-events: none;
    margin-bottom: 5px;
  }
  
  .card[data-tooltip]:hover::before {
    content: '';
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: rgba(0, 0, 0, 0.8);
    z-index: 1000;
    pointer-events: none;
  }
  
  /* Loading indicator styles */
  .loading-indicator {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid var(--muted);
    border-radius: 50%;
    border-top-color: var(--primary, #007bff);
    animation: spin 1s ease-in-out infinite;
    margin-left: 8px;
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  
  .hidden { display: none; }
  .subnote { font-size:12px; color:var(--muted); margin-top:4px; }
  .sections { display:grid; gap:12px; }
  details { background:var(--card); border:1px solid var(--grid); border-radius:12px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  details > summary { cursor:pointer; padding:12px 14px; font-weight:600; list-style:none; display:flex; align-items:center; gap:8px; user-select:none; }
  details > summary::before { content: '▸'; font-size:12px; color:var(--muted); transition: transform .15s ease; }
  details[open] > summary::before { transform: rotate(90deg); }
  .section-body { padding:12px 14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px; border-bottom:1px solid var(--grid); }
  th { color:var(--muted); font-weight:600; }
  .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; }
  .pill.buy { background:#e6fffa; color:#2c7a7b; }
  .pill.sell { background:#fff5f5; color:#c53030; }
  #chart { width:100%; height:420px; }

  .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
  .controls label { font-size:12px; color:var(--muted); }
  .controls select, .controls input[type="text"] {
    font-size:12px; padding:4px 6px; border:1px solid var(--grid); border-radius:8px; background:#fff;
  }
  
  /* Light yellow highlighting for better visibility */
  .highlight-order {
    background-color: rgba(255, 251, 125, 0.3) !important; /* Light yellow background */
    font-weight: bold !important; /* Bold text */
    border-left: 3px solid rgba(255, 193, 7, 0.8);
  }
  
  /* Purple grid boundary highlighting */
  .grid-boundary {
    color: #8b5cf6 !important;
  }
</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <h1>DOGE Grid Monitor — <span id="pair" class="mono"></span> <span id="lastUpdated" class="last-update">Last updated —</span></h1>
      <div class="top-actions">
        <button id="btnRefresh" class="icon-btn" title="Refresh all data (orders, history, stats)">🔄</button>
        <button id="btnStop" class="icon-btn" title="Stop the trading bot">⏹️</button>
        <button id="btnResume" class="icon-btn" title="Resume the trading bot">▶️</button>
        <button id="btnCancel" class="icon-btn" title="Cancel all open orders">❌</button>
      </div>
    </div>

    <!-- Top info cards -->
    <div class="cards">
      <!-- New info boxes for initial investments -->
      <div class="card" data-tooltip="Total USDT amount initially invested in the trading bot">
        <h3>Initial USDT Invested</h3>
        <div id="initialUsdtVal" class="v mono">—</div>
      </div>

      <div class="card" data-tooltip="Total DOGE amount initially invested and its equivalent USDT value at time of investment">
        <h3>Initial DOGE Invested</h3>
        <div id="initialDogeVal" class="v mono">—</div>
      </div>

      <!-- Bot Range card -->
      <div class="card">
        <h3>Bot Range</h3>
        <div id="rangeVal" class="v mono">—</div>
        <div class="subnote">Layer spacing: <span id="spacingVal">—</span>% • <span id="layersCountVal">—</span> layers</div>
      </div>

      <div class="card"><h3>Current Price</h3><div id="priceVal" class="v mono">—</div></div>

      <!-- Total Profit card with (profit/trigger) subnote -->
      <div class="card">
        <h3>Total Profit (USD)</h3>
        <div id="profitVal" class="v mono">—</div>
        <div class="subnote" id="profitTriggerNote">(— / 4.0$ chunk trigger)</div>
      </div>

      <div class="card"><h3>Sell Trades Count</h3><div id="sellTradesVal" class="v mono">—</div></div>
      <div class="card"><h3>Actual Splits Count</h3><div id="actualSplitsVal" class="v mono">—</div></div>
      <div class="card"><h3>Converted to BNB (USD)</h3><div id="bnbVal" class="v mono">—</div></div>
    </div>

    <!-- EXTRA profit cards (values only; לא נוגעים בשאר) -->
    <div class="cards">
      <div class="card"><h3>Realized Profit (USD)</h3><div id="profitRealizedVal" class="v mono">—</div></div>
      <div class="card"><h3>Unrealized Profit (USD)</h3><div id="profitUnrealizedVal" class="v mono">—</div></div>
      <div class="card"><h3>Grid Profit (USD)</h3><div id="profitGridVal" class="v mono">—</div></div>
      <div class="card"><h3>Fees (USD)</h3><div id="feesVal" class="v mono">—</div></div>
      <div class="card"><h3>Profit %</h3><div id="profitPctVal" class="v mono">—</div></div>
    </div>

    <div class="sections">
      <!-- Chart -->
      <details open id="chartBox">
        <summary>Price Chart</summary>
        <div class="section-body">
          <!-- Color Legend -->
          <div style="margin-bottom: 12px; padding: 8px; background: #f8f9fa; border-radius: 6px; font-size: 12px;">
            <strong>Chart Legend:</strong>
            <span style="margin-left: 12px;">
              <span style="display: inline-block; width: 12px; height: 2px; background: rgba(46, 204, 113, 0.6); margin-right: 4px;"></span>
              <span style="color: #2c7a7b;">Buy Orders</span>
            </span>
            <span style="margin-left: 12px;">
              <span style="display: inline-block; width: 12px; height: 2px; background: rgba(243, 156, 18, 0.6); margin-right: 4px;"></span>
              <span style="color: #c53030;">Sell Orders</span>
            </span>
            <span style="margin-left: 12px;">
              <span style="display: inline-block; width: 12px; height: 2px; background: rgba(139, 92, 246, 0.8); margin-right: 4px;"></span>
              <span style="color: #8b5cf6;">Grid Boundaries</span>
            </span>
            <span style="margin-left: 12px;">
              <span style="display: inline-block; width: 12px; height: 1px; background: #cccccc; margin-right: 4px;"></span>
              <span style="color: #666;">Gray Latitudes</span>
            </span>
          </div>
          <div style="display:flex;gap:12px;margin-bottom:8px;flex-wrap:wrap">
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showGrid" type="checkbox" checked/>
              <span>Show grid layers</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showActiveLayers" type="checkbox"/>
              <span>Show active layers</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showLat" type="checkbox" checked/>
              <span>Show gray latitudes</span>
            </label>
          </div>
          <div id="chart"></div>
        </div>
      </details>

      <!-- Open Orders -->
      <details open id="openBox">
        <summary>Open Orders <span id="openCount" class="mono" style="color:var(--muted)">(0)</span></summary>
        <div class="section-body">
          <!-- sort & filter controls -->
          <div class="controls">
            <label>Sort by
              <select id="openSortBy">
                <option value="time">Time</option>
                <option value="side">Side</option>
                <option value="price">Price</option>
                <option value="amount">Amount</option>
                <option value="value_usdt">Value</option>
              </select>
            </label>
            <label>Direction
              <select id="openSortDir">
                <option value="desc">Desc</option>
                <option value="asc">Asc</option>
              </select>
            </label>
            <label>Filter
              <input id="openFilter" type="text" placeholder="e.g. buy / sell" />
            </label>
          </div>

          <table id="openTbl">
            <thead><tr>
              <th>Time</th>
              <th>Side</th>
              <th class="mono">Price</th>
              <th class="mono">Amount (DOGE)</th>
              <th class="mono">Value (USDT)</th>
            </tr></thead>
            <tbody></tbody>
          </table>
          <div id="openNote" style="color:var(--muted);margin-top:6px;"></div>
        </div>
      </details>

      <!-- Orders History -->
      <details open id="histBox">
        <summary>Orders History <span id="histCount" class="mono" style="color:var(--muted)">(0)</span></summary>
        <div class="section-body">
          <!-- sort & filter controls -->
          <div class="controls">
            <label>Sort by
              <select id="histSortBy">
                <option value="time">Time</option>
                <option value="side">Side</option>
                <option value="status">Status</option>
                <option value="price">Price</option>
                <option value="amount">Amount</option>
                <option value="value_usdt">Value</option>
              </select>
            </label>
            <label>Direction
              <select id="histSortDir">
                <option value="desc">Desc</option>
                <option value="asc">Asc</option>
              </select>
            </label>
            <label>Filter
              <input id="histFilter" type="text" placeholder="e.g. buy / sell / filled" />
            </label>
          </div>

          <table id="histTbl">
            <thead><tr>
              <th>Time</th>
              <th>Execution Time</th>
              <th>Side</th>
              <th>Status</th>
              <th class="mono">Price</th>
              <th class="mono">Amount (DOGE)</th>
              <th class="mono">Value (USDT)</th>
            </tr></thead>
            <tbody></tbody>
          </table>
          <div id="histNote" style="color:var(--muted);margin-top:6px;"></div>
        </div>
      </details>
    </div>
  </div>

<script>
"use strict";

var showGridEl, showActiveEl, showLatEl;

const PAIR = {{ pair|tojson }};
const SPLIT_TRIGGER_ENV = {{ split_trigger_env|tojson }};
const SPLIT_CHUNK_USD = {{ split_chunk_usd|tojson }};
const BASE_ORDER_USD = {{ base_order_usd|tojson }};
const MAX_USD_FOR_CYCLE = {{ max_usd_for_cycle|tojson }};
document.getElementById('pair').textContent = PAIR;

/* range & spacing from server-side (env), if provided */
const GRID_MIN = {{ grid_min|tojson }};
const GRID_MAX = {{ grid_max|tojson }};
const GRID_STEP_PCT = {{ grid_step_pct|tojson }};

(function setRangeCard(){
  const r = document.getElementById('rangeVal');
  const s = document.getElementById('spacingVal');
  const l = document.getElementById('layersCountVal');
  if (GRID_MIN != null && GRID_MAX != null) {
    r.textContent = `${Number(GRID_MIN).toFixed(6).replace(/^\./, '0.')} – ${Number(GRID_MAX).toFixed(6).replace(/^\./, '0.')}`;
  } else {
    r.textContent = '—';
  }
  if (GRID_STEP_PCT != null) {
    s.textContent = String(Number(GRID_STEP_PCT));
    // Calculate number of layers
    if (GRID_MIN != null && GRID_MAX != null) {
      const levels = buildAllLevels();
      l.textContent = String(levels.length);
    } else {
      l.textContent = '—';
    }
  } else {
    s.textContent = '—';
    l.textContent = '—';
  }
})();

(function setInitialInvestments(){
  // Initial investments will be loaded via API in loadInitialInvestments()
  const usdtEl = document.getElementById('initialUsdtVal');
  const dogeEl = document.getElementById('initialDogeVal');
  
  if (usdtEl) usdtEl.textContent = '—';
  if (dogeEl) dogeEl.textContent = '—';
})();

/* helpers */
function pad2(n){ return n<10 ? '0'+n : ''+n; }
function fmt(n, d=6){
  if(n===null||n===undefined||isNaN(n)) return '—';
  let s = Number(n).toFixed(d);
  if (s.startsWith('.')) {
    s = '0' + s;
  } else if (s.startsWith('-.')) {
    s = s.replace('-.', '-0.');
  }
  return s;
}
function fmt2(n){ return fmt(n,2); }
function fmt0(n){ return (n==null)?'—':String(n); }

/* date/time: dd/mm/yyyy HH:MM:SS (24h) */
function fmtDateTimeLocal(s){
  const d = new Date(s);
  if (isNaN(d.getTime())) return '—';
  const day = pad2(d.getDate());
  const mon = pad2(d.getMonth()+1);
  const yr  = d.getFullYear();
  const hh  = pad2(d.getHours());
  const mm  = pad2(d.getMinutes());
  const ss  = pad2(d.getSeconds());
  return `${day}/${mon}/${yr} ${hh}:${mm}:${ss}`;
}

function updateLastUpdated(){
  const el = document.getElementById('lastUpdated');
  if(!el) return;
  const now = new Date();
  el.textContent = 'Last updated ' + fmtDateTimeLocal(now);
}

/* ===== Build full grid levels list (min → max) ===== */
function buildAllLevels(){
  if (GRID_MIN == null || GRID_MAX == null || GRID_STEP_PCT == null) return [];
  const min = Number(GRID_MIN), max = Number(GRID_MAX), step = Number(GRID_STEP_PCT)/100.0;
  if (!(min > 0) || !(max > min) || !(step > 0)) return [];
  const levels = [min];
  let p = min;
  const limit = 2000; // הגנה
  let guard = 0;
  while (guard++ < limit){
    const next = p * (1 + step);
    if (next > max * (1 + 1e-12)) break;
    levels.push(next);
    p = next;
    if (Math.abs(next - max) / max < 1e-10) break;
  }
  if (levels[levels.length-1] < max - 1e-12) levels.push(max);
  return levels;
}

/* ===== Choose nearest below/above levels for emphasis ===== */
function nearestBracket(levels, price){
  if (!levels.length || price == null || isNaN(price)) return {below:null, above:null};
  let below = null, above = null;
  for (let i=0; i<levels.length; i++){
    const y = levels[i];
    if (y <= price) below = y;
    if (y >= price){ above = y; break; }
  }
  return {below, above};
}

/* ===== Chart bootstrap guard ===== */
let _chartReady = false;

/* ===== Data validation functions ===== */
function isValidChartData(data) {
  if (!Array.isArray(data)) {
    console.warn('Chart data is not an array:', data);
    return false;
  }
  
  for (let i = 0; i < data.length; i++) {
    const point = data[i];
    if (!point || typeof point !== 'object') {
      console.warn('Invalid data point at index', i, ':', point);
      return false;
    }
    if (!('t' in point) || !('p' in point)) {
      console.warn('Data point missing required fields (t, p) at index', i, ':', point);
      return false;
    }
    if (typeof point.p !== 'number' || isNaN(point.p)) {
      console.warn('Invalid price value at index', i, ':', point.p);
      return false;
    }
  }
  return true;
}

function sanitizeChartData(data) {
  if (!Array.isArray(data)) return [];
  
  return data.filter(point => {
    return point && 
           typeof point === 'object' && 
           't' in point && 
           'p' in point && 
           typeof point.p === 'number' && 
           !isNaN(point.p) &&
           isFinite(point.p);
  });
}

function showChartError(message) {
  console.error('Chart error:', message);
  const chartEl = document.getElementById('chart');
  if (chartEl) {
    chartEl.innerHTML = `<div style="padding: 20px; text-align: center; color: var(--muted); border: 1px dashed #ccc; background: #f9f9f9;">
      <p style="margin: 0; font-size: 14px;">⚠️ Chart Error</p>
      <p style="margin: 5px 0 0 0; font-size: 12px;">${message}</p>
    </div>`;
  }
}


/* ===== Loading state management ===== */
const cardLoadingStates = new Set();
const initializedCards = new Set();

function setLoadingState(id) {
  cardLoadingStates.add(id);
  const el = document.getElementById(id);
  if (el) {
    el.innerHTML = '<span class="loading-indicator"></span>';
  }
}

function clearLoadingState(id) {
  cardLoadingStates.delete(id);
  initializedCards.add(id);
}

function isCardLoading(id) {
  return cardLoadingStates.has(id);
}

function isCardInitialized(id) {
  return initializedCards.has(id);
}

/* ===== Update profits cards ===== */
function setText(id, val, digits=2){
  const el = document.getElementById(id);
  if (!el) return;
  
  // Clear loading state since we're setting data (even if null)
  clearLoadingState(id);
  
  // For Total Profit card, always show the data even if it's null/undefined
  if (id === 'profitVal') {
    if (val === null || val === undefined || isNaN(val)) {
      el.textContent = '0.00';
    } else {
      el.textContent = digits === 0 ? String(Math.round(val)) : Number(val).toFixed(digits);
    }
  } else {
    if (val === null || val === undefined || isNaN(val)) {
      el.textContent = '—';
    } else {
      // Real value (including real zeros)
      el.textContent = digits === 0 ? String(Math.round(val)) : Number(val).toFixed(digits);
    }
  }
}

function updateProfitWithTrigger(profit, actualSplitsCount){
  const el = document.getElementById('profitTriggerNote');
  if (!el) return;
  const p = (profit==null || isNaN(profit)) ? null : Number(profit);
  // Use SPLIT_CHUNK_USD from environment variable
  const chunkAmount = SPLIT_CHUNK_USD || 4.0;
  
  if (p === null) {
    el.textContent = `(— / ${chunkAmount}$ chunk trigger)`;
  } else {
    el.textContent = `(${p.toFixed(2)} / ${chunkAmount}$ chunk trigger)`;
  }
}

/* ===== Initialize loading states for all cards ===== */
function initializeCardLoadingStates() {
  // Set loading indicators for all data cards that should show loading initially
  const cardIds = [
    'priceVal', 'profitVal', 'sellTradesVal', 'actualSplitsVal', 'bnbVal',
    'profitRealizedVal', 'profitUnrealizedVal', 'profitGridVal', 'feesVal', 'profitPctVal'
  ];
  
  cardIds.forEach(id => {
    // Only set loading for cards that haven't been initialized yet
    if (!isCardInitialized(id)) {
      setLoadingState(id);
    }
  });
}

/* ===== stats (polling fallback) ===== */
async function loadStats(){
  try{
    const r = await fetch('/api/stats');
    const j = await r.json();
    
    // Handle price separately since it uses a different format
    if('price' in j && j.price !== null) {
      const priceEl = document.getElementById('priceVal');
      if (priceEl) {
        clearLoadingState('priceVal');
        priceEl.textContent = fmt(j.price, 6);
      }
    } else {
      // Price is null/missing
      setText('priceVal', null, 6);
    }
    
    setText('profitVal', j.profit_usd, 2);
    setText('sellTradesVal', j.sell_trades_count, 0);
    setText('actualSplitsVal', j.actual_splits_count, 0);
    setText('bnbVal', j.bnb_converted_usd, 2);

    // EXTRA profits - pass the actual values (including nulls)
    setText('profitRealizedVal', j.realized_profit_usd, 2);
    setText('profitUnrealizedVal', j.unrealized_profit_usd, 2);
    setText('profitGridVal', j.grid_profit_usd, 2);
    setText('feesVal', j.fees_usd, 2);
    setText('profitPctVal', j.profit_pct, 2);

    updateProfitWithTrigger(j.profit_usd ?? 0, j.actual_splits_count ?? 0);
    updateLastUpdated();
  }catch(e){}
}

/* ===== Load initial investments ===== */
async function loadInitialInvestments(){
  const usdtEl = document.getElementById('initialUsdtVal');
  const dogeEl = document.getElementById('initialDogeVal');
  
  // Show loading indicators after 2 seconds if data hasn't loaded yet
  let loadingTimeout = setTimeout(() => {
    if (usdtEl && usdtEl.textContent === '—') {
      usdtEl.innerHTML = '— <span class="loading-indicator"></span>';
    }
    if (dogeEl && dogeEl.textContent === '—') {
      dogeEl.innerHTML = '— <span class="loading-indicator"></span>';
    }
  }, 2000);
  
  try{
    const r = await fetch('/api/initial_investments');
    const j = await r.json();
    
    // Clear loading timeout since we got data
    clearTimeout(loadingTimeout);
    
    if (usdtEl) {
      // Add $ symbol to USDT values
      if (j.initial_usdt > 0) {
        usdtEl.textContent = `$${Number(j.initial_usdt).toFixed(2)}`;
      } else {
        usdtEl.textContent = '—';
      }
    }
    
    if (dogeEl) {
      if (j.initial_doge > 0) {
        const dogeAmount = Number(j.initial_doge).toFixed(2);
        
        // Show USDT equivalent at time of investment if available
        let usdtEquivalent = '';
        if (j.initial_doge_usdt_value && j.initial_doge_usdt_value > 0) {
          usdtEquivalent = `~$${j.initial_doge_usdt_value.toFixed(2)} at time of investment`;
        }
        
        if (usdtEquivalent) {
          dogeEl.innerHTML = `${dogeAmount} DOGE<div class="subnote">${usdtEquivalent}</div>`;
        } else {
          dogeEl.textContent = `${dogeAmount} DOGE`;
        }
      } else {
        dogeEl.textContent = '—';
      }
    }
  }catch(e){
    // Clear loading timeout on error
    clearTimeout(loadingTimeout);
    
    // Remove loading indicators and show error state
    if (usdtEl) usdtEl.textContent = '—';
    if (dogeEl) dogeEl.textContent = '—';
    
    console.warn('Failed to load initial investments:', e);
  }
}

/* ===== history + chart ===== */
async function loadHistory(){
  const chartEl = document.getElementById('chart');
  if (!chartEl) {
    console.error('Chart element not found');
    return;
  }
  console.log('DEBUG: chartEl exists:', !!chartEl);

  try{
    console.log('Loading history data...');
    const r = await fetch('/history');
    
    if (!r.ok) {
      throw new Error(`HTTP ${r.status}: ${r.statusText}`);
    }
    
    const j = await r.json();
    
    // Validate response structure
    if (!j || typeof j !== 'object') {
      throw new Error('Invalid JSON response from /history');
    }
    
    const rawData = j.data || [];
    console.log('Raw history data points:', rawData.length);
    
    if (!isValidChartData(rawData)) {
      console.warn('Invalid chart data received, attempting to sanitize...');
    }
    
    const pts = sanitizeChartData(rawData);
    console.log('Sanitized data points:', pts.length);
    
    if (pts.length === 0) {
      console.warn('No valid data points after sanitization');
      showChartError('No historical data available');
      return;
    }

    const xs = pts.map(p => new Date(p.t));
    const ys = pts.map(p => p.p);

    // Validate that we have valid coordinates
    if (xs.some(x => isNaN(x.getTime())) || ys.some(y => !isFinite(y))) {
      throw new Error('Invalid time or price values in data');
    }

    const levels = buildAllLevels();
    const yTicksVals = levels;
  // Always show leading zero for y-axis ticks
  const yTicksText = levels.map(v => Number(v).toFixed(6).replace(/^\./, '0.'));

    const layout = {
      margin:{l:90,r:20,t:10,b:50},
      xaxis:{ 
        title: { text: 'Time', standoff: 25 },
        showgrid:false, zeroline:false,
        tickformat: "%d/%m<br><i style='font-size:0.8em'>(%H:00)</i>", hoverformat: "%d/%m/%Y %H:%M:%S" 
      },
      yaxis:{
        title:{
          text:'Price (USDT)',
          standoff: 40 // add space to prevent overlap
        },
        showgrid:false, zeroline:false,
        tickmode: (yTicksVals.length? 'array':'auto'),
        tickvals: (yTicksVals.length? yTicksVals: undefined),
        ticktext: (yTicksVals.length? yTicksText: undefined),
        hoverformat: ".6f"
      },
      paper_bgcolor:'rgba(0,0,0,0)',
      plot_bgcolor:'rgba(0,0,0,0)',
      shapes: []
    };
    const data = [{ x: xs, y: ys, mode:'lines', name: PAIR }];
    
  console.log('DEBUG: About to call Plotly.react. Data:', data, 'Layout:', layout);
  console.log('Creating chart with', data[0].x.length, 'data points');
  await Plotly.react('chart', data, layout, {displayModeBar:false});
    _chartReady = true;
    updateChart();
    updateLastUpdated();
    console.log('Chart loaded successfully');
    
  }catch(e){
    console.error('History load failed:', e);
    
    // Try to create an empty chart as fallback
    try{
      console.log('Creating fallback empty chart...');
      const levels = buildAllLevels();
      const yTicksVals = levels;
  const yTicksText = levels.map(v => Number(v).toFixed(6).replace(/^\./, '0.'));
      
      await Plotly.newPlot('chart',
        [{x:[], y:[], mode:'lines', name: PAIR}],
        { margin:{l:80,r:20,t:10,b:50},
          xaxis:{ 
            title: { text: 'Time', standoff: 25 },
            showgrid:false, tickformat:"%d/%m<br><i style='font-size:0.8em'>(%H:00)</i>", hoverformat:"%d/%m/%Y %H:%M:%S" 
          },
          yaxis:{
            title:{
              text:'Price (USDT)',
              standoff: 40
            },
            showgrid:false,
            tickmode: (yTicksVals.length? 'array':'auto'),
            tickvals: (yTicksVals.length? yTicksVals: undefined),
            ticktext: (yTicksVals.length? yTicksText: undefined),
            hoverformat: ".6f"
          },
          paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
          shapes: [] },
        { displayModeBar:false });
      _chartReady = true;
      updateChart();
      updateLastUpdated();
      console.log('Fallback empty chart created');
      
    }catch(fallbackError){
      console.error('Failed to create fallback chart:', fallbackError);
      showChartError(`Failed to load chart: ${e.message || 'Unknown error'}`);
    }
  }
}

/* ===== Chart Line and Tick Logic ===== */

// Helper to create a line shape for Plotly
function shapeForY(y, color, width, dash) {
    return {
        type: 'line', xref: 'paper', x0: 0, x1: 1,
        yref: 'y', y0: y, y1: y,
        line: { color, width, dash },
    };
}

// Main function to update chart lines and ticks based on the selected mode
async function updateChart() {
    if (!_chartReady) return;
    const chartEl = document.getElementById('chart');
    if (!chartEl || !chartEl.layout) return;

    const mode = localStorage.getItem('chartMode') || 'grid';
    const currentPrice = window.__currentPrice;

    let shapes = [];
    let yTicksVals = [];

    // Always add purple boundary lines
    if (GRID_MIN != null) {
        shapes.push(shapeForY(GRID_MIN, 'rgba(139, 92, 246, 0.8)', 2, 'solid'));
        yTicksVals.push(GRID_MIN);
    }
    if (GRID_MAX != null) {
        shapes.push(shapeForY(GRID_MAX, 'rgba(139, 92, 246, 0.8)', 2, 'solid'));
        yTicksVals.push(GRID_MAX);
    }

    if (mode === 'latitudes') {
        const numLines = 10; // Fixed number of intervals
        if (GRID_MIN != null && GRID_MAX != null && GRID_MAX > GRID_MIN) {
            const step = (GRID_MAX - GRID_MIN) / (numLines + 1);
            for (let i = 1; i <= numLines; i++) {
                const y = GRID_MIN + i * step;
                shapes.push(shapeForY(y, '#cccccc', 1, 'solid'));
                yTicksVals.push(y);
            }
        }
        
        // Check if purple boundary lines represent active layers and mark them with dashed lines
        const activeOrderPrices = new Set(OPEN_ORDERS_RAW.map(o => o.price));
        
        // Add dashed line markings for active purple lines
        if (GRID_MIN != null && activeOrderPrices.has(GRID_MIN)) {
            // Add dashed lines at top and bottom edges of purple line
            const offset = (GRID_MAX - GRID_MIN) * 0.001; // Small offset for visibility
            shapes.push(shapeForY(GRID_MIN + offset, 'rgba(139, 92, 246, 0.8)', 2, 'dash'));
            shapes.push(shapeForY(GRID_MIN - offset, 'rgba(139, 92, 246, 0.8)', 2, 'dash'));
        }
        
        if (GRID_MAX != null && activeOrderPrices.has(GRID_MAX)) {
            // Add dashed lines at top and bottom edges of purple line
            const offset = (GRID_MAX - GRID_MIN) * 0.001; // Small offset for visibility
            shapes.push(shapeForY(GRID_MAX + offset, 'rgba(139, 92, 246, 0.8)', 2, 'dash'));
            shapes.push(shapeForY(GRID_MAX - offset, 'rgba(139, 92, 246, 0.8)', 2, 'dash'));
        }
    } else if (mode === 'active') {
        const activeOrders = OPEN_ORDERS_RAW.map(o => o.price).sort((a, b) => a - b);
        const { below, above } = nearestBracket(activeOrders, currentPrice);

        for (const p of activeOrders) {
            const isNearest = (p === below || p === above);
            const color = isNearest ? 'rgba(255, 165, 0, 0.9)' : 'rgba(255, 165, 0, 0.4)';
            const width = isNearest ? 2.5 : 1.5;
            const dash = isNearest ? 'longdash' : 'dash';
            shapes.push(shapeForY(p, color, width, dash));
            yTicksVals.push(p);
        }
    } else { // 'grid' mode is the default
        const allLevels = buildAllLevels();
        for (const y of allLevels) {
            if (y === GRID_MIN || y === GRID_MAX) continue;
            const isBuy = (y <= (currentPrice ?? 0));
            const color = isBuy ? 'rgba(46, 204, 113, 0.6)' : 'rgba(243, 156, 18, 0.6)';
            shapes.push(shapeForY(y, color, 1, 'dash'));
            yTicksVals.push(y);
        }
    }

    // Finalize ticks and update layout
    yTicksVals = [...new Set(yTicksVals)].sort((a, b) => a - b);
    let yTicksText = yTicksVals.map(v => fmt(v, 6));
    
    // For active layers mode, make nearest layer ticks bold and black
    if (mode === 'active') {
        const activeOrders = OPEN_ORDERS_RAW.map(o => o.price).sort((a, b) => a - b);
        const { below, above } = nearestBracket(activeOrders, currentPrice);
        
        yTicksText = yTicksVals.map(v => {
            const isNearest = (v === below || v === above);
            if (isNearest) {
                return `<b style="color: black">${fmt(v, 6)}</b>`;
            }
            return fmt(v, 6);
        });
    } else if (mode === 'grid') {
        // For grid mode, show exact grid tick numbers with consistent formatting
        const allLevels = buildAllLevels();
        yTicksText = yTicksVals.map(v => {
            // Just show the formatted value without [#] annotations
            return fmt(v, 6);
        });
    }

    // Add gray lines when price touches purple boundary lines (for all modes)
    if (currentPrice != null && (GRID_MIN != null || GRID_MAX != null)) {
        const tolerance = 0.000001; // Small tolerance for "touching"
        
        if (GRID_MIN != null && Math.abs(currentPrice - GRID_MIN) < tolerance) {
            // Price is touching GRID_MIN, add three gray lines below
            const spacing = (GRID_MAX - GRID_MIN) * 0.01; // 1% spacing
            for (let i = 1; i <= 3; i++) {
                const grayLineY = GRID_MIN - (spacing * i);
                shapes.push(shapeForY(grayLineY, '#999999', 1, 'solid'));
            }
        }
        
        if (GRID_MAX != null && Math.abs(currentPrice - GRID_MAX) < tolerance) {
            // Price is touching GRID_MAX, add three gray lines above
            const spacing = (GRID_MAX - GRID_MIN) * 0.01; // 1% spacing
            for (let i = 1; i <= 3; i++) {
                const grayLineY = GRID_MAX + (spacing * i);
                shapes.push(shapeForY(grayLineY, '#999999', 1, 'solid'));
            }
        }
    }

    Plotly.relayout('chart', {
        shapes: shapes,
        'yaxis.tickmode': 'array',
        'yaxis.tickvals': yTicksVals,
        'yaxis.ticktext': yTicksText,
    });
}

// Handles switching between chart modes
function setChartMode(mode) {
    showGridEl.checked = (mode === 'grid');
    showActiveEl.checked = (mode === 'active');
    showLatEl.checked = (mode === 'latitudes');
    localStorage.setItem('chartMode', mode);
    updateChart();
}

// This function will be called during initialization to set up event listeners
function setupChartControls() {
    showGridEl.addEventListener('change', () => { if(showGridEl.checked) setChartMode('grid'); });
    showActiveEl.addEventListener('change', () => { if(showActiveEl.checked) setChartMode('active'); });
    showLatEl.addEventListener('change', () => { if(showLatEl.checked) setChartMode('latitudes'); });

    // Restore saved mode on page load
    const savedMode = localStorage.getItem('chartMode') || 'grid';
    setChartMode(savedMode);
}

/* ====== SSE ====== */
window.__currentPrice = null;

function startSSE(){
  try{
    const es = new EventSource('/stream');

    // live price ticks
    es.addEventListener('tick', async ev=>{
      try{
        const j = JSON.parse(ev.data);
        
        // Validate tick data structure
        if (!j || typeof j !== 'object') {
          console.warn('Invalid tick data structure:', j);
          return;
        }
        
        if (typeof j.p !== 'number' || !isFinite(j.p)) {
          console.warn('Invalid price in tick data:', j.p);
          return;
        }
        
        if (!j.t) {
          console.warn('Missing timestamp in tick data:', j);
          return;
        }

        // Update price display
        const priceEl = document.getElementById('priceVal');
        if (priceEl) {
          clearLoadingState('priceVal');
          priceEl.textContent = fmt(j.p, 6);
        }
        
        const t = new Date(j.t);
        if (isNaN(t.getTime())) {
          console.warn('Invalid timestamp in tick data:', j.t);
          return;
        }
        
        window.__currentPrice = Number(j.p);

        const chartEl = document.getElementById('chart');
        if (!chartEl) {
          console.warn('Chart element not found for tick update');
          return;
        }

        if (!_chartReady){
          console.log('Chart not ready, initializing with tick data...');
          try {
            const levels = buildAllLevels();
            const yTicksVals = levels;
            const yTicksText = levels.map(v => Number(v).toFixed(6).replace(/^\./, '0.'));
            
            await Plotly.newPlot('chart',
              [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
              { margin:{l:80,r:20,t:10,b:50},
                xaxis:{ 
                  title: { text: 'Time', standoff: 25 },
                  showgrid:false, tickformat:"%d/%m<br><i style='font-size:0.8em'>(%H:00)</i>", hoverformat:"%d/%m/%Y %H:%M:%S" 
                },
                yaxis:{
                  title:{
                    text:'Price (USDT)',
                    standoff: 40
                  },
                  showgrid:false,
                  tickmode: (yTicksVals.length? 'array':'auto'),
                  tickvals: (yTicksVals.length? yTicksVals: undefined),
                  ticktext: (yTicksVals.length? yTicksText: undefined),
                  hoverformat: ".6f"
                },
                paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                shapes: [] },
              { displayModeBar:false });
            _chartReady = true;
            updateChart();
            updateLastUpdated();
            console.log('Chart initialized with tick data');
          } catch (initError) {
            console.error('Failed to initialize chart with tick data:', initError);
            showChartError(`Failed to initialize chart: ${initError.message || 'Unknown error'}`);
            return;
          }
        } else {
          // Try to extend existing chart
          try {
            Plotly.extendTraces('chart', {x:[[t]], y:[[j.p]]}, [0], 10000);
          } catch (extendError) {
            console.warn('Failed to extend traces, recreating chart:', extendError);
            
            // Fallback: recreate chart with new data point
            try{
              const levels = buildAllLevels();
              const yTicksVals = levels;
              const yTicksText = levels.map(v => fmt(v, 6));
              
              await Plotly.newPlot('chart',
                [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
                { margin:{l:80,r:20,t:10,b:50},
                  xaxis:{ 
                    title: { text: 'Time', standoff: 25 },
                    showgrid:false, tickformat:"%d/%m<br><i style='font-size:0.8em'>(%H:00)</i>", hoverformat:"%d/%m/%Y %H:%M:%S" 
                  },
                  yaxis:{
                    title:{
                      text:'Price (USDT)',
                      standoff: 40
                    },
                    showgrid:false,
                    tickmode: (yTicksVals.length? 'array':'auto'),
                    tickvals: (yTicksVals.length? yTicksVals: undefined),
                    ticktext: (yTicksVals.length? yTicksText: undefined),
                    hoverformat: ".6f"
                  },
                  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                  shapes: [] },
                { displayModeBar:false });
              _chartReady = true;
              updateChart();
              updateLastUpdated();
              console.log('Chart recreated successfully');
            }catch(recreateError){
              console.error('Failed to recreate chart:', recreateError);
              showChartError(`Chart update failed: ${recreateError.message || 'Unknown error'}`);
              return;
            }
          }
        }

        // Update chart lines based on new price
        updateChart();
        updateLastUpdated();
        
      }catch(e){
        console.error('Error processing tick event:', e);
      }
    });

    // live stats events (after file change)
    es.addEventListener('stats', ev=>{
      try{
        const s = JSON.parse(ev.data);
        // עדכון כרטיסי רווח
        setText('profitVal', s.profit_usd, 2);
        setText('sellTradesVal', s.sell_trades_count, 0);
        setText('actualSplitsVal', s.actual_splits_count, 0);
        setText('profitRealizedVal', s.realized_profit_usd, 2);
        setText('profitUnrealizedVal', s.unrealized_profit_usd, 2);
        setText('profitGridVal', s.grid_profit_usd, 2);
        setText('feesVal', s.fees_usd, 2);
        setText('profitPctVal', s.profit_pct, 2);

        const actualSplitsCount = (s.actual_splits_count!=null) ? s.actual_splits_count : 0;
        updateProfitWithTrigger(s.profit_usd ?? 0, actualSplitsCount);
        updateLastUpdated();
      }catch(e){}
    });

  }catch(e){}
}

/* ===== Open/History tables with counts & sort/filter ===== */
let OPEN_ORDERS_RAW = [];
let HIST_ORDERS_RAW = [];
window.__gridLevels = [];
window.__lower_bound = null;
window.__upper_bound = null;

function sortBy(arr, key, dir){
  const m = dir === 'asc' ? 1 : -1;
  return [...arr].sort((a,b)=>{
    let va = a[key], vb = b[key];
    if (key === 'time') { va = new Date(a.time).getTime(); vb = new Date(b.time).getTime(); }
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return -1*m;
    if (va > vb) return  1*m;
    return 0;
  });
}
function textFilter(arr, text){
  if (!text) return arr;
  const q = text.toLowerCase();
  return arr.filter(o =>
    (o.time||'').toLowerCase().includes(q) ||
    (o.side||'').toLowerCase().includes(q) ||
    String(o.price).toLowerCase().includes(q) ||
    String(o.amount).toLowerCase().includes(q) ||
    String(o.value_usdt).toLowerCase().includes(q) ||
    (o.status? String(o.status).toLowerCase().includes(q): false)
  );
}

function renderOpenOrders(){
  const tb = document.querySelector('#openTbl tbody'); tb.innerHTML='';
  const sortKey = document.getElementById('openSortBy').value;
  const sortDir = document.getElementById('openSortDir').value;
  const q = document.getElementById('openFilter').value.trim();

  let rows = textFilter(OPEN_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  document.getElementById('openCount').textContent = `(${rows.length})`;

  // Find nearest buy and sell orders to current price
  const currentPrice = window.__currentPrice;
  let nearestBuy = null, nearestSell = null;
  let nearestBuyDist = Infinity, nearestSellDist = Infinity;
  
  if (currentPrice && !isNaN(currentPrice)) {
    rows.forEach(o => {
      const price = parseFloat(o.price);
      if (!isFinite(price)) return;
      
      if (o.side === 'buy' && price <= currentPrice) {
        const dist = currentPrice - price;
        if (dist < nearestBuyDist) {
          nearestBuyDist = dist;
          nearestBuy = o;
        }
      } else if (o.side === 'sell' && price >= currentPrice) {
        const dist = price - currentPrice;
        if (dist < nearestSellDist) {
          nearestSellDist = dist;
          nearestSell = o;
        }
      }
    });
  }

  rows.forEach((o,idx)=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td class="mono">${fmt(o.price, 6)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    
    // Highlight nearest buy or sell order instead of first 2 rows
    if (o === nearestBuy || o === nearestSell) {
      tr.className = 'highlight-order';
    }
    
    tb.appendChild(tr);
  });
}

async function loadOpenOrders(){
  const note = document.getElementById('openNote');
  try{
    const r = await fetch('/api/open_orders');
    const j = await r.json();
    if(j.ok && Array.isArray(j.orders)){
      OPEN_ORDERS_RAW = j.orders;
      note.textContent = j.orders.length? '' : 'No open orders.';
      renderOpenOrders();
    }else{
      note.textContent = j.error || 'Auth required (API key/secret).';
      OPEN_ORDERS_RAW = [];
      renderOpenOrders();
    }
  }catch(e){
    note.textContent = 'Failed to load.';
    OPEN_ORDERS_RAW = [];
    renderOpenOrders();
  }
  updateLastUpdated();
}

function renderHistOrders(){
  const tb = document.querySelector('#histTbl tbody'); tb.innerHTML='';
  const sortKey = document.getElementById('histSortBy').value;
  const sortDir = document.getElementById('histSortDir').value;
  const q = document.getElementById('histFilter').value.trim();

  let rows = textFilter(HIST_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  document.getElementById('histCount').textContent = `(${rows.length})`;

  for(const o of rows){
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td>${fmtDateTimeLocal(o.execution_time || o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td>${o.status ?? '—'}</td>
      <td class="mono">${fmt(o.price, 6)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    tb.appendChild(tr);
  }
}

async function loadHistoryOrders(){
  const note = document.getElementById('histNote');
  try{
    const r = await fetch('/api/order_history');
    const j = await r.json();
    if(j.ok && Array.isArray(j.orders)){
      HIST_ORDERS_RAW = j.orders;
      note.textContent = j.orders.length? '' : 'No history to show.';
      renderHistOrders();
    }else{
      note.textContent = j.error || 'Auth required (API key/secret).';
      HIST_ORDERS_RAW = [];
      renderHistOrders();
    }
  }catch(e){
    note.textContent = 'Failed to load.';
    HIST_ORDERS_RAW = [];
    renderHistOrders();
  }
  updateLastUpdated();
}

/* wire controls + showGrid local state */
function wireControls(){
  function bindPersist(id, evt, handler){
    const el = document.getElementById(id);
    if(!el) return;
    const key = 'ui.'+id;
    try{
      const saved = localStorage.getItem(key);
      if(saved!==null) el.value = saved;
    }catch(_){}
    el.addEventListener(evt, ()=>{
      try{ localStorage.setItem(key, el.value); }catch(_){}
      handler();
    });
  }
  bindPersist('openSortBy','change', renderOpenOrders);
  bindPersist('openSortDir','change', renderOpenOrders);
  bindPersist('openFilter','input', renderOpenOrders);
  bindPersist('histSortBy','change', renderHistOrders);
  bindPersist('histSortDir','change', renderHistOrders);
  bindPersist('histFilter','input', renderHistOrders);

  // Setup chart view controls
  setupChartControls();

  const refreshBtn = document.getElementById('btnRefresh');
  if(refreshBtn) refreshBtn.addEventListener('click', ()=>{
    loadStats(); loadOpenOrders(); loadHistoryOrders(); loadHistory(); updateLastUpdated();
  });
  const stopBtn = document.getElementById('btnStop');
  if(stopBtn) stopBtn.addEventListener('click', ()=>{ fetch('/api/stop_bot', {method:'POST'}); });
  const resumeBtn = document.getElementById('btnResume');
  if(resumeBtn) resumeBtn.addEventListener('click', ()=>{ fetch('/api/resume_bot', {method:'POST'}); });
  const cancelBtn = document.getElementById('btnCancel');
  if(cancelBtn) cancelBtn.addEventListener('click', ()=>{ fetch('/api/cancel_all_orders', {method:'POST'}); });

  // Persist collapsible states for dashboard components
  function persistCollapsibleState(){
    ['chartBox', 'openBox', 'histBox'].forEach(id => {
      const element = document.getElementById(id);
      if (!element) return;
      
      const key = `ui.${id}.open`;
      // Restore state
      try {
        const saved = localStorage.getItem(key);
        if (saved !== null) {
          element.open = JSON.parse(saved);
        }
      } catch(_) {}
      
      // Save state on toggle
      element.addEventListener('toggle', () => {
        try {
          localStorage.setItem(key, JSON.stringify(element.open));
        } catch(_) {}
      });
    });
  }
  
  persistCollapsibleState();

  renderOpenOrders();
  renderHistOrders();
}

async function boot(){
  showGridEl = document.getElementById('showGrid');
  showActiveEl = document.getElementById('showActiveLayers');
  showLatEl = document.getElementById('showLat');
  wireControls();
  
  // Initialize loading states for cards that start with dashes
  initializeCardLoadingStates();
  
  await loadStats();
  await loadInitialInvestments();
  await loadHistory();    // טוען היסטוריה לפני הזרם
  startSSE();             // ואז סטרים חי למחיר + סטטיסטיקות
  await loadOpenOrders();
  await loadHistoryOrders();
  // רענונים תקופתיים (fallback)
  setInterval(loadStats, 15000);
  setInterval(loadInitialInvestments, 30000); // Refresh initial investments every 30s
  setInterval(loadOpenOrders, 20000);
  setInterval(loadHistoryOrders, 25000);
}

document.addEventListener('DOMContentLoaded', boot);
</script>

</body>
</html>"""

@app.get("/")
def index():
    html_content = render_template_string(
        HTML,
        pair=PAIR,
        grid_min=GRID_MIN,
        grid_max=GRID_MAX,
        grid_step_pct=GRID_STEP_PCT,
        split_trigger_env=SPLIT_TRIGGER_ENV,
        split_chunk_usd=SPLIT_CHUNK_USD,
        base_order_usd=BASE_ORDER_USD,
        max_usd_for_cycle=MAX_USD_FOR_CYCLE,
    )
    response = make_response(html_content)
    # Add cache-busting headers
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# =========================================================
# MAIN
# =========================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}/"
    if args.open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    print(f"* Serving Flask on {url}")
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        _sse_stop.set()

if __name__ == "__main__":
    main()