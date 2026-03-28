"""
ROC-C03 — Borrow rate monitor for Binance France margin trading.

Continuously monitors borrow rates on open short positions and
auto-closes expensive shorts when cost exceeds thresholds.

Thresholds:
  - MAX_DAILY_RATE       = 0.001  (0.1%/day = ~36%/an)
  - MAX_MONTHLY_COST_PCT = 2.0    (2% of capital/month in borrow cost)
  - CHECK_INTERVAL       = 900s   (15 min)

Alert levels:
  - WARNING:  rate > MAX_DAILY_RATE for any asset
  - CRITICAL: rate spikes 3× within 1 hour
  - CRITICAL: projected monthly cost > MAX_MONTHLY_COST_PCT
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

MAX_DAILY_RATE = 0.001           # 0.1% per day
MAX_MONTHLY_COST_PCT = 2.0       # 2% of capital per month
CHECK_INTERVAL = 900             # 15 minutes
SPIKE_MULTIPLIER = 3.0           # 3× increase = spike
SPIKE_WINDOW_SECONDS = 3600      # 1 hour lookback for spike detection
RATE_HISTORY_MAX_ENTRIES = 500   # max entries per asset in memory


# ──────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────

@dataclass
class BorrowAlert:
    """A single borrow-rate alert."""

    level: str          # "WARNING" or "CRITICAL"
    asset: str
    message: str
    rate: float
    timestamp: str


@dataclass
class BorrowReport:
    """Per-asset borrow summary."""

    asset: str
    current_rate: float
    avg_24h: float
    max_24h: float
    annualised_pct: float
    daily_cost_usd: float
    monthly_cost_usd: float


# ──────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────

class BorrowRateMonitor:
    """Monitors borrow rates on Binance margin positions.

    Usage::

        monitor = BorrowRateMonitor(broker=binance_broker, capital=15_000)
        alerts  = monitor.check_rates(positions)
        closed  = monitor.auto_close_expensive_shorts(positions, broker)
        report  = monitor.get_report()
    """

    def __init__(
        self,
        broker: object,
        capital: float,
        max_daily_rate: float = MAX_DAILY_RATE,
        max_monthly_cost_pct: float = MAX_MONTHLY_COST_PCT,
    ):
        """Initialise the monitor.

        Args:
            broker: BinanceBroker instance (needs get_borrow_rate(asset)).
            capital: Total crypto capital in USD.
            max_daily_rate: Threshold daily rate per asset.
            max_monthly_cost_pct: Threshold monthly cost as % of capital.
        """
        self._broker = broker
        self._capital = capital
        self._max_daily_rate = max_daily_rate
        self._max_monthly_cost_pct = max_monthly_cost_pct

        # rate history: asset -> list of {rate, ts}
        self._rate_history: dict[str, list[dict]] = defaultdict(list)
        self._alerts: list[BorrowAlert] = []
        self._last_check: float = 0.0

    # ── Public API ────────────────────────────────────────────────────

    def check_rates(
        self,
        positions: list[dict],
    ) -> list[BorrowAlert]:
        """Check borrow rates for all short positions.

        Args:
            positions: List of position dicts.  Each short position should
                have at least: ``symbol``, ``side`` (SHORT), ``qty``,
                ``notional_usd``.

        Returns:
            List of BorrowAlert for any concerning rates.
        """
        now = time.time()
        alerts: list[BorrowAlert] = []

        shorts = [p for p in positions if p.get("side", "").upper() == "SHORT"]
        if not shorts:
            return alerts

        total_monthly_cost = 0.0

        for pos in shorts:
            asset = pos.get("symbol", "UNKNOWN")
            notional = pos.get("notional_usd", 0.0)

            # Fetch current rate from broker
            rate_info = self._fetch_rate(asset)
            daily_rate = rate_info.get("daily_rate", 0.0)

            # Record in history
            self._record_rate(asset, daily_rate, now)

            # Daily cost for this position
            daily_cost = notional * daily_rate
            monthly_cost = daily_cost * 30
            total_monthly_cost += monthly_cost

            # Check 1: Rate exceeds MAX_DAILY_RATE
            if daily_rate > self._max_daily_rate:
                alert = BorrowAlert(
                    level="WARNING",
                    asset=asset,
                    message=(
                        f"Borrow rate {daily_rate:.4%}/day "
                        f"({daily_rate * 365:.1%}/an) exceeds "
                        f"threshold {self._max_daily_rate:.4%}/day"
                    ),
                    rate=daily_rate,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                alerts.append(alert)
                logger.warning("BorrowMonitor: %s — %s", asset, alert.message)

            # Check 2: Rate spike (3× in 1h)
            if self._detect_spike(asset, daily_rate, now):
                alert = BorrowAlert(
                    level="CRITICAL",
                    asset=asset,
                    message=(
                        f"Borrow rate SPIKE: {daily_rate:.4%}/day — "
                        f"{SPIKE_MULTIPLIER:.0f}× increase in last hour"
                    ),
                    rate=daily_rate,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                alerts.append(alert)
                logger.critical("BorrowMonitor: %s — %s", asset, alert.message)

        # Check 3: Total monthly cost vs capital
        monthly_cost_pct = (total_monthly_cost / self._capital * 100) if self._capital > 0 else 0
        if monthly_cost_pct > self._max_monthly_cost_pct:
            alert = BorrowAlert(
                level="CRITICAL",
                asset="PORTFOLIO",
                message=(
                    f"Projected monthly borrow cost ${total_monthly_cost:.2f} "
                    f"= {monthly_cost_pct:.2f}% of capital "
                    f"(threshold: {self._max_monthly_cost_pct:.1f}%)"
                ),
                rate=monthly_cost_pct / 100,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            alerts.append(alert)
            logger.critical("BorrowMonitor: %s", alert.message)

        self._alerts.extend(alerts)
        self._last_check = now

        return alerts

    def auto_close_expensive_shorts(
        self,
        positions: list[dict],
        broker: Optional[object] = None,
    ) -> list[str]:
        """Auto-close most expensive shorts until total cost < threshold.

        Sorts shorts by daily cost (most expensive first) and closes
        them one by one until the projected monthly cost drops below
        MAX_MONTHLY_COST_PCT.

        Args:
            positions: List of position dicts with ``symbol``, ``side``,
                ``qty``, ``notional_usd``.
            broker: BinanceBroker to use for closing.  Falls back to
                self._broker if not provided.

        Returns:
            List of symbols that were closed.
        """
        broker = broker or self._broker
        shorts = [p for p in positions if p.get("side", "").upper() == "SHORT"]

        if not shorts:
            return []

        # Calculate cost per short
        costed: list[tuple[dict, float]] = []
        for pos in shorts:
            asset = pos.get("symbol", "UNKNOWN")
            notional = pos.get("notional_usd", 0.0)
            rate_info = self._fetch_rate(asset)
            daily_rate = rate_info.get("daily_rate", 0.0)
            daily_cost = notional * daily_rate
            costed.append((pos, daily_cost))

        # Sort most expensive first
        costed.sort(key=lambda x: x[1], reverse=True)

        total_monthly = sum(c * 30 for _, c in costed)
        threshold = self._capital * self._max_monthly_cost_pct / 100

        if total_monthly <= threshold:
            return []

        closed: list[str] = []

        for pos, daily_cost in costed:
            if total_monthly <= threshold:
                break

            symbol = pos.get("symbol", "UNKNOWN")
            qty = pos.get("qty", 0)

            try:
                logger.warning(
                    "BorrowMonitor: auto-closing short %s (daily cost $%.2f)",
                    symbol,
                    daily_cost,
                )
                # Close by buying back the borrowed amount
                if hasattr(broker, "close_position"):
                    broker.close_position(symbol)
                else:
                    logger.error(
                        "BorrowMonitor: broker has no close_position method"
                    )
                    continue

                total_monthly -= daily_cost * 30
                closed.append(symbol)

            except Exception as e:
                logger.error(
                    "BorrowMonitor: failed to close short %s: %s", symbol, e
                )

        if closed:
            logger.info(
                "BorrowMonitor: auto-closed %d shorts: %s — "
                "projected monthly cost now $%.2f",
                len(closed),
                closed,
                total_monthly,
            )

        return closed

    def get_report(self) -> dict:
        """Generate a summary report of borrow rates.

        Returns:
            Dict with ``assets`` (list of per-asset reports), ``total_daily_cost``,
            ``total_monthly_cost``, ``capital``, ``cost_pct``, ``timestamp``.
        """
        now = time.time()
        asset_reports: list[dict] = []
        total_daily = 0.0

        for asset, history in self._rate_history.items():
            if not history:
                continue

            current_rate = history[-1]["rate"]

            # 24h stats
            cutoff_24h = now - 86400
            recent = [h for h in history if h["ts"] >= cutoff_24h]

            if recent:
                rates = [h["rate"] for h in recent]
                avg_24h = sum(rates) / len(rates)
                max_24h = max(rates)
            else:
                avg_24h = current_rate
                max_24h = current_rate

            annualised = current_rate * 365 * 100  # in percent
            daily_cost = 0.0  # Would need notional to compute; report rate only

            report = BorrowReport(
                asset=asset,
                current_rate=current_rate,
                avg_24h=avg_24h,
                max_24h=max_24h,
                annualised_pct=annualised,
                daily_cost_usd=daily_cost,
                monthly_cost_usd=daily_cost * 30,
            )
            asset_reports.append({
                "asset": report.asset,
                "current_rate": round(report.current_rate, 6),
                "avg_24h": round(report.avg_24h, 6),
                "max_24h": round(report.max_24h, 6),
                "annualised_pct": round(report.annualised_pct, 2),
            })

        return {
            "assets": asset_reports,
            "total_assets_monitored": len(asset_reports),
            "capital": self._capital,
            "last_check": datetime.fromtimestamp(
                self._last_check, tz=timezone.utc
            ).isoformat() if self._last_check > 0 else None,
            "recent_alerts": len(self._alerts),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_recent_alerts(self, limit: int = 50) -> list[dict]:
        """Return recent alerts as dicts.

        Args:
            limit: Max number of alerts to return.

        Returns:
            List of alert dicts (most recent last).
        """
        return [
            {
                "level": a.level,
                "asset": a.asset,
                "message": a.message,
                "rate": a.rate,
                "timestamp": a.timestamp,
            }
            for a in self._alerts[-limit:]
        ]

    # ── Private helpers ───────────────────────────────────────────────

    def _fetch_rate(self, asset: str) -> dict:
        """Fetch borrow rate from broker, with error handling.

        Args:
            asset: Asset symbol (e.g. "BTC", "ETH").

        Returns:
            Dict with at least ``daily_rate`` key.
        """
        try:
            if hasattr(self._broker, "get_borrow_rate"):
                return self._broker.get_borrow_rate(asset)
        except Exception as e:
            logger.error("BorrowMonitor: failed to fetch rate for %s: %s", asset, e)
        return {"daily_rate": 0.0}

    def _record_rate(self, asset: str, rate: float, ts: float) -> None:
        """Record a rate observation in history.

        Args:
            asset: Asset symbol.
            rate: Daily rate as decimal.
            ts: Unix timestamp.
        """
        history = self._rate_history[asset]
        history.append({"rate": rate, "ts": ts})

        # Prune old entries
        if len(history) > RATE_HISTORY_MAX_ENTRIES:
            self._rate_history[asset] = history[-RATE_HISTORY_MAX_ENTRIES:]

    def _detect_spike(self, asset: str, current_rate: float, now: float) -> bool:
        """Detect if current rate is a 3× spike vs 1h-ago baseline.

        Args:
            asset: Asset symbol.
            current_rate: Current daily rate.
            now: Current unix timestamp.

        Returns:
            True if spike detected.
        """
        history = self._rate_history.get(asset, [])
        if len(history) < 2:
            return False

        cutoff = now - SPIKE_WINDOW_SECONDS
        old_rates = [h["rate"] for h in history if h["ts"] <= cutoff]

        if not old_rates:
            return False

        baseline = sum(old_rates) / len(old_rates)

        if baseline <= 0:
            return False

        return current_rate >= baseline * SPIKE_MULTIPLIER
