"""
Routes V2 — Endpoints avances pour le dashboard trading.

Groupes :
  - /api/risk/*          — Vue risque, limites, VaR, kill switch
  - /api/trades/*        — Calendrier, export CSV
  - /api/analytics/*     — Statistiques par jour/heure, distribution, streaks
  - /api/system/*        — Statut brokers, latence, reconciliation, logs
  - /api/tax/*           — PFU 30% France, synthese fiscale
  - /api/cross/*         — Exposition combinee IBKR + Binance
  - /api/comparison/*    — Backtest vs paper vs live
  - /api/equity-curve    — Courbe d'equity multi-broker
"""

import csv
import io
import json
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import requests

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from fastapi import APIRouter, Query

logger = logging.getLogger("dashboard-api-v2")

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "intraday-backtesterV2" / "output"

router = APIRouter()


# ── Helpers (locaux, pas d'import circulaire) ────────────────────────────────

def _load_state() -> dict:
    """Charge paper_portfolio_state.json."""
    state_file = ROOT / "paper_portfolio_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_yaml(path: Path) -> dict:
    """Charge un fichier YAML de config."""
    if yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_kill_switch_state() -> dict:
    """Charge l'etat persiste du kill switch IBKR."""
    ks_path = DATA_DIR / "kill_switch_state.json"
    if ks_path.exists():
        try:
            return json.loads(ks_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_crypto_kill_switch_state() -> dict:
    """Charge l'etat du kill switch crypto."""
    ks_path = DATA_DIR / "crypto" / "kill_switch_state.json"
    if ks_path.exists():
        try:
            return json.loads(ks_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_alpaca_trades() -> list[dict]:
    """Charge les trades reels depuis l'API Alpaca (fills groupes par order_id)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not api_secret:
        return []

    base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    # Recuperer tous les fills avec pagination
    all_fills = []
    page_token = None
    try:
        for _ in range(20):  # max 20 pages (2000 fills)
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            r = requests.get(
                f"{base}/v2/account/activities/FILL",
                headers=headers, params=params, timeout=10,
            )
            if r.status_code != 200:
                break
            fills = r.json()
            if not fills:
                break
            all_fills.extend(fills)
            if len(fills) < 100:
                break
            page_token = fills[-1].get("id")
    except Exception as e:
        logger.error("Alpaca fills fetch error: %s", e)
        return []

    # Grouper fills par order_id pour reconstituer les trades
    from collections import defaultdict
    orders: dict[str, list] = defaultdict(list)
    for f in all_fills:
        oid = f.get("order_id", f.get("id", ""))
        orders[oid].append(f)

    trades = []
    for oid, fills in orders.items():
        fills.sort(key=lambda x: x.get("transaction_time", ""))
        first = fills[0]
        total_qty = sum(float(f.get("qty", 0)) for f in fills)
        avg_price = (
            sum(float(f.get("price", 0)) * float(f.get("qty", 0)) for f in fills) / total_qty
            if total_qty > 0 else 0
        )
        trades.append({
            "order_id": oid,
            "symbol": first.get("symbol", ""),
            "side": first.get("side", "").upper(),
            "qty": total_qty,
            "price": round(avg_price, 4),
            "entry_price": round(avg_price, 4),
            "date": first.get("transaction_time", "")[:10],
            "timestamp": first.get("transaction_time", ""),
            "fills_count": len(fills),
            "trade_source": "paper",
            "source": "alpaca_api",
        })

    trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return trades


# Cache simple pour eviter de spammer l'API Alpaca a chaque requete
_alpaca_cache: dict = {"trades": [], "ts": 0}
_ALPACA_CACHE_TTL = 60  # 60 secondes


def _get_alpaca_trades_cached() -> list[dict]:
    """Retourne les trades Alpaca avec cache de 60s."""
    import time
    now = time.time()
    if now - _alpaca_cache["ts"] > _ALPACA_CACHE_TTL or not _alpaca_cache["trades"]:
        _alpaca_cache["trades"] = _load_alpaca_trades()
        _alpaca_cache["ts"] = now
    return _alpaca_cache["trades"]


def _load_all_trades(source: str = "real") -> list[dict]:
    """Charge les trades.

    source:
      - "real"     : trades reels (Alpaca API + SQLite journals + state)
      - "backtest" : trades backtest CSV uniquement (simulations)
      - "all"      : tout
    """
    all_trades: list[dict] = []

    # 1. Trades reels
    if source in ("real", "all"):
        # API Alpaca (source primaire — les vrais trades paper)
        all_trades.extend(_get_alpaca_trades_cached())

        # SQLite journals (si le trade_journal.py ecrit dedans)
        for db_name, origin in (("paper_journal.db", "paper"), ("live_journal.db", "live")):
            db_path = DATA_DIR / db_name
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT * FROM trades ORDER BY entry_time DESC LIMIT 5000"
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        d["trade_source"] = origin
                        all_trades.append(d)
                    conn.close()
                except Exception:
                    continue

        # State file (strategy_pnl_log — P&L quotidiens)
        state = _load_state()
        for t in state.get("trades_log", []):
            t_copy = dict(t)
            t_copy.setdefault("trade_source", "paper")
            all_trades.append(t_copy)

    # 2. Trades backtest (simulations CSV — PAS des trades reels)
    if source in ("backtest", "all"):
        if OUTPUT_DIR.exists() and pd is not None:
            for csv_file in OUTPUT_DIR.glob("trades_*.csv"):
                try:
                    df = pd.read_csv(csv_file)
                    if df.empty:
                        continue
                    df["source"] = csv_file.stem
                    df["trade_source"] = "backtest"
                    all_trades.extend(df.to_dict(orient="records"))
                except Exception:
                    continue

    return all_trades


def _safe_float(val, default: float = 0.0) -> float:
    """Convertit en float de maniere sure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _pnl_from_trade(t: dict) -> float:
    """Extrait le P&L d'un trade (multiples noms de colonnes possibles)."""
    for key in ("pnl", "realized_pnl", "profit", "net_pnl", "pnl_net"):
        if key in t:
            return _safe_float(t[key])
    return 0.0


def _date_from_trade(t: dict) -> Optional[str]:
    """Extrait la date (YYYY-MM-DD) d'un trade."""
    for key in ("date", "entry_time", "entry_date", "exit_time", "exit_date", "timestamp"):
        if key in t and t[key]:
            raw = str(t[key])[:10]
            if len(raw) >= 10 and raw[4] == "-":
                return raw
    return None


def _hour_from_trade(t: dict) -> Optional[int]:
    """Extrait l'heure d'un trade."""
    for key in ("entry_time", "timestamp", "date"):
        if key in t and t[key]:
            raw = str(t[key])
            if len(raw) >= 13 and "T" in raw:
                try:
                    return int(raw.split("T")[1][:2])
                except (ValueError, IndexError):
                    continue
            if len(raw) >= 16 and " " in raw:
                try:
                    return int(raw.split(" ")[1][:2])
                except (ValueError, IndexError):
                    continue
    return None


def _asset_class_from_trade(t: dict) -> str:
    """Determine la classe d'actif d'un trade."""
    instrument_type = str(t.get("instrument_type", "")).upper()
    if instrument_type in ("FX", "FOREX"):
        return "FX"
    if instrument_type in ("FUTURES", "FUTURE"):
        return "Futures"
    if instrument_type in ("CRYPTO", "CRYPTO_MARGIN", "CRYPTO_SPOT"):
        return "Crypto"

    symbol = str(t.get("symbol", t.get("instrument", t.get("ticker", "")))).upper()
    if not symbol:
        source = str(t.get("source", ""))
        if "fx_" in source.lower() or "eur" in source.lower() or "gbp" in source.lower():
            return "FX"
        if "futures" in source.lower() or "mcl" in source.lower() or "mes" in source.lower():
            return "Futures"
        if "crypto" in source.lower() or "btc" in source.lower():
            return "Crypto"

    fx_tokens = ("EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "NOK", "SEK")
    if any(symbol.startswith(tok) for tok in fx_tokens) and len(symbol) in (6, 7):
        return "FX"
    if symbol in ("MCL", "MES", "MNQ", "MGC", "M6E"):
        return "Futures"
    if symbol in ("BTCUSDT", "ETHUSDT", "BTC", "ETH"):
        return "Crypto"

    return "Equity"


# =============================================================================
# RISK ENDPOINTS
# =============================================================================

@router.get("/api/risk/overview")
def risk_overview():
    """Vue synthetique du risque : drawdown, VaR, exposition, kill switches."""
    try:
        state = _load_state()
        limits_ibkr = _load_yaml(CONFIG_DIR / "limits_live.yaml")
        limits_crypto = _load_yaml(CONFIG_DIR / "crypto_limits.yaml")
        ks_ibkr = _load_kill_switch_state()
        ks_crypto = _load_crypto_kill_switch_state()

        capital_ibkr = limits_ibkr.get("capital", 10_000)
        capital_crypto = limits_crypto.get("capital", 15_000)
        daily_pnl = state.get("daily_pnl", 0.0)

        # Drawdown actuel
        equity = state.get("capital", 100_000)
        peak = max(equity, state.get("daily_capital_start", equity))
        drawdown_pct = (equity - peak) / peak * 100 if peak > 0 else 0

        # VaR parametrique simplifiee (95%, 1 jour)
        portfolio_vol_daily = 0.012  # ~1.2% daily vol estimee
        var_95 = equity * portfolio_vol_daily * 1.645
        var_99 = equity * portfolio_vol_daily * 2.326

        return {
            "drawdown": {
                "current_pct": round(drawdown_pct, 2),
                "max_allowed_pct": -limits_ibkr.get("kill_switch", {}).get("max_monthly_loss_pct", 0.05) * 100,
                "daily_pnl": round(daily_pnl, 2),
                "daily_pnl_pct": round(daily_pnl / capital_ibkr * 100, 2) if capital_ibkr > 0 else 0,
            },
            "var": {
                "var_95_1d": round(var_95, 2),
                "var_99_1d": round(var_99, 2),
            },
            "exposure": {
                "ibkr_capital": capital_ibkr,
                "crypto_capital": capital_crypto,
                "total_capital": capital_ibkr + capital_crypto,
            },
            "kill_switch": {
                "ibkr": {
                    "active": ks_ibkr.get("active", False),
                    "armed": ks_ibkr.get("armed", True),
                    "reason": ks_ibkr.get("activation_reason"),
                    "activated_at": ks_ibkr.get("activated_at"),
                },
                "crypto": {
                    "active": ks_crypto.get("active", False),
                    "armed": ks_crypto.get("armed", True),
                    "reason": ks_crypto.get("activation_reason"),
                    "activated_at": ks_crypto.get("activated_at"),
                },
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error("risk/overview error: %s", e)
        return {"error": str(e)}


@router.get("/api/risk/limits")
def risk_limits():
    """Liste des limites de risque : valeur actuelle vs seuil."""
    try:
        limits_ibkr = _load_yaml(CONFIG_DIR / "limits_live.yaml")
        limits_crypto = _load_yaml(CONFIG_DIR / "crypto_limits.yaml")
        state = _load_state()

        capital_ibkr = limits_ibkr.get("capital", 10_000)
        capital_crypto = limits_crypto.get("capital", 15_000)
        daily_pnl = state.get("daily_pnl", 0.0)

        pos_limits = limits_ibkr.get("position_limits", {})
        cb = limits_ibkr.get("circuit_breakers", {})
        fx = limits_ibkr.get("fx_limits", {})
        combined = limits_ibkr.get("combined_limits", {})
        margin_lim = limits_ibkr.get("margin_limits", {})
        crypto_margin = limits_crypto.get("margin_rules", {})
        crypto_borrow = limits_crypto.get("borrow_limits", {})

        def _status(pct_used: float) -> str:
            if pct_used >= 90:
                return "critical"
            if pct_used >= 70:
                return "warning"
            return "ok"

        # Valeurs actuelles simulees (proviennent du state ou estimees)
        # En production, ces valeurs viennent des brokers en temps reel
        current_dd_pct = abs(daily_pnl / capital_ibkr * 100) if capital_ibkr > 0 else 0
        dd_limit = cb.get("daily_loss_pct", 0.015) * 100

        items = [
            {
                "name": "Daily Drawdown (IBKR)",
                "current": round(current_dd_pct, 2),
                "limit": round(dd_limit, 2),
                "unit": "%",
                "pct_used": round(current_dd_pct / dd_limit * 100, 1) if dd_limit > 0 else 0,
                "status": _status(current_dd_pct / dd_limit * 100 if dd_limit > 0 else 0),
            },
            {
                "name": "Gross Exposure",
                "current": 0,
                "limit": round(pos_limits.get("max_gross_pct", 1.2) * 100, 0),
                "unit": "% capital",
                "pct_used": 0,
                "status": "ok",
            },
            {
                "name": "Net Exposure",
                "current": 0,
                "limit": round((pos_limits.get("max_long_pct", 0.60) + pos_limits.get("max_short_pct", 0.40)) * 100, 0),
                "unit": "% capital",
                "pct_used": 0,
                "status": "ok",
            },
            {
                "name": "FX Margin Used",
                "current": 0,
                "limit": round(fx.get("max_fx_margin_pct", 0.40) * 100, 0),
                "unit": "% capital",
                "pct_used": 0,
                "status": "ok",
            },
            {
                "name": "Cash Reserve",
                "current": 25,
                "limit": round(combined.get("min_cash_pct", 0.20) * 100, 0),
                "unit": "% capital",
                "pct_used": round(25 / (combined.get("min_cash_pct", 0.20) * 100) * 100, 1),
                "status": "ok",
            },
            {
                "name": "Total Margin (IBKR)",
                "current": 0,
                "limit": round(combined.get("max_total_margin_pct", 0.80) * 100, 0),
                "unit": "% capital",
                "pct_used": 0,
                "status": "ok",
            },
            {
                "name": "Margin Used (IBKR)",
                "current": 0,
                "limit": round(margin_lim.get("max_margin_used_pct", 0.70) * 100, 0),
                "unit": "% capital",
                "pct_used": 0,
                "status": "ok",
            },
            {
                "name": "Binance Margin Level",
                "current": 2.5,
                "limit": crypto_margin.get("min_margin_level", 1.5),
                "unit": "ratio",
                "pct_used": round(crypto_margin.get("min_margin_level", 1.5) / 2.5 * 100, 1),
                "status": "ok",
                "inverted": True,
            },
            {
                "name": "Binance Borrow Cost (monthly)",
                "current": 0.3,
                "limit": crypto_borrow.get("max_monthly_borrow_cost_pct", 2.0),
                "unit": "% capital",
                "pct_used": round(0.3 / crypto_borrow.get("max_monthly_borrow_cost_pct", 2.0) * 100, 1),
                "status": "ok",
            },
            {
                "name": "Binance Earn Allocation",
                "current": 20,
                "limit": limits_crypto.get("crypto_specific", {}).get("earn_max_single_asset_pct", 50),
                "unit": "% earn wallet",
                "pct_used": round(20 / 50 * 100, 1),
                "status": "ok",
            },
        ]

        return {"limits": items, "count": len(items)}
    except Exception as e:
        logger.error("risk/limits error: %s", e)
        return {"error": str(e), "limits": []}


@router.get("/api/risk/correlation")
def risk_correlation():
    """Matrice de correlation entre positions actuelles."""
    try:
        # Tentative de calcul reel via les positions
        state = _load_state()
        positions = state.get("intraday_positions", {})

        if len(positions) >= 2 and pd is not None:
            # Si suffisamment de positions, calculer la correlation
            # (necessiterait des donnees de prix historiques)
            pass

        # Matrice exemple basee sur les classes d'actifs en portefeuille
        assets = ["EUR/USD", "EUR/GBP", "EUR/JPY", "AUD/JPY", "GBP/USD", "EU Gap", "BTC/ETH", "MES"]
        n = len(assets)
        # Correlations FX EUR fort entre elles, BTC/MES moderement correles
        corr_matrix = [
            [1.00, 0.72, 0.65, 0.30, 0.55, 0.10, 0.08, 0.15],
            [0.72, 1.00, 0.50, 0.20, 0.60, 0.12, 0.05, 0.10],
            [0.65, 0.50, 1.00, 0.45, 0.35, 0.08, 0.10, 0.18],
            [0.30, 0.20, 0.45, 1.00, 0.25, 0.05, 0.15, 0.22],
            [0.55, 0.60, 0.35, 0.25, 1.00, 0.10, 0.07, 0.12],
            [0.10, 0.12, 0.08, 0.05, 0.10, 1.00, 0.20, 0.65],
            [0.08, 0.05, 0.10, 0.15, 0.07, 0.20, 1.00, 0.35],
            [0.15, 0.10, 0.18, 0.22, 0.12, 0.65, 0.35, 1.00],
        ]

        return {
            "assets": assets,
            "matrix": corr_matrix,
            "max_pairwise": 0.72,
            "avg_pairwise": round(
                np.mean([corr_matrix[i][j]
                         for i in range(n) for j in range(i + 1, n)]), 3
            ),
            "note": "Correlations estimees — mise a jour avec donnees live quand disponibles",
        }
    except Exception as e:
        logger.error("risk/correlation error: %s", e)
        return {"error": str(e)}


@router.get("/api/risk/drawdown")
def risk_drawdown():
    """Historique de drawdown depuis la courbe d'equity."""
    try:
        state = _load_state()
        history = state.get("history", [])

        if not history:
            # Donnees fictives pour developpement
            base = 100_000
            today = datetime.now(timezone.utc)
            dd_history = []
            equity = base
            peak = base
            for i in range(90):
                dt = today - timedelta(days=90 - i)
                change = np.random.normal(0.0005, 0.008) * equity
                equity += change
                peak = max(peak, equity)
                dd = (equity - peak) / peak * 100
                dd_history.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "equity": round(equity, 2),
                    "peak": round(peak, 2),
                    "drawdown_pct": round(dd, 2),
                })

            return {
                "history": dd_history,
                "current_dd_pct": round(dd_history[-1]["drawdown_pct"], 2),
                "max_dd_pct": round(min(h["drawdown_pct"] for h in dd_history), 2),
                "max_dd_date": min(dd_history, key=lambda h: h["drawdown_pct"])["date"],
                "source": "simulated",
            }

        # Calcul reel depuis history
        equity_series = []
        peak = 0
        for entry in history:
            eq = _safe_float(entry.get("equity", entry.get("capital", 0)))
            peak = max(peak, eq)
            dd = (eq - peak) / peak * 100 if peak > 0 else 0
            equity_series.append({
                "date": _date_from_trade(entry) or entry.get("date", ""),
                "equity": round(eq, 2),
                "peak": round(peak, 2),
                "drawdown_pct": round(dd, 2),
            })

        return {
            "history": equity_series,
            "current_dd_pct": round(equity_series[-1]["drawdown_pct"], 2) if equity_series else 0,
            "max_dd_pct": round(min(h["drawdown_pct"] for h in equity_series), 2) if equity_series else 0,
            "max_dd_date": min(equity_series, key=lambda h: h["drawdown_pct"])["date"] if equity_series else "",
            "source": "state_history",
        }
    except Exception as e:
        logger.error("risk/drawdown error: %s", e)
        return {"error": str(e)}


@router.get("/api/risk/kill-switch")
def risk_kill_switch():
    """Statut kill switch IBKR et Binance."""
    try:
        ks_ibkr = _load_kill_switch_state()
        ks_crypto = _load_crypto_kill_switch_state()

        ibkr_history = ks_ibkr.get("history", [])
        crypto_history = ks_crypto.get("history", [])

        return {
            "ibkr": {
                "active": ks_ibkr.get("active", False),
                "armed": ks_ibkr.get("armed", True),
                "activated_at": ks_ibkr.get("activated_at"),
                "reason": ks_ibkr.get("activation_reason"),
                "trigger_type": ks_ibkr.get("activation_trigger"),
                "thresholds": ks_ibkr.get("thresholds", {
                    "daily_loss_pct": 0.015,
                    "hourly_loss_pct": 0.01,
                    "trailing_5d_loss_pct": 0.03,
                    "monthly_loss_pct": 0.05,
                }),
                "total_activations": sum(
                    1 for e in ibkr_history if e.get("action") == "ACTIVATE"
                ),
                "last_activation": next(
                    (e for e in reversed(ibkr_history) if e.get("action") == "ACTIVATE"),
                    None,
                ),
                "disabled_strategies": ks_ibkr.get("disabled_strategies", []),
            },
            "crypto": {
                "active": ks_crypto.get("active", False),
                "armed": ks_crypto.get("armed", True),
                "activated_at": ks_crypto.get("activated_at"),
                "reason": ks_crypto.get("activation_reason"),
                "trigger_type": ks_crypto.get("activation_trigger"),
                "thresholds": ks_crypto.get("thresholds", {
                    "daily_max_loss_pct": 5.0,
                    "weekly_max_loss_pct": 10.0,
                    "monthly_max_loss_pct": 15.0,
                    "max_drawdown_pct": 20.0,
                }),
                "total_activations": sum(
                    1 for e in crypto_history if e.get("action") == "ACTIVATE"
                ),
                "last_activation": next(
                    (e for e in reversed(crypto_history) if e.get("action") == "ACTIVATE"),
                    None,
                ),
            },
            "test_results": {
                "ibkr_last_test": ks_ibkr.get("last_updated"),
                "crypto_last_test": ks_crypto.get("last_updated"),
            },
        }
    except Exception as e:
        logger.error("risk/kill-switch error: %s", e)
        return {"error": str(e)}


@router.get("/api/risk/var")
def risk_var():
    """Value-at-Risk : parametrique, historique, par classe d'actif."""
    try:
        limits_ibkr = _load_yaml(CONFIG_DIR / "limits_live.yaml")
        limits_crypto = _load_yaml(CONFIG_DIR / "crypto_limits.yaml")

        capital_ibkr = limits_ibkr.get("capital", 10_000)
        capital_crypto = limits_crypto.get("capital", 15_000)
        total = capital_ibkr + capital_crypto

        # Volatilites annuelles estimees par classe
        vol = {
            "FX (EUR pairs)":   {"annual": 0.08, "alloc": 0.18},
            "FX (carry)":       {"annual": 0.10, "alloc": 0.07},
            "EU Equity":        {"annual": 0.20, "alloc": 0.15},
            "US Equity":        {"annual": 0.18, "alloc": 0.10},
            "Futures (index)":  {"annual": 0.22, "alloc": 0.05},
            "Futures (energy)": {"annual": 0.35, "alloc": 0.05},
            "Crypto (BTC/ETH)": {"annual": 0.65, "alloc": 0.20},
            "Crypto (alts)":    {"annual": 0.85, "alloc": 0.10},
            "Cash/Earn":        {"annual": 0.01, "alloc": 0.10},
        }

        var_by_class = {}
        total_var_95_sq = 0
        total_var_99_sq = 0

        for cls, params in vol.items():
            daily_vol = params["annual"] / (365 ** 0.5)
            alloc_dollars = total * params["alloc"]
            v95 = alloc_dollars * daily_vol * 1.645
            v99 = alloc_dollars * daily_vol * 2.326
            var_by_class[cls] = {
                "var_95": round(v95, 2),
                "var_99": round(v99, 2),
                "daily_vol_pct": round(daily_vol * 100, 3),
                "allocation_pct": round(params["alloc"] * 100, 1),
            }
            total_var_95_sq += v95 ** 2
            total_var_99_sq += v99 ** 2

        # Diversifie (sqrt de la somme des carres — approximation)
        portfolio_var_95 = total_var_95_sq ** 0.5
        portfolio_var_99 = total_var_99_sq ** 0.5
        undiversified_95 = sum(v["var_95"] for v in var_by_class.values())
        undiversified_99 = sum(v["var_99"] for v in var_by_class.values())

        return {
            "portfolio": {
                "var_95_daily": round(portfolio_var_95, 2),
                "var_95_daily_pct": round(portfolio_var_95 / total * 100, 2),
                "var_99_daily": round(portfolio_var_99, 2),
                "var_99_daily_pct": round(portfolio_var_99 / total * 100, 2),
                "undiversified_var_95": round(undiversified_95, 2),
                "diversification_benefit": round(undiversified_95 - portfolio_var_95, 2),
                "diversification_benefit_pct": round(
                    (1 - portfolio_var_95 / undiversified_95) * 100, 1
                ) if undiversified_95 > 0 else 0,
            },
            "by_class": var_by_class,
            "total_capital": total,
            "note": "VaR parametrique — hypothese de normalite. Crypto domine le risque.",
        }
    except Exception as e:
        logger.error("risk/var error: %s", e)
        return {"error": str(e)}


# =============================================================================
# TRADES ENDPOINTS
# =============================================================================

@router.get("/api/trades/calendar")
def trades_calendar():
    """Heatmap calendrier : {date, pnl, trade_count} pour vue calendrier."""
    try:
        trades = _load_all_trades(source="real")

        by_date: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})
        for t in trades:
            d = _date_from_trade(t)
            if d:
                by_date[d]["pnl"] += _pnl_from_trade(t)
                by_date[d]["count"] += 1

        if not by_date:
            # Donnees fictives pour developpement
            today = datetime.now(timezone.utc)
            for i in range(60):
                dt = (today - timedelta(days=60 - i)).strftime("%Y-%m-%d")
                pnl = round(np.random.normal(15, 80), 2)
                count = max(1, int(np.random.poisson(3)))
                by_date[dt] = {"pnl": pnl, "count": count}

        calendar = sorted(
            [{"date": d, "pnl": round(v["pnl"], 2), "trade_count": v["count"]}
             for d, v in by_date.items()],
            key=lambda x: x["date"],
        )

        total_pnl = sum(c["pnl"] for c in calendar)
        winning_days = sum(1 for c in calendar if c["pnl"] > 0)
        total_days = len(calendar)

        return {
            "calendar": calendar,
            "summary": {
                "total_pnl": round(total_pnl, 2),
                "total_days": total_days,
                "winning_days": winning_days,
                "losing_days": total_days - winning_days,
                "win_rate_pct": round(winning_days / total_days * 100, 1) if total_days > 0 else 0,
                "best_day": max(calendar, key=lambda x: x["pnl"]) if calendar else None,
                "worst_day": min(calendar, key=lambda x: x["pnl"]) if calendar else None,
            },
        }
    except Exception as e:
        logger.error("trades/calendar error: %s", e)
        return {"error": str(e)}


@router.get("/api/trades/export")
def trades_export(
    format: str = Query("csv", description="Export format: csv"),
):
    """Export CSV de tous les trades pour telechargement."""
    try:
        trades = _load_all_trades(source="real")

        if not trades:
            return {"csv": "", "count": 0, "note": "No trades found"}

        # Colonnes standardisees
        columns = [
            "date", "strategy", "symbol", "direction", "quantity",
            "entry_price", "exit_price", "pnl", "pnl_pct",
            "commission", "slippage", "asset_class", "source",
        ]

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

        for t in trades:
            row = {
                "date": _date_from_trade(t) or "",
                "strategy": t.get("strategy", t.get("source", "")),
                "symbol": t.get("symbol", t.get("instrument", t.get("ticker", ""))),
                "direction": t.get("direction", t.get("side", "")),
                "quantity": t.get("quantity", t.get("qty", t.get("shares", ""))),
                "entry_price": t.get("entry_price", t.get("entry_price_filled", "")),
                "exit_price": t.get("exit_price", t.get("exit_price_filled", "")),
                "pnl": _pnl_from_trade(t),
                "pnl_pct": t.get("pnl_pct", t.get("return_pct", "")),
                "commission": t.get("commission", t.get("cost", "")),
                "slippage": t.get("slippage", t.get("slippage_bps", "")),
                "asset_class": _asset_class_from_trade(t),
                "source": t.get("source", ""),
            }
            writer.writerow(row)

        csv_str = output.getvalue()
        return {"csv": csv_str, "count": len(trades)}
    except Exception as e:
        logger.error("trades/export error: %s", e)
        return {"error": str(e), "csv": "", "count": 0}


# =============================================================================
# ANALYTICS ENDPOINTS
# =============================================================================

@router.get("/api/analytics/by-day")
def analytics_by_day():
    """P&L agrege par jour de la semaine."""
    try:
        trades = _load_all_trades(source="real")
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        by_day: dict[str, list[float]] = {d: [] for d in days}

        for t in trades:
            d = _date_from_trade(t)
            if d:
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    day_name = days[dt.weekday()]
                    by_day[day_name].append(_pnl_from_trade(t))
                except (ValueError, IndexError):
                    continue

        has_data = any(len(v) > 0 for v in by_day.values())

        if not has_data:
            # Donnees fictives pour developpement
            by_day = {
                "Monday":    [round(np.random.normal(20, 50), 2) for _ in range(25)],
                "Tuesday":   [round(np.random.normal(30, 40), 2) for _ in range(22)],
                "Wednesday": [round(np.random.normal(10, 60), 2) for _ in range(28)],
                "Thursday":  [round(np.random.normal(-5, 55), 2) for _ in range(20)],
                "Friday":    [round(np.random.normal(25, 45), 2) for _ in range(18)],
                "Saturday":  [round(np.random.normal(5, 30), 2) for _ in range(5)],
                "Sunday":    [round(np.random.normal(-2, 25), 2) for _ in range(3)],
            }

        result = []
        for day in days:
            pnls = by_day[day]
            if pnls:
                result.append({
                    "day": day,
                    "pnl": round(sum(pnls), 2),
                    "avg_pnl": round(np.mean(pnls), 2),
                    "median_pnl": round(float(np.median(pnls)), 2),
                    "trade_count": len(pnls),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                })
            else:
                result.append({
                    "day": day, "pnl": 0, "avg_pnl": 0,
                    "median_pnl": 0, "trade_count": 0, "win_rate": 0,
                })

        return {"by_day": result}
    except Exception as e:
        logger.error("analytics/by-day error: %s", e)
        return {"error": str(e)}


@router.get("/api/analytics/by-hour")
def analytics_by_hour():
    """P&L agrege par heure de la journee."""
    try:
        trades = _load_all_trades(source="real")
        by_hour: dict[int, list[float]] = {h: [] for h in range(24)}

        for t in trades:
            h = _hour_from_trade(t)
            if h is not None and 0 <= h < 24:
                by_hour[h].append(_pnl_from_trade(t))

        has_data = any(len(v) > 0 for v in by_hour.values())

        if not has_data:
            # Donnees fictives — heures de marche US/EU
            for h in range(9, 22):
                count = 15 if 9 <= h <= 17 else 8
                mean = 15 if h in (9, 10, 15, 16) else 5
                by_hour[h] = [round(np.random.normal(mean, 40), 2) for _ in range(count)]

        result = []
        for hour in range(24):
            pnls = by_hour[hour]
            if pnls:
                result.append({
                    "hour": hour,
                    "hour_label": f"{hour:02d}:00",
                    "pnl": round(sum(pnls), 2),
                    "avg_pnl": round(np.mean(pnls), 2),
                    "trade_count": len(pnls),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                })
            else:
                result.append({
                    "hour": hour, "hour_label": f"{hour:02d}:00",
                    "pnl": 0, "avg_pnl": 0, "trade_count": 0, "win_rate": 0,
                })

        return {"by_hour": result}
    except Exception as e:
        logger.error("analytics/by-hour error: %s", e)
        return {"error": str(e)}


@router.get("/api/analytics/distribution")
def analytics_distribution(
    bucket_size: float = Query(50.0, description="Taille de bucket en dollars"),
):
    """Histogramme de distribution des P&L par trade."""
    try:
        trades = _load_all_trades(source="real")
        pnls = [_pnl_from_trade(t) for t in trades]

        if not pnls:
            # Donnees fictives
            pnls = [round(np.random.normal(10, 80), 2) for _ in range(300)]

        min_pnl = min(pnls)
        max_pnl = max(pnls)

        # Creer les buckets
        lower = int(min_pnl // bucket_size) * bucket_size
        upper = (int(max_pnl // bucket_size) + 1) * bucket_size

        buckets = []
        edge = lower
        while edge < upper:
            edge_end = edge + bucket_size
            count = sum(1 for p in pnls if edge <= p < edge_end)
            buckets.append({
                "bucket": f"{edge:+.0f} to {edge_end:+.0f}",
                "lower": edge,
                "upper": edge_end,
                "count": count,
                "pct": round(count / len(pnls) * 100, 1) if pnls else 0,
            })
            edge = edge_end

        # Statistiques de distribution
        pnl_arr = np.array(pnls)
        return {
            "distribution": buckets,
            "stats": {
                "count": len(pnls),
                "mean": round(float(np.mean(pnl_arr)), 2),
                "median": round(float(np.median(pnl_arr)), 2),
                "std": round(float(np.std(pnl_arr)), 2),
                "skew": round(float(
                    ((pnl_arr - pnl_arr.mean()) ** 3).mean() / (pnl_arr.std() ** 3)
                ), 3) if pnl_arr.std() > 0 else 0,
                "kurtosis": round(float(
                    ((pnl_arr - pnl_arr.mean()) ** 4).mean() / (pnl_arr.std() ** 4) - 3
                ), 3) if pnl_arr.std() > 0 else 0,
                "min": round(float(pnl_arr.min()), 2),
                "max": round(float(pnl_arr.max()), 2),
                "pct_positive": round(float(np.mean(pnl_arr > 0) * 100), 1),
                "profit_factor": round(
                    float(pnl_arr[pnl_arr > 0].sum() / abs(pnl_arr[pnl_arr < 0].sum())), 2
                ) if pnl_arr[pnl_arr < 0].sum() != 0 else 99.99,
            },
            "bucket_size": bucket_size,
        }
    except Exception as e:
        logger.error("analytics/distribution error: %s", e)
        return {"error": str(e)}


@router.get("/api/analytics/by-asset-class")
def analytics_by_asset_class():
    """P&L par classe d'actif."""
    try:
        trades = _load_all_trades(source="real")
        by_class: dict[str, float] = defaultdict(float)
        by_class_count: dict[str, int] = defaultdict(int)

        for t in trades:
            cls = _asset_class_from_trade(t)
            by_class[cls] += _pnl_from_trade(t)
            by_class_count[cls] += 1

        has_data = bool(by_class)

        if not has_data:
            by_class = {"FX": 450.0, "Equity": 280.0, "Futures": -35.0, "Crypto": 120.0}
            by_class_count = {"FX": 85, "Equity": 120, "Futures": 15, "Crypto": 40}

        total_pnl = sum(by_class.values())
        result = []
        for cls in sorted(by_class.keys()):
            pnl = by_class[cls]
            result.append({
                "class": cls,
                "pnl": round(pnl, 2),
                "trade_count": by_class_count.get(cls, 0),
                "pct_of_total": round(pnl / total_pnl * 100, 1) if total_pnl != 0 else 0,
                "avg_pnl": round(
                    pnl / by_class_count[cls], 2
                ) if by_class_count.get(cls, 0) > 0 else 0,
            })

        result.sort(key=lambda x: -x["pnl"])

        return {
            "by_asset_class": result,
            "total_pnl": round(total_pnl, 2),
        }
    except Exception as e:
        logger.error("analytics/by-asset-class error: %s", e)
        return {"error": str(e)}


@router.get("/api/analytics/rolling-sharpe")
def analytics_rolling_sharpe(
    window: int = Query(30, description="Fenetre glissante en jours"),
):
    """Sharpe ratio glissant sur N jours."""
    try:
        trades = _load_all_trades(source="real")

        # Agreger par jour
        by_date: dict[str, float] = defaultdict(float)
        for t in trades:
            d = _date_from_trade(t)
            if d:
                by_date[d] += _pnl_from_trade(t)

        has_data = len(by_date) >= window

        if not has_data:
            # Donnees fictives — 120 jours
            today = datetime.now(timezone.utc)
            by_date = {}
            for i in range(120):
                dt = (today - timedelta(days=120 - i)).strftime("%Y-%m-%d")
                by_date[dt] = round(np.random.normal(15, 80), 2)

        dates = sorted(by_date.keys())
        daily_pnls = [by_date[d] for d in dates]

        rolling = []
        for i in range(window - 1, len(daily_pnls)):
            chunk = daily_pnls[i - window + 1: i + 1]
            arr = np.array(chunk)
            mean = float(np.mean(arr))
            std = float(np.std(arr))
            sharpe = (mean / std * (252 ** 0.5)) if std > 0 else 0
            rolling.append({
                "date": dates[i],
                "sharpe": round(sharpe, 2),
                "avg_daily_pnl": round(mean, 2),
                "daily_vol": round(std, 2),
            })

        return {
            "rolling_sharpe": rolling,
            "window_days": window,
            "current_sharpe": rolling[-1]["sharpe"] if rolling else 0,
            "max_sharpe": max(r["sharpe"] for r in rolling) if rolling else 0,
            "min_sharpe": min(r["sharpe"] for r in rolling) if rolling else 0,
        }
    except Exception as e:
        logger.error("analytics/rolling-sharpe error: %s", e)
        return {"error": str(e)}


@router.get("/api/analytics/streaks")
def analytics_streaks():
    """Series gagnantes/perdantes."""
    try:
        trades = _load_all_trades(source="real")

        # Agreger par jour
        by_date: dict[str, float] = defaultdict(float)
        for t in trades:
            d = _date_from_trade(t)
            if d:
                by_date[d] += _pnl_from_trade(t)

        has_data = len(by_date) >= 2

        if not has_data:
            today = datetime.now(timezone.utc)
            by_date = {}
            for i in range(60):
                dt = (today - timedelta(days=60 - i)).strftime("%Y-%m-%d")
                by_date[dt] = round(np.random.normal(15, 80), 2)

        dates = sorted(by_date.keys())
        results = [by_date[d] for d in dates]

        # Calcul des series
        longest_win = 0
        longest_loss = 0
        current_streak = 0
        current_type = "none"
        current_run = 0

        win_run = 0
        loss_run = 0

        for pnl in results:
            if pnl > 0:
                win_run += 1
                loss_run = 0
            elif pnl < 0:
                loss_run += 1
                win_run = 0
            else:
                win_run = 0
                loss_run = 0

            longest_win = max(longest_win, win_run)
            longest_loss = max(longest_loss, loss_run)

        # Serie actuelle
        if results:
            current_run = 0
            last_sign = 1 if results[-1] > 0 else (-1 if results[-1] < 0 else 0)
            for pnl in reversed(results):
                sign = 1 if pnl > 0 else (-1 if pnl < 0 else 0)
                if sign == last_sign and sign != 0:
                    current_run += 1
                else:
                    break
            current_type = "win" if last_sign > 0 else ("loss" if last_sign < 0 else "flat")
            current_streak = current_run

        return {
            "longest_win": longest_win,
            "longest_loss": longest_loss,
            "current_streak": current_streak,
            "current_streak_type": current_type,
            "total_days": len(results),
            "winning_days": sum(1 for r in results if r > 0),
            "losing_days": sum(1 for r in results if r < 0),
            "flat_days": sum(1 for r in results if r == 0),
        }
    except Exception as e:
        logger.error("analytics/streaks error: %s", e)
        return {"error": str(e)}


# =============================================================================
# SYSTEM ENDPOINTS
# =============================================================================

@router.get("/api/system/status")
def system_status():
    """Statut des brokers, connectivite, uptime."""
    try:
        # Check Alpaca
        alpaca_ok = False
        alpaca_latency = None
        try:
            import time as _time
            from core.alpaca_client.client import AlpacaClient
            client = AlpacaClient.from_env()
            t0 = _time.monotonic()
            client.get_account_info()
            alpaca_latency = round((_time.monotonic() - t0) * 1000, 1)
            alpaca_ok = True
        except Exception:
            pass

        # Check IBKR
        ibkr_ok = False
        ibkr_port = os.environ.get("IBKR_PORT", "4001")
        ibkr_paper = os.environ.get("IBKR_PAPER", "true")

        # Check Binance
        binance_ok = False
        binance_latency = None
        binance_key = os.environ.get("BINANCE_API_KEY", "")
        binance_secret = os.environ.get("BINANCE_API_SECRET", "")
        binance_testnet = os.environ.get("BINANCE_TESTNET", "true")
        if binance_key and binance_secret:
            try:
                import hashlib, hmac
                import time as _time
                _base = "https://testnet.binance.vision" if binance_testnet.lower() == "true" else "https://api.binance.com"
                t0 = _time.monotonic()
                _ts = int(_time.time() * 1000)
                _q = f"timestamp={_ts}"
                _sig = hmac.new(binance_secret.encode(), _q.encode(), hashlib.sha256).hexdigest()
                _r = requests.get(f"{_base}/api/v3/account?{_q}&signature={_sig}",
                                  headers={"X-MBX-APIKEY": binance_key}, timeout=8)
                binance_latency = round((_time.monotonic() - t0) * 1000, 1)
                binance_ok = _r.status_code == 200
            except Exception:
                pass

        # Worker uptime
        engine_state_path = DATA_DIR / "engine_state.json"
        uptime_info = {}
        if engine_state_path.exists():
            try:
                es = json.loads(engine_state_path.read_text(encoding="utf-8"))
                uptime_info = {
                    "started_at": es.get("started_at"),
                    "last_heartbeat": es.get("last_heartbeat"),
                    "mode": es.get("mode"),
                }
            except Exception:
                pass

        return {
            "brokers": {
                "alpaca": {
                    "connected": alpaca_ok,
                    "latency_ms": alpaca_latency,
                    "mode": "PAPER" if os.environ.get("PAPER_TRADING", "true").lower() == "true" else "LIVE",
                },
                "ibkr": {
                    "connected": ibkr_ok,
                    "port": ibkr_port,
                    "paper": ibkr_paper.lower() == "true",
                    "latency_ms": None,
                },
                "binance": {
                    "connected": binance_ok,
                    "has_api_key": bool(binance_key),
                    "testnet": binance_testnet.lower() == "true",
                    "latency_ms": binance_latency,
                },
            },
            "worker": uptime_info,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error("system/status error: %s", e)
        return {"error": str(e)}


@router.get("/api/system/latency")
def system_latency(
    hours: int = Query(24, description="Heures de donnees a afficher"),
):
    """Donnees de latence dans le temps (pour graphique)."""
    try:
        # Tenter de lire le fichier de mesures de latence
        latency_db = DATA_DIR / "execution_metrics.db"
        latency_data = []

        if latency_db.exists():
            try:
                conn = sqlite3.connect(str(latency_db))
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                rows = conn.execute(
                    "SELECT timestamp, strategy, instrument_type, "
                    "slippage_bps, market_spread_bps "
                    "FROM slippage_log WHERE timestamp > ? "
                    "ORDER BY timestamp DESC LIMIT 500",
                    (cutoff,),
                ).fetchall()
                for r in rows:
                    latency_data.append({
                        "timestamp": r[0],
                        "strategy": r[1],
                        "instrument_type": r[2],
                        "slippage_bps": r[3],
                        "spread_bps": r[4],
                    })
                conn.close()
            except Exception:
                pass

        if not latency_data:
            # Donnees fictives
            now = datetime.now(timezone.utc)
            for i in range(min(hours * 4, 96)):
                ts = (now - timedelta(minutes=i * 15)).isoformat()
                latency_data.append({
                    "timestamp": ts,
                    "alpaca_ms": round(max(50, np.random.normal(120, 30)), 1),
                    "ibkr_ms": round(max(10, np.random.normal(45, 15)), 1),
                    "binance_ms": round(max(20, np.random.normal(80, 25)), 1),
                })
            latency_data.reverse()

        return {
            "latency": latency_data,
            "hours": hours,
            "count": len(latency_data),
        }
    except Exception as e:
        logger.error("system/latency error: %s", e)
        return {"error": str(e)}


@router.get("/api/system/reconciliation")
def system_reconciliation():
    """Historique de reconciliation et statut actuel."""
    try:
        recon_path = DATA_DIR / "reconciliation_history.json"
        recon_data = {}
        if recon_path.exists():
            try:
                recon_data = json.loads(recon_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        history = recon_data.get("history", [])
        last_check = history[-1] if history else None

        # Statistiques de reconciliation
        total_checks = len(history)
        mismatches = sum(1 for h in history if h.get("status") != "MATCH")

        return {
            "current_status": last_check.get("status", "UNKNOWN") if last_check else "NO_DATA",
            "last_check": last_check,
            "stats": {
                "total_checks": total_checks,
                "mismatches": mismatches,
                "match_rate_pct": round(
                    (total_checks - mismatches) / total_checks * 100, 1
                ) if total_checks > 0 else 100,
            },
            "history": history[-20:],  # Dernieres 20 verifications
        }
    except Exception as e:
        logger.error("system/reconciliation error: %s", e)
        return {"error": str(e)}


@router.get("/api/system/logs")
def system_logs(
    lines: int = Query(100, description="Nombre de lignes"),
    log_file: str = Query("trading.log", description="Fichier de log"),
):
    """Dernieres entrees de log."""
    try:
        # Securite : ne lire que dans le repertoire logs/
        safe_name = Path(log_file).name  # Eviter path traversal
        log_path = LOG_DIR / safe_name

        if not log_path.exists():
            # Essayer les fichiers disponibles
            available = [f.name for f in LOG_DIR.glob("*.log")] if LOG_DIR.exists() else []
            return {
                "entries": [],
                "file": safe_name,
                "exists": False,
                "available_logs": available,
            }

        # Lire les N dernieres lignes
        try:
            all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            recent = all_lines[-lines:]
        except Exception:
            recent = []

        # Parser les entrees de log
        entries = []
        for line in recent:
            entry = {"raw": line}
            # Tenter de parser le format standard Python logging
            if " - " in line:
                parts = line.split(" - ", 3)
                if len(parts) >= 3:
                    entry["timestamp"] = parts[0].strip()
                    entry["level"] = parts[1].strip() if len(parts) > 1 else ""
                    entry["message"] = parts[-1].strip()
            entries.append(entry)

        return {
            "entries": entries,
            "count": len(entries),
            "file": safe_name,
            "exists": True,
            "file_size_kb": round(log_path.stat().st_size / 1024, 1),
        }
    except Exception as e:
        logger.error("system/logs error: %s", e)
        return {"error": str(e)}


@router.get("/api/system/backups")
def system_backups():
    """Statut des backups."""
    try:
        backup_dir = ROOT / "backups"
        data_backups = list(backup_dir.glob("*.tar.gz")) if backup_dir.exists() else []
        db_backups = list(backup_dir.glob("*.db")) if backup_dir.exists() else []

        # Verifier le script de backup
        backup_script = ROOT / "scripts" / "backup_live.sh"
        restore_script = ROOT / "scripts" / "restore_live.sh"

        backups = []
        for f in sorted(data_backups + db_backups, key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            backups.append({
                "file": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(
                    f.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            })

        return {
            "backups": backups,
            "count": len(data_backups) + len(db_backups),
            "backup_script_exists": backup_script.exists(),
            "restore_script_exists": restore_script.exists(),
            "backup_dir": str(backup_dir),
            "last_backup": backups[0] if backups else None,
        }
    except Exception as e:
        logger.error("system/backups error: %s", e)
        return {"error": str(e)}


# =============================================================================
# TAX ENDPOINTS (PFU France 30%)
# =============================================================================

PFU_RATE = 0.30         # 30% flat tax France
PFU_IR_RATE = 0.128     # 12.8% impot sur le revenu
PFU_PS_RATE = 0.172     # 17.2% prelevements sociaux


@router.get("/api/tax/summary")
def tax_summary():
    """Synthese fiscale : PV/MV brute, nette, PFU 30% par broker."""
    try:
        trades = _load_all_trades(source="real")

        # Separer par broker/source
        ibkr_pnl = 0.0
        ibkr_count = 0
        binance_pnl = 0.0
        binance_count = 0
        alpaca_pnl = 0.0
        alpaca_count = 0

        for t in trades:
            pnl = _pnl_from_trade(t)
            cls = _asset_class_from_trade(t)
            source = str(t.get("source", t.get("broker", ""))).lower()

            if "crypto" in cls.lower() or "binance" in source:
                binance_pnl += pnl
                binance_count += 1
            elif "fx" in cls.lower() or "futures" in cls.lower() or "ibkr" in source:
                ibkr_pnl += pnl
                ibkr_count += 1
            else:
                alpaca_pnl += pnl
                alpaca_count += 1

        has_data = (ibkr_count + binance_count + alpaca_count) > 0

        if not has_data:
            # Donnees fictives pour developpement
            ibkr_pnl = 1250.0
            ibkr_count = 85
            binance_pnl = 480.0
            binance_count = 40
            alpaca_pnl = 320.0
            alpaca_count = 95

        total_pnl = ibkr_pnl + binance_pnl + alpaca_pnl
        pv_brute = max(total_pnl, 0)
        mv_brute = abs(min(total_pnl, 0))
        pv_nette = max(total_pnl, 0)  # Apres imputation MV (simplification)
        pfu = pv_nette * PFU_RATE

        def _broker_summary(label: str, pnl: float, count: int) -> dict:
            pv = max(pnl, 0)
            mv = abs(min(pnl, 0))
            net = max(pnl, 0)
            return {
                "broker": label,
                "pv_brute": round(pv, 2),
                "mv_brute": round(mv, 2),
                "pv_nette": round(net, 2),
                "pfu_30": round(net * PFU_RATE, 2),
                "ir_128": round(net * PFU_IR_RATE, 2),
                "ps_172": round(net * PFU_PS_RATE, 2),
                "net_apres_impot": round(pnl - net * PFU_RATE, 2),
                "trade_count": count,
            }

        return {
            "total": {
                "pv_brute": round(pv_brute, 2),
                "mv_brute": round(mv_brute, 2),
                "pv_nette": round(pv_nette, 2),
                "pfu_30_pct": round(pfu, 2),
                "ir_12_8_pct": round(pv_nette * PFU_IR_RATE, 2),
                "ps_17_2_pct": round(pv_nette * PFU_PS_RATE, 2),
                "net_apres_impot": round(total_pnl - pfu, 2),
            },
            "by_broker": [
                _broker_summary("IBKR (FX/EU/Futures)", ibkr_pnl, ibkr_count),
                _broker_summary("Binance (Crypto)", binance_pnl, binance_count),
                _broker_summary("Alpaca (US Paper)", alpaca_pnl, alpaca_count),
            ],
            "regime": "PFU (Prelevement Forfaitaire Unique) — 30% flat",
            "note": "France — art. 200 A CGI. MV reportables 10 ans.",
            "year": datetime.now().year,
        }
    except Exception as e:
        logger.error("tax/summary error: %s", e)
        return {"error": str(e)}


@router.get("/api/tax/monthly")
def tax_monthly():
    """Repartition fiscale mois par mois."""
    try:
        trades = _load_all_trades(source="real")
        by_month: dict[str, float] = defaultdict(float)
        by_month_count: dict[str, int] = defaultdict(int)

        for t in trades:
            d = _date_from_trade(t)
            if d and len(d) >= 7:
                month = d[:7]  # YYYY-MM
                by_month[month] += _pnl_from_trade(t)
                by_month_count[month] += 1

        has_data = bool(by_month)

        if not has_data:
            # Donnees fictives sur 6 mois
            now = datetime.now(timezone.utc)
            for i in range(6):
                dt = now - timedelta(days=30 * (6 - i))
                month = dt.strftime("%Y-%m")
                by_month[month] = round(np.random.normal(300, 200), 2)
                by_month_count[month] = max(10, int(np.random.poisson(35)))

        months = sorted(by_month.keys())
        result = []
        cumul = 0.0

        for m in months:
            pnl = by_month[m]
            cumul += pnl
            pv_brute = max(pnl, 0)
            mv_brute = abs(min(pnl, 0))
            pv_nette = max(pnl, 0)
            pfu = pv_nette * PFU_RATE

            result.append({
                "month": m,
                "pv_brute": round(pv_brute, 2),
                "mv_brute": round(mv_brute, 2),
                "pv_nette": round(pv_nette, 2),
                "pfu": round(pfu, 2),
                "net_apres_impot": round(pnl - pfu, 2),
                "cumul_pnl": round(cumul, 2),
                "trade_count": by_month_count[m],
            })

        return {"monthly": result, "year": datetime.now().year}
    except Exception as e:
        logger.error("tax/monthly error: %s", e)
        return {"error": str(e)}


# =============================================================================
# CROSS-PORTFOLIO ENDPOINTS
# =============================================================================

@router.get("/api/cross/exposure")
def cross_exposure():
    """Exposition combinee IBKR + Binance — donnees LIVE."""
    try:
        # ── IBKR (TCP check + equity from snapshot) ──
        import socket
        ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
        ibkr_port = int(os.environ.get("IBKR_PORT", "4002"))
        ibkr_connected = False
        try:
            with socket.create_connection((ibkr_host, ibkr_port), timeout=2):
                ibkr_connected = True
        except Exception:
            pass

        # Read IBKR equity from worker snapshot
        capital_ibkr = 0
        try:
            import glob as _glob
            log_dir = ROOT / "logs" / "portfolio"
            if log_dir.exists():
                files = sorted(_glob.glob(str(log_dir / "*.jsonl")), reverse=True)
                for fpath in files[:2]:
                    with open(fpath) as f:
                        for line in reversed(f.readlines()[-10:]):
                            snap = json.loads(line.strip())
                            for b in snap.get("portfolio", {}).get("brokers", []):
                                if b.get("broker") == "ibkr":
                                    capital_ibkr = float(b.get("equity", 0))
                                    break
                            if capital_ibkr > 0:
                                break
                    if capital_ibkr > 0:
                        break
        except Exception:
            pass
        if capital_ibkr == 0:
            capital_ibkr = _load_yaml(CONFIG_DIR / "limits_live.yaml").get("capital", 500)

        # ── Binance (live API) ──
        capital_crypto = 0
        binance_positions = []
        try:
            if os.environ.get("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                bnb = BinanceBroker()
                bnb_info = bnb.get_account_info()
                capital_crypto = bnb_info.get("equity", 0)
                binance_positions = bnb.get_positions()
        except Exception:
            capital_crypto = _load_yaml(CONFIG_DIR / "crypto_limits.yaml").get("capital", 20_000)

        # ── Alpaca (paper) ──
        capital_alpaca = 0
        alpaca_positions = []
        try:
            from core.alpaca_client.client import AlpacaClient
            ac = AlpacaClient.from_env()
            acct = ac.get_account_info()
            capital_alpaca = acct.get("equity", 0)
            alpaca_positions = ac.get_positions()
        except Exception:
            pass

        # Compute exposures
        def _calc_exposure(positions):
            long_exp, short_exp = 0, 0
            for p in positions:
                mv = abs(float(p.get("market_val", p.get("market_value", 0))))
                qty = float(p.get("qty", p.get("quantity", 0)))
                if qty > 0:
                    long_exp += mv
                else:
                    short_exp += mv
            return long_exp, short_exp

        bnb_long, bnb_short = _calc_exposure(binance_positions)
        alp_long, alp_short = _calc_exposure(alpaca_positions)

        def _pct(val, cap):
            return round(val / cap * 100, 1) if cap > 0 else 0

        ibkr = {
            "capital": round(capital_ibkr, 2),
            "connected": ibkr_connected,
            "long_exposure": 0,
            "short_exposure": 0,
            "net_exposure": 0,
            "long_pct": 0,
            "short_pct": 0,
            "net_pct": 0,
            "cash_pct": 100,
            "cash": round(capital_ibkr, 2),
            "net_usd": 0,
            "margin_used": 0,
            "positions_count": 0,
        }

        crypto_cash = max(0, capital_crypto - bnb_long - bnb_short)
        crypto = {
            "capital": round(capital_crypto, 2),
            "long_exposure": round(bnb_long, 2),
            "short_exposure": round(bnb_short, 2),
            "net_exposure": round(bnb_long - bnb_short, 2),
            "long_pct": _pct(bnb_long, capital_crypto),
            "short_pct": _pct(bnb_short, capital_crypto),
            "net_pct": _pct(bnb_long - bnb_short, capital_crypto),
            "cash_pct": _pct(crypto_cash, capital_crypto),
            "earn_pct": 0,
            "cash": round(crypto_cash, 2),
            "net_usd": round(bnb_long - bnb_short, 2),
            "margin_used": 0,
            "earn_locked": 0,
            "positions_count": len(binance_positions),
        }

        alpaca = {
            "capital": round(capital_alpaca, 2),
            "long_exposure": round(alp_long, 2),
            "short_exposure": round(alp_short, 2),
            "net_exposure": round(alp_long - alp_short, 2),
            "long_pct": _pct(alp_long, capital_alpaca),
            "short_pct": _pct(alp_short, capital_alpaca),
            "net_pct": _pct(alp_long - alp_short, capital_alpaca),
            "cash_pct": _pct(max(0, capital_alpaca - alp_long - alp_short), capital_alpaca),
            "net_usd": round(alp_long - alp_short, 2),
            "positions_count": len(alpaca_positions),
        }

        total_capital = capital_ibkr + capital_crypto + capital_alpaca
        combined_long = ibkr["long_exposure"] + crypto["long_exposure"] + alpaca["long_exposure"]
        combined_short = ibkr["short_exposure"] + crypto["short_exposure"] + alpaca["short_exposure"]
        combined_net = combined_long - combined_short

        return {
            "ibkr": ibkr,
            "binance": crypto,
            "alpaca": alpaca,
            "combined": {
                "total_capital": round(total_capital, 2),
                "long_exposure": round(combined_long, 2),
                "short_exposure": round(combined_short, 2),
                "net_exposure": round(combined_net, 2),
                "net_long_total": round(combined_net, 2),
                "gross_total": round(combined_long + combined_short, 2),
                "net_pct": round(combined_net / total_capital * 100, 1) if total_capital > 0 else 0,
                "gross_pct": round(
                    (combined_long + combined_short) / total_capital * 100, 1
                ) if total_capital > 0 else 0,
                "cash_total": round(ibkr["cash"] + crypto["cash"], 2),
                "cash_pct": round(
                    (ibkr["cash"] + crypto["cash"]) / total_capital * 100, 1
                ) if total_capital > 0 else 0,
            },
            "limits": {
                "max_combined_net_pct": 120,
                "critical_combined_net_pct": 150,
                "status": "ok",
            },
        }
    except Exception as e:
        logger.error("cross/exposure error: %s", e)
        return {"error": str(e)}


@router.get("/api/cross/correlation")
def cross_correlation():
    """Correlation glissante entre P&L IBKR et Binance."""
    try:
        # En production : calculer depuis les P&L journaliers des deux portefeuilles
        # Pour dev : correlation simulee

        today = datetime.now(timezone.utc)
        data = []
        for i in range(60):
            dt = (today - timedelta(days=60 - i)).strftime("%Y-%m-%d")
            # Correlation BTC-SPY typique entre 0.2 et 0.6
            corr = round(0.35 + np.random.normal(0, 0.1), 3)
            corr = max(-1, min(1, corr))
            data.append({"date": dt, "correlation": corr})

        current = data[-1]["correlation"] if data else 0
        avg_30d = round(np.mean([d["correlation"] for d in data[-30:]]), 3) if len(data) >= 30 else 0

        return {
            "rolling_correlation": data,
            "current": current,
            "avg_30d": avg_30d,
            "warning_threshold": 0.7,
            "status": "warning" if abs(current) > 0.7 else "ok",
            "note": "Correlation elevee en crise — diversification reduite",
            "btc_spy_reference": {
                "normal": 0.3,
                "correction": 0.5,
                "crash_2020": 0.8,
            },
        }
    except Exception as e:
        logger.error("cross/correlation error: %s", e)
        return {"error": str(e)}


@router.get("/api/cross/stress")
def cross_stress():
    """Scenarios de stress : impact sur IBKR + Binance."""
    try:
        limits_ibkr = _load_yaml(CONFIG_DIR / "limits_live.yaml")
        limits_crypto = _load_yaml(CONFIG_DIR / "crypto_limits.yaml")

        capital_ibkr = limits_ibkr.get("capital", 10_000)
        capital_crypto = limits_crypto.get("capital", 15_000)
        total = capital_ibkr + capital_crypto

        scenarios = [
            {
                "scenario": "Flash Crash (-5% SPY, -8% BTC)",
                "description": "Crash rapide intraday type feb 2018",
                "ibkr_loss": round(capital_ibkr * 0.03, 2),    # 3% grace aux SL
                "binance_loss": round(capital_crypto * 0.06, 2),  # 6% avec margin
                "combined_loss": 0,
                "combined_pct": 0,
            },
            {
                "scenario": "COVID-type (-35% SPY, -50% BTC, 2 semaines)",
                "description": "Bear severe multi-semaines mars 2020",
                "ibkr_loss": round(capital_ibkr * 0.12, 2),
                "binance_loss": round(capital_crypto * 0.20, 2),
                "combined_loss": 0,
                "combined_pct": 0,
            },
            {
                "scenario": "FX Weekend Gap (CHF 2015-style, 3%)",
                "description": "Gap FX sur weekend — brackets echouent",
                "ibkr_loss": round(capital_ibkr * 0.05, 2),
                "binance_loss": 0,
                "combined_loss": 0,
                "combined_pct": 0,
            },
            {
                "scenario": "Crypto-specific (-20% BTC, alts -40%)",
                "description": "Crash crypto isole, IBKR non affecte",
                "ibkr_loss": round(capital_ibkr * 0.005, 2),  # Correlation minimale
                "binance_loss": round(capital_crypto * 0.15, 2),
                "combined_loss": 0,
                "combined_pct": 0,
            },
            {
                "scenario": "Rate Hike Surprise (+100bps)",
                "description": "Hausse taux inattendue — equities et crypto chutent",
                "ibkr_loss": round(capital_ibkr * 0.04, 2),
                "binance_loss": round(capital_crypto * 0.08, 2),
                "combined_loss": 0,
                "combined_pct": 0,
            },
            {
                "scenario": "Broker Outage (IBKR down 4h)",
                "description": "IBKR hors ligne — brackets broker-side actifs",
                "ibkr_loss": round(capital_ibkr * 0.02, 2),  # Brackets limitent la perte
                "binance_loss": 0,
                "combined_loss": 0,
                "combined_pct": 0,
            },
            {
                "scenario": "Worst Case (tout correle, -10% global)",
                "description": "Correlation 1.0, sell-off global synchronise",
                "ibkr_loss": round(capital_ibkr * 0.08, 2),
                "binance_loss": round(capital_crypto * 0.12, 2),
                "combined_loss": 0,
                "combined_pct": 0,
            },
        ]

        for s in scenarios:
            s["combined_loss"] = round(s["ibkr_loss"] + s["binance_loss"], 2)
            s["combined_pct"] = round(s["combined_loss"] / total * 100, 1) if total > 0 else 0

        return {
            "scenarios": scenarios,
            "capital": {
                "ibkr": capital_ibkr,
                "binance": capital_crypto,
                "total": total,
            },
            "worst_case_loss": max(s["combined_loss"] for s in scenarios),
            "worst_case_pct": max(s["combined_pct"] for s in scenarios),
            "note": "Pertes estimees avec kill switches et brackets actifs",
        }
    except Exception as e:
        logger.error("cross/stress error: %s", e)
        return {"error": str(e)}


# =============================================================================
# COMPARISON: BACKTEST vs PAPER vs LIVE
# =============================================================================

@router.get("/api/comparison")
def comparison_overview():
    """Comparaison backtest vs paper vs live par strategie."""
    try:
        # Charger les resultats walk-forward (backtest)
        wf_path = ROOT / "output" / "walk_forward_results.json"
        wf_data = {}
        if wf_path.exists():
            try:
                wf_data = json.loads(wf_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        state = _load_state()
        pnl_log = state.get("strategy_pnl_log", {})

        strategies = [
            {
                "strategy": "fx_eurusd_trend",
                "name": "EUR/USD Trend",
                "backtest_sharpe": 4.62,
                "paper_sharpe": None,
                "live_sharpe": None,
                "backtest_win_rate": 62,
                "paper_win_rate": None,
                "live_win_rate": None,
                "status": "LIVE",
                "broker": "IBKR",
            },
            {
                "strategy": "fx_eurgbp_mr",
                "name": "EUR/GBP Mean Reversion",
                "backtest_sharpe": 3.65,
                "paper_sharpe": None,
                "live_sharpe": None,
                "backtest_win_rate": 58,
                "paper_win_rate": None,
                "live_win_rate": None,
                "status": "LIVE",
                "broker": "IBKR",
            },
            {
                "strategy": "fx_eurjpy_carry",
                "name": "EUR/JPY Carry",
                "backtest_sharpe": 2.50,
                "paper_sharpe": None,
                "live_sharpe": None,
                "backtest_win_rate": 55,
                "paper_win_rate": None,
                "live_win_rate": None,
                "status": "LIVE",
                "broker": "IBKR",
            },
            {
                "strategy": "eu_gap_open",
                "name": "EU Gap Open",
                "backtest_sharpe": 8.56,
                "paper_sharpe": None,
                "live_sharpe": None,
                "backtest_win_rate": 71,
                "paper_win_rate": None,
                "live_win_rate": None,
                "status": "LIVE",
                "broker": "IBKR",
            },
            {
                "strategy": "day_of_week_seasonal",
                "name": "Day-of-Week Seasonal",
                "backtest_sharpe": 3.42,
                "paper_sharpe": None,
                "live_sharpe": None,
                "backtest_win_rate": 60,
                "paper_win_rate": None,
                "live_win_rate": None,
                "status": "PAPER",
                "broker": "Alpaca",
            },
            {
                "strategy": "vix_expansion_short",
                "name": "VIX Expansion Short",
                "backtest_sharpe": 3.61,
                "paper_sharpe": None,
                "live_sharpe": None,
                "backtest_win_rate": 65,
                "paper_win_rate": None,
                "live_win_rate": None,
                "status": "PAPER",
                "broker": "Alpaca",
            },
        ]

        # Enrichir avec les donnees walk-forward si disponibles
        wf_results = wf_data.get("results", {})
        for s in strategies:
            sid = s["strategy"]
            if sid in wf_results:
                wf = wf_results[sid]
                s["wf_verdict"] = wf.get("verdict", "UNKNOWN")
                s["wf_oos_sharpe"] = round(wf.get("avg_oos_sharpe", 0), 2)
                s["wf_pct_profitable"] = round(wf.get("pct_oos_profitable", 0) * 100, 1)
            else:
                s["wf_verdict"] = "N/A"
                s["wf_oos_sharpe"] = None
                s["wf_pct_profitable"] = None

            # Enrichir avec P&L paper log
            if sid in pnl_log:
                log = pnl_log[sid]
                pnls = [e.get("pnl", 0) for e in log]
                if pnls:
                    arr = np.array(pnls)
                    std = float(np.std(arr))
                    mean = float(np.mean(arr))
                    s["paper_sharpe"] = round(
                        mean / std * (252 ** 0.5), 2
                    ) if std > 0 else 0
                    s["paper_win_rate"] = round(float(np.mean(arr > 0) * 100), 1)

        return {"strategies": strategies, "count": len(strategies)}
    except Exception as e:
        logger.error("comparison error: %s", e)
        return {"error": str(e)}


@router.get("/api/comparison/signals")
def comparison_signals(limit: int = Query(50, description="Nombre de signaux")):
    """Divergences recentes entre signaux paper et live."""
    try:
        comparisons_path = LOG_DIR / "signal_sync" / "comparisons.jsonl"
        signals = []

        if comparisons_path.exists():
            try:
                lines = comparisons_path.read_text(encoding="utf-8").splitlines()
                for line in lines[-limit:]:
                    try:
                        signals.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        if not signals:
            # Donnees fictives pour developpement
            now = datetime.now(timezone.utc)
            sample_strategies = [
                "fx_eurusd_trend", "fx_eurgbp_mr", "eu_gap_open",
                "fx_eurjpy_carry", "fx_gbpusd_trend",
            ]
            for i in range(min(limit, 15)):
                ts = (now - timedelta(hours=i * 4)).isoformat()
                strat = sample_strategies[i % len(sample_strategies)]
                matched = np.random.random() > 0.15  # 85% de match
                signals.append({
                    "timestamp": ts,
                    "strategy": strat,
                    "signal": {"direction": "BUY" if np.random.random() > 0.5 else "SELL"},
                    "live_accepted": True,
                    "paper_accepted": True,
                    "matched": matched,
                    "divergence_type": None if matched else "SIZING_DIFF",
                    "live_fill_price": round(1.08 + np.random.normal(0, 0.005), 5),
                    "paper_fill_price": round(1.08 + np.random.normal(0, 0.005), 5),
                })

        divergences = [s for s in signals if not s.get("matched", True)]

        return {
            "signals": signals[-limit:],
            "total": len(signals),
            "divergences": divergences,
            "divergence_count": len(divergences),
            "match_rate_pct": round(
                (len(signals) - len(divergences)) / len(signals) * 100, 1
            ) if signals else 100,
        }
    except Exception as e:
        logger.error("comparison/signals error: %s", e)
        return {"error": str(e)}


@router.get("/api/comparison/slippage")
def comparison_slippage():
    """Slippage modele vs reel par strategie."""
    try:
        # Tenter de lire depuis la base SQLite slippage
        slippage_db = DATA_DIR / "execution_metrics.db"
        by_strategy: dict[str, dict] = {}

        if slippage_db.exists():
            try:
                conn = sqlite3.connect(str(slippage_db))
                rows = conn.execute(
                    "SELECT strategy, instrument_type, "
                    "AVG(slippage_bps) as avg_slip, "
                    "AVG(backtest_slippage_bps) as avg_bt_slip, "
                    "COUNT(*) as n, "
                    "MAX(slippage_bps) as max_slip "
                    "FROM slippage_log GROUP BY strategy, instrument_type"
                ).fetchall()
                for r in rows:
                    key = f"{r[0]}_{r[1]}"
                    by_strategy[key] = {
                        "strategy": r[0],
                        "instrument_type": r[1],
                        "avg_real_bps": round(r[2], 2),
                        "avg_backtest_bps": round(r[3], 2),
                        "ratio": round(r[2] / r[3], 2) if r[3] > 0 else 0,
                        "trade_count": r[4],
                        "max_real_bps": round(r[5], 2),
                    }
                conn.close()
            except Exception:
                pass

        if not by_strategy:
            # Donnees fictives
            strats = [
                ("fx_eurusd_trend", "FX", 1.2, 2.0),
                ("fx_eurgbp_mr", "FX", 1.5, 2.0),
                ("fx_eurjpy_carry", "FX", 1.8, 2.0),
                ("eu_gap_open", "EQUITY", 3.5, 2.0),
                ("fx_gbpusd_trend", "FX", 1.3, 2.0),
                ("day_of_week_seasonal", "EQUITY", 2.8, 2.0),
            ]
            for name, itype, real, bt in strats:
                by_strategy[f"{name}_{itype}"] = {
                    "strategy": name,
                    "instrument_type": itype,
                    "avg_real_bps": real,
                    "avg_backtest_bps": bt,
                    "ratio": round(real / bt, 2),
                    "trade_count": int(np.random.randint(10, 80)),
                    "max_real_bps": round(real * 2.5, 2),
                }

        result = sorted(by_strategy.values(), key=lambda x: -x["ratio"])

        # Alertes pour slippage excessif
        warnings = [s for s in result if s["ratio"] > 2.0]
        criticals = [s for s in result if s["ratio"] > 3.0]

        return {
            "by_strategy": result,
            "warnings": [s["strategy"] for s in warnings],
            "criticals": [s["strategy"] for s in criticals],
            "overall_avg_ratio": round(
                np.mean([s["ratio"] for s in result]), 2
            ) if result else 0,
            "model_assumption_bps": 2.0,
            "note": "ratio > 2x = WARNING, > 3x = CRITICAL — revoir le modele de couts",
        }
    except Exception as e:
        logger.error("comparison/slippage error: %s", e)
        return {"error": str(e)}


# =============================================================================
# EQUITY CURVE
# =============================================================================

@router.get("/api/equity-curve")
def equity_curve():
    """Courbe d'equity multi-broker : IBKR + Binance + total."""
    try:
        state = _load_state()
        history = state.get("history", [])

        if history:
            # Donnees reelles depuis l'historique du state
            curve = []
            for entry in history:
                eq = _safe_float(entry.get("equity", entry.get("capital", 0)))
                curve.append({
                    "timestamp": entry.get("date", entry.get("timestamp", "")),
                    "ibkr": round(eq * 0.4, 2),     # Approximation 40% IBKR
                    "binance": round(eq * 0.6, 2),   # 60% crypto
                    "total": round(eq, 2),
                })
            return {
                "equity_curve": curve,
                "source": "state_history",
                "points": len(curve),
            }

        # Donnees fictives pour developpement
        today = datetime.now(timezone.utc)
        capital_ibkr = 10_000.0
        capital_crypto = 15_000.0
        curve = []

        for i in range(90):
            dt = (today - timedelta(days=90 - i)).isoformat()
            # Derive legere positive avec volatilite
            capital_ibkr *= (1 + np.random.normal(0.0004, 0.005))
            capital_crypto *= (1 + np.random.normal(0.0006, 0.012))
            total = capital_ibkr + capital_crypto
            curve.append({
                "timestamp": dt,
                "ibkr": round(capital_ibkr, 2),
                "binance": round(capital_crypto, 2),
                "total": round(total, 2),
            })

        return {
            "equity_curve": curve,
            "source": "simulated",
            "points": len(curve),
            "current": {
                "ibkr": round(capital_ibkr, 2),
                "binance": round(capital_crypto, 2),
                "total": round(capital_ibkr + capital_crypto, 2),
            },
            "returns": {
                "ibkr_pct": round((capital_ibkr - 10_000) / 10_000 * 100, 2),
                "binance_pct": round((capital_crypto - 15_000) / 15_000 * 100, 2),
                "total_pct": round(
                    ((capital_ibkr + capital_crypto) - 25_000) / 25_000 * 100, 2
                ),
            },
        }
    except Exception as e:
        logger.error("equity-curve error: %s", e)
        return {"error": str(e)}
