"""Phase 5.5 — Automated state backup with rotation.

Backs up all critical state files to data/backups/<date>/.
Keeps last 7 daily backups + latest snapshot.

Usage:
    python scripts/backup_state.py              # Run backup now
    python scripts/backup_state.py --list       # List existing backups
    python scripts/backup_state.py --restore <date>  # Restore from backup
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "data" / "backups"
STATE_DIR = ROOT / "data" / "state"
MAX_BACKUPS = 7

CRITICAL_STATE_FILES = [
    "data/state/futures_positions_live.json",
    "data/state/futures_positions_paper.json",
    "data/state/paper_portfolio_state.json",
    "data/state/paper_momentum_state.json",
    "data/state/paper_pairs_state.json",
    "data/state/paper_trading_state.json",
    "data/state/paper_vrp_state.json",
    "data/crypto_kill_switch_state.json",
    "data/engine_state.json",
    "data/risk/monte_carlo_report.json",
    "data/risk/unified_portfolio.json",
    "data/risk/last_known_broker_state.json",
    "data/orchestrator/state.json",
    "config/live_whitelist.yaml",
    "config/books_registry.yaml",
    "config/strategies_registry.yaml",
    "config/risk_registry.yaml",
    "config/health_registry.yaml",
    "config/allocation.yaml",
    "config/crypto_allocation.yaml",
    "config/limits_live.yaml",
    "config/crypto_limits.yaml",
]

CRITICAL_STATE_DIRS = [
    "data/state/kill_switches",
    "data/reconciliation",
]


def run_backup() -> dict:
    """Create a dated backup of all critical state files."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = BACKUP_DIR / date_str / ts
    backup_path.mkdir(parents=True, exist_ok=True)

    copied = 0
    missing = 0
    errors = []

    for rel_path in CRITICAL_STATE_FILES:
        src = ROOT / rel_path
        if not src.exists():
            missing += 1
            continue
        dst = backup_path / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(dst))
            copied += 1
        except Exception as e:
            errors.append(f"{rel_path}: {e}")

    for rel_dir in CRITICAL_STATE_DIRS:
        src_dir = ROOT / rel_dir
        if not src_dir.exists():
            continue
        dst_dir = backup_path / rel_dir
        try:
            shutil.copytree(str(src_dir), str(dst_dir), dirs_exist_ok=True)
            copied += sum(1 for _ in Path(dst_dir).rglob("*") if _.is_file())
        except Exception as e:
            errors.append(f"{rel_dir}/: {e}")

    manifest = {
        "ts": ts,
        "date": date_str,
        "copied": copied,
        "missing": missing,
        "errors": errors,
        "files": [str(p.relative_to(backup_path)) for p in backup_path.rglob("*") if p.is_file()],
    }
    (backup_path / "_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    _rotate_backups()

    return manifest


def _rotate_backups() -> int:
    """Keep only the last MAX_BACKUPS date directories."""
    if not BACKUP_DIR.exists():
        return 0
    date_dirs = sorted(
        [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    removed = 0
    for old_dir in date_dirs[MAX_BACKUPS:]:
        try:
            shutil.rmtree(str(old_dir))
            removed += 1
        except Exception:
            pass
    return removed


def list_backups() -> list[dict]:
    """List all available backups."""
    if not BACKUP_DIR.exists():
        return []
    results = []
    for date_dir in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for snap_dir in sorted(date_dir.iterdir(), reverse=True):
            manifest_file = snap_dir / "_manifest.json"
            if manifest_file.exists():
                try:
                    m = json.loads(manifest_file.read_text(encoding="utf-8"))
                    m["path"] = str(snap_dir)
                    results.append(m)
                except Exception:
                    results.append({"path": str(snap_dir), "error": "bad manifest"})
            else:
                file_count = sum(1 for _ in snap_dir.rglob("*") if _.is_file())
                results.append({"path": str(snap_dir), "files_count": file_count})
    return results


def restore_backup(date_str: str) -> dict:
    """Restore state files from a backup date (latest snapshot of that date)."""
    date_dir = BACKUP_DIR / date_str
    if not date_dir.exists():
        return {"error": f"No backup for date {date_str}"}

    snapshots = sorted(date_dir.iterdir(), reverse=True)
    if not snapshots:
        return {"error": f"Empty backup dir for {date_str}"}

    snap = snapshots[0]
    restored = 0
    errors = []

    for src_file in snap.rglob("*"):
        if not src_file.is_file() or src_file.name == "_manifest.json":
            continue
        rel = src_file.relative_to(snap)
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src_file), str(dst))
            restored += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")

    return {
        "snapshot": str(snap),
        "restored": restored,
        "errors": errors,
    }


def main():
    ap = argparse.ArgumentParser(description="State file backup/restore")
    ap.add_argument("--list", action="store_true", help="List backups")
    ap.add_argument("--restore", help="Restore from date (YYYY-MM-DD)")
    args = ap.parse_args()

    if args.list:
        backups = list_backups()
        if not backups:
            print("No backups found.")
            return 0
        for b in backups:
            print(f"  {b.get('date', '?')} {b.get('ts', '?')}: "
                  f"{b.get('copied', b.get('files_count', '?'))} files")
        return 0

    if args.restore:
        print(f"Restoring from {args.restore}...")
        result = restore_backup(args.restore)
        if "error" in result:
            print(f"ERROR: {result['error']}")
            return 1
        print(f"Restored {result['restored']} files from {result['snapshot']}")
        if result["errors"]:
            print(f"Errors: {result['errors']}")
        return 0

    print("Running state backup...")
    result = run_backup()
    print(f"Backup complete: {result['copied']} files copied, "
          f"{result['missing']} missing, {len(result['errors'])} errors")
    if result["errors"]:
        for e in result["errors"]:
            print(f"  ERROR: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
