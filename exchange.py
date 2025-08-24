import os, time, ccxt

class Exchange:
    def __init__(self, paper=True):
        self.paper = paper
        self.client = ccxt.binance({
            "apiKey": os.getenv("BINANCE_API_KEY", ""),
            "secret": os.getenv("BINANCE_API_SECRET", ""),
            "enableRateLimit": True,
        })
        self.markets = self.client.load_markets()

    # ---- Market meta ----
    def ticker_price(self, symbol: str) -> float:
        return float(self.client.fetch_ticker(symbol)["last"])

    def exchange_info(self, symbol: str):
        m = self.markets[symbol]
        precision = m.get("precision", {}) or {}
        limits = m.get("limits", {}) or {}

        amount_prec = precision.get("amount", None)
        price_prec  = precision.get("price",  None)

        amount_step = None
        price_tick  = None
        min_cost    = (limits.get("cost") or {}).get("min", None)
        min_notional = None
        try:
            filters = (m.get("info") or {}).get("filters", [])
            for f in filters:
                t = f.get("filterType")
                if t == "LOT_SIZE":
                    step = f.get("stepSize")
                    if step is not None:
                        amount_step = float(step)
                elif t == "PRICE_FILTER":
                    tick = f.get("tickSize")
                    if tick is not None:
                        price_tick = float(tick)
                elif t in ("NOTIONAL", "MIN_NOTIONAL"):
                    mn = f.get("minNotional")
                    if mn is not None:
                        min_notional = float(mn)
        except Exception:
            pass

        if amount_step is None and amount_prec == 0:
            amount_step = 1.0
        if min_notional is not None:
            min_cost = float(min_notional) if min_cost is None else max(float(min_cost), float(min_notional))
        if min_cost is None:
            min_cost = 5.0

        return {
            "amount_precision": amount_prec,
            "price_precision":  price_prec,
            "amount_step": amount_step,
            "price_tick": price_tick,
            "min_cost": float(min_cost),
        }

    # ---- Client IDs ----
    def new_cid(self, prefix="bot"):
        return f"{prefix}_{int(time.time()*1000)}"

    # ---- Paper/Live orders ----
    def place_limit_buy(self, symbol, amount, price, client_id=None):
        if self.paper:
            return {"id": f"paper_buy_{price}", "status": "paper"}
        params = {"newClientOrderId": client_id} if client_id else {}
        o = self.client.create_order(symbol, "limit", "buy", amount, price, params)
        return {"id": o["id"], "status": o.get("status", "open")}

    def place_limit_sell(self, symbol, amount, price, client_id=None):
        if self.paper:
            return {"id": f"paper_sell_{price}", "status": "paper"}
        params = {"newClientOrderId": client_id} if client_id else {}
        o = self.client.create_order(symbol, "limit", "sell", amount, price, params)
        return {"id": o["id"], "status": o.get("status", "open")}

    def place_market_buy_quote(self, symbol, quote_usdt: float):
        """Market Buy לפי סכום ב-quote (USDT) – נשתמש לקניית BNB מה-profit bank."""
        if self.paper:
            return {"id": "paper_mkt_buy", "status": "paper"}
        o = self.client.create_order(symbol, "market", "buy", None, None, {"quoteOrderQty": quote_usdt})
        return {"id": o["id"], "status": o.get("status", "closed")}

    # ---- Fetch/Cancel ----
    def fetch_open_orders(self, symbol):
        return self.client.fetch_open_orders(symbol)

    def fetch_order(self, order_id, symbol):
        return self.client.fetch_order(order_id, symbol)

    def cancel_order(self, order_id, symbol):
        return self.client.cancel_order(order_id, symbol)

    # ---- Balances ----
    def fetch_free(self, code: str) -> float:
        """יתרה חופשית במטבע (למשל USDT)."""
        b = self.client.fetch_balance()
        try:
            return float((b.get("free") or {}).get(code, 0) or 0)
        except Exception:
            return 0.0
