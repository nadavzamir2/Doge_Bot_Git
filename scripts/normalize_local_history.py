#!/usr/bin/env python3
"""Normalize local order history timestamps and statuses.
Creates a timestamped backup at data/order_history_local.json.bak-<iso>.
-converts numeric ms timestamps (int or digit strings) to ISO UTC
-sets row['time'] to execution_time if time is missing or '—'
-normalizes status to 'executed' or 'canceled'
"""
from __future__ import annotations
import json, time, re, shutil, pathlib
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE / 'data'
HIST_FILE = DATA_DIR / 'order_history_local.json'

def ts_to_iso(ts):
    if ts is None:
        return None
    # int/float assumed ms
    try:
        if isinstance(ts, (int, float)):
            return datetime.utcfromtimestamp(ts/1000.0).isoformat() + 'Z'
        if isinstance(ts, str):
            s = ts.strip()
            if not s:
                return None
            # digits-only => ms
            if re.fullmatch(r"\d+", s):
                return datetime.utcfromtimestamp(int(s)/1000.0).isoformat() + 'Z'
            # already ISO-ish? simple heuristic
            if 'T' in s and s.endswith('Z'):
                return s
            # attempt parse common numeric float string
            try:
                f = float(s)
                return datetime.utcfromtimestamp(int(f)/1000.0).isoformat() + 'Z'
            except Exception:
                return s
    except Exception:
        return str(ts)


def norm_status(s):
    s = (s or '').lower()
    if s in ('canceled','cancelled'):
        return 'canceled'
    if s in ('filled','closed','done','executed'):
        return 'executed'
    return 'executed'


def main():
    if not HIST_FILE.exists():
        print('history file not found:', HIST_FILE)
        return
    bak = HIST_FILE.with_name(HIST_FILE.name + '.bak-' + datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
    shutil.copy2(HIST_FILE, bak)
    print('backup created:', bak)
    raw = []
    with HIST_FILE.open('r', encoding='utf-8') as f:
        try:
            raw = json.load(f)
        except Exception as e:
            print('failed to read json:', e)
            return
    changed = 0
    cleaned = []
    for r in raw:
        nr = dict(r)
        et = nr.get('execution_time')
        tt = nr.get('time')
        et_iso = ts_to_iso(et)
        tt_iso = ts_to_iso(tt)
        # prefer execution_time when time missing or placeholder
        if (not tt_iso) or (isinstance(tt, str) and tt.strip() == '—'):
            if et_iso:
                nr['time'] = et_iso
            else:
                if tt_iso:
                    nr['time'] = tt_iso
        else:
            nr['time'] = tt_iso
        if et_iso:
            nr['execution_time'] = et_iso
        elif nr.get('execution_time') in (None, '', '—'):
            nr['execution_time'] = nr.get('execution_time')
        # normalize status
        nr['status'] = norm_status(nr.get('status'))
        if nr != r:
            changed += 1
        cleaned.append(nr)
    with HIST_FILE.open('w', encoding='utf-8') as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print('rows:', len(cleaned), 'changed:', changed)
    print('normalized file written:', HIST_FILE)

if __name__ == '__main__':
    main()
