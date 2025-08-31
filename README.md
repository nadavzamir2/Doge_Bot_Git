# DOGE Grid Trading Bot

A sophisticated automated grid trading bot for DOGE/USDT on Binance, featuring web-based monitoring, profit tracking, and comprehensive state management.

## Features

🚀 **Core Trading**
- Grid trading strategy with configurable price ranges and step percentages
- Support for both Binance.com and Binance.us
- Live and paper trading modes
- Automatic order placement and fill processing
- State persistence and recovery

📊 **Monitoring & Analytics**
- Real-time web dashboard with order tracking
- Profit/loss calculations and statistics
- Trading history visualization
- Performance metrics and reporting
- Order management interface

⚡ **Advanced Features**
- Configurable profit splitting and reinvestment
- Automatic grid rebalancing tools
- Telegram notifications support
- Comprehensive testing suite
- Modular architecture for easy extension

## Quick Start

### Prerequisites

- Python 3.12+
- Binance account with API access
- DOGE/USDT trading pair

### Installation

1. Clone the repository:
```bash
git clone https://github.com/nadavzamir2/Doge_Bot_Git.git
cd Doge_Bot_Git
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

### Configuration

Create a `.env` file in your home directory at `~/doge_bot/.env` or in the project root:

```bash
# Trading Configuration
MODE=LIVE                    # LIVE or PAPER
BINANCE_REGION=com          # com or us
PAIR=DOGE/USDT

# API Keys
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
# Or use separate trade/read keys:
# BINANCE_TRADE_KEY=your_trade_key
# BINANCE_TRADE_SECRET=your_trade_secret

# Grid Parameters
GRID_LOW=0.13               # Lower price bound
GRID_HIGH=0.32              # Upper price bound
STEP_PCT=1.0                # Step percentage between orders
BASE_ORDER_USD=5.0          # Base order size in USD
MAX_CYCLE_USD=1000.0        # Maximum cycle investment

# Optional
BINANCE_RECVWINDOW=10000
POLL_SECONDS=30
```

## Usage

### Running the Bot

**Main Trading Bot:**
```bash
python main.py
```

**Web Dashboard:**
```bash
python dash_server.py
# Access at http://localhost:8899
```

**Grid Management:**
```bash
# Preview new grid setup
python regrid.py --min 0.13 --max 0.32 --step 1.0

# Apply new grid (cancels existing orders)
python regrid.py --min 0.13 --max 0.32 --step 1.0 --apply

# Cancel all orders only
python regrid.py --cancel-only
```

### Scripts

The `scripts/` directory contains utility scripts:

- `run_bot.sh` - Start the trading bot
- `run_bot_daemon.sh` - Run bot as daemon
- `restart_doge_bot.sh` - Restart bot service
- `doge_tmux.sh` - Run in tmux session

## Architecture

```
├── main.py                 # Main bot orchestrator (refactored)
├── config.py              # Centralized configuration
├── dash_server.py         # Web dashboard server
├── regrid.py              # Grid management utility
│
├── dogebot/               # Core trading modules
│   ├── exchange.py        # Exchange client management
│   ├── state.py           # State persistence
│   ├── orders.py          # Order placement and processing
│   ├── grid.py            # Grid calculation logic
│   ├── stats.py           # Statistics and reporting
│   └── local_store.py     # Local data storage
│
├── scripts/               # Utility scripts
├── data/                  # Runtime data directory
└── logs/                  # Log files
```

## Testing

Run the test suite:

```bash
# Run all tests
python -m pytest

# Run specific test modules
python -m pytest test_dashboard_data.py
python -m pytest test_functionality.py

# Run with verbose output
python -m pytest -v
```

## API Reference

### Web Dashboard Endpoints

- `GET /` - Main dashboard interface
- `GET /api/stats` - Trading statistics
- `GET /api/open_orders` - Current open orders
- `GET /api/order_history` - Historical orders
- `POST /api/stop_bot` - Stop bot trading
- `POST /api/resume_bot` - Resume bot trading
- `POST /api/cancel_all_orders` - Cancel all open orders

### Configuration Functions

```python
from config import (
    MODE, TRADING_PAIR, API_KEY, API_SECRET,
    GRID_LOW_PRICE, GRID_HIGH_PRICE, GRID_STEP_PERCENT,
    get_config_summary, validate_required_config
)
```

### Trading Functions

```python
from dogebot.exchange import create_client
from dogebot.state import load_state, save_state
from dogebot.orders import bootstrap_buys, process_fills
from dogebot.grid import compute_grid_levels
from dogebot.stats import build_stats, write_stats
```

## Security Considerations

⚠️ **Important Security Notes:**

1. **API Keys**: Store API keys securely, never commit them to version control
2. **Permissions**: Use trading keys with minimal required permissions
3. **Network**: Consider running on secure, dedicated infrastructure
4. **Monitoring**: Regularly monitor bot activity and performance
5. **Limits**: Set appropriate position and loss limits

## Troubleshooting

### Common Issues

**Connection Errors:**
- Verify API keys and permissions
- Check internet connectivity
- Confirm Binance region setting (com/us)

**Order Placement Failures:**
- Check account balance and trading limits
- Verify pair spelling and availability
- Review minimum order size requirements

**State Recovery:**
- Bot automatically recovers from `state.json`
- Check `data/` directory for state files
- Use dashboard to monitor recovery progress

### Logging

Logs are written to:
- Console output (INFO level)
- `logs/` directory (if configured)
- Dashboard displays recent activity

### Support

For issues and questions:
1. Check the troubleshooting section above
2. Review test outputs for configuration issues
3. Examine log files for detailed error information
4. Open an issue on GitHub with relevant details

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

This project is for educational and research purposes. Use at your own risk.
Trading cryptocurrencies involves substantial risk of loss and may not be suitable for all investors.

## Disclaimer

This software is provided "as is" without warranty. The authors are not responsible for any financial losses incurred through the use of this software. Always test thoroughly with paper trading before using real funds.