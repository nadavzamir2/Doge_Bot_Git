#!/usr/bin/env bash
# DOGE Trading Bot - Complete Setup and Runner Script
# 
# This script provides a comprehensive solution for setting up and running
# the DOGE trading bot with all its components (main bot, dashboard, profit watcher).
# 
# Features:
# - Automatic environment setup (venv, dependencies)
# - Configuration validation
# - Multiple running modes (bot-only, dashboard-only, all components)
# - Process management and monitoring
# - Comprehensive logging
# - Error handling and recovery

set -euo pipefail

# =================== CONFIGURATION ===================
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
readonly APP_NAME="DOGE Trading Bot"
readonly VENV_DIR="$PROJECT_DIR/venv"
readonly LOG_DIR="$PROJECT_DIR/logs"
readonly ENV_FILE="$PROJECT_DIR/.env"
readonly STATE_FILE="$PROJECT_DIR/state.json"
readonly REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"

# Default configuration
readonly DEFAULT_PORT="${PORT:-8050}"
readonly OPEN_DASHBOARD="${OPEN_DASHBOARD:-1}"
readonly PROFIT_WATCHER_BACKFILL_DAYS="${PROFIT_WATCHER_BACKFILL_DAYS:-7}"

# Component files
readonly MAIN_BOT="main.py"
readonly DASHBOARD="dash_server.py"
readonly PROFIT_WATCHER="profit_watcher.py"

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[0;33m'
readonly BLUE='\033[0;34m'
readonly PURPLE='\033[0;35m'
readonly NC='\033[0m' # No Color

# =================== UTILITY FUNCTIONS ===================

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

log_step() {
    echo -e "${PURPLE}[STEP]${NC} $1"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if port is available
is_port_free() {
    local port="$1"
    ! lsof -i ":$port" >/dev/null 2>&1
}

# Find free port starting from given port
find_free_port() {
    local start_port="${1:-$DEFAULT_PORT}"
    local port="$start_port"
    
    for i in {0..50}; do
        if is_port_free "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
    done
    
    log_error "Could not find free port starting from $start_port"
    return 1
}

# Open URL in browser
open_url() {
    local url="$1"
    if [[ "$OPEN_DASHBOARD" == "1" ]]; then
        if command_exists open; then
            open "$url"
        elif command_exists xdg-open; then
            xdg-open "$url"
        elif command_exists firefox; then
            firefox "$url" >/dev/null 2>&1 &
        else
            log_info "Dashboard available at: $url"
        fi
    else
        log_info "Dashboard available at: $url"
    fi
}

# =================== VALIDATION FUNCTIONS ===================

# Check system requirements
check_system_requirements() {
    log_step "Checking system requirements..."
    
    local missing_commands=()
    
    # Check for Python 3
    if ! command_exists python3; then
        missing_commands+=("python3")
    fi
    
    # Check for pip
    if ! command_exists pip3 && ! python3 -m pip --version >/dev/null 2>&1; then
        missing_commands+=("pip3")
    fi
    
    # Check for git (optional but recommended)
    if ! command_exists git; then
        log_warning "Git not found - recommended for version control"
    fi
    
    if [[ ${#missing_commands[@]} -gt 0 ]]; then
        log_error "Missing required commands: ${missing_commands[*]}"
        log_error "Please install the missing dependencies and try again"
        exit 1
    fi
    
    log_success "System requirements check passed"
}

# Validate environment configuration
validate_environment() {
    log_step "Validating environment configuration..."
    
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error "Environment file not found: $ENV_FILE"
        log_info "Please create a .env file with your configuration"
        show_env_template
        exit 1
    fi
    
    # Source environment variables
    set -a
    source "$ENV_FILE"
    set +a
    
    # Required environment variables
    local required_vars=(
        "BINANCE_API_KEY"
        "BINANCE_API_SECRET"
        "BINANCE_REGION"
        "PAIR"
    )
    
    local missing_vars=()
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            missing_vars+=("$var")
        fi
    done
    
    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        log_error "Missing required environment variables: ${missing_vars[*]}"
        show_env_template
        exit 1
    fi
    
    # Validate specific values
    if [[ "$BINANCE_REGION" != "com" && "$BINANCE_REGION" != "us" ]]; then
        log_error "BINANCE_REGION must be 'com' or 'us', got: $BINANCE_REGION"
        exit 1
    fi
    
    log_success "Environment configuration validated"
}

# Show environment template
show_env_template() {
    cat << 'EOF'

Example .env file template:
=====================================
BINANCE_REGION=com
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
PAIR=DOGE/USDT
GRID_MIN=0.215
GRID_MAX=0.25
GRID_STEP_PCT=0.8
BASE_ORDER_USD=5
MAX_USD_FOR_CYCLE=40
SPLIT_CHUNK_USD=4.0
SPLIT_RATIO=0.5
BNB_SYMBOL=BNB/USDT
TELEGRAM_BOT_TOKEN=your_telegram_token (optional)
TELEGRAM_CHAT_ID=your_chat_id (optional)
=====================================

EOF
}

# =================== SETUP FUNCTIONS ===================

# Setup Python virtual environment
setup_virtual_environment() {
    log_step "Setting up Python virtual environment..."
    
    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
        log_success "Virtual environment created"
    else
        log_info "Virtual environment already exists"
    fi
    
    # Activate virtual environment
    source "$VENV_DIR/bin/activate"
    
    # Upgrade pip
    log_info "Upgrading pip..."
    python -m pip install --upgrade pip --quiet
    
    log_success "Virtual environment setup complete"
}

# Install Python dependencies
install_dependencies() {
    log_step "Installing Python dependencies..."
    
    if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
        log_warning "Requirements file not found: $REQUIREMENTS_FILE"
        log_info "Installing basic dependencies..."
        pip install ccxt flask python-dotenv waitress --quiet --timeout 60 || {
            log_warning "Failed to install dependencies, continuing with existing packages"
            return 0
        }
    else
        log_info "Installing from requirements.txt..."
        pip install -r "$REQUIREMENTS_FILE" --quiet --timeout 60 || {
            log_warning "Failed to install some dependencies, continuing with existing packages"
            return 0
        }
    fi
    
    log_success "Dependencies installation completed"
}

# Setup logging directory
setup_logging() {
    log_step "Setting up logging..."
    
    mkdir -p "$LOG_DIR"
    
    # Create today's log file if it doesn't exist
    local today=$(date +%Y%m%d)
    local bot_log="$LOG_DIR/bot_$today.log"
    local dashboard_log="$LOG_DIR/dashboard_$today.log"
    local profit_watcher_log="$LOG_DIR/profit_watcher_$today.log"
    
    touch "$bot_log" "$dashboard_log" "$profit_watcher_log"
    
    log_success "Logging setup complete"
    log_info "Log files location: $LOG_DIR"
}

# Complete environment setup
setup_environment() {
    log_info "Starting $APP_NAME environment setup..."
    
    cd "$PROJECT_DIR"
    
    check_system_requirements
    validate_environment
    setup_virtual_environment
    install_dependencies
    setup_logging
    
    log_success "Environment setup completed successfully!"
}

# =================== PROCESS MANAGEMENT ===================

# Stop existing processes
stop_existing_processes() {
    log_step "Stopping any existing bot processes..."
    
    # Kill processes by script name
    local processes=("$MAIN_BOT" "$DASHBOARD" "$PROFIT_WATCHER")
    
    for process in "${processes[@]}"; do
        if pgrep -f "$process" >/dev/null 2>&1; then
            log_info "Stopping $process..."
            pkill -f "$process" || true
            sleep 1
        fi
    done
    
    log_success "Existing processes stopped"
}

# =================== RUNNING FUNCTIONS ===================

# Run main trading bot
run_bot() {
    log_step "Starting main trading bot..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    local today=$(date +%Y%m%d)
    local log_file="$LOG_DIR/bot_$today.log"
    
    log_info "Bot logs: $log_file"
    
    # Run bot with logging
    python3 "$MAIN_BOT" 2>&1 | tee -a "$log_file"
}

# Run dashboard server
run_dashboard() {
    log_step "Starting dashboard server..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    local port
    port=$(find_free_port "$DEFAULT_PORT")
    if [[ "$port" == "0" ]]; then
        log_error "No free port found starting from $DEFAULT_PORT"
        exit 1
    fi
    
    export PORT="$port"
    export OPEN_DASHBOARD="$OPEN_DASHBOARD"
    
    local url="http://127.0.0.1:$port"
    local today=$(date +%Y%m%d)
    local log_file="$LOG_DIR/dashboard_$today.log"
    
    log_success "Dashboard starting on: $url"
    log_info "Dashboard logs: $log_file"
    
    # Open URL after short delay
    (sleep 3 && open_url "$url") &
    
    # Run dashboard with logging
    python3 "$DASHBOARD" 2>&1 | tee -a "$log_file"
}

# Run profit watcher
run_profit_watcher() {
    log_step "Starting profit watcher..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    local today=$(date +%Y%m%d)
    local log_file="$LOG_DIR/profit_watcher_$today.log"
    
    log_info "Profit watcher logs: $log_file"
    
    # Run with backfill if specified
    if [[ "$PROFIT_WATCHER_BACKFILL_DAYS" -gt 0 ]]; then
        log_info "Running with backfill: $PROFIT_WATCHER_BACKFILL_DAYS days"
        python3 "$PROFIT_WATCHER" --backfill --since-days "$PROFIT_WATCHER_BACKFILL_DAYS" 2>&1 | tee -a "$log_file"
    else
        python3 "$PROFIT_WATCHER" 2>&1 | tee -a "$log_file"
    fi
}

# Run bot and dashboard together
run_both() {
    log_step "Starting bot and dashboard together..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    # Start dashboard in background
    local port
    port=$(find_free_port "$DEFAULT_PORT")
    if [[ "$port" == "0" ]]; then
        log_error "No free port found starting from $DEFAULT_PORT"
        exit 1
    fi
    
    export PORT="$port"
    export OPEN_DASHBOARD="$OPEN_DASHBOARD"
    
    local url="http://127.0.0.1:$port"
    local today=$(date +%Y%m%d)
    local dashboard_log="$LOG_DIR/dashboard_$today.log"
    local bot_log="$LOG_DIR/bot_$today.log"
    
    log_success "Dashboard starting on: $url"
    log_info "Dashboard logs: $dashboard_log"
    log_info "Bot logs: $bot_log"
    
    # Start dashboard in background
    (python3 "$DASHBOARD" 2>&1 | tee -a "$dashboard_log") &
    local dashboard_pid=$!
    
    # Wait for dashboard to start
    sleep 3
    open_url "$url"
    
    # Setup signal handler to kill dashboard when bot stops
    trap "kill $dashboard_pid 2>/dev/null || true" EXIT
    
    # Run bot in foreground
    log_step "Starting main trading bot..."
    python3 "$MAIN_BOT" 2>&1 | tee -a "$bot_log"
}

# Run all components (bot, dashboard, profit watcher)
run_all() {
    log_step "Starting all components (bot, dashboard, profit watcher)..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    # Start dashboard
    local port
    port=$(find_free_port "$DEFAULT_PORT")
    if [[ "$port" == "0" ]]; then
        log_error "No free port found starting from $DEFAULT_PORT"
        exit 1
    fi
    
    export PORT="$port"
    export OPEN_DASHBOARD="$OPEN_DASHBOARD"
    
    local url="http://127.0.0.1:$port"
    local today=$(date +%Y%m%d)
    local dashboard_log="$LOG_DIR/dashboard_$today.log"
    local profit_watcher_log="$LOG_DIR/profit_watcher_$today.log"
    local bot_log="$LOG_DIR/bot_$today.log"
    
    log_success "Dashboard starting on: $url"
    log_info "Dashboard logs: $dashboard_log"
    log_info "Profit watcher logs: $profit_watcher_log"
    log_info "Bot logs: $bot_log"
    
    # Start dashboard in background
    (python3 "$DASHBOARD" 2>&1 | tee -a "$dashboard_log") &
    local dashboard_pid=$!
    
    # Start profit watcher in background
    if [[ "$PROFIT_WATCHER_BACKFILL_DAYS" -gt 0 ]]; then
        (python3 "$PROFIT_WATCHER" --backfill --since-days "$PROFIT_WATCHER_BACKFILL_DAYS" 2>&1 | tee -a "$profit_watcher_log") &
    else
        (python3 "$PROFIT_WATCHER" 2>&1 | tee -a "$profit_watcher_log") &
    fi
    local profit_watcher_pid=$!
    
    # Wait for services to start
    sleep 3
    open_url "$url"
    
    # Setup signal handler to kill background processes when bot stops
    trap "kill $dashboard_pid $profit_watcher_pid 2>/dev/null || true" EXIT
    
    # Run main bot in foreground
    log_step "Starting main trading bot..."
    python3 "$MAIN_BOT" 2>&1 | tee -a "$bot_log"
}

# =================== STATUS AND MONITORING ===================

# Show system status
show_status() {
    log_info "$APP_NAME - System Status"
    echo "================================="
    
    # Check if processes are running
    local processes=("$MAIN_BOT" "$DASHBOARD" "$PROFIT_WATCHER")
    for process in "${processes[@]}"; do
        if pgrep -f "$process" >/dev/null 2>&1; then
            echo -e "✅ $process: ${GREEN}RUNNING${NC}"
        else
            echo -e "❌ $process: ${RED}STOPPED${NC}"
        fi
    done
    
    echo ""
    echo "Configuration:"
    echo "- Project directory: $PROJECT_DIR"
    echo "- Environment file: $ENV_FILE"
    echo "- Log directory: $LOG_DIR"
    echo "- Virtual environment: $VENV_DIR"
    
    if [[ -f "$ENV_FILE" ]]; then
        source "$ENV_FILE" 2>/dev/null || true
        echo "- Trading pair: ${PAIR:-N/A}"
        echo "- Binance region: ${BINANCE_REGION:-N/A}"
        echo "- Grid range: ${GRID_MIN:-N/A} - ${GRID_MAX:-N/A}"
    fi
    
    echo ""
    echo "Recent log entries:"
    echo "==================="
    local today=$(date +%Y%m%d)
    local recent_log="$LOG_DIR/bot_$today.log"
    if [[ -f "$recent_log" ]]; then
        tail -n 10 "$recent_log"
    else
        echo "No recent log entries found"
    fi
}

# Show logs
show_logs() {
    local component="${1:-bot}"
    local today=$(date +%Y%m%d)
    local log_file="$LOG_DIR/${component}_$today.log"
    
    if [[ -f "$log_file" ]]; then
        log_info "Following $component logs: $log_file"
        tail -f "$log_file"
    else
        log_error "Log file not found: $log_file"
        log_info "Available log files:"
        ls -la "$LOG_DIR"/ 2>/dev/null || echo "No log files found"
    fi
}

# =================== MAIN SCRIPT LOGIC ===================

# Show usage information
show_usage() {
    cat << EOF
$APP_NAME - Complete Setup and Runner

USAGE: $0 [COMMAND] [OPTIONS]

COMMANDS:
  setup              - Setup environment (venv, dependencies, config validation)
  bot                - Run main trading bot only
  dashboard          - Run dashboard server only  
  profit-watcher     - Run profit watcher only
  both               - Run bot + dashboard together
  all                - Run all components (bot + dashboard + profit watcher)
  status             - Show system status and running processes
  logs [component]   - Follow logs (component: bot, dashboard, profit-watcher)
  stop               - Stop all running processes
  help               - Show this help message

ENVIRONMENT VARIABLES:
  PROJECT_DIR                    - Project directory (default: script directory)
  PORT                          - Dashboard port (default: 8050)
  OPEN_DASHBOARD                - Auto-open dashboard (default: 1)
  PROFIT_WATCHER_BACKFILL_DAYS  - Backfill days for profit watcher (default: 7)

EXAMPLES:
  $0 setup                      # First-time setup
  $0 all                        # Run everything
  $0 both                       # Run bot + dashboard
  $0 bot                        # Run bot only
  $0 status                     # Check what's running
  $0 logs dashboard             # Follow dashboard logs
  $0 stop                       # Stop everything

FIRST TIME SETUP:
1. Create .env file with your configuration
2. Run: $0 setup
3. Run: $0 all

EOF
}

# Main execution function
main() {
    local command="${1:-help}"
    
    case "$command" in
        "setup")
            setup_environment
            ;;
        "bot")
            setup_environment
            stop_existing_processes
            run_bot
            ;;
        "dashboard")
            setup_environment
            stop_existing_processes
            run_dashboard
            ;;
        "profit-watcher")
            setup_environment
            stop_existing_processes
            run_profit_watcher
            ;;
        "both")
            setup_environment
            stop_existing_processes
            run_both
            ;;
        "all")
            setup_environment
            stop_existing_processes
            run_all
            ;;
        "status")
            show_status
            ;;
        "logs")
            show_logs "${2:-bot}"
            ;;
        "stop")
            stop_existing_processes
            ;;
        "help"|"-h"|"--help")
            show_usage
            ;;
        *)
            log_error "Unknown command: $command"
            echo ""
            show_usage
            exit 1
            ;;
    esac
}

# Execute main function with all arguments
main "$@"