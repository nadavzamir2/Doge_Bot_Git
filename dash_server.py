#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DOGE Grid Monitor Dashboard (single file)
- Light theme (LTR)
- Info cards (Current Price, Total Profit, Splits, Converted to BNB)
- Collapsible sections: Chart, Open Orders, Orders History
- Live price via SSE (/stream)
- Persistent history via /history (saved to ~/doge_bot/data/price_history.json)
- Separate endpoints for open orders and order history (not the same data)

ADDITIONS in this version (as requested):
- Bot Range card + spacing
- Dates dd/mm/yyyy and 24h time everywhere
- Sort & Filter controls for Open Orders / Orders History + Reset buttons
- Counts in section titles (Open Orders / Orders History)
- Grid overlay on chart: BUY (light green) / SELL (light orange); closest layers emphasized
- Y-axis ticks: leading zero (e.g., 0.234), generated from grid layers + orders; refresh dynamically
- Persist collapsible open/close state across refresh (localStorage)
- Highlight edge rows in Open Orders (closest BUY below price & closest SELL above price)
- Stats card “Total Profit (USD)” shows “Since split: <accumulator> / <chunk>”
- New cards for BNB Pending (USD) and Reinvest Pool (USD)
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
from typing import Optional, Dict, Any, List

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

# Grid info for UI card
def _env_float(name: str):
    v = os.getenv(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None

GRID_MIN = _env_float("GRID_MIN")
GRID_MAX = _env_float("GRID_MAX")
GRID_STEP_PCT = _env_float("GRID_STEP_PCT")

# Split config for UI/API (profit_split.py)
SPLIT_CHUNK_USD = float(os.getenv("SPLIT_CHUNK_USD", "4.0"))

DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "price_history.json"
STATS_FILE = DATA_DIR / "runtime_stats.json"

# profit_split.py state.json lives alongside this file
SPLIT_STATE_PATH = pathlib.Path(__file__).with_name("state.json")

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
            # לא למשוך מטבעות ב-load_markets (יכול להפיל)
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

def _read_stats_file() -> Dict[str, Any]:
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
    }

def _read_split_state() -> Dict[str, float]:
    """
    קורא את state.json של profit_split.py.
    מחזיר:
      split_accumulator_usd, bnb_pending_usd, reinvest_pool_usd
    אם אין — מחזיר ערכי 0.0, לא מפיל API.
    """
    out = {
        "split_accumulator_usd": 0.0,
        "bnb_pending_usd": 0.0,
        "reinvest_pool_usd": 0.0,
    }
    try:
        if SPLIT_STATE_PATH.exists():
            with SPLIT_STATE_PATH.open("r", encoding="utf-8") as f:
                st = json.load(f)
            for k in out.keys():
                out[k] = float(st.get(k, 0.0) or 0.0)
    except Exception:
        pass
    return out

_load_history_file()

# =========================================================
# LIVE PRICE RECORDER (poll public ticker + SSE stream)
# =========================================================

_current_price = None
_current_ts_ms = None
_sse_stop = threading.Event()

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

