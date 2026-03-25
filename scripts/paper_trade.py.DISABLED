"""
Paper Trading Runner — rsi_iwm_1d_v1

Lancer une fois par jour, idéalement à 9h25 ET (avant l'ouverture US).
Utilise les données du jour précédent (close) pour calculer le signal.
Exécute à l'ouverture de marché via ordre market Alpaca.

Usage :
    python scripts/paper_trade.py              # Signal + exécution
    python scripts/paper_trade.py --dry-run    # Signal uniquement, sans ordre
    python scripts/paper_trade.py --status     # Affiche la position courante

Fichier d'état : paper_trading_state.json (dans trading-platform/)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("paper_trade")

# ─── Configuration stratégie ────────────────────────────────────────────────

STRATEGY = {
    "strategy_id":    "rsi_iwm_1d_v1",
    "symbol":         "IWM",
    "rsi_period":     8,
    "oversold":       25,
    "overbought":     60,
    "stop_loss_pct":  0.8 / 100,
    "take_profit_pct": 3.0 / 100,
    "trailing_stop_pct": 0.6 / 100,
    "max_position_pct":  0.05,      # 5% du capital
}

STATE_FILE = Path(__file__).parent.parent / "paper_trading_state.json"


# ─── État persistant ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "position":     None,       # dict ou None
        "trade_log":    [],
        "total_pnl":    0.0,
        "last_run":     None,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ─── Signal RSI ─────────────────────────────────────────────────────────────

def compute_signal(bars: list[dict]) -> str | None:
    """
    Calcule le signal RSI sur les N dernières barres.
    Retourne "long", "short" ou None.
    NO LOOKAHEAD : signal basé sur close[t-1], exécuté à open[t].
    """
    import numpy as np

    closes = np.array([b["c"] for b in bars], dtype=float)
    period = STRATEGY["rsi_period"]

    if len(closes) < period + 2:
        log.warning(f"Pas assez de barres pour RSI({period}) : {len(closes)}")
        return None

    # RSI Wilder (EMA)
    delta = np.diff(closes)
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)

    alpha = 1.0 / period
    avg_gain = np.zeros(len(gain))
    avg_loss = np.zeros(len(loss))
    avg_gain[0] = gain[0]
    avg_loss[0] = loss[0]
    for i in range(1, len(gain)):
        avg_gain[i] = alpha * gain[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * loss[i] + (1 - alpha) * avg_loss[i - 1]

    with np.errstate(divide="ignore", invalid="ignore"):
        rs  = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
        rsi = 100 - (100 / (1 + rs))

    # Signal sur la barre PRÉCÉDENTE (no-lookahead)
    rsi_prev  = rsi[-2]
    rsi_prev2 = rsi[-3]

    os_thresh = STRATEGY["oversold"]
    ob_thresh = STRATEGY["overbought"]

    log.info(f"RSI({period}) : ...{rsi[-3]:.1f} → {rsi[-2]:.1f} → {rsi[-1]:.1f}")

    # Croisement de seuil (signal sur barre[t-1])
    if rsi_prev > os_thresh and rsi_prev2 <= os_thresh:
        return "long"
    if rsi_prev < ob_thresh and rsi_prev2 >= ob_thresh:
        return "short"
    return None


# ─── Gestion de la position ──────────────────────────────────────────────────

def check_exit(position: dict, current_bar: dict) -> str | None:
    """
    Vérifie les conditions de sortie sur la barre courante.
    Retourne "stop_loss", "take_profit", "trailing_stop" ou None.
    """
    high  = current_bar["h"]
    low   = current_bar["l"]
    close = current_bar["c"]

    if position["direction"] == "long":
        # Mise à jour trailing stop
        if high > position.get("high_watermark", position["entry_price"]):
            position["high_watermark"] = high
            new_stop = high * (1 - STRATEGY["trailing_stop_pct"])
            if new_stop > position["stop"]:
                position["stop"] = new_stop
                log.info(f"Trailing stop mis à jour : {new_stop:.2f}")

        if low <= position["stop"]:
            return "stop_loss"
        if high >= position["target"]:
            return "take_profit"

    else:  # short
        if low < position.get("low_watermark", position["entry_price"]):
            position["low_watermark"] = low
            new_stop = low * (1 + STRATEGY["trailing_stop_pct"])
            if new_stop < position["stop"]:
                position["stop"] = new_stop
                log.info(f"Trailing stop mis à jour : {new_stop:.2f}")

        if high >= position["stop"]:
            return "stop_loss"
        if low <= position["target"]:
            return "take_profit"

    return None


# ─── Exécution Alpaca ────────────────────────────────────────────────────────

def get_alpaca_client():
    from core.alpaca_client.client import AlpacaClient
    return AlpacaClient.from_env()


def get_account_equity(client) -> float:
    info = client.get_account_info()
    return info["equity"]


def place_order(client, direction: str, equity: float, dry_run: bool) -> dict | None:
    """Place un ordre market + bracket (stop loss + take profit) sur Alpaca."""
    symbol    = STRATEGY["symbol"]
    notional  = equity * STRATEGY["max_position_pct"]

    log.info(f"Ordre : {direction.upper()} {symbol} — notional=${notional:.0f}")

    if dry_run:
        log.info("[DRY RUN] Ordre non envoyé")
        return {"orderId": "DRY-RUN", "symbol": symbol, "side": direction, "dry_run": True}

    try:
        result = client.create_position(
            symbol=symbol,
            direction="BUY" if direction == "long" else "SELL",
            notional=round(notional, 2),
        )
        log.info(f"Ordre placé : {result}")
        return result
    except Exception as e:
        log.error(f"Erreur ordre Alpaca : {e}")
        return None


def close_order(client, symbol: str, dry_run: bool) -> dict | None:
    """Ferme la position ouverte."""
    log.info(f"Fermeture position {symbol}")
    if dry_run:
        log.info("[DRY RUN] Fermeture non envoyée")
        return {"orderId": "DRY-RUN-CLOSE"}
    try:
        return client.close_position(symbol)
    except Exception as e:
        log.error(f"Erreur fermeture position : {e}")
        return None


# ─── Runner principal ────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    log.info("=" * 60)
    log.info(f"  PAPER TRADE — {STRATEGY['strategy_id']}")
    log.info(f"  {'[DRY RUN]' if dry_run else '[LIVE PAPER]'}")
    log.info("=" * 60)

    state  = load_state()
    client = get_alpaca_client()

    # 1. Récupérer les dernières barres IWM
    from core.alpaca_client.client import AlpacaClient
    raw = client.get_prices(STRATEGY["symbol"], "1D", bars=60)
    bars = raw["bars"]
    if not bars:
        log.error("Aucune donnée reçue d'Alpaca")
        return

    last_bar  = bars[-1]
    last_date = last_bar["t"][:10]
    log.info(f"Dernière barre : {last_date} | O={last_bar['o']} H={last_bar['h']} L={last_bar['l']} C={last_bar['c']}")

    # 2. Compte
    equity = get_account_equity(client)
    log.info(f"Capital : ${equity:,.0f}")

    # 3. Vérifier sortie si position ouverte
    position = state.get("position")
    if position:
        exit_reason = check_exit(position, last_bar)
        if exit_reason:
            log.info(f"SORTIE : {exit_reason.upper()} sur {position['symbol']}")
            close_result = close_order(client, position["symbol"], dry_run)

            # Calculer P&L
            close_price = last_bar["c"]
            if position["direction"] == "long":
                pnl = (close_price - position["entry_price"]) * position["size"]
            else:
                pnl = (position["entry_price"] - close_price) * position["size"]

            log.info(f"P&L estimé : ${pnl:+.2f}")

            state["trade_log"].append({
                "date_exit":    last_date,
                "symbol":       position["symbol"],
                "direction":    position["direction"],
                "entry_price":  position["entry_price"],
                "exit_price":   close_price,
                "exit_reason":  exit_reason,
                "pnl":          round(pnl, 2),
            })
            state["total_pnl"] += pnl
            state["position"] = None
            save_state(state)
            log.info(f"P&L total cumulé : ${state['total_pnl']:+.2f}")

    # 4. Calculer signal du jour
    signal = compute_signal(bars)
    log.info(f"Signal : {signal or 'NEUTRE'}")

    # 5. Entrée en position si signal et pas déjà en position
    position = state.get("position")

    if signal and not position:
        log.info(f"ENTRÉE {signal.upper()} — envoi ordre Alpaca")
        order = place_order(client, signal, equity, dry_run)

        if order:
            entry_price = last_bar["c"]  # estimation (exécution au prochain open)
            size        = (equity * STRATEGY["max_position_pct"]) / entry_price
            stop  = entry_price * (1 - STRATEGY["stop_loss_pct"])  if signal == "long" \
                    else entry_price * (1 + STRATEGY["stop_loss_pct"])
            target = entry_price * (1 + STRATEGY["take_profit_pct"]) if signal == "long" \
                    else entry_price * (1 - STRATEGY["take_profit_pct"])

            state["position"] = {
                "symbol":         STRATEGY["symbol"],
                "direction":      signal,
                "entry_date":     last_date,
                "entry_price":    entry_price,
                "size":           round(size, 4),
                "stop":           round(stop, 4),
                "target":         round(target, 4),
                "high_watermark": entry_price,
                "low_watermark":  entry_price,
                "order_id":       order.get("orderId", ""),
            }
            save_state(state)
            log.info(f"Position enregistrée : {state['position']}")

    elif signal and position:
        # Signal inverse = sortie + renversement
        if signal != position["direction"]:
            log.info(f"Signal inverse ({signal}) — fermeture position {position['direction']}")
            close_order(client, position["symbol"], dry_run)
            state["position"] = None
            save_state(state)
        else:
            log.info(f"Signal {signal} mais déjà en position {position['direction']} — ignoré")

    elif not signal and not position:
        log.info("Pas de signal, pas de position — rien à faire")
    elif not signal and position:
        log.info(f"Position ouverte {position['direction']} — pas de sortie signal aujourd'hui")

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    log.info("=" * 60)
    _print_status(state)


def show_status():
    state = load_state()
    _print_status(state)


def _print_status(state: dict):
    print(f"\n{'-'*50}")
    print(f"  STATUT PAPER TRADING -- {STRATEGY['strategy_id']}")
    print(f"{'-'*50}")
    print(f"  Dernier run  : {state.get('last_run', 'jamais')}")
    print(f"  P&L total    : ${state.get('total_pnl', 0):+.2f}")
    print(f"  Trades       : {len(state.get('trade_log', []))}")

    pos = state.get("position")
    if pos:
        print(f"\n  Position ouverte :")
        print(f"    {pos['direction'].upper()} {pos['symbol']}")
        print(f"    Entrée       : ${pos['entry_price']:.2f}  ({pos['entry_date']})")
        print(f"    Stop         : ${pos['stop']:.2f}")
        print(f"    Target       : ${pos['target']:.2f}")
        print(f"    Taille       : {pos['size']:.2f} actions")
    else:
        print(f"\n  Aucune position ouverte")

    log_entries = state.get("trade_log", [])
    if log_entries:
        print(f"\n  Historique :")
        for t in log_entries[-5:]:
            pnl_str = f"${t['pnl']:+.2f}"
            print(f"    {t['date_exit']}  {t['direction']:5s}  {t['exit_reason']:15s}  {pnl_str}")
    print(f"{'-'*50}\n")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Paper trading quotidien — rsi_iwm_1d_v1")
    parser.add_argument("--dry-run", action="store_true",
                        help="Calculer le signal sans envoyer d'ordre")
    parser.add_argument("--status", action="store_true",
                        help="Afficher la position courante et l'historique")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
