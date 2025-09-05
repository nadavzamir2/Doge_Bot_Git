#!/usr/bin/env python3
"""Recompute realized P&L and initial investments from state files.

Produces a CSV at data/pnl_recompute.csv and prints a short summary.
"""
import json
import csv
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'


def load_json(path: Path):
    if not path.exists():
        return None
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def parse_time(t):
    if not t:
        return None
    # numeric epoch (ms or s)
    try:
        s = str(t)
        if s.isdigit():
            iv = int(s)
            # milliseconds if length > 10
            if len(s) > 10:
                return datetime.fromtimestamp(iv / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(iv, tz=timezone.utc)
        # ISO strings ending with Z
        s2 = s.replace('Z', '+00:00')
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def main():
    state = load_json(DATA / 'state.json') or load_json(ROOT / 'state.json') or {}
    order_history = load_json(DATA / 'order_history_local.json') or []

    buy_fills: Dict[str, Any] = state.get('buy_fills', {})
    sell_fills: Dict[str, Any] = state.get('sell_fills', {})
    child_sells: Dict[str, str] = state.get('child_sells', {})

    # build order index by id for timestamps and extra data
    eh_index = {o.get('id'): o for o in order_history}

    # Build buys list with remaining amount
    buys: List[Dict[str, Any]] = []
    for buy_id, buy in buy_fills.items():
        amount = float(buy.get('amount', 0))
        price = float(buy.get('price', 0))
        oh = eh_index.get(buy_id) or {}
        bt = parse_time(oh.get('execution_time') or oh.get('time'))
        buys.append({
            'id': buy_id,
            'price': price,
            'amount': amount,
            'remaining': amount,
            'time': bt
        })

    # Build sells list: prefer order_history sells, but also include sell_fills not in history
    sells: List[Dict[str, Any]] = []
    for o in order_history:
        if o.get('side') != 'sell':
            continue
        status = str(o.get('status', '')).lower()
        if status not in ('closed', 'filled', 'done'):
            continue
        amt = float(o.get('amount', 0))
        if amt <= 0:
            continue
        st = parse_time(o.get('execution_time') or o.get('time'))
        sells.append({
            'id': o.get('id'),
            'price': float(o.get('price', 0)),
            'amount': amt,
            'remaining': amt,
            'time': st
        })

    # include sell_fills entries not present in order_history
    for sid, s in sell_fills.items():
        if sid in eh_index:
            continue
        amt = float(s.get('amount', 0))
        if amt <= 0:
            continue
        sells.append({
            'id': sid,
            'price': float(s.get('price', 0)),
            'amount': amt,
            'remaining': amt,
            'time': None
        })

    # sort buys by time (None go last), then original order
    buys.sort(key=lambda b: (b['time'] is None, b['time'] or datetime.min.replace(tzinfo=timezone.utc)))
    # sort sells by time (None go last)
    sells.sort(key=lambda s: (s['time'] is None, s['time'] or datetime.min.replace(tzinfo=timezone.utc)))

    rows = []
    total_buy = 0.0
    total_sell = 0.0
    total_profit = 0.0

    # FIFO matching: iterate sells and allocate to earliest buys with remaining>0
    for sell in sells:
        while sell['remaining'] > 1e-12:
            # find earliest buy with remaining > 0 and buy.time <= sell.time (if both present)
            buy_idx = None
            for i, b in enumerate(buys):
                if b['remaining'] <= 1e-12:
                    continue
                if b['time'] and sell['time'] and b['time'] > sell['time']:
                    # don't match buys that happen after this sell if timestamp available
                    continue
                buy_idx = i
                break
            if buy_idx is None:
                # nothing eligible by time; try any remaining buy
                for i, b in enumerate(buys):
                    if b['remaining'] > 1e-12:
                        buy_idx = i
                        break
            if buy_idx is None:
                # no buys left to match
                break

            b = buys[buy_idx]
            chunk = min(b['remaining'], sell['remaining'])
            profit = (sell['price'] - b['price']) * chunk

            rows.append({
                'buy_id': b['id'],
                'buy_time': b['time'].isoformat() if b['time'] else '',
                'buy_price': b['price'],
                'buy_amount_chunk': chunk,
                'sell_id': sell['id'],
                'sell_time': sell['time'].isoformat() if sell['time'] else '',
                'sell_price': sell['price'],
                'sell_amount_chunk': chunk,
                'profit_usd': round(profit, 8)
            })

            b['remaining'] -= chunk
            sell['remaining'] -= chunk
            total_profit += profit
            total_buy += b['price'] * chunk
            total_sell += sell['price'] * chunk

    # Any remaining buys are unmatched
    unmatched = [b for b in buys if b['remaining'] > 1e-12]

    out_csv = DATA / 'pnl_recompute_fifo.csv'
    with out_csv.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'buy_id', 'buy_time', 'buy_price', 'buy_amount_chunk',
            'sell_id', 'sell_time', 'sell_price', 'sell_amount_chunk', 'profit_usd'
        ])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print('Wrote', out_csv)
    print('Matched sell chunks (rows):', len(rows))
    print('Unmatched buys (ids):', [b['id'] for b in unmatched])
    print('Total buy USD (matched chunks):', round(total_buy, 8))
    print('Total sell USD (matched chunks):', round(total_sell, 8))
    print('Total realized profit USD (matched):', round(total_profit, 8))


if __name__ == '__main__':
    main()
