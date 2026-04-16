"""Phase 10 / WP 3.3 — Promotion committee formel.

Template + checklist pour toute promotion live.

Usage:
    python scripts/promotion_committee.py --strat <strategy_id>
    python scripts/promotion_committee.py --review-pending
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROMOTIONS_DIR = ROOT / "docs" / "audit" / "promotions"


PROMOTION_TEMPLATE = """# Promotion request — {strategy_id}

**Date** : {date}
**Submitted by** : Marc
**Target status** : {target_status}
**Current status** : {current_status}

## Doctrine projet (gates obligatoires)

- [ ] **Backtest reproductible** : path + seed + commit hash
- [ ] **Walk-forward 5 windows** : >= 3/5 OOS profitable
- [ ] **Monte Carlo 1000 sims** : P(DD>30%) < 15%
- [ ] **Stress tests** : 2018-19, 2020-21, 2022, 2023-24, 2025-26
- [ ] **Cost model + slippage** : explicite + sensibilite
- [ ] **Capacity check** : capital cible compatible avec liquidite
- [ ] **Correlation portfolio** : delta Sharpe + delta MaxDD vs baseline
- [ ] **Budget capital + drawdown** approuve dans risk_registry.yaml
- [ ] **Paper run >= 30 jours** sans divergence > 2 sigma vs backtest
- [ ] **Reconciliation OK** sur duree paper
- [ ] **kill_criteria** definis (consec_losses, sharpe_min, dd_max)

## Evidence refs

- Backtest : {backtest_ref}
- WF/MC : {wf_mc_ref}
- Paper run : {paper_ref}
- Scorecard : {scorecard_ref}

## Risques identifies

1.
2.
3.

## Decision (committee)

- **Verdict** : APPROVE / REQUEST_REVISIONS / REJECT
- **Approved by** : Marc + (Claude / PO subagent)
- **Conditions** :
- **Re-review date** :

## Apres approval

- [ ] Edit `config/live_whitelist.yaml` : status -> {target_status}
- [ ] Update `config/strategies_registry.yaml` : status -> live (si live)
- [ ] Commit + push + redeploy worker
- [ ] Monitoring 7 jours premier deploy avec sizing reduit (1/2)
"""


def create_promotion_request(strategy_id: str, target_status: str = "live_probation") -> Path:
    """Generate promotion request markdown for a strategy."""
    PROMOTIONS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = PROMOTIONS_DIR / f"{date_str}_{strategy_id}_promotion.md"

    # Try to load current status from whitelist
    current_status = "?"
    backtest_ref = "TODO"
    try:
        from core.governance.registry_loader import load_strategies_registry
        strats = load_strategies_registry()
        s = strats.get(strategy_id, {})
        current_status = s.get("status", "?")
        evidence = s.get("promotion_evidence_refs", [])
        if evidence:
            backtest_ref = evidence[0]
    except Exception:
        pass

    content = PROMOTION_TEMPLATE.format(
        strategy_id=strategy_id, date=date_str,
        target_status=target_status, current_status=current_status,
        backtest_ref=backtest_ref,
        wf_mc_ref="TODO (cf scripts/research/wf_mc_*.py)",
        paper_ref="TODO (cf logs/portfolio/*.jsonl)",
        scorecard_ref="TODO",
    )
    out.write_text(content, encoding="utf-8")
    return out


def review_pending() -> None:
    """List promotion requests pending review."""
    PROMOTIONS_DIR.mkdir(parents=True, exist_ok=True)
    pending = [p for p in PROMOTIONS_DIR.glob("*_promotion.md")]
    if not pending:
        print("No pending promotions.")
        return
    print(f"=== {len(pending)} promotion(s) pending ===\n")
    for p in sorted(pending):
        try:
            content = p.read_text(encoding="utf-8")
            verdict_line = next(
                (l for l in content.splitlines() if "**Verdict**" in l),
                "  Verdict: ?")
            print(f"  {p.name}")
            print(f"    {verdict_line.strip()}")
        except Exception as e:
            print(f"  {p.name}: read error {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strat", help="Create promotion request for this strategy_id")
    ap.add_argument("--target", default="live_probation",
                    help="Target status (default: live_probation)")
    ap.add_argument("--review-pending", action="store_true",
                    help="List pending promotion requests")
    args = ap.parse_args()

    if args.review_pending:
        review_pending()
        return 0
    if args.strat:
        out = create_promotion_request(args.strat, args.target)
        print(f"Promotion request created: {out}")
        print("Fill in evidence + risks + decision, then commit + apply.")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
