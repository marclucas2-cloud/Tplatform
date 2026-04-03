"""P3-03: Commission Burn Analysis per strategy.

Calculates commission_burn = total_commissions / gross_profit for each strategy.
Classifies strategies: SAFE (<15%), WATCH (15-25%), FRAGILE (25-40%), KILL (>40%).

Uses BacktestResults to compute burn from WF OOS data when live data insufficient.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


class BurnLevel(str, Enum):
    SAFE = "SAFE"          # < 15%
    WATCH = "WATCH"        # 15-25%
    FRAGILE = "FRAGILE"    # 25-40%
    KILL = "KILL"          # > 40%


BURN_THRESHOLDS = {
    BurnLevel.SAFE: 0.15,
    BurnLevel.WATCH: 0.25,
    BurnLevel.FRAGILE: 0.40,
}

# Cost models per broker (commission + slippage in bps of notional)
BROKER_COST_BPS = {
    "alpaca": {"commission_bps": 0.0, "slippage_bps": 2.0},       # $0 + 0.02%
    "ibkr_fx": {"commission_bps": 0.5, "slippage_bps": 1.0},      # ~$2/25K + 1bps
    "ibkr_eu": {"commission_bps": 5.0, "slippage_bps": 3.0},      # 0.05% + 3bps
    "binance": {"commission_bps": 7.5, "slippage_bps": 2.0},      # 0.075% maker + 2bps
    "ibkr_futures": {"commission_bps": 1.0, "slippage_bps": 2.0},  # $0.62/contract ~1bps
}


@dataclass
class StrategyBurn:
    """Commission burn analysis for a single strategy."""
    strategy: str
    broker: str
    n_trades: int
    total_gross_profit: float
    total_commission: float
    total_slippage_cost: float
    total_cost: float
    commission_burn: float  # total_cost / gross_profit
    burn_level: BurnLevel
    avg_trade_pnl: float
    avg_trade_cost: float
    avg_notional: float
    break_even_slippage_bps: float  # max slippage before strategy dies
    trades_per_6m: int  # annualized trade frequency
    viable: bool

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "broker": self.broker,
            "n_trades": self.n_trades,
            "total_gross_profit": round(self.total_gross_profit, 2),
            "total_commission": round(self.total_commission, 2),
            "total_slippage_cost": round(self.total_slippage_cost, 2),
            "total_cost": round(self.total_cost, 2),
            "commission_burn": round(self.commission_burn, 4),
            "burn_level": self.burn_level.value,
            "avg_trade_pnl": round(self.avg_trade_pnl, 2),
            "avg_trade_cost": round(self.avg_trade_cost, 4),
            "avg_notional": round(self.avg_notional, 2),
            "break_even_slippage_bps": round(self.break_even_slippage_bps, 1),
            "trades_per_6m": self.trades_per_6m,
            "viable": self.viable,
        }


@dataclass
class CommissionBurnReport:
    """Full report for all strategies."""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    strategies: dict[str, StrategyBurn] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def add(self, burn: StrategyBurn):
        self.strategies[burn.strategy] = burn

    def compute_summary(self):
        by_level = {level: [] for level in BurnLevel}
        for burn in self.strategies.values():
            by_level[burn.burn_level].append(burn.strategy)

        total_cost = sum(b.total_cost for b in self.strategies.values())
        total_profit = sum(b.total_gross_profit for b in self.strategies.values())

        self.summary = {
            "total_strategies": len(self.strategies),
            "portfolio_burn": round(total_cost / total_profit, 4) if total_profit > 0 else 0,
            "total_cost": round(total_cost, 2),
            "total_gross_profit": round(total_profit, 2),
            "by_level": {level.value: strats for level, strats in by_level.items()},
            "kill_candidates": by_level[BurnLevel.KILL],
            "fragile": by_level[BurnLevel.FRAGILE],
        }

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "summary": self.summary,
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
        }

    def save(self, path: Path | None = None):
        path = path or (REPORTS_DIR / "commission_burn.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Commission burn report saved to %s", path)


class CommissionBurnAnalyzer:
    """Analyzes commission burn per strategy from backtest trades.

    Usage:
        analyzer = CommissionBurnAnalyzer()
        analyzer.add_trades("fx_carry_vs", "ibkr_fx", trades_list)
        report = analyzer.analyze()
        report.save()
    """

    def __init__(self, period_months: int = 6):
        self._trades: dict[str, dict] = {}  # strategy -> {broker, trades}
        self._period_months = period_months

    def add_trades(
        self,
        strategy: str,
        broker: str,
        trades: list[dict],
    ):
        """Add trades for a strategy.

        Each trade dict should have: notional, pnl_gross, commission (optional).
        """
        self._trades[strategy] = {"broker": broker, "trades": trades}

    def analyze(self) -> CommissionBurnReport:
        """Run commission burn analysis on all registered strategies."""
        report = CommissionBurnReport()

        for strategy, data in self._trades.items():
            broker = data["broker"]
            trades = data["trades"]

            if not trades:
                continue

            burn = self._analyze_strategy(strategy, broker, trades)
            report.add(burn)

        report.compute_summary()
        return report

    def _analyze_strategy(
        self,
        strategy: str,
        broker: str,
        trades: list[dict],
    ) -> StrategyBurn:
        """Analyze a single strategy's commission burn."""
        cost_model = BROKER_COST_BPS.get(broker, BROKER_COST_BPS["alpaca"])
        comm_bps = cost_model["commission_bps"]
        slip_bps = cost_model["slippage_bps"]

        n_trades = len(trades)
        total_gross = 0.0
        total_comm = 0.0
        total_slip = 0.0
        total_notional = 0.0

        for trade in trades:
            notional = trade.get("notional", 0)
            pnl = trade.get("pnl_gross", 0)
            # Use explicit commission if provided, else model
            comm = trade.get("commission", notional * comm_bps / 10_000)
            slip_cost = notional * slip_bps / 10_000

            total_gross += pnl
            total_comm += comm
            total_slip += slip_cost
            total_notional += notional

        total_cost = total_comm + total_slip

        if total_gross > 0:
            burn_ratio = total_cost / total_gross
        else:
            burn_ratio = float("inf") if total_cost > 0 else 0.0

        # Classify
        burn_level = BurnLevel.KILL
        for level, threshold in sorted(BURN_THRESHOLDS.items(), key=lambda x: x[1]):
            if burn_ratio < threshold:
                burn_level = level
                break
        else:
            if burn_ratio < 0.40:
                burn_level = BurnLevel.FRAGILE

        # Break-even slippage: what slippage would eat all the profit?
        if n_trades > 0 and total_notional > 0:
            net_after_comm = total_gross - total_comm
            break_even_slip_bps = (net_after_comm / total_notional) * 10_000
        else:
            break_even_slip_bps = 0.0

        # Frequency extrapolation
        trades_per_6m = n_trades  # assume trades cover ~6 months

        # Viability rules from V12.5:
        # "> 200 trades/6m + position < $5K = mort"
        avg_notional = total_notional / n_trades if n_trades > 0 else 0
        high_freq_small_pos = trades_per_6m > 200 and avg_notional < 5000
        viable = burn_ratio < 0.40 and not high_freq_small_pos

        return StrategyBurn(
            strategy=strategy,
            broker=broker,
            n_trades=n_trades,
            total_gross_profit=total_gross,
            total_commission=total_comm,
            total_slippage_cost=total_slip,
            total_cost=total_cost,
            commission_burn=min(burn_ratio, 9.99),  # cap for display
            burn_level=burn_level,
            avg_trade_pnl=total_gross / n_trades if n_trades else 0,
            avg_trade_cost=total_cost / n_trades if n_trades else 0,
            avg_notional=avg_notional,
            break_even_slippage_bps=max(break_even_slip_bps, 0),
            trades_per_6m=trades_per_6m,
            viable=viable,
        )

    def analyze_from_backtest_results(
        self,
        results: dict[str, Any],
        broker_map: dict[str, str] | None = None,
    ) -> CommissionBurnReport:
        """Analyze from BacktestResults dict (trades list with pnl/notional).

        broker_map: {strategy_name: broker_key}
        """
        default_broker_map = {
            "fx_": "ibkr_fx",
            "eu_": "ibkr_eu",
            "btc_": "binance",
            "eth_": "binance",
            "crypto_": "binance",
            "futures_": "ibkr_futures",
        }
        broker_map = broker_map or {}

        for strat_name, strat_data in results.items():
            trades = strat_data.get("trades", [])
            if not trades:
                continue

            # Determine broker
            broker = broker_map.get(strat_name)
            if not broker:
                for prefix, b in default_broker_map.items():
                    if strat_name.startswith(prefix):
                        broker = b
                        break
                else:
                    broker = "alpaca"

            self.add_trades(strat_name, broker, trades)

        return self.analyze()
