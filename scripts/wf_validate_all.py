"""
WF-VALIDATE-ALL -- Master validation script for all strategy asset classes.

Runs walk-forward validation for:
  1. EU strategies (10) via wf_eu_all.py
  2. FX strategies (12) via wf_fx_all.py
  3. Crypto strategies (8) via wf_crypto_all.py

Then computes:
  - Cross-asset-class correlation matrix
  - Final approved strategy list
  - Portfolio-level diversification metrics

Outputs: output/validated_strategies.json

Usage:
  python scripts/wf_validate_all.py                      # Run all
  python scripts/wf_validate_all.py --asset-class eu      # Only EU
  python scripts/wf_validate_all.py --skip-mc             # Skip Monte Carlo
  python scripts/wf_validate_all.py --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


# =====================================================================
# Asset class runners
# =====================================================================


def _run_eu(
    verbose: bool = False,
    data_dir: Path | None = None,
    run_mc: bool = True,
) -> Dict[str, Any]:
    """Run EU walk-forward validation.

    Returns:
        Dict of strategy_name -> result dict with at least 'verdict' key.
    """
    try:
        from scripts.wf_eu_all import run_all as run_eu_all
        results = run_eu_all(verbose=verbose, data_dir=data_dir, run_mc=run_mc)
        return {
            name: {
                "verdict": r.verdict,
                "asset_class": "eu",
                "avg_oos_sharpe": r.avg_oos_sharpe,
                "profitable_ratio": r.profitable_ratio,
                "total_oos_trades": r.total_oos_trades,
                "commission_burn_rate": r.commission_burn_rate,
                "avg_oos_max_dd": r.avg_oos_max_dd,
                "oos_returns": [w.oos_return for w in r.windows],
            }
            for name, r in results.items()
        }
    except Exception as e:
        logger.error("EU validation failed: %s", e)
        print(f"  EU validation ERROR: {e}")
        return {}


def _run_fx(
    verbose: bool = False,
    data_dir: Path | None = None,
) -> Dict[str, Any]:
    """Run FX walk-forward validation.

    Returns:
        Dict of strategy_name -> result dict.
    """
    try:
        from scripts.wf_fx_all import run_all as run_fx_all
        results = run_fx_all(verbose=verbose, data_dir=data_dir)
        return {
            name: {
                "verdict": r.verdict,
                "asset_class": "fx",
                "avg_oos_sharpe": r.avg_oos_sharpe,
                "profitable_ratio": r.profitable_ratio,
                "total_oos_trades": r.total_oos_trades,
                "commission_burn_rate": 0.0,  # Not tracked in FX WF
                "avg_oos_max_dd": 0.0,
                "oos_returns": [w.oos_return for w in r.windows],
            }
            for name, r in results.items()
        }
    except Exception as e:
        logger.error("FX validation failed: %s", e)
        print(f"  FX validation ERROR: {e}")
        return {}


def _run_crypto(
    verbose: bool = False,
    data_dir: Path | None = None,
) -> Dict[str, Any]:
    """Run crypto walk-forward validation.

    Returns:
        Dict of strategy_name -> result dict.
    """
    try:
        from scripts.wf_crypto_all import run_all as run_crypto_all
        results = run_crypto_all(verbose=verbose, data_dir=data_dir)
        return {
            name: {
                "verdict": r.verdict,
                "asset_class": "crypto",
                "avg_oos_sharpe": r.avg_oos_sharpe,
                "profitable_ratio": r.profitable_ratio,
                "total_oos_trades": r.total_oos_trades,
                "commission_burn_rate": 0.0,
                "avg_oos_max_dd": 0.0,
                "oos_returns": [w.oos_return for w in r.windows],
            }
            for name, r in results.items()
        }
    except Exception as e:
        logger.error("Crypto validation failed: %s", e)
        print(f"  Crypto validation ERROR: {e}")
        return {}


# =====================================================================
# Cross-strategy correlation matrix
# =====================================================================


def compute_cross_correlation(
    all_results: Dict[str, Dict[str, Any]],
    correlation_threshold: float = 0.60,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Compute cross-asset-class correlation matrix.

    Uses OOS return series from each strategy. Flags pairs exceeding
    the correlation threshold.

    Args:
        all_results: Merged results from all asset classes.
        correlation_threshold: Max acceptable correlation.
        verbose: Print details.

    Returns:
        Dict with matrix, flagged_pairs, and diversification metrics.
    """
    # Filter to surviving strategies with return data
    surviving = {
        name: info for name, info in all_results.items()
        if info.get("verdict") in ("VALIDATED", "BORDERLINE")
        and len(info.get("oos_returns", [])) >= 3
    }

    if len(surviving) < 2:
        return {
            "matrix": {},
            "flagged_pairs": [],
            "n_surviving": len(surviving),
            "avg_pairwise_correlation": 0.0,
            "diversification_ratio": 1.0,
        }

    names = sorted(surviving.keys())
    n = len(names)

    # Build return vectors (pad to same length with zeros)
    max_len = max(len(surviving[name]["oos_returns"]) for name in names)
    return_matrix = np.zeros((n, max_len))
    for i, name in enumerate(names):
        rets = surviving[name]["oos_returns"]
        return_matrix[i, :len(rets)] = rets

    # Correlation matrix
    corr_matrix = {}
    flagged_pairs = []
    all_corrs = []

    for i in range(n):
        corr_matrix[names[i]] = {}
        for j in range(n):
            if i == j:
                corr_matrix[names[i]][names[j]] = 1.0
                continue

            a = return_matrix[i]
            b = return_matrix[j]

            # Only correlate non-zero overlapping region
            mask = (a != 0) & (b != 0)
            if mask.sum() < 3:
                corr = 0.0
            else:
                corr_val = np.corrcoef(a[mask], b[mask])[0, 1]
                corr = 0.0 if np.isnan(corr_val) else float(corr_val)

            corr_matrix[names[i]][names[j]] = round(corr, 3)

            if i < j:
                all_corrs.append(corr)
                if abs(corr) > correlation_threshold:
                    flagged_pairs.append({
                        "strategy_a": names[i],
                        "strategy_b": names[j],
                        "asset_class_a": surviving[names[i]]["asset_class"],
                        "asset_class_b": surviving[names[j]]["asset_class"],
                        "correlation": round(corr, 3),
                    })

    # Average pairwise correlation
    avg_corr = float(np.mean(all_corrs)) if all_corrs else 0.0

    # Diversification ratio: 1 / (1 + avg_corr) -- higher is better
    div_ratio = 1.0 / (1.0 + max(avg_corr, 0.0))

    if verbose and flagged_pairs:
        print()
        print(f"  CROSS-STRATEGY HIGH CORRELATION PAIRS (> {correlation_threshold:.0%}):")
        for fp in flagged_pairs:
            print(
                f"    {fp['strategy_a']} ({fp['asset_class_a']}) <-> "
                f"{fp['strategy_b']} ({fp['asset_class_b']}): "
                f"corr={fp['correlation']:.3f}"
            )

    return {
        "matrix": corr_matrix,
        "flagged_pairs": flagged_pairs,
        "n_surviving": len(surviving),
        "avg_pairwise_correlation": round(avg_corr, 3),
        "diversification_ratio": round(div_ratio, 3),
    }


