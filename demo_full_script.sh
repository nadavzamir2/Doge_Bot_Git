#!/usr/bin/env bash
# DOGE Trading Bot - Demo Script
# 
# This script demonstrates the full running script functionality
# without requiring network access or actual trading setup.

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEMO_DIR="/tmp/doge_bot_demo"

# Colors
readonly GREEN='\033[0;32m'
readonly BLUE='\033[0;34m'
readonly YELLOW='\033[0;33m'
readonly NC='\033[0m'

log_info() {
    echo -e "${BLUE}[DEMO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_step() {
    echo -e "${YELLOW}[STEP]${NC} $1"
}

demo_setup() {
    log_step "Setting up demo environment..."
    
    # Create demo directory
    mkdir -p "$DEMO_DIR"
    cd "$DEMO_DIR"
    
    # Copy the main script
    cp "$SCRIPT_DIR/run_doge_bot.sh" "$DEMO_DIR/"
    cp "$SCRIPT_DIR/requirements.txt" "$DEMO_DIR/"
    
    # Create a demo .env file
    cat > "$DEMO_DIR/.env" << 'EOF'
BINANCE_REGION=com
BINANCE_API_KEY=demo_api_key_here
BINANCE_API_SECRET=demo_api_secret_here
PAIR=DOGE/USDT
GRID_MIN=0.215
GRID_MAX=0.25
GRID_STEP_PCT=0.8
BASE_ORDER_USD=5
MAX_USD_FOR_CYCLE=40
SPLIT_CHUNK_USD=4.0
SPLIT_RATIO=0.5
BNB_SYMBOL=BNB/USDT
EOF
    
    # Create dummy Python files
    cat > "$DEMO_DIR/main.py" << 'EOF'
#!/usr/bin/env python3
import time
import sys

print("DEMO: DOGE Trading Bot starting...")
print("DEMO: This is a demonstration version")
print("DEMO: Grid trading configured for DOGE/USDT")
print("DEMO: Bot would run continuously in real usage")

try:
    for i in range(10):
        print(f"DEMO: Trading loop iteration {i+1}/10")
        time.sleep(2)
except KeyboardInterrupt:
    print("DEMO: Bot stopped by user")
    sys.exit(0)

print("DEMO: Demo completed successfully")
EOF
    
    cat > "$DEMO_DIR/dash_server.py" << 'EOF'
#!/usr/bin/env python3
import time
import sys
import os

port = os.getenv('PORT', '8050')
print(f"DEMO: Dashboard server starting on port {port}")
print("DEMO: In real usage, this would provide a web interface")
print("DEMO: Dashboard would show trading statistics and controls")

try:
    for i in range(15):
        print(f"DEMO: Dashboard serving requests... {i+1}/15")
        time.sleep(1)
except KeyboardInterrupt:
    print("DEMO: Dashboard stopped by user")
    sys.exit(0)

print("DEMO: Dashboard demo completed")
EOF
    
    cat > "$DEMO_DIR/profit_watcher.py" << 'EOF'
#!/usr/bin/env python3
import time
import sys

print("DEMO: Profit watcher starting...")
print("DEMO: This monitors and splits trading profits")

try:
    for i in range(8):
        print(f"DEMO: Monitoring profits... {i+1}/8")
        time.sleep(1)
except KeyboardInterrupt:
    print("DEMO: Profit watcher stopped by user")
    sys.exit(0)

print("DEMO: Profit watcher demo completed")
EOF
    
    chmod +x "$DEMO_DIR/main.py" "$DEMO_DIR/dash_server.py" "$DEMO_DIR/profit_watcher.py"
    chmod +x "$DEMO_DIR/run_doge_bot.sh"
    
    log_success "Demo environment created in: $DEMO_DIR"
}

demo_show_features() {
    log_step "Demonstrating script features..."
    
    cd "$DEMO_DIR"
    
    echo ""
    log_info "1. Showing help:"
    echo "----------------------------------------"
    ./run_doge_bot.sh help | head -20
    
    echo ""
    log_info "2. Showing status (before setup):"
    echo "----------------------------------------"
    ./run_doge_bot.sh status
    
    echo ""
    log_info "3. Setting up environment (offline mode):"
    echo "----------------------------------------"
    # Create a basic venv without network operations
    python3 -m venv venv
    mkdir -p logs
    echo "DEMO: Environment setup completed (offline mode)"
    
    echo ""
    log_info "4. Showing status (after setup):"
    echo "----------------------------------------"
    ./run_doge_bot.sh status
    
    echo ""
    log_info "5. Running bot demo (5 seconds):"
    echo "----------------------------------------"
    timeout 5 ./run_doge_bot.sh bot || true
    
    echo ""
    log_info "6. Running dashboard demo (3 seconds):"
    echo "----------------------------------------"
    OPEN_DASHBOARD=0 timeout 3 ./run_doge_bot.sh dashboard || true
    
    echo ""
    log_info "7. Final status check:"
    echo "----------------------------------------"
    ./run_doge_bot.sh status
}

main() {
    echo "==========================================="
    echo "DOGE Trading Bot - Full Script Demo"
    echo "==========================================="
    echo ""
    
    demo_setup
    demo_show_features
    
    echo ""
    log_success "Demo completed successfully!"
    echo ""
    echo "The full script provides:"
    echo "• Complete environment setup (Python venv, dependencies)"
    echo "• Configuration validation"
    echo "• Multiple running modes (bot-only, dashboard-only, all components)"
    echo "• Process management and monitoring"
    echo "• Comprehensive logging"
    echo "• Status checking and log viewing"
    echo ""
    echo "Usage: ./run_doge_bot.sh [setup|bot|dashboard|both|all|status|logs|stop|help]"
    echo ""
    echo "Demo files created in: $DEMO_DIR"
}

main "$@"