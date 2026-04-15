#!/usr/bin/env python3
"""Backtest 12 US large-cap strategies on S&P 500 universe (5 years daily).

10 strats from the research brief + 2 market-neutral additions (BAB, Low Vol).

Costs: 3 bps per side (Alpaca $0 commission + 2 bps PFOF spread + 1 bps slippage).
Output: reports/us_research/{summary.csv, report.md, trades_<strat>.csv}

Run: python scripts/backtest_us_stocks.py
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest_us")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "us_stocks"
OUT_DIR = ROOT / "reports" / "us_research"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COST_PER_SIDE = 0.0003          # 3 bps per side
COST_ROUND_TRIP = 2 * COST_PER_SIDE


@dataclass
class Trade:
    strat: str
    ticker: str
    entry_date: str
    exit_date: str
    side: str
    entry_px: float
    exit_px: float
    pnl_gross: float
    pnl_net: float
    bars: int


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)


def make_trade(strat, ticker, entry_date, exit_date, side, entry_px, exit_px, bars) -> Trade:
    if side == "LONG":
        gross = (exit_px - entry_px) / entry_px
    else:
        gross = (entry_px - exit_px) / entry_px
    net = gross - COST_ROUND_TRIP
    return Trade(
        strat=strat, ticker=ticker,
        entry_date=_fmt_date(entry_date), exit_date=_fmt_date(exit_date),
        side=side, entry_px=float(entry_px), exit_px=float(exit_px),
        pnl_gross=float(gross), pnl_net=float(net), bars=int(bars),
    )


# ==================================================================
# Data load + precompute
# ==================================================================
def _trading_month_ends(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Return last trading day of each (year, month) present in idx."""
    s = pd.Series(idx, index=idx)
    last = s.groupby([idx.year, idx.month]).last()
    return pd.DatetimeIndex(last.values)


def load_universe() -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    meta = pd.read_csv(DATA_DIR / "_metadata.csv")
    universe = meta[meta["pass_all"]]["ticker"].tolist()
    data = {}
    for t in universe:
        f = DATA_DIR / f"{t}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df.index = pd.to_datetime(df.index)
        data[t] = df
    spy = pd.read_parquet(DATA_DIR / "SPY.parquet")
    spy.index = pd.to_datetime(spy.index)
    return data, meta, spy


def precompute(data: dict) -> None:
    for t, df in data.items():
        close = df["adj_close"].astype(float)
        vol = df["volume"].astype(float)
        df["ret"] = close.pct_change()
        df["sma20"] = close.rolling(20).mean()
        df["sma200"] = close.rolling(200).mean()
        std20 = close.rolling(20).std()
        df["bb_up"] = df["sma20"] + 2 * std20
        df["bb_dn"] = df["sma20"] - 2 * std20
        df["bb_w"] = (df["bb_up"] - df["bb_dn"]) / df["sma20"]
        df["bb_w_pct10"] = df["bb_w"].rolling(100).quantile(0.10)
        df["vol20"] = vol.rolling(20).mean()
        df["high52w"] = close.rolling(252).max()
        delta = close.diff()
        up = delta.clip(lower=0)
        dn = -delta.clip(upper=0)
        avg_up = up.rolling(2).mean()
        avg_dn = dn.rolling(2).mean()
        rs = avg_up / avg_dn.replace(0, np.nan)
        df["rsi2"] = 100 - 100 / (1 + rs)


# ==================================================================
# STRAT 1: PEAD (price-based proxy — vol spike + big move)
# ==================================================================
def strat_pead(data: dict, cooldown=30, hold=20, sl=-0.05, tp=0.10) -> list[Trade]:
    trades = []
    for t, df in data.items():
        if len(df) < 250:
            continue
        ret = df["ret"]
        vol_spike = df["volume"] > 2 * df["vol20"]
        big_move = ret.abs() > 0.03
        events = df.index[vol_spike & big_move].tolist()
        last_idx = -10**9
        for ed in events:
            idx = df.index.get_loc(ed)
            if idx - last_idx < cooldown:
                continue
            if idx + hold >= len(df):
                continue
            day_ret = ret.iloc[idx]
            side = "LONG" if day_ret > 0.03 else ("SHORT" if day_ret < -0.03 else None)
            if side is None:
                continue
            entry_px = df["close"].iloc[idx]
            exit_idx = idx + hold
            for j in range(1, hold + 1):
                px = df["close"].iloc[idx + j]
                r = (px - entry_px) / entry_px if side == "LONG" else (entry_px - px) / entry_px
                if r <= sl or r >= tp:
                    exit_idx = idx + j
                    break
            trades.append(make_trade(
                "pead", t, ed, df.index[exit_idx],
                side, entry_px, df["close"].iloc[exit_idx], exit_idx - idx,
            ))
            last_idx = idx
    return trades


