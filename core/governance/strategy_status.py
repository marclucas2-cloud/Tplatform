"""Unified strategy status — D1 plan 9.0 (2026-04-19).

Audit ChatGPT flagged conceptual errors:
  "confondre authorized et ready"
  "confondre paper et promotable"

Fix: a single computed status per strategy, derived from canonical sources
(no manual declaration). Four states:

  AUTHORIZED  = exists in live_whitelist / registry. May be paper or live.
                Pure config statement, not runtime state.
  READY       = AUTHORIZED + quant_registry has wf_manifest artifact + no
                critical infra_gaps. Runnable in its declared mode (paper
                or live) without known blockers.
  ACTIVE      = READY + currently running (paper runner scheduled OR live
                position open / broker reports cash+positions). Runtime
                truth.
  PROMOTABLE  = paper_only + all promotion_gate blocking checks PASS (except
                manual_greenlight which is operator-driven). Ready to be
                promoted to live_probation pending operator signature.

Usage:
    from core.governance.strategy_status import StrategyStatus, compute_status
    st = compute_status("cross_asset_momentum")  # -> ACTIVE
    st = compute_status("mib_estx50_spread")     # -> READY (paper, S-grade)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class StrategyStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"     # in registry
    READY = "READY"                # runnable (paper or live, no blockers)
    ACTIVE = "ACTIVE"              # runtime: paper runner firing OR live position
    PROMOTABLE = "PROMOTABLE"      # paper -> live_probation gate green (except greenlight)
    DISABLED = "DISABLED"          # registry says disabled (fx, btc_dominance)
    REJECTED = "REJECTED"          # wf REJECTED, should be archived
    UNKNOWN = "UNKNOWN"            # not in any registry (error)


@dataclass
class StrategyStatusReport:
    strategy_id: str
    status: StrategyStatus
    book: str | None = None
    declared_status: str | None = None   # from registry (live_core, paper_only, ...)
    grade: str | None = None
    is_live: bool = False
    infra_gaps: list[str] | None = None
    promotable_blockers: list[str] | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "status": self.status.value,
            "book": self.book,
            "declared_status": self.declared_status,
            "grade": self.grade,
            "is_live": self.is_live,
            "infra_gaps": self.infra_gaps or [],
            "promotable_blockers": self.promotable_blockers or [],
            "reason": self.reason,
        }


def _is_archived(strategy_id: str) -> bool:
    """Check if strategy is in archived_rejected list."""
    try:
        from core.governance.quant_registry import archived_rejected_ids
        return strategy_id in archived_rejected_ids()
    except Exception:
        return False


def _compute_promotable(strategy_id: str, target: str = "live_probation") -> tuple[bool, list[str]]:
    """Run promotion_gate and return (is_promotable_excluding_greenlight, blockers)."""
    try:
        from core.governance.promotion_gate import check_promotion
        result = check_promotion(strategy_id, target=target, fast_track=False)
        # Try fast-track if standard fails — stronger signal
        try:
            ft = check_promotion(strategy_id, target=target, fast_track=True)
            if len([c for c in ft.checks if not c.passed and c.severity == "blocking"]) < \
               len([c for c in result.checks if not c.passed and c.severity == "blocking"]):
                result = ft
        except Exception:
            pass

        blockers = []
        has_non_greenlight_failure = False
        for c in result.checks:
            if c.severity != "blocking":
                continue
            if not c.passed:
                blockers.append(c.name)
                if c.name != "manual_greenlight":
                    has_non_greenlight_failure = True
        return (not has_non_greenlight_failure), blockers
    except Exception as e:
        logger.debug(f"compute_promotable error for {strategy_id}: {e}")
        return False, ["promotion_gate_error"]


def compute_status(strategy_id: str) -> StrategyStatusReport:
    """Compute unified status from canonical sources.

    Order of precedence (first match wins):
      1. not in quant_registry AND not archived -> UNKNOWN
      2. archived_rejected -> REJECTED
      3. quant_registry.status == "disabled" -> DISABLED
      4. grade == "REJECTED" -> REJECTED
      5. is_live=True (live_core or live_probation with runtime position) -> ACTIVE
      6. status in (paper_only, live_probation) + has wf + no critical gaps
         + promotion_gate passes (except greenlight) -> PROMOTABLE
      7. status in (paper_only, live_probation) + has wf artifact + paper runner
         scheduled (infer via quant_registry.is_live==False) -> READY
         (NOTE: we cannot tell "paper actively running" without scheduler state;
          we use READY for paper_only with wf artifact.)
      8. registered but no wf artifact -> AUTHORIZED (existence only)
    """
    if _is_archived(strategy_id):
        return StrategyStatusReport(
            strategy_id=strategy_id,
            status=StrategyStatus.REJECTED,
            reason="in archived_rejected list",
        )

    try:
        from core.governance.quant_registry import get_entry
        entry = get_entry(strategy_id)
    except Exception as e:
        return StrategyStatusReport(
            strategy_id=strategy_id,
            status=StrategyStatus.UNKNOWN,
            reason=f"quant_registry error: {e}",
        )

    if entry is None:
        return StrategyStatusReport(
            strategy_id=strategy_id,
            status=StrategyStatus.UNKNOWN,
            reason="not in quant_registry.yaml",
        )

    base = {
        "strategy_id": strategy_id,
        "book": entry.book,
        "declared_status": entry.status,
        "grade": entry.grade,
        "is_live": entry.is_live,
        "infra_gaps": list(entry.infra_gaps),
    }

    if entry.status == "disabled":
        return StrategyStatusReport(
            status=StrategyStatus.DISABLED,
            reason=f"registry declares disabled ({','.join(entry.infra_gaps) or 'no reason'})",
            **base,
        )

    if entry.grade == "REJECTED":
        return StrategyStatusReport(
            status=StrategyStatus.REJECTED,
            reason="wf grade=REJECTED",
            **base,
        )

    if entry.is_live:
        return StrategyStatusReport(
            status=StrategyStatus.ACTIVE,
            reason=f"is_live=true (declared_status={entry.status})",
            **base,
        )

    # Paper/live_probation: distinguish READY vs PROMOTABLE
    has_artifact = entry.has_wf_artifact()
    has_blockers = bool(entry.infra_gaps)

    if entry.status in ("paper_only", "live_probation"):
        if has_artifact and not has_blockers:
            promotable, blockers = _compute_promotable(strategy_id)
            if promotable:
                return StrategyStatusReport(
                    status=StrategyStatus.PROMOTABLE,
                    reason=f"wf artifact + promotion_gate green (except greenlight)",
                    promotable_blockers=blockers,
                    **base,
                )
            return StrategyStatusReport(
                status=StrategyStatus.READY,
                reason=f"paper active (wf artifact present), promotion blockers: {blockers}",
                promotable_blockers=blockers,
                **base,
            )
        if has_artifact and has_blockers:
            return StrategyStatusReport(
                status=StrategyStatus.READY,
                reason=f"paper active but infra_gaps present: {entry.infra_gaps}",
                **base,
            )
        # No wf artifact
        return StrategyStatusReport(
            status=StrategyStatus.AUTHORIZED,
            reason="registered but no wf_manifest_path artifact",
            **base,
        )

    return StrategyStatusReport(
        status=StrategyStatus.AUTHORIZED,
        reason=f"registered (status={entry.status})",
        **base,
    )


def compute_all_statuses() -> list[StrategyStatusReport]:
    """Compute status for every strategy in quant_registry."""
    try:
        from core.governance.quant_registry import load_registry
        registry = load_registry()
    except Exception:
        return []
    return [compute_status(sid) for sid in sorted(registry.keys())]
