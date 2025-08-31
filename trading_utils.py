#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trading utilities for the DOGE Grid Trading Bot.

This module contains common utility functions for trading operations,
price/amount rounding, and other shared functionality.
"""

import math
from decimal import Decimal, ROUND_DOWN, ROUND_FLOOR
from typing import Optional


def round_price_down(price: Decimal, tick_size: Decimal) -> Decimal:
    """
    Round a price down to the nearest valid tick size.
    
    Args:
        price: Price to round
        tick_size: Minimum price increment (tick size)
        
    Returns:
        Price rounded down to nearest tick
        
    Example:
        >>> round_price_down(Decimal('0.123456'), Decimal('0.000001'))
        Decimal('0.123456')
        >>> round_price_down(Decimal('0.123456'), Decimal('0.0001'))
        Decimal('0.1234')
    """
    if tick_size == 0:
        return price
    
    # Calculate how many ticks fit into the price
    num_ticks = price / tick_size
    
    # Round down to whole number of ticks
    rounded_ticks = num_ticks.quantize(Decimal(1), rounding=ROUND_DOWN)
    
    # Convert back to price
    return rounded_ticks * tick_size


def round_amount_down(amount: Decimal, step_size: Decimal) -> Decimal:
    """
    Round an amount down to the nearest valid step size.
    
    Args:
        amount: Amount to round
        step_size: Minimum amount increment (step size)
        
    Returns:
        Amount rounded down to nearest step
        
    Example:
        >>> round_amount_down(Decimal('123.456789'), Decimal('0.01'))
        Decimal('123.45')
        >>> round_amount_down(Decimal('123.456789'), Decimal('1'))
        Decimal('123')
    """
    if step_size == 0:
        return amount
    
    # Calculate how many steps fit into the amount
    num_steps = amount / step_size
    
    # Round down to whole number of steps
    rounded_steps = num_steps.quantize(Decimal(1), rounding=ROUND_DOWN)
    
    # Convert back to amount
    return rounded_steps * step_size


def round_price_to_precision(price: float, precision: Optional[int] = None, tick_size: Optional[float] = None) -> float:
    """
    Round price down using either precision or tick size (compatibility with utils.py).
    
    Args:
        price: Price to round
        precision: Number of decimal places (optional)
        tick_size: Minimum price increment (optional)
        
    Returns:
        Rounded price as float
    """
    if tick_size:
        # Use tick size for more accurate rounding
        return float(round_price_down(Decimal(str(price)), Decimal(str(tick_size))))
    elif precision is not None:
        # Fallback to precision-based rounding
        factor = 10 ** precision
        return math.floor(price * factor) / factor
    else:
        return price


def round_amount_to_precision(amount: float, precision: Optional[int] = None, step_size: Optional[float] = None) -> float:
    """
    Round amount down using either precision or step size (compatibility with utils.py).
    
    Args:
        amount: Amount to round
        precision: Number of decimal places (optional)
        step_size: Minimum amount increment (optional)
        
    Returns:
        Rounded amount as float
    """
    if step_size:
        # Use step size for more accurate rounding
        return float(round_amount_down(Decimal(str(amount)), Decimal(str(step_size))))
    elif precision is not None:
        # Fallback to precision-based rounding
        factor = 10 ** precision
        return math.floor(amount * factor) / factor
    else:
        return amount


def to_decimal(value) -> Decimal:
    """
    Convert various numeric types to Decimal safely.
    
    Args:
        value: Value to convert (int, float, str, or Decimal)
        
    Returns:
        Decimal representation of the value
        
    Raises:
        ValueError: If value cannot be converted to Decimal
    """
    if isinstance(value, Decimal):
        return value
    elif isinstance(value, (int, float)):
        return Decimal(str(value))
    elif isinstance(value, str):
        return Decimal(value)
    else:
        raise ValueError(f"Cannot convert {type(value)} to Decimal: {value}")


def format_price(price: float, decimals: int = 6) -> str:
    """
    Format a price for display with consistent decimal places.
    
    Args:
        price: Price to format
        decimals: Number of decimal places
        
    Returns:
        Formatted price string
    """
    return f"{price:.{decimals}f}"


def format_amount(amount: float, decimals: int = 2) -> str:
    """
    Format an amount for display with consistent decimal places.
    
    Args:
        amount: Amount to format
        decimals: Number of decimal places
        
    Returns:
        Formatted amount string
    """
    return f"{amount:.{decimals}f}"


def calculate_order_value(price: float, amount: float) -> float:
    """
    Calculate the total value of an order.
    
    Args:
        price: Price per unit
        amount: Number of units
        
    Returns:
        Total value (price * amount)
    """
    return price * amount


def validate_order_params(price: float, amount: float, min_cost: float = 0.0) -> tuple[bool, str]:
    """
    Validate order parameters against exchange requirements.
    
    Args:
        price: Order price
        amount: Order amount
        min_cost: Minimum order cost required by exchange
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if price <= 0:
        return False, "Price must be positive"
    
    if amount <= 0:
        return False, "Amount must be positive"
    
    order_value = calculate_order_value(price, amount)
    if order_value < min_cost:
        return False, f"Order value {order_value:.6f} below minimum {min_cost:.6f}"
    
    return True, ""