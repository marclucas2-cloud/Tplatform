"""Tests for SlippageAnalytics -- advanced slippage analytics.

Covers:
  - Per-strategy analysis with recommendations
  - Time-of-day profiling
  - Instrument-type comparison
  - Total dollar cost computation
  - Order-type recommendation engine
  - HFT feeding detection
  - Full report generation
  - Telegram report formatting
  - Empty database handling
"""
import os
import sys
import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.slippage_tracker import SlippageTracker
from core.execution.slippage_analytics import SlippageAnalytics


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temp SQLite db and initialize schema via SlippageTracker."""
    db = tmp_path / "test_execution_metrics.db"
    # Init schema
    SlippageTracker(db_path=db, alert_callback=None)
    return db


@pytest.fixture
def analytics(tmp_db):
    """SlippageAnalytics pointing to the temp db."""
    return SlippageAnalytics(db_path=tmp_db)


@pytest.fixture
def tracker(tmp_db):
    """SlippageTracker for populating test data."""
    return SlippageTracker(db_path=tmp_db, alert_callback=None)


def _insert_trade(db_path, trade_id, strategy, instrument, instrument_type,
                  side, order_type, requested_price, filled_price,
                  slippage_bps, backtest_slippage_bps=2.0,
                  market_spread_bps=None, volume_at_fill=None,
                  hours_ago=0, days_ago=0):
    """Insert a trade with explicit control over all fields and timestamp."""
    ts = (datetime.now(timezone.utc)
          - timedelta(days=days_ago, hours=hours_ago)).isoformat()
    ratio = slippage_bps / backtest_slippage_bps if backtest_slippage_bps > 0 else 0.0
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO slippage_log
               (trade_id, timestamp, strategy, instrument, instrument_type,
                side, order_type, requested_price, filled_price,
                slippage_bps, backtest_slippage_bps, ratio_real_vs_backtest,
                market_spread_bps, volume_at_fill)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, ts, strategy, instrument, instrument_type,
             side, order_type, requested_price, filled_price,
             slippage_bps, backtest_slippage_bps, ratio,
             market_spread_bps, volume_at_fill),
        )
        conn.commit()


def _insert_trade_at_hour(db_path, trade_id, hour_utc, slippage_bps,
                          strategy="test_strat", days_ago=1):
    """Insert a trade at a specific hour of day for time-of-day testing."""
    dt = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(
        hour=hour_utc, minute=30, second=0, microsecond=0)
    ts = dt.isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO slippage_log
               (trade_id, timestamp, strategy, instrument, instrument_type,
                side, order_type, requested_price, filled_price,
                slippage_bps, backtest_slippage_bps, ratio_real_vs_backtest,
                market_spread_bps, volume_at_fill)
               VALUES (?, ?, ?, 'SPY', 'EQUITY', 'BUY', 'MARKET',
                       100.0, ?, ?, 2.0, ?, NULL, NULL)""",
            (trade_id, ts, strategy,
             100.0 + slippage_bps / 10000 * 100.0,
             slippage_bps, slippage_bps / 2.0),
        )
        conn.commit()