# ==================================================================
# STRAT 2: Sector rotation (long best / short worst sector, monthly)
# ==================================================================
def strat_sector_rotation(data: dict, meta: pd.DataFrame, lookback=63) -> list[Trade]:
    sector_map = dict(zip(meta["ticker"], meta["sector"]))
    tickers = [t for t in data if t in sector_map and not pd.isna(sector_map[t])]
    all_dates = sorted(set().union(*[set(data[t].index) for t in tickers]))
    all_dates = pd.DatetimeIndex(all_dates)
    px = pd.DataFrame({t: data[t]["close"] for t in tickers}).reindex(all_dates).ffill()

    trades = []
    month_ends = _trading_month_ends(px.index)
    for i in range(3, len(month_ends) - 1):
        rebal = month_ends[i]
        lb = month_ends[i - 3]
        try:
            px_now = px.loc[rebal]
            px_bk = px.loc[lb]
        except KeyError:
            continue
        stock_ret = (px_now / px_bk) - 1
        sec_ret = {}
        for s in set(sector_map.values()):
            ts = [t for t in tickers if sector_map.get(t) == s]
            rets = stock_ret[ts].dropna()
            if len(rets) >= 3:
                sec_ret[s] = rets.mean()
        if len(sec_ret) < 2:
            continue
        sec_sorted = sorted(sec_ret.items(), key=lambda x: -x[1])
        top_sec, bot_sec = sec_sorted[0][0], sec_sorted[-1][0]
        top_stocks = sorted(
            [(t, stock_ret[t]) for t in tickers if sector_map[t] == top_sec and not pd.isna(stock_ret[t])],
            key=lambda x: -x[1],
        )
        bot_stocks = sorted(
            [(t, stock_ret[t]) for t in tickers if sector_map[t] == bot_sec and not pd.isna(stock_ret[t])],
            key=lambda x: x[1],
        )
        if not top_stocks or not bot_stocks:
            continue
        long_t, short_t = top_stocks[0][0], bot_stocks[0][0]
        exit_date = month_ends[i + 1]
        for t, side in [(long_t, "LONG"), (short_t, "SHORT")]:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_date, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade(
                "sector_rot", t, rebal, exit_date, side, ep, xp, (exit_date - rebal).days,
            ))
    return trades


# ==================================================================
# STRAT 3: RSI2 Mean Reversion (Connors, long-only)
# ==================================================================
def strat_rsi2(data: dict, rsi_entry=5, rsi_exit=65, sl=-0.03, max_hold=7) -> list[Trade]:
    trades = []
    for t, df in data.items():
        if len(df) < 250:
            continue
        cond = (df["rsi2"] < rsi_entry) & (df["close"] > df["sma200"])
        entries = df.index[cond].tolist()
        last_idx = -10**9
        for ed in entries:
            idx = df.index.get_loc(ed)
            if idx - last_idx < 5:
                continue
            if idx + max_hold + 1 >= len(df):
                continue
            entry_px = df["close"].iloc[idx]
            exit_idx = idx + max_hold
            for j in range(1, max_hold + 1):
                px = df["close"].iloc[idx + j]
                r = (px - entry_px) / entry_px
                if r <= sl:
                    exit_idx = idx + j
                    break
                if df["rsi2"].iloc[idx + j] > rsi_exit:
                    exit_idx = idx + j
                    break
            trades.append(make_trade(
                "rsi2_mr", t, ed, df.index[exit_idx], "LONG",
                entry_px, df["close"].iloc[exit_idx], exit_idx - idx,
            ))
            last_idx = idx
    return trades


# ==================================================================
# STRAT 4: Gap & Go (daily proxy — open-to-close)
# ==================================================================
def strat_gap_go(data: dict, gap_min=0.015, prev_min=0.005) -> list[Trade]:
    trades = []
    for t, df in data.items():
        if len(df) < 250:
            continue
        op = df["open"].values
        cl = df["close"].values
        prev_cl = np.roll(cl, 1)
        prev_prev_cl = np.roll(cl, 2)
        gap = (op - prev_cl) / prev_cl
        prev_ret = (prev_cl - prev_prev_cl) / prev_prev_cl
        for i in range(3, len(df)):
            g, pr = gap[i], prev_ret[i]
            side = None
            if g > gap_min and pr > prev_min:
                side = "LONG"
            elif g < -gap_min and pr < -prev_min:
                side = "SHORT"
            if side is None:
                continue
            ep = op[i]
            xp = cl[i]
            trades.append(make_trade(
                "gap_go", t, df.index[i], df.index[i], side, ep, xp, 1,
            ))
    return trades


