"""Contract definitions for Binance API responses.

Contracts validate the STRUCTURE of API responses, not the values.
When a contract fails, it means Binance changed their API.
"""


class BinanceContract:
    """Expected response structures from Binance API."""

    @staticmethod
    def account_balance(response: dict) -> tuple[bool, str]:
        """Validate GET /api/v3/account response."""
        required_keys = {"balances", "canTrade", "canWithdraw"}
        missing = required_keys - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"

        if not isinstance(response["balances"], list):
            return False, "balances is not a list"

        for i, balance in enumerate(response["balances"][:5]):
            bal_keys = {"asset", "free", "locked"}
            bal_missing = bal_keys - set(balance.keys())
            if bal_missing:
                return False, f"Balance[{i}] missing keys: {bal_missing}"
            try:
                float(balance["free"])
                float(balance["locked"])
            except (ValueError, TypeError) as e:
                return False, f"Balance[{i}] non-numeric value: {e}"

        return True, "OK"

    @staticmethod
    def order_response(response: dict) -> tuple[bool, str]:
        """Validate POST /api/v3/order response."""
        required = {
            "symbol", "orderId", "status", "type",
            "side", "executedQty", "cummulativeQuoteQty"
        }
        missing = required - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        return True, "OK"

    @staticmethod
    def margin_account(response: dict) -> tuple[bool, str]:
        """Validate GET /sapi/v1/margin/account response."""
        required = {"marginLevel", "totalAssetOfBtc", "totalLiabilityOfBtc"}
        missing = required - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        return True, "OK"

    @staticmethod
    def exchange_info(response: dict) -> tuple[bool, str]:
        """Validate GET /api/v3/exchangeInfo response."""
        if "symbols" not in response:
            return False, "Missing 'symbols' key"
        if not isinstance(response["symbols"], list):
            return False, "'symbols' is not a list"
        for sym in response["symbols"][:5]:
            required = {"symbol", "status", "baseAsset", "quoteAsset"}
            missing = required - set(sym.keys())
            if missing:
                return False, f"Symbol missing keys: {missing}"
        return True, "OK"

    @staticmethod
    def klines(response: list) -> tuple[bool, str]:
        """Validate GET /api/v3/klines response."""
        if not isinstance(response, list):
            return False, "Response is not a list"
        if len(response) == 0:
            return True, "OK (empty)"
        # Each kline is a list of 12 elements
        kline = response[0]
        if not isinstance(kline, list):
            return False, "Kline entry is not a list"
        if len(kline) < 11:
            return False, f"Kline has {len(kline)} elements (expected >=11)"
        return True, "OK"
