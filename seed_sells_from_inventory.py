#!/usr/bin/env python3
import os, argparse, math, time
from decimal import Decimal
from dotenv import load_dotenv
import ccxt

SYMBOL = "DOGE/USDT"
SEED_TAG = "SEED"

def main():
    p = argparse.ArgumentParser(description="Place a sell ladder from existing DOGE inventory")
    p.add_argument("--levels", type=int, default=8, help="כמה מדרגות מכירה להציב")
    p.add_argument("--step-pct", type=float, default=1.0, help="מרווח בין מדרגות (%)")
    p.add_argument("--lot-doge", type=float, default=30.0, help="כמות DOGE לכל מדרגה")
    p.add_argument("--dry-run", action="store_true", help="הצבה כ־Dry-run (לא שולח הזמנות)")
    p.add_argument("--cancel-seed", action="store_true", help="לבטל הזמנות SEED פתוחות במקום להציב חדשות")
    args = p.parse_args()

    load_dotenv()
    key = os.getenv("BINANCE_API_KEY")
    sec = os.getenv("BINANCE_API_SECRET")
    if not key or not sec:
        raise SystemExit("Missing BINANCE_API_KEY / BINANCE_API_SECRET in .env")

    client = ccxt.binance({
        "apiKey": key,
        "secret": sec,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })

    client.load_markets()
    mkt = client.market(SYMBOL)

    # רט״ז
    ticker = client.fetch_ticker(SYMBOL)
    last = float(ticker["last"])
    print(f"[INFO] Last price {SYMBOL}: {last:.6f}")

    # יתרה חופשית
    balance = client.fetch_balance()
    free_doge = float(balance["free"].get("DOGE", 0.0))
    open_orders = client.fetch_open_orders(SYMBOL)

    seed_orders = [o for o in open_orders if (o.get("clientOrderId") or "").startswith(SEED_TAG)]
    if args.cancel_seed:
        print(f"[INFO] Cancelling {len(seed_orders)} existing SEED orders...")
        for o in seed_orders:
            try:
                client.cancel_order(o["id"], SYMBOL)
                print(f"[OK] Canceled {o['id']} @ {o['price']}")
            except Exception as e:
                print(f"[WARN] Cancel failed {o.get('id')}: {e}")
        return

    if free_doge <= 0:
        raise SystemExit("[ABORT] No free DOGE balance found.")

    print(f"[INFO] Free DOGE: {free_doge}")

    # כמה הזמנות אפשר להציב לפי ה-lot
    max_levels_by_balance = int(free_doge // args.lot_doge)
    levels = max(0, min(args.levels, max_levels_by_balance))
    if levels == 0:
        raise SystemExit(f"[ABORT] Not enough free DOGE for lot {args.lot_doge} x {args.levels} levels")

    # מחירים לסולם (מעל המחיר הנוכחי)
    step = args.step_pct / 100.0
    targets = [ last * ((1.0 + step) ** (i+1)) for i in range(levels) ]

    # אל תכפיל הזמנות שכבר קיימות סביב אותם מחירים (±0.05%)
    def too_close(p1, p2):
        return abs(p1 - p2) / p2 <= 0.0005

    existing_prices = [ float(o["price"]) for o in open_orders if o["side"].lower()=="sell" ]
    plan = []
    for px in targets:
        if any(too_close(px, ep) for ep in existing_prices):
            print(f"[SKIP] Near existing sell @ {px:.6f}")
            continue
        plan.append(px)

    if not plan:
        print("[INFO] Nothing to place (all targets near existing sells).")
        return

    # עיגון דיוק לפי הבורסה
    def p2p(x: float) -> str:  # price to precision (string)
        return client.price_to_precision(SYMBOL, x)
    def a2p(x: float) -> str:  # amount to precision (string)
        return client.amount_to_precision(SYMBOL, x)

    print(f"[PLAN] Will place {len(plan)} sells, lot={args.lot_doge} DOGE, step={args.step_pct}%")
    for i, px in enumerate(plan, 1):
        price_str = p2p(px)
        amt_str   = a2p(args.lot_doge)
        cid = f"{SEED_TAG}-{int(time.time())}-{i}"
        print(f"  SELL {amt_str} @ {price_str}  cid={cid}")
        if not args.dry_run:
            try:
                client.create_order(SYMBOL, "limit", "sell", float(amt_str), float(price_str), {
                    "newClientOrderId": cid
                })
                print("   -> OK")
            except Exception as e:
                print(f"   -> FAIL: {e}")

if __name__ == "__main__":
    main()
