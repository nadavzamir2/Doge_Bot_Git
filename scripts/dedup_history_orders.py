#!/usr/bin/env python3
"""Remove duplicate orders from local history file, keeping only unique (id, execution_time, side, price, amount, status)."""
import json
from pathlib import Path

base = Path(__file__).resolve().parent.parent
history = base / 'data' / 'order_history_local.json'

def dedup(rows):
    seen = set()
    cleaned = []
    for r in rows:
        key = (
            str(r.get('id','')),
            str(r.get('execution_time','')),
            str(r.get('side','')),
            round(float(r.get('price',0.0)),8),
            round(float(r.get('amount',0.0)),8),
            str(r.get('status','')),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(r)
    return cleaned

if history.exists():
    with history.open('r', encoding='utf-8') as f:
        try:
            rows = json.load(f)
        except Exception:
            rows = []
    cleaned = dedup(rows)
    with history.open('w', encoding='utf-8') as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print(f"Deduplicated {len(rows)} -> {len(cleaned)} rows in {history.name}")
else:
    print(f"File not found: {history}")
