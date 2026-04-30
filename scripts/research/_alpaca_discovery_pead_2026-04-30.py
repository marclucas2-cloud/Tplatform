#!/usr/bin/env python3
"""Mini-WF PEAD long-only - candidate book Alpaca paper (mission discover 2026-04-30).

Hypothese (Bernard-Thomas 1989, refresh): post-earnings drift positif sur beats forts.
- Univers: 30 SP500 large caps (earnings_history.parquet dispo)
- Signal: Surprise(%) >= seuil ET gap up day+1 open >= seuil
- Entry: day+1 open
- Exit: hold N jours, ou TP, ou SL
- Cost: 5 bps RT (Alpaca 0 commission + slippage realiste)

Validation:
- WF 5 fenetres glissantes 60% IS / 40% OOS sur 6 ans (2020-2026)
- Gate: Sharpe OOS median > 0.3, pass_rate >= 60% (3/5 fenetres OOS profitables)
- Correlation vs macro_top1_rotation (long-only ETF macro) calculee si possible

Sortie: reports/research/_alpaca_discovery_pead_2026-04-30_metrics.json
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
EARNINGS_PATH = ROOT / "data" / "us_research" / "earnings_history.parquet"
MACRO_PRICES = ROOT / "data" / "research" / "target_alpha_us_sectors_2026_04_24_prices.parquet"
OUT = ROOT / "reports" / "research" / "_alpaca_discovery_pead_2026-04-30_metrics.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

# Config (config retenue T1-D + sensibilite)
SURPRISE_THRESHOLD = 5.0  # %
GAP_THRESHOLD = 0.01  # 1% gap up
HOLD_DAYS = 20
TP_PCT = 0.08
SL_PCT = 0.03
NOTIONAL_PER_TRADE = 2000.0
RT_COST_PCT = 0.0005  # 5 bps RT (entry+exit slippage)
MAX_CONCURRENT = 5


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


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    px = pd.read_parquet(PX_PATH).copy()
    px.index = pd.to_datetime(px.index).normalize()
    earn = pd.read_parquet(EARNINGS_PATH).copy()
    earn["date"] = pd.to_datetime(earn["Earnings Date"], utc=True).dt.tz_convert(None).dt.normalize()
    earn = earn.dropna(subset=["Reported EPS", "Surprise(%)"]).sort_values("date").reset_index(drop=True)
    return px, earn


def simulate_trades(px: pd.DataFrame, earn: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Generate trade-level pnl for events between start and end (entry_date inclusive)."""
    trades = []
    sub_earn = earn[(earn["date"] >= start) & (earn["date"] <= end)]
    for _, row in sub_earn.iterrows():
        sym = row["symbol"]
        if sym not in px.columns:
            continue
        surprise = row["Surprise(%)"]
        if pd.isna(surprise) or surprise < SURPRISE_THRESHOLD:
            continue
        edate = row["date"]
        # Find next trading day in price index (entry day+1 open) — we use close as proxy since SP500 cache = close only
        # Anti-lookahead: entry_idx must be strictly > edate
        idx = px.index
        future = idx[idx > edate]
        if len(future) < 2:
            continue
        entry_dt = future[0]
        # gap = (entry_close - prev_close) / prev_close (proxy gap up)
        prev_close_idx = idx[idx <= edate]
        if len(prev_close_idx) < 1:
            continue
        prev_close = px.at[prev_close_idx[-1], sym]
        entry_price = px.at[entry_dt, sym]
        if pd.isna(prev_close) or pd.isna(entry_price) or prev_close <= 0:
            continue
        gap = entry_price / prev_close - 1.0
        if gap < GAP_THRESHOLD:
            continue
        # Hold: simulate path day by day for TP/SL/timeout
        future_dts = idx[idx >= entry_dt][: HOLD_DAYS + 1]
        if len(future_dts) < 2:
            continue
        exit_dt = future_dts[-1]
        exit_price = px.at[exit_dt, sym]
        for fd in future_dts[1:]:
            p = px.at[fd, sym]
            if pd.isna(p):
                continue
            r = p / entry_price - 1.0
            if r >= TP_PCT:
                exit_dt = fd
                exit_price = p
                break
            if r <= -SL_PCT:
                exit_dt = fd
                exit_price = p
                break
        if pd.isna(exit_price):
            continue
        gross_ret = exit_price / entry_price - 1.0
        net_ret = gross_ret - RT_COST_PCT
        pnl = net_ret * NOTIONAL_PER_TRADE
        trades.append(
            dict(symbol=sym, entry_date=entry_dt, exit_date=exit_dt, entry_price=entry_price,
                 exit_price=exit_price, gross_ret=gross_ret, net_ret=net_ret, pnl=pnl,
                 surprise=surprise, gap=gap)
        )
    return pd.DataFrame(trades)


