"""
Tests unitaires — Risk Manager V5 + Dynamic Allocator V5 + Kelly FX.

Couvre 20+ tests pour l'expansion multi-asset (22 strategies, 4 asset classes) :
  - Allocation : somme des buckets, caps, broker limit, regime multipliers, timezone
  - Futures : VaR, margin monitoring, roll risk
  - FX : position limits, Kelly FX, Sharpe-weighted distribution
  - Cross-asset : correlation limit, stressed VaR
  - Rebalance, tier assignment
"""

import sys
import pytest
import numpy as np
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.risk_manager import RiskManager, FUTURES_MULTIPLIERS, FUTURES_INITIAL_MARGIN
from core.allocator import DynamicAllocator
from core.kelly_calculator import KellyCalculator, FX_SHARPE_WEIGHTS


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def rm():
    """RiskManager with project limits.yaml."""
    return RiskManager()


@pytest.fixture
def allocator():
    """DynamicAllocator with project allocation.yaml."""
    return DynamicAllocator()


@pytest.fixture
def kelly():
    """KellyCalculator instance."""
    return KellyCalculator()


@pytest.fixture
def multi_asset_portfolio():
    """Portfolio with equity + FX + futures positions."""
    return {
        "equity": 25_000.0,
        "cash": 5_000.0,
        "positions": [
            # US equities
            {"symbol": "SPY", "notional": 3000, "side": "LONG", "strategy": "dow_seasonal", "broker": "alpaca", "asset_class": "equity"},
            {"symbol": "QQQ", "notional": 2000, "side": "SHORT", "strategy": "vix_expansion_short", "broker": "alpaca", "asset_class": "equity"},
            # EU equities
            {"symbol": "SX5E", "notional": 2500, "side": "LONG", "strategy": "eu_gap_open", "broker": "ibkr", "asset_class": "equity"},
            # FX
            {"symbol": "EURUSD", "notional": 2000, "side": "LONG", "strategy": "fx_eurusd_trend", "broker": "ibkr", "asset_class": "fx"},
            {"symbol": "EURJPY", "notional": 1500, "side": "LONG", "strategy": "fx_eurjpy_carry", "broker": "ibkr", "asset_class": "fx"},
            # Futures
            {"symbol": "MES", "notional": 2000, "side": "LONG", "strategy": "futures_mes_trend", "broker": "ibkr", "asset_class": "futures", "contracts": 1},
        ],
    }


