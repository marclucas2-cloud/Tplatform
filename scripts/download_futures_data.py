"""
Download futures historical data — IBKR primary, yfinance ETF fallback.

Instruments :
  - MES (Micro E-mini S&P)   / ES (full-size)  → ETF proxy: SPY
  - MNQ (Micro E-mini Nasdaq) / NQ             → ETF proxy: QQQ
  - MCL (Micro WTI Crude)    / CL              → ETF proxy: USO
  - MGC (Micro Gold)         / GC              → ETF proxy: GLD

Timeframes : 5min, 1h, daily
Period : 2021-01-01 to today
Storage : data/futures_historical/{symbol}_{timeframe}.csv

Continuous contract construction :
  - Back-adjusted avec ratio method
  - Roll 5 jours avant expiry

yfinance fallback :
  - Utilise les ETF proxies quand IBKR n'est pas disponible
  - Validation : compare ETF vs futures daily closes (< 1% divergence)

Usage :
  python scripts/download_futures_data.py                  # Tous les symboles
  python scripts/download_futures_data.py --symbol MES     # Un seul symbole
  python scripts/download_futures_data.py --fallback-only  # yfinance uniquement
  python scripts/download_futures_data.py --validate       # Validation ETF vs futures
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Ajouter le root du projet au path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


logger = logging.getLogger(__name__)

# --- Configuration ---
OUTPUT_DIR = ROOT / "data" / "futures_historical"

SYMBOLS_MICRO = ["MES", "MNQ", "MCL", "MGC"]
SYMBOLS_FULL = ["ES", "NQ", "CL", "GC"]
ALL_SYMBOLS = SYMBOLS_MICRO + SYMBOLS_FULL

# Mapping symbol → ETF proxy (pour yfinance fallback)
ETF_PROXY_MAP = {
    "ES": "SPY", "MES": "SPY",
    "NQ": "QQQ", "MNQ": "QQQ",
    "CL": "USO", "MCL": "USO",
    "GC": "GLD", "MGC": "GLD",
}

# Timeframes a telecharger
TIMEFRAMES = {
    "5min": {"yf_interval": "5m", "yf_period": "60d", "ibkr_bar": "5 mins"},
    "1h": {"yf_interval": "1h", "yf_period": "730d", "ibkr_bar": "1 hour"},
    "daily": {"yf_interval": "1d", "yf_period": "max", "ibkr_bar": "1 day"},
}

# Retry config pour IBKR pacing limits
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0
MAX_BACKOFF = 60.0

# Period
START_DATE = "2021-01-01"


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def retry_with_backoff(func, *args, max_retries=MAX_RETRIES, **kwargs):
    """Execute une fonction avec retry et backoff exponentiel.

    Gere les pacing violations IBKR (erreur 162) et les timeouts.
    """
    backoff = INITIAL_BACKOFF
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            err_str = str(e).lower()

            # IBKR pacing violation — attendre plus longtemps
            if "pacing" in err_str or "162" in err_str:
                wait = backoff * 2
                logger.warning(
                    f"IBKR pacing violation (tentative {attempt}/{max_retries}) "
                    f"— attente {wait:.0f}s"
                )
            else:
                wait = backoff
                logger.warning(
                    f"Erreur (tentative {attempt}/{max_retries}): {e} "
                    f"— retry dans {wait:.0f}s"
                )

            if attempt < max_retries:
                time.sleep(wait)
                backoff = min(backoff * 2, MAX_BACKOFF)

    raise last_error


# =============================================================================
# IBKR Data Download
# =============================================================================

def download_ibkr_futures(
    symbol: str,
    timeframe: str,
    start_date: str = START_DATE,
) -> pd.DataFrame | None:
    """Telecharge les donnees futures via IBKR.

    Args:
        symbol: symbole futures (ex. "MES")
        timeframe: "5min", "1h", ou "daily"
        start_date: date de debut (ISO format)

    Returns:
        DataFrame OHLCV ou None si echec
    """
    try:
        from core.broker.ibkr_adapter import IBKRBroker
        from core.broker.ibkr_futures import IBKRFuturesClient
    except ImportError:
        logger.warning("ib_insync non disponible — skip IBKR download")
        return None

    try:
        broker = IBKRBroker()
        client = IBKRFuturesClient(broker)
    except Exception as e:
        logger.warning(f"IBKR connexion impossible: {e}")
        return None

    tf_config = TIMEFRAMES[timeframe]
    bar_size = tf_config["ibkr_bar"]

    # Calculer la duree
    start = datetime.fromisoformat(start_date)
    days = (datetime.now() - start).days

    # IBKR limite : 1 an pour barres < 30min, sinon illimite
    if timeframe == "5min":
        # 5min : max ~30 jours par requete, faire des chunks
        chunks = _chunk_date_range(start_date, days, chunk_days=25)
    elif timeframe == "1h":
        chunks = _chunk_date_range(start_date, days, chunk_days=300)
    else:
        chunks = _chunk_date_range(start_date, days, chunk_days=365)

    all_bars = []
    for chunk_end in chunks:
        try:
            def _fetch():
                return client.get_futures_prices(
                    symbol=symbol,
                    timeframe={"5min": "5M", "1h": "1H", "daily": "1D"}[timeframe],
                    bars=5000,
                )

            result = retry_with_backoff(_fetch)
            bars = result.get("bars", [])
            all_bars.extend(bars)
            logger.info(f"  IBKR {symbol} {timeframe}: {len(bars)} barres (chunk)")

            # Pause entre les requetes pour eviter le pacing
            time.sleep(1.5)

        except Exception as e:
            logger.error(f"IBKR {symbol} {timeframe} chunk echoue: {e}")

    if not all_bars:
        return None

    df = pd.DataFrame(all_bars)
    df.rename(columns={"t": "datetime", "o": "open", "h": "high",
                        "l": "low", "c": "close", "v": "volume"}, inplace=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)

    logger.info(f"IBKR {symbol} {timeframe}: {len(df)} barres totales")
    return df


def _chunk_date_range(start_date: str, total_days: int, chunk_days: int) -> list[str]:
    """Decoupe une plage de dates en chunks pour IBKR."""
    start = datetime.fromisoformat(start_date)
    chunks = []
    current = start
    while current < datetime.now():
        current += timedelta(days=chunk_days)
        chunks.append(min(current, datetime.now()).strftime("%Y%m%d %H:%M:%S"))
    return chunks


# =============================================================================
# yfinance Fallback (ETF Proxies)
# =============================================================================

def download_yfinance_proxy(
    symbol: str,
    timeframe: str,
    start_date: str = START_DATE,
) -> pd.DataFrame | None:
    """Telecharge les donnees ETF proxy via yfinance comme fallback.

    Args:
        symbol: symbole futures (ex. "MES") — mappe vers ETF (SPY)
        timeframe: "5min", "1h", ou "daily"
        start_date: date de debut

    Returns:
        DataFrame OHLCV ou None si echec
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance non installe — pip install yfinance")
        return None

    etf = ETF_PROXY_MAP.get(symbol)
    if not etf:
        logger.error(f"Pas de proxy ETF pour {symbol}")
        return None

    tf_config = TIMEFRAMES[timeframe]
    interval = tf_config["yf_interval"]

    logger.info(f"yfinance fallback: {symbol} → {etf} ({timeframe}, interval={interval})")

    try:
        # yfinance: pour 5min, limite a 60 jours
        # pour 1h, limite a 730 jours
        if timeframe == "5min":
            # Telecharger en chunks de 59 jours
            df = _download_yf_chunked(etf, interval, start_date, chunk_days=59)
        elif timeframe == "1h":
            df = _download_yf_chunked(etf, interval, start_date, chunk_days=700)
        else:
            ticker = yf.Ticker(etf)
            df = ticker.history(start=start_date, interval=interval)
            df = df.reset_index()

        if df is None or df.empty:
            logger.warning(f"yfinance: aucune donnee pour {etf} ({timeframe})")
            return None

        # Normaliser les colonnes
        df = _normalize_yf_dataframe(df)
        logger.info(f"yfinance {etf} ({timeframe}): {len(df)} barres")
        return df

    except Exception as e:
        logger.error(f"yfinance erreur pour {etf}: {e}")
        return None


