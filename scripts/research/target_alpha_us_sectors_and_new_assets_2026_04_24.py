#!/usr/bin/env python3
"""Target alpha research: US sectors + new assets (non-prod only).

Mission 2026-04-24:
  - search the TARGET of the desk, not today's runtime
  - compare US sectors / long-short / pair sectors / macro assets
  - stay outside prod-used files

Outputs:
  - data/research/target_alpha_us_sectors_2026_04_24_prices.parquet
  - reports/research/target_alpha_us_sectors_and_new_assets_2026-04-24_metrics.json
  - reports/research/target_alpha_us_sectors_and_new_assets_2026-04-24_returns.parquet
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data" / "research"
REPORT_DIR = ROOT / "reports" / "research"
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

ETF_CACHE = DATA_DIR / "target_alpha_us_sectors_2026_04_24_prices.parquet"
METRICS_OUT = REPORT_DIR / "target_alpha_us_sectors_and_new_assets_2026-04-24_metrics.json"
RETURNS_OUT = REPORT_DIR / "target_alpha_us_sectors_and_new_assets_2026-04-24_returns.parquet"

SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY"]
MACRO_ETFS = ["TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]
DOWNLOAD_UNIVERSE = sorted(set(SECTOR_ETFS + MACRO_ETFS))

ETF_RT_COST_PCT = 0.0010
ETF_SHORT_BORROW_ANNUAL = 0.0050
ETF_SHORT_BORROW_DAILY = ETF_SHORT_BORROW_ANNUAL / 252.0
LEG_NOTIONAL = 1_000.0
LONG_ONLY_NOTIONAL = 2_000.0


def load_baseline() -> pd.DataFrame:
    path = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).normalize()
    return df.sort_index()


def download_or_load_etfs(start: str = "2018-01-01") -> pd.DataFrame:
    if ETF_CACHE.exists():
        return pd.read_parquet(ETF_CACHE)

    import yfinance as yf

    raw = yf.download(
        DOWNLOAD_UNIVERSE,
        start=start,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    frames = {}
    for sym in DOWNLOAD_UNIVERSE:
        if sym not in raw.columns.get_level_values(0):
            continue
        close = raw[sym]["Close"].copy()
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        frames[sym] = close.rename(sym)
    prices = pd.DataFrame(frames).sort_index().dropna(how="all")
    prices.to_parquet(ETF_CACHE)
    return prices


def compute_metrics(pnl: pd.Series) -> dict:
    pnl = pnl.dropna()
    if len(pnl) < 50:
        return {"n_days": int(len(pnl)), "error": "too_few_days"}
    eq = 10_000.0 + pnl.cumsum()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    sd = pnl.std()
    sharpe = float(pnl.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    final = float(eq.iloc[-1])
    years = len(pnl) / 252.0
    cagr = (final / 10_000.0) ** (1 / years) - 1 if years > 0 and final > 0 else -1.0
    return {
        "n_days": int(len(pnl)),
        "n_years": round(years, 2),
        "total_pnl": round(float(pnl.sum()), 2),
        "sharpe": round(sharpe, 3),
        "cagr_pct": round(cagr * 100, 2),
        "max_dd_pct": round(float(dd.min()) * 100, 2),
        "active_days": int((pnl != 0).sum()),
    }


def walk_forward(pnl: pd.Series, n_splits: int = 5) -> dict:
    pnl = pnl.dropna()
    if len(pnl) < 400:
        return {"n_splits": 0, "validated": False, "ratio": 0.0, "windows": []}
    step = len(pnl) // (n_splits + 1)
    windows = []
    for i in range(1, n_splits + 1):
        oos = pnl.iloc[i * step:min((i + 1) * step, len(pnl))]
        if len(oos) < 40:
            continue
        sd = oos.std()
        sharpe = float(oos.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
        total = float(oos.sum())
        windows.append(
            {
                "window": i,
                "sharpe": round(sharpe, 3),
                "pnl": round(total, 2),
                "profitable": bool(total > 0),
            }
        )
    profitable = sum(1 for w in windows if w["profitable"])
    ratio = profitable / len(windows) if windows else 0.0
    return {
        "n_splits": len(windows),
        "profitable": profitable,
        "ratio": round(ratio, 2),
        "validated": ratio >= 0.5 if windows else False,
        "windows": windows,
    }


def _sector_etf_universe(prices: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in prices.columns if c.startswith("XL")]
    return prices[cols].dropna(how="all")


def sector_ls_momentum(prices: pd.DataFrame, lookback: int, hold_days: int, label: str) -> pd.Series:
    rets = prices.pct_change()
    mom = (1.0 + rets).rolling(lookback).apply(np.prod, raw=True) - 1.0
    target = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns, dtype=float)
    trade_days = 0
    for i, dt in enumerate(prices.index):
        if i < lookback:
            continue
        if (i - lookback) % hold_days != 0:
            continue
        ranks = mom.loc[dt].dropna().sort_values()
        vec = pd.Series(0.0, index=prices.columns)
        if len(ranks) >= 2:
            vec[ranks.index[-1]] = 1.0
            vec[ranks.index[0]] = -1.0
            target.loc[dt] = vec
            trade_days += 1
    target = target.ffill(limit=max(hold_days - 1, 0)).fillna(0.0)
    pos = target.shift(1).fillna(0.0)
    cost = pos.diff().abs().fillna(pos.abs()) * LEG_NOTIONAL * ETF_RT_COST_PCT
    borrow = pos.clip(upper=0).abs() * LEG_NOTIONAL * ETF_SHORT_BORROW_DAILY
    pnl = (pos * rets * LEG_NOTIONAL).sum(axis=1) - cost.sum(axis=1) - borrow.sum(axis=1)
    pnl.name = label
    pnl.attrs["trade_days"] = trade_days
    return pnl


def sector_ls_reversion(prices: pd.DataFrame, lookback: int, hold_days: int, label: str) -> pd.Series:
    rets = prices.pct_change()
    rev = (1.0 + rets).rolling(lookback).apply(np.prod, raw=True) - 1.0
    target = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns, dtype=float)
    trade_days = 0
    for i, dt in enumerate(prices.index):
        if i < lookback:
            continue
        if (i - lookback) % hold_days != 0:
            continue
        ranks = rev.loc[dt].dropna().sort_values()
        vec = pd.Series(0.0, index=prices.columns)
        if len(ranks) >= 2:
            vec[ranks.index[0]] = 1.0
            vec[ranks.index[-1]] = -1.0
            target.loc[dt] = vec
            trade_days += 1
    target = target.ffill(limit=max(hold_days - 1, 0)).fillna(0.0)
    pos = target.shift(1).fillna(0.0)
    cost = pos.diff().abs().fillna(pos.abs()) * LEG_NOTIONAL * ETF_RT_COST_PCT
    borrow = pos.clip(upper=0).abs() * LEG_NOTIONAL * ETF_SHORT_BORROW_DAILY
    pnl = (pos * rets * LEG_NOTIONAL).sum(axis=1) - cost.sum(axis=1) - borrow.sum(axis=1)
    pnl.name = label
    pnl.attrs["trade_days"] = trade_days
    return pnl


def sector_top1_long_only(prices: pd.DataFrame, lookback: int, hold_days: int, label: str) -> pd.Series:
    rets = prices.pct_change()
    mom = (1.0 + rets).rolling(lookback).apply(np.prod, raw=True) - 1.0
    target = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns, dtype=float)
    trade_days = 0
    for i, dt in enumerate(prices.index):
        if i < lookback:
            continue
        if (i - lookback) % hold_days != 0:
            continue
        ranks = mom.loc[dt].dropna().sort_values()
        vec = pd.Series(0.0, index=prices.columns)
        if len(ranks) >= 1:
            vec[ranks.index[-1]] = 1.0
            target.loc[dt] = vec
            trade_days += 1
    target = target.ffill(limit=max(hold_days - 1, 0)).fillna(0.0)
    pos = target.shift(1).fillna(0.0)
    cost = pos.diff().abs().fillna(pos.abs()) * LONG_ONLY_NOTIONAL * ETF_RT_COST_PCT
    pnl = (pos * rets * LONG_ONLY_NOTIONAL).sum(axis=1) - cost.sum(axis=1)
    pnl.name = label
    pnl.attrs["trade_days"] = trade_days
    return pnl


def defensive_vs_cyclical(prices: pd.DataFrame, label: str) -> pd.Series:
    rets = prices.pct_change()
    spy = prices["SPY"]
    sma200 = spy.rolling(200).mean()
    defensives = ["XLU", "XLV", "XLP"]
    cyclicals = ["XLY", "XLI", "XLF"]
    signal = pd.Series(index=prices.index, dtype=float)
    signal[spy < sma200] = -1.0
    signal[spy >= sma200] = 1.0
    pos = signal.shift(1).fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    cost = turnover * LEG_NOTIONAL * ETF_RT_COST_PCT
    pnl = pd.Series(0.0, index=prices.index, name=label)
    pnl[pos > 0] = (
        rets.loc[pos > 0, cyclicals].mean(axis=1) * LEG_NOTIONAL
        - rets.loc[pos > 0, defensives].mean(axis=1) * LEG_NOTIONAL
        - LEG_NOTIONAL * ETF_SHORT_BORROW_DAILY
    )
    pnl[pos < 0] = (
        rets.loc[pos < 0, defensives].mean(axis=1) * LEG_NOTIONAL
        - rets.loc[pos < 0, cyclicals].mean(axis=1) * LEG_NOTIONAL
        - LEG_NOTIONAL * ETF_SHORT_BORROW_DAILY
    )
    pnl = pnl - cost
    pnl.attrs["trade_days"] = int((turnover > 0).sum())
    return pnl


def pair_ratio_momentum(prices: pd.DataFrame, a: str, b: str, lookback: int, band: float, label: str) -> pd.Series:
    ratio = np.log(prices[a]) - np.log(prices[b])
    roll_std = ratio.rolling(lookback).std()
    roll_std = roll_std.where(roll_std > 1e-12, np.nan)
    z = (ratio - ratio.rolling(lookback).mean()) / roll_std
    rets = prices[[a, b]].pct_change()
    signal = pd.Series(0.0, index=prices.index)
    signal[z > band] = 1.0
    signal[z < -band] = -1.0
    pos = signal.shift(1).fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    cost = turnover * LEG_NOTIONAL * ETF_RT_COST_PCT
    pnl = pd.Series(0.0, index=prices.index, name=label)
    pnl[pos > 0] = (
        rets.loc[pos > 0, a] * LEG_NOTIONAL
        - rets.loc[pos > 0, b] * LEG_NOTIONAL
        - LEG_NOTIONAL * ETF_SHORT_BORROW_DAILY
    )
    pnl[pos < 0] = (
        rets.loc[pos < 0, b] * LEG_NOTIONAL
        - rets.loc[pos < 0, a] * LEG_NOTIONAL
        - LEG_NOTIONAL * ETF_SHORT_BORROW_DAILY
    )
    pnl = pnl - cost
    pnl.attrs["trade_days"] = int((turnover > 0).sum())
    return pnl


def macro_top1_rotation(prices: pd.DataFrame, universe: list[str], lookback: int, hold_days: int, label: str) -> pd.Series:
    sub = prices[universe].dropna(how="all")
    rets = sub.pct_change()
    mom = (1.0 + rets).rolling(lookback).apply(np.prod, raw=True) - 1.0
    target = pd.DataFrame(np.nan, index=sub.index, columns=sub.columns, dtype=float)
    trade_days = 0
    for i, dt in enumerate(sub.index):
        if i < lookback:
            continue
        if (i - lookback) % hold_days != 0:
            continue
        ranks = mom.loc[dt].dropna().sort_values()
        vec = pd.Series(0.0, index=sub.columns)
        if len(ranks) >= 1:
            vec[ranks.index[-1]] = 1.0
            target.loc[dt] = vec
            trade_days += 1
    target = target.ffill(limit=max(hold_days - 1, 0)).fillna(0.0)
    pos = target.shift(1).fillna(0.0)
    cost = pos.diff().abs().fillna(pos.abs()) * LONG_ONLY_NOTIONAL * ETF_RT_COST_PCT
    pnl = (pos * rets * LONG_ONLY_NOTIONAL).sum(axis=1) - cost.sum(axis=1)
    pnl.name = label
    pnl.attrs["trade_days"] = trade_days
    return pnl


def macro_risk_switch(prices: pd.DataFrame, label: str) -> pd.Series:
    sub = prices[["SPY", "TLT", "GLD"]].dropna(how="all")
    rets = sub.pct_change()
    sma200 = sub["SPY"].rolling(200).mean()
    signal = pd.Series(index=sub.index, dtype=float)
    signal[sub["SPY"] > sma200] = 1.0
    signal[sub["SPY"] <= sma200] = -1.0
    pos = signal.shift(1).fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    cost = turnover * LONG_ONLY_NOTIONAL * ETF_RT_COST_PCT
    pnl = pd.Series(0.0, index=sub.index, name=label)
    pnl[pos > 0] = rets.loc[pos > 0, "SPY"] * LONG_ONLY_NOTIONAL
    pnl[pos < 0] = rets.loc[pos < 0, ["TLT", "GLD"]].mean(axis=1) * LONG_ONLY_NOTIONAL
    pnl = pnl - cost
    pnl.attrs["trade_days"] = int((turnover > 0).sum())
    return pnl


def build_local_sector_candidates() -> dict[str, pd.Series]:
    from scripts.research.backtest_t3b_us_sector_ls import load_sector_return_matrix, variant_sector_ls

    mat = load_sector_return_matrix()
    return {
        "stock_sector_ls_20_5": variant_sector_ls(mat, 20, 5, "stock_sector_ls_20_5"),
        "stock_sector_ls_40_5": variant_sector_ls(mat, 40, 5, "stock_sector_ls_40_5"),
    }


def build_candidates(etf_prices: pd.DataFrame) -> dict[str, pd.Series]:
    sectors = _sector_etf_universe(etf_prices)
    candidates = {
        "etf_sector_ls_mom_20_5": sector_ls_momentum(sectors, 20, 5, "etf_sector_ls_mom_20_5"),
        "etf_sector_ls_mom_40_5": sector_ls_momentum(sectors, 40, 5, "etf_sector_ls_mom_40_5"),
        "etf_sector_ls_rev_5_3": sector_ls_reversion(sectors, 5, 3, "etf_sector_ls_rev_5_3"),
        "etf_sector_top1_long_40_5": sector_top1_long_only(sectors, 40, 5, "etf_sector_top1_long_40_5"),
        "defensive_vs_cyclical_regime": defensive_vs_cyclical(etf_prices[["SPY", "XLU", "XLV", "XLP", "XLY", "XLI", "XLF"]], "defensive_vs_cyclical_regime"),
        "pair_xle_xlk_ratio": pair_ratio_momentum(etf_prices[["XLE", "XLK"]], "XLE", "XLK", 30, 1.0, "pair_xle_xlk_ratio"),
        "pair_xlf_xlu_ratio": pair_ratio_momentum(etf_prices[["XLF", "XLU"]], "XLF", "XLU", 30, 1.0, "pair_xlf_xlu_ratio"),
        "macro_top1_rotation": macro_top1_rotation(etf_prices[["SPY", "TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]], ["SPY", "TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"], 60, 21, "macro_top1_rotation"),
        "macro_risk_switch": macro_risk_switch(etf_prices[["SPY", "TLT", "GLD"]], "macro_risk_switch"),
    }
    candidates.update(build_local_sector_candidates())
    return candidates


def main() -> int:
    from scripts.research.portfolio_marginal_score import score_candidate

    baseline = load_baseline()
    etf_prices = download_or_load_etfs()
    candidates = build_candidates(etf_prices)

    results = {}
    rets = {}
    for name, pnl in candidates.items():
        aligned = pnl.dropna()
        rets[name] = aligned
        results[name] = {
            "standalone": compute_metrics(aligned),
            "wf": walk_forward(aligned),
            "score": score_candidate(name, aligned, baseline, 10_000.0, 1.0).to_dict(),
            "trade_days": int(aligned.attrs.get("trade_days", 0)),
        }
        print(
            f"{name}: Sharpe={results[name]['standalone'].get('sharpe')} "
            f"WF={results[name]['wf'].get('ratio')} "
            f"Verdict={results[name]['score']['verdict']} "
            f"Score={results[name]['score']['marginal_score']:+.3f}"
        )

    corr = pd.DataFrame(rets).corr(min_periods=100)
    with METRICS_OUT.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "download_universe": DOWNLOAD_UNIVERSE,
                "price_range": {
                    "start": str(etf_prices.index.min().date()),
                    "end": str(etf_prices.index.max().date()),
                    "rows": len(etf_prices),
                },
                "results": results,
                "correlation": corr.to_dict(),
            },
            fh,
            indent=2,
            default=str,
        )
    pd.DataFrame(rets).to_parquet(RETURNS_OUT)
    print(f"Saved -> {METRICS_OUT}")
    print(f"Saved -> {RETURNS_OUT}")
    print(f"Saved -> {ETF_CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