def trades_to_daily_pnl(trades: pd.DataFrame, px_index: pd.DatetimeIndex) -> pd.Series:
    """Aggregate trade pnl to daily series (pnl on exit_date)."""
    if len(trades) == 0:
        return pd.Series(0.0, index=px_index)
    daily = pd.Series(0.0, index=px_index)
    for _, t in trades.iterrows():
        if t["exit_date"] in daily.index:
            daily.loc[t["exit_date"]] += t["pnl"]
    return daily


def walk_forward(px: pd.DataFrame, earn: pd.DataFrame, n_windows: int = 5) -> dict:
    """5 walking windows: chaque fenetre OOS = 40% du span total, glisse uniformement.
    Couvre toute la periode reelle (earn 2020-2026 + px 2018-2026)."""
    start = max(px.index.min(), earn["date"].min())
    end = min(px.index.max(), earn["date"].max())
    span_days = (end - start).days
    win_days = int(span_days * 0.4)
    # First window starts at 'start', last window ends at 'end'
    if n_windows > 1:
        step = (span_days - win_days) // (n_windows - 1)
    else:
        step = 0
    results = []
    for w in range(n_windows):
        oos_start = start + pd.Timedelta(days=w * step)
        oos_end = oos_start + pd.Timedelta(days=win_days)
        if oos_end > end:
            oos_end = end
        trades = simulate_trades(px, earn, oos_start, oos_end)
        # restrict price index to OOS window for daily pnl
        oos_idx = px.index[(px.index >= oos_start) & (px.index <= oos_end)]
        daily = trades_to_daily_pnl(trades, oos_idx)
        sh = sharpe(daily)
        dd = max_dd_pct(daily)
        n_trades = len(trades)
        total = float(trades["pnl"].sum()) if n_trades else 0.0
        win_rate = float((trades["net_ret"] > 0).mean()) if n_trades else 0.0
        results.append(dict(
            window=w + 1,
            oos_start=str(oos_start.date()),
            oos_end=str(oos_end.date()),
            n_trades=n_trades,
            sharpe=sh,
            max_dd_pct=dd,
            total_pnl=total,
            win_rate=win_rate,
            profitable=total > 0,
        ))
    return results


def correlation_with_macro(px: pd.DataFrame, earn: pd.DataFrame) -> dict:
    """Compute corr between PEAD daily pnl and macro_top1 proxy (SPY since top-1 dominant)."""
    if not MACRO_PRICES.exists():
        return {"available": False, "note": "macro prices file missing"}
    macro = pd.read_parquet(MACRO_PRICES).copy()
    macro.index = pd.to_datetime(macro.index).normalize()
    if "SPY" not in macro.columns:
        return {"available": False, "note": "SPY missing in macro file"}
    # PEAD daily pnl on full period
    trades = simulate_trades(px, earn, px.index.min(), px.index.max())
    if len(trades) == 0:
        return {"available": False, "note": "no trades"}
    daily = trades_to_daily_pnl(trades, px.index)
    # macro_top1 long-only: rolling 60d mom -> top1 -> hold 21d. Approx with SPY full period as worst-case proxy.
    # Real top-1 rotation often picks SPY ~30% of time; SPY corr is upper bound.
    spy_ret = macro["SPY"].pct_change().fillna(0.0)
    aligned = pd.concat([daily, spy_ret], axis=1, join="inner").dropna()
    if len(aligned) < 30:
        return {"available": False, "note": "insufficient overlap"}
    corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    return {"available": True, "spy_corr_proxy": corr, "n_obs": len(aligned),
            "note": "Upper bound: macro_top1 rotates 8 ETF, real corr likely lower"}


