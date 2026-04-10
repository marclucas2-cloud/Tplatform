"""
Download EU indices intraday data via IBKR — 5min + 15min, 5 ans.

Utilise IBKRDataLoader existant (core/data/ibkr_data_loader.py).
Connexion : Hetzner IB Gateway paper 4003, clientId 105 (pas de conflit worker).

Symboles : DAX, CAC40, ESTX50, FTSE100 (Index IBKR, pas Futures)
  - Avantage : pas de roll a gerer, serie continue propre
  - Inconvenient : volume=0 (Index pas Future) → les strats volume-filter
    devront utiliser ATR ou range-based a la place

Timeframes : 5 mins + 15 mins
Periode : 2021-01-01 a aujourd'hui (~5 ans)

Chunks :
  - 5min  : 7 jours par requete (limite IBKR "1 W")
  - 15min : 30 jours par requete ("1 M")

Rate limit : 60 req/10min, 10.5s entre requetes.
Estimation : ~3.5-4h pour 4 symboles × 2 timeframes.

Resumable : sauvegarde parquet apres chaque chunk, skip si deja present.

Storage : data/eu_intraday/{symbol}_{TF}.parquet
  TF = 5M ou 15M

Usage :
  python scripts/download_eu_intraday.py                    # Tout
  python scripts/download_eu_intraday.py --symbol DAX       # Un seul
  python scripts/download_eu_intraday.py --tf 5M            # Un timeframe
  python scripts/download_eu_intraday.py --start 2023-01-01 # Periode custom
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Python 3.14 compat : eventkit / ib_insync attendent un event loop pre-existant
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.data.ibkr_data_loader import IBKRDataLoader

logger = logging.getLogger(__name__)

# Connection — Hetzner paper, clientId unique 105
IBKR_HOST = "178.104.125.74"
IBKR_PORT = 4003
CLIENT_ID = 105

# Defaults
DEFAULT_START = "2021-01-01"
OUTPUT_DIR = ROOT / "data" / "eu_intraday"

# Symboles supportes (IBKRDataLoader EU_INDEX_CONTRACTS)
SYMBOLS = ["DAX", "CAC40", "ESTX50", "FTSE100"]

# Timeframe config : {label: (ibkr_bar_size, chunk_days, duration_str)}
TIMEFRAMES = {
    "5M":  ("5 mins",  7,  "7 D"),
    "15M": ("15 mins", 30, "1 M"),
}

# Rate limit
SLEEP_BETWEEN_REQUESTS = 11.0  # Secondes, safe marge > 10.5
PACING_BACKOFF = 30.0           # Apres pacing violation


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )


def chunked_date_ranges(
    start_date: str,
    end_date: str | None,
    chunk_days: int,
) -> list[tuple[datetime, datetime]]:
    """Decoupe [start, end] en chunks de chunk_days jours.

    Returns:
        Liste de (start, end) datetime UTC, oldest first.
    """
    start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) if end_date is None else \
        datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

    chunks = []
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        chunks.append((current, chunk_end))
        current = chunk_end

    return chunks


def download_chunk(
    loader: IBKRDataLoader,
    symbol: str,
    bar_size: str,
    duration_str: str,
    end_dt: datetime,
    max_retries: int = 3,
) -> pd.DataFrame | None:
    """Download un seul chunk avec retry sur pacing.

    IBKR format endDateTime: 'yyyymmdd hh:mm:ss TZ'
    """
    end_str = end_dt.strftime("%Y%m%d %H:%M:%S UTC")

    for attempt in range(1, max_retries + 1):
        try:
            df = loader.download_bars(
                symbol=symbol,
                duration=duration_str,
                bar_size=bar_size,
                use_rth=True,
                end_datetime=end_str,
                what_to_show="TRADES",
            )
            return df
        except Exception as e:
            err = str(e).lower()
            if "pacing" in err or "162" in err:
                wait = PACING_BACKOFF * attempt
                logger.warning(
                    f"  Pacing violation ({attempt}/{max_retries}) "
                    f"pour {symbol} {bar_size} @ {end_str}, attente {wait}s"
                )
                time.sleep(wait)
            elif "no market data" in err or "hmds query" in err:
                # Pas de data pour ce chunk (weekend/ferie complet)
                logger.debug(f"  {symbol} {bar_size}: no data @ {end_str}")
                return pd.DataFrame()
            else:
                logger.error(f"  {symbol} chunk error ({attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(5)

    return None


def load_existing(filepath: Path) -> pd.DataFrame:
    """Charge parquet existant ou retourne DataFrame vide."""
    if filepath.exists():
        try:
            df = pd.read_parquet(filepath)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df
        except Exception as e:
            logger.warning(f"Impossible de charger {filepath}: {e}")
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def save_parquet(df: pd.DataFrame, filepath: Path) -> None:
    """Sauvegarde atomique : write to tmp puis rename."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(".tmp.parquet")
    df.to_parquet(tmp)
    tmp.replace(filepath)


