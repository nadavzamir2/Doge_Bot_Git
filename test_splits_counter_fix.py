#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test for the splits counter fix.

Tests that the system correctly tracks both sell trades and actual profit splits separately.
"""

import os
import tempfile
import json
import pathlib
import pytest
from unittest.mock import patch, MagicMock

# Import our modules
import utils_stats
import profit_split


def test_sell_trades_vs_actual_splits_separation():
    """Test that sell trades and actual profit splits are tracked separately."""
    
    # Use a temporary directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        test_stats_file = pathlib.Path(tmpdir) / "runtime_stats.json"
        
        # Patch the STATS_FILE location
        with patch.object(utils_stats, 'STATS_FILE', test_stats_file):
            # Test that the default stats include both counters
            stats = utils_stats.read_stats()
            assert "sell_trades_count" in stats
            assert "actual_splits_count" in stats
            assert stats["sell_trades_count"] == 0
            assert stats["actual_splits_count"] == 0
            
            # Test adding sell trades
            utils_stats.add_realized_profit(10.0, inc_sell_trades=3)
            stats = utils_stats.read_stats()
            assert stats["sell_trades_count"] == 3
            assert stats["actual_splits_count"] == 0  # Should not change
            
            # Test adding actual splits
            utils_stats.add_actual_splits(2)
            stats = utils_stats.read_stats()
            assert stats["sell_trades_count"] == 3  # Should not change
            assert stats["actual_splits_count"] == 2


def test_profit_split_tracks_actual_chunks():
    """Test that profit_split.py tracks actual chunks processed."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_split_file = pathlib.Path(tmpdir) / "split_state.json"
        test_stats_file = pathlib.Path(tmpdir) / "runtime_stats.json"
        
        # Patch both file locations
        with patch.object(profit_split, 'STATE_FILE_PATH', test_split_file), \
             patch.object(utils_stats, 'STATS_FILE', test_stats_file):
            
            # Mock exchange client
            mock_exchange = MagicMock()
            mock_exchange.fetch_ticker.return_value = {"last": 600.0}
            mock_exchange.amount_to_precision.return_value = 0.01
            mock_exchange.create_order.return_value = {"id": "test_order"}
            
            # Test with profit amount that creates complete chunks
            # 8.5 USD should create 2 complete chunks (4 USD each) with 0.5 remainder
            result = profit_split.handle_realized_profit(8.5, mock_exchange)
            
            # Should have processed 2 chunks
            assert result["chunks"] == 2
            
            # Check that actual_splits_count was updated
            stats = utils_stats.read_stats()
            assert stats["actual_splits_count"] == 2


def test_backward_compatibility():
    """Test that old field names are still supported for backward compatibility."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        test_stats_file = pathlib.Path(tmpdir) / "runtime_stats.json"
        
        # Create a legacy stats file with old field name
        legacy_stats = {
            "schema_version": 1,
            "cumulative_profit_usd": 100.0,
            "bnb_converted_usd": 50.0,
            "splits_count": 25,  # Old field name
            "trade_count": 100,
            "trigger_amount_usd": 4.0,
            "last_update_ts": 1234567890
        }
        test_stats_file.write_text(json.dumps(legacy_stats))
        
        with patch.object(utils_stats, 'STATS_FILE', test_stats_file):
            # Should upgrade the schema and add new fields
            stats = utils_stats.read_stats()
            assert "sell_trades_count" in stats
            assert "actual_splits_count" in stats
            assert stats["actual_splits_count"] == 0  # New field defaults to 0


def test_validation_script_updated():
    """Test that the validation script checks for the new field names."""
    from validate_data import REQUIRED_STATS_KEYS
    
    assert "sell_trades_count" in REQUIRED_STATS_KEYS
    assert "actual_splits_count" in REQUIRED_STATS_KEYS
    assert "splits_count" not in REQUIRED_STATS_KEYS  # Old field should be removed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])