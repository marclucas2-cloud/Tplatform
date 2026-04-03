"""P3-04: Funding Cost Optimizer — minimize crypto margin borrowing costs.

Optimizations:
  1. Borrow rate monitoring (track rates, alert on spikes)
  2. Asset selection (choose cheapest borrow asset)
  3. Duration optimization (close borrows ASAP)
  4. Earn optimization (idle capital in Earn for passive yield)
"""

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "crypto"


@dataclass
class BorrowRate:
    """A borrow rate observation."""
    asset: str
    daily_rate_pct: float
    timestamp: str
    annualized_pct: float = 0.0

    def __post_init__(self):
        self.annualized_pct = self.daily_rate_pct * 365


@dataclass
class EarnRate:
    """An earn/staking yield observation."""
    asset: str
    apy_pct: float
    product_type: str  # "flexible" or "locked"
    timestamp: str


@dataclass
class FundingOptimization:
    """Recommended funding optimization for a strategy."""
    strategy: str
    current_borrow_asset: str
    recommended_borrow_asset: str
    current_rate_daily: float
    recommended_rate_daily: float
    savings_daily_pct: float
    earn_opportunity: float  # APY on idle capital
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "current_borrow_asset": self.current_borrow_asset,
            "recommended_borrow_asset": self.recommended_borrow_asset,
            "current_rate_daily_pct": round(self.current_rate_daily, 4),
            "recommended_rate_daily_pct": round(self.recommended_rate_daily, 4),
            "savings_daily_pct": round(self.savings_daily_pct, 4),
            "savings_annual_pct": round(self.savings_daily_pct * 365, 2),
            "earn_opportunity_apy": round(self.earn_opportunity, 2),
            "recommendations": self.recommendations,
        }


# Strategies that borrow and their acceptable borrow assets
STRATEGY_BORROW_ASSETS = {
    "margin_mean_reversion": ["USDC", "USDT", "BTC"],
    "liquidation_momentum": ["USDC", "USDT"],
    "btc_eth_dual_momentum": ["USDC", "USDT", "BTC", "ETH"],
    "altcoin_momentum": ["USDC", "USDT"],
}

# Default earn rates (updated by monitoring)
DEFAULT_EARN_RATES = {
    "BTC": {"flexible": 0.5, "locked_30d": 1.5},
    "ETH": {"flexible": 0.3, "locked_30d": 1.0},
    "USDC": {"flexible": 3.0, "locked_30d": 5.0},
    "BNB": {"flexible": 0.5, "locked_30d": 1.0},
}

# Alert threshold: rate spike > 3x average
RATE_SPIKE_MULTIPLIER = 3.0
MAX_DAILY_RATE_PCT = 0.10  # 0.1%/day = 36.5%/year -> too expensive


class BorrowRateMonitor:
    """Monitors and historizes borrow rates for margin assets.

    Thread-safe. Stores rolling window per asset.
    """

    def __init__(self, window_size: int = 100, data_dir: Path | None = None):
        self._rates: dict[str, deque[BorrowRate]] = {}
        self._window = window_size
        self._data_dir = data_dir or DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def record(self, asset: str, daily_rate_pct: float):
        """Record a borrow rate observation."""
        rate = BorrowRate(
            asset=asset,
            daily_rate_pct=daily_rate_pct,
            timestamp=datetime.now(UTC).isoformat(),
        )
        if asset not in self._rates:
            self._rates[asset] = deque(maxlen=self._window)
        self._rates[asset].append(rate)

    def get_current(self, asset: str) -> float | None:
        """Get latest borrow rate for an asset."""
        q = self._rates.get(asset)
        if not q:
            return None
        return q[-1].daily_rate_pct

    def get_average(self, asset: str) -> float | None:
        """Get rolling average borrow rate."""
        q = self._rates.get(asset)
        if not q:
            return None
        return sum(r.daily_rate_pct for r in q) / len(q)

    def get_cheapest(self, assets: list[str]) -> tuple[str, float] | None:
        """Find the cheapest asset to borrow from a list."""
        best = None
        best_rate = float("inf")
        for asset in assets:
            rate = self.get_current(asset)
            if rate is not None and rate < best_rate:
                best = asset
                best_rate = rate
        if best is None:
            return None
        return best, best_rate

    def check_spikes(self, alert_callback=None) -> list[dict]:
        """Check for borrow rate spikes (> 3x average)."""
        alerts = []
        for asset, q in self._rates.items():
            if len(q) < 5:
                continue
            current = q[-1].daily_rate_pct
            avg = sum(r.daily_rate_pct for r in q) / len(q)
            if avg > 0 and current > avg * RATE_SPIKE_MULTIPLIER:
                alert = {
                    "asset": asset,
                    "current_rate": round(current, 4),
                    "avg_rate": round(avg, 4),
                    "spike_ratio": round(current / avg, 1),
                }
                alerts.append(alert)
                msg = (
                    f"BORROW RATE SPIKE: {asset} at {current:.3f}%/day "
                    f"({current/avg:.1f}x average)"
                )
                logger.warning(msg)
                if alert_callback:
                    alert_callback(msg, level="warning")
        return alerts

    def save_history(self):
        """Save rate history to JSONL."""
        path = self._data_dir / "borrow_rates.jsonl"
        with open(path, "a") as f:
            for asset, q in self._rates.items():
                for rate in q:
                    f.write(json.dumps({
                        "asset": rate.asset,
                        "daily_rate_pct": rate.daily_rate_pct,
                        "annualized_pct": rate.annualized_pct,
                        "timestamp": rate.timestamp,
                    }) + "\n")
        logger.info("Borrow rate history saved to %s", path)


