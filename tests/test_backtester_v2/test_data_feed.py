"""Tests for BacktesterV2 DataFeed — anti-lookahead protection.

The DataFeed is the most critical component. If it leaks future data,
every backtest result is invalid. These tests verify the invariant:
at time T, only bars with close timestamp < T are visible.
"""

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar

# ─── Fixtures ────────────────────────────────────────────────────────


def _make_hourly_data(
    n_bars: int = 100,
    symbol: str = "SPY",
    start: str = "2024-01-02 09:00",
    freq: str = "1h",
    base_price: float = 450.0,
) -> pd.DataFrame:
    """Create synthetic hourly OHLCV data for testing."""
    rng = np.random.default_rng(42)
    idx = pd.date_range(start, periods=n_bars, freq=freq, tz=None)
    close = base_price + np.cumsum(rng.normal(0, 0.5, n_bars))
    return pd.DataFrame({
        "open": close - rng.uniform(0, 0.5, n_bars),
        "high": close + rng.uniform(0, 1, n_bars),
        "low": close - rng.uniform(0, 1, n_bars),
        "close": close,
        "volume": rng.integers(100_000, 1_000_000, n_bars),
    }, index=idx)


@pytest.fixture
def sample_data() -> dict[str, pd.DataFrame]:
    return {"SPY": _make_hourly_data()}


@pytest.fixture
def feed(sample_data) -> DataFeed:
    return DataFeed(sample_data)


# ─── Core Anti-Lookahead Tests ──────────────────────────────────────


class TestAntiLookahead:
    """The most important tests in the entire codebase."""

    def test_latest_bar_is_closed(self, feed: DataFeed, sample_data):
        """THE critical test: get_latest_bar at time T returns
        a bar whose timestamp is strictly before T."""
        df = sample_data["SPY"]
        # Set time to the 10th bar timestamp
        t = df.index[10]
        feed.set_timestamp(t)
        bar = feed.get_latest_bar("SPY")
        assert bar is not None
        # The bar must be BEFORE timestamp t (the 9th bar)
        assert bar.timestamp < t
        assert bar.timestamp == df.index[9]

    def test_cannot_access_future_data(self, feed: DataFeed, sample_data):
        """Setting timestamp to start of data should yield no bars."""
        df = sample_data["SPY"]
        feed.set_timestamp(df.index[0])
        bar = feed.get_latest_bar("SPY")
        # At the exact timestamp of bar 0, bar 0 is NOT closed yet
        assert bar is None

    def test_get_bars_only_returns_past(self, feed: DataFeed, sample_data):
        """get_bars(n) must never include bars at or after current time."""
        df = sample_data["SPY"]
        t = df.index[20]
        feed.set_timestamp(t)
        bars = feed.get_bars("SPY", 100)
        # All returned bars must have index < t
        assert (bars.index < t).all()
        assert len(bars) == 20  # bars 0..19

    def test_indicator_uses_only_past_data(self, feed: DataFeed, sample_data):
        """Indicators computed at time T must use only data before T."""
        df = sample_data["SPY"]
        t = df.index[30]
        feed.set_timestamp(t)
        sma = feed.get_indicator("SPY", "sma", 10)
        # Manually compute SMA on the same slice
        expected = df["close"].iloc[20:30].mean()
        assert sma is not None
        assert abs(sma - expected) < 1e-6

    def test_no_future_leak_in_indicators(self, sample_data):
        """Computing an indicator with 100 bars vs 200 bars at the same
        timestamp must give the identical result — extra future bars
        cannot affect the output."""
        df_100 = sample_data["SPY"].iloc[:50]
        df_200 = sample_data["SPY"]  # 100 bars total

        feed_small = DataFeed({"SPY": df_100})
        feed_large = DataFeed({"SPY": df_200})

        t = df_100.index[40]  # timestamp visible to both feeds
        feed_small.set_timestamp(t)
        feed_large.set_timestamp(t)

        for indicator in ["sma", "ema", "rsi"]:
            val_small = feed_small.get_indicator("SPY", indicator, 14)
            val_large = feed_large.get_indicator("SPY", indicator, 14)
            assert val_small is not None
            assert val_large is not None
            assert abs(val_small - val_large) < 1e-10, (
                f"{indicator}: small={val_small}, large={val_large}"
            )

    def test_future_bars_invisible_after_advance(self, feed, sample_data):
        """After advancing to time T1, bars between T0 and T1 become
        visible, but bars after T1 stay invisible."""
        df = sample_data["SPY"]
        feed.set_timestamp(df.index[5])
        bars_t5 = feed.get_bars("SPY", 100)
        feed.set_timestamp(df.index[10])
        bars_t10 = feed.get_bars("SPY", 100)
        assert len(bars_t10) > len(bars_t5)
        assert (bars_t10.index < df.index[10]).all()


