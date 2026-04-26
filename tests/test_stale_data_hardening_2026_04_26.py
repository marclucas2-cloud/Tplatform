"""Mission 1 hardening: tests non-regression stale-data.

Verrouille:
  1. Aucun fichier RUNTIME (worker.py, core/worker, core/broker, core/runtime,
     strategies_v2) ne doit utiliser le pattern toxique:
        df.index = pd.to_datetime(df["datetime"])
     suivi de drop NaT, sans avoir prefere d'abord un DatetimeIndex valide.
     Les scripts research et tools sont exclus du verrou (ils sont manuels).

  2. Le helper canonique load_daily_parquet_safe doit:
     - preserver un DatetimeIndex valide quand le fichier en a un
     - drop la colonne datetime legacy
     - resister a la corruption "datetime=NaT pour bars recents"

  3. Le check preflight content_freshness DOIT detecter la corruption
     stale-content (last bar trop vieille via le safe loader).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent

# Repertoires runtime ou le pattern toxique est INTERDIT.
# Les scripts/, tests/, docs/, reports/ ne sont PAS verrouilles (manuels).
RUNTIME_DIRS = [
    ROOT / "worker.py",
    ROOT / "core" / "worker",
    ROOT / "core" / "broker",
    ROOT / "core" / "runtime",
    ROOT / "core" / "data" / "loader.py",
    ROOT / "strategies_v2",
]

# Pattern toxique: override l'index par la colonne legacy datetime SANS
# preserver d'abord un DatetimeIndex valide.
_TOXIC_OVERRIDE = re.compile(
    r'df\.index\s*=\s*pd\.to_datetime\(df\[["\']datetime["\']\]\)'
)
# Garde-corps: si le fichier preserve d'abord un DatetimeIndex valide, c'est OK.
_PRESERVE_GUARD = re.compile(
    r'isinstance\(\s*df\.index\s*,\s*pd\.DatetimeIndex\s*\)\s*'
    r'and\s+df\.index\.notna\(\)'
)


def _walk_python_files(root: Path):
    if root.is_file() and root.suffix == ".py":
        yield root
        return
    if root.is_dir():
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def test_no_toxic_datetime_legacy_pattern_in_runtime():
    """Verrou source-level: aucun runtime n'utilise le pattern toxique sans guard."""
    offenders = []
    for runtime_path in RUNTIME_DIRS:
        for f in _walk_python_files(runtime_path):
            text = f.read_text(encoding="utf-8")
            if _TOXIC_OVERRIDE.search(text):
                # Pattern present: doit etre precede d'un guard preserve
                if not _PRESERVE_GUARD.search(text):
                    offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, (
        "Pattern toxique 'df.index = pd.to_datetime(df[\"datetime\"])' detecte "
        "dans des fichiers runtime SANS garde-corps DatetimeIndex preserve. "
        "Cause du bug stale-data 2026-03-27 -> 2026-04-24. Utiliser "
        "core.data.parquet_safe_loader.load_daily_parquet_safe a la place. "
        f"Offenders: {offenders}"
    )


def test_safe_loader_preserves_valid_datetimeindex(tmp_path):
    """Le helper canonique doit ignorer la col datetime corrompue."""
    from core.data.parquet_safe_loader import load_daily_parquet_safe

    idx = pd.to_datetime(["2026-04-22", "2026-04-23", "2026-04-24"])
    df = pd.DataFrame(
        {
            "open": [7100.0, 7120.0, 7140.0],
            "close": [7122.25, 7132.50, 7195.50],
            # Corruption typique: ancien rows avaient datetime, les nouveaux non.
            "datetime": [pd.Timestamp("2026-04-22"), pd.NaT, pd.NaT],
        },
        index=idx,
    )
    path = tmp_path / "MES_1D.parquet"
    df.to_parquet(path)

    loaded = load_daily_parquet_safe(path)

    assert list(loaded.index.strftime("%Y-%m-%d")) == [
        "2026-04-22",
        "2026-04-23",
        "2026-04-24",
    ]
    assert "datetime" not in loaded.columns
    assert float(loaded.iloc[-1]["close"]) == 7195.50


