"""Backfill wf_manifests/*.json from existing Markdown WF reports.

Purpose: wf_canonical.py v2 introduced S/A/B grade classification, but historical
WF results live in docs/research/wf_reports/*.md. This script writes a minimal
manifest per strategy so promotion_gate can consult the grade via
_latest_wf_grade().

Output: data/research/wf_manifests/{strategy_id}_2026-04-19_backfill.json

Manifests created are marked with `source: "md_backfill"` to distinguish from
authoritative wf_canonical runs.

Usage: python scripts/generate_wf_manifests_from_reports.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.research.wf_canonical import WF_SCHEMA_VERSION, classify_grade  # noqa: E402

OUT_DIR = ROOT / "data" / "research" / "wf_manifests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Compiled from docs/research/wf_reports/*.md + live_whitelist.yaml notes.
# n_trials: how many variants/strategies were searched for this class of strat
# (from report tables). Higher n_trials -> DSR more stringent.
CANDIDATES = [
    # strategy_id, wf_pass, wf_total, median_sharpe_oos, n_bars_oos, n_trials, source
    ("cross_asset_momentum",          4, 5, 0.87, 1260, 1,
     "docs/research/wf_reports/INT-C (5Y IBKR portfolio)"),
    ("gold_oil_rotation",             5, 5, 6.44, 1260, 1,
     "scripts/wf_gold_oil_rotation.py (Sharpe solo high, portfolio 0.87)"),
    ("mes_pre_holiday_long",          5, 5, 0.57, 1135, 7,
     "INT-A_tier1_validation.md T1-A (pre_holiday_drift)"),
    ("mes_monday_long_oc",            3, 5, 0.40, 1135, 7,
     "INT-A_tier1_validation.md T1-A (long_mon_oc)"),
    ("mes_wednesday_long_oc",         4, 5, 0.26, 1135, 7,
     "INT-A_tier1_validation.md T1-A (long_wed_oc)"),
    ("mcl_overnight_mon_trend10",     4, 5, 0.80, 1416, 3,
     "T3A-01_mcl_overnight.md (2015-2026)"),
    ("btc_asia_mes_leadlag_q70_v80",  4, 5, 1.07, 245, 3,
     "T3A-02_mes_btc_asia_leadlag.md (489 days, 97 active)"),
    ("eu_relmom_40_3",                4, 5, 0.71, 673, 2,
     "T3A-03_eu_indices_relmom.md (1346 days)"),
    ("us_sector_ls_40_5",             3, 5, 0.39, 637, 2,
     "T3B-01_us_sector_ls.md (1274 days)"),
    ("alt_rel_strength_14_60_7",      3, 5, 1.11, 409, 5,
     "T4A-02_crypto_relative_strength.md (797 active days, 818 total)"),
    ("mib_estx50_spread",             4, 5, 3.91, 360, 2,
     "reports/research/wf_mib_estx50_corrected.json (24mo OOS, post-fix)"),
]


def main() -> int:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    written = 0
    report_rows = []
    for sid, wf_pass, wf_total, sharpe, n_bars, n_trials, src in CANDIDATES:
        from core.research.wf_canonical import compute_deflated_sharpe_pvalue
        dsr_pvalue = compute_deflated_sharpe_pvalue(
            sharpe=sharpe, n_observations=n_bars, n_trials=n_trials
        )
        pass_rate = wf_pass / wf_total
        grade = classify_grade(pass_rate, sharpe, dsr_pvalue)

        manifest = {
            "schema_version": WF_SCHEMA_VERSION,
            "run_id": f"backfill_{sid}_{date_str}",
            "strategy_id": sid,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "source": "md_backfill",
            "source_report": src,
            "params": {
                "n_windows": wf_total,
                "n_bars_oos": n_bars,
                "n_trials": n_trials,
            },
            "summary": {
                "windows_pass": wf_pass,
                "windows_total": wf_total,
                "pass_rate": round(pass_rate, 4),
                "median_sharpe": sharpe,
                "dsr_pvalue": round(dsr_pvalue, 4),
                "grade": grade,
                "verdict": "VALIDATED" if grade in ("S", "A", "B") else "REJECTED",
            },
        }
        path = OUT_DIR / f"{sid}_{date_str}_backfill.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        written += 1
        report_rows.append(
            (sid, grade, f"{wf_pass}/{wf_total}", f"{sharpe:+.2f}",
             f"{dsr_pvalue:.3f}", n_trials, n_bars)
        )

    print(f"Wrote {written} manifests to {OUT_DIR}")
    print()
    print(f"{'strategy_id':<38} {'grade':<8} {'WF':<6} {'sharpe':<8} {'dsr_p':<7} {'trials':<7} {'n_obs':<6}")
    print("-" * 90)
    for row in report_rows:
        print(f"{row[0]:<38} {row[1]:<8} {row[2]:<6} {row[3]:<8} {row[4]:<7} {str(row[5]):<7} {str(row[6]):<6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
