#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DOGE Grid Monitor Dashboard (single file)
- Light theme (LTR)
- Info cards (Current Price, Total Profit, Splits, Converted to BNB)
- NEW: Bot Range card + spacing
- NEW: Extra info cards (Open Orders count, History rows, Split accum/trigger, BNB pending, Reinvest pool)
- Collapsible sections: Chart, Open Orders, Orders History
- Live price via SSE (/stream)
- Persistent history via /history (saved to ~/doge_bot/data/price_history.json)
- Separate endpoints for open orders and order history (not the same data)
- NEW: /api/split_state reading ~/doge_bot/state.json (+ SPLIT_CHUNK_USD)
- NEW: Grid layers checkbox: draws levels from actual Open Orders (buys=green, sells=orange)
- NEW: Chart Y-axis ticks are exactly the grid levels (full price formatting .6f)
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

# Grid info for UI card (optional)
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

# Profit-split trigger (for info card)
SPLIT_CHUNK_USD = float(os.getenv("SPLIT_CHUNK_USD", "4.0"))

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

def _read_stats_file() -> Dict[str, Any]:
    # אם הבוט שלך כותב לכאן, הדשבורד יציג; אחרת יוצגו אפסים.
    try:
        if STATS_FILE.exists():
            with STATS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # normalize
                return {
                    "cumulative_profit_usd": float(data.get("cumulative_profit_usd", 0.0) or 0.0),
                    "splits_count": int(data.get("splits_count", 0) or 0),
                    "bnb_converted_usd": float(data.get("bnb_converted_usd", 0.0) or 0.0),
                    "trades_count": int(data.get("trades_count", 0) or 0),
                }
    except Exception as e:
        print(f"[WARN] read stats failed: {e}")
    return {
        "cumulative_profit_usd": 0.0,
        "splits_count": 0,
        "bnb_converted_usd": 0.0,
        "trades_count": 0,
    }

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
    return {
        "price": _current_price,
        "profit_usd": float(stats.get("cumulative_profit_usd", 0.0) or 0.0),
        "splits_count": int(stats.get("splits_count", 0) or 0),
        "bnb_converted_usd": float(stats.get("bnb_converted_usd", 0.0) or 0.0),
        "trades_count": int(stats.get("trades_count", 0) or 0),
        "split_chunk_usd": SPLIT_CHUNK_USD,
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

@app.get("/api/split_state")
def api_split_state():
    """
    Return profit-split runtime state from ~/doge_bot/state.json
    (produced by profit_split.py). Adds split_chunk_usd from ENV.
    """
    try:
        state_path = pathlib.Path.home() / "doge_bot" / "state.json"
        st = {}
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as f:
                st = json.load(f) or {}
        return {
            "ok": True,
            "state": {
                "split_accumulator_usd": float(st.get("split_accumulator_usd", 0.0) or 0.0),
                "bnb_pending_usd": float(st.get("bnb_pending_usd", 0.0) or 0.0),
                "reinvest_pool_usd": float(st.get("reinvest_pool_usd", 0.0) or 0.0),
                "total_sent_to_bnb_usd": float(st.get("total_sent_to_bnb_usd", 0.0) or 0.0),
                "total_reinvested_usd": float(st.get("total_reinvested_usd", 0.0) or 0.0),
                "last_update_ts": float(st.get("last_update_ts", 0.0) or 0.0),
            },
            "split_chunk_usd": SPLIT_CHUNK_USD,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "state": {}, "split_chunk_usd": SPLIT_CHUNK_USD}

# =========================================================
# FULL UI (HTML)
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
  .wrap { max-width: 1200px; margin: 24px auto; padding: 0 16px; }
  h1 { margin: 4px 0 16px; font-size: 22px; }
  .cards { display:grid; grid-template-columns: repeat(4, minmax(160px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--grid); border-radius:12px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .card h3 { margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; }
  .card .v { font-size:20px; font-weight:700; }
  .small { font-size:12px; color:var(--muted); }
  .sections { display:grid; gap:12px; }
  details { background:var(--card); border:1px solid var(--grid); border-radius:12px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  details > summary { cursor:pointer; padding:12px 14px; font-weight:600; list-style:none; display:flex; gap:8px; align-items:center;}
  details > summary::-webkit-details-marker { display:none; }
  details[open] > summary { border-bottom:1px solid var(--grid); }
  .chev { font-size:12px; color:var(--muted); }
  .section-body { padding:12px 14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px; border-bottom:1px solid var(--grid); }
  th { color:var(--muted); font-weight:600; }
  .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; }
  .pill.buy { background:#e6fffa; color:#2c7a7b; }
  .pill.sell { background:#fff5f5; color:#c53030; }
  #chart { width:100%; height:420px; }

  /* controls for sort/filter */
  .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
  .controls label { font-size:12px; color:var(--muted); }
  .controls select, .controls input[type="text"] {
    font-size:12px; padding:4px 6px; border:1px solid var(--grid); border-radius:8px; background:#fff;
  }
  .subnote { font-size:12px; color:var(--muted); margin-top:4px; }

  .row-emph { background: #fffceb; } /* הדגשת שורת הקצה בטבלה */
</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div class="wrap">
    <h1>DOGE Grid Monitor — <span id="pair" class="mono"></span></h1>

    <!-- Top info cards -->
    <div class="cards">
      <!-- Bot Range -->
      <div class="card">
        <h3>Bot Range</h3>
        <div id="rangeVal" class="v mono">—</div>
        <div class="subnote">Layer spacing: <span id="spacingVal">—</span>%</div>
      </div>

      <div class="card"><h3>Current Price</h3><div id="priceVal" class="v mono">—</div></div>
      <div class="card"><h3>Total Profit (USD)</h3><div id="profitVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Splits Count</h3><div id="splitsVal" class="v mono">0</div></div>
      <div class="card"><h3>Converted to BNB (USD)</h3><div id="bnbVal" class="v mono">0.00</div></div>

      <!-- NEW live cards -->
      <div class="card"><h3>Open Orders (Count)</h3><div id="openCountVal" class="v mono">0</div></div>
      <div class="card"><h3>History Rows (Count)</h3><div id="histCountVal" class="v mono">0</div></div>
      <div class="card"><h3>Split Accum / Trigger</h3><div id="splitAccumVal" class="v mono">0.00/0.00</div></div>
      <div class="card"><h3>BNB Pending (USD)</h3><div id="bnbPendVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Reinvest Pool (USD)</h3><div id="rePoolVal" class="v mono">0.00</div></div>
    </div>

    <div class="sections">
      <!-- Chart -->
      <details open id="secChart">
        <summary><span class="chev">▸</span> Price Chart</summary>
        <div class="section-body" style="padding-top:8px;">
          <label style="font-size:12px;color:var(--muted);user-select:none;">
            <input id="chkGridLayers" type="checkbox" style="vertical-align:middle;"/>
            Show grid layers
          </label>
          <div id="chart" style="margin-top:8px;"></div>
        </div>
      </details>

      <!-- Open Orders -->
      <details open id="secOpen">
        <summary><span class="chev">▸</span> <span id="ttlOpen">Open Orders</span></summary>
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
          <div id="openNote" class="small"></div>
        </div>
      </details>

      <!-- Orders History -->
      <details open id="secHist">
        <summary><span class="chev">▸</span> <span id="ttlHist">Orders History</span></summary>
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
          <div id="histNote" class="small"></div>
        </div>
      </details>
    </div>
  </div>

<script>
"use strict";

const PAIR = {{ pair|tojson }};
document.getElementById('pair').textContent = PAIR;

/* range & spacing for the top card */
const GRID_MIN = {{ grid_min|tojson }};
const GRID_MAX = {{ grid_max|tojson }};
const GRID_STEP_PCT = {{ grid_step_pct|tojson }};

(function setRangeCard(){
  const r = document.getElementById('rangeVal');
  const s = document.getElementById('spacingVal');
  if (GRID_MIN != null && GRID_MAX != null) {
    r.textContent = `${Number(GRID_MIN).toFixed(6).replace(/^\./, '0.')} – ${Number(GRID_MAX).toFixed(6).replace(/^\./, '0.')}`;
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
function fmt6(n){ return fmt(n,6); }
function fmt0(n){ return (n==null)?'—':String(n); }

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

/* state */
let _chartReady = false;
let _lastLivePrice = null;

/* ===== Grid Layers (checkbox, shapes from open orders levels) ===== */
function getLocalBool(key, def=false){
  try{ const v = localStorage.getItem(key); if(v===null) return def; return v === "1"; }catch(_){ return def; }
}
function setLocalBool(key, val){ try{ localStorage.setItem(key, val ? "1" : "0"); }catch(_){} }

const GRID_KEY_SHOW = "ui.showGridLayers";

let _gridShow = getLocalBool(GRID_KEY_SHOW, false);
let _gridLevelsFromOrders = {buys: [], sells: []};  // updated from open orders
let _tickVals = []; // y-axis ticks derived from grid levels

function wireGridCheckbox(){
  const chk = document.getElementById("chkGridLayers");
  if(!chk) return;
  chk.checked = _gridShow;
  chk.addEventListener("change", ()=>{
    _gridShow = chk.checked;
    setLocalBool(GRID_KEY_SHOW, _gridShow);
    updateGridLayersOnChart(_lastLivePrice ?? 0);
  });
}

function uniqSorted(arr){
  const s = Array.from(new Set(arr.filter(x=>typeof x==="number" && isFinite(x))));
  s.sort((a,b)=>a-b);
  return s;
}

function buildShapesFromLevels(levels, currentPrice){
  const buys = uniqSorted(levels.buys || []);
  const sells = uniqSorted(levels.sells || []);
  // find nearest below and above
  let lower=null, upper=null;
  for (const v of buys){ if (v<=currentPrice) lower = v; else break; }
  for (const v of sells){ if (v>=currentPrice) { upper = v; break; } }
  const shapes = [];
  // buys → green, thin dashed; nearest a bit thicker
  for (const v of buys){
    const emph = (lower!=null && Math.abs(v-lower)<1e-15) ? 0.9 : 0.5;
    const width = (lower!=null && Math.abs(v-lower)<1e-15) ? 2 : 1;
    shapes.push({
      type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:v, y1:v,
      line:{ color:`rgba(46,204,113,${emph})`, width:width, dash:"dot" }
    });
  }
  // sells → orange
  for (const v of sells){
    const emph = (upper!=null && Math.abs(v-upper)<1e-15) ? 0.9 : 0.5;
    const width = (upper!=null && Math.abs(v-upper)<1e-15) ? 2 : 1;
    shapes.push({
      type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:v, y1:v,
      line:{ color:`rgba(243,156,18,${emph})`, width:width, dash:"dot" }
    });
  }
  return shapes;
}

function updateYAxisTicksFromLevels(levels){
  const vals = uniqSorted([...(levels.buys||[]), ...(levels.sells||[])]);
  _tickVals = vals;
  const ticktext = vals.map(v=>Number(v).toFixed(6).replace(/^\./, '0.'));
  const layoutUpdate = {
    yaxis: {
      tickmode: "array",
      tickvals: vals,
      ticktext: ticktext,
      automargin: true,
      title: { text: "Price (USDT)", standoff: 14 },
      ticklabelposition: "outside",
      tickfont: { size: 8 }
    }
  };
  try { Plotly.relayout('chart', layoutUpdate); } catch(_) {}
}

function updateGridLayersOnChart(currentPrice){
  _lastLivePrice = currentPrice;
  const show = document.getElementById("chkGridLayers")?.checked ?? false;
  const div = document.getElementById('chart');
  if(!div || !div.layout) return;
  if(!show){
    const newLayout = {...div.layout};
    delete newLayout.shapes;
    try{ Plotly.relayout('chart', newLayout); }catch(_){}
    return;
  }
  const shapes = buildShapesFromLevels(_gridLevelsFromOrders, typeof currentPrice==="number"?currentPrice:0);
  try{ Plotly.relayout('chart', { shapes }); }catch(_){}
}

/* ===== Stats / Split state ===== */
async function loadStats(){
  try{
    const r = await fetch('/api/stats');
    const j = await r.json();
    if('price' in j && j.price!=null) document.getElementById('priceVal').textContent = fmt6(j.price);
    document.getElementById('profitVal').textContent = fmt2(j.profit_usd);
    document.getElementById('splitsVal').textContent = fmt0(j.splits_count);
    document.getElementById('bnbVal').textContent = fmt2(j.bnb_converted_usd);
  }catch(e){}
}

async function loadSplitState(){
  try{
    const r = await fetch('/api/split_state');
    const j = await r.json();
    if (j.ok && j.state){
      const acc = Number(j.state.split_accumulator_usd||0);
      const trig = Number(j.split_chunk_usd||0);
      document.getElementById('splitAccumVal').textContent = `${fmt2(acc)}/${fmt2(trig)}`;
      document.getElementById('bnbPendVal').textContent = fmt2(Number(j.state.bnb_pending_usd||0));
      document.getElementById('rePoolVal').textContent = fmt2(Number(j.state.reinvest_pool_usd||0));
    }
  }catch(e){}
}

/* ===== Chart ===== */
async function loadHistory(){
  try{
    const r = await fetch('/history');
    const j = await r.json();
    const pts = Array.isArray(j.data)? j.data : [];
    const xs = pts.map(p => new Date(p.t));
    const ys = pts.map(p => p.p);
    const layout = {
      margin:{l:60,r:20,t:10,b:40},
      xaxis:{ title:'Time', showgrid:true, zeroline:false,
              tickformat: "%d/%m/%Y %H:%M", hoverformat: "%d/%m/%Y %H:%M:%S" },
      yaxis:{
        title: { text:'Price (USDT)', standoff:14 }, /* מרווח כדי שלא יעלה על השנתות */
        showgrid:true, zeroline:false,
        tickformat: ".6f",
        automargin: true,
        ticklabelposition: "outside",
        tickfont: { size: 11 }
      },
      paper_bgcolor:'rgba(0,0,0,0)',
      plot_bgcolor:'rgba(0,0,0,0)'
    };
    const data = [{ x: xs, y: ys, mode:'lines', name: 'DOGE/USDT' }];
    await Plotly.react('chart', data, layout, {displayModeBar:false});
    _chartReady = true;
  }catch(e){
    console.warn('history load failed', e);
    try{
      await Plotly.newPlot('chart',
        [{x:[], y:[], mode:'lines', name:'DOGE/USDT'}],
        { margin:{l:60,r:20,t:10,b:40},
          xaxis:{ title:'Time', tickformat:"%d/%m/%Y %H:%M", hoverformat:"%d/%m/%Y %H:%M:%S" },
          yaxis:{ title:{text:'Price (USDT)', standoff:14}, tickformat:".6f", automargin:true, ticklabelposition:"outside", tickfont:{size:11}},
          paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)' },
        { displayModeBar:false });
      _chartReady = true;
    }catch(_){}
  }
  wireGridCheckbox();
  updateGridLayersOnChart(_lastLivePrice ?? 0);
}

/* ===== SSE live price ===== */
function startSSE(){
  try{
    const es = new EventSource('/stream');
    es.addEventListener('tick', async ev=>{
      try{
        const j = JSON.parse(ev.data);
        if(j && typeof j.p === 'number'){
          document.getElementById('priceVal').textContent = fmt6(j.p);
          const t = new Date(j.t);
          _lastLivePrice = j.p;
          if (!_chartReady){
            await Plotly.newPlot('chart',
              [{ x:[t], y:[j.p], mode:'lines', name:'DOGE/USDT' }],
              { margin:{l:60,r:20,t:10,b:40},
                xaxis:{ title:'Time', tickformat:"%d/%m/%Y %H:%M", hoverformat:"%d/%m/%Y %H:%M:%S" },
                yaxis:{ title:{text:'Price (USDT)', standoff:14}, tickformat:".6f", automargin:true, ticklabelposition:"outside", tickfont:{size:11}},
                paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)' },
              { displayModeBar:false });
            _chartReady = true;
          } else {
            try {
              Plotly.extendTraces('chart', {x:[[t]], y:[[j.p]]}, [0], 10000);
            } catch (e) {
              try{
                await Plotly.newPlot('chart',
                  [{ x:[t], y:[j.p], mode:'lines', name:'DOGE/USDT' }],
                  { margin:{l:60,r:20,t:10,b:40},
                    xaxis:{ title:'Time', tickformat:"%d/%m/%Y %H:%M", hoverformat:"%d/%m/%Y %H:%M:%S" },
                    yaxis:{ title:{text:'Price (USDT)', standoff:14}, tickformat:".6f", automargin:true, ticklabelposition:"outside", tickfont:{size:11}},
                    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)' },
                  { displayModeBar:false });
                _chartReady = true;
              }catch(_){}
            }
          }
          // refresh grid shapes position relative to live price
          updateGridLayersOnChart(j.p);
        }
      }catch(e){}
    });
  }catch(e){}
}

/* ===== Open Orders (sort/filter + counts + highlight edge rows) ===== */
let OPEN_ORDERS_RAW = [];

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

function computeLevelsFromOrders(orders){
  const buys = [];
  const sells = [];
  for (const o of orders){
    const p = Number(o.price||0);
    if (!isFinite(p) || p<=0) continue;
    if ((o.side||'').toLowerCase()==='buy') buys.push(p);
    else if ((o.side||'').toLowerCase()==='sell') sells.push(p);
  }
  return { buys: uniqSorted(buys), sells: uniqSorted(sells) };
}

function renderOpenOrders(){
  const tb = document.querySelector('#openTbl tbody'); tb.innerHTML='';
  const sortKey = document.getElementById('openSortBy').value;
  const sortDir = document.getElementById('openSortDir').value;
  const q = document.getElementById('openFilter').value.trim();

  let rows = textFilter(OPEN_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  // find edge rows around current price (nearest buy below, nearest sell above)
  let edgeBuyIndex = -1, edgeSellIndex = -1;
  if (_lastLivePrice!=null){
    let maxBelow=-Infinity, minAbove=Infinity;
    let idxBelow=-1, idxAbove=-1;
    for (let i=0;i<rows.length;i++){
      const o = rows[i];
      const p = Number(o.price||0);
      if (!isFinite(p) || p<=0) continue;
      const s = (o.side||'').toLowerCase();
      if (s==='buy' && p<=_lastLivePrice && p>maxBelow){ maxBelow=p; idxBelow=i; }
      if (s==='sell' && p>=_lastLivePrice && p<minAbove){ minAbove=p; idxAbove=i; }
    }
    edgeBuyIndex = idxBelow;
    edgeSellIndex = idxAbove;
  }

  for(let i=0;i<rows.length;i++){
    const o = rows[i];
    const tr = document.createElement('tr');
    if (i===edgeBuyIndex || i===edgeSellIndex) tr.classList.add('row-emph');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td class="mono">${fmt6(o.price)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    tb.appendChild(tr);
  }
}

async function loadOpenOrders(){
  const note = document.getElementById('openNote');
  const ttl = document.getElementById('ttlOpen');
  try{
    const r = await fetch('/api/open_orders');
    const j = await r.json();
    if(j.ok && Array.isArray(j.orders)){
      OPEN_ORDERS_RAW = j.orders;
      // counts
      document.getElementById('openCountVal').textContent = String(j.orders.length);
      ttl.textContent = `Open Orders (${j.orders.length})`;

      note.textContent = j.orders.length? '' : 'No open orders.';
      renderOpenOrders();

      // rebuild grid levels from orders and update chart shapes & ticks
      _gridLevelsFromOrders = computeLevelsFromOrders(j.orders);
      updateYAxisTicksFromLevels(_gridLevelsFromOrders);
      updateGridLayersOnChart(_lastLivePrice ?? 0);
    }else{
      note.textContent = j.error || 'Auth required (API key/secret).';
      OPEN_ORDERS_RAW = [];
      renderOpenOrders();
      _gridLevelsFromOrders = {buys:[], sells:[]};
      updateYAxisTicksFromLevels(_gridLevelsFromOrders);
      updateGridLayersOnChart(_lastLivePrice ?? 0);
      ttl.textContent = `Open Orders`;
      document.getElementById('openCountVal').textContent = "0";
    }
  }catch(e){
    note.textContent = 'Failed to load.';
    OPEN_ORDERS_RAW = [];
    renderOpenOrders();
    _gridLevelsFromOrders = {buys:[], sells:[]};
    updateYAxisTicksFromLevels(_gridLevelsFromOrders);
    updateGridLayersOnChart(_lastLivePrice ?? 0);
    ttl.textContent = `Open Orders`;
    document.getElementById('openCountVal').textContent = "0";
  }
}

/* ===== History Orders (sort/filter + counts) ===== */
let HIST_ORDERS_RAW = [];

function renderHistOrders(){
  const tb = document.querySelector('#histTbl tbody'); tb.innerHTML='';
  const sortKey = document.getElementById('histSortBy').value;
  const sortDir = document.getElementById('histSortDir').value;
  const q = document.getElementById('histFilter').value.trim();

  let rows = textFilter(HIST_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  for(const o of rows){
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td>${o.status ?? '—'}</td>
      <td class="mono">${fmt6(o.price)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    tb.appendChild(tr);
  }
}

async function loadHistoryOrders(){
  const note = document.getElementById('histNote');
  const ttl = document.getElementById('ttlHist');
  try{
    const r = await fetch('/api/order_history');
    const j = await r.json();
    if(j.ok && Array.isArray(j.orders)){
      HIST_ORDERS_RAW = j.orders;
      note.textContent = j.orders.length? '' : 'No history to show.';
      renderHistOrders();
      document.getElementById('histCountVal').textContent = String(j.orders.length);
      ttl.textContent = `Orders History (${j.orders.length})`;
    }else{
      note.textContent = j.error || 'Auth required (API key/secret).';
      HIST_ORDERS_RAW = [];
      renderHistOrders();
      document.getElementById('histCountVal').textContent = "0";
      ttl.textContent = `Orders History`;
    }
  }catch(e){
    note.textContent = 'Failed to load.';
    HIST_ORDERS_RAW = [];
    renderHistOrders();
    document.getElementById('histCountVal').textContent = "0";
    ttl.textContent = `Orders History`;
  }
}

/* ===== wire controls ===== */
function wireControls(){
  const ids = ['openSortBy','openSortDir','openFilter','histSortBy','histSortDir','histFilter'];
  ids.forEach(id=>{
    const el = document.getElementById(id);
    if (!el) return;
    const handler = id.startsWith('open') ? renderOpenOrders : renderHistOrders;
    el.addEventListener('input', handler);
    el.addEventListener('change', handler);
  });

  // small chevron for collapsibles (visual only)
  document.querySelectorAll('details').forEach(d=>{
    const s = d.querySelector('summary .chev');
    if (!s) return;
    const upd = ()=>{ s.textContent = d.open ? "▾" : "▸"; };
    d.addEventListener('toggle', upd);
    upd();
  });
}

/* ===== boot ===== */
async function boot(){
  await loadStats();
  await loadSplitState();
  await loadHistory();    // load chart first
  startSSE();             // then live updates
  await loadOpenOrders(); // this will also set grid levels + ticks
  await loadHistoryOrders();
  wireControls();

  // periodic refresh
  setInterval(loadStats, 10000);
  setInterval(loadSplitState, 10000);
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