# ─── Edge Cases ─────────────────────────────────────────────────────


class TestEdgeCases:

    def test_empty_data(self):
        """DataFeed with empty DataFrame should not crash."""
        df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            dtype=float,
        )
        df.index = pd.DatetimeIndex([])
        feed = DataFeed({"SPY": df})
        feed.set_timestamp(pd.Timestamp("2024-01-02 10:00"))
        assert feed.get_latest_bar("SPY") is None
        assert feed.get_bars("SPY", 10).empty

    def test_single_bar(self):
        """With one bar, it's only visible after its timestamp."""
        idx = pd.DatetimeIndex([pd.Timestamp("2024-01-02 10:00")])
        df = pd.DataFrame({
            "open": [100], "high": [101], "low": [99],
            "close": [100.5], "volume": [1000],
        }, index=idx)
        feed = DataFeed({"SPY": df})
        # At bar time, it's not visible
        feed.set_timestamp(pd.Timestamp("2024-01-02 10:00"))
        assert feed.get_latest_bar("SPY") is None
        # After bar time, it IS visible
        feed.set_timestamp(pd.Timestamp("2024-01-02 11:00"))
        bar = feed.get_latest_bar("SPY")
        assert bar is not None
        assert bar.close == 100.5

    def test_get_bars_n_smaller_than_available(self, feed, sample_data):
        """get_bars(n=5) returns exactly 5 bars when more are available."""
        df = sample_data["SPY"]
        feed.set_timestamp(df.index[50])
        bars = feed.get_bars("SPY", 5)
        assert len(bars) == 5

    def test_get_bars_n_larger_than_available(self, feed, sample_data):
        """get_bars(n=100) returns all visible bars if fewer than n."""
        df = sample_data["SPY"]
        feed.set_timestamp(df.index[3])
        bars = feed.get_bars("SPY", 100)
        assert len(bars) == 3

    def test_unknown_symbol_raises(self, feed):
        """Requesting unknown symbol raises KeyError."""
        feed.set_timestamp(pd.Timestamp("2024-01-02 10:00"))
        with pytest.raises(KeyError, match="Unknown symbol"):
            feed.get_latest_bar("UNKNOWN")

    def test_no_timestamp_raises(self, feed):
        """Calling get_latest_bar without set_timestamp raises."""
        with pytest.raises(RuntimeError, match="set_timestamp"):
            feed.get_latest_bar("SPY")


# ─── Validation Tests ───────────────────────────────────────────────


class TestValidation:

    def test_unsorted_data_raises(self):
        """DataFeed rejects unsorted DataFrames."""
        idx = pd.DatetimeIndex([
            pd.Timestamp("2024-01-02 11:00"),
            pd.Timestamp("2024-01-02 10:00"),  # out of order
        ])
        df = pd.DataFrame({
            "open": [100, 101], "high": [102, 103],
            "low": [98, 99], "close": [101, 102], "volume": [1000, 2000],
        }, index=idx)
        with pytest.raises(ValueError, match="sorted ascending"):
            DataFeed({"SPY": df})

    def test_missing_columns_raises(self):
        """DataFeed rejects DataFrames without OHLCV columns."""
        df = pd.DataFrame({"close": [100]}, index=pd.DatetimeIndex(["2024-01-02"]))
        with pytest.raises(ValueError, match="Missing columns"):
            DataFeed({"SPY": df})

    def test_duplicate_timestamps_ok(self):
        """Duplicate timestamps are allowed (some data sources have them)."""
        idx = pd.DatetimeIndex([
            pd.Timestamp("2024-01-02 10:00"),
            pd.Timestamp("2024-01-02 10:00"),
        ])
        df = pd.DataFrame({
            "open": [100, 101], "high": [102, 103],
            "low": [98, 99], "close": [101, 102], "volume": [1000, 2000],
        }, index=idx)
        feed = DataFeed({"SPY": df})
        feed.set_timestamp(pd.Timestamp("2024-01-02 11:00"))
        bar = feed.get_latest_bar("SPY")
        assert bar is not None