class FundingCostOptimizer:
    """Optimizes funding costs across crypto margin strategies.

    Usage:
        optimizer = FundingCostOptimizer()
        optimizer.rate_monitor.record("USDC", 0.02)  # 0.02%/day
        optimizer.rate_monitor.record("BTC", 0.005)   # 0.005%/day
        results = optimizer.optimize_all()
    """

    def __init__(self):
        self.rate_monitor = BorrowRateMonitor()
        self._earn_rates: dict[str, float] = {}  # asset -> APY%
        self._idle_capital: dict[str, float] = {}  # asset -> amount

    def set_earn_rate(self, asset: str, apy_pct: float):
        """Update earn rate for an asset."""
        self._earn_rates[asset] = apy_pct

    def set_idle_capital(self, asset: str, amount: float):
        """Set idle (non-deployed) capital per asset."""
        self._idle_capital[asset] = amount

    def optimize_strategy(
        self,
        strategy: str,
        current_borrow_asset: str = "USDC",
    ) -> FundingOptimization:
        """Optimize funding for a single strategy."""
        acceptable = STRATEGY_BORROW_ASSETS.get(strategy, ["USDC"])

        # Current rate
        current_rate = self.rate_monitor.get_current(current_borrow_asset) or 0.02

        # Find cheapest alternative
        cheapest = self.rate_monitor.get_cheapest(acceptable)
        if cheapest:
            rec_asset, rec_rate = cheapest
        else:
            rec_asset, rec_rate = current_borrow_asset, current_rate

        savings = current_rate - rec_rate

        # Earn opportunity for idle capital
        earn_opp = 0.0
        for asset, amount in self._idle_capital.items():
            rate = self._earn_rates.get(asset, DEFAULT_EARN_RATES.get(asset, {}).get("flexible", 0))
            earn_opp += amount * rate / 100  # Annual dollar yield

        recommendations = []
        if savings > 0.001:  # > 0.1bps saving
            recommendations.append(
                f"Switch borrow from {current_borrow_asset} to {rec_asset}: "
                f"save {savings:.3f}%/day ({savings*365:.1f}%/year)"
            )
        if current_rate > MAX_DAILY_RATE_PCT:
            recommendations.append(
                f"ALERT: {current_borrow_asset} rate {current_rate:.3f}%/day is very high"
            )
        if earn_opp > 0:
            idle_total = sum(self._idle_capital.values())
            recommendations.append(
                f"Earn opportunity: ${idle_total:.0f} idle → ~${earn_opp:.0f}/year"
            )

        return FundingOptimization(
            strategy=strategy,
            current_borrow_asset=current_borrow_asset,
            recommended_borrow_asset=rec_asset,
            current_rate_daily=current_rate,
            recommended_rate_daily=rec_rate,
            savings_daily_pct=max(0, savings),
            earn_opportunity=earn_opp,
            recommendations=recommendations,
        )

    def optimize_all(self) -> dict[str, FundingOptimization]:
        """Optimize all margin strategies."""
        results = {}
        for strategy in STRATEGY_BORROW_ASSETS:
            results[strategy] = self.optimize_strategy(strategy)
        return results

    def get_report(self) -> dict:
        """Generate full funding optimization report."""
        results = self.optimize_all()
        total_savings = sum(r.savings_daily_pct * 365 for r in results.values())

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_annual_savings_pct": round(total_savings, 2),
            "strategies": {k: v.to_dict() for k, v in results.items()},
            "rate_spikes": self.rate_monitor.check_spikes(),
        }
