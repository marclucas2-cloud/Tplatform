"""
WF-EU -- Walk-forward validation for all EU strategies (IBKR).

Validates each EU strategy with WF config appropriate for EU equities:
  - Train 70% / Test 30%, 5 rolling windows
  - EU annualization: 252 trading days
  - IBKR cost model: 0.05% of trade value (min EUR 3) + slippage 2-4 bps
  - Monte Carlo: 10K paths per strategy
  - Commission burn rate check (max 25%)

Strategies (10 total):
  - eu_gap_open, eu_mean_reversion_dax, eu_mean_reversion_cac
  - eu_mean_reversion_sx5e, eu_orb_frankfurt, eu_orb_paris
  - eu_cross_asset_lead_lag, eu_sector_rotation
  - eu_bce_press_conference, eu_ftse_mean_reversion

Usage:
  python scripts/wf_eu_all.py                          # Run all 10
  python scripts/wf_eu_all.py --strategy eu_gap_open
  python scripts/wf_eu_all.py --output-dir output/wf_eu_results --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


# =====================================================================
# Data classes
# =====================================================================


@dataclass
class WFConfig:
    """Walk-forward configuration for a single EU strategy."""

    strategy_name: str
    strategy_cls_name: str
    tier: str                        # "INDEX", "ORB", "LEAD_LAG", "EVENT", "SECTOR"
    train_pct: float = 0.70          # 70% in-sample
    n_windows: int = 5               # 5 rolling windows
    min_trades_per_window: int = 10
    use_bootstrap: bool = False
    bootstrap_n: int = 1000
    skip: bool = False
    skip_reason: str = ""
    symbols: list = field(default_factory=lambda: ["ESTX50"])
    param_grid: Dict[str, List[Any]] = field(default_factory=dict)


@dataclass
class WFWindowResult:
    """Result of a single walk-forward window."""

    window_idx: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    is_sharpe: float
    oos_sharpe: float
    oos_return: float
    oos_trades: int
    oos_profitable: bool
    oos_max_drawdown: float
    oos_commission_total: float
    oos_gross_profit: float


@dataclass
class MCResultSummary:
    """Summary of Monte Carlo simulation."""

    median_sharpe: float
    p5_sharpe: float
    p95_sharpe: float
    median_max_dd: float
    p95_max_dd: float
    prob_profitable: float
    prob_ruin: float
    n_simulations: int


@dataclass
class WFStrategyResult:
    """Walk-forward result for one EU strategy."""

    strategy_name: str
    tier: str
    verdict: str                    # VALIDATED / BORDERLINE / REJECTED / SKIPPED / NO_DATA
    avg_oos_sharpe: float
    avg_is_sharpe: float
    oos_is_ratio: float
    profitable_windows: int
    total_windows: int
    profitable_ratio: float
    total_oos_trades: int
    commission_burn_rate: float
    avg_oos_max_dd: float
    windows: List[WFWindowResult] = field(default_factory=list)
    monte_carlo: Optional[MCResultSummary] = None
    bootstrap: Optional[dict] = None
    rejection_reasons: List[str] = field(default_factory=list)
    error: str = ""
    timestamp: str = ""


# =====================================================================
# Cost model -- IBKR EU equities
# =====================================================================


# Commission: 0.05% of trade value, min EUR 3
EU_COMMISSION_PCT = 0.0005
EU_MIN_COMMISSION = 3.0  # EUR (treated as base currency)

# Spread in basis points by index/symbol
SPREAD_BPS = {
    "ESTX50": 1.5,    # Euro Stoxx 50 — very liquid
    "DAX": 1.5,       # DAX 40
    "CAC": 1.8,       # CAC 40
    "SX5E": 1.5,      # Euro Stoxx 50 (alternate ticker)
    "FTSE": 2.0,      # FTSE 100
    "IBEX": 2.5,      # IBEX 35 — less liquid
    "MIB": 2.5,       # FTSE MIB
    "AEX": 2.0,       # AEX 25
    "SMI": 2.0,       # Swiss Market Index
}

# Slippage in basis points
SLIPPAGE_BPS = {
    "ESTX50": 2.0,
    "DAX": 2.0,
    "CAC": 2.5,
    "SX5E": 2.0,
    "FTSE": 2.5,
    "IBEX": 3.5,
    "MIB": 3.5,
    "AEX": 3.0,
    "SMI": 3.0,
}


def get_spread_bps(symbol: str) -> float:
    """Return spread in basis points for an EU instrument."""
    return SPREAD_BPS.get(symbol, 2.5)


def get_slippage_bps(symbol: str) -> float:
    """Return slippage in basis points for an EU instrument."""
    return SLIPPAGE_BPS.get(symbol, 3.0)


def get_cost_per_trade_bps(symbol: str) -> float:
    """Return total cost per trade in basis points (spread + slippage).

    Commission (0.05% = 5 bps) is added separately as it depends on
    notional value.
    """
    return get_spread_bps(symbol) + get_slippage_bps(symbol)


def get_total_cost_bps(symbol: str) -> float:
    """Return all-in cost per trade in basis points.

    Includes commission (5 bps) + spread + slippage.
    """
    return EU_COMMISSION_PCT * 10_000 + get_spread_bps(symbol) + get_slippage_bps(symbol)


# =====================================================================
# Parameter grids per strategy type
# =====================================================================


PARAM_GRIDS = {
    # Mean Reversion strategies
    "mean_reversion": {
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
        "sl_pct": [0.01, 0.015, 0.02],
    },
    # Opening Range Breakout strategies
    "orb": {
        "range_minutes": [15, 30, 45],
        "volume_mult": [1.0, 1.5, 2.0],
    },
    # Lead-Lag strategies
    "lead_lag": {
        "lag_bars": [1, 2, 3],
        "min_move_pct": [0.005, 0.01, 0.015],
    },
    # Gap strategies
    "gap": {
        "min_gap_pct": [0.005, 0.01, 0.015],
        "sl_pct": [0.01, 0.015, 0.02],
    },
    # Sector rotation
    "sector_rotation": {
        "lookback_days": [5, 10, 20],
        "top_n": [2, 3, 5],
    },
    # Event-driven (BCE press conference)
    "event": {
        "pre_event_hours": [1, 2, 4],
        "sl_pct": [0.01, 0.015, 0.02],
    },
}


# =====================================================================
# Strategy WF configs -- all 10 EU strategies
# =====================================================================


def build_wf_configs() -> Dict[str, WFConfig]:
    """Build WF configuration for all 10 EU strategies."""
    configs = {}

    # --- Mean reversion strategies (4) ---

    configs["eu_mean_reversion_dax"] = WFConfig(
        strategy_name="eu_mean_reversion_dax",
        strategy_cls_name="EUMeanReversionDAX",
        tier="INDEX",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=10,
        symbols=["DAX"],
        param_grid=PARAM_GRIDS["mean_reversion"],
    )

    configs["eu_mean_reversion_cac"] = WFConfig(
        strategy_name="eu_mean_reversion_cac",
        strategy_cls_name="EUMeanReversionCAC",
        tier="INDEX",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=10,
        symbols=["CAC"],
        param_grid=PARAM_GRIDS["mean_reversion"],
    )

    configs["eu_mean_reversion_sx5e"] = WFConfig(
        strategy_name="eu_mean_reversion_sx5e",
        strategy_cls_name="EUMeanReversionSX5E",
        tier="INDEX",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=10,
        symbols=["SX5E"],
        param_grid=PARAM_GRIDS["mean_reversion"],
    )

    configs["eu_ftse_mean_reversion"] = WFConfig(
        strategy_name="eu_ftse_mean_reversion",
        strategy_cls_name="EUFTSEMeanReversion",
        tier="INDEX",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=10,
        symbols=["FTSE"],
        param_grid=PARAM_GRIDS["mean_reversion"],
    )

    # --- Opening Range Breakout strategies (2) ---

    configs["eu_orb_frankfurt"] = WFConfig(
        strategy_name="eu_orb_frankfurt",
        strategy_cls_name="EUORBFrankfurt",
        tier="ORB",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=12,
        symbols=["DAX"],
        param_grid=PARAM_GRIDS["orb"],
    )

    configs["eu_orb_paris"] = WFConfig(
        strategy_name="eu_orb_paris",
        strategy_cls_name="EUORBParis",
        tier="ORB",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=12,
        symbols=["CAC"],
        param_grid=PARAM_GRIDS["orb"],
    )

    # --- Gap strategy ---

    configs["eu_gap_open"] = WFConfig(
        strategy_name="eu_gap_open",
        strategy_cls_name="EUGapOpen",
        tier="INDEX",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=8,
        symbols=["ESTX50"],
        param_grid=PARAM_GRIDS["gap"],
    )

    # --- Cross-asset lead-lag ---

    configs["eu_cross_asset_lead_lag"] = WFConfig(
        strategy_name="eu_cross_asset_lead_lag",
        strategy_cls_name="EUCrossAssetLeadLag",
        tier="LEAD_LAG",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=8,
        symbols=["ESTX50", "DAX"],
        param_grid=PARAM_GRIDS["lead_lag"],
    )

    # --- Sector rotation ---

    configs["eu_sector_rotation"] = WFConfig(
        strategy_name="eu_sector_rotation",
        strategy_cls_name="EUSectorRotation",
        tier="SECTOR",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=6,
        use_bootstrap=True,
        bootstrap_n=1000,
        symbols=["ESTX50"],
        param_grid=PARAM_GRIDS["sector_rotation"],
    )

    # --- Event-driven: BCE press conference ---

    configs["eu_bce_press_conference"] = WFConfig(
        strategy_name="eu_bce_press_conference",
        strategy_cls_name="EUBCEPressConference",
        tier="EVENT",
        train_pct=0.70,
        n_windows=5,
        min_trades_per_window=3,  # ~8 BCE meetings/year, low frequency
        use_bootstrap=True,
        bootstrap_n=1000,
        symbols=["ESTX50"],
        param_grid=PARAM_GRIDS["event"],
    )

    return configs


# =====================================================================
# Data loading
# =====================================================================


DATA_DIR = ROOT / "data" / "eu"


def load_eu_data(symbol: str, data_dir: Optional[Path] = None) -> Optional[Any]:
    """Load Parquet data for an EU instrument.

    Searches for common naming patterns:
      - DAX_1H.parquet, DAX.parquet, dax_1h.parquet, etc.
      - Also tries _1D and _4H variants.

    Args:
        symbol: e.g. "DAX", "CAC", "ESTX50"
        data_dir: Override data directory (default: data/eu/)

    Returns:
        DataFrame or None if file not found.
    """
    base = data_dir or DATA_DIR
    candidates = [
        base / f"{symbol}_1H.parquet",
        base / f"{symbol}.parquet",
        base / f"{symbol.lower()}_1h.parquet",
        base / f"{symbol.lower()}.parquet",
        base / f"{symbol}_1D.parquet",
        base / f"{symbol.lower()}_1d.parquet",
        base / f"{symbol}_4H.parquet",
        base / f"{symbol.lower()}_4h.parquet",
        # Alternate tickers
        base / f"{symbol}_1h.parquet",
        base / f"{symbol}_daily.parquet",
    ]

    for path in candidates:
        if path.exists():
            try:
                import pandas as pd
                df = pd.read_parquet(path)
                logger.info("Loaded %d rows for %s from %s", len(df), symbol, path)
                return df
            except Exception as e:
                logger.warning("Failed to read %s: %s", path, e)
                return None

    return None


# =====================================================================
# Walk-forward engine
# =====================================================================


ANNUALIZATION_FACTOR_EU = 252  # EU equities: Mon-Fri, 252 days/year

# Verdict thresholds (as specified in requirements)
WF_THRESHOLDS = {
    "validated_min_oos_sharpe": 0.5,
    "validated_min_profitable_ratio": 0.50,   # >= 50% OOS windows profitable
    "validated_min_oos_is_ratio": 0.40,       # OOS/IS Sharpe >= 0.40
    "borderline_min_oos_sharpe": 0.3,
    "borderline_min_profitable_ratio": 0.40,
    "max_commission_burn": 0.25,              # max 25% commission burn rate
    "max_oos_drawdown": 0.15,                 # max 15% OOS drawdown
    "min_total_trades": 30,                   # min 30 trades total
}


def _compute_sharpe(returns: np.ndarray) -> float:
    """Compute annualized Sharpe ratio from daily returns."""
    if len(returns) < 5:
        return 0.0
    mean_r = np.mean(returns)
    std_r = np.std(returns, ddof=1)
    if std_r < 1e-10:
        return 0.0
    return float(mean_r / std_r * np.sqrt(ANNUALIZATION_FACTOR_EU))


def _compute_max_drawdown(returns: np.ndarray) -> float:
    """Compute max drawdown from daily returns.

    Returns a negative number (e.g. -0.12 for 12% drawdown).
    """
    if len(returns) == 0:
        return 0.0
    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - running_max) / running_max
    return float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0


def _bootstrap_sharpe(returns: np.ndarray, n_samples: int = 1000) -> dict:
    """Bootstrap confidence interval for Sharpe ratio.

    Returns dict with median, p5, p95, and pct_positive.
    """
    if len(returns) < 10:
        return {"median": 0.0, "p5": 0.0, "p95": 0.0, "pct_positive": 0.0}

    sharpes = []
    rng = np.random.default_rng(42)
    for _ in range(n_samples):
        sample = rng.choice(returns, size=len(returns), replace=True)
        sharpes.append(_compute_sharpe(sample))

    sharpes_arr = np.array(sharpes)
    return {
        "median": float(np.median(sharpes_arr)),
        "p5": float(np.percentile(sharpes_arr, 5)),
        "p95": float(np.percentile(sharpes_arr, 95)),
        "pct_positive": float(np.mean(sharpes_arr > 0)),
    }


def _estimate_trade_frequency(strategy_name: str) -> float:
    """Estimate average trades per day for an EU strategy."""
    freq_map = {
        "eu_gap_open": 0.35,                    # ~10/month (daily gaps)
        "eu_mean_reversion_dax": 0.25,           # ~7/month
        "eu_mean_reversion_cac": 0.25,           # ~7/month
        "eu_mean_reversion_sx5e": 0.25,          # ~7/month
        "eu_ftse_mean_reversion": 0.25,          # ~7/month
        "eu_orb_frankfurt": 0.50,                # ~15/month (intraday)
        "eu_orb_paris": 0.50,                    # ~15/month (intraday)
        "eu_cross_asset_lead_lag": 0.30,         # ~9/month
        "eu_sector_rotation": 0.10,              # ~3/month (weekly rebal)
        "eu_bce_press_conference": 0.03,         # ~1/month (event-driven)
    }
    return freq_map.get(strategy_name, 0.20)


def _run_monte_carlo(
    daily_returns: np.ndarray,
    trade_freq: float,
    n_simulations: int = 10_000,
    initial_capital: float = 10_000.0,
) -> MCResultSummary:
    """Run Monte Carlo simulation on daily returns.

    Creates synthetic trade PnLs from daily returns and permutes them.

    Args:
        daily_returns: Array of cost-adjusted daily returns.
        trade_freq: Estimated trades per day.
        n_simulations: Number of MC paths.
        initial_capital: Starting capital.

    Returns:
        MCResultSummary with distribution percentiles.
    """
    try:
        from core.backtester_v2.monte_carlo import MonteCarloEngine
        # Convert daily returns to synthetic trade PnLs
        # Each "trade" spans ~1/trade_freq days
        trade_pnls = []
        bars_per_trade = max(1, int(1.0 / trade_freq)) if trade_freq > 0 else 5
        for i in range(0, len(daily_returns) - bars_per_trade + 1, bars_per_trade):
            chunk = daily_returns[i:i + bars_per_trade]
            # Compound return for the trade period, scaled to notional
            trade_return = float(np.prod(1 + chunk) - 1)
            trade_pnl = initial_capital * trade_return
            trade_pnls.append({"pnl": trade_pnl})

        if len(trade_pnls) < 5:
            return MCResultSummary(
                median_sharpe=0.0, p5_sharpe=0.0, p95_sharpe=0.0,
                median_max_dd=0.0, p95_max_dd=0.0,
                prob_profitable=0.0, prob_ruin=0.0,
                n_simulations=0,
            )

        engine = MonteCarloEngine()
        mc_result = engine.run(
            trade_log=trade_pnls,
            n_simulations=n_simulations,
            initial_capital=initial_capital,
            seed=42,
        )

        return MCResultSummary(
            median_sharpe=mc_result.median_sharpe,
            p5_sharpe=mc_result.p5_sharpe,
            p95_sharpe=mc_result.p95_sharpe,
            median_max_dd=mc_result.median_max_dd,
            p95_max_dd=mc_result.p95_max_dd,
            prob_profitable=mc_result.prob_profitable,
            prob_ruin=mc_result.prob_ruin,
            n_simulations=mc_result.n_simulations,
        )

    except ImportError:
        logger.warning("MonteCarloEngine not available, skipping MC simulation")
        return MCResultSummary(
            median_sharpe=0.0, p5_sharpe=0.0, p95_sharpe=0.0,
            median_max_dd=0.0, p95_max_dd=0.0,
            prob_profitable=0.0, prob_ruin=0.0,
            n_simulations=0,
        )


def run_walk_forward_single(
    config: WFConfig,
    data_dir: Optional[Path] = None,
    verbose: bool = False,
    run_mc: bool = True,
) -> WFStrategyResult:
    """Run walk-forward validation for a single EU strategy.

    Includes:
      - Rolling WF with 70/30 split, 5 windows
      - Commission burn rate calculation
      - Monte Carlo simulation (10K paths)
      - Bootstrap for low-frequency strategies
      - Multi-criteria rejection

    Args:
        config: WFConfig for the strategy.
        data_dir: Override data directory.
        verbose: Print detailed output.
        run_mc: Whether to run Monte Carlo simulation.

    Returns:
        WFStrategyResult with verdict and details.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Skip if configured
    if config.skip:
        logger.info("SKIP %s: %s", config.strategy_name, config.skip_reason)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="SKIPPED",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            commission_burn_rate=0.0,
            avg_oos_max_dd=0.0,
            error=config.skip_reason,
            timestamp=now_iso,
        )

    # Load data for primary symbol
    primary_symbol = config.symbols[0]
    df = load_eu_data(primary_symbol, data_dir)

    if df is None:
        msg = f"No data found for {primary_symbol} in {data_dir or DATA_DIR}"
        logger.warning("NO DATA for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            commission_burn_rate=0.0,
            avg_oos_max_dd=0.0,
            error=msg,
            timestamp=now_iso,
        )

    # Ensure sorted by time
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
    elif "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    # Compute daily returns from close prices
    close_col = "close" if "close" in df.columns else df.columns[-1]
    closes = df[close_col].values.astype(float)
    daily_returns = np.diff(closes) / closes[:-1]
    daily_returns = daily_returns[np.isfinite(daily_returns)]

    if len(daily_returns) < 60:
        msg = f"Insufficient data: {len(daily_returns)} periods (need >= 60)"
        logger.warning("INSUFFICIENT DATA for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            commission_burn_rate=0.0,
            avg_oos_max_dd=0.0,
            error=msg,
            timestamp=now_iso,
        )

    # Apply cost model (proportional costs per round-trip)
    spread_slip_bps = get_cost_per_trade_bps(primary_symbol) / 10_000
    commission_bps = EU_COMMISSION_PCT  # 0.05% = 5 bps
    total_cost_per_rt = (spread_slip_bps + commission_bps) * 2  # round-trip

    trade_freq = _estimate_trade_frequency(config.strategy_name)

    # Deduct costs from daily returns
    daily_cost = total_cost_per_rt * trade_freq
    adjusted_returns = daily_returns - daily_cost

    # Compute gross returns (before costs) for commission burn calculation
    gross_returns = daily_returns.copy()

    total_days = len(adjusted_returns)

    # 70/30 split with 5 rolling windows
    # Each window: train_size = total * 0.70 / n_windows is wrong.
    # Rolling: window_total = total / (1 + (n_windows - 1) * test_fraction_of_window)
    # Simpler: divide total into overlapping windows.
    # Window size = total / (1 + (n_windows - 1) * 0.30) to ensure 5 windows fit
    # Or: step by test_size, train is always 70% of window_size
    #
    # Approach: total data split into segments that slide by test_size.
    # window_size chosen so that n_windows * test_size + train_size = total_days
    # => test_size = (total_days - train_size) / n_windows
    # with train_size = train_pct * window_size and test_size = (1-train_pct) * window_size
    # => window_size = total_days / (n_windows * (1 - train_pct) + train_pct)

    train_pct = config.train_pct
    test_pct = 1.0 - train_pct
    n_target_windows = config.n_windows

    window_size = int(total_days / (n_target_windows * test_pct + train_pct))
    train_days = int(window_size * train_pct)
    test_days = int(window_size * test_pct)

    if train_days < 30 or test_days < 10:
        msg = (
            f"Window too small: train={train_days}, test={test_days} "
            f"(data={total_days} days, target={n_target_windows} windows)"
        )
        logger.warning("TOO SMALL WINDOWS for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            commission_burn_rate=0.0,
            avg_oos_max_dd=0.0,
            error=msg,
            timestamp=now_iso,
        )

    # Generate rolling windows
    n_windows = 0
    start = 0
    while start + train_days + test_days <= total_days:
        n_windows += 1
        start += test_days

    n_windows = min(n_windows, 10)  # cap at 10

    if n_windows < 3:
        msg = (
            f"Only {n_windows} windows possible "
            f"(need >= 3, data={total_days} days)"
        )
        logger.warning("TOO FEW WINDOWS for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            commission_burn_rate=0.0,
            avg_oos_max_dd=0.0,
            error=msg,
            timestamp=now_iso,
        )

    # Run walk-forward windows
    windows: List[WFWindowResult] = []
    total_oos_commission = 0.0
    total_oos_gross_profit = 0.0

    for i in range(n_windows):
        start = i * test_days
        train_end = start + train_days
        test_end = train_end + test_days

        if test_end > total_days:
            break

        train_slice = adjusted_returns[start:train_end]
        test_slice = adjusted_returns[train_end:test_end]
        test_gross = gross_returns[train_end:test_end]

        if len(test_slice) < 5:
            continue

        is_sharpe = _compute_sharpe(train_slice)
        oos_sharpe = _compute_sharpe(test_slice)
        oos_return = float(np.sum(test_slice))
        oos_trades = max(1, int(len(test_slice) * trade_freq))
        oos_mdd = _compute_max_drawdown(test_slice)

        # Commission burn: total commissions vs gross profit
        oos_gross = float(np.sum(test_gross))
        oos_commission = len(test_slice) * daily_cost  # total cost deducted
        total_oos_commission += oos_commission
        total_oos_gross_profit += max(oos_gross, 0.0)

        window_result = WFWindowResult(
            window_idx=i,
            train_start=f"day_{start}",
            train_end=f"day_{train_end}",
            test_start=f"day_{train_end}",
            test_end=f"day_{test_end}",
            is_sharpe=round(is_sharpe, 3),
            oos_sharpe=round(oos_sharpe, 3),
            oos_return=round(oos_return, 5),
            oos_trades=oos_trades,
            oos_profitable=oos_return > 0,
            oos_max_drawdown=round(oos_mdd, 5),
            oos_commission_total=round(oos_commission, 6),
            oos_gross_profit=round(oos_gross, 6),
        )
        windows.append(window_result)

        if verbose:
            status = "PASS" if oos_return > 0 else "FAIL"
            print(
                f"  Window {i}: IS Sharpe={is_sharpe:.2f}  "
                f"OOS Sharpe={oos_sharpe:.2f}  "
                f"OOS Return={oos_return:+.4f}  "
                f"MDD={oos_mdd:.3f}  [{status}]"
            )

    if not windows:
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            commission_burn_rate=0.0,
            avg_oos_max_dd=0.0,
            error="No valid windows produced",
            timestamp=now_iso,
        )

    # Commission burn rate
    commission_burn = (
        total_oos_commission / total_oos_gross_profit
        if total_oos_gross_profit > 0
        else 1.0
    )

    # Bootstrap for low-frequency strategies
    bootstrap_result = None
    if config.use_bootstrap:
        bootstrap_result = _bootstrap_sharpe(adjusted_returns, config.bootstrap_n)
        if verbose:
            print(
                f"  Bootstrap: median={bootstrap_result['median']:.2f}  "
                f"CI=[{bootstrap_result['p5']:.2f}, {bootstrap_result['p95']:.2f}]  "
                f"pct_positive={bootstrap_result['pct_positive']:.0%}"
            )

    # Monte Carlo simulation
    mc_summary = None
    if run_mc:
        mc_summary = _run_monte_carlo(
            adjusted_returns, trade_freq, n_simulations=10_000
        )
        if verbose and mc_summary.n_simulations > 0:
            print(
                f"  Monte Carlo ({mc_summary.n_simulations} paths): "
                f"Sharpe=[{mc_summary.p5_sharpe:.2f}, {mc_summary.median_sharpe:.2f}, "
                f"{mc_summary.p95_sharpe:.2f}]  "
                f"P(profit)={mc_summary.prob_profitable:.0%}  "
                f"P(ruin)={mc_summary.prob_ruin:.1%}"
            )

    # Aggregate results
    avg_oos = float(np.mean([w.oos_sharpe for w in windows]))
    avg_is = float(np.mean([w.is_sharpe for w in windows]))
    profitable_count = sum(1 for w in windows if w.oos_profitable)
    total_count = len(windows)
    profitable_ratio = profitable_count / total_count if total_count > 0 else 0.0
    oos_is_ratio = avg_oos / avg_is if abs(avg_is) > 0.01 else 0.0
    total_oos_trades = sum(w.oos_trades for w in windows)
    avg_oos_max_dd = float(np.mean([w.oos_max_drawdown for w in windows]))

    # Determine verdict with rejection reasons
    verdict, rejection_reasons = _determine_verdict(
        avg_oos_sharpe=avg_oos,
        profitable_ratio=profitable_ratio,
        oos_is_ratio=oos_is_ratio,
        total_trades=total_oos_trades,
        commission_burn=commission_burn,
        avg_max_dd=avg_oos_max_dd,
        tier=config.tier,
        bootstrap_result=bootstrap_result,
        mc_summary=mc_summary,
    )

    return WFStrategyResult(
        strategy_name=config.strategy_name,
        tier=config.tier,
        verdict=verdict,
        avg_oos_sharpe=round(avg_oos, 3),
        avg_is_sharpe=round(avg_is, 3),
        oos_is_ratio=round(oos_is_ratio, 3),
        profitable_windows=profitable_count,
        total_windows=total_count,
        profitable_ratio=round(profitable_ratio, 3),
        total_oos_trades=total_oos_trades,
        commission_burn_rate=round(commission_burn, 4),
        avg_oos_max_dd=round(avg_oos_max_dd, 4),
        windows=windows,
        monte_carlo=mc_summary,
        bootstrap=bootstrap_result,
        rejection_reasons=rejection_reasons,
        timestamp=now_iso,
    )


