"""Slippage stress testing for BacktesterV2 strategies.

Tests strategy performance at escalating slippage levels (1x, 2x, 3x, 5x)
to determine at what point a strategy breaks even. This identifies strategies
that are fragile to execution quality degradation.

Usage:
    from core.backtester_v2.slippage_stress import run_slippage_stress_test

    results = run_slippage_stress_test(
        strategy_cls=MyStrategy,
        data={"EURUSD": df},
        base_slippage_bps=2.0,
        stress_levels=[1.0, 2.0, 3.0, 5.0],
    )
    for level, metrics in results.items():
        print(f"{level}x: Sharpe={metrics['sharpe']:.2f}, PnL={metrics['total_pnl']:.2f}")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SlippageStressResult:
    """Result of a single slippage stress level."""

    level: float               # Multiplier (1.0 = base)
    slippage_bps: float        # Effective slippage in basis points
    sharpe: float
    total_pnl: float
    num_trades: int
    max_drawdown: float
    win_rate: float
    profit_factor: float
    is_profitable: bool        # total_pnl > 0
    break_even_degradation: float  # PnL change vs base level


@dataclass
class SlippageStressReport:
    """Aggregated slippage stress test report."""

    strategy_name: str
    base_slippage_bps: float
    levels: Dict[float, SlippageStressResult]
    break_even_level: Optional[float]   # Level at which strategy breaks even (None if always profitable or always losing)
    robustness_score: float             # 0-1, fraction of levels that remain profitable
    max_profitable_level: float         # Highest level still profitable


def _evaluate_strategy(
    strategy_class: Type,
    data: Dict[str, pd.DataFrame],
    slippage_bps: float,
    commission_per_share: float = 0.005,
    initial_capital: float = 100_000.0,
) -> Dict[str, Any]:
    """Evaluate a strategy with specific slippage on given data.

    Runs a simplified backtest: iterates bars, collects signals,
    computes PnL with the specified slippage. Compatible with any
    StrategyBase subclass.

    Args:
        strategy_class: Strategy class to instantiate.
        data: Dict of symbol -> DataFrame with OHLCV + DatetimeIndex.
        slippage_bps: Slippage in basis points applied to each fill.
        commission_per_share: Commission per share (default IBKR US).
        initial_capital: Starting capital.

    Returns:
        Dict with sharpe, num_trades, total_pnl, max_dd, win_rate, profit_factor.
    """
    try:
        from core.backtester_v2.types import Bar, PortfolioState
    except ImportError:
        from core.backtester_v2.types import Bar, PortfolioState

    strategy = strategy_class()

    trades: List[float] = []
    equity = initial_capital
    equity_curve = [equity]
    position: Optional[Dict[str, Any]] = None

    portfolio = PortfolioState(equity=equity, cash=equity)

    for symbol, df in data.items():
        for ts, row in df.iterrows():
            bar = Bar(
                symbol=symbol,
                timestamp=pd.Timestamp(ts),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 100_000)),
            )

            signal = strategy.on_bar(bar, portfolio)

            if signal is not None and position is None and signal.side == "BUY":
                slip = bar.close * slippage_bps / 10_000
                entry_price = bar.close + slip
                qty = max(int(equity * 0.1 / entry_price), 1)
                commission = qty * commission_per_share
                position = {
                    "entry": entry_price,
                    "qty": qty,
                    "commission_entry": commission,
                }
                equity -= commission

            elif signal is not None and position is not None and signal.side == "SELL":
                slip = bar.close * slippage_bps / 10_000
                exit_price = bar.close - slip
                pnl = (exit_price - position["entry"]) * position["qty"]
                commission = position["qty"] * commission_per_share
                pnl -= position["commission_entry"] + commission
                trades.append(pnl)
                equity += pnl
                position = None

            equity_curve.append(max(equity, 0.0))
            portfolio = PortfolioState(equity=equity, cash=equity)

    # Compute metrics
    num_trades = len(trades)
    total_pnl = sum(trades) if trades else 0.0

    # Sharpe from trade PnLs
    if num_trades >= 2:
        arr = np.array(trades)
        std = float(arr.std())
        sharpe = float(arr.mean() / std * math.sqrt(252)) if std > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown from equity curve
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, 1.0)
    max_dd = float(abs(dd.min())) if len(dd) > 0 else 0.0

    # Win rate and profit factor
    if trades:
        pnls = np.array(trades)
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0.0
        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        win_rate = 0.0
        profit_factor = 0.0

    return {
        "sharpe": sharpe,
        "num_trades": num_trades,
        "total_pnl": total_pnl,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
    }


def run_slippage_stress_test(
    strategy_cls: Type,
    data: Dict[str, pd.DataFrame],
    base_slippage_bps: float = 2.0,
    stress_levels: Optional[List[float]] = None,
    commission_per_share: float = 0.005,
    initial_capital: float = 100_000.0,
) -> SlippageStressReport:
    """Test strategy at escalating slippage levels.

    Runs the strategy at 1x, 2x, 3x, 5x (default) of the base slippage
    to determine at what point it breaks even.

    Args:
        strategy_cls: Strategy class (StrategyBase subclass).
        data: Dict of symbol -> DataFrame with OHLCV + DatetimeIndex.
        base_slippage_bps: Base slippage in basis points (default 2.0).
        stress_levels: Multiplier levels to test (default [1.0, 2.0, 3.0, 5.0]).
        commission_per_share: Commission per share.
        initial_capital: Starting capital.

    Returns:
        SlippageStressReport with per-level results and robustness score.
    """
    if stress_levels is None:
        stress_levels = [1.0, 2.0, 3.0, 5.0]

    strategy_name = getattr(strategy_cls, "name", strategy_cls.__name__)
    if callable(strategy_name) or isinstance(strategy_name, property):
        try:
            strategy_name = strategy_cls().name
        except Exception:
            strategy_name = strategy_cls.__name__

    logger.info(
        "Slippage stress test: %s, base=%.1f bps, levels=%s",
        strategy_name, base_slippage_bps, stress_levels,
    )

    levels_results: Dict[float, SlippageStressResult] = {}
    base_pnl: Optional[float] = None

    for level in sorted(stress_levels):
        effective_slippage = base_slippage_bps * level

        metrics = _evaluate_strategy(
            strategy_class=strategy_cls,
            data=data,
            slippage_bps=effective_slippage,
            commission_per_share=commission_per_share,
            initial_capital=initial_capital,
        )

        if base_pnl is None:
            base_pnl = metrics["total_pnl"]

        degradation = (
            (metrics["total_pnl"] - base_pnl) / abs(base_pnl)
            if abs(base_pnl) > 0
            else 0.0
        )

        result = SlippageStressResult(
            level=level,
            slippage_bps=effective_slippage,
            sharpe=round(metrics["sharpe"], 3),
            total_pnl=round(metrics["total_pnl"], 2),
            num_trades=metrics["num_trades"],
            max_drawdown=round(metrics["max_dd"], 4),
            win_rate=round(metrics["win_rate"], 3),
            profit_factor=round(metrics["profit_factor"], 3),
            is_profitable=metrics["total_pnl"] > 0,
            break_even_degradation=round(degradation, 4),
        )
        levels_results[level] = result

        logger.info(
            "  %sx (%.1f bps): Sharpe=%.2f, PnL=%.2f, trades=%d, profitable=%s",
            level, effective_slippage, result.sharpe, result.total_pnl,
            result.num_trades, result.is_profitable,
        )

    # Determine break-even level via linear interpolation
    break_even_level = _find_break_even_level(levels_results)

    # Robustness score: fraction of levels that remain profitable
    profitable_count = sum(1 for r in levels_results.values() if r.is_profitable)
    robustness = profitable_count / len(levels_results) if levels_results else 0.0

    # Max profitable level
    profitable_levels = [
        r.level for r in levels_results.values() if r.is_profitable
    ]
    max_profitable = max(profitable_levels) if profitable_levels else 0.0

    report = SlippageStressReport(
        strategy_name=strategy_name,
        base_slippage_bps=base_slippage_bps,
        levels=levels_results,
        break_even_level=break_even_level,
        robustness_score=round(robustness, 2),
        max_profitable_level=max_profitable,
    )

    return report


def _find_break_even_level(
    levels_results: Dict[float, SlippageStressResult],
) -> Optional[float]:
    """Find the slippage level at which strategy PnL crosses zero.

    Uses linear interpolation between the last profitable and first
    unprofitable level.

    Returns:
        Break-even multiplier, or None if always profitable/always losing.
    """
    sorted_levels = sorted(levels_results.keys())
    pnls = [(lvl, levels_results[lvl].total_pnl) for lvl in sorted_levels]

    # Check if all profitable or all losing
    all_profitable = all(p > 0 for _, p in pnls)
    all_losing = all(p <= 0 for _, p in pnls)

    if all_profitable or all_losing:
        return None

    # Find transition point
    for i in range(len(pnls) - 1):
        lvl_a, pnl_a = pnls[i]
        lvl_b, pnl_b = pnls[i + 1]

        if pnl_a > 0 and pnl_b <= 0:
            # Linear interpolation: find level where PnL = 0
            if abs(pnl_a - pnl_b) < 1e-10:
                return lvl_a
            ratio = pnl_a / (pnl_a - pnl_b)
            break_even = lvl_a + ratio * (lvl_b - lvl_a)
            return round(break_even, 2)

    return None


def print_stress_report(report: SlippageStressReport) -> None:
    """Print a formatted slippage stress test report.

    Args:
        report: SlippageStressReport to display.
    """
    print()
    print("=" * 70)
    print(f"  SLIPPAGE STRESS TEST: {report.strategy_name}")
    print(f"  Base slippage: {report.base_slippage_bps:.1f} bps")
    print("=" * 70)
    print()

    print(
        f"{'Level':>6} {'Slip(bps)':>10} {'Sharpe':>8} "
        f"{'PnL':>12} {'Trades':>7} {'WinRate':>8} {'PF':>7} {'Status':>10}"
    )
    print("-" * 70)

    for level in sorted(report.levels.keys()):
        r = report.levels[level]
        status = "PROFIT" if r.is_profitable else "LOSS"
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 100 else "inf"
        print(
            f"{r.level:>5.1f}x {r.slippage_bps:>9.1f} {r.sharpe:>8.2f} "
            f"{r.total_pnl:>12.2f} {r.num_trades:>7} "
            f"{r.win_rate:>7.1%} {pf_str:>7} {status:>10}"
        )

    print("-" * 70)
    print()

    if report.break_even_level is not None:
        print(f"  Break-even at: {report.break_even_level:.1f}x slippage "
              f"({report.break_even_level * report.base_slippage_bps:.1f} bps)")
    elif report.robustness_score == 1.0:
        print("  Strategy remains profitable at all tested slippage levels.")
    else:
        print("  Strategy is unprofitable even at base slippage.")

    print(f"  Robustness score: {report.robustness_score:.0%} "
          f"({sum(1 for r in report.levels.values() if r.is_profitable)}"
          f"/{len(report.levels)} levels profitable)")
    print(f"  Max profitable level: {report.max_profitable_level:.1f}x")
    print("=" * 70)


def report_to_dict(report: SlippageStressReport) -> Dict[str, Any]:
    """Convert SlippageStressReport to a JSON-serializable dict.

    Args:
        report: Report to serialize.

    Returns:
        Dict suitable for json.dump().
    """
    return {
        "strategy_name": report.strategy_name,
        "base_slippage_bps": report.base_slippage_bps,
        "break_even_level": report.break_even_level,
        "robustness_score": report.robustness_score,
        "max_profitable_level": report.max_profitable_level,
        "levels": {
            str(level): {
                "level": r.level,
                "slippage_bps": r.slippage_bps,
                "sharpe": r.sharpe,
                "total_pnl": r.total_pnl,
                "num_trades": r.num_trades,
                "max_drawdown": r.max_drawdown,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "is_profitable": r.is_profitable,
                "break_even_degradation": r.break_even_degradation,
            }
            for level, r in report.levels.items()
        },
    }
