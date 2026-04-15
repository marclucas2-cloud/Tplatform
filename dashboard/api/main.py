"""
Trading Dashboard API — FastAPI backend.

Multi-broker (Alpaca + IBKR + Binance), JWT auth, static SPA serving.
"""
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Setup paths
ROOT = Path(__file__).resolve().parent.parent.parent
API_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # Project root FIRST (strategies.crypto lives here)
sys.path.append(str(ROOT / "archive" / "intraday-backtesterV2"))  # append, not insert — avoid shadowing strategies/
sys.path.insert(1, str(API_DIR))  # Pour strategy_registry

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard-api")

app = FastAPI(title="Trading Dashboard API", version="2.0.0")

# ── CORS ─────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "https://trading.aucoeurdeville-laval.fr",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ─────────────────────────────────────────────────────────────────────
from auth import router as auth_router
from chat import router as chat_router

app.include_router(auth_router)
app.include_router(chat_router)

# Auth middleware — protect all /api/* except /api/auth/* and /api/health
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/me", "/api/health", "/docs", "/openapi.json"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Skip auth for non-API routes (frontend static files), public paths, websocket
    if not path.startswith("/api/") or path in PUBLIC_PATHS or path.startswith("/api/auth/"):
        return await call_next(request)
    # Validate JWT
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing token"})
    from auth import _verify_token
    token = auth_header.split(" ", 1)[1]
    if _verify_token(token) is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    return await call_next(request)

# ── IBKR helpers (non-blocking, no ib_insync) ────────────────────────────────

def _check_ibkr_port() -> bool:
    """Check if IB Gateway port is open (fast TCP check, no ib_insync)."""
    import socket
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "4002"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def _get_ibkr_equity_from_snapshot() -> float:
    """Read IBKR equity from latest worker JSONL snapshot."""
    import glob
    log_dir = ROOT / "logs" / "portfolio"
    if not log_dir.exists():
        return 0.0
    files = sorted(glob.glob(str(log_dir / "*.jsonl")), reverse=True)
    for fpath in files[:2]:  # Check today + yesterday
        try:
            with open(fpath) as f:
                lines = f.readlines()
            for line in reversed(lines[-10:]):  # Last 10 entries
                snap = json.loads(line.strip())
                brokers = snap.get("portfolio", {}).get("brokers", [])
                for b in brokers:
                    if b.get("broker") == "ibkr":
                        return float(b.get("equity", 0))
        except Exception:
            continue
    return 0.0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_alpaca_client():
    from core.alpaca_client.client import AlpacaClient
    return AlpacaClient.from_env()


def _load_state() -> dict:
    state_file = ROOT / "data" / "state" / "paper_portfolio_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {}


def _get_strategies_config() -> dict:
    from scripts.paper_portfolio import STRATEGIES, TIER_ALLOCATION
    return STRATEGIES, TIER_ALLOCATION


def _tier_for_strategy(sid: str, tier_alloc: dict) -> str:
    pct = tier_alloc.get(sid, 0)
    if pct >= 0.20:
        return "S"
    elif pct >= 0.10:
        return "A"
    elif pct >= 0.01:
        return "B"
    else:
        return "C"


