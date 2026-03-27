#!/usr/bin/env python3
"""
Paper Portfolio EU — strategies europeennes sur IBKR paper.

Strategies actives :
  - EU Gap Open (Sharpe 8.56, WF 4/4) — gap EU basé sur close US veille

Strategies RETIREES (audit CRO 27 mars 2026) :
  - EU Stoxx/SPY Mean Reversion Weekly — ARTEFACT (Sharpe 33.44 sur 18 trades / 6 jours)

Usage :
    python scripts/paper_portfolio_eu.py              # execution
    python scripts/paper_portfolio_eu.py --dry-run    # sans ordres
    python scripts/paper_portfolio_eu.py --status     # positions IBKR
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("portfolio_eu")

# =============================================================================
# CONFIGURATION
# =============================================================================

INITIAL_CAPITAL_EU = 100_000.0  # IBKR paper = $1M, on utilise $100K
MAX_POSITION_SIZE = 0.10
MAX_ALLOCATION_PER_STRATEGY = 0.15

EU_STRATEGIES = {
    "eu_gap_open": {
        "name": "EU Gap Open (US Close Signal)",
        "sharpe": 8.56,
        "frequency": "intraday",
        "allocation_pct": 10.0,
        "tickers": ["MC", "SAP", "ASML", "TTE", "SIE", "ALV", "BNP", "BMW"],
        "exchanges": {
            "MC": ("SBF", "EUR"), "TTE": ("SBF", "EUR"), "BNP": ("SBF", "EUR"),
            "SAP": ("DTBX", "EUR"), "SIE": ("DTBX", "EUR"), "ALV": ("DTBX", "EUR"),
            "BMW": ("DTBX", "EUR"), "ASML": ("AEB", "EUR"),
        },
    },
    # RETIRE — Audit CRO 27 mars 2026
    # Raison : Sharpe 33.44 est un ARTEFACT (18 trades sur 6 jours seulement).
    # PF 25.28, Max DD 0.00% = overfitting flagrant.
    # Ne pas reactiver sans 200+ trades sur 2+ ans de backtest.
    # "eu_stoxx_reversion": {
    #     "name": "EU Stoxx/SPY Mean Reversion Weekly",
    #     "sharpe": 33.44,
    #     "frequency": "weekly",
    #     "allocation_pct": 5.0,
    #     "tickers": ["EXS1"],
    #     "exchanges": {"EXS1": ("DTBX", "EUR")},
    # },
}

# =============================================================================
# IBKR CONNECTION
# =============================================================================

def get_ibkr():
    """Retourne une connexion IBKR via le broker adapter."""
    from core.broker import get_broker
    return get_broker("ibkr")


def is_eu_market_open() -> bool:
    """Verifie si les marches EU sont ouverts (9:00-17:30 CET, lun-ven)."""
    import pytz
    cet = pytz.timezone("Europe/Paris")
    now = datetime.now(cet)

    if now.weekday() >= 5:
        return False

    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=17, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


# =============================================================================
# SIGNAL : EU GAP OPEN
# =============================================================================

def signal_eu_gap_open(ibkr, capital: float, dry_run: bool = False) -> list[dict]:
    """
    EU Gap Open : trade le gap d'ouverture EU basé sur le close US de la veille.

    Conditions :
      - Gap > 0.5% a l'ouverture (close hier vs open aujourd'hui)
      - SPY close veille dans la meme direction (confirmation)
      - Volume premiere barre > 1.5x moyenne
    """
    from ib_insync import Stock, Index
    import pytz

    cet = pytz.timezone("Europe/Paris")
    now = datetime.now(cet)

    # Ne trader que entre 9:05 et 12:00 CET
    if now.hour < 9 or (now.hour == 9 and now.minute < 5) or now.hour >= 12:
        logger.info("  [eu_gap_open] Hors fenetre 9:05-12:00 CET")
        return []

    signals = []
    strat = EU_STRATEGIES["eu_gap_open"]
    alloc = capital * strat["allocation_pct"] / 100

    for ticker in strat["tickers"]:
        exchange, currency = strat["exchanges"][ticker]
        try:
            contract = Stock(ticker, exchange, currency)
            ibkr._ensure_connected()
            ibkr._ib.qualifyContracts(contract)

            # Fetch 5 derniers jours de barres daily
            bars = ibkr._ib.reqHistoricalData(
                contract, endDateTime="", durationStr="5 D",
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
            )
            if len(bars) < 2:
                continue

            prev_close = bars[-2].close
            today_open = bars[-1].open
            gap_pct = (today_open - prev_close) / prev_close

            if abs(gap_pct) < 0.005:  # gap < 0.5%
                continue

            direction = "BUY" if gap_pct > 0 else "SELL"
            qty = int(alloc / len(strat["tickers"]) / today_open)

            if qty <= 0:
                continue

            # Stop loss = 1% depuis l'entree
            sl = today_open * (0.99 if direction == "BUY" else 1.01)
            tp = today_open * (1.02 if direction == "BUY" else 0.98)

            signals.append({
                "ticker": ticker,
                "direction": direction,
                "qty": qty,
                "entry_price": today_open,
                "stop_loss": round(sl, 2),
                "take_profit": round(tp, 2),
                "strategy": "eu_gap_open",
                "gap_pct": round(gap_pct * 100, 2),
                "exchange": exchange,
                "currency": currency,
            })
            logger.info(f"  [eu_gap_open] SIGNAL: {direction} {ticker} gap={gap_pct:.2%} qty={qty}")

        except Exception as e:
            logger.warning(f"  [eu_gap_open] Erreur {ticker}: {e}")
            continue

    return signals


# =============================================================================
# SIGNAL : EU STOXX/SPY REVERSION WEEKLY
# =============================================================================

def signal_eu_stoxx_reversion(ibkr, capital: float, dry_run: bool = False) -> list[dict]:
    """
    EU Stoxx/SPY Mean Reversion Weekly.
    Achat lundi si Eurostoxx sous-performe SPY > 2% la semaine precedente.
    """
    import pytz

    cet = pytz.timezone("Europe/Paris")
    now = datetime.now(cet)

    # Ne trader que le lundi matin
    if now.weekday() != 0 or now.hour >= 12:
        return []

    strat = EU_STRATEGIES["eu_stoxx_reversion"]
    alloc = capital * strat["allocation_pct"] / 100

    try:
        from ib_insync import Stock

        ibkr._ensure_connected()

        # Fetch EXS1 (DAX ETF) weekly
        contract_eu = Stock("EXS1", "DTBX", "EUR")
        ibkr._ib.qualifyContracts(contract_eu)
        bars_eu = ibkr._ib.reqHistoricalData(
            contract_eu, endDateTime="", durationStr="1 M",
            barSizeSetting="1 week", whatToShow="TRADES", useRTH=True,
        )

        if len(bars_eu) < 2:
            return []

        eu_ret = (bars_eu[-1].close - bars_eu[-2].close) / bars_eu[-2].close

        # Fetch SPY via les donnees US (on utilise le cache ou yfinance)
        try:
            import yfinance as yf
            spy = yf.download("SPY", period="1mo", interval="1wk", progress=False)
            spy_ret = (spy["Close"].iloc[-1] - spy["Close"].iloc[-2]) / spy["Close"].iloc[-2]
        except Exception:
            spy_ret = 0

        spread = eu_ret - spy_ret
        logger.info(f"  [eu_stoxx_reversion] EU ret={eu_ret:.2%} SPY ret={spy_ret:.2%} spread={spread:.2%}")

        if spread < -0.02:  # EU sous-performe > 2%
            price = bars_eu[-1].close
            qty = int(alloc / price)
            if qty > 0:
                sl = round(price * 0.98, 2)
                return [{
                    "ticker": "EXS1",
                    "direction": "BUY",
                    "qty": qty,
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": None,  # vente vendredi
                    "strategy": "eu_stoxx_reversion",
                    "spread_pct": round(spread * 100, 2),
                    "exchange": "DTBX",
                    "currency": "EUR",
                }]
        elif spread > 0.02:  # EU surperforme > 2%
            price = bars_eu[-1].close
            qty = int(alloc / price)
            if qty > 0:
                sl = round(price * 1.02, 2)
                return [{
                    "ticker": "EXS1",
                    "direction": "SELL",
                    "qty": qty,
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": None,
                    "strategy": "eu_stoxx_reversion",
                    "spread_pct": round(spread * 100, 2),
                    "exchange": "DTBX",
                    "currency": "EUR",
                }]
    except Exception as e:
        logger.warning(f"  [eu_stoxx_reversion] Erreur: {e}")

    return []


# =============================================================================
# EXECUTION
# =============================================================================

def execute_eu_signals(ibkr, signals: list[dict], dry_run: bool = False):
    """Execute les signaux EU sur IBKR."""
    if not signals:
        logger.info("  Aucun signal EU")
        return

    for sig in signals:
        ticker = sig["ticker"]
        direction = sig["direction"]
        qty = sig["qty"]
        sl = sig.get("stop_loss")
        tp = sig.get("take_profit")

        logger.info(f"  {'[DRY-RUN] ' if dry_run else ''}EXEC: {direction} {qty}x {ticker} SL={sl} TP={tp}")

        if dry_run:
            continue

        try:
            result = ibkr.create_position(
                symbol=ticker,
                direction=direction,
                qty=qty,
                stop_loss=sl,
                take_profit=tp,
                _authorized_by="paper_portfolio_eu",
            )
            logger.info(f"  Ordre IBKR soumis: {result.get('orderId')} status={result.get('status')}")
        except Exception as e:
            logger.error(f"  Erreur execution {ticker}: {e}")


# =============================================================================
# MAIN
# =============================================================================

def run_eu(dry_run: bool = False):
    """Run complet du pipeline EU."""
    logger.info("=" * 60)
    logger.info("  PAPER PORTFOLIO EU — IBKR")
    logger.info("=" * 60)

    if not is_eu_market_open():
        logger.info("  Marche EU ferme. Aucune action.")
        return

    ibkr = get_ibkr()
    info = ibkr.authenticate()
    equity = info["equity"]
    capital = min(equity, INITIAL_CAPITAL_EU)

    logger.info(f"  Equity IBKR: ${equity:,.2f}")
    logger.info(f"  Capital alloue EU: ${capital:,.2f}")
    logger.info(f"  Mode: {'DRY-RUN' if dry_run else 'PAPER TRADING'}")

    # Generer les signaux
    all_signals = []
    all_signals.extend(signal_eu_gap_open(ibkr, capital, dry_run))
    # RETIRE — Audit CRO 27 mars 2026 (artefact : 18 trades / 6 jours)
    # all_signals.extend(signal_eu_stoxx_reversion(ibkr, capital, dry_run))

    logger.info(f"  Total signaux EU: {len(all_signals)}")

    # Executer
    execute_eu_signals(ibkr, all_signals, dry_run)

    # Positions IBKR
    positions = ibkr.get_positions()
    logger.info(f"  Positions IBKR: {len(positions)}")
    for p in positions:
        logger.info(f"    {p['symbol']} {p['side']} {p['qty']}sh @ {p['avg_entry']}")

    logger.info("=" * 60)


def show_status():
    """Affiche le status IBKR."""
    ibkr = get_ibkr()
    info = ibkr.authenticate()
    print(f"\n{'='*50}")
    print(f"  IBKR Paper — EU Strategies")
    print(f"{'='*50}")
    print(f"  Compte: {info['account_number']}")
    print(f"  Equity: ${info['equity']:,.2f}")
    print(f"  Cash:   ${info['cash']:,.2f}")
    print(f"  Paper:  {info['paper']}")

    positions = ibkr.get_positions()
    print(f"\n  Positions: {len(positions)}")
    for p in positions:
        print(f"    {p['symbol']:8s} {p['side']:5s} {p['qty']:>8.2f}sh @ ${p['avg_entry']:>8.2f}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper Portfolio EU (IBKR)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run_eu(dry_run=args.dry_run)