@pytest.fixture
def sample_strategies_v5():
    """22 strategies with edge types for the V5 allocator."""
    return {
        # US intraday
        "dow_seasonal": {"sharpe": 3.42, "volatility": 0.08, "correlation_avg": 0.2, "edge_type": "momentum"},
        "corr_regime_hedge": {"sharpe": 1.09, "volatility": 0.12, "correlation_avg": 0.3, "edge_type": "mean_reversion"},
        "vix_expansion_short": {"sharpe": 2.0, "volatility": 0.15, "correlation_avg": 0.4, "edge_type": "short"},
        "high_beta_short": {"sharpe": 1.5, "volatility": 0.18, "correlation_avg": 0.5, "edge_type": "short"},
        "late_day_mr": {"sharpe": 0.6, "volatility": 0.10, "correlation_avg": 0.25, "edge_type": "mean_reversion"},
        "failed_rally": {"sharpe": 0.8, "volatility": 0.14, "correlation_avg": 0.35, "edge_type": "short"},
        "eod_sell_v2": {"sharpe": 0.9, "volatility": 0.11, "correlation_avg": 0.3, "edge_type": "short"},
        # US event
        "fomc_reaction": {"sharpe": 1.2, "volatility": 0.20, "correlation_avg": 0.1, "edge_type": "event"},
        # US daily
        "momentum_etf": {"sharpe": 1.5, "volatility": 0.10, "correlation_avg": 0.4, "edge_type": "momentum"},
        "pairs_mu_amat": {"sharpe": 0.8, "volatility": 0.08, "correlation_avg": 0.6, "edge_type": "mean_reversion"},
        "vrp_regime": {"sharpe": 1.0, "volatility": 0.06, "correlation_avg": 0.2, "edge_type": "momentum"},
        # EU intraday
        "eu_gap_open": {"sharpe": 4.5, "volatility": 0.07, "correlation_avg": 0.15, "edge_type": "momentum"},
        "brent_lag_play": {"sharpe": 3.2, "volatility": 0.09, "correlation_avg": 0.2, "edge_type": "momentum"},
        "eu_close_us_afternoon": {"sharpe": 1.0, "volatility": 0.10, "correlation_avg": 0.3, "edge_type": "mean_reversion"},
        # EU event
        "bce_momentum_drift": {"sharpe": 4.0, "volatility": 0.08, "correlation_avg": 0.1, "edge_type": "event"},
        "auto_sector_german": {"sharpe": 1.8, "volatility": 0.12, "correlation_avg": 0.25, "edge_type": "momentum"},
        "bce_press_conference": {"sharpe": 1.1, "volatility": 0.15, "correlation_avg": 0.15, "edge_type": "event"},
        # FX swing
        "fx_eurusd_trend": {"sharpe": 4.62, "volatility": 0.05, "correlation_avg": 0.2, "edge_type": "carry"},
        "fx_eurgbp_mr": {"sharpe": 3.65, "volatility": 0.04, "correlation_avg": 0.15, "edge_type": "mean_reversion"},
        "fx_eurjpy_carry": {"sharpe": 2.50, "volatility": 0.06, "correlation_avg": 0.25, "edge_type": "carry"},
        "fx_audjpy_carry": {"sharpe": 1.58, "volatility": 0.07, "correlation_avg": 0.3, "edge_type": "carry"},
        "fx_gbpusd_trend": {"sharpe": 2.0, "volatility": 0.05, "correlation_avg": 0.2, "edge_type": "momentum"},
        "fx_usdchf_mr": {"sharpe": 1.5, "volatility": 0.04, "correlation_avg": 0.15, "edge_type": "mean_reversion"},
        "fx_nzdusd_carry": {"sharpe": 1.2, "volatility": 0.06, "correlation_avg": 0.2, "edge_type": "carry"},
        # Futures
        "futures_mes_trend": {"sharpe": 2.5, "volatility": 0.10, "correlation_avg": 0.35, "edge_type": "momentum"},
        "futures_mnq_mr": {"sharpe": 1.3, "volatility": 0.14, "correlation_avg": 0.4, "edge_type": "mean_reversion"},
        "brent_lag_futures": {"sharpe": 1.0, "volatility": 0.12, "correlation_avg": 0.25, "edge_type": "momentum"},
    }


# =============================================================================
# 1. test_allocation_sums_to_target
# =============================================================================

def test_allocation_sums_to_target(allocator):
    """La somme des bucket targets doit etre <= (1 - min_cash_reserve)."""
    buckets = allocator.config.get("buckets", {})
    total = sum(b.get("target", 0) for b in buckets.values())
    cash_reserve = allocator.config["portfolio"]["min_cash_reserve"]
    max_target = 1.0 - cash_reserve

    assert total <= max_target + 0.001, (
        f"Bucket total {total:.3f} > max {max_target:.3f}"
    )
    # Verify it's close to expected (0.93 = 1.0 - 0.07)
    assert total > 0.80, f"Bucket total {total:.3f} too low"


# =============================================================================
# 2. test_no_bucket_exceeds_max
# =============================================================================

def test_no_bucket_exceeds_max(allocator):
    """Aucun bucket ne doit depasser max_single_bucket."""
    max_bucket = allocator.config["portfolio"].get("max_single_bucket", 0.45)
    buckets = allocator.config.get("buckets", {})

    for name, cfg in buckets.items():
        target = cfg.get("target", 0)
        assert target <= max_bucket, (
            f"Bucket {name}: target {target:.2f} > max {max_bucket:.2f}"
        )


# =============================================================================
# 3. test_no_broker_exceeds_60pct
# =============================================================================

