#!/usr/bin/env python3
"""
Paper Portfolio EU — pipeline multi-strategies europeennes sur IBKR paper.

INFRA-005 : Refactored to support multiple strategies simultaneously.
Loads strategy registry from config/strategies_eu.yaml.

Strategies actives :
  - BCE Momentum Drift v2 (Sharpe 14.93, event BCE)
  - Auto Sector German (Sharpe 13.43, event auto sector)
  - Brent Lag Play (Sharpe 4.08, momentum oil)
  - EU Close -> US Afternoon (Sharpe 2.43, cross-timezone)
  - EU Gap Open (Sharpe 8.56, gap opening EU)

Usage :
    python scripts/paper_portfolio_eu.py              # execution
    python scripts/paper_portfolio_eu.py --dry-run    # sans ordres
    python scripts/paper_portfolio_eu.py --status     # positions IBKR
    python scripts/paper_portfolio_eu.py --intraday   # mode intraday (cron 5min)
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
from datetime import datetime, date as dt_date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("portfolio_eu")

# =============================================================================
# PATHS
# =============================================================================

ROOT = Path(__file__).parent.parent
STRATEGIES_YAML = ROOT / "config" / "strategies_eu.yaml"
STATE_FILE = ROOT / "paper_portfolio_eu_state.json"

# =============================================================================
# CONFIGURATION
# =============================================================================

INITIAL_CAPITAL_EU = 100_000.0       # IBKR paper — $100K alloue au pipe EU
MAX_DAILY_DRAWDOWN = 0.05            # 5% circuit-breaker
MAX_POSITION_SIZE = 0.10             # 10% max par position individuelle
MAX_LIVE_POSITIONS = 10              # Max positions simultanees EU
MAX_NET_LONG_EXPOSURE = 0.40         # 40% max exposition nette long
MAX_NET_SHORT_EXPOSURE = 0.20        # 20% max exposition nette short
STRATEGY_KILL_SWITCH_PCT = 0.02      # -2% du capital alloue sur 5j rolling = desactive
STRATEGY_KILL_SWITCH_DAYS = 5        # Fenetre rolling pour le kill switch

# Fermeture forcee (Europe/Paris)
EU_FORCE_CLOSE_HOUR = 17             # 17:35 CET pour positions EU
EU_FORCE_CLOSE_MINUTE = 35
CROSS_TZ_FORCE_CLOSE_HOUR = 22      # 22:00 CET pour cross-timezone
CROSS_TZ_FORCE_CLOSE_MINUTE = 0

# Jours feries principaux — marches EU FERMES (Euronext, XETRA, LSE overlap)
EU_HOLIDAYS_2026 = {
    "2026-01-01",  # Jour de l'an
    "2026-04-03",  # Vendredi saint
    "2026-04-06",  # Lundi de Paques
    "2026-05-01",  # Fete du travail
    "2026-12-25",  # Noel
    "2026-12-26",  # Saint-Etienne (Boxing Day)
}


# =============================================================================
# STRATEGY REGISTRY — loaded from YAML
# =============================================================================

def load_strategies_from_yaml(path: Path = STRATEGIES_YAML) -> dict:
    """Charge le registre de strategies depuis le fichier YAML.

    Returns:
        dict {strategy_id: {name, enabled, sharpe, ...}}
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    strategies = data.get("strategies", {})

    # Validation des champs obligatoires
    required_fields = {
        "name", "enabled", "sharpe", "trades", "wf_status", "edge_type",
        "market_hours", "tickers", "allocation_pct", "max_position_pct",
        "sl_pct", "tp_pct",
    }
    for sid, cfg in strategies.items():
        missing = required_fields - set(cfg.keys())
        if missing:
            raise ValueError(
                f"Strategie '{sid}' : champs manquants = {missing}"
            )
        # Valider market_hours sub-fields
        mh = cfg["market_hours"]
        for k in ("start", "end", "tz"):
            if k not in mh:
                raise ValueError(
                    f"Strategie '{sid}' : market_hours manque '{k}'"
                )

    logger.info(
        "EU strategies loaded: %d total, %d enabled",
        len(strategies),
        sum(1 for s in strategies.values() if s.get("enabled")),
    )
    return strategies


# Global — charge au demarrage du module
try:
    EU_STRATEGIES = load_strategies_from_yaml()
except Exception as e:
    logger.warning("Impossible de charger strategies_eu.yaml: %s", e)
    EU_STRATEGIES = {}


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_state() -> dict:
    """Charge le state depuis le fichier JSON (ou reconstruit depuis IBKR)."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning("State file corrompu, reconstruction")

    logger.info("Reconstruction du state EU...")
    state = {
        "capital": INITIAL_CAPITAL_EU,
        "positions": {},
        "allocations": {},
        "daily_capital_start": INITIAL_CAPITAL_EU,
        "daily_pnl": 0.0,
        "last_run_date": None,
        "history": [],
        "intraday_positions": {},
        "strategy_pnl_log": {},
    }

    try:
        ibkr = _get_ibkr()
        info = ibkr.authenticate()
        state["capital"] = info["equity"]
        state["daily_capital_start"] = info["equity"]

        positions = ibkr.get_positions()
        for p in positions:
            sym = p["symbol"]
            state["intraday_positions"][sym] = {
                "strategy": "unknown",
                "direction": "LONG" if float(p.get("qty", 0)) > 0 else "SHORT",
                "entry_price": float(p.get("avg_entry", 0)),
                "opened_at": datetime.utcnow().isoformat(),
            }
        logger.info("  Equity IBKR: $%.2f, %d positions", info["equity"], len(positions))
    except Exception as e:
        logger.warning("  Impossible de reconstruire depuis IBKR: %s", e)

    save_state(state)
    return state


def save_state(state: dict):
    """Persiste le state dans paper_portfolio_eu_state.json."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except IOError as e:
        logger.warning("Impossible de sauvegarder le state EU: %s", e)


