"""Canonical quant registry loader.

Replaces regex parsing of `notes:` text in live_whitelist.yaml with a
structured YAML source (config/quant_registry.yaml). Used by promotion_gate,
runtime_audit, and dashboard for machine-readable strategy metadata.

Plan 9.0 (2026-04-19) block B2+B3: promotion_gate must read paper_start_at
and wf_manifest_path from here, not from live_whitelist notes text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, date
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = ROOT / "config" / "quant_registry.yaml"


@dataclass
class QuantEntry:
    strategy_id: str
    book: str
    status: str                                 # disabled | frozen | paper_only | live_micro | live_probation | live_core
    paper_start_at: date | None                 # parsed from YYYY-MM-DD
    live_start_at: date | None
    wf_manifest_path: Path | None               # absolute path if set
    grade: str | None                           # S | A | B | REJECTED | None
    last_wf_run_at: date | None
    is_live: bool
    infra_gaps: list[str] = field(default_factory=list)
    # G2 iter1 (2026-04-19): legitimate reason for absent wf_manifest_path.
    # When set, runtime_audit.py PAPER_WITHOUT_WF check is skipped (not an
    # incoherence, just pending or meta-portfolio).
    wf_exempt_reason: str | None = None

    def age_paper_days(self, now: datetime | None = None) -> int | None:
        """Days on paper. None if paper_start_at is null."""
        if self.paper_start_at is None:
            return None
        now = now or datetime.now(UTC)
        return (now.date() - self.paper_start_at).days

    def has_wf_artifact(self) -> bool:
        """True if wf_manifest_path points to an existing, readable file."""
        if self.wf_manifest_path is None:
            return False
        return self.wf_manifest_path.exists() and self.wf_manifest_path.is_file()


def _parse_date(v) -> date | None:
    if v is None or v == "null":
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _parse_entry(raw: dict) -> QuantEntry:
    manifest_raw = raw.get("wf_manifest_path")
    manifest_path = ROOT / manifest_raw if manifest_raw else None
    return QuantEntry(
        strategy_id=raw["strategy_id"],
        book=raw["book"],
        status=raw.get("status", "unknown"),
        paper_start_at=_parse_date(raw.get("paper_start_at")),
        live_start_at=_parse_date(raw.get("live_start_at")),
        wf_manifest_path=manifest_path,
        grade=raw.get("grade"),
        last_wf_run_at=_parse_date(raw.get("last_wf_run_at")),
        is_live=bool(raw.get("is_live", False)),
        infra_gaps=list(raw.get("infra_gaps") or []),
        wf_exempt_reason=raw.get("wf_exempt_reason"),
    )


@lru_cache(maxsize=1)
def _load_registry_cached(mtime: float) -> dict[str, QuantEntry]:
    """Load registry. Cache invalidates when file mtime changes."""
    if not REGISTRY_PATH.exists():
        return {}
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    entries = data.get("strategies", []) or []
    return {e["strategy_id"]: _parse_entry(e) for e in entries if "strategy_id" in e}


def load_registry() -> dict[str, QuantEntry]:
    """Load registry with mtime-based cache invalidation."""
    if not REGISTRY_PATH.exists():
        return {}
    mtime = REGISTRY_PATH.stat().st_mtime
    return _load_registry_cached(mtime)


def get_entry(strategy_id: str) -> QuantEntry | None:
    return load_registry().get(strategy_id)


def archived_rejected_ids() -> set[str]:
    """Strategies archived as REJECTED or INSUFFICIENT_TRADES post-drain."""
    if not REGISTRY_PATH.exists():
        return set()
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    return set(data.get("archived_rejected", []) or [])