# ==================================================================
# STRAT 5: Dividend Capture (5 days pre-ex, exit T-1)
# ==================================================================
def strat_dividend(data: dict, days_before=5) -> list[Trade]:
    trades = []
    for t, df in data.items():
        if "dividends" not in df.columns or len(df) < 250:
            continue
        ex_dates = df.index[df["dividends"] > 0].tolist()
        for ed in ex_dates:
            idx = df.index.get_loc(ed)
            if idx - days_before < 0 or idx - 1 < 0:
                continue
            entry_idx = idx - days_before
            exit_idx = idx - 1
            ep = df["close"].iloc[entry_idx]
            xp = df["close"].iloc[exit_idx]
            trades.append(make_trade(
                "dividend", t, df.index[entry_idx], df.index[exit_idx],
                "LONG", ep, xp, exit_idx - entry_idx,
            ))
    return trades


# ==================================================================
# STRAT 6: 52-Week High Momentum (long)
# ==================================================================
def strat_high_52w(data: dict, hold=20, sl=-0.05, tp=0.10, cooldown=30) -> list[Trade]:
    trades = []
    for t, df in data.items():
        if len(df) < 300:
            continue
        is_new_high = df["close"] == df["high52w"]
        entries = df.index[is_new_high].tolist()
        last_idx = -10**9
        for ed in entries:
            idx = df.index.get_loc(ed)
            if idx - last_idx < cooldown:
                continue
            if idx + hold >= len(df):
                continue
            ep = df["close"].iloc[idx]
            exit_idx = idx + hold
            for j in range(1, hold + 1):
                px = df["close"].iloc[idx + j]
                r = (px - ep) / ep
                if r <= sl or r >= tp:
                    exit_idx = idx + j
                    break
            trades.append(make_trade(
                "high_52w", t, ed, df.index[exit_idx], "LONG",
                ep, df["close"].iloc[exit_idx], exit_idx - idx,
            ))
            last_idx = idx
    return trades


# ==================================================================
# STRAT 7: Relative Strength vs SPY (cross-sectional MN, top5/bot5)
# ==================================================================
def strat_rs_spy(data: dict, spy: pd.DataFrame, top_n=5) -> list[Trade]:
    tickers = list(data.keys())
    all_dates = sorted(set().union(*[set(data[t].index) for t in tickers]))
    all_dates = pd.DatetimeIndex(all_dates)
    px = pd.DataFrame({t: data[t]["close"] for t in tickers}).reindex(all_dates).ffill()
    spy_px = spy["close"].reindex(all_dates).ffill()

    trades = []
    month_ends = _trading_month_ends(px.index)
    for i in range(1, len(month_ends) - 1):
        rebal = month_ends[i]
        prev = month_ends[i - 1]
        exit_d = month_ends[i + 1]
        try:
            stock_ret = (px.loc[rebal] / px.loc[prev]) - 1
            spy_ret = (spy_px.loc[rebal] / spy_px.loc[prev]) - 1
        except KeyError:
            continue
        alpha = (stock_ret - spy_ret).dropna()
        if len(alpha) < 20:
            continue
        sorted_alpha = alpha.sort_values(ascending=False)
        longs = sorted_alpha.head(top_n).index.tolist()
        shorts = sorted_alpha.tail(top_n).index.tolist()
        for t in longs:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade(
                "rs_spy", t, rebal, exit_d, "LONG", ep, xp, (exit_d - rebal).days,
            ))
        for t in shorts:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade(
                "rs_spy", t, rebal, exit_d, "SHORT", ep, xp, (exit_d - rebal).days,
            ))
    return trades


