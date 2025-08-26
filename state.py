"""
State management module for the DOGE trading bot.

This module handles loading and saving the application state to/from JSON files,
including trading parameters, bank balances, and dynamic trading bounds.
"""

import json
import os
from typing import Dict, Any


# Default state configuration
DEFAULT_STATE = {
    "base_order_usd": 5.0,
    "bank": {"usd": 0.0, "reinvest_usd": 0.0, "bnb_usd": 0.0},
    "dyn_bounds": None,  # [lower, upper] dynamic trading bounds
}

STATE_FILE = "state.json"


def load_state() -> Dict[str, Any]:
    """
    Load the trading bot state from the JSON file.

    Returns:
        Dict[str, Any]: The loaded state dictionary with default values applied
                       for any missing keys.

    Note:
        If the state file doesn't exist or is corrupted, returns the default state.
    """
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                # Ensure all default keys are present
                for key, value in DEFAULT_STATE.items():
                    data.setdefault(key, value)
                return data
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load state file: {e}")

    return DEFAULT_STATE.copy()


def save_state(state_data: Dict[str, Any]) -> None:
    """
    Save the trading bot state to the JSON file atomically.

    Args:
        state_data: Dictionary containing the state to save

    Note:
        Uses atomic write (write to temp file then replace) to prevent
        corruption if the process is interrupted during writing.
    """
    temp_file = STATE_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(state_data, file, indent=2, ensure_ascii=False)
        os.replace(temp_file, STATE_FILE)
    except (IOError, OSError) as e:
        print(f"Error: Could not save state file: {e}")
        # Clean up temp file if it exists
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