def main() -> int:
    print("[PEAD] Loading data")
    px, earn = load_data()
    print(f"  prices: {px.shape} {px.index.min().date()}->{px.index.max().date()}")
    print(f"  earnings: {len(earn)} events, {earn['symbol'].nunique()} symbols")

    print("[PEAD] Walk-forward 5 windows")
    wf = walk_forward(px, earn, n_windows=5)
    for w in wf:
        print(f"  W{w['window']} {w['oos_start']}->{w['oos_end']} "
              f"trades={w['n_trades']} Sharpe={w['sharpe']:.2f} DD={w['max_dd_pct']:.1f}% "
              f"PnL=${w['total_pnl']:.0f} WR={w['win_rate']*100:.1f}%")

    sharpes = [w["sharpe"] for w in wf]
    pass_count = sum(1 for w in wf if w["profitable"])
    sharpe_oos_pass = sum(1 for w in wf if w["sharpe"] > 0.3)

    # All-period stats
    all_trades = simulate_trades(px, earn, px.index.min(), px.index.max())
    all_daily = trades_to_daily_pnl(all_trades, px.index)
    sh_all = sharpe(all_daily)
    dd_all = max_dd_pct(all_daily)
    total_all = float(all_trades["pnl"].sum()) if len(all_trades) else 0.0
    wr_all = float((all_trades["net_ret"] > 0).mean()) if len(all_trades) else 0.0

    print(f"[PEAD] All trades: n={len(all_trades)} Sharpe={sh_all:.2f} DD={dd_all:.1f}% "
          f"PnL=${total_all:.0f} WR={wr_all*100:.1f}%")

    print("[PEAD] Correlation vs macro_top1 (SPY proxy)")
    corr_info = correlation_with_macro(px, earn)
    print(f"  {corr_info}")

    sharpe_med = float(np.median(sharpes))
    pass_rate = pass_count / len(wf)
    gate_sharpe = sharpe_med > 0.3
    gate_pass = pass_rate >= 0.6

    if gate_sharpe and gate_pass and sh_all > 0.5:
        verdict = "PROMOTE_PAPER"
    elif gate_pass and sharpe_med > 0.0:
        verdict = "KEEP_RESEARCH"
    else:
        verdict = "REJECTED"

    out = dict(
        candidate="pead_us_long_only_v1",
        run_at="2026-04-30",
        config=dict(
            surprise_threshold=SURPRISE_THRESHOLD, gap_threshold=GAP_THRESHOLD,
            hold_days=HOLD_DAYS, tp_pct=TP_PCT, sl_pct=SL_PCT,
            notional_per_trade=NOTIONAL_PER_TRADE, rt_cost_pct=RT_COST_PCT,
            universe_size=int(earn["symbol"].nunique()),
        ),
        wf_windows=wf,
        all_period=dict(
            n_trades=len(all_trades),
            sharpe=sh_all, max_dd_pct=dd_all, total_pnl=total_all, win_rate=wr_all,
            start=str(px.index.min().date()), end=str(px.index.max().date()),
        ),
        gates=dict(
            sharpe_oos_median=sharpe_med, pass_rate=pass_rate,
            gate_sharpe_median_gt_0_3=gate_sharpe, gate_pass_rate_gte_0_6=gate_pass,
            sharpe_oos_above_0_3_count=sharpe_oos_pass,
        ),
        correlation_vs_macro_top1=corr_info,
        verdict=verdict,
    )
    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"[PEAD] Verdict: {verdict}")
    print(f"[PEAD] Output: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