# ==================================================================
# STRAT 8: Vol Contraction Breakout (BB squeeze)
# ==================================================================
def strat_vol_contract(data: dict, max_hold=10) -> list[Trade]:
    trades = []
    for t, df in data.items():
        if len(df) < 250:
            continue
        squeeze = df["bb_w"] < df["bb_w_pct10"]
        up_break = (df["close"] > df["bb_up"]) & squeeze.shift(1)
        dn_break = (df["close"] < df["bb_dn"]) & squeeze.shift(1)
        events_up = df.index[up_break.fillna(False)].tolist()
        events_dn = df.index[dn_break.fillna(False)].tolist()
        entries = [(d, "LONG") for d in events_up] + [(d, "SHORT") for d in events_dn]
        entries.sort(key=lambda x: x[0])
        last_idx = -10**9
        for ed, side in entries:
            idx = df.index.get_loc(ed)
            if idx - last_idx < 5:
                continue
            if idx + max_hold >= len(df):
                continue
            ep = df["close"].iloc[idx]
            mid = df["sma20"].iloc[idx]
            sl = mid
            tp = 2 * ep - mid if side == "LONG" else mid - 2 * (mid - ep)
            exit_idx = idx + max_hold
            for j in range(1, max_hold + 1):
                px = df["close"].iloc[idx + j]
                if side == "LONG":
                    if px <= sl or px >= tp:
                        exit_idx = idx + j
                        break
                else:
                    if px >= sl or px <= tp:
                        exit_idx = idx + j
                        break
            trades.append(make_trade(
                "vol_contract", t, ed, df.index[exit_idx], side,
                ep, df["close"].iloc[exit_idx], exit_idx - idx,
            ))
            last_idx = idx
    return trades


# ==================================================================
# STRAT 9: Pairs Trading (predefined large-cap pairs)
# ==================================================================
PAIRS = [
    ("KO", "PEP"), ("XOM", "CVX"), ("GS", "MS"), ("UPS", "FDX"),
    ("PG", "CL"), ("MRK", "PFE"), ("LMT", "RTX"), ("HD", "LOW"),
    ("V", "MA"), ("ADI", "TXN"),
]

def strat_pairs(data: dict, z_entry=2.0, z_exit=0.5, max_hold=15) -> list[Trade]:
    trades = []
    for a, b in PAIRS:
        if a not in data or b not in data:
            continue
        dfa, dfb = data[a], data[b]
        idx = dfa.index.intersection(dfb.index)
        if len(idx) < 100:
            continue
        la = np.log(dfa["close"].reindex(idx).astype(float))
        lb = np.log(dfb["close"].reindex(idx).astype(float))
        # Simple spread (no rolling regression — use ratio for speed)
        spread = la - lb
        m = spread.rolling(60).mean()
        s = spread.rolling(60).std()
        z = (spread - m) / s
        in_pos = False
        entry_i = None
        side_a = None
        for i in range(60, len(idx)):
            zi = z.iloc[i]
            if not in_pos:
                if zi > z_entry:
                    side_a = "SHORT"  # short A, long B
                    entry_i = i
                    in_pos = True
                elif zi < -z_entry:
                    side_a = "LONG"
                    entry_i = i
                    in_pos = True
            else:
                hold = i - entry_i
                if abs(zi) < z_exit or hold >= max_hold:
                    for t, s_t in [(a, side_a), (b, "SHORT" if side_a == "LONG" else "LONG")]:
                        ep = float(data[t]["close"].loc[idx[entry_i]])
                        xp = float(data[t]["close"].loc[idx[i]])
                        trades.append(make_trade(
                            f"pairs_{a}_{b}", t, idx[entry_i], idx[i], s_t, ep, xp, hold,
                        ))
                    in_pos = False
    # Rename strat on all trades to just "pairs"
    for tr in trades:
        tr.strat = "pairs"
    return trades


# ==================================================================
# STRAT 10: Turn of Month (top momentum longs, last day → 3rd day)
# ==================================================================
def strat_tom(data: dict, n_stocks=10) -> list[Trade]:
    tickers = list(data.keys())
    all_dates = sorted(set().union(*[set(data[t].index) for t in tickers]))
    all_dates = pd.DatetimeIndex(all_dates)
    px = pd.DataFrame({t: data[t]["close"] for t in tickers}).reindex(all_dates).ffill()

    trades = []
    # Group by year-month
    month_groups = px.groupby([px.index.year, px.index.month])
    month_last = [g.index[-1] for _, g in month_groups]  # last trading day of month
    month_first = [g.index[0] for _, g in month_groups]  # first trading day

    for i in range(1, len(month_last) - 1):
        entry_d = month_last[i]
        # Exit = 3rd trading day of next month = month_first[i+1] advanced 2 bars
        next_month_first = month_first[i + 1]
        next_first_idx = all_dates.get_loc(next_month_first)
        exit_idx = min(next_first_idx + 2, len(all_dates) - 1)
        exit_d = all_dates[exit_idx]
        # 1-month momentum for ranking
        try:
            ret_1m = (px.loc[entry_d] / px.loc[month_last[i - 1]]) - 1
        except KeyError:
            continue
        top = ret_1m.sort_values(ascending=False).head(n_stocks).index.tolist()
        for t in top:
            ep = px.loc[entry_d, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade(
                "tom", t, entry_d, exit_d, "LONG", ep, xp, (exit_d - entry_d).days,
            ))
    return trades