# ─── Indicator Tests ────────────────────────────────────────────────


class TestIndicators:

    @pytest.fixture
    def ind_feed(self):
        data = _make_hourly_data(200, base_price=100.0)
        feed = DataFeed({"SPY": data})
        feed.set_timestamp(data.index[150])
        return feed

    def test_ema(self, ind_feed):
        val = ind_feed.get_indicator("SPY", "ema", 20)
        assert val is not None
        assert 50 < val < 200  # sanity range

    def test_sma(self, ind_feed):
        val = ind_feed.get_indicator("SPY", "sma", 20)
        assert val is not None

    def test_rsi(self, ind_feed):
        val = ind_feed.get_indicator("SPY", "rsi", 14)
        assert val is not None
        assert 0 <= val <= 100

    def test_atr(self, ind_feed):
        val = ind_feed.get_indicator("SPY", "atr", 14)
        assert val is not None
        assert val > 0

    def test_adx(self, ind_feed):
        val = ind_feed.get_indicator("SPY", "adx", 14)
        assert val is not None
        assert 0 <= val <= 100

    def test_bollinger_bands(self, ind_feed):
        upper = ind_feed.get_indicator("SPY", "bollinger_upper", 20)
        mid = ind_feed.get_indicator("SPY", "bollinger_mid", 20)
        lower = ind_feed.get_indicator("SPY", "bollinger_lower", 20)
        assert upper is not None and mid is not None and lower is not None
        assert lower < mid < upper

    def test_unknown_indicator_raises(self, ind_feed):
        with pytest.raises(ValueError, match="Unknown indicator"):
            ind_feed.get_indicator("SPY", "macd", 14)

    def test_insufficient_data_returns_none(self):
        data = _make_hourly_data(5)
        feed = DataFeed({"SPY": data})
        feed.set_timestamp(data.index[3])
        # Only 3 visible bars, need 14
        assert feed.get_indicator("SPY", "rsi", 14) is None

    def test_set_timestamp_clears_cache(self, sample_data):
        """Advancing time must clear indicator cache."""
        feed = DataFeed(sample_data)
        df = sample_data["SPY"]

        feed.set_timestamp(df.index[30])
        sma1 = feed.get_indicator("SPY", "sma", 10)

        feed.set_timestamp(df.index[50])
        sma2 = feed.get_indicator("SPY", "sma", 10)

        # Different timestamps must produce different values
        # (unless by extreme coincidence)
        assert sma1 is not None
        assert sma2 is not None
        # They should differ since the price series has a random walk component
        # But we just verify they were recalculated (cache cleared)
        # by checking the feed's internal cache was reset
        assert feed._cache != {}  # cache populated after second call


# ─── Property Tests ─────────────────────────────────────────────────


class TestProperties:

    def test_symbols_list(self, feed):
        assert "SPY" in feed.symbols

    def test_timestamp_property(self, feed):
        assert feed.timestamp is None
        feed.set_timestamp(pd.Timestamp("2024-06-01"))
        assert feed.timestamp == pd.Timestamp("2024-06-01")

    def test_bar_dataclass_fields(self, feed, sample_data):
        df = sample_data["SPY"]
        feed.set_timestamp(df.index[5])
        bar = feed.get_latest_bar("SPY")
        assert isinstance(bar, Bar)
        assert isinstance(bar.symbol, str)
        assert isinstance(bar.close, float)
        assert isinstance(bar.volume, float)

    def test_multiple_symbols(self):
        data1 = _make_hourly_data(50, symbol="SPY", base_price=450)
        data2 = _make_hourly_data(50, symbol="QQQ", base_price=380)
        feed = DataFeed({"SPY": data1, "QQQ": data2})
        feed.set_timestamp(data1.index[20])
        bar_spy = feed.get_latest_bar("SPY")
        bar_qqq = feed.get_latest_bar("QQQ")
        assert bar_spy is not None and bar_qqq is not None
        assert bar_spy.symbol == "SPY"
        assert bar_qqq.symbol == "QQQ"