# =============================================================================
# BROKER ACCESS
# =============================================================================

def _get_ibkr():
    """Retourne une connexion IBKR via le broker adapter."""
    from core.broker import get_broker
    return get_broker("ibkr")


def _get_smart_router():
    """Retourne le SmartRouter pour le routage cross-broker."""
    from core.broker.factory import SmartRouter
    return SmartRouter()


# =============================================================================
# MARKET HOURS
# =============================================================================

def is_eu_market_open() -> bool:
    """Verifie si les marches EU sont ouverts (9:00-17:30 CET, lun-ven, hors feries)."""
    import pytz
    cet = pytz.timezone("Europe/Paris")
    now = datetime.now(cet)

    if now.weekday() >= 5:
        return False

    today_str = now.strftime("%Y-%m-%d")
    if today_str in EU_HOLIDAYS_2026:
        return False

    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=17, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_strategy_active(strategy_id: str, config: dict) -> bool:
    """Verifie si une strategie est dans sa fenetre horaire active.

    Args:
        strategy_id: identifiant de la strategie
        config: configuration de la strategie (depuis YAML)

    Returns:
        True si la strategie est dans sa fenetre horaire
    """
    import pytz

    mh = config["market_hours"]
    tz = pytz.timezone(mh["tz"])
    now = datetime.now(tz)

    if now.weekday() >= 5:
        return False

    today_str = now.strftime("%Y-%m-%d")
    if today_str in EU_HOLIDAYS_2026:
        return False

    start_h, start_m = map(int, mh["start"].split(":"))
    end_h, end_m = map(int, mh["end"].split(":"))

    start_time = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    return start_time <= now <= end_time


def is_force_close_time(strategy_id: str, config: dict) -> bool:
    """Verifie si on est a l'heure de fermeture forcee pour cette strategie.

    - EU standard : 17:35 CET
    - Cross-timezone : 22:00 CET
    """
    import pytz
    cet = pytz.timezone("Europe/Paris")
    now = datetime.now(cet)

    is_cross_tz = config.get("execution_broker") == "alpaca" or config.get("edge_type") == "cross_timezone"

    if is_cross_tz:
        close_time = now.replace(
            hour=CROSS_TZ_FORCE_CLOSE_HOUR,
            minute=CROSS_TZ_FORCE_CLOSE_MINUTE,
            second=0, microsecond=0,
        )
    else:
        close_time = now.replace(
            hour=EU_FORCE_CLOSE_HOUR,
            minute=EU_FORCE_CLOSE_MINUTE,
            second=0, microsecond=0,
        )

    # Fenetre de fermeture : 5 minutes
    return close_time <= now <= close_time + timedelta(minutes=5)


# =============================================================================
# ALLOCATION
# =============================================================================

def compute_eu_allocations(strategies: dict, total_capital: float) -> dict[str, dict]:
    """Allocation Sharpe-weighted pour les strategies EU.

    Calcule le capital alloue a chaque strategie enabled,
    en pondérant par le Sharpe et en respectant les caps du YAML.

    Args:
        strategies: dict {sid: config}
        total_capital: capital total alloue au pipe EU

    Returns:
        dict {sid: {pct, capital, max_position}}
    """
    enabled = {
        sid: cfg for sid, cfg in strategies.items()
        if cfg.get("enabled", False)
    }

    if not enabled:
        return {}

    # Base allocation from YAML
    base_alloc = {}
    for sid, cfg in enabled.items():
        base_alloc[sid] = cfg["allocation_pct"]

    # Normaliser pour que total = 100% du capital EU
    total_pct = sum(base_alloc.values())
    if total_pct > 0:
        base_alloc = {sid: pct / total_pct for sid, pct in base_alloc.items()}

    # Cap par strategie a allocation_pct * 2 (safety cap)
    MAX_ALLOC = 0.25
    for sid, pct in base_alloc.items():
        if pct > MAX_ALLOC:
            base_alloc[sid] = MAX_ALLOC

    # Re-normaliser apres cap
    total_pct = sum(base_alloc.values())
    if total_pct > 0 and total_pct != 1.0:
        base_alloc = {sid: pct / total_pct for sid, pct in base_alloc.items()}

    result = {}
    for sid, pct in base_alloc.items():
        cfg = enabled[sid]
        allocated = total_capital * pct
        max_pos = min(
            total_capital * cfg["max_position_pct"],
            total_capital * MAX_POSITION_SIZE,
        )
        result[sid] = {
            "pct": round(pct, 4),
            "capital": round(allocated, 2),
            "max_position": round(max_pos, 2),
        }

    return result


