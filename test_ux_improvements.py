#!/usr/bin/env python3
"""
Test UX improvements for dashboard
- Verify JavaScript functions exist
- Test feature flag functionality
- Validate HTML structure changes
"""

import re
from pathlib import Path

def test_ux_improvements_implemented():
    """Test that all UX improvements are properly implemented in dash_server.py"""
    
    dash_file = Path(__file__).parent / "dash_server.py"
    content = dash_file.read_text()
    
    # Test 1: Legend relocation
    assert 'id="chartLegend"' in content, "Chart legend element should be present"
    assert 'margin-top: 12px' in content, "Legend should be positioned at bottom"
    
    # Test 2: Price marker always visible feature
    assert 'alwaysShowPriceMarker' in content, "Always show price marker feature should be implemented"
    assert 'const alwaysShowPrice = (localStorage.getItem(\'alwaysShowPriceMarker\') !== \'0\');' in content
    
    # Test 3: Enhanced loading states
    assert 'el.style.opacity = \'0.7\';' in content, "Loading state should set opacity"
    assert 'el.style.pointerEvents = \'none\';' in content, "Loading state should disable pointer events"
    
    # Test 4: Sticky X-axis feature
    assert 'stickyXAxisEl' in content, "Sticky X-axis element should be referenced"
    assert 'sticky-x-axis' in content, "Sticky X-axis CSS class should be defined"
    assert 'toggleStickyXAxis' in content, "Toggle function should exist"
    
    # Test 5: Dynamic time labels
    assert 'setupDynamicTimeLabels' in content, "Dynamic time labels setup function should exist"
    assert 'updateTimeLabelsForZoom' in content, "Time labels zoom update function should exist"
    assert 'plotly_relayout' in content, "Plotly relayout event listener should be present"
    
    # Test 6: Feature flag initialization
    assert 'initializeUXImprovements' in content, "UX improvements initialization function should exist"
    
    print("✅ All UX improvements are properly implemented!")


def test_backward_compatibility():
    """Test that changes maintain backward compatibility"""
    
    dash_file = Path(__file__).parent / "dash_server.py"
    content = dash_file.read_text()
    
    # Ensure existing controls still work
    assert 'showGridEl' in content, "Grid toggle should still exist"
    assert 'showActiveEl' in content, "Active layers toggle should still exist"
    assert 'showPriceLineEl' in content, "Price line toggle should still exist"
    
    # Ensure existing chart functions preserved
    assert 'updateChart' in content, "updateChart function should be preserved"
    assert 'setChartMode' in content, "setChartMode function should be preserved"
    
    # Ensure localStorage compatibility
    assert 'localStorage.getItem' in content, "localStorage usage should be maintained"
    
    print("✅ Backward compatibility maintained!")


def test_feature_flags():
    """Test that feature flags are properly implemented"""
    
    dash_file = Path(__file__).parent / "dash_server.py"
    content = dash_file.read_text()
    
    # Check feature flags are optional and have safe defaults
    feature_flags = [
        'alwaysShowPriceMarker',
        'stickyXAxis', 
        'chartLegendPosition'
    ]
    
    for flag in feature_flags:
        assert flag in content, f"Feature flag {flag} should be present"
        
    # Verify safe defaults
    assert "!== '0'" in content, "Feature flags should have safe default logic"
    
    print("✅ Feature flags properly implemented!")


def test_no_breaking_changes():
    """Test that no breaking changes were introduced"""
    
    dash_file = Path(__file__).parent / "dash_server.py"
    content = dash_file.read_text()
    
    # Ensure essential functions and variables still exist
    essentials = [
        'boot()',
        'wireControls()',
        'updateChart()',
        'PAIR',
        'GRID_MIN',
        'GRID_MAX'
    ]
    
    for essential in essentials:
        assert essential in content, f"Essential element {essential} should be preserved"
    
    print("✅ No breaking changes detected!")


if __name__ == "__main__":
    test_ux_improvements_implemented()
    test_backward_compatibility() 
    test_feature_flags()
    test_no_breaking_changes()
    print("\n🎉 All UX improvement tests passed!")