# ── Portfolio ────────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
def get_portfolio():
    """Etat global du portefeuille — LIVE et PAPER séparés.

    equity = LIVE only (Binance + IBKR).
    Alpaca paper est affiché séparément.
    """
    try:
        # Alpaca (PAPER — $100K, not real money)
        alpaca_equity, alpaca_cash, alpaca_positions = 0, 0, []
        alpaca_is_paper = True
        try:
            client = _get_alpaca_client()
            account = client.get_account_info()
            alpaca_positions = client.get_positions()
            alpaca_equity = account["equity"]
            alpaca_cash = account["cash"]
            alpaca_is_paper = os.environ.get("PAPER_TRADING", "true").lower() == "true"
        except Exception:
            pass

        # Binance (LIVE)
        binance_equity = 0
        try:
            if os.environ.get("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                bnb = BinanceBroker()
                bnb_info = bnb.get_account_info()
                binance_equity = bnb_info.get("equity", 0)
        except Exception:
            pass

        # IBKR (LIVE — read from worker snapshot)
        ibkr_equity = _get_ibkr_equity_from_snapshot()

        # LIVE equity = Binance + IBKR (real money only)
        live_equity = binance_equity + ibkr_equity
        # Include Alpaca only if NOT paper
        if not alpaca_is_paper:
            live_equity += alpaca_equity

        total_pnl = sum(p.get("unrealized_pl", 0) for p in alpaca_positions)

        # Daily P&L based on live equity only
        state = _load_state()
        daily_start = state.get("live_daily_start", live_equity or 20_000)
        pnl_day = live_equity - daily_start if daily_start > 0 else 0

        # Regime
        try:
            from scripts.paper_portfolio import get_market_regime
            regime = get_market_regime()
        except Exception:
            regime = {"regime": "UNKNOWN"}

        return {
            "equity": round(live_equity, 2),
            "cash": round(alpaca_cash if not alpaca_is_paper else 0, 2),
            "pnl_day": round(pnl_day, 2),
            "pnl_day_pct": round(pnl_day / daily_start * 100, 2) if daily_start > 0 else 0,
            "pnl_unrealized": round(total_pnl, 2),
            "positions_count": len(alpaca_positions),
            # Per-broker breakdown
            "alpaca_equity": round(alpaca_equity, 2),
            "alpaca_is_paper": alpaca_is_paper,
            "ibkr_equity": round(ibkr_equity, 2),
            "binance_equity": round(binance_equity, 2),
            # Paper vs Live totals
            "live_equity": round(live_equity, 2),
            "paper_equity": round(alpaca_equity if alpaca_is_paper else 0, 2),
            "initial_capital": daily_start,
            "total_return_pct": round((live_equity - daily_start) / daily_start * 100, 2) if daily_start > 0 else 0,
            "regime": regime.get("regime", "UNKNOWN"),
            "regime_detail": regime,
            "market_open": _is_market_open(),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        return {"error": str(e), "equity": 0, "cash": 0}


def _is_market_open() -> bool:
    try:
        from scripts.paper_portfolio import is_us_market_open
        return is_us_market_open()
    except Exception:
        return False


# ── Positions ────────────────────────────────────────────────────────────────

@app.get("/api/positions")
def get_positions():
    """Positions ouvertes."""
    try:
        client = _get_alpaca_client()
        positions = client.get_positions()
        state = _load_state()
        intraday_pos = state.get("intraday_positions", {})

        result = []
        for p in positions:
            sym = p["symbol"]
            pos_info = intraday_pos.get(sym, {})
            result.append({
                "ticker": sym,
                "direction": "LONG" if float(p.get("qty", 0)) > 0 else "SHORT",
                "shares": abs(float(p.get("qty", 0))),
                "entry_price": float(p.get("avg_entry", 0)),
                "current_price": abs(float(p.get("market_val", 0)) / float(p.get("qty", 1))) if float(p.get("qty", 0)) != 0 else 0,
                "pnl": float(p.get("unrealized_pl", 0)),
                "pnl_pct": float(p.get("unrealized_plpc", 0)) * 100,
                "market_value": float(p.get("market_val", 0)),
                "strategy": pos_info.get("strategy", "daily"),
                "stop_loss": pos_info.get("stop_loss"),
                "take_profit": pos_info.get("take_profit"),
            })

        total_long = sum(r["market_value"] for r in result if r["direction"] == "LONG")
        total_short = sum(abs(r["market_value"]) for r in result if r["direction"] == "SHORT")
        total = total_long + total_short

        # Total capital across all brokers for % calculations
        try:
            account = client.get_account_info()
            alpaca_eq = account.get("equity", 0)
        except Exception:
            alpaca_eq = 0
        binance_eq = 0
        try:
            if os.environ.get("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                binance_eq = BinanceBroker().get_account_info().get("equity", 0)
        except Exception:
            pass
        ibkr_eq = _get_ibkr_equity_from_snapshot()
        total_capital = alpaca_eq + binance_eq + ibkr_eq
        if total_capital == 0:
            total_capital = state.get("capital", 100_000)

        return {
            "positions": result,
            "count": len(result),
            "exposure_long": round(total_long, 2),
            "exposure_short": round(total_short, 2),
            "exposure_net": round(total_long - total_short, 2),
            "exposure_long_pct": round(total_long / total_capital * 100, 1) if total_capital > 0 else 0,
            "exposure_short_pct": round(total_short / total_capital * 100, 1) if total_capital > 0 else 0,
            "total_capital": round(total_capital, 2),
        }
    except Exception as e:
        return {"error": str(e), "positions": [], "count": 0}


# ── Strategies ───────────────────────────────────────────────────────────────

@app.get("/api/strategies")
def get_strategies():
    """Liste de toutes les strategies avec config, sante et phase lifecycle."""
    try:
        strategies, tier_alloc = _get_strategies_config()
        state = _load_state()
        pnl_log = state.get("strategy_pnl_log", {})

        # Charger les phases depuis le registre
        phase_map = {}
        try:
            from strategy_registry import STRATEGY_PHASES
            phase_map = STRATEGY_PHASES
        except Exception:
            pass

        result = []
        for sid, s in strategies.items():
            # Kill switch info
            log = pnl_log.get(sid, [])
            pnl_5d = sum(e.get("pnl", 0) for e in log[-5:]) if log else 0
            alloc_pct = tier_alloc.get(sid, 0)
            threshold = -alloc_pct * 100_000 * 0.02  # -2% du capital alloue
            tier = _tier_for_strategy(sid, tier_alloc)

            # Status legacy
            status = "ACTIVE"
            if pnl_5d < threshold and len(log) >= 5:
                status = "PAUSED"
            if sid == "triple_ema":
                try:
                    from scripts.paper_portfolio import get_market_regime
                    regime = get_market_regime()
                    if not regime.get("bull", True):
                        status = "DISABLED_BEAR"
                except Exception:
                    pass

            # Phase lifecycle (nouveau)
            phase_info = phase_map.get(sid, {})
            phase = phase_info.get("phase", "CODE")
            asset_class = phase_info.get("asset_class", "US")
            broker = phase_info.get("broker", "ALPACA")
            phase_since = phase_info.get("phase_since", "")

            result.append({
                "id": sid,
                "name": s["name"],
                "tier": tier,
                "status": status,
                "phase": phase,
                "asset_class": asset_class,
                "broker": broker,
                "phase_since": phase_since,
                "type": s.get("frequency", "intraday"),
                "sharpe": s["sharpe"],
                "allocation_pct": round(alloc_pct * 100, 1),
                "capital": round(alloc_pct * 100_000, 0),
                "pnl_5d": round(pnl_5d, 2),
                "kill_threshold": round(threshold, 2),
                "kill_margin_pct": round((pnl_5d - threshold) / abs(threshold) * 100, 0) if threshold != 0 else 100,
            })

        # Ajouter les strategies du phase_map qui ne sont pas dans le config
        existing_ids = {s["id"] for s in result}
        registry = _load_strategy_registry()
        for sid, info in phase_map.items():
            if sid not in existing_ids:
                reg_entry = registry.get(sid, {})
                bt = reg_entry.get("backtest", {})
                result.append({
                    "id": sid,
                    "name": reg_entry.get("name", sid.replace("_", " ").title()),
                    "tier": "C",
                    "status": "INACTIVE",
                    "phase": info.get("phase", "CODE"),
                    "asset_class": info.get("asset_class", ""),
                    "broker": info.get("broker", ""),
                    "phase_since": info.get("phase_since", ""),
                    "type": reg_entry.get("type", "daily"),
                    "sharpe": bt.get("sharpe", 0),
                    "allocation_pct": 0,
                    "capital": 0,
                    "pnl_5d": 0,
                    "kill_threshold": 0,
                    "kill_margin_pct": 100,
                })

        result.sort(key=lambda x: -x["sharpe"])
        return {"strategies": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e), "strategies": []}


def _get_total_strategies_count() -> int:
    """Count total strategies from STRATEGY_PHASES (most complete source)."""
    try:
        from strategy_registry import STRATEGY_PHASES
        return len(STRATEGY_PHASES)
    except Exception:
        return len(_load_strategy_registry())


def _load_strategy_registry() -> dict:
    """Charge le registre des strategies via importlib (pas exec)."""
    import importlib.util
    candidates = [
        API_DIR / "strategy_registry.py",
        ROOT / "dashboard" / "api" / "strategy_registry.py",
        Path(__file__).parent / "strategy_registry.py",
    ]
    for registry_path in candidates:
        try:
            registry_path = registry_path.resolve()
            if registry_path.exists():
                spec = importlib.util.spec_from_file_location("strategy_registry", registry_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                reg = getattr(mod, "STRATEGY_REGISTRY", {})
                if reg:
                    logger.info(f"Strategy registry loaded: {len(reg)} strategies from {registry_path}")
                    return reg
        except Exception as e:
            logger.warning(f"Failed to load registry from {registry_path}: {e}")
    logger.error("Strategy registry NOT FOUND in any candidate path")
    return {}


@app.get("/api/strategies/{strategy_id}")
def get_strategy_detail(strategy_id: str):
    """Detail complet d'une strategie avec registre (edge, parametres, SL/TP).

    V14: Cherche dans TOUTES les sources — paper_portfolio.py, strategy_registry,
    crypto strategies, FX, EU, futures. Plus jamais "introuvable".
    """
    try:
        STRATEGY_REGISTRY = _load_strategy_registry()

        strategies, tier_alloc = _get_strategies_config()

        # Phase lifecycle
        phase_info = {}
        try:
            from strategy_registry import STRATEGY_PHASES
            phase_info = STRATEGY_PHASES.get(strategy_id, {})
        except Exception:
            pass

        # Registre complet (description, edge, parametres)
        registry = STRATEGY_REGISTRY.get(strategy_id, {})

        # V14: Chercher dans paper_portfolio OU dans le registre phases
        if strategy_id in strategies:
            s = strategies[strategy_id]
            name = s["name"]
            sharpe = s["sharpe"]
            frequency = s.get("frequency", "intraday")
            tier = _tier_for_strategy(strategy_id, tier_alloc)
            alloc_pct = round(tier_alloc.get(strategy_id, 0) * 100, 1)
        elif strategy_id in phase_info or strategy_id in STRATEGY_REGISTRY:
            # Strat dans le registre mais pas dans paper_portfolio (crypto, FX live, etc.)
            bt = registry.get("backtest", {})
            name = registry.get("name", strategy_id.replace("_", " ").title())
            sharpe = bt.get("sharpe", 0)
            frequency = registry.get("type", phase_info.get("asset_class", "daily").lower())
            tier = "B" if phase_info.get("phase") == "LIVE" else "C"
            alloc_pct = 0
        else:
            return {"error": f"Strategy {strategy_id} not found"}

        # Load trades CSV if exists
        trades = []
        output_dir = ROOT / "archive" / "intraday-backtesterV2" / "output"
        safe_name = name.lower().replace(" ", "_") if name else strategy_id
        csv_candidates = list(output_dir.glob(f"trades_*{safe_name}*.csv"))
        if not csv_candidates:
            csv_candidates = list(output_dir.glob(f"trades_*{strategy_id}*.csv"))

        if csv_candidates:
            import pandas as pd
            df = pd.read_csv(csv_candidates[0])
            if not df.empty:
                trades = df.head(50).to_dict(orient="records")

        return {
            "id": strategy_id,
            "name": name,
            "tier": tier,
            "sharpe": sharpe,
            "frequency": frequency,
            "allocation_pct": alloc_pct,
            "phase": phase_info.get("phase", "CODE"),
            "asset_class": phase_info.get("asset_class", ""),
            "broker": phase_info.get("broker", ""),
            "phase_since": phase_info.get("phase_since", ""),
            "trades_sample": trades,
            "trades_count": len(trades),
            # Registre complet
            "description": registry.get("description", ""),
            "why_it_works": registry.get("why_it_works", ""),
            "edge_type": registry.get("edge_type", ""),
            "parameters": registry.get("parameters", {}),
            "tickers": registry.get("tickers", []),
            "backtest": registry.get("backtest", {}),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Crypto Strategies ────────────────────────────────────────────────────────

@app.get("/api/crypto/strategies")
def get_crypto_strategies():
    """Liste des 8 strategies crypto Binance avec config, wallet et statut."""
    try:
        # Charger la config allocation pour obtenir le capital total
        import yaml
        from strategies.crypto import CRYPTO_STRATEGIES
        alloc_path = ROOT / "config" / "crypto_allocation.yaml"
        crypto_config = {}
        if alloc_path.exists():
            try:
                crypto_config = yaml.safe_load(
                    alloc_path.read_text(encoding="utf-8")
                ).get("crypto_allocation", {})
            except Exception:
                pass

        wallets = crypto_config.get("wallets", {})

        # Equity Binance LIVE (remplace le YAML statique)
        total_capital = crypto_config.get("total_capital", 10_000)
        binance_info = {}
        try:
            if os.environ.get("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                bnb = BinanceBroker()
                binance_info = bnb.get_account_info()
                total_capital = binance_info.get("equity", total_capital)
        except Exception as e:
            logger.debug(f"Binance account info indisponible: {e}")

        result = []
        for strat_id, strat_data in CRYPTO_STRATEGIES.items():
            config = strat_data["config"]
            market_type = config.get("market_type", "spot")

            # Mapping market_type -> wallet
            wallet_map = {"spot": "spot", "margin": "margin", "earn": "earn"}
            wallet = wallet_map.get(market_type, "spot")

            alloc_pct = config.get("allocation_pct", 0)
            capital_allocated = round(total_capital * alloc_pct, 2)

            result.append({
                "id": strat_id,
                "name": config.get("name", strat_id),
                "status": "LIVE",
                "wallet": wallet,
                "market_type": market_type,
                "allocation_pct": round(alloc_pct * 100, 1),
                "capital_allocated": capital_allocated,
                "symbols": config.get("symbols", []),
                "timeframe": config.get("timeframe", "4h"),
                "frequency": config.get("frequency", "4h"),
                "max_leverage": config.get("max_leverage", 1),
                "kelly_fraction": 0.125,  # SOFT_LAUNCH 1/8 Kelly
            })

        # Ajouter les infos de balance Binance si disponibles
        balance_info = {}
        if binance_info:
            balance_info = {
                "equity": binance_info.get("equity", 0),
                "cash_usdt": binance_info.get("spot_usdt", 0),
                "spot_total_usd": binance_info.get("spot_total_usd", 0),
                "margin_level": binance_info.get("margin_level", 0),
            }

        total_alloc_pct = sum(s["allocation_pct"] for s in result)
        return {
            "strategies": result,
            "count": len(result),
            "total_capital": total_capital,
            "total_allocation_pct": round(total_alloc_pct, 1),
            "wallets": wallets,
            "binance_balance": balance_info,
            "phase": "SOFT_LAUNCH",
            "kelly_fraction": 0.125,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"Crypto strategies error: {e}")
        return {"error": str(e), "strategies": [], "count": 0}


# ── Allocation ───────────────────────────────────────────────────────────────

@app.get("/api/allocation")
def get_allocation():
    """Allocation actuelle avec regime."""
    try:
        from scripts.paper_portfolio import STRATEGIES, compute_allocations, get_market_regime
        regime = get_market_regime()
        client = _get_alpaca_client()
        account = client.get_account_info()
        equity = account["equity"]

        allocs = compute_allocations(STRATEGIES, equity)

        tiers = {"S": [], "A": [], "B": [], "C": []}
        strategies, tier_alloc = _get_strategies_config()
        for sid, a in allocs.items():
            tier = _tier_for_strategy(sid, tier_alloc)
            tiers[tier].append({
                "id": sid,
                "name": strategies[sid]["name"],
                "pct": a["pct"],
                "capital": a["capital"],
            })

        return {
            "allocations": {sid: {"pct": a["pct"], "capital": a["capital"]} for sid, a in allocs.items()},
            "tiers": tiers,
            "regime": regime,
            "total_capital": round(equity, 2),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Alerts ───────────────────────────────────────────────────────────────────

@app.get("/api/alerts")
def get_alerts(limit: int = 20):
    """Alertes recentes."""
    # For now, return from state file
    state = _load_state()
    alerts = state.get("alerts", [])
    return {"alerts": alerts[-limit:], "count": len(alerts)}


# ── Regime ───────────────────────────────────────────────────────────────────

@app.get("/api/regime")
def get_regime():
    """Regime de marche actuel."""
    try:
        from scripts.paper_portfolio import get_market_regime
        return get_market_regime()
    except Exception as e:
        return {"error": str(e), "regime": "UNKNOWN"}


# ── Trades ───────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def get_trades(
    strategy: str | None = None,
    limit: int = 50,
    source: str = "real",
    mode: str | None = None,
):
    """Historique des trades.

    source: "real" (Alpaca API + journals), "backtest" (simulations CSV), "all"
    mode: "live", "paper", None (all)
    """
    try:
        from routes_v2 import _load_all_trades
        all_trades = _load_all_trades(source=source)
    except ImportError:
        all_trades = []

    # Add futures trades from events.jsonl
    try:
        events_path = ROOT / "logs" / "events.jsonl"
        if events_path.exists():
            with open(events_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line.strip())
                        if ev.get("action") != "futures_trade":
                            continue
                        d = ev.get("details", {})
                        all_trades.append({
                            "date": ev.get("timestamp", "")[:10],
                            "timestamp": ev.get("timestamp", ""),
                            "strategy": ev.get("strategy", ""),
                            "symbol": d.get("symbol", ""),
                            "side": d.get("side", ""),
                            "entry_price": d.get("fill_price", 0),
                            "qty": d.get("qty", 1),
                            "broker": "IBKR",
                            "asset_class": "futures",
                            "trade_source": d.get("mode", "live").lower(),
                        })
                    except Exception:
                        continue
    except Exception:
        pass

    # Filtre par mode live/paper
    if mode == "live":
        all_trades = [t for t in all_trades if t.get("trade_source") == "live"]
    elif mode == "paper":
        all_trades = [t for t in all_trades if t.get("trade_source") in ("paper", "")]

    if strategy:
        all_trades = [
            t for t in all_trades
            if strategy.lower() in str(
                t.get("strategy", t.get("symbol", t.get("source", "")))
            ).lower()
        ]

    return {"trades": all_trades[:limit], "count": len(all_trades), "source": source, "mode": mode}


# ── System Health ────────────────────────────────────────────────────────────

@app.get("/api/system/health")
def get_system_health():
    """Etat du systeme — 3 brokers."""
    # Alpaca
    alpaca_ok = False
    alpaca_equity = 0
    try:
        client = _get_alpaca_client()
        acct = client.get_account_info()
        alpaca_ok = True
        alpaca_equity = acct.get("equity", 0)
    except Exception:
        pass

    # IBKR (TCP port check + snapshot — no direct ib_insync to avoid client_id conflict with worker)
    ibkr_ok = _check_ibkr_port()
    ibkr_equity = _get_ibkr_equity_from_snapshot()

    # Binance
    binance_ok = False
    binance_equity = 0
    try:
        if os.environ.get("BINANCE_API_KEY"):
            from core.broker.binance_broker import BinanceBroker
            bnb = BinanceBroker()
            bnb_info = bnb.get_account_info()
            binance_ok = True
            binance_equity = bnb_info.get("equity", 0)
    except Exception:
        pass

    # Worker health
    worker_ok = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as resp:
            worker_ok = resp.status == 200
    except Exception:
        pass

    cache_dir = ROOT / "archive" / "intraday-backtesterV2" / "data_cache"
    cache_files = list(cache_dir.glob("*.parquet")) if cache_dir.exists() else []
    cache_size_mb = sum(f.stat().st_size for f in cache_files) / (1024 * 1024)

    return {
        "alpaca_connected": alpaca_ok,
        "ibkr_connected": ibkr_ok,
        "binance_connected": binance_ok,
        "worker_running": worker_ok,
        "alpaca_equity": round(alpaca_equity, 2),
        "ibkr_equity": round(ibkr_equity, 2),
        "binance_equity": round(binance_equity, 2),
        "total_equity": round(alpaca_equity + ibkr_equity + binance_equity, 2),
        "cache_files": len(cache_files),
        "cache_size_mb": round(cache_size_mb, 1),
        "strategies_count": _get_total_strategies_count(),
        "cro_score": 9.5,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── WebSocket (live updates) ────────────────────────────────────────────────

connected_clients: list[WebSocket] = []


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    # Auth: require valid JWT token as query param ?token=...
    token = websocket.query_params.get("token", "")
    if token:
        from auth import _verify_token
        if _verify_token(token) is None:
            await websocket.close(code=4001, reason="Invalid token")
            return
    else:
        await websocket.close(code=4001, reason="Missing token")
        return
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            # Keep connection alive, send heartbeat every 30s
            import asyncio
            await asyncio.sleep(30)
            try:
                portfolio = get_portfolio()
                await websocket.send_json({"type": "heartbeat", "data": portfolio})
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


# ── Walk-Forward Results ─────────────────────────────────────────────────────

def _load_walk_forward() -> dict:
    """Charge les resultats walk-forward depuis output/walk_forward_results.json."""
    wf_path = ROOT / "output" / "walk_forward_results.json"
    if not wf_path.exists():
        return {}
    try:
        return json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load walk-forward results: {e}")
        return {}


@app.get("/api/walk-forward")
def get_walk_forward():
    """Resultats walk-forward par strategie."""
    wf = _load_walk_forward()
    if not wf:
        return {"error": "Walk-forward results not found", "strategies": []}

    results = []
    for name, r in wf.get("results", {}).items():
        results.append({
            "strategy": name,
            "verdict": r.get("verdict", "UNKNOWN"),
            "n_trades": r.get("n_trades", 0),
            "n_windows": r.get("n_windows", 0),
            "avg_oos_sharpe": round(r.get("avg_oos_sharpe", 0), 2),
            "avg_is_sharpe": round(r.get("avg_is_sharpe", 0), 2),
            "avg_ratio": round(r.get("avg_ratio", 0), 2),
            "pct_oos_profitable": round(r.get("pct_oos_profitable", 0) * 100, 1),
            "pct_oos_sharpe_positive": round(r.get("pct_oos_sharpe_positive", 0) * 100, 1),
            "reason": r.get("reason", ""),
            "windows": r.get("windows", []),
        })

    # Trier par verdict (VALIDATED > BORDERLINE > REJECTED) puis par Sharpe OOS
    verdict_order = {"VALIDATED": 0, "BORDERLINE": 1, "REJECTED": 2}
    results.sort(key=lambda x: (verdict_order.get(x["verdict"], 3), -x["avg_oos_sharpe"]))

    meta = wf.get("meta", {})
    return {
        "strategies": results,
        "meta": {
            "timestamp": meta.get("timestamp", ""),
            "n_total": meta.get("n_strategies_total", 0),
            "n_validated": meta.get("n_strategies_validated", 0),
            "n_borderline": meta.get("n_strategies_borderline", 0),
            "n_rejected": meta.get("n_strategies_rejected", 0),
            "parameters": meta.get("parameters", {}),
        },
    }


@app.get("/api/confidence")
def get_confidence():
    """Metriques de confiance par strategie : trades, verdict WF, alpha decay trend."""
    wf = _load_walk_forward()
    if not wf:
        return {"error": "Walk-forward results not found", "strategies": []}

    results = []
    for name, r in wf.get("results", {}).items():
        windows = r.get("windows", [])
        verdict = r.get("verdict", "UNKNOWN")

        # Alpha decay trend : comparer OOS Sharpe des dernieres fenetres
        alpha_decay = "stable"
        if len(windows) >= 3:
            oos_sharpes = [w.get("oos_sharpe", 0) for w in windows]
            first_half = np.mean(oos_sharpes[:len(oos_sharpes) // 2]) if oos_sharpes else 0
            second_half = np.mean(oos_sharpes[len(oos_sharpes) // 2:]) if oos_sharpes else 0

            if second_half < first_half * 0.5:
                alpha_decay = "declining"
            elif second_half > first_half * 1.3:
                alpha_decay = "improving"
            else:
                alpha_decay = "stable"

        # Confidence score (0-100)
        confidence = 0
        if verdict == "VALIDATED":
            confidence = 70
        elif verdict == "BORDERLINE":
            confidence = 40
        else:
            confidence = 10

        # Boost confidence based on trade count
        n_trades = r.get("n_trades", 0)
        if n_trades >= 100:
            confidence += 15
        elif n_trades >= 50:
            confidence += 10
        elif n_trades >= 25:
            confidence += 5

        # Boost for high OOS Sharpe
        avg_oos = r.get("avg_oos_sharpe", 0)
        if avg_oos > 3:
            confidence += 15
        elif avg_oos > 1.5:
            confidence += 10
        elif avg_oos > 0:
            confidence += 5

        confidence = min(confidence, 100)

        results.append({
            "strategy": name,
            "n_trades": n_trades,
            "verdict": verdict,
            "alpha_decay": alpha_decay,
            "confidence_score": confidence,
            "avg_oos_sharpe": round(r.get("avg_oos_sharpe", 0), 2),
            "pct_oos_profitable": round(r.get("pct_oos_profitable", 0) * 100, 1),
            "last_window_oos_sharpe": round(windows[-1]["oos_sharpe"], 2) if windows else None,
            "last_window_oos_pnl": round(windows[-1]["oos_pnl"], 2) if windows else None,
        })

    results.sort(key=lambda x: -x["confidence_score"])

    return {
        "strategies": results,
        "summary": {
            "avg_confidence": round(np.mean([r["confidence_score"] for r in results]), 1) if results else 0,
            "n_high_confidence": sum(1 for r in results if r["confidence_score"] >= 70),
            "n_medium_confidence": sum(1 for r in results if 40 <= r["confidence_score"] < 70),
            "n_low_confidence": sum(1 for r in results if r["confidence_score"] < 40),
            "n_declining_alpha": sum(1 for r in results if r["alpha_decay"] == "declining"),
        },
    }


# ── Multi-Market (DASH-002) ──────────────────────────────────────────────────

def _load_eu_state() -> dict:
    state_file = ROOT / "paper_portfolio_eu_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {}


def _load_eu_strategies() -> dict:
    import yaml
    config_path = ROOT / "config" / "strategies_eu.yaml"
    if config_path.exists():
        try:
            return yaml.safe_load(config_path.read_text(encoding="utf-8")).get("strategies", {})
        except Exception:
            pass
    return {}


@app.get("/api/markets")
def get_markets_overview():
    """Vue multi-marche : P&L par marche, allocation, heures actives."""
    from datetime import datetime

    import pytz

    now_cet = datetime.now(pytz.timezone("Europe/Paris"))
    hour_cet = now_cet.hour

    # US state
    us_state = _load_state()
    us_pnl = us_state.get("daily_pnl", 0)

    # EU state
    eu_state = _load_eu_state()
    eu_pnl = eu_state.get("daily_pnl", 0)

    # Determine active markets
    markets = {
        "us": {
            "name": "US Equities (Alpaca)",
            "broker": "alpaca",
            "active": 15 <= hour_cet < 22,
            "hours": "15:30-22:00 CET",
            "pnl_today": round(us_pnl, 2),
            "strategies_count": len(us_state.get("allocations", {})),
            "positions_count": len(us_state.get("intraday_positions", {})),
            "allocation_target_pct": 40,
        },
        "eu": {
            "name": "EU Equities (IBKR)",
            "broker": "ibkr",
            "active": 9 <= hour_cet < 18,
            "hours": "09:00-17:30 CET",
            "pnl_today": round(eu_pnl, 2),
            "strategies_count": len(_load_eu_strategies()),
            "positions_count": len(eu_state.get("intraday_positions", {})),
            "allocation_target_pct": 25,
        },
        "fx": {
            "name": "FX (IBKR)",
            "broker": "ibkr",
            "active": True,  # FX trades ~22h/day
            "hours": "00:00-22:00 CET (sauf rollover)",
            "pnl_today": 0,
            "strategies_count": 7,
            "positions_count": 0,
            "allocation_target_pct": 18,
        },
        "futures": {
            "name": "Futures Micro (IBKR)",
            "broker": "ibkr",
            "active": hour_cet >= 1 or hour_cet <= 23,
            "hours": "01:00-23:00 CET (quasi 24h)",
            "pnl_today": 0,
            "strategies_count": 4,
            "positions_count": 0,
            "allocation_target_pct": 10,
        },
        "crypto": {
            "name": "Crypto (Binance France)",
            "broker": "binance",
            "active": True,  # Crypto 24/7
            "hours": "24/7",
            "pnl_today": 0,
            "strategies_count": 8,
            "positions_count": 0,
            "allocation_target_pct": 7,
        },
    }

    total_pnl = sum(m["pnl_today"] for m in markets.values())
    active_count = sum(1 for m in markets.values() if m["active"])

    return {
        "markets": markets,
        "total_pnl_today": round(total_pnl, 2),
        "active_markets": active_count,
        "current_hour_cet": hour_cet,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/api/markets/heatmap")
def get_capital_heatmap():
    """Heatmap 24h : capital actif par creneau horaire (CET)."""
    heatmap = []
    strategies_eu = _load_eu_strategies()

    for hour in range(24):
        active_capital_pct = 0
        active_strategies = []

        # FX (quasi 24h sauf 22-23 rollover)
        if hour < 22 or hour >= 23:
            active_capital_pct += 15
            active_strategies.extend(["EUR/USD", "EUR/GBP", "EUR/JPY", "AUD/JPY", "GBP/USD", "USD/CHF", "NZD/USD"])

        # Futures (quasi 24h sauf 23-01)
        if 1 <= hour <= 23:
            active_capital_pct += 8
            active_strategies.extend(["MES Trend", "MNQ MR", "MCL Brent"])

        # EU (09:00-17:30)
        if 9 <= hour < 18:
            active_capital_pct += 20
            for sid, s in strategies_eu.items():
                start_h = int(s.get("market_hours", {}).get("start", "09:00").split(":")[0])
                end_h = int(s.get("market_hours", {}).get("end", "17:30").split(":")[0])
                if start_h <= hour < end_h:
                    active_strategies.append(s.get("name", sid))

        # US (15:30-22:00 CET)
        if 15 <= hour < 22:
            active_capital_pct += 35
            active_strategies.extend(["US Intraday", "US Shorts", "FOMC (si event)"])

        # Crypto (24/7)
        active_capital_pct += 7
        active_strategies.extend(["BTC/ETH Dual Momentum", "BTC Mean Reversion", "Borrow Rate Carry"])

        heatmap.append({
            "hour_cet": f"{hour:02d}:00",
            "capital_active_pct": min(active_capital_pct, 90),
            "strategies_active": list(set(active_strategies)),
            "strategies_count": len(set(active_strategies)),
        })

    total_active_hours = sum(1 for h in heatmap if h["capital_active_pct"] > 10)
    avg_utilization = round(sum(h["capital_active_pct"] for h in heatmap) / 24, 1)

    return {
        "heatmap": heatmap,
        "summary": {
            "hours_active": total_active_hours,
            "avg_utilization_pct": avg_utilization,
            "peak_hour": max(heatmap, key=lambda h: h["capital_active_pct"])["hour_cet"],
            "dead_zones": [h["hour_cet"] for h in heatmap if h["capital_active_pct"] < 10],
        },
    }


@app.get("/api/markets/correlation")
def get_cross_asset_correlation():
    """Matrice de correlation entre classes d'actifs (estimee)."""
    # Correlations estimees basees sur donnees historiques
    # En conditions normales vs en crise
    normal = {
        "us_equity": {"us_equity": 1.0, "eu_equity": 0.65, "fx": 0.15, "futures_index": 0.90, "futures_energy": 0.30, "gold": -0.15},
        "eu_equity": {"us_equity": 0.65, "eu_equity": 1.0, "fx": 0.25, "futures_index": 0.60, "futures_energy": 0.35, "gold": -0.10},
        "fx":        {"us_equity": 0.15, "eu_equity": 0.25, "fx": 1.0, "futures_index": 0.10, "futures_energy": 0.20, "gold": 0.30},
        "futures_index": {"us_equity": 0.90, "eu_equity": 0.60, "fx": 0.10, "futures_index": 1.0, "futures_energy": 0.25, "gold": -0.20},
        "futures_energy": {"us_equity": 0.30, "eu_equity": 0.35, "fx": 0.20, "futures_index": 0.25, "futures_energy": 1.0, "gold": 0.15},
        "gold":      {"us_equity": -0.15, "eu_equity": -0.10, "fx": 0.30, "futures_index": -0.20, "futures_energy": 0.15, "gold": 1.0},
    }

    crisis = {
        "us_equity": {"us_equity": 1.0, "eu_equity": 0.90, "fx": 0.40, "futures_index": 0.95, "futures_energy": 0.70, "gold": -0.30},
        "eu_equity": {"us_equity": 0.90, "eu_equity": 1.0, "fx": 0.45, "futures_index": 0.85, "futures_energy": 0.65, "gold": -0.25},
        "fx":        {"us_equity": 0.40, "eu_equity": 0.45, "fx": 1.0, "futures_index": 0.35, "futures_energy": 0.30, "gold": 0.50},
        "futures_index": {"us_equity": 0.95, "eu_equity": 0.85, "fx": 0.35, "futures_index": 1.0, "futures_energy": 0.60, "gold": -0.35},
        "futures_energy": {"us_equity": 0.70, "eu_equity": 0.65, "fx": 0.30, "futures_index": 0.60, "futures_energy": 1.0, "gold": 0.10},
        "gold":      {"us_equity": -0.30, "eu_equity": -0.25, "fx": 0.50, "futures_index": -0.35, "futures_energy": 0.10, "gold": 1.0},
    }

    return {
        "normal": normal,
        "crisis": crisis,
        "note": "Correlations estimees — normales vs mars 2020 stress",
    }


@app.get("/api/markets/var")
def get_portfolio_var():
    """VaR portfolio multi-asset avec contribution par classe."""
    allocation = {"us_equity": 0.40, "eu_equity": 0.25, "fx": 0.18, "futures": 0.10, "cash": 0.07}
    vol_annual = {"us_equity": 0.18, "eu_equity": 0.20, "fx": 0.08, "futures": 0.22, "cash": 0.0}

    # VaR parametrique 99% daily
    var_by_class = {}
    total_var = 0
    for ac, alloc in allocation.items():
        vol = vol_annual.get(ac, 0)
        daily_vol = vol / (252 ** 0.5)
        var_99 = alloc * daily_vol * 2.33  # 99% z-score
        var_by_class[ac] = round(var_99 * 100, 2)  # en %
        total_var += var_99 ** 2

    portfolio_var = round((total_var ** 0.5) * 100, 2)  # diversification benefit

    return {
        "var_99_daily_pct": portfolio_var,
        "var_by_class": var_by_class,
        "diversification_benefit_pct": round(sum(var_by_class.values()) - portfolio_var, 2),
        "allocation": allocation,
    }


# ── Routes V2 (risk, analytics, tax, cross-portfolio, system) ────────────────

try:
    from routes_v2 import router as v2_router
    app.include_router(v2_router)
    logger.info("Routes V2 loaded successfully")
except Exception as e:
    logger.error(f"Failed to load routes_v2: {e}")


# ── Startup ──────────────────────────────────────────────────────────────────

# ── Health (public, no auth) ─────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


# ── Static SPA serving ───────────────────────────────────────────────────────
# Must be LAST — catches all non-API routes and serves the React frontend.

DIST_DIR = ROOT / "dashboard" / "frontend" / "dist"
if DIST_DIR.exists():
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA — all non-API routes return index.html."""
        file_path = DIST_DIR / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(DIST_DIR / "index.html")
else:
    logger.warning(f"Frontend dist not found at {DIST_DIR} — run 'npm run build'")


# ── Startup ──────────────────────────────────────────────────────────────────

# ── Events / Futures trades ─────────────────────────────────────────────────

@app.get("/api/events")
def get_events(limit: int = 100, action: str | None = None):
    """Recent events from worker (trades, signals, kill switch, etc)."""
    events_path = ROOT / "logs" / "events.jsonl"
    if not events_path.exists():
        return {"events": [], "count": 0}
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        events = []
        for line in reversed(lines[-500:]):
            try:
                ev = json.loads(line.strip())
                if action and ev.get("action") != action:
                    continue
                events.append(ev)
                if len(events) >= limit:
                    break
            except Exception:
                continue
        return {"events": events, "count": len(events)}
    except Exception as e:
        return {"error": str(e), "events": [], "count": 0}


@app.get("/api/futures/trades")
def get_futures_trades(limit: int = 50):
    """Futures trades from events log."""
    events_path = ROOT / "logs" / "events.jsonl"
    if not events_path.exists():
        return {"trades": [], "count": 0}
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        trades = []
        for line in reversed(lines):
            try:
                ev = json.loads(line.strip())
                if ev.get("action") != "futures_trade":
                    continue
                d = ev.get("details", {})
                trades.append({
                    "timestamp": ev.get("timestamp", ""),
                    "strategy": ev.get("strategy", ""),
                    "symbol": d.get("symbol", ""),
                    "side": d.get("side", ""),
                    "qty": d.get("qty", 1),
                    "fill_price": d.get("fill_price", 0),
                    "sl": d.get("sl", 0),
                    "tp": d.get("tp", 0),
                    "mode": d.get("mode", ""),
                    "equity": d.get("equity", 0),
                })
                if len(trades) >= limit:
                    break
            except Exception:
                continue
        return {"trades": trades, "count": len(trades)}
    except Exception as e:
        return {"error": str(e), "trades": [], "count": 0}


@app.get("/api/futures/positions")
def get_futures_positions():
    """Current futures positions from state files."""
    result = {"live": {}, "paper": {}}
    for suffix in ("live", "paper"):
        fp = ROOT / "data" / "state" / f"futures_positions_{suffix}.json"
        if fp.exists():
            try:
                result[suffix] = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return result


@app.get("/api/equity-history")
def get_equity_history(days: int = 7):
    """Equity curve from portfolio snapshots."""
    snap_dir = ROOT / "logs" / "portfolio"
    if not snap_dir.exists():
        return {"data": [], "count": 0}
    import glob
    files = sorted(glob.glob(str(snap_dir / "*.jsonl")))[-days:]
    data = []
    for fpath in files:
        try:
            with open(fpath) as f:
                for line in f:
                    try:
                        snap = json.loads(line.strip())
                        data.append({
                            "timestamp": snap.get("timestamp", ""),
                            "total_equity": snap.get("portfolio", {}).get("total_equity", 0),
                            "daily_pnl_pct": snap.get("portfolio", {}).get("daily_pnl_pct", 0),
                            "brokers": snap.get("portfolio", {}).get("brokers", []),
                        })
                    except Exception:
                        continue
        except Exception:
            continue
    return {"data": data, "count": len(data)}


@app.get("/api/books/status")
def api_books_status():
    """Return per-book health status (GREEN/DEGRADED/BLOCKED/UNKNOWN).

    P1.2 live hardening — books are independent. A DEGRADED crypto book
    does NOT imply DEGRADED futures book. Use per-book status for decisions.
    """
    try:
        from core.governance import get_all_books_health, get_global_status
        books = get_all_books_health(use_cache=True)
        return {
            "global": get_global_status(use_cache=True).value,
            "books": {name: h.to_dict() for name, h in books.items()},
        }
    except Exception as e:
        logger.error(f"/api/books/status error: {e}")
        return {"error": str(e), "global": "UNKNOWN", "books": {}}


@app.get("/api/governance/live-whitelist")
def api_live_whitelist():
    """Return the canonical live whitelist (read-only).

    P1.1 live hardening — single source of truth for what's allowed in LIVE.
    """
    try:
        from core.governance import load_live_whitelist
        return load_live_whitelist()
    except Exception as e:
        logger.error(f"/api/governance/live-whitelist error: {e}")
        return {"error": str(e)}


@app.on_event("startup")
async def startup():
    logger.info("Trading Dashboard API v2.0 — Auth + Multi-Broker + SPA")
    logger.info(f"Root: {ROOT}")
    logger.info(f"Frontend dist: {'FOUND' if DIST_DIR.exists() else 'MISSING'}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