# =============================================================================
# KILL SWITCH & CIRCUIT-BREAKER
# =============================================================================

def check_circuit_breaker_eu(state: dict, equity: float) -> bool:
    """Circuit-breaker EU : True si drawdown journalier > 5%.

    Returns:
        True si le trading doit etre BLOQUE.
    """
    daily_start = state.get("daily_capital_start", equity)
    if daily_start <= 0:
        return False

    daily_dd = (equity - daily_start) / daily_start
    if daily_dd < -MAX_DAILY_DRAWDOWN:
        logger.critical(
            "CIRCUIT-BREAKER EU: DD journalier %.1f%% > %.0f%% — AUCUN ordre. "
            "Equity=$%.2f, start=$%.2f",
            daily_dd * 100, MAX_DAILY_DRAWDOWN * 100, equity, daily_start,
        )
        try:
            from core.telegram_alert import send_circuit_breaker
            send_circuit_breaker(equity, daily_start, daily_dd)
        except Exception:
            pass
        return True
    return False


def check_kill_switch_eu(state: dict, strategy_id: str, allocated_capital: float) -> bool:
    """Kill switch par strategie EU : PnL rolling 5j < -2% du capital alloue.

    Returns:
        True si la strategie est KILL (ne doit pas trader).
    """
    kill_log = state.get("strategy_pnl_log", {}).get(strategy_id, [])
    if len(kill_log) < STRATEGY_KILL_SWITCH_DAYS:
        return False

    recent = kill_log[-STRATEGY_KILL_SWITCH_DAYS:]
    rolling_pnl = sum(entry.get("pnl", 0) for entry in recent)
    threshold = -allocated_capital * STRATEGY_KILL_SWITCH_PCT

    if rolling_pnl < threshold:
        strat_name = EU_STRATEGIES.get(strategy_id, {}).get("name", strategy_id)
        logger.critical(
            "KILL SWITCH EU [%s]: PnL rolling %dj = $%.2f < seuil $%.2f "
            "(-%.0f%% de $%.0f). Strategie DESACTIVEE.",
            strat_name, STRATEGY_KILL_SWITCH_DAYS,
            rolling_pnl, threshold,
            STRATEGY_KILL_SWITCH_PCT * 100, allocated_capital,
        )
        try:
            from core.telegram_alert import send_kill_switch
            send_kill_switch(strat_name, rolling_pnl, threshold)
        except Exception:
            pass
        return True
    return False


def log_strategy_daily_pnl_eu(state: dict, strategy_id: str, daily_pnl: float):
    """Enregistre le PnL quotidien d'une strategie EU pour le kill switch."""
    today = dt_date.today().isoformat()
    state.setdefault("strategy_pnl_log", {}).setdefault(strategy_id, [])
    log = state["strategy_pnl_log"][strategy_id]

    if log and log[-1].get("date") == today:
        log[-1]["pnl"] = log[-1].get("pnl", 0) + daily_pnl
    else:
        log.append({"date": today, "pnl": round(daily_pnl, 2)})

    # Garder max 30 jours
    if len(log) > 30:
        state["strategy_pnl_log"][strategy_id] = log[-30:]


# =============================================================================
# SIGNAL GENERATORS
# =============================================================================

def signal_bce_momentum_drift(broker, config: dict, capital: float, state: dict) -> list[dict]:
    """BCE Momentum Drift v2 : trade le drift post-annonce BCE sur bancaires EU.

    Conditions :
      - Jour de meeting BCE (via EventCalendar)
      - Achat si momentum 5j positif, short si negatif
      - Fenetre 13:45-17:30 CET
    """
    from core.event_calendar import EventCalendar

    today = dt_date.today()
    try:
        cal = EventCalendar()
        if not cal.is_bce_day(today):
            logger.info("  [bce_momentum_drift] Pas de meeting BCE aujourd'hui")
            return []
    except Exception as e:
        logger.warning("  [bce_momentum_drift] EventCalendar indisponible: %s", e)
        return []

    signals = []
    alloc_per_ticker = capital / max(len(config["tickers"]), 1)

    for ticker in config["tickers"]:
        try:
            prices = broker.get_prices(ticker, timeframe="1D", bars=10)
            bars = prices.get("bars", [])
            if len(bars) < 6:
                continue

            # Momentum 5j
            close_now = bars[-1]["c"]
            close_5d = bars[-6]["c"]
            momentum = (close_now - close_5d) / close_5d

            if abs(momentum) < 0.005:  # seuil minimum 0.5%
                continue

            direction = "BUY" if momentum > 0 else "SELL"
            qty = int(alloc_per_ticker / close_now)
            if qty <= 0:
                continue

            sl_mult = 1 - config["sl_pct"] if direction == "BUY" else 1 + config["sl_pct"]
            tp_mult = 1 + config["tp_pct"] if direction == "BUY" else 1 - config["tp_pct"]

            signals.append({
                "ticker": ticker,
                "direction": direction,
                "qty": qty,
                "entry_price": close_now,
                "stop_loss": round(close_now * sl_mult, 2),
                "take_profit": round(close_now * tp_mult, 2),
                "strategy": "bce_momentum_drift",
                "momentum_5d": round(momentum * 100, 2),
            })
            logger.info(
                "  [bce_momentum_drift] SIGNAL: %s %s momentum=%.2f%% qty=%d",
                direction, ticker, momentum * 100, qty,
            )

        except Exception as e:
            logger.warning("  [bce_momentum_drift] Erreur %s: %s", ticker, e)
            continue

    return signals


