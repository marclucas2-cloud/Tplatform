"""Safe loader pour parquet daily du desk.

Single source of truth pour charger un *_1D.parquet sans tomber dans le piege
"colonne datetime legacy avec NaT" qui a corrompu silencieusement les decisions
desk pendant 2026-03-27 -> 2026-04-24 (cf reports/audit/stale_window_*).

Regle:
  - preserve un DatetimeIndex valide existant
  - fallback sur la colonne ``datetime`` UNIQUEMENT si l'index n'est pas DT valide
  - drop la colonne ``datetime`` apres usage pour eviter recontamination
  - dedupe + sort + strip tz

Usage:
    from core.data.parquet_safe_loader import load_daily_parquet_safe
    df = load_daily_parquet_safe(Path("data/futures/MES_1D.parquet"))

Tout nouveau loader runtime *_1D doit utiliser cette fonction. Tout ajout
d'un autre pattern de loading sur ces fichiers est verrouille par
tests/test_no_toxic_datetime_legacy_pattern_2026_04_26.py.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_daily_parquet_safe(path: Path) -> pd.DataFrame:
    """Charge un parquet daily en preservant un DatetimeIndex valide.

    Args:
        path: chemin vers le parquet a charger.

    Returns:
        DataFrame avec un DatetimeIndex valide, sans colonne ``datetime``,
        sans NaT, dedupe, trie ascendant, sans timezone.

    Raises:
        FileNotFoundError: si le fichier n'existe pas.
    """
    if not path.exists():
        raise FileNotFoundError(f"Parquet not found: {path}")

    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]

    has_valid_dt_index = (
        isinstance(df.index, pd.DatetimeIndex) and df.index.notna().any()
    )
    if has_valid_dt_index:
        pass
    elif "datetime" in df.columns:
        df.index = pd.to_datetime(df["datetime"], errors="coerce")
    else:
        df.index = pd.to_datetime(df.index, errors="coerce")

    if "datetime" in df.columns:
        df = df.drop(columns=["datetime"])

    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def parquet_content_age_days(path: Path) -> int | None:
    """Renvoie l'age en jours de la derniere bar lisible (via load_daily_parquet_safe).

    Different de mtime: detecte le bug "fichier reecrit chaque jour mais contenu
    stale" (cas observe en avril 2026 ou la col datetime corrompue masquait
    les nouvelles bars).

    Returns:
        int: nombre de jours entre aujourd'hui (UTC) et la derniere bar.
        None si le fichier est absent ou vide.
    """
    if not path.exists():
        return None
    try:
        df = load_daily_parquet_safe(path)
    except Exception:
        return None
    if df.empty:
        return None
    last = df.index.max().normalize()
    now = pd.Timestamp.now().normalize()
    return int((now - last).days)