def _populate_multi_strategy(db_path):
    """Populate db with trades across multiple strategies and instrument types."""
    # momentum -- equity, moderate slippage
    for i in range(10):
        _insert_trade(db_path, f"MOM{i:03d}", "momentum", "SPY", "EQUITY",
                      "BUY", "MARKET", 450.0, 450.0 + 0.009 * (i + 1),
                      slippage_bps=2.0 + i * 0.5, market_spread_bps=1.5,
                      volume_at_fill=100, days_ago=i)

    # fx_carry -- FX, low slippage
    for i in range(8):
        _insert_trade(db_path, f"FXC{i:03d}", "fx_carry", "EUR/USD", "FX",
                      "BUY", "LIMIT", 1.085, 1.085 + 0.00001 * (i + 1),
                      slippage_bps=0.5 + i * 0.1, market_spread_bps=0.8,
                      volume_at_fill=10000, days_ago=i)

    # crypto_momentum -- crypto, high slippage
    for i in range(12):
        _insert_trade(db_path, f"CRY{i:03d}", "crypto_momentum", "BTCUSDC", "CRYPTO",
                      "BUY", "MARKET", 65000.0, 65000.0 + 6.5 * (i + 1),
                      slippage_bps=5.0 + i * 1.0, market_spread_bps=3.0,
                      volume_at_fill=0.1, days_ago=i)

    # futures_trend -- futures, moderate
    for i in range(6):
        _insert_trade(db_path, f"FUT{i:03d}", "futures_trend", "ES", "FUTURES",
                      "SELL", "STOP", 5200.0, 5200.0 - 0.52 * (i + 1),
                      slippage_bps=1.0 + i * 0.3, market_spread_bps=0.5,
                      volume_at_fill=2, days_ago=i)


# ============================================================================
# TESTS -- Empty database
# ============================================================================

class TestEmptyDatabase:
    """All methods should handle an empty database gracefully."""

    def test_analyze_by_strategy_empty(self, analytics):
        result = analytics.analyze_by_strategy("nonexistent")
        assert result["n_trades"] == 0
        assert result["avg_slippage_bps"] == 0.0
        assert result["recommendation"] == "OK"

    def test_analyze_by_time_of_day_empty(self, analytics):
        result = analytics.analyze_by_time_of_day()
        assert result["worst_hour"] is None
        assert result["best_hour"] is None
        assert all(v["n_trades"] == 0 for v in result["by_hour"].values())

    def test_analyze_by_instrument_type_empty(self, analytics):
        result = analytics.analyze_by_instrument_type()
        assert result["by_type"] == {}

    def test_compute_total_cost_empty(self, analytics):
        result = analytics.compute_total_slippage_cost()
        assert result["total_cost_usd"] == 0.0
        assert result["avg_per_trade_usd"] == 0.0
        assert result["n_adverse_trades"] == 0
        assert result["by_strategy"] == {}

    def test_recommend_order_type_empty(self, analytics):
        result = analytics.recommend_order_type("UNKNOWN", "BUY")
        assert result["order_type"] == "LIMIT"
        assert "No historical data" in result["reason"]

    def test_detect_hft_feeding_empty(self, analytics):
        result = analytics.detect_hft_feeding("nonexistent")
        assert result["is_feeding_hft"] is False
        assert result["n_trades"] == 0

    def test_get_slippage_report_empty(self, analytics):
        report = analytics.get_slippage_report()
        assert report["n_trades"] == 0
        assert report["total_cost_usd"] == 0.0
        assert report["worst_strategy"] is None

    def test_format_telegram_report_empty(self, analytics):
        text = analytics.format_telegram_report()
        assert "SLIPPAGE REPORT" in text
        assert "Trades: 0" in text


# ============================================================================
# TESTS -- Analyze by strategy
# ============================================================================