def signal_auto_sector_german(broker, config: dict, capital: float, state: dict) -> list[dict]:
    """Auto Sector German : trade les constructeurs allemands sur events sectoriels.

    Conditions :
      - Volume premiere heure > 1.5x moyenne 20j
      - Gap > 0.3% a l'ouverture
    """
    signals = []
    alloc_per_ticker = capital / max(len(config["tickers"]), 1)

    for ticker in config["tickers"]:
        try:
            prices = broker.get_prices(ticker, timeframe="1D", bars=25)
            bars = prices.get("bars", [])
            if len(bars) < 2:
                continue

            prev_close = bars[-2]["c"]
            today_open = bars[-1]["o"]
            gap_pct = (today_open - prev_close) / prev_close

            if abs(gap_pct) < 0.003:  # gap < 0.3%
                continue

            # Volume check (si disponible)
            volumes = [b.get("v", 0) for b in bars[-21:-1]]
            avg_vol = np.mean(volumes) if volumes else 0
            today_vol = bars[-1].get("v", 0)
            if avg_vol > 0 and today_vol < avg_vol * 1.5:
                continue

            direction = "BUY" if gap_pct > 0 else "SELL"
            price = bars[-1]["c"]
            qty = int(alloc_per_ticker / price)
            if qty <= 0:
                continue

            sl_mult = 1 - config["sl_pct"] if direction == "BUY" else 1 + config["sl_pct"]
            tp_mult = 1 + config["tp_pct"] if direction == "BUY" else 1 - config["tp_pct"]

            signals.append({
                "ticker": ticker,
                "direction": direction,
                "qty": qty,
                "entry_price": price,
                "stop_loss": round(price * sl_mult, 2),
                "take_profit": round(price * tp_mult, 2),
                "strategy": "auto_sector_german",
                "gap_pct": round(gap_pct * 100, 2),
            })
            logger.info(
                "  [auto_sector_german] SIGNAL: %s %s gap=%.2f%% qty=%d",
                direction, ticker, gap_pct * 100, qty,
            )

        except Exception as e:
            logger.warning("  [auto_sector_german] Erreur %s: %s", ticker, e)
            continue

    return signals


def signal_brent_lag_play(broker, config: dict, capital: float, state: dict) -> list[dict]:
    """Brent Lag Play : exploite le decalage entre Brent et petroliers EU/UK.

    Conditions :
      - Brent bouge > 1% (via Brent proxy ou TTE.PA)
      - Tickers petroliers en retard sur le mouvement
      - Fenetre 15:30-20:00 CET
    """
    signals = []
    alloc_per_ticker = capital / max(len(config["tickers"]), 1)

    # Utiliser TTE.PA comme proxy du mouvement oil
    try:
        tte_prices = broker.get_prices("TTE.PA", timeframe="1D", bars=5)
        tte_bars = tte_prices.get("bars", [])
        if len(tte_bars) < 2:
            return signals
        oil_move = (tte_bars[-1]["c"] - tte_bars[-2]["c"]) / tte_bars[-2]["c"]
    except Exception as e:
        logger.warning("  [brent_lag_play] Erreur proxy oil: %s", e)
        return signals

    if abs(oil_move) < 0.01:  # mouvement oil < 1%
        logger.info("  [brent_lag_play] Mouvement oil trop faible: %.2f%%", oil_move * 100)
        return signals

    for ticker in config["tickers"]:
        try:
            prices = broker.get_prices(ticker, timeframe="1D", bars=5)
            bars = prices.get("bars", [])
            if len(bars) < 2:
                continue

            stock_move = (bars[-1]["c"] - bars[-2]["c"]) / bars[-2]["c"]

            # Lag = le ticker n'a pas encore suivi le mouvement oil
            lag = oil_move - stock_move
            if abs(lag) < 0.005:  # lag < 0.5%
                continue

            direction = "BUY" if lag > 0 else "SELL"
            price = bars[-1]["c"]
            qty = int(alloc_per_ticker / price)
            if qty <= 0:
                continue

            sl_mult = 1 - config["sl_pct"] if direction == "BUY" else 1 + config["sl_pct"]
            tp_mult = 1 + config["tp_pct"] if direction == "BUY" else 1 - config["tp_pct"]

            signals.append({
                "ticker": ticker,
                "direction": direction,
                "qty": qty,
                "entry_price": price,
                "stop_loss": round(price * sl_mult, 2),
                "take_profit": round(price * tp_mult, 2),
                "strategy": "brent_lag_play",
                "oil_move_pct": round(oil_move * 100, 2),
                "lag_pct": round(lag * 100, 2),
            })
            logger.info(
                "  [brent_lag_play] SIGNAL: %s %s lag=%.2f%% qty=%d",
                direction, ticker, lag * 100, qty,
            )

        except Exception as e:
            logger.warning("  [brent_lag_play] Erreur %s: %s", ticker, e)
            continue

    return signals


