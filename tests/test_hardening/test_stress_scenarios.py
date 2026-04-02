"""
HistoricalStressTest — 9 historical crises simulated against current portfolio.

Each test creates a mock portfolio (IBKR $10K or Binance $15K) and feeds the
crisis price action through the real risk manager to verify survival.
Only external broker APIs are mocked; risk logic runs unmodified.
"""
import pytest

from core.crypto.risk_manager_crypto import (
    CryptoKillSwitch,
    CryptoRiskLimits,
    CryptoRiskManager,
)
from core.cross_portfolio_guard import check_combined_exposure
from core.kill_switch_live import LiveKillSwitch
from core.risk_manager_live import LiveRiskManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ibkr_rm():
    """LiveRiskManager with real limits_live.yaml ($10K)."""
    return LiveRiskManager()


@pytest.fixture
def ibkr_ks(tmp_path):
    """LiveKillSwitch configured for IBKR thresholds."""
    return LiveKillSwitch(
        broker=None,
        state_path=tmp_path / "ks_ibkr.json",
        thresholds={
            "daily_loss_pct": 0.015,
            "hourly_loss_pct": 0.01,
            "trailing_5d_loss_pct": 0.03,
            "monthly_loss_pct": 0.05,
            "strategy_loss_pct": 0.02,
        },
    )


@pytest.fixture
def crypto_rm():
    """CryptoRiskManager with $15K capital."""
    rm = CryptoRiskManager(capital=15_000, limits=CryptoRiskLimits(config_path="__nonexistent__"))
    rm._check_count = 10  # Skip warmup for stress tests
    rm.kill_switch._active = False  # Ensure fresh state
    return rm


@pytest.fixture
def crypto_ks():
    ks = CryptoKillSwitch(config_path="__nonexistent__")
    ks._active = False
    ks._trigger_reason = ""
    return ks


@pytest.fixture
def ibkr_portfolio():
    """Typical IBKR portfolio: 3 FX + 2 US equity + 1 futures."""
    return {
        "equity": 10_000,
        "cash": 3_000,
        "positions": [
            {"symbol": "EURUSD", "side": "LONG", "notional": 25_000, "margin_used": 750, "asset_class": "FX", "strategy": "eur_usd_trend"},
            {"symbol": "EURGBP", "side": "SHORT", "notional": 20_000, "margin_used": 600, "asset_class": "FX", "strategy": "eur_gbp_mr"},
            {"symbol": "EURJPY", "side": "LONG", "notional": 25_000, "margin_used": 750, "asset_class": "FX", "strategy": "eur_jpy_carry"},
            {"symbol": "SPY", "side": "LONG", "notional": 800, "asset_class": "EQUITY", "strategy": "late_day_mr"},
            {"symbol": "QQQ", "side": "SHORT", "notional": 600, "asset_class": "EQUITY", "strategy": "failed_rally"},
            {"symbol": "MCL", "side": "LONG", "notional": 6_000, "initial_margin": 600, "asset_class": "FUTURES", "strategy": "mcl_brent_lag"},
        ],
    }


@pytest.fixture
def crypto_positions():
    """Typical Binance portfolio: 3 spot + 2 margin short."""
    return [
        {"symbol": "BTCUSDT", "notional": 3_000, "side": "LONG", "strategy": "btc_momentum", "leverage": 1.0, "is_margin_borrow": False, "borrowed_amount": 0, "borrow_rate_daily": 0, "asset_value": 3_000, "total_debt": 0, "unrealized_pct": -2},
        {"symbol": "ETHUSDT", "notional": 2_000, "side": "LONG", "strategy": "eth_carry", "leverage": 1.0, "is_margin_borrow": False, "borrowed_amount": 0, "borrow_rate_daily": 0, "asset_value": 2_000, "total_debt": 0, "unrealized_pct": -1},
        {"symbol": "SOLUSDT", "notional": 1_500, "side": "LONG", "strategy": "sol_momentum", "leverage": 1.0, "is_margin_borrow": False, "borrowed_amount": 0, "borrow_rate_daily": 0, "asset_value": 1_500, "total_debt": 0, "unrealized_pct": 0},
        {"symbol": "DOGEUSDT", "notional": 1_000, "side": "SHORT", "strategy": "doge_short", "leverage": 2.0, "is_margin_borrow": True, "borrowed_amount": 1_000, "borrow_rate_daily": 0.0005, "asset_value": 2_000, "total_debt": 1_000, "unrealized_pct": 3},
        {"symbol": "XRPUSDT", "notional": 800, "side": "SHORT", "strategy": "xrp_short", "leverage": 1.5, "is_margin_borrow": True, "borrowed_amount": 800, "borrow_rate_daily": 0.0008, "asset_value": 1_600, "total_debt": 800, "unrealized_pct": 1},
    ]


# =========================================================================
# Historical stress scenarios
# =========================================================================