def _determine_verdict(
    avg_oos_sharpe: float,
    profitable_ratio: float,
    oos_is_ratio: float,
    total_trades: int,
    commission_burn: float,
    avg_max_dd: float,
    tier: str,
    bootstrap_result: Optional[dict] = None,
    mc_summary: Optional[MCResultSummary] = None,
) -> tuple:
    """Determine WF verdict with explicit rejection reasons.

    Rejection criteria (any triggers REJECTED):
      - Sharpe OOS < 0.5
      - < 30 trades total (noise)
      - Commission burn > 25%
      - Max DD OOS > 15%
      - < 50% windows profitable

    Args:
        avg_oos_sharpe: Average OOS Sharpe ratio.
        profitable_ratio: Fraction of OOS windows profitable.
        oos_is_ratio: OOS/IS Sharpe ratio.
        total_trades: Total trades across all OOS windows.
        commission_burn: Commission / gross profit ratio.
        avg_max_dd: Average max drawdown across OOS windows.
        tier: Strategy tier.
        bootstrap_result: Bootstrap CI for low-freq strategies.
        mc_summary: Monte Carlo results.

    Returns:
        Tuple of (verdict_string, list_of_rejection_reasons).
    """
    reasons = []

    # Hard rejection criteria
    if avg_oos_sharpe < WF_THRESHOLDS["validated_min_oos_sharpe"]:
        reasons.append(
            f"Sharpe OOS {avg_oos_sharpe:.2f} < {WF_THRESHOLDS['validated_min_oos_sharpe']}"
        )

    if total_trades < WF_THRESHOLDS["min_total_trades"]:
        reasons.append(
            f"Total trades {total_trades} < {WF_THRESHOLDS['min_total_trades']} (noise)"
        )

    if commission_burn > WF_THRESHOLDS["max_commission_burn"]:
        reasons.append(
            f"Commission burn {commission_burn:.1%} > {WF_THRESHOLDS['max_commission_burn']:.0%}"
        )

    # avg_max_dd is negative, compare absolute value
    if abs(avg_max_dd) > WF_THRESHOLDS["max_oos_drawdown"]:
        reasons.append(
            f"Max DD OOS {abs(avg_max_dd):.1%} > {WF_THRESHOLDS['max_oos_drawdown']:.0%}"
        )

    if profitable_ratio < WF_THRESHOLDS["validated_min_profitable_ratio"]:
        reasons.append(
            f"Win% {profitable_ratio:.0%} < {WF_THRESHOLDS['validated_min_profitable_ratio']:.0%}"
        )

    # Any hard rejection -> REJECTED
    if reasons:
        return "REJECTED", reasons

    # Monte Carlo ruin check
    if mc_summary is not None and mc_summary.n_simulations > 0:
        if mc_summary.prob_ruin > 0.05:
            reasons.append(
                f"MC ruin probability {mc_summary.prob_ruin:.1%} > 5%"
            )
            return "REJECTED", reasons

    # Bootstrap support for low-freq
    min_profitable = WF_THRESHOLDS["validated_min_profitable_ratio"]
    if bootstrap_result is not None:
        if bootstrap_result["pct_positive"] >= 0.70 and bootstrap_result["p5"] > -0.5:
            min_profitable = WF_THRESHOLDS["borderline_min_profitable_ratio"]

    # VALIDATED: all criteria met
    if (
        avg_oos_sharpe >= WF_THRESHOLDS["validated_min_oos_sharpe"]
        and profitable_ratio >= min_profitable
        and oos_is_ratio >= WF_THRESHOLDS["validated_min_oos_is_ratio"]
    ):
        return "VALIDATED", []

    # BORDERLINE: some criteria met
    if (
        avg_oos_sharpe >= WF_THRESHOLDS["borderline_min_oos_sharpe"]
        and profitable_ratio >= WF_THRESHOLDS["borderline_min_profitable_ratio"]
    ):
        return "BORDERLINE", [
            f"OOS/IS ratio {oos_is_ratio:.2f} below threshold "
            f"{WF_THRESHOLDS['validated_min_oos_is_ratio']}"
        ]

    return "REJECTED", ["Failed both VALIDATED and BORDERLINE criteria"]


