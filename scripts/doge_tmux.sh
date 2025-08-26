#!/usr/bin/env bash
# DOGE Trading Bot - Tmux Session Manager
# 
# This script manages a tmux session for running the DOGE trading bot.
# It provides functionality to start, stop, restart, attach to, and monitor
# the bot process with comprehensive logging support.

set -euo pipefail

# Configuration
readonly SESSION_NAME="doge"
readonly PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LOG_DIR="$PROJECT_DIR/logs"
readonly LOG_FILE="$LOG_DIR/run_$(date +%F).log"

# Utility functions
check_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "❌ Missing required command: '$1'"
        return 1
    }
}

ensure_tmux_available() {
    if ! check_command tmux; then
        echo "❌ tmux is not installed."
        echo "Install on macOS with Homebrew: brew install tmux"
        echo "Install on Ubuntu/Debian: sudo apt-get install tmux"
        exit 1
    fi
}

ensure_environment() {
    # Check for virtual environment
    if [[ ! -f "$PROJECT_DIR/venv/bin/activate" ]]; then
        echo "❌ Virtual environment not found. Create one with:"
        echo "  python3 -m venv venv"
        exit 1
    fi
    
    # Check for main bot script
    if [[ ! -f "$PROJECT_DIR/main.py" ]]; then
        echo "❌ main.py not found in $PROJECT_DIR"
        exit 1
    fi
    
    # Ensure log directory exists
    mkdir -p "$LOG_DIR"
}

# Command functions
start_bot() {
    ensure_tmux_available
    ensure_environment
    
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "ℹ️  Session '$SESSION_NAME' already exists. Use: $0 attach"
        exit 0
    fi

    echo "▶️  Starting tmux session '$SESSION_NAME' and running bot..."
    
    # Create tmux session and run bot with logging
    tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_DIR" \
        "bash -lc 'source venv/bin/activate && mkdir -p \"$LOG_DIR\" && echo \"---- \$(date) Bot starting ----\" >> \"$LOG_FILE\" && exec python3 main.py >> \"$LOG_FILE\" 2>&1'"

    echo "✅ Bot started successfully!"
    echo "View logs: tail -f \"$LOG_FILE\""
    echo "Attach to session: $0 attach"
}

stop_bot() {
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "⏹️  Stopping tmux session '$SESSION_NAME'..."
        tmux kill-session -t "$SESSION_NAME"
        echo "✅ Session stopped."
    else
        echo "ℹ️  No session '$SESSION_NAME' found. Attempting to stop process (pkill)..."
        if pkill -f "python3 main.py" 2>/dev/null; then
            echo "✅ Python process stopped."
        else
            echo "ℹ️ No active process found."
        fi
    fi
}

restart_bot() {
    stop_bot
    sleep 1
    start_bot
}

attach_to_session() {
    ensure_tmux_available
    
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        tmux attach -t "$SESSION_NAME"
    else
        echo "ℹ️  No session '$SESSION_NAME' found. Start with: $0 start"
    fi
}

show_logs() {
    mkdir -p "$LOG_DIR"
    echo "📜 Following log file: $LOG_FILE"
    
    if [[ -f "$LOG_FILE" ]]; then
        tail -n 100 -f "$LOG_FILE"
    else
        echo "❌ Log file not found. Bot may not be running."
    fi
}

show_status() {
    echo "=== DOGE Bot Status ==="
    
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "✅ tmux session '$SESSION_NAME' is RUNNING"
    else
        echo "❌ tmux session '$SESSION_NAME' is NOT running"
    fi
    
    echo ""
    echo "Recent log entries:"
    if [[ -f "$LOG_FILE" ]]; then
        tail -n 30 "$LOG_FILE"
    else
        echo "(No log file found yet)"
    fi
}

show_usage() {
    cat <<USAGE
DOGE Trading Bot - Tmux Manager

Usage: $0 {start|stop|restart|attach|logs|status}

Commands:
  start    - Start tmux session and run bot with logging to: $LOG_FILE
  stop     - Stop the session (or process if no session exists)
  restart  - Stop and start the bot
  attach   - Attach to tmux session (for real-time monitoring)
  logs     - Follow today's log file
  status   - Show running status and recent log entries

Examples:
  $0 start          # Start the bot
  $0 attach         # Monitor the bot in real-time
  $0 logs           # Watch log output
  $0 stop           # Stop the bot

USAGE
}

# Main command dispatch
main() {
    local command="${1:-status}"
    
    case "$command" in
        start)   start_bot        ;;
        stop)    stop_bot         ;;
        restart) restart_bot      ;;
        attach)  attach_to_session ;;
        logs)    show_logs        ;;
        status)  show_status      ;;
        help|-h|--help) show_usage ;;
        *) 
            echo "❌ Unknown command: $command"
            show_usage
            exit 1
            ;;
    esac
}

# Execute main function with all arguments
main "$@"
