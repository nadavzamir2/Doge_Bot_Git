#!/usr/bin/env python3
"""
Configuration validation utility for DOGE Grid Trading Bot.

This script validates trading configuration and helps identify potential issues
before starting the bot.
"""

import os
import sys
from decimal import Decimal
from typing import List, Tuple, Optional


def validate_api_credentials() -> Tuple[bool, List[str]]:
    """Validate API credentials are configured."""
    errors = []
    
    # Check for API keys
    api_key = os.getenv("BINANCE_TRADE_KEY") or os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_TRADE_SECRET") or os.getenv("BINANCE_API_SECRET")
    
    if not api_key:
        errors.append("Missing API key (BINANCE_API_KEY or BINANCE_TRADE_KEY)")
    elif len(api_key) < 10:
        errors.append("API key appears to be too short")
    
    if not api_secret:
        errors.append("Missing API secret (BINANCE_API_SECRET or BINANCE_TRADE_SECRET)")
    elif len(api_secret) < 10:
        errors.append("API secret appears to be too short")
    
    return len(errors) == 0, errors


def validate_grid_parameters() -> Tuple[bool, List[str]]:
    """Validate grid trading parameters."""
    errors = []
    
    try:
        grid_low = Decimal(os.getenv("GRID_LOW", "0") or "0")
        grid_high = Decimal(os.getenv("GRID_HIGH", "0") or "0")
        step_pct = Decimal(os.getenv("STEP_PCT", "0") or "0")
        base_order = Decimal(os.getenv("BASE_ORDER_USD", "0") or "0")
        max_cycle = Decimal(os.getenv("MAX_CYCLE_USD", "0") or "0")
        
        # Grid bounds validation
        if grid_low <= 0:
            errors.append("GRID_LOW must be positive")
        
        if grid_high <= 0:
            errors.append("GRID_HIGH must be positive") 
        
        if grid_low >= grid_high:
            errors.append("GRID_LOW must be less than GRID_HIGH")
        
        # Step percentage validation
        if step_pct <= 0:
            errors.append("STEP_PCT must be positive")
        elif step_pct > 50:
            errors.append("STEP_PCT seems too high (>50%), consider using smaller steps")
        elif step_pct < 0.1:
            errors.append("STEP_PCT seems too small (<0.1%), may create too many orders")
        
        # Order size validation
        if base_order <= 0:
            errors.append("BASE_ORDER_USD must be positive")
        elif base_order < 5:
            errors.append("BASE_ORDER_USD may be too small (min order sizes on Binance)")
        
        if max_cycle <= 0:
            errors.append("MAX_CYCLE_USD must be positive")
        elif max_cycle < base_order:
            errors.append("MAX_CYCLE_USD should be larger than BASE_ORDER_USD")
        
        # Calculate estimated number of orders
        if grid_low > 0 and grid_high > 0 and step_pct > 0:
            price_range = grid_high - grid_low
            step_size = grid_low * (step_pct / 100)
            estimated_orders = int(price_range / step_size)
            
            if estimated_orders > 200:
                errors.append(f"Grid configuration may create {estimated_orders} orders (too many)")
            elif estimated_orders < 5:
                errors.append(f"Grid configuration may create only {estimated_orders} orders (too few)")
            
            # Estimate total investment
            avg_price = (grid_low + grid_high) / 2
            estimated_investment = estimated_orders * base_order / 2  # Approximate
            
            if estimated_investment > max_cycle:
                errors.append(f"Estimated investment (${estimated_investment:.2f}) exceeds MAX_CYCLE_USD")
        
    except (ValueError, TypeError) as e:
        errors.append(f"Error parsing grid parameters: {e}")
    
    return len(errors) == 0, errors


