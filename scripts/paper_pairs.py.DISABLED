#!/usr/bin/env python3
"""
Paper Trading Runner — Pairs Trading MU/AMAT sur Alpaca.

Strategie dollar-neutral : long MU / short AMAT (ou inverse)
basee sur le z-score du spread cointegre.

Execution quotidienne (avant ouverture US) :
  1. Telecharge les 60 derniers jours de prix
  2. Calcule le hedge ratio OLS et le z-score rolling
  3. Si |z-score| > entry_z : ouvrir la paire
  4. Si |z-score| < exit_z : fermer la paire
  5. Log dans paper_pairs_state.json

Usage :
    python scripts/paper_pairs.py              # execution quotidienne
    python scripts/paper_pairs.py --dry-run    # sans ordres
    python scripts/paper_pairs.py --status     # status actuel
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

STATE_FILE = Path(__file__).parent.parent / "paper_pairs_state.json"

# ─── Configuration ────────────────────────────────────────────────────────────

SYMBOL_A = "MU"
SYMBOL_B = "AMAT"
ENTRY_Z = 2.0
EXIT_Z = 0.5
STOP_Z = 4.0
ZSCORE_WINDOW = 30
LOOKBACK_DAYS = 120
NOTIONAL = 10_000  # $ par jambe


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"position": None, "history": [], "pnl_total": 0.0}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def get_prices() -> tuple[pd.Series, pd.Series]:
    """Charge les prix recents des deux actifs."""
    from core.data.loader import OHLCVLoader
    data_a = OHLCVLoader.from_yfinance(SYMBOL_A, "1D", period="1y")
    data_b = OHLCVLoader.from_yfinance(SYMBOL_B, "1D", period="1y")
    close_a = data_a.df["close"].rename(SYMBOL_A)
    close_b = data_b.df["close"].rename(SYMBOL_B)
    df = pd.concat([close_a, close_b], axis=1).dropna()
    return df[SYMBOL_A], df[SYMBOL_B]


def compute_zscore(close_a: pd.Series, close_b: pd.Series) -> dict:
    """Calcule le hedge ratio OLS et le z-score courant."""
    log_a = np.log(close_a.iloc[-LOOKBACK_DAYS:])
    log_b = np.log(close_b.iloc[-LOOKBACK_DAYS:])

    # Hedge ratio OLS (sans intercept)
    beta = (log_b * log_a).sum() / (log_b * log_b).sum()
    alpha = (log_a - beta * log_b).mean()

    # Spread
    spread = np.log(close_a) - beta * np.log(close_b) - alpha

    # Z-score rolling
    spread_window = spread.iloc[-ZSCORE_WINDOW:]
    mu = spread_window.mean()
    sigma = spread_window.std()
    zscore = (spread.iloc[-1] - mu) / sigma if sigma > 0 else 0.0

    return {
        "beta": float(beta),
        "alpha": float(alpha),
        "zscore": float(zscore),
        "spread": float(spread.iloc[-1]),
        "price_a": float(close_a.iloc[-1]),
        "price_b": float(close_b.iloc[-1]),
    }


def execute_pair_trade(action: str, direction: str, stats: dict,
                       dry_run: bool = False) -> dict:
    """Execute un trade de paire sur Alpaca."""
    from core.alpaca_client.client import AlpacaClient
    client = AlpacaClient.from_env()

    price_a = stats["price_a"]
    price_b = stats["price_b"]
    qty_a = int(NOTIONAL / price_a)
    qty_b = int(NOTIONAL / price_b)

    orders = []

    if dry_run:
        logger.info(f"  [DRY-RUN] {action} {direction}: "
                    f"{SYMBOL_A} qty={qty_a}, {SYMBOL_B} qty={qty_b}")
        return {"dry_run": True, "orders": []}

    if action == "open":
        if direction == "long_a_short_b":
            # z-score < -ENTRY_Z : spread trop bas, long A short B
            r1 = client.create_position(SYMBOL_A, "BUY", qty=qty_a)
            r2 = client.create_position(SYMBOL_B, "SELL", qty=qty_b)
            orders = [r1, r2]
        else:
            # z-score > ENTRY_Z : spread trop haut, short A long B
            r1 = client.create_position(SYMBOL_A, "SELL", qty=qty_a)
            r2 = client.create_position(SYMBOL_B, "BUY", qty=qty_b)
            orders = [r1, r2]
        logger.info(f"  OUVERT paire {direction}: {SYMBOL_A}={qty_a}, {SYMBOL_B}={qty_b}")

    elif action == "close":
        try:
            client.close_position(SYMBOL_A)
        except Exception:
            pass
        try:
            client.close_position(SYMBOL_B)
        except Exception:
            pass
        logger.info(f"  FERME toutes les positions")

    return {"orders": orders, "dry_run": False}


def run(dry_run: bool = False):
    """Logique principale quotidienne."""
    now = datetime.now(timezone.utc)
    state = load_state()

    print(f"\n{'='*60}")
    print(f"  PAIRS TRADING {SYMBOL_A}/{SYMBOL_B} — CHECK QUOTIDIEN")
    print(f"{'='*60}")
    print(f"  Date: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Seuils: entry={ENTRY_Z}, exit={EXIT_Z}, stop={STOP_Z}")
    print(f"  Mode: {'DRY-RUN' if dry_run else 'PAPER TRADING'}")

    # Calculer le z-score
    close_a, close_b = get_prices()
    stats = compute_zscore(close_a, close_b)

    z = stats["zscore"]
    print(f"\n  {SYMBOL_A}: ${stats['price_a']:.2f}")
    print(f"  {SYMBOL_B}: ${stats['price_b']:.2f}")
    print(f"  Beta: {stats['beta']:.4f}")
    print(f"  Z-score: {z:+.3f}")

    pos = state.get("position")
    action_taken = None

    if pos is None:
        # Pas de position — chercher une entree
        if z > ENTRY_Z:
            print(f"  -> SIGNAL: z={z:+.3f} > {ENTRY_Z} -> SHORT A / LONG B")
            execute_pair_trade("open", "short_a_long_b", stats, dry_run)
            state["position"] = {"direction": "short_a_long_b",
                                  "entry_z": z, "entry_date": now.isoformat(),
                                  "entry_price_a": stats["price_a"],
                                  "entry_price_b": stats["price_b"]}
            action_taken = f"OPEN short_a_long_b z={z:+.3f}"

        elif z < -ENTRY_Z:
            print(f"  -> SIGNAL: z={z:+.3f} < -{ENTRY_Z} -> LONG A / SHORT B")
            execute_pair_trade("open", "long_a_short_b", stats, dry_run)
            state["position"] = {"direction": "long_a_short_b",
                                  "entry_z": z, "entry_date": now.isoformat(),
                                  "entry_price_a": stats["price_a"],
                                  "entry_price_b": stats["price_b"]}
            action_taken = f"OPEN long_a_short_b z={z:+.3f}"
        else:
            print(f"  -> PAS DE SIGNAL (|z|={abs(z):.3f} < {ENTRY_Z})")
    else:
        # Position ouverte — chercher une sortie
        print(f"  Position: {pos['direction']} depuis {pos.get('entry_date','?')}")
        print(f"  Entry z: {pos.get('entry_z', 0):+.3f}, current z: {z:+.3f}")

        should_close = False
        reason = ""

        if abs(z) < EXIT_Z:
            should_close = True
            reason = f"mean reversion (|z|={abs(z):.3f} < {EXIT_Z})"
        elif abs(z) > STOP_Z:
            should_close = True
            reason = f"stop loss (|z|={abs(z):.3f} > {STOP_Z})"
        elif (pos["direction"] == "long_a_short_b" and z > ENTRY_Z):
            should_close = True
            reason = "signal inverse"
        elif (pos["direction"] == "short_a_long_b" and z < -ENTRY_Z):
            should_close = True
            reason = "signal inverse"

        if should_close:
            print(f"  -> CLOSE: {reason}")
            execute_pair_trade("close", "", stats, dry_run)
            state["position"] = None
            action_taken = f"CLOSE {reason} z={z:+.3f}"
        else:
            print(f"  -> HOLD (pas de signal de sortie)")

    # Sauvegarder
    if action_taken:
        state["history"].append({
            "date": now.strftime("%Y-%m-%d"),
            "action": action_taken,
            "zscore": round(z, 3),
            "price_a": stats["price_a"],
            "price_b": stats["price_b"],
        })
    save_state(state)
    print(f"{'='*60}\n")


def show_status():
    state = load_state()
    print(f"\n{'='*60}")
    print(f"  PAIRS {SYMBOL_A}/{SYMBOL_B} — STATUS")
    print(f"{'='*60}")

    pos = state.get("position")
    if pos:
        print(f"  Position: {pos['direction']}")
        print(f"  Depuis: {pos.get('entry_date', '?')}")
        print(f"  Entry z: {pos.get('entry_z', 0):+.3f}")
    else:
        print(f"  Position: FLAT (pas de position)")

    # Positions Alpaca
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        positions = client.get_positions()
        relevant = [p for p in positions if p["symbol"] in (SYMBOL_A, SYMBOL_B)]
        if relevant:
            print(f"\n  Positions Alpaca:")
            for p in relevant:
                print(f"    {p['symbol']:<6} {p['qty']:>6} shares  "
                      f"P&L={p['unrealized_pl']:>+8.2f}")
    except Exception as e:
        print(f"\n  Alpaca: {e}")

    history = state.get("history", [])
    if history:
        print(f"\n  Historique ({len(history)} actions):")
        for h in history[-5:]:
            print(f"    {h['date']}: {h['action']}")

    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Pairs Trading MU/AMAT")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