def signal_eu_close_us_afternoon(broker, config: dict, capital: float, state: dict) -> list[dict]:
    """EU Close -> US Afternoon : signal EU cloture, execution US via Alpaca.

    Cross-broker : detecte le signal sur les indices EU, execute sur SPY/QQQ/IWM.

    Conditions :
      - EU close dans les 30 dernieres minutes (17:00-17:30 CET)
      - Momentum EU intraday > 0.5%
      - Execute cote US (15:30-21:00 CET = marche US ouvert)
    """
    signals = []
    alloc_per_ticker = capital / max(len(config["tickers"]), 1)

    # Detecter le signal EU (momentum intraday EU)
    try:
        # Utiliser un proxy EU accessible via le broker IBKR
        eu_prices = broker.get_prices("SIE.DE", timeframe="1D", bars=5)
        eu_bars = eu_prices.get("bars", [])
        if len(eu_bars) < 1:
            return signals

        eu_intraday = (eu_bars[-1]["c"] - eu_bars[-1]["o"]) / eu_bars[-1]["o"]
    except Exception as e:
        logger.warning("  [eu_close_us_afternoon] Erreur signal EU: %s", e)
        return signals

    if abs(eu_intraday) < 0.005:  # mouvement EU < 0.5%
        logger.info(
            "  [eu_close_us_afternoon] Mouvement EU trop faible: %.2f%%",
            eu_intraday * 100,
        )
        return signals

    # Signal : suivre la direction EU sur les ETFs US
    direction = "BUY" if eu_intraday > 0 else "SELL"

    for ticker in config["tickers"]:
        qty_estimate = int(alloc_per_ticker / 450)  # prix moyen SPY/QQQ/IWM ~$450
        if qty_estimate <= 0:
            qty_estimate = 1

        sl_mult = 1 - config["sl_pct"] if direction == "BUY" else 1 + config["sl_pct"]
        tp_mult = 1 + config["tp_pct"] if direction == "BUY" else 1 - config["tp_pct"]

        signals.append({
            "ticker": ticker,
            "direction": direction,
            "qty": qty_estimate,
            "entry_price": 0,  # sera determine par le broker US
            "stop_loss_pct": config["sl_pct"],
            "take_profit_pct": config["tp_pct"],
            "strategy": "eu_close_us_afternoon",
            "eu_momentum_pct": round(eu_intraday * 100, 2),
            "execution_broker": "alpaca",
        })
        logger.info(
            "  [eu_close_us_afternoon] SIGNAL: %s %s (EU momentum=%.2f%%) -> Alpaca",
            direction, ticker, eu_intraday * 100,
        )

    return signals


def signal_eu_gap_open(broker, config: dict, capital: float, state: dict) -> list[dict]:
    """EU Gap Open : trade le gap d'ouverture EU base sur le close US veille.

    Conditions :
      - Gap > 0.5% a l'ouverture (close hier vs open aujourd'hui)
      - Fenetre 9:05-12:00 CET
    """
    signals = []
    alloc_per_ticker = capital / max(len(config["tickers"]), 1)

    for ticker in config["tickers"]:
        try:
            prices = broker.get_prices(ticker, timeframe="1D", bars=5)
            bars = prices.get("bars", [])
            if len(bars) < 2:
                continue

            prev_close = bars[-2]["c"]
            today_open = bars[-1]["o"]
            gap_pct = (today_open - prev_close) / prev_close

            if abs(gap_pct) < 0.005:  # gap < 0.5%
                continue

            direction = "BUY" if gap_pct > 0 else "SELL"
            price = today_open
            qty = int(alloc_per_ticker / price)
            if qty <= 0:
                continue

            sl_mult = 1 - config["sl_pct"] if direction == "BUY" else 1 + config["sl_pct"]
            tp_mult = 1 + config["tp_pct"] if direction == "BUY" else 1 - config["tp_pct"]

            signals.append({
                "ticker": ticker,
                "direction": direction,
                "qty": qty,
                "entry_price": price,
                "stop_loss": round(price * sl_mult, 2),
                "take_profit": round(price * tp_mult, 2),
                "strategy": "eu_gap_open",
                "gap_pct": round(gap_pct * 100, 2),
            })
            logger.info(
                "  [eu_gap_open] SIGNAL: %s %s gap=%.2f%% qty=%d",
                direction, ticker, gap_pct * 100, qty,
            )

        except Exception as e:
            logger.warning("  [eu_gap_open] Erreur %s: %s", ticker, e)
            continue

    return signals


# Dispatch map: strategy_id -> signal function
SIGNAL_DISPATCH = {
    "bce_momentum_drift": signal_bce_momentum_drift,
    "auto_sector_german": signal_auto_sector_german,
    "brent_lag_play": signal_brent_lag_play,
    "eu_close_us_afternoon": signal_eu_close_us_afternoon,
    "eu_gap_open": signal_eu_gap_open,
}


# =============================================================================
# EXECUTION
# =============================================================================

