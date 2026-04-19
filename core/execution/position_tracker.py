"""PositionTracker — registry + persistance des PositionStateMachine.

Phase B post-XXL plan (2026-04-19). Same pattern as OrderTracker.

Mode shadow par defaut: le tracker est instancie en parallele du state existant
(futures_positions_*.json, paper_*_state.json) sans le remplacer. Permet de
collecter des donnees + comparer divergences avant migration source-of-truth.

Persistence atomique sur chaque transition (tempfile + os.replace + fsync).
Recovery au boot via load_state() + recovery_summary().
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from core.execution.position_state_machine import (
    IllegalPositionTransition,
    PositionInvariantViolation,
    PositionState,
    PositionStateMachine,
)

logger = logging.getLogger("execution.position_tracker")

POSITION_TRACKER_SCHEMA_VERSION = 1


class PositionTracker:
    """Thread-safe registry of all positions and their state machines.

    Optional persistence via state_path: atomic save on every transition + load
    on init. recovery_summary() exposes active positions for boot reconciliation.
    """

    def __init__(self, alert_callback=None, state_path: Path | None = None):
        self._positions: dict[str, PositionStateMachine] = {}
        self._lock = threading.Lock()
        self._alert_cb = alert_callback
        self._state_path = state_path
        self._recovered_count = 0
        self._recovered_active: list[str] = []
        if state_path is not None:
            self._load_state()

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def create_position(
        self,
        symbol: str,
        side: str,
        broker: str = "",
        order_id: str | None = None,
    ) -> PositionStateMachine:
        """Create a new PENDING position (after order SUBMITTED, before fill)."""
        position_id = f"POS-{uuid.uuid4().hex[:8].upper()}"
        psm = PositionStateMachine(
            position_id=position_id,
            symbol=symbol,
            side=side.upper(),
            broker=broker,
            order_id=order_id,
        )
        with self._lock:
            self._positions[position_id] = psm
        logger.info(
            f"Position created: {position_id} {side} {symbol} broker={broker}"
        )
        self.save_state()
        return psm

    def confirm_open(
        self,
        position_id: str,
        entry_price: float,
        quantity: float,
        sl_price: float,
    ) -> bool:
        """Transition PENDING -> OPEN after order fill + SL placed.

        Invariant: SL must be in place (guard_open raises otherwise).
        """
        psm = self._get(position_id)
        if not psm:
            return False
        try:
            ok = psm.transition(
                PositionState.OPEN,
                has_sl=True,
                sl_price=sl_price,
                entry_price=entry_price,
                quantity=quantity,
            )
        except (IllegalPositionTransition, PositionInvariantViolation) as e:
            self._alert(f"PSM transition error: {e}")
            return False
        self.save_state()
        return ok

    def reduce(self, position_id: str, remaining_quantity: float) -> bool:
        """Transition OPEN -> REDUCING (partial close in progress)."""
        psm = self._get(position_id)
        if not psm:
            return False
        try:
            ok = psm.transition(PositionState.REDUCING)
        except IllegalPositionTransition as e:
            self._alert(str(e))
            return False
        if ok:
            psm.quantity = remaining_quantity
        self.save_state()
        return ok

    def closing(self, position_id: str) -> bool:
        """Transition OPEN -> CLOSING (full close in progress)."""
        psm = self._get(position_id)
        if not psm:
            return False
        try:
            ok = psm.transition(PositionState.CLOSING)
        except IllegalPositionTransition as e:
            self._alert(str(e))
            return False
        self.save_state()
        return ok

    def close_complete(
        self,
        position_id: str,
        realized_pnl: float,
    ) -> bool:
        """Transition CLOSING -> CLOSED (final, terminal)."""
        psm = self._get(position_id)
        if not psm:
            return False
        try:
            ok = psm.transition(
                PositionState.CLOSED,
                realized_pnl=realized_pnl,
            )
        except IllegalPositionTransition as e:
            self._alert(str(e))
            return False
        self.save_state()
        return ok

    def emergency_close(self, position_id: str, reason: str = "kill_switch") -> bool:
        """Force into EMERGENCY state (kill switch / margin critical / ...)."""
        psm = self._get(position_id)
        if not psm:
            return False
        try:
            ok = psm.transition(PositionState.EMERGENCY)
        except IllegalPositionTransition as e:
            self._alert(str(e))
            return False
        psm.history.append({"emergency_reason": reason})
        self.save_state()
        return ok

    def mark_orphan(self, symbol: str, broker: str, qty: float) -> PositionStateMachine:
        """Create an ORPHAN position (broker has it, no internal record).

        Caller (reconciliation) detects orphan -> marks here -> manual decision
        to adopt (transition to OPEN) or close.
        """
        position_id = f"POS-ORPHAN-{uuid.uuid4().hex[:8].upper()}"
        psm = PositionStateMachine(
            position_id=position_id,
            symbol=symbol,
            broker=broker,
            quantity=qty,
            state=PositionState.ORPHAN,
        )
        with self._lock:
            self._positions[position_id] = psm
        self._alert(
            f"ORPHAN position registered: {position_id} {symbol} qty={qty} on {broker}"
        )
        self.save_state()
        return psm

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, position_id: str) -> PositionStateMachine | None:
        return self._get(position_id)

    def get_active_positions(self) -> list[PositionStateMachine]:
        with self._lock:
            return [p for p in self._positions.values() if p.is_active]

    def get_positions_by_symbol(self, symbol: str) -> list[PositionStateMachine]:
        with self._lock:
            return [p for p in self._positions.values() if p.symbol == symbol]

    def get_orphans(self) -> list[PositionStateMachine]:
        with self._lock:
            return [
                p for p in self._positions.values()
                if p.state == PositionState.ORPHAN
            ]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_terminal(self, max_age_hours: int = 24) -> int:
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        to_remove = []
        with self._lock:
            for pid, psm in self._positions.items():
                if psm.is_terminal and psm.created_at.timestamp() < cutoff:
                    to_remove.append(pid)
            for pid in to_remove:
                del self._positions[pid]
        if to_remove:
            self.save_state()
        return len(to_remove)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        if self._state_path is None:
            return
        with self._lock:
            payload = {
                "schema_version": POSITION_TRACKER_SCHEMA_VERSION,
                "saved_at": datetime.now().isoformat(),
                "positions": {pid: psm.to_dict() for pid, psm in self._positions.items()},
            }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=self._state_path.name + ".",
            suffix=".tmp",
            dir=str(self._state_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._state_path)
        except Exception as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.critical(f"PositionTracker save_state FAILED: {exc}")

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.critical(
                f"PositionTracker state CORRUPT at {self._state_path}: {exc}"
            )
            self._alert(f"PositionTracker state CORRUPT: {exc}")
            return

        if not isinstance(raw, dict) or raw.get("schema_version") != POSITION_TRACKER_SCHEMA_VERSION:
            logger.critical(
                f"PositionTracker schema mismatch at {self._state_path}: "
                f"expected v{POSITION_TRACKER_SCHEMA_VERSION}, got {raw.get('schema_version')!r}"
            )
            return

        with self._lock:
            for pid, raw_psm in raw.get("positions", {}).items():
                try:
                    self._positions[pid] = PositionStateMachine.from_dict(raw_psm)
                    self._recovered_count += 1
                    if self._positions[pid].is_active:
                        self._recovered_active.append(pid)
                except (KeyError, ValueError, TypeError) as exc:
                    logger.error(f"PositionTracker: skipping corrupt entry {pid}: {exc}")

        logger.info(
            f"PositionTracker recovered {self._recovered_count} positions from "
            f"{self._state_path} ({len(self._recovered_active)} still active)"
        )

    def recovery_summary(self) -> dict:
        return {
            "total_recovered": self._recovered_count,
            "active_position_ids": list(self._recovered_active),
            "orphan_position_ids": [
                pid for pid, psm in self._positions.items()
                if psm.state == PositionState.ORPHAN
            ],
            "state_path": str(self._state_path) if self._state_path else None,
        }

    # ------------------------------------------------------------------

    def _get(self, position_id: str) -> PositionStateMachine | None:
        with self._lock:
            return self._positions.get(position_id)

    def _alert(self, message: str) -> None:
        logger.error(message)
        if self._alert_cb:
            try:
                self._alert_cb(message)
            except Exception:
                pass
