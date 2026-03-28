"""
Tests for HARDEN-001 — FX margin vs notional + futures margin checks.

Covers:
  1. FX order accepted if margin < 40% AND notional < 1500%
  2. FX order rejected if margin OK but notional > 1500%
  3. FX order rejected if notional OK but margin > 40%
  4. Equity order not affected by FX/futures limits
  5. Futures order accepted if margin < 35%
  6. Futures order rejected if margin > 35%
  7. Futures order rejected if contract not whitelisted
  8. Mix FX + equity + futures: combined margin < 80%
  9. Mix FX + futures: cash > 20%
  10. 4 FX + 2 MCL + 1 MES: all checks pass
  11. Position that would breach min cash -> rejected
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

# Relaxed position/strategy limits so the NEW FX/futures/combined checks
# are the binding constraints, not the legacy per-position ones.
LIVE_LIMITS_YAML = """
capital: 10000
mode: LIVE

position_limits:
  max_position_pct: 0.25
  max_strategy_pct: 0.50
  max_long_pct: 0.80
  max_short_pct: 0.40
  max_gross_pct: 1.20
  max_positions: 15
  min_cash_pct: 0.05

margin_limits:
  max_margin_used_pct: 0.70
  block_margin_pct: 0.85

circuit_breakers:
  daily_loss_pct: 0.015
  hourly_loss_pct: 0.01
  weekly_loss_pct: 0.03

kill_switch:
  trailing_5d_loss_pct: 0.03
  max_monthly_loss_pct: 0.05

deleveraging:
  level_1_dd_pct: 0.01
  level_1_action: 0.30
  level_2_dd_pct: 0.015
  level_2_action: 0.50
  level_3_dd_pct: 0.02
  level_3_action: 1.00

sector_limits:
  max_sector_pct: 0.50
  max_fx_pair_pct: 0.25
  max_futures_instrument_pct: 0.25

fx_limits:
  max_fx_notional_pct: 15.0
  max_fx_margin_pct: 0.40
  max_single_pair_notional: 40000
  max_single_pair_margin_pct: 0.15

futures_limits:
  max_futures_margin_pct: 0.35
  max_single_contract_margin_pct: 0.20
  allowed_contracts: [MCL, MES]
  max_contracts_per_symbol: 2

combined_limits:
  max_total_margin_pct: 0.80
  min_cash_pct: 0.20

sector_map:
  tech: [AAPL, MSFT]
  futures_index: [MES]
  futures_energy: [MCL]
