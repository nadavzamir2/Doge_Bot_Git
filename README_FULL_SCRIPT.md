# DOGE Trading Bot - Complete Running Script

This repository contains a comprehensive DOGE trading bot with a complete setup and running script that handles all aspects of deployment and operation.

## Quick Start

1. **Clone and setup:**
   ```bash
   git clone <repository-url>
   cd Doge_Bot_Git
   chmod +x run_doge_bot.sh
   ```

2. **Configure your environment:**
   Create a `.env` file with your configuration:
   ```bash
   cp .env.example .env  # Edit with your settings
   ```

3. **Setup and run:**
   ```bash
   ./run_doge_bot.sh setup    # First-time setup
   ./run_doge_bot.sh all      # Run everything
   ```

## Features

### 🚀 Complete Environment Setup
- Automatic Python virtual environment creation
- Dependency installation from requirements.txt
- Configuration validation
- Logging directory setup

### 🎛️ Multiple Running Modes
- **Bot only**: `./run_doge_bot.sh bot`
- **Dashboard only**: `./run_doge_bot.sh dashboard`
- **Profit watcher only**: `./run_doge_bot.sh profit-watcher`
- **Bot + Dashboard**: `./run_doge_bot.sh both`
- **All components**: `./run_doge_bot.sh all`

### 📊 Monitoring & Management
- Process status monitoring
- Real-time log viewing
- Graceful process stopping
- Comprehensive error handling

### 🔧 Configuration Management
- Environment variable validation
- Automatic port finding for dashboard
- Browser auto-opening for dashboard
- Configurable profit watcher backfill

## Script Commands

```bash
./run_doge_bot.sh [COMMAND] [OPTIONS]
```

### Commands

| Command | Description |
|---------|-------------|
| `setup` | Setup environment (venv, dependencies, config validation) |
| `bot` | Run main trading bot only |
| `dashboard` | Run dashboard server only |
| `profit-watcher` | Run profit watcher only |
| `both` | Run bot + dashboard together |
| `all` | Run all components (bot + dashboard + profit watcher) |
| `status` | Show system status and running processes |
| `logs [component]` | Follow logs (component: bot, dashboard, profit-watcher) |
| `stop` | Stop all running processes |
| `help` | Show help message |

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PROJECT_DIR` | Project directory | script directory |
| `PORT` | Dashboard port | 8050 |
| `OPEN_DASHBOARD` | Auto-open dashboard | 1 |
| `PROFIT_WATCHER_BACKFILL_DAYS` | Backfill days for profit watcher | 7 |

## Configuration (.env file)

Required environment variables:

```bash
# Binance Configuration
BINANCE_REGION=com                           # 'com' or 'us'
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Trading Configuration
PAIR=DOGE/USDT                              # Trading pair
GRID_MIN=0.215                              # Grid minimum price
GRID_MAX=0.25                               # Grid maximum price
GRID_STEP_PCT=0.8                           # Grid step percentage
BASE_ORDER_USD=5                            # Base order size in USD
MAX_USD_FOR_CYCLE=40                        # Maximum USD per cycle

# Profit Management
SPLIT_CHUNK_USD=4.0                         # Profit split chunk size
SPLIT_RATIO=0.5                             # BNB conversion ratio (0.5 = 50%)
BNB_SYMBOL=BNB/USDT                         # BNB trading pair

# Optional: Telegram Notifications
TELEGRAM_BOT_TOKEN=your_telegram_token       # Optional
TELEGRAM_CHAT_ID=your_chat_id               # Optional

# Optional: Advanced Settings
BINANCE_RECVWINDOW=10000                    # API receive window
POLL_SECONDS=7                              # Trading loop interval
MODE=LIVE                                   # LIVE or PAPER trading
```

## Components

### 1. Main Trading Bot (`main.py`)
- Grid trading strategy implementation
- Automatic buy/sell order management
- State persistence and recovery
- Signal handling for graceful shutdown

### 2. Dashboard (`dash_server.py`)
- Web-based monitoring interface
- Real-time trading statistics
- Grid visualization
- Manual controls (stop/resume/cancel orders)

### 3. Profit Watcher (`profit_watcher.py`)
- Automatic profit monitoring
- BNB conversion management
- Profit splitting and reinvestment
- Historical profit tracking

## Usage Examples

### First-time Setup
```bash
# 1. Setup environment
./run_doge_bot.sh setup

# 2. Check status
./run_doge_bot.sh status

# 3. Run everything
./run_doge_bot.sh all
```

### Development and Testing
```bash
# Run only the dashboard for testing
./run_doge_bot.sh dashboard

# Run bot without dashboard
./run_doge_bot.sh bot

# View live logs
./run_doge_bot.sh logs bot
./run_doge_bot.sh logs dashboard
```

### Production Deployment
```bash
# Run all components together
./run_doge_bot.sh all

# Monitor in another terminal
./run_doge_bot.sh status
./run_doge_bot.sh logs bot
```

### Maintenance
```bash
# Stop all processes
./run_doge_bot.sh stop

# Check status
./run_doge_bot.sh status

# Restart with new configuration
./run_doge_bot.sh all
```

## Logging

The script automatically creates comprehensive logs in the `logs/` directory:

- `bot_YYYYMMDD.log` - Main trading bot logs
- `dashboard_YYYYMMDD.log` - Dashboard server logs  
- `profit_watcher_YYYYMMDD.log` - Profit watcher logs

## Process Management

The script includes intelligent process management:

- **Automatic cleanup**: Stops existing processes before starting new ones
- **Signal handling**: Graceful shutdown on SIGINT/SIGTERM
- **Process monitoring**: Shows running status of all components
- **Port management**: Automatically finds free ports for dashboard

## Error Handling

- Configuration validation before startup
- Dependency installation with timeout handling
- Network error resilience
- Graceful degradation for missing components

## Security Notes

- Never commit your `.env` file with real API keys
- Use paper trading mode for testing (`MODE=PAPER`)
- Monitor your bot regularly
- Set appropriate position limits

## Troubleshooting

### Common Issues

1. **"Missing required environment variables"**
   - Check your `.env` file configuration
   - Ensure all required variables are set

2. **"No free port found"**
   - Change the default port: `PORT=8051 ./run_doge_bot.sh dashboard`
   - Check for other running services

3. **"Dependencies installation failed"**
   - Check internet connection
   - Run setup again: `./run_doge_bot.sh setup`

4. **Bot not starting**
   - Check logs: `./run_doge_bot.sh logs bot`
   - Verify API keys and permissions
   - Check trading pair availability

### Debug Mode

For detailed debugging, you can run components manually:

```bash
source venv/bin/activate
python3 main.py          # Direct bot execution
python3 dash_server.py   # Direct dashboard execution
```

## Contributing

When making changes to the bot:

1. Test with paper trading first (`MODE=PAPER`)
2. Use the demo script to validate functionality
3. Update documentation for any new features
4. Test all running modes before committing

## License

[Your license information here]

---

**⚠️ Risk Warning**: Cryptocurrency trading carries significant risk. Only trade with funds you can afford to lose. This bot is provided as-is without warranties. Always test thoroughly before using with real funds.