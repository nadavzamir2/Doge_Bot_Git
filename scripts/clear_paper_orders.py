#!/usr/bin/env python3
"""Remove all PAPER mode orders from local open orders and history files."""
import json
from pathlib import Path

base = Path(__file__).resolve().parent.parent
open_orders = base / 'data' / 'open_orders_local.json'
history = base / 'data' / 'order_history_local.json'

def filter_paper(rows):
    return [r for r in rows if not str(r.get('id','')).startswith('PAPER-')]

def clean_file(path):
    if not path.exists():
        return
    with path.open('r', encoding='utf-8') as f:
        try:
            rows = json.load(f)
        except Exception:
            rows = []
    cleaned = filter_paper(rows)
    with path.open('w', encoding='utf-8') as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print(f"Cleaned {path.name}: {len(rows)} -> {len(cleaned)} rows")

clean_file(open_orders)
clean_file(history)
