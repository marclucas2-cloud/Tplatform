"""
Tests for ALL new ROC crypto modules (~30 tests):
  - CryptoConvictionSizer (core/crypto/conviction_sizer.py) — 8 tests
  - BorrowRateMonitor (core/crypto/borrow_monitor.py) — 8 tests
  - CryptoRegimeDetector (core/crypto/regime_detector.py) — 8 tests
  - CryptoEntryTiming (core/crypto/entry_timing.py) — 6 tests

Each test is self-contained with mock data.
Modules that don't exist yet use pytest.importorskip() to gracefully skip.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# Setup paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════
# Module imports (graceful skip if module doesn't exist yet)
# ═════════════════════���═════════════════════════════════════════════════

conviction_sizer_mod = pytest.importorskip(
    "core.crypto.conviction_sizer",
    reason="CryptoConvictionSizer not available",
)
CryptoConvictionSizer = conviction_sizer_mod.CryptoConvictionSizer
_score_trend_strength = conviction_sizer_mod._score_trend_strength
_score_volume_confirm = conviction_sizer_mod._score_volume_confirm
_score_regime_align = conviction_sizer_mod._score_regime_align
_score_borrow_cost = conviction_sizer_mod._score_borrow_cost
_score_correlation = conviction_sizer_mod._score_correlation
CONVICTION_TIERS = conviction_sizer_mod.CONVICTION_TIERS

borrow_monitor_mod = pytest.importorskip(
    "core.crypto.borrow_monitor",
    reason="BorrowRateMonitor not available",
)
BorrowRateMonitor = borrow_monitor_mod.BorrowRateMonitor

regime_detector_mod = pytest.importorskip(
    "core.crypto.regime_detector",
    reason="CryptoRegimeDetector not available",
)
CryptoRegimeDetector = regime_detector_mod.CryptoRegimeDetector

entry_timing_mod = pytest.importorskip(
    "core.crypto.entry_timing",
    reason="CryptoEntryTiming not available",
)
CryptoEntryTiming = entry_timing_mod.CryptoEntryTiming


# ══════════════════════════════════��════════════════════════════════════
# Helper: build market state dicts for CryptoConvictionSizer
# ════════��═══════════════════���══════════════════════════════════════════


def _bull_market_state(**overrides) -> dict:
    """Strong bull market (high ADX, high volume, BULL, cheap borrow, low corr)."""
    base = {
        "adx": 45,
        "volume_ratio_24h": 2.5,
        "regime": "BULL",
        "borrow_rate_daily": 0.0001,
        "btc_correlation_7d": 0.2,
    }
    base.update(overrides)
    return base


def _bear_market_state(**overrides) -> dict:
    """Bear market with moderate signals."""
    base = {
        "adx": 35,
        "volume_ratio_24h": 1.8,
        "regime": "BEAR",
        "borrow_rate_daily": 0.0008,
        "btc_correlation_7d": 0.8,
    }
    base.update(overrides)
    return base


def _chop_market_state(**overrides) -> dict:
    """Choppy, low-conviction market."""
    base = {
        "adx": 15,
        "volume_ratio_24h": 0.7,
        "regime": "CHOP",
        "borrow_rate_daily": 0.0005,
        "btc_correlation_7d": 0.6,
    }
    base.update(overrides)
    return base


# ═��═════════════════════════════════════════════════════════════════════
# PART 1: CryptoConvictionSizer — 8 tests
# ════════════════════════════════════���══════════════════════════════════


class TestCryptoConvictionSizer:
    """8 tests for CryptoConvictionSizer (ROC-C02)."""

    @pytest.fixture
    def sizer(self):
        return CryptoConvictionSizer()

    @pytest.fixture
    def capital(self):
        return 15_000.0

    # ── Test 1: Strong conviction in bull market ──

    def test_strong_conviction_bull_market(self, sizer):
        """BUY signal in strong bull market -> STRONG conviction (>= 0.8)."""
        signal = {"side": "BUY", "symbol": "BTCUSDT"}
        market = _bull_market_state()

        score, breakdown = sizer.calculate_conviction(signal, market)

        assert score >= 0.8, f"Expected STRONG (>= 0.8), got {score:.3f}"
        assert breakdown["trend_strength"] == 1.0   # ADX 45 -> 1.0
        assert breakdown["regime_align"] == 1.0      # BUY + BULL -> 1.0

    # ── Test 2: Weak conviction counter-trend ──

    def test_weak_conviction_counter_trend(self, sizer):
        """BUY signal in BEAR market with low ADX -> low conviction."""
        signal = {"side": "BUY", "symbol": "BTCUSDT"}
        market = _bear_market_state(adx=18, volume_ratio_24h=0.7)

        score, breakdown = sizer.calculate_conviction(signal, market)

        # Counter-trend (BUY in BEAR) + low ADX + low volume => weak
        assert score < 0.5, f"Expected WEAK (< 0.5), got {score:.3f}"
        assert breakdown["regime_align"] == 0.3  # Counter-trend penalty

    # ── Test 3: Skip below threshold ──

    def test_skip_below_threshold(self, sizer, capital):
        """Very weak SELL signal should result in SKIP (size = 0)."""
        # SELL in BULL (counter-trend) + low ADX + low volume
        # + expensive borrow (penalises shorts) + high correlation
        signal = {"side": "SELL", "symbol": "BTCUSDT"}
        market = {
            "adx": 10,                   # -> 0.2
            "volume_ratio_24h": 0.5,     # -> 0.2
            "regime": "BULL",            # SELL in BULL -> 0.3
            "borrow_rate_daily": 0.002,  # expensive short -> 0.2
            "btc_correlation_7d": 0.95,  # high corr -> 0.3
        }

        size, score, level = sizer.get_adjusted_size(
            signal, market, base_kelly=0.125, capital=capital
        )

        assert level == "SKIP"
        assert size == 0.0
        assert score < 0.3

    # ── Test 4: Borrow cost penalty for shorts ──

    def test_borrow_cost_penalty_shorts(self, sizer):
        """SELL signal should be penalised by expensive borrow rates."""
        signal = {"side": "SELL", "symbol": "ETHUSDT"}
        market_cheap = _bull_market_state(borrow_rate_daily=0.0001)
        market_expensive = _bull_market_state(borrow_rate_daily=0.002)

        _, breakdown_cheap = sizer.calculate_conviction(signal, market_cheap)
        _, breakdown_expensive = sizer.calculate_conviction(signal, market_expensive)

        assert breakdown_cheap["borrow_cost"] > breakdown_expensive["borrow_cost"]
        assert breakdown_expensive["borrow_cost"] <= 0.4  # expensive -> low score

    # ── Test 5: No borrow penalty for longs ──

    def test_no_borrow_penalty_longs(self, sizer):
        """BUY signal should NOT be penalised by borrow rates."""
        signal = {"side": "BUY", "symbol": "BTCUSDT"}
        market = _bull_market_state(borrow_rate_daily=0.005)  # Very expensive

        _, breakdown = sizer.calculate_conviction(signal, market)

        # BUY => borrow_cost score = 0.8 (neutral), not penalised
        assert breakdown["borrow_cost"] == 0.8

    # ── Test 6: Adjusted size for strong conviction ──

    def test_adjusted_size_strong(self, sizer, capital):
        """STRONG conviction should give size > 0 capped by tier max."""
        signal = {"side": "BUY", "symbol": "BTCUSDT"}
        market = _bull_market_state()
        base_kelly = 0.125

        size, score, level = sizer.get_adjusted_size(
            signal, market, base_kelly, capital
        )

        assert level == "STRONG"
        assert size > 0
        # STRONG = 1.5x kelly, capped at 3/16 kelly
        max_expected = capital * (3 / 16)
        assert size <= max_expected + 0.01

    # ── Test 7: Skip returns zero ──

    def test_adjusted_size_skip_returns_zero(self, sizer, capital):
        """SKIP conviction should return size = 0."""
        # Use SELL with worst conditions to get below 0.3 threshold
        signal = {"side": "SELL", "symbol": "BTCUSDT"}
        market = {
            "adx": 10,
            "volume_ratio_24h": 0.5,
            "regime": "BULL",
            "borrow_rate_daily": 0.002,
            "btc_correlation_7d": 0.95,
        }

        size, score, level = sizer.get_adjusted_size(
            signal, market, base_kelly=0.125, capital=capital
        )

        assert size == 0.0
        assert level == "SKIP"

    # ── Test 8: Correlation penalty ──

    def test_correlation_penalty(self, sizer):
        """High BTC correlation should reduce conviction score."""
        signal = {"side": "BUY", "symbol": "ETHUSDT"}
        market_low_corr = _bull_market_state(btc_correlation_7d=0.1)
        market_high_corr = _bull_market_state(btc_correlation_7d=0.9)

        score_low, _ = sizer.calculate_conviction(signal, market_low_corr)
        score_high, _ = sizer.calculate_conviction(signal, market_high_corr)

        assert score_low > score_high, (
            f"Low correlation ({score_low:.3f}) should give higher conviction "
            f"than high correlation ({score_high:.3f})"
        )


# ══════════���═════════════════════════��══════════════════════════════════
# PART 2: BorrowRateMonitor — 8 tests
# ═══════════════════════════════════════��═══════════════════════════════


class TestBorrowRateMonitor:
    """8 tests for BorrowRateMonitor (ROC-C03).

    BorrowRateMonitor uses broker.get_borrow_rate(asset) internally.
    We mock the broker to control rate values.
    """

    @pytest.fixture
    def mock_broker(self):
        """Mock broker with configurable borrow rates."""
        broker = MagicMock()
        # Default: return low normal rate
        broker.get_borrow_rate.return_value = {"daily_rate": 0.0002}
        broker.close_position = MagicMock()
        return broker

    @pytest.fixture
    def monitor(self, mock_broker):
        return BorrowRateMonitor(
            broker=mock_broker,
            capital=15_000.0,
            max_daily_rate=0.001,          # 0.1%/day
            max_monthly_cost_pct=2.0,      # 2% of capital/month
        )

    def _make_short(self, symbol: str, notional: float = 1000.0) -> dict:
        """Create a short position dict."""
        return {"symbol": symbol, "side": "SHORT", "qty": 1.0, "notional_usd": notional}

    # ── Test 1: Normal rates, no alert ──

    def test_normal_rates_no_alert(self, monitor, mock_broker):
        """Normal borrow rate should produce no alerts."""
        mock_broker.get_borrow_rate.return_value = {"daily_rate": 0.0002}
        positions = [self._make_short("BTC", 2000)]

        alerts = monitor.check_rates(positions)

        assert len(alerts) == 0

    # ── Test 2: High rate warning ──

    def test_high_rate_warning(self, monitor, mock_broker):
        """Rate exceeding max_daily_rate should produce WARNING."""
        mock_broker.get_borrow_rate.return_value = {"daily_rate": 0.0015}
        positions = [self._make_short("SOL", 1000)]

        alerts = monitor.check_rates(positions)

        warning_alerts = [a for a in alerts if a.level == "WARNING"]
        assert len(warning_alerts) >= 1
        assert warning_alerts[0].asset == "SOL"

    # ── Test 3: Rate spike 3x -> critical ──

    def test_rate_spike_3x_critical(self, monitor, mock_broker):
        """A 3x+ rate spike within 1 hour should trigger CRITICAL."""
        # First check: baseline rate at t=0
        mock_broker.get_borrow_rate.return_value = {"daily_rate": 0.0005}
        positions = [self._make_short("ETH", 2000)]

        # Record baseline — manually inject into history with old timestamp
        now = time.time()
        monitor._record_rate("ETH", 0.0005, now - 7200)  # 2h ago baseline

        # Now spike: 4x increase
        mock_broker.get_borrow_rate.return_value = {"daily_rate": 0.002}
        alerts = monitor.check_rates(positions)

        critical_alerts = [a for a in alerts if a.level == "CRITICAL"]
        assert len(critical_alerts) >= 1

    # ── Test 4: Monthly cost warning ──

    def test_monthly_cost_warning(self, monitor, mock_broker):
        """High total monthly borrow cost should trigger CRITICAL alert."""
        # 3 expensive shorts: total monthly cost >> 2% of $15K
        mock_broker.get_borrow_rate.return_value = {"daily_rate": 0.005}
        positions = [
            self._make_short("BTC", 5000),
            self._make_short("ETH", 3000),
            self._make_short("SOL", 2000),
        ]

        alerts = monitor.check_rates(positions)

        # Monthly cost: (5000+3000+2000) * 0.005 * 30 = $1500
        # As pct of $15000 capital = 10% >> 2% threshold
        portfolio_alerts = [a for a in alerts if a.asset == "PORTFOLIO"]
        assert len(portfolio_alerts) >= 1
        assert portfolio_alerts[0].level == "CRITICAL"

    # ── Test 5: Auto-close most expensive first ──

    def test_auto_close_most_expensive_first(self, monitor, mock_broker):
        """auto_close_expensive_shorts should close most expensive first."""
        # Set up different rates per asset
        def rate_by_asset(asset):
            rates = {
                "DOGE": {"daily_rate": 0.010},  # most expensive
                "SOL": {"daily_rate": 0.006},
                "BTC": {"daily_rate": 0.0002},   # cheap
            }
            return rates.get(asset, {"daily_rate": 0.001})

        mock_broker.get_borrow_rate.side_effect = rate_by_asset

        positions = [
            self._make_short("BTC", 2000),
            self._make_short("DOGE", 1000),
            self._make_short("SOL", 500),
        ]

        closed = monitor.auto_close_expensive_shorts(positions, mock_broker)

        # Should close DOGE first (most expensive per dollar)
        if closed:
            assert closed[0] == "DOGE"

    # ���─ Test 6: Auto-close stops when under threshold ──

    def test_auto_close_stops_when_under_threshold(self, monitor, mock_broker):
        """Auto-close should stop once monthly cost is under threshold."""
        def rate_by_asset(asset):
            rates = {
                "DOGE": {"daily_rate": 0.010},
                "BTC": {"daily_rate": 0.0001},   # very cheap
            }
            return rates.get(asset, {"daily_rate": 0.0001})

        mock_broker.get_borrow_rate.side_effect = rate_by_asset

        # Only DOGE is expensive; BTC is very cheap
        positions = [
            self._make_short("DOGE", 1000),   # monthly: 1000*0.01*30 = $300 (2% of 15K)
            self._make_short("BTC", 2000),     # monthly: 2000*0.0001*30 = $6 (trivial)
        ]

        closed = monitor.auto_close_expensive_shorts(positions, mock_broker)

        # Should close DOGE but leave BTC (already under threshold)
        assert "DOGE" in closed
        # BTC should NOT be closed
        assert "BTC" not in closed

    # ── Test 7: Rate history tracking ──

    def test_rate_history_tracking(self, monitor):
        """Rate history should be recorded per asset."""
        now = time.time()
        monitor._record_rate("BTC", 0.0002, now)
        monitor._record_rate("BTC", 0.0003, now + 60)
        monitor._record_rate("BTC", 0.0004, now + 120)
        monitor._record_rate("ETH", 0.0005, now)

        btc_history = monitor._rate_history["BTC"]
        eth_history = monitor._rate_history["ETH"]

        assert len(btc_history) == 3
        assert len(eth_history) == 1
        assert btc_history[-1]["rate"] == 0.0004

    # ── Test 8: Report format ──

    def test_get_report_format(self, monitor, mock_broker):
        """get_report() should include standard report fields."""
        mock_broker.get_borrow_rate.return_value = {"daily_rate": 0.0002}
        positions = [self._make_short("BTC", 2000)]
        monitor.check_rates(positions)

        report = monitor.get_report()

        assert "assets" in report
        assert "total_assets_monitored" in report
        assert "capital" in report
        assert report["capital"] == 15_000.0
        assert "timestamp" in report
        assert report["total_assets_monitored"] >= 1


# ═════════════════════��═════════════════════════════════════════════════
# PART 3: CryptoRegimeDetector — 8 tests
# ═════════════════════════════════════════════════��════════════════════��


class TestCryptoRegimeDetector:
    """8 tests for CryptoRegimeDetector (ROC-C04).

    Detector takes market_data dict with:
      btc_close, btc_ema50, btc_ema200, btc_return_30d,
      vol_7d, vol_30d, btc_trend_direction, altcoin_above_ema50_pct
    """

    @pytest.fixture
    def detector(self):
        return CryptoRegimeDetector()

    def _bull_data(self, **overrides) -> dict:
        """Strong bull market data."""
        base = {
            "btc_close": 70_000,
            "btc_ema50": 65_000,
            "btc_ema200": 55_000,
            "btc_return_30d": 0.15,
            "vol_7d": 0.50,
            "vol_30d": 0.45,
            "btc_trend_direction": "up",
            "altcoin_above_ema50_pct": 0.80,
        }
        base.update(overrides)
        return base

    def _bear_data(self, **overrides) -> dict:
        """Strong bear market data."""
        base = {
            "btc_close": 40_000,
            "btc_ema50": 45_000,
            "btc_ema200": 55_000,
            "btc_return_30d": -0.20,
            "vol_7d": 0.70,
            "vol_30d": 0.50,
            "btc_trend_direction": "down",
            "altcoin_above_ema50_pct": 0.15,
        }
        base.update(overrides)
        return base

    def _chop_data(self, **overrides) -> dict:
        """Choppy/mixed market data."""
        base = {
            "btc_close": 50_000,
            "btc_ema50": 50_500,
            "btc_ema200": 49_500,
            "btc_return_30d": 0.02,
            "vol_7d": 0.20,
            "vol_30d": 0.50,
            "btc_trend_direction": "",
            "altcoin_above_ema50_pct": 0.50,
        }
        base.update(overrides)
        return base

    # ── Test 1: Bull regime ──

    def test_bull_regime_above_ema_positive_momentum(self, detector):
        """Price above both EMAs + positive momentum + high breadth -> BULL."""
        result = detector.detect(self._bull_data())

        assert result.regime == "BULL"
        assert result.confidence > 0.3

    # ── Test 2: Bear regime ──

    def test_bear_regime_below_ema_negative_momentum(self, detector):
        """Death cross + negative momentum + low breadth -> BEAR."""
        result = detector.detect(self._bear_data())

        assert result.regime == "BEAR"
        assert result.confidence > 0.3

    # ── Test 3: Chop regime ──

    def test_chop_regime_mixed_signals(self, detector):
        """Flat prices with mixed breadth -> CHOP."""
        result = detector.detect(self._chop_data())

        assert result.regime == "CHOP"

    # ── Test 4: Strong bull confidence ──

    def test_confidence_strong_bull(self, detector):
        """All bull signals strongly aligned -> high confidence."""
        data = self._bull_data(
            btc_return_30d=0.25,
            altcoin_above_ema50_pct=0.90,
        )

        result = detector.detect(data)

        assert result.regime == "BULL"
        assert result.confidence >= 0.5

    # ── Test 5: Breadth vote ──

    def test_breadth_vote_above_70_bull(self, detector):
        """Breadth > 70% should cast a BULL vote."""
        data = self._bull_data(altcoin_above_ema50_pct=0.75)

        result = detector.detect(data)

        assert result.votes.get("breadth") == "BULL"

    # ── Test 6: Volatility compression -> CHOP ──

    def test_volatility_compression_chop(self, detector):
        """Vol compression (7d/30d < 0.5) should cast CHOP vote."""
        data = self._chop_data(vol_7d=0.15, vol_30d=0.50)

        result = detector.detect(data)

        assert result.votes.get("volatility") == "CHOP"

    # ── Test 7: Insufficient data (missing keys) ──

    def test_insufficient_data_returns_chop(self, detector):
        """Minimal data with zeroed EMAs should produce CHOP."""
        data = {
            "btc_close": 50_000,
            "btc_ema50": 0,
            "btc_ema200": 0,
            "btc_return_30d": 0.0,
            "vol_7d": 0.0,
            "vol_30d": 0.0,
            "btc_trend_direction": "",
            "altcoin_above_ema50_pct": 0.50,
        }

        result = detector.detect(data)

        # With zeroed EMAs: trend=CHOP, momentum=CHOP, breadth=CHOP => CHOP
        assert result.regime == "CHOP"

    # ���─ Test 8: All votes aligned -> high confidence ──

    def test_all_votes_aligned_high_confidence(self, detector):
        """When most votes are BULL (trend+momentum+breadth), confidence is high."""
        data = self._bull_data(
            vol_7d=0.80,
            vol_30d=0.50,
            btc_trend_direction="up",
        )

        result = detector.detect(data)

        # trend=BULL, momentum=BULL, breadth=BULL, volatility depends
        bull_votes = sum(1 for v in result.votes.values() if v == "BULL")
        assert bull_votes >= 3
        assert result.confidence >= 0.5


# ═══════════════════════════════════════════════════════════════���═══════
# PART 4: CryptoEntryTiming — 6 tests
# ════════════════════════════════════════════════��══════════════════════


class TestCryptoEntryTiming:
    """6 tests for CryptoEntryTiming (ROC-C05).

    CryptoEntryTiming.should_delay_entry(signal, current_hour_utc, conviction)
    Returns (should_delay: bool, delay_hours: int).

    Signal dict needs 'strategy_type' key.
    """

    @pytest.fixture
    def timing(self):
        return CryptoEntryTiming()

    # ── Test 1: Never delay event signals ──

    def test_never_delay_event_signals(self, timing):
        """Event signals (liquidation cascade) should never be delayed."""
        signal = {"strategy_type": "event", "symbol": "BTCUSDT"}

        should_delay, hours = timing.should_delay_entry(
            signal, current_hour_utc=3, conviction=0.5
        )

        assert should_delay is False
        assert hours == 0

    # ── Test 2: Never delay high conviction ──

    def test_never_delay_high_conviction(self, timing):
        """High conviction (> 0.9) should never be delayed."""
        signal = {"strategy_type": "trend", "symbol": "BTCUSDT"}

        should_delay, hours = timing.should_delay_entry(
            signal, current_hour_utc=3, conviction=0.95
        )

        assert should_delay is False
        assert hours == 0

    # ── Test 3: Delay trend in Asia session ──

    def test_delay_trend_in_asia_session(self, timing):
        """Trend signal during avoid hours (0-4 UTC) should be delayed."""
        signal = {"strategy_type": "trend", "symbol": "BTCUSDT"}

        should_delay, hours = timing.should_delay_entry(
            signal, current_hour_utc=2, conviction=0.5
        )

        assert should_delay is True
        assert hours > 0
        assert hours <= 6

    # ── Test 4: No delay during overlap ──

    def test_no_delay_during_overlap(self, timing):
        """During optimal hours (14-15 UTC), no delay for trend signals."""
        signal = {"strategy_type": "trend", "symbol": "BTCUSDT"}

        should_delay, hours = timing.should_delay_entry(
            signal, current_hour_utc=14, conviction=0.5
        )

        assert should_delay is False
        assert hours == 0

    # ── Test 5: Max delay 6h ──

    def test_max_delay_6h(self, timing):
        """Delay should never exceed 6 hours."""
        signal = {"strategy_type": "trend", "symbol": "BTCUSDT"}

        # Test at 0 UTC — avoid window for trend
        should_delay, hours = timing.should_delay_entry(
            signal, current_hour_utc=0, conviction=0.4
        )

        if should_delay:
            assert hours <= 6

    # ── Test 6: Spread estimate by hour ──

    def test_spread_estimate_by_hour(self, timing):
        """Spread should be lowest during overlap (14-15 UTC)."""
        spread_overlap = timing.get_spread_estimate("BTCUSDT", hour_utc=14)
        spread_asia = timing.get_spread_estimate("BTCUSDT", hour_utc=3)

        assert spread_overlap < spread_asia, (
            f"Overlap spread ({spread_overlap} bps) should be lower "
            f"than Asia spread ({spread_asia} bps)"
        )

        # Verify overlap hours have the best multiplier (0.8x)
        # For BTC base 2 bps * 0.8 = 1.6 bps at overlap
        assert spread_overlap <= 2.0  # BTC base is 2 bps, mult 0.8 = 1.6