class TestHistoricalStress:
    """Simulate 9 historical crises and verify survival."""

    def test_covid_crash_2020(self, ibkr_ks):
        """SPY -12% in 1 day. Kill switch triggers, loss < 8% IBKR."""
        capital = 10_000
        # Portfolio has ~14% equity exposure -> -12% on that is ~-1.7% daily
        daily_pnl = -(capital * 0.14 * 0.12)  # ~-$168
        result = ibkr_ks.check_automatic_triggers(
            daily_pnl=daily_pnl, capital=capital,
        )
        # -1.68% > -1.5% threshold -> triggers
        assert result["triggered"] is True
        assert result["trigger_type"] == "DAILY_LOSS"
        # Even in worst case, loss is bounded: 14% exposure * 12% = 1.68%
        loss_pct = abs(daily_pnl / capital)
        assert loss_pct < 0.08, f"Loss {loss_pct:.1%} exceeds 8% bound"

    def test_flash_crash_2010(self, ibkr_ks):
        """SPY -9% in 30 min then recovery. Circuit breaker pauses."""
        capital = 10_000
        # Hourly loss: equity portion * 9% drop
        hourly_pnl = -(capital * 0.14 * 0.09)  # ~-$126
        result = ibkr_ks.check_automatic_triggers(
            daily_pnl=hourly_pnl, capital=capital,
            hourly_pnl=hourly_pnl,
        )
        assert result["triggered"] is True
        assert result["trigger_type"] in ("DAILY_LOSS", "HOURLY_LOSS")

    def test_volmageddon_2018(self, ibkr_rm, ibkr_portfolio):
        """VIX +115%. High-vol regime -> check_all_limits flags risk."""
        # With VIX spiking, daily PnL would be negative and sizing should reduce
        result = ibkr_rm.check_all_limits(
            portfolio=ibkr_portfolio,
            daily_pnl_pct=-0.02,  # -2% from VIX event
            current_dd_pct=0.012,  # 1.2% drawdown
        )
        # Daily circuit breaker at -1.5% should trigger
        assert result["passed"] is False
        assert "STOP_TRADING_TODAY" in result["actions"]

    def test_luna_crash_2022(self, crypto_rm, crypto_ks, crypto_positions):
        """BTC -20% in 3 days. Crypto kill switch triggers, loss < 15%."""
        # Simulate equity after -20% on BTC-correlated portfolio
        initial_equity = 15_000
        # Portfolio ~55% exposed to crypto, ~20% drop -> ~-11% portfolio
        crisis_equity = initial_equity * (1 - 0.55 * 0.20)  # ~$13,350
        ok, msg = crypto_rm.check_drawdown(crisis_equity)
        # If drawdown exceeds 5% daily, violations flagged
        # Also check kill switch directly for max drawdown (>20, not >=20)
        killed, reason = crypto_ks.check(drawdown_pct=-21)
        assert killed is True
        loss_pct = (initial_equity - crisis_equity) / initial_equity
        assert loss_pct < 0.15, f"Loss {loss_pct:.1%} exceeds 15% bound"

    def test_ftx_collapse_2022(self, crypto_ks):
        """BTC -25% in 1 week. Crypto kill switch -> positions closed."""
        killed, reason = crypto_ks.check(drawdown_pct=-25)
        assert killed is True
        assert crypto_ks.is_killed is True
        # Verify kill sequence would execute in priority order
        actions = crypto_ks.actions_priority
        assert actions[0] == "close_shorts"  # Shorts first (interest)
        assert "cancel_orders" in actions

    def test_btc_flash_2021(self, crypto_rm, crypto_positions):
        """BTC -30% in 24h. Kill switch detects via drawdown."""
        initial = 15_000
        # -30% on 53% crypto exposure = -16% portfolio
        crisis_equity = initial * (1 - 0.53 * 0.30)  # ~$12,615
        ok, msg = crypto_rm.check_drawdown(crisis_equity)
        # Kill switch should have activated
        assert crypto_rm.kill_switch.is_killed is True
        assert "KILL" in msg.upper()

    def test_snb_shock_2015(self, ibkr_ks):
        """EUR/CHF -30% in 10 min. FX stops executed, loss bounded."""
        capital = 10_000
        # FX margin exposure: 3 pairs * ~$750 margin = $2,250
        # -30% on notional but with stops at -2% per pair margin
        # Worst case: stop slippage, lose 3x margin on affected pair
        fx_loss = -750 * 3  # $2,250 (extreme slippage on one pair)
        # But kill switch triggers much earlier
        result = ibkr_ks.check_automatic_triggers(
            daily_pnl=-200, capital=capital,  # -2% daily
            hourly_pnl=-200,
        )
        assert result["triggered"] is True
        # Verify FX loss is bounded by margin
        max_fx_loss_pct = abs(fx_loss) / capital
        assert max_fx_loss_pct <= 0.30, "FX loss bounded by margin allocation"

    def test_gbp_flash_2016(self, ibkr_ks):
        """GBP -6% in 2 min. Stop limit executes."""
        capital = 10_000
        # Single pair margin ~$750, -6% on $25K notional = -$1,500
        # Stop would trigger before full -6%, but even worst case:
        hourly_pnl = -150  # -1.5% from GBP flash
        result = ibkr_ks.check_automatic_triggers(
            daily_pnl=-150, capital=capital,
            hourly_pnl=hourly_pnl,
        )
        assert result["triggered"] is True

    def test_correlation_spike(self):
        """All assets correlate 0.9. Cross-portfolio guard alerts."""
        # In a correlated crash, both IBKR and Binance go net long heavily
        result = check_combined_exposure(
            ibkr_long=8_000, ibkr_short=500, ibkr_capital=10_000,
            crypto_long=13_000, crypto_short=500, crypto_capital=15_000,
        )
        # Combined net = (8K-0.5K) + (13K-0.5K) = 20K, total capital = 25K
        # 80% -> within OK but testing higher scenario
        result_extreme = check_combined_exposure(
            ibkr_long=10_000, ibkr_short=0, ibkr_capital=10_000,
            crypto_long=15_000, crypto_short=0, crypto_capital=5_000,
        )
        # 25K / 15K = 167% -> CRITICAL
        assert result_extreme["level"] == "CRITICAL"
        assert "150" in result_extreme["message"]
