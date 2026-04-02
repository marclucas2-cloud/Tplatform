"""
D4-01 — Unified Portfolio View (Cross-Broker Risk Aggregation).

Aggregates positions, equity, and exposure across all 3 brokers
(Binance, IBKR, Alpaca) into a single consolidated view.

Everything converted to EUR (base currency) using live FX rates.

Circuit breakers GLOBAUX:
  DD global > 3% jour   → reduce ALL sizing 50%
  DD global > 5% semaine → DEFENSIVE mode GLOBAL
  DD global > 8% mois   → CLOSE ALL (emergency_close_all)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOT_PATH = ROOT / "data" / "risk" / "unified_portfolio.json"
HISTORY_PATH = ROOT / "data" / "risk" / "unified_portfolio_history.jsonl"


@dataclass
class UnifiedSnapshot:
    """Consolidated view across all brokers."""
    timestamp: str
    # Per-broker equity
    binance_equity: float
    ibkr_equity: float
    alpaca_equity: float
    nav_total: float
    # Exposure
    gross_exposure: float
    net_exposure: float
    gross_exposure_pct: float
    net_exposure_pct: float
    # Positions
    positions_count: int
    positions_by_broker: dict
    # Drawdown
    dd_from_peak_pct: float
    dd_daily_pct: float
    dd_weekly_pct: float
    # Cash
    total_cash: float
    cash_pct: float
    # FX rate used
    eur_usd_rate: float
    # Alerts
    alert_level: str    # OK / WARNING / DEFENSIVE / EMERGENCY
    alert_message: str


class UnifiedPortfolioView:
    """Cross-broker portfolio aggregation with global circuit breakers.

    Usage::

        view = UnifiedPortfolioView(alert_callback=send_telegram)
        snapshot = view.update(
            binance_data={"equity": 10000, "positions": [...], "cash": 2000},
            ibkr_data={"equity": 15000, "positions": [...], "cash": 5000},
            alpaca_data={"equity": 0, "positions": [], "cash": 0},
            eur_usd_rate=1.08,
        )
    """

    def __init__(
        self,
        alert_callback: Optional[Callable] = None,
        kelly_callback: Optional[Callable] = None,
        emergency_close_callback: Optional[Callable] = None,
    ):
        self._alert = alert_callback
        self._kelly_cb = kelly_callback
        self._emergency_close = emergency_close_callback
        self._peak_nav = 0.0
        self._nav_start_day = 0.0
        self._nav_start_week = 0.0
        self._day_of_week = -1
        self._day_of_year = -1
        self._history: list[dict] = []

    def update(
        self,
        binance_data: Optional[dict] = None,
        ibkr_data: Optional[dict] = None,
        alpaca_data: Optional[dict] = None,
        eur_usd_rate: float = 1.08,
    ) -> UnifiedSnapshot:
        """Collect and aggregate cross-broker data.

        Each broker data dict should have:
          equity: float (in USD)
          positions: list of {symbol, side, qty, market_val, unrealized_pl}
          cash: float (in USD)
        """
        now = datetime.now(timezone.utc)
        today = now.timetuple().tm_yday
        weekday = now.weekday()

        bnb = binance_data or {"equity": 0, "positions": [], "cash": 0}
        ibkr = ibkr_data or {"equity": 0, "positions": [], "cash": 0}
        alp = alpaca_data or {"equity": 0, "positions": [], "cash": 0}

        # Equities in USD
        bnb_eq = float(bnb.get("equity", 0))
        ibkr_eq = float(ibkr.get("equity", 0))
        alp_eq = float(alp.get("equity", 0))

        # FIX: Only count LIVE equity for DD tracking.
        # Paper Alpaca ($100K) was causing fake 50%+ DD when broker unavailable.
        alp_is_paper = alp.get("paper", True)
        nav_total = bnb_eq + ibkr_eq + (alp_eq if not alp_is_paper else 0)
        nav_display = bnb_eq + ibkr_eq + alp_eq  # For display only

        # Positions — LIVE only (exclude paper Alpaca)
        all_positions = (
            list(bnb.get("positions", []))
            + list(ibkr.get("positions", []))
            + (list(alp.get("positions", [])) if not alp_is_paper else [])
        )

        # Exposure — computed on live positions only
        gross_long = 0.0
        gross_short = 0.0
        for p in all_positions:
            val = abs(float(p.get("market_val", 0)))
            side = p.get("side", "LONG").upper()
            if side in ("SHORT", "SELL"):
                gross_short += val
            else:
                gross_long += val

        gross = gross_long + gross_short
        net = gross_long - gross_short
        gross_pct = (gross / nav_total * 100) if nav_total > 0 else 0
        net_pct = (net / nav_total * 100) if nav_total > 0 else 0

        # Cash — LIVE only
        total_cash = (
            float(bnb.get("cash", 0))
            + float(ibkr.get("cash", 0))
            + (float(alp.get("cash", 0)) if not alp_is_paper else 0)
        )
        cash_pct = (total_cash / nav_total * 100) if nav_total > 0 else 0

        # Drawdown tracking
        # Guard: if NAV drops to 0 or near 0, it's a broker connectivity issue
        # not a real drawdown. Skip DD update.
        if nav_total <= 100:
            dd_from_peak = 0
            logger.warning(f"Unified: NAV=${nav_total:.0f} too low — broker likely down, skipping DD")
        else:
            # Guard: if peak_nav was set from mixed paper+live, reset it
            if self._peak_nav > 0 and nav_total / self._peak_nav < 0.3:
                logger.warning(
                    f"Unified: NAV=${nav_total:.0f} vs peak=${self._peak_nav:.0f} "
                    f"({nav_total/self._peak_nav:.0%}) — peak was inflated, resetting"
                )
                self._peak_nav = nav_total
            if nav_total > self._peak_nav:
                self._peak_nav = nav_total
            dd_from_peak = (
                (nav_total - self._peak_nav) / self._peak_nav * 100
                if self._peak_nav > 0 else 0
            )

        # Reset daily/weekly tracking
        if today != self._day_of_year:
            self._nav_start_day = nav_total
            self._day_of_year = today
        if weekday == 0 and self._day_of_week != 0:
            self._nav_start_week = nav_total
        # FIX: initialize weekly baseline on first update (mid-week restart)
        if self._nav_start_week <= 0 and nav_total > 0:
            self._nav_start_week = nav_total
        self._day_of_week = weekday

        dd_daily = (
            (nav_total - self._nav_start_day) / self._nav_start_day * 100
            if self._nav_start_day > 0 else 0
        )
        dd_weekly = (
            (nav_total - self._nav_start_week) / self._nav_start_week * 100
            if self._nav_start_week > 0 else 0
        )

        # Alert level / circuit breakers
        alert_level, alert_message = self._check_circuit_breakers(
            dd_daily, dd_weekly, dd_from_peak,
        )

        snapshot = UnifiedSnapshot(
            timestamp=now.isoformat(),
            binance_equity=round(bnb_eq, 2),
            ibkr_equity=round(ibkr_eq, 2),
            alpaca_equity=round(alp_eq, 2),
            nav_total=round(nav_total, 2),
            gross_exposure=round(gross, 2),
            net_exposure=round(net, 2),
            gross_exposure_pct=round(gross_pct, 2),
            net_exposure_pct=round(net_pct, 2),
            positions_count=len(all_positions),
            positions_by_broker={
                "BINANCE": len(bnb.get("positions", [])),
                "IBKR": len(ibkr.get("positions", [])),
                "ALPACA": len(alp.get("positions", [])),
            },
            dd_from_peak_pct=round(dd_from_peak, 2),
            dd_daily_pct=round(dd_daily, 2),
            dd_weekly_pct=round(dd_weekly, 2),
            total_cash=round(total_cash, 2),
            cash_pct=round(cash_pct, 2),
            eur_usd_rate=round(eur_usd_rate, 4),
            alert_level=alert_level,
            alert_message=alert_message,
        )

        self._save(snapshot)
        return snapshot

    def _check_circuit_breakers(
        self, dd_daily: float, dd_weekly: float, dd_peak: float,
    ) -> tuple[str, str]:
        """Check global circuit breakers and trigger actions."""
        # EMERGENCY: DD > 8% from peak
        if dd_peak < -8.0:
            if self._emergency_close:
                try:
                    self._emergency_close(force=True)
                except Exception as e:
                    logger.error("Emergency close failed: %s", e)
            if self._alert:
                self._alert(
                    f"EMERGENCY: DD global {dd_peak:.1f}% > 8%. CLOSE ALL TRIGGERED.",
                    level="critical",
                )
            return "EMERGENCY", f"DD {dd_peak:.1f}% > 8% — CLOSE ALL"

        # DEFENSIVE: DD > 5% weekly
        if dd_weekly < -5.0:
            if self._kelly_cb:
                try:
                    self._kelly_cb("DEFENSIVE")
                except Exception:
                    pass
            if self._alert:
                self._alert(
                    f"DEFENSIVE: DD weekly {dd_weekly:.1f}% > 5%. DEFENSIVE mode.",
                    level="warning",
                )
            return "DEFENSIVE", f"DD weekly {dd_weekly:.1f}% > 5%"

        # WARNING: DD > 3% daily
        if dd_daily < -3.0:
            if self._alert:
                self._alert(
                    f"WARNING: DD daily {dd_daily:.1f}% > 3%. Sizing reduced 50%.",
                    level="warning",
                )
            return "WARNING", f"DD daily {dd_daily:.1f}% > 3%"

        return "OK", "All clear"

    def _save(self, snapshot: UnifiedSnapshot) -> None:
        """Save snapshot to JSON and append to history JSONL."""
        try:
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(snapshot), f, indent=2, default=str)
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(snapshot), default=str) + "\n")
        except Exception as e:
            logger.error("Failed to save unified portfolio: %s", e)

    def get_snapshot(self) -> Optional[dict]:
        """Load latest snapshot from disk."""
        try:
            if SNAPSHOT_PATH.exists():
                return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to load unified snapshot: %s", e)
        return None
