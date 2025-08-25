#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
rotate_price_history.py
-----------------------
מייצא את price_history.json לפורמט יומי JSONL.gz:
- data/price_history-YYYY-MM-DD.jsonl.gz
- משאיר את price_history.json רק עם נקודות "טריות" (N ימים אחרונים).

פרמטרים דרך ENV:
  ROTATE_KEEP_DAYS=7   (כמה ימים להשאיר בנוכחי)
"""

from __future__ import annotations
import os, json, gzip, datetime as dt, pathlib

KEEP_DAYS = int(os.getenv("ROTATE_KEEP_DAYS", "7"))

DATA_DIR = pathlib.Path.home() / "doge_bot" / "data"
SRC = DATA_DIR / "price_history.json"

def _load_points():
    if not SRC.exists():
        return []
    with open(SRC, "r", encoding="utf-8") as f:
        j = json.load(f)
    if not isinstance(j, list):
        return []
    out = []
    for p in j:
        try:
            t = int(p.get("t"))
            price = float(p.get("p"))
            out.append((t, price))
        except Exception:
            continue
    return out

def _date_key(ms: int) -> str:
    d = dt.datetime.utcfromtimestamp(ms/1000.0)
    return d.strftime("%Y-%m-%d")

def _write_day_file(day: str, rows: list[tuple[int,float]]) -> None:
    path = DATA_DIR / f"price_history-{day}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        for t, p in rows:
            gz.write(json.dumps({"t": t, "p": p}, ensure_ascii=False) + "\n")

def main():
    pts = _load_points()
    if not pts:
        print("[i] no points to rotate.")
        return

    # קיבוץ לפי יום (UTC)
    by_day: dict[str, list[tuple[int,float]]] = {}
    for t, p in pts:
        day = _date_key(t)
        by_day.setdefault(day, []).append((t, p))

    # כתיבה ליומיים-שלושה אחרונים נשאיר בזיכרון, את השאר נאכסן
    all_days = sorted(by_day.keys())
    keep_tail = all_days[-KEEP_DAYS:] if len(all_days) > KEEP_DAYS else all_days
    export_days = [d for d in all_days if d not in keep_tail]

    for day in export_days:
        _write_day_file(day, by_day[day])
        print(f"[OK] wrote {len(by_day[day])} pts → price_history-{day}.jsonl.gz")

    # השארת הימים האחרונים בקובץ המקורי
    remain = []
    for day in keep_tail:
        remain.extend(by_day[day])
    remain.sort(key=lambda x: x[0])  # לפי זמן

    tmp = SRC.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([{"t": t, "p": p} for t, p in remain], f, ensure_ascii=False)
    os.replace(tmp, SRC)
    print(f"[OK] kept {len(remain)} recent pts in price_history.json (days={len(keep_tail)})")

if __name__ == "__main__":
    main()