def execute_eu_signals(
    broker,
    signals: dict[str, list[dict]],
    state: dict,
    dry_run: bool = False,
) -> list[dict]:
    """Execute les signaux EU sur le broker appropriate.

    Args:
        broker: broker IBKR par defaut
        signals: {strategy_id: [signal dicts]}
        state: state persistant
        dry_run: True pour simuler

    Returns:
        liste d'ordres executes
    """
    orders = []
    smart_router = None

    for sid, sigs in signals.items():
        if not sigs:
            continue

        for sig in sigs:
            ticker = sig["ticker"]
            direction = sig["direction"]
            qty = sig["qty"]
            sl = sig.get("stop_loss")
            tp = sig.get("take_profit")

            # Determiner le broker d'execution
            exec_broker = broker
            if sig.get("execution_broker") == "alpaca":
                # Cross-broker routing
                try:
                    if smart_router is None:
                        smart_router = _get_smart_router()
                    exec_broker = smart_router.route(
                        symbol=ticker,
                        strategy=sid,
                        asset_type="equity",
                    )
                    logger.info(
                        "  [%s] Routage cross-broker: %s -> %s",
                        sid, ticker, exec_broker.name,
                    )
                except Exception as e:
                    logger.error("  [%s] Erreur SmartRouter: %s", sid, e)
                    continue

                # Pour les ordres cross-broker, calculer SL/TP en prix absolu
                if sig.get("entry_price", 0) <= 0 and "stop_loss_pct" in sig:
                    try:
                        account = exec_broker.authenticate()
                        # Fetch prix via le broker US
                        prices = exec_broker.get_prices(ticker, timeframe="1D", bars=2)
                        bars_data = prices.get("bars", [])
                        if bars_data:
                            current_price = bars_data[-1]["c"]
                            sig["entry_price"] = current_price
                            sl_pct = sig["stop_loss_pct"]
                            tp_pct = sig["take_profit_pct"]
                            if direction == "BUY":
                                sl = round(current_price * (1 - sl_pct), 2)
                                tp = round(current_price * (1 + tp_pct), 2)
                            else:
                                sl = round(current_price * (1 + sl_pct), 2)
                                tp = round(current_price * (1 - tp_pct), 2)
                            # Recalculer qty
                            config = EU_STRATEGIES.get(sid, {})
                            alloc_per_ticker = state.get("allocations", {}).get(sid, {}).get("capital", 5000)
                            alloc_per_ticker /= max(len(config.get("tickers", [ticker])), 1)
                            qty = int(alloc_per_ticker / current_price)
                            if qty <= 0:
                                continue
                    except Exception as e:
                        logger.error("  [%s] Erreur fetch prix US %s: %s", sid, ticker, e)
                        continue

            log_prefix = "[DRY-RUN] " if dry_run else ""
            logger.info(
                "  %sEXEC: %s %dx %s SL=%s TP=%s (strat=%s, broker=%s)",
                log_prefix, direction, qty, ticker, sl, tp, sid,
                getattr(exec_broker, 'name', 'unknown'),
            )

            if dry_run:
                orders.append({
                    "sid": sid, "action": "dry_run",
                    "symbol": ticker, "direction": direction, "qty": qty,
                })
                continue

            try:
                result = exec_broker.create_position(
                    symbol=ticker,
                    direction=direction,
                    qty=qty,
                    stop_loss=sl,
                    take_profit=tp,
                    _authorized_by="paper_portfolio_eu",
                )
                order_id = result.get("orderId", "?")
                status = result.get("status", "?")
                logger.info(
                    "  Ordre soumis: id=%s status=%s (broker=%s)",
                    order_id, status, getattr(exec_broker, 'name', 'unknown'),
                )
                orders.append({
                    "sid": sid, "action": "executed",
                    "symbol": ticker, "direction": direction, "qty": qty,
                    "orderId": order_id, "status": status,
                })

                # Track la position
                state.setdefault("intraday_positions", {})[ticker] = {
                    "strategy": sid,
                    "direction": direction,
                    "entry_price": sig.get("entry_price", 0),
                    "stop_loss": sl,
                    "take_profit": tp,
                    "opened_at": datetime.utcnow().isoformat(),
                    "broker": getattr(exec_broker, 'name', 'unknown'),
                }

            except Exception as e:
                logger.error("  Erreur execution %s (%s): %s", ticker, sid, e)

    return orders


# =============================================================================
# FORCE CLOSE POSITIONS
# =============================================================================

