#!/usr/bin/env python3
"""
Fetch EU Universe — Donnees daily 5 ans via yfinance.

Telecharge TOUS les tickers EU (actions, indices, ETFs, forex, commodities,
volatilite, Asie) en daily 5 ans, calcule des stats, filtre les eligibles.

Outputs:
  data_cache/eu/{ticker}_daily_5y.parquet  (un fichier par ticker)
  data_cache/eu/eu_universe_stats.csv      (stats consolidees)
  data_cache/eu/eu_eligible_tickers.json   (tickers filtres)

Usage:
    python intraday-backtesterV2/fetch_eu_universe.py
    python intraday-backtesterV2/fetch_eu_universe.py --force   # re-telecharge tout
    python intraday-backtesterV2/fetch_eu_universe.py --stats-only  # recalcule stats sans fetch
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CACHE_DIR = PROJECT_ROOT / "data_cache" / "eu"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("fetch_eu_universe")

# =============================================================================
# EU UNIVERSE DEFINITION
# =============================================================================

# France (.PA — Euronext Paris)
FR_TICKERS = [
    "MC.PA", "TTE.PA", "SAN.PA", "OR.PA", "AI.PA", "SU.PA",
    "BNP.PA", "GLE.PA", "RMS.PA", "DSY.PA", "KER.PA", "CAP.PA",
    "SGO.PA", "VIV.PA", "STM.PA",
]

# Allemagne (.DE — Xetra)
DE_TICKERS = [
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "BAS.DE", "BMW.DE",
    "MBG.DE", "DBK.DE", "IFX.DE", "ADS.DE", "VOW3.DE", "MUV2.DE",
    "DPW.DE", "HEN3.DE", "RWE.DE",
]

# Pays-Bas (.AS — Euronext Amsterdam)
NL_TICKERS = [
    "ASML.AS", "SHEL.AS", "PRX.AS", "ADYEN.AS", "PHIA.AS", "UNA.AS",
]

# UK (.L — London Stock Exchange) — prix en PENCE, diviser par 100
UK_TICKERS = [
    "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L", "RIO.L",
    "GLEN.L", "LSEG.L", "BARC.L", "DGE.L",
]

# Espagne (.MC — Bolsa de Madrid)
ES_TICKERS = [
    "SAN.MC", "BBVA.MC", "IBE.MC", "ITX.MC", "TEF.MC",
]

# Italie (.MI — Borsa Italiana)
IT_TICKERS = [
    "UCG.MI", "ISP.MI", "ENEL.MI", "ENI.MI", "STLAM.MI",
]

# Indices
INDEX_TICKERS = [
    "^GDAXI",     # DAX
    "^FCHI",       # CAC 40
    "^FTSE",       # FTSE 100
    "^STOXX50E",   # Euro Stoxx 50
    "^STOXX",      # Stoxx Europe 600
]

# ETFs
ETF_TICKERS = [
    "EXS1.DE",    # iShares DAX
    "SX5S.DE",    # Invesco Euro Stoxx 50
    "EXSA.DE",    # iShares MSCI Europe
    "ISF.L",      # iShares FTSE 100
    "EXV1.DE",    # iShares Stoxx Europe 600 Financials
    "EXV3.DE",    # iShares Stoxx Europe 600 Healthcare
    "EXH1.DE",    # iShares Stoxx Europe 600 Technology
    "EXV4.DE",    # iShares Stoxx Europe 600 Industrials
    "EXV5.DE",    # iShares Stoxx Europe 600 Consumer Staples
    "EXH4.DE",    # iShares Stoxx Europe 600 Energy
]

# Forex
FX_TICKERS = [
    "EURUSD=X", "GBPUSD=X", "EURGBP=X", "EURJPY=X", "EURCHF=X", "AUDJPY=X",
]

# Commodities
COMMODITY_TICKERS = [
    "BZ=F",  # Brent Crude
    "GC=F",  # Gold
]

# Volatilite
VOL_TICKERS = [
    "^V2TX",  # VSTOXX
    "^VIX",   # VIX (reference)
]

# Asie (signal)
ASIA_TICKERS = [
    "^N225",  # Nikkei 225
    "^HSI",   # Hang Seng
]

# Reference US
US_REF_TICKERS = [
    "SPY",    # S&P 500 ETF (pour correlation)
]

# All tickers grouped
ALL_GROUPS = {
    "FR": FR_TICKERS,
    "DE": DE_TICKERS,
    "NL": NL_TICKERS,
    "UK": UK_TICKERS,
    "ES": ES_TICKERS,
    "IT": IT_TICKERS,
    "INDEX": INDEX_TICKERS,
    "ETF": ETF_TICKERS,
    "FX": FX_TICKERS,
    "COMMODITY": COMMODITY_TICKERS,
    "VOL": VOL_TICKERS,
    "ASIA": ASIA_TICKERS,
    "US_REF": US_REF_TICKERS,
}

# Tickers UK en pence (diviser par 100)
UK_PENCE_SET = set(UK_TICKERS)


def all_tickers() -> list[str]:
    """Retourne la liste complete des tickers a telecharger."""
    tickers = []
    for group in ALL_GROUPS.values():
        tickers.extend(group)
    return tickers


def ticker_to_filename(ticker: str) -> str:
    """Convertit un ticker yfinance en nom de fichier propre.

    Ex: MC.PA -> MC_PA_daily_5y.parquet
        ^GDAXI -> GDAXI_daily_5y.parquet
        EURUSD=X -> EURUSD_X_daily_5y.parquet
    """
    clean = ticker.replace(".", "_").replace("^", "").replace("=", "_")
    return f"{clean}_daily_5y.parquet"


def ticker_group(ticker: str) -> str:
    """Retourne le groupe d'un ticker."""
    for group_name, group_tickers in ALL_GROUPS.items():
        if ticker in group_tickers:
            return group_name
    return "UNKNOWN"


