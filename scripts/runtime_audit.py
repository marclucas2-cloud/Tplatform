"""Runtime audit — vraie verite vs narrative.

F1 plan 9.0 (2026-04-19). Rapport CLI qui lit TOUTES les sources canoniques
(quant_registry, books_registry, live_whitelist, wf_manifests, reconciliation,
equity_state, promotion_gate) et affiche l'etat REEL de la plateforme, pas
ce que les notes disent.

A lancer:
  - Apres chaque deploy pour valider que tout s'aligne
  - Chaque semaine pour spotter narrative drift
  - Avant toute promotion live pour confirmer que l'infra est prete

Usage:
  python scripts/runtime_audit.py             # Rapport humain lisible
  python scripts/runtime_audit.py --json      # JSON pour parsing CI
  python scripts/runtime_audit.py --strict    # Exit non-zero si incoherences
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from core.governance.quant_registry import (  # noqa: E402
    archived_rejected_ids,
    load_registry,
)
from core.governance.strategy_status import (  # noqa: E402
    StrategyStatus,
    compute_all_statuses,
)
from core.runtime.preflight import boot_preflight  # noqa: E402


def _color(s: str, code: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def _status_color(status: StrategyStatus) -> str:
    return {
        StrategyStatus.ACTIVE: "32",       # green
        StrategyStatus.PROMOTABLE: "36",   # cyan
        StrategyStatus.READY: "34",        # blue
        StrategyStatus.AUTHORIZED: "33",   # yellow
        StrategyStatus.DISABLED: "90",     # grey
        StrategyStatus.REJECTED: "31",     # red
        StrategyStatus.UNKNOWN: "91",      # bright red
    }.get(status, "0")


def build_report() -> dict:
    """Build structured runtime report."""
    ts = datetime.now(UTC).isoformat()

    # Preflight (advisory mode)
    preflight = boot_preflight(fail_closed=False)

    # Strategies
    reports = compute_all_statuses()
    by_status: dict[str, list[dict]] = {}
    for r in reports:
        by_status.setdefault(r.status.value, []).append(r.to_dict())

    # Load raw registry for extra fields
    registry = load_registry()
    archived = sorted(archived_rejected_ids())

    # Coherence checks
    incoherences = []

    # Incoherence 1: strategy with is_live=True but grade=REJECTED
    for sid, entry in registry.items():
        if entry.is_live and entry.grade == "REJECTED":
            incoherences.append({
                "type": "LIVE_WITH_REJECTED_GRADE",
                "strategy_id": sid,
                "message": "is_live=true but grade=REJECTED - should be demoted immediately",
            })

    # Incoherence 2: strategy in archived_rejected but still in registry as active
    for sid in archived:
        if sid in registry and registry[sid].status not in ("disabled",):
            incoherences.append({
                "type": "ARCHIVED_BUT_ACTIVE",
                "strategy_id": sid,
                "message": f"in archived_rejected but registry status={registry[sid].status}",
            })

    # Incoherence 3: paper strategy without wf_manifest_path.
    # G2 iter1 (2026-04-19): wf_exempt_reason legitimise l'absence (meta-portfolio,
    # WF recalibration in progress, etc.) - skip l'incoherence si flag present.
    for sid, entry in registry.items():
        if entry.status not in ("paper_only", "live_probation"):
            continue
        if entry.wf_manifest_path is not None:
            continue
        if entry.wf_exempt_reason:
            # Legitimate exemption, not an incoherence
            continue
        incoherences.append({
            "type": "PAPER_WITHOUT_WF",
            "strategy_id": sid,
            "severity": "warning",
            "message": "paper strategy has null wf_manifest_path - promotion will fail wf_source check",
        })

    # Incoherence 4: strategy with infra_gaps but no backlog ticket (best-effort check)
    strats_with_gaps = [
        {"strategy_id": sid, "gaps": list(entry.infra_gaps)}
        for sid, entry in registry.items()
        if entry.infra_gaps
    ]

    return {
        "generated_at": ts,
        "preflight": {
            "all_passed": preflight.all_passed,
            "critical_failures": len(preflight.critical_failures),
            "checks": [
                {"name": c.name, "passed": c.passed, "severity": c.severity,
                 "message": c.message}
                for c in preflight.checks
            ],
        },
        "strategies": {
            "total": len(reports),
            "by_status": {s: len(lst) for s, lst in by_status.items()},
            "details": [r.to_dict() for r in reports],
        },
        "archived_rejected": archived,
        "incoherences": incoherences,
        "infra_gaps_summary": strats_with_gaps,
    }


def print_human_report(report: dict) -> None:
    print("=" * 72)
    print(f"  RUNTIME AUDIT  —  {report['generated_at']}")
    print("=" * 72)

    # Preflight
    pre = report["preflight"]
    pre_status = _color("OK ", "32") if pre["all_passed"] else _color("FAIL", "31")
    print(f"\nBoot preflight: {pre_status} ({pre['critical_failures']} critical failures)")
    for c in pre["checks"]:
        mark = _color("OK ", "32") if c["passed"] else _color("FAIL", "31")
        sev = f"[{c['severity']}]".ljust(10)
        print(f"  {mark} {sev} {c['name']}: {c['message']}")

    # Strategies by status
    strats = report["strategies"]
    print(f"\nStrategies: {strats['total']} total")
    order = ["ACTIVE", "PROMOTABLE", "READY", "AUTHORIZED", "DISABLED", "REJECTED", "UNKNOWN"]
    for status in order:
        count = strats["by_status"].get(status, 0)
        if count:
            col = _status_color(StrategyStatus(status))
            print(f"  {_color(status.ljust(12), col)} {count}")

    # Details per strategy
    print("\nStrategy details:")
    for r in strats["details"]:
        col = _status_color(StrategyStatus(r["status"]))
        status_str = _color(r["status"].ljust(12), col)
        grade = r.get("grade") or "—"
        book = (r.get("book") or "—").ljust(16)
        print(f"  {status_str} {r['strategy_id'].ljust(38)} {book} grade={grade}")
        if r.get("infra_gaps"):
            print(f"    gaps: {', '.join(r['infra_gaps'])}")

    # Incoherences
    inc = report["incoherences"]
    if inc:
        print(f"\n{_color('INCOHERENCES DETECTEES:', '31')} ({len(inc)})")
        for i in inc:
            sev = i.get("severity", "critical")
            col = "31" if sev == "critical" else "33"
            print(f"  {_color('[' + sev.upper() + ']', col)} {i['type']}: {i['strategy_id']}")
            print(f"    {i['message']}")
    else:
        print(f"\n{_color('No registry/runtime incoherences detected.', '32')}")

    # Archived
    if report["archived_rejected"]:
        print(f"\nArchived (REJECTED) strategies: {len(report['archived_rejected'])}")
        for sid in report["archived_rejected"]:
            print(f"  - {sid}")

    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime audit — real state vs narrative")
    parser.add_argument("--json", action="store_true", help="Output JSON (machine-readable)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero if any incoherence OR preflight critical failure")
    args = parser.parse_args()

    report = build_report()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_human_report(report)

    if args.strict:
        if report["incoherences"] or not report["preflight"]["all_passed"]:
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