"""


@pytest.fixture(autouse=True)
def env_setup():
    """Ensure clean env for all tests."""
    with patch.dict(os.environ, {
        "PAPER_TRADING": "true",
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }):
        yield


@pytest.fixture
def live_rm(tmp_path):
    """LiveRiskManager with temp config including FX/futures limits."""
    config_file = tmp_path / "limits_live.yaml"
    config_file.write_text(LIVE_LIMITS_YAML)
    from core.risk_manager_live import LiveRiskManager
    return LiveRiskManager(limits_path=config_file)


@pytest.fixture
def empty_portfolio():
    """$10K portfolio with no positions, full cash."""
    return {
        "equity": 10_000.0,
        "cash": 10_000.0,
        "positions": [],
        "margin_used_pct": 0.0,
    }


@pytest.fixture
def portfolio_with_fx():
    """Portfolio with 3 existing FX positions (margin-based).

    Each pair uses a unique strategy to avoid tripping strategy_limit.
    Total FX margin = 3 x $750 = $2,250 (22.5% of equity).
    Total FX notional = 3 x $25K = $75K (750% of equity).
    """
    return {
        "equity": 10_000.0,
        "cash": 7_750.0,
        "positions": [
            {
                "symbol": "EURUSD", "notional": 25_000, "margin_used": 750,
                "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
            },
            {
                "symbol": "GBPUSD", "notional": 25_000, "margin_used": 750,
                "side": "LONG", "strategy": "fx_carry_gbp", "asset_class": "FX",
            },
            {
                "symbol": "AUDJPY", "notional": 25_000, "margin_used": 750,
                "side": "SHORT", "strategy": "fx_carry_aud", "asset_class": "FX",
            },
        ],
        "margin_used_pct": 0.225,
    }


@pytest.fixture
def portfolio_with_futures():
    """Portfolio with 1 MCL and 1 MES futures position.

    Each uses a unique strategy. Total margin = $600 + $1400 = $2000 (20%).
    """
    return {
        "equity": 10_000.0,
        "cash": 8_000.0,
        "positions": [
            {
                "symbol": "MCL", "notional": 6_800, "initial_margin": 600,
                "side": "LONG", "strategy": "futures_energy", "asset_class": "FUTURES",
                "qty": 1,
            },
            {
                "symbol": "MES", "notional": 5_200, "initial_margin": 1_400,
                "side": "LONG", "strategy": "futures_index", "asset_class": "FUTURES",
                "qty": 1,
            },
        ],
        "margin_used_pct": 0.20,
    }


@pytest.fixture
def mixed_portfolio():
    """Portfolio with equity, FX, and futures positions.

    Equity: AAPL $1,500 notional (effective cost = $1,500)
    FX: EURUSD $25K notional, $750 margin (effective cost = $750)
    Futures: MCL $600 initial_margin (effective cost = $600)
    Total effective = 1500 + 750 + 600 = $2,850 (28.5%)
    """
    return {
        "equity": 10_000.0,
        "cash": 5_000.0,
        "positions": [
            {
                "symbol": "AAPL", "notional": 1_500, "side": "LONG",
                "strategy": "momentum_daily", "asset_class": "EQUITY",
            },
            {
                "symbol": "EURUSD", "notional": 25_000, "margin_used": 750,
                "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
            },
            {
                "symbol": "MCL", "notional": 6_800, "initial_margin": 600,
                "side": "LONG", "strategy": "futures_energy", "asset_class": "FUTURES",
                "qty": 1,
            },
        ],
        "margin_used_pct": 0.30,
    }


# =============================================================================
# HELPER METHOD TESTS
# =============================================================================

class TestGetExposureByType:
    """Tests for _get_exposure_by_type helper."""

    def test_empty_portfolio(self, live_rm, empty_portfolio):
        exp = live_rm._get_exposure_by_type(empty_portfolio)
        assert exp["equity_exposure"] == 0.0
        assert exp["fx_margin"] == 0.0
        assert exp["fx_notional"] == 0.0
        assert exp["futures_margin"] == 0.0
        assert exp["total_margin"] == 0.0

    def test_mixed_portfolio(self, live_rm, mixed_portfolio):
        exp = live_rm._get_exposure_by_type(mixed_portfolio)
        assert exp["equity_exposure"] == 1_500.0
        assert exp["fx_margin"] == 750.0
        assert exp["fx_notional"] == 25_000.0
        assert exp["futures_margin"] == 600.0
        assert exp["total_margin"] == 1_500.0 + 750.0 + 600.0


# =============================================================================
# TEST 1: FX order accepted — margin < 40% AND notional < 1500%
# =============================================================================

class TestFxAccepted:
    def test_fx_order_within_limits(self, live_rm, empty_portfolio):
        """FX order with margin < 40% and notional < 1500% -> accepted."""
        order = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "notional": 25_000,       # 250% of equity (well under 1500%)
            "margin_used": 750,       # 7.5% of equity (well under 40%)
            "strategy": "fx_carry_eur",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, empty_portfolio)
        assert passed, f"Should accept FX order within limits, got: {msg}"

    def test_fx_order_with_existing_positions(self, live_rm, portfolio_with_fx):
        """Adding a 4th FX pair when existing 3 use 22.5% margin -> accepted.

        After order: total FX margin = 4 x $750 = $3,000 = 30% (< 40%).
        """
        order = {
            "symbol": "EURGBP",
            "direction": "LONG",
            "notional": 25_000,
            "margin_used": 750,
            "strategy": "fx_carry_eurgbp",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, portfolio_with_fx)
        assert passed, f"Should accept 4th FX pair, got: {msg}"


# =============================================================================
# TEST 2: FX order rejected — margin OK but notional > 1500%
# =============================================================================

class TestFxRejectedNotional:
    def test_fx_notional_exceeds_limit(self, live_rm, empty_portfolio):
        """FX order with notional > 15x equity but margin OK -> rejected."""
        order = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "notional": 160_000,      # 1600% of equity (> 1500%)
            "margin_used": 1_200,     # 12% margin (OK, under 40%)
            "strategy": "fx_carry_eur",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, empty_portfolio)
        assert not passed, "Should reject FX order exceeding notional limit"
        assert "FX notional limit" in msg


# =============================================================================
# TEST 3: FX order rejected — notional OK but margin > 40%
# =============================================================================

class TestFxRejectedMargin:
    def test_fx_margin_exceeds_limit(self, live_rm, portfolio_with_fx):
        """FX order that pushes total FX margin > 40% -> rejected.

        Existing FX margin = 22.5%. New order margin = $2,000 = 20%.
        Total = 42.5% > 40%.
        """
        order = {
            "symbol": "NZDUSD",
            "direction": "LONG",
            "notional": 30_000,
            "margin_used": 2_000,     # Would push total to 42.5% (> 40%)
            "strategy": "fx_carry_nzd",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, portfolio_with_fx)
        assert not passed, "Should reject FX order exceeding margin limit"
        assert "FX margin limit" in msg


# =============================================================================
# TEST 4: Equity order not affected by FX/futures limits
# =============================================================================

class TestEquityUnaffected:
    def test_equity_order_passes_fx_futures_checks(self, live_rm, empty_portfolio):
        """Standard equity order should not be checked against FX/futures limits."""
        order = {
            "symbol": "AAPL",
            "direction": "LONG",
            "notional": 1_000,
            "strategy": "momentum_daily",
            "asset_class": "EQUITY",
        }
        passed, msg = live_rm.validate_order(order, empty_portfolio)
        assert passed, f"Equity order should pass, got: {msg}"

    def test_equity_ignores_fx_checks(self, live_rm):
        """Equity order passes even when FX positions have high margin usage."""
        portfolio = {
            "equity": 10_000.0,
            "cash": 8_000.0,
            "positions": [
                {
                    "symbol": "EURUSD", "notional": 25_000, "margin_used": 2_000,
                    "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
                },
            ],
            "margin_used_pct": 0.20,
        }
        order = {
            "symbol": "MSFT",
            "direction": "LONG",
            "notional": 1_000,
            "strategy": "momentum_daily",
            "asset_class": "EQUITY",
        }
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed, f"Equity order should not be blocked by FX limits, got: {msg}"


# =============================================================================
# TEST 5: Futures order accepted — margin < 35%
# =============================================================================

class TestFuturesAccepted:
    def test_futures_order_within_limits(self, live_rm, empty_portfolio):
        """Futures order with margin < 35% and allowed contract -> accepted."""
        order = {
            "symbol": "MCL",
            "direction": "LONG",
            "notional": 6_800,
            "initial_margin": 600,    # 6% of equity (well under 35%)
            "strategy": "futures_energy",
            "asset_class": "FUTURES",
            "qty": 1,
        }
        passed, msg = live_rm.validate_order(order, empty_portfolio)
        assert passed, f"Should accept futures order within limits, got: {msg}"


# =============================================================================
# TEST 6: Futures order rejected — margin > 35%
# =============================================================================

class TestFuturesRejectedMargin:
    def test_futures_margin_exceeds_limit(self, live_rm, portfolio_with_futures):
        """Adding futures when total futures margin would exceed 35% -> rejected.

        Existing: MCL($600) + MES($1,400) = $2,000 (20%).
        New MCL order: $1,800 margin -> total = 38% > 35%.
        Position limit for MCL: $600 + $1800 = $2400 = 24% (< 25%), OK.
        """
        order = {
            "symbol": "MCL",
            "direction": "LONG",
            "notional": 6_800,
            "initial_margin": 1_800,
            "strategy": "futures_energy_v2",
            "asset_class": "FUTURES",
            "qty": 1,
        }
        passed, msg = live_rm.validate_order(order, portfolio_with_futures)
        assert not passed, "Should reject futures order exceeding margin limit"
        assert "Futures margin limit" in msg


# =============================================================================
# TEST 7: Futures order rejected — contract not whitelisted
# =============================================================================

class TestFuturesRejectedContract:
    def test_futures_contract_not_allowed(self, live_rm, empty_portfolio):
        """Futures order for non-whitelisted contract (MGC) -> rejected."""
        order = {
            "symbol": "MGC",
            "direction": "LONG",
            "notional": 5_000,
            "initial_margin": 1_000,
            "strategy": "futures_metals",
            "asset_class": "FUTURES",
            "qty": 1,
        }
        passed, msg = live_rm.validate_order(order, empty_portfolio)
        assert not passed, "Should reject non-whitelisted futures contract"
        assert "not allowed" in msg
        assert "MGC" in msg


# =============================================================================
# TEST 8: Mix FX + equity + futures: combined margin < 80%
# =============================================================================

class TestCombinedMarginMix:
    def test_mixed_within_combined_limit(self, live_rm, mixed_portfolio):
        """Adding equity to mixed portfolio stays under 80% combined margin.

        Existing total_margin = 1500 + 750 + 600 = 2850 (28.5%).
        New equity $1000 -> total 38.5% (< 80%).
        """
        order = {
            "symbol": "MSFT",
            "direction": "LONG",
            "notional": 1_000,
            "strategy": "momentum_intra",
            "asset_class": "EQUITY",
        }
        passed, msg = live_rm.validate_order(order, mixed_portfolio)
        assert passed, f"Mixed portfolio under 80% combined margin, got: {msg}"

    def test_combined_margin_exceeded(self, live_rm):
        """When combined margin would exceed 80% -> rejected.

        Existing: equity $2K LONG + equity $1K SHORT + fx margin $2K LONG
                  + futures margin $2.5K SHORT = $7.5K (75%).
        Long effective = $2K + $2K = $4K (40%). Short effective = $1K + $2.5K = $3.5K (35%).
        New FX order: LONG $750 margin -> long = 47.5%, combined = 82.5% > 80%.
        """
        portfolio = {
            "equity": 10_000.0,
            "cash": 3_000.0,
            "positions": [
                {
                    "symbol": "AAPL", "notional": 2_000, "side": "LONG",
                    "strategy": "momentum_daily", "asset_class": "EQUITY",
                },
                {
                    "symbol": "MSFT", "notional": 1_000, "side": "SHORT",
                    "strategy": "momentum_intra", "asset_class": "EQUITY",
                },
                {
                    "symbol": "EURUSD", "notional": 50_000, "margin_used": 2_000,
                    "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
                },
                {
                    "symbol": "MCL", "notional": 6_800, "initial_margin": 2_500,
                    "side": "SHORT", "strategy": "futures_energy", "asset_class": "FUTURES",
                    "qty": 1,
                },
            ],
            "margin_used_pct": 0.70,
        }
        # Existing total_margin = 2000 + 1000 + 2000 + 2500 = 7500 (75%)
        order = {
            "symbol": "GBPUSD",
            "direction": "LONG",
            "notional": 25_000,
            "margin_used": 750,       # Would push to 82.5% (> 80%)
            "strategy": "fx_carry_gbp",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, portfolio)
        assert not passed, "Should reject when combined margin > 80%"
        assert "Combined margin limit" in msg


# =============================================================================
# TEST 9: Mix FX + futures: cash > 20%
# =============================================================================

class TestCashReserveCombined:
    def test_cash_above_min(self, live_rm, mixed_portfolio):
        """Order that keeps cash >= 20% -> accepted.

        Cash = $5000, order margin = $750, remaining = $4250 = 42.5%.
        """
        order = {
            "symbol": "EURGBP",
            "direction": "LONG",
            "notional": 25_000,
            "margin_used": 750,
            "strategy": "fx_carry_eurgbp",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, mixed_portfolio)
        assert passed, f"Cash still above 20%, got: {msg}"

    def test_cash_below_min(self, live_rm):
        """FX order that would push cash < 20% (combined check) -> rejected.

        Cash = $2,800. Order margin = $900. Remaining = $1,900 = 19% < 20%.
        Note: old cash_reserve limit is 5% (relaxed in test config), so
        the combined_limits min_cash_pct of 20% is the binding constraint.
        """
        portfolio = {
            "equity": 10_000.0,
            "cash": 2_800.0,
            "positions": [
                {
                    "symbol": "EURUSD", "notional": 25_000, "margin_used": 750,
                    "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
                },
                {
                    "symbol": "MCL", "notional": 6_800, "initial_margin": 600,
                    "side": "LONG", "strategy": "futures_energy", "asset_class": "FUTURES",
                    "qty": 1,
                },
            ],
            "margin_used_pct": 0.15,
        }
        order = {
            "symbol": "GBPUSD",
            "direction": "LONG",
            "notional": 25_000,
            "margin_used": 900,       # Cash: $2800 - $900 = $1900 = 19% (< 20%)
            "strategy": "fx_carry_gbp",
            "asset_class": "FX",
        }
        passed, msg = live_rm.validate_order(order, portfolio)
        assert not passed, "Should reject when cash would fall below 20%"
        assert "Combined min cash" in msg


# =============================================================================
# TEST 10: 4 FX + 2 MCL + 1 MES — all checks pass
# =============================================================================

class TestFullMixAllPass:
    def test_realistic_multi_asset_portfolio(self, live_rm):
        """Realistic portfolio: 4 FX pairs + 2 MCL + 1 MES, all checks pass.

        FX margin: 4 x $750 = $3,000 (30% < 40%)
        Futures margin after order: $1,200 + $1,400 = $2,600 (26% < 35%)
        Combined: $0 equity + $3,000 + $2,600 = $5,600 (56% < 80%)
        Cash: $4,850 - $1,400 = $3,450 = 34.5% (> 20%)
        """
        portfolio = {
            "equity": 10_000.0,
            "cash": 4_850.0,
            "positions": [
                {
                    "symbol": "EURUSD", "notional": 25_000, "margin_used": 750,
                    "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
                },
                {
                    "symbol": "GBPUSD", "notional": 25_000, "margin_used": 750,
                    "side": "LONG", "strategy": "fx_carry_gbp", "asset_class": "FX",
                },
                {
                    "symbol": "AUDJPY", "notional": 25_000, "margin_used": 750,
                    "side": "SHORT", "strategy": "fx_carry_aud", "asset_class": "FX",
                },
                {
                    "symbol": "NZDUSD", "notional": 25_000, "margin_used": 750,
                    "side": "LONG", "strategy": "fx_carry_nzd", "asset_class": "FX",
                },
                {
                    "symbol": "MCL", "notional": 6_800, "initial_margin": 600,
                    "side": "LONG", "strategy": "futures_energy", "asset_class": "FUTURES",
                    "qty": 1,
                },
                {
                    "symbol": "MCL", "notional": 6_800, "initial_margin": 600,
                    "side": "LONG", "strategy": "futures_energy_2", "asset_class": "FUTURES",
                    "qty": 1,
                },
            ],
            "margin_used_pct": 0.45,
        }
        order = {
            "symbol": "MES",
            "direction": "LONG",
            "notional": 5_200,
            "initial_margin": 1_400,
            "strategy": "futures_index",
            "asset_class": "FUTURES",
            "qty": 1,
        }
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed, f"Full mix should pass all checks, got: {msg}"

        # Verify exposure breakdown
        exp = live_rm._get_exposure_by_type(portfolio)
        assert exp["fx_margin"] == 3_000.0
        assert exp["fx_notional"] == 100_000.0
        assert exp["futures_margin"] == 1_200.0
        assert exp["equity_exposure"] == 0.0


# =============================================================================
# TEST 11: Position that would breach min cash -> rejected
# =============================================================================

class TestMinCashBreach:
    def test_equity_order_breaches_combined_min_cash(self, live_rm):
        """Equity order that would push remaining cash below 20% -> rejected.

        Cash = $2,500. Order = $1,000 equity. Remaining = $1,500 = 15% < 20%.
        The old cash_reserve check (min 5%) passes, but combined_margin
        min_cash_pct of 20% rejects it.
        """
        portfolio = {
            "equity": 10_000.0,
            "cash": 2_500.0,
            "positions": [
                {
                    "symbol": "AAPL", "notional": 1_500, "side": "LONG",
                    "strategy": "momentum_daily", "asset_class": "EQUITY",
                },
                {
                    "symbol": "EURUSD", "notional": 25_000, "margin_used": 1_000,
                    "side": "LONG", "strategy": "fx_carry_eur", "asset_class": "FX",
                },
            ],
            "margin_used_pct": 0.25,
        }
        order = {
            "symbol": "MSFT",
            "direction": "LONG",
            "notional": 1_000,
            "strategy": "momentum_intra",
            "asset_class": "EQUITY",
        }
        passed, msg = live_rm.validate_order(order, portfolio)
        assert not passed, "Should reject when order breaches min cash"
        assert "Combined min cash" in msg

    def test_futures_order_breaches_min_cash(self, live_rm):
        """Futures order that would leave < 20% cash -> rejected.

        Cash = $2,500. Order margin = $1,400. Remaining = $1,100 = 11% < 20%.
        Old cash_reserve (5%) passes. Combined min_cash (20%) rejects.
        """
        portfolio = {
            "equity": 10_000.0,
            "cash": 2_500.0,
            "positions": [
                {
                    "symbol": "MCL", "notional": 6_800, "initial_margin": 600,
                    "side": "LONG", "strategy": "futures_energy", "asset_class": "FUTURES",
                    "qty": 1,
                },
            ],
            "margin_used_pct": 0.06,
        }
        order = {
            "symbol": "MES",
            "direction": "LONG",
            "notional": 5_200,
            "initial_margin": 1_400,
            "strategy": "futures_index",
            "asset_class": "FUTURES",
            "qty": 1,
        }
        passed, msg = live_rm.validate_order(order, portfolio)
        assert not passed, "Should reject when futures order breaches min cash"
        assert "Combined min cash" in msg