def _download_yf_chunked(
    ticker_symbol: str,
    interval: str,
    start_date: str,
    chunk_days: int,
) -> pd.DataFrame:
    """Telecharge les donnees yfinance en chunks (pour contourner les limites)."""
    import yfinance as yf

    start = datetime.fromisoformat(start_date)
    end = datetime.now()
    chunks = []

    current_start = start
    while current_start < end:
        current_end = min(current_start + timedelta(days=chunk_days), end)
        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(
                start=current_start.strftime("%Y-%m-%d"),
                end=current_end.strftime("%Y-%m-%d"),
                interval=interval,
            )
            if df is not None and not df.empty:
                df = df.reset_index()
                chunks.append(df)
        except Exception as e:
            logger.warning(
                f"yfinance chunk {current_start.date()} → {current_end.date()} "
                f"echoue: {e}"
            )

        current_start = current_end
        time.sleep(0.5)  # Rate limit yfinance

    if not chunks:
        return pd.DataFrame()

    combined = pd.concat(chunks, ignore_index=True)
    return combined


def _normalize_yf_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise un DataFrame yfinance vers notre format standard."""
    # yfinance utilise 'Date' ou 'Datetime' selon l'interval
    date_col = None
    for col in ["Datetime", "Date", "datetime", "date"]:
        if col in df.columns:
            date_col = col
            break

    if date_col is None:
        # Peut etre dans l'index
        df = df.reset_index()
        for col in ["Datetime", "Date", "datetime", "date"]:
            if col in df.columns:
                date_col = col
                break

    if date_col is None:
        raise ValueError(f"Colonne date introuvable. Colonnes: {list(df.columns)}")

    result = pd.DataFrame({
        "datetime": pd.to_datetime(df[date_col]),
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low": df["Low"].astype(float),
        "close": df["Close"].astype(float),
        "volume": df["Volume"].astype(float),
    })

    result = result.drop_duplicates(subset="datetime").sort_values("datetime")
    return result.reset_index(drop=True)


# =============================================================================
# Continuous Contract Construction (Back-Adjusted, Ratio Method)
# =============================================================================

def build_continuous_contract(
    df: pd.DataFrame,
    roll_dates: list[dict] | None = None,
) -> pd.DataFrame:
    """Construit un contrat continu back-adjusted (methode ratio).

    La methode ratio ajuste les prix historiques par le ratio
    entre l'ancien et le nouveau contrat au moment du roll.
    Cela preserve les rendements pourcentuels (pas de gaps de roll).

    Args:
        df: DataFrame OHLCV avec colonne 'datetime'
        roll_dates: [{date, ratio}] pour chaque roll (optionnel)

    Returns:
        DataFrame avec prix back-adjusted
    """
    if roll_dates is None or len(roll_dates) == 0:
        # Pas de roll : retourner tel quel
        return df.copy()

    df = df.copy().sort_values("datetime").reset_index(drop=True)

    # Appliquer les ajustements ratio du plus recent au plus ancien
    for roll in sorted(roll_dates, key=lambda r: r["date"], reverse=True):
        roll_dt = pd.to_datetime(roll["date"])
        ratio = roll.get("ratio", 1.0)

        if ratio == 0 or ratio == 1.0:
            continue

        # Ajuster toutes les barres avant le roll
        mask = df["datetime"] < roll_dt
        for col in ["open", "high", "low", "close"]:
            df.loc[mask, col] = df.loc[mask, col] * ratio

    df["adjusted"] = True
    return df


# =============================================================================
# Validation : ETF vs Futures divergence
# =============================================================================

def validate_etf_vs_futures(
    futures_df: pd.DataFrame,
    etf_df: pd.DataFrame,
    max_divergence: float = 0.01,
) -> dict:
    """Compare les closes daily futures vs ETF proxy.

    Args:
        futures_df: DataFrame futures (daily)
        etf_df: DataFrame ETF proxy (daily)
        max_divergence: divergence max acceptable (defaut 1%)

    Returns:
        {
            valid: bool,
            mean_divergence: float,
            max_divergence: float,
            divergence_days: int,  # jours au-dessus du seuil
            total_days: int,
            correlation: float,
        }
    """
    # Aligner sur les dates communes
    futures_daily = futures_df.copy()
    etf_daily = etf_df.copy()

    futures_daily["date"] = pd.to_datetime(futures_daily["datetime"]).dt.date
    etf_daily["date"] = pd.to_datetime(etf_daily["datetime"]).dt.date

    # Garder uniquement les dates communes
    common_dates = set(futures_daily["date"]) & set(etf_daily["date"])
    if len(common_dates) < 10:
        logger.warning(f"Trop peu de dates communes ({len(common_dates)}) pour validation")
        return {
            "valid": False,
            "mean_divergence": None,
            "max_divergence_observed": None,
            "divergence_days": 0,
            "total_days": len(common_dates),
            "correlation": None,
        }

    futures_daily = futures_daily[futures_daily["date"].isin(common_dates)]
    etf_daily = etf_daily[etf_daily["date"].isin(common_dates)]

    # Aggreer par jour (prendre le dernier close)
    f_close = futures_daily.groupby("date")["close"].last().sort_index()
    e_close = etf_daily.groupby("date")["close"].last().sort_index()

    # Aligner
    common = f_close.index.intersection(e_close.index)
    f_aligned = f_close.loc[common]
    e_aligned = e_close.loc[common]

    # Calculer les rendements pour la comparaison (normalise les echelles differentes)
    f_returns = f_aligned.pct_change().dropna()
    e_returns = e_aligned.pct_change().dropna()

    # Aligner les rendements
    common_ret = f_returns.index.intersection(e_returns.index)
    f_ret = f_returns.loc[common_ret]
    e_ret = e_returns.loc[common_ret]

    if len(f_ret) < 5:
        return {
            "valid": False,
            "mean_divergence": None,
            "max_divergence_observed": None,
            "divergence_days": 0,
            "total_days": len(common_ret),
            "correlation": None,
        }

    # Divergence = difference absolue des rendements
    divergence = (f_ret - e_ret).abs()
    mean_div = float(divergence.mean())
    max_div = float(divergence.max())
    div_days = int((divergence > max_divergence).sum())
    correlation = float(f_ret.corr(e_ret))

    valid = mean_div < max_divergence and correlation > 0.90

    logger.info(
        f"Validation ETF: mean_div={mean_div:.4f}, max_div={max_div:.4f}, "
        f"corr={correlation:.4f}, valid={valid}"
    )

    return {
        "valid": valid,
        "mean_divergence": round(mean_div, 6),
        "max_divergence_observed": round(max_div, 6),
        "divergence_days": div_days,
        "total_days": len(common_ret),
        "correlation": round(correlation, 4),
    }


# =============================================================================
# Save / Load
# =============================================================================

def save_data(df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
    """Sauvegarde les donnees en CSV."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = OUTPUT_DIR / f"{symbol}_{timeframe}.csv"
    df.to_csv(filepath, index=False)
    logger.info(f"Sauvegarde: {filepath} ({len(df)} lignes)")
    return filepath