class TestAnalyzeByStrategy:
    """Tests for analyze_by_strategy."""

    def test_basic_stats(self, analytics, tmp_db):
        for i in range(10):
            _insert_trade(tmp_db, f"S{i:03d}", "momentum", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.0 + 0.001 * (i + 1),
                          slippage_bps=1.0 * (i + 1), days_ago=i)

        result = analytics.analyze_by_strategy("momentum")
        assert result["n_trades"] == 10
        assert result["avg_slippage_bps"] > 0
        assert result["median_slippage_bps"] > 0
        assert result["p95_slippage_bps"] >= result["avg_slippage_bps"]

    def test_recommendation_ok(self, analytics, tmp_db):
        """Low slippage strategy should get OK recommendation."""
        for i in range(10):
            _insert_trade(tmp_db, f"OK{i:03d}", "good_strat", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.002,
                          slippage_bps=0.5, days_ago=i)

        result = analytics.analyze_by_strategy("good_strat")
        assert result["recommendation"] == "OK"

    def test_recommendation_switch_to_limit(self, analytics, tmp_db):
        """High avg slippage should trigger SWITCH_TO_LIMIT."""
        for i in range(10):
            _insert_trade(tmp_db, f"LIM{i:03d}", "bad_strat", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.05,
                          slippage_bps=5.0, days_ago=i)

        result = analytics.analyze_by_strategy("bad_strat")
        assert result["recommendation"] == "SWITCH_TO_LIMIT"

    def test_recommendation_reduce_size(self, analytics, tmp_db):
        """Very high P95 should trigger REDUCE_SIZE."""
        for i in range(10):
            slip = 2.0 if i < 8 else 15.0  # 2 outliers push P95 high
            _insert_trade(tmp_db, f"RS{i:03d}", "volatile_strat", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.0 + slip / 10000 * 100,
                          slippage_bps=slip, days_ago=i)

        result = analytics.analyze_by_strategy("volatile_strat")
        assert result["recommendation"] == "REDUCE_SIZE"

    def test_insufficient_trades(self, analytics, tmp_db):
        """With < MIN_TRADES trades, recommendation should be OK (not enough data)."""
        for i in range(3):
            _insert_trade(tmp_db, f"FEW{i:03d}", "few_trades", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.10,
                          slippage_bps=10.0, days_ago=i)

        result = analytics.analyze_by_strategy("few_trades")
        assert result["n_trades"] == 3
        assert result["recommendation"] == "OK"  # Not enough data to recommend

    def test_lookback_filter(self, analytics, tmp_db):
        """Only trades within lookback period should be included."""
        # Recent
        _insert_trade(tmp_db, "REC001", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, days_ago=5)
        # Old (outside 7-day lookback)
        _insert_trade(tmp_db, "OLD001", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.10,
                      slippage_bps=10.0, days_ago=40)

        result = analytics.analyze_by_strategy("strat", lookback_days=7)
        assert result["n_trades"] == 1
        assert abs(result["avg_slippage_bps"] - 2.0) < 0.01

    def test_cost_vs_profit_pct(self, analytics, tmp_db):
        """cost_vs_profit_pct calculated from adverse vs backtest."""
        for i in range(6):
            _insert_trade(tmp_db, f"CVP{i:03d}", "strat_cvp", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.04,
                          slippage_bps=4.0, backtest_slippage_bps=2.0, days_ago=i)

        result = analytics.analyze_by_strategy("strat_cvp")
        # total_adverse = 6 * 4.0 = 24.0, total_backtest = 6 * 2.0 = 12.0
        # cost_vs_profit_pct = 24/12 * 100 = 200%
        assert result["cost_vs_profit_pct"] == 200.0


# ============================================================================
# TESTS -- Analyze by time of day
# ============================================================================

class TestAnalyzeByTimeOfDay:
    """Tests for analyze_by_time_of_day."""

    def test_basic_time_profiling(self, analytics, tmp_db):
        """Trades at different hours should be grouped correctly."""
        _insert_trade_at_hour(tmp_db, "H09_1", 9, 1.0)
        _insert_trade_at_hour(tmp_db, "H09_2", 9, 2.0)
        _insert_trade_at_hour(tmp_db, "H14_1", 14, 8.0)
        _insert_trade_at_hour(tmp_db, "H14_2", 14, 10.0)
        _insert_trade_at_hour(tmp_db, "H20_1", 20, 0.5)

        result = analytics.analyze_by_time_of_day()

        assert result["by_hour"][9]["n_trades"] == 2
        assert abs(result["by_hour"][9]["avg_bps"] - 1.5) < 0.01
        assert result["by_hour"][14]["n_trades"] == 2
        assert abs(result["by_hour"][14]["avg_bps"] - 9.0) < 0.01
        assert result["worst_hour"] == 14
        assert result["best_hour"] == 20

    def test_all_24_hours_present(self, analytics, tmp_db):
        """Result should contain all 24 hours even with no data."""
        _insert_trade_at_hour(tmp_db, "H12_1", 12, 2.0)
        result = analytics.analyze_by_time_of_day()
        assert len(result["by_hour"]) == 24
        # Hour 0 should have 0 trades
        assert result["by_hour"][0]["n_trades"] == 0

    def test_worst_and_best_hour(self, analytics, tmp_db):
        """Worst and best hours correctly identified."""
        _insert_trade_at_hour(tmp_db, "BEST", 3, -1.0)  # Favorable
        _insert_trade_at_hour(tmp_db, "WORST", 15, 12.0)

        result = analytics.analyze_by_time_of_day()
        assert result["worst_hour"] == 15
        assert result["best_hour"] == 3


