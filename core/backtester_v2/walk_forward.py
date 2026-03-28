"""Walk-Forward Engine for BacktesterV2.

Performs rolling, expanding, or anchored walk-forward optimization
to validate strategy robustness on out-of-sample data.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np
import pandas as pd

from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import (
    BacktestConfig,
    BacktestResults,
    Bar,
    PortfolioState,
    Signal,
)

logger = logging.getLogger(__name__)


# ─── Config & Result dataclasses ─────────────────────────────────────


@dataclass
class WFConfig:
    """Walk-forward configuration."""

    train_months: int = 12
    test_months: int = 3
    min_windows: int = 3
    min_ratio: float = 0.4
    min_profitable_pct: float = 0.5
    backtest_config: BacktestConfig = field(default_factory=BacktestConfig)
    mode: str = "rolling"  # "rolling" | "expanding" | "anchored"


@dataclass
class WFWindowResult:
    """Result of a single walk-forward window."""

    window: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_sharpe: float
    test_sharpe: float
    test_trades: int
    test_pnl: float
    test_max_dd: float
    best_params: Dict[str, Any]
    oos_profitable: bool


@dataclass
class WFResult:
    """Aggregated walk-forward result with verdict."""

    verdict: str  # "VALIDATED" | "BORDERLINE" | "REJECTED"
    windows: List[WFWindowResult]
    avg_oos_sharpe: float
    avg_is_sharpe: float
    oos_is_ratio: float
    pct_profitable: float
    total_oos_trades: int


# ─── Walk-Forward Engine ─────────────────────────────────────────────


class WalkForwardEngine:
    """Walk-forward optimization engine integrated with BacktesterV2.

    Splits data into train/test windows, optimizes parameters on each
    training window via grid search, then evaluates on the test window.
    """

    MIN_TRADES_PER_WINDOW = 10

    def run(
        self,
        strategy_class: Type[StrategyBase],
        data: Dict[str, pd.DataFrame],
        config: WFConfig,
    ) -> WFResult:
        """Run walk-forward analysis.

        Args:
            strategy_class: Strategy class to instantiate and test.
            data: Dict of symbol -> DataFrame with OHLCV + DatetimeIndex.
            config: Walk-forward configuration.

        Returns:
            WFResult with verdict and per-window details.
        """
        windows = self._generate_windows(data, config)

        if len(windows) < config.min_windows:
            logger.warning(
                "Insufficient data: %d windows < min %d",
                len(windows), config.min_windows,
            )
            return WFResult(
                verdict="REJECTED",
                windows=[],
                avg_oos_sharpe=0.0,
                avg_is_sharpe=0.0,
                oos_is_ratio=0.0,
                pct_profitable=0.0,
                total_oos_trades=0,
            )

        results: List[WFWindowResult] = []
        for i, (train_data, test_data) in enumerate(windows):
            best_params = self._optimize(strategy_class, train_data, config)

            # Evaluate on training set
            train_metrics = self._evaluate(
                strategy_class, train_data, best_params, config
            )

            # Evaluate on test set
            test_metrics = self._evaluate(
                strategy_class, test_data, best_params, config
            )

            # Get date bounds from first symbol
            first_sym = next(iter(train_data))
            train_idx = train_data[first_sym].index
            test_idx = test_data[first_sym].index

            wr = WFWindowResult(
                window=i,
                train_start=train_idx[0],
                train_end=train_idx[-1],
                test_start=test_idx[0],
                test_end=test_idx[-1],
                train_sharpe=train_metrics["sharpe"],
                test_sharpe=test_metrics["sharpe"],
                test_trades=test_metrics["num_trades"],
                test_pnl=test_metrics["total_pnl"],
                test_max_dd=test_metrics["max_dd"],
                best_params=best_params,
                oos_profitable=test_metrics["total_pnl"] > 0,
            )
            results.append(wr)
            logger.info(
                "Window %d: IS Sharpe=%.2f, OOS Sharpe=%.2f, trades=%d",
                i, wr.train_sharpe, wr.test_sharpe, wr.test_trades,
            )

        return self._aggregate(results, config)

    def _generate_windows(
        self,
        data: Dict[str, pd.DataFrame],
        config: WFConfig,
    ) -> List[Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]]:
        """Generate train/test window pairs based on mode.

        Returns:
            List of (train_data, test_data) dict pairs.
        """
        # Use first symbol's index to determine date range
        first_sym = next(iter(data))
        idx = data[first_sym].index
        data_start = idx.min()
        data_end = idx.max()

        train_td = pd.DateOffset(months=config.train_months)
        test_td = pd.DateOffset(months=config.test_months)

        windows: List[Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]] = []
        anchor_start = data_start

        if config.mode == "anchored":
            # Anchored: train always starts from data_start, grows
            cursor = data_start + train_td
            while cursor + test_td <= data_end:
                train_end = cursor
                test_start = cursor
                test_end = cursor + test_td

                train_dict = {
                    sym: df.loc[anchor_start:train_end]
                    for sym, df in data.items()
                }
                test_dict = {
                    sym: df.loc[test_start:test_end]
                    for sym, df in data.items()
                }

                if all(len(v) > 0 for v in train_dict.values()) and all(
                    len(v) > 0 for v in test_dict.values()
                ):
                    windows.append((train_dict, test_dict))

                cursor += test_td

        elif config.mode == "expanding":
            # Expanding: train start is anchored, end advances by test_months
            cursor = data_start + train_td
            while cursor + test_td <= data_end:
                train_end = cursor
                test_start = cursor
                test_end = cursor + test_td

                train_dict = {
                    sym: df.loc[anchor_start:train_end]
                    for sym, df in data.items()
                }
                test_dict = {
                    sym: df.loc[test_start:test_end]
                    for sym, df in data.items()
                }

                if all(len(v) > 0 for v in train_dict.values()) and all(
                    len(v) > 0 for v in test_dict.values()
                ):
                    windows.append((train_dict, test_dict))

                cursor += test_td

        else:
            # Rolling (default): fixed-size train window slides forward
            cursor = data_start
            while cursor + train_td + test_td <= data_end:
                train_start = cursor
                train_end = cursor + train_td
                test_start = train_end
                test_end = train_end + test_td

                train_dict = {
                    sym: df.loc[train_start:train_end]
                    for sym, df in data.items()
                }
                test_dict = {
                    sym: df.loc[test_start:test_end]
                    for sym, df in data.items()
                }

                if all(len(v) > 0 for v in train_dict.values()) and all(
                    len(v) > 0 for v in test_dict.values()
                ):
                    windows.append((train_dict, test_dict))

                cursor += test_td

        return windows

    def _optimize(
        self,
        strategy_class: Type[StrategyBase],
        train_data: Dict[str, pd.DataFrame],
        config: WFConfig,
    ) -> Dict[str, Any]:
        """Optimize strategy parameters on training data.

        Uses grid search over the strategy's param_grid if defined.
        """
        strategy = strategy_class()
        param_grid = getattr(strategy, "param_grid", None)

        if not param_grid:
            return strategy.get_parameters()

        return self._grid_search(strategy_class, train_data, param_grid, config)

    def _grid_search(
        self,
        strategy_class: Type[StrategyBase],
        data: Dict[str, pd.DataFrame],
        param_grid: Dict[str, List[Any]],
        config: WFConfig,
    ) -> Dict[str, Any]:
        """Grid search for best parameters by Sharpe ratio.

        Only considers parameter combos that produce >= MIN_TRADES_PER_WINDOW.

        Args:
            strategy_class: Strategy class to instantiate.
            data: Training data.
            param_grid: Dict of param_name -> list of values to try.
            config: Walk-forward config.

        Returns:
            Best parameter combination.
        """
        keys = list(param_grid.keys())
        values = list(param_grid.values())

        best_sharpe = -float("inf")
        best_params: Dict[str, Any] = {}

        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            metrics = self._evaluate(strategy_class, data, params, config)

            if metrics["num_trades"] < self.MIN_TRADES_PER_WINDOW:
                continue

            if metrics["sharpe"] > best_sharpe:
                best_sharpe = metrics["sharpe"]
                best_params = params

        # Fallback to default params if nothing passed min trades
        if not best_params:
            strategy = strategy_class()
            best_params = strategy.get_parameters()

        return best_params

    def _evaluate(
        self,
        strategy_class: Type[StrategyBase],
        data: Dict[str, pd.DataFrame],
        params: Dict[str, Any],
        config: WFConfig,
    ) -> Dict[str, Any]:
        """Evaluate a strategy with given params on data.

        Uses a simplified approach: iterates bars, collects signals,
        computes PnL directly without full BacktesterV2 overhead.

        Returns:
            Dict with sharpe, num_trades, total_pnl, max_dd.
        """
        strategy = strategy_class()
        strategy.set_parameters(params)

        trades: List[float] = []
        equity = config.backtest_config.initial_capital or 100_000.0
        equity_curve = [equity]
        position: Optional[Dict[str, Any]] = None
        commission_per_share = 0.005
        slippage_bps = 2.0

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
            sharpe = float(arr.mean() / arr.std() * math.sqrt(252)) if arr.std() > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown from equity curve
        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / np.where(peak > 0, peak, 1.0)
        max_dd = float(abs(dd.min())) if len(dd) > 0 else 0.0

        return {
            "sharpe": sharpe,
            "num_trades": num_trades,
            "total_pnl": total_pnl,
            "max_dd": max_dd,
        }

    def _aggregate(
        self,
        results: List[WFWindowResult],
        config: WFConfig,
    ) -> WFResult:
        """Aggregate window results and determine verdict.

        Verdict logic:
        - VALIDATED: pct_profitable >= min_profitable_pct AND oos_is_ratio >= min_ratio
        - BORDERLINE: one criterion met, or close to thresholds
        - REJECTED: neither criterion met
        """
        if not results:
            return WFResult(
                verdict="REJECTED",
                windows=results,
                avg_oos_sharpe=0.0,
                avg_is_sharpe=0.0,
                oos_is_ratio=0.0,
                pct_profitable=0.0,
                total_oos_trades=0,
            )

        avg_oos = float(np.mean([w.test_sharpe for w in results]))
        avg_is = float(np.mean([w.train_sharpe for w in results]))
        oos_is_ratio = avg_oos / avg_is if abs(avg_is) > 1e-10 else 0.0

        profitable_windows = [w for w in results if w.oos_profitable]
        pct_profitable = len(profitable_windows) / len(results)

        total_oos_trades = sum(w.test_trades for w in results)

        # Verdict
        ratio_ok = oos_is_ratio >= config.min_ratio
        profit_ok = pct_profitable >= config.min_profitable_pct

        if ratio_ok and profit_ok:
            verdict = "VALIDATED"
        elif ratio_ok or profit_ok:
            verdict = "BORDERLINE"
        else:
            verdict = "REJECTED"

        return WFResult(
            verdict=verdict,
            windows=results,
            avg_oos_sharpe=avg_oos,
            avg_is_sharpe=avg_is,
            oos_is_ratio=oos_is_ratio,
            pct_profitable=pct_profitable,
            total_oos_trades=total_oos_trades,
        )
