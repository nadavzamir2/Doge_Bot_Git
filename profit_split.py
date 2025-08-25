#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profit_split.py
---------------
חלוקת רווחים לצ'אנקים (ברירת מחדל 4$):
- מכל צ'אנק: SPLIT_RATIO ל-BNB (בקנייה מרקט כשמצטבר לפחות minCost), והשאר לבריכת reinvest.
- שומר מצב ב- ~/doge_bot/data/split_state.json
- מחזיר מידע לפונקציה הקוראת (כמה צ'אנקים נוצרו, כמה $ נקנו BNB, כמה $ נוספו ל-reinvest).

קונפיג דרך ENV:
  SPLIT_CHUNK_USD  (דיפולט 4.0)
  SPLIT_RATIO      (דיפולט 0.5 = 50/50)
  BNB_SYMBOL       (דיפולט "BNB/USDT")
"""

from __future__ import annotations
import os, json, time, pathlib
from dataclasses import dataclass, asdict
import contextlib

SPLIT_CHUNK_USD = float(os.getenv("SPLIT_CHUNK_USD", "4.0"))
SPLIT_RATIO     = float(os.getenv("SPLIT_RATIO", "0.5"))   # 0.5 = חצי ל-BNB חצי לרה-אינבסט
BNB_SYMBOL      = os.getenv("BNB_SYMBOL", "BNB/USDT")

DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA_DIR / "split_state.json"
SCHEMA_VERSION = 1

@contextlib.contextmanager
def file_lock(path: pathlib.Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = open(lock_path, "a+")
    try:
        try:
            import fcntl  # posix
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            yield
    finally:
        fh.close()

@dataclass
class SplitState:
    schema_version: int = SCHEMA_VERSION
    split_accumulator_usd: float = 0.0   # רווח שנצבר ולא חולק (פחות מ-4$)
    bnb_pending_usd: float = 0.0         # צבירה לקניית BNB
    reinvest_pool_usd: float = 0.0       # בריכה להגדלת ה-BUY הבא
    total_sent_to_bnb_usd: float = 0.0   # סטטיסטיקה
    total_reinvested_usd: float = 0.0    # סטטיסטיקה
    last_update_ts: float = 0.0
    last_action: str = ""                # תיעוד אחרון (אבחון)
    min_cost_cache_usd: float = 0.0      # מטמון למינימום קנייה
    min_cost_cache_ts: float = 0.0

def _atomic_write_json(path: pathlib.Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _load() -> SplitState:
    if not STATE_PATH.exists():
        return SplitState()
    try:
        with file_lock(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        if isinstance(data, dict):
            data["schema_version"] = SCHEMA_VERSION
            return SplitState(**data)
    except Exception:
        pass
    return SplitState()

def _save(st: SplitState) -> None:
    st.schema_version = SCHEMA_VERSION
    st.last_update_ts = time.time()
    with file_lock(STATE_PATH):
        _atomic_write_json(STATE_PATH, asdict(st))

def _get_min_cost(client, symbol: str, fallback: float = 10.0) -> float:
    """מביא minNotional/Cost; עם מטמון 60 שניות."""
    st = _load()
    now = time.time()
    if st.min_cost_cache_usd > 0 and now - st.min_cost_cache_ts < 60:
        return st.min_cost_cache_usd
    try:
        client.load_markets()
        m = client.market(symbol)
        lim = (m.get("limits") or {}).get("cost") or {}
        mn = lim.get("min")
        val = float(mn) if mn else fallback
    except Exception:
        val = fallback
    st.min_cost_cache_usd = val
    st.min_cost_cache_ts = now
    _save(st)
    return val

def handle_profit(profit_usd: float, client) -> dict:
    """
    לקרוא בכל פעם שיש רווח ממומש (FIFO). מחזיר:
      { "chunks": int, "bnb_bought_usd": float, "reinvest_added_usd": float }
    """
    info = {"chunks": 0, "bnb_bought_usd": 0.0, "reinvest_added_usd": 0.0}
    if profit_usd <= 0:
        return info

    st = _load()
    st.split_accumulator_usd += float(profit_usd)

    chunk = SPLIT_CHUNK_USD
    chunks = int(st.split_accumulator_usd // chunk)  # כמה "צ'אנקים" שלמים (למשל 4$)
    remainder = st.split_accumulator_usd - chunks * chunk

    if chunks > 0:
        per_chunk_to_bnb = chunk * SPLIT_RATIO
        per_chunk_to_re  = chunk - per_chunk_to_bnb
        total_to_bnb = per_chunk_to_bnb * chunks
        total_to_re  = per_chunk_to_re  * chunks
        st.bnb_pending_usd   += total_to_bnb
        st.reinvest_pool_usd += total_to_re
        info["chunks"] = chunks
        info["reinvest_added_usd"] = total_to_re
        st.split_accumulator_usd = remainder
        st.last_action = f"chunked: +{chunks} (bnb+={total_to_bnb:.2f}, reinv+={total_to_re:.2f})"
    else:
        st.last_action = f"accumulate: +{profit_usd:.4f} (pending chunk)"

    # ננסה לקנות BNB אם עמדנו במינימום
    min_cost = _get_min_cost(client, BNB_SYMBOL, fallback=10.0)
    if st.bnb_pending_usd >= min_cost:
        usd_to_spend = st.bnb_pending_usd
        try:
            ticker = client.fetch_ticker(BNB_SYMBOL)
            last = float(ticker["last"])
            qty_est = usd_to_spend / last
            qty = float(client.amount_to_precision(BNB_SYMBOL, qty_est))
            if qty > 0:
                client.create_order(BNB_SYMBOL, "market", "buy", qty, None, {
                    "newClientOrderId": f"SPLITBNB-{int(time.time())}"
                })
                st.total_sent_to_bnb_usd += usd_to_spend
                st.bnb_pending_usd = 0.0
                info["bnb_bought_usd"] = usd_to_spend
                st.last_action = f"BNB market buy ~${usd_to_spend:.2f} (qty≈{qty})"
        except Exception as e:
            st.last_action = f"BNB buy failed: {e}"

    _save(st)
    return info

def pull_reinvestment(max_add_usd: float) -> float:
    """נקרא לפני פתיחת BUY חדש: מחזיר כמה דולר להוסיף להזמנה, ומוריד מהבריכה."""
    if max_add_usd <= 0:
        return 0.0
    st = _load()
    add = min(st.reinvest_pool_usd, float(max_add_usd))
    if add > 0:
        st.reinvest_pool_usd -= add
        st.total_reinvested_usd += add
        st.last_action = f"reinvest pulled {add:.2f}"
        _save(st)
    return float(add)

def read_state() -> dict:
    return asdict(_load())

if __name__ == "__main__":
    import sys, json as _json
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(_json.dumps(read_state(), indent=2, ensure_ascii=False))
    else:
        print("Usage: python3 profit_split.py status")
