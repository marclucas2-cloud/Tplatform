#!/usr/bin/env python3
"""
Paper Portfolio Runner — orchestration unifiee des 3 strategies sur Alpaca.

Respecte le pipeline multi-agents :
  1. PortfolioManager : allocation risk-parity (Sharpe-weighted, cap 40%)
  2. ExecutionManager : circuit-breaker drawdown 5%, sizing par allocated_capital
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
MAX_ALLOCATION_PER_STRATEGY = 0.40  # 40% max par strategie
BENCHMARK = "SPY"

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
    "orb_5min": {
        "name": "ORB 5-Min Breakout",
        "sharpe": 3.47,            # backtest 186 tickers, 121j
        "frequency": "intraday",
        "multi_asset": True,
    },
    "opex_gamma": {
        "name": "OpEx Gamma Pin",
        "sharpe": 7.08,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "earnings_drift": {
        "name": "Earnings Drift",
        "sharpe": 13.50,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "dow_seasonal": {
        "name": "Day-of-Week Seasonal",
        "sharpe": 1.85,
        "frequency": "intraday",
        "multi_asset": True,
    },
    "ml_cluster": {
        "name": "ML Volume Cluster",
        "sharpe": 1.13,
        "frequency": "intraday",
        "multi_asset": True,
    },
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
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "positions": {},           # strategy_id -> {symbols, direction, entry_prices...}
        "allocations": {},         # strategy_id -> {pct, capital}
        "last_monthly": None,
        "daily_capital_start": INITIAL_CAPITAL,
        "daily_pnl": 0.0,
        "benchmark_start_price": None,
        "benchmark_start_date": None,
        "history": [],
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# =============================================================================
# PORTFOLIO ALLOCATION (replica de PortfolioManagerAgent._compute_allocations)
# =============================================================================

def compute_allocations(strategies: dict, total_capital: float) -> dict[str, dict]:
    """
    Allocation risk-parity basee sur le Sharpe ratio.
    Cap 40% par strategie. Renormalise apres plafonnement.
    """
    sharpes = {sid: max(s["sharpe"], 0.1) for sid, s in strategies.items()}
    total_sharpe = sum(sharpes.values())

    allocations = {}
    for sid, sharpe in sharpes.items():
        raw = sharpe / total_sharpe
        capped = min(raw, MAX_ALLOCATION_PER_STRATEGY)
        allocations[sid] = capped

    # Renormaliser
    total_alloc = sum(allocations.values())
    if total_alloc > 0:
        allocations = {sid: v / total_alloc for sid, v in allocations.items()}

    result = {}
    for sid, pct in allocations.items():
        allocated = total_capital * pct
        result[sid] = {
            "pct": round(pct, 4),
            "capital": round(allocated, 2),
            "max_position": round(allocated, 2),  # 100% de l'allocation par strategie
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
        ORB5MinStrategy,
        OpExGammaPinStrategy,
        EarningsDriftStrategy,
        DayOfWeekSeasonalStrategy,
        VolumeProfileClusterStrategy,
    )

    STRAT_MAP = {
        "orb_5min": ORB5MinStrategy,
        "opex_gamma": OpExGammaPinStrategy,
        "earnings_drift": EarningsDriftStrategy,
        "dow_seasonal": DayOfWeekSeasonalStrategy,
        "ml_cluster": VolumeProfileClusterStrategy,
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

        request = StockBarsRequest(
            symbol_or_symbols=required_tickers[:50],  # Alpaca max 50/batch
            timeframe=TimeFrame(5, TimeFrame.Minute.unit),
            start=start_dt,
            end=end_dt,
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
        logger.error(f"Erreur fetch intraday: {e}")
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
# EXECUTION (avec circuit-breaker)
# =============================================================================

def is_us_market_open() -> bool:
    """Verifie si le marche US est ouvert (9:30-16:00 ET, lun-ven)."""
    import zoneinfo
    et = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    # Weekend
    if now_et.weekday() >= 5:
        return False
    # Horaires reguliers
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def execute_orders(signals: dict, allocations: dict, state: dict,
                   dry_run: bool) -> list[dict]:
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
            logger.warning(
                f"CIRCUIT-BREAKER: DD journalier {daily_dd*100:.1f}% > {MAX_DAILY_DRAWDOWN*100}%"
                f" — AUCUN ordre execute")
            return []

    orders = []
    current_positions = {p["symbol"]: p for p in client.get_positions()}

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

            # Acheter les nouveaux targets
            notional_each = max_capital / len(targets) * 0.95 if targets else 0
            for sym in target_set - current_syms:
                if notional_each > 10:
                    try:
                        result = client.create_position(sym, "BUY", notional=round(notional_each, 2), _authorized_by="paper_portfolio")
                        orders.append({"sid": sid, "action": "buy", "symbol": sym,
                                      "notional": notional_each})
                        logger.info(f"  [{sid}] ACHETE {sym} ${notional_each:,.0f}")
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
                client.create_position(sym_a, dir_a, notional=round(notional, 2), _authorized_by="paper_portfolio")
                client.create_position(sym_b, dir_b, notional=round(notional, 2), _authorized_by="paper_portfolio")
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

            # Ne pas ouvrir si on a deja une position intraday sur ce ticker
            intraday_pos = state.get("intraday_positions", {})
            if ticker in intraday_pos:
                continue

            # Position sizing : max 5% du capital alloue
            entry_price = signal.get("entry_price", 0)
            if entry_price <= 0:
                continue
            notional = min(capital * 0.05, capital)

            side = "BUY" if direction == "LONG" else "SELL"
            try:
                result = client.create_position(
                    ticker, side, notional=round(notional, 2),
                    _authorized_by="paper_portfolio_intraday"
                )
                orders.append({
                    "sid": sid, "action": "intraday_open", "symbol": ticker,
                    "direction": direction, "notional": notional,
                    "stop_loss": signal.get("stop_loss"),
                    "take_profit": signal.get("take_profit"),
                })
                logger.info(f"  [{sid}] INTRADAY {direction} {ticker} ${notional:,.0f}")

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

    total_capital = state.get("capital", INITIAL_CAPITAL)

    print(f"\n{'='*70}")
    print(f"  PAPER PORTFOLIO — EXECUTION UNIFIEE")
    print(f"{'='*70}")
    print(f"  Date     : {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Capital  : ${total_capital:,.2f}")
    print(f"  Mode     : {'DRY-RUN' if dry_run else 'PAPER TRADING'}")

    # 1. Calculer les allocations
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
    orders = execute_orders(signals, allocations, state, dry_run)

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
    total_capital = state.get("capital", INITIAL_CAPITAL)

    print(f"\n{'='*70}")
    print(f"  PAPER PORTFOLIO — INTRADAY EXECUTION")
    print(f"{'='*70}")
    print(f"  Date     : {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Capital  : ${total_capital:,.2f}")
    print(f"  Mode     : {'DRY-RUN' if dry_run else 'PAPER TRADING'}")

    # Filtrer seulement les strategies intraday
    intraday_strats = {k: v for k, v in STRATEGIES.items() if v["frequency"] == "intraday"}

    # Allocations sur l'ensemble du portefeuille (daily + intraday)
    allocations = compute_allocations(STRATEGIES, total_capital)

    print(f"\n  ALLOCATIONS INTRADAY:")
    for sid in intraday_strats:
        alloc = allocations.get(sid, {})
        name = STRATEGIES[sid]["name"]
        print(f"    {name:<25} {alloc.get('pct', 0)*100:>5.1f}%  ${alloc.get('capital', 0):>10,.2f}")

    # Generer les signaux intraday
    print(f"\n  SIGNAUX INTRADAY:")
    signals = {}
    for sid in intraday_strats:
        alloc_capital = allocations.get(sid, {}).get("capital", 0)
        sig = signal_intraday(sid, alloc_capital, state)
        signals[sid] = sig
        name = STRATEGIES[sid]["name"]
        if sig["action"] == "intraday_trade":
            print(f"    {name:<25} >> {sig['direction']} {sig['ticker']} "
                  f"@ ${sig.get('entry_price', 0):.2f}")
        else:
            print(f"    {name:<25} -- {sig.get('reason', 'hold')}")

    # Fermer les positions intraday qui ont atteint leur stop/target
    # (simplifie : on ferme toutes les positions intraday de > 4h)
    intraday_pos = state.get("intraday_positions", {})
    stale_positions = []
    for ticker, pos in intraday_pos.items():
        opened_at = datetime.fromisoformat(pos["opened_at"])
        age_hours = (now - opened_at).total_seconds() / 3600
        if age_hours > 4:
            stale_positions.append(ticker)

    if stale_positions and not dry_run and is_us_market_open():
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        for ticker in stale_positions:
            try:
                client.close_position(ticker, _authorized_by="paper_portfolio_intraday")
                logger.info(f"  [INTRADAY] Ferme {ticker} (> 4h)")
                intraday_pos.pop(ticker, None)
            except Exception as e:
                logger.warning(f"  [INTRADAY] Erreur fermeture {ticker}: {e}")

    # Executer
    print(f"\n  EXECUTION:")
    orders = execute_orders(signals, allocations, state, dry_run)

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
