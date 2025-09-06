#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Hebrew greeting functionality.
"""

import re
from dash_server import HTML


def test_dashboard_has_hebrew_greeting():
    """Test that the dashboard HTML includes the Hebrew greeting."""
    # Check that the Hebrew greeting is present in the HTML template
    hebrew_text = "היי! ברוכים הבאים לבוט הדוגקוין"
    english_text = "Hi! Welcome to the DOGE Bot"
    
    assert hebrew_text in HTML, "Hebrew greeting should be present in dashboard HTML"
    assert english_text in HTML, "English greeting should be present in dashboard HTML"
    
    # Check that the greeting has proper styling
    assert "background: linear-gradient" in HTML, "Greeting should have gradient background styling"


def test_main_log_has_hebrew_greeting():
    """Test that the main log function includes Hebrew greeting."""
    # Import here to avoid config loading issues in tests
    import logging
    from io import StringIO
    
    # Set up string capture for log output
    log_capture = StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.INFO)
    
    # Get the logger and add our handler
    logger = logging.getLogger("doge_grid_bot")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    try:
        from main import log_startup_info
        log_startup_info()
        
        log_output = log_capture.getvalue()
        
        # Check that Hebrew greeting is in the logs
        assert "היי! ברוכים הבאים לבוט הדוגקוין" in log_output, "Hebrew greeting should be in startup logs"
        assert "Hi! Welcome to the DOGE Bot" in log_output, "English greeting should be in startup logs"
        
    finally:
        logger.removeHandler(handler)


if __name__ == "__main__":
    test_dashboard_has_hebrew_greeting()
    test_main_log_has_hebrew_greeting()
    print("✅ All Hebrew greeting tests passed!")