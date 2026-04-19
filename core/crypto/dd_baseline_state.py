"""DD baseline persistence for CryptoRiskManager.

4 boot states:
  - FIRST_BOOT     : no state file ever written -> sync baselines to current
  - STATE_RESTORED : state loaded fresh (last_check < STALE_THRESHOLD_HOURS)
  - STATE_STALE    : state loaded but last_check too old -> keep peak_equity,
                     reset period baselines based on anchors
  - STATE_CORRUPT  : state file exists but unparseable / wrong schema -> ALERT,
                     fall back to FIRST_BOOT behavior

Persisted fields (atomic JSON write):
  - peak_equity
  - daily_start_equity + daily_anchor (UTC date YYYY-MM-DD)
  - weekly_start_equity + weekly_anchor (ISO year-week YYYY-Www)
  - monthly_start_equity + monthly_anchor (YYYY-MM)
  - last_check_ts (unix seconds)
  - session_id (uuid set at first save of a session)
  - schema_version

NOT persisted (session-scoped):
  - hourly_start_equity / _last_hourly_reset (always reset on boot)

Why: reboot in DD must NOT reset peak. The flag _baselines_synced was in-memory
only -> after restart, peak became current_equity -> kill switch silent on real
DD. See feedback_baselines_persistence_bug.md.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STALE_THRESHOLD_HOURS = 6.0


class BootState(str, Enum):
    FIRST_BOOT = "first_boot"
    STATE_RESTORED = "state_restored"
    STATE_STALE = "state_stale"
    STATE_CORRUPT = "state_corrupt"


@dataclass
class DDBaselines:
    peak_equity: float = 0.0
    daily_start_equity: float = 0.0
    daily_anchor: str = ""           # YYYY-MM-DD UTC
    weekly_start_equity: float = 0.0
    weekly_anchor: str = ""          # YYYY-Www UTC ISO
    monthly_start_equity: float = 0.0
    monthly_anchor: str = ""         # YYYY-MM UTC
    last_check_ts: float = 0.0
    session_id: str = ""
    schema_version: int = SCHEMA_VERSION
    # Specialized: total_equity (incl earn) used by worker.py spot/earn transfer
    # detection to distinguish reclassification from real DD. Optional, 0 = unset.
    total_equity: float = 0.0

    @staticmethod
    def utc_anchors(ts: float | None = None) -> tuple[str, str, str]:
        """Return (daily, weekly, monthly) UTC anchors for given ts (default now)."""
        dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=UTC)
        iso_year, iso_week, _ = dt.isocalendar()
        return (
            dt.strftime("%Y-%m-%d"),
            f"{iso_year:04d}-W{iso_week:02d}",
            dt.strftime("%Y-%m"),
        )

    def is_stale(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        if self.last_check_ts <= 0:
            return True
        return (now - self.last_check_ts) > STALE_THRESHOLD_HOURS * 3600


def _migrate_legacy_schema(raw: dict) -> DDBaselines | None:
    """Migrate legacy worker.py schema (no schema_version field).

    Legacy keys: peak_equity, daily_start, weekly_start, monthly_start,
                 last_date, last_week, last_month, last_updated.
    Returns DDBaselines or None if not migratable.
    """
    if "peak_equity" not in raw or "daily_start" not in raw:
        return None
    try:
        peak = float(raw.get("peak_equity", 0.0))
        if peak <= 0:
            return None
        last_updated_iso = raw.get("last_updated", "")
        try:
            last_check_ts = datetime.fromisoformat(
                last_updated_iso.replace("Z", "+00:00")
            ).timestamp() if last_updated_iso else 0.0
        except (ValueError, AttributeError):
            last_check_ts = 0.0
        return DDBaselines(
            peak_equity=peak,
            daily_start_equity=float(raw.get("daily_start", peak)),
            daily_anchor=str(raw.get("last_date", "")),
            weekly_start_equity=float(raw.get("weekly_start", peak)),
            weekly_anchor=str(raw.get("last_week", "")).replace("-W", "-W"),
            monthly_start_equity=float(raw.get("monthly_start", peak)),
            monthly_anchor=str(raw.get("last_month", "")),
            last_check_ts=last_check_ts,
            session_id="migrated-legacy",
            schema_version=SCHEMA_VERSION,
            total_equity=float(raw.get("total_equity", 0.0)),
        )
    except (TypeError, ValueError):
        return None


def load_baselines(path: Path) -> tuple[BootState, DDBaselines]:
    """Load baselines from disk and classify boot state.

    Returns (state, baselines). For FIRST_BOOT/STATE_CORRUPT, baselines are empty
    sentinel and caller must initialize from current equity.

    Supports legacy worker.py schema (no schema_version field) via auto-migration.
    """
    if not path.exists():
        return BootState.FIRST_BOOT, DDBaselines()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(
            f"DD baseline state CORRUPT at {path}: {exc} -> falling back to FIRST_BOOT"
        )
        return BootState.STATE_CORRUPT, DDBaselines()

    if not isinstance(raw, dict):
        logger.error(f"DD baseline state at {path} not a dict -> CORRUPT")
        return BootState.STATE_CORRUPT, DDBaselines()

    schema_version = raw.get("schema_version")
    if schema_version is None:
        legacy = _migrate_legacy_schema(raw)
        if legacy is None:
            logger.error(
                f"DD baseline state at {path} has no schema_version "
                f"and is not migratable -> CORRUPT"
            )
            return BootState.STATE_CORRUPT, DDBaselines()
        logger.info(
            f"DD baseline state at {path} migrated from legacy schema "
            f"(peak=${legacy.peak_equity:,.0f})"
        )
        baselines = legacy
    elif schema_version != SCHEMA_VERSION:
        logger.error(
            f"DD baseline state schema mismatch at {path}: "
            f"expected v{SCHEMA_VERSION}, got {schema_version!r} -> CORRUPT"
        )
        return BootState.STATE_CORRUPT, DDBaselines()
    else:
        try:
            baselines = DDBaselines(
                peak_equity=float(raw.get("peak_equity", 0.0)),
                daily_start_equity=float(raw.get("daily_start_equity", 0.0)),
                daily_anchor=str(raw.get("daily_anchor", "")),
                weekly_start_equity=float(raw.get("weekly_start_equity", 0.0)),
                weekly_anchor=str(raw.get("weekly_anchor", "")),
                monthly_start_equity=float(raw.get("monthly_start_equity", 0.0)),
                monthly_anchor=str(raw.get("monthly_anchor", "")),
                last_check_ts=float(raw.get("last_check_ts", 0.0)),
                session_id=str(raw.get("session_id", "")),
                schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
                total_equity=float(raw.get("total_equity", 0.0)),
            )
        except (TypeError, ValueError) as exc:
            logger.error(
                f"DD baseline state field type error at {path}: {exc} -> CORRUPT"
            )
            return BootState.STATE_CORRUPT, DDBaselines()

    if baselines.peak_equity <= 0:
        logger.error(
            f"DD baseline state has invalid peak_equity={baselines.peak_equity} -> CORRUPT"
        )
        return BootState.STATE_CORRUPT, DDBaselines()

    state = BootState.STATE_STALE if baselines.is_stale() else BootState.STATE_RESTORED
    return state, baselines


def save_baselines(path: Path, baselines: DDBaselines) -> None:
    """Atomic write: tmp file then rename. Never leaves partial state on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(baselines)
    payload["schema_version"] = SCHEMA_VERSION
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp on any error to avoid junk in state dir
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def init_baselines_from_equity(current_equity: float) -> DDBaselines:
    """Build a fresh DDBaselines snapshot anchored on current equity."""
    daily, weekly, monthly = DDBaselines.utc_anchors()
    return DDBaselines(
        peak_equity=current_equity,
        daily_start_equity=current_equity,
        daily_anchor=daily,
        weekly_start_equity=current_equity,
        weekly_anchor=weekly,
        monthly_start_equity=current_equity,
        monthly_anchor=monthly,
        last_check_ts=time.time(),
        session_id=str(uuid.uuid4()),
        schema_version=SCHEMA_VERSION,
    )


def roll_period_anchors(
    baselines: DDBaselines, current_equity: float
) -> tuple[DDBaselines, list[str]]:
    """Reset daily/weekly/monthly baselines if their anchor period has changed.

    Returns (updated_baselines, list_of_periods_rolled).
    Peak is NEVER reset here.
    """
    daily, weekly, monthly = DDBaselines.utc_anchors()
    rolled: list[str] = []

    if baselines.daily_anchor != daily:
        baselines.daily_start_equity = current_equity
        baselines.daily_anchor = daily
        rolled.append("daily")

    if baselines.weekly_anchor != weekly:
        baselines.weekly_start_equity = current_equity
        baselines.weekly_anchor = weekly
        rolled.append("weekly")

    if baselines.monthly_anchor != monthly:
        baselines.monthly_start_equity = current_equity
        baselines.monthly_anchor = monthly
        rolled.append("monthly")

    return baselines, rolled
