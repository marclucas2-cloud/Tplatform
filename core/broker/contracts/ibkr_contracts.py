"""Contract definitions for IBKR (via ib_insync) responses."""


class IBKRContract:
    """Expected response structures from IBKR API."""

    @staticmethod
    def account_info(response: dict) -> tuple[bool, str]:
        """Validate account info dict (from get_account_info())."""
        expected = {"equity", "cash"}
        missing = expected - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        try:
            float(response["equity"])
            float(response["cash"])
        except (ValueError, TypeError) as e:
            return False, f"Non-numeric account value: {e}"
        return True, "OK"

    @staticmethod
    def position(response: dict) -> tuple[bool, str]:
        """Validate a single position dict."""
        expected = {"symbol", "qty"}
        missing = expected - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        return True, "OK"

    @staticmethod
    def positions_list(response: list) -> tuple[bool, str]:
        """Validate list of positions."""
        if not isinstance(response, list):
            return False, "Response is not a list"
        for i, pos in enumerate(response[:5]):
            ok, msg = IBKRContract.position(pos)
            if not ok:
                return False, f"Position[{i}]: {msg}"
        return True, "OK"

    @staticmethod
    def order_status(response: dict) -> tuple[bool, str]:
        """Validate order status dict."""
        expected = {"orderId", "status"}
        missing = expected - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        return True, "OK"
