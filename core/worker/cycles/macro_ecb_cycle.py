"""Macro ECB cycle runner — event-driven futures execution.

Trigger : ECB Governing Council meeting days, ~14:50 CET (35min after
14:15 announcement). Fetches the 30-min move on DAX/CAC40/ESTX50 indices
and emits BUY/SELL futures signals if the absolute move exceeds 0.15%.

Usage from worker.py :

    from core.worker.cycles.macro_ecb_cycle import run_macro_ecb_cycle

    # In scheduler loop, ECB days only :
    if is_bce_day(today) and now_paris.hour == 14 and now_paris.minute == 50:
        if not getattr(run_macro_ecb_cycle, '_done_today', False):
            _runners["macro_ecb"].run()
            run_macro_ecb_cycle._done_today = True
    if now_paris.hour < 14:
        run_macro_ecb_cycle._done_today = False

This cycle is :
  - Idempotent : will not fire twice the same day
  - Safe : skips entirely on non-ECB days
  - Conservative : SL/TP attached to every order, max 1 contract per
    instrument (risk_engineer will validate sizing).

Connection : IBKR live port (4002), clientId range 100-109 (no conflict
with futures 70-79, fx 80-89, sl_backup 90-98).

Backtest 2021-2026 (5 years), portfolio context :
  3 instruments (DAX/CAC40/ESTX50), 39 trades, +$2,886
  Sharpe 1.00 portfolio (vs 0.83 baseline), MaxDD +4%
  ROC 22.8% -> 31.7%/an (+8.9pts)
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")
UTC_TZ = ZoneInfo("UTC")

# IBKR live indices contract specs
EU_INDEX_SPECS = {
    "DAX":    {"sec_type": "IND", "exchange": "EUREX", "currency": "EUR"},
    "CAC40":  {"sec_type": "IND", "exchange": "MONEP", "currency": "EUR"},
    "ESTX50": {"sec_type": "IND", "exchange": "EUREX", "currency": "EUR"},
}

# Futures contracts to actually trade (we trade the future, not the index)
INDEX_TO_FUTURE = {
    "DAX":    "FDXM",   # DAX mini future
    "CAC40":  "FCE",    # CAC40 future
    "ESTX50": "FESX",   # Euro Stoxx 50 future
}


def is_bce_day(d: date) -> bool:
    """Return True if d is a date in the ECB calendar."""
    from strategies_v2.futures.macro_ecb import get_bce_dates
    return d in get_bce_dates()


def fetch_intraday_eu_bars(
    ib,
    symbol: str,
    n_bars: int = 12,
    bar_size: str = "5 mins",
):
    """Fetch the last n_bars of intraday data for an EU index via IBKR.

    Returns a pandas DataFrame with OHLCV index in UTC, or None on failure.
    """
    import pandas as pd
    from ib_insync import Index

    spec = EU_INDEX_SPECS.get(symbol)
    if not spec:
        logger.error(f"Unknown EU symbol: {symbol}")
        return None

    contract = Index(symbol=symbol, exchange=spec["exchange"], currency=spec["currency"])
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        logger.error(f"Failed to qualify {symbol}")
        return None
    contract = qualified[0]

    # Request ~1h of 5min bars (12 bars)
    duration = f"{max(3600, n_bars * 300)} S"
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
        )
    except Exception as e:
        logger.error(f"Historical data error for {symbol}: {e}")
        return None

    if not bars:
        return None

    rows = [
        {
            "datetime": b.date,
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close),
            "volume": int(b.volume) if b.volume >= 0 else 0,
        }
        for b in bars
    ]
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def run_macro_ecb_cycle(
    *,
    ibkr_host: str | None = None,
    ibkr_port: int | None = None,
    dry_run: bool = True,
    futures_executor: Any = None,
) -> dict:
    """Run one ECB macro cycle.

    Args :
        ibkr_host : IBKR Gateway host (default: env IBKR_HOST or 127.0.0.1)
        ibkr_port : IBKR Gateway port (default: env IBKR_PORT or 4002)
        dry_run : if True, only compute signals and log them, do NOT send orders
        futures_executor : callable(signal) -> bool, used when dry_run=False

    Returns :
        dict with keys :
            - is_ecb_day : bool
            - signals : list of (symbol, side, sl, tp, strength) tuples
            - sent_orders : list of executed signals (only when dry_run=False)
            - skipped : reason if cycle was skipped
    """
    result: dict = {"is_ecb_day": False, "signals": [], "sent_orders": [], "skipped": None}

    today_paris = datetime.now(PARIS_TZ).date()
    if not is_bce_day(today_paris):
        result["skipped"] = "not_an_ecb_day"
        logger.info(f"  MACRO ECB SKIP — {today_paris} is not in BCE calendar")
        return result

    result["is_ecb_day"] = True

    host = ibkr_host or os.environ.get("IBKR_HOST", "127.0.0.1")
    port = ibkr_port or int(os.environ.get("IBKR_PORT", "4002"))
    client_id = random.randint(100, 109)

    logger.info(f"  MACRO ECB CYCLE — {today_paris} (ECB day) | {host}:{port} clientId={client_id}")

    # Connect to IBKR
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(host, port, clientId=client_id, timeout=10)
        import time as _t; _t.sleep(2)
    except Exception as e:
        logger.error(f"  MACRO ECB connect failed: {e}")
        result["skipped"] = f"connect_failed: {e}"
        return result

    try:
        from core.backtester_v2.data_feed import DataFeed
        from core.backtester_v2.types import Bar, PortfolioState
        from strategies_v2.futures.macro_ecb import MacroECB

        # Get current equity for PortfolioState
        equity = 0.0
        try:
            for a in ib.accountSummary():
                if a.tag == "NetLiquidation":
                    equity = float(a.value)
                    break
        except Exception:
            pass

        portfolio_state = PortfolioState(equity=max(equity, 1.0), cash=max(equity, 1.0))

        # For each instrument, fetch bars and run strategy
        for sym in ["DAX", "CAC40", "ESTX50"]:
            logger.info(f"    [{sym}] Fetching 5min bars...")
            df = fetch_intraday_eu_bars(ib, sym, n_bars=15)
            if df is None or df.empty:
                logger.warning(f"    [{sym}] No data, skipping")
                continue

            logger.info(f"    [{sym}] {len(df)} bars (latest {df.index[-1]})")

            # Setup DataFeed with single symbol
            feed = DataFeed({sym: df})
            feed.set_timestamp(df.index[-1])

            # Run strategy
            strat = MacroECB(symbol=sym)
            strat.set_data_feed(feed)

            last_bar = df.iloc[-1]
            bar = Bar(
                symbol=sym,
                timestamp=df.index[-1],
                open=float(last_bar["open"]),
                high=float(last_bar["high"]),
                low=float(last_bar["low"]),
                close=float(last_bar["close"]),
                volume=float(last_bar.get("volume", 0)),
            )

            sig = strat.on_bar(bar, portfolio_state)
            if sig is not None:
                future_sym = INDEX_TO_FUTURE[sym]
                logger.info(
                    f"    [{sym}] SIGNAL: {sig.side} @ {bar.close:.2f} "
                    f"SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f} "
                    f"strength={sig.strength:.2f} | future={future_sym}"
                )
                result["signals"].append({
                    "index_symbol": sym,
                    "future_symbol": future_sym,
                    "side": sig.side,
                    "entry_price": bar.close,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "strength": sig.strength,
                })

                if not dry_run and futures_executor is not None:
                    try:
                        ok = futures_executor(sig)
                        if ok:
                            result["sent_orders"].append(sig)
                            logger.info(f"    [{sym}] Order sent")
                        else:
                            logger.warning(f"    [{sym}] Order rejected by executor")
                    except Exception as e:
                        logger.error(f"    [{sym}] Order error: {e}")
                elif dry_run:
                    logger.info(f"    [{sym}] DRY-RUN: not sending order")
            else:
                logger.info(f"    [{sym}] No signal (move below threshold or wrong window)")

    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    return result
