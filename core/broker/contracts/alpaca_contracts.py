"""Contract definitions for Alpaca API responses."""


class AlpacaContract:
    """Expected response structures from Alpaca API."""

    @staticmethod
    def account(response: dict) -> tuple[bool, str]:
        """Validate account info dict."""
        expected = {"equity", "cash"}
        missing = expected - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        try:
            float(response["equity"])
            float(response["cash"])
        except (ValueError, TypeError) as e:
            return False, f"Non-numeric: {e}"
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
            ok, msg = AlpacaContract.position(pos)
            if not ok:
                return False, f"Position[{i}]: {msg}"
        return True, "OK"

    @staticmethod
    def order(response: dict) -> tuple[bool, str]:
        """Validate order response."""
        expected = {"id", "status", "symbol", "side", "qty"}
        missing = expected - set(response.keys())
        if missing:
            return False, f"Missing keys: {missing}"
        return True, "OK"
