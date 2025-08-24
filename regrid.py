#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
regrid.py — Cancel all open orders and (optionally) seed a new grid by range & step.
- Uses ccxt and your .env in ~/doge_bot/.env
- Supports dry preview (no trading) and live apply
- Safe by default: preview unless you pass --apply
"""

import os, sys, math, argparse, time
from dataclasses import dataclass
from typing import List, Tuple
from dotenv import load_dotenv

# --------------------
# Load env
# --------------------
ENV_FILE = os.path.expanduser("~/doge_bot/.env")
load_dotenv(ENV_FILE)

import ccxt

def make_client():
    region = os.getenv("BINANCE_REGION","com").strip().lower()
    Cls = ccxt.binanceus if region == "us" else ccxt.binance
    api_key = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY") or ""
    api_secret = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET") or ""
    kwargs = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,
        }
    }
    if api_key and api_secret:
        kwargs["apiKey"] = api_key
        kwargs["secret"] = api_secret
    ex = Cls(kwargs)
    try:
        ex.load_markets()
    except Exception as e:
        print(f"[WARN] load_markets failed: {e}", file=sys.stderr)
    return ex

# --------------------
# Helpers
# --------------------
@dataclass
class GridParams:
    pair: str
    pmin: float
    pmax: float
    step_pct: float            # e.g. 0.8 means 0.8%
    base_order_usd: float      # dollar size per order

def geometric_levels(pmin: float, pmax: float, step_pct: float) -> List[float]:
    """
    Make price levels pmin .. pmax using geometric spacing by step_pct%.
    """
    if pmin <= 0 or pmax <= 0 or pmax <= pmin:
        return []
    r = 1.0 + (step_pct / 100.0)
    levels = [pmin]
    # avoid infinite loop if step too tiny
    max_levels = 5000
    while levels[-1] * r < pmax and len(levels) < max_levels:
        levels.append(levels[-1] * r)
    if levels[-1] != pmax:
        levels.append(pmax)
    # unique & sorted
    uniq = sorted(set([round(x, 8) for x in levels]))
    return uniq

def lot_from_usd(ex: ccxt.Exchange, pair: str, price: float, usd: float) -> float:
    """
    Convert USD amount to base-asset amount, rounded to exchange precision.
    """
    if price <= 0: return 0.0
    amt = usd / price
    market = ex.markets.get(pair, {})
    prec = market.get("precision", {})
    step = market.get("limits", {}).get("amount", {}).get("min")
    # round by precision if exists
    amount = amt
    if "amount" in prec and isinstance(prec["amount"], int):
        q = prec["amount"]
        amount = float(f"{amt:.{q}f}")
    # enforce min amount if exists
    if step and amount < step:
        amount = step
    return amount

def fetch_last_price(ex: ccxt.Exchange, pair: str) -> float:
    t = ex.fetch_ticker(pair)
    return float(t.get("last") or t.get("close") or t.get("bid") or t.get("ask") or 0.0)

def cancel_all_open_orders(ex: ccxt.Exchange, pair: str):
    try:
        opens = ex.fetch_open_orders(pair)
    except Exception as e:
        print(f"[ERR] fetch_open_orders failed: {e}")
        return
    if not opens:
        print("[OK] No open orders to cancel.")
        return
    print(f"[INFO] Cancelling {len(opens)} open orders...")
    for o in opens:
        oid = o.get("id")
        try:
            ex.cancel_order(oid, pair)
            print(f"  - cancelled {oid} ({o.get('side')} {o.get('price')})")
        except Exception as e:
            print(f"  ! cancel failed for {oid}: {e}")
        time.sleep(ex.rateLimit/1000.0 if getattr(ex, "rateLimit", 0) else 0.1)
    print("[OK] Done cancelling.")

def seed_grid(ex: ccxt.Exchange, gp: GridParams, apply: bool):
    last = fetch_last_price(ex, gp.pair)
    if last <= 0:
        print("[ERR] Could not fetch last price. Aborting.")
        return

    levels = geometric_levels(gp.pmin, gp.pmax, gp.step_pct)
    if not levels or len(levels) < 2:
        print("[ERR] Not enough levels for grid (check min/max/step).")
        return

    buys: List[Tuple[float,float]] = []   # (price, amount)
    sells: List[Tuple[float,float]] = []

    for px in levels:
        if px < last:
            amt = lot_from_usd(ex, gp.pair, px, gp.base_order_usd)
            if amt > 0: buys.append((px, amt))
        elif px > last:
            amt = lot_from_usd(ex, gp.pair, px, gp.base_order_usd)
            if amt > 0: sells.append((px, amt))
        # if px == last -> skip exact midpoint

    print(f"\n[PLAN] Pair: {gp.pair} | last: {last:.6f}")
    print(f"[PLAN] Range: {gp.pmin:.6f} – {gp.pmax:.6f} | step: {gp.step_pct}%")
    print(f"[PLAN] Base order: ${gp.base_order_usd:.2f} | Levels: {len(levels)}")
    print(f"[PLAN] Buy layers: {len(buys)} | Sell layers: {len(sells)}")

    if not apply:
        # Preview first/last few
        def head_tail(rows):
            if len(rows) <= 6: return rows
            return rows[:3] + [("...", 0.0)] + rows[-3:]
        print("\n[PREVIEW] First/last BUY levels (price, amount):")
        for p,a in head_tail(buys): print(f"  {p:.6f}, {a}" if p!="..." else "  ...")
        print("\n[PREVIEW] First/last SELL levels (price, amount):")
        for p,a in head_tail(sells): print(f"  {p:.6f}, {a}" if p!="..." else "  ...")
        print("\n[NOTE] Dry preview only. Use --apply to place orders.")
        return

    # Apply live
    print("\n[APPLY] Placing BUY orders...")
    for p, a in buys:
        try:
            ex.create_limit_buy_order(gp.pair, a, p, params={"newClientOrderId": f"grid_buy_{int(p*1e6)}"})
            print(f"  + BUY {a} @ {p}")
        except Exception as e:
            print(f"  ! BUY {a} @ {p} failed: {e}")
        time.sleep(ex.rateLimit/1000.0 if getattr(ex, "rateLimit", 0) else 0.2)

    print("[APPLY] Placing SELL orders...")
    for p, a in sells:
        try:
            ex.create_limit_sell_order(gp.pair, a, p, params={"newClientOrderId": f"grid_sell_{int(p*1e6)}"})
            print(f"  + SELL {a} @ {p}")
        except Exception as e:
            print(f"  ! SELL {a} @ {p} failed: {e}")
        time.sleep(ex.rateLimit/1000.0 if getattr(ex, "rateLimit", 0) else 0.2)

    print("\n[OK] Grid seeded.")

def main():
    ap = argparse.ArgumentParser(description="Cancel open orders and (re)seed grid by range/step.")
    ap.add_argument("--pair", default=os.getenv("PAIR","DOGE/USDT"))
    ap.add_argument("--min", type=float, default=float(os.getenv("GRID_MIN","0") or 0))
    ap.add_argument("--max", type=float, default=float(os.getenv("GRID_MAX","0") or 0))
    ap.add_argument("--step", type=float, default=float(os.getenv("GRID_STEP_PCT","0.8")))
    ap.add_argument("--base", type=float, default=float(os.getenv("BASE_ORDER_USD","5")))
    ap.add_argument("--cancel-only", action="store_true", help="Only cancel existing open orders.")
    ap.add_argument("--apply", action="store_true", help="Place new grid orders (LIVE). Omit for dry preview.")
    args = ap.parse_args()

    ex = make_client()

    # 1) cancel all open orders
    cancel_all_open_orders(ex, args.pair)
    if args.cancel_only:
        return

    # 2) preview / apply new grid
    gp = GridParams(pair=args.pair, pmin=args.min, pmax=args.max, step_pct=args.step, base_order_usd=args.base)
    seed_grid(ex, gp, apply=args.apply)

if __name__ == "__main__":
    main()
