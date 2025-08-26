"""
Profit and Loss (PnL) calculation utilities for trading operations.

This module provides functions to calculate realized profits considering
trading fees and market conditions.
"""


def calculate_realized_profit_usd(
    buy_price: float, sell_price: float, quantity: float, fee_rate_per_side: float = 0.001
) -> float:
    """
    Calculate realized profit in USD from a buy-sell trade pair.

    Args:
        buy_price: Price at which the asset was bought
        sell_price: Price at which the asset was sold
        quantity: Quantity of asset traded
        fee_rate_per_side: Fee rate applied to each side of the trade (default 0.1%)

    Returns:
        float: Net realized profit in USD after fees

    Example:
        >>> calculate_realized_profit_usd(100.0, 110.0, 1.0, 0.001)
        9.79  # $10 gross profit - $0.21 fees
    """
    gross_profit = (sell_price - buy_price) * quantity

    # Calculate fees on both buy and sell sides
    buy_fees = buy_price * quantity * fee_rate_per_side
    sell_fees = sell_price * quantity * fee_rate_per_side
    total_fees = buy_fees + sell_fees

    return float(gross_profit - total_fees)


# Legacy function name for backward compatibility
def realized_profit_usd(
    buy_price: float, sell_price: float, qty: float, fee_rate_each_side: float = 0.001
) -> float:
    """
    Legacy function for calculating realized profit.

    Deprecated: Use calculate_realized_profit_usd instead.
    """
    return calculate_realized_profit_usd(buy_price, sell_price, qty, fee_rate_each_side)
