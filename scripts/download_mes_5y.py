"""Download MES 5min historical bars 5 ans via IBKR ContFuture (continuous).

ContFuture suit automatiquement le front month et applique le back-adjust.
Plus robuste que de qualifier un contract specifique.

Connexion : Hetzner paper 4003 ou local 127.0.0.1, clientId 106.
Output : data/futures/MES_5M_5Y.parquet

Usage : python scripts/download_mes_5y.py [--host 127.0.0.1] [--port 4003]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Python 3.14 compat
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

OUT_FILE = ROOT / "data" / "futures" / "MES_5M_5Y.parquet"
DEFAULT_START = "2021-01-01"
SLEEP_BETWEEN = 11.0


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] - %(message)s",
        datefmt="%H:%M:%S",
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4003)
    p.add_argument("--client-id", type=int, default=106)
    p.add_argument("--start", default=DEFAULT_START)
    args = p.parse_args()

    setup_logging()

    from ib_insync import IB, ContFuture, Future

    ib = IB()
    logger.info(f"Connecting to {args.host}:{args.port} clientId={args.client_id}")
    ib.connect(args.host, args.port, clientId=args.client_id, timeout=60)

    # Step 1 : qualifier ContFuture pour avoir le conId du front month courant
    cont = ContFuture(symbol="MES", exchange="CME", currency="USD")
    qualified_cont = ib.qualifyContracts(cont)
    if not qualified_cont:
        logger.error("Cannot qualify ContFuture MES")
        ib.disconnect()
        return

    cont_qf = qualified_cont[0]
    logger.info(f"ContFuture qualified: {cont_qf}")

    # Step 2 : creer un Future SPECIFIQUE avec le conId pour permettre endDateTime
    # IBKR fait le back-adjust historique automatiquement sur ce contract
    cont = Future(
        symbol="MES",
        exchange="CME",
        currency="USD",
        lastTradeDateOrContractMonth=cont_qf.lastTradeDateOrContractMonth,
        multiplier=cont_qf.multiplier,
        localSymbol=cont_qf.localSymbol,
    )
    qualified = ib.qualifyContracts(cont)
    if not qualified:
        logger.error("Cannot qualify Future MES")
        ib.disconnect()
        return
    cont = qualified[0]
    logger.info(f"Future for historical: {cont}")

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end_dt = datetime.now(timezone.utc)

    # Charger l'existant si present
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if OUT_FILE.exists():
        existing = pd.read_parquet(OUT_FILE)
        if existing.index.tz is None:
            existing.index = existing.index.tz_localize("UTC")
        logger.info(f"Existing: {len(existing)} bars ({existing.index.min()} -> {existing.index.max()})")
    else:
        existing = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    all_new = []
    current_end = end_dt
    chunk_count = 0
    total_chunks = (end_dt - start_dt).days // 7 + 1

    while current_end > start_dt:
        end_str = current_end.strftime("%Y%m%d %H:%M:%S UTC")
        chunk_count += 1
        logger.info(f"[{chunk_count}/{total_chunks}] Fetching MES 5M ending {end_str}")

        try:
            bars = ib.reqHistoricalData(
                cont,
                endDateTime=end_str,
                durationStr="7 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
            )
        except Exception as e:
            logger.error(f"  Error: {e}")
            time.sleep(15)
            current_end -= timedelta(days=7)
            continue

        if bars:
            for b in bars:
                all_new.append({
                    "datetime": b.date,
                    "open": float(b.open), "high": float(b.high),
                    "low": float(b.low), "close": float(b.close),
                    "volume": int(b.volume) if b.volume >= 0 else 0,
                })
            logger.info(f"  +{len(bars)} bars")
        else:
            logger.info(f"  empty")

        # Checkpoint every 20 chunks
        if chunk_count % 20 == 0 and all_new:
            df_new = pd.DataFrame(all_new)
            df_new["datetime"] = pd.to_datetime(df_new["datetime"])
            df_new = df_new.set_index("datetime").sort_index()
            if df_new.index.tz is None:
                df_new.index = df_new.index.tz_localize("UTC")
            combined = pd.concat([existing, df_new])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            combined.to_parquet(OUT_FILE)
            logger.info(f"  CHECKPOINT: {len(combined)} bars saved")

        time.sleep(SLEEP_BETWEEN)
        current_end -= timedelta(days=7)

    # Final save
    if all_new:
        df_new = pd.DataFrame(all_new)
        df_new["datetime"] = pd.to_datetime(df_new["datetime"])
        df_new = df_new.set_index("datetime").sort_index()
        if df_new.index.tz is None:
            df_new.index = df_new.index.tz_localize("UTC")
        combined = pd.concat([existing, df_new])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_parquet(OUT_FILE)
        logger.info(f"DONE: {len(combined)} bars total | range {combined.index.min()} -> {combined.index.max()}")
    else:
        logger.warning("No new bars downloaded")

    ib.disconnect()


if __name__ == "__main__":
    main()
