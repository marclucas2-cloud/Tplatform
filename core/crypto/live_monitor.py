"""
MON-001 — CryptoLiveMonitor: Real-time crypto monitoring with JSONL file logging.

Collects portfolio snapshots every CHECK_INTERVAL seconds (5 min default):
  - Equity, P&L, drawdown, positions (spot + margin + earn)
  - Margin health (margin_level, borrow_rate, borrow_cost)
  - Risk metrics (gross/net exposure, kill switch status)
  - Regime (BULL/BEAR/CHOP)

Logs every snapshot to LOG_FILE as JSONL (one JSON object per line).
Dashboard reads this file for historical charts and tables.

Alerts:
  - Drawdown > 5% = CRITICAL, > 3% = WARNING
  - Margin level < 1.3 = CRITICAL, < 1.5 = WARNING
  - Borrow rate > 0.1%/day per position = WARNING
  - Daily PnL loss > 5% of capital = CRITICAL
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


class CryptoLiveMonitor:
    """Real-time crypto portfolio monitor with JSONL file logging."""

    LOG_FILE = "logs/crypto_monitor.jsonl"
    CHECK_INTERVAL = 300  # 5 minutes

    def __init__(
        self,
        broker,
        risk_manager=None,
        capital: float = 10_000,
    ):
        self._broker = broker
        self._risk_manager = risk_manager
        self._start_capital = capital
        self._peak_equity = capital
        self._snapshots: list[dict] = []
        self._log_path = ROOT / self.LOG_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_check(self) -> dict:
        """Collect a snapshot, check alerts, log to JSONL, return snapshot."""
        try:
            snapshot = self._collect_snapshot()
        except Exception as e:
            logger.error(f"Snapshot collection failed: {e}")
            snapshot = {
                "timestamp": datetime.now(UTC).isoformat(),
                "error": str(e),
                "equity": None,
                "pnl_total": None,
                "pnl_pct": None,
                "positions": [],
                "earn_positions": [],
                "balances": {},
                "risk": {},
                "regime": "UNKNOWN",
            }

        # Alerts
        try:
            alerts = self._check_alerts(snapshot)
            snapshot["alerts"] = [
                {"level": lvl, "message": msg} for lvl, msg in alerts
            ]
        except Exception as e:
            logger.error(f"Alert check failed: {e}")
            snapshot["alerts"] = []

        # Log to JSONL
        try:
            self._log(snapshot)
        except Exception as e:
            logger.error(f"JSONL log write failed: {e}")

        # Keep in memory
        self._snapshots.append(snapshot)

        # Trim in-memory snapshots to last 24h worth (288 at 5-min intervals)
        max_snapshots = max(1, int(86400 / self.CHECK_INTERVAL))
        if len(self._snapshots) > max_snapshots:
            self._snapshots = self._snapshots[-max_snapshots:]

        return snapshot

    def get_summary(self, period_hours: float = 24) -> dict | None:
        """Summarize recent snapshots over the given period.

        Returns:
            dict with equity_start, equity_end, pnl_period, max_drawdown,
            avg_positions, alerts_count.  None if no data.
        """
        if not self._snapshots:
            return None

        cutoff = datetime.now(UTC) - timedelta(hours=period_hours)
        cutoff_iso = cutoff.isoformat()

        recent = [
            s for s in self._snapshots
            if s.get("timestamp", "") >= cutoff_iso
        ]

        if not recent:
            return None

        equities = [
            s["equity"] for s in recent
            if s.get("equity") is not None
        ]

        if not equities:
            return None

        equity_start = equities[0]
        equity_end = equities[-1]
        peak = max(equities)
        trough = min(equities)
        max_dd = (trough - peak) / peak * 100 if peak > 0 else 0

        position_counts = [
            len(s.get("positions", []))
            for s in recent
        ]
        avg_positions = (
            sum(position_counts) / len(position_counts)
            if position_counts else 0
        )

        total_alerts = sum(
            len(s.get("alerts", [])) for s in recent
        )

        return {
            "period_hours": period_hours,
            "snapshots_count": len(recent),
            "equity_start": round(equity_start, 2),
            "equity_end": round(equity_end, 2),
            "pnl_period": round(equity_end - equity_start, 2),
            "pnl_period_pct": round(
                (equity_end - equity_start) / equity_start * 100, 2
            ) if equity_start > 0 else 0,
            "max_drawdown_pct": round(max_dd, 2),
            "avg_positions": round(avg_positions, 1),
            "alerts_count": total_alerts,
        }

    # ------------------------------------------------------------------
    # Snapshot collection
    # ------------------------------------------------------------------

    def _collect_snapshot(self) -> dict:
        """Gather full portfolio state from broker."""
        now = datetime.now(UTC).isoformat()

        # -- Positions --
        raw_positions = []
        try:
            raw_positions = self._broker.get_positions()
        except Exception as e:
            logger.warning(f"get_positions failed: {e}")

        positions = []
        for p in raw_positions:
            entry = {
                "symbol": p.get("symbol", ""),
                "side": p.get("side", ""),
                "entry_price": p.get("avg_entry_price", p.get("entry_price", 0)),
                "current_price": p.get("current_price", 0),
                "pnl": p.get("unrealized_pl", 0),
                "strategy": p.get("strategy", ""),
                "mode": p.get("asset_type", ""),
            }

            entry_price = entry["entry_price"]
            current_price = entry["current_price"]
            if entry_price and entry_price > 0:
                if entry["side"].upper() in ("LONG", "BUY"):
                    entry["pnl_pct"] = (current_price - entry_price) / entry_price * 100
                elif entry["side"].upper() in ("SHORT", "SELL"):
                    entry["pnl_pct"] = (entry_price - current_price) / entry_price * 100
                else:
                    entry["pnl_pct"] = 0
            else:
                entry["pnl_pct"] = 0

            # Margin-specific fields
            asset_type = p.get("asset_type", "").upper()
            if asset_type in ("CRYPTO_MARGIN", "MARGIN"):
                entry["borrow_rate"] = p.get("borrow_rate", 0)
                entry["margin_level"] = p.get("margin_level", 0)
                entry["borrow_cost_cumul"] = p.get("borrow_cost_cumul", p.get("interest_accrued", 0))

            positions.append(entry)

        # -- Balances --
        balances = {}
        try:
            account = self._broker.get_account_info()
            balances = {
                "spot_usdt": account.get("spot_usdt", 0),
                "margin_usdt": account.get("margin_usdt", 0),
                "earn_usdt": account.get("earn_usdt", 0),
                "cash": account.get("cash", 0),
            }
        except Exception as e:
            logger.warning(f"get_account_info failed: {e}")
            account = {}

        # -- Equity --
        total_equity = (
            balances.get("spot_usdt", 0)
            + balances.get("margin_usdt", 0)
            + balances.get("earn_usdt", 0)
            + balances.get("cash", 0)
            + sum(p.get("pnl", 0) for p in positions)
        )

        # Track peak for drawdown
        if total_equity > self._peak_equity:
            self._peak_equity = total_equity

        pnl_total = total_equity - self._start_capital
        pnl_pct = (pnl_total / self._start_capital * 100) if self._start_capital > 0 else 0

        drawdown_pct = 0
        if self._peak_equity > 0:
            drawdown_pct = (total_equity - self._peak_equity) / self._peak_equity * 100

        # -- Earn positions --
        earn_positions = []
        earn_data = account.get("earn_positions", {})
        if isinstance(earn_data, dict):
            for asset, info in earn_data.items():
                earn_positions.append({
                    "asset": asset,
                    "amount": info.get("amount", 0),
                    "apy": info.get("apy", 0),
                    "daily_yield": info.get("amount", 0) * info.get("apy", 0) / 36500,
                })
        elif isinstance(earn_data, list):
            for info in earn_data:
                earn_positions.append({
                    "asset": info.get("asset", ""),
                    "amount": info.get("amount", 0),
                    "apy": info.get("apy", 0),
                    "daily_yield": info.get("amount", 0) * info.get("apy", 0) / 36500,
                })

        # -- Risk metrics --
        margin_level = account.get("margin_level", 999)
        gross_long = sum(
            abs(p.get("current_price", 0) * p.get("qty", 1))
            for p in raw_positions
            if p.get("side", "").upper() in ("LONG", "BUY")
        )
        gross_short = sum(
            abs(p.get("current_price", 0) * p.get("qty", 1))
            for p in raw_positions
            if p.get("side", "").upper() in ("SHORT", "SELL")
        )
        gross_total = gross_long + gross_short

        risk = {
            "drawdown_pct": round(drawdown_pct, 2),
            "gross_exposure_pct": round(
                gross_total / total_equity * 100, 2
            ) if total_equity > 0 else 0,
            "net_exposure_pct": round(
                (gross_long - gross_short) / total_equity * 100, 2
            ) if total_equity > 0 else 0,
            "margin_level": round(margin_level, 2),
            "kill_switch_active": False,
        }

        # Check kill switch from risk manager
        if self._risk_manager:
            try:
                ks = self._risk_manager.kill_switch
                risk["kill_switch_active"] = getattr(ks, "is_killed", False)
            except Exception:
                pass

        # -- Regime --
        regime = "UNKNOWN"
        if self._risk_manager:
            try:
                regime = getattr(self._risk_manager, "current_regime", "UNKNOWN")
            except Exception:
                pass

        return {
            "timestamp": now,
            "equity": round(total_equity, 2),
            "pnl_total": round(pnl_total, 2),
            "pnl_pct": round(pnl_pct, 2),
            "positions": positions,
            "earn_positions": earn_positions,
            "balances": {k: round(v, 2) for k, v in balances.items()},
            "risk": risk,
            "regime": regime,
        }

    # ------------------------------------------------------------------
    # Alert checks
    # ------------------------------------------------------------------

    def _check_alerts(self, snapshot: dict) -> list[tuple[str, str]]:
        """Evaluate snapshot against alert thresholds.

        Returns:
            List of (level, message) tuples.
        """
        alerts: list[tuple[str, str]] = []

        # Drawdown alerts
        dd = snapshot.get("risk", {}).get("drawdown_pct", 0)
        if dd < -5:
            alerts.append(("CRITICAL", f"Drawdown {dd:.1f}% exceeds -5% threshold"))
        elif dd < -3:
            alerts.append(("WARNING", f"Drawdown {dd:.1f}% exceeds -3% threshold"))

        # Margin level alerts
        margin_level = snapshot.get("risk", {}).get("margin_level", 999)
        if margin_level < 999:  # Only alert if we have real margin data
            if margin_level < 1.3:
                alerts.append(("CRITICAL", f"Margin level {margin_level:.2f} below 1.3 — liquidation risk"))
            elif margin_level < 1.5:
                alerts.append(("WARNING", f"Margin level {margin_level:.2f} below 1.5"))

        # Borrow rate alerts (per position)
        for pos in snapshot.get("positions", []):
            borrow_rate = pos.get("borrow_rate", 0)
            if borrow_rate > 0.001:  # > 0.1%/day
                alerts.append((
                    "WARNING",
                    f"High borrow rate on {pos['symbol']}: {borrow_rate*100:.3f}%/day"
                ))

        # Daily PnL loss check
        pnl_pct = snapshot.get("pnl_pct", 0)
        # We check against recent snapshots for daily loss
        if self._snapshots:
            # Find snapshot from ~24h ago
            now_str = snapshot.get("timestamp", "")
            try:
                cutoff = datetime.now(UTC) - timedelta(hours=24)
                cutoff_iso = cutoff.isoformat()
                old_snapshots = [
                    s for s in self._snapshots
                    if s.get("timestamp", "") <= cutoff_iso
                    and s.get("equity") is not None
                ]
                if old_snapshots:
                    equity_24h_ago = old_snapshots[-1]["equity"]
                    current_equity = snapshot.get("equity") or 0
                    if equity_24h_ago and equity_24h_ago > 0:
                        daily_loss_pct = (current_equity - equity_24h_ago) / equity_24h_ago * 100
                        if daily_loss_pct < -5:
                            alerts.append((
                                "CRITICAL",
                                f"Daily loss {daily_loss_pct:.1f}% exceeds -5% threshold"
                            ))
            except Exception as e:
                logger.debug(f"Daily loss check failed: {e}")

        return alerts

    # ------------------------------------------------------------------
    # JSONL logging
    # ------------------------------------------------------------------

    def _log(self, snapshot: dict) -> None:
        """Append snapshot as a single JSON line to LOG_FILE."""
        log_dir = self._log_path.parent
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")