def close_eu_positions(state: dict, dry_run: bool = False):
    """Ferme toutes les positions EU a 17:35 CET (sauf cross-timezone).

    Cross-timezone positions (eu_close_us_afternoon) sont fermees a 22:00 CET.
    """
    intraday_pos = state.get("intraday_positions", {})
    if not intraday_pos:
        logger.info("  Aucune position EU a fermer")
        return

    ibkr = None
    smart_router = None
    closed_tickers = []

    for ticker, pos_info in list(intraday_pos.items()):
        sid = pos_info.get("strategy", "unknown")
        config = EU_STRATEGIES.get(sid, {})
        broker_name = pos_info.get("broker", "ibkr")

        # Verifier si c'est l'heure de fermer cette position
        is_cross_tz = config.get("edge_type") == "cross_timezone" or broker_name == "alpaca"
        if is_cross_tz and not is_force_close_time(sid, config):
            logger.info("  [%s] %s : cross-timezone, pas encore l'heure (22:00 CET)", sid, ticker)
            continue

        log_prefix = "[DRY-RUN] " if dry_run else ""
        logger.info("  %sFERMETURE: %s (strat=%s, broker=%s)", log_prefix, ticker, sid, broker_name)

        if dry_run:
            closed_tickers.append(ticker)
            continue

        try:
            if broker_name == "alpaca":
                if smart_router is None:
                    smart_router = _get_smart_router()
                exec_broker = smart_router.route(symbol=ticker, strategy=sid)
            else:
                if ibkr is None:
                    ibkr = _get_ibkr()
                exec_broker = ibkr

            exec_broker.close_position(ticker, _authorized_by="paper_portfolio_eu_close")
            logger.info("  FERME %s", ticker)
            closed_tickers.append(ticker)

            # Log PnL (approximatif)
            log_strategy_daily_pnl_eu(state, sid, 0)  # PnL reel sera calcule par reconciliation

        except Exception as e:
            logger.error("  Erreur fermeture %s: %s", ticker, e)

    # Retirer les positions fermees du state
    for ticker in closed_tickers:
        intraday_pos.pop(ticker, None)


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_eu(dry_run: bool = False):
    """Run complet du pipeline EU multi-strategies."""
    import pytz
    cet = pytz.timezone("Europe/Paris")
    now = datetime.now(cet)

    logger.info("=" * 60)
    logger.info("  PAPER PORTFOLIO EU — IBKR MULTI-STRATEGY")
    logger.info("=" * 60)
    logger.info("  Date: %s", now.strftime("%Y-%m-%d %H:%M CET"))

    state = load_state()

    # Reset daily PnL si nouveau jour
    today = now.strftime("%Y-%m-%d")
    if state.get("last_run_date") != today:
        state["daily_capital_start"] = state.get("capital", INITIAL_CAPITAL_EU)
        state["daily_pnl"] = 0.0
        state["last_run_date"] = today

    # Connexion IBKR
    try:
        ibkr = _get_ibkr()
        info = ibkr.authenticate()
        equity = info["equity"]
        capital = min(equity, INITIAL_CAPITAL_EU)
        state["capital"] = equity
    except Exception as e:
        logger.error("  Connexion IBKR impossible: %s", e)
        save_state(state)
        return

    logger.info("  Equity IBKR: $%s", f"{equity:,.2f}")
    logger.info("  Capital alloue EU: $%s", f"{capital:,.2f}")
    logger.info("  Mode: %s", "DRY-RUN" if dry_run else "PAPER TRADING")

    # Circuit-breaker
    if check_circuit_breaker_eu(state, equity):
        save_state(state)
        return

    # ── Check fermeture forcee ──
    # Verifier si des positions doivent etre fermees
    any_force_close = False
    for sid, config in EU_STRATEGIES.items():
        if is_force_close_time(sid, config):
            any_force_close = True
            break

    if any_force_close:
        logger.info("  FERMETURE FORCEE — positions EU")
        close_eu_positions(state, dry_run)
        save_state(state)
        return

    # Filtrer les strategies enabled
    enabled_strategies = {
        sid: cfg for sid, cfg in EU_STRATEGIES.items()
        if cfg.get("enabled", False)
    }
    if not enabled_strategies:
        logger.info("  Aucune strategie EU enabled")
        save_state(state)
        return

    # Allocations
    allocations = compute_eu_allocations(enabled_strategies, capital)
    state["allocations"] = allocations

    logger.info("  ALLOCATIONS EU:")
    for sid, alloc in allocations.items():
        name = enabled_strategies[sid]["name"]
        logger.info(
            "    %-30s %5.1f%%  $%10s",
            name, alloc["pct"] * 100, f"{alloc['capital']:,.2f}",
        )

    # Generer les signaux par strategie (avec checks horaires + kill switch)
    logger.info("  SIGNAUX EU:")
    all_signals: dict[str, list[dict]] = {}

    for sid, config in enabled_strategies.items():
        alloc_capital = allocations.get(sid, {}).get("capital", 0)
        name = config["name"]

        # Check horaires de la strategie
        if not is_strategy_active(sid, config):
            logger.info("    %-30s -- hors fenetre horaire", name)
            all_signals[sid] = []
            continue

        # Kill switch
        if check_kill_switch_eu(state, sid, alloc_capital):
            logger.info("    %-30s !! KILL SWITCH (-2%% sur 5j)", name)
            all_signals[sid] = []
            continue

        # Generer le signal via la dispatch map
        signal_fn = SIGNAL_DISPATCH.get(sid)
        if signal_fn is None:
            logger.warning("    %-30s -- pas de signal generator", name)
            all_signals[sid] = []
            continue

        try:
            sigs = signal_fn(ibkr, config, alloc_capital, state)
            all_signals[sid] = sigs
            if sigs:
                for s in sigs:
                    logger.info(
                        "    %-30s >> %s %s @ $%.2f",
                        name, s["direction"], s["ticker"], s.get("entry_price", 0),
                    )
            else:
                logger.info("    %-30s -- aucun signal", name)
        except Exception as e:
            logger.error("    %-30s !! erreur: %s", name, e)
            all_signals[sid] = []

    # Executer
    logger.info("  EXECUTION:")
    total_signals = sum(len(sigs) for sigs in all_signals.values())
    logger.info("  Total signaux EU: %d", total_signals)

    orders = execute_eu_signals(ibkr, all_signals, state, dry_run)

    if not orders:
        logger.info("  Aucun ordre EU")

    # Positions IBKR
    try:
        positions = ibkr.get_positions()
        logger.info("  Positions IBKR: %d", len(positions))
        for p in positions:
            logger.info(
                "    %s %s %ssh @ %s",
                p["symbol"], p["side"], p["qty"], p["avg_entry"],
            )
    except Exception:
        pass

    # Historique
    state.setdefault("history", []).append({
        "date": today,
        "signals": {sid: len(sigs) for sid, sigs in all_signals.items()},
        "orders": len(orders),
        "capital": capital,
    })

    save_state(state)
    logger.info("=" * 60)


