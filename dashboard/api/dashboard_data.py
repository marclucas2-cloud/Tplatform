"""Canonical data helpers for the dashboard API.

This module keeps the dashboard aligned with the desk's source-of-truth files:
``config/quant_registry.yaml`` for strategies, broker/state snapshots for
portfolio data, and persisted guard state for kill-switch visibility.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


logger = logging.getLogger("dashboard-data")

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
STATE_DIR = DATA_DIR / "state"
CONFIG_DIR = ROOT / "config"
LOG_DIR = ROOT / "logs"

BOOK_TO_BROKER = {
    "alpaca_us": "ALPACA",
    "binance_crypto": "BINANCE",
    "ibkr_eu": "IBKR",
    "ibkr_futures": "IBKR",
    "ibkr_fx": "IBKR",
}

BOOK_TO_ASSET = {
    "alpaca_us": "US",
    "binance_crypto": "CRYPTO",
    "ibkr_eu": "EU",
    "ibkr_futures": "FUTURES",
    "ibkr_fx": "FX",
}

STATUS_TO_PHASE = {
    "disabled": "DISABLED",
    "frozen": "FROZEN",
    "keep_research": "RESEARCH",
    "live_core": "LIVE",
    "live_micro": "LIVE_MICRO",
    "live_probation": "PROBATION",
    "paper_only": "PAPER",
    "paper_retrospective": "PAPER",
}

GRADE_NOTIONAL_PCT = {
    "S": 2.0,
    "A": 1.2,
    "B": 0.8,
}

DISPLAY_STRATEGY_ID = {
    "Cross-Asset Mom": "cross_asset_momentum",
    "Cross Asset Mom": "cross_asset_momentum",
    "Gold-Oil Rotation": "gold_oil_rotation",
    "Gold Oil Rotation": "gold_oil_rotation",
    "Gold Trend MGC": "gold_trend_mgc",
    "btc_asia_mes_leadlag_q80_long_only": "btc_asia_mes_leadlag_q80_v80_long_only",
    "btc_asia_mes_leadlag_q80_live_micro": "btc_asia_mes_leadlag_q80_v80_long_only",
    "btc_asia_q80_live_micro": "btc_asia_mes_leadlag_q80_v80_long_only",
    "alt_rel_strength": "alt_rel_strength_14_60_7",
    "us_sector_ls": "us_sector_ls_40_5",
    "eu_relmom": "eu_relmom_40_3",
    "mib_estx50": "mib_estx50_spread",
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to load json %s: %s", path, exc)
    return default


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        return {}
    try:
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("Failed to load yaml %s: %s", path, exc)
    return {}


def load_quant_registry() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "quant_registry.yaml")


def quant_strategy_entries() -> list[dict[str, Any]]:
    registry = load_quant_registry()
    strategies = registry.get("strategies", [])
    if isinstance(strategies, list):
        return [s for s in strategies if isinstance(s, dict) and s.get("strategy_id")]
    return []


def canonical_strategy_id(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw in DISPLAY_STRATEGY_ID:
        return DISPLAY_STRATEGY_ID[raw]
    canonical_ids = {str(s.get("strategy_id")) for s in quant_strategy_entries()}
    if raw in canonical_ids:
        return raw
    lowered = raw.lower().strip()
    if lowered in DISPLAY_STRATEGY_ID:
        return DISPLAY_STRATEGY_ID[lowered]
    normalized = lowered.replace("-", "_").replace(" ", "_")
    normalized = "".join(ch for ch in normalized if ch.isalnum() or ch == "_")
    if normalized in DISPLAY_STRATEGY_ID:
        return DISPLAY_STRATEGY_ID[normalized]
    if normalized in canonical_ids:
        return normalized
    if normalized.endswith("_live_micro") and normalized[:-11] in DISPLAY_STRATEGY_ID:
        return DISPLAY_STRATEGY_ID[normalized[:-11]]
    return normalized or None


def _title_strategy(strategy_id: str) -> str:
    return strategy_id.replace("_", " ").title()


def _manifest_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _find_first_numeric(obj: Any, names: tuple[str, ...]) -> float | None:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                value = safe_float(obj.get(name), default=float("nan"))
                if value == value:
                    return value
        for value in obj.values():
            found = _find_first_numeric(value, names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_first_numeric(value, names)
            if found is not None:
                return found
    return None


def manifest_sharpe(path_value: str | None) -> float | None:
    path = _manifest_path(path_value)
    if not path:
        return None
    data = load_json(path, {})
    if not isinstance(data, dict):
        return None
    candidates = [
        ("summary", "sharpe"),
        ("summary", "all_trades", "sharpe"),
        ("summary", "median_sharpe"),
        ("sensitivity_grid", "best_config", "sharpe"),
    ]
    for keys in candidates:
        cur: Any = data
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                cur = None
                break
            cur = cur[key]
        if cur is not None:
            value = safe_float(cur, default=float("nan"))
            if value == value:
                return round(value, 2)
    found = _find_first_numeric(data, ("sharpe", "median_sharpe", "oos_mean_sharpe"))
    return round(found, 2) if found is not None else None


def load_safe003_disabled() -> set[str]:
    state = load_json(STATE_DIR / "safe003_disabled.json", {})
    values = state.get("disabled", []) if isinstance(state, dict) else []
    return {str(v) for v in values}


def load_kill_switch_state() -> dict[str, Any]:
    return load_json(DATA_DIR / "kill_switch_state.json", {})


def load_live_risk_dd_state() -> dict[str, Any]:
    return load_json(DATA_DIR / "live_risk_dd_state.json", {})


def load_crypto_dd_state() -> dict[str, Any]:
    return load_json(DATA_DIR / "crypto_dd_state.json", {})


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _latest_portfolio_snapshot(broker: str | None = None) -> dict[str, Any]:
    snap_dir = LOG_DIR / "portfolio"
    if not snap_dir.exists():
        return {}
    for fpath in reversed(sorted(glob.glob(str(snap_dir / "*.jsonl")))):
        try:
            lines = Path(fpath).read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            try:
                snap = json.loads(line)
            except Exception:
                continue
            if broker is None:
                return snap
            brokers = snap.get("portfolio", {}).get("brokers", [])
            for item in brokers:
                if str(item.get("broker", "")).lower() == broker.lower():
                    item = dict(item)
                    item.setdefault("timestamp", snap.get("timestamp"))
                    return item
    return {}


def _state_snapshot_candidates(name: str) -> list[Path]:
    return [
        STATE_DIR / name / "equity_state.json",
        STATE_DIR / f"{name}_equity.json",
        DATA_DIR / f"{name}_equity.json",
        DATA_DIR / f"{name}_equity_state.json",
    ]


def load_ibkr_account_snapshot() -> dict[str, Any]:
    for path in [
        STATE_DIR / "ibkr_futures" / "equity_state.json",
        STATE_DIR / "ibkr_equity.json",
        DATA_DIR / "ibkr_equity.json",
        DATA_DIR / "live_risk_snapshot.json",
    ]:
        state = load_json(path, {})
        if isinstance(state, dict):
            equity = safe_float(
                state.get("equity", state.get("net_liquidation", state.get("net_liquidation_usd"))),
                0.0,
            )
            if equity > 0:
                return {
                    "equity": equity,
                    "cash": safe_float(
                        state.get("cash", state.get("available_funds", state.get("cash_usd"))),
                        equity,
                    ),
                    "buying_power": safe_float(state.get("buying_power", state.get("available_funds")), 0.0),
                    "source": str(path.relative_to(ROOT)),
                    "timestamp": state.get("timestamp") or state.get("updated_at"),
                }

    snap = _latest_portfolio_snapshot("ibkr")
    equity = safe_float(snap.get("equity", snap.get("net_liquidation")), 0.0)
    if equity <= 0:
        latest = _latest_portfolio_snapshot()
        equity = safe_float(latest.get("ibkr_equity", latest.get("portfolio", {}).get("ibkr_equity")), 0.0)
        snap = latest
    if equity > 0:
        return {
            "equity": equity,
            "cash": safe_float(snap.get("cash", snap.get("available_funds")), equity),
            "buying_power": safe_float(snap.get("buying_power", snap.get("available_funds")), 0.0),
            "source": "logs/portfolio",
            "timestamp": snap.get("timestamp"),
        }
    return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0, "source": "missing", "timestamp": None}


def load_binance_account_snapshot() -> dict[str, Any]:
    if os.environ.get("BINANCE_API_KEY") and os.environ.get("BINANCE_LIVE_CONFIRMED", "").lower() == "true":
        try:
            from core.broker.binance_broker import BinanceBroker

            info = BinanceBroker().get_account_info()
            equity = safe_float(info.get("equity", info.get("total_equity")), 0.0)
            if equity > 0:
                return {
                    "equity": equity,
                    "cash": safe_float(
                        info.get("cash", info.get("spot_usdt", info.get("available_usdt"))),
                        0.0,
                    ),
                    "source": "binance_api",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "raw": info,
                }
        except Exception as exc:
            logger.debug("Binance live account unavailable: %s", exc)

    for path in _state_snapshot_candidates("binance_crypto"):
        state = load_json(path, {})
        if isinstance(state, dict):
            equity = safe_float(state.get("equity", state.get("total_equity")), 0.0)
            if equity > 0:
                return {
                    "equity": equity,
                    "cash": safe_float(state.get("cash", state.get("spot_usdt")), 0.0),
                    "source": str(path.relative_to(ROOT)),
                    "timestamp": state.get("timestamp") or state.get("updated_at"),
                }

    snap = _latest_portfolio_snapshot("binance")
    equity = safe_float(snap.get("equity"), 0.0)
    if equity > 0:
        return {
            "equity": equity,
            "cash": safe_float(snap.get("cash", snap.get("spot_usdt")), 0.0),
            "source": "logs/portfolio",
            "timestamp": snap.get("timestamp"),
        }
    return {"equity": 0.0, "cash": 0.0, "source": "missing", "timestamp": None}


def get_alpaca_account() -> dict[str, Any]:
    try:
        from core.alpaca_client.client import AlpacaClient

        client = AlpacaClient.from_env()
        account = client.get_account_info()
        positions = client.get_positions()
        is_paper = os.environ.get("PAPER_TRADING", "true").lower() == "true"
        return {
            "equity": safe_float(account.get("equity"), 0.0),
            "cash": safe_float(account.get("cash"), 0.0),
            "positions": positions,
            "is_paper": is_paper,
            "source": "alpaca_api",
        }
    except Exception as exc:
        logger.debug("Alpaca account unavailable: %s", exc)
        return {"equity": 0.0, "cash": 0.0, "positions": [], "is_paper": True, "source": "missing"}


def _state_positions(mode: str) -> dict[str, Any]:
    return load_json(STATE_DIR / f"futures_positions_{mode}.json", {})


def _sl_tp_by_local_symbol(port: int, client_id: int) -> dict[str, dict[str, float]]:
    try:
        from ib_insync import IB

        ib = IB()
        ib.connect(
            os.environ.get("IBKR_HOST", "127.0.0.1"),
            port,
            clientId=client_id,
            timeout=4,
            readonly=True,
        )
        mapping: dict[str, dict[str, float]] = {}
        try:
            for tr in ib.openTrades():
                contract = tr.contract
                order = tr.order
                key = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", "")
                if not key:
                    continue
                row = mapping.setdefault(key, {})
                order_type = str(getattr(order, "orderType", "")).upper()
                if "STP" in order_type or order_type == "TRAIL":
                    value = safe_float(
                        getattr(order, "auxPrice", None)
                        or getattr(order, "trailStopPrice", None)
                        or getattr(order, "lmtPrice", None),
                        0.0,
                    )
                    if value:
                        row["stop_loss"] = value
                elif order_type == "LMT":
                    value = safe_float(getattr(order, "lmtPrice", None), 0.0)
                    if value:
                        row["take_profit"] = value
        finally:
            ib.disconnect()
        return mapping
    except Exception as exc:
        logger.debug("IBKR open-trades lookup unavailable on %s: %s", port, exc)
        return {}


def get_ibkr_positions_via_insync(port: int | None = None, mode: str = "live") -> list[dict[str, Any]]:
    port = port or int(os.environ.get("IBKR_PORT", "4002"))
    client_id = int(os.environ.get("DASHBOARD_IBKR_CLIENT_ID", "240")) + random.randint(0, 20)
    try:
        from ib_insync import IB

        ib = IB()
        ib.connect(
            os.environ.get("IBKR_HOST", "127.0.0.1"),
            port,
            clientId=client_id,
            timeout=4,
            readonly=True,
        )
        try:
            bracket_map = _sl_tp_by_local_symbol(port, client_id + 100)
            state = _state_positions(mode)
            rows = []
            for item in ib.portfolio():
                qty = safe_float(getattr(item, "position", 0.0), 0.0)
                if qty == 0:
                    continue
                contract = item.contract
                local_symbol = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", "")
                symbol = getattr(contract, "symbol", local_symbol)
                state_item = state.get(symbol, {}) or state.get(local_symbol, {})
                brackets = bracket_map.get(local_symbol, {}) or bracket_map.get(symbol, {})
                market_value = safe_float(getattr(item, "marketValue", 0.0), 0.0)
                current_price = safe_float(getattr(item, "marketPrice", 0.0), 0.0)
                entry = safe_float(getattr(item, "averageCost", 0.0), 0.0)
                rows.append({
                    "ticker": local_symbol or symbol,
                    "symbol": symbol,
                    "broker": "IBKR",
                    "mode": mode,
                    "_mode": mode,
                    "asset_class": "FUTURES",
                    "direction": "LONG" if qty > 0 else "SHORT",
                    "shares": abs(qty),
                    "qty": qty,
                    "entry_price": entry,
                    "current_price": current_price,
                    "pnl": safe_float(getattr(item, "unrealizedPNL", 0.0), 0.0),
                    "pnl_pct": (safe_float(getattr(item, "unrealizedPNL", 0.0), 0.0) / abs(market_value) * 100) if market_value else 0.0,
                    "market_value": market_value,
                    "strategy": state_item.get("strategy", "broker"),
                    "stop_loss": brackets.get("stop_loss") or state_item.get("sl"),
                    "take_profit": brackets.get("take_profit") or state_item.get("tp"),
                    "source": "ibkr_api",
                })
            return rows
        finally:
            ib.disconnect()
    except Exception as exc:
        logger.debug("IBKR positions unavailable on %s: %s", port, exc)
        rows = []
        state = _state_positions(mode)
        for symbol, item in state.items():
            qty = safe_float(item.get("qty", 0.0), 0.0)
            if qty == 0:
                continue
            entry = safe_float(item.get("entry", item.get("entry_price")), 0.0)
            rows.append({
                "ticker": item.get("contract", symbol),
                "symbol": symbol,
                "broker": "IBKR",
                "mode": mode,
                "_mode": mode,
                "asset_class": "FUTURES",
                "direction": "LONG" if str(item.get("side", "BUY")).upper() in {"BUY", "LONG"} else "SHORT",
                "shares": abs(qty),
                "qty": qty,
                "entry_price": entry,
                "current_price": entry,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "market_value": abs(qty * entry),
                "strategy": item.get("strategy", "state"),
                "stop_loss": item.get("sl"),
                "take_profit": item.get("tp"),
                "source": "state_fallback",
            })
        return rows


def get_binance_positions() -> list[dict[str, Any]]:
    if os.environ.get("BINANCE_API_KEY") and os.environ.get("BINANCE_LIVE_CONFIRMED", "").lower() == "true":
        try:
            from core.broker.binance_broker import BinanceBroker

            positions = BinanceBroker().get_positions()
            rows = []
            for p in positions:
                qty = safe_float(p.get("qty", p.get("quantity")), 0.0)
                market_value = safe_float(p.get("market_val", p.get("market_value")), 0.0)
                rows.append({
                    "ticker": p.get("symbol", ""),
                    "symbol": p.get("symbol", ""),
                    "broker": "BINANCE",
                    "mode": "live",
                    "_mode": "live",
                    "asset_class": "CRYPTO",
                    "direction": "LONG" if qty >= 0 else "SHORT",
                    "shares": abs(qty),
                    "qty": qty,
                    "entry_price": safe_float(p.get("avg_entry", p.get("entry_price")), 0.0),
                    "current_price": safe_float(p.get("current_price"), 0.0),
                    "pnl": safe_float(p.get("unrealized_pl", p.get("pnl")), 0.0),
                    "pnl_pct": safe_float(p.get("unrealized_plpc", p.get("pnl_pct")), 0.0) * 100,
                    "market_value": market_value,
                    "strategy": p.get("strategy", "binance"),
                    "stop_loss": p.get("stop_loss"),
                    "take_profit": p.get("take_profit"),
                    "source": "binance_api",
                })
            return [r for r in rows if r["ticker"]]
        except Exception as exc:
            logger.debug("Binance positions unavailable: %s", exc)
    return []


def get_alpaca_positions() -> list[dict[str, Any]]:
    account = get_alpaca_account()
    intraday = load_json(STATE_DIR / "paper_portfolio_state.json", {}).get("intraday_positions", {})
    rows = []
    for p in account.get("positions", []):
        qty = safe_float(p.get("qty"), 0.0)
        if qty == 0:
            continue
        sym = p.get("symbol", "")
        pos_info = intraday.get(sym, {})
        market_value = safe_float(p.get("market_val"), 0.0)
        current = abs(market_value / qty) if qty else 0.0
        rows.append({
            "ticker": sym,
            "symbol": sym,
            "broker": "ALPACA",
            "mode": "paper" if account.get("is_paper", True) else "live",
            "_mode": "paper" if account.get("is_paper", True) else "live",
            "asset_class": "US",
            "direction": "LONG" if qty > 0 else "SHORT",
            "shares": abs(qty),
            "qty": qty,
            "entry_price": safe_float(p.get("avg_entry"), 0.0),
            "current_price": current,
            "pnl": safe_float(p.get("unrealized_pl"), 0.0),
            "pnl_pct": safe_float(p.get("unrealized_plpc"), 0.0) * 100,
            "market_value": market_value,
            "strategy": pos_info.get("strategy", "daily"),
            "stop_loss": pos_info.get("stop_loss"),
            "take_profit": pos_info.get("take_profit"),
            "source": "alpaca_api",
        })
    return rows


def get_dashboard_positions(include_paper: bool = True) -> dict[str, Any]:
    positions = []
    positions.extend(get_ibkr_positions_via_insync(mode="live"))
    positions.extend(get_binance_positions())
    if include_paper:
        paper_port = int(os.environ.get("IBKR_PAPER_PORT", "4003"))
        positions.extend(get_ibkr_positions_via_insync(port=paper_port, mode="paper"))
        positions.extend(get_alpaca_positions())

    total_long = sum(safe_float(p.get("market_value")) for p in positions if p.get("direction") == "LONG")
    total_short = sum(abs(safe_float(p.get("market_value"))) for p in positions if p.get("direction") == "SHORT")
    live_capital = live_equity_total()["live_equity"]
    paper_capital = get_alpaca_account().get("equity", 0.0) if include_paper else 0.0
    total_capital = live_capital + paper_capital
    if total_capital <= 0:
        total_capital = 1.0

    return {
        "positions": positions,
        "count": len(positions),
        "live_count": len([p for p in positions if p.get("mode") == "live"]),
        "paper_count": len([p for p in positions if p.get("mode") == "paper"]),
        "exposure_long": round(total_long, 2),
        "exposure_short": round(total_short, 2),
        "exposure_net": round(total_long - total_short, 2),
        "exposure_long_pct": round(total_long / total_capital * 100, 1),
        "exposure_short_pct": round(total_short / total_capital * 100, 1),
        "total_capital": round(total_capital, 2),
        "live_capital": round(live_capital, 2),
        "paper_capital": round(paper_capital, 2),
    }


def live_equity_total() -> dict[str, float]:
    ibkr = load_ibkr_account_snapshot()
    binance = load_binance_account_snapshot()
    alpaca = get_alpaca_account()
    alpaca_live = 0.0 if alpaca.get("is_paper", True) else safe_float(alpaca.get("equity"), 0.0)
    return {
        "ibkr_equity": safe_float(ibkr.get("equity"), 0.0),
        "binance_equity": safe_float(binance.get("equity"), 0.0),
        "alpaca_live_equity": alpaca_live,
        "live_equity": safe_float(ibkr.get("equity"), 0.0) + safe_float(binance.get("equity"), 0.0) + alpaca_live,
    }


def get_dashboard_portfolio() -> dict[str, Any]:
    ibkr = load_ibkr_account_snapshot()
    binance = load_binance_account_snapshot()
    alpaca = get_alpaca_account()
    positions = get_dashboard_positions(include_paper=True)
    live_equity = safe_float(ibkr.get("equity")) + safe_float(binance.get("equity"))
    if not alpaca.get("is_paper", True):
        live_equity += safe_float(alpaca.get("equity"))

    live_cash = safe_float(ibkr.get("cash"), safe_float(ibkr.get("equity"))) + safe_float(binance.get("cash"))
    if not alpaca.get("is_paper", True):
        live_cash += safe_float(alpaca.get("cash"))

    dd = live_drawdown_snapshot(live_equity)
    daily_start = dd.get("daily_start") or live_equity
    pnl_day = live_equity - daily_start if daily_start > 0 else 0.0
    return {
        "equity": round(live_equity, 2),
        "cash": round(live_cash, 2),
        "pnl_day": round(pnl_day, 2),
        "pnl_day_pct": round(pnl_day / daily_start * 100, 2) if daily_start > 0 else 0.0,
        "pnl_unrealized": round(sum(safe_float(p.get("pnl")) for p in positions["positions"] if p.get("mode") == "live"), 2),
        "positions_count": positions["live_count"],
        "paper_positions_count": positions["paper_count"],
        "alpaca_equity": round(safe_float(alpaca.get("equity")), 2),
        "alpaca_is_paper": alpaca.get("is_paper", True),
        "ibkr_equity": round(safe_float(ibkr.get("equity")), 2),
        "ibkr_cash": round(safe_float(ibkr.get("cash")), 2),
        "binance_equity": round(safe_float(binance.get("equity")), 2),
        "binance_cash": round(safe_float(binance.get("cash")), 2),
        "live_equity": round(live_equity, 2),
        "paper_equity": round(safe_float(alpaca.get("equity")) if alpaca.get("is_paper", True) else 0.0, 2),
        "initial_capital": round(daily_start, 2),
        "total_return_pct": round((live_equity - daily_start) / daily_start * 100, 2) if daily_start > 0 else 0.0,
        "regime": "UNKNOWN",
        "regime_detail": {"regime": "UNKNOWN"},
        "market_open": False,
        "sources": {
            "ibkr": ibkr.get("source"),
            "binance": binance.get("source"),
            "alpaca": alpaca.get("source"),
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _pnl_timestamp(item: dict[str, Any]) -> datetime | None:
    for key in (
        "exit_time",
        "timestamp_closed",
        "ts_utc",
        "logged_at_utc",
        "timestamp",
        "closed_at",
        "as_of_date",
        "target_date",
        "date",
        "entry_time",
        "timestamp_filled",
        "timestamp_signal",
    ):
        dt = _parse_dt(item.get(key))
        if dt is not None:
            return dt
    return None


def _first_pnl_value(item: dict[str, Any]) -> float | None:
    for key in (
        "net_pnl_usd",
        "realized_pnl_usd",
        "pnl_usd",
        "pnl_net",
        "pnl_gross",
        "realized_pnl",
        "day_pnl_usd",
        "pnl",
    ):
        if key in item and item.get(key) is not None:
            return safe_float(item.get(key), 0.0)
    return None


def _add_strategy_pnl(
    totals: dict[str, dict[str, Any]],
    strategy: Any,
    pnl: float,
    source: str,
) -> None:
    sid = canonical_strategy_id(strategy)
    if not sid:
        return
    row = totals.setdefault(sid, {"pnl": 0.0, "sources": {}})
    row["pnl"] += float(pnl)
    row["sources"][source] = int(row["sources"].get(source, 0)) + 1


def _load_sqlite_strategy_pnl(cutoff: datetime, totals: dict[str, dict[str, Any]]) -> None:
    for db_name in ("live_journal.db", "paper_journal.db"):
        db_path = DATA_DIR / db_name
        if not db_path.exists():
            continue
        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
                if "strategy" not in cols:
                    continue
                select_cols = ", ".join(sorted(cols))
                for row in conn.execute(f"SELECT {select_cols} FROM trades").fetchall():
                    item = dict(row)
                    status = str(item.get("status", "")).lower()
                    if status and status not in {"closed", "filled", "exit"}:
                        continue
                    dt = _pnl_timestamp(item)
                    if dt is not None and dt < cutoff:
                        continue
                    pnl = _first_pnl_value(item)
                    if pnl is None:
                        continue
                    _add_strategy_pnl(totals, item.get("strategy"), pnl, db_name)
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Failed to aggregate strategy PnL from %s: %s", db_path, exc)


def _load_jsonl_strategy_pnl(cutoff: datetime, totals: dict[str, dict[str, Any]]) -> None:
    for path in STATE_DIR.glob("**/*journal*.jsonl"):
        try:
            records = []
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    records.append(item)
            if not records:
                continue

            default_sid = canonical_strategy_id(path.parent.name)
            cumulative_key = next(
                (
                    key
                    for key in ("cumulative_pnl_usd", "cum_pnl_usd", "cumulative_pnl")
                    if any(key in r for r in records)
                ),
                None,
            )
            if cumulative_key:
                by_sid: dict[str, list[dict[str, Any]]] = {}
                for item in records:
                    sid = canonical_strategy_id(item.get("strategy_id") or item.get("strategy") or default_sid)
                    if sid:
                        by_sid.setdefault(sid, []).append(item)
                for sid, items in by_sid.items():
                    before = None
                    last = None
                    for item in items:
                        dt = _pnl_timestamp(item)
                        if dt is None:
                            continue
                        value = safe_float(item.get(cumulative_key), float("nan"))
                        if value != value:
                            continue
                        if dt < cutoff:
                            before = value
                        else:
                            if before is None:
                                before = 0.0
                            last = value
                    if last is not None:
                        _add_strategy_pnl(totals, sid, last - (before or 0.0), str(path.relative_to(ROOT)))
                continue

            for item in records:
                dt = _pnl_timestamp(item)
                if dt is not None and dt < cutoff:
                    continue
                pnl = _first_pnl_value(item)
                if pnl is None:
                    continue
                sid = item.get("strategy_id") or item.get("strategy") or default_sid
                _add_strategy_pnl(totals, sid, pnl, str(path.relative_to(ROOT)))
        except Exception as exc:
            logger.debug("Failed to aggregate strategy PnL from %s: %s", path, exc)


def _load_open_strategy_pnl(totals: dict[str, dict[str, Any]]) -> None:
    position_sources = [
        lambda: get_ibkr_positions_via_insync(mode="live"),
        lambda: get_ibkr_positions_via_insync(port=int(os.environ.get("IBKR_PAPER_PORT", "4003")), mode="paper"),
        get_binance_positions,
        get_alpaca_positions,
    ]
    for load_positions in position_sources:
        try:
            for pos in load_positions():
                pnl = safe_float(pos.get("pnl"), 0.0)
                strategy = pos.get("strategy") or pos.get("strategy_id")
                if strategy and (pnl != 0.0 or pos.get("source")):
                    _add_strategy_pnl(totals, strategy, pnl, "open_positions")
        except Exception as exc:
            logger.debug("Failed to aggregate open-position strategy PnL: %s", exc)


def _strategy_pnl_5d() -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=5)
    totals: dict[str, dict[str, Any]] = {}
    state = load_json(STATE_DIR / "paper_portfolio_state.json", {})
    for sid, entries in state.get("strategy_pnl_log", {}).items() if isinstance(state, dict) else []:
        if isinstance(entries, list):
            pnl = sum(safe_float(e.get("pnl")) for e in entries[-5:] if isinstance(e, dict))
            _add_strategy_pnl(totals, sid, pnl, "paper_portfolio_state")

    _load_sqlite_strategy_pnl(cutoff, totals)
    _load_jsonl_strategy_pnl(cutoff, totals)
    _load_open_strategy_pnl(totals)
    for row in totals.values():
        row["pnl"] = round(float(row.get("pnl", 0.0)), 2)
        row["sources"] = dict(sorted(row.get("sources", {}).items()))
    return totals


def build_strategy_rows() -> list[dict[str, Any]]:
    entries = quant_strategy_entries()
    live = live_equity_total()
    pnl_5d = _strategy_pnl_5d()
    safe_disabled = load_safe003_disabled()
    kill_state = load_kill_switch_state()
    kill_disabled = set(kill_state.get("disabled_strategies", []) or [])

    rows = []
    for entry in entries:
        sid = str(entry.get("strategy_id"))
        status = str(entry.get("status", "disabled"))
        grade = str(entry.get("grade", "B"))
        book = str(entry.get("book", ""))
        phase = STATUS_TO_PHASE.get(status, status.upper())
        if grade == "REJECTED":
            phase = "REJECTED"
        broker = BOOK_TO_BROKER.get(book, book.upper() or "UNKNOWN")
        asset = BOOK_TO_ASSET.get(book, "OTHER")
        broker_equity = live["ibkr_equity"] if broker == "IBKR" else live["binance_equity"] if broker == "BINANCE" else live["alpaca_live_equity"]

        allocation_pct: float | None = None
        if status == "live_core":
            allocation_pct = safe_float(entry.get("risk_budget_pct"), 0.08) * 100
        elif status in {"live_micro", "live_probation"}:
            allocation_pct = GRADE_NOTIONAL_PCT.get(grade, 0.8)

        capital = broker_equity * allocation_pct / 100 if allocation_pct else 0.0
        disabled_reason = None
        if sid in safe_disabled:
            disabled_reason = "SAFE-003"
        if sid in kill_disabled:
            disabled_reason = "KILL_SWITCH"

        kill_status = disabled_reason or ("OK" if phase in {"LIVE", "LIVE_MICRO", "PROBATION", "PAPER"} else "N/A")
        threshold = -(capital * 0.2) if capital else 0.0
        pnl_detail = pnl_5d.get(sid)
        pnl = float(pnl_detail["pnl"]) if pnl_detail is not None else None
        rows.append({
            "id": sid,
            "name": entry.get("display_name") or entry.get("name") or _title_strategy(sid),
            "tier": grade,
            "grade": grade,
            "status": status.upper(),
            "phase": phase,
            "asset_class": asset,
            "broker": broker,
            "book": book,
            "phase_since": entry.get("live_start_at") or entry.get("paper_start_at") or entry.get("frozen_at") or "",
            "type": entry.get("runtime_entrypoint") or "registry",
            "sharpe": manifest_sharpe(entry.get("wf_manifest_path")),
            "allocation_pct": round(allocation_pct, 2) if allocation_pct is not None else None,
            "capital": round(capital, 2),
            "pnl_5d": round(pnl, 2) if pnl is not None else None,
            "pnl_5d_sources": pnl_detail.get("sources", {}) if pnl_detail else {},
            "kill_threshold": round(threshold, 2),
            "kill_margin_pct": round(((pnl or 0.0) - threshold) / abs(threshold) * 100, 0) if threshold else 100,
            "kill_switch_status": kill_status,
            "kill_switch_reason": disabled_reason,
            "is_live": bool(entry.get("is_live", False)),
            "paper_start_at": entry.get("paper_start_at"),
            "live_start_at": entry.get("live_start_at"),
            "wf_manifest_path": entry.get("wf_manifest_path"),
            "infra_gaps": entry.get("infra_gaps") or [],
            "notes": entry.get("notes", ""),
        })
    rows.sort(key=lambda row: (
        {"LIVE": 0, "LIVE_MICRO": 1, "PROBATION": 2, "PAPER": 3, "FROZEN": 4, "RESEARCH": 5, "DISABLED": 6, "REJECTED": 7}.get(row["phase"], 9),
        row["id"],
    ))
    return rows


def strategy_detail(strategy_id: str) -> dict[str, Any] | None:
    for row in build_strategy_rows():
        if row["id"] == strategy_id:
            manifest = load_json(_manifest_path(row.get("wf_manifest_path")) or Path(""), {})
            row = dict(row)
            row["backtest"] = manifest.get("summary", {}) if isinstance(manifest, dict) else {}
            row["parameters"] = manifest.get("params", {}) if isinstance(manifest, dict) else {}
            row["trades_sample"] = []
            row["trades_count"] = 0
            return row
    return None


def live_drawdown_snapshot(live_equity: float | None = None) -> dict[str, Any]:
    live = live_equity_total()
    equity = safe_float(live_equity, 0.0) or live["live_equity"]
    ibkr = load_live_risk_dd_state()
    crypto = load_crypto_dd_state()
    ibkr_equity = live["ibkr_equity"]
    binance_equity = live["binance_equity"]

    def stable_anchor(anchor: float, current: float) -> float:
        """Ignore stale anchors from an old equity perimeter.

        This prevents dashboard-only false daily PnL spikes when a broker
        snapshot starts including cash/earn buckets that the persisted DD anchor
        did not include yet.
        """
        if current <= 0:
            return anchor
        if anchor <= 0:
            return current
        return current if abs(anchor - current) / current > 0.25 else anchor

    ibkr_start = stable_anchor(safe_float(ibkr.get("daily_start_equity"), ibkr_equity), ibkr_equity)
    crypto_start = stable_anchor(safe_float(crypto.get("daily_start_equity", crypto.get("daily_start")), binance_equity), binance_equity)
    daily_start = (ibkr_start if ibkr_start > 0 else ibkr_equity) + (crypto_start if crypto_start > 0 else binance_equity)

    ibkr_peak = stable_anchor(safe_float(ibkr.get("peak_equity"), ibkr_equity), ibkr_equity)
    crypto_peak = stable_anchor(safe_float(crypto.get("peak_equity", crypto.get("peak")), binance_equity), binance_equity)
    peak = (ibkr_peak if ibkr_peak > 0 else ibkr_equity) + (crypto_peak if crypto_peak > 0 else binance_equity)
    if peak < equity:
        peak = equity

    current_pct = (equity - peak) / peak * 100 if peak > 0 else 0.0
    daily_pnl = equity - daily_start if daily_start > 0 else 0.0
    daily_pct = daily_pnl / daily_start * 100 if daily_start > 0 else 0.0
    return {
        "equity": equity,
        "daily_start": daily_start,
        "peak": peak,
        "current_pct": current_pct,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pct,
        "sources": {
            "ibkr_dd": bool(ibkr),
            "crypto_dd": bool(crypto),
        },
    }