def merge_dedupe(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge 2 DataFrames, dedupe sur index, sort."""
    if existing.empty:
        return new
    if new.empty:
        return existing
    combined = pd.concat([existing, new])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return combined


def download_symbol_tf(
    loader: IBKRDataLoader,
    symbol: str,
    tf_label: str,
    start_date: str,
    end_date: str | None,
) -> dict:
    """Download tous les chunks pour (symbol, timeframe).

    Resume-able : merge avec l'existant et skip les chunks deja couverts.

    Returns:
        {status, bars_added, bars_total, chunks_ok, chunks_fail}
    """
    bar_size, chunk_days, duration_str = TIMEFRAMES[tf_label]
    filepath = OUTPUT_DIR / f"{symbol}_{tf_label}.parquet"

    existing = load_existing(filepath)
    logger.info(
        f"  {symbol} {tf_label}: existant={len(existing)} barres"
        + (f" ({existing.index.min()} -> {existing.index.max()})" if not existing.empty else "")
    )

    chunks = chunked_date_ranges(start_date, end_date, chunk_days)
    logger.info(f"  {symbol} {tf_label}: {len(chunks)} chunks de {chunk_days}j")

    chunks_ok = 0
    chunks_fail = 0
    bars_added = 0

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        # Skip si deja couvert (bars presents dans la fenetre)
        if not existing.empty:
            window_bars = existing[
                (existing.index >= chunk_start) & (existing.index < chunk_end)
            ]
            # Heuristique : si > 50% de la capacite attendue, skip
            if tf_label == "5M":
                expected_min = chunk_days * 50  # ~50 bars/jour RTH
            else:
                expected_min = chunk_days * 20
            if len(window_bars) >= expected_min * 0.5:
                logger.debug(
                    f"  [{i}/{len(chunks)}] {symbol} {tf_label} {chunk_start.date()} "
                    f"-> {chunk_end.date()}: SKIP ({len(window_bars)} barres deja)"
                )
                continue

        # Download
        logger.info(
            f"  [{i}/{len(chunks)}] {symbol} {tf_label} "
            f"end={chunk_end.strftime('%Y-%m-%d')} duration={duration_str}"
        )
        df_chunk = download_chunk(loader, symbol, bar_size, duration_str, chunk_end)

        if df_chunk is None:
            chunks_fail += 1
            logger.warning(f"    FAIL (apres retries)")
        elif df_chunk.empty:
            chunks_ok += 1
            logger.debug(f"    Empty")
        else:
            # Filtrer aux dates du chunk pour eviter doublons
            df_chunk = df_chunk[
                (df_chunk.index >= chunk_start) & (df_chunk.index < chunk_end)
            ]
            chunks_ok += 1
            before = len(existing)
            existing = merge_dedupe(existing, df_chunk)
            delta = len(existing) - before
            bars_added += delta
            logger.info(
                f"    +{len(df_chunk)} new / +{delta} net -> total {len(existing)}"
            )

            # Checkpoint : sauvegarde apres chaque chunk reussi
            save_parquet(existing, filepath)

        # Rate limit
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    # Sauvegarde finale
    save_parquet(existing, filepath)

    return {
        "symbol": symbol,
        "tf": tf_label,
        "status": "ok",
        "bars_total": len(existing),
        "bars_added": bars_added,
        "chunks_ok": chunks_ok,
        "chunks_fail": chunks_fail,
        "file": str(filepath),
    }


def main():
    parser = argparse.ArgumentParser(description="Download EU indices intraday via IBKR")
    parser.add_argument("--symbol", "-s", help="Symbole unique (ex. DAX)")
    parser.add_argument("--tf", "-t", choices=["5M", "15M"], help="Timeframe unique")
    parser.add_argument("--start", default=DEFAULT_START, help=f"Date debut (defaut {DEFAULT_START})")
    parser.add_argument("--end", default=None, help="Date fin (defaut: aujourd'hui)")
    parser.add_argument("--host", default=IBKR_HOST, help=f"IBKR host (defaut {IBKR_HOST})")
    parser.add_argument("--port", type=int, default=IBKR_PORT, help=f"IBKR port (defaut {IBKR_PORT})")
    parser.add_argument("--client-id", type=int, default=CLIENT_ID, help=f"Client ID (defaut {CLIENT_ID})")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    setup_logging(args.verbose)

    symbols = [args.symbol.upper()] if args.symbol else SYMBOLS
    timeframes = [args.tf] if args.tf else list(TIMEFRAMES.keys())

    logger.info("=" * 70)
    logger.info("  DOWNLOAD EU INTRADAY IBKR")
    logger.info("=" * 70)
    logger.info(f"  Host:     {args.host}:{args.port}")
    logger.info(f"  ClientID: {args.client_id}")
    logger.info(f"  Period:   {args.start} -> {args.end or 'now'}")
    logger.info(f"  Symbols:  {symbols}")
    logger.info(f"  TFs:      {timeframes}")
    logger.info(f"  Output:   {OUTPUT_DIR}")
    logger.info("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    loader = IBKRDataLoader(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        timeout=60,
    )

    t0 = time.time()
    results = []

    try:
        loader.connect()
        logger.info("Connecte a IBKR")

        for symbol in symbols:
            for tf in timeframes:
                logger.info("")
                logger.info(f"--- {symbol} / {tf} ---")
                try:
                    res = download_symbol_tf(
                        loader, symbol, tf, args.start, args.end
                    )
                    results.append(res)
                except Exception as e:
                    logger.error(f"FATAL pour {symbol}/{tf}: {e}", exc_info=True)
                    results.append({
                        "symbol": symbol, "tf": tf,
                        "status": "fatal", "error": str(e),
                    })

    finally:
        try:
            loader.disconnect()
            logger.info("Deconnecte de IBKR")
        except Exception:
            pass

    # Resume
    elapsed = time.time() - t0
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"  RESUME ({elapsed/60:.1f} min)")
    logger.info("=" * 70)
    for r in results:
        if r.get("status") == "ok":
            logger.info(
                f"  {r['symbol']:<10} {r['tf']:<4} "
                f"total={r['bars_total']:>7}  +{r['bars_added']:>6}  "
                f"chunks {r['chunks_ok']}/{r['chunks_ok']+r['chunks_fail']}"
            )
        else:
            logger.error(f"  {r['symbol']:<10} {r['tf']:<4} FATAL: {r.get('error', '?')}")

    total_bars = sum(r.get("bars_total", 0) for r in results if r.get("status") == "ok")
    logger.info(f"  TOTAL: {total_bars:,} barres telechargees")


if __name__ == "__main__":
    main()
