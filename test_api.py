#!/usr/bin/env python3
"""
Simple test of the dashboard API without running the full server.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# Mock the Flask parts for testing
class MockApp:
    def get(self, route):
        def decorator(func):
            return func
        return decorator
    
app = MockApp()

# Import and patch the app
import dash_server
dash_server.app = app

# Test the API function directly
def test_api():
    print("Testing /api/stats endpoint...")
    
    # Call the API function directly
    result = dash_server.api_stats()
    
    print(f"API response: {result}")
    print(f"Sell trades count: {result.get('sell_trades_count')}")
    print(f"Actual splits count: {result.get('actual_splits_count')}")
    print(f"Legacy splits count: {result.get('splits_count')}")
    
    # Verify both fields exist
    assert 'sell_trades_count' in result
    assert 'actual_splits_count' in result
    assert 'splits_count' in result  # For backward compatibility
    
    print("API test completed successfully!")

if __name__ == "__main__":
    test_api()