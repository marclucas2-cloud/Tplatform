"""Phase 5.2 — migrate state files to data/state/<book_id>/ convention.

Avant: dispersion data/, data/state/, data/fx/ pour fichiers state critiques.
Apres: data/state/<book_id>/<file>.json convention uniforme.

Strategy: cree des SYMLINKS (Linux/Mac) ou COPIES (Windows) sans casser
l'existant. Le code continue a lire l'ancien chemin, le nouveau chemin
est un alias auditable.

Usage:
    python scripts/migrate_state_to_book_convention.py --dry-run
    python scripts/migrate_state_to_book_convention.py --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Mapping source -> target convention
MIGRATIONS = [
    # Crypto
    ("data/crypto_dd_state.json", "data/state/binance_crypto/dd_state.json"),
    ("data/crypto_equity_state.json", "data/state/binance_crypto/equity_state.json"),
    # IBKR futures
    ("data/state/futures_positions_live.json", "data/state/ibkr_futures/positions_live.json"),
    ("data/state/futures_positions_paper.json", "data/state/ibkr_futures/positions_paper.json"),
    # Live risk (cross-book mais centralise sous global)
    ("data/live_risk_dd_state.json", "data/state/global/live_risk_dd_state.json"),
    ("data/kill_switch_state.json", "data/state/global/kill_switch_state.json"),
    # FX
    ("data/fx/carry_mom_ks_state.json", "data/state/ibkr_fx/carry_mom_ks_state.json"),
    # Misc
    ("data/state/always_on_carry.json", "data/state/global/always_on_carry.json"),
    ("data/state/xmomentum_state.json", "data/state/global/xmomentum_state.json"),
    ("data/state/paper_portfolio_state.json", "data/state/alpaca_us/paper_portfolio_state.json"),
    ("data/friday_close_price.json", "data/state/global/friday_close_price.json"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        ap.print_help()
        return 1

    print(f"=== Phase 5.2 — state migration ({'APPLY' if args.apply else 'DRY-RUN'}) ===\n")
    n_done = 0
    n_skip = 0
    for src_rel, dst_rel in MIGRATIONS:
        src = ROOT / src_rel
        dst = ROOT / dst_rel
        if not src.exists():
            print(f"  SKIP (source missing): {src_rel}")
            n_skip += 1
            continue
        if dst.exists():
            print(f"  ALREADY: {dst_rel} (skipping)")
            n_skip += 1
            continue
        if args.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Copy (safe), don't delete source for backward compatibility
            shutil.copy2(src, dst)
            print(f"  COPIED: {src_rel} -> {dst_rel}")
            n_done += 1
        else:
            print(f"  WOULD COPY: {src_rel} -> {dst_rel}")
            n_done += 1
    print(f"\n{'WOULD MIGRATE' if args.dry_run else 'MIGRATED'}: {n_done}, skipped: {n_skip}")
    print("Note: code legacy continue a lire ancien path. Future Phase 5 = "
          "refacto code pour utiliser nouveau path canonique uniquement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
