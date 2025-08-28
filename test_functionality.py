#!/usr/bin/env python3
"""
Simple test script to verify the stats functionality works.
"""

import utils_stats
import profit_split

def test_functionality():
    print("Testing utils_stats functionality...")
    
    # Test reading/writing stats
    stats = utils_stats.read_stats()
    print(f"Initial stats: {stats}")
    
    # Test adding sell trades
    utils_stats.add_realized_profit(5.0, inc_sell_trades=2)
    stats = utils_stats.read_stats()
    print(f"After adding sell trades: sell_trades_count={stats['sell_trades_count']}, actual_splits_count={stats['actual_splits_count']}")
    
    # Test adding actual splits
    utils_stats.add_actual_splits(1)
    stats = utils_stats.read_stats()
    print(f"After adding actual splits: sell_trades_count={stats['sell_trades_count']}, actual_splits_count={stats['actual_splits_count']}")
    
    print("\nTesting profit_split functionality...")
    
    # Test split state
    state = profit_split.get_current_state()
    print(f"Split state: accumulator={state.get('split_accumulator_usd', 0)}")
    
    print("\nAll tests completed successfully!")

if __name__ == "__main__":
    test_functionality()