#!/usr/bin/env python3
"""S0 — Rebuild crypto returns harmonisees pour enrichir la baseline.

Contexte: le baseline timeseries (WP-01) contient uniquement les 3 strats
futures. Pour scorer les candidates crypto (T1-C basis carry, T1-E L/S,
T2-B liquidation), on a besoin de daily PnL crypto harmonises.

Approche pragmatique: 4 strats crypto proxies backtestees vectorized sur
BTC/ETH daily LONG (2018-2026), avec couts Binance 25 bps RT spot.

Strats proxies (representent les 4 familles crypto):
  1. btc_trend : long BTC si close > SMA50, flat sinon
  2. eth_trend : long ETH si close > SMA50, flat sinon
  3. btc_dual_momentum : long BTC ou ETH selon 20j momentum
  4. btc_mean_reversion : long BTC si RSI14 < 30, sortie si RSI > 50

Output: data/research/portfolio_baseline_timeseries.parquet (enrichi avec
4 colonnes crypto ajoutees aux 3 existantes, total 7 strats).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
BTC_PATH = ROOT / "data" / "crypto" / "candles" / "BTCUSDT_1D_LONG.parquet"
ETH_PATH = ROOT / "data" / "crypto" / "candles" / "ETHUSDT_1D_LONG.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"

# Binance costs spot (from cost_capacity_assumptions.md)
SPOT_RT_BPS = 25  # 0.25% RT commission + slippage + spread
SPOT_RT = SPOT_RT_BPS / 10_000  # as fraction

# Capital per strat proxy (so PnL $ magnitudes are comparable with futures)
STRAT_CAPITAL = 5_000.0


def load_daily(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index()
    return df


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(com=window - 1, adjust=False).mean()
    roll_dn = down.ewm(com=window - 1, adjust=False).mean()
    rs = roll_up / roll_dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def strat_trend(df: pd.DataFrame, label: str, sma: int = 50) -> pd.Series:
    """Long if close > SMA, else flat. Position sized as STRAT_CAPITAL."""
    sma_v = df["close"].rolling(sma).mean()
    # Signal: 1 if close > SMA50, 0 otherwise. Enter at next day open (no lookahead).
    signal = (df["close"] > sma_v).shift(1).fillna(False).astype(int)
    # Daily return = close/close_prev - 1, scaled by position
    daily_ret = df["close"].pct_change().fillna(0)
    pos_change = signal.diff().fillna(0).abs()
    # PnL daily = signal * daily_ret * STRAT_CAPITAL - costs when position changes
    pnl = signal * daily_ret * STRAT_CAPITAL - pos_change * SPOT_RT * STRAT_CAPITAL
    pnl.name = label
    return pnl


def strat_dual_momentum(btc: pd.DataFrame, eth: pd.DataFrame, label: str = "crypto_dual_momentum") -> pd.Series:
    """Long BTC or ETH depending on 20d momentum (whichever is higher)."""
    btc_mom = btc["close"].pct_change(20)
    eth_mom = eth["close"].pct_change(20)
    # Signal: +1 for BTC, +2 for ETH, 0 if both negative (risk-off)
    choose_btc = (btc_mom > eth_mom) & (btc_mom > 0)
    choose_eth = (eth_mom > btc_mom) & (eth_mom > 0)
    sig_btc = choose_btc.shift(1).fillna(False).astype(int)
    sig_eth = choose_eth.shift(1).fillna(False).astype(int)
    btc_ret = btc["close"].pct_change().fillna(0)
    eth_ret = eth["close"].pct_change().fillna(0)
    # Position change cost (whenever leg swaps)
    pos_btc_change = sig_btc.diff().fillna(0).abs()
    pos_eth_change = sig_eth.diff().fillna(0).abs()
    pnl = (
        sig_btc * btc_ret * STRAT_CAPITAL
        + sig_eth * eth_ret * STRAT_CAPITAL
        - (pos_btc_change + pos_eth_change) * SPOT_RT * STRAT_CAPITAL
    )
    pnl.name = label
    return pnl


def strat_mean_reversion(df: pd.DataFrame, label: str, entry_rsi: float = 30, exit_rsi: float = 50) -> pd.Series:
    """Long if RSI14 < 30, exit when RSI > 50. State machine."""
    r = rsi(df["close"], 14)
    # Position state (1 if long, 0 otherwise) — built iteratively
    pos = pd.Series(0, index=df.index, dtype=int)
    in_pos = False
    for i, dt in enumerate(df.index):
        if i == 0:
            continue
        prev_rsi = r.iloc[i - 1]
        if not in_pos and prev_rsi < entry_rsi:
            in_pos = True
        elif in_pos and prev_rsi > exit_rsi:
            in_pos = False
        pos.iloc[i] = 1 if in_pos else 0
    daily_ret = df["close"].pct_change().fillna(0)
    pos_change = pos.diff().fillna(0).abs()
    pnl = pos * daily_ret * STRAT_CAPITAL - pos_change * SPOT_RT * STRAT_CAPITAL
    pnl.name = label
    return pnl


def main():
    print("=== S0 — Rebuild crypto returns harmonisees ===\n")

    btc = load_daily(BTC_PATH)
    eth = load_daily(ETH_PATH)
    print(f"BTC: {len(btc)} days, {btc.index.min().date()} -> {btc.index.max().date()}")
    print(f"ETH: {len(eth)} days, {eth.index.min().date()} -> {eth.index.max().date()}")

    # Align on intersection
    common = btc.index.intersection(eth.index)
    btc = btc.loc[common]
    eth = eth.loc[common]
    print(f"Common: {len(common)} days\n")

    # Generate 4 strat proxies
    print("Generating crypto strat proxies:")
    pnl_btc_trend = strat_trend(btc, "btc_trend_sma50", sma=50)
    pnl_eth_trend = strat_trend(eth, "eth_trend_sma50", sma=50)
    pnl_dual_mom = strat_dual_momentum(btc, eth)
    pnl_btc_mr = strat_mean_reversion(btc, "btc_mean_reversion_rsi")

    for s in [pnl_btc_trend, pnl_eth_trend, pnl_dual_mom, pnl_btc_mr]:
        total = s.sum()
        active = (s != 0).sum()
        sharpe = s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
        print(f"  {s.name:<30s} total=${total:>+9,.0f}  active={active:>4d}d  sharpe={sharpe:+.2f}")

    # Assemble crypto dataframe
    crypto_df = pd.DataFrame({
        "btc_trend_sma50": pnl_btc_trend,
        "eth_trend_sma50": pnl_eth_trend,
        "crypto_dual_momentum": pnl_dual_mom,
        "btc_mean_reversion_rsi": pnl_btc_mr,
    })
    crypto_df.index.name = "date"

    # Load existing baseline
    print(f"\nLoading existing baseline from {BASELINE_PATH}...")
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    print(f"  Before: {baseline.shape}")

    # Align to outer union of dates, fill NaN with 0 (strat not active)
    all_dates = baseline.index.union(crypto_df.index).sort_values()
    baseline_aligned = baseline.reindex(all_dates).fillna(0)
    crypto_aligned = crypto_df.reindex(all_dates).fillna(0)
    combined = pd.concat([baseline_aligned, crypto_aligned], axis=1)
    combined.index.name = "date"

    print(f"  After: {combined.shape}, cols={list(combined.columns)}")
    combined.to_parquet(BASELINE_PATH)
    print(f"  Saved -> {BASELINE_PATH}")

    # Also save standalone crypto-only version for candidate backtests
    crypto_only_path = BASELINE_PATH.parent / "crypto_returns_harmonized.parquet"
    crypto_df.to_parquet(crypto_only_path)
    print(f"  Saved crypto-only -> {crypto_only_path}")

    # Summary
    combined_daily = combined.sum(axis=1)
    print(f"\nCombined portfolio (7 strats):")
    print(f"  Total PnL: ${combined_daily.sum():+,.0f}")
    print(f"  Sharpe: {combined_daily.mean() / combined_daily.std() * np.sqrt(252):.2f}")
    print(f"  Active days: {(combined_daily != 0).sum()}/{len(combined_daily)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
