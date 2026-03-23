#!/usr/bin/env python3
"""
Paper Trading Runner — Momentum Rotation mensuelle sur Alpaca.

Execute le rebalancement le 1er jour ouvrable de chaque mois :
  1. Calcule le ROC(N mois) sur l'univers ETF
  2. Classe par momentum, selectionne les top K
  3. Applique le crash filter (SPY < SMA(200) -> tout cash)
  4. Rebalance les positions via Alpaca (paper trading)
  5. Log tout dans paper_momentum_state.json

Usage :
    # Rebalancement (a executer le 1er du mois ou via cron)
    python scripts/paper_momentum.py

    # Dry-run (calcule sans passer d'ordres)
    python scripts/paper_momentum.py --dry-run

    # Status des positions
    python scripts/paper_momentum.py --status

    # Forcer le rebalancement meme si pas le 1er du mois
    python scripts/paper_momentum.py --force

Config :
    ALPACA_API_KEY, ALPACA_SECRET_KEY dans .env
    PAPER_TRADING=true (obligatoire)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "paper_momentum_state.json"

# ─── Configuration ────────────────────────────────────────────────────────────

ETF_UNIVERSE = [
    "SPY", "QQQ", "IWM", "EFA", "EEM",
    "TLT", "IEF", "GLD",
    "XLE", "XLF", "XLK", "XLV", "VNQ",
]

BENCHMARK = "SPY"
LOOKBACK_MONTHS = 3
TOP_N = 2
CRASH_FILTER_SMA = 200  # jours
POSITION_PCT = 1.0 / TOP_N  # equal weight


def load_state() -> dict:
    """Charge l'etat du paper trading."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "positions": {},
        "cash": 0.0,
        "total_equity": 0.0,
        "last_rebalance": None,
        "history": [],
    }


