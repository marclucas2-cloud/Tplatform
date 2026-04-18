"""Phase 6 — Data freshness gates.

Bloque le trade si les data critiques sont stale (e.g. *_1D.parquet pas
refreshe depuis > 24h, ce qui est le bug constate aujourd'hui sur VPS).

API:
    check_data_freshness(book_id) -> (fresh, details)

Used in pre_order_guard (optional, opt-in via env var DATA_FRESHNESS_GATE=true).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent

# Per-book required data files + max age in hours
FRESHNESS_REQUIREMENTS = {
    "binance_crypto": [
        ("data/crypto/candles/BTCUSDT_1d.parquet", 36),  # 36h max (weekend tolerance)
        ("data/crypto/candles/ETHUSDT_1d.parquet", 36),
    ],
    "ibkr_futures": [
        # 2026-04-18 audit P0.1: bascule de *_LONG.parquet (jamais existes
        # sur VPS, pointaient vers fichiers fantomes -> ibkr_futures DEGRADED
        # permanent) vers *_1D.parquet (cron data_refresh quotidien actif,
        # bug NaT/duplicates fixe session 15 avril dans worker data loader).
        # Tolerance 48h pour couvrir weekend (vendredi -> dimanche).
        ("data/futures/MES_1D.parquet", 48),
        ("data/futures/MGC_1D.parquet", 48),
        ("data/futures/MCL_1D.parquet", 48),
    ],
    "ibkr_eu": [
        ("data/futures/DAX_1D.parquet", 96),
        ("data/futures/CAC40_1D.parquet", 96),
        ("data/futures/ESTX50_1D.parquet", 96),
    ],
    "alpaca_us": [
        # Alpaca utilise yfinance daily, pas de fichier statique
    ],
    "ibkr_fx": [
        ("data/fx/AUDJPY_1D.parquet", 96),
    ],
}


def check_data_freshness(book_id: str) -> tuple[bool, dict]:
    """Check if data for book is fresh enough.

    Returns:
        (fresh, details). details = {file: age_hours} for each required file.
    """
    requirements = FRESHNESS_REQUIREMENTS.get(book_id, [])
    if not requirements:
        return True, {"note": "no freshness requirements"}

    now = datetime.now(timezone.utc)
    details = {}
    fresh = True
    for rel_path, max_hours in requirements:
        path = ROOT / rel_path
        if not path.exists():
            details[rel_path] = {"status": "missing", "max_hours": max_hours}
            fresh = False
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age_hours = (now - mtime).total_seconds() / 3600
        details[rel_path] = {
            "status": "fresh" if age_hours <= max_hours else "stale",
            "age_hours": round(age_hours, 1),
            "max_hours": max_hours,
        }
        if age_hours > max_hours:
            fresh = False

    return fresh, details


def is_data_freshness_gate_enabled() -> bool:
    return os.environ.get("DATA_FRESHNESS_GATE", "").lower() == "true"
