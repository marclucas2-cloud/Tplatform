"""Portfolio State Engine — unified cross-broker portfolio view.

Aggregates IBKR + Binance into a single real-time state:
  - Total capital, invested, at risk (ERE)
  - Leverage, exposure (long/short/net/gross)
  - Cluster risk, drawdown, slippage vs backtest

Usage:
    state_engine = PortfolioStateEngine(smart_router, ere_calc, corr_engine)
    state = state_engine.get_state()
    print(state.total_capital)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class BrokerState:
    broker: str
    equity: float
    cash: float
    positions: List[Dict[str, Any]]
    n_positions: int
    exposure_long: float
    exposure_short: float
    exposure_net: float
    exposure_gross: float
    unrealized_pnl: float
    paper: bool


@dataclass
class PortfolioState:
    timestamp: datetime

    # Capital
    total_capital: float
    total_cash: float
    total_invested: float
    capital_at_risk: float  # ERE
    capital_at_risk_pct: float

    # Leverage
    leverage_real: float
    leverage_target: float

    # Exposure
    exposure_long: float
    exposure_short: float
    exposure_net: float
    exposure_gross: float
    exposure_net_pct: float
    exposure_gross_pct: float

    # PnL
    unrealized_pnl: float
    daily_pnl: float
    daily_pnl_pct: float
    drawdown_pct: float

    # Risk metrics
    correlation_score: float
    n_clusters: int
    n_strategies_active: int
    n_positions: int

    # Brokers
    brokers: List[BrokerState]

    # Alerts
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_capital": round(self.total_capital, 2),
            "total_cash": round(self.total_cash, 2),
            "total_invested": round(self.total_invested, 2),
            "capital_at_risk": round(self.capital_at_risk, 2),
            "capital_at_risk_pct": round(self.capital_at_risk_pct, 4),
            "leverage_real": round(self.leverage_real, 3),
            "leverage_target": round(self.leverage_target, 3),
            "exposure_long": round(self.exposure_long, 2),
            "exposure_short": round(self.exposure_short, 2),
            "exposure_net": round(self.exposure_net, 2),
            "exposure_gross": round(self.exposure_gross, 2),
            "exposure_net_pct": round(self.exposure_net_pct, 4),
            "exposure_gross_pct": round(self.exposure_gross_pct, 4),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 4),
            "drawdown_pct": round(self.drawdown_pct, 4),
            "correlation_score": round(self.correlation_score, 3),
            "n_clusters": self.n_clusters,
            "n_strategies_active": self.n_strategies_active,
            "n_positions": self.n_positions,
            "brokers": [
                {
                    "broker": b.broker,
                    "equity": round(b.equity, 2),
                    "n_positions": b.n_positions,
                    "exposure_net": round(b.exposure_net, 2),
                    "unrealized_pnl": round(b.unrealized_pnl, 2),
                    "paper": b.paper,
                }
                for b in self.brokers
            ],
            "alerts": self.alerts,
        }


class PortfolioStateEngine:
    """Unified portfolio state across all brokers."""

    def __init__(
        self,
        smart_router=None,
        ere_calculator=None,
        correlation_engine=None,
        data_dir: str = "data",
    ):
        self.smart_router = smart_router
        self.ere_calculator = ere_calculator
        self.correlation_engine = correlation_engine
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._peak_equity: float = 0.0
        self._daily_start_equity: float = 0.0
        self._last_reset_date: str | None = None

    def get_state(
        self,
        leverage_target: float = 1.0,
        active_strategies: List[str] | None = None,
    ) -> PortfolioState:
        """Compute unified portfolio state from all brokers.

        Args:
            leverage_target: Target leverage from LeverageManager.
            active_strategies: List of currently active strategy names.
        """
        brokers_state = self._collect_broker_states()

        # Aggregates — LIVE only (exclude paper brokers from risk metrics)
        live_brokers = [b for b in brokers_state if not getattr(b, "paper", False)]
        if not live_brokers:
            live_brokers = brokers_state  # Fallback if all paper (dev mode)
        total_equity = sum(b.equity for b in live_brokers)
        total_cash = sum(b.cash for b in live_brokers)
        total_long = sum(b.exposure_long for b in live_brokers)
        total_short = sum(b.exposure_short for b in live_brokers)
        total_net = total_long - total_short
        total_gross = total_long + total_short
        total_unrealized = sum(b.unrealized_pnl for b in live_brokers)
        total_positions = sum(b.n_positions for b in live_brokers)

        # Invested = equity - cash
        total_invested = max(0, total_equity - total_cash)

        # Leverage = gross exposure / equity
        leverage_real = total_gross / total_equity if total_equity > 0 else 0.0

        # Drawdown tracking
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._daily_start_equity = total_equity
            self._last_reset_date = today

        if total_equity > self._peak_equity:
            self._peak_equity = total_equity

        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - total_equity) / self._peak_equity

        daily_pnl = total_equity - self._daily_start_equity
        daily_pnl_pct = daily_pnl / self._daily_start_equity if self._daily_start_equity > 0 else 0.0

        # ERE — live brokers only (same filter as aggregates above)
        capital_at_risk = 0.0
        capital_at_risk_pct = 0.0
        if self.ere_calculator is not None:
            try:
                all_positions = []
                for b in live_brokers:
                    all_positions.extend(b.positions)
                ere_result = self.ere_calculator.calculate(all_positions, total_equity)
                capital_at_risk = ere_result.ere_absolute
                capital_at_risk_pct = ere_result.ere_pct
            except Exception as e:
                logger.warning(f"ERE calculation failed: {e}")

        # Correlation
        corr_score = 0.0
        n_clusters = 0
        if self.correlation_engine is not None:
            try:
                corr_score = self.correlation_engine.get_global_score()
                clusters = self.correlation_engine.detect_clusters()
                n_clusters = len(clusters)
            except Exception as e:
                logger.warning(f"Correlation check failed: {e}")

        # Alerts
        alerts = []
        if drawdown_pct >= 0.05:
            alerts.append(f"CRITICAL: DD={drawdown_pct:.1%}")
        elif drawdown_pct >= 0.03:
            alerts.append(f"WARNING: DD={drawdown_pct:.1%}")

        if capital_at_risk_pct >= 0.35:
            alerts.append(f"CRITICAL: ERE={capital_at_risk_pct:.1%}")
        elif capital_at_risk_pct >= 0.25:
            alerts.append(f"WARNING: ERE={capital_at_risk_pct:.1%}")

        if corr_score >= 0.85:
            alerts.append(f"CRITICAL: corr={corr_score:.2f}")
        elif corr_score >= 0.70:
            alerts.append(f"WARNING: corr={corr_score:.2f}")

        if leverage_real > leverage_target * 1.2:
            alerts.append(
                f"WARNING: leverage {leverage_real:.2f}x > target {leverage_target:.2f}x"
            )

        n_strats = len(active_strategies) if active_strategies else 0

        state = PortfolioState(
            timestamp=datetime.utcnow(),
            total_capital=total_equity,
            total_cash=total_cash,
            total_invested=total_invested,
            capital_at_risk=capital_at_risk,
            capital_at_risk_pct=capital_at_risk_pct,
            leverage_real=leverage_real,
            leverage_target=leverage_target,
            exposure_long=total_long,
            exposure_short=total_short,
            exposure_net=total_net,
            exposure_gross=total_gross,
            exposure_net_pct=total_net / total_equity if total_equity > 0 else 0.0,
            exposure_gross_pct=total_gross / total_equity if total_equity > 0 else 0.0,
            unrealized_pnl=total_unrealized,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            drawdown_pct=drawdown_pct,
            correlation_score=corr_score,
            n_clusters=n_clusters,
            n_strategies_active=n_strats,
            n_positions=total_positions,
            brokers=brokers_state,
            alerts=alerts,
        )

        return state

    def record_snapshot(self, state: PortfolioState) -> None:
        """Append portfolio snapshot to JSONL log."""
        path = self._data_dir / "live_portfolio_snapshots.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(state.to_dict()) + "\n")
        except Exception as e:
            logger.warning(f"Failed to record snapshot: {e}")

    # ─── Internal ────────────────────────────────────────────────────────

    def _collect_broker_states(self) -> List[BrokerState]:
        """Collect state from all available brokers."""
        states = []

        if self.smart_router is None:
            return states

        try:
            brokers = self.smart_router.get_all_brokers()
        except Exception as e:
            logger.warning(f"Failed to get brokers: {e}")
            return states

        for name, broker in brokers.items():
            try:
                state = self._get_broker_state(name, broker)
                states.append(state)
            except Exception as e:
                logger.warning(f"Failed to get {name} state: {e}")

        return states

    def _get_broker_state(self, name: str, broker) -> BrokerState:
        """Get state from a single broker."""
        try:
            account = broker.get_account_info()
            equity = float(account.get("equity", account.get("total_equity", 0)))
            cash = float(account.get("cash", account.get("available_balance", 0)))
            paper = bool(account.get("paper", False))
        except Exception:
            equity = 0.0
            cash = 0.0
            paper = False

        try:
            positions = broker.get_positions()
        except Exception:
            positions = []

        exposure_long = 0.0
        exposure_short = 0.0
        unrealized = 0.0

        for pos in positions:
            qty = abs(float(pos.get("qty", pos.get("quantity", 0))))
            price = float(pos.get("current_price", pos.get("market_price", 0)))
            market_val = qty * price if price > 0 else abs(float(pos.get("market_val", 0)))

            side = pos.get("side", pos.get("direction", "LONG")).upper()
            if side in ("LONG", "BUY"):
                exposure_long += market_val
            else:
                exposure_short += market_val

            unrealized += float(pos.get("unrealized_pl", pos.get("unrealized_pnl", 0)))

        return BrokerState(
            broker=name,
            equity=equity,
            cash=cash,
            positions=positions,
            n_positions=len(positions),
            exposure_long=exposure_long,
            exposure_short=exposure_short,
            exposure_net=exposure_long - exposure_short,
            exposure_gross=exposure_long + exposure_short,
            unrealized_pnl=unrealized,
            paper=paper,
        )
