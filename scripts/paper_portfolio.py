#!/usr/bin/env python3
"""
Paper Portfolio Runner — orchestration unifiee des 3 strategies sur Alpaca.

Respecte le pipeline multi-agents :
  1. PortfolioManager : allocation risk-parity (Sharpe-weighted, cap 40%)
  2. ExecutionManager : circuit-breaker drawdown 5%, sizing par allocated_capital (cap 10%)
  3. MonitoringManager : tracking vs benchmark SPY, alpha, P&L consolide

Strategies actives :
  - Momentum Rotation 25 ETFs (mensuel, ROC 3m, crash filter)
  - Pairs Trading MU/AMAT (daily, z-score cointegre)
  - VRP Rotation SVXY/SPY/TLT (mensuel, regime de volatilite)

Usage :
    python scripts/paper_portfolio.py              # execution quotidienne
    python scripts/paper_portfolio.py --dry-run    # sans ordres
    python scripts/paper_portfolio.py --status     # dashboard consolide
    python scripts/paper_portfolio.py --force      # forcer le rebalancement mensuel
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("portfolio")

STATE_FILE = Path(__file__).parent.parent / "paper_portfolio_state.json"

# =============================================================================
# CONFIGURATION
# =============================================================================

INITIAL_CAPITAL = 100_000.0
MAX_DAILY_DRAWDOWN = 0.05       # 5% circuit-breaker
MAX_ALLOCATION_PER_STRATEGY = 0.20  # 20% max par strategie
MAX_POSITION_SIZE = 0.10            # 10% max par position individuelle
MAX_LIVE_POSITIONS = 10             # Max positions simultanees en live
MAX_NET_LONG_EXPOSURE = 0.40        # 40% max exposition nette long
MAX_NET_SHORT_EXPOSURE = 0.20       # 20% max exposition nette short
MAX_SECTOR_EXPOSURE = 0.40          # 40% max dans un meme secteur
DAILY_TRAILING_STOP_PCT = 0.05      # 5% trailing stop sur positions daily
STRATEGY_KILL_SWITCH_PCT = 0.02     # -2% du capital alloue sur 5j rolling = desactive
STRATEGY_KILL_SWITCH_DAYS = 5       # Fenetre rolling pour le kill switch
BENCHMARK = "SPY"

# Jours feries NYSE 2026 (marche FERME)
NYSE_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}

# Jours de fermeture anticipee NYSE 2026 (marche ferme a 13:00 ET)
NYSE_EARLY_CLOSE_2026 = {
    "2026-11-27",  # Veille de Thanksgiving (lendemain)
    "2026-12-24",  # Veille de Noel
}

# Seuil PDT (Pattern Day Trader) : equity minimum pour intraday
PDT_EQUITY_MINIMUM = 25_000.0

STRATEGIES = {
    # === Daily / Monthly (existantes) ===
    "momentum_25etf": {
        "name": "Momentum 25 ETFs",
        "sharpe": 0.88,           # backtest valide
        "frequency": "monthly",
        "multi_asset": True,
    },
    "pairs_mu_amat": {
        "name": "Pairs MU/AMAT",
        "sharpe": 0.94,
        "frequency": "daily",
        "multi_asset": False,
    },
    "vrp_rotation": {
        "name": "VRP SVXY/SPY/TLT",
        "sharpe": 0.75,
        "frequency": "monthly",
        "multi_asset": False,
    },
    # === Intraday (walk-forward validees 2026-03-24) ===
    # === Intraday (re-valide avec horaires stricts 9:35-15:55 ET, 2026-03-24) ===
    "opex_gamma": {
        "name": "OpEx Gamma Pin",
        "sharpe": 10.41,           # re-backtest horaires stricts
        "frequency": "intraday",
        "multi_asset": True,
    },
    "gap_continuation": {
        "name": "Overnight Gap Continuation",
        "sharpe": 5.22,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "dow_seasonal": {
        "name": "Day-of-Week Seasonal",
        "sharpe": 3.42,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "lateday_meanrev": {
        "name": "Late Day Mean Reversion",
        "sharpe": 0.60,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "crypto_proxy_v2": {
        "name": "Crypto-Proxy Regime V2",
        "sharpe": 3.49,
        "frequency": "intraday",
        "multi_asset": True,
    },
    # === Batch optimisations V2 (25 mars 2026) ===
    "orb_v2": {
        "name": "ORB 5-Min V2",
        "sharpe": 2.28,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "meanrev_v2": {
        "name": "Mean Reversion V2",
        "sharpe": 1.44,
        "frequency": "intraday",
        "multi_asset": True,
    },
    # === Mission nuit 25-26 mars 2026 — walk-forward valides ===
    "vwap_micro": {
        "name": "VWAP Micro-Deviation",
        "sharpe": 3.08,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "triple_ema": {
        "name": "Triple EMA Pullback",
        "sharpe": 1.06,
        "frequency": "intraday",
        "multi_asset": True,
    },
    # RETIRES apres re-backtest horaires stricts :
    # - ORB 5-Min : Sharpe -0.05 (ne survit pas aux couts sur univers large)
    # - Earnings Drift : Sharpe -9.55 (overtrade sur small caps)
    # - ML Volume Cluster : Sharpe -1.36
}

# ─── Univers ETFs Momentum ───────────────────────────────────────────────────
MOMENTUM_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "VEA", "VWO",
    "TLT", "IEF", "SHY", "LQD", "HYG", "TIP",
    "GLD", "SLV", "USO", "DBC",
    "XLE", "XLF", "XLK", "XLV", "XLI", "XLP", "XLU",
]
MOMENTUM_LOOKBACK = 3   # mois
MOMENTUM_TOP_N = 2
MOMENTUM_CRASH_SMA = 200


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning("State file corrompu, reconstruction depuis Alpaca")

    # Pas de state file (premier run ou redeploy Railway)
    # Reconstruire depuis Alpaca
    logger.info("Reconstruction du state depuis Alpaca...")
    state = {
        "capital": INITIAL_CAPITAL,
        "positions": {},
        "allocations": {},
        "last_monthly": None,
        "daily_capital_start": INITIAL_CAPITAL,
        "daily_pnl": 0.0,
        "benchmark_start_price": None,
        "benchmark_start_date": None,
        "history": [],
        "intraday_positions": {},
    }
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        account = client.authenticate()
        state["capital"] = account["equity"]
        state["daily_capital_start"] = account["equity"]

        # Reconstruire les positions depuis Alpaca
        positions = client.get_positions()
        for p in positions:
            sym = p["symbol"]
            # Tracker comme position intraday par defaut
            state["intraday_positions"][sym] = {
                "strategy": "unknown",
                "direction": "LONG" if float(p.get("qty", 0)) > 0 else "SHORT",
                "entry_price": float(p.get("avg_entry_price", 0)),
                "opened_at": datetime.now(timezone.utc).isoformat(),
            }
        logger.info(f"  Equity: ${account['equity']:,.2f}, {len(positions)} positions")
    except Exception as e:
        logger.warning(f"  Impossible de reconstruire depuis Alpaca: {e}")

    save_state(state)
    return state


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except IOError as e:
        logger.warning(f"Impossible de sauvegarder le state: {e}")


# =============================================================================
# PORTFOLIO ALLOCATION (replica de PortfolioManagerAgent._compute_allocations)
# =============================================================================

def compute_allocations(strategies: dict, total_capital: float) -> dict[str, dict]:
    """
    Allocation risk-parity basee sur le Sharpe ratio.
    Cap strict par strategie. Redistribution iterative du surplus.
    """
    sharpes = {sid: max(s["sharpe"], 0.1) for sid, s in strategies.items()}
    total_sharpe = sum(sharpes.values())

    # Allocation initiale proportionnelle au Sharpe
    allocations = {sid: sharpe / total_sharpe for sid, sharpe in sharpes.items()}

    # Cap iteratif : redistribuer le surplus aux non-cappes
    for _ in range(10):  # Max 10 iterations
        capped = {}
        uncapped = {}
        surplus = 0.0
        for sid, pct in allocations.items():
            if pct > MAX_ALLOCATION_PER_STRATEGY:
                capped[sid] = MAX_ALLOCATION_PER_STRATEGY
                surplus += pct - MAX_ALLOCATION_PER_STRATEGY
            else:
                uncapped[sid] = pct

        if surplus == 0:
            break  # Rien a redistribuer

        # Redistribuer le surplus proportionnellement aux non-cappes
        uncapped_total = sum(uncapped.values())
        if uncapped_total > 0:
            for sid in uncapped:
                uncapped[sid] += surplus * (uncapped[sid] / uncapped_total)

        allocations = {**capped, **uncapped}

    result = {}
    for sid, pct in allocations.items():
        allocated = total_capital * pct
        result[sid] = {
            "pct": round(pct, 4),
            "capital": round(allocated, 2),
            "max_position": round(allocated, 2),
        }

    return result


# =============================================================================
# SIGNAL GENERATORS
# =============================================================================

def signal_momentum(allocated_capital: float, state: dict, force_monthly: bool) -> dict:
    """Genere le signal pour la strategie Momentum Rotation."""
    from core.data.loader import OHLCVLoader

    # Verifier si c'est un jour de rebalancement mensuel
    now = datetime.now(timezone.utc)
    last = state.get("last_monthly")
    if last and not force_monthly:
        last_dt = datetime.fromisoformat(last)
        if last_dt.month == now.month and last_dt.year == now.year:
            return {"action": "hold", "reason": "pas de rebalancement ce mois"}

    # Crash filter
    try:
        data_spy = OHLCVLoader.from_yfinance("SPY", "1D", period="2y")
        spy_close = data_spy.df["close"]
        spy_price = float(spy_close.iloc[-1])
        spy_sma = float(spy_close.rolling(MOMENTUM_CRASH_SMA).mean().iloc[-1])

        if spy_price < spy_sma:
            return {
                "action": "sell_all",
                "reason": f"crash filter SPY={spy_price:.0f} < SMA{MOMENTUM_CRASH_SMA}={spy_sma:.0f}",
                "targets": [],
            }
    except Exception as e:
        logger.warning(f"Momentum crash filter error: {e}")

    # Ranking momentum
    scores = {}
    for ticker in MOMENTUM_ETFS:
        try:
            data = OHLCVLoader.from_yfinance(ticker, "1D", period="1y")
            close = data.df["close"]
            n_bars = MOMENTUM_LOOKBACK * 21
            if len(close) > n_bars:
                scores[ticker] = float(close.iloc[-1] / close.iloc[-n_bars] - 1)
        except Exception:
            pass

    ranked = sorted(scores, key=scores.get, reverse=True)
    targets = ranked[:MOMENTUM_TOP_N]

    return {
        "action": "rebalance",
        "targets": targets,
        "scores": {t: round(scores[t], 4) for t in targets},
        "capital": allocated_capital,
    }


def signal_pairs(allocated_capital: float, state: dict) -> dict:
    """Genere le signal pour la strategie Pairs MU/AMAT."""
    from core.data.loader import OHLCVLoader

    data_a = OHLCVLoader.from_yfinance("MU", "1D", period="1y")
    data_b = OHLCVLoader.from_yfinance("AMAT", "1D", period="1y")

    close_a = data_a.df["close"]
    close_b = data_b.df["close"]
    df = pd.concat([close_a.rename("a"), close_b.rename("b")], axis=1).dropna()

    # Hedge ratio OLS
    log_a = np.log(df["a"].iloc[-120:])
    log_b = np.log(df["b"].iloc[-120:])
    beta = float((log_b * log_a).sum() / (log_b * log_b).sum())
    alpha = float((log_a - beta * log_b).mean())

    # Spread et z-score
    spread = np.log(df["a"]) - beta * np.log(df["b"]) - alpha
    window = spread.iloc[-30:]
    mu = window.mean()
    sigma = window.std()
    zscore = float((spread.iloc[-1] - mu) / sigma) if sigma > 0 else 0.0

    current_pos = state.get("positions", {}).get("pairs_mu_amat")

    if current_pos is None:
        # Pas de position — chercher entree
        if zscore > 2.0:
            return {"action": "open", "direction": "short_a_long_b",
                    "zscore": zscore, "beta": beta, "capital": allocated_capital}
        elif zscore < -2.0:
            return {"action": "open", "direction": "long_a_short_b",
                    "zscore": zscore, "beta": beta, "capital": allocated_capital}
        else:
            return {"action": "hold", "zscore": zscore, "reason": f"|z|={abs(zscore):.2f} < 2.0"}
    else:
        # Position ouverte — chercher sortie
        if abs(zscore) < 0.5:
            return {"action": "close", "reason": f"mean reversion z={zscore:+.2f}", "zscore": zscore}
        elif abs(zscore) > 4.0:
            return {"action": "close", "reason": f"stop loss z={zscore:+.2f}", "zscore": zscore}
        else:
            return {"action": "hold", "zscore": zscore, "reason": "hold position"}


def signal_vrp(allocated_capital: float, state: dict, force_monthly: bool) -> dict:
    """Genere le signal pour la strategie VRP."""
    from core.data.loader import OHLCVLoader

    now = datetime.now(timezone.utc)
    last = state.get("last_monthly")
    if last and not force_monthly:
        last_dt = datetime.fromisoformat(last)
        if last_dt.month == now.month and last_dt.year == now.year:
            return {"action": "hold", "reason": "pas de rebalancement ce mois"}

    data = OHLCVLoader.from_yfinance("SPY", "1D", period="1y")
    returns = data.df["close"].pct_change().dropna()
    vol_20d = float(returns.iloc[-20:].std() * np.sqrt(252) * 100)
    vol_60d = float(returns.iloc[-60:].std() * np.sqrt(252) * 100)
    trend = "rising" if vol_20d > vol_60d else "falling"

    if vol_20d > 25 and trend == "rising":
        target = "TLT"
    elif vol_20d > 20 and trend == "falling":
        target = "SVXY"
    else:
        target = "SPY"

    return {
        "action": "rebalance",
        "targets": [target],
        "vol_20d": vol_20d,
        "vol_trend": trend,
        "capital": allocated_capital,
    }


# =============================================================================
# INTRADAY SIGNAL GENERATORS
# =============================================================================

def signal_intraday(strategy_id: str, allocated_capital: float, state: dict) -> dict:
    """
    Genere les signaux intraday en fetchant les barres 5M du jour depuis Alpaca
    et en executant la strategie correspondante.
    """
    import zoneinfo
    from datetime import date as dt_date

    et = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    today = now_et.date()

    # Ne trader que pendant les heures de marche
    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 35):
        return {"action": "hold", "reason": "avant 9:35 ET"}
    if now_et.hour >= 16:
        return {"action": "hold", "reason": "apres 16:00 ET"}

    # Importer les strategies
    backtester_path = str(Path(__file__).parent.parent / "intraday-backtesterV2")
    if backtester_path not in sys.path:
        sys.path.insert(0, backtester_path)

    from strategies import (
        OpExGammaPinStrategy,
        DayOfWeekSeasonalStrategy,
        OvernightGapContinuationStrategy,
        LateDayMeanReversionStrategy,
    )
    from strategies.crypto_proxy_regime_v2 import CryptoProxyRegimeV2Strategy
    from strategies.orb_5min_v2 import ORB5MinV2Strategy
    from strategies.mean_reversion_v2 import MeanReversionV2Strategy
    from strategies.vwap_micro_reversion import VWAPMicroReversionStrategy
    from strategies.triple_ema_pullback import TripleEMAPullbackStrategy

    STRAT_MAP = {
        "opex_gamma": OpExGammaPinStrategy,
        "dow_seasonal": DayOfWeekSeasonalStrategy,
        "gap_continuation": OvernightGapContinuationStrategy,
        "lateday_meanrev": LateDayMeanReversionStrategy,
        "crypto_proxy_v2": CryptoProxyRegimeV2Strategy,
        "orb_v2": ORB5MinV2Strategy,
        "meanrev_v2": MeanReversionV2Strategy,
        "vwap_micro": VWAPMicroReversionStrategy,
        "triple_ema": TripleEMAPullbackStrategy,
    }

    strat_class = STRAT_MAP.get(strategy_id)
    if not strat_class:
        return {"action": "hold", "reason": f"strategie inconnue: {strategy_id}"}

    strategy = strat_class()
    required_tickers = strategy.get_required_tickers()

    # Fetch les barres 5M du jour depuis Alpaca
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        import os

        client = StockHistoricalDataClient(
            api_key=os.getenv("ALPACA_API_KEY"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
        )

        start_dt = datetime.combine(today, datetime.min.time()).replace(tzinfo=et)
        end_dt = now_et

        from alpaca.data.enums import DataFeed
        request = StockBarsRequest(
            symbol_or_symbols=required_tickers[:50],  # Alpaca max 50/batch
            timeframe=TimeFrame(5, TimeFrame.Minute.unit),
            start=start_dt,
            end=end_dt,
            feed=DataFeed.IEX,  # Feed gratuit (SIP necessite abonnement)
        )
        bars = client.get_stock_bars(request)

        # Construire les DataFrames
        data = {}
        if bars:
            for ticker in required_tickers:
                if ticker in bars.data and bars.data[ticker]:
                    rows = []
                    for bar in bars.data[ticker]:
                        rows.append({
                            "timestamp": bar.timestamp,
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": int(bar.volume),
                            "vwap": float(bar.vwap) if bar.vwap else None,
                        })
                    if rows:
                        df = pd.DataFrame(rows)
                        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                        df = df.set_index("timestamp").sort_index()
                        df.index = df.index.tz_convert("US/Eastern")
                        data[ticker] = df

        if not data:
            return {"action": "hold", "reason": "pas de donnees intraday"}

    except Exception as e:
        logger.warning(f"WARNING API ALPACA: erreur fetch intraday — {type(e).__name__}: {e}")
        return {"action": "hold", "reason": f"erreur fetch: {e}"}

    # Generer les signaux
    try:
        signals = strategy.generate_signals(data, today)
    except Exception as e:
        logger.error(f"Erreur generate_signals {strategy_id}: {e}")
        return {"action": "hold", "reason": f"erreur signal: {e}"}

    if not signals:
        return {"action": "hold", "reason": "aucun signal"}

    # Prendre le premier signal (le plus fort)
    sig = signals[0]
    return {
        "action": "intraday_trade",
        "ticker": sig.ticker,
        "direction": sig.action,  # "LONG" ou "SHORT"
        "entry_price": sig.entry_price,
        "stop_loss": sig.stop_loss,
        "take_profit": sig.take_profit,
        "capital": allocated_capital,
        "metadata": sig.metadata,
    }


# =============================================================================
# FERMETURE FORCEE — 15:55 ET
# =============================================================================

def _close_all_intraday_positions(state: dict, dry_run: bool = False):
    """Ferme toutes les positions intraday + annule les ordres pendants. Appele a 15:55 ET."""
    from core.alpaca_client.client import AlpacaClient
    client = AlpacaClient.from_env()

    # ETAPE 1 : Annuler TOUS les ordres pendants (CRITIQUE — evite les orphelins)
    if not dry_run:
        try:
            tc = client._get_trading_client()
            tc.cancel_orders()
            print("    Ordres pendants annules")
        except Exception as e:
            logger.error(f"    Erreur annulation ordres: {e}")

    # ETAPE 2 : Fermer toutes les positions Alpaca
    try:
        positions = client.get_positions()
        if not positions:
            print("    Aucune position a fermer")
            # Reset le state intraday
            state["intraday_positions"] = {}
            return

        total_pnl = 0.0
        for p in positions:
            sym = p["symbol"]
            pnl = p.get("unrealized_pl", 0)
            total_pnl += pnl
            if dry_run:
                print(f"    [DRY-RUN] Fermerait {sym} ({p['qty']} shares, P&L ${pnl:+.2f})")
            else:
                try:
                    client.close_position(sym, _authorized_by="paper_portfolio_eod_close")
                    print(f"    FERME {sym} ({p['qty']} shares, P&L ${pnl:+.2f})")
                except Exception as e:
                    logger.error(f"    Erreur fermeture {sym}: {e}")

        print(f"    P&L total cloture: ${total_pnl:+.2f}")

        # Logger le PnL par strategie pour le kill switch
        intraday_pos = state.get("intraday_positions", {})
        for p in positions:
            sym = p["symbol"]
            pnl = p.get("unrealized_pl", 0)
            # Trouver quelle strategie a ouvert cette position
            pos_info = intraday_pos.get(sym, {})
            sid = pos_info.get("strategy", "unknown")
            if sid != "unknown":
                log_strategy_daily_pnl(state, sid, pnl)

    except Exception as e:
        logger.error(f"    Erreur Alpaca: {e}")

    # Reset le state intraday
    state["intraday_positions"] = {}


# =============================================================================
# EXECUTION (avec circuit-breaker)
# =============================================================================

def get_market_close_hour(date_str: str | None = None) -> tuple[int, int]:
    """
    Retourne l'heure de fermeture du marche (hour, minute) en ET.
    13:00 pour les early close, 16:00 sinon.
    """
    if date_str is None:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        date_str = datetime.now(et).strftime("%Y-%m-%d")
    if date_str in NYSE_EARLY_CLOSE_2026:
        return (13, 0)
    return (16, 0)


def is_us_market_open() -> bool:
    """Verifie si le marche US est ouvert (9:30-fermeture ET, lun-ven, hors jours feries)."""
    import zoneinfo
    et = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    # Weekend
    if now_et.weekday() >= 5:
        return False
    # Jours feries NYSE
    today_str = now_et.strftime("%Y-%m-%d")
    if today_str in NYSE_HOLIDAYS_2026:
        return False
    # Horaires (avec early close)
    close_h, close_m = get_market_close_hour(today_str)
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def _check_daily_trailing_stops(client, current_positions: dict, state: dict):
    """
    Verifie les positions daily/monthly et ferme celles qui depassent
    le trailing stop de 5% (DAILY_TRAILING_STOP_PCT).
    Logue un WARNING pour les positions daily sans SL cote Alpaca.
    """
    daily_strategies = {sid for sid, s in STRATEGIES.items()
                        if s["frequency"] in ("daily", "monthly")}

    state_positions = state.get("positions", {})
    for sid in daily_strategies:
        pos_info = state_positions.get(sid, {})
        symbols = pos_info.get("symbols", [])
        for sym in symbols:
            if sym not in current_positions:
                continue

            p = current_positions[sym]
            avg_entry = float(p.get("avg_entry", 0))
            qty = float(p.get("qty", 0))
            market_val = float(p.get("market_val", 0))

            if avg_entry <= 0 or qty == 0:
                continue

            # Calculer le prix courant implicite
            current_price = abs(market_val / qty) if qty != 0 else 0

            # Trailing stop check : 5% de perte depuis l'entree
            if qty > 0:
                # Long position
                stop_price = avg_entry * (1 - DAILY_TRAILING_STOP_PCT)
                if current_price < stop_price:
                    logger.warning(
                        f"TRAILING STOP DAILY: {sym} (strat={sid}) "
                        f"prix=${current_price:.2f} < stop=${stop_price:.2f} "
                        f"(entree=${avg_entry:.2f}, -5%). FERMETURE."
                    )
                    try:
                        client.close_position(sym, _authorized_by="paper_portfolio_trailing_stop")
                    except Exception as e:
                        logger.error(f"  Erreur fermeture trailing stop {sym}: {e}")
            else:
                # Short position
                stop_price = avg_entry * (1 + DAILY_TRAILING_STOP_PCT)
                if current_price > stop_price:
                    logger.warning(
                        f"TRAILING STOP DAILY: {sym} (strat={sid}) "
                        f"prix=${current_price:.2f} > stop=${stop_price:.2f} "
                        f"(entree=${avg_entry:.2f}, +5%). FERMETURE."
                    )
                    try:
                        client.close_position(sym, _authorized_by="paper_portfolio_trailing_stop")
                    except Exception as e:
                        logger.error(f"  Erreur fermeture trailing stop {sym}: {e}")

            # Avertissement si pas de SL visible
            logger.info(
                f"  [{sid}] Position daily {sym}: "
                f"qty={qty}, entry=${avg_entry:.2f}, "
                f"current=${current_price:.2f}, "
                f"trailing_stop=${stop_price:.2f}"
            )


def check_strategy_kill_switch(state: dict, strategy_id: str, allocated_capital: float) -> bool:
    """
    Kill switch par strategie : si PnL rolling sur N jours < -2% du capital alloue,
    la strategie est desactivee.
    Retourne True si la strategie est KILL (ne doit pas trader).
    """
    kill_log = state.get("strategy_pnl_log", {}).get(strategy_id, [])
    if len(kill_log) < STRATEGY_KILL_SWITCH_DAYS:
        return False  # Pas assez d'historique

    # Prendre les N derniers jours
    recent = kill_log[-STRATEGY_KILL_SWITCH_DAYS:]
    rolling_pnl = sum(entry.get("pnl", 0) for entry in recent)
    threshold = -allocated_capital * STRATEGY_KILL_SWITCH_PCT

    if rolling_pnl < threshold:
        logger.critical(
            f"KILL SWITCH [{strategy_id}]: PnL rolling {STRATEGY_KILL_SWITCH_DAYS}j = "
            f"${rolling_pnl:,.2f} < seuil ${threshold:,.2f} "
            f"(-{STRATEGY_KILL_SWITCH_PCT*100}% de ${allocated_capital:,.0f}). "
            f"Strategie DESACTIVEE."
        )
        try:
            from core.telegram_alert import send_kill_switch
            strat_name = STRATEGIES.get(strategy_id, {}).get("name", strategy_id)
            send_kill_switch(strat_name, rolling_pnl, threshold)
        except Exception:
            pass
        return True
    return False


def log_strategy_daily_pnl(state: dict, strategy_id: str, daily_pnl: float):
    """Enregistre le PnL quotidien d'une strategie pour le kill switch."""
    from datetime import date as dt_date
    today = dt_date.today().isoformat()
    state.setdefault("strategy_pnl_log", {}).setdefault(strategy_id, [])
    log = state["strategy_pnl_log"][strategy_id]

    # Eviter les doublons
    if log and log[-1].get("date") == today:
        log[-1]["pnl"] = log[-1].get("pnl", 0) + daily_pnl
    else:
        log.append({"date": today, "pnl": round(daily_pnl, 2)})

    # Garder max 30 jours
    if len(log) > 30:
        state["strategy_pnl_log"][strategy_id] = log[-30:]


