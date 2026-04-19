#!/usr/bin/env python3
"""T3-A2 - MES to BTC Asia-session lead-lag research batch.

Research-only cross-timezone signal:
  - derive a daily MES session signal from the US session
  - trade BTC during the next Asia session (00:00-07:00 UTC)
  - apply threshold and volatility filters to avoid choppy regimes

Outputs:
  - docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md
  - output/research/wf_reports/T3A-02_scorecards.json
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

BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MES_PATH = ROOT / "data" / "futures" / "MES_1H_YF2Y.parquet"
BTC_PATH = ROOT / "data" / "crypto" / "candles" / "BTCUSDT_1h.parquet"
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T3A-02_mes_btc_asia_leadlag.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T3A-02_scorecards.json"

BTC_NOTIONAL = 10_000.0
BINANCE_RT_COST_PCT = 0.0010  # 10 bps RT


def build_daily_dataset() -> pd.DataFrame:
    mes = pd.read_parquet(MES_PATH).copy().reset_index()
    mes = mes.rename(columns={mes.columns[0]: "timestamp"})
    mes["timestamp"] = pd.to_datetime(mes["timestamp"]).dt.tz_localize("UTC")
    mes = mes.sort_values("timestamp")
    mes["date"] = mes["timestamp"].dt.floor("D").dt.tz_localize(None)
    mes["ret_bar"] = mes["close"].pct_change()

    mes_sig = (
        mes[mes["timestamp"].dt.hour.isin([15, 16, 17, 18, 19, 20, 21])]
        .groupby("date")["ret_bar"]
        .sum()
        .rename("mes_sig")
    )
    mes_vol = mes.groupby("date")["ret_bar"].std().rename("mes_vol")

    btc = pd.read_parquet(BTC_PATH).copy()
    btc["timestamp"] = pd.to_datetime(btc["timestamp"], utc=True)
    btc = btc.sort_values("timestamp")
    btc["date"] = btc["timestamp"].dt.floor("D").dt.tz_localize(None)

    asia = btc[btc["timestamp"].dt.hour.isin(list(range(0, 8)))].copy()
    first_open = asia.groupby("date")["open"].first()
    last_close = asia.groupby("date")["close"].last()
    asia_ret = (last_close / first_open - 1.0).rename("btc_asia_ret")

    daily = pd.concat([mes_sig.shift(1), mes_vol.shift(1), asia_ret], axis=1).dropna()
    return daily


def variant_threshold(
    daily: pd.DataFrame,
    signal_quantile: float,
    vol_quantile: float,
    mode: str,
    label: str,
) -> pd.Series:
    thr = daily["mes_sig"].abs().quantile(signal_quantile)
    vol_thr = daily["mes_vol"].quantile(vol_quantile)
    sig = np.zeros(len(daily))
    if mode in ("both", "long_only"):
        sig = np.where(
            (daily["mes_sig"] >= thr) & (daily["mes_vol"] <= vol_thr),
            1,
            sig,
        )
    if mode in ("both", "short_only"):
        sig = np.where(
            (daily["mes_sig"] <= -thr) & (daily["mes_vol"] <= vol_thr),
            -1,
            sig,
        )
    sig = pd.Series(sig, index=daily.index)
    trade_cost = (sig != 0).astype(float) * BTC_NOTIONAL * BINANCE_RT_COST_PCT
    pnl = sig * daily["btc_asia_ret"] * BTC_NOTIONAL - trade_cost
    pnl = pnl.astype(float)
    pnl.name = label
    return pnl


def build_variants(daily: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "btc_asia_mes_leadlag_q70_v80": variant_threshold(
            daily, 0.70, 0.80, "both", "btc_asia_mes_leadlag_q70_v80"
        ),
        "btc_asia_mes_longonly_q80_v80": variant_threshold(
            daily, 0.80, 0.80, "long_only", "btc_asia_mes_longonly_q80_v80"
        ),
        "btc_asia_mes_shortonly_q85_v80": variant_threshold(
            daily, 0.85, 0.80, "short_only", "btc_asia_mes_shortonly_q85_v80"
        ),
    }


def _standalone_stats(pnl: pd.Series) -> dict:
    active = int((pnl != 0).sum())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() != 0 else 0.0
    eq = 10_000.0 + pnl.cumsum()
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    return {
        "active_days": active,
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe,
        "max_dd_pct": dd * 100,
    }


def main() -> int:
    print("=== T3-A2 : MES -> BTC Asia lead-lag ===")
    daily = build_daily_dataset()
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = build_variants(daily)
    scorecards = []
    rows = []
    for name, pnl in variants.items():
        stats = _standalone_stats(pnl)
        sc = score_candidate(name, pnl, baseline, 10_000.0, 1.0)
        scorecards.append(sc.to_dict())
        rows.append((name, stats, sc))
        print(
            f"{name}: total=${stats['total_pnl']:+,.0f} sharpe={stats['sharpe']:+.2f} "
            f"[{sc.verdict}] score={sc.marginal_score:+.3f}"
        )

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(scorecards, indent=2, default=str), encoding="utf-8")

    md = [
        "# T3-A2 - MES to BTC Asia lead-lag",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Data** : {daily.index.min().date()} -> {daily.index.max().date()} ({len(daily)} days)",
        "**Signal** : previous MES US session return proxy from 15:00-21:59 UTC",
        "**Execution** : BTC Asia session 00:00-07:59 UTC next day",
        f"**Cost model** : {BINANCE_RT_COST_PCT * 100:.2f}% round trip on ${BTC_NOTIONAL:,.0f} notional",
        "",
        "## Thesis",
        "",
        "- late US equity futures tone can propagate into crypto during the following Asia session",
        "- the edge is fragile without threshold and volatility filters",
        "",
        "## Variants",
        "",
        "| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for name, stats, sc in rows:
        md.append(
            f"| `{name}` | {stats['active_days']} | ${stats['total_pnl']:+,.0f} | "
            f"{stats['sharpe']:+.2f} | {stats['max_dd_pct']:.1f}% | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | {sc.delta_maxdd:+.2f}pp | "
            f"{sc.corr_to_portfolio:+.2f} |"
        )

    best = max(rows, key=lambda row: row[2].marginal_score)
    md += [
        "",
        "## Best candidate",
        "",
        f"- `{best[0]}`",
        f"- Verdict : **{best[2].verdict}**",
        f"- Marginal score : {best[2].marginal_score:+.3f}",
        f"- Delta Sharpe : {best[2].delta_sharpe:+.3f}",
        f"- Delta MaxDD : {best[2].delta_maxdd:+.2f}pp",
        f"- Corr to portfolio : {best[2].corr_to_portfolio:+.3f}",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
