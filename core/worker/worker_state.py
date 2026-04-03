"""Thread-safe shared state across all worker cycles.

Provides granular locking per domain (positions, regime, kill switches, metrics)
to avoid contention. Each cycle receives a reference to WorkerState.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class WorkerState:
    """Shared state across all worker cycles. Thread-safe."""

    # Locks per domain (not one global lock — too much contention)
    _position_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )
    _regime_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False
    )
    _kill_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )
    _metrics_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )

    # Position state (source of truth = broker, this is local cache)
    _positions: dict = field(default_factory=dict)

    # Regime state
    _current_regime: dict = field(default_factory=lambda: {
        "fx": "UNKNOWN",
        "crypto": "UNKNOWN",
        "us_equity": "UNKNOWN",
        "eu_equity": "UNKNOWN",
        "global": "UNKNOWN",
    })
    _regime_updated_at: Optional[datetime] = None

    # Kill switches
    _kill_switches: dict = field(default_factory=lambda: {
        "ibkr": False,
        "binance": False,
        "alpaca": False,
        "global": False,
    })
    _kill_reasons: dict = field(default_factory=dict)

    # Cycle metrics
    _cycle_metrics: dict = field(default_factory=dict)

    # --- Positions ---

    def get_positions(self, broker: Optional[str] = None) -> dict:
        """Get all positions, optionally filtered by broker."""
        with self._position_lock:
            if broker:
                return {
                    k: v for k, v in self._positions.items()
                    if v.get("broker") == broker
                }
            return dict(self._positions)

    def update_position(self, key: str, position: dict) -> None:
        with self._position_lock:
            self._positions[key] = position

    def remove_position(self, key: str) -> None:
        with self._position_lock:
            self._positions.pop(key, None)

    def clear_positions(self, broker: Optional[str] = None) -> None:
        with self._position_lock:
            if broker:
                self._positions = {
                    k: v for k, v in self._positions.items()
                    if v.get("broker") != broker
                }
            else:
                self._positions.clear()

    # --- Regime ---

    def get_regime(self, asset_class: str = "global") -> str:
        with self._regime_lock:
            return self._current_regime.get(asset_class, "UNKNOWN")

    def set_regime(self, asset_class: str, regime: str) -> None:
        with self._regime_lock:
            self._current_regime[asset_class] = regime
            self._regime_updated_at = datetime.now()

    def get_all_regimes(self) -> dict:
        with self._regime_lock:
            return dict(self._current_regime)

    @property
    def regime_age_seconds(self) -> Optional[float]:
        with self._regime_lock:
            if self._regime_updated_at is None:
                return None
            return (datetime.now() - self._regime_updated_at).total_seconds()

    # --- Kill switches ---

    def is_killed(self, broker: str = "global") -> bool:
        with self._kill_lock:
            return (
                self._kill_switches.get(broker, False)
                or self._kill_switches.get("global", False)
            )

    def activate_kill(self, broker: str, reason: str = "") -> None:
        with self._kill_lock:
            self._kill_switches[broker] = True
            if reason:
                self._kill_reasons[broker] = reason

    def deactivate_kill(self, broker: str) -> None:
        with self._kill_lock:
            self._kill_switches[broker] = False
            self._kill_reasons.pop(broker, None)

    def get_kill_reason(self, broker: str) -> str:
        with self._kill_lock:
            return self._kill_reasons.get(broker, "")

    def get_active_kills(self) -> dict:
        with self._kill_lock:
            return {
                k: self._kill_reasons.get(k, "no reason")
                for k, v in self._kill_switches.items() if v
            }

    # --- Cycle metrics ---

    def record_cycle_metrics(self, name: str, metrics: dict) -> None:
        with self._metrics_lock:
            self._cycle_metrics[name] = metrics

    def get_cycle_metrics(self, name: str) -> Optional[dict]:
        with self._metrics_lock:
            return self._cycle_metrics.get(name)

    def get_all_cycle_metrics(self) -> dict:
        with self._metrics_lock:
            return dict(self._cycle_metrics)

    # --- Snapshot ---

    def snapshot(self) -> dict:
        """Thread-safe snapshot of entire state for event logging."""
        return {
            "positions": self.get_positions(),
            "regimes": self.get_all_regimes(),
            "kills": self.get_active_kills(),
            "cycle_metrics": self.get_all_cycle_metrics(),
        }
