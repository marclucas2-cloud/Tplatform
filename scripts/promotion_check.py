#!/usr/bin/env python3
"""Promotion gate CLI — Phase 7 XXL plan.

Usage:
  python scripts/promotion_check.py <strategy_id> [--target=live_probation|live_core]
  python scripts/promotion_check.py <strategy_id> --grant-greenlight=<target>
                                                  --signer=<name>
                                                  [--note=<text>]

Examples:
  # Check if alt_rel_strength_14_60_7 is ready for live_probation
  python scripts/promotion_check.py alt_rel_strength_14_60_7

  # Grant greenlight (manual operator step before promotion)
  python scripts/promotion_check.py alt_rel_strength_14_60_7 \\
      --grant-greenlight=live_probation --signer=marc \\
      --note="Reviewed paper journal 30j, divergence < 1 sigma"

Exit codes:
  0 = all blocking checks PASS
  1 = at least one blocking check FAIL
  2 = invalid arguments
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.governance.promotion_gate import (  # noqa: E402
    check_promotion,
    grant_greenlight,
)


def main():
    parser = argparse.ArgumentParser(description="Promotion gate CLI")
    parser.add_argument("strategy_id", help="Canonical strategy_id from live_whitelist.yaml")
    parser.add_argument(
        "--target", default="live_probation",
        choices=("live_probation", "live_core"),
        help="Promotion target status",
    )
    parser.add_argument(
        "--grant-greenlight",
        choices=("live_probation", "live_core"),
        help="Create signed greenlight file (operator manual step)",
    )
    parser.add_argument("--signer", help="Signer name (required with --grant-greenlight)")
    parser.add_argument("--note", default="", help="Optional note for greenlight")
    args = parser.parse_args()

    if args.grant_greenlight:
        if not args.signer:
            print("ERROR: --signer required with --grant-greenlight", file=sys.stderr)
            return 2
        path = grant_greenlight(
            strategy_id=args.strategy_id,
            target=args.grant_greenlight,
            signer=args.signer,
            note=args.note,
        )
        print(f"Greenlight created: {path}")
        return 0

    result = check_promotion(args.strategy_id, target=args.target)
    print(result.summary())
    return 0 if result.is_pass() else 1


if __name__ == "__main__":
    sys.exit(main())