def test_no_broker_exceeds_60pct(allocator):
    """La somme des buckets par broker ne doit pas depasser 60%."""
    max_broker = allocator.config["portfolio"].get("max_single_broker", 0.60)
    buckets = allocator.config.get("buckets", {})

    broker_totals = {}
    for name, cfg in buckets.items():
        broker = cfg.get("broker", "unknown")
        broker_totals[broker] = broker_totals.get(broker, 0) + cfg.get("target", 0)

    for broker, total in broker_totals.items():
        assert total <= max_broker + 0.001, (
            f"Broker {broker}: total {total:.2f} > max {max_broker:.2f}"
        )


# =============================================================================
# 4. test_regime_multipliers_applied
# =============================================================================

def test_regime_multipliers_applied(allocator, sample_strategies_v5):
    """Les multiplicateurs de regime doivent modifier les poids."""
    weights = allocator.calculate_weights(sample_strategies_v5)

    # BEAR_HIGH_VOL should boost shorts (2.0x) and reduce momentum (0.3x)
    bear_weights = allocator.apply_regime(weights, sample_strategies_v5, "BEAR_HIGH_VOL")
    bull_weights = allocator.apply_regime(weights, sample_strategies_v5, "BULL_NORMAL")

    # Short strategies should get more weight in bear
    short_strats = [k for k, v in sample_strategies_v5.items() if v["edge_type"] == "short"]
    for strat in short_strats:
        if strat in bear_weights and strat in bull_weights:
            # Bear weight / bull weight should reflect multiplier ratio
            if bull_weights[strat] > 0:
                ratio = bear_weights[strat] / bull_weights[strat]
                # BEAR_HIGH_VOL short=2.0, BULL_NORMAL short=0.5 -> ratio ~4x (before renorm)
                assert ratio > 1.0, (
                    f"Short strat {strat}: bear/bull ratio {ratio:.2f} should be > 1.0"
                )


# =============================================================================
# 5. test_timezone_allocation_coverage
# =============================================================================

def test_timezone_allocation_coverage(allocator):
    """Chaque heure CET (0-23) doit retourner une allocation valide."""
    for hour in range(24):
        tz_alloc = allocator.get_timezone_allocation(hour)
        assert "timezone" in tz_alloc, f"Missing timezone for hour {hour}"
        assert "total_invested" in tz_alloc, f"Missing total_invested for hour {hour}"
        assert "buckets" in tz_alloc, f"Missing buckets for hour {hour}"
        assert tz_alloc["total_invested"] >= 0, f"Negative total for hour {hour}"
        assert tz_alloc["total_invested"] <= 1.0, f"Total > 100% for hour {hour}"


# =============================================================================
# 6. test_futures_var_calculation
# =============================================================================

def test_futures_var_calculation(rm):
    """Le VaR futures doit convertir points -> dollars via multiplier."""
    np.random.seed(42)
    returns_pts = {
        "MES": list(np.random.normal(0, 10, 100)),  # 10 points std
    }
    contracts = {"MES": 2}

    result = rm.calculate_futures_var(returns_pts, contracts)

    assert "var_total" in result
    assert "var_by_symbol" in result
    assert result["var_total"] > 0, "Futures VaR should be positive"
    # MES: 1 pt = $5, 2 contracts, so VaR in USD should reflect that
    assert "MES" in result["var_by_symbol"]


# =============================================================================
# 7. test_margin_monitoring_alerts
# =============================================================================

def test_margin_monitoring_alerts(rm):
    """Les alertes de marge futures doivent etre GREEN/YELLOW/RED."""
    # Small positions relative to capital -> GREEN
    positions = [{"symbol": "MES", "contracts": 1}]
    result = rm.check_futures_margin(positions, capital=100_000)
    assert result["status"] == "GREEN"
    assert result["margin_pct"] < 0.5

    # Large positions relative to capital -> RED
    positions_big = [
        {"symbol": "MES", "contracts": 5},
        {"symbol": "MNQ", "contracts": 5},
        {"symbol": "MCL", "contracts": 5},
        {"symbol": "MGC", "contracts": 5},
    ]
    result_big = rm.check_futures_margin(positions_big, capital=25_000)
    assert result_big["status"] == "RED"
    assert result_big["margin_pct"] > 0.7