def run_intraday_eu(dry_run: bool = False):
    """Execute les strategies EU en mode intraday (appele par cron toutes les 5 min)."""
    # Identique a run_eu — le pipeline EU est intrinsequement intraday
    run_eu(dry_run=dry_run)


# =============================================================================
# STATUS
# =============================================================================

def show_status():
    """Affiche le status IBKR et l'etat du pipeline EU multi-strategies."""
    state = load_state()

    print(f"\n{'='*60}")
    print(f"  PAPER PORTFOLIO EU — MULTI-STRATEGY DASHBOARD")
    print(f"{'='*60}")

    total_capital = state.get("capital", INITIAL_CAPITAL_EU)
    port_return = (total_capital / INITIAL_CAPITAL_EU - 1) * 100

    print(f"  Capital  : ${total_capital:,.2f}")
    print(f"  Return   : {port_return:+.2f}%")

    # Connexion IBKR
    try:
        ibkr = _get_ibkr()
        info = ibkr.authenticate()
        print(f"\n  Compte IBKR ({'PAPER' if info.get('paper') else 'LIVE'}):")
        print(f"    Account : {info['account_number']}")
        print(f"    Equity  : ${info['equity']:>12,.2f}")
        print(f"    Cash    : ${info['cash']:>12,.2f}")

        positions = ibkr.get_positions()
        if positions:
            print(f"\n  Positions ({len(positions)}):")
            for p in positions:
                print(
                    f"    {p['symbol']:10s} {p['side']:5s} "
                    f"{p['qty']:>8}sh @ ${p['avg_entry']:>8.2f}"
                )
    except Exception as e:
        print(f"\n  IBKR: {e}")

    # Strategies
    print(f"\n  Strategies EU ({len(EU_STRATEGIES)}):")
    for sid, cfg in EU_STRATEGIES.items():
        status = "ON " if cfg.get("enabled") else "OFF"
        alloc = state.get("allocations", {}).get(sid, {})
        cap_str = f"${alloc.get('capital', 0):>10,.2f}" if alloc else "      N/A"
        print(
            f"    [{status}] {cfg['name']:<30s} Sharpe={cfg['sharpe']:>6.2f}  "
            f"Alloc={cap_str}"
        )

    # Kill switch status
    pnl_log = state.get("strategy_pnl_log", {})
    if pnl_log:
        print(f"\n  Kill Switch Status:")
        for sid, logs in pnl_log.items():
            recent = logs[-STRATEGY_KILL_SWITCH_DAYS:] if len(logs) >= STRATEGY_KILL_SWITCH_DAYS else logs
            rolling_pnl = sum(e.get("pnl", 0) for e in recent)
            alloc_cap = state.get("allocations", {}).get(sid, {}).get("capital", 0)
            threshold = -alloc_cap * STRATEGY_KILL_SWITCH_PCT if alloc_cap > 0 else 0
            status = "KILL" if (threshold != 0 and rolling_pnl < threshold) else "OK"
            name = EU_STRATEGIES.get(sid, {}).get("name", sid)
            print(f"    {name:<30s} PnL 5j=${rolling_pnl:>+8.2f}  seuil=${threshold:>+8.2f}  [{status}]")

    # Historique
    history = state.get("history", [])
    if history:
        print(f"\n  Derniers runs ({len(history)}):")
        for h in history[-5:]:
            sigs = h.get("signals", {})
            sig_str = " | ".join(f"{k[:8]}={v}" for k, v in sigs.items())
            print(f"    {h['date']}: {sig_str} ({h.get('orders', 0)} ordres)")

    print(f"{'='*60}\n")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper Portfolio EU Multi-Strategy (IBKR)")
    parser.add_argument("--dry-run", action="store_true", help="Simuler sans ordres")
    parser.add_argument("--status", action="store_true", help="Dashboard consolide")
    parser.add_argument("--intraday", action="store_true", help="Mode intraday (cron 5min)")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.intraday:
        run_intraday_eu(dry_run=args.dry_run)
    else:
        run_eu(dry_run=args.dry_run)