def _sse_generator():
    """Server-Sent Events generator pushing the most recent price periodically."""
    last_sent = None
    while not _sse_stop.is_set():
        if _current_price is not None:
            payload = {"t": _current_ts_ms or int(time.time() * 1000), "p": _current_price}
            js = json.dumps(payload, ensure_ascii=False)
            if js != last_sent:
                yield f"event: tick\ndata: {js}\n\n"
                last_sent = js
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
    split = _read_split_state()
    return {
        "price": _current_price,
        "profit_usd": float(stats.get("cumulative_profit_usd", 0.0) or 0.0),
        "splits_count": int(stats.get("splits_count", 0) or 0),
        "bnb_converted_usd": float(stats.get("bnb_converted_usd", 0.0) or 0.0),
        "split_chunk_usd": float(SPLIT_CHUNK_USD),
        "split_accumulator_usd": float(split["split_accumulator_usd"]),
        "bnb_pending_usd": float(split["bnb_pending_usd"]),
        "reinvest_pool_usd": float(split["reinvest_pool_usd"]),
        # expose grid env too (unchanged behavior elsewhere)
        "grid_min": GRID_MIN,
        "grid_max": GRID_MAX,
        "grid_step_pct": GRID_STEP_PCT,
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
                "id": o.get("id") or o.get("order") or "",
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
                "id": o.get("id") or o.get("order") or "",
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
                    "id": t.get("id") or "",
                })
            return {"ok": True, "orders": out}
        except Exception as e2:
            return {"ok": False, "error": str(e2), "orders": []}

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

    --buyLine: rgba(56, 161, 105, .35);   /* light green */
    --sellLine: rgba(237, 137, 54, .35);  /* light orange */
    --buyEdge: rgba(56, 161, 105, .75);
    --sellEdge: rgba(237, 137, 54, .75);
    --edgeRowBg: #fffbe6;
  }
  body { margin:0; font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background: var(--bg); color: var(--fg); }
  .wrap { width: 90vw; max-width: 1800px; margin: 24px auto; padding: 0 16px; }
  h1 { margin: 4px 0 16px; font-size: 22px; }
  .cards { display:grid; grid-template-columns: repeat(4, minmax(180px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--grid); border-radius:12px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .card h3 { margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; }
  .card .v { font-size:20px; font-weight:700; }
  .subnote { font-size:12px; color:var(--muted); margin-top:4px; }

  .sections { display:grid; gap:12px; }
  details { background:var(--card); border:1px solid var(--grid); border-radius:12px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  details > summary { cursor:pointer; padding:12px 14px; font-weight:600; list-style:none; display:flex; align-items:center; gap:8px; }
  details > summary::-webkit-details-marker { display:none; }
  details[open] > summary { border-bottom:1px solid var(--grid); }
  .arrow { transition: transform .15s ease; display:inline-block; width:10px; height:10px; border-right:2px solid var(--muted); border-bottom:2px solid var(--muted); transform: rotate(-45deg); margin-inline-start: 0; }
  details[open] > summary .arrow { transform: rotate(45deg); }
  .section-body { padding:12px 14px; }

  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px; border-bottom:1px solid var(--grid); }
  th { color:var(--muted); font-weight:600; }
  .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; }
  .pill.buy { background:#e6fffa; color:#2c7a7b; }
  .pill.sell { background:#fff5f5; color:#c53030; }
  #chart { width:100%; height:460px; }

  .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
  .controls label { font-size:12px; color:var(--muted); }
  .controls select, .controls input[type="text"] {
    font-size:12px; padding:4px 6px; border:1px solid var(--grid); border-radius:8px; background:#fff;
  }
  .controls .btn {
    font-size:12px; padding:4px 8px; border:1px solid var(--grid); background:#fff; border-radius:8px; cursor:pointer;
  }

  /* highlight closest edge orders */
  .edge-row { background: var(--edgeRowBg); font-weight:600; }
</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div class="wrap">
    <h1>DOGE Grid Monitor — <span id="pair" class="mono"></span></h1>

    <!-- Top info cards -->
    <div class="cards">
      <!-- Bot Range card -->
      <div class="card">
        <h3>Bot Range</h3>
        <div id="rangeVal" class="v mono">—</div>
        <div class="subnote">Layer spacing: <span id="spacingVal">—</span>%</div>
      </div>

      <div class="card"><h3>Current Price</h3><div id="priceVal" class="v mono">—</div></div>

      <div class="card">
        <h3>Total Profit (USD)</h3>
        <div id="profitVal" class="v mono">0.00</div>
        <div class="subnote">Since split: <span class="mono" id="profitChunk">— / —</span></div>
      </div>

      <div class="card"><h3>Splits Count</h3><div id="splitsVal" class="v mono">0</div></div>
      <div class="card"><h3>Converted to BNB (USD)</h3><div id="bnbVal" class="v mono">0.00</div></div>

      <!-- NEW profit-related cards -->
      <div class="card"><h3>BNB Pending (USD)</h3><div id="bnbPendVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Reinvest Pool (USD)</h3><div id="rePoolVal" class="v mono">0.00</div></div>
    </div>

    <div class="sections">
      <!-- Chart -->
      <details id="sec-chart" open>
        <summary><span class="arrow"></span> Price Chart</summary>
        <div class="section-body">
          <div id="chart"></div>
        </div>
      </details>

      <!-- Open Orders -->
      <details id="sec-open" open>
        <summary><span class="arrow"></span> Open Orders <span id="openCount" class="mono" style="color:var(--muted)">(0)</span></summary>
        <div class="section-body">
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
            <button class="btn" id="openReset">Reset</button>
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
      <details id="sec-hist" open>
        <summary><span class="arrow"></span> Orders History <span id="histCount" class="mono" style="color:var(--muted)">(0)</span></summary>
        <div class="section-body">
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
            <button class="btn" id="histReset">Reset</button>
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
document.getElementById('pair').textContent = PAIR;

/* grid env to client */
const GRID_MIN = {{ grid_min|tojson }};
const GRID_MAX = {{ grid_max|tojson }};
const GRID_STEP_PCT = {{ grid_step_pct|tojson }};

/* helpers */
function pad2(n){ return n<10 ? '0'+n : ''+n; }
function fmt(n, d=5){ if(n===null||n===undefined||isNaN(n)) return '—'; return Number(n).toFixed(d); }
function fmt2(n){ return fmt(n,2); }
function fmt0(n){ return (n==null)?'—':String(n); }

/* dd/mm/yyyy HH:MM:SS (24h) */
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

/* Leading zero price string (e.g., 0.234000) */
function fmtPriceTick(v, dec=6){
  if (v == null || isNaN(v)) return '';
  return Number(v).toFixed(dec); // ensures "0.xyz"
}

/* Range card */
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

/* collapsible persist */
(function persistCollapsibles(){
  const ids = ['sec-chart','sec-open','sec-hist'];
  const key = 'collapsibleState.v1';
  let state = {};
  try{ state = JSON.parse(localStorage.getItem(key)||'{}'); }catch(_){}
  ids.forEach(id=>{
    const det = document.getElementById(id);
    if (!det) return;
    if (state[id] !== undefined){
      if (state[id]) det.setAttribute('open','');
      else det.removeAttribute('open');
    }
    det.addEventListener('toggle', ()=>{
      state[id] = det.open;
      try{ localStorage.setItem(key, JSON.stringify(state)); }catch(_){}
    });
  });
})();

/* Chart + grid overlay state */
let _chartReady = false;
let _currentPrice = null;
let _gridLevels = { buys: [], sells: [], all: [] };
let _openOrderPrices = [];

/* Compute grid levels from env + optionally clamp to range */
function computeGridLevels(min, max, stepPct){
  const out = { buys: [], sells: [], all: [] };
  if (min == null || max == null || stepPct == null) return out;
  const s = Number(stepPct)/100.0;
  if (s <= 0) return out;
  // multiplicative grid (common in % step strategies)
  // ensure we include both ends
  let p = Number(min);
  out.all.push(p);
  while (p < max){
    p = p * (1 + s);
    if (p > max) break;
    out.all.push(Number(p));
  }
  // split buys/sells relative to current price later
  return out;
}

/* Build tick values from grid + open orders (limit to avoid clutter) */
function buildYAxisTicks(){
  const arr = new Set();
  _gridLevels.all.forEach(v=>arr.add(Number(v)));
  _openOrderPrices.forEach(v=>arr.add(Number(v)));
  // sort asc
  let vals = Array.from(arr).filter(v=>isFinite(v));
  vals.sort((a,b)=>a-b);
  // cap to at most ~40 ticks for readability
  if (vals.length > 40){
    // sample roughly every Nth value
    const step = Math.ceil(vals.length / 40);
    vals = vals.filter((_,i)=> i % step === 0);
  }
  const texts = vals.map(v=>fmtPriceTick(v, 6));
  return { vals, texts };
}

/* Apply y-axis tickvals/ticktext + overlay grid shapes */
async function applyYAxisAndShapes(){
  if (!_chartReady) return;
  const ticks = buildYAxisTicks();
  const shapes = buildGridShapes();
  const layout = {
    'yaxis.tickvals': ticks.vals,
    'yaxis.ticktext': ticks.texts,
    'shapes': shapes,
  };
  try{ await Plotly.relayout('chart', layout); }catch(_){}
}

/* Determine closest BUY below price and SELL above price for emphasis */
function findEdgeLevels(){
  if (_gridLevels.all.length === 0 || _currentPrice==null) return {buy:null, sell:null};
  let buy=null, sell=null;
  let minBelow = -Infinity, minAbove = Infinity;
  for(const lv of _gridLevels.all){
    if (lv <= _currentPrice && lv > minBelow){ minBelow = lv; buy = lv; }
    if (lv >= _currentPrice && lv < minAbove){ minAbove = lv; sell = lv; }
  }
  return { buy, sell };
}

/* Build grid overlay shapes for Plotly */
function buildGridShapes(){
  const shapes = [];
  const edges = findEdgeLevels();
  // BUY/Sell classification relative to current price: buys <= price, sells >= price
  for(const lv of _gridLevels.all){
    const isBuySide = (_currentPrice!=null) ? (lv <= _currentPrice) : true;
    const isEdge = (lv === edges.buy) || (lv === edges.sell);
    shapes.push({
      type: 'line',
      xref: 'paper',
      x0: 0, x1: 1,
      yref: 'y',
      y0: lv, y1: lv,
      line: {
        color: isBuySide ? (isEdge ? getComputedStyle(document.documentElement).getPropertyValue('--buyEdge') : getComputedStyle(document.documentElement).getPropertyValue('--buyLine'))
                         : (isEdge ? getComputedStyle(document.documentElement).getPropertyValue('--sellEdge') : getComputedStyle(document.documentElement).getPropertyValue('--sellLine')),
        width: isEdge ? 2.6 : 1.2,
        dash: 'dot'
      },
      layer: 'below'
    });
  }
  return shapes;
}

/* stats */
async function loadStats(){
  try{
    const r = await fetch('/api/stats');
    const j = await r.json();
    if('price' in j){
      _currentPrice = (j.price==null? null : Number(j.price));
      document.getElementById('priceVal').textContent = fmt(j.price, 6);
    }
    document.getElementById('profitVal').textContent = fmt2(j.profit_usd);
    document.getElementById('splitsVal').textContent = fmt0(j.splits_count);
    document.getElementById('bnbVal').textContent = fmt2(j.bnb_converted_usd);

    // NEW profit-related cards
    if (j.bnb_pending_usd != null) document.getElementById('bnbPendVal').textContent = fmt2(j.bnb_pending_usd);
    if (j.reinvest_pool_usd != null) document.getElementById('rePoolVal').textContent = fmt2(j.reinvest_pool_usd);

    // accumulator / chunk
    const acc = (j.split_accumulator_usd != null) ? Number(j.split_accumulator_usd) : null;
    const chunk = (j.split_chunk_usd != null) ? Number(j.split_chunk_usd) : null;
    const txt = (acc!=null && chunk!=null) ? `${acc.toFixed(2)} / ${chunk.toFixed(2)}` : '— / —';
    document.getElementById('profitChunk').textContent = txt;

    // If grid env changed from server, recompute grid levels
    if (j.grid_min!=null && j.grid_max!=null && j.grid_step_pct!=null){
      const gl = computeGridLevels(Number(j.grid_min), Number(j.grid_max), Number(j.grid_step_pct));
      _gridLevels = gl;
      await applyYAxisAndShapes();
    }
  }catch(e){}
}

/* history + chart */
async function loadHistory(){
  try{
    const r = await fetch('/history');
    const j = await r.json();
    const pts = Array.isArray(j.data)? j.data : [];
    const xs = pts.map(p => new Date(p.t));
    const ys = pts.map(p => p.p);
    const layout = {
      margin:{l:60,r:24,t:10,b:40},
      xaxis:{ title:'Time', showgrid:true, zeroline:false,
              tickformat: "%d/%m/%Y %H:%M", hoverformat: "%d/%m/%Y %H:%M:%S" },
      yaxis:{ title:'Price (USDT)', showgrid:true, zeroline:false },
      paper_bgcolor:'rgba(0,0,0,0)',
      plot_bgcolor:'rgba(0,0,0,0)',
      shapes: []
    };
    const data = [{ x: xs, y: ys, mode:'lines', name: PAIR }];
    await Plotly.react('chart', data, layout, {displayModeBar:false});
    _chartReady = true;
    await applyYAxisAndShapes();
  }catch(e){
    console.warn('history load failed', e);
    try{
      await Plotly.newPlot('chart',
        [{x:[], y:[], mode:'lines', name: PAIR}],
        { margin:{l:60,r:24,t:10,b:40},
          xaxis:{ title:'Time', tickformat:"%d/%m/%Y %H:%M", hoverformat:"%d/%m/%Y %H:%M:%S" },
          yaxis:{ title:'Price (USDT)' },
          paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
          shapes: [] },
        { displayModeBar:false });
      _chartReady = true;
      await applyYAxisAndShapes();
    }catch(_){}
  }
}

/* live */
function startSSE(){
  try{
    const es = new EventSource('/stream');
    es.addEventListener('tick', async ev=>{
      try{
        const j = JSON.parse(ev.data);
        if(j && typeof j.p === 'number'){
          _currentPrice = Number(j.p);
          document.getElementById('priceVal').textContent = fmt(j.p, 6);
          const t = new Date(j.t);
          if (!_chartReady){
            await Plotly.newPlot('chart',
              [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
              { margin:{l:60,r:24,t:10,b:40},
                xaxis:{ title:'Time', tickformat:"%d/%m/%Y %H:%M", hoverformat:"%d/%m/%Y %H:%M:%S" },
                yaxis:{ title:'Price (USDT)' },
                paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                shapes: [] },
              { displayModeBar:false });
            _chartReady = true;
          } else {
            try {
              Plotly.extendTraces('chart', {x:[[t]], y:[[j.p]]}, [0], 10000);
            } catch (e) {
              try{
                await Plotly.newPlot('chart',
                  [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
                  { margin:{l:60,r:24,t:10,b:40},
                    xaxis:{ title:'Time', tickformat:"%d/%m/%Y %H:%M", hoverformat:"%d/%m/%Y %H:%M:%S" },
                    yaxis:{ title:'Price (USDT)' },
                    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                    shapes: [] },
                  { displayModeBar:false });
                _chartReady = true;
              }catch(_){}
            }
          }
          // update shapes (edge lines), and open orders edge-row highlight
          await applyYAxisAndShapes();
          renderOpenOrders(); // to refresh edge-row highlighting based on _currentPrice
        }
      }catch(e){}
    });
  }catch(e){}
}

/* tables state */
let OPEN_ORDERS_RAW = [];
let HIST_ORDERS_RAW = [];
let _openCountsEl = null, _histCountsEl = null;
document.addEventListener('DOMContentLoaded', ()=>{
  _openCountsEl = document.getElementById('openCount');
  _histCountsEl = document.getElementById('histCount');
});

/* utils for sort/filter */
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
    String(o.value_usdt).toLowerCase().includes(q)
  );
}

/* highlight edge rows in Open Orders */
function computeEdgeOrderIds(){
  if (!Array.isArray(OPEN_ORDERS_RAW) || OPEN_ORDERS_RAW.length===0 || _currentPrice==null){
    return { buyId:null, sellId:null, buyPrice:null, sellPrice:null };
  }
  let buyBest=null, sellBest=null;
  let buyBestPrice=-Infinity, sellBestPrice=Infinity;
  for(const o of OPEN_ORDERS_RAW){
    if (typeof o.price !== 'number') continue;
    if (o.side==='buy' && o.price <= _currentPrice && o.price > buyBestPrice){
      buyBestPrice = o.price; buyBest = o;
    }
    if (o.side==='sell' && o.price >= _currentPrice && o.price < sellBestPrice){
      sellBestPrice = o.price; sellBest = o;
    }
  }
  return {
    buyId: buyBest? (buyBest.id||`${buyBest.side}-${buyBest.price}-${buyBest.time}`): null,
    sellId: sellBest? (sellBest.id||`${sellBest.side}-${sellBest.price}-${sellBest.time}`): null,
    buyPrice: buyBest? buyBest.price : null,
    sellPrice: sellBest? sellBest.price : null
  };
}

function renderOpenOrders(){
  const tb = document.querySelector('#openTbl tbody'); if (!tb) return;
  const sortKey = document.getElementById('openSortBy').value;
  const sortDir = document.getElementById('openSortDir').value;
  const q = document.getElementById('openFilter').value.trim();

  let rows = textFilter(OPEN_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  tb.innerHTML='';
  const edges = computeEdgeOrderIds();

  for(const o of rows){
    const id = o.id || `${o.side}-${o.price}-${o.time}`;
    const isEdge = (id===edges.buyId) || (id===edges.sellId);
    const tr = document.createElement('tr');
    if (isEdge) tr.classList.add('edge-row');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td class="mono">${fmt(o.price, 6)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    tb.appendChild(tr);
  }
  if (_openCountsEl) _openCountsEl.textContent = `(${rows.length})`;

  // Update y-axis ticks also from open order prices
  _openOrderPrices = rows.map(o => Number(o.price)).filter(v=>isFinite(v));
  applyYAxisAndShapes();
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
}

/* Orders History */
function renderHistOrders(){
  const tb = document.querySelector('#histTbl tbody'); if (!tb) return;
  const sortKey = document.getElementById('histSortBy').value;
  const sortDir = document.getElementById('histSortDir').value;
  const q = document.getElementById('histFilter').value.trim();

  let rows = textFilter(HIST_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  tb.innerHTML='';
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
  if (_histCountsEl) _histCountsEl.textContent = `(${rows.length})`;
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
}

/* wire controls + reset buttons */
function wireControls(){
  const ids = ['openSortBy','openSortDir','openFilter','histSortBy','histSortDir','histFilter'];
  ids.forEach(id=>{
    const el = document.getElementById(id);
    if (!el) return;
    const handler = id.startsWith('open') ? renderOpenOrders : renderHistOrders;
    el.addEventListener('input', handler);
    el.addEventListener('change', handler);
  });

  const openReset = document.getElementById('openReset');
  if (openReset){
    openReset.addEventListener('click', ()=>{
      document.getElementById('openSortBy').value = 'time';
      document.getElementById('openSortDir').value = 'desc';
      document.getElementById('openFilter').value = '';
      renderOpenOrders();
    });
  }
  const histReset = document.getElementById('histReset');
  if (histReset){
    histReset.addEventListener('click', ()=>{
      document.getElementById('histSortBy').value = 'time';
      document.getElementById('histSortDir').value = 'desc';
      document.getElementById('histFilter').value = '';
      renderHistOrders();
    });
  }
}

/* boot */
async function boot(){
  await loadStats();
  // initial grid levels from env if available
  if (GRID_MIN!=null && GRID_MAX!=null && GRID_STEP_PCT!=null){
    _gridLevels = computeGridLevels(Number(GRID_MIN), Number(GRID_MAX), Number(GRID_STEP_PCT));
  }
  await loadHistory();
  startSSE();

  await loadOpenOrders();
  await loadHistoryOrders();
  wireControls();

  // periodic refresh
  setInterval(loadStats, 10000);
  setInterval(loadOpenOrders, 15000);
  setInterval(loadHistoryOrders, 20000);
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