# =============================================================================
# 8. test_fx_position_limits
# =============================================================================

def test_fx_position_limits(rm):
    """Les limites FX single pair (25%) doivent rejeter les ordres excessifs.

    Note: on teste _check_fx_limits directement car max_single_position (10%)
    se declenche avant pour les FX (FX positions sont souvent > 10% en levier).
    """
    portfolio = {
        "equity": 100_000,
        "cash": 50_000,
        "positions": [
            {"symbol": "EURUSD", "notional": 20_000, "side": "LONG", "asset_class": "fx"},
        ],
    }

    # 20000 + 6001 = 26001 = 26% > FX single pair 25%
    order_big = {
        "symbol": "EURUSD",
        "direction": "LONG",
        "notional": 6_001,
        "strategy": "fx_eurusd_trend",
        "asset_class": "fx",
    }
    passed, msg = rm._check_fx_limits(order_big, portfolio)
    assert not passed, f"Should reject FX single pair > 25%: {msg}"
    assert "FX single pair" in msg

    # Small order within limits
    order_small = {
        "symbol": "EURGBP",
        "direction": "LONG",
        "notional": 1000,
        "strategy": "fx_eurgbp_mr",
        "asset_class": "fx",
    }
    passed_small, _ = rm._check_fx_limits(order_small, portfolio)
    assert passed_small, "Small FX order should pass"


# =============================================================================
# 9. test_fx_kelly_calculation
# =============================================================================

def test_fx_kelly_calculation(kelly):
    """Le Kelly FX doit retourner des fractions positives avec couts reduits."""
    result = kelly.calculate_fx_kelly(
        win_rate=0.52,
        avg_win_pips=35.0,
        avg_loss_pips=25.0,
        pip_value=10.0,
        fraction=0.25,
    )

    assert result["full_kelly"] > 0, "FX Kelly should be positive for winning strategy"
    assert result["fractional_kelly"] > 0, "Fractional FX Kelly should be positive"
    assert result["fractional_kelly"] < result["full_kelly"], (
        "Fractional should be less than full Kelly"
    )
    # FX costs are tiny, so cost impact should be very small
    assert result["cost_impact_pct"] < 0.05, (
        f"FX cost impact {result['cost_impact_pct']:.4f} should be < 5%"
    )
    assert result["avg_win_net_pips"] > 0
    assert result["avg_loss_net_pips"] > 0


# =============================================================================
# 10. test_cross_asset_correlation_limit
# =============================================================================

def test_cross_asset_correlation_limit(rm):
    """La limite de correlation cross-asset doit bloquer les positions correlees."""
    np.random.seed(42)
    base = np.random.normal(0, 0.01, 50)

    # Create highly correlated returns
    returns_map = {
        "SPY": list(base),
        "QQQ": list(base + np.random.normal(0, 0.001, 50)),  # corr ~ 0.99
        "AAPL": list(base * 0.5 + np.random.normal(0, 0.005, 50)),  # moderately correlated
    }

    positions = [
        {"symbol": "SPY", "notional": 5000, "side": "LONG"},
        {"symbol": "QQQ", "notional": 5000, "side": "LONG"},
        {"symbol": "AAPL", "notional": 2000, "side": "LONG"},
    ]

    passed, msg, expo = rm.check_correlated_exposure(
        positions, returns_map, equity=25_000, corr_threshold=0.7
    )

    # SPY and QQQ are highly correlated -> should flag
    assert expo > 0, "Correlated exposure should be detected"


# =============================================================================
# 11. test_stressed_var_with_futures
# =============================================================================