# =====================================================================
# Build final approved strategy list
# =====================================================================


def build_approved_list(
    all_results: Dict[str, Dict[str, Any]],
    corr_info: Dict[str, Any],
    max_correlated_pairs: int = 3,
) -> List[Dict[str, Any]]:
    """Build the final list of approved strategies.

    Includes VALIDATED strategies, plus BORDERLINE if needed.
    Removes one strategy from each highly correlated pair (keeps higher Sharpe).

    Args:
        all_results: Merged results from all asset classes.
        corr_info: Correlation analysis results.
        max_correlated_pairs: Max flagged pairs before forced removal.

    Returns:
        List of approved strategy dicts.
    """
    # Start with VALIDATED strategies
    approved = {
        name: info for name, info in all_results.items()
        if info.get("verdict") == "VALIDATED"
    }

    # Handle high-correlation pairs: drop the weaker strategy
    removed = set()
    for fp in corr_info.get("flagged_pairs", []):
        a = fp["strategy_a"]
        b = fp["strategy_b"]
        if a in removed or b in removed:
            continue

        sharpe_a = all_results.get(a, {}).get("avg_oos_sharpe", 0)
        sharpe_b = all_results.get(b, {}).get("avg_oos_sharpe", 0)

        # Remove the weaker one
        if sharpe_a >= sharpe_b:
            if b in approved:
                removed.add(b)
                logger.info(
                    "Removed %s (Sharpe=%.2f) due to high correlation with %s (Sharpe=%.2f)",
                    b, sharpe_b, a, sharpe_a,
                )
        else:
            if a in approved:
                removed.add(a)
                logger.info(
                    "Removed %s (Sharpe=%.2f) due to high correlation with %s (Sharpe=%.2f)",
                    a, sharpe_a, b, sharpe_b,
                )

    for name in removed:
        approved.pop(name, None)

    # Build output list
    approved_list = []
    for name, info in sorted(approved.items()):
        approved_list.append({
            "strategy_name": name,
            "asset_class": info.get("asset_class", "unknown"),
            "verdict": info["verdict"],
            "avg_oos_sharpe": info.get("avg_oos_sharpe", 0.0),
            "profitable_ratio": info.get("profitable_ratio", 0.0),
            "total_oos_trades": info.get("total_oos_trades", 0),
        })

    return approved_list


