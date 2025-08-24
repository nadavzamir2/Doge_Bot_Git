#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DOGE Grid Monitor Dashboard (single file, full)
------------------------------------------------
שינויים בולטים (לפי בקשתך בלבד, בלי לגעת בשאר הלוגיקה):
- כרטיס מידע נוסף למעלה: טווח פעילות הבוט (GRID_MIN–GRID_MAX) + מרווח שכבות (GRID_STEP_PCT)
- כל התאריכים בפורמט dd/mm/yyyy ושעות 24h
- מיון וסינון בטבלאות Open Orders ו-Orders History
- חיצי collapsible ליד כל כותרת, כותרת לחיצה, וספירת שורות בסוגריים
- שכבות Grid בגרף (Buy/Sell), קווים דקים/מקווקווים, הקרובות מודגשות קלות, עם Toggle ושמירת מצב ב-localStorage
- רוחב מסך ~90% (responsive)

הערה: לא שונו נקודות קצה/לוגיקה עסקית/מבנה נתונים—רק UI/JS ופורמט תאריכים.
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

# Grid info for UI card (optional, from .env)
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
    --bg: #f5f7fb;
    --fg: #1a202c;
    --muted: #4a5568;
    --card: #ffffff;
    --accent: #2b6cb0;
    --green: #2f855a;
    --red: #c53030;
    --grid: #e2e8f0;
    --blue-weak: rgba(25, 118, 210, 0.18);
    --blue-strong: rgba(25, 118, 210, 0.38);
    --red-weak: rgba(229, 62, 62, 0.18);
    --red-strong: rgba(229, 62, 62, 0.38);
  }
  html, body { height:100%; }
  body { margin:0; font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background: var(--bg); color: var(--fg); }
  .wrap { width: 80vw; max-width: 1800px; margin: 24px auto; padding: 0 16px; }
  h1 { margin: 4px 0 16px; font-size: 22px; }
  .cards { display:grid; grid-template-columns: repeat(5, minmax(160px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--grid); border-radius:12px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .card h3 { margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; }
  .card .v { font-size:20px; font-weight:700; }
  .sections { display:grid; gap:12px; }

  /* collapsible containers */
  details { background:var(--card); border:1px solid var(--grid); border-radius:12px; box-shadow:0 1px 2px rgba(0,0,0,.04); overflow:hidden; }
  details > summary { cursor:pointer; padding:12px 14px; font-weight:700; list-style:none; display:flex; align-items:center; gap:8px; user-select:none; }
  details > summary::-webkit-details-marker { display:none; }
  details[open] > summary { border-bottom:1px solid var(--grid); }
  .section-body { padding:12px 14px; }
  .summary-caret { width:10px; height:10px; display:inline-block; transform: rotate(-90deg); transition: transform .18s ease; border-left: 5px solid var(--muted); border-top: 5px solid transparent; border-bottom: 5px solid transparent; }
  details[open] .summary-caret { transform: rotate(0deg); }

  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px; border-bottom:1px solid var(--grid); }
  th { color:var(--muted); font-weight:600; white-space:nowrap; }
  .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; }
  .pill.buy { background:#e6fffa; color:#2c7a7b; }
  .pill.sell { background:#fff5f5; color:#c53030; }
  #chart { width:100%; height:460px; }

  /* Small controls row for sort/filter */
  .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
  .controls label { font-size:12px; color:var(--muted); }
  .controls select, .controls input[type="text"] {
    font-size:12px; padding:4px 6px; border:1px solid var(--grid); border-radius:8px; background:#fff;
  }
  .subnote { font-size:12px; color:var(--muted); margin-top:4px; }

  /* checkbox line under chart title */
  .chart-toggles { display:flex; align-items:center; gap:8px; padding:6px 14px 0 14px; color:var(--muted); font-size:13px; }
  .chart-toggles label { display:flex; gap:6px; align-items:center; cursor:pointer; user-select:none; }
  .chart-toggles input { transform: translateY(1px); }

  /* status/notes */
  .note { color:var(--muted); margin-top:6px; }
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
      <div class="card"><h3>Total Profit (USD)</h3><div id="profitVal" class="v mono">0.00</div></div>
      <div class="card"><h3>Splits Count</h3><div id="splitsVal" class="v mono">0</div></div>
      <div class="card"><h3>Converted to BNB (USD)</h3><div id="bnbVal" class="v mono">0.00</div></div>
    </div>

    <div class="sections">
      <!-- Chart -->
      <details open id="chartSection">
        <summary><span class="summary-caret"></span><span>Price Chart</span></summary>
        <div class="chart-toggles">
          <label title="Show/hide grid layers on chart">
            <input type="checkbox" id="showGridLayers" checked />
            <span>Show grid layers</span>
          </label>
        </div>
        <div class="section-body">
          <div id="chart"></div>
        </div>
      </details>

      <!-- Open Orders -->
      <details open id="openSection">
        <summary><span class="summary-caret"></span><span>Open Orders (<span id="openCount">0</span>)</span></summary>
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
          <div id="openNote" class="note"></div>
        </div>
      </details>

      <!-- Orders History -->
      <details open id="histSection">
        <summary><span class="summary-caret"></span><span>Orders History (<span id="histCount">0</span>)</span></summary>
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
          <div id="histNote" class="note"></div>
        </div>
      </details>
    </div>
  </div>

<script>
"use strict";

// Pair label
const PAIR = {{ pair|tojson }};
document.getElementById('pair').textContent = PAIR;

// grid values from server (.env)
const GRID_MIN = {{ grid_min|tojson }};
const GRID_MAX = {{ grid_max|tojson }};
const GRID_STEP_PCT = {{ grid_step_pct|tojson }};

// ----- Range card -----
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

// ===== Helpers =====
function pad2(n){ return n<10 ? '0'+n : ''+n; }
function fmt(n, d=5){ if(n===null||n===undefined||isNaN(n)) return '—'; return Number(n).toFixed(d); }
function fmt2(n){ return fmt(n,2); }
function fmt0(n){ return (n==null)?'—':String(n); }

// date/time: dd/mm/yyyy HH:MM:SS (24h)
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

// ===== Chart & Grid Layers =====
let _chartReady = false;
let _lastPrice = null;

// Idempotent globals (מונעים "already been declared" אם נטען פעמיים)
window.SHOW_GRID_KEY = window.SHOW_GRID_KEY || 'showGridLayers';
window.showGridEl = window.showGridEl || document.getElementById('showGridLayers');

// החזר העדפה מה-localStorage
const savedShowGrid = localStorage.getItem(SHOW_GRID_KEY);
if (savedShowGrid === 'false') showGridEl.checked = false;

// חישוב רמות השכבות לפי GRID_MIN/MAX/STEP_PCT
function computeLevels(gridMin, gridMax, stepPct){
  const out = [];
  if (gridMin == null || gridMax == null || stepPct == null) return out;
  const min = Number(gridMin), max = Number(gridMax), pct = Number(stepPct);
  if (!(pct > 0) || !(max > min)) return out;
  // נשתמש בצעד אחוזי מצטבר (כמו grid ליניארי באחוזים)
  // נתחיל מה-min ונעלה באינקרמנט יחסית (1+pct/100) עד max
  const ratio = 1 + pct/100;
  let lvl = min;
  const guard = 10000; // מונע לולאה אינסופית
  let i=0;
  while (lvl <= max && i < guard){
    out.push(Number(lvl));
    lvl = lvl * ratio;
    i++;
    // אם בגלל צבירת floating נתקע—נשבור ידנית
    if (lvl === out[out.length-1]) break;
  }
  // ודא ש-max בפנים (בדיוק) אם טרם הוסף
  if (out.length && out[out.length-1] < max) out.push(max);
  return out;
}

function buildGridShapes(levels, lastPrice){
  if (!Array.isArray(levels) || !levels.length) return [];
  // קווים אופקיים על כל רוחב הגרף: נציב xref='paper' 0..1
  const shapes = [];
  let nearestBuy = null, nearestSell = null;

  if (typeof lastPrice === 'number'){
    nearestBuy  = Math.max(...levels.filter(v => v <= lastPrice));
    const sells = levels.filter(v => v > lastPrice);
    nearestSell = sells.length ? Math.min(...sells) : null;
  }

  for (const y of levels){
    const isBuy = (typeof lastPrice === 'number') ? (y <= lastPrice) : true;
    const near  = (y === nearestBuy) || (y === nearestSell);
    shapes.push({
      type: 'line',
      xref: 'paper',
      x0: 0, x1: 1,
      yref: 'y',
      y0: y, y1: y,
      line: {
        color: isBuy ? (near ? 'var(--blue-strong)' : 'var(--blue-weak)') :
                       (near ? 'var(--red-strong)'  : 'var(--red-weak)'),
        width: near ? 1.6 : 0.9,
        dash: 'dot'
      },
      layer: 'below'
    });
  }
  return shapes;
}

function baseLayout(){
  return {
    margin:{l:50,r:20,t:10,b:40},
    xaxis:{
      title:'Time',
      showgrid:true, zeroline:false,
      tickformat: "%d/%m/%Y %H:%M", hoverformat: "%d/%m/%Y %H:%M:%S"
    },
    yaxis:{ title:'Price (USDT)', showgrid:true, zeroline:false },
    paper_bgcolor:'rgba(0,0,0,0)',
    plot_bgcolor:'rgba(0,0,0,0)'
  };
}

async function loadHistory(){
  try{
    const r = await fetch('/history');
    const j = await r.json();
    const pts = Array.isArray(j.data)? j.data : [];
    const xs = pts.map(p => new Date(p.t));
    const ys = pts.map(p => p.p);
    const lay = baseLayout();

    // grid layers (אם checkbox מסומן ויש נתונים ב-.env)
    if (showGridEl.checked){
      const levels = computeLevels(GRID_MIN, GRID_MAX, GRID_STEP_PCT);
      lay.shapes = buildGridShapes(levels, _lastPrice ?? (ys.length? ys[ys.length-1] : null));
    }

    const data = [{ x: xs, y: ys, mode:'lines', name: PAIR }];
    await Plotly.react('chart', data, lay, {displayModeBar:false});
    _chartReady = true;
  }catch(e){
    console.warn('history load failed', e);
    try{
      const lay = baseLayout();
      if (showGridEl.checked){
        const levels = computeLevels(GRID_MIN, GRID_MAX, GRID_STEP_PCT);
        lay.shapes = buildGridShapes(levels, _lastPrice);
      }
      await Plotly.newPlot('chart',
        [{x:[], y:[], mode:'lines', name: PAIR}],
        lay,
        { displayModeBar:false });
      _chartReady = true;
    }catch(_){}
  }
}

// עדכון שכבות בגרף (ללא פגיעה בסדרה עצמה)
async function refreshGridShapes(){
  if (!_chartReady) return;
  const gd = document.getElementById('chart');
  const lay = gd.layout ? JSON.parse(JSON.stringify(gd.layout)) : baseLayout();
  if (showGridEl.checked){
    const levels = computeLevels(GRID_MIN, GRID_MAX, GRID_STEP_PCT);
    lay.shapes = buildGridShapes(levels, _lastPrice);
  } else {
    lay.shapes = [];
  }
  try{
    await Plotly.relayout('chart', lay);
  }catch(e){
    // fallback—רנדר מלא
    try{
      const x = gd.data?.[0]?.x || [];
      const y = gd.data?.[0]?.y || [];
      await Plotly.react('chart', [{x, y, mode:'lines', name: PAIR}], lay, {displayModeBar:false});
    }catch(_){}
  }
}

// ===== Stats =====
async function loadStats(){
  try{
    const r = await fetch('/api/stats');
    const j = await r.json();
    if('price' in j){
      _lastPrice = (typeof j.price === 'number') ? j.price : _lastPrice;
      document.getElementById('priceVal').textContent = fmt(j.price, 6);
    }
    document.getElementById('profitVal').textContent = fmt2(j.profit_usd);
    document.getElementById('splitsVal').textContent = fmt0(j.splits_count);
    document.getElementById('bnbVal').textContent = fmt2(j.bnb_converted_usd);
  }catch(e){}
}

// ===== SSE =====
function startSSE(){
  try{
    const es = new EventSource('/stream');
    es.addEventListener('tick', async ev=>{
      try{
        const j = JSON.parse(ev.data);
        if(j && typeof j.p === 'number'){
          _lastPrice = j.p;
          document.getElementById('priceVal').textContent = fmt(j.p, 6);
          const t = new Date(j.t);
          if (!_chartReady){
            const lay = baseLayout();
            if (showGridEl.checked){
              const levels = computeLevels(GRID_MIN, GRID_MAX, GRID_STEP_PCT);
              lay.shapes = buildGridShapes(levels, _lastPrice);
            }
            await Plotly.newPlot('chart',
              [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
              lay,
              { displayModeBar:false });
            _chartReady = true;
          } else {
            try {
              Plotly.extendTraces('chart', {x:[[t]], y:[[j.p]]}, [0], 10000);
            } catch (e) {
              const lay = baseLayout();
              if (showGridEl.checked){
                const levels = computeLevels(GRID_MIN, GRID_MAX, GRID_STEP_PCT);
                lay.shapes = buildGridShapes(levels, _lastPrice);
              }
              await Plotly.newPlot('chart',
                [{ x:[t], y:[j.p], mode:'lines', name: PAIR }],
                lay,
                { displayModeBar:false });
              _chartReady = true;
            }
            // לעדכן שכבות אם צריך (במיוחד אם חצינו רמה)
            if (showGridEl.checked) refreshGridShapes();
          }
        }
      }catch(e){}
    });
  }catch(e){}
}

// ===== Tables: Open Orders =====
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

function renderOpenOrders(){
  const tb = document.querySelector('#openTbl tbody'); tb.innerHTML='';
  const sortKey = document.getElementById('openSortBy').value;
  const sortDir = document.getElementById('openSortDir').value;
  const q = document.getElementById('openFilter').value.trim();

  let rows = textFilter(OPEN_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  for(const o of rows){
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtDateTimeLocal(o.time)}</td>
      <td><span class="pill ${o.side==='buy'?'buy':'sell'}">${o.side ?? '—'}</span></td>
      <td class="mono">${fmt(o.price, 6)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    tb.appendChild(tr);
  }

  // עדכון מונה
  document.getElementById('openCount').textContent = String(rows.length);
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

// ===== Tables: History =====
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
      <td class="mono">${fmt(o.price, 6)}</td>
      <td class="mono">${fmt(o.amount, 2)}</td>
      <td class="mono">${fmt2(o.value_usdt)}</td>`;
    tb.appendChild(tr);
  }

  document.getElementById('histCount').textContent = String(rows.length);
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

// ===== Wire controls & persistence =====
function wireControls(){
  const ids = ['openSortBy','openSortDir','openFilter','histSortBy','histSortDir','histFilter'];
  ids.forEach(id=>{
    const el = document.getElementById(id);
    if (!el) return;
    const handler = id.startsWith('open') ? renderOpenOrders : renderHistOrders;
    el.addEventListener('input', handler);
    el.addEventListener('change', handler);
  });

  // שמירת מצב checkbox להצגת שכבות (Idempotent: אין const כפול)
  showGridEl.addEventListener('change', ()=>{
    localStorage.setItem(SHOW_GRID_KEY, showGridEl.checked ? 'true' : 'false');
    refreshGridShapes();
  });
}

// ===== Boot =====
async function boot(){
  await loadStats();
  await loadHistory();    // load history first
  startSSE();             // then live updates
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