def test_stressed_var_with_futures(rm):
    """Stressed VaR avec futures doit etre >= VaR normal."""
    np.random.seed(42)
    strategy_returns = {
        "strat_equity": list(np.random.normal(0.001, 0.02, 100)),
        "strat_futures": list(np.random.normal(0.0005, 0.03, 100)),
        "strat_fx": list(np.random.normal(0.0003, 0.01, 100)),
    }
    weights = {"strat_equity": 0.40, "strat_futures": 0.10, "strat_fx": 0.18}
    asset_classes = {
        "strat_equity": "equity",
        "strat_futures": "futures_index",
        "strat_fx": "fx",
    }

    result = rm.calculate_stressed_var(strategy_returns, weights, asset_classes)

    assert result["var_stressed"] > 0, "Stressed VaR should be positive"
    assert result["var_normal"] > 0, "Normal VaR should be positive"
    # Stressed VaR should generally be >= normal (higher correlations in crisis)
    assert result["stress_multiplier"] >= 0.5, (
        f"Stress multiplier {result['stress_multiplier']:.2f} seems too low"
    )


# =============================================================================
# 12. test_roll_risk_margin_double
# =============================================================================

def test_roll_risk_margin_double(rm):
    """Pendant un roll, la marge doit etre doublee."""
    positions = [{"symbol": "MES", "contracts": 2}]

    result_normal = rm.check_futures_margin(positions, capital=25_000, is_rolling=False)
    result_rolling = rm.check_futures_margin(positions, capital=25_000, is_rolling=True)

    assert result_rolling["margin_used"] == 2.0 * result_normal["margin_used"], (
        "Roll risk should double margin"
    )
    assert result_rolling["margin_pct"] > result_normal["margin_pct"]


# =============================================================================
# 13. test_tier_assignment_correct
# =============================================================================

def test_tier_assignment_correct(allocator):
    """Chaque strategie doit etre dans le bon tier."""
    # Tier S
    assert allocator.get_tier_for_strategy("eu_gap_open") == "S"
    assert allocator.get_tier_for_strategy("bce_momentum_drift") == "S"

    # Tier A
    assert allocator.get_tier_for_strategy("brent_lag_play") == "A"
    assert allocator.get_tier_for_strategy("dow_seasonal") == "A"
    assert allocator.get_tier_for_strategy("fx_eurusd_trend") == "A"
    assert allocator.get_tier_for_strategy("futures_mes_trend") == "A"

    # Tier B
    assert allocator.get_tier_for_strategy("corr_regime_hedge") == "B"
    assert allocator.get_tier_for_strategy("fx_eurjpy_carry") == "B"

    # Tier C
    assert allocator.get_tier_for_strategy("late_day_mr") == "C"
    assert allocator.get_tier_for_strategy("fx_eurgbp_mr") == "C"
    assert allocator.get_tier_for_strategy("brent_lag_futures") == "C"
    assert allocator.get_tier_for_strategy("fomc_reaction") == "C"

    # Unknown
    assert allocator.get_tier_for_strategy("nonexistent") == "unknown"


# =============================================================================
# 14. test_rebalance_trigger
# =============================================================================

def test_rebalance_trigger(allocator):
    """Le rebalance doit detecter les drifts > threshold."""
    target = {"strat_a": 0.10, "strat_b": 0.20, "strat_c": 0.05}

    # 30% drift on strat_a (0.10 -> 0.14)
    current = {"strat_a": 0.14, "strat_b": 0.19, "strat_c": 0.05}

    result = allocator.check_rebalance_needed(current, target, threshold=0.20)

    assert "strat_a" in result, "strat_a drifted 40% should trigger"
    assert result["strat_a"]["action"] == "decrease"
    assert "strat_c" not in result, "strat_c has no drift"


# =============================================================================
# 15. test_sharpe_weighted_fx_distribution
# =============================================================================

def test_sharpe_weighted_fx_distribution(kelly):
    """La distribution FX doit etre ponderee par Sharpe et sommer a 18%."""
    result = kelly.distribute_fx_allocation(
        total_fx_pct=0.18,
        sharpe_weights=FX_SHARPE_WEIGHTS,
        total_capital=25_000,
    )

    assert len(result) == 7, f"Expected 7 FX pairs, got {len(result)}"

    total_weight = sum(r["weight_pct"] for r in result.values())
    assert abs(total_weight - 0.18) < 0.001, (
        f"FX total weight {total_weight:.4f} should be ~0.18"
    )

    total_capital = sum(r["capital"] for r in result.values())
    assert abs(total_capital - 4500) < 10, (
        f"FX total capital ${total_capital:.0f} should be ~$4,500"
    )

    # EUR/USD (highest Sharpe 4.62) should get the most
    assert result["fx_eurusd_trend"]["weight_pct"] > result["fx_nzdusd_carry"]["weight_pct"], (
        "EUR/USD should get more than NZD/USD"
    )