def execute_orders(signals: dict, allocations: dict, state: dict,
                   dry_run: bool, total_capital: float = INITIAL_CAPITAL) -> list[dict]:
    """Execute les ordres via Alpaca avec respect des allocations."""
    if dry_run:
        logger.info("[DRY-RUN] Aucun ordre execute")
        return []

    if not is_us_market_open():
        logger.warning("MARCHE US FERME — aucun ordre execute. "
                       "Reessayer pendant les heures de marche (15:30-22:00 Paris)")
        return []

    from core.alpaca_client.client import AlpacaClient
    client = AlpacaClient.from_env()
    account = client.authenticate()
    equity = account["equity"]

    # Circuit-breaker : verifier le drawdown journalier
    daily_start = state.get("daily_capital_start", equity)
    if daily_start > 0:
        daily_dd = (equity - daily_start) / daily_start
        if daily_dd < -MAX_DAILY_DRAWDOWN:
            logger.critical(
                f"CIRCUIT-BREAKER DECLENCHE: DD journalier {daily_dd*100:.1f}% > "
                f"{MAX_DAILY_DRAWDOWN*100}% — AUCUN ordre execute. "
                f"Equity=${equity:,.2f}, start=${daily_start:,.2f}")
            try:
                from core.telegram_alert import send_circuit_breaker
                send_circuit_breaker(equity, daily_start, daily_dd)
            except Exception:
                pass
            return []

    orders = []
    positions_list = client.get_positions()
    current_positions = {p["symbol"]: p for p in positions_list}

    # === PDT GUARD : si equity < $25K, bloquer toutes les strategies intraday ===
    pdt_blocked = equity < PDT_EQUITY_MINIMUM
    if pdt_blocked:
        logger.warning(
            f"PDT GUARD: equity ${equity:,.2f} < ${PDT_EQUITY_MINIMUM:,.2f} — "
            f"strategies intraday DESACTIVEES"
        )

    # === CHECK TRAILING STOP sur positions daily/monthly (5%) ===
    _check_daily_trailing_stops(client, current_positions, state)

    # Guard : max positions simultanees
    if len(current_positions) >= MAX_LIVE_POSITIONS:
        logger.warning(
            f"MAX POSITIONS ATTEINT ({len(current_positions)}/{MAX_LIVE_POSITIONS}) "
            f"— aucun nouvel ordre")
        return []

    # Guard : exposition directionnelle nette
    long_exposure = sum(
        float(p.get("market_val", 0)) for p in positions_list
        if float(p.get("qty", 0)) > 0
    )
    short_exposure = abs(sum(
        float(p.get("market_val", 0)) for p in positions_list
        if float(p.get("qty", 0)) < 0
    ))
    net_long_pct = long_exposure / equity if equity > 0 else 0
    net_short_pct = short_exposure / equity if equity > 0 else 0

    exposure_blocked = False
    if net_long_pct > MAX_NET_LONG_EXPOSURE:
        logger.warning(f"EXPOSITION LONG {net_long_pct*100:.1f}% > {MAX_NET_LONG_EXPOSURE*100}% — pas de nouveau LONG")
        exposure_blocked = "LONG"
    if net_short_pct > MAX_NET_SHORT_EXPOSURE:
        logger.warning(f"EXPOSITION SHORT {net_short_pct*100:.1f}% > {MAX_NET_SHORT_EXPOSURE*100}% — pas de nouveau SHORT")
        exposure_blocked = "SHORT"

    for sid, signal in signals.items():
        alloc = allocations.get(sid, {})
        max_capital = alloc.get("max_position", 0)

        if signal["action"] == "sell_all":
            # Vendre tout ce qui appartient a cette strategie
            strat_pos = state.get("positions", {}).get(sid, {})
            for sym in strat_pos.get("symbols", []):
                if sym in current_positions:
                    try:
                        client.close_position(sym, _authorized_by="paper_portfolio")
                        orders.append({"sid": sid, "action": "sell", "symbol": sym})
                        logger.info(f"  [{sid}] VENDU {sym}")
                    except Exception as e:
                        logger.error(f"  [{sid}] Erreur vente {sym}: {e}")

        elif signal["action"] == "rebalance":
            targets = signal.get("targets", [])
            strat_pos = state.get("positions", {}).get(sid, {})
            current_syms = set(strat_pos.get("symbols", []))
            target_set = set(targets)

            # Vendre ce qui n'est plus dans les targets
            for sym in current_syms - target_set:
                if sym in current_positions:
                    try:
                        client.close_position(sym, _authorized_by="paper_portfolio")
                        orders.append({"sid": sid, "action": "sell", "symbol": sym})
                        logger.info(f"  [{sid}] VENDU {sym}")
                    except Exception as e:
                        logger.error(f"  [{sid}] Erreur vente {sym}: {e}")

            # Acheter les nouveaux targets — avec bracket SL broker-side
            notional_each = max_capital / len(targets) * 0.95 if targets else 0
            for sym in target_set - current_syms:
                if notional_each > 10:
                    try:
                        # Fetch prix actuel pour convertir notional -> qty (bracket exige qty)
                        positions_data = client.get_positions()
                        # Utiliser le dernier prix connu via Alpaca
                        from alpaca.data.historical import StockHistoricalDataClient
                        import os
                        data_client = StockHistoricalDataClient(
                            api_key=os.getenv("ALPACA_API_KEY"),
                            secret_key=os.getenv("ALPACA_SECRET_KEY"),
                        )
                        from alpaca.data.requests import StockLatestQuoteRequest
                        quote = data_client.get_stock_latest_quote(
                            StockLatestQuoteRequest(symbol_or_symbols=sym)
                        )
                        price = float(quote[sym].ask_price or quote[sym].bid_price or 0)
                        if price <= 0:
                            # Fallback sans bracket
                            result = client.create_position(sym, "BUY", notional=round(notional_each, 2), _authorized_by="paper_portfolio")
                        else:
                            qty = int(notional_each / price)
                            if qty < 1:
                                continue
                            stop_loss = round(price * (1 - DAILY_TRAILING_STOP_PCT), 2)  # SL a -5%
                            result = client.create_position(
                                sym, "BUY", qty=qty,
                                stop_loss=stop_loss,
                                _authorized_by="paper_portfolio"
                            )
                            sl_info = f" SL=${stop_loss:.2f}" if result.get("bracket") else " (no bracket)"
                            logger.info(f"  [{sid}] ACHETE {sym} {qty} shares @ ~${price:.2f}{sl_info}")
                        orders.append({"sid": sid, "action": "buy", "symbol": sym,
                                      "notional": notional_each})
                    except Exception as e:
                        logger.error(f"  [{sid}] Erreur achat {sym}: {e}")

            # Mettre a jour le state
            if "positions" not in state:
                state["positions"] = {}
            state["positions"][sid] = {"symbols": targets}

        elif signal["action"] == "open" and "direction" in signal:
            # Pairs trading : open
            direction = signal["direction"]
            capital = min(signal.get("capital", 10000), max_capital)
            notional = capital / 2  # moitie par jambe

            sym_a, sym_b = "MU", "AMAT"
            if direction == "long_a_short_b":
                dir_a, dir_b = "BUY", "SELL"
            else:
                dir_a, dir_b = "SELL", "BUY"

            try:
                # Pour les shorts, utiliser qty entiere (Alpaca rejette notional short)
                # Ajout SL broker-side a 5% pour chaque jambe
                from core.data.loader import OHLCVLoader
                for sym, dir_side in [(sym_a, dir_a), (sym_b, dir_b)]:
                    price = float(OHLCVLoader.from_yfinance(sym, "1D", period="5d").df["close"].iloc[-1])
                    qty = int(notional / price)
                    if qty < 1:
                        continue
                    if dir_side == "SELL":
                        stop_loss = round(price * (1 + DAILY_TRAILING_STOP_PCT), 2)
                    else:
                        stop_loss = round(price * (1 - DAILY_TRAILING_STOP_PCT), 2)
                    client.create_position(
                        sym, dir_side, qty=qty,
                        stop_loss=stop_loss,
                        _authorized_by="paper_portfolio"
                    )
                orders.append({"sid": sid, "action": "open_pair",
                              "direction": direction, "notional_per_leg": notional})
                logger.info(f"  [{sid}] PAIR {direction}: {sym_a} {dir_a} + {sym_b} {dir_b} ${notional:,.0f}/leg")
                state.setdefault("positions", {})[sid] = {
                    "symbols": [sym_a, sym_b], "direction": direction}
            except Exception as e:
                logger.error(f"  [{sid}] Erreur pairs: {e}")

        elif signal["action"] == "close":
            # Pairs trading : close
            for sym in ["MU", "AMAT"]:
                if sym in current_positions:
                    try:
                        client.close_position(sym, _authorized_by="paper_portfolio")
                        logger.info(f"  [{sid}] FERME {sym}")
                    except Exception:
                        pass
            orders.append({"sid": sid, "action": "close_pair"})
            state.get("positions", {}).pop(sid, None)

        elif signal["action"] == "intraday_trade":
            # Intraday : buy/sell avec stop et target
            ticker = signal["ticker"]
            direction = signal["direction"]
            capital = min(signal.get("capital", 5000), max_capital)

            # PDT Guard : bloquer les intraday si equity < $25K
            if pdt_blocked:
                logger.info(f"  [{sid}] BLOQUE {direction} {ticker} (PDT guard: equity < $25K)")
                continue

            # Guard exposition : bloquer si exposition nette depasse le cap
            if exposure_blocked == "LONG" and direction == "LONG":
                logger.info(f"  [{sid}] BLOQUE {direction} {ticker} (exposition long max)")
                continue
            if exposure_blocked == "SHORT" and direction == "SHORT":
                logger.info(f"  [{sid}] BLOQUE {direction} {ticker} (exposition short max)")
                continue

            # Guard conflit : ne pas ouvrir si position existante en sens oppose
            if ticker in current_positions:
                existing_qty = float(current_positions[ticker].get("qty", 0))
                if (direction == "LONG" and existing_qty < 0) or \
                   (direction == "SHORT" and existing_qty > 0):
                    logger.warning(f"  [{sid}] CONFLIT {direction} {ticker} — position opposee existante, skip")
                    continue

            # Ne pas ouvrir si on a deja une position intraday sur ce ticker
            intraday_pos = state.get("intraday_positions", {})
            if ticker in intraday_pos:
                continue

            # Position sizing : 15% du capital alloue, max 10% du capital total
            entry_price = signal.get("entry_price", 0)
            if entry_price <= 0:
                continue
            max_pos = total_capital * MAX_POSITION_SIZE  # 10% du capital total
            notional = min(capital * 0.15, max_pos)

            side = "BUY" if direction == "LONG" else "SELL"
            stop_loss = signal.get("stop_loss")
            take_profit = signal.get("take_profit")

            try:
                # Toujours convertir en qty pour supporter les bracket orders
                qty = int(notional / entry_price)
                if qty < 1:
                    continue

                result = client.create_position(
                    ticker, side, qty=qty,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    _authorized_by="paper_portfolio_intraday"
                )

                filled_price = result.get("filled_price") or entry_price
                sl_info = f" SL=${stop_loss:.2f}" if stop_loss else ""
                tp_info = f" TP=${take_profit:.2f}" if take_profit else ""
                bracket_info = " [BRACKET]" if result.get("bracket") else ""

                orders.append({
                    "sid": sid, "action": "intraday_open", "symbol": ticker,
                    "direction": direction, "qty": qty,
                    "entry_price": entry_price,
                    "filled_price": filled_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "bracket": result.get("bracket", False),
                })
                logger.info(
                    f"  [{sid}] INTRADAY {direction} {ticker} "
                    f"{qty} shares @ ${filled_price:.2f}{sl_info}{tp_info}{bracket_info}"
                )

                # Track la position
                state.setdefault("intraday_positions", {})[ticker] = {
                    "strategy": sid,
                    "direction": direction,
                    "entry_price": entry_price,
                    "stop_loss": signal.get("stop_loss"),
                    "take_profit": signal.get("take_profit"),
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                logger.error(f"  [{sid}] Erreur intraday {ticker}: {e}")

    return orders


# =============================================================================
# BENCHMARK TRACKING
# =============================================================================

def get_benchmark_price() -> float:
    from core.data.loader import OHLCVLoader
    data = OHLCVLoader.from_yfinance(BENCHMARK, "1D", period="5d")
    return float(data.df["close"].iloc[-1])


# =============================================================================
# MAIN
# =============================================================================

def run(dry_run: bool = False, force: bool = False):
    now = datetime.now(timezone.utc)
    state = load_state()

    # Init benchmark tracking
    if state.get("benchmark_start_price") is None:
        state["benchmark_start_price"] = get_benchmark_price()
        state["benchmark_start_date"] = now.isoformat()

    # Reset daily PnL si nouveau jour
    today = now.strftime("%Y-%m-%d")
    if state.get("last_run_date") != today:
        state["daily_capital_start"] = state.get("capital", INITIAL_CAPITAL)
        state["daily_pnl"] = 0.0
        state["last_run_date"] = today

    # Utiliser l'equity ACTUELLE Alpaca (pas le capital initial)
    try:
        from core.alpaca_client.client import AlpacaClient
        _client = AlpacaClient.from_env()
        _account = _client.authenticate()
        total_capital = _account["equity"]
        state["capital"] = total_capital
    except Exception:
        total_capital = state.get("capital", INITIAL_CAPITAL)

    print(f"\n{'='*70}")
    print(f"  PAPER PORTFOLIO — EXECUTION UNIFIEE")
    print(f"{'='*70}")
    print(f"  Date     : {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Capital  : ${total_capital:,.2f} (equity Alpaca)")
    print(f"  Mode     : {'DRY-RUN' if dry_run else 'PAPER TRADING'}")

    # 1. Calculer les allocations sur capital ACTUEL
    allocations = compute_allocations(STRATEGIES, total_capital)
    state["allocations"] = allocations

    print(f"\n  ALLOCATIONS (risk-parity, cap 40%):")
    for sid, alloc in allocations.items():
        name = STRATEGIES[sid]["name"]
        print(f"    {name:<25} {alloc['pct']*100:>5.1f}%  ${alloc['capital']:>10,.2f}")

    # 2. Generer les signaux
    print(f"\n  SIGNAUX:")
    signals = {}

    sig_mom = signal_momentum(allocations["momentum_25etf"]["capital"], state, force)
    signals["momentum_25etf"] = sig_mom
    print(f"    Momentum : {sig_mom['action']} — {sig_mom.get('reason', sig_mom.get('targets', ''))}")

    sig_pairs = signal_pairs(allocations["pairs_mu_amat"]["capital"], state)
    signals["pairs_mu_amat"] = sig_pairs
    print(f"    Pairs    : {sig_pairs['action']} — z={sig_pairs.get('zscore', 0):+.3f} {sig_pairs.get('reason', '')}")

    sig_vrp = signal_vrp(allocations["vrp_rotation"]["capital"], state, force)
    signals["vrp_rotation"] = sig_vrp
    print(f"    VRP      : {sig_vrp['action']} — {sig_vrp.get('reason', sig_vrp.get('targets', ''))}")

    # 3. Executer avec circuit-breaker
    print(f"\n  EXECUTION:")
    orders = execute_orders(signals, allocations, state, dry_run, total_capital)

    if not orders:
        print(f"    Aucun ordre")

    # 4. Marquer le rebalancement mensuel
    if force or sig_mom["action"] != "hold":
        state["last_monthly"] = now.isoformat()

    # 5. Benchmark tracking
    spy_now = get_benchmark_price()
    spy_start = state.get("benchmark_start_price", spy_now)
    spy_return = (spy_now / spy_start - 1) * 100 if spy_start > 0 else 0
    port_return = (total_capital / INITIAL_CAPITAL - 1) * 100

    print(f"\n  PERFORMANCE:")
    print(f"    Portfolio : {port_return:+.2f}%")
    print(f"    SPY B&H   : {spy_return:+.2f}%")
    print(f"    Alpha     : {port_return - spy_return:+.2f}%")

    # 6. Sauvegarder
    state["history"].append({
        "date": today,
        "signals": {sid: s.get("action") for sid, s in signals.items()},
        "orders": len(orders),
        "capital": total_capital,
    })
    save_state(state)

    print(f"\n{'='*70}\n")


def show_status():
    state = load_state()

    print(f"\n{'='*70}")
    print(f"  PAPER PORTFOLIO — DASHBOARD")
    print(f"{'='*70}")

    total_capital = state.get("capital", INITIAL_CAPITAL)
    port_return = (total_capital / INITIAL_CAPITAL - 1) * 100

    print(f"  Capital      : ${total_capital:,.2f}")
    print(f"  Return       : {port_return:+.2f}%")

    # Benchmark
    try:
        spy_now = get_benchmark_price()
        spy_start = state.get("benchmark_start_price", spy_now)
        spy_return = (spy_now / spy_start - 1) * 100 if spy_start > 0 else 0
        print(f"  SPY B&H      : {spy_return:+.2f}%")
        print(f"  Alpha        : {port_return - spy_return:+.2f}%")
    except Exception:
        pass

    # Allocations
    allocs = state.get("allocations", {})
    if allocs:
        print(f"\n  Allocations:")
        for sid, a in allocs.items():
            name = STRATEGIES.get(sid, {}).get("name", sid)
            print(f"    {name:<25} {a['pct']*100:>5.1f}%  ${a['capital']:>10,.2f}")

    # Positions
    positions = state.get("positions", {})
    if positions:
        print(f"\n  Positions actives:")
        for sid, pos in positions.items():
            name = STRATEGIES.get(sid, {}).get("name", sid)
            syms = pos.get("symbols", [])
            direction = pos.get("direction", "")
            print(f"    {name:<25} {', '.join(syms)} {direction}")

    # Alpaca
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        account = client.authenticate()
        positions_alpaca = client.get_positions()

        print(f"\n  Compte Alpaca ({'PAPER' if account.get('paper') else 'LIVE'}):")
        print(f"    Equity: ${account['equity']:>12,.2f}")
        print(f"    Cash:   ${account['cash']:>12,.2f}")

        if positions_alpaca:
            total_pnl = 0
            for p in positions_alpaca:
                pnl = p["unrealized_pl"]
                total_pnl += pnl
                print(f"    {p['symbol']:<6} {p['qty']:>8} shares  "
                      f"val=${p['market_val']:>10,.2f}  P&L=${pnl:>+8.2f}")
            print(f"    {'TOTAL':>36} P&L=${total_pnl:>+8.2f}")
    except Exception as e:
        print(f"\n  Alpaca: {e}")

    # Historique
    history = state.get("history", [])
    if history:
        print(f"\n  Derniers runs ({len(history)}):")
        for h in history[-5:]:
            sigs = h.get("signals", {})
            sig_str = " | ".join(f"{k.split('_')[0]}={v}" for k, v in sigs.items())
            print(f"    {h['date']}: {sig_str} ({h.get('orders', 0)} ordres)")

    print(f"{'='*70}\n")


def run_intraday(dry_run: bool = False):
    """Execute les strategies intraday pendant les heures de marche."""
    now = datetime.now(timezone.utc)
    state = load_state()

    # Utiliser l'equity ACTUELLE Alpaca
    try:
        from core.alpaca_client.client import AlpacaClient
        _client = AlpacaClient.from_env()
        _account = _client.authenticate()
        total_capital = _account["equity"]
        state["capital"] = total_capital
    except Exception:
        total_capital = state.get("capital", INITIAL_CAPITAL)

    print(f"\n{'='*70}")
    print(f"  PAPER PORTFOLIO — INTRADAY EXECUTION")
    print(f"{'='*70}")
    print(f"  Date     : {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Capital  : ${total_capital:,.2f}")
    print(f"  Mode     : {'DRY-RUN' if dry_run else 'PAPER TRADING'}")

    # ── Check fermeture forcee (5 min avant fermeture marche) ──
    import zoneinfo
    et = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    today_str = now_et.strftime("%Y-%m-%d")
    close_h, close_m = get_market_close_hour(today_str)
    # Fermeture forcee 5 min avant la cloture
    close_time = now_et.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    from datetime import timedelta
    force_close_time = close_time - timedelta(minutes=5)
    is_close_time = now_et >= force_close_time and now_et < close_time
    is_after_close = now_et >= close_time

    if (is_close_time or is_after_close) and not dry_run:
        print(f"\n  FERMETURE FORCEE ({now_et.strftime('%H:%M')} ET)")
        _close_all_intraday_positions(state, dry_run=False)
        save_state(state)
        print(f"\n{'='*70}\n")
        return

    # Filtrer seulement les strategies intraday
    intraday_strats = {k: v for k, v in STRATEGIES.items() if v["frequency"] == "intraday"}

    # Allocations sur l'ensemble du portefeuille (daily + intraday)
    allocations = compute_allocations(STRATEGIES, total_capital)

    print(f"\n  ALLOCATIONS INTRADAY:")
    for sid in intraday_strats:
        alloc = allocations.get(sid, {})
        name = STRATEGIES[sid]["name"]
        print(f"    {name:<25} {alloc.get('pct', 0)*100:>5.1f}%  ${alloc.get('capital', 0):>10,.2f}")

    # Generer les signaux intraday (avec kill switch check)
    print(f"\n  SIGNAUX INTRADAY:")
    signals = {}
    for sid in intraday_strats:
        alloc_capital = allocations.get(sid, {}).get("capital", 0)
        name = STRATEGIES[sid]["name"]

        # Kill switch : verifier si la strategie est desactivee
        if check_strategy_kill_switch(state, sid, alloc_capital):
            print(f"    {name:<25} !! KILL SWITCH (-2% sur 5j)")
            signals[sid] = {"action": "hold", "reason": "kill switch"}
            continue

        sig = signal_intraday(sid, alloc_capital, state)
        signals[sid] = sig
        if sig["action"] == "intraday_trade":
            print(f"    {name:<25} >> {sig['direction']} {sig['ticker']} "
                  f"@ ${sig.get('entry_price', 0):.2f}")
        else:
            print(f"    {name:<25} -- {sig.get('reason', 'hold')}")

    # Executer
    print(f"\n  EXECUTION:")
    orders = execute_orders(signals, allocations, state, dry_run, total_capital)

    if not orders:
        print(f"    Aucun ordre intraday")

    # Sauvegarder
    save_state(state)
    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Paper Portfolio Runner unifie")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Forcer le rebalancement mensuel")
    parser.add_argument("--intraday", action="store_true",
                        help="Executer les strategies intraday")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.intraday:
        run_intraday(dry_run=args.dry_run)
    else:
        run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
