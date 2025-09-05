#!/usr/bin/env python3
"""Compute profit impact if all open orders execute.

Outputs:
 - total open buy USD, total open sell USD, net cash change (sell - buy)
 - estimated realized profit from open sells when matched FIFO to remaining buys
"""
import json
from pathlib import Path
from typing import List, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'


def load_json(p: Path):
    if not p.exists():
        return None
    return json.load(p.open('r', encoding='utf-8'))


def main():
    state = load_json(ROOT / 'state.json') or {}
    data_state = load_json(DATA / 'state.json') or {}
    # buy fills from state (use data/state.json if available else top-level)
    buy_fills = data_state.get('buy_fills') or state.get('buy_fills') or {}

    # build buys list
    buys = []
    for bid, info in buy_fills.items():
        amt = float(info.get('amount', 0))
        price = float(info.get('price', 0))
        buys.append({'id': bid, 'price': price, 'amount': amt, 'remaining': amt})

    # gather executed sells from order_history (closed/done/filled)
    order_history = load_json(DATA / 'order_history_local.json') or []
    executed_sells = []
    for o in order_history:
        side = (o.get('side') or '').lower()
        status = (o.get('status') or '').lower()
        if side == 'sell' and status in ('closed','filled','done'):
            executed_sells.append({'price': float(o.get('price',0)), 'amount': float(o.get('amount',0)), 'id': o.get('id')})

    # Also include sell_fills from state (if not present in order_history)
    sell_fills = (data_state.get('sell_fills') or state.get('sell_fills') or {})
    oh_ids = {o.get('id') for o in order_history if isinstance(o, dict) and o.get('id')}
    for sid, s in (sell_fills.items()):
        if sid in oh_ids:
            continue
        executed_sells.append({'price': float(s.get('price',0)), 'amount': float(s.get('amount',0)), 'id': sid})

    # FIFO allocate executed sells to buys to reduce remaining
    for s in executed_sells:
        rem = s['amount']
        for b in buys:
            if rem <= 0:
                break
            take = min(b['remaining'], rem)
            b['remaining'] -= take
            rem -= take
        # if rem>0, sells exceeded buys; ignore extra for remaining calculation

    # Now load open orders
    open_orders = load_json(DATA / 'open_orders_local.json') or []
    open_buys = [o for o in open_orders if (o.get('side') or '').lower() == 'buy']
    open_sells = [o for o in open_orders if (o.get('side') or '').lower() == 'sell']

    total_open_buy_usd = sum(float(o.get('price',0))*float(o.get('amount',0)) for o in open_buys)
    total_open_sell_usd = sum(float(o.get('price',0))*float(o.get('amount',0)) for o in open_sells)

    # Net cash change if all open orders executed
    net_cash_change = total_open_sell_usd - total_open_buy_usd

    # Estimate realized profit: match open sells FIFO to remaining buys
    est_profit = 0.0
    unmatched_sell_amount = 0.0
    for s in open_sells:
        rem = float(s.get('amount',0))
        price = float(s.get('price',0))
        for b in buys:
            if rem <= 0:
                break
            take = min(b['remaining'], rem)
            if take <= 0:
                continue
            est_profit += (price - b['price']) * take
            b['remaining'] -= take
            rem -= take
        if rem > 0:
            unmatched_sell_amount += rem

    print('Open orders summary:')
    print('  open buys count:', len(open_buys), ' total USD:', round(total_open_buy_usd,8))
    print('  open sells count:', len(open_sells), ' total USD:', round(total_open_sell_usd,8))
    print('Net cash change if all open orders execute (sell - buy) =', round(net_cash_change,8), 'USD')
    print('Estimated realized profit from open sells (matched to remaining buys FIFO) =', round(est_profit,8), 'USD')
    if unmatched_sell_amount > 0:
        print('  Note: sells exceed remaining buy inventory by', unmatched_sell_amount, 'units — profit for that portion is not estimated (would create short position).')


if __name__ == '__main__':
    main()