# =====================================================================
# Main orchestrator
# =====================================================================


def run_all(
    asset_classes: List[str] | None = None,
    verbose: bool = False,
    run_mc: bool = True,
) -> Dict[str, Any]:
    """Run walk-forward validation across all asset classes.

    Args:
        asset_classes: List of asset classes to validate.
            Options: ["eu", "fx", "crypto"]. Default: all three.
        verbose: Print detailed output.
        run_mc: Whether to run Monte Carlo simulation (EU only).

    Returns:
        Dict with all results, correlation info, and approved list.
    """
    if asset_classes is None:
        asset_classes = ["eu", "fx", "crypto"]

    all_results: Dict[str, Dict[str, Any]] = {}
    asset_class_counts = {}

    print()
    print("#" * 78)
    print("  MASTER WALK-FORWARD VALIDATION -- ALL ASSET CLASSES")
    print("#" * 78)
    print()

    # --- EU ---
    if "eu" in asset_classes:
        print("=" * 78)
        print("  [1/3] EU EQUITY STRATEGIES")
        print("=" * 78)
        eu_results = _run_eu(verbose=verbose, run_mc=run_mc)
        all_results.update(eu_results)
        asset_class_counts["eu"] = len(eu_results)
        print()

    # --- FX ---
    if "fx" in asset_classes:
        print("=" * 78)
        print("  [2/3] FX STRATEGIES")
        print("=" * 78)
        fx_results = _run_fx(verbose=verbose)
        all_results.update(fx_results)
        asset_class_counts["fx"] = len(fx_results)
        print()

    # --- Crypto ---
    if "crypto" in asset_classes:
        print("=" * 78)
        print("  [3/3] CRYPTO STRATEGIES")
        print("=" * 78)
        crypto_results = _run_crypto(verbose=verbose)
        all_results.update(crypto_results)
        asset_class_counts["crypto"] = len(crypto_results)
        print()

    # --- Cross-strategy correlation ---
    print("=" * 78)
    print("  CROSS-STRATEGY CORRELATION ANALYSIS")
    print("=" * 78)

    corr_info = compute_cross_correlation(
        all_results, correlation_threshold=0.60, verbose=verbose
    )

    print(f"\n  Surviving strategies: {corr_info['n_surviving']}")
    print(f"  Avg pairwise correlation: {corr_info['avg_pairwise_correlation']:.3f}")
    print(f"  Diversification ratio: {corr_info['diversification_ratio']:.3f}")
    if corr_info["flagged_pairs"]:
        print(f"  High-correlation pairs: {len(corr_info['flagged_pairs'])}")
    else:
        print("  No high-correlation pairs detected.")

    # --- Build approved list ---
    approved = build_approved_list(all_results, corr_info)

    # --- Print final summary ---
    _print_final_summary(all_results, approved, asset_class_counts, corr_info)

    # --- Save results ---
    output_dir = ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    _save_results(all_results, approved, corr_info, output_dir)

    return {
        "all_results": all_results,
        "correlation": corr_info,
        "approved": approved,
    }


