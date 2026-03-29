"""Phase 1 Safety Mode — conservative limits for initial live trading.

When SAFE_LIVE_MODE is active:
  - Max 5 strategies active simultaneously
  - Max leverage 1.0x
  - Max ERE 20%
  - Auto-disable on any anomaly

Usage:
    safety = SafetyMode()
    safety.activate()
    if not safety.can_trade(n_active=6):
        # Too many strategies
    safety.check_anomaly(ere_pct=0.25)  # Auto-disables if > 20%
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafetyLimits:
    max_strategies: int = 5
    max_leverage: float = 1.0
    max_ere_pct: float = 0.20  # 20%
    max_drawdown_pct: float = 0.03  # 3%
    max_correlation_score: float = 0.80
    max_positions: int = 10


class SafetyMode:
    """Phase 1 Safety Mode for initial live deployment."""

    def __init__(
        self,
        limits: Optional[SafetyLimits] = None,
        data_dir: str = "data",
    ):
        self.limits = limits or SafetyLimits()
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._data_dir / "safety_mode_state.json"

        self._active: bool = True  # Active by default in Phase 1
        self._disabled_reason: Optional[str] = None
        self._disabled_at: Optional[datetime] = None
        self._anomaly_count: int = 0
        self._load_state()

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> None:
        """Enable safety mode."""
        self._active = True
        self._disabled_reason = None
        self._disabled_at = None
        self._anomaly_count = 0
        self._save_state()
        logger.info("Safety mode ACTIVATED")

    def deactivate(self, authorized_by: str = "manual") -> None:
        """Disable safety mode (e.g., after Phase 1 validation)."""
        self._active = False
        self._save_state()
        logger.info(f"Safety mode DEACTIVATED by {authorized_by}")

    def can_trade(
        self,
        n_active_strategies: int = 0,
        n_positions: int = 0,
        current_leverage: float = 0.0,
    ) -> Dict[str, Any]:
        """Check if trading is allowed under safety constraints.

        Returns: {"allowed": bool, "violations": [...]}
        """
        if not self._active:
            return {"allowed": True, "violations": [], "safety_mode": False}

        violations = []

        if n_active_strategies > self.limits.max_strategies:
            violations.append(
                f"strategies: {n_active_strategies} > {self.limits.max_strategies}"
            )

        if n_positions >= self.limits.max_positions:
            violations.append(
                f"positions: {n_positions} >= {self.limits.max_positions}"
            )

        if current_leverage > self.limits.max_leverage:
            violations.append(
                f"leverage: {current_leverage:.2f}x > {self.limits.max_leverage:.1f}x"
            )

        return {
            "allowed": len(violations) == 0,
            "violations": violations,
            "safety_mode": True,
        }

    def check_anomaly(
        self,
        ere_pct: float = 0.0,
        drawdown_pct: float = 0.0,
        correlation_score: float = 0.0,
    ) -> Dict[str, Any]:
        """Check for anomalies and auto-disable if thresholds breached.

        Returns: {"anomaly": bool, "action": str, "details": [...]}
        """
        if not self._active:
            return {"anomaly": False, "action": "NONE", "details": []}

        details = []

        if ere_pct > self.limits.max_ere_pct:
            details.append(f"ERE {ere_pct:.1%} > {self.limits.max_ere_pct:.0%}")

        if drawdown_pct > self.limits.max_drawdown_pct:
            details.append(
                f"DD {drawdown_pct:.1%} > {self.limits.max_drawdown_pct:.0%}"
            )

        if correlation_score > self.limits.max_correlation_score:
            details.append(
                f"corr {correlation_score:.2f} > {self.limits.max_correlation_score:.2f}"
            )

        if details:
            self._anomaly_count += 1
            self._disabled_reason = "; ".join(details)
            self._disabled_at = datetime.utcnow()
            self._save_state()

            action = "ALERT"
            if self._anomaly_count >= 3:
                action = "DISABLE_TRADING"
                logger.warning(
                    f"Safety mode: DISABLE_TRADING after {self._anomaly_count} anomalies: "
                    + self._disabled_reason
                )
            else:
                logger.warning(
                    f"Safety mode anomaly #{self._anomaly_count}: {self._disabled_reason}"
                )

            return {
                "anomaly": True,
                "action": action,
                "details": details,
                "anomaly_count": self._anomaly_count,
            }

        # Reset anomaly count on clean check
        if self._anomaly_count > 0:
            self._anomaly_count = max(0, self._anomaly_count - 1)
            self._save_state()

        return {"anomaly": False, "action": "NONE", "details": []}

    def get_status(self) -> Dict[str, Any]:
        """Full safety mode status for dashboard."""
        return {
            "active": self._active,
            "limits": {
                "max_strategies": self.limits.max_strategies,
                "max_leverage": self.limits.max_leverage,
                "max_ere_pct": self.limits.max_ere_pct,
                "max_drawdown_pct": self.limits.max_drawdown_pct,
                "max_correlation_score": self.limits.max_correlation_score,
                "max_positions": self.limits.max_positions,
            },
            "anomaly_count": self._anomaly_count,
            "disabled_reason": self._disabled_reason,
            "disabled_at": self._disabled_at.isoformat() if self._disabled_at else None,
        }

    def clamp_leverage(self, requested: float) -> float:
        """Clamp leverage to safety limit."""
        if not self._active:
            return requested
        return min(requested, self.limits.max_leverage)

    def filter_strategies(self, strategies: List[str]) -> List[str]:
        """Keep only top N strategies if over limit."""
        if not self._active:
            return strategies
        return strategies[: self.limits.max_strategies]

    # ─── Internal ────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        try:
            state = {
                "active": self._active,
                "anomaly_count": self._anomaly_count,
                "disabled_reason": self._disabled_reason,
                "disabled_at": self._disabled_at.isoformat() if self._disabled_at else None,
                "updated_at": datetime.utcnow().isoformat(),
            }
            self._state_path.write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Failed to save safety state: {e}")

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._active = state.get("active", True)
            self._anomaly_count = state.get("anomaly_count", 0)
            self._disabled_reason = state.get("disabled_reason")
            da = state.get("disabled_at")
            if da:
                self._disabled_at = datetime.fromisoformat(da)
        except Exception as e:
            logger.warning(f"Failed to load safety state: {e}")
