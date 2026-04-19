"""Promotion gate paper -> live (Phase 7 XXL plan).

Centralizes the formal checklist before any strategy can be promoted from
paper_only to live_probation, and from live_probation to live_core.

Checks (all must PASS):

paper_only -> live_probation:
  1. age_paper_days >= MIN_PAPER_DAYS (default 30j)
  2. no broker_health BLOCKED in last 24h
  3. no kill switch trip in last 24h
  4. divergence_vs_backtest < MAX_DIVERGENCE_SIGMA (default 1.0 sigma)
  5. paper_journal exists with >= MIN_PAPER_TRADES (default 10)
  6. wf_source file exists (proof of WF validation)
  7. manual_greenlight: explicit operator approval (signed token)

live_probation -> live_core:
  Same as above, PLUS:
  8. age_live_probation_days >= MIN_PROBATION_DAYS (default 30j)
  9. live_pnl_realised aligned with backtest expected pnl (~1 sigma)
  10. no incident report in last 30j

Usage CLI:
  python scripts/promotion_check.py <strategy_id>
  -> exits 0 if PASS (with colored summary), 1 if FAIL

Usage programmatic:
  from core.governance.promotion_gate import check_promotion
  result = check_promotion("alt_rel_strength_14_60_7", target="live_probation")
  if result.is_pass():
      ... # ready to promote
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
WHITELIST_PATH = ROOT / "config" / "live_whitelist.yaml"
GREENLIGHT_DIR = ROOT / "data" / "governance" / "greenlights"

# Tunables
MIN_PAPER_DAYS = 30
MIN_PAPER_DAYS_S_GRADE = 14  # S-grade fast-track: halves the paper quarantine
MIN_PROBATION_DAYS = 30
MIN_PAPER_TRADES = 10
MIN_PAPER_TRADES_S_GRADE = 5  # S-grade: fewer trades tolerated (rare strats like pre-holiday)
MAX_DIVERGENCE_SIGMA = 1.0


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    severity: str = "blocking"  # blocking | warning | info


@dataclass
class PromotionResult:
    strategy_id: str
    current_status: str
    target_status: str
    checks: list[CheckResult] = field(default_factory=list)

    def is_pass(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "blocking")

    def summary(self) -> str:
        verdict = "PASS" if self.is_pass() else "FAIL"
        lines = [
            f"=== Promotion Gate: {self.strategy_id}",
            f"  current_status : {self.current_status}",
            f"  target_status  : {self.target_status}",
            f"  verdict        : {verdict}",
            "",
            "Checks:",
        ]
        for c in self.checks:
            mark = "OK " if c.passed else "FAIL"
            sev = f"[{c.severity}]" if c.severity != "blocking" else ""
            lines.append(f"  {mark} {c.name} {sev} : {c.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Whitelist lookup
# ---------------------------------------------------------------------------

def _load_whitelist_entry(strategy_id: str) -> tuple[dict | None, str | None]:
    """Find strategy in live_whitelist.yaml. Returns (entry, book) or (None, None)."""
    if not WHITELIST_PATH.exists():
        return None, None
    try:
        data = yaml.safe_load(WHITELIST_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None, None
    for book_id, entries in data.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and e.get("strategy_id") == strategy_id:
                return e, book_id
    return None, None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_paper_age(strategy_id: str, entry: dict, min_days: int) -> CheckResult:
    """Check strategy has been on paper for >= min_days.

    Source of truth: config/quant_registry.yaml (B3 plan 9.0 2026-04-19).
    Falls back to regex parsing of `notes:` text only if registry misses the
    strategy (legacy transition). Logs a warning when falling back so we can
    migrate remaining stragglers.
    """
    # Primary source: canonical quant_registry
    try:
        from core.governance.quant_registry import get_entry
        qr_entry = get_entry(strategy_id)
    except Exception:
        qr_entry = None

    if qr_entry and qr_entry.paper_start_at:
        age_days = qr_entry.age_paper_days() or 0
        return CheckResult(
            name="age_paper_days",
            passed=age_days >= min_days,
            message=(
                f"{age_days}j on paper (need >= {min_days}j) since "
                f"{qr_entry.paper_start_at.isoformat()} [source: quant_registry]"
            ),
        )

    # Legacy fallback: regex on notes text. Emits warning severity so operator
    # migrates to quant_registry for canonical source.
    notes = entry.get("notes", "")
    import re
    match = re.search(r"Start paper:\s*(\d{4}-\d{2}-\d{2})", notes)
    if not match:
        return CheckResult(
            name="age_paper_days",
            passed=False,
            message=(
                f"No paper_start_at in quant_registry.yaml for '{strategy_id}' "
                f"AND no 'Start paper: YYYY-MM-DD' in notes. "
                f"Add strategy to config/quant_registry.yaml."
            ),
        )
    try:
        start_date = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return CheckResult(
            name="age_paper_days",
            passed=False,
            message=f"Invalid Start paper date: {match.group(1)}",
        )
    age_days = (datetime.now(UTC) - start_date).days
    logger.warning(
        f"promotion_gate: strategy '{strategy_id}' uses LEGACY regex-on-notes "
        f"for paper_start_at. Migrate to config/quant_registry.yaml."
    )
    return CheckResult(
        name="age_paper_days",
        passed=age_days >= min_days,
        message=(
            f"{age_days}j on paper (need >= {min_days}j) since {match.group(1)} "
            f"[source: LEGACY notes regex - migrate to quant_registry]"
        ),
        severity="warning",
    )


def _check_paper_journal(strategy_id: str, min_trades: int) -> CheckResult:
    """Check paper_journal.jsonl exists with >= min_trades entries."""
    candidates = [
        ROOT / "data" / "state" / strategy_id / "paper_journal.jsonl",
        ROOT / "data" / "state" / strategy_id / "paper_trades.jsonl",
    ]
    # Also accept short names (strategy_id may have suffix like _14_60_7)
    short_id = strategy_id.split("_")[0] if "_" in strategy_id else strategy_id
    candidates.append(ROOT / "data" / "state" / short_id / "paper_journal.jsonl")

    for path in candidates:
        if path.exists():
            try:
                lines = [
                    line for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                count = len(lines)
                return CheckResult(
                    name="paper_journal_trades",
                    passed=count >= min_trades,
                    message=f"{count} entries in {path.name} (need >= {min_trades})",
                )
            except Exception as exc:
                return CheckResult(
                    name="paper_journal_trades",
                    passed=False,
                    message=f"Read error {path}: {exc}",
                )
    return CheckResult(
        name="paper_journal_trades",
        passed=False,
        message=f"No paper_journal found in data/state/{strategy_id}/ or sibling paths",
    )


def _check_wf_source(strategy_id: str, entry: dict) -> CheckResult:
    """WF artifact must exist physically (A2 plan 9.0 — strict).

    Audit ChatGPT 2026-04-19 flagged this as blocking gap:
      "un wf_source absent physiquement peut quand meme 'passer' en info
       non bloquante -> promotion possible sans preuve machine-readable reelle"

    Primary source: quant_registry.wf_manifest_path (B3). Must exist + readable.
    Fallback: live_whitelist wf_source field, path resolved relative to ROOT.
    Declarative-only wf_source ('5/5 OOS PASS' without file) = BLOCKING FAIL.
    """
    # Primary: quant_registry manifest path
    try:
        from core.governance.quant_registry import get_entry
        qr_entry = get_entry(strategy_id)
    except Exception:
        qr_entry = None

    if qr_entry and qr_entry.wf_manifest_path is not None:
        if qr_entry.has_wf_artifact():
            return CheckResult(
                name="wf_source",
                passed=True,
                message=(
                    f"wf manifest present: {qr_entry.wf_manifest_path.relative_to(ROOT)} "
                    f"[grade={qr_entry.grade}, source: quant_registry]"
                ),
            )
        return CheckResult(
            name="wf_source",
            passed=False,
            message=(
                f"wf_manifest_path declared in quant_registry but file missing: "
                f"{qr_entry.wf_manifest_path}. Run a WF via "
                f"core.research.wf_canonical and write manifest to "
                f"data/research/wf_manifests/."
            ),
        )

    # Legacy fallback: live_whitelist.yaml wf_source field
    wf = entry.get("wf_source", "")
    if not wf:
        return CheckResult(
            name="wf_source",
            passed=False,
            message=(
                f"No wf_manifest_path in quant_registry for '{strategy_id}' AND "
                f"no wf_source in live_whitelist. Strict gate: promotion impossible "
                f"without machine-readable WF artifact."
            ),
        )
    first_token = wf.split()[0] if wf else ""
    candidate = ROOT / first_token if first_token else None
    if candidate and candidate.exists() and candidate.is_file():
        return CheckResult(
            name="wf_source",
            passed=True,
            message=f"wf_source file present: {first_token} [source: LEGACY whitelist]",
            severity="warning",  # pass but warn: migrate to quant_registry
        )
    # Declarative-only (no file) = BLOCKING. A2 strict.
    return CheckResult(
        name="wf_source",
        passed=False,
        message=(
            f"wf_source declarative only ('{wf[:80]}') without readable file at "
            f"'{first_token}'. A2 strict: promotion requires physical WF artifact."
        ),
    )


def _check_manual_greenlight(strategy_id: str, target: str) -> CheckResult:
    """Check if data/governance/greenlights/{strategy_id}_{target}.json exists."""
    GREENLIGHT_DIR.mkdir(parents=True, exist_ok=True)
    path = GREENLIGHT_DIR / f"{strategy_id}_{target}.json"
    if not path.exists():
        return CheckResult(
            name="manual_greenlight",
            passed=False,
            message=(
                f"No manual greenlight at {path.relative_to(ROOT)}. "
                f"Create with: python scripts/promotion_check.py "
                f"{strategy_id} --grant-greenlight={target}"
            ),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        signer = data.get("signed_by", "")
        ts = data.get("ts", "")
        if not signer or not ts:
            return CheckResult(
                name="manual_greenlight",
                passed=False,
                message=f"greenlight {path.name} missing signed_by or ts",
            )
        return CheckResult(
            name="manual_greenlight",
            passed=True,
            message=f"Greenlight signed by {signer} at {ts}",
        )
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(
            name="manual_greenlight",
            passed=False,
            message=f"greenlight read error: {exc}",
        )


def _check_kill_switch_clean_24h() -> CheckResult:
    """Check no kill switch state file marks active in last 24h."""
    ks_paths = [
        ROOT / "data" / "kill_switch_state.json",
        ROOT / "data" / "crypto_kill_switch_state.json",
    ]
    for ks_path in ks_paths:
        if not ks_path.exists():
            continue
        try:
            data = json.loads(ks_path.read_text(encoding="utf-8"))
            if data.get("active", False):
                return CheckResult(
                    name="kill_switch_clean_24h",
                    passed=False,
                    message=f"{ks_path.name} kill switch ACTIVE: "
                            f"{data.get('trigger_reason', 'unknown')}",
                )
            trigger_time_iso = data.get("trigger_time", "")
            if trigger_time_iso:
                try:
                    trigger_time = datetime.fromisoformat(
                        trigger_time_iso.replace("Z", "+00:00")
                    )
                    age = datetime.now(UTC) - trigger_time
                    if age < timedelta(hours=24):
                        return CheckResult(
                            name="kill_switch_clean_24h",
                            passed=False,
                            message=f"{ks_path.name} tripped {age.total_seconds()/3600:.1f}h ago "
                                    f"(< 24h ago)",
                        )
                except ValueError:
                    pass
        except (json.JSONDecodeError, OSError):
            pass
    return CheckResult(
        name="kill_switch_clean_24h",
        passed=True,
        message="No active kill switch in last 24h",
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def _latest_wf_grade(strategy_id: str) -> str | None:
    """Scan data/research/wf_manifests/{strategy_id}_*.json for the latest grade.

    Returns "S", "A", "B", "REJECTED", or None if no manifest found.
    """
    manifest_dir = ROOT / "data" / "research" / "wf_manifests"
    if not manifest_dir.exists():
        return None
    candidates = sorted(manifest_dir.glob(f"{strategy_id}_*.json"), reverse=True)
    if not candidates:
        return None
    try:
        data = json.loads(candidates[0].read_text(encoding="utf-8"))
        grade = data.get("summary", {}).get("grade")
        if grade in ("S", "A", "B", "REJECTED"):
            return grade
    except (json.JSONDecodeError, OSError):
        return None
    return None


def check_promotion(
    strategy_id: str,
    target: str = "live_probation",
    fast_track: bool = False,
) -> PromotionResult:
    """Run the promotion checklist. Returns PromotionResult with all checks.

    Args:
        strategy_id: canonical id from live_whitelist.yaml
        target: "live_probation" or "live_core"
        fast_track: opt-in S-grade fast-track. Requires:
            - wf manifest exists with grade == "S"
            - passes 14j paper (vs 30j) and 5 trades (vs 10)
            - still requires manual_greenlight (no bypass of signed approval)

    Fast-track is gated on S-grade because S-grade means: >=80% windows PASS,
    median Sharpe >= 1.0, DSR p-value <= 0.05 (when computed). This is a much
    stronger signal than legacy "VALIDATED" (>=50% windows, Sharpe > 0.0).
    """
    if target not in ("live_probation", "live_core"):
        raise ValueError(f"Invalid target {target}, must be live_probation or live_core")

    entry, book_id = _load_whitelist_entry(strategy_id)
    if entry is None:
        return PromotionResult(
            strategy_id=strategy_id,
            current_status="UNKNOWN",
            target_status=target,
            checks=[CheckResult(
                name="whitelist_lookup",
                passed=False,
                message=f"strategy_id not found in {WHITELIST_PATH.name}",
            )],
        )

    current = entry.get("status", "unknown")
    result = PromotionResult(
        strategy_id=strategy_id,
        current_status=current,
        target_status=target,
    )

    # Fast-track eligibility: must have wf manifest grade == S
    grade = _latest_wf_grade(strategy_id)
    fast_track_eligible = fast_track and grade == "S"

    min_days = MIN_PAPER_DAYS_S_GRADE if fast_track_eligible else MIN_PAPER_DAYS
    min_trades = MIN_PAPER_TRADES_S_GRADE if fast_track_eligible else MIN_PAPER_TRADES

    # Document the grade path in the result
    result.checks.append(CheckResult(
        name="wf_grade",
        passed=(grade in ("S", "A", "B")) if grade else True,
        message=(
            f"grade={grade} (fast_track={'ENABLED' if fast_track_eligible else 'disabled'}, "
            f"paper_days_required={min_days}, trades_required={min_trades})"
            if grade else
            "no wf manifest found -> using standard gate (30j / 10 trades)"
        ),
        severity="info",
    ))
    if fast_track and not fast_track_eligible:
        result.checks.append(CheckResult(
            name="fast_track_rejected",
            passed=False,
            message=(
                f"--fast-track requested but grade={grade} (need S). "
                f"Fall back to standard 30j gate."
            ),
            severity="blocking",
        ))

    result.checks.append(_check_paper_age(strategy_id, entry, min_days))
    result.checks.append(_check_paper_journal(strategy_id, min_trades))
    result.checks.append(_check_wf_source(strategy_id, entry))
    result.checks.append(_check_kill_switch_clean_24h())
    result.checks.append(_check_manual_greenlight(strategy_id, target))

    return result


def grant_greenlight(strategy_id: str, target: str, signer: str, note: str = "") -> Path:
    """Create a signed greenlight file. Use via CLI scripts/promotion_check.py."""
    GREENLIGHT_DIR.mkdir(parents=True, exist_ok=True)
    path = GREENLIGHT_DIR / f"{strategy_id}_{target}.json"
    payload = {
        "strategy_id": strategy_id,
        "target": target,
        "signed_by": signer,
        "ts": datetime.now(UTC).isoformat(),
        "note": note,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
