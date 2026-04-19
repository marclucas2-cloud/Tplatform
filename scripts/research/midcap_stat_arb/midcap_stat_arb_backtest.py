"""
MidCap Statistical Arbitrage — Backtester

Walk-forward compatible backtester for the stat arb strategy.
Tests pair formation → signal generation → execution on historical data.

Usage:
    results = backtest_stat_arb(
        prices=prices_dict,
        volumes=volumes_dict,
        start_date="2022-01-01",
        end_date="2025-12-31",
    )

Compatible with BacktesterV2 walk-forward framework.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import logging

from strategies_v2.us.midcap_stat_arb_scanner import PairScanner, PairCandidate
from strategies_v2.us.midcap_stat_arb_strategy import (
    MidCapStatArbStrategy, StatArbConfig, PairPosition,
)

logger = logging.getLogger("stat_arb.backtest")


# ============================================================
# Cost Model
# ============================================================

@dataclass
class AlpacaCostModel:
    """Cost model for Alpaca broker."""
    commission_per_trade: float = 0.0     # $0 commission
    spread_bps: float = 1.0              # ~1 bps spread (PFOF)
    slippage_bps: float = 2.0            # ~2 bps slippage (mid-cap)
    short_borrow_annual_pct: float = 1.5  # ~1.5% annual short borrow cost
    min_order_usd: float = 1.0           # Fractional shares

    def calculate_cost(
        self,
        notional: float,
        is_short: bool = False,
        holding_days: int = 1,
    ) -> float:
        """Calculate total cost for a trade (one-way)."""
        spread_cost = notional * self.spread_bps / 10_000
        slippage_cost = notional * self.slippage_bps / 10_000
        borrow_cost = 0.0
        if is_short:
            borrow_cost = notional * self.short_borrow_annual_pct / 100 * holding_days / 365

        return spread_cost + slippage_cost + borrow_cost


# ============================================================
# Backtest Result
# ============================================================

@dataclass
class BacktestResult:
    """Complete backtest results."""
    # Summary
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    total_trades: int = 0
    avg_holding_days: float = 0.0
    profit_factor: float = 0.0

    # Time series
    equity_curve: pd.Series = None
    daily_returns: pd.Series = None

    # Trades
    trades: List[Dict] = field(default_factory=list)

    # Costs
    total_costs: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    commission_burn_pct: float = 0.0

    # Pair stats
    pairs_tested: int = 0
    pairs_traded: int = 0
    avg_pairs_per_period: float = 0.0

    # Exit reasons
    exit_reasons: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """Pretty-print summary."""
        return (
            f"\n{'='*60}\n"
            f"STAT ARB BACKTEST RESULTS\n"
            f"{'='*60}\n"
            f"Total Return:     {self.total_return_pct:>8.2f}%\n"
            f"Annualized:       {self.annualized_return_pct:>8.2f}%\n"
            f"Sharpe Ratio:     {self.sharpe_ratio:>8.2f}\n"
            f"Max Drawdown:     {self.max_drawdown_pct:>8.2f}%\n"
            f"Calmar Ratio:     {self.calmar_ratio:>8.2f}\n"
            f"Win Rate:         {self.win_rate:>8.2f}%\n"
            f"Profit Factor:    {self.profit_factor:>8.2f}\n"
            f"Avg Trade PnL:    ${self.avg_trade_pnl:>7.2f}\n"
            f"Total Trades:     {self.total_trades:>8d}\n"
            f"Avg Holding:      {self.avg_holding_days:>8.1f} days\n"
            f"{'─'*60}\n"
            f"Gross PnL:        ${self.gross_pnl:>8.2f}\n"
            f"Total Costs:      ${self.total_costs:>8.2f}\n"
            f"Net PnL:          ${self.net_pnl:>8.2f}\n"
            f"Commission Burn:  {self.commission_burn_pct:>8.2f}%\n"
            f"{'─'*60}\n"
            f"Pairs Tested:     {self.pairs_tested:>8d}\n"
            f"Pairs Traded:     {self.pairs_traded:>8d}\n"
            f"Avg Active Pairs: {self.avg_pairs_per_period:>8.1f}\n"
            f"{'─'*60}\n"
            f"Exit Reasons:\n"
        ) + "\n".join(
            f"  {reason:<20} {count:>5}"
            for reason, count in sorted(self.exit_reasons.items())
        ) + f"\n{'='*60}\n"


# ============================================================
# Backtester
# ============================================================

def backtest_stat_arb(
    prices: Dict[str, pd.DataFrame],
    start_date: str = "2022-01-01",
    end_date: str = "2025-12-31",
    initial_capital: float = 30_000.0,
    config: StatArbConfig = None,
    cost_model: AlpacaCostModel = None,
    volumes: Optional[Dict[str, pd.Series]] = None,
    rebalance_every_n_days: int = 5,  # Weekly pair reformation
) -> BacktestResult:
    """
    Run a full backtest of the stat arb strategy.

    Args:
        prices: Dict of ticker -> DataFrame with 'close' column and DatetimeIndex
        start_date: Backtest start (needs formation_period before this)
        end_date: Backtest end
        initial_capital: Starting capital
        config: Strategy config
        cost_model: Cost assumptions
        volumes: Optional daily dollar volumes
        rebalance_every_n_days: How often to reform pairs

    Returns:
        BacktestResult with full metrics
    """
    config = config or StatArbConfig()
    cost_model = cost_model or AlpacaCostModel()

    strategy = MidCapStatArbStrategy(config)

    # Get all trading dates
    sample_ticker = list(prices.keys())[0]
    all_dates = prices[sample_ticker].index
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)

    # Filter to backtest period
    bt_dates = all_dates[(all_dates >= start_dt) & (all_dates <= end_dt)]

    if len(bt_dates) == 0:
        logger.error("No trading dates in the specified range")
        return BacktestResult()

    # Track equity
    equity = initial_capital
    equity_series = {}
    daily_pnl_list = []
    all_trades = []
    total_costs = 0.0
    gross_pnl = 0.0
    pairs_traded_set = set()
    active_pairs_count = []
    exit_reasons = {}

    last_rebalance_idx = -rebalance_every_n_days  # Force rebalance on first day

    logger.info(f"Starting backtest: {start_date} to {end_date}, "
                 f"capital=${initial_capital:,.0f}, {len(bt_dates)} trading days")

    for i, date in enumerate(bt_dates):
        # ---- Weekly pair reformation ----
        if i - last_rebalance_idx >= rebalance_every_n_days:
            # Slice prices up to current date for formation
            prices_slice = {}
            for ticker, df in prices.items():
                mask = df.index <= date
                if mask.sum() >= config.formation_period_days:
                    prices_slice[ticker] = df[mask]

            if len(prices_slice) > 20:
                strategy.update_pairs(prices_slice, volumes)
                last_rebalance_idx = i
                active_pairs_count.append(len(strategy.active_pairs))

        # ---- Daily signal generation ----
        prices_current = {}
        for ticker, df in prices.items():
            mask = df.index <= date
            if mask.sum() > 0:
                prices_current[ticker] = df[mask]

        signals = strategy.generate_signals(prices_current, current_regime="MEAN_REVERT")

        # ---- Process signals ----
        day_pnl = 0.0

        for signal in signals:
            if signal["type"] == "ENTRY":
                # Check capital
                leg_size = signal.get("leg_size_usd", config.position_per_leg_usd)
                total_needed = leg_size * 2
                if total_needed > equity * config.max_portfolio_pct:
                    continue

                # Execute entry (both legs)
                price_a = signal["price_a"]
                price_b = signal["price_b"]

                # Cost for entry (both legs)
                cost_a = cost_model.calculate_cost(abs(signal["quantity_a"] * price_a))
                cost_b = cost_model.calculate_cost(
                    abs(signal["quantity_b"] * price_b),
                    is_short=(signal["quantity_b"] < 0),
                )
                entry_cost = cost_a + cost_b
                total_costs += entry_cost

                # Record position
                strategy.on_entry_filled(signal, price_a, price_b)
                pairs_traded_set.add(signal["pair_id"])

            elif signal["type"] == "EXIT":
                pair_id = signal["pair_id"]
                price_a = signal["price_a"]
                price_b = signal["price_b"]

                pos = strategy.open_positions.get(pair_id)
                if not pos:
                    continue

                # Cost for exit (both legs)
                cost_a = cost_model.calculate_cost(abs(pos.quantity_a * price_a))
                cost_b = cost_model.calculate_cost(
                    abs(pos.quantity_b * price_b),
                    is_short=(pos.quantity_b < 0),
                    holding_days=pos.holding_days,
                )
                exit_cost = cost_a + cost_b
                total_costs += exit_cost

                # Close position
                closed_pos = strategy.on_exit_filled(
                    pair_id, price_a, price_b, signal["reason"]
                )

                if closed_pos:
                    trade_gross = closed_pos.pnl
                    trade_net = trade_gross - entry_cost - exit_cost
                    gross_pnl += trade_gross
                    day_pnl += trade_net
                    equity += trade_net

                    # Track exit reason
                    reason = signal["reason"]
                    exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

                    all_trades.append({
                        "pair_id": pair_id,
                        "direction": closed_pos.direction,
                        "entry_date": closed_pos.entry_time,
                        "exit_date": date,
                        "holding_days": closed_pos.holding_days,
                        "gross_pnl": round(trade_gross, 2),
                        "costs": round(entry_cost + exit_cost, 2),
                        "net_pnl": round(trade_net, 2),
                        "pnl_pct": round(closed_pos.pnl_pct, 4),
                        "entry_z": closed_pos.entry_z,
                        "exit_reason": reason,
                    })

        # Update unrealized PnL for open positions
        for pair_id, pos in strategy.open_positions.items():
            if pos.ticker_a in prices_current and pos.ticker_b in prices_current:
                pos.update_pnl(
                    prices_current[pos.ticker_a]["close"].iloc[-1],
                    prices_current[pos.ticker_b]["close"].iloc[-1],
                )

        equity_series[date] = equity
        daily_pnl_list.append(day_pnl)

        # Reset daily PnL in strategy
        strategy.reset_daily_pnl()

    # ---- Calculate metrics ----
    equity_curve = pd.Series(equity_series)
    daily_returns = equity_curve.pct_change().dropna()

    total_return = (equity - initial_capital) / initial_capital * 100
    n_years = len(bt_dates) / 252
    ann_return = ((equity / initial_capital) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

    sharpe = 0.0
    if len(daily_returns) > 0 and daily_returns.std() > 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)

    # Max drawdown
    peak = equity_curve.expanding().max()
    dd = (equity_curve - peak) / peak
    max_dd = dd.min() * 100

    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    # Trade stats
    wins = [t for t in all_trades if t["net_pnl"] > 0]
    losses = [t for t in all_trades if t["net_pnl"] <= 0]
    win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0

    gross_wins = sum(t["net_pnl"] for t in wins)
    gross_losses = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    avg_pnl = np.mean([t["net_pnl"] for t in all_trades]) if all_trades else 0
    avg_holding = np.mean([t["holding_days"] for t in all_trades]) if all_trades else 0

    net_pnl = equity - initial_capital
    comm_burn = total_costs / gross_pnl * 100 if gross_pnl > 0 else 0

    result = BacktestResult(
        total_return_pct=round(total_return, 2),
        annualized_return_pct=round(ann_return, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
        calmar_ratio=round(calmar, 2),
        win_rate=round(win_rate, 2),
        avg_trade_pnl=round(avg_pnl, 2),
        total_trades=len(all_trades),
        avg_holding_days=round(avg_holding, 1),
        profit_factor=round(profit_factor, 2),
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        trades=all_trades,
        total_costs=round(total_costs, 2),
        gross_pnl=round(gross_pnl, 2),
        net_pnl=round(net_pnl, 2),
        commission_burn_pct=round(comm_burn, 2),
        pairs_tested=len(prices),
        pairs_traded=len(pairs_traded_set),
        avg_pairs_per_period=np.mean(active_pairs_count) if active_pairs_count else 0,
        exit_reasons=exit_reasons,
    )

    logger.info(f"Backtest complete: {result.total_trades} trades, "
                 f"Sharpe {result.sharpe_ratio}, "
                 f"Return {result.total_return_pct}%")

    return result


# ============================================================
# Walk-Forward wrapper
# ============================================================

def walk_forward_stat_arb(
    prices: Dict[str, pd.DataFrame],
    n_windows: int = 5,
    train_pct: float = 0.7,
    initial_capital: float = 30_000.0,
    volumes: Optional[Dict[str, pd.Series]] = None,
) -> Dict:
    """
    Walk-forward analysis for the stat arb strategy.

    Splits data into n_windows of train/test.
    Trains pair selection on train, tests on test.

    Returns dict with per-window and aggregate results.
    """
    # Get date range
    sample_ticker = list(prices.keys())[0]
    all_dates = prices[sample_ticker].index
    total_days = len(all_dates)
    window_size = total_days // n_windows

    results = []

    for w in range(n_windows):
        start_idx = w * window_size
        end_idx = min((w + 1) * window_size, total_days)

        window_dates = all_dates[start_idx:end_idx]
        train_end_idx = int(len(window_dates) * train_pct)

        train_end = window_dates[train_end_idx]
        test_start = window_dates[train_end_idx + 1] if train_end_idx + 1 < len(window_dates) else window_dates[-1]
        test_end = window_dates[-1]

        logger.info(f"Window {w+1}/{n_windows}: "
                     f"train ends {train_end.date()}, "
                     f"test {test_start.date()} to {test_end.date()}")

        # Backtest on test period (formation uses train period data)
        result = backtest_stat_arb(
            prices=prices,
            start_date=str(test_start.date()),
            end_date=str(test_end.date()),
            initial_capital=initial_capital,
            volumes=volumes,
        )

        results.append({
            "window": w + 1,
            "train_end": str(train_end.date()),
            "test_start": str(test_start.date()),
            "test_end": str(test_end.date()),
            "sharpe": result.sharpe_ratio,
            "return_pct": result.total_return_pct,
            "max_dd_pct": result.max_drawdown_pct,
            "trades": result.total_trades,
            "win_rate": result.win_rate,
        })

    # Aggregate
    sharpes = [r["sharpe"] for r in results]
    profitable_windows = sum(1 for r in results if r["return_pct"] > 0)

    summary = {
        "windows": results,
        "n_windows": n_windows,
        "avg_sharpe_oos": round(np.mean(sharpes), 2),
        "median_sharpe_oos": round(np.median(sharpes), 2),
        "min_sharpe_oos": round(min(sharpes), 2),
        "max_sharpe_oos": round(max(sharpes), 2),
        "profitable_windows_pct": round(profitable_windows / n_windows * 100, 1),
        "total_trades": sum(r["trades"] for r in results),
    }

    # Verdict
    if (summary["avg_sharpe_oos"] >= 0.5
        and summary["profitable_windows_pct"] >= 60
        and summary["total_trades"] >= 30):
        summary["verdict"] = "VALIDATED"
    elif (summary["avg_sharpe_oos"] >= 0.3
          and summary["profitable_windows_pct"] >= 40):
        summary["verdict"] = "BORDERLINE"
    else:
        summary["verdict"] = "REJECTED"

    logger.info(f"Walk-forward complete: {summary['verdict']} "
                 f"(avg Sharpe OOS = {summary['avg_sharpe_oos']}, "
                 f"{summary['profitable_windows_pct']}% profitable windows)")

    return summary
