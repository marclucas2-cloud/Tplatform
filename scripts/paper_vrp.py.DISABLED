#!/usr/bin/env python3
"""
Paper Trading Runner — Volatility Risk Premium (SVXY/SPY/TLT).

Rotation mensuelle basee sur le regime de volatilite :
  - Vol haute et en baisse -> SVXY (collecter le VRP via contango)
  - Vol tres haute et en hausse -> TLT (risk off, obligations)
  - Vol normale -> SPY (ride le marche)

Usage :
    python scripts/paper_vrp.py              # rebalancement mensuel
    python scripts/paper_vrp.py --dry-run    # sans ordres
    python scripts/paper_vrp.py --status     # status actuel
    python scripts/paper_vrp.py --force      # forcer le rebalancement
"""
from __future__ import annotations

import argparse
import json
import logging
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

STATE_FILE = Path(__file__).parent.parent / "paper_vrp_state.json"

VOL_HIGH = 20       # seuil vol realisee haute
VOL_CRISIS = 25     # seuil risk-off


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"holding": None, "last_rebalance": None, "history": []}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def get_vol_regime() -> dict:
    """Calcule le regime de volatilite actuel."""
    from core.data.loader import OHLCVLoader
    data = OHLCVLoader.from_yfinance("SPY", "1D", period="2y")
    close = data.df["close"]
    returns = close.pct_change().dropna()

    vol_20d = returns.iloc[-20:].std() * np.sqrt(252) * 100
    vol_60d = returns.iloc[-60:].std() * np.sqrt(252) * 100
    vol_sma = returns.rolling(60).std().iloc[-1] * np.sqrt(252) * 100

    spy_price = float(close.iloc[-1])
    spy_sma200 = float(close.rolling(200).mean().iloc[-1])

    return {
        "vol_20d": float(vol_20d),
        "vol_60d": float(vol_60d),
        "vol_sma": float(vol_sma),
        "vol_trend": "rising" if vol_20d > vol_60d else "falling",
        "spy_price": spy_price,
        "spy_sma200": spy_sma200,
        "spy_above_sma": spy_price > spy_sma200,
    }


def decide_holding(regime: dict) -> str:
    """Decide l'allocation basee sur le regime de vol."""
    vol = regime["vol_20d"]
    trend = regime["vol_trend"]

    if vol > VOL_CRISIS and trend == "rising":
        return "TLT"    # Risk off
    elif vol > VOL_HIGH and trend == "falling":
        return "SVXY"   # Collecter le VRP
    else:
        return "SPY"    # Regime normal


def rebalance_alpaca(target: str, current: str | None, dry_run: bool) -> dict:
    """Rebalance vers le target."""
    if target == current:
        logger.info(f"  Deja positionne sur {target}, rien a faire")
        return {"action": "hold"}

    if dry_run:
        logger.info(f"  [DRY-RUN] {current or 'CASH'} -> {target}")
        return {"action": "dry_run"}

    from core.alpaca_client.client import AlpacaClient
    client = AlpacaClient.from_env()
    account = client.authenticate()

    # Fermer la position actuelle
    if current:
        try:
            client.close_position(current)
            logger.info(f"  VENDU {current}")
        except Exception as e:
            logger.warning(f"  Erreur vente {current}: {e}")
        import time; time.sleep(2)
        account = client.authenticate()

    # Acheter le target (95% du buying power)
    notional = account["buying_power"] * 0.48  # ~48% car buying_power = 2x equity en paper
    try:
        result = client.create_position(target, "BUY", notional=round(notional, 2))
        logger.info(f"  ACHETE {target}: ${notional:,.0f}")
        return {"action": "rebalanced", "result": result}
    except Exception as e:
        logger.error(f"  Erreur achat {target}: {e}")
        return {"action": "error", "error": str(e)}


def run(dry_run: bool = False, force: bool = False):
    now = datetime.now(timezone.utc)
    state = load_state()

    # Check si deja rebalance ce mois
    last = state.get("last_rebalance")
    if last and not force:
        last_dt = datetime.fromisoformat(last)
        if last_dt.month == now.month and last_dt.year == now.year:
            logger.info(f"Deja rebalance ce mois ({last}). --force pour forcer.")
            return

    print(f"\n{'='*60}")
    print(f"  VRP ROTATION — REBALANCEMENT MENSUEL")
    print(f"{'='*60}")

    regime = get_vol_regime()
    target = decide_holding(regime)

    print(f"  Vol 20j: {regime['vol_20d']:.1f}%  60j: {regime['vol_60d']:.1f}%")
    print(f"  Tendance vol: {regime['vol_trend']}")
    print(f"  SPY: ${regime['spy_price']:.2f} vs SMA200: ${regime['spy_sma200']:.2f}")
    print(f"  Decision: -> {target}")

    current = state.get("holding")
    rebalance_alpaca(target, current, dry_run)

    state["holding"] = target
    state["last_rebalance"] = now.isoformat()
    state["history"].append({
        "date": now.strftime("%Y-%m-%d"),
        "holding": target,
        "vol_20d": round(regime["vol_20d"], 1),
        "vol_trend": regime["vol_trend"],
        "dry_run": dry_run,
    })
    save_state(state)

    print(f"\n  Position: {target}")
    print(f"{'='*60}\n")


def show_status():
    state = load_state()
    print(f"\n{'='*60}")
    print(f"  VRP ROTATION — STATUS")
    print(f"{'='*60}")
    print(f"  Holding: {state.get('holding', 'AUCUN')}")
    print(f"  Dernier rebalancement: {state.get('last_rebalance', 'jamais')}")

    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        positions = client.get_positions()
        relevant = [p for p in positions
                     if p["symbol"] in ("SVXY", "SPY", "TLT")]
        if relevant:
            for p in relevant:
                print(f"  {p['symbol']}: {p['qty']} shares, P&L={p['unrealized_pl']:+.2f}")
    except Exception as e:
        print(f"  Alpaca: {e}")

    for h in state.get("history", [])[-5:]:
        print(f"  {h['date']}: {h['holding']} (vol={h.get('vol_20d','?')}%, {h.get('vol_trend','?')})")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="VRP Rotation SVXY/SPY/TLT")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