def load_data(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Charge les donnees depuis un CSV."""
    filepath = OUTPUT_DIR / f"{symbol}_{timeframe}.csv"
    if not filepath.exists():
        return None
    df = pd.read_csv(filepath, parse_dates=["datetime"])
    return df


# =============================================================================
# Main Pipeline
# =============================================================================

def download_all(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    fallback_only: bool = False,
    validate: bool = False,
):
    """Pipeline principal de telechargement.

    Args:
        symbols: liste de symboles (defaut: tous les micros)
        timeframes: liste de timeframes (defaut: tous)
        fallback_only: True = yfinance uniquement (pas d'IBKR)
        validate: True = comparer ETF vs futures apres telechargement
    """
    if symbols is None:
        symbols = SYMBOLS_MICRO
    if timeframes is None:
        timeframes = list(TIMEFRAMES.keys())

    results = {}

    for symbol in symbols:
        for tf in timeframes:
            key = f"{symbol}_{tf}"
            logger.info(f"\n{'='*60}")
            logger.info(f"Telechargement: {symbol} ({tf})")
            logger.info(f"{'='*60}")

            df = None

            # Essayer IBKR d'abord (sauf fallback_only)
            if not fallback_only:
                try:
                    df = download_ibkr_futures(symbol, tf)
                except Exception as e:
                    logger.warning(f"IBKR echoue pour {symbol} ({tf}): {e}")

            # Fallback yfinance
            if df is None:
                logger.info(f"Fallback yfinance pour {symbol} ({tf})")
                df = download_yfinance_proxy(symbol, tf)

            if df is not None and not df.empty:
                filepath = save_data(df, symbol, tf)
                results[key] = {
                    "status": "ok",
                    "rows": len(df),
                    "path": str(filepath),
                    "source": "ibkr" if not fallback_only else "yfinance",
                }
            else:
                results[key] = {"status": "failed", "rows": 0}
                logger.error(f"ECHEC: {symbol} ({tf}) — aucune donnee")

    # Validation ETF vs Futures
    if validate:
        logger.info(f"\n{'='*60}")
        logger.info("VALIDATION ETF vs Futures")
        logger.info(f"{'='*60}")
        _run_validation(symbols)

    # Resume
    logger.info(f"\n{'='*60}")
    logger.info("RESUME")
    logger.info(f"{'='*60}")
    ok = sum(1 for r in results.values() if r["status"] == "ok")
    failed = sum(1 for r in results.values() if r["status"] == "failed")
    logger.info(f"  OK: {ok}/{len(results)}")
    logger.info(f"  ECHEC: {failed}/{len(results)}")
    for key, r in results.items():
        status = "OK" if r["status"] == "ok" else "FAIL"
        rows = r.get("rows", 0)
        logger.info(f"  {key}: {status} ({rows} lignes)")

    return results


def _run_validation(symbols: list[str]):
    """Execute la validation ETF vs futures pour les symboles donnes."""
    for symbol in symbols:
        etf = ETF_PROXY_MAP.get(symbol)
        if not etf:
            continue

        futures_df = load_data(symbol, "daily")
        etf_df = download_yfinance_proxy(symbol, "daily")

        if futures_df is None or etf_df is None:
            logger.warning(f"Validation {symbol}/{etf}: donnees manquantes")
            continue

        result = validate_etf_vs_futures(futures_df, etf_df)
        status = "PASS" if result["valid"] else "FAIL"
        logger.info(
            f"Validation {symbol} vs {etf}: {status} "
            f"(mean_div={result['mean_divergence']}, corr={result['correlation']})"
        )


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Download futures historical data"
    )
    parser.add_argument(
        "--symbol", "-s",
        help="Symbole unique a telecharger (ex. MES)",
    )
    parser.add_argument(
        "--timeframe", "-t",
        choices=["5min", "1h", "daily"],
        help="Timeframe unique",
    )
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="Utiliser uniquement yfinance (pas d'IBKR)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Valider ETF vs futures apres telechargement",
    )
    parser.add_argument(
        "--full-size",
        action="store_true",
        help="Inclure les contrats full-size (ES, NQ, CL, GC)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Logs detailles",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    symbols = None
    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.full_size:
        symbols = ALL_SYMBOLS
    # else: default = SYMBOLS_MICRO

    timeframes = None
    if args.timeframe:
        timeframes = [args.timeframe]

    download_all(
        symbols=symbols,
        timeframes=timeframes,
        fallback_only=args.fallback_only,
        validate=args.validate,
    )


if __name__ == "__main__":
    main()