def test_safe_loader_falls_back_to_datetime_col_when_index_invalid(tmp_path):
    """Si l'index n'est pas DT valide, le helper utilise la col datetime."""
    from core.data.parquet_safe_loader import load_daily_parquet_safe

    df = pd.DataFrame(
        {
            "open": [10.0, 11.0],
            "close": [10.5, 11.2],
            "datetime": [pd.Timestamp("2026-04-22"), pd.Timestamp("2026-04-23")],
        },
        # Index entier (pas DT)
        index=[0, 1],
    )
    path = tmp_path / "fallback.parquet"
    df.to_parquet(path)

    loaded = load_daily_parquet_safe(path)
    assert list(loaded.index.strftime("%Y-%m-%d")) == ["2026-04-22", "2026-04-23"]
    assert "datetime" not in loaded.columns


def test_parquet_content_age_days(tmp_path):
    from core.data.parquet_safe_loader import parquet_content_age_days

    # Empty path
    assert parquet_content_age_days(tmp_path / "missing.parquet") is None

    # Fresh content
    fresh_idx = pd.to_datetime([pd.Timestamp.now().normalize() - pd.Timedelta(days=1)])
    pd.DataFrame({"close": [100.0]}, index=fresh_idx).to_parquet(tmp_path / "fresh.parquet")
    age = parquet_content_age_days(tmp_path / "fresh.parquet")
    assert age == 1

    # Stale content
    stale_idx = pd.to_datetime([pd.Timestamp.now().normalize() - pd.Timedelta(days=20)])
    pd.DataFrame({"close": [100.0]}, index=stale_idx).to_parquet(tmp_path / "stale.parquet")
    age = parquet_content_age_days(tmp_path / "stale.parquet")
    assert age == 20


def test_preflight_content_freshness_flags_corrupted_parquet(tmp_path, monkeypatch):
    """Le check preflight content_freshness doit DETECTER un parquet stale-content."""
    from core.runtime import preflight as pf

    # Build a parquet with the EXACT corruption: index OK + col datetime NaT
    # for last 2 rows. The OLD loader would truncate; the SAFE loader sees it fresh.
    # Thus the content age check should pass for fresh content.
    fresh_idx = pd.to_datetime([
        pd.Timestamp.now().normalize() - pd.Timedelta(days=2),
        pd.Timestamp.now().normalize() - pd.Timedelta(days=1),
    ])
    df_fresh = pd.DataFrame(
        {"close": [100.0, 101.0], "datetime": [pd.NaT, pd.NaT]},
        index=fresh_idx,
    )
    fresh_path = tmp_path / "fresh.parquet"
    df_fresh.to_parquet(fresh_path)
    check = pf._check_parquet_content_freshness(fresh_path, max_age_days=4, tag="TEST")
    assert check.passed, f"Fresh parquet should pass: {check.message}"

    # Now stale parquet: last bar 30 days ago
    stale_idx = pd.to_datetime([pd.Timestamp.now().normalize() - pd.Timedelta(days=30)])
    pd.DataFrame({"close": [100.0]}, index=stale_idx).to_parquet(tmp_path / "stale.parquet")
    check = pf._check_parquet_content_freshness(tmp_path / "stale.parquet",
                                                  max_age_days=4, tag="TEST")
    assert not check.passed
    assert check.severity == "critical"
    assert "STALE" in check.message


def test_preflight_content_check_in_boot_preflight():
    """Le boot_preflight doit inclure les checks data_content::*."""
    from core.runtime.preflight import boot_preflight

    result = boot_preflight(
        check_equity_state=False,
        check_data_freshness=True,
        check_ibkr_gateway=False,
        fail_closed=False,
    )
    content_checks = [c for c in result.checks if c.name.startswith("data_content::")]
    assert content_checks, (
        "Le boot_preflight doit produire au moins 1 check data_content::* "
        "pour detecter le bug stale-content"
    )
