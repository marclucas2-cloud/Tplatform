"""Paper-only runners for conditional short-list strategies.

These cycles are deliberately log-only. They do not import broker clients and
never place orders. Their purpose is to start collecting forward paper evidence
for watchlist candidates that were not strong enough for live promotion.

Strategies:
  - bnb_defensive_trend_24h: Binance spot observer, BNBUSDC execution proxy.
  - zn_month_end_extension: IBKR futures observer, ZN=F proxy.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

logger = logging.getLogger("worker")

ROOT = Path(__file__).resolve().parents[3]
PAPER_START = date(2026, 5, 14)


# ---------------------------------------------------------------------------
# Shared IO helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _date_str(d: date | pd.Timestamp | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, pd.Timestamp):
        return d.date().isoformat()
    return d.isoformat()


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("%s: corrupted json state (%s), resetting", path, exc)
    return dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def _last_lines_jsonl(path: Path, limit: int = 1000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows


# ---------------------------------------------------------------------------
# BNB defensive trend 24h
# ---------------------------------------------------------------------------

BNB_STRATEGY_ID = "bnb_defensive_trend_24h"
BNB_STATE_DIR = ROOT / "data" / "state" / BNB_STRATEGY_ID
BNB_STATE_FILE = BNB_STATE_DIR / "state.json"
BNB_JOURNAL_FILE = BNB_STATE_DIR / "journal.jsonl"

BNB_NOTIONAL_USD = 2000.0
BNB_PAPER_CAPITAL_USD = 10000.0
BNB_STOP_PCT = 0.04
BNB_TAKE_PROFIT_PCT = 0.08
BNB_ROUND_TRIP_COST_PCT = 0.0025


@dataclass(frozen=True)
class BnbSignal:
    enter: bool
    reason: str
    bnb_close: float | None = None
    btc_close: float | None = None
    bnb_sma50: float | None = None
    btc_sma100: float | None = None
    bnb_return_10d: float | None = None
    bnb_btc_return_10d: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enter": self.enter,
            "reason": self.reason,
            "bnb_close": self.bnb_close,
            "btc_close": self.btc_close,
            "bnb_sma50": self.bnb_sma50,
            "btc_sma100": self.btc_sma100,
            "bnb_return_10d": self.bnb_return_10d,
            "bnb_btc_return_10d": self.bnb_btc_return_10d,
        }


def _empty_bnb_state() -> dict[str, Any]:
    return {
        "strategy_id": BNB_STRATEGY_ID,
        "mode": "paper_only_log",
        "paper_start_at": PAPER_START.isoformat(),
        "paper_equity_usd": BNB_PAPER_CAPITAL_USD,
        "realized_pnl_usd": 0.0,
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "last_bar_date": None,
        "latest_available_bar": None,
        "last_cycle_utc": None,
        "bootstrap_wait_logged_for": None,
    }


def _fetch_binance_daily(symbol: str, limit: int = 420) -> pd.DataFrame:
    params = urlencode({"symbol": symbol, "interval": "1d", "limit": limit})
    url = f"https://api.binance.com/api/v3/klines?{params}"
    with urlopen(url, timeout=20) as response:
        raw = json.loads(response.read().decode("utf-8"))

    rows = []
    for item in raw:
        rows.append({
            "timestamp": pd.to_datetime(int(item[0]), unit="ms", utc=True).tz_localize(None).normalize(),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"Binance returned no bars for {symbol}")
    df = df.set_index("timestamp").sort_index()

    today_utc = _utc_now().date()
    return df[df.index.date < today_utc]


def _load_local_crypto_daily(symbol: str) -> pd.DataFrame:
    candidates = [
        ROOT / "data" / "crypto" / "candles" / f"{symbol}_1D_LONG.parquet",
        ROOT / "data" / "crypto" / "candles" / f"{symbol}_1d.parquet",
    ]
    for path in candidates:
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        if "timestamp" in df.columns:
            idx = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None).dt.normalize()
            df = df.drop(columns=["timestamp"])
            df.index = idx
        else:
            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None).normalize()
        cols = {c: str(c).lower() for c in df.columns}
        df = df.rename(columns=cols)
        needed = ["open", "high", "low", "close"]
        if all(c in df.columns for c in needed):
            return df[needed + ([c for c in ["volume"] if c in df.columns])].sort_index()
    raise FileNotFoundError(f"No local crypto daily parquet found for {symbol}")


def _load_bnb_panel() -> tuple[pd.DataFrame, str]:
    source = "binance_api"
    try:
        bnb = _fetch_binance_daily("BNBUSDT")
        btc = _fetch_binance_daily("BTCUSDT")
    except Exception as exc:
        logger.warning("bnb_defensive_trend_24h: Binance fetch failed, using local cache (%s)", exc)
        source = "local_parquet"
        bnb = _load_local_crypto_daily("BNBUSDT")
        btc = _load_local_crypto_daily("BTCUSDT")

    panel = (
        bnb[["open", "high", "low", "close"]]
        .rename(columns={c: f"bnb_{c}" for c in ["open", "high", "low", "close"]})
        .join(
            btc[["open", "high", "low", "close"]].rename(
                columns={c: f"btc_{c}" for c in ["open", "high", "low", "close"]}
            ),
            how="inner",
        )
        .dropna()
        .sort_index()
    )
    return panel, source


def compute_bnb_defensive_signal(panel: pd.DataFrame, target_date: date | pd.Timestamp) -> BnbSignal:
    """Signal for target_date using only bars strictly before target_date."""
    target_ts = pd.Timestamp(target_date).tz_localize(None).normalize()
    hist = panel[panel.index < target_ts].copy()
    if len(hist) < 111:
        return BnbSignal(False, f"warmup:{len(hist)}_bars")

    bnb_close = hist["bnb_close"]
    btc_close = hist["btc_close"]
    bnb_sma50 = float(bnb_close.rolling(50).mean().iloc[-1])
    btc_sma100 = float(btc_close.rolling(100).mean().iloc[-1])
    last_bnb = float(bnb_close.iloc[-1])
    last_btc = float(btc_close.iloc[-1])
    bnb_ret10 = float(last_bnb / bnb_close.iloc[-11] - 1.0)
    ratio = bnb_close / btc_close
    rel_ret10 = float(ratio.iloc[-1] / ratio.iloc[-11] - 1.0)

    checks = {
        "bnb_above_sma50": last_bnb > bnb_sma50,
        "btc_above_sma100": last_btc > btc_sma100,
        "bnb_return_10d_gt_minus_3pct": bnb_ret10 > -0.03,
        "bnb_btc_return_10d_gt_minus_3pct": rel_ret10 > -0.03,
    }
    enter = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    reason = "enter" if enter else "blocked:" + ",".join(failed)
    return BnbSignal(
        enter=enter,
        reason=reason,
        bnb_close=last_bnb,
        btc_close=last_btc,
        bnb_sma50=bnb_sma50,
        btc_sma100=btc_sma100,
        bnb_return_10d=bnb_ret10,
        bnb_btc_return_10d=rel_ret10,
    )


def _simulate_bnb_trade(bar: pd.Series) -> dict[str, Any]:
    entry = float(bar["bnb_open"])
    stop = entry * (1.0 - BNB_STOP_PCT)
    take_profit = entry * (1.0 + BNB_TAKE_PROFIT_PCT)
    high = float(bar["bnb_high"])
    low = float(bar["bnb_low"])
    close = float(bar["bnb_close"])

    if low <= stop:
        exit_price = stop
        exit_reason = "stop_loss"
    elif high >= take_profit:
        exit_price = take_profit
        exit_reason = "take_profit"
    else:
        exit_price = close
        exit_reason = "time_exit_24h"

    gross_return = exit_price / entry - 1.0
    gross_pnl = BNB_NOTIONAL_USD * gross_return
    costs = BNB_NOTIONAL_USD * BNB_ROUND_TRIP_COST_PCT
    net_pnl = gross_pnl - costs
    return {
        "entry_price": round(entry, 8),
        "exit_price": round(exit_price, 8),
        "stop_price": round(stop, 8),
        "take_profit_price": round(take_profit, 8),
        "exit_reason": exit_reason,
        "gross_return_pct": round(gross_return * 100.0, 4),
        "gross_pnl_usd": round(gross_pnl, 2),
        "costs_usd": round(costs, 2),
        "pnl_usd": round(net_pnl, 2),
    }


def run_bnb_defensive_trend_paper_cycle() -> None:
    """Daily paper observer for bnb_defensive_trend_24h.

    Schedule after the UTC daily close. Each processed bar is simulated
    retrospectively from that day's open/high/low/close, while the entry signal
    uses only data up to the previous daily close.
    """
    logger.info("=== BNB_DEFENSIVE_TREND_24H PAPER CYCLE ===")
    now = _utc_now()
    state = _read_json(BNB_STATE_FILE, _empty_bnb_state())

    try:
        panel, source = _load_bnb_panel()
    except Exception as exc:
        state["last_cycle_utc"] = now.isoformat()
        _write_json(BNB_STATE_FILE, state)
        _append_jsonl(BNB_JOURNAL_FILE, {
            "ts_utc": now.isoformat(),
            "strategy_id": BNB_STRATEGY_ID,
            "event": "skip",
            "reason": f"data_load_failed:{exc}",
        })
        logger.warning("bnb_defensive_trend_24h: data load failed: %s", exc)
        return

    latest = panel.index.max()
    state["latest_available_bar"] = _date_str(latest)
    state["last_cycle_utc"] = now.isoformat()

    if latest.date() < PAPER_START:
        if state.get("bootstrap_wait_logged_for") != latest.date().isoformat():
            _append_jsonl(BNB_JOURNAL_FILE, {
                "ts_utc": now.isoformat(),
                "strategy_id": BNB_STRATEGY_ID,
                "event": "bootstrap_wait",
                "latest_available_bar": _date_str(latest),
                "paper_start_at": PAPER_START.isoformat(),
                "source": source,
            })
            state["bootstrap_wait_logged_for"] = latest.date().isoformat()
        _write_json(BNB_STATE_FILE, state)
        logger.info("bnb_defensive_trend_24h: waiting for first paper bar >= %s", PAPER_START)
        return

    last_done = pd.Timestamp(state["last_bar_date"]) if state.get("last_bar_date") else None
    if last_done is not None:
        last_done = last_done.tz_localize(None).normalize()

    processed = 0
    for bar_ts, bar in panel.iterrows():
        if bar_ts.date() < PAPER_START:
            continue
        if last_done is not None and bar_ts <= last_done:
            continue

        signal = compute_bnb_defensive_signal(panel, bar_ts)
        event: dict[str, Any] = {
            "ts_utc": now.isoformat(),
            "strategy_id": BNB_STRATEGY_ID,
            "event": "entry_exit" if signal.enter else "no_signal",
            "bar_date": _date_str(bar_ts),
            "symbol": "BNBUSDC",
            "data_symbol": "BNBUSDT",
            "source": source,
            "notional_usd": BNB_NOTIONAL_USD,
            "signal": signal.to_dict(),
            "paper_only": True,
        }
        if signal.enter:
            trade = _simulate_bnb_trade(bar)
            event["trade"] = trade
            state["trade_count"] = int(state.get("trade_count", 0)) + 1
            state["realized_pnl_usd"] = round(float(state.get("realized_pnl_usd", 0.0)) + trade["pnl_usd"], 2)
            state["paper_equity_usd"] = round(BNB_PAPER_CAPITAL_USD + float(state["realized_pnl_usd"]), 2)
            if trade["pnl_usd"] > 0:
                state["wins"] = int(state.get("wins", 0)) + 1
            else:
                state["losses"] = int(state.get("losses", 0)) + 1
        state["last_bar_date"] = _date_str(bar_ts)
        _append_jsonl(BNB_JOURNAL_FILE, event)
        processed += 1

    _write_json(BNB_STATE_FILE, state)
    logger.info(
        "bnb_defensive_trend_24h: processed=%s latest=%s trades=%s pnl=$%.2f",
        processed,
        _date_str(latest),
        state.get("trade_count", 0),
        float(state.get("realized_pnl_usd", 0.0)),
    )


# ---------------------------------------------------------------------------
# ZN month-end extension
# ---------------------------------------------------------------------------

ZN_STRATEGY_ID = "zn_month_end_extension"
ZN_STATE_DIR = ROOT / "data" / "state" / ZN_STRATEGY_ID
ZN_STATE_FILE = ZN_STATE_DIR / "state.json"
ZN_JOURNAL_FILE = ZN_STATE_DIR / "journal.jsonl"

ZN_CONTRACT = "ZN"
ZN_YF_SYMBOL = "ZN=F"
ZN_DOLLARS_PER_POINT = 1000.0
ZN_TICK_SIZE = 1.0 / 64.0
ZN_TICK_VALUE = 15.625
ZN_STOP_ATR = 1.2
ZN_TAKE_PROFIT_ATR = 2.0
ZN_COMMISSION_RT_USD = 1.24
ZN_IBKR_CAPITAL_USD = 29500.0
ZN_MAX_RISK_USD = ZN_IBKR_CAPITAL_USD * 0.05


def _empty_zn_state() -> dict[str, Any]:
    return {
        "strategy_id": ZN_STRATEGY_ID,
        "mode": "paper_only_log",
        "paper_start_at": PAPER_START.isoformat(),
        "paper_equity_usd": 100000.0,
        "realized_pnl_usd": 0.0,
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "open_position": None,
        "last_bar_date": None,
        "latest_available_bar": None,
        "last_cycle_utc": None,
        "bootstrap_wait_logged_for": None,
    }


def month_end_entry_date(d: date) -> date:
    month_start = date(d.year, d.month, 1)
    if d.month == 12:
        next_month = date(d.year + 1, 1, 1)
    else:
        next_month = date(d.year, d.month + 1, 1)
    month_end = next_month - timedelta(days=1)
    business_days = [ts.date() for ts in pd.bdate_range(month_start, month_end)]
    if len(business_days) < 3:
        return business_days[0]
    return business_days[-3]


def month_end_exit_date(entry_date: date) -> date:
    if entry_date.month == 12:
        year, month = entry_date.year + 1, 1
    else:
        year, month = entry_date.year, entry_date.month + 1
    month_start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    month_end = next_month - timedelta(days=1)
    business_days = [ts.date() for ts in pd.bdate_range(month_start, month_end)]
    return business_days[1] if len(business_days) >= 2 else business_days[-1]


def _round_to_tick(price: float) -> float:
    return round(round(price / ZN_TICK_SIZE) * ZN_TICK_SIZE, 8)


def _normalize_yfinance_ohlc(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1)
        else:
            df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={c: str(c).lower().replace(" ", "_") for c in df.columns})
    needed = ["open", "high", "low", "close"]
    if not all(c in df.columns for c in needed):
        raise ValueError(f"Missing OHLC columns in yfinance payload: {df.columns}")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[needed + ([c for c in ["volume"] if c in df.columns])].dropna().sort_index()
    today_utc = _utc_now().date()
    return df[df.index.date < today_utc]


def _load_zn_daily() -> tuple[pd.DataFrame, str]:
    try:
        import yfinance as yf

        raw = yf.download(ZN_YF_SYMBOL, period="18mo", interval="1d", progress=False, auto_adjust=False)
        df = _normalize_yfinance_ohlc(raw, ZN_YF_SYMBOL)
        if df.empty:
            raise ValueError("empty yfinance data")
        return df, "yfinance_ZN=F"
    except Exception as exc:
        logger.warning("zn_month_end_extension: yfinance failed, using local IBKR cache (%s)", exc)

    path = ROOT / "data" / "futures" / "ZN_1H_IBKR6M.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No ZN fallback data at {path}")
    raw = pd.read_parquet(path)
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    day = raw.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    today_utc = _utc_now().date()
    return day[day.index.date < today_utc], "local_ZN_1H_IBKR6M"


def _atr_at_prior_close(df: pd.DataFrame, loc: int, window: int = 20) -> float | None:
    hist = df.iloc[:loc].copy()
    if len(hist) < window + 1:
        return None
    prev_close = hist["close"].shift(1)
    tr = pd.concat(
        [
            hist["high"] - hist["low"],
            (hist["high"] - prev_close).abs(),
            (hist["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window).mean().iloc[-1]
    if pd.isna(atr):
        return None
    return float(atr)


def _zn_round_trip_cost_usd(price: float) -> float:
    notional = price * ZN_DOLLARS_PER_POINT
    slippage_ticks_rt = 2.0 * ZN_TICK_VALUE
    notional_slippage_rt = notional * 0.0004
    return slippage_ticks_rt + notional_slippage_rt + ZN_COMMISSION_RT_USD


def _close_zn_position(
    state: dict[str, Any],
    bar_ts: pd.Timestamp,
    exit_price: float,
    reason: str,
    source: str,
    costs_usd: float,
) -> dict[str, Any]:
    pos = state["open_position"]
    entry = float(pos["entry_price"])
    exit_px = _round_to_tick(exit_price)
    gross_pnl = (exit_px - entry) * ZN_DOLLARS_PER_POINT
    net_pnl = gross_pnl - costs_usd
    held_days = (bar_ts.date() - date.fromisoformat(pos["entry_date"])).days
    trade = {
        "entry_date": pos["entry_date"],
        "exit_date": _date_str(bar_ts),
        "entry_price": entry,
        "exit_price": exit_px,
        "exit_reason": reason,
        "hold_days": held_days,
        "gross_pnl_usd": round(gross_pnl, 2),
        "costs_usd": round(costs_usd, 2),
        "pnl_usd": round(net_pnl, 2),
        "source": source,
    }
    state["open_position"] = None
    state["trade_count"] = int(state.get("trade_count", 0)) + 1
    state["realized_pnl_usd"] = round(float(state.get("realized_pnl_usd", 0.0)) + trade["pnl_usd"], 2)
    state["paper_equity_usd"] = round(100000.0 + float(state["realized_pnl_usd"]), 2)
    if trade["pnl_usd"] > 0:
        state["wins"] = int(state.get("wins", 0)) + 1
    else:
        state["losses"] = int(state.get("losses", 0)) + 1
    return {
        "ts_utc": _utc_now().isoformat(),
        "strategy_id": ZN_STRATEGY_ID,
        "event": "exit",
        "bar_date": _date_str(bar_ts),
        "symbol": ZN_CONTRACT,
        "trade": trade,
        "paper_only": True,
    }


def _process_zn_bar(
    state: dict[str, Any],
    df: pd.DataFrame,
    loc: int,
    source: str,
) -> list[dict[str, Any]]:
    bar_ts = df.index[loc]
    bar = df.iloc[loc]
    events: list[dict[str, Any]] = []
    current_date = bar_ts.date()

    if state.get("open_position"):
        pos = state["open_position"]
        costs = _zn_round_trip_cost_usd(float(pos["entry_price"]))
        stop = float(pos["stop_price"])
        take_profit = float(pos["take_profit_price"])
        if float(bar["low"]) <= stop:
            events.append(_close_zn_position(state, bar_ts, stop, "stop_loss", source, costs))
        elif float(bar["high"]) >= take_profit:
            events.append(_close_zn_position(state, bar_ts, take_profit, "take_profit", source, costs))
        elif current_date >= date.fromisoformat(pos["target_exit_date"]):
            events.append(_close_zn_position(state, bar_ts, float(bar["close"]), "calendar_exit", source, costs))

    if state.get("open_position") is None:
        entry_day = month_end_entry_date(current_date)
        if current_date == entry_day:
            atr = _atr_at_prior_close(df, loc)
            if atr is None:
                events.append({
                    "ts_utc": _utc_now().isoformat(),
                    "strategy_id": ZN_STRATEGY_ID,
                    "event": "skip",
                    "bar_date": _date_str(bar_ts),
                    "reason": "atr_warmup",
                    "paper_only": True,
                })
            else:
                entry = _round_to_tick(float(bar["open"]))
                stop_points = ZN_STOP_ATR * atr
                take_points = ZN_TAKE_PROFIT_ATR * atr
                stop = _round_to_tick(entry - stop_points)
                take_profit = _round_to_tick(entry + take_points)
                costs = _zn_round_trip_cost_usd(entry)
                risk_usd = (entry - stop) * ZN_DOLLARS_PER_POINT + costs
                if risk_usd > ZN_MAX_RISK_USD:
                    events.append({
                        "ts_utc": _utc_now().isoformat(),
                        "strategy_id": ZN_STRATEGY_ID,
                        "event": "skip",
                        "bar_date": _date_str(bar_ts),
                        "reason": "risk_cap_exceeded",
                        "entry_price": entry,
                        "risk_usd": round(risk_usd, 2),
                        "max_risk_usd": round(ZN_MAX_RISK_USD, 2),
                        "atr20": round(atr, 6),
                        "paper_only": True,
                    })
                else:
                    state["open_position"] = {
                        "entry_date": _date_str(bar_ts),
                        "entry_price": entry,
                        "stop_price": stop,
                        "take_profit_price": take_profit,
                        "target_exit_date": month_end_exit_date(current_date).isoformat(),
                        "atr20": round(atr, 6),
                        "risk_usd": round(risk_usd, 2),
                    }
                    events.append({
                        "ts_utc": _utc_now().isoformat(),
                        "strategy_id": ZN_STRATEGY_ID,
                        "event": "entry",
                        "bar_date": _date_str(bar_ts),
                        "symbol": ZN_CONTRACT,
                        "position": state["open_position"],
                        "source": source,
                        "paper_only": True,
                    })

                    # Conservative same-bar bracket check after entry.
                    if float(bar["low"]) <= stop:
                        events.append(_close_zn_position(state, bar_ts, stop, "same_day_stop_loss", source, costs))
                    elif float(bar["high"]) >= take_profit:
                        events.append(_close_zn_position(state, bar_ts, take_profit, "same_day_take_profit", source, costs))
        else:
            events.append({
                "ts_utc": _utc_now().isoformat(),
                "strategy_id": ZN_STRATEGY_ID,
                "event": "no_signal",
                "bar_date": _date_str(bar_ts),
                "next_entry_date": month_end_entry_date(current_date).isoformat(),
                "paper_only": True,
            })

    return events


def run_zn_month_end_extension_paper_cycle() -> None:
    """Daily paper observer for zn_month_end_extension."""
    logger.info("=== ZN_MONTH_END_EXTENSION PAPER CYCLE ===")
    now = _utc_now()
    state = _read_json(ZN_STATE_FILE, _empty_zn_state())

    try:
        df, source = _load_zn_daily()
    except Exception as exc:
        state["last_cycle_utc"] = now.isoformat()
        _write_json(ZN_STATE_FILE, state)
        _append_jsonl(ZN_JOURNAL_FILE, {
            "ts_utc": now.isoformat(),
            "strategy_id": ZN_STRATEGY_ID,
            "event": "skip",
            "reason": f"data_load_failed:{exc}",
        })
        logger.warning("zn_month_end_extension: data load failed: %s", exc)
        return

    latest = df.index.max()
    state["latest_available_bar"] = _date_str(latest)
    state["last_cycle_utc"] = now.isoformat()

    if latest.date() < PAPER_START:
        if state.get("bootstrap_wait_logged_for") != latest.date().isoformat():
            _append_jsonl(ZN_JOURNAL_FILE, {
                "ts_utc": now.isoformat(),
                "strategy_id": ZN_STRATEGY_ID,
                "event": "bootstrap_wait",
                "latest_available_bar": _date_str(latest),
                "paper_start_at": PAPER_START.isoformat(),
                "source": source,
            })
            state["bootstrap_wait_logged_for"] = latest.date().isoformat()
        _write_json(ZN_STATE_FILE, state)
        logger.info("zn_month_end_extension: waiting for first paper bar >= %s", PAPER_START)
        return

    last_done = pd.Timestamp(state["last_bar_date"]) if state.get("last_bar_date") else None
    if last_done is not None:
        last_done = last_done.tz_localize(None).normalize()

    processed = 0
    for loc, bar_ts in enumerate(df.index):
        if bar_ts.date() < PAPER_START:
            continue
        if last_done is not None and bar_ts <= last_done:
            continue
        for event in _process_zn_bar(state, df, loc, source):
            _append_jsonl(ZN_JOURNAL_FILE, event)
        state["last_bar_date"] = _date_str(bar_ts)
        processed += 1

    _write_json(ZN_STATE_FILE, state)
    logger.info(
        "zn_month_end_extension: processed=%s latest=%s trades=%s pnl=$%.2f open=%s",
        processed,
        _date_str(latest),
        state.get("trade_count", 0),
        float(state.get("realized_pnl_usd", 0.0)),
        bool(state.get("open_position")),
    )


# ---------------------------------------------------------------------------
# Paper desk watcher
# ---------------------------------------------------------------------------

PAPER_WATCH_DIR = ROOT / "reports" / "paper_watch"
PAPER_WATCH_STATE = ROOT / "data" / "state" / "paper_watch" / "latest.json"

WATCHED_STATE_DIRS = {
    BNB_STRATEGY_ID: {"max_bar_age_days": 2, "expected": "daily_crypto"},
    ZN_STRATEGY_ID: {"max_bar_age_days": 5, "expected": "daily_futures"},
    "alt_rel_strength": {"max_bar_age_days": 7, "expected": "daily_crypto"},
    "btc_asia_mes_leadlag": {"max_bar_age_days": 7, "expected": "daily_crypto"},
    "macro_top1_rotation": {"max_bar_age_days": 7, "expected": "weekday_us"},
    "pead_long_only_v1": {"max_bar_age_days": 7, "expected": "weekday_us"},
    "mib_estx50_spread": {"max_bar_age_days": 7, "expected": "weekday_eu"},
}


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).date()
    except Exception:
        return None


def _find_journal(path: Path) -> Path | None:
    for name in ("journal.jsonl", "paper_journal.jsonl", "paper_trades.jsonl"):
        candidate = path / name
        if candidate.exists():
            return candidate
    return None


def _summarize_paper_dir(strategy_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    state_dir = ROOT / "data" / "state" / strategy_id
    state_path = state_dir / "state.json"
    journal_path = _find_journal(state_dir)
    state = _read_json(state_path, {}) if state_path.exists() else {}
    rows = _last_lines_jsonl(journal_path, 500) if journal_path else []
    last_event = rows[-1] if rows else {}

    now_date = _utc_now().date()
    bar_date = (
        _parse_date(state.get("last_bar_date"))
        or _parse_date(state.get("latest_available_bar"))
        or _parse_date(last_event.get("bar_date"))
        or _parse_date(last_event.get("target_date"))
        or _parse_date(last_event.get("as_of"))
    )
    cycle_date = _parse_date(state.get("last_cycle_utc") or last_event.get("ts_utc"))
    bar_age = (now_date - bar_date).days if bar_date else None
    cycle_age = (now_date - cycle_date).days if cycle_date else None
    max_age = int(cfg.get("max_bar_age_days", 7))

    status = "OK"
    reasons: list[str] = []
    if not state_dir.exists():
        status = "MISSING"
        reasons.append("state_dir_missing")
    elif not rows:
        status = "NO_JOURNAL"
        reasons.append("journal_missing_or_empty")
    if bar_age is not None and bar_age > max_age:
        status = "STALE"
        reasons.append(f"bar_age_{bar_age}d")
    if cycle_age is not None and cycle_age > max_age:
        status = "STALE"
        reasons.append(f"cycle_age_{cycle_age}d")

    pnl = state.get("realized_pnl_usd")
    if pnl is None:
        pnl = sum(float(r.get("pnl_usd", r.get("pnl_net", 0.0)) or 0.0) for r in rows)

    return {
        "strategy_id": strategy_id,
        "status": status,
        "reasons": reasons,
        "last_event": last_event.get("event") or last_event.get("action"),
        "last_bar_date": bar_date.isoformat() if bar_date else None,
        "bar_age_days": bar_age,
        "last_cycle_date": cycle_date.isoformat() if cycle_date else None,
        "cycle_age_days": cycle_age,
        "trade_count": state.get("trade_count", state.get("rebal_count")),
        "realized_pnl_usd": round(float(pnl or 0.0), 2),
        "open_position": bool(state.get("open_position") or state.get("active_positions")),
        "journal_path": str(journal_path.relative_to(ROOT)) if journal_path else None,
        "expected": cfg.get("expected"),
    }


def _render_paper_watch_markdown(summaries: list[dict[str, Any]], as_of: datetime) -> str:
    ok_count = sum(1 for s in summaries if s["status"] == "OK")
    warn_count = len(summaries) - ok_count
    lines = [
        "# Paper Watch",
        "",
        f"- as_of_utc: {as_of.isoformat()}",
        f"- ok: {ok_count}",
        f"- warnings: {warn_count}",
        "",
        "| strategy | status | last_bar | age_d | event | trades | pnl_usd | open | reason |",
        "|---|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for item in summaries:
        reason = ",".join(item["reasons"]) if item["reasons"] else ""
        lines.append(
            "| {strategy_id} | {status} | {last_bar_date} | {bar_age_days} | "
            "{last_event} | {trade_count} | {realized_pnl_usd:.2f} | {open_position} | {reason} |".format(
                **{**item, "reason": reason}
            )
        )
    lines.append("")
    lines.append("Paper-only desk hygiene: warnings mean the strategy is not generating fresh observable evidence.")
    return "\n".join(lines) + "\n"


def run_paper_watch_cycle(send_telegram: bool = True) -> None:
    """Build a daily paper monitoring report and optionally send a short alert."""
    logger.info("=== PAPER WATCH CYCLE ===")
    now = _utc_now()
    summaries = [
        _summarize_paper_dir(strategy_id, cfg)
        for strategy_id, cfg in WATCHED_STATE_DIRS.items()
    ]
    report = _render_paper_watch_markdown(summaries, now)

    PAPER_WATCH_DIR.mkdir(parents=True, exist_ok=True)
    report_path = PAPER_WATCH_DIR / f"paper_watch_{now.date().isoformat()}.md"
    report_path.write_text(report, encoding="utf-8")
    _write_json(PAPER_WATCH_STATE, {
        "as_of_utc": now.isoformat(),
        "report_path": str(report_path.relative_to(ROOT)),
        "summaries": summaries,
    })

    warn = [s for s in summaries if s["status"] != "OK"]
    if send_telegram:
        try:
            from core.worker.alerts import send_alert

            if warn:
                msg = (
                    f"PAPER WATCH: {len(warn)} warning(s). "
                    + "; ".join(f"{s['strategy_id']}={s['status']}" for s in warn[:5])
                    + f"\nReport: {report_path}"
                )
                send_alert(msg, level="warning")
            else:
                send_alert(f"PAPER WATCH OK: {len(summaries)} strategies checked\nReport: {report_path}", level="info")
        except Exception as exc:
            logger.debug("paper_watch: alert skipped (%s)", exc)

    logger.info("paper_watch: wrote %s", report_path)
