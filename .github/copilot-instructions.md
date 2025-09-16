# DOGE Grid Trading Bot - AI Development Guide

## Architecture Overview

This is a **grid trading bot** for DOGE/USDT with a modular, clean architecture:

- `main.py` - Main orchestrator with signal handling and trading loop
- `dogebot/` - Core trading modules (exchange, grid, orders, state, stats)
- `config.py` - Centralized configuration from environment variables
- `dash_server.py` - Real-time monitoring dashboard (3800+ lines, feature-rich)
- `data/` - Persistent state files (JSON format)
- `scripts/` - Operational tools and daemon management

## Key Design Patterns

### State Management
- **Single source of truth**: `~/doge_bot/state.json` for trading state
- **Atomic writes**: Use `.tmp` files + `os.replace()` for safe persistence
- **File locking**: `utils_stats.py` uses fcntl for inter-process coordination
- **Default fallbacks**: Always merge with `default_state()` on load

```python
# Standard state loading pattern
state = load_state()  # Always returns valid dict with defaults
save_state(state)     # Atomic write with temp file
```

### Configuration Architecture
- **Environment-driven**: All config via `~/doge_bot/.env` file
- **Decimal precision**: Uses `Decimal` for all financial calculations
- **Mode switching**: `MODE=LIVE|PAPER` for testing vs production
- **Regional support**: `BINANCE_REGION=com|us` for different exchanges

### Grid Trading Logic
- **Price levels**: Computed from `GRID_LOW`, `GRID_HIGH`, `STEP_PCT`
- **Order management**: Tracks `processed_buys`, `child_sells` in state
- **Fill processing**: Core loop in `process_fills()` handles buy/sell execution
- **Profit splitting**: Automated conversion to BNB when thresholds hit

## Critical Developer Workflows

### Running the Bot
```bash
# Development setup
scripts/run_bot.sh                    # Starts bot + dashboard
scripts/doge_tmux.sh start           # Production tmux session
scripts/doge_tmux.sh attach          # Attach to running session
```

### Testing Patterns
- **Functional tests**: `test_*.py` files verify core components
- **Paper trading**: Set `MODE=PAPER` for risk-free testing
- **Local data**: Use `FORCE_LOCAL_DATA=1` for dashboard without live API

### Debugging Tools
```bash
python test_functionality.py         # Verify stats/profit systems
python diagnose_keys.py              # Check API authentication
python scripts/pnl_audit.py          # Audit profit calculations
```

## Integration Points

### Binance Exchange
- **CCXT library**: Unified exchange interface with regional switching
- **Rate limiting**: Built-in with `enableRateLimit: true`
- **Error handling**: Graceful fallbacks for API failures
- **Paper mode**: Complete simulation without real orders

### Data Persistence
- **State files**: `state.json`, `runtime_stats.json`, `split_state.json`
- **Order history**: Local JSON storage for dashboard fallback
- **Price history**: Persistent chart data in `price_history.json`
- **Logs**: Structured logging to `logs/` with daily rotation

### Dashboard Features
- **Real-time updates**: SSE (Server-Sent Events) for live price/stats
- **Grid visualization**: Dynamic chart with buy/sell levels
- **Order management**: View/filter open orders and history
- **Profit tracking**: Multiple profit metrics (realized/unrealized/fees)

## Project Conventions

### File Organization
- `dogebot/` - Pure business logic, no I/O in core functions
- `scripts/` - Operational tools, deployment helpers
- `test_*.py` - Unit/integration tests (run directly with Python)
- `*_utils.py` - Shared utilities (trading, stats, etc.)

### Error Handling
- **Graceful degradation**: Dashboard works without live API data
- **Logging over exceptions**: Log errors, continue operation when possible
- **Signal handling**: Proper SIGTERM/SIGINT for graceful shutdown

### Code Style
- **Type hints**: Extensive use of typing annotations
- **Decimal arithmetic**: Financial calculations use `Decimal`, not `float`
- **Environment configuration**: No hardcoded values, all via config
- **Modular imports**: Clean separation between modules

## Common Gotchas

1. **State corruption**: Always use atomic writes for JSON state files
2. **Precision loss**: Use `Decimal` for prices, `float` only for display
3. **API keys**: Separate TRADE/READ keys supported, check both env vars
4. **Regional differences**: US vs international Binance have different APIs
5. **File locking**: Multiple processes can conflict on stats files

## Development Tips

- **State inspection**: Check `~/doge_bot/data/` for current bot state
- **Config debugging**: Run `python -c "from config import *; print(locals())"` 
- **Dashboard development**: Use `FORCE_LOCAL_DATA=1` to avoid API limits
- **Testing changes**: Always test in `MODE=PAPER` first
- **Log monitoring**: `tail -f logs/run_$(date +%F).log` for real-time debugging