"""
Simple logging and notification utilities for the trading bot.

This module provides basic logging functions with colored output
for different message types.
"""

import sys
from typing import Optional


def log_info(message: str, prefix: str = "[INFO]") -> None:
    """
    Log an informational message.

    Args:
        message: Message to log
        prefix: Optional prefix for the message
    """
    print(f"{prefix} {message}")


def log_warning(message: str, prefix: str = "[WARN]") -> None:
    """
    Log a warning message.

    Args:
        message: Warning message to log
        prefix: Optional prefix for the message
    """
    print(f"{prefix} {message}", file=sys.stderr)


def log_error(message: str, prefix: str = "[ERROR]") -> None:
    """
    Log an error message.

    Args:
        message: Error message to log
        prefix: Optional prefix for the message
    """
    print(f"{prefix} {message}", file=sys.stderr)


# Legacy function names for backward compatibility
def info(msg: str) -> None:
    """Legacy function. Use log_info instead."""
    log_info(msg)


def warn(msg: str) -> None:
    """Legacy function. Use log_warning instead."""
    log_warning(msg)


def err(msg: str) -> None:
    """Legacy function. Use log_error instead."""
    log_error(msg)