def save_state(state: dict) -> None:
    """Sauvegarde l'etat."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)
    logger.info(f"State sauvegarde: {STATE_FILE}")


def get_momentum_ranking(lookback: int = LOOKBACK_MONTHS) -> dict[str, float]:
    """Calcule le ROC sur N mois pour chaque ETF et retourne le ranking."""
    from core.data.loader import OHLCVLoader

    scores = {}
    for ticker in ETF_UNIVERSE:
        try:
            data = OHLCVLoader.from_yfinance(ticker, "1D", period="2y")
            close = data.df["close"]
            # ROC sur N mois (~21 jours de trading par mois)
            n_bars = lookback * 21
            if len(close) < n_bars + 5:
                continue
            roc = close.iloc[-1] / close.iloc[-n_bars] - 1
            scores[ticker] = float(roc)
        except Exception as e:
            logger.warning(f"  {ticker}: erreur ({e})")

    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


def check_crash_filter() -> bool:
    """Retourne True si le crash filter est actif (SPY < SMA(200))."""
    from core.data.loader import OHLCVLoader

    try:
        data = OHLCVLoader.from_yfinance(BENCHMARK, "1D", period="2y")
        close = data.df["close"]
        sma = close.rolling(CRASH_FILTER_SMA).mean()
        current = close.iloc[-1]
        sma_val = sma.iloc[-1]
        is_crash = current < sma_val
        logger.info(
            f"  Crash filter: SPY={current:.2f} vs SMA({CRASH_FILTER_SMA})={sma_val:.2f} "
            f"-> {'CASH (crash)' if is_crash else 'OK (normal)'}"
        )
        return is_crash
    except Exception:
        return False  # En cas d'erreur, pas de crash filter


def rebalance_alpaca(target_holdings: list[str], dry_run: bool = False) -> dict:
    """Rebalance les positions Alpaca vers les target_holdings (equal weight)."""
    from core.alpaca_client.client import AlpacaClient

    client = AlpacaClient.from_env()
    account = client.authenticate()
    equity = account["equity"]
    current_positions = client.get_positions()

    logger.info(f"  Compte: equity=${equity:,.0f}, cash=${account['cash']:,.0f}")

    # Positions actuelles
    current_symbols = {p["symbol"] for p in current_positions}
    target_set = set(target_holdings)

    # Determiner les actions
    to_sell = current_symbols - target_set
    to_buy = target_set - current_symbols
    to_keep = current_symbols & target_set

    logger.info(f"  Vendre: {to_sell or 'rien'}")
    logger.info(f"  Acheter: {to_buy or 'rien'}")
    logger.info(f"  Garder: {to_keep or 'rien'}")

    orders = []

    if dry_run:
        logger.info("  [DRY-RUN] Aucun ordre execute")
        return {"orders": [], "dry_run": True}

    # 1. Vendre les positions a fermer
    for symbol in to_sell:
        try:
            result = client.close_position(symbol)
            orders.append({"action": "sell", "symbol": symbol, "result": result})
            logger.info(f"  VENDU {symbol}: {result}")
        except Exception as e:
            logger.error(f"  Erreur vente {symbol}: {e}")

    # 2. Recalculer le cash disponible apres ventes
    # (attendre un peu pour que les ordres se remplissent)
    import time
    if to_sell:
        time.sleep(2)

    try:
        account = client.authenticate()
        available_cash = account["buying_power"]
    except Exception:
        available_cash = equity

    # 3. Acheter les nouvelles positions (equal weight)
    notional_per_position = available_cash * POSITION_PCT * 0.98  # 2% marge securite

    for symbol in to_buy:
        try:
            result = client.create_position(
                symbol=symbol,
                direction="BUY",
                notional=round(notional_per_position, 2),
            )
            orders.append({"action": "buy", "symbol": symbol,
                          "notional": notional_per_position, "result": result})
            logger.info(f"  ACHETE {symbol}: ${notional_per_position:,.0f} -> {result}")
        except Exception as e:
            logger.error(f"  Erreur achat {symbol}: {e}")

    return {"orders": orders, "dry_run": False}


def show_status():
    """Affiche le status actuel."""
    state = load_state()

    print(f"\n{'='*60}")
    print(f"  MOMENTUM ROTATION — STATUS PAPER TRADING")
    print(f"{'='*60}")
    print(f"  Dernier rebalancement: {state.get('last_rebalance', 'jamais')}")
    print(f"  Positions: {state.get('positions', {})}")

    # Positions Alpaca
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        account = client.authenticate()
        positions = client.get_positions()

        print(f"\n  Compte Alpaca ({('PAPER' if account.get('paper') else 'LIVE')}):")
        print(f"    Equity: ${account['equity']:,.2f}")
        print(f"    Cash: ${account['cash']:,.2f}")

        if positions:
            print(f"\n  Positions ouvertes:")
            total_pnl = 0
            for p in positions:
                pnl = p["unrealized_pl"]
                total_pnl += pnl
                print(f"    {p['symbol']:<6} qty={p['qty']:>8} "
                      f"avg=${p['avg_entry']:>8.2f} "
                      f"val=${p['market_val']:>10.2f} "
                      f"P&L={pnl:>+10.2f}")
            print(f"    {'':6} {'':>8} {'':>8} {'TOTAL':>10} {total_pnl:>+10.2f}")
        else:
            print(f"\n  Aucune position ouverte")
    except Exception as e:
        print(f"\n  Impossible de se connecter a Alpaca: {e}")

    # Historique
    history = state.get("history", [])
    if history:
        print(f"\n  Historique ({len(history)} rebalancements):")
        for h in history[-5:]:
            print(f"    {h.get('date', '?')}: {h.get('holdings', '?')} "
                  f"({h.get('action', '?')})")

    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Paper Trading Momentum Rotation (Alpaca)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Calculer sans passer d'ordres")
    parser.add_argument("--status", action="store_true",
                        help="Afficher le status actuel")
    parser.add_argument("--force", action="store_true",
                        help="Forcer le rebalancement")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    now = datetime.now(timezone.utc)
    state = load_state()

    # Verifier si c'est le moment de rebalancer
    last_reb = state.get("last_rebalance")
    if last_reb and not args.force:
        last_date = datetime.fromisoformat(last_reb)
        if last_date.month == now.month and last_date.year == now.year:
            logger.info(f"Deja rebalance ce mois-ci ({last_reb}). Utiliser --force pour forcer.")
            return

    print(f"\n{'='*60}")
    print(f"  MOMENTUM ROTATION — REBALANCEMENT MENSUEL")
    print(f"{'='*60}")
    print(f"  Date: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Univers: {len(ETF_UNIVERSE)} ETFs")
    print(f"  Lookback: {LOOKBACK_MONTHS} mois, Top {TOP_N}")
    print(f"  Mode: {'DRY-RUN' if args.dry_run else 'PAPER TRADING'}")
    print(f"{'='*60}")

    # 1. Crash filter
    crash = check_crash_filter()

    if crash:
        logger.info("  CRASH FILTER ACTIF -> liquidation de toutes les positions")
        target = []
    else:
        # 2. Calcul du ranking momentum
        logger.info("\n  Calcul du momentum...")
        ranking = get_momentum_ranking(LOOKBACK_MONTHS)

        print(f"\n  Ranking momentum (ROC {LOOKBACK_MONTHS}m):")
        for i, (ticker, score) in enumerate(ranking.items()):
            marker = " <-- TOP" if i < TOP_N else ""
            print(f"    {i+1:2d}. {ticker:<6} {score:+.2%}{marker}")

        target = list(ranking.keys())[:TOP_N]

    print(f"\n  Target holdings: {target or 'CASH (tout vendre)'}")

    # 3. Rebalancer (skip Alpaca en dry-run)
    if args.dry_run:
        result = {"orders": [], "dry_run": True}
        logger.info("  [DRY-RUN] Aucun ordre execute")
    else:
        result = rebalance_alpaca(target, dry_run=False)

    # 4. Sauvegarder l'etat
    state["last_rebalance"] = now.isoformat()
    state["positions"] = {t: POSITION_PCT for t in target}
    state["history"].append({
        "date": now.strftime("%Y-%m-%d"),
        "holdings": ", ".join(target) if target else "CASH",
        "action": "dry-run" if args.dry_run else "rebalanced",
        "crash_filter": crash,
        "ranking_top5": dict(list(ranking.items())[:5]) if not crash and 'ranking' in dir() else {},
    })
    save_state(state)

    print(f"\n  Rebalancement {'(dry-run) ' if args.dry_run else ''}termine.")
    print(f"  Prochain rebalancement: 1er jour ouvrable du mois prochain")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
