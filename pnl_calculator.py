import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from exchange import Exchange

def calculate_unrealized_pnl(
    inventory_doge: float,
    avg_buy_price: float,
    current_price: float
) -> Dict[str, float]:
    """
    Calculates unrealized profit and loss based on current inventory.
    """
    if inventory_doge == 0:
        return {"unrealized_pnl_usd": 0.0, "unrealized_pnl_pct": 0.0}

    inventory_value_at_cost = inventory_doge * avg_buy_price
    inventory_value_at_market = inventory_doge * current_price
    
    unrealized_pnl_usd = inventory_value_at_market - inventory_value_at_cost
    
    unrealized_pnl_pct = 0.0
    if inventory_value_at_cost > 0:
        unrealized_pnl_pct = (unrealized_pnl_usd / inventory_value_at_cost) * 100

    return {
        "unrealized_pnl_usd": unrealized_pnl_usd,
        "unrealized_pnl_pct": unrealized_pnl_pct,
    }

def calculate_realized_pnl(
    exchange: Exchange,
    pair: str,
    since: Optional[int] = None
) -> Dict[str, float]:
    """
    Calculates realized PnL, total fees, and trade counts from historical trades.
    """
    try:
        # Fetch all trades since the start if `since` is not specified
        trades = exchange.fetch_my_trades(pair, since=since, limit=1000)
    except Exception as e:
        print(f"Could not fetch trades: {e}")
        return {
            "realized_pnl_usd": 0.0,
            "total_fees_usd": 0.0,
            "buy_trades_count": 0,
            "sell_trades_count": 0,
            "total_buy_volume_usd": 0.0,
            "total_sell_volume_usd": 0.0,
        }

    realized_pnl_usd = 0.0
    total_fees_usd = 0.0
    buy_trades_count = 0
    sell_trades_count = 0
    total_buy_volume_usd = 0.0
    total_sell_volume_usd = 0.0

    # This is a simplified PnL calculation. For 100% accuracy, a full ledger is needed.
    # This method calculates profit by pairing sells with a running average cost of buys.
    inventory_qty = 0.0
    inventory_cost = 0.0

    for trade in sorted(trades, key=lambda x: x['timestamp']):
        side = trade['side']
        price = trade['price']
        amount = trade['amount']
        cost = trade['cost']
        fee = trade.get('fee', {})
        
        if fee and fee.get('cost'):
            # This assumes the fee is in the quote currency (e.g., USDT).
            # A more robust solution would handle fees in base or quote currency.
            if fee.get('currency') == exchange.market(pair)['quote']:
                 total_fees_usd += fee['cost']
            # If fee is in base currency, it needs conversion. For now, we skip this complexity.

        if side == 'buy':
            buy_trades_count += 1
            total_buy_volume_usd += cost
            
            # Update inventory
            current_inventory_value = inventory_qty * (inventory_cost / inventory_qty if inventory_qty > 0 else 0)
            new_total_cost = current_inventory_value + cost
            inventory_qty += amount
            inventory_cost = new_total_cost

        elif side == 'sell':
            sell_trades_count += 1
            total_sell_volume_usd += cost

            if inventory_qty > 0:
                avg_cost_of_inventory = inventory_cost / inventory_qty
                cost_of_goods_sold = amount * avg_cost_of_inventory
                profit = cost - cost_of_goods_sold
                realized_pnl_usd += profit
                
                # Update inventory
                inventory_qty -= amount
                inventory_cost -= cost_of_goods_sold
                if inventory_qty < 1e-9: # Handle float precision issues
                    inventory_qty = 0
                    inventory_cost = 0

    return {
        "realized_pnl_usd": realized_pnl_usd,
        "total_fees_usd": total_fees_usd,
        "buy_trades_count": buy_trades_count,
        "sell_trades_count": sell_trades_count,
        "total_buy_volume_usd": total_buy_volume_usd,
        "total_sell_volume_usd": total_sell_volume_usd,
    }

def calculate_all_pnl(
    exchange: Exchange,
    pair: str,
    inventory_doge: float,
    avg_buy_price: float,
    current_price: float,
    initial_investment_usd: float,
    since: Optional[int] = None
) -> Dict[str, Any]:
    """
    Calculates and combines all PnL metrics.
    """
    realized = calculate_realized_pnl(exchange, pair, since)
    unrealized = calculate_unrealized_pnl(inventory_doge, avg_buy_price, current_price)

    # In a grid strategy, "grid profit" is essentially the realized profit from completed buy/sell cycles.
    grid_profit_usd = realized['realized_pnl_usd']
    
    total_profit_usd = realized['realized_pnl_usd'] + unrealized['unrealized_pnl_usd']
    
    profit_pct = 0.0
    if initial_investment_usd > 0:
        profit_pct = (total_profit_usd / initial_investment_usd) * 100

    return {
        "realized_profit_usd": realized['realized_pnl_usd'],
        "unrealized_profit_usd": unrealized['unrealized_pnl_usd'],
        "grid_profit_usd": grid_profit_usd,
        "fees_usd": realized['total_fees_usd'],
        "total_profit_usd": total_profit_usd,
        "profit_pct": profit_pct,
        "sell_trades_count": realized['sell_trades_count'],
        # You can include other stats from `realized` if needed on the dashboard
    }
