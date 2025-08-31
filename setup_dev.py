#!/usr/bin/env python3
"""
Development setup and validation script for DOGE Grid Trading Bot.

This script helps developers set up the environment and validate configuration.
"""

import os
import sys
import subprocess
from pathlib import Path


def check_python_version():
    """Check if Python version is compatible."""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ required. Current version:", sys.version)
        return False
    print(f"✅ Python version: {sys.version}")
    return True


def check_dependencies():
    """Check if all required dependencies are installed."""
    try:
        import ccxt
        import flask
        import pytest
        from dotenv import load_dotenv
        print("✅ All required dependencies are installed")
        return True
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Run: pip install -r requirements.txt")
        return False


def validate_project_structure():
    """Validate that all required files and directories exist."""
    required_files = [
        "main.py",
        "config.py", 
        "dash_server.py",
        "requirements.txt",
        "dogebot/__init__.py",
    ]
    
    required_dirs = [
        "dogebot",
        "scripts",
    ]
    
    all_good = True
    
    for file_path in required_files:
        if not Path(file_path).exists():
            print(f"❌ Missing required file: {file_path}")
            all_good = False
    
    for dir_path in required_dirs:
        if not Path(dir_path).is_dir():
            print(f"❌ Missing required directory: {dir_path}")
            all_good = False
    
    if all_good:
        print("✅ Project structure is valid")
    
    return all_good


def check_environment_setup():
    """Check if environment is properly configured."""
    env_files = [
        Path.home() / "doge_bot" / ".env",
        Path(".env"),
        Path(".env.example")
    ]
    
    has_env = any(env_file.exists() for env_file in env_files[:-1])
    has_example = env_files[-1].exists()
    
    if has_example:
        print("✅ Environment example file exists")
    else:
        print("❌ No .env.example file found")
    
    if has_env:
        print("✅ Environment configuration file found")
        return True
    else:
        print("❌ No .env file found. Copy .env.example to .env and configure")
        return False


def run_tests():
    """Run the test suite."""
    try:
        result = subprocess.run([
            sys.executable, "-m", "pytest", 
            "--ignore", "test_binance_auth.py",
            "-v"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ All tests passed")
            return True
        else:
            print("❌ Some tests failed:")
            print(result.stdout)
            print(result.stderr)
            return False
    except Exception as e:
        print(f"❌ Failed to run tests: {e}")
        return False


def check_config_imports():
    """Test if configuration can be imported without errors."""
    try:
        import config
        print("✅ Configuration module loads successfully")
        
        # Test some basic config values
        required_attrs = ['MODE', 'TRADING_PAIR', 'GRID_LOW_PRICE', 'GRID_HIGH_PRICE']
        for attr in required_attrs:
            if hasattr(config, attr):
                print(f"  ✅ {attr}: {getattr(config, attr)}")
            else:
                print(f"  ❌ Missing config attribute: {attr}")
                return False
        
        return True
    except Exception as e:
        print(f"❌ Configuration import failed: {e}")
        return False


def main():
    """Run all validation checks."""
    print("🚀 DOGE Grid Trading Bot - Development Setup Validation")
    print("=" * 60)
    
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Project Structure", validate_project_structure),
        ("Environment Setup", check_environment_setup),
        ("Configuration", check_config_imports),
        ("Test Suite", run_tests),
    ]
    
    results = []
    for name, check_func in checks:
        print(f"\n📋 Checking {name}...")
        try:
            success = check_func()
            results.append((name, success))
        except Exception as e:
            print(f"❌ {name} check failed with error: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("📊 SUMMARY:")
    
    all_passed = True
    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {name}")
        if not success:
            all_passed = False
    
    if all_passed:
        print("\n🎉 All checks passed! Your development environment is ready.")
        print("\n📖 Next steps:")
        print("   1. Configure your .env file with API keys")
        print("   2. Run 'python main.py' to start the bot")
        print("   3. Run 'python dash_server.py' for the web dashboard")
        return 0
    else:
        print("\n⚠️  Some checks failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())