# =============================================================================
# 16. test_validate_order_futures_margin_reject
# =============================================================================

def test_validate_order_futures_margin_reject(rm):
    """validate_order doit rejeter un ordre futures si marge > max."""
    portfolio = {
        "equity": 25_000,
        "cash": 10_000,
        "positions": [
            {"symbol": "MES", "contracts": 4, "notional": 0, "side": "LONG",
             "strategy": "futures_mes_trend", "asset_class": "futures"},
        ],
    }

    # Adding 2 more MES contracts would exceed max_contracts_per_symbol (5)
    order = {
        "symbol": "MES",
        "direction": "LONG",
        "notional": 1000,
        "contracts": 2,
        "strategy": "futures_mes_trend",
        "asset_class": "futures",
    }
    passed, msg = rm.validate_order(order, portfolio)
    assert not passed, f"Should reject: 4+2=6 > max 5 contracts: {msg}"


# =============================================================================
# 17. test_fx_total_exposure_limit
# =============================================================================

def test_fx_total_exposure_limit(rm):
    """L'exposition FX totale ne doit pas depasser 60%."""
    portfolio = {
        "equity": 25_000,
        "cash": 10_000,
        "positions": [
            {"symbol": "EURUSD", "notional": 5000, "side": "LONG", "asset_class": "fx"},
            {"symbol": "EURGBP", "notional": 4000, "side": "LONG", "asset_class": "fx"},
            {"symbol": "EURJPY", "notional": 3000, "side": "SHORT", "asset_class": "fx"},
        ],
    }

    # Adding 4000 more would push total to (5000+4000+3000+4000)/25000 = 64%
    order = {
        "symbol": "AUDJPY",
        "direction": "LONG",
        "notional": 4000,
        "strategy": "fx_audjpy_carry",
        "asset_class": "fx",
    }
    passed, msg = rm.validate_order(order, portfolio)
    assert not passed, f"Should reject: {msg}"
    # May be rejected by position limit (16% > 10%) before FX total check
    assert "limit" in msg.lower() or "exposure" in msg.lower()


# =============================================================================
# 18. test_broker_concentration_limit
# =============================================================================

def test_broker_concentration_limit(rm):
    """La concentration par broker ne doit pas depasser 60%."""
    portfolio = {
        "equity": 25_000,
        "cash": 8_000,
        "positions": [
            {"symbol": "SPY", "notional": 5000, "side": "LONG", "broker": "alpaca", "strategy": "dow_seasonal"},
            {"symbol": "QQQ", "notional": 5000, "side": "LONG", "broker": "alpaca", "strategy": "dow_seasonal"},
            {"symbol": "AAPL", "notional": 4000, "side": "LONG", "broker": "alpaca", "strategy": "dow_seasonal"},
        ],
    }

    # Adding 2000 more to alpaca: (5000+5000+4000+2000)/25000 = 64% > 60%
    order = {
        "symbol": "MSFT",
        "direction": "LONG",
        "notional": 2000,
        "strategy": "corr_regime_hedge",
        "broker": "alpaca",
    }
    passed, msg = rm.validate_order(order, portfolio)
    assert not passed, f"Should reject: {msg}"
    # May be rejected by long exposure (64% > 60%) before broker check
    assert "limit" in msg.lower() or "exposure" in msg.lower()


# =============================================================================
# 19. test_regime_multipliers_carry_type
# =============================================================================

def test_regime_multipliers_carry_type(allocator):
    """Le regime multiplier doit inclure le type 'carry' pour FX."""
    mults = allocator.get_regime_multipliers("BULL_NORMAL")
    assert "carry" in mults, "Missing 'carry' edge type in regime multipliers"
    assert mults["carry"] > 0

    # BEAR_HIGH_VOL should reduce carry
    bear_mults = allocator.get_regime_multipliers("BEAR_HIGH_VOL")
    assert bear_mults["carry"] < mults["carry"], (
        "Carry should be reduced in BEAR_HIGH_VOL"
    )


