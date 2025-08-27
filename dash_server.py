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

from flask import Flask, Response, jsonify, request, render_template_string
from dotenv import load_dotenv
import ccxt

# =========================================================
# ENV & CONSTANTS
# =========================================================

ENV_FILE = os.path.expanduser("~/doge_bot/.env")
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
        return {"data": list(PRICE_WINDOW)}

@app.get("/api/stats")
def api_stats():
    stats = _read_stats_file()
    # החזר גם את כל סוגי הרווחים אם קיימים
    split_trigger = stats.get("split_trigger_usd", SPLIT_TRIGGER_ENV)
    return {
        "price": _current_price,
        "profit_usd": float(stats.get("total_profit_usd", stats.get("cumulative_profit_usd", 0.0)) or 0.0),
        "splits_count": int(stats.get("splits_count", 0) or 0),
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
            ts = o.get("timestamp") or o.get("datetime")
            if isinstance(ts, (int, float)):
                ts_iso = datetime.utcfromtimestamp(ts / 1000.0).isoformat() + "Z"
            else:
                ts_iso = str(ts)
            price = float(o.get("price") or o.get("average") or 0)
            amount = float(o.get("amount") or o.get("filled") or 0)
            out.append({
                "time": ts_iso,
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
                ts = t.get("timestamp") or t.get("datetime")
                if isinstance(ts, (int, float)):
                    ts_iso = datetime.utcfromtimestamp(ts / 1000.0).isoformat() + "Z"
                else:
                    ts_iso = str(ts)
                price = float(t.get("price") or 0)
                amount = float(t.get("amount") or 0)
                out.append({
                    "time": ts_iso,
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
  .icon-btn { border:1px solid var(--grid); background:var(--card); border-radius:8px; padding:4px 6px; cursor:pointer; }
  .icon-btn:hover { background:#f5f5f5; }
  .cards { display:grid; grid-template-columns: repeat(5, minmax(160px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--grid); border-radius:12px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .card h3 { margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; }
  .card .v { font-size:20px; font-weight:700; }
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
</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <h1>DOGE Grid Monitor — <span id="pair" class="mono"></span> <span id="lastUpdated" class="last-update">Last updated —</span></h1>
      <div class="top-actions">
        <button id="btnRefresh" class="icon-btn" title="Refresh">🔄</button>
        <button id="btnStop" class="icon-btn" title="Stop bot">⏹️</button>
        <button id="btnResume" class="icon-btn" title="Resume">▶️</button>
        <button id="btnCancel" class="icon-btn" title="Cancel all orders">❌</button>
      </div>
    </div>

    <!-- Top info cards -->
    <div class="cards">
      <!-- Bot Range card -->
      <div class="card">
        <h3>Bot Range</h3>
        <div id="rangeVal" class="v mono">—</div>
        <div class="subnote">Layer spacing: <span id="spacingVal">—</span>%</div>
      </div>

      <div class="card"><h3>Current Price</h3><div id="priceVal" class="v mono">—</div></div>

      <!-- Total Profit card with (profit/trigger) subnote -->
      <div class="card">
        <h3>Total Profit (USD)</h3>
        <div id="profitVal" class="v mono">0.00</div>
        <div class="subnote" id="profitTriggerNote">(0.00 / 0.00)</div>
      </div>

      <div class="card"><h3>Splits Count</h3><div id="splitsVal" class="v mono">0</div></div>
      <div class="card"><h3>Converted to BNB (USD)</h3><div id="bnbVal" class="v mono">0.00</div></div>
    </div>

    <!-- EXTRA profit cards (values only; לא נוגעים בשאר) -->
    <div class="cards">
      <div class="card"><h3>Realized Profit (USD)</h3><div id="profitRealizedVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Unrealized Profit (USD)</h3><div id="profitUnrealizedVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Grid Profit (USD)</h3><div id="profitGridVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Fees (USD)</h3><div id="feesVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Profit %</h3><div id="profitPctVal" class="v mono">0.00</div></div>
    </div>

    <div class="sections">
      <!-- Chart -->
      <details open id="chartBox">
        <summary>Price Chart</summary>
        <div class="section-body">
          <div style="display:flex;gap:12px;margin-bottom:8px;flex-wrap:wrap">
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showGrid" type="checkbox" checked/>
              <span>Show grid layers</span>
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

const PAIR = {{ pair|tojson }};
const SPLIT_TRIGGER_ENV = {{ split_trigger_env|tojson }};
document.getElementById('pair').textContent = PAIR;

/* range & spacing from server-side (env), if provided */
const GRID_MIN = {{ grid_min|tojson }};
const GRID_MAX = {{ grid_max|tojson }};
const GRID_STEP_PCT = {{ grid_step_pct|tojson }};

(function setRangeCard(){
  const r = document.getElementById('rangeVal');
  const s = document.getElementById('spacingVal');
  if (GRID_MIN != null && GRID_MAX != null) {
    r.textContent = `${Number(GRID_MIN).toFixed(6)} – ${Number(GRID_MAX).toFixed(6)}`;
  } else {
    r.textContent = '—';
  }
  if (GRID_STEP_PCT != null) s.textContent = String(Number(GRID_STEP_PCT));
  else s.textContent = '—';
})();

/* helpers */
function pad2(n){ return n<10 ? '0'+n : ''+n; }
function fmt(n, d=5){ if(n===null||n===undefined||isNaN(n)) return '—'; return Number(n).toFixed(d); }
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

/* ===== Update profits cards ===== */
function setText(id, val, digits=2){
  const el = document.getElementById(id);
  if (!el) return;
  if (val === null || val === undefined || isNaN(val)) el.textContent = '0.00';
  else el.textContent = Number(val).toFixed(digits);
}

function updateProfitWithTrigger(profit, trigger){
  const el = document.getElementById('profitTriggerNote');
  if (!el) return;
  const p = (profit==null || isNaN(profit)) ? 0 : Number(profit);
  const t = (trigger==null || isNaN(trigger)) ? 0 : Number(trigger);
  el.textContent = `(${p.toFixed(2)} / ${t.toFixed(2)})`;
}

/* ===== stats (polling fallback) ===== */
async function loadStats(){
  try{
    const r = await fetch('/api/stats');
    const j = await r.json();
    if('price' in j) document.getElementById('priceVal').textContent = fmt(j.price, 6);
    document.getElementById('profitVal').textContent = fmt2(j.profit_usd);
    document.getElementById('splitsVal').textContent = fmt0(j.splits_count);
    document.getElementById('bnbVal').textContent = fmt2(j.bnb_converted_usd);

    // EXTRA profits
    setText('profitRealizedVal', j.realized_profit_usd ?? 0, 2);
    setText('profitUnrealizedVal', j.unrealized_profit_usd ?? 0, 2);
    setText('profitGridVal', j.grid_profit_usd ?? 0, 2);
    setText('feesVal', j.fees_usd ?? 0, 2);
    setText('profitPctVal', j.profit_pct ?? 0, 2);

    updateProfitWithTrigger(j.profit_usd ?? 0, j.split_trigger_usd ?? SPLIT_TRIGGER_ENV);
    updateLastUpdated();
  }catch(e){}
}

/* ===== history + chart ===== */
async function loadHistory(){
  const chartEl = document.getElementById('chart');
  if (!chartEl) {
    console.error('Chart element not found');
    return;
  }

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
    const yTicksText = levels.map(v => Number(v).toFixed(6));

    const layout = {
      margin:{l:50,r:20,t:10,b:40},
      xaxis:{ title:'Time', showgrid:false, zeroline:false,
              tickformat: "%d/%m", hoverformat: "%d/%m/%Y %H:%M:%S" },
      yaxis:{ title:'Price (USDT)', showgrid:false, zeroline:false,
              tickmode: (yTicksVals.length? 'array':'auto'),
              tickvals: (yTicksVals.length? yTicksVals: undefined),
              ticktext: (yTicksVals.length? yTicksText: undefined),
              hoverformat: ".6f" },
      paper_bgcolor:'rgba(0,0,0,0)',
      plot_bgcolor:'rgba(0,0,0,0)',
      shapes: []
    };
    const data = [{ x: xs, y: ys, mode:'lines', name: PAIR }];
    
    console.log('Creating chart with', data[0].x.length, 'data points');
    await Plotly.react('chart', data, layout, {displayModeBar:false});
    _chartReady = true;
    maybeAddGridLines();
    updateLastUpdated();
    console.log('Chart loaded successfully');
    
  }catch(e){
    console.error('History load failed:', e);
    
    // Try to create an empty chart as fallback
    try{
      console.log('Creating fallback empty chart...');
      const levels = buildAllLevels();
      const yTicksVals = levels;
      const yTicksText = levels.map(v => Number(v).toFixed(6));
      
      await Plotly.newPlot('chart',
        [{x:[], y:[], mode:'lines', name: PAIR}],
        { margin:{l:50,r:20,t:10,b:40},
          xaxis:{ title:'Time', showgrid:false, tickformat:"%d/%m", hoverformat:"%d/%m/%Y %H:%M:%S" },
          yaxis:{ title:'Price (USDT)', showgrid:false,
                  tickmode: (yTicksVals.length? 'array':'auto'),
                  tickvals: (yTicksVals.length? yTicksVals: undefined),
                  ticktext: (yTicksVals.length? yTicksText: undefined),
                  hoverformat: ".6f" },
          paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
          shapes: [] },
        { displayModeBar:false });
      _chartReady = true;
      maybeAddGridLines();
      updateLastUpdated();
      console.log('Fallback empty chart created');
      
    }catch(fallbackError){
      console.error('Failed to create fallback chart:', fallbackError);
      showChartError(`Failed to load chart: ${e.message || 'Unknown error'}`);
    }
  }
}

/* ===== Grid layers (BUY=light green, SELL=light orange) dynamic emphasis ===== */
function shapeForY(y, color, width, dash){
  return {
    type: 'line',
    xref: 'paper', x0: 0, x1: 1,
    yref: 'y', y0: y, y1: y,
    line: { color, width, dash },
  };
}

function applyGridTicks(){
  if (!_chartReady) return;
  
  try {
    const chartEl = document.getElementById('chart');
    if (!chartEl) {
      console.warn('Chart element not found for applyGridTicks');
      return;
    }
    
    const levels = buildAllLevels();
    const yTicksVals = levels;
    const yTicksText = levels.map(v => Number(v).toFixed(6));
    const rel = {
      'yaxis.tickmode': (yTicksVals.length? 'array':'auto'),
      'yaxis.tickvals': (yTicksVals.length? yTicksVals: null),
      'yaxis.ticktext': (yTicksVals.length? yTicksText: null),
    };
    Plotly.relayout('chart', rel);
  } catch (e) {
    console.error('Error applying grid ticks:', e);
  }
}

function addGridShapesDynamic(currentPrice, showAll){
  if (!_chartReady) return;
  
  try {
    const chartEl = document.getElementById('chart');
    if (!chartEl) {
      console.warn('Chart element not found for addGridShapesDynamic');
      return;
    }
    
    const fig = chartEl;
    const lay = fig._fullLayout || {};
    const shapes = [];

    const levels = buildAllLevels();
    if (!levels.length) {
      Plotly.relayout('chart', { shapes });
      return;
    }

    const thinBuy  = 'rgba(46, 204, 113, 0.25)';   // light green
    const thinSell = 'rgba(243, 156, 18, 0.25)';   // light orange
    const boldBuy  = 'rgba(46, 204, 113, 0.60)';   // emphasized
    const boldSell = 'rgba(243, 156, 18, 0.60)';

    // nearest lines around current price
    const {below, above} = nearestBracket(levels, currentPrice);

    for (const y of levels){
      if (!isFinite(y)) continue; // Skip invalid levels
      
      const isBuy = (currentPrice != null && !isNaN(currentPrice)) ? (y <= currentPrice) : false;
      const isEmph = (y === below) || (y === above);
      if (isEmph || showAll){
        const color = isBuy ? (isEmph ? boldBuy : thinBuy) : (isEmph ? boldSell : thinSell);
        const width = isEmph ? 2.5 : 1;
        shapes.push(shapeForY(y, color, width, 'dot'));
      }
    }

    Plotly.relayout('chart', { shapes });
    applyGridTicks();
  } catch (e) {
    console.error('Error adding grid shapes:', e);
  }
}

function maybeAddGridLines(){
  if (!_chartReady) return;
  
  try {
    const chartEl = document.getElementById('chart');
    if (!chartEl) {
      console.warn('Chart element not found for maybeAddGridLines');
      return;
    }
    
    const showGridEl = document.getElementById('showGrid');
    const showLatEl = document.getElementById('showLat');
    const cp = window.__currentPrice;
    const showAll = showGridEl ? showGridEl.checked : false;
    const showLat = showLatEl ? showLatEl.checked : false;
    
    Plotly.relayout('chart', { 'yaxis.showgrid': showLat, 'yaxis.gridcolor':'#cccccc' });
    addGridShapesDynamic(cp, showAll);
  } catch (e) {
    console.error('Error updating grid lines:', e);
  }
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
            const yTicksText = levels.map(v => Number(v).toFixed(6));
            
            await Plotly.newPlot('chart',
              [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
              { margin:{l:50,r:20,t:10,b:40},
                xaxis:{ title:'Time', showgrid:false, tickformat:"%d/%m", hoverformat:"%d/%m/%Y %H:%M:%S" },
                yaxis:{ title:'Price (USDT)', showgrid:false,
                        tickmode: (yTicksVals.length? 'array':'auto'),
                        tickvals: (yTicksVals.length? yTicksVals: undefined),
                        ticktext: (yTicksVals.length? yTicksText: undefined),
                        hoverformat: ".6f" },
                paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                shapes: [] },
              { displayModeBar:false });
            _chartReady = true;
            maybeAddGridLines();
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
              const yTicksText = levels.map(v => Number(v).toFixed(6));
              
              await Plotly.newPlot('chart',
                [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
                { margin:{l:50,r:20,t:10,b:40},
                  xaxis:{ title:'Time', showgrid:false, tickformat:"%d/%m", hoverformat:"%d/%m/%Y %H:%M:%S" },
                  yaxis:{ title:'Price (USDT)', showgrid:false,
                          tickmode: (yTicksVals.length? 'array':'auto'),
                          tickvals: (yTicksVals.length? yTicksVals: undefined),
                          ticktext: (yTicksVals.length? yTicksText: undefined),
                          hoverformat: ".6f" },
                  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                  shapes: [] },
                { displayModeBar:false });
              _chartReady = true;
              maybeAddGridLines();
              updateLastUpdated();
              console.log('Chart recreated successfully');
            }catch(recreateError){
              console.error('Failed to recreate chart:', recreateError);
              showChartError(`Chart update failed: ${recreateError.message || 'Unknown error'}`);
              return;
            }
          }
        }

        // עדכן הדגשת שכבות סביב המחיר הנוכחי
        maybeAddGridLines();
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
        setText('profitVal', s.profit_usd ?? 0, 2);
        setText('profitRealizedVal', s.realized_profit_usd ?? 0, 2);
        setText('profitUnrealizedVal', s.unrealized_profit_usd ?? 0, 2);
        setText('profitGridVal', s.grid_profit_usd ?? 0, 2);
        setText('feesVal', s.fees_usd ?? 0, 2);
        setText('profitPctVal', s.profit_pct ?? 0, 2);

        const trigger = (s.split_trigger_usd!=null) ? s.split_trigger_usd : SPLIT_TRIGGER_ENV;
        updateProfitWithTrigger(s.profit_usd ?? 0, trigger);
        updateLastUpdated();
      }catch(e){}
    });

  }catch(e){}
}

/* ===== Open/History tables with counts & sort/filter ===== */
let OPEN_ORDERS_RAW = [];
let HIST_ORDERS_RAW = [];

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

  rows.forEach((o,idx)=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td class="mono">${fmt(o.price, 6)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    if(idx < 2){ tr.style.backgroundColor = 'yellow'; tr.style.fontWeight = 'bold'; }
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

  const showGridEl = document.getElementById('showGrid');
  try{ const saved = localStorage.getItem('ui.showGrid'); if(saved!==null) showGridEl.checked = JSON.parse(saved)?true:false; }catch(_){}
  showGridEl.addEventListener('change', ()=>{
    try{ localStorage.setItem('ui.showGrid', JSON.stringify(showGridEl.checked)); }catch(_){}
    maybeAddGridLines();
  });

  const showLatEl = document.getElementById('showLat');
  try{ const savedL = localStorage.getItem('ui.showLat'); if(savedL!==null) showLatEl.checked = JSON.parse(savedL)?true:false; }catch(_){}
  showLatEl.addEventListener('change', ()=>{
    try{ localStorage.setItem('ui.showLat', JSON.stringify(showLatEl.checked)); }catch(_){}
    maybeAddGridLines();
  });

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

  renderOpenOrders();
  renderHistOrders();
  maybeAddGridLines();
}

async function boot(){
  wireControls();
  await loadStats();
  await loadHistory();    // טוען היסטוריה לפני הזרם
  startSSE();             // ואז סטרים חי למחיר + סטטיסטיקות
  await loadOpenOrders();
  await loadHistoryOrders();
  // רענונים תקופתיים (fallback)
  setInterval(loadStats, 15000);
  setInterval(loadOpenOrders, 20000);
  setInterval(loadHistoryOrders, 25000);
}

document.addEventListener('DOMContentLoaded', boot);
</script>

</body>
</html>"""

@app.get("/")
def index():
    return render_template_string(
        HTML,
        pair=PAIR,
        grid_min=GRID_MIN,
        grid_max=GRID_MAX,
        grid_step_pct=GRID_STEP_PCT,
        split_trigger_env=SPLIT_TRIGGER_ENV,
    )

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


    