def validate_trading_mode() -> Tuple[bool, List[str]]:
    """Validate trading mode configuration."""
    errors = []
    
    mode = os.getenv("MODE", "").upper()
    region = os.getenv("BINANCE_REGION", "").lower()
    pair = os.getenv("PAIR", "")
    
    if mode not in ["LIVE", "PAPER"]:
        errors.append("MODE must be 'LIVE' or 'PAPER'")
    
    if region not in ["com", "us"]:
        errors.append("BINANCE_REGION must be 'com' or 'us'")
    
    if not pair:
        errors.append("PAIR must be specified (e.g., 'DOGE/USDT')")
    elif "/" not in pair:
        errors.append("PAIR must be in format 'BASE/QUOTE' (e.g., 'DOGE/USDT')")
    
    return len(errors) == 0, errors


def validate_optional_features() -> Tuple[bool, List[str]]:
    """Validate optional feature configuration."""
    warnings = []
    
    # Telegram notifications
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID")
    
    if telegram_token and not telegram_chat:
        warnings.append("TELEGRAM_BOT_TOKEN set but TELEGRAM_CHAT_ID missing")
    elif telegram_chat and not telegram_token:
        warnings.append("TELEGRAM_CHAT_ID set but TELEGRAM_BOT_TOKEN missing")
    
    # Data directory
    data_dir = os.getenv("DATA_DIR")
    if data_dir and not os.path.exists(data_dir):
        warnings.append(f"DATA_DIR specified but directory doesn't exist: {data_dir}")
    
    return True, warnings  # These are warnings, not errors


def check_environment_file() -> Optional[str]:
    """Check which environment file is being used."""
    env_locations = [
        os.path.expanduser("~/doge_bot/.env"),
        ".env"
    ]
    
    for env_file in env_locations:
        if os.path.exists(env_file):
            return env_file
    
    return None


def main():
    """Run configuration validation."""
    print("🔧 DOGE Grid Trading Bot - Configuration Validator")
    print("=" * 60)
    
    # Check environment file
    env_file = check_environment_file()
    if env_file:
        print(f"📁 Using environment file: {env_file}")
        from dotenv import load_dotenv
        load_dotenv(env_file)
    else:
        print("⚠️  No .env file found. Using system environment variables only.")
    
    print()
    
    # Run validations
    validations = [
        ("Trading Mode", validate_trading_mode),
        ("API Credentials", validate_api_credentials),
        ("Grid Parameters", validate_grid_parameters),
        ("Optional Features", validate_optional_features),
    ]
    
    all_valid = True
    total_errors = 0
    total_warnings = 0
    
    for name, validator in validations:
        print(f"🔍 Validating {name}...")
        
        try:
            is_valid, issues = validator()
            
            if name == "Optional Features":
                # These are warnings
                for warning in issues:
                    print(f"  ⚠️  {warning}")
                    total_warnings += 1
                if not issues:
                    print("  ✅ No issues found")
            else:
                # These are errors
                if is_valid:
                    print("  ✅ Valid")
                else:
                    all_valid = False
                    for error in issues:
                        print(f"  ❌ {error}")
                        total_errors += 1
        
        except Exception as e:
            print(f"  ❌ Validation failed: {e}")
            all_valid = False
            total_errors += 1
    
    print("\n" + "=" * 60)
    print("📊 VALIDATION SUMMARY:")
    
    if all_valid:
        print("✅ Configuration is valid!")
        if total_warnings > 0:
            print(f"⚠️  {total_warnings} warnings (non-critical)")
    else:
        print(f"❌ Configuration has {total_errors} errors that must be fixed")
        if total_warnings > 0:
            print(f"⚠️  Plus {total_warnings} warnings")
    
    if all_valid:
        print("\n🚀 Configuration looks good! You can start the bot with confidence.")
        print("\n💡 Pro tips:")
        print("   • Start with PAPER mode to test your configuration")
        print("   • Monitor the first few trades closely")
        print("   • Keep an eye on your account balance")
        print("   • Use the web dashboard to monitor progress")
        return 0
    else:
        print("\n🛠️  Please fix the configuration errors above before starting the bot.")
        print("   💡 Check the .env.example file for reference")
        return 1


if __name__ == "__main__":
    sys.exit(main())