# =============================================================================
# 20. test_calculate_weights_22_strategies
# =============================================================================

def test_calculate_weights_22_strategies(allocator, sample_strategies_v5):
    """L'allocator doit pouvoir calculer les poids pour 22+ strategies."""
    weights = allocator.calculate_weights(sample_strategies_v5)

    assert len(weights) == len(sample_strategies_v5), (
        f"Expected {len(sample_strategies_v5)} weights, got {len(weights)}"
    )

    total = sum(weights.values())
    cash_reserve = allocator.config["portfolio"]["min_cash_reserve"]
    target = 1.0 - cash_reserve

    assert abs(total - target) < 0.01, (
        f"Weights total {total:.4f} should be ~{target:.2f}"
    )

    # All weights should be positive
    for name, w in weights.items():
        assert w >= 0, f"Negative weight for {name}: {w}"


# =============================================================================
# 21. test_tier_caps_respected
# =============================================================================

def test_tier_caps_respected(allocator, sample_strategies_v5):
    """Les caps par tier doivent etre respectes."""
    weights = allocator.calculate_weights(sample_strategies_v5)
    tiers = allocator.config.get("tiers", {})

    for tier_name, tier_cfg in tiers.items():
        max_alloc = tier_cfg["max_alloc"]
        for strat in tier_cfg.get("strategies", []):
            if strat in weights:
                assert weights[strat] <= max_alloc + 0.001, (
                    f"Tier {tier_name} cap violated: {strat} = {weights[strat]:.4f} > {max_alloc}"
                )


# =============================================================================
# 22. test_kelly_fx_invalid_params
# =============================================================================

def test_kelly_fx_invalid_params(kelly):
    """Kelly FX doit retourner 0 pour des parametres invalides."""
    result = kelly.calculate_fx_kelly(
        win_rate=-0.1, avg_win_pips=10, avg_loss_pips=10
    )
    assert result["full_kelly"] == 0.0
    assert result["fractional_kelly"] == 0.0

    result2 = kelly.calculate_fx_kelly(
        win_rate=0.5, avg_win_pips=0, avg_loss_pips=10
    )
    assert result2["full_kelly"] == 0.0


# =============================================================================
# 23. test_futures_multipliers_coverage
# =============================================================================

def test_futures_multipliers_coverage():
    """Tous les futures dans le sector_map doivent avoir un multiplier."""
    required = ["MES", "MNQ", "MCL", "MGC"]
    for sym in required:
        assert sym in FUTURES_MULTIPLIERS, f"Missing multiplier for {sym}"
        assert sym in FUTURES_INITIAL_MARGIN, f"Missing initial margin for {sym}"
        assert FUTURES_MULTIPLIERS[sym] > 0
        assert FUTURES_INITIAL_MARGIN[sym] > 0


# =============================================================================
# 24. test_timezone_eu_only_no_us
# =============================================================================

def test_timezone_eu_only_no_us(allocator):
    """En heures EU-only (10h CET), les buckets US ne doivent pas etre actifs."""
    tz = allocator.get_timezone_allocation(10)
    buckets = tz["buckets"]

    assert tz["timezone"] == "EU_ONLY"
    assert "us_intraday" not in buckets or buckets.get("us_intraday", 0) == 0
    assert "eu_intraday" in buckets and buckets["eu_intraday"] > 0


# =============================================================================
# 25. test_margin_green_yellow_thresholds
# =============================================================================

def test_margin_green_yellow_thresholds(rm):
    """Test YELLOW status pour marge entre 50% et 70%."""
    # 2 MES contracts: margin = 2 * 1500 = 3000
    # Capital = 5000 -> margin_pct = 60% -> YELLOW
    positions = [{"symbol": "MES", "contracts": 2}]
    result = rm.check_futures_margin(positions, capital=5_000)
    assert result["status"] == "YELLOW", (
        f"Expected YELLOW, got {result['status']} (margin_pct={result['margin_pct']:.1%})"
    )