# =============================================================================
# FETCH
# =============================================================================

def is_cache_fresh(filepath: Path, max_age_hours: int = 24) -> bool:
    """Verifie si un fichier cache existe et a moins de max_age_hours."""
    if not filepath.exists():
        return False
    mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
    age = datetime.now() - mtime
    return age < timedelta(hours=max_age_hours)


def fetch_ticker(ticker: str, force: bool = False) -> pd.DataFrame | None:
    """Telecharge les donnees daily 5 ans pour un ticker via yfinance.

    Returns:
        DataFrame avec colonnes OHLCV, ou None si echec.
    """
    import yfinance as yf

    filename = ticker_to_filename(ticker)
    filepath = CACHE_DIR / filename

    # Idempotence : skip si cache < 24h
    if not force and is_cache_fresh(filepath):
        logger.info(f"  [CACHE] {ticker} -> {filename} (frais < 24h)")
        try:
            df = pd.read_parquet(filepath)
            return df
        except Exception:
            pass  # cache corrompu, re-telecharger

    logger.info(f"  [FETCH] {ticker} ...")

    try:
        data = yf.download(
            ticker,
            period="5y",
            interval="1d",
            progress=False,
            auto_adjust=True,
            timeout=30,
        )

        if data is None or data.empty:
            logger.warning(f"  [EMPTY] {ticker} — aucune donnee retournee")
            return None

        # Flatten MultiIndex columns if present (yfinance sometimes returns multi-level)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # UK pence -> GBP
        if ticker in UK_PENCE_SET:
            for col in ["Open", "High", "Low", "Close"]:
                if col in data.columns:
                    data[col] = data[col] / 100.0
            logger.info(f"  [GBP] {ticker} converti de pence en livres")

        # Ajouter metadata
        data.attrs["ticker"] = ticker
        data.attrs["group"] = ticker_group(ticker)
        data.attrs["fetched_at"] = datetime.now().isoformat()

        # Save parquet
        data.to_parquet(filepath, engine="pyarrow")
        logger.info(f"  [OK] {ticker} -> {filename} ({len(data)} barres)")

        return data

    except Exception as e:
        logger.error(f"  [ERROR] {ticker}: {e}")
        return None


def fetch_all(force: bool = False) -> dict[str, pd.DataFrame]:
    """Telecharge tous les tickers. Retourne un dict ticker -> DataFrame."""
    import yfinance as yf

    tickers = all_tickers()
    logger.info(f"\n{'='*70}")
    logger.info(f"  FETCH EU UNIVERSE — {len(tickers)} tickers")
    logger.info(f"  Cache: {CACHE_DIR}")
    logger.info(f"  Force: {force}")
    logger.info(f"{'='*70}\n")

    results = {}
    success = 0
    cached = 0
    failed = 0
    failed_tickers = []

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"[{i}/{len(tickers)}] {ticker}")

        filename = ticker_to_filename(ticker)
        filepath = CACHE_DIR / filename
        was_cached = not force and is_cache_fresh(filepath)

        df = fetch_ticker(ticker, force=force)

        if df is not None and not df.empty:
            results[ticker] = df
            if was_cached:
                cached += 1
            else:
                success += 1
        else:
            failed += 1
            failed_tickers.append(ticker)

        # Rate limit (seulement si on a vraiment telecharge)
        if not was_cached:
            time.sleep(0.5)

    logger.info(f"\n{'='*70}")
    logger.info(f"  RESUME FETCH")
    logger.info(f"  Telecharges: {success}")
    logger.info(f"  Depuis cache: {cached}")
    logger.info(f"  Echecs: {failed}")
    if failed_tickers:
        logger.info(f"  Tickers echoues: {', '.join(failed_tickers)}")
    logger.info(f"{'='*70}\n")

    return results


