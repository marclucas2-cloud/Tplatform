"""
Leverage Manager — Phase-based leverage control with automatic advancement.

Phases: PHASE_1 (1.5x) -> PHASE_2 (2.0x) -> PHASE_3 (2.5x) -> PHASE_4 (3.0x)
Each phase has minimum duration and KPI conditions to advance.

State persisted in data/leverage_state.json.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Ordered phase names for progression
PHASE_ORDER = ["SOFT_LAUNCH", "PHASE_1", "PHASE_2", "PHASE_3", "PHASE_4"]

# Sizing overrides by tag
SIZING_OVERRIDES = {
    "SOFT_LAUNCH": {
        "tier1": {"kelly_fraction": 0.125, "max_position_pct": 0.10},
        "borderline": {"kelly_fraction": 0.0625, "max_position_pct": 0.03, "max_loss_per_trade_pct": 0.005},
    },
    "PHASE_1": {
        "tier1": {"kelly_fraction": 0.25, "max_position_pct": 0.15},
        "borderline": {"kelly_fraction": 0.125, "max_position_pct": 0.05, "max_loss_per_trade_pct": 0.01},
    },
    "PHASE_2": {
        "tier1": {"kelly_fraction": 0.25, "max_position_pct": 0.20},
        "borderline": {"kelly_fraction": 0.125, "max_position_pct": 0.08, "max_loss_per_trade_pct": 0.015},
    },
    "PHASE_3": {
        "tier1": {"kelly_fraction": 0.33, "max_position_pct": 0.20},
        "borderline": {"kelly_fraction": 0.167, "max_position_pct": 0.10, "max_loss_per_trade_pct": 0.02},
    },
    "PHASE_4": {
        "tier1": {"kelly_fraction": 0.50, "max_position_pct": 0.25},
        "borderline": {"kelly_fraction": 0.25, "max_position_pct": 0.12, "max_loss_per_trade_pct": 0.02},
    },
}

# Default paths
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "leverage_schedule.yaml"
DEFAULT_STATE_PATH = Path(__file__).parent.parent / "data" / "leverage_state.json"


class LeverageManager:
    """Manages leverage limits by phase with automatic phase advancement.

    Phases: PHASE_1 (1.5x) -> PHASE_2 (2.0x) -> PHASE_3 (2.5x) -> PHASE_4 (3.0x)
    Each phase has minimum duration and KPI conditions to advance.

    State persisted in data/leverage_state.json.
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH

        # Load YAML config
        with open(self._config_path, "r") as f:
            self._config = yaml.safe_load(f)

        self._phases = self._config["phases"]

        # Load or initialise state
        self._state = self._load_state()
        logger.info(
            "LeverageManager initialised — phase=%s max_leverage=%.1fx",
            self._state["current_phase"],
            self.max_leverage,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_phase(self) -> str:
        """Current phase name."""
        return self._state["current_phase"]

    @property
    def max_leverage(self) -> float:
        """Maximum leverage allowed for current phase."""
        return float(self._phases[self.current_phase]["max_leverage"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_leverage(self, proposed_leverage: float) -> dict:
        """Check if proposed leverage is within limits.

        Returns:
            dict with keys: allowed, max_leverage, current_phase, reason
        """
        allowed = proposed_leverage <= self.max_leverage
        reason = "OK" if allowed else (
            f"Proposed leverage {proposed_leverage:.2f}x exceeds "
            f"phase {self.current_phase} max of {self.max_leverage:.1f}x"
        )
        return {
            "allowed": allowed,
            "max_leverage": self.max_leverage,
            "current_phase": self.current_phase,
            "reason": reason,
        }

    def can_advance_phase(self, kpi: dict) -> dict:
        """Check if conditions to advance to next phase are met.

        Args:
            kpi: dict with keys matching advance_conditions
                 (e.g. sharpe_30d, drawdown_pct, trades, critical_bugs, etc.)

        Returns:
            dict with keys: can_advance, next_phase, missing_conditions, met_conditions
        """
        phase_idx = PHASE_ORDER.index(self.current_phase)
        if phase_idx >= len(PHASE_ORDER) - 1:
            return {
                "can_advance": False,
                "next_phase": None,
                "missing_conditions": [],
                "met_conditions": [],
                "reason": "Already at maximum phase (PHASE_4)",
            }

        next_phase = PHASE_ORDER[phase_idx + 1]

        # Check min duration
        met = []
        missing = []

        min_duration = self._phases[self.current_phase].get("min_duration_days", 0)
        if min_duration > 0:
            days_in_phase = self._days_in_current_phase()
            if days_in_phase >= min_duration:
                met.append(f"min_duration_days: {days_in_phase}/{min_duration}")
            else:
                missing.append(
                    f"min_duration_days: {days_in_phase}/{min_duration} "
                    f"({min_duration - days_in_phase} days remaining)"
                )

        # Check advance conditions
        conditions = self._phases[self.current_phase].get("advance_conditions", {})
        for cond_name, threshold in conditions.items():
            passed = self._evaluate_condition(cond_name, threshold, kpi)
            actual = self._get_kpi_value(cond_name, kpi)
            if passed:
                met.append(f"{cond_name}: {actual} (threshold: {threshold})")
            else:
                missing.append(f"{cond_name}: {actual} (threshold: {threshold})")

        can_advance = len(missing) == 0
        return {
            "can_advance": can_advance,
            "next_phase": next_phase,
            "missing_conditions": missing,
            "met_conditions": met,
        }

    def advance_phase(self, kpi: Optional[dict] = None) -> str:
        """Advance to next phase. Persists state.

        Args:
            kpi: optional KPI dict. If provided, conditions are verified first.

        Returns:
            New phase name.

        Raises:
            ValueError: if already at max phase or conditions not met.
        """
        phase_idx = PHASE_ORDER.index(self.current_phase)
        if phase_idx >= len(PHASE_ORDER) - 1:
            raise ValueError(
                f"Cannot advance beyond {self.current_phase} (maximum phase)"
            )

        if kpi is not None:
            check = self.can_advance_phase(kpi)
            if not check["can_advance"]:
                raise ValueError(
                    f"Conditions not met to advance from {self.current_phase}: "
                    f"{check['missing_conditions']}"
                )

        old_phase = self.current_phase
        new_phase = PHASE_ORDER[phase_idx + 1]
        now_iso = datetime.now(timezone.utc).isoformat()

        # Record history
        self._state.setdefault("history", []).append({
            "from_phase": old_phase,
            "to_phase": new_phase,
            "timestamp": now_iso,
        })

        self._state["current_phase"] = new_phase
        self._state["phase_start_date"] = now_iso
        self._save_state()

        logger.info(
            "Phase advanced: %s -> %s (max_leverage: %.1fx -> %.1fx)",
            old_phase,
            new_phase,
            self._phases[old_phase]["max_leverage"],
            self._phases[new_phase]["max_leverage"],
        )
        return new_phase

    def get_status(self) -> dict:
        """Full status: current phase, leverage, days in phase, conditions progress."""
        phase_cfg = self._phases[self.current_phase]
        days = self._days_in_current_phase()
        min_duration = phase_cfg.get("min_duration_days", 0)
        conditions = phase_cfg.get("advance_conditions", {})

        phase_idx = PHASE_ORDER.index(self.current_phase)
        next_phase = PHASE_ORDER[phase_idx + 1] if phase_idx < len(PHASE_ORDER) - 1 else None
        next_leverage = (
            self._phases[next_phase]["max_leverage"] if next_phase else None
        )

        return {
            "current_phase": self.current_phase,
            "max_leverage": self.max_leverage,
            "days_in_phase": days,
            "min_duration_days": min_duration,
            "duration_met": days >= min_duration if min_duration > 0 else True,
            "advance_conditions": dict(conditions),
            "next_phase": next_phase,
            "next_max_leverage": next_leverage,
            "phase_start_date": self._state["phase_start_date"],
            "history": self._state.get("history", []),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self):
        """Persist state to JSON file (atomic write)."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_path.parent), suffix='.tmp'
            )
            try:
                with os.fdopen(tmp_fd, 'w') as f:
                    json.dump(self._state, f, indent=2)
                os.replace(tmp_path, str(self._state_path))
            except:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as e:
            logger.error("Failed to save state: %s", e)
        logger.debug("State saved to %s", self._state_path)

    def _load_state(self) -> dict:
        """Load state from JSON file, or create default state."""
        if self._state_path.exists():
            try:
                with open(self._state_path, "r") as f:
                    state = json.load(f)
                # Validate loaded state
                if state.get("current_phase") in PHASE_ORDER:
                    return state
                logger.warning(
                    "Invalid phase '%s' in state file, resetting",
                    state.get("current_phase"),
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Corrupt state file (%s), resetting", e)

        # Default state: SOFT_LAUNCH
        default = {
            "current_phase": "SOFT_LAUNCH",
            "phase_start_date": datetime.now(timezone.utc).isoformat(),
            "history": [],
        }
        return default

    def get_sizing(self, strategy_tag: str = "tier1", strategy_name: str = None) -> dict:
        """Get sizing parameters for current phase and strategy tag.

        Args:
            strategy_tag: "tier1" or "borderline"
            strategy_name: optional specific strategy name for per-strategy overrides

        Returns:
            dict with kelly_fraction, max_position_pct, and optionally max_loss_per_trade_pct
        """
        phase = self.current_phase

        # Check per-strategy override in config first
        if strategy_name:
            phase_cfg = self._phases.get(phase, {})
            overrides = phase_cfg.get("overrides", {})
            if strategy_name in overrides:
                return dict(overrides[strategy_name])

        # Then check SIZING_OVERRIDES by phase/tag
        if phase in SIZING_OVERRIDES and strategy_tag in SIZING_OVERRIDES[phase]:
            return dict(SIZING_OVERRIDES[phase][strategy_tag])
        # Default fallback
        return {"kelly_fraction": 0.25, "max_position_pct": 0.15}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _days_in_current_phase(self) -> int:
        """Number of days since current phase started."""
        start = datetime.fromisoformat(self._state["phase_start_date"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - start).days

    def _get_kpi_value(self, cond_name: str, kpi: dict):
        """Extract the KPI value matching a condition name.

        Mapping:
            min_sharpe_30d  -> kpi['sharpe_30d']
            max_drawdown_pct -> kpi['drawdown_pct']
            min_trades -> kpi['trades']
            zero_critical_bugs -> kpi['critical_bugs']
            max_cost_ratio -> kpi['cost_ratio']
            min_capital -> kpi['capital']
            min_sharpe_60d -> kpi['sharpe_60d']
            min_sharpe_90d -> kpi['sharpe_90d']
        """
        # Strip min_/max_/zero_ prefix to derive KPI key
        key = cond_name
        for prefix in ("min_", "max_", "zero_"):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break

        return kpi.get(key)

    def _evaluate_condition(self, cond_name: str, threshold, kpi: dict) -> bool:
        """Evaluate a single advance condition against KPI data.

        Returns True if the condition is satisfied.
        """
        actual = self._get_kpi_value(cond_name, kpi)
        if actual is None:
            return False

        if cond_name.startswith("min_"):
            return actual >= threshold
        elif cond_name.startswith("max_"):
            return actual <= threshold
        elif cond_name.startswith("zero_"):
            # zero_critical_bugs: threshold is True, actual should be 0
            return actual == 0
        else:
            # Unknown prefix: exact match
            return actual == threshold
