#!/usr/bin/env python3
"""T1-B — Futures intraday mean reversion MES/MGC.

Detect jours avec excess move (>2x ATR daily), fade la direction au close suivant.
Hypothese : retracement 50-70% dans les 24h apres un move exagere.

Variantes testees:
  - fade_2atr_mes : fade si |close-open| > 2x ATR14, holding 1 day
  - fade_2atr_mgc : idem gold
  - fade_25atr_mes : seuil plus strict 2.5x ATR
  - fade_15atr_mes : seuil plus lache 1.5x ATR
  - fade_3atr_mes : seuil tres strict 3x ATR
  - fade_with_trend_filter_mes : fade seulement si EMA50 slope < seuil (no trend)

Couts: IBKR $0.85/side + 2 ticks slippage = $6.70 RT MES, $5.70 RT MGC.

Data: MES_1H_YF2Y (2Y donnees), resample daily OHLC.

Usage:
    python scripts/research/backtest_futures_intraday_mr.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.portfolio_marginal_score import score_candidate  # noqa: E402

MES_1H = ROOT / "data" / "futures" / "MES_1H_YF2Y.parquet"
MGC_1H = ROOT / "data" / "futures" / "MGC_1H_YF2Y.parquet"
MES_LONG = ROOT / "data" / "futures" / "MES_LONG.parquet"
MGC_LONG = ROOT / "data" / "futures" / "MGC_LONG.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"
MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)

# Contract specs
MES_POINT_VALUE = 5.0
MES_RT_COST = 6.70   # 2 ticks slippage + $0.85/side
MGC_POINT_VALUE = 10.0
MGC_RT_COST = 5.70


def load_daily_from_intraday(path: Path) -> pd.DataFrame:
    """Load 1H bars and resample to daily OHLC."""
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return daily


def load_daily_long(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def variant_fade(
    df: pd.DataFrame,
    multiplier: float,
    point_value: float,
    rt_cost: float,
    label: str,
    trend_filter: bool = False,
) -> pd.Series:
    """Fade excess move. Entry : close today, exit : close next day.

    Signal: if |close - open| > multiplier * ATR14, fade direction next day.
    """
    a = atr(df, 14)
    move = df["close"] - df["open"]
    excess = move.abs() > (multiplier * a)
    # Direction to fade: if close > open -> short, else long
    direction = -np.sign(move)  # -1 = short, +1 = long
    # Holding day: enter at today's close, exit at tomorrow's close
    next_close = df["close"].shift(-1)
    pnl_gross = direction * (next_close - df["close"]) * point_value
    # Apply filter
    signal = excess.fillna(False)
    if trend_filter:
        # EMA50 slope filter: avoid fading in strong trend
        ema50 = df["close"].ewm(span=50, adjust=False).mean()
        slope = (ema50 - ema50.shift(10)) / ema50.shift(10)
        # Fade only when slope is mild (|slope| < 2%)
        mild = slope.abs() < 0.02
        signal = signal & mild.fillna(False)
    pnl = pnl_gross.where(signal, 0.0)
    cost = np.where(signal, rt_cost, 0.0)
    pnl = (pnl - cost).astype(float).fillna(0.0)
    pnl.name = label
    return pnl


def main():
    print(f"=== T1-B : Futures intraday mean reversion MES/MGC ===\n")

    # Prefer 10Y LONG daily data (more robust), fallback on 2Y 1H resampled
    mes = load_daily_long(MES_LONG)
    mgc = load_daily_long(MGC_LONG)
    print(f"MES daily: {mes.shape[0]} days, {mes.index.min().date()} -> {mes.index.max().date()}")
    print(f"MGC daily: {mgc.shape[0]} days, {mgc.index.min().date()} -> {mgc.index.max().date()}")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    print(f"Baseline: {baseline.shape}\n")

    variants = []
    # MES fade with different thresholds
    variants.append(variant_fade(mes, 1.5, MES_POINT_VALUE, MES_RT_COST, "mes_fade_1.5atr"))
    variants.append(variant_fade(mes, 2.0, MES_POINT_VALUE, MES_RT_COST, "mes_fade_2.0atr"))
    variants.append(variant_fade(mes, 2.5, MES_POINT_VALUE, MES_RT_COST, "mes_fade_2.5atr"))
    variants.append(variant_fade(mes, 3.0, MES_POINT_VALUE, MES_RT_COST, "mes_fade_3.0atr"))
    variants.append(variant_fade(mes, 2.0, MES_POINT_VALUE, MES_RT_COST, "mes_fade_2atr_trend_filter", trend_filter=True))
    # MGC fade
    variants.append(variant_fade(mgc, 1.5, MGC_POINT_VALUE, MGC_RT_COST, "mgc_fade_1.5atr"))
    variants.append(variant_fade(mgc, 2.0, MGC_POINT_VALUE, MGC_RT_COST, "mgc_fade_2.0atr"))
    variants.append(variant_fade(mgc, 2.5, MGC_POINT_VALUE, MGC_RT_COST, "mgc_fade_2.5atr"))
    variants.append(variant_fade(mgc, 2.0, MGC_POINT_VALUE, MGC_RT_COST, "mgc_fade_2atr_trend_filter", trend_filter=True))

    print(f"{'Variant':<35s} {'Trades':>8s} {'TotPnL$':>10s} {'WinRate':>8s}")
    for v in variants:
        trades = int((v != 0).sum())
        total = float(v.sum())
        wins = int((v > 0).sum())
        win_rate = wins / trades if trades > 0 else 0
        print(f"{v.name:<35s} {trades:>8d} {total:>10.0f} {win_rate:>7.1%}")

    print("\n--- Scoring via marginal score engine ---")
    scorecards = []
    for v in variants:
        try:
            sc = score_candidate(
                candidate_id=v.name,
                candidate_returns=v,
                portfolio_returns=baseline,
                initial_equity=10_000.0,
                candidate_weight=1.0,
            )
            scorecards.append(sc)
            print(f"  [{sc.verdict:<20s}] {sc.candidate_id:<35s} "
                  f"score={sc.marginal_score:+.3f} dSharpe={sc.delta_sharpe:+.3f} "
                  f"dMaxDD={sc.delta_maxdd:+.2f}pp corr={sc.corr_to_portfolio:+.2f}")
        except Exception as e:
            print(f"  [SKIP] {v.name}: {e}")

    scorecards.sort(key=lambda r: r.marginal_score, reverse=True)

    json_out = JSON_OUT_DIR / "T1-03_scorecards.json"
    json_out.write_text(json.dumps([sc.to_dict() for sc in scorecards], indent=2, default=str))

    # Markdown report
    md_lines = [
        "# T1-B — Futures intraday mean reversion MES/MGC",
        "",
        f"**Run date** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Instruments** : MES (S&P 500 micro), MGC (micro gold)",
        f"**Methodologie** : fade les excess moves (|close-open| > kxATR14), holding 1 day",
        f"**Historique** : MES {mes.index.min().date()} -> {mes.index.max().date()} ({len(mes)} days)",
        f"**Baseline** : 7 strats (3 futures + 4 crypto post-S0)",
        f"**Couts** : MES ${MES_RT_COST} RT, MGC ${MGC_RT_COST} RT",
        "",
        "## Standalone stats",
        "",
        "| Variant | Trades | Total PnL $ | Win rate |",
        "|---|---:|---:|---:|",
    ]
    for v in variants:
        trades = int((v != 0).sum())
        total = float(v.sum())
        wins = int((v > 0).sum())
        win_rate = wins / trades if trades > 0 else 0
        md_lines.append(f"| `{v.name}` | {trades} | {total:+,.0f} | {win_rate:.1%} |")

    md_lines += [
        "",
        "## Scorecards (marginal vs baseline 7 strats)",
        "",
        "| Variant | Verdict | Score | dSharpe | dCAGR | dMaxDD | Corr | Tail | Penalties |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for sc in scorecards:
        pen = ", ".join(sc.penalties)[:80] if sc.penalties else "-"
        md_lines.append(
            f"| `{sc.candidate_id}` | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | "
            f"{sc.delta_cagr:+.2f}% | {sc.delta_maxdd:+.2f}pp | "
            f"{sc.corr_to_portfolio:+.2f} | {sc.tail_overlap:.0%} | {pen} |"
        )

    by_verdict = {}
    for sc in scorecards:
        by_verdict.setdefault(sc.verdict, []).append(sc.candidate_id)
    md_lines += ["", "## Verdict summary", ""]
    for v in ["PROMOTE_LIVE", "PROMOTE_PAPER", "KEEP_FOR_RESEARCH", "DROP"]:
        if v in by_verdict:
            md_lines.append(f"- **{v}** : {', '.join(f'`{c}`' for c in by_verdict[v])}")

    md_out = MD_OUT_DIR / "T1-03_futures_intraday_mr.md"
    md_out.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"\n=== FINAL SUMMARY ===")
    for v, lst in by_verdict.items():
        print(f"  {v}: {len(lst)}")
    print(f"\nReports: {md_out}, {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