def _print_final_summary(
    all_results: Dict[str, Dict[str, Any]],
    approved: List[Dict[str, Any]],
    asset_class_counts: Dict[str, int],
    corr_info: Dict[str, Any],
) -> None:
    """Print the final cross-asset validation summary."""

    # Count by verdict
    verdicts = {}
    for info in all_results.values():
        v = info.get("verdict", "UNKNOWN")
        verdicts[v] = verdicts.get(v, 0) + 1

    # Count approved by asset class
    approved_by_class = {}
    for a in approved:
        ac = a["asset_class"]
        approved_by_class[ac] = approved_by_class.get(ac, 0) + 1

    print()
    print("#" * 78)
    print("  FINAL VALIDATION SUMMARY")
    print("#" * 78)
    print()

    # Per-asset-class breakdown
    print("  Per asset class:")
    for ac, total in sorted(asset_class_counts.items()):
        n_approved = approved_by_class.get(ac, 0)
        ac_validated = sum(
            1 for info in all_results.values()
            if info.get("asset_class") == ac and info.get("verdict") == "VALIDATED"
        )
        ac_rejected = sum(
            1 for info in all_results.values()
            if info.get("asset_class") == ac and info.get("verdict") == "REJECTED"
        )
        print(f"    {ac.upper():>8}: {total} total, {ac_validated} validated, "
              f"{ac_rejected} rejected, {n_approved} approved")

    print()
    print(f"  Total strategies evaluated: {len(all_results)}")
    print(f"  VALIDATED: {verdicts.get('VALIDATED', 0)}")
    print(f"  BORDERLINE: {verdicts.get('BORDERLINE', 0)}")
    print(f"  REJECTED: {verdicts.get('REJECTED', 0)}")
    print(f"  SKIPPED/NO_DATA: "
          f"{verdicts.get('SKIPPED', 0) + verdicts.get('NO_DATA', 0)}")
    print()
    print(f"  Final approved strategies: {len(approved)}")
    print(f"  Avg pairwise correlation: {corr_info['avg_pairwise_correlation']:.3f}")
    print(f"  Diversification ratio: {corr_info['diversification_ratio']:.3f}")

    if approved:
        print()
        print("  APPROVED STRATEGY LIST:")
        print("  " + "-" * 68)
        print(f"  {'Name':<30} {'Class':<8} {'Sharpe':>8} {'Win%':>6} {'Trades':>7}")
        print("  " + "-" * 68)
        for a in approved:
            print(
                f"  {a['strategy_name']:<30} {a['asset_class']:<8} "
                f"{a['avg_oos_sharpe']:>8.2f} {a['profitable_ratio']:>5.0%} "
                f"{a['total_oos_trades']:>7}"
            )
        print("  " + "-" * 68)

    print()
    print("#" * 78)


def _save_results(
    all_results: Dict[str, Dict[str, Any]],
    approved: List[Dict[str, Any]],
    corr_info: Dict[str, Any],
    output_dir: Path,
) -> None:
    """Save final validated strategies and full results.

    Outputs:
      - output/validated_strategies.json (approved list + metadata)
      - output/wf_all_results.json (full results for all strategies)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Approved strategies file
    validated_output = {
        "run_timestamp": datetime.now(UTC).isoformat(),
        "n_total_evaluated": len(all_results),
        "n_approved": len(approved),
        "approved_strategies": approved,
        "correlation": {
            "n_surviving": corr_info.get("n_surviving", 0),
            "avg_pairwise_correlation": corr_info.get("avg_pairwise_correlation", 0.0),
            "diversification_ratio": corr_info.get("diversification_ratio", 0.0),
            "flagged_pairs": corr_info.get("flagged_pairs", []),
        },
        "by_asset_class": _group_by_asset_class(approved),
    }

    validated_path = output_dir / "validated_strategies.json"
    with open(validated_path, "w") as f:
        json.dump(validated_output, f, indent=2, ensure_ascii=False)

    # Full results file (sanitize: remove numpy arrays etc.)
    sanitized = {}
    for name, info in all_results.items():
        sanitized[name] = {
            k: v for k, v in info.items()
            if k != "oos_returns"  # Large array, saved in per-asset files
        }

    full_path = output_dir / "wf_all_results.json"
    with open(full_path, "w") as f:
        json.dump(sanitized, f, indent=2, ensure_ascii=False)

    print("\nFinal results saved to:")
    print(f"  {validated_path}")
    print(f"  {full_path}")


def _group_by_asset_class(
    approved: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Group approved strategies by asset class."""
    groups: Dict[str, List[str]] = {}
    for a in approved:
        ac = a["asset_class"]
        if ac not in groups:
            groups[ac] = []
        groups[ac].append(a["strategy_name"])
    return groups


# =====================================================================
# CLI
# =====================================================================


def parse_args(argv: list | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Master walk-forward validation for all asset classes",
    )
    parser.add_argument(
        "--asset-class",
        type=str,
        nargs="+",
        choices=["eu", "fx", "crypto"],
        default=None,
        help="Run only these asset classes (default: all)",
    )
    parser.add_argument(
        "--skip-mc",
        action="store_true",
        help="Skip Monte Carlo simulation (faster)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed per-window results",
    )
    return parser.parse_args(argv)


def main(argv: list | None = None) -> Dict[str, Any]:
    """Entry point for CLI and test usage."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    return run_all(
        asset_classes=args.asset_class,
        verbose=args.verbose,
        run_mc=not args.skip_mc,
    )


if __name__ == "__main__":
    main()
