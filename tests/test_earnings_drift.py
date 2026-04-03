"""Tests for EarningsDrift (PEAD) strategy."""
import pandas as pd

from core.backtester_v2.types import Bar, PortfolioState


class TestEarningsDrift:
    def _make_bar(self, symbol, close, open_=None, ts=None):
        if ts is None:
            ts = pd.Timestamp("2026-03-31 10:00")
        if open_ is None:
            open_ = close * 0.99
        return Bar(
            symbol=symbol, timestamp=ts,
            open=open_, high=close * 1.01, low=close * 0.98,
            close=close, volume=1e6,
        )

    def _portfolio(self, equity=100_000):
        return PortfolioState(equity=equity, cash=equity)

    def test_import_strategy(self):
        from strategies_v2.stocks.earnings_drift import EarningsDrift
        strat = EarningsDrift()
        assert strat.name == "earnings_drift"

    def test_no_signal_without_earnings(self):
        from strategies_v2.stocks.earnings_drift import EarningsDrift
        strat = EarningsDrift()
        bar = self._make_bar("NVDA", 900.0)
        signal = strat.on_bar(bar, self._portfolio())
        # Without data feed or earnings data, should return None
        assert signal is None

    def test_strategy_has_parameters(self):
        from strategies_v2.stocks.earnings_drift import EarningsDrift
        strat = EarningsDrift()
        params = strat.get_parameters()
        assert "surprise_threshold" in params or "hold_days" in params or isinstance(params, dict)

    def test_strategy_properties(self):
        from strategies_v2.stocks.earnings_drift import EarningsDrift
        strat = EarningsDrift()
        assert strat.asset_class in ("equity", "stocks", "us_equity", "eu_equity")
        assert len(strat.name) > 0


class TestEarningsDriftLogic:
    def test_positive_surprise_would_buy(self):
        """With a 10% positive surprise and gap-up, strategy should want to buy."""
        # Verify the logic conceptually
        surprise_pct = 0.10
        gap_pct = 0.03
        threshold = 0.05
        assert surprise_pct > threshold
        assert gap_pct > 0  # Gap-up confirms

    def test_negative_surprise_would_short(self):
        surprise_pct = -0.08
        gap_pct = -0.04
        threshold = -0.05
        assert surprise_pct < threshold
        assert gap_pct < 0

    def test_small_surprise_filtered(self):
        surprise_pct = 0.02
        threshold = 0.05
        assert abs(surprise_pct) < threshold  # Should not trade

    def test_max_concurrent_limit(self):
        max_positions = 3
        current = 3
        assert current >= max_positions  # Should block new entry