# ============================================================================
# TESTS -- Analyze by instrument type
# ============================================================================

class TestAnalyzeByInstrumentType:
    """Tests for analyze_by_instrument_type."""

    def test_multi_type_grouping(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        result = analytics.analyze_by_instrument_type()

        assert "EQUITY" in result["by_type"]
        assert "FX" in result["by_type"]
        assert "CRYPTO" in result["by_type"]
        assert "FUTURES" in result["by_type"]

    def test_correct_trade_counts(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        result = analytics.analyze_by_instrument_type()

        assert result["by_type"]["EQUITY"]["n_trades"] == 10
        assert result["by_type"]["FX"]["n_trades"] == 8
        assert result["by_type"]["CRYPTO"]["n_trades"] == 12
        assert result["by_type"]["FUTURES"]["n_trades"] == 6

    def test_crypto_higher_slippage(self, analytics, tmp_db):
        """Crypto should have higher avg slippage than FX in our test data."""
        _populate_multi_strategy(tmp_db)
        result = analytics.analyze_by_instrument_type()

        crypto_avg = result["by_type"]["CRYPTO"]["avg_slippage_bps"]
        fx_avg = result["by_type"]["FX"]["avg_slippage_bps"]
        assert crypto_avg > fx_avg

    def test_stats_include_all_fields(self, analytics, tmp_db):
        _insert_trade(tmp_db, "TS001", "s", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02, slippage_bps=2.0)
        result = analytics.analyze_by_instrument_type()
        equity_stats = result["by_type"]["EQUITY"]
        assert "avg_slippage_bps" in equity_stats
        assert "median_slippage_bps" in equity_stats
        assert "p95_slippage_bps" in equity_stats
        assert "n_trades" in equity_stats


# ============================================================================
# TESTS -- Compute total slippage cost
# ============================================================================

class TestComputeTotalSlippageCost:
    """Tests for compute_total_slippage_cost."""

    def test_basic_cost_calculation(self, analytics, tmp_db):
        """Verify dollar cost: bps / 10000 * price * qty."""
        # 2 bps on $100 * 100 shares = $0.02 * 100 = $2.00
        _insert_trade(tmp_db, "COST001", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, volume_at_fill=100)

        result = analytics.compute_total_slippage_cost()
        assert abs(result["total_cost_usd"] - 2.0) < 0.01
        assert result["n_adverse_trades"] == 1

    def test_favorable_slippage_excluded(self, analytics, tmp_db):
        """Favorable (negative) slippage should not count toward cost."""
        _insert_trade(tmp_db, "FAV001", "strat", "SPY", "EQUITY",
                      "BUY", "LIMIT", 100.0, 99.98,
                      slippage_bps=-2.0, volume_at_fill=100)

        result = analytics.compute_total_slippage_cost()
        assert result["total_cost_usd"] == 0.0
        assert result["n_adverse_trades"] == 0

    def test_cost_by_strategy(self, analytics, tmp_db):
        """Cost should be broken down by strategy."""
        _insert_trade(tmp_db, "CBS001", "alpha", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, volume_at_fill=50)
        _insert_trade(tmp_db, "CBS002", "beta", "QQQ", "EQUITY",
                      "BUY", "MARKET", 200.0, 200.06,
                      slippage_bps=3.0, volume_at_fill=20)

        result = analytics.compute_total_slippage_cost()
        assert "alpha" in result["by_strategy"]
        assert "beta" in result["by_strategy"]
        # alpha: 2.0/10000 * 100 * 50 = $1.00
        assert abs(result["by_strategy"]["alpha"] - 1.0) < 0.01
        # beta: 3.0/10000 * 200 * 20 = $1.20
        assert abs(result["by_strategy"]["beta"] - 1.20) < 0.01

    def test_no_volume_defaults_to_one(self, analytics, tmp_db):
        """If volume_at_fill is NULL, default quantity is 1."""
        _insert_trade(tmp_db, "NV001", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, volume_at_fill=None)

        result = analytics.compute_total_slippage_cost()
        # 2.0/10000 * 100 * 1 = $0.02
        assert abs(result["total_cost_usd"] - 0.02) < 0.001

    def test_avg_per_trade(self, analytics, tmp_db):
        """Average cost per adverse trade."""
        _insert_trade(tmp_db, "APT001", "s", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, volume_at_fill=100)
        _insert_trade(tmp_db, "APT002", "s", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.04,
                      slippage_bps=4.0, volume_at_fill=100)

        result = analytics.compute_total_slippage_cost()
        # Total: $2.00 + $4.00 = $6.00, avg = $3.00
        assert abs(result["avg_per_trade_usd"] - 3.0) < 0.01

    def test_pct_of_pnl(self, analytics, tmp_db):
        """pct_of_pnl = actual_cost / expected_cost * 100."""
        # actual: 4 bps, backtest: 2 bps -> 200%
        _insert_trade(tmp_db, "PNL001", "s", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.04,
                      slippage_bps=4.0, backtest_slippage_bps=2.0,
                      volume_at_fill=100)

        result = analytics.compute_total_slippage_cost()
        assert abs(result["pct_of_pnl"] - 200.0) < 0.01


# ============================================================================
# TESTS -- Recommend order type
# ============================================================================

class TestRecommendOrderType:
    """Tests for recommend_order_type."""

    def test_no_data_normal_urgency(self, analytics):
        """No data + NORMAL urgency -> LIMIT with default offset."""
        result = analytics.recommend_order_type("UNKNOWN", "BUY", "NORMAL")
        assert result["order_type"] == "LIMIT"
        assert result["limit_offset_bps"] > 0
        assert "No historical data" in result["reason"]

    def test_no_data_high_urgency(self, analytics):
        """No data + HIGH urgency -> MARKET."""
        result = analytics.recommend_order_type("UNKNOWN", "BUY", "HIGH")
        assert result["order_type"] == "MARKET"
        assert result["limit_offset_bps"] == 0.0

    def test_high_slippage_vs_spread_pegged_mid(self, analytics, tmp_db):
        """When avg slippage > 2x spread -> PEGGED_MID."""
        for i in range(10):
            _insert_trade(tmp_db, f"PM{i:03d}", "strat", "AAPL", "EQUITY",
                          "BUY", "MARKET", 180.0, 180.036,
                          slippage_bps=5.0, market_spread_bps=1.5,
                          volume_at_fill=100)

        result = analytics.recommend_order_type("AAPL", "BUY")
        assert result["order_type"] == "PEGGED_MID"
        assert result["limit_offset_bps"] > 0

    def test_liquid_high_urgency_market(self, analytics, tmp_db):
        """Liquid ticker + HIGH urgency -> MARKET."""
        for i in range(10):
            _insert_trade(tmp_db, f"LQ{i:03d}", "strat", "SPY", "EQUITY",
                          "BUY", "MARKET", 450.0, 450.009,
                          slippage_bps=0.2, market_spread_bps=0.3,
                          volume_at_fill=50000)

        result = analytics.recommend_order_type("SPY", "BUY", "HIGH")
        assert result["order_type"] == "MARKET"

    def test_elevated_slippage_limit(self, analytics, tmp_db):
        """Elevated avg slippage without spread data -> LIMIT."""
        for i in range(10):
            _insert_trade(tmp_db, f"EL{i:03d}", "strat", "TSLA", "EQUITY",
                          "BUY", "MARKET", 200.0, 200.08,
                          slippage_bps=4.0, market_spread_bps=None,
                          volume_at_fill=500)

        result = analytics.recommend_order_type("TSLA", "BUY")
        assert result["order_type"] == "LIMIT"
        assert result["limit_offset_bps"] > 0

    def test_low_slippage_normal_urgency_limit(self, analytics, tmp_db):
        """Low slippage + NORMAL urgency -> LIMIT (conservative)."""
        for i in range(10):
            _insert_trade(tmp_db, f"LS{i:03d}", "strat", "QQQ", "EQUITY",
                          "BUY", "MARKET", 380.0, 380.0038,
                          slippage_bps=0.1, market_spread_bps=0.5,
                          volume_at_fill=500)

        result = analytics.recommend_order_type("QQQ", "BUY", "NORMAL")
        assert result["order_type"] == "LIMIT"

    def test_low_slippage_high_urgency_market(self, analytics, tmp_db):
        """Low slippage + HIGH urgency -> MARKET."""
        for i in range(10):
            _insert_trade(tmp_db, f"LH{i:03d}", "strat", "QQQ", "EQUITY",
                          "BUY", "MARKET", 380.0, 380.0038,
                          slippage_bps=0.1, market_spread_bps=0.5,
                          volume_at_fill=500)

        result = analytics.recommend_order_type("QQQ", "BUY", "HIGH")
        assert result["order_type"] == "MARKET"


# ============================================================================
# TESTS -- Detect HFT feeding
# ============================================================================

class TestDetectHFTFeeding:
    """Tests for detect_hft_feeding."""

    def test_no_data(self, analytics):
        result = analytics.detect_hft_feeding("nonexistent")
        assert result["is_feeding_hft"] is False
        assert result["n_trades"] == 0

    def test_insufficient_spread_data(self, analytics, tmp_db):
        """Trades without spread data -> cannot detect HFT."""
        for i in range(10):
            _insert_trade(tmp_db, f"NS{i:03d}", "strat_no_spread", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.05,
                          slippage_bps=5.0, market_spread_bps=None)

        result = analytics.detect_hft_feeding("strat_no_spread")
        assert result["is_feeding_hft"] is False
        assert "Insufficient spread data" in result["recommendation"]

    def test_feeding_hft_detected(self, analytics, tmp_db):
        """Avg slippage > spread should flag HFT feeding."""
        for i in range(10):
            _insert_trade(tmp_db, f"HFT{i:03d}", "hft_victim", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.05,
                          slippage_bps=5.0, market_spread_bps=1.5)

        result = analytics.detect_hft_feeding("hft_victim")
        assert result["is_feeding_hft"] is True
        assert result["avg_slippage_vs_spread"] > 1.0
        assert "WARNING" in result["recommendation"] or "CRITICAL" in result["recommendation"]

    def test_critical_hft_feeding(self, analytics, tmp_db):
        """Extreme HFT feeding (> 3x spread) -> CRITICAL."""
        for i in range(10):
            _insert_trade(tmp_db, f"CHFT{i:03d}", "hft_critical", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.10,
                          slippage_bps=10.0, market_spread_bps=1.0)

        result = analytics.detect_hft_feeding("hft_critical")
        assert result["is_feeding_hft"] is True
        assert result["avg_slippage_vs_spread"] > 3.0
        assert "CRITICAL" in result["recommendation"]

    def test_no_hft_feeding(self, analytics, tmp_db):
        """Avg slippage < spread -> no HFT concern."""
        for i in range(10):
            _insert_trade(tmp_db, f"SAFE{i:03d}", "safe_strat", "SPY", "EQUITY",
                          "BUY", "MARKET", 100.0, 100.005,
                          slippage_bps=0.5, market_spread_bps=1.5)

        result = analytics.detect_hft_feeding("safe_strat")
        assert result["is_feeding_hft"] is False
        assert result["avg_slippage_vs_spread"] < 1.0
        assert "within normal range" in result["recommendation"]


# ============================================================================
# TESTS -- Full slippage report
# ============================================================================

class TestGetSlippageReport:
    """Tests for get_slippage_report."""

    def test_report_structure(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        report = analytics.get_slippage_report()

        assert "lookback_days" in report
        assert "total_cost_usd" in report
        assert "pct_of_pnl" in report
        assert "n_trades" in report
        assert "worst_strategy" in report
        assert "worst_strategy_avg_bps" in report
        assert "worst_hour" in report
        assert "best_hour" in report
        assert "by_strategy" in report
        assert "by_instrument_type" in report
        assert "cost_by_strategy" in report
        assert "action_items" in report

    def test_report_trade_count(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        report = analytics.get_slippage_report()
        assert report["n_trades"] == 36  # 10 + 8 + 12 + 6

    def test_report_worst_strategy(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        report = analytics.get_slippage_report()
        # crypto_momentum has the highest avg slippage in our test data
        assert report["worst_strategy"] == "crypto_momentum"

    def test_report_has_all_strategies(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        report = analytics.get_slippage_report()
        assert "momentum" in report["by_strategy"]
        assert "fx_carry" in report["by_strategy"]
        assert "crypto_momentum" in report["by_strategy"]
        assert "futures_trend" in report["by_strategy"]

    def test_report_has_all_instrument_types(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        report = analytics.get_slippage_report()
        assert "EQUITY" in report["by_instrument_type"]
        assert "FX" in report["by_instrument_type"]
        assert "CRYPTO" in report["by_instrument_type"]
        assert "FUTURES" in report["by_instrument_type"]

    def test_report_action_items(self, analytics, tmp_db):
        """High-slippage strategies should generate action items."""
        _populate_multi_strategy(tmp_db)
        report = analytics.get_slippage_report()
        # crypto_momentum should have a recommendation
        assert len(report["action_items"]) > 0


# ============================================================================
# TESTS -- Telegram report format
# ============================================================================

class TestTelegramReport:
    """Tests for format_telegram_report."""

    def test_telegram_header(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        text = analytics.format_telegram_report()
        assert "SLIPPAGE REPORT (30d)" in text

    def test_telegram_total_cost(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        text = analytics.format_telegram_report()
        assert "Total cost: $" in text

    def test_telegram_worst_strategy(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        text = analytics.format_telegram_report()
        assert "Worst strategy:" in text

    def test_telegram_by_type(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        text = analytics.format_telegram_report()
        assert "--- By type ---" in text
        assert "EQUITY" in text
        assert "CRYPTO" in text

    def test_telegram_custom_lookback(self, analytics, tmp_db):
        _populate_multi_strategy(tmp_db)
        text = analytics.format_telegram_report(lookback_days=7)
        assert "SLIPPAGE REPORT (7d)" in text


# ============================================================================
# TESTS -- Integration with SlippageTracker
# ============================================================================

class TestIntegrationWithTracker:
    """Verify analytics reads data written by SlippageTracker.record_fill."""

    def test_read_tracker_data(self, tracker, analytics):
        """Analytics should read trades recorded by SlippageTracker."""
        tracker.record_fill(
            trade_id="INT001",
            strategy="integrated_strat",
            instrument="AAPL",
            instrument_type="EQUITY",
            side="BUY",
            order_type="MARKET",
            requested_price=180.0,
            filled_price=180.036,
            market_spread_bps=1.5,
            volume_at_fill=50,
        )

        result = analytics.analyze_by_strategy("integrated_strat")
        assert result["n_trades"] == 1
        assert result["avg_slippage_bps"] == 2.0

    def test_multi_trade_integration(self, tracker, analytics):
        """Multiple tracker records visible to analytics."""
        for i in range(5):
            tracker.record_fill(
                trade_id=f"MULTI{i:03d}",
                strategy="multi_strat",
                instrument="SPY",
                instrument_type="EQUITY",
                side="BUY",
                order_type="MARKET",
                requested_price=450.0,
                filled_price=450.0 + 0.045 * (i + 1),
                market_spread_bps=1.0,
                volume_at_fill=100,
            )

        cost = analytics.compute_total_slippage_cost()
        assert cost["n_adverse_trades"] == 5
        assert cost["total_cost_usd"] > 0
        assert "multi_strat" in cost["by_strategy"]

    def test_shared_db_path(self, tracker, analytics, tmp_db):
        """Tracker and analytics use the same DB file."""
        assert str(tracker.db_path) == str(analytics.db_path)


# ============================================================================
# TESTS -- Edge cases
# ============================================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_single_trade(self, analytics, tmp_db):
        """All methods work with exactly one trade."""
        _insert_trade(tmp_db, "SOLO001", "solo", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, market_spread_bps=1.0,
                      volume_at_fill=100)

        assert analytics.analyze_by_strategy("solo")["n_trades"] == 1
        assert analytics.analyze_by_time_of_day()["worst_hour"] is not None
        assert "EQUITY" in analytics.analyze_by_instrument_type()["by_type"]
        assert analytics.compute_total_slippage_cost()["n_adverse_trades"] == 1

    def test_zero_slippage_trades(self, analytics, tmp_db):
        """Trades with 0 bps slippage."""
        for i in range(5):
            _insert_trade(tmp_db, f"ZERO{i:03d}", "zero_slip", "SPY", "EQUITY",
                          "BUY", "LIMIT", 100.0, 100.0,
                          slippage_bps=0.0, volume_at_fill=100)

        result = analytics.analyze_by_strategy("zero_slip")
        assert result["avg_slippage_bps"] == 0.0
        assert result["recommendation"] == "OK"

        cost = analytics.compute_total_slippage_cost()
        assert cost["total_cost_usd"] == 0.0

    def test_negative_slippage_only(self, analytics, tmp_db):
        """All favorable slippage -> zero cost."""
        for i in range(5):
            _insert_trade(tmp_db, f"NEG{i:03d}", "favorable", "SPY", "EQUITY",
                          "BUY", "LIMIT", 100.0, 99.98,
                          slippage_bps=-2.0, volume_at_fill=100)

        cost = analytics.compute_total_slippage_cost()
        assert cost["total_cost_usd"] == 0.0
        assert cost["n_adverse_trades"] == 0

    def test_mixed_instruments_same_strategy(self, analytics, tmp_db):
        """Strategy trading multiple instruments."""
        _insert_trade(tmp_db, "MIX001", "multi_asset", "SPY", "EQUITY",
                      "BUY", "MARKET", 450.0, 450.09,
                      slippage_bps=2.0)
        _insert_trade(tmp_db, "MIX002", "multi_asset", "EUR/USD", "FX",
                      "BUY", "MARKET", 1.085, 1.08505,
                      slippage_bps=0.5)

        result = analytics.analyze_by_strategy("multi_asset")
        assert result["n_trades"] == 2

    def test_lookback_zero_days(self, analytics, tmp_db):
        """lookback_days=0 should return nothing (or only very recent trades)."""
        _insert_trade(tmp_db, "LB0001", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, days_ago=1)

        result = analytics.analyze_by_strategy("strat", lookback_days=0)
        assert result["n_trades"] == 0

    def test_large_lookback(self, analytics, tmp_db):
        """lookback_days=365 should include all trades."""
        _insert_trade(tmp_db, "LB365_1", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.02,
                      slippage_bps=2.0, days_ago=300)
        _insert_trade(tmp_db, "LB365_2", "strat", "SPY", "EQUITY",
                      "BUY", "MARKET", 100.0, 100.04,
                      slippage_bps=4.0, days_ago=1)

        result = analytics.analyze_by_strategy("strat", lookback_days=365)
        assert result["n_trades"] == 2
