#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Profit Split:
כל רווח ממומש נחלק לצ'אנקים של 4$ (ברירת מחדל).
מכל 4$: חצי ל-BNB (נרכש כשמצטבר minCost), חצי לרה-אינבסט (הגדלת BUY הבא).
שומר מצב ב-state.json בתיקיית הפרויקט.

ניתן לקנפג דרך ENV:
  SPLIT_CHUNK_USD  (ברירת מחדל 4.0)
  SPLIT_RATIO      (ברירת מחדל 0.5 = 50/50)
  BNB_SYMBOL       (ברירת מחדל "BNB/USDT")
"""

import json
import os
import time
from dataclasses import dataclass, asdict

SPLIT_CHUNK_USD = float(os.getenv("SPLIT_CHUNK_USD", "4.0"))
SPLIT_RATIO     = float(os.getenv("SPLIT_RATIO", "0.5"))   # 0.5 = חצי ל-BNB חצי לרה-אינבסט
BNB_SYMBOL      = os.getenv("BNB_SYMBOL", "BNB/USDT")

STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")


@dataclass
class SplitState:
    split_accumulator_usd: float = 0.0  # רווח שנצבר ולא חולק (פחות מ-4$)
    bnb_pending_usd: float = 0.0        # צבירה לקניית BNB
    reinvest_pool_usd: float = 0.0      # בריכה להגדלת ה-BUY הבא
    total_sent_to_bnb_usd: float = 0.0  # סטטיסטיקה
    total_reinvested_usd: float = 0.0   # סטטיסטיקה
    last_update_ts: float = 0.0


def _load() -> SplitState:
    if not os.path.exists(STATE_PATH):
        return SplitState()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SplitState(**data)
    except Exception:
        return SplitState()


def _save(st: SplitState) -> None:
    st.last_update_ts = time.time()
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(st), f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _get_min_cost(client, symbol: str, fallback: float = 10.0) -> float:
    """מנסה להביא minNotional/Cost מהבורסה; אם לא זמין—נופל ל-fallback."""
    try:
        client.load_markets()
        m = client.market(symbol)
        lim = (m.get("limits") or {}).get("cost") or {}
        mn = lim.get("min")
        return float(mn) if mn else fallback
    except Exception:
        return fallback


def handle_profit(profit_usd: float, client) -> None:
    """
    לקרוא לפונקציה הזו בכל פעם שסגירת SELL מייצרת רווח ממומש בדולר.
    - צובר רווח עד שמגיע ל"צ'אנקים" של SPLIT_CHUNK_USD (דיפולט 4$).
    - מכל צ'אנק: SPLIT_RATIO ל-BNB והשאר לרה-אינבסט.
    - קניית BNB מבוצעת רק לאחר שהצטבר >= minCost של BNB/USDT בבורסה (Market).
    """
    if profit_usd <= 0:
        return

    st = _load()
    st.split_accumulator_usd += float(profit_usd)

    chunk = SPLIT_CHUNK_USD
    chunks = int(st.split_accumulator_usd // chunk)  # כמה "4$" שלמים
    remainder = st.split_accumulator_usd - chunks * chunk

    if chunks > 0:
        per_chunk_to_bnb = chunk * SPLIT_RATIO
        per_chunk_to_re  = chunk - per_chunk_to_bnb
        total_to_bnb = per_chunk_to_bnb * chunks
        total_to_re  = per_chunk_to_re  * chunks

        st.bnb_pending_usd   += total_to_bnb
        st.reinvest_pool_usd += total_to_re
        st.split_accumulator_usd = remainder

    # ננסה לקנות BNB אם עמדנו במינימום
    min_cost = _get_min_cost(client, BNB_SYMBOL, fallback=10.0)
    if st.bnb_pending_usd >= min_cost:
        usd_to_spend = st.bnb_pending_usd
        try:
            ticker = client.fetch_ticker(BNB_SYMBOL)
            last = float(ticker["last"])
            qty_est = usd_to_spend / last  # כמות BNB לשוק
            qty = float(client.amount_to_precision(BNB_SYMBOL, qty_est))
            if qty > 0:
                client.create_order(BNB_SYMBOL, "market", "buy", qty, None, {
                    "newClientOrderId": f"SPLITBNB-{int(time.time())}"
                })
                st.total_sent_to_bnb_usd += usd_to_spend
                st.bnb_pending_usd = 0.0
                print(f"[SPLIT] Bought {BNB_SYMBOL} qty={qty} (~${usd_to_spend:.2f})")
        except Exception as e:
            # נשאיר בצבירה; ננסה בפעם הבאה
            print(f"[SPLIT][WARN] BNB buy failed: {e}")

    _save(st)


def pull_reinvestment(max_add_usd: float) -> float:
    """
    לקרוא לפני פתיחת BUY חדש: מחזיר כמה דולר להוסיף להזמנה,
    ומוריד את הסכום מהבריכה. ברירת המחדל—מאפשר להגדיל עד גודל הבייס.
    """
    if max_add_usd <= 0:
        return 0.0
    st = _load()
    add = min(st.reinvest_pool_usd, float(max_add_usd))
    if add > 0:
        st.reinvest_pool_usd -= add
        st.total_reinvested_usd += add
        _save(st)
    return float(add)


def read_state() -> dict:
    """לוג/דיבאג/מוניטורינג."""
    return asdict(_load())


if __name__ == "__main__":
    import sys, json as _json
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(_json.dumps(read_state(), indent=2, ensure_ascii=False))
    else:
        print("Usage: python3 profit_split.py status")