# ==================================================================
# STRAT 11: Betting Against Beta (long low-beta, short high-beta, MN)
# ==================================================================
def strat_bab(data: dict, spy: pd.DataFrame, n_each=10) -> list[Trade]:
    tickers = list(data.keys())
    all_dates = sorted(set().union(*[set(data[t].index) for t in tickers]))
    all_dates = pd.DatetimeIndex(all_dates)
    px = pd.DataFrame({t: data[t]["close"] for t in tickers}).reindex(all_dates).ffill()
    spy_px = spy["close"].reindex(all_dates).ffill()
    rets = px.pct_change()
    spy_ret = spy_px.pct_change()

    trades = []
    month_ends = _trading_month_ends(px.index)
    for i in range(6, len(month_ends) - 1):
        rebal = month_ends[i]
        exit_d = month_ends[i + 1]
        window_start = month_ends[i - 6]
        rr = rets.loc[window_start:rebal]
        sr = spy_ret.loc[window_start:rebal]
        if len(rr) < 60:
            continue
        var_m = sr.var()
        if var_m == 0:
            continue
        betas = rr.apply(lambda c: c.cov(sr) / var_m if c.notna().sum() > 30 else np.nan)
        betas = betas.dropna()
        if len(betas) < 2 * n_each:
            continue
        sorted_b = betas.sort_values()
        longs = sorted_b.head(n_each).index.tolist()
        shorts = sorted_b.tail(n_each).index.tolist()
        for t in longs:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade("bab", t, rebal, exit_d, "LONG", ep, xp, (exit_d - rebal).days))
        for t in shorts:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade("bab", t, rebal, exit_d, "SHORT", ep, xp, (exit_d - rebal).days))
    return trades


# ==================================================================
# STRAT 12: Low Vol Long / High Vol Short (MN)
# ==================================================================
def strat_low_vol(data: dict, n_each=10) -> list[Trade]:
    tickers = list(data.keys())
    all_dates = sorted(set().union(*[set(data[t].index) for t in tickers]))
    all_dates = pd.DatetimeIndex(all_dates)
    px = pd.DataFrame({t: data[t]["close"] for t in tickers}).reindex(all_dates).ffill()
    rets = px.pct_change()

    trades = []
    month_ends = _trading_month_ends(px.index)
    for i in range(3, len(month_ends) - 1):
        rebal = month_ends[i]
        exit_d = month_ends[i + 1]
        window_start = month_ends[i - 3]
        rr = rets.loc[window_start:rebal]
        vol60 = rr.std() * np.sqrt(252)
        vol60 = vol60.dropna()
        if len(vol60) < 2 * n_each:
            continue
        sorted_v = vol60.sort_values()
        longs = sorted_v.head(n_each).index.tolist()
        shorts = sorted_v.tail(n_each).index.tolist()
        for t in longs:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade("low_vol", t, rebal, exit_d, "LONG", ep, xp, (exit_d - rebal).days))
        for t in shorts:
            ep = px.loc[rebal, t]
            xp = px.loc[exit_d, t]
            if pd.isna(ep) or pd.isna(xp):
                continue
            trades.append(make_trade("low_vol", t, rebal, exit_d, "SHORT", ep, xp, (exit_d - rebal).days))
    return trades