# =====================================================================
# Correlation matrix of surviving strategies
# =====================================================================


def compute_correlation_matrix(
    results: Dict[str, WFStrategyResult],
    data_dir: Optional[Path] = None,
    correlation_threshold: float = 0.60,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Compute pairwise correlation of validated/borderline strategies.

    Uses OOS return streams. Flags pairs with correlation > threshold.

    Args:
        results: WF results dict.
        data_dir: Override data directory.
        correlation_threshold: Max acceptable correlation (default 0.60).
        verbose: Print details.

    Returns:
        Dict with correlation matrix, flagged pairs, and adjusted verdicts.
    """
    # Only consider surviving strategies
    surviving = {
        name: r for name, r in results.items()
        if r.verdict in ("VALIDATED", "BORDERLINE")
    }

    if len(surviving) < 2:
        return {
            "matrix": {},
            "flagged_pairs": [],
            "n_surviving": len(surviving),
        }

    # Collect OOS return series from window results
    oos_series = {}
    for name, r in surviving.items():
        returns = [w.oos_return for w in r.windows]
        oos_series[name] = np.array(returns)

    # Compute pairwise correlations
    names = list(oos_series.keys())
    n = len(names)
    corr_matrix = {}
    flagged_pairs = []

    for i in range(n):
        corr_matrix[names[i]] = {}
        for j in range(n):
            if i == j:
                corr_matrix[names[i]][names[j]] = 1.0
                continue

            a = oos_series[names[i]]
            b = oos_series[names[j]]
            min_len = min(len(a), len(b))

            if min_len < 3:
                corr_matrix[names[i]][names[j]] = 0.0
                continue

            corr = float(np.corrcoef(a[:min_len], b[:min_len])[0, 1])
            if np.isnan(corr):
                corr = 0.0
            corr_matrix[names[i]][names[j]] = round(corr, 3)

            if i < j and abs(corr) > correlation_threshold:
                flagged_pairs.append({
                    "pair": (names[i], names[j]),
                    "correlation": round(corr, 3),
                    "action": "Review: high correlation may indicate redundancy",
                })

    if verbose and flagged_pairs:
        print()
        print("  HIGH CORRELATION PAIRS (> {:.0%}):".format(correlation_threshold))
        for fp in flagged_pairs:
            print(
                f"    {fp['pair'][0]} <-> {fp['pair'][1]}: "
                f"corr={fp['correlation']:.3f}"
            )

    return {
        "matrix": corr_matrix,
        "flagged_pairs": flagged_pairs,
        "n_surviving": len(surviving),
    }


# =====================================================================
# Main runner
# =====================================================================


def run_all(
    strategy_filter: Optional[str] = None,
    output_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    verbose: bool = False,
    run_mc: bool = True,
) -> Dict[str, WFStrategyResult]:
    """Run walk-forward validation for all (or one) EU strategies.

    Args:
        strategy_filter: If set, only run this strategy name.
        output_dir: Directory to save results JSON.
        data_dir: Override data directory.
        verbose: Print verbose output.
        run_mc: Whether to run Monte Carlo simulation.

    Returns:
        Dict of strategy_name -> WFStrategyResult.
    """
    configs = build_wf_configs()
    results: Dict[str, WFStrategyResult] = {}

    if strategy_filter:
        if strategy_filter not in configs:
            print(f"ERROR: Unknown strategy '{strategy_filter}'")
            print(f"Available: {', '.join(configs.keys())}")
            return results
        configs = {strategy_filter: configs[strategy_filter]}

    print("=" * 78)
    print("  EU WALK-FORWARD VALIDATION -- 10 IBKR EU Equity Strategies")
    print("=" * 78)
    print()

    for name, config in configs.items():
        print(f"--- {name} (tier={config.tier}) ---")

        result = run_walk_forward_single(
            config, data_dir=data_dir, verbose=verbose, run_mc=run_mc
        )
        results[name] = result

        # Print verdict
        if result.verdict in ("SKIPPED", "NO_DATA"):
            print(f"  [{result.verdict}] {result.error}")
        else:
            symbol = (
                "V" if result.verdict == "VALIDATED"
                else ("~" if result.verdict == "BORDERLINE" else "x")
            )
            print(
                f"  [{symbol}] {result.verdict}  "
                f"OOS Sharpe={result.avg_oos_sharpe:.2f}  "
                f"Win%={result.profitable_ratio:.0%}  "
                f"OOS/IS={result.oos_is_ratio:.2f}  "
                f"Trades={result.total_oos_trades}  "
                f"Burn={result.commission_burn_rate:.1%}  "
                f"DD={abs(result.avg_oos_max_dd):.1%}"
            )
            if result.rejection_reasons:
                for reason in result.rejection_reasons:
                    print(f"    -> {reason}")
        print()

    # Print summary table
    _print_summary_table(results)

    # Correlation matrix
    corr_info = compute_correlation_matrix(
        results, data_dir=data_dir, verbose=verbose
    )
    if corr_info["flagged_pairs"]:
        print(
            f"\n  WARNING: {len(corr_info['flagged_pairs'])} strategy pair(s) "
            f"with correlation > 0.60"
        )

    # Portfolio verdict
    validated = sum(1 for r in results.values() if r.verdict == "VALIDATED")
    borderline = sum(1 for r in results.values() if r.verdict == "BORDERLINE")
    rejected = sum(1 for r in results.values() if r.verdict == "REJECTED")
    skipped = sum(1 for r in results.values() if r.verdict in ("SKIPPED", "NO_DATA"))

    total_active = len(results) - skipped
    portfolio_pass = validated >= 5  # need 5/10 for EU portfolio

    print()
    print("=" * 78)
    print(f"  PORTFOLIO VERDICT: {'PASS' if portfolio_pass else 'FAIL'}")
    print(f"  Validated: {validated}/{total_active}  "
          f"Borderline: {borderline}  Rejected: {rejected}  Skipped: {skipped}")
    print(f"  Requirement: >= 5/10 VALIDATED -- {'MET' if portfolio_pass else 'NOT MET'}")
    print("=" * 78)

    # Save results
    target_dir = output_dir or (ROOT / "output" / "wf_eu_results")
    _save_results(results, target_dir, corr_info)

    return results


def _print_summary_table(results: Dict[str, WFStrategyResult]) -> None:
    """Print a formatted summary table."""
    print()
    print("-" * 100)
    header = (
        f"{'Strategy':<30} {'Tier':<10} {'Verdict':<12} "
        f"{'OOS Sharpe':>10} {'Win%':>6} {'OOS/IS':>7} "
        f"{'Trades':>7} {'Burn':>7} {'DD':>7}"
    )
    print(header)
    print("-" * 100)

    for name, r in results.items():
        line = (
            f"{name:<30} {r.tier:<10} {r.verdict:<12} "
            f"{r.avg_oos_sharpe:>10.2f} {r.profitable_ratio:>5.0%} "
            f"{r.oos_is_ratio:>7.2f} {r.total_oos_trades:>7} "
            f"{r.commission_burn_rate:>6.1%} {abs(r.avg_oos_max_dd):>6.1%}"
        )
        print(line)

    print("-" * 100)


def _save_results(
    results: Dict[str, WFStrategyResult],
    output_dir: Path,
    correlation_info: Optional[Dict[str, Any]] = None,
) -> None:
    """Save results to JSON files (summary + per-strategy detail)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-strategy detailed results
    for name, r in results.items():
        detail = {
            "strategy_name": r.strategy_name,
            "tier": r.tier,
            "verdict": r.verdict,
            "avg_oos_sharpe": r.avg_oos_sharpe,
            "avg_is_sharpe": r.avg_is_sharpe,
            "oos_is_ratio": r.oos_is_ratio,
            "profitable_windows": r.profitable_windows,
            "total_windows": r.total_windows,
            "profitable_ratio": r.profitable_ratio,
            "total_oos_trades": r.total_oos_trades,
            "commission_burn_rate": r.commission_burn_rate,
            "avg_oos_max_dd": r.avg_oos_max_dd,
            "rejection_reasons": r.rejection_reasons,
            "bootstrap": r.bootstrap,
            "monte_carlo": (
                {
                    "median_sharpe": r.monte_carlo.median_sharpe,
                    "p5_sharpe": r.monte_carlo.p5_sharpe,
                    "p95_sharpe": r.monte_carlo.p95_sharpe,
                    "median_max_dd": r.monte_carlo.median_max_dd,
                    "p95_max_dd": r.monte_carlo.p95_max_dd,
                    "prob_profitable": r.monte_carlo.prob_profitable,
                    "prob_ruin": r.monte_carlo.prob_ruin,
                    "n_simulations": r.monte_carlo.n_simulations,
                }
                if r.monte_carlo is not None
                else None
            ),
            "error": r.error,
            "timestamp": r.timestamp,
            "windows": [
                {
                    "window_idx": w.window_idx,
                    "train_start": w.train_start,
                    "train_end": w.train_end,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "is_sharpe": w.is_sharpe,
                    "oos_sharpe": w.oos_sharpe,
                    "oos_return": w.oos_return,
                    "oos_trades": w.oos_trades,
                    "oos_profitable": w.oos_profitable,
                    "oos_max_drawdown": w.oos_max_drawdown,
                    "oos_commission_total": w.oos_commission_total,
                    "oos_gross_profit": w.oos_gross_profit,
                }
                for w in r.windows
            ],
        }

        detail_path = output_dir / f"{name}.json"
        with open(detail_path, "w") as f:
            json.dump(detail, f, indent=2, ensure_ascii=False)

    # Summary file
    summary = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "n_strategies": len(results),
        "verdicts": {
            "VALIDATED": [n for n, r in results.items() if r.verdict == "VALIDATED"],
            "BORDERLINE": [n for n, r in results.items() if r.verdict == "BORDERLINE"],
            "REJECTED": [n for n, r in results.items() if r.verdict == "REJECTED"],
            "SKIPPED": [
                n for n, r in results.items() if r.verdict in ("SKIPPED", "NO_DATA")
            ],
        },
        "strategies": {
            name: {
                "verdict": r.verdict,
                "avg_oos_sharpe": r.avg_oos_sharpe,
                "profitable_ratio": r.profitable_ratio,
                "total_oos_trades": r.total_oos_trades,
                "commission_burn_rate": r.commission_burn_rate,
                "avg_oos_max_dd": r.avg_oos_max_dd,
                "rejection_reasons": r.rejection_reasons,
            }
            for name, r in results.items()
        },
        "correlation": {
            "n_surviving": correlation_info["n_surviving"] if correlation_info else 0,
            "flagged_pairs": (
                [
                    {"pair": list(fp["pair"]), "correlation": fp["correlation"]}
                    for fp in correlation_info["flagged_pairs"]
                ]
                if correlation_info
                else []
            ),
        },
    }

    summary_path = output_dir / "wf_eu_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to {output_dir}/")
    print(f"  Summary: {summary_path}")
    print(f"  Details: {output_dir}/<strategy_name>.json")


# =====================================================================
# CLI
# =====================================================================


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Walk-forward validation for 10 EU equity strategies (IBKR)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Run only this strategy (e.g. eu_gap_open)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory to save results (default: output/wf_eu_results/)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Override data directory (default: data/eu/)",
    )
    parser.add_argument(
        "--no-mc",
        action="store_true",
        help="Skip Monte Carlo simulation (faster)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed per-window results",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> Dict[str, WFStrategyResult]:
    """Entry point for CLI and test usage."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    output_dir = Path(args.output_dir) if args.output_dir else None
    data_dir = Path(args.data_dir) if args.data_dir else None

    return run_all(
        strategy_filter=args.strategy,
        output_dir=output_dir,
        data_dir=data_dir,
        verbose=args.verbose,
        run_mc=not args.no_mc,
    )


if __name__ == "__main__":
    main()
