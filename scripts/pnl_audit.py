#!/usr/bin/env python3
"""Simple audit: verify CSV totals vs recompute output."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'

csv_path = DATA / 'pnl_recompute_fifo.csv'
if not csv_path.exists():
    print('CSV missing:', csv_path)
    raise SystemExit(1)

buy_sum = 0.0
sell_sum = 0.0
profit_sum = 0.0

with csv_path.open('r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        b = float(r.get('buy_price', 0)) * float(r.get('buy_amount_chunk', 0))
        s = float(r.get('sell_price', 0)) * float(r.get('sell_amount_chunk', 0))
        p = float(r.get('profit_usd', 0))
        buy_sum += b
        sell_sum += s
        profit_sum += p

print('CSV sums: buy=', round(buy_sum,8), ' sell=', round(sell_sum,8), ' profit=', round(profit_sum,8))
# compare to state
state_path = DATA / 'state.json'
if state_path.exists():
    import json
    st = json.load(state_path.open())
    print('state.realized_profit_usd =', st.get('realized_profit_usd'))
else:
    print('no data/state.json')
