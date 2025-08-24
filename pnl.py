def realized_profit_usd(buy_price: float, sell_price: float, qty: float,
                        fee_rate_each_side: float = 0.001) -> float:
    gross = (sell_price - buy_price) * qty
    fees  = (buy_price * qty + sell_price * qty) * fee_rate_each_side
    return float(gross - fees)
