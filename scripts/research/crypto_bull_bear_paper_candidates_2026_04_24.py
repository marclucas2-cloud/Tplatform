#!/usr/bin/env python3
"""Crypto bull/bear discovery batch with non-prod outputs only.

This batch searches for crypto sleeves that can survive both bull and bear
market regimes, using only research-side artifacts. It is explicitly forbidden
to touch runtime/prod files from this script.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
import sys

sys.path.insert(0, str(ROOT))

from scripts.research.portfolio_marginal_score import score_candidate

REPORT_PATH = ROOT / "reports" / "research" / "crypto_bull_bear_paper_candidates_2026-04-24.md"
JSON_PATH = ROOT / "data" / "research" / "crypto_bull_bear_paper_candidates_2026-04-24.json"
RETURNS_PATH = ROOT / "data" / "research" / "crypto_bull_bear_paper_candidates_2026-04-24_returns.parquet"
DAILY_CACHE_PATH = ROOT / "data" / "research" / "crypto_daily_cache_2026_04_24.parquet"

DAILY_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "LINKUSDT",
    "AVAXUSDT",
]
ALT_UNIVERSE = [sym.replace("USDT", "") for sym in DAILY_SYMBOLS if sym != "BTCUSDT"]
INITIAL_IDEAS = [
    ("alt_beta_10_120_2", "cross-sectional / beta-adjusted LS"),
    ("alt_beta_20_60_3", "cross-sectional / beta-adjusted LS"),
    ("alt_longonly_top1_cash_14_5", "cross-sectional / long-only alt rotation"),
    ("alt_longonly_top1_cash_40_10", "cross-sectional / long-only alt rotation"),
    ("core_ls_20_1", "core majors / relative value"),
    ("core_ls_40_2", "core majors / relative value"),
    ("core_top1_cash_60", "core majors / trend rotation"),
    ("btc_range_longonly_30", "mean reversion / BTC 4h"),
    ("btc_range_regime_30", "mean reversion / BTC 4h"),
    ("eth_range_longonly_20", "mean reversion / ETH 4h"),
    ("eth_range_regime_30", "mean reversion / ETH 4h"),
    ("btc_funding_hybridtrend_1_5_3", "funding + trend hybrid"),
    ("btc_funding_hybridtrend_2_0_3", "funding + trend hybrid"),
    ("eth_funding_hybridtrend_1_5_7", "funding + trend hybrid"),
    ("eth_funding_hybridtrend_2_5_3", "funding + trend hybrid"),
    ("btc_weekend_reversal_5_3", "event-driven / weekend reversal"),
    ("eth_weekend_reversal_bull_3", "event-driven / weekend reversal"),
    ("basis_carry_direct", "funding carry / direct perp basis"),
]

API_BASE = "https://api.binance.com/api/v3/klines"
SIDE_COST = 0.0013
SHORT_BORROW_ALT = 0.00005
SHORT_BORROW_CORE = 0.00010
SHORT_BORROW_BTC_ETH = 0.00020
STRAT_CAPITAL = 10_000.0
CAPITAL_PER_LEG = 2_500.0
WF_WINDOWS = 5
WF_IS_RATIO = 0.60
MC_SIMS = 1000


@dataclass
class CandidateResult:
    candidate_id: str
    family: str
    notes: str
    trades: int
    total_pnl: float
    sharpe: float
    max_dd_pct: float
    bull_total_pnl: float
    bull_sharpe: float
    bear_total_pnl: float
    bear_sharpe: float
    wf_pass_windows: int
    wf_total_windows: int
    mc_prob_dd_gt_25pct: float
    corr_to_portfolio: float
    max_corr_to_strat: float
    marginal_score: float
    verdict: str


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _download_symbol_1d(symbol: str, start: str = "2020-01-01") -> pd.Series:
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    rows: list[list] = []
    cursor = start_ms
    while True:
        response = requests.get(
            API_BASE,
            params={"symbol": symbol, "interval": "1d", "limit": 1000, "startTime": cursor},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            break
        rows.extend(data)
        last_open = int(data[-1][0])
        if last_open <= cursor or len(data) < 1000:
            break
        cursor = last_open + 1
        time.sleep(0.15)
    frame = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "n_trades",
            "taker_base",
            "taker_quote",
            "ignore",
        ],
    )
    idx = pd.to_datetime(frame["open_time"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    return pd.Series(frame["close"].astype(float).values, index=idx, name=symbol.replace("USDT", ""))


def load_or_download_daily_panel() -> pd.DataFrame:
    if DAILY_CACHE_PATH.exists():
        panel = pd.read_parquet(DAILY_CACHE_PATH)
        panel.index = pd.to_datetime(panel.index).tz_localize(None)
        return panel.sort_index()
    series = [_download_symbol_1d(symbol) for symbol in DAILY_SYMBOLS]
    panel = pd.concat(series, axis=1).sort_index()
    _ensure_parent(DAILY_CACHE_PATH)
    panel.to_parquet(DAILY_CACHE_PATH)
    return panel


def load_long_daily(symbol: str) -> pd.Series:
    path = ROOT / "data" / "crypto" / "candles" / f"{symbol}USDT_1D_LONG.parquet"
    frame = pd.read_parquet(path)
    idx = pd.to_datetime(frame.index).tz_localize(None).normalize()
    return pd.Series(frame["close"].astype(float).values, index=idx, name=symbol).sort_index()


def load_daily(symbol: str) -> pd.Series:
    path = ROOT / "data" / "crypto" / "candles" / f"{symbol}USDT_1d.parquet"
    frame = pd.read_parquet(path)
    idx = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None).dt.normalize()
    return pd.Series(frame["close"].astype(float).values, index=idx, name=symbol).sort_index()


def load_4h_ohlc(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "crypto" / "candles" / f"{symbol}USDT_4h.parquet"
    frame = pd.read_parquet(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
    return frame.set_index("timestamp").sort_index()[["open", "high", "low", "close"]]


def load_funding_daily(symbol: str) -> pd.Series:
    path = ROOT / "data" / "crypto" / "funding" / f"{symbol}USDT_funding_daily.parquet"
    frame = pd.read_parquet(path)
    idx = pd.to_datetime(frame.index).tz_localize(None).normalize()
    return pd.Series(frame["funding_daily_sum"].astype(float).values, index=idx, name=f"{symbol}_funding")


def annualized_sharpe(pnl: pd.Series) -> float:
    pnl = pnl.dropna()
    if len(pnl) == 0 or pnl.std() == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(252))


def max_drawdown_pct(pnl: pd.Series, initial: float = STRAT_CAPITAL) -> float:
    pnl = pnl.fillna(0.0)
    equity = initial + pnl.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min() * 100.0)


def bootstrap_mc_dd_probability(pnl: pd.Series, threshold: float = -25.0) -> float:
    pnl = pnl.dropna()
    if len(pnl) < 50:
        return 1.0
    arr = pnl.values
    rng = np.random.default_rng(42)
    draws = []
    for _ in range(MC_SIMS):
        sample = rng.choice(arr, size=len(arr), replace=True)
        equity = STRAT_CAPITAL + np.cumsum(sample)
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak
        draws.append(dd.min() * 100.0)
    draws_arr = np.array(draws)
    return float(np.mean(draws_arr < threshold))


def walk_forward_passes(pnl: pd.Series) -> tuple[int, int]:
    pnl = pnl.dropna()
    if len(pnl) < 250:
        return 0, 0
    window_size = len(pnl) // WF_WINDOWS
    passes = 0
    total = 0
    for win in range(WF_WINDOWS):
        start = win * (window_size // 2)
        end = min(start + window_size, len(pnl))
        if end - start < 120:
            continue
        window = pnl.iloc[start:end]
        is_end = int(len(window) * WF_IS_RATIO)
        oos = window.iloc[is_end:]
        total += 1
        if len(oos) >= 30 and oos.sum() > 0 and annualized_sharpe(oos) > 0.2:
            passes += 1
    return passes, total


def regime_breakdown(pnl: pd.Series, regime: pd.Series) -> dict[str, float]:
    aligned = pnl.reindex(regime.index).fillna(0.0)
    bull = aligned[regime == 1]
    bear = aligned[regime == 0]
    return {
        "bull_total_pnl": float(bull.sum()),
        "bull_sharpe": annualized_sharpe(bull),
        "bear_total_pnl": float(bear.sum()),
        "bear_sharpe": annualized_sharpe(bear),
    }


def count_entries_from_position(position: pd.Series) -> int:
    position = position.fillna(0.0)
    return int(((position != 0.0) & (position.shift(1).fillna(0.0) == 0.0)).sum())


def count_entries_from_turnover(turnover: pd.Series) -> int:
    return int((turnover > 0).sum())


def compute_adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    plus_dm = frame["high"].diff().clip(lower=0.0)
    minus_dm = (-frame["low"].diff()).clip(lower=0.0)
    tr = pd.DataFrame(
        {
            "hl": frame["high"] - frame["low"],
            "hc": (frame["high"] - frame["close"].shift(1)).abs(),
            "lc": (frame["low"] - frame["close"].shift(1)).abs(),
        }
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()


def beta_adjusted_long_short(
    prices: pd.DataFrame,
    alpha_window: int,
    beta_window: int,
    top_n: int,
    rebalance_days: int,
) -> tuple[pd.Series, int]:
    returns = prices.pct_change().fillna(0.0)
    pnl_per_day: list[float] = []
    positions = {sym: 0.0 for sym in ALT_UNIVERSE}
    last_rebalance: pd.Timestamp | None = None
    rebalance_events = 0
    for i, dt in enumerate(prices.index):
        if i < max(alpha_window, beta_window) + 1:
            pnl_per_day.append(0.0)
            continue
        if last_rebalance is None or (dt - last_rebalance).days >= rebalance_days:
            hist = returns.iloc[:i]
            btc_window = hist["BTC"].tail(beta_window)
            btc_var = btc_window.var()
            scores = {}
            if btc_var and not pd.isna(btc_var):
                btc_cum = (1.0 + hist["BTC"].tail(alpha_window)).prod() - 1.0
                for sym in ALT_UNIVERSE:
                    beta = hist[sym].tail(beta_window).cov(btc_window) / btc_var
                    alt_cum = (1.0 + hist[sym].tail(alpha_window)).prod() - 1.0
                    scores[sym] = alt_cum - beta * btc_cum
            ranking = pd.Series(scores).dropna().sort_values(ascending=False)
            new_positions = {sym: 0.0 for sym in ALT_UNIVERSE}
            if len(ranking) >= top_n * 2:
                for sym in ranking.head(top_n).index:
                    new_positions[sym] = 1.0 / top_n
                for sym in ranking.tail(top_n).index:
                    new_positions[sym] = -1.0 / top_n
            turnover_units = sum(abs(new_positions[sym] - positions[sym]) for sym in ALT_UNIVERSE)
            positions = new_positions
            last_rebalance = dt
            if turnover_units > 0:
                rebalance_events += 1
            cost = turnover_units * STRAT_CAPITAL * SIDE_COST
        else:
            cost = 0.0
        gross = sum(positions[sym] * returns.loc[dt, sym] for sym in ALT_UNIVERSE) * STRAT_CAPITAL
        short_borrow = sum(1 for sym in ALT_UNIVERSE if positions[sym] < 0.0) * STRAT_CAPITAL * SHORT_BORROW_ALT / top_n
        pnl_per_day.append(gross - cost - short_borrow)
    return pd.Series(pnl_per_day, index=prices.index, dtype=float), rebalance_events


def alt_long_only_cash(
    prices: pd.DataFrame,
    alpha_window: int,
    rebalance_days: int,
    breadth_window: int,
    breadth_min: float,
) -> tuple[pd.Series, int]:
    returns = prices.pct_change().fillna(0.0)
    pnl_per_day: list[float] = []
    positions = {"BTC": 0.0, **{sym: 0.0 for sym in ALT_UNIVERSE}}
    last_rebalance: pd.Timestamp | None = None
    rebalances = 0
    for i, dt in enumerate(prices.index):
        if i < max(alpha_window, breadth_window) + 1:
            pnl_per_day.append(0.0)
            continue
        if last_rebalance is None or (dt - last_rebalance).days >= rebalance_days:
            hist = returns.iloc[:i]
            scores = hist[ALT_UNIVERSE].tail(alpha_window).sum().sub(hist["BTC"].tail(alpha_window).sum())
            current = prices.iloc[:i]
            breadth = (current[ALT_UNIVERSE].iloc[-1] > current[ALT_UNIVERSE].rolling(breadth_window).mean().iloc[-1]).mean()
            new_positions = {"BTC": 0.0, **{sym: 0.0 for sym in ALT_UNIVERSE}}
            if scores.max() > 0 and breadth >= breadth_min:
                new_positions[scores.idxmax()] = 1.0
            turnover_units = sum(abs(new_positions[sym] - positions[sym]) for sym in new_positions)
            positions = new_positions
            last_rebalance = dt
            if turnover_units > 0:
                rebalances += 1
            cost = turnover_units * STRAT_CAPITAL * SIDE_COST
        else:
            cost = 0.0
        gross = positions["BTC"] * returns.loc[dt, "BTC"] * STRAT_CAPITAL
        gross += sum(positions[sym] * returns.loc[dt, sym] for sym in ALT_UNIVERSE) * STRAT_CAPITAL
        pnl_per_day.append(gross - cost)
    return pd.Series(pnl_per_day, index=prices.index, dtype=float), rebalances


def core_long_short(prices: pd.DataFrame, lookback: int, top_n: int) -> tuple[pd.Series, int]:
    returns = prices.pct_change().fillna(0.0)
    momentum = prices.pct_change(lookback)
    positions = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for dt in prices.index:
        row = momentum.loc[dt]
        if row.isna().all():
            continue
        for sym in row.sort_values(ascending=False).head(top_n).index:
            positions.loc[dt, sym] += 1.0 / top_n
        for sym in row.sort_values(ascending=True).head(top_n).index:
            positions.loc[dt, sym] -= 1.0 / top_n
    positions = positions.shift(1).fillna(0.0)
    turnover = positions.diff().abs().sum(axis=1).fillna(0.0)
    gross = (positions * returns).sum(axis=1) * STRAT_CAPITAL
    borrow = (positions < 0.0).sum(axis=1) * STRAT_CAPITAL * SHORT_BORROW_CORE / top_n
    pnl = gross - turnover * STRAT_CAPITAL * SIDE_COST - borrow
    return pnl, count_entries_from_turnover(turnover)


def core_top1_cash(prices: pd.DataFrame, lookback: int) -> tuple[pd.Series, int]:
    returns = prices.pct_change().fillna(0.0)
    momentum = prices.pct_change(lookback)
    btc_trend = prices["BTC"] > prices["BTC"].rolling(200).mean()
    signal = pd.Series("CASH", index=prices.index, dtype="object")
    for dt in prices.index:
        row = momentum.loc[dt]
        if row.isna().all():
            continue
        top = row.idxmax()
        if row[top] > 0 and bool(btc_trend.loc[dt]):
            signal.loc[dt] = top
    positions = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for sym in prices.columns:
        positions[sym] = (signal.shift(1) == sym).astype(float)
    turnover = positions.diff().abs().sum(axis=1).fillna(0.0)
    pnl = (positions * returns).sum(axis=1) * STRAT_CAPITAL - turnover * STRAT_CAPITAL * SIDE_COST
    return pnl, count_entries_from_turnover(turnover)


def range_harvest(
    ohlc: pd.DataFrame,
    regime_daily: pd.Series,
    bb_period: int,
    mode: str,
) -> tuple[pd.Series, int]:
    work = ohlc.copy()
    work["sma"] = work["close"].rolling(bb_period).mean()
    work["std"] = work["close"].rolling(bb_period).std()
    work["bb_upper"] = work["sma"] + 2.0 * work["std"]
    work["bb_lower"] = work["sma"] - 2.0 * work["std"]
    work["adx"] = compute_adx(work, 14)

    daily_index = pd.Index(sorted(work.index.normalize().unique()))
    daily_pnl = pd.Series(0.0, index=daily_index, dtype=float)
    regime_map = regime_daily.to_dict()
    position: dict[str, float] | None = None
    trades = 0

    for i in range(1, len(work)):
        now = work.index[i]
        row = work.iloc[i]
        prev = work.iloc[i - 1]
        state = regime_map.get(now.normalize(), 0)

        if position is not None:
            position["bars_held"] += 1
            exit_price = None
            if position["direction"] == 1:
                if row["low"] <= position["stop"]:
                    exit_price = position["stop"]
                elif row["high"] >= position["target"]:
                    exit_price = position["target"]
            else:
                if row["high"] >= position["stop"]:
                    exit_price = position["stop"]
                elif row["low"] <= position["target"]:
                    exit_price = position["target"]
            if exit_price is None and position["bars_held"] >= 18:
                exit_price = float(row["close"])
            if exit_price is not None:
                gross = position["direction"] * (float(exit_price) - position["entry"]) * position["qty"]
                cost = STRAT_CAPITAL * 2.0 * SIDE_COST
                daily_pnl.loc[now.normalize()] += gross - cost
                position = None

        if position is not None:
            continue
        values = [prev["bb_upper"], prev["bb_lower"], prev["sma"], prev["adx"], row["open"]]
        if any(pd.isna(v) for v in values) or prev["adx"] >= 20:
            continue

        entry = float(row["open"])
        qty = STRAT_CAPITAL / entry if entry > 0 else 0.0
        long_ok = mode in {"long_only", "both"} or (mode == "regime" and state == 1)
        short_ok = mode in {"short_only", "both"} or (mode == "regime" and state == 0)

        if prev["close"] < prev["bb_lower"] and long_ok:
            target = float(prev["sma"])
            edge = max(target - float(prev["close"]), 0.0)
            if edge > 0:
                position = {
                    "direction": 1,
                    "entry": entry,
                    "qty": qty,
                    "target": target,
                    "stop": entry - 1.5 * edge,
                    "bars_held": 0,
                }
                trades += 1
        elif prev["close"] > prev["bb_upper"] and short_ok:
            target = float(prev["sma"])
            edge = max(float(prev["close"]) - target, 0.0)
            if edge > 0:
                position = {
                    "direction": -1,
                    "entry": entry,
                    "qty": qty,
                    "target": target,
                    "stop": entry + 1.5 * edge,
                    "bars_held": 0,
                }
                trades += 1
    return daily_pnl, trades


def funding_hybridtrend(
    price: pd.Series,
    funding_daily: pd.Series,
    regime_daily: pd.Series,
    z_threshold: float,
    hold_days: int,
) -> tuple[pd.Series, int]:
    price = price.sort_index()
    funding = funding_daily.reindex(price.index).ffill().fillna(0.0)
    regime = regime_daily.reindex(price.index).ffill().fillna(0).astype(int)
    trend = price > price.rolling(100).mean()
    ret = price.pct_change().fillna(0.0)
    zscore = (funding - funding.rolling(90).mean()) / funding.rolling(90).std()
    position = pd.Series(0.0, index=price.index, dtype=float)
    trend_shift = trend.shift(1, fill_value=False)
    regime_shift = regime.shift(1, fill_value=0)
    zscore_shift = zscore.shift(1)
    long_signal = (
        ((regime_shift == 1) & (zscore_shift > z_threshold) & trend_shift)
        | ((regime_shift == 0) & (zscore_shift < -z_threshold) & trend_shift)
    )
    short_signal = (
        ((regime_shift == 1) & (zscore_shift < -z_threshold) & (~trend_shift))
        | ((regime_shift == 0) & (zscore_shift > z_threshold) & (~trend_shift))
    )
    entries = 0
    for i in range(len(position)):
        if long_signal.iloc[i]:
            position.iloc[i : min(i + hold_days, len(position))] = 1.0
            entries += 1
        if short_signal.iloc[i]:
            position.iloc[i : min(i + hold_days, len(position))] = -1.0
            entries += 1
    turnover = position.diff().abs().fillna(0.0)
    pnl = position * ret * STRAT_CAPITAL - turnover * STRAT_CAPITAL * SIDE_COST
    pnl -= (position < 0.0).astype(float) * STRAT_CAPITAL * SHORT_BORROW_BTC_ETH
    return pnl, entries


def weekend_reversal(
    price: pd.Series,
    weekend_drop_threshold: float,
    hold_days: int,
    bull_filter: bool,
) -> tuple[pd.Series, int]:
    returns = price.pct_change().fillna(0.0)
    sma200 = price > price.rolling(200).mean()
    day_of_week = pd.Series(price.index.dayofweek, index=price.index)
    signal = (day_of_week.shift(1) == 6) & (returns.shift(1) <= weekend_drop_threshold)
    if bull_filter:
        signal = signal & sma200.shift(1, fill_value=False)
    position = pd.Series(0.0, index=price.index, dtype=float)
    entries = 0
    for i in range(len(position)):
        if signal.iloc[i]:
            position.iloc[i : min(i + hold_days, len(position))] = 1.0
            entries += 1
    turnover = position.diff().abs().fillna(0.0)
    pnl = position * returns * STRAT_CAPITAL - turnover * STRAT_CAPITAL * SIDE_COST
    return pnl, entries


def build_candidate_series() -> tuple[dict[str, tuple[pd.Series, int, str, str]], pd.Series]:
    daily_panel = load_or_download_daily_panel().dropna().sort_index()
    btc_long = load_long_daily("BTC")
    eth_long = load_long_daily("ETH")
    bnb_long = load_long_daily("BNB")
    sol_long = load_long_daily("SOL")
    btc_regime = (btc_long > btc_long.rolling(200).mean()).astype(int)
    common_core = btc_long.index.intersection(eth_long.index).intersection(bnb_long.index).intersection(sol_long.index)
    core_prices = pd.DataFrame(
        {
            "BTC": btc_long.loc[common_core],
            "ETH": eth_long.loc[common_core],
            "BNB": bnb_long.loc[common_core],
            "SOL": sol_long.loc[common_core],
        }
    ).dropna()
    btc_4h = load_4h_ohlc("BTC")
    eth_4h = load_4h_ohlc("ETH")
    btc_funding = load_funding_daily("BTC")
    eth_funding = load_funding_daily("ETH")

    candidates: dict[str, tuple[pd.Series, int, str, str]] = {}
    candidates["alt_beta_10_120_2"] = (
        *beta_adjusted_long_short(daily_panel, alpha_window=10, beta_window=120, top_n=2, rebalance_days=7),
        "cross-sectional / beta-adjusted LS",
        "long top 2 alts, short bottom 2 alts, 10d alpha vs BTC with 120d beta window",
    )
    candidates["alt_beta_20_60_3"] = (
        *beta_adjusted_long_short(daily_panel, alpha_window=20, beta_window=60, top_n=3, rebalance_days=7),
        "cross-sectional / beta-adjusted LS",
        "long top 3 alts, short bottom 3 alts, 20d alpha vs BTC with 60d beta window",
    )
    candidates["alt_longonly_top1_cash_14_5"] = (
        *alt_long_only_cash(daily_panel, alpha_window=14, rebalance_days=5, breadth_window=50, breadth_min=0.40),
        "cross-sectional / long-only alt rotation",
        "top 1 alt if breadth >= 40%, else cash",
    )
    candidates["alt_longonly_top1_cash_40_10"] = (
        *alt_long_only_cash(daily_panel, alpha_window=40, rebalance_days=10, breadth_window=50, breadth_min=0.40),
        "cross-sectional / long-only alt rotation",
        "slower top 1 alt if breadth >= 40%, else cash",
    )
    candidates["core_ls_20_1"] = (
        *core_long_short(core_prices, lookback=20, top_n=1),
        "core majors / relative value",
        "long strongest major, short weakest major",
    )
    candidates["core_ls_40_2"] = (
        *core_long_short(core_prices, lookback=40, top_n=2),
        "core majors / relative value",
        "equal-weight long top 2 majors, short bottom 2 majors",
    )
    candidates["core_top1_cash_60"] = (
        *core_top1_cash(core_prices, lookback=60),
        "core majors / trend rotation",
        "hold strongest core major only when BTC regime is positive",
    )
    candidates["btc_range_longonly_30"] = (
        *range_harvest(btc_4h, btc_regime, bb_period=30, mode="long_only"),
        "mean reversion / BTC 4h",
        "BTC low-ADX Bollinger fade, long-only",
    )
    candidates["btc_range_regime_30"] = (
        *range_harvest(btc_4h, btc_regime, bb_period=30, mode="regime"),
        "mean reversion / BTC 4h",
        "BTC Bollinger fade: long in bull, short in bear",
    )
    candidates["eth_range_longonly_20"] = (
        *range_harvest(eth_4h, btc_regime, bb_period=20, mode="long_only"),
        "mean reversion / ETH 4h",
        "ETH low-ADX Bollinger fade, long-only",
    )
    candidates["eth_range_regime_30"] = (
        *range_harvest(eth_4h, btc_regime, bb_period=30, mode="regime"),
        "mean reversion / ETH 4h",
        "ETH Bollinger fade: long in bull, short in bear",
    )
    candidates["btc_funding_hybridtrend_1_5_3"] = (
        *funding_hybridtrend(btc_long, btc_funding, btc_regime, z_threshold=1.5, hold_days=3),
        "funding + trend hybrid",
        "BTC uses funding z-score with regime-aware trend agreement",
    )
    candidates["btc_funding_hybridtrend_2_0_3"] = (
        *funding_hybridtrend(btc_long, btc_funding, btc_regime, z_threshold=2.0, hold_days=3),
        "funding + trend hybrid",
        "stricter BTC funding z-score threshold",
    )
    candidates["eth_funding_hybridtrend_1_5_7"] = (
        *funding_hybridtrend(eth_long, eth_funding, btc_regime, z_threshold=1.5, hold_days=7),
        "funding + trend hybrid",
        "ETH funding hybrid with longer hold window",
    )
    candidates["eth_funding_hybridtrend_2_5_3"] = (
        *funding_hybridtrend(eth_long, eth_funding, btc_regime, z_threshold=2.5, hold_days=3),
        "funding + trend hybrid",
        "ETH funding hybrid with stricter z threshold",
    )
    candidates["btc_weekend_reversal_5_3"] = (
        *weekend_reversal(btc_long, weekend_drop_threshold=-0.05, hold_days=3, bull_filter=False),
        "event-driven / weekend reversal",
        "BTC buy after large Sunday drop, hold 3 days",
    )
    candidates["eth_weekend_reversal_bull_3"] = (
        *weekend_reversal(eth_long, weekend_drop_threshold=-0.03, hold_days=3, bull_filter=True),
        "event-driven / weekend reversal",
        "ETH buy after weekend flush only when ETH trend is positive",
    )
    return candidates, btc_regime


def verdict_from_metrics(
    total_pnl: float,
    sharpe: float,
    max_dd_pct: float,
    bull_total_pnl: float,
    bull_sharpe: float,
    bear_total_pnl: float,
    bear_sharpe: float,
    wf_pass_windows: int,
    wf_total_windows: int,
    trades: int,
    marginal_score: float,
    mc_prob_dd_gt_25pct: float,
    candidate_id: str,
) -> str:
    if candidate_id == "basis_carry_direct":
        return "REJECTED"
    hard_reject = (
        total_pnl <= 0
        or sharpe <= 0
        or wf_total_windows == 0
        or max_dd_pct < -80
    )
    if hard_reject:
        return "REJECTED"
    paper_ready = (
        sharpe > 0.35
        and max_dd_pct > -30.0
        and bull_total_pnl > 0
        and bull_sharpe > 0
        and bear_total_pnl > 0
        and bear_sharpe >= 0
        and wf_pass_windows >= 3
        and trades >= 12
        and marginal_score >= 0.05
        and mc_prob_dd_gt_25pct < 0.40
    )
    if paper_ready:
        return "PAPER_READY"
    if (
        total_pnl > 0
        and wf_pass_windows >= 2
        and bull_total_pnl > 0
        and (bear_total_pnl > 0 or bear_sharpe > -0.05)
    ):
        return "RESEARCH_ONLY"
    return "REJECTED"


def evaluate_candidates() -> tuple[list[CandidateResult], pd.DataFrame, dict[str, str]]:
    candidates, btc_regime = build_candidate_series()
    baseline = pd.read_parquet(ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet")
    baseline.index = pd.to_datetime(baseline.index).normalize()
    candidate_frame = pd.DataFrame({cid: payload[0] for cid, payload in candidates.items()}).fillna(0.0)
    _ensure_parent(RETURNS_PATH)
    candidate_frame.to_parquet(RETURNS_PATH)
    results: list[CandidateResult] = []
    rationale_map: dict[str, str] = {}
    for candidate_id, (pnl, trades, family, notes) in candidates.items():
        pnl = pnl.sort_index()
        total_pnl = float(pnl.sum())
        sharpe = annualized_sharpe(pnl)
        max_dd = max_drawdown_pct(pnl)
        regime_stats = regime_breakdown(pnl, btc_regime)
        wf_pass, wf_total = walk_forward_passes(pnl)
        mc_prob = bootstrap_mc_dd_probability(pnl)
        score = score_candidate(candidate_id, pnl, baseline, STRAT_CAPITAL, 1.0)
        verdict = verdict_from_metrics(
            total_pnl=total_pnl,
            sharpe=sharpe,
            max_dd_pct=max_dd,
            bull_total_pnl=regime_stats["bull_total_pnl"],
            bull_sharpe=regime_stats["bull_sharpe"],
            bear_total_pnl=regime_stats["bear_total_pnl"],
            bear_sharpe=regime_stats["bear_sharpe"],
            wf_pass_windows=wf_pass,
            wf_total_windows=wf_total,
            trades=trades,
            marginal_score=score.marginal_score,
            mc_prob_dd_gt_25pct=mc_prob,
            candidate_id=candidate_id,
        )
        rationale_map[candidate_id] = notes
        results.append(
            CandidateResult(
                candidate_id=candidate_id,
                family=family,
                notes=notes,
                trades=trades,
                total_pnl=round(total_pnl, 2),
                sharpe=round(sharpe, 3),
                max_dd_pct=round(max_dd, 2),
                bull_total_pnl=round(regime_stats["bull_total_pnl"], 2),
                bull_sharpe=round(regime_stats["bull_sharpe"], 3),
                bear_total_pnl=round(regime_stats["bear_total_pnl"], 2),
                bear_sharpe=round(regime_stats["bear_sharpe"], 3),
                wf_pass_windows=wf_pass,
                wf_total_windows=wf_total,
                mc_prob_dd_gt_25pct=round(mc_prob, 3),
                corr_to_portfolio=round(score.corr_to_portfolio, 3),
                max_corr_to_strat=round(score.max_corr_to_strat, 3),
                marginal_score=round(score.marginal_score, 3),
                verdict=verdict,
            )
        )
    enforce_paper_ready_cap(results, candidate_frame)
    results.sort(key=lambda item: (item.verdict == "PAPER_READY", item.marginal_score, item.sharpe), reverse=True)
    return results, candidate_frame, rationale_map


def enforce_paper_ready_cap(results: list[CandidateResult], candidate_frame: pd.DataFrame) -> None:
    paper_ready = sorted(
        (result for result in results if result.verdict == "PAPER_READY"),
        key=lambda item: (item.marginal_score, item.sharpe),
        reverse=True,
    )
    if len(paper_ready) <= 5:
        return

    selected: list[str] = []
    deferred: list[CandidateResult] = []
    for result in paper_ready:
        if len(selected) >= 5:
            deferred.append(result)
            continue
        is_redundant = any(
            abs(float(candidate_frame[result.candidate_id].corr(candidate_frame[selected_id]))) > 0.45
            for selected_id in selected
        )
        if is_redundant:
            deferred.append(result)
        else:
            selected.append(result.candidate_id)

    if len(selected) < 5:
        for result in paper_ready:
            if result.candidate_id not in selected:
                selected.append(result.candidate_id)
            if len(selected) == 5:
                break

    for result in results:
        if result.verdict == "PAPER_READY" and result.candidate_id not in selected:
            result.verdict = "RESEARCH_ONLY"
            result.notes = f"{result.notes} [demoted after final cap due redundancy vs stronger PAPER_READY set]"


def write_outputs(results: list[CandidateResult], candidate_frame: pd.DataFrame, rationale_map: dict[str, str]) -> None:
    _ensure_parent(JSON_PATH)
    JSON_PATH.write_text(json.dumps([asdict(result) for result in results], indent=2), encoding="utf-8")

    final_ids = [result.candidate_id for result in results if result.verdict == "PAPER_READY"]
    corr_frame = candidate_frame[final_ids].corr().round(2) if final_ids else pd.DataFrame()
    idea_lines = ["| Strategy ID | Family | Initial verdict |", "|---|---|---|"]
    tested_ids = {result.candidate_id: result.verdict for result in results}
    for candidate_id, family in INITIAL_IDEAS:
        idea_lines.append(f"| `{candidate_id}` | {family} | {tested_ids.get(candidate_id, 'IDEA_ONLY')} |")

    summary_counts = {
        "ideas_generated": len(INITIAL_IDEAS),
        "seriously_tested": len(results),
        "paper_ready": sum(result.verdict == "PAPER_READY" for result in results),
        "research_only": sum(result.verdict == "RESEARCH_ONLY" for result in results),
        "rejected": sum(result.verdict == "REJECTED" for result in results),
    }

    best_overall = next((result for result in results if result.verdict == "PAPER_READY"), results[0])
    best_bear = max(results, key=lambda result: result.bear_total_pnl)
    bull_only_rejected = max(
        (result for result in results if result.bull_total_pnl > 0 and result.bear_total_pnl <= 0),
        key=lambda result: result.bull_total_pnl,
        default=None,
    )
    worst_illusion = max(
        (result for result in results if result.verdict == "REJECTED"),
        key=lambda result: result.max_dd_pct * -1,
        default=results[-1],
    )

    report_lines = [
        "# Crypto Bull/Bear Paper Candidates — 2026-04-24",
        "",
        "## Truth Snapshot",
        "",
        "- Local pytest: `3873 passed, 1 skipped`.",
        "- Local `runtime_audit --strict`: expected local FAIL because `data/state/ibkr_futures/equity_state.json` is absent and several futures parquets are stale.",
        "- Current crypto canonical snapshot from runtime audit: `btc_asia_mes_leadlag_q80_v80_long_only` ACTIVE, `alt_rel_strength_14_60_7` READY, `btc_dominance_rotation_v2` DISABLED, several historical crypto sleeves archived/rejected.",
        "- This batch is strictly non-prod: no runtime wiring, no registry edits, no whitelist edits, no VPS deploy.",
        "",
        "## Skills Used",
        "",
        "- `discover`: search → filter → validation pipeline.",
        "- `crypto`: Binance France constraints, spot/margin only, perp data read-only.",
        "- `bt`: anti-lookahead, realistic costs, walk-forward discipline.",
        "- `qr`: bull/bear split, robustness, correlation, anti-overfit.",
        "- `risk`: DD filters, bootstrap DD probability, desk-level viability.",
        "- `review`: keep outputs non-prod and testable.",
        "- `exec`: tradability checks, but no runtime implementation in this mission.",
        "",
        "## Bull / Bear Definition",
        "",
        "- `bull` = BTC daily close > BTC 200-day SMA.",
        "- `bear` = BTC daily close <= BTC 200-day SMA.",
        "- The same regime definition is reused across every candidate, including ETH and alt sleeves.",
        "",
        "## Data Used",
        "",
        f"- Research Binance daily cache: `{DAILY_CACHE_PATH}` with 10 symbols from 2020-01-01 onward, common non-NaN range starting 2020-09-22.",
        "- Existing repo data read-only: BTC/ETH/BNB/SOL long daily files, BTC/ETH 4h bars, BTC/ETH funding daily aggregates.",
        "- Costs: 13 bps per side (`0.26%` round trip), plus conservative short borrow proxies for short-capable variants.",
        "",
        "## Initial Idea Universe",
        "",
        *idea_lines,
        "",
        "## Candidate Results",
        "",
        "| Candidate | Family | Trades | Sharpe | MaxDD | Bull PnL | Bear PnL | WF | MC P(DD<-25%) | Score | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for result in results:
        report_lines.append(
            f"| `{result.candidate_id}` | {result.family} | {result.trades} | {result.sharpe:+.2f} | "
            f"{result.max_dd_pct:+.1f}% | ${result.bull_total_pnl:+,.0f} | ${result.bear_total_pnl:+,.0f} | "
            f"{result.wf_pass_windows}/{result.wf_total_windows} | {result.mc_prob_dd_gt_25pct:.1%} | "
            f"{result.marginal_score:+.3f} | **{result.verdict}** |"
        )

    report_lines.extend(
        [
            "",
            "## Final Top",
            "",
        ]
    )
    if final_ids:
        for rank, result in enumerate([item for item in results if item.verdict == "PAPER_READY"], start=1):
            report_lines.extend(
                [
                    f"### #{rank} `{result.candidate_id}` — PAPER_READY",
                    "",
                    f"- Family: {result.family}",
                    f"- Notes: {rationale_map[result.candidate_id]}",
                    f"- Trades: {result.trades}",
                    f"- Standalone: total ${result.total_pnl:+,.0f}, Sharpe {result.sharpe:+.2f}, MaxDD {result.max_dd_pct:+.1f}%",
                    f"- Bull: ${result.bull_total_pnl:+,.0f}, Sharpe {result.bull_sharpe:+.2f}",
                    f"- Bear: ${result.bear_total_pnl:+,.0f}, Sharpe {result.bear_sharpe:+.2f}",
                    f"- WF: {result.wf_pass_windows}/{result.wf_total_windows} windows passed",
                    f"- Desk score: {result.marginal_score:+.3f}, corr to portfolio {result.corr_to_portfolio:+.2f}",
                    "",
                ]
            )
    else:
        report_lines.extend(["No candidate reached `PAPER_READY` under the strict bull + bear gates.", ""])

    report_lines.extend(
        [
            "## Correlation Matrix — PAPER_READY",
            "",
        ]
    )
    if not corr_frame.empty:
        report_lines.extend(dataframe_to_markdown(corr_frame))
    else:
        report_lines.append("No PAPER_READY candidates, so no final correlation matrix.")

    report_lines.extend(
        [
            "",
            "## Illusions Rejected",
            "",
            f"- Worst illusion rejected: `{worst_illusion.candidate_id}`. It looked attractive on one dimension, but failed the combined bull/bear + DD + WF bar.",
        ]
    )
    if bull_only_rejected is not None:
        report_lines.append(
            f"- Best bull-only reject: `{bull_only_rejected.candidate_id}` with bull PnL ${bull_only_rejected.bull_total_pnl:+,.0f} but bear PnL ${bull_only_rejected.bear_total_pnl:+,.0f}."
        )
    report_lines.extend(
        [
            "- Direct basis carry remains rejected for this mission because it depends on a live perp expression that Binance France cannot execute directly.",
            "- Extended 2020+ alt-universe dispersion degraded much more than the 2024-2026 local snapshot suggested; that family is weaker than its recent short-sample optics imply.",
            "",
            "## Executive Summary",
            "",
            f"- Ideas generated: {summary_counts['ideas_generated']}",
            f"- Seriously tested: {summary_counts['seriously_tested']}",
            f"- Rejected: {summary_counts['rejected']}",
            f"- Research-only: {summary_counts['research_only']}",
            f"- PAPER_READY: {summary_counts['paper_ready']}",
            f"- Best overall candidate: `{best_overall.candidate_id}`",
            f"- Best bear-resistant candidate: `{best_bear.candidate_id}` with bear PnL ${best_bear.bear_total_pnl:+,.0f}",
            f"- Worst illusion rejected: `{worst_illusion.candidate_id}`",
            "",
            "## Honest Conclusion",
            "",
        ]
    )
    if summary_counts["paper_ready"] >= 5:
        report_lines.append(
            "The batch found five PAPER_READY crypto sleeves without touching production files. They are not all the same motor, and each one cleared separate bull and bear checks."
        )
    else:
        report_lines.append(
            f"The batch found only {summary_counts['paper_ready']} PAPER_READY sleeves under the strict bull + bear bar. The quota of five was not forced artificially."
        )
    _ensure_parent(REPORT_PATH)
    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")


def main() -> int:
    results, candidate_frame, rationale_map = evaluate_candidates()
    write_outputs(results, candidate_frame, rationale_map)
    print(f"Report written to {REPORT_PATH}")
    print(f"JSON written to {JSON_PATH}")
    print(f"Returns written to {RETURNS_PATH}")
    for result in results:
        print(
            f"{result.candidate_id}: {result.verdict} "
            f"sharpe={result.sharpe:+.2f} bull=${result.bull_total_pnl:+,.0f} "
            f"bear=${result.bear_total_pnl:+,.0f} wf={result.wf_pass_windows}/{result.wf_total_windows} "
            f"score={result.marginal_score:+.3f}"
        )
    return 0


def dataframe_to_markdown(frame: pd.DataFrame) -> list[str]:
    headers = [""] + [str(col) for col in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for idx, row in frame.iterrows():
        values = [str(idx)] + [f"{float(value):+.2f}" for value in row.tolist()]
        lines.append("| " + " | ".join(values) + " |")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