# =============================================================================
# STATS
# =============================================================================

def compute_stats(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Calcule les stats pour chaque ticker.

    Stats:
      - volume_avg_daily : volume moyen journalier (en monnaie locale)
      - atr_pct : ATR journalier en % du close
      - corr_spy : correlation avec SPY
      - corr_dax : correlation avec ^GDAXI
      - spread_avg_pct : (high-low)/close moyen
      - gaps_gt1pct_per_month : nombre de gaps > 1% par mois
      - return_by_dow : return moyen par jour de semaine (dict)
      - data_years : annees de donnees disponibles
      - total_bars : nombre total de barres
    """
    logger.info("\n  Calcul des stats univers EU...")

    # Charger SPY et DAX pour correlations
    spy_df = data.get("SPY")
    dax_df = data.get("^GDAXI")

    spy_ret = spy_df["Close"].pct_change().dropna() if spy_df is not None else None
    dax_ret = dax_df["Close"].pct_change().dropna() if dax_df is not None else None

    stats_rows = []

    for ticker, df in data.items():
        if df is None or df.empty or len(df) < 20:
            continue

        try:
            close = df["Close"]
            high = df["High"]
            low = df["Low"]
            volume = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)

            returns = close.pct_change().dropna()

            # Volume moyen daily
            vol_avg = volume.mean() if volume.sum() > 0 else 0

            # Volume moyen daily en valeur monetaire
            vol_value = (volume * close).mean() if volume.sum() > 0 else 0

            # ATR daily en %
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1] if len(tr) > 14 else tr.mean()
            atr_pct = (atr / close.iloc[-1]) * 100 if close.iloc[-1] > 0 else 0

            # Correlation avec SPY
            corr_spy = np.nan
            if spy_ret is not None and len(returns) > 20:
                common = returns.index.intersection(spy_ret.index)
                if len(common) > 20:
                    corr_spy = returns.loc[common].corr(spy_ret.loc[common])

            # Correlation avec DAX
            corr_dax = np.nan
            if dax_ret is not None and len(returns) > 20:
                common = returns.index.intersection(dax_ret.index)
                if len(common) > 20:
                    corr_dax = returns.loc[common].corr(dax_ret.loc[common])

            # Proxy spread (high-low)/close moyen
            spread = ((high - low) / close).mean() * 100

            # Gaps > 1% par mois
            gaps = (close / close.shift(1) - 1).abs()
            gaps_gt1 = (gaps > 0.01).sum()
            months = max((df.index[-1] - df.index[0]).days / 30, 1)
            gaps_per_month = gaps_gt1 / months

            # Return moyen par jour de semaine
            df_temp = returns.copy()
            df_temp.index = pd.to_datetime(df_temp.index)
            dow_returns = {}
            for day_num, day_name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
                day_rets = df_temp[df_temp.index.dayofweek == day_num]
                dow_returns[day_name] = round(day_rets.mean() * 100, 4) if len(day_rets) > 0 else 0

            # Annees de donnees
            data_days = (df.index[-1] - df.index[0]).days
            data_years = round(data_days / 365.25, 2)

            row = {
                "ticker": ticker,
                "group": ticker_group(ticker),
                "total_bars": len(df),
                "data_years": data_years,
                "last_close": round(float(close.iloc[-1]), 4),
                "volume_avg_shares": round(float(vol_avg), 0),
                "volume_avg_value": round(float(vol_value), 0),
                "atr_pct": round(float(atr_pct), 4),
                "corr_spy": round(float(corr_spy), 4) if not np.isnan(corr_spy) else np.nan,
                "corr_dax": round(float(corr_dax), 4) if not np.isnan(corr_dax) else np.nan,
                "spread_avg_pct": round(float(spread), 4),
                "gaps_gt1pct_per_month": round(float(gaps_per_month), 2),
                "return_mon": dow_returns.get("Mon", 0),
                "return_tue": dow_returns.get("Tue", 0),
                "return_wed": dow_returns.get("Wed", 0),
                "return_thu": dow_returns.get("Thu", 0),
                "return_fri": dow_returns.get("Fri", 0),
            }
            stats_rows.append(row)

        except Exception as e:
            logger.warning(f"  [STATS ERROR] {ticker}: {e}")
            continue

    stats_df = pd.DataFrame(stats_rows)
    return stats_df


def filter_eligible(stats_df: pd.DataFrame) -> list[dict]:
    """Filtre les tickers eligibles pour le trading.

    Criteres :
      - ATR > 0.5% (assez de mouvement)
      - Donnees > 2 ans
      - Tous les mega-caps EU sont gardes (on ne filtre pas par volume pour eux)
    """
    # Groupes toujours eligibles (mega-caps EU, indices, ETFs, refs)
    always_eligible_groups = {"FR", "DE", "NL", "UK", "ES", "IT", "INDEX", "ETF", "US_REF"}

    eligible = []

    for _, row in stats_df.iterrows():
        ticker = row["ticker"]
        group = row["group"]
        atr = row.get("atr_pct", 0)
        years = row.get("data_years", 0)

        # Toujours garder les mega-caps EU et refs
        if group in always_eligible_groups:
            if years >= 1.0:  # au moins 1 an de donnees
                eligible.append({
                    "ticker": ticker,
                    "group": group,
                    "atr_pct": round(float(atr), 4),
                    "data_years": float(years),
                    "reason": "mega_cap" if group not in {"INDEX", "ETF", "US_REF"} else group.lower(),
                })
            continue

        # Pour les autres (FX, commodities, vol, asia) : ATR > 0.5% et > 2 ans
        if atr > 0.5 and years >= 2.0:
            eligible.append({
                "ticker": ticker,
                "group": group,
                "atr_pct": round(float(atr), 4),
                "data_years": float(years),
                "reason": "filtered",
            })

    return eligible


# =============================================================================
# MAIN
# =============================================================================

def load_cached_data() -> dict[str, pd.DataFrame]:
    """Charge toutes les donnees depuis le cache parquet."""
    data = {}
    tickers = all_tickers()

    for ticker in tickers:
        filename = ticker_to_filename(ticker)
        filepath = CACHE_DIR / filename

        if filepath.exists():
            try:
                df = pd.read_parquet(filepath)
                data[ticker] = df
            except Exception as e:
                logger.warning(f"  [LOAD ERROR] {ticker}: {e}")

    return data


def main():
    parser = argparse.ArgumentParser(description="Fetch EU Universe data (yfinance, daily 5Y)")
    parser.add_argument("--force", action="store_true",
                        help="Re-telecharger meme si cache < 24h")
    parser.add_argument("--stats-only", action="store_true",
                        help="Recalculer les stats sans re-telecharger")
    args = parser.parse_args()

    start_time = time.time()

    # 1. Fetch (ou charger depuis cache)
    if args.stats_only:
        logger.info("Mode --stats-only : chargement depuis cache...")
        data = load_cached_data()
        if not data:
            logger.error("Aucune donnee en cache. Lancez d'abord sans --stats-only.")
            sys.exit(1)
    else:
        data = fetch_all(force=args.force)

    logger.info(f"\n  Donnees chargees: {len(data)} tickers")

    # 2. Calculer les stats
    stats_df = compute_stats(data)

    # Sauvegarder stats CSV
    stats_path = CACHE_DIR / "eu_universe_stats.csv"
    stats_df.to_csv(stats_path, index=False)
    logger.info(f"  Stats sauvegardees: {stats_path} ({len(stats_df)} tickers)")

    # 3. Filtrer les eligibles
    eligible = filter_eligible(stats_df)
    eligible_path = CACHE_DIR / "eu_eligible_tickers.json"
    with open(eligible_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "total_universe": len(all_tickers()),
            "total_fetched": len(data),
            "total_eligible": len(eligible),
            "tickers": eligible,
        }, f, indent=2, ensure_ascii=False)
    logger.info(f"  Eligibles: {eligible_path} ({len(eligible)} tickers)")

    # 4. Resume
    elapsed = time.time() - start_time
    logger.info(f"\n{'='*70}")
    logger.info(f"  FETCH EU UNIVERSE — TERMINE")
    logger.info(f"  Tickers telecharges: {len(data)}/{len(all_tickers())}")
    logger.info(f"  Stats calculees: {len(stats_df)}")
    logger.info(f"  Eligibles: {len(eligible)}")
    logger.info(f"  Temps: {elapsed:.1f}s")
    logger.info(f"{'='*70}")

    # Afficher les top tickers par ATR
    if not stats_df.empty:
        print(f"\n  TOP 15 EU par ATR% :")
        top = stats_df.nlargest(15, "atr_pct")
        for _, r in top.iterrows():
            print(f"    {r['ticker']:<15s} ATR={r['atr_pct']:>6.2f}%  "
                  f"corrSPY={r.get('corr_spy', 0):>6.3f}  "
                  f"corrDAX={r.get('corr_dax', 0):>6.3f}  "
                  f"gaps/m={r.get('gaps_gt1pct_per_month', 0):>5.1f}  "
                  f"years={r.get('data_years', 0):.1f}")

    # Afficher les tickers echoues (pas dans data)
    all_t = set(all_tickers())
    fetched_t = set(data.keys())
    missing = all_t - fetched_t
    if missing:
        print(f"\n  TICKERS MANQUANTS ({len(missing)}) :")
        for t in sorted(missing):
            print(f"    {t}")


if __name__ == "__main__":
    main()
