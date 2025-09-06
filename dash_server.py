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
- /api/open_orders and /api/order_history with client-side sort/filter
- Local state for “Show grid layers” checkbox (localStorage)
"""
import os, json, time, argparse, webbrowser, pathlib, threading
from datetime import datetime
from collections import deque
from typing import Optional
import ccxt
from flask import Flask, Response, jsonify, request, render_template_string, make_response, send_file
import subprocess, sys
from dotenv import load_dotenv as _load_dotenv_for_reload
from config import (
  API_KEY, API_SECRET, BASE_ORDER_USD, DATA_DIR, GRID_MAX, GRID_MIN, GRID_STEP_PCT,
  HISTORY_FILE_PATH as HISTORY_FILE, MAX_USD_FOR_CYCLE, PROFIT_SPLIT_TRIGGER_USD,
  RECV_WINDOW, REGION as BINANCE_REGION, SPLIT_CHUNK_USD, STATS_FILE_PATH as STATS_FILE,
  TRADING_PAIR as PAIR, STATE_FILE_PATH, FORCE_LOCAL_DATA
)
try:
  from dogebot import local_store
except Exception:
  try:
    import dogebot.local_store as local_store
  except Exception:
    local_store = None

# Dashboard-specific runtime structures
MAX_HISTORY = int(os.getenv("DASH_MAX_HISTORY", "10000"))
from collections import deque as _deque
PRICE_WINDOW = _deque([], maxlen=MAX_HISTORY)
HISTORY_LOCK = threading.Lock()
SPLIT_TRIGGER_ENV = PROFIT_SPLIT_TRIGGER_USD
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
      # Important: don't fetch SAPI currencies during load_markets (needs extra perms and can fail for some users)
      "fetchCurrencies": False,
    },
  }
  if API_KEY and API_SECRET:
    kwargs["apiKey"] = API_KEY
    kwargs["secret"] = API_SECRET
  ex = Cls(kwargs)
  try:
    ex.load_markets()  # No params (avoids -1104)
  except Exception as e:
    print(f"[WARN] load_markets failed: {e}")
  return ex

CLIENT = make_client()
_PUBLIC_FALLBACK_CLIENT = None  # created lazily if auth client fails for public ticker

# Dynamic credential reload support
_CLIENT_LOCK = threading.Lock()
_LAST_KEYS = (API_KEY or "", API_SECRET or "", BINANCE_REGION)

def _maybe_reload_client():
  """Reload ccxt client if .env keys changed (allows updating keys without restart).

  We re-read environment (.env) and compare current API key/secret/region. If changed,
  rebuild CLIENT (and reset fallback) under lock.
  """
  global CLIENT, API_KEY, API_SECRET, BINANCE_REGION, _PUBLIC_FALLBACK_CLIENT, _LAST_KEYS
  try:
    # Re-load .env (best effort)
    _load_dotenv_for_reload(os.getenv("ENV_FILE") or os.path.expanduser("~/doge_bot/.env"), override=False)
  except Exception:
    pass
  new_key = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY") or API_KEY
  new_secret = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET") or API_SECRET
  new_region = os.getenv("BINANCE_REGION", BINANCE_REGION)
  current = (new_key or "", new_secret or "", new_region)
  if current == _LAST_KEYS:
    return
  with _CLIENT_LOCK:
    if current != _LAST_KEYS:  # re-check after acquiring
      _LAST_KEYS = current
      API_KEY = new_key
      API_SECRET = new_secret
      BINANCE_REGION = new_region
      try:
        CLIENT = make_client()  # rebuild
        _PUBLIC_FALLBACK_CLIENT = None
        print("[INFO] Rebuilt Binance client after key/region change")
      except Exception as e:
        print(f"[WARN] Failed to rebuild client: {e}")

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
  # If the bot writes stats here they will display; otherwise zeros are shown.
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
  # Optional extra profit fields:
        "realized_profit_usd": 0.0,
        "unrealized_profit_usd": 0.0,
        "grid_profit_usd": 0.0,
        "fees_usd": 0.0,
        "profit_pct": 0.0,
  # The trigger can also be written directly by the bot:
        "split_trigger_usd": SPLIT_TRIGGER_ENV,
  # total_profit_usd alternative if present:
        "total_profit_usd": 0.0,
    }

_load_history_file()

# =========================================================
# LIVE PRICE & LIVE STATS (SSE)
# =========================================================

_current_price = None
_current_ts_ms = None
_current_price_source = None  # 'auth', 'public', 'history'
_sse_stop = threading.Event()

_stats_mtime = None
_stats_cache = None

def record_price_point(price: float, ts_ms: Optional[int] = None, source: Optional[str] = None):
  """Append price point to history (memory + disk) and update current.

  source: str (auth|public|history) for UI badge.
  """
  global _current_price, _current_ts_ms, _current_price_source
  if ts_ms is None:
    ts_ms = int(time.time() * 1000)
  pt = {"t": int(ts_ms), "p": float(price)}
  with HISTORY_LOCK:
    PRICE_WINDOW.append(pt)
    _save_history_file()
  _current_price = float(price)
  _current_ts_ms = int(ts_ms)
  if source:
    _current_price_source = source

def _seed_initial_price():
  """Seed initial current price so dashboard shows a value immediately.

  Order of attempts:
  1. Use last point from loaded PRICE_WINDOW history (if any)
  2. Try auth client fetch_ticker
  3. Try public fallback client fetch_ticker
  Silently ignore failures; dashboard will rely on poller later.
  """
  global _current_price, _current_ts_ms, _PUBLIC_FALLBACK_CLIENT, _current_price_source
  if _current_price is not None:
    return
  # 1. history
  try:
    if PRICE_WINDOW:
      last = PRICE_WINDOW[-1]
      _current_price = float(last["p"])
      _current_ts_ms = int(last["t"])
      _current_price_source = 'history'
      return
  except Exception:
    pass
  # 2. auth client
  try:
    t = CLIENT.fetch_ticker(PAIR)
    price = t.get("last") or t.get("close") or t.get("bid") or t.get("ask")
    if price:
      record_price_point(price, source='auth')
      return
  except Exception:
    pass
  # 3. public client
  try:
    if _PUBLIC_FALLBACK_CLIENT is None:
      if BINANCE_REGION == "us":
        Cls = ccxt.binanceus
      else:
        Cls = ccxt.binance
      _PUBLIC_FALLBACK_CLIENT = Cls({
        "enableRateLimit": True,
        "options": {"defaultType": "spot", "fetchCurrencies": False},
      })
      try:
        _PUBLIC_FALLBACK_CLIENT.load_markets()
      except Exception:
        pass
    t2 = _PUBLIC_FALLBACK_CLIENT.fetch_ticker(PAIR)
    p2 = t2.get("last") or t2.get("close") or t2.get("bid") or t2.get("ask")
    if p2:
      record_price_point(p2, source='public')
  except Exception:
    pass

def _price_poller():
  """Fetch latest price every few seconds to keep chart moving (even without bot)."""
  while not _sse_stop.is_set():
    try:
      _maybe_reload_client()
      t = CLIENT.fetch_ticker(PAIR)
      price = t.get("last") or t.get("close") or t.get("bid") or t.get("ask")
      if price:
        record_price_point(price, source='auth')
    except Exception:
      global _PUBLIC_FALLBACK_CLIENT
      try:
        if _PUBLIC_FALLBACK_CLIENT is None:
          if BINANCE_REGION == "us":
            Cls = ccxt.binanceus
          else:
            Cls = ccxt.binance
          _PUBLIC_FALLBACK_CLIENT = Cls({
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "fetchCurrencies": False},
          })
          try:
            _PUBLIC_FALLBACK_CLIENT.load_markets()
          except Exception:
            pass
        t2 = _PUBLIC_FALLBACK_CLIENT.fetch_ticker(PAIR)
        p2 = t2.get("last") or t2.get("close") or t2.get("bid") or t2.get("ask")
        if p2:
          record_price_point(p2, source='public')
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
            payload = {"t": _current_ts_ms or int(time.time() * 1000), "p": _current_price, "s": _current_price_source}
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
                # Decide which profit metric to show: prefer total_profit_usd else fallback to cumulative_profit_usd
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
_seed_initial_price()
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
  # Also return all individual profit components if present
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

def _extract_binance_code(exc: Exception) -> dict:
  """Attempt to extract Binance error code/message from ccxt exception text/info."""
  code = None
  msg = None
  try:
    # ccxt exceptions often have .args[0] as a JSON-ish string
    txt = str(exc)
    # simple patterns
    import re
    m = re.search(r'"code"\s*:\s*(-?\d+)', txt)
    if not m:
      m = re.search(r"'code'\s*:\s*(-?\d+)", txt)
    if m:
      code = int(m.group(1))
    mm = re.search(r'"msg"\s*:\s*"(.*?)"', txt)
    if not mm:
      mm = re.search(r"'msg'\s*:\s*'([^']+)" , txt)
    if mm:
      msg = mm.group(1)
  except Exception:
    pass
  return {"binance_code": code, "binance_msg": msg}

@app.get("/api/open_orders")
def api_open_orders():
  if not _auth_available():
    if local_store:
      return {"ok": True, "source": "local", "orders": local_store.list_open_orders()}
    return {"ok": False, "error": "No API key/secret configured", "orders": []}
  try:
    _maybe_reload_client()
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
    details = _extract_binance_code(e)
    # On auth failure (-2015) or any error, fallback to local
    if local_store:
      loc = local_store.list_open_orders()
      if loc:
        return {"ok": True, "source": "local", "orders": loc, "error": str(e), **details}
    return {"ok": False, "error": str(e), **details, "orders": []}

@app.get("/api/order_history")
def api_order_history():
  """Return order history with optional merge & enrichment.

  Query params:
    limit: max rows to return (default 500)
    full=1: raise cap to 2000
    include=trades,state: force enrichment sources
    merge=0: disable merge with local history file
    enrich=0: disable enrichment (trades/state)
    statuses=all or comma list: filter statuses (default closed,filled,canceled)
    debug=1: include debug stats
  """
  display_limit = int(request.args.get("limit", "500") or 500)
  want_full = request.args.get("full") in ("1","true","True")
  max_return = 2000 if want_full else max(100, min(display_limit, 2000))
  include_arg = request.args.get("include", "").strip().lower()
  include_set = {p.strip() for p in include_arg.split(',') if p.strip()}
  merge_enabled = request.args.get("merge", "1") not in ("0","false","False")
  enrich_enabled = request.args.get("enrich", "1") not in ("0","false","False")
  debug_mode = request.args.get("debug") in ("1","true","True")
  statuses_param = request.args.get("statuses", "")
  if statuses_param:
    if statuses_param.lower() == 'all':
      allowed_statuses = None
    else:
      allowed_statuses = {s.strip().lower() for s in statuses_param.split(',') if s.strip()}
      if not allowed_statuses:
        allowed_statuses = {"closed","filled","canceled"}
  else:
    allowed_statuses = {"closed","filled","canceled"}
  ENRICH_THRESHOLD = 40

  # Local-only path (no auth or forced)
  if not _auth_available() or FORCE_LOCAL_DATA:
    if local_store:
      rows = local_store.list_history()
      return {"ok": True, "source": "local", "orders": rows[-max_return:], "total": len(rows)}
    return {"ok": False, "error": "No API key/secret configured", "orders": []}

  all_rows: list[dict] = []
  raw_total = 0
  filtered_out = 0
  batch_meta = [] if debug_mode else None
  try:
    _maybe_reload_client()
    end_time = None
    batch_limit = 100
    for _ in range(25):
      params = {"recvWindow": RECV_WINDOW}
      if end_time is not None:
        params["endTime"] = end_time - 1
      try:
        batch = CLIENT.fetch_orders(PAIR, limit=batch_limit, params=params) or []
      except Exception as e_fetch:
        if not all_rows:
          raise e_fetch
        break
      if not batch:
        break
      oldest_seen = None
      raw_total += len(batch)
      for o in batch:
        status = (o.get("status") or "").lower()
        if allowed_statuses is not None and status not in allowed_statuses:
          filtered_out += 1
          continue
        placement_ts = o.get("timestamp") or o.get("datetime")
        exec_ts = o.get("lastTradeTimestamp") or (o.get("info", {}) if o.get("info") else {}).get("updateTime") or placement_ts
        if isinstance(placement_ts,(int,float)):
          placement_iso = datetime.utcfromtimestamp(placement_ts/1000.0).isoformat()+"Z"
        else:
          placement_iso = str(placement_ts)
        if isinstance(exec_ts,(int,float)):
          exec_iso = datetime.utcfromtimestamp(exec_ts/1000.0).isoformat()+"Z"
        else:
          exec_iso = str(exec_ts)
        price = float(o.get("price") or o.get("average") or 0)
        amount = float(o.get("amount") or o.get("filled") or 0)
        all_rows.append({
          "id": o.get("id") or o.get("clientOrderId") or o.get("orderId"),
          "time": placement_iso,
          "execution_time": exec_iso,
          "side": o.get("side"),
          "price": price,
          "amount": amount,
          "value_usdt": price * amount,
          "status": status,
        })
        if isinstance(placement_ts,(int,float)):
          if oldest_seen is None or placement_ts < oldest_seen:
            oldest_seen = placement_ts
      if oldest_seen is None:
        break
      end_time = oldest_seen
      if len(all_rows) >= max_return:
        break
      if len(all_rows) > 500:
        batch_limit = 200
      if debug_mode:
        batch_meta.append({"fetched": len(batch), "kept": len(all_rows), "oldest_ts": oldest_seen})

    enrich_sources = []
    # Enrich with trades
    if enrich_enabled and ((len(all_rows) < ENRICH_THRESHOLD) or ("trades" in include_set)):
      try:
        trades = CLIENT.fetch_my_trades(PAIR, limit=1000, params={"recvWindow": RECV_WINDOW}) or []
        seen = { (r.get('id'), r.get('time'), r.get('side'), r.get('status')) for r in all_rows }
        added = 0
        for t in trades:
          exec_ts = t.get("timestamp") or t.get("datetime")
          if isinstance(exec_ts,(int,float)):
            exec_iso = datetime.utcfromtimestamp(exec_ts/1000.0).isoformat()+"Z"
          else:
            exec_iso = str(exec_ts)
          price = float(t.get("price") or 0)
          amount = float(t.get("amount") or 0)
          row = {
            "id": t.get("id") or t.get("tradeId"),
            "time": "—",
            "execution_time": exec_iso,
            "side": t.get("side"),
            "price": price,
            "amount": amount,
            "value_usdt": price * amount,
            "status": "done",
          }
          key = (row['id'], row['time'], row['side'], row['status'])
          if key in seen:
            continue
          all_rows.append(row)
          seen.add(key)
          added += 1
        if added:
          enrich_sources.append(f"trades(+{added})")
      except Exception:
        pass

    # Enrich with state sells
    if enrich_enabled and ((len(all_rows) < ENRICH_THRESHOLD) or ("state" in include_set)):
      try:
        if STATE_FILE_PATH and os.path.exists(STATE_FILE_PATH):
          with open(STATE_FILE_PATH,'r',encoding='utf-8') as f:
            st = json.load(f)
          sell_fills = (st or {}).get('sell_fills', {})
          seen = { (r.get('id'), r.get('time'), r.get('side'), r.get('status')) for r in all_rows }
          added = 0
          for sid, info in sell_fills.items():
            price = float(info.get('price',0.0))
            amt = float(info.get('amount',0.0))
            row = {
              'id': sid,
              'time': '—',
              'execution_time': '—',
              'side': 'sell',
              'price': price,
              'amount': amt,
              'value_usdt': price * amt,
              'status': 'done'
            }
            key = (row['id'], row['time'], row['side'], row['status'])
            if key in seen:
              continue
            all_rows.append(row)
            seen.add(key)
            added += 1
          if added:
            enrich_sources.append(f"state(+{added})")
      except Exception:
        pass

    source = "live"
    if merge_enabled and local_store and hasattr(local_store, 'merge_history'):
      merged = local_store.merge_history(all_rows)
      total = len(merged)
      resp = {"ok": True, "source": source, "orders": merged[-max_return:], "total": total, "enrich": enrich_sources}
    else:
      total = len(all_rows)
      resp = {"ok": True, "source": source, "orders": all_rows[-max_return:], "total": total, "enrich": enrich_sources}
    if debug_mode:
      resp['debug'] = {
        'raw_total': raw_total,
        'filtered_out': filtered_out,
        'kept': total,
        'allowed_statuses': None if allowed_statuses is None else sorted(list(allowed_statuses)),
        'batches': batch_meta,
      }
    return resp
  except Exception as e:
    details = _extract_binance_code(e)
    try:
      trades = CLIENT.fetch_my_trades(PAIR, limit=100, params={"recvWindow": RECV_WINDOW}) or []
      parsed = []
      for t in trades:
        exec_ts = t.get("timestamp") or t.get("datetime")
        if isinstance(exec_ts,(int,float)):
          exec_iso = datetime.utcfromtimestamp(exec_ts/1000.0).isoformat()+"Z"
        else:
          exec_iso = str(exec_ts)
        price = float(t.get("price") or 0)
        amount = float(t.get("amount") or 0)
        parsed.append({
          'id': t.get('id') or t.get('tradeId'),
          'time': '—',
          'execution_time': exec_iso,
          'side': t.get('side'),
          'price': price,
          'amount': amount,
          'value_usdt': price * amount,
          'status': 'done'
        })
      if merge_enabled and local_store and hasattr(local_store,'merge_history') and parsed:
        merged = local_store.merge_history(parsed)
        return {"ok": True, "source": "trades_fallback", "orders": merged[-max_return:], "error": str(e), **details, "total": len(merged)}
      return {"ok": True, "source": "trades_fallback", "orders": parsed[-max_return:], "error": str(e), **details, "total": len(parsed)}
    except Exception as e2:
      details2 = _extract_binance_code(e2)
      if local_store:
        loc = local_store.list_history()
        if loc:
          return {"ok": True, "source": "local", "orders": loc[-max_return:], "error": str(e2), **details2, "total": len(loc)}
      try:
        if STATE_FILE_PATH and os.path.exists(STATE_FILE_PATH):
          with open(STATE_FILE_PATH,'r',encoding='utf-8') as f:
            st = json.load(f)
          sell_fills = (st or {}).get('sell_fills', {})
          synth = []
          for sid, info in sell_fills.items():
            price = float(info.get('price',0.0))
            amt = float(info.get('amount',0.0))
            synth.append({
              'id': sid,
              'time': '—',
              'execution_time': '—',
              'side': 'sell',
              'price': price,
              'amount': amt,
              'value_usdt': price * amt,
              'status': 'done'
            })
          if synth:
            if merge_enabled and local_store and hasattr(local_store,'merge_history'):
              merged = local_store.merge_history(synth)
              return {"ok": True, "source": "synthetic_state", "orders": merged[-max_return:], "error": str(e2), **details2, "total": len(merged)}
            return {"ok": True, "source": "synthetic_state", "orders": synth[-max_return:], "error": str(e2), **details2, "total": len(synth)}
      except Exception:
        pass
  return {"ok": False, "error": str(e2), **details2, "orders": [], "total": 0}

@app.get("/api/auth_status")
def api_auth_status():
  """Report current auth health and whether keys are loaded.

  Returns fields:
    has_keys: bool
    last_error: str|None
    region: str
    using_public_fallback: bool
  """
  has_keys = bool(API_KEY and API_SECRET)
  health = {
    "has_keys": has_keys,
    "region": BINANCE_REGION,
    "using_public_fallback": _PUBLIC_FALLBACK_CLIENT is not None,
    "last_error": None,
  }
  if has_keys:
    try:
      _maybe_reload_client()
      CLIENT.check_required_credentials()
      # lightweight private call: fetch_balance (may raise -2015 fast)
      CLIENT.fetch_status()  # uses public; cheaper than balance
    except Exception as e:
      health["last_error"] = str(e)[:200]
  return health

@app.post("/api/reload_keys")
def api_reload_keys():
  _maybe_reload_client()
  return {"ok": True, "has_keys": bool(API_KEY and API_SECRET), "region": BINANCE_REGION}


def _is_request_allowed(req) -> bool:
  """Allow only localhost or requests with correct admin token header/query param.

  Set DASH_ADMIN_TOKEN in environment to allow remote requests via header `X-ADMIN-TOKEN`.
  """
  try:
    addr = req.remote_addr
    if addr in ("127.0.0.1", "::1", "localhost"):
      return True
    token = req.headers.get('X-ADMIN-TOKEN') or req.args.get('token')
    if token and token == os.getenv('DASH_ADMIN_TOKEN'):
      return True
  except Exception:
    pass
  return False


@app.post('/api/recompute_pnl')
def api_recompute_pnl():
  """Run the local recompute script and return summary + CSV rows.

  Protected: only localhost or requests providing correct DASH_ADMIN_TOKEN.
  """
  if not _is_request_allowed(request):
    return jsonify({'ok': False, 'error': 'forbidden'}), 403
  try:
    base = pathlib.Path(__file__).resolve().parent
    script = base / 'scripts' / 'recompute_pnl.py'
    if not script.exists():
      return jsonify({'ok': False, 'error': 'recompute script missing', 'path': str(script)})
    # Run script (short timeout)
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30)
    stdout = proc.stdout or ''
    stderr = proc.stderr or ''
    csv_path = base / 'data' / 'pnl_recompute_fifo.csv'
    if not csv_path.exists():
      return jsonify({'ok': False, 'error': 'csv missing after run', 'stdout': stdout, 'stderr': stderr})
    # read CSV
    import csv as _csv
    rows = []
    with csv_path.open('r', encoding='utf-8') as f:
      for r in _csv.DictReader(f):
        rows.append(r)
    total_profit = sum(float(r.get('profit_usd') or 0) for r in rows)
    # small summary
    summary = {
      'rows': len(rows),
      'total_profit': round(total_profit, 8),
      'csv_path': str(csv_path),
      'stdout': stdout[:2000],
      'stderr': stderr[:2000],
    }
    return jsonify({'ok': True, 'summary': summary, 'rows': rows})
  except subprocess.TimeoutExpired:
    return jsonify({'ok': False, 'error': 'script timeout'})
  except Exception as e:
    return jsonify({'ok': False, 'error': str(e)})


@app.get('/recompute_report')
def recompute_report():
  """Human-readable HTML report for the last recompute CSV.

  Protected similar to the POST endpoint.
  """
  if not _is_request_allowed(request):
    return make_response('forbidden', 403)
  try:
    base = pathlib.Path(__file__).resolve().parent
    csv_path = base / 'data' / 'pnl_recompute_fifo.csv'
    if not csv_path.exists():
      return make_response('No recompute CSV found. Run POST /api/recompute_pnl first.', 404)
    import csv as _csv
    rows = []
    with csv_path.open('r', encoding='utf-8') as fh:
      for r in _csv.DictReader(fh):
        rows.append(r)
    total_profit = sum(float(r.get('profit_usd') or 0) for r in rows)
    # Build a simple HTML table
    html = ['<h2>Recompute P&L Report</h2>']
    html.append(f'<p>Total rows: {len(rows)} — Total realized profit: {round(total_profit,8)} USD</p>')
    # include download link
    html.append(f'<p><a href="/api/pnl_csv" target="_blank">Download CSV</a></p>')
    html.append('<table border="1" cellpadding="4" style="border-collapse:collapse;font-family:monospace">')
    html.append('<tr><th>buy_id</th><th>buy_time</th><th>buy_price</th><th>chunk</th><th>sell_id</th><th>sell_time</th><th>sell_price</th><th>profit_usd</th></tr>')
    for r in rows:
      html.append('<tr>')
      html.append('<td>%s</td>' % (r.get('buy_id') or ''))
      html.append('<td>%s</td>' % (r.get('buy_time') or ''))
      html.append('<td>%.8f</td>' % (float(r.get('buy_price') or 0)))
      html.append('<td>%.8f</td>' % (float(r.get('buy_amount_chunk') or 0)))
      html.append('<td>%s</td>' % (r.get('sell_id') or ''))
      html.append('<td>%s</td>' % (r.get('sell_time') or ''))
      html.append('<td>%.8f</td>' % (float(r.get('sell_price') or 0)))
      html.append('<td>%.8f</td>' % (float(r.get('profit_usd') or 0)))
      html.append('</tr>')
    html.append('</table>')
    return render_template_string('\n'.join(html))
  except Exception as e:
    return make_response(f'error: {e}', 500)


@app.get('/api/pnl_csv')
def api_pnl_csv():
  if not _is_request_allowed(request):
    return jsonify({'ok': False, 'error': 'forbidden'}), 403
  base = pathlib.Path(__file__).resolve().parent
  csv_path = base / 'data' / 'pnl_recompute_fifo.csv'
  if not csv_path.exists():
    return jsonify({'ok': False, 'error': 'missing_csv'}), 404
  return send_file(str(csv_path), mimetype='text/csv', as_attachment=True, download_name='pnl_recompute_fifo.csv')

@app.get("/api/diagnose_auth")
def api_diagnose_auth():
  """Perform deeper auth diagnostics to help get real (non-fallback) data.

  Tries a sequence of private endpoints and reports individual results. This helps
  distinguish common causes of -2015 (bad key / IP / permissions) vs timing issues.
  """
  steps = []
  if not _auth_available():
    return {"ok": False, "error": "missing_keys", "hint": "Set BINANCE_TRADE_KEY / BINANCE_TRADE_SECRET then POST /api/reload_keys"}
  _maybe_reload_client()
  # 1. check credentials object
  try:
    CLIENT.check_required_credentials()
    steps.append({"step": "check_required_credentials", "ok": True})
  except Exception as e:
    steps.append({"step": "check_required_credentials", "ok": False, "error": str(e)})
  # 2. fetch_time (public)
  try:
    server_time = CLIENT.fetch_time()
    steps.append({"step": "fetch_time", "ok": True, "server_time": server_time})
  except Exception as e:
    steps.append({"step": "fetch_time", "ok": False, "error": str(e)})
  # 3. fetch_status (public)
  try:
    status = CLIENT.fetch_status()
    steps.append({"step": "fetch_status", "ok": True, "status": status})
  except Exception as e:
    steps.append({"step": "fetch_status", "ok": False, "error": str(e)})
  # 4. private call: fetch_balance
  try:
    bal = CLIENT.fetch_balance(params={"recvWindow": RECV_WINDOW})
    steps.append({"step": "fetch_balance", "ok": True, "keys": list(bal.keys())[:8]})
  except Exception as e:
    info = _extract_binance_code(e)
    steps.append({"step": "fetch_balance", "ok": False, "error": str(e), **info})
  # 5. private call: fetch_open_orders
  try:
    oo = CLIENT.fetch_open_orders(PAIR, params={"recvWindow": RECV_WINDOW})
    steps.append({"step": "fetch_open_orders", "ok": True, "count": len(oo)})
  except Exception as e:
    info = _extract_binance_code(e)
    steps.append({"step": "fetch_open_orders", "ok": False, "error": str(e), **info})
  # 6. private call: fetch_my_trades (limit=1)
  try:
    tr = CLIENT.fetch_my_trades(PAIR, limit=1, params={"recvWindow": RECV_WINDOW})
    steps.append({"step": "fetch_my_trades", "ok": True, "count": len(tr)})
  except Exception as e:
    info = _extract_binance_code(e)
    steps.append({"step": "fetch_my_trades", "ok": False, "error": str(e), **info})
  # summarize
  overall_ok = all(s.get("ok") for s in steps if s["step"] not in ("fetch_open_orders","fetch_my_trades"))
  return {"ok": overall_ok, "steps": steps, "region": BINANCE_REGION}

@app.post("/api/stop_bot")
def api_stop_bot():
    print("[API] stop bot requested")
    return {"ok": True}

@app.post("/api/resume_bot")
def api_resume_bot():
    print("[API] resume bot requested")
    return {"ok": True}


@app.get('/api/bot_status')
def api_bot_status():
  """Return whether the bot is running on this host.

  Checks for a tmux session named 'doge' if tmux is available, else
  checks for a running 'main.py' python process.
  """
  import shutil, subprocess
  running = False
  method = 'none'
  try:
    if shutil.which('tmux'):
      res = subprocess.run(['tmux', 'has-session', '-t', 'doge'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
      if res.returncode == 0:
        running = True
        method = 'tmux'
    if not running:
      # Fallback: look for a running python main.py process
      if shutil.which('pgrep'):
        p = subprocess.run(['pgrep', '-f', 'main.py'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if p.returncode == 0 and p.stdout.strip():
          running = True
          method = 'proc'
  except Exception:
    pass
  return { 'running': bool(running), 'method': method }

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
  
  /* Sticky X-axis feature styles */
  .sticky-x-axis {
    position: sticky;
    bottom: 0;
    background: white;
    border-top: 1px solid #eee;
    z-index: 10;
    padding: 4px 0;
  }
  
  .chart-container.sticky-mode {
    position: relative;
  }
  
  /* Make Plotly's x-axis layers sticky so axis labels remain visible when scrolling */
  .chart-container.sticky-mode .plotly .xaxislayer-above,
  .chart-container.sticky-mode .plotly .xaxislayer-below,
  .chart-container.sticky-mode .plotly .xaxis,
  .chart-container.sticky-mode .plotly .xtick {
    position: sticky;
    bottom: 0;
    z-index: 20;
    background: white; /* keep labels readable against plot */
    transform: translateZ(0);
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  
  .hidden { display: none; }
  .subnote { font-size:12px; color:var(--muted); margin-top:4px; }
  .sections { display:grid; gap:12px; }
  details { background:var(--card); border:1px solid var(--grid); border-radius:12px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  details > summary { cursor:pointer; padding:12px 14px; font-weight:600; list-style:none; display:flex; align-items:center; gap:8px; user-select:none; }
  details > summary::before { content: '▸'; font-size:20px; font-weight: bold; color:var(--muted); transition: transform .15s ease; }
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
  .src-badge { display:inline-block; margin-left:6px; padding:2px 6px; font-size:10px; border-radius:6px; background:#edf2f7; color:#2d3748; border:1px solid #cbd5e0; font-weight:600; letter-spacing:.5px; text-transform:uppercase; }
  .src-badge.local { background:#fffbea; border-color:#fbd38d; color:#975a16; }
  .src-badge.synthetic_state { background:#ebf8ff; border-color:#90cdf4; color:#2b6cb0; }
  .src-badge.error { background:#ffecec; border-color:#feb2b2; color:#c53030; }
  .src-badge.hidden { display:none; }
  .auth-ok { background:#e6fffa; border-color:#81e6d9; color:#046c4e; }
  .auth-missing { background:#fffbea; border-color:#fbd38d; color:#975a16; }
  .auth-error { background:#ffecec; border-color:#feb2b2; color:#c53030; }
</style>
<style>
/* Modal for recompute report */
.recompute-modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); align-items:center; justify-content:center; z-index:9999; }
.recompute-modal .panel { background:#fff; padding:16px; border-radius:8px; width:90%; max-width:1100px; max-height:80vh; overflow:auto; box-shadow:0 6px 24px rgba(0,0,0,0.25); }
.recompute-modal .panel .close { float:right; cursor:pointer; font-size:18px; padding:4px 8px; border-radius:4px; }
.recompute-modal pre { white-space:pre-wrap; font-family:monospace; font-size:13px }
</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
  <h1>DOGE Grid Monitor — <span id="pair" class="mono"></span> <span id="lastUpdated" class="last-update">Last updated —</span> <span id="authStatus" class="src-badge hidden" title="Authentication status"></span></h1>
      <div class="top-actions">
        <div class="toolbar-group">
          <button id="btnRefresh" class="icon-btn" title="Refresh all data (orders, history, stats)">🔄</button>
          <button id="btnRecompute" class="icon-btn" title="Recompute realized P&L and generate report">📊</button>
          <label for="autoRefreshMs" class="small-label" title="Auto refresh interval (ms) for stats & tables">⏱</label>
          <input id="autoRefreshMs" type="number" min="5000" step="1000" value="25000" style="width:90px" title="Auto refresh interval in milliseconds">
          <button id="btnApplyInterval" class="icon-btn" title="Apply interval">✅</button>
          <button id="btnExportCSV" class="icon-btn" title="Download history as CSV">💾</button>
        </div>
  <button id="btnStop" class="icon-btn" title="Stop the trading bot">⏹️</button>
        <button id="btnResume" class="icon-btn" title="Resume the trading bot">▶️</button>
        <button id="btnCancel" class="icon-btn" title="Cancel all open orders">❌</button>
  <button id="btnReloadKeys" class="icon-btn" title="Reload API keys from .env and refresh clients">🔐</button>
      </div>
    </div>

    <!-- Top info cards -->
    <div class="cards">
      <!-- New info boxes for initial investments -->
  <div class="card" data-tooltip="Total starting USDT committed when the bot began (excludes unrealized changes)">
        <h3>Initial USDT Invested</h3>
        <div id="initialUsdtVal" class="v mono">—</div>
      </div>

  <div class="card" data-tooltip="Initial DOGE quantity deposited (shown in DOGE; value at entry used for base exposure calculations)">
        <h3>Initial DOGE Invested</h3>
        <div id="initialDogeVal" class="v mono">—</div>
      </div>

      <!-- Bot Range card -->
  <div class="card" data-tooltip="Configured grid trading range (MIN to MAX price) within which orders are placed">
        <h3>Bot Range</h3>
        <div id="rangeVal" class="v mono">—</div>
  <div class="subnote" data-tooltip="Distance between adjacent grid layers as percentage; total number of active price layers">Layer spacing: <span id="spacingVal">—</span>% • <span id="layersCountVal">—</span> layers</div>
      </div>

  <div class="card" data-tooltip="Latest market price fetched (source badge shows origin: exchange or local)"><h3>Current Price <span id="priceSource" class="src-badge hidden" title="Price source"></span></h3><div id="priceVal" class="v mono">—</div></div>

      <!-- Total Profit card with (profit/trigger) subnote -->
  <div class="card" data-tooltip="Aggregated realized + unrealized profit in USD (updates with fills and mark-to-market)">
        <h3>Total Profit (USD)</h3>
        <div id="profitVal" class="v mono">—</div>
  <div class="subnote" id="profitTriggerNote" data-tooltip="Progress toward next profit split action / chunk threshold">(— / 4.0$ chunk trigger)</div>
      </div>

  <div class="card" data-tooltip="Number of sell executions completed since session start / tracking reset"><h3>Sell Trades Count</h3><div id="sellTradesVal" class="v mono">—</div></div>
  <div class="card" data-tooltip="How many profit split transfers actually executed (successful conversions)"><h3>Actual Splits Count</h3><div id="actualSplitsVal" class="v mono">—</div></div>
    </div>

  <!-- EXTRA profit cards (values only; leave others untouched) -->
    <div class="cards">
  <div class="card" data-tooltip="USD value of DOGE proceeds converted into BNB for fee optimization or bookkeeping"><h3>Converted to BNB (USD)</h3><div id="bnbVal" class="v mono">—</div></div>
  <div class="card" data-tooltip="Profit locked in from closed trades (excludes open position PnL)"><h3>Realized Profit (USD)</h3><div id="profitRealizedVal" class="v mono">—</div></div>
  <div class="card" data-tooltip="Mark-to-market profit on current inventory relative to initial cost basis"><h3>Unrealized Profit (USD)</h3><div id="profitUnrealizedVal" class="v mono">—</div></div>
  <div class="card" data-tooltip="Cumulative profit generated by grid fills alone (excluding conversions/splits)"><h3>Grid Profit (USD)</h3><div id="profitGridVal" class="v mono">—</div></div>
  <div class="card" data-tooltip="Estimated or reported trading fees paid (converted to USD)"><h3>Fees (USD)</h3><div id="feesVal" class="v mono">—</div></div>
  <div class="card" data-tooltip="Overall return percentage relative to initial total capital deployed"><h3>Profit %</h3><div id="profitPctVal" class="v mono">—</div></div>
    </div>

    <div class="sections">
      <!-- Chart -->
      <details open id="chartBox">
        <summary>Price Chart</summary>
        <div class="section-body">
          <style>
            .control-row { display:flex; gap:12px; margin-bottom:8px; flex-wrap:wrap; align-items:center; }
            .ctrl { display:flex; align-items:center; gap:6px; user-select:none; }
            .ctrl-marker span { font-family: 'Courier New', monospace; font-weight:500; letter-spacing:0.3px; font-size:11px; color:#ff5c99; opacity:0.85; }
            .control-row .spacer { flex:1 1 auto; }
          </style>
          <!-- Color Legend - Relocated to bottom with feature flag -->
          <div id="chartLegend" style="margin-bottom: 12px; padding: 8px; background: #f8f9fa; border-radius: 6px; font-size: 12px;">
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
          <div class="control-row">
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showGrid" type="checkbox" checked/>
              <span>Show grid layers</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showActiveLayers" type="checkbox"/>
              <span>Show active layers</span>
            </label>
            <!-- activeEmphasis UI removed; active levels are emphasized at the default intensity -->
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showLat" type="checkbox" checked/>
              <span>Show gray latitudes</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="showPriceLine" type="checkbox" checked/>
              <span>Show price line</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="followPrice" type="checkbox"/>
              <span>Follow price</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;user-select:none">
              <input id="autoZoom" type="checkbox"/>
              <span>Auto zoom</span>
            </label>
            <!-- Sticky X-axis control removed; axis will auto-stick when chart is scrollable -->
            <!-- legend toggle removed (always show price curve) -->
          </div>
          <div id="chartScroll" class="chart-container" style="max-height:520px; overflow-y:auto; border:1px solid #eee; border-radius:4px;">
            <div id="chart" style="height:520px;"></div>
          </div>
        </div>
      </details>

      <!-- Open Orders -->
      <details open id="openBox">
        <summary>Open Orders <span id="openCount" class="mono" style="color:var(--muted)">(0)</span><span id="openSourceBadge" class="src-badge hidden" title="Data source"></span></summary>
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
        <summary>Orders History <span id="histCount" class="mono" style="color:var(--muted)">(0)</span><span id="histSourceBadge" class="src-badge hidden" title="Data source"></span></summary>
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

          <div class="table-controls">
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
              <input id="histFilter" placeholder="filter..." />
            </label>
             <button id="btnHistOlder" title="Fetch older history (paginate backwards)">⬇ Older</button>
            <span id="histOlderSpinner" class="spinner" style="display:none">⏳</span>
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

    <!-- Modal for recompute report -->
    <div id="recomputeModal" class="recompute-modal">
      <div class="panel">
        <button id="recomputeClose" class="close">✖</button>
        <div id="recomputeContent">Loading...</div>
      </div>
    </div>

<script>
"use strict";

var showGridEl, showActiveEl, showLatEl, autoZoomEl, showPriceLineEl; // stickyXAxis auto-detect (control removed)

const PAIR = {{ pair|tojson }};
const SPLIT_TRIGGER_ENV = {{ split_trigger_env|tojson }};
const SPLIT_CHUNK_USD = {{ split_chunk_usd|tojson }};
const BASE_ORDER_USD = {{ base_order_usd|tojson }};
const MAX_USD_FOR_CYCLE = {{ max_usd_for_cycle|tojson }};
document.getElementById('pair').textContent = PAIR;

/* range & spacing from server-side (env), if provided */
const GRID_MIN = {{ grid_min|tojson }};
const GRID_MAX = {{ grid_max|tojson }};
// expose for headless checks and external scripts
try{ window.GRID_MIN = GRID_MIN; window.GRID_MAX = GRID_MAX; }catch(_){ }
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
  const limit = 2000; // safety guard
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
    // Make loading more visible with explicit text and improved styling
    el.innerHTML = '<span class="loading-indicator"></span> Loading...';
    el.style.opacity = '0.7';
    el.style.pointerEvents = 'none';
  }
}

function clearLoadingState(id) {
  cardLoadingStates.delete(id);
  initializedCards.add(id);
  const el = document.getElementById(id);
  if (el) {
    // Restore normal state
    el.style.opacity = '1';
    el.style.pointerEvents = 'auto';
  }
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
  // Only show loading indicators the first time (avoid flicker / delay perception)
  if(!isCardInitialized('profitVal') && !isCardLoading('profitVal')){
    setLoadingState('profitVal');
  }
  if(!isCardInitialized('sellTradesVal') && !isCardLoading('sellTradesVal')){
    setLoadingState('sellTradesVal');
  }
  
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
    hoverformat: ".6f",
        // range set below dynamically
      },
      paper_bgcolor:'rgba(0,0,0,0)',
      plot_bgcolor:'rgba(0,0,0,0)',
      shapes: []
    };
    // Apply dynamic padded range
    if (GRID_MIN != null && GRID_MAX != null) {
      layout.yaxis.range = (function(){
        const span = GRID_MAX - GRID_MIN;
        const pad = span * 0.03; // 3% padding
        return [GRID_MIN - pad, GRID_MAX + pad];
      })();
    }
    const data = [
      { x: xs, y: ys, mode:'lines', name: PAIR, line:{width:1.5,color:'#1f77b4'} },
  { x:[xs[xs.length-1]], y:[ys[ys.length-1]], mode:'markers', name:'price', marker:{color:'#ff0066', size:10, line:{color:'#fff', width:1}}, hoverinfo:'none', visible:true }
    ];
    
  console.log('DEBUG: About to call Plotly.react. Data:', data, 'Layout:', layout);
  console.log('Creating chart with', data[0].x.length, 'data points');
  await Plotly.react('chart', data, layout, {displayModeBar:false});
    _chartReady = true;
    setupDynamicTimeLabels();  // Setup event handlers after chart is ready
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
        [
          {x:[], y:[], mode:'lines', name: PAIR, line:{width:1.5,color:'#1f77b4'}},
          {x:[], y:[], mode:'markers', name:'price', marker:{color:'#ff0066', size:10, line:{color:'#fff', width:1}}, hoverinfo:'none', visible:true }
        ],
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
      setupDynamicTimeLabels();  // Setup event handlers after fallback chart is ready
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
  const scrollWrap = document.getElementById('chartScroll');

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
    // Show evenly spaced light-gray latitude lines across the configured grid range.
    if (GRID_MIN != null && GRID_MAX != null && GRID_MAX > GRID_MIN) {
      const LAT_COUNT = 10; // number of intervals -> LAT_COUNT-1 intermediate lines
      const span = GRID_MAX - GRID_MIN;
      for (let i = 1; i < LAT_COUNT; i++) {
        const y = GRID_MIN + (span * i / LAT_COUNT);
        // Skip exact boundaries (they are added earlier)
        if (Math.abs(y - GRID_MIN) < 1e-12 || Math.abs(y - GRID_MAX) < 1e-12) continue;
        shapes.push(shapeForY(y, 'rgba(200,200,200,0.9)', 1, 'solid'));
        yTicksVals.push(y);
      }
    } else {
      // Fallback: reuse grid levels if available
      try {
        const allLevels = buildAllLevels();
        for (const y of allLevels) {
          if (y === GRID_MIN || y === GRID_MAX) continue;
          shapes.push(shapeForY(y, 'rgba(200,200,200,0.9)', 1, 'solid'));
          yTicksVals.push(y);
        }
      } catch (e) {
        // ignore if buildAllLevels unavailable
      }
    }
  } else if (mode === 'active') {
        // Active mode: ONLY render active order horizontal lines and ticks.
        // Do not show grid-level shapes or ticks for inactive layers.
        const activeOrders = OPEN_ORDERS_RAW.map(o => o.price).filter(p => isFinite(p)).sort((a, b) => a - b);
        const { below, above } = nearestBracket(activeOrders, currentPrice);
        // Add shapes for active orders only
        for (const p of activeOrders) {
          const isNearest = (p === below || p === above);
          const color = isNearest ? 'rgba(255, 165, 0, 0.95)' : 'rgba(255, 165, 0, 0.65)';
          const width = isNearest ? 2.5 : 1.6;
          const dash = isNearest ? 'longdash' : 'dash';
          shapes.push(shapeForY(p, color, width, dash));
          yTicksVals.push(p);
        }
  } else { // 'grid' mode is the default
    const allLevels = buildAllLevels();
    // Gather active order prices to highlight them within grid mode
    const activeOrders = OPEN_ORDERS_RAW.map(o => o.price).filter(p=>isFinite(p)).sort((a,b)=>a-b);
    const activeSet = new Set(activeOrders.map(a=>Number(a)));
    const { below: activeBelow, above: activeAbove } = nearestBracket(activeOrders, currentPrice);
    for (const y of allLevels) {
      if (y === GRID_MIN || y === GRID_MAX) continue;
      // If this level corresponds to an active order, emphasize it
      const isActiveLevel = Array.from(activeSet).some(a => Math.abs(a - y) <= (Math.abs(y) * 1e-12 + 1e-12));
  if (isActiveLevel) {
        // nearest active orders are more prominent
        const isNearestActive = (Math.abs(y - (activeBelow || NaN)) <= (Math.abs(y) * 1e-12 + 1e-12)) || (Math.abs(y - (activeAbove || NaN)) <= (Math.abs(y) * 1e-12 + 1e-12));
  // activeEmphasis UI removed; use fixed medium emphasis values
  let alpha = isNearestActive ? 0.95 : 0.65;
  let width = isNearestActive ? 2.5 : 1.6;
        const color = `rgba(255, 165, 0, ${alpha.toFixed(3)})`;
        const dash = isNearestActive ? 'longdash' : 'dash';
        shapes.push(shapeForY(y, color, width, dash));
      } else {
        const isBuy = (y <= (currentPrice ?? 0));
        const color = isBuy ? 'rgba(46, 204, 113, 0.6)' : 'rgba(243, 156, 18, 0.6)';
        shapes.push(shapeForY(y, color, 1, 'dash'));
      }
      yTicksVals.push(y);
    }
  }

  // Add dynamic current price horizontal reference line (always on top)
  if (isFinite(currentPrice)) {
    const priceLineEnabled = (localStorage.getItem('showPriceLine') !== '0');
    if (priceLineEnabled) {
      // Distinct vivid color & slightly thicker for visibility
      shapes.push(shapeForY(currentPrice, 'rgba(255, 0, 102, 0.9)', 2.5, 'solid'));
      yTicksVals.push(currentPrice);
    }
  }

  // (Initial tick list built; finalization happens after boundary-touch augmentation below)
  let yTicksText = [];

  // Persistent tracking: once price touches a purple boundary, keep showing 4 gray latitude lines on that side
  const extraLinesInfo = (function handleBoundaryTouch(){
      if (GRID_MIN == null || GRID_MAX == null) return { minLines: 0, maxLines: 0 };
      if (!window._boundaryTouch) {
    window._boundaryTouch = { lastMin: GRID_MIN, lastMax: GRID_MAX, minTouched: false, maxTouched: false, lastPrice: null, minExtremePrice: null, maxExtremePrice: null };
      }
      const bt = window._boundaryTouch;
      // Reset tracking if grid bounds changed
      if (bt.lastMin !== GRID_MIN || bt.lastMax !== GRID_MAX) {
        bt.lastMin = GRID_MIN;
        bt.lastMax = GRID_MAX;
        bt.minTouched = false;
        bt.maxTouched = false;
    bt.lastPrice = null;
    bt.minExtremePrice = null;
    bt.maxExtremePrice = null;
      }
      const span = (GRID_MAX - GRID_MIN) || 1;
      const tolerance = Math.max(span * 1e-5, 1e-9); // slightly larger tolerance for practical touches
      if (currentPrice != null) {
    // Direct touch
    if (!bt.minTouched && Math.abs(currentPrice - GRID_MIN) <= tolerance) bt.minTouched = true;
    if (!bt.maxTouched && Math.abs(currentPrice - GRID_MAX) <= tolerance) bt.maxTouched = true;
    // Crossing detection using last price
    if (bt.lastPrice != null) {
      if (!bt.minTouched && bt.lastPrice > GRID_MIN && currentPrice <= GRID_MIN + tolerance) bt.minTouched = true;
      if (!bt.maxTouched && bt.lastPrice < GRID_MAX && currentPrice >= GRID_MAX - tolerance) bt.maxTouched = true;
    }
    // Track extremes beyond boundaries for dynamic extension
    if (currentPrice < GRID_MIN - tolerance) {
      bt.minTouched = true;
      if (bt.minExtremePrice == null || currentPrice < bt.minExtremePrice) bt.minExtremePrice = currentPrice;
    }
    if (currentPrice > GRID_MAX + tolerance) {
      bt.maxTouched = true;
      if (bt.maxExtremePrice == null || currentPrice > bt.maxExtremePrice) bt.maxExtremePrice = currentPrice;
    }
    bt.lastPrice = currentPrice;
      }
      const spacing = span * 0.01; // 1% spacing
      const MIN_LINES = 4; // minimum lines once touched
      // Determine dynamic counts based on extremes reached
      let minCount = 0;
      if (bt.minTouched) {
        if (bt.minExtremePrice != null) {
          const needed = Math.ceil((GRID_MIN - bt.minExtremePrice) / spacing);
          minCount = Math.max(MIN_LINES, needed);
        } else {
          minCount = MIN_LINES;
        }
  // Follow-price mode: if price currently below GRID_MIN, always keep 4 extra layers below price
  const _followMode = (window.followPriceEl && window.followPriceEl.checked);
  if (_followMode && isFinite(currentPrice) && currentPrice < GRID_MIN) {
          const extraNeeded = Math.ceil((GRID_MIN - currentPrice)/spacing) + 4; // distance to price + 4 more
          if (extraNeeded > minCount) minCount = extraNeeded;
        }
  const includeTick = (i)=> _followMode ? (i % 4 === 1) : (i <= 2); // in follow mode, show every 4th tick
        for (let i = 1; i <= minCount; i++) {
          const y = GRID_MIN - spacing * i;
          const maxIdx = minCount;
          const alpha = Math.max(0.15, 0.85 * (1 - (i-1)/(maxIdx)) );
          shapes.push(shapeForY(y, `rgba(153,153,153,${alpha.toFixed(3)})`, 1, 'solid'));
          if (includeTick(i)) yTicksVals.push(y);
        }
      }
      let maxCount = 0;
      if (bt.maxTouched) {
        if (bt.maxExtremePrice != null) {
          const needed = Math.ceil((bt.maxExtremePrice - GRID_MAX) / spacing);
          maxCount = Math.max(MIN_LINES, needed);
        } else {
          maxCount = MIN_LINES;
        }
  // Follow-price mode: if price currently above GRID_MAX, always keep 4 extra layers above price
  const _followModeTop = (window.followPriceEl && window.followPriceEl.checked);
  if (_followModeTop && isFinite(currentPrice) && currentPrice > GRID_MAX) {
          const extraNeededTop = Math.ceil((currentPrice - GRID_MAX)/spacing) + 4;
          if (extraNeededTop > maxCount) maxCount = extraNeededTop;
        }
  const includeTickTop = (i)=> _followModeTop ? (i % 4 === 1) : (i <= 2); // in follow mode, show every 4th tick
        for (let i = 1; i <= maxCount; i++) {
          const y = GRID_MAX + spacing * i;
          const maxIdx = maxCount;
          const alpha = Math.max(0.15, 0.85 * (1 - (i-1)/(maxIdx)) );
          shapes.push(shapeForY(y, `rgba(153,153,153,${alpha.toFixed(3)})`, 1, 'solid'));
          if (includeTickTop(i)) yTicksVals.push(y);
        }
      }
      return { minLines: minCount, maxLines: maxCount };
    })();

  // Finalize ticks AFTER adding any gray boundary-extension lines
  yTicksVals = [...new Set(yTicksVals)].sort((a, b) => a - b);


  // Compute total extra extension lines (no compact badges: removed by user request)
  const extraLinesTotal = (extraLinesInfo && ((extraLinesInfo.minLines||0) + (extraLinesInfo.maxLines||0))) || 0;

  // Soft-thinning: reduce visual weight (alpha/width) of extension-line shapes to reduce perceived density
  function reduceAlpha(col, factor){
    try{
      const m = String(col).match(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\)/);
      if (m){ const r=m[1], g=m[2], b=m[3], a=parseFloat(m[4])*factor; return `rgba(${r}, ${g}, ${b}, ${Math.max(0.03, a).toFixed(3)})`; }
    }catch(_){ }
    return col;
  }

  // Apply thinning to extension shapes (those outside GRID_MIN..GRID_MAX)
  try{
    for (let si=0; si<shapes.length; si++){
      const s = shapes[si];
      if (!s || !('y0' in s)) continue;
      const yv = Number(s.y0);
      if (GRID_MIN != null && GRID_MAX != null && (yv < GRID_MIN - 1e-12 || yv > GRID_MAX + 1e-12)){
        // reduce width and alpha depending on how many extras we have
        const factor = extraLinesTotal > 20 ? 0.35 : (extraLinesTotal > 8 ? 0.55 : 0.75);
        s.line.width = Math.min(1.0, s.line.width || 1.0);
        s.line.color = reduceAlpha(s.line.color || 'rgba(153,153,153,0.6)', factor);
      }
    }
  }catch(_){ }

  // When showing price line, remove other ticks that are too close to avoid overlap
  const priceLineEnabledForCulling = (localStorage.getItem('showPriceLine') !== '0');
  if (priceLineEnabledForCulling && isFinite(currentPrice)) {
    const priceSpan = (GRID_MAX != null && GRID_MIN != null && GRID_MAX > GRID_MIN) ? (GRID_MAX - GRID_MIN) : (currentPrice * 0.02);
    const cullRange = priceSpan * 0.01; // 1.0% of span is the exclusion zone

    const priceTickVal = yTicksVals.find(v => Math.abs(v - currentPrice) <= (currentPrice * 1e-9 + 1e-12));

    if (priceTickVal !== undefined) {
      yTicksVals = yTicksVals.filter(v => {
        // Always keep the price tick itself and grid boundaries
        if (v === priceTickVal) return true;
        if (GRID_MIN != null && Math.abs(v - GRID_MIN) <= 1e-12) return true;
        if (GRID_MAX != null && Math.abs(v - GRID_MAX) <= 1e-12) return true;
        return Math.abs(v - priceTickVal) > cullRange; // Remove ticks that are too close
      });
    }
  }

  // General tick culling to prevent overlap for all other ticks
  if (yTicksVals.length > 20) { // Only apply if there are many ticks
    const priceSpan = (GRID_MAX != null && GRID_MIN != null && GRID_MAX > GRID_MIN) ? (GRID_MAX - GRID_MIN) : (currentPrice * 0.02);
    // Increase separation when there are many extension lines so the area below/above grid is less dense
    const extraLinesTotal = (extraLinesInfo && ((extraLinesInfo.minLines||0) + (extraLinesInfo.maxLines||0))) || 0;
    const sepFactor = extraLinesTotal > 8 ? 0.02 : 0.012;
    const minSeparation = priceSpan * sepFactor; // Minimum separation factor of the span

    const culledTicks = [];
    if (yTicksVals.length > 0) {
      culledTicks.push(yTicksVals[0]);
      let lastTick = yTicksVals[0];
      for (let i = 1; i < yTicksVals.length - 1; i++) {
        const currentTick = yTicksVals[i];
        // Keep grid boundaries and price tick preferentially
        if (GRID_MIN != null && Math.abs(currentTick - GRID_MIN) <= 1e-12) { culledTicks.push(currentTick); lastTick = currentTick; continue; }
        if (GRID_MAX != null && Math.abs(currentTick - GRID_MAX) <= 1e-12) { culledTicks.push(currentTick); lastTick = currentTick; continue; }
        const isPriceTick = (isFinite(currentPrice) && Math.abs(currentTick - currentPrice) <= (currentPrice * 1e-9 + 1e-12));
        if (isPriceTick) { culledTicks.push(currentTick); lastTick = currentTick; continue; }
        if (Math.abs(currentTick - lastTick) > minSeparation) {
          culledTicks.push(currentTick);
          lastTick = currentTick;
        }
      }
      // Always include the last tick, ensuring it's not overlapping the previously added one
      const lastOriginalTick = yTicksVals[yTicksVals.length - 1];
      if (culledTicks[culledTicks.length - 1] !== lastOriginalTick) {
        if (Math.abs(lastOriginalTick - culledTicks[culledTicks.length - 1]) > minSeparation) {
          culledTicks.push(lastOriginalTick);
        } else {
          // If the last tick is too close, but it's a grid boundary, ensure it's present
          if (GRID_MAX != null && Math.abs(lastOriginalTick - GRID_MAX) <= 1e-12) culledTicks.push(lastOriginalTick);
        }
      }
    }
    yTicksVals = culledTicks;
  }

  // Always ensure boundaries are present (protect from any earlier culling)
  try{
    if (GRID_MIN != null && !yTicksVals.some(v => Math.abs(v - GRID_MIN) <= 1e-12)) yTicksVals.push(GRID_MIN);
    if (GRID_MAX != null && !yTicksVals.some(v => Math.abs(v - GRID_MAX) <= 1e-12)) yTicksVals.push(GRID_MAX);
    yTicksVals = [...new Set(yTicksVals)].sort((a,b)=>a-b);
  }catch(_){ }

  // Compact badges (annotations / DOM badge) were removed per user request.

  if (mode === 'active') {
    const activeOrders = OPEN_ORDERS_RAW.map(o => o.price).sort((a, b) => a - b);
    const { below, above } = nearestBracket(activeOrders, currentPrice);
    yTicksText = yTicksVals.map(v => {
      const isBoundary = (GRID_MIN != null && v === GRID_MIN) || (GRID_MAX != null && v === GRID_MAX);
      if (isBoundary) return `<b style="color:#5B21B6">${fmt(v,6)}</b>`;
      const isNearest = (v === below || v === above);
      const isPrice = (isFinite(currentPrice) && Math.abs(v - currentPrice) <= (currentPrice * 1e-9 + 1e-12));
      const priceLineEnabled = (localStorage.getItem('showPriceLine') !== '0');
      if (isPrice && priceLineEnabled) return `<b style="color: red;">${fmt(v,6)}</b>`;
      if (isNearest) return `<b style="color: black">${fmt(v, 6)}</b>`;
      return fmt(v, 6);
    });
  } else {
    // grid or latitudes modes just show formatted numbers
    yTicksText = yTicksVals.map(v => {
      const isBoundary = (GRID_MIN != null && v === GRID_MIN) || (GRID_MAX != null && v === GRID_MAX);
      if (isBoundary) return `<b style="color:#5B21B6">${fmt(v,6)}</b>`;
      const isPrice = (isFinite(currentPrice) && Math.abs(v - currentPrice) <= (currentPrice * 1e-9 + 1e-12));
      const priceLineEnabled = (localStorage.getItem('showPriceLine') !== '0');
      if (isPrice && priceLineEnabled) return `<b style="color: red;">${fmt(v,6)}</b>`;
      return fmt(v, 6);
    });
  }

  let yRange = undefined;
  const followPrice = (window.followPriceEl && window.followPriceEl.checked);
  const autoZoom = (autoZoomEl && autoZoomEl.checked);
  if (followPrice && isFinite(currentPrice)) {
    try {
      let span = null;
      if (GRID_MIN != null && GRID_MAX != null && GRID_MAX > GRID_MIN) {
        span = (GRID_MAX - GRID_MIN) * 0.4;
      } else {
        const trace = chartEl.data && chartEl.data[0];
        if (trace && trace.y && trace.y.length > 30) {
          const recent = trace.y.slice(-300).filter(v=>isFinite(v));
          if (recent.length) {
            const ymin = Math.min(...recent);
            const ymax = Math.max(...recent);
            const spanRecent = ymax - ymin;
            if (spanRecent > 0) span = spanRecent * 0.8;
          }
        }
      }
      if (!span || span <= 0) span = currentPrice * 0.01;
      const half = span/2;
      yRange = [currentPrice - half, currentPrice + half];
    } catch(e){ console.warn('followPrice calc failed', e); }
  } else if (autoZoom) {
    try {
      const gd = chartEl;
      const trace = gd.data && gd.data[0];
      if (trace && trace.y && trace.y.length) {
        const ys = trace.y.filter(v=>isFinite(v));
        if (ys.length) {
          const ymin = Math.min(...ys);
          const ymax = Math.max(...ys);
          if (ymax > ymin) {
            const span = ymax - ymin;
            const pad = span * 0.05; // 5% padding for auto range
            yRange = [ymin - pad, ymax + pad];
          }
        }
      }
    } catch(e){ console.warn('autoZoom calc failed', e); }
  }
  if (!yRange && GRID_MIN != null && GRID_MAX != null) {
    const span = GRID_MAX - GRID_MIN;
    const pad = span * 0.03; // fallback padding
    let low = GRID_MIN - pad;
    let high = GRID_MAX + pad;
    if (yTicksVals.length) {
      // Ensure gray extension lines are visible
      low = Math.min(low, yTicksVals[0]);
      high = Math.max(high, yTicksVals[yTicksVals.length - 1]);
    }
    yRange = [low, high];
  }

  // Ensure grid boundaries remain visible even when followPrice/autoZoom set a narrow yRange.
  // This avoids hiding the purple boundary tick (GRID_MIN/GRID_MAX) when the chart is centered
  // around the current price or auto-zoomed.
  if (yRange && GRID_MIN != null) {
    try {
      const span = Math.max(1e-9, Math.abs(yRange[1] - yRange[0]));
      const pad = Math.max(span * 0.02, Math.abs((GRID_MAX || 0) - GRID_MIN) * 0.01, 1e-9);
      if (GRID_MIN < yRange[0]) yRange[0] = Math.min(yRange[0], GRID_MIN - pad);
      if (GRID_MAX != null && GRID_MAX > yRange[1]) yRange[1] = Math.max(yRange[1], GRID_MAX + pad);
    } catch(e) { /* noop */ }
  }

  // Auto-zoom latitude override: when autoZoom (and not followPrice) is active, replace ticks with dynamic bright gray latitudes
  if (autoZoom && !followPrice && yRange) {
    try {
      const span = yRange[1] - yRange[2-1]; // yRange[1]-yRange[0]; keeping original style but correct expression below
    } catch(_){/* noop */}
    const span2 = yRange[1] - yRange[0];
      if (span2 > 0) {
      const LAT_COUNT = 10; // number of intervals (produces LAT_COUNT+1 lines)
      const latVals = [];
      for (let i=0;i<=LAT_COUNT;i++) {
        const v = yRange[0] + (span2 * i / LAT_COUNT);
        latVals.push(v);
      }
      // Build faint shapes for these dynamic latitudes (avoid duplicating existing shapes at boundaries)
      // Use low alpha and put shapes below traces so they don't dominate the chart (Option B: fade + layer:'below')
      for (const v of latVals) {
        if (GRID_MIN != null && Math.abs(v-GRID_MIN) < 1e-12) continue;
        if (GRID_MAX != null && Math.abs(v-GRID_MAX) < 1e-12) continue;
        const sh = shapeForY(v, 'rgba(210,210,210,0.12)', 1, 'dot');
        try{ sh.layer = 'below'; }catch(_){ }
        // ensure thin line
        try{ sh.line = sh.line || {}; sh.line.width = 1; sh.line.dash = 'dot'; }catch(_){ }
        shapes.push(sh);
      }
      // Ensure price tick retained/highlighted
      if (isFinite(currentPrice) && !latVals.some(v=>Math.abs(v-currentPrice) <= (Math.abs(currentPrice)*1e-9 + 1e-12))) {
        latVals.push(currentPrice);
      }
      // Ensure grid boundaries are present in the tick list so they stay visible
      try{
        if (GRID_MIN != null && !latVals.some(v => Math.abs(v - GRID_MIN) <= 1e-12)) latVals.push(GRID_MIN);
        if (GRID_MAX != null && !latVals.some(v => Math.abs(v - GRID_MAX) <= 1e-12)) latVals.push(GRID_MAX);
      }catch(_){ }
      latVals.sort((a,b)=>a-b);
      // Deduplicate and assign
      yTicksVals = [...new Set(latVals.map(v=>Number(v)))].sort((a,b)=>a-b);
      yTicksText = yTicksVals.map(v=>{
        const isBoundary = (GRID_MIN != null && Math.abs(v - GRID_MIN) <= 1e-12) || (GRID_MAX != null && Math.abs(v - GRID_MAX) <= 1e-12);
        const isPrice = isFinite(currentPrice) && Math.abs(v-currentPrice) <= (Math.abs(currentPrice)*1e-9 + 1e-12);
        const priceLineEnabled = (localStorage.getItem('showPriceLine') !== '0');
        if (isBoundary) return `<b style="color:#5B21B6">${fmt(v,6)}</b>`;
        if (isPrice && priceLineEnabled) return `<b style="color: red;">${fmt(v,6)}</b>`;
        return fmt(v,6);
      });
    }
  }

  // Ensure sticky-mode is applied if chart scroll container is scrollable
  try{ if (typeof ensureStickyIfScrollable === 'function') ensureStickyIfScrollable(); }catch(_){ }
  // Apply layout updates (shapes and tick arrays). Keep annotations out so
  // only the y-axis tick text is shown for boundary lines (prevents duplicate labels).
  Plotly.relayout('chart', {
    shapes: shapes,
    'yaxis.tickmode': 'array',
    'yaxis.tickvals': yTicksVals,
    'yaxis.ticktext': yTicksText,
    'yaxis.range': yRange
  });

  // Removed right-edge price annotation (was previously price-edge-dot)

  // Adaptive height: if many extended lines, increase inner chart height to enable scroll
  if (scrollWrap) {
    try {
      const baseHeight = 520; // px
      const extraLines = (extraLinesInfo.minLines || 0) + (extraLinesInfo.maxLines || 0);
      const added = Math.min(2000, extraLines * 20); // 20px per extra line (capped)
      const newHeight = baseHeight + added;
      const chartDiv = document.getElementById('chart');
      if (chartDiv && chartDiv.style.height !== newHeight + 'px') {
        chartDiv.style.height = newHeight + 'px';
        // Force Plotly to resize
        Plotly.Plots.resize(chartDiv);
      }
    } catch(e){ console.warn('adaptive height failed', e); }
  }
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
  // Persist and toggle latitudes mode: when checked -> latitudes, when unchecked -> grid
  try{ const savedL = localStorage.getItem('ui.showLat'); if(savedL!==null) showLatEl.checked = JSON.parse(savedL)?true:false; }catch(_){ }
  showLatEl.addEventListener('change', () => {
    try{ localStorage.setItem('ui.showLat', JSON.stringify(showLatEl.checked)); }catch(_){ }
    if (showLatEl.checked) setChartMode('latitudes'); else setChartMode('grid');
  });
  if (showPriceLineEl){
    showPriceLineEl.checked = (localStorage.getItem('showPriceLine') !== '0');
    showPriceLineEl.addEventListener('change', () => {
      localStorage.setItem('showPriceLine', showPriceLineEl.checked ? '1':'0');
      updateChart();
    });
  }
  // Price marker toggle removed (always visible now)
  // Follow price toggle
  if (window.followPriceEl){
    window.followPriceEl.checked = (localStorage.getItem('followPrice') === '1');
    window.followPriceEl.addEventListener('change', ()=>{
      localStorage.setItem('followPrice', window.followPriceEl.checked ? '1':'0');
      if (window.followPriceEl.checked && autoZoomEl){
        autoZoomEl.checked = false;
        localStorage.setItem('chartAutoZoom','0');
      }
      updateChart();
    });
  }
  if (autoZoomEl) {
    autoZoomEl.checked = localStorage.getItem('chartAutoZoom') === '1';
    autoZoomEl.addEventListener('change', () => {
      localStorage.setItem('chartAutoZoom', autoZoomEl.checked ? '1':'0');
      if (autoZoomEl.checked && window.followPriceEl){
        window.followPriceEl.checked = false;
        localStorage.setItem('followPrice','0');
      }
      updateChart();
    });
  }
  
  // Sticky X-axis control removed — axis will auto-stick when chart container is scrollable.
  const savedMode = localStorage.getItem('chartMode') || 'grid';
  setChartMode(savedMode);

  // Auto-sticky helper: add/remove sticky-mode based on whether the chart container
  // currently has a vertical scrollbar (content taller than container).
  function ensureStickyIfScrollable(){
    try{
      const wrap = document.getElementById('chartScroll');
      if (!wrap) return;
      const isScrollable = wrap.scrollHeight > wrap.clientHeight + 1; // small fudge
      if (isScrollable) wrap.classList.add('sticky-mode'); else wrap.classList.remove('sticky-mode');
    }catch(e){ /* noop */ }
  }

  // Legend toggle (DOGE/USDT) - toggles primary price trace visibility (trace 0)
  // legend toggle removed; price curve always visible
}

/* ====== SSE ====== */
window.__currentPrice = null;
// Control element refs (populated in boot)
// price marker always visible (legacy toggle removed)
var followPriceEl = null;
// New UI controls refs
// activeEmphasis UI removed; emphasis fixed at 'medium'

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

        // Update price display & source badge
        const priceEl = document.getElementById('priceVal');
        if (priceEl) {
          clearLoadingState('priceVal');
          priceEl.textContent = fmt(j.p, 6);
        }
        const srcEl = document.getElementById('priceSource');
        if (srcEl){
          const src = j.s;
          if (src){
            srcEl.textContent = src;
            srcEl.className = 'src-badge ' + (src==='auth'?'local':src); // reuse styling; 'auth' not mapped so keep base style
          } else {
            srcEl.textContent='';
            srcEl.className='src-badge hidden';
          }
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
              [
                { x:[t], y:[j.p], mode:'lines', name: PAIR, line:{width:1.5,color:'#1f77b4'} },
                { x:[t], y:[j.p], mode:'markers', name:'price', marker:{color:'#ff0066', size:10, line:{color:'#fff', width:1}}, hoverinfo:'none', visible:true }
              ],
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
            setupDynamicTimeLabels();  // Setup event handlers after chart recreation
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
            try {
              const markerVisible = true;
              Plotly.restyle('chart', {x:[[t]], y:[[j.p]], visible: markerVisible, 'marker.size':10}, [1]);
            } catch(markerErr) { console.warn('marker restyle failed', markerErr); }
          } catch (extendError) {
            console.warn('Failed to extend traces, recreating chart:', extendError);
            
            // Fallback: recreate chart with new data point
            try{
              const levels = buildAllLevels();
              const yTicksVals = levels;
              const yTicksText = levels.map(v => fmt(v, 6));
              
              await Plotly.newPlot('chart',
                [
                  { x:[t], y:[j.p], mode:'lines', name: PAIR, line:{width:1.5,color:'#1f77b4'} },
                  { x:[t], y:[j.p], mode:'markers', name:'price', marker:{color:'#ff0066', size:10, line:{color:'#fff', width:1}}, hoverinfo:'none', visible:true }
                ],
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
              setupDynamicTimeLabels();  // Setup event handlers after chart recreation
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
  // Update profit cards
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
  const badge = document.getElementById('openSourceBadge');
  try{
    const r = await fetch('/api/open_orders');
    const j = await r.json();
    if(j.ok && Array.isArray(j.orders)){
      OPEN_ORDERS_RAW = j.orders;
      note.textContent = j.orders.length? '' : 'No open orders.';
      if (badge){
        if (j.source){
          badge.textContent = j.source;
          badge.className = 'src-badge ' + j.source;
        } else {
          badge.textContent = '';
          badge.className = 'src-badge hidden';
        }
      }
      renderOpenOrders();
    }else{
      note.textContent = j.error || 'Auth required (API key/secret).';
      OPEN_ORDERS_RAW = [];
      if (badge){
        if (j.source){
          badge.textContent = j.source;
          badge.className = 'src-badge error';
        } else {
          badge.textContent='';
          badge.className = 'src-badge hidden';
        }
      }
      renderOpenOrders();
    }
  }catch(e){
    note.textContent = 'Failed to load.';
    OPEN_ORDERS_RAW = [];
    if (badge){ badge.textContent=''; badge.className='src-badge hidden'; }
    renderOpenOrders();
  }
  updateLastUpdated();
}

function renderHistOrders(meta){
  const tb = document.querySelector('#histTbl tbody'); tb.innerHTML='';
  const sortKey = document.getElementById('histSortBy').value;
  const sortDir = document.getElementById('histSortDir').value;
  const q = document.getElementById('histFilter').value.trim();

  let rows = textFilter(HIST_ORDERS_RAW, q);
  rows = sortBy(rows, sortKey, sortDir);

  const cntEl = document.getElementById('histCount');
  if (cntEl){
    if (meta && typeof meta.total==='number'){
      cntEl.textContent = `(${rows.length}/${meta.total})`;
    } else {
      cntEl.textContent = `(${rows.length})`;
    }
  }

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

let _histFirstLoad = true;
let _histNextEndTime = null; // track oldest timestamp fetched for backward pagination
async function loadHistoryOrders(){
  const note = document.getElementById('histNote');
  const badge = document.getElementById('histSourceBadge');
  try{
  let url = '/api/order_history';
  if (_histFirstLoad){ url += '?full=1'; }
  else if(_histNextEndTime){
    const sep = url.includes('?') ? '&' : '?';
    url += sep + 'endTime=' + encodeURIComponent(_histNextEndTime);
  }
  const r = await fetch(url);
    const j = await r.json();
    if(j.ok && Array.isArray(j.orders)){
      if(Array.isArray(HIST_ORDERS_RAW) && HIST_ORDERS_RAW.length){
        const existingKeys = new Set(HIST_ORDERS_RAW.map(r=>[r.id,r.time,r.side,Number(r.price).toFixed(8),Number(r.amount).toFixed(8),r.status].join('|')));
        for(const r of j.orders){
          const k = [r.id,r.time,r.side,Number(r.price).toFixed(8),Number(r.amount).toFixed(8),r.status].join('|');
          if(!existingKeys.has(k)){
            existingKeys.add(k);
            HIST_ORDERS_RAW.push(r);
          }
        }
      } else {
        HIST_ORDERS_RAW = j.orders.slice();
      }
      // Cap client-side accumulation to 5000
      if (HIST_ORDERS_RAW.length > 5000){
        HIST_ORDERS_RAW = HIST_ORDERS_RAW.slice(-5000);
      }
      note.textContent = j.orders.length? '' : 'No history to show.';
      if (badge){
        if (j.source){
          badge.textContent = j.source;
          badge.className = 'src-badge ' + j.source;
        } else {
          badge.textContent='';
          badge.className = 'src-badge hidden';
        }
      }
      renderHistOrders({total: j.total});
      // If first load and still thin (<40) attempt enrichment
      if(_histFirstLoad && HIST_ORDERS_RAW.length < 40){
        try {
          const r2 = await fetch('/api/order_history?full=1&include=trades,state');
          const j2 = await r2.json();
          if (j2.ok && Array.isArray(j2.orders)){
            const existingKeys2 = new Set(HIST_ORDERS_RAW.map(r=>[r.id,r.time,r.side,Number(r.price).toFixed(8),Number(r.amount).toFixed(8),r.status].join('|')));
            for(const r of j2.orders){
              const k = [r.id,r.time,r.side,Number(r.price).toFixed(8),Number(r.amount).toFixed(8),r.status].join('|');
              if(!existingKeys2.has(k)){
                existingKeys2.add(k);
                HIST_ORDERS_RAW.push(r);
              }
            }
            if (HIST_ORDERS_RAW.length > 5000){
              HIST_ORDERS_RAW = HIST_ORDERS_RAW.slice(-5000);
            }
            renderHistOrders({total: j2.total});
          }
        }catch(_e){}
      }
      _histFirstLoad = false;
    }else{
      note.textContent = j.error || 'Auth required (API key/secret).';
      HIST_ORDERS_RAW = [];
      if (badge){
        if (j.source){
          badge.textContent = j.source;
          badge.className = 'src-badge error';
        } else {
          badge.textContent='';
          badge.className = 'src-badge hidden';
        }
      }
      renderHistOrders();
    }
  }catch(e){
    note.textContent = 'Failed to load.';
    HIST_ORDERS_RAW = [];
    if (badge){ badge.textContent=''; badge.className='src-badge hidden'; }
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
  const recomputeBtn = document.getElementById('btnRecompute');
  if(refreshBtn) refreshBtn.addEventListener('click', ()=>{
    loadStats(); loadOpenOrders(); loadHistoryOrders(); loadHistory(); updateLastUpdated();
  });
  // Topbar action buttons: stop/resume/cancel/reload keys
  const stopBtn = document.getElementById('btnStop');
  if (stopBtn) stopBtn.addEventListener('click', async () => {
    try{
      const r = await fetch('/api/stop_bot', { method: 'POST' });
      const j = await r.json().catch(()=>null);
      console.log('stop_bot ->', j);
  // Refresh bot status after stop
  try{ await updateBotStatus(); }catch(_){ }
    }catch(e){ console.warn('stop request failed', e); }
  });
  const resumeBtn = document.getElementById('btnResume');
  if (resumeBtn) resumeBtn.addEventListener('click', async () => {
    try{
      const r = await fetch('/api/resume_bot', { method: 'POST' });
      const j = await r.json().catch(()=>null);
      console.log('resume_bot ->', j);
  // Refresh bot status after resume
  try{ await updateBotStatus(); }catch(_){ }
    }catch(e){ console.warn('resume request failed', e); }
  });
  const cancelBtn = document.getElementById('btnCancel');
  if (cancelBtn) cancelBtn.addEventListener('click', async () => {
    // Show confirmation with expected money/profit details
    try{
      const count = Array.isArray(OPEN_ORDERS_RAW) ? OPEN_ORDERS_RAW.length : 0;
      const totalValue = (Array.isArray(OPEN_ORDERS_RAW) ? OPEN_ORDERS_RAW.reduce((s,o)=>s + (Number(o.value_usdt) || 0), 0) : 0);
      let profit = 0;
      const profitEl = document.getElementById('profitVal');
      if (profitEl) {
        // remove any non-numeric chars (commas, $) then parse
        const cleaned = (profitEl.textContent || '').replace(/[^0-9.\-]/g,'');
        profit = parseFloat(cleaned) || 0;
      }
      const msg = `Cancel all open orders (${count})?\nEstimated open orders value: $${totalValue.toFixed(2)}\nCurrent Total Profit: $${profit.toFixed(2)}\n\nThis will send cancel requests for all open orders. Continue?`;
      if (!confirm(msg)) return;

      cancelBtn.disabled = true;
      const r = await fetch('/api/cancel_all_orders', { method: 'POST' });
      const j = await r.json().catch(()=>null);
      console.log('cancel_all_orders ->', j);
      // Refresh open orders after cancel attempt
      setTimeout(()=>{ loadOpenOrders(); }, 800);
    }catch(e){ console.warn('cancel request failed', e); }
    finally { try{ cancelBtn.disabled = false; }catch(_){}};
  });
  const reloadKeysBtn = document.getElementById('btnReloadKeys');

  if (recomputeBtn) {
    recomputeBtn.addEventListener('click', async () => {
      if (!confirm('Run recompute P&L now? This will read state files and regenerate the CSV.')) return;
      recomputeBtn.disabled = true;
      try {
        const token = localStorage.getItem('DASH_ADMIN_TOKEN') || '';
        const resp = await fetch('/api/recompute_pnl', {method: 'POST', headers: token ? {'X-ADMIN-TOKEN': token} : {}});
        const j = await resp.json();
        if (!j.ok) {
          alert('Recompute failed: ' + (j.error || JSON.stringify(j)));
          return;
        }
        // fetch HTML report and show in modal
        const reportResp = await fetch('/recompute_report', {headers: token ? {'X-ADMIN-TOKEN': token} : {}});
        if (!reportResp.ok) {
          alert('Failed to load report: ' + reportResp.statusText);
          return;
        }
        const html = await reportResp.text();
        const modal = document.getElementById('recomputeModal');
        const content = document.getElementById('recomputeContent');
        content.innerHTML = html;
        modal.style.display = 'flex';
        // wire close
        const closeBtn = document.getElementById('recomputeClose');
        const hide = () => { modal.style.display = 'none'; };
        closeBtn.onclick = hide;
        modal.onclick = (ev) => { if (ev.target === modal) hide(); };
      } catch (e) {
        alert('Recompute request failed: ' + e);
      } finally {
        recomputeBtn.disabled = false;
      }
    });
  }
  if (reloadKeysBtn) reloadKeysBtn.addEventListener('click', async () => {
    try{
      const r = await fetch('/api/reload_keys', { method: 'POST' });
      const j = await r.json().catch(()=>null);
      console.log('reload_keys ->', j);
      // Refresh auth status badge and open orders
      refreshAuthStatus(); loadOpenOrders();
      try{ await updateBotStatus(); }catch(_){ }
    }catch(e){ console.warn('reload keys request failed', e); }
  });

  // Query /api/bot_status and update Stop/Resume button states
  async function updateBotStatus(){
    try{
      const r = await fetch('/api/bot_status');
      const j = await r.json();
      const running = !!(j && j.running);
      if (stopBtn) stopBtn.disabled = !running; // disable Stop if not running
      if (resumeBtn) resumeBtn.disabled = running; // disable Resume if already running
      return j;
    }catch(e){ console.warn('bot status check failed', e); return {running:false}; }
  }
  const olderBtn = document.getElementById('btnHistOlder');
  if(olderBtn) olderBtn.addEventListener('click', ()=>{ fetchOlderHistory(); });
  const applyIntBtn = document.getElementById('btnApplyInterval');
  if(applyIntBtn) applyIntBtn.addEventListener('click', ()=>{ applyAutoRefreshInterval(); });
  const exportBtn = document.getElementById('btnExportCSV');
  if(exportBtn) exportBtn.addEventListener('click', exportHistoryCSV);
  
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

// Export history respecting current filter & sort
function exportHistoryCSV(){
  if(!HIST_ORDERS_RAW || !HIST_ORDERS_RAW.length){ return; }
  const sortKeyEl = document.getElementById('histSortBy');
  const sortDirEl = document.getElementById('histSortDir');
  const filterEl = document.getElementById('histFilter');
  const sortKey = sortKeyEl ? sortKeyEl.value : 'time';
  const sortDir = sortDirEl ? sortDirEl.value : 'desc';
  const filterTxt = filterEl ? filterEl.value.trim() : '';
  let rows = HIST_ORDERS_RAW;
  rows = textFilter(rows, filterTxt);
  rows = sortBy(rows, sortKey, sortDir);
  if(!rows.length) return;
  const headers = ['id','time','execution_time','side','status','price','amount','value_usdt'];
  const lines = [headers.join(',')];
  for(const r of rows){
    const row = headers.map(h=>{
      let v = r[h];
      if(v===undefined || v===null) v='';
      if(typeof v === 'string'){
        if(v.includes(',') || v.includes('"') || v.includes('\n')){
          v = '"'+v.replace(/"/g,'""')+'"';
        }
      }
      return v;
    }).join(',');
    lines.push(row);
  }
  const blob = new Blob([lines.join('\n')], {type:'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const ts = new Date().toISOString().replace(/[:T]/g,'-').slice(0,19);
  a.download = 'order_history_filtered_'+ts+'.csv';
  document.body.appendChild(a);
  a.click();
  setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); }, 500);
}

/* ===== UX Improvements ===== */

function initializeUXImprovements() {
  // 1. Initialize legend position feature flag (default: bottom)
  const legendPosition = localStorage.getItem('chartLegendPosition') || 'bottom';
  toggleLegendPosition(legendPosition);
  
  // 2. Initialize always visible price marker feature flag (default: true)
  const alwaysShowPrice = localStorage.getItem('alwaysShowPriceMarker') !== '0';
  if (alwaysShowPrice) {
    localStorage.setItem('alwaysShowPriceMarker', '1');
  }
  
  // 3. Auto-sticky: ensure sticky if chart is scrollable (checkbox removed)
  try{ if (typeof ensureStickyIfScrollable === 'function') ensureStickyIfScrollable(); }catch(_){ }
}

function toggleLegendPosition(position) {
  const legendEl = document.getElementById('chartLegend');
  if (legendEl) {
    if (position === 'bottom') {
      legendEl.style.display = 'block';
    } else {
      legendEl.style.display = 'none';
    }
  }
}

function toggleStickyXAxis(enabled) {
  const chartContainer = document.getElementById('chartScroll');
  if (chartContainer) {
    if (enabled) {
      chartContainer.classList.add('sticky-mode');
    } else {
      chartContainer.classList.remove('sticky-mode');
    }
  }
  localStorage.setItem('stickyXAxis', enabled ? '1' : '0');
}

function setupDynamicTimeLabels() {
  // Enhanced time label formatting based on zoom level
  const chartEl = document.getElementById('chart');
  if (!chartEl || !window.Plotly) return;
  
  // Remove any existing event listeners first to avoid duplicates
  try {
    chartEl.removeAllListeners && chartEl.removeAllListeners('plotly_relayout');
  } catch (e) {
    // Ignore if removeAllListeners doesn't exist
  }
  
  // Listen for plotly relayout events (zoom/pan) using Plotly's event API
  chartEl.on('plotly_relayout', function(eventData) {
    if (eventData['xaxis.range[0]'] || eventData['xaxis.range[1]']) {
      updateTimeLabelsForZoom(eventData);
    }
  });
}

function updateTimeLabelsForZoom(eventData) {
  const chartEl = document.getElementById('chart');
  if (!chartEl || !chartEl.layout) return;
  
  try {
    const xRange = chartEl.layout.xaxis.range;
    if (!xRange || xRange.length < 2) return;
    
    const startTime = new Date(xRange[0]);
    const endTime = new Date(xRange[1]);
    const timeDiff = endTime - startTime;
    
    // Dynamic formatting based on time range
    let tickformat;
    if (timeDiff < 3600000) { // Less than 1 hour
      tickformat = "%H:%M:%S";
    } else if (timeDiff < 86400000) { // Less than 1 day  
      tickformat = "%H:%M";
    } else if (timeDiff < 604800000) { // Less than 1 week
      tickformat = "%m/%d<br>%H:%M";
    } else {
      tickformat = "%m/%d<br>%Y";
    }
    
    // Update layout with new tick format
    Plotly.relayout(chartEl, {
      'xaxis.tickformat': tickformat
    });
  } catch (e) {
    console.warn('Dynamic time label update failed:', e);
  }
}

function applyAutoRefreshInterval() {
  // Clear any existing interval
  if (window._autoRefreshTimer) {
    clearInterval(window._autoRefreshTimer);
  }
  
  try {
    const input = document.getElementById('autoRefreshMs');
    if (!input) return;
    
    const intervalMs = parseInt(input.value, 10);
    if (isNaN(intervalMs) || intervalMs < 5000) {
      console.warn('Invalid refresh interval, using default 25000ms');
      input.value = '25000';
      return;
    }
    
    // Store the setting
    localStorage.setItem('ui.autoRefreshMs', String(intervalMs));
    
    // Set up new interval for refreshing stats and orders
    window._autoRefreshTimer = setInterval(() => {
      try {
        loadStats();
        loadOpenOrders();
        loadHistoryOrders();
        updateLastUpdated();
      } catch (e) {
        console.error('Auto refresh failed:', e);
      }
    }, intervalMs);
    
    console.log(`Auto refresh interval set to ${intervalMs}ms`);
  } catch (e) {
    console.error('Failed to apply auto refresh interval:', e);
  }
}

async function boot(){
  showGridEl = document.getElementById('showGrid');
  showActiveEl = document.getElementById('showActiveLayers');
  showLatEl = document.getElementById('showLat');
  showPriceLineEl = document.getElementById('showPriceLine');
  // stickyXAxis control removed
  // price marker always visible (legacy toggle removed)
  followPriceEl = document.getElementById('followPrice');
  autoZoomEl = document.getElementById('autoZoom');
  // activeEmphasis control removed

  // Observe chart scroll container for size/content changes and auto-apply sticky behavior
  try{
    const chartWrap = document.getElementById('chartScroll');
    if (chartWrap) {
      // initial pass
      ensureStickyIfScrollable();
      // listen to scroll/resize and mutations
      chartWrap.addEventListener('scroll', ensureStickyIfScrollable);
      window.addEventListener('resize', ensureStickyIfScrollable);
      const mo = new MutationObserver(ensureStickyIfScrollable);
      mo.observe(chartWrap, {childList:true, subtree:true, attributes:true});
    }
  }catch(_){ }
  
  // Initialize UX improvements with feature flags
  initializeUXImprovements();
  
  wireControls();
  
  // Initialize loading states for cards that start with dashes
  initializeCardLoadingStates();
  
  await loadStats();
  await loadInitialInvestments();
  await loadHistory();    // load history before starting stream
  startSSE();             // then live stream for price + statistics
  await loadOpenOrders();
  await loadHistoryOrders();
  // Periodic refresh via dynamic interval
  try{
    const saved = localStorage.getItem('ui.autoRefreshMs');
    if(saved){ const v = parseInt(saved,10); if(!isNaN(v)) document.getElementById('autoRefreshMs').value = v; }
  }catch(_){ }
  applyAutoRefreshInterval();
  refreshAuthStatus();
}

document.addEventListener('DOMContentLoaded', boot);

async function refreshAuthStatus(){
  try{
    const r = await fetch('/api/auth_status');
    const j = await r.json();
    const el = document.getElementById('authStatus');
    if(!el) return;
    if(!j.has_keys){
      el.textContent = 'NO KEYS';
      el.className = 'src-badge auth-missing';
      return;
    }
    if(j.last_error){
      el.textContent = 'AUTH ERR';
      el.title = j.last_error;
      el.className = 'src-badge auth-error';
    } else {
      el.textContent = 'AUTH OK';
      el.className = 'src-badge auth-ok';
    }
  }catch(e){
    const el = document.getElementById('authStatus');
    if(el){
      el.textContent = 'AUTH ?';
      el.className = 'src-badge auth-error';
    }
  }
}
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