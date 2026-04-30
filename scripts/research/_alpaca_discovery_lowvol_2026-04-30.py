#!/usr/bin/env python3
"""Mini-WF Low-Volatility / Betting-Against-Beta - candidate book Alpaca paper (2026-04-30).

Hypothese (Frazzini-Pedersen 2014): basse vol surperforme haute vol risk-adjusted.
- Univers: 30 SP500 large caps (sp500_prices_cache.parquet, 8 ans)
- Signal: rolling 60d realised vol -> bottom decile long, top decile short
- Rebalance: monthly (21 busdays)
- Long-only variante (PDT-safe pure long, sans borrow)
- L/S variante (avec borrow 1%/an)
- Cost: 5 bps RT slippage

Validation:
- WF 5 fenetres glissantes sur 8 ans, gate Sharpe OOS median > 0.3, pass_rate >= 60%
- Anti-lookahead: rolling vol .shift(1), iloc[:i] pour rebalance

Sortie: reports/research/_alpaca_discovery_lowvol_2026-04-30_metrics.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PX_PATH = ROOT / "data" / "us_research" / "sp500_prices_cache.parquet"
MACRO_PRICES = ROOT / "data" / "research" / "target_alpha_us_sectors_2026_04_24_prices.parquet"
OUT = ROOT / "reports" / "research" / "_alpaca_discovery_lowvol_2026-04-30_metrics.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

VOL_LOOKBACK = 60
REBAL_DAYS = 21
N_PER_LEG = 5  # 5 longs (bottom vol) and 5 shorts (top vol) on 30 stocks
RT_COST_PCT = 0.0005  # 5 bps RT
BORROW_ANNUAL = 0.01  # 1% short borrow
BORROW_DAILY = BORROW_ANNUAL / 252.0
GROSS_NOTIONAL = 10_000.0


def sharpe(pnl: pd.Series, periods: int = 252) -> float:
    pnl = pnl.dropna()
    if len(pnl) < 5 or pnl.std() == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(periods))


def max_dd_pct(pnl: pd.Series, init: float = 10_000.0) -> float:
    if len(pnl) == 0:
        return 0.0
    eq = init + pnl.cumsum()
    peak = eq.cummax()
    return float(((eq - peak) / peak).min()) * 100


def build_signals_long_only(px: pd.DataFrame) -> pd.DataFrame:
    """Bottom-N vol = longs, equal weight, rebalance every REBAL_DAYS. Anti-lookahead via shift."""
    rets = px.pct_change()
    vol = rets.rolling(VOL_LOOKBACK).std().shift(1)  # signal disponible day+1
    target = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    last_rebal = -REBAL_DAYS - 1
    current_w = pd.Series(0.0, index=px.columns)
    for i in range(len(px.index)):
        if i - last_rebal >= REBAL_DAYS:
            row = vol.iloc[i]
            valid = row.dropna()
            if len(valid) >= N_PER_LEG * 2:
                bottom = valid.nsmallest(N_PER_LEG).index
                w = pd.Series(0.0, index=px.columns)
                w.loc[bottom] = 1.0 / N_PER_LEG
                current_w = w
                last_rebal = i
        target.iloc[i] = current_w
    return target


def build_signals_ls(px: pd.DataFrame) -> pd.DataFrame:
    """Bottom-N vol long, top-N vol short. Equal weight per leg."""
    rets = px.pct_change()
    vol = rets.rolling(VOL_LOOKBACK).std().shift(1)
    target = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    last_rebal = -REBAL_DAYS - 1
    current_w = pd.Series(0.0, index=px.columns)
    for i in range(len(px.index)):
        if i - last_rebal >= REBAL_DAYS:
            row = vol.iloc[i]
            valid = row.dropna()
            if len(valid) >= N_PER_LEG * 2:
                bottom = valid.nsmallest(N_PER_LEG).index
                top = valid.nlargest(N_PER_LEG).index
                w = pd.Series(0.0, index=px.columns)
                w.loc[bottom] = 0.5 / N_PER_LEG
                w.loc[top] = -0.5 / N_PER_LEG
                current_w = w
                last_rebal = i
        target.iloc[i] = current_w
    return target


def simulate_pnl(px: pd.DataFrame, weights: pd.DataFrame, ls: bool) -> pd.Series:
    """Daily pnl on GROSS_NOTIONAL, with cost on weight changes and borrow on short leg if ls."""
    rets = px.pct_change().fillna(0.0)
    # Daily pnl: weights[t-1] * rets[t] (no lookahead)
    w_lag = weights.shift(1).fillna(0.0)
    daily_ret = (w_lag * rets).sum(axis=1)
    pnl = daily_ret * GROSS_NOTIONAL
    # Cost: |dW| * notional * cost_pct (one-way ~ 2.5 bps, total RT 5 bps)
    dw = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = dw * GROSS_NOTIONAL * (RT_COST_PCT / 2.0)
    pnl = pnl - cost
    if ls:
        short_w = (-weights.clip(upper=0)).sum(axis=1)  # absolute short notional ratio
        borrow_cost = short_w.shift(1).fillna(0.0) * GROSS_NOTIONAL * BORROW_DAILY
        pnl = pnl - borrow_cost
    return pnl


def walk_forward(px: pd.DataFrame, weights: pd.DataFrame, ls: bool, n_windows: int = 5) -> list:
    daily = simulate_pnl(px, weights, ls)
    daily = daily.dropna()
    if len(daily) < 252:
        return []
    n = len(daily)
    win_size = int(n * 0.4)
    step = (n - win_size) // (n_windows - 1) if n_windows > 1 else 0
    results = []
    for w in range(n_windows):
        s = w * step
        e = min(s + win_size, n)
        slc = daily.iloc[s:e]
        sh = sharpe(slc)
        dd = max_dd_pct(slc)
        total = float(slc.sum())
        results.append(dict(
            window=w + 1,
            oos_start=str(slc.index[0].date()),
            oos_end=str(slc.index[-1].date()),
            n_days=len(slc),
            sharpe=sh,
            max_dd_pct=dd,
            total_pnl=total,
            profitable=total > 0,
        ))
    return results


def correlation_with_macro(px: pd.DataFrame, weights: pd.DataFrame, ls: bool) -> dict:
    if not MACRO_PRICES.exists():
        return {"available": False, "note": "macro prices missing"}
    macro = pd.read_parquet(MACRO_PRICES).copy()
    macro.index = pd.to_datetime(macro.index).normalize()
    if "SPY" not in macro.columns:
        return {"available": False, "note": "SPY missing"}
    daily = simulate_pnl(px, weights, ls).dropna()
    spy_ret = macro["SPY"].pct_change().fillna(0.0)
    aligned = pd.concat([daily, spy_ret], axis=1, join="inner").dropna()
    if len(aligned) < 30:
        return {"available": False, "note": "insufficient overlap"}
    corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    return {"available": True, "spy_corr_proxy": corr, "n_obs": len(aligned)}


def evaluate_variant(label: str, px: pd.DataFrame, weights: pd.DataFrame, ls: bool) -> dict:
    daily = simulate_pnl(px, weights, ls)
    sh_all = sharpe(daily)
    dd_all = max_dd_pct(daily)
    total_all = float(daily.sum())
    wf = walk_forward(px, weights, ls, n_windows=5)
    sharpes = [w["sharpe"] for w in wf]
    pass_count = sum(1 for w in wf if w["profitable"])
    pass_rate = pass_count / len(wf) if wf else 0.0
    sharpe_med = float(np.median(sharpes)) if sharpes else 0.0
    gate_sharpe = sharpe_med > 0.3
    gate_pass = pass_rate >= 0.6
    if gate_sharpe and gate_pass and sh_all > 0.5:
        verdict = "PROMOTE_PAPER"
    elif gate_pass and sharpe_med > 0.0:
        verdict = "KEEP_RESEARCH"
    else:
        verdict = "REJECTED"
    corr = correlation_with_macro(px, weights, ls)
    print(f"[LV][{label}] Sharpe_all={sh_all:.2f} DD={dd_all:.1f}% PnL=${total_all:.0f} "
          f"WF_pass={pass_count}/{len(wf)} med_sh={sharpe_med:.2f} verdict={verdict}")
    for w in wf:
        print(f"   W{w['window']} {w['oos_start']}->{w['oos_end']} Sh={w['sharpe']:.2f} "
              f"DD={w['max_dd_pct']:.1f}% PnL=${w['total_pnl']:.0f}")
    return dict(
        label=label,
        all_period=dict(sharpe=sh_all, max_dd_pct=dd_all, total_pnl=total_all,
                       n_days=int(daily.dropna().shape[0])),
        wf_windows=wf,
        gates=dict(sharpe_oos_median=sharpe_med, pass_rate=pass_rate,
                  gate_sharpe_gt_0_3=gate_sharpe, gate_pass_rate_gte_0_6=gate_pass),
        correlation_vs_macro_top1=corr,
        verdict=verdict,
    )


def main() -> int:
    print("[LowVol] Loading prices")
    px = pd.read_parquet(PX_PATH).copy()
    px.index = pd.to_datetime(px.index).normalize()
    print(f"  shape: {px.shape} {px.index.min().date()}->{px.index.max().date()}")

    results = {}
    for label, ls in [("long_only_bottom5_vol", False), ("ls_bottom5_top5_vol_borrow1pct", True)]:
        print(f"[LowVol] Variant {label} (ls={ls})")
        if ls:
            w = build_signals_ls(px)
        else:
            w = build_signals_long_only(px)
        results[label] = evaluate_variant(label, px, w, ls)

    out = dict(
        candidate="lowvol_bab_v1",
        run_at="2026-04-30",
        config=dict(
            vol_lookback=VOL_LOOKBACK, rebal_days=REBAL_DAYS, n_per_leg=N_PER_LEG,
            rt_cost_pct=RT_COST_PCT, borrow_annual=BORROW_ANNUAL,
            gross_notional=GROSS_NOTIONAL, universe_size=int(px.shape[1]),
        ),
        variants=results,
    )
    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"[LowVol] Output: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
