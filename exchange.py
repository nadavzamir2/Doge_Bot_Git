"""
Exchange interface wrapper for cryptocurrency trading operations.

This module provides a simplified interface for cryptocurrency exchange operations,
supporting both paper trading and live trading modes with comprehensive
market data access and order management.
"""

import os
import time
from typing import Any, Dict, List, Optional

import ccxt


class ExchangeInterface:
    """
    Wrapper class for CCXT exchange operations.

    Provides a simplified interface for trading operations with support
    for both paper trading and live trading modes.
    """

    def __init__(self, paper_trading: bool = True):
        """
        Initialize the exchange interface.

        Args:
            paper_trading: If True, run in paper trading mode
        """
        self.paper_trading = paper_trading

        # Initialize CCXT client
        self.client = ccxt.binance(
            {
                "apiKey": os.getenv("BINANCE_API_KEY", ""),
                "secret": os.getenv("BINANCE_API_SECRET", ""),
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
            }
        )

        # Load market data
        self.markets = self.client.load_markets()

    def get_ticker_price(self, symbol: str) -> float:
        """
        Get the current ticker price for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'DOGE/USDT')

        Returns:
            float: Current price
        """
        ticker = self.client.fetch_ticker(symbol)
        return float(ticker["last"])

    def get_exchange_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get exchange-specific trading information for a symbol.

        Args:
            symbol: Trading symbol to query

        Returns:
            Dict[str, Any]: Trading information including precision and limits
        """
        market_info = self.markets[symbol]
        precision = market_info.get("precision", {}) or {}
        limits = market_info.get("limits", {}) or {}

        amount_precision = precision.get("amount")
        price_precision = precision.get("price")

        # Extract step sizes and tick sizes from filters
        amount_step = None
        price_tick = None
        min_notional = None

        try:
            filters = market_info.get("info", {}).get("filters", [])
            for filter_info in filters:
                filter_type = filter_info.get("filterType")

                if filter_type == "LOT_SIZE":
                    step_size = filter_info.get("stepSize")
                    if step_size is not None:
                        amount_step = float(step_size)
                elif filter_type == "PRICE_FILTER":
                    tick_size = filter_info.get("tickSize")
                    if tick_size is not None:
                        price_tick = float(tick_size)
                elif filter_type in ("NOTIONAL", "MIN_NOTIONAL"):
                    min_notional_value = filter_info.get("minNotional")
                    if min_notional_value is not None:
                        min_notional = float(min_notional_value)
        except Exception:
            pass

        # Set defaults for missing values
        if amount_step is None and amount_precision == 0:
            amount_step = 1.0

        # Calculate minimum cost
        min_cost = limits.get("cost", {}).get("min")
        if min_notional is not None:
            if min_cost is None:
                min_cost = min_notional
            else:
                min_cost = max(float(min_cost), min_notional)

        if min_cost is None:
            min_cost = 5.0  # Default fallback

        return {
            "amount_precision": amount_precision,
            "price_precision": price_precision,
            "amount_step": amount_step,
            "price_tick": price_tick,
            "min_cost": float(min_cost),
        }

    def generate_client_id(self, prefix: str = "bot") -> str:
        """
        Generate a unique client order ID.

        Args:
            prefix: Prefix for the client ID

        Returns:
            str: Unique client order ID
        """
        timestamp = int(time.time() * 1000)
        return f"{prefix}_{timestamp}"

    def place_limit_buy_order(
        self, symbol: str, amount: float, price: float, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place a limit buy order.

        Args:
            symbol: Trading symbol
            amount: Amount to buy
            price: Limit price
            client_order_id: Optional client order ID

        Returns:
            Dict[str, Any]: Order result with ID and status
        """
        if self.paper_trading:
            return {"id": f"paper_buy_{price}", "status": "paper"}

        params = {}
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        order = self.client.create_order(symbol, "limit", "buy", amount, price, params)
        return {"id": order["id"], "status": order.get("status", "open")}

    def place_limit_sell_order(
        self, symbol: str, amount: float, price: float, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place a limit sell order.

        Args:
            symbol: Trading symbol
            amount: Amount to sell
            price: Limit price
            client_order_id: Optional client order ID

        Returns:
            Dict[str, Any]: Order result with ID and status
        """
        if self.paper_trading:
            return {"id": f"paper_sell_{price}", "status": "paper"}

        params = {}
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        order = self.client.create_order(symbol, "limit", "sell", amount, price, params)
        return {"id": order["id"], "status": order.get("status", "open")}

    def place_market_buy_order_by_quote(
        self, symbol: str, quote_amount_usdt: float
    ) -> Dict[str, Any]:
        """
        Place a market buy order specifying the quote amount.

        Args:
            symbol: Trading symbol
            quote_amount_usdt: Amount in USDT to spend

        Returns:
            Dict[str, Any]: Order result with ID and status

        Note:
            Useful for buying BNB from profit bank with exact USD amount.
        """
        if self.paper_trading:
            return {"id": "paper_market_buy", "status": "paper"}

        order = self.client.create_order(
            symbol, "market", "buy", None, None, {"quoteOrderQty": quote_amount_usdt}
        )
        return {"id": order["id"], "status": order.get("status", "closed")}

    def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Fetch all open orders for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List[Dict[str, Any]]: List of open orders
        """
        return self.client.fetch_open_orders(symbol)

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Fetch details of a specific order.

        Args:
            order_id: Order ID to fetch
            symbol: Trading symbol

        Returns:
            Dict[str, Any]: Order details
        """
        return self.client.fetch_order(order_id, symbol)

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Cancel a specific order.

        Args:
            order_id: Order ID to cancel
            symbol: Trading symbol

        Returns:
            Dict[str, Any]: Cancellation result
        """
        return self.client.cancel_order(order_id, symbol)

    def get_free_balance(self, currency_code: str) -> float:
        """
        Get the free balance for a specific currency.

        Args:
            currency_code: Currency code (e.g., 'USDT', 'BNB')

        Returns:
            float: Free balance amount
        """
        try:
            balance = self.client.fetch_balance()
            free_balances = balance.get("free", {})
            return float(free_balances.get(currency_code, 0) or 0)
        except Exception:
            return 0.0


# Legacy class name for backward compatibility
class Exchange(ExchangeInterface):
    """Legacy class name. Use ExchangeInterface instead."""

    def __init__(self, paper: bool = True):
        """Legacy constructor. Use ExchangeInterface instead."""
        super().__init__(paper_trading=paper)

    def ticker_price(self, symbol: str) -> float:
        """Legacy method. Use get_ticker_price instead."""
        return self.get_ticker_price(symbol)

    def exchange_info(self, symbol: str) -> Dict[str, Any]:
        """Legacy method. Use get_exchange_info instead."""
        return self.get_exchange_info(symbol)

    def new_cid(self, prefix: str = "bot") -> str:
        """Legacy method. Use generate_client_id instead."""
        return self.generate_client_id(prefix)

    def place_limit_buy(
        self, symbol: str, amount: float, price: float, client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Legacy method. Use place_limit_buy_order instead."""
        return self.place_limit_buy_order(symbol, amount, price, client_id)

    def place_limit_sell(
        self, symbol: str, amount: float, price: float, client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Legacy method. Use place_limit_sell_order instead."""
        return self.place_limit_sell_order(symbol, amount, price, client_id)

    def place_market_buy_quote(self, symbol: str, quote_usdt: float) -> Dict[str, Any]:
        """Legacy method. Use place_market_buy_order_by_quote instead."""
        return self.place_market_buy_order_by_quote(symbol, quote_usdt)

    def fetch_free(self, code: str) -> float:
        """Legacy method. Use get_free_balance instead."""
        return self.get_free_balance(code)