# ==================================================================
# Stats + report
# ==================================================================
def compute_stats(trades: list[Trade], strat_name: str) -> dict:
    if not trades:
        return {"strat": strat_name, "n_trades": 0, "win_rate": 0,
                "avg_pnl_pct": 0, "total_pnl_pct": 0, "sharpe": 0,
                "profit_factor": 0, "max_dd_pct": 0, "avg_bars": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_date")
    net = df["pnl_net"].values
    eq = net.cumsum()
    wr = float((net > 0).mean())
    avg = float(net.mean())
    tot = float(net.sum())
    daily = df.groupby(df["exit_date"].dt.date)["pnl_net"].sum()
    if len(daily) > 1 and daily.std() > 0:
        sharpe = float(daily.mean() / daily.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    wins_sum = float(df[df["pnl_net"] > 0]["pnl_net"].sum())
    losses_sum = float(-df[df["pnl_net"] < 0]["pnl_net"].sum())
    pf = wins_sum / losses_sum if losses_sum > 0 else float("inf")
    peak = np.maximum.accumulate(eq)
    mdd = float((eq - peak).min())
    return {
        "strat": strat_name,
        "n_trades": len(df),
        "win_rate": round(wr, 3),
        "avg_pnl_pct": round(avg * 100, 3),
        "total_pnl_pct": round(tot * 100, 2),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else 999.0,
        "max_dd_pct": round(mdd * 100, 2),
        "avg_bars": round(float(df["bars"].mean()), 1),
    }


def write_report(summary: pd.DataFrame) -> None:
    lines = [
        "# US Stock Research — 12 strats backtest",
        "",
        f"Universe: 496 S&P 500 stocks, 5 years daily (2021-03 → 2026-04)",
        f"Costs: 3 bps/side ($0 commission Alpaca + 2 bps PFOF + 1 bps slippage)",
        "",
        "## Summary",
        "",
        "| Strat | Trades | WR | Avg/trade | Total | Sharpe | PF | MaxDD | Avg bars |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in summary.iterrows():
        lines.append(
            f"| {r['strat']} | {int(r['n_trades'])} | {r['win_rate']:.0%} | "
            f"{r['avg_pnl_pct']:.2f}% | {r['total_pnl_pct']:.1f}% | "
            f"{r['sharpe']:.2f} | {r['profit_factor']:.2f} | {r['max_dd_pct']:.1f}% | "
            f"{r['avg_bars']:.1f} |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- PEAD uses a price-action proxy (vol spike + big move), not real EPS surprise — academic edge is similar but less precise.")
    lines.append("- Gap&Go uses daily bars → exit = same-day close (no intraday SL/TP). Realistic only as a first screen.")
    lines.append("- Market-neutral strats: sector_rot, rs_spy, pairs, bab, low_vol.")
    lines.append("- Each trade = flat unit (1% of notional). Portfolio sizing + WF + slippage stress = next pass.")
    lines.append("")
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    t_start = time.time()
    logger.info("Loading universe…")
    data, meta, spy = load_universe()
    logger.info(f"  loaded {len(data)} tickers")
    logger.info("Pre-computing indicators…")
    precompute(data)

    strats = [
        ("pead", lambda: strat_pead(data)),
        ("sector_rot", lambda: strat_sector_rotation(data, meta)),
        ("rsi2_mr", lambda: strat_rsi2(data)),
        ("gap_go", lambda: strat_gap_go(data)),
        ("dividend", lambda: strat_dividend(data)),
        ("high_52w", lambda: strat_high_52w(data)),
        ("rs_spy", lambda: strat_rs_spy(data, spy)),
        ("vol_contract", lambda: strat_vol_contract(data)),
        ("pairs", lambda: strat_pairs(data)),
        ("tom", lambda: strat_tom(data)),
        ("bab", lambda: strat_bab(data, spy)),
        ("low_vol", lambda: strat_low_vol(data)),
    ]

    all_stats = []
    for name, fn in strats:
        t0 = time.time()
        logger.info(f"Running {name}…")
        try:
            trades = fn()
        except Exception as e:
            logger.exception(f"{name} failed: {e}")
            trades = []
        dt = time.time() - t0
        stats = compute_stats(trades, name)
        stats["runtime_s"] = round(dt, 1)
        all_stats.append(stats)
        logger.info(
            f"  {name}: n={stats['n_trades']} WR={stats['win_rate']:.0%} "
            f"tot={stats['total_pnl_pct']:.1f}% Sh={stats['sharpe']:.2f} "
            f"MDD={stats['max_dd_pct']:.1f}% ({dt:.1f}s)"
        )
        if trades:
            df_tr = pd.DataFrame([asdict(t) for t in trades])
            df_tr.to_csv(OUT_DIR / f"trades_{name}.csv", index=False)

    summary = pd.DataFrame(all_stats)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    write_report(summary)

    logger.info("")
    logger.info(f"Total runtime: {(time.time() - t_start) / 60:.1f} min")
    logger.info(f"Output: {OUT_DIR}")
    logger.info("")
    logger.info("=== SUMMARY ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
