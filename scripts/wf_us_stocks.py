#!/usr/bin/env python3
"""Walk-forward + portfolio Sharpe for US stock candidates.

Uses the existing trades from reports/us_research/trades_<strat>.csv and splits
them chronologically into 5 rolling windows (70% IS / 30% OOS). This is valid
because our strats have FIXED params (no IS optimization) — what we're testing
is whether the edge persists over time.

Also computes a portfolio-level Sharpe (daily equity curve assuming N concurrent
positions) which is more realistic than the aggregated daily Sharpe from the
first-pass backtest (which inflated TOM Sharpe to 6.17).

Gate criteria (V15.3 standard):
  - OOS Sharpe average > 0.5
  - OOS/IS ratio > 0.5
  - >= 50% OOS windows profitable
  - >= 30 OOS trades
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TRADES_DIR = ROOT / "reports" / "us_research"
OUT = TRADES_DIR / "wf_report.md"

CANDIDATES = ["rs_spy", "sector_rot", "tom", "high_52w"]


def load_trades(name: str) -> pd.DataFrame:
    f = TRADES_DIR / f"trades_{name}.csv"
    df = pd.read_csv(f)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    return df.sort_values("exit_date").reset_index(drop=True)


def trade_sharpe(trades: pd.DataFrame) -> float:
    """Sharpe based on trade returns, annualized by trades/year."""
    if len(trades) < 10:
        return 0.0
    span_days = (trades["exit_date"].max() - trades["entry_date"].min()).days
    if span_days <= 0:
        return 0.0
    trades_per_year = len(trades) / span_days * 365
    mu = trades["pnl_net"].mean()
    sd = trades["pnl_net"].std()
    if sd == 0:
        return 0.0
    return float(mu / sd * np.sqrt(trades_per_year))


def portfolio_sharpe(trades: pd.DataFrame, max_concurrent: int = 10) -> float:
    """Daily equity curve assuming N concurrent positions, equal weight.

    Distributes each trade's PnL across its holding days, caps exposure at
    max_concurrent positions. This gives a portfolio return series that
    accounts for diversification across time and is NOT inflated by
    cross-sectional strats that exit many positions on the same day.
    """
    daily_pnl: dict[pd.Timestamp, float] = {}
    for _, t in trades.iterrows():
        dates = pd.bdate_range(t["entry_date"], t["exit_date"])
        if len(dates) == 0:
            continue
        per_day = t["pnl_net"] / len(dates)
        for d in dates:
            daily_pnl[d] = daily_pnl.get(d, 0) + per_day / max_concurrent
    if not daily_pnl:
        return 0.0
    s = pd.Series(daily_pnl).sort_index()
    if s.std() == 0:
        return 0.0
    return float(s.mean() / s.std() * np.sqrt(252))


def max_dd_portfolio(trades: pd.DataFrame, max_concurrent: int = 10) -> float:
    daily_pnl: dict[pd.Timestamp, float] = {}
    for _, t in trades.iterrows():
        dates = pd.bdate_range(t["entry_date"], t["exit_date"])
        if len(dates) == 0:
            continue
        per_day = t["pnl_net"] / len(dates)
        for d in dates:
            daily_pnl[d] = daily_pnl.get(d, 0) + per_day / max_concurrent
    if not daily_pnl:
        return 0.0
    s = pd.Series(daily_pnl).sort_index().cumsum()
    peak = s.cummax()
    dd = s - peak
    return float(dd.min())


def walk_forward(trades: pd.DataFrame, n_windows: int = 5, oos_frac: float = 0.3) -> list[dict]:
    n = len(trades)
    if n < 50:
        return []
    # Rolling anchored: overlap IS, non-overlap OOS
    total_slices = n_windows + 1
    slice_size = n // total_slices
    is_size = int(slice_size * (1 / oos_frac - 1))
    results = []
    for i in range(n_windows):
        oos_start = (i + 1) * slice_size
        is_start = max(0, oos_start - is_size)
        oos_end = oos_start + slice_size
        if oos_end > n:
            break
        is_df = trades.iloc[is_start:oos_start]
        oos_df = trades.iloc[oos_start:oos_end]
        if len(oos_df) < 5:
            break
        results.append({
            "window": i + 1,
            "is_n": len(is_df),
            "oos_n": len(oos_df),
            "is_sharpe": trade_sharpe(is_df),
            "oos_sharpe": trade_sharpe(oos_df),
            "is_pnl_pct": float(is_df["pnl_net"].sum() * 100),
            "oos_pnl_pct": float(oos_df["pnl_net"].sum() * 100),
            "oos_wr": float((oos_df["pnl_net"] > 0).mean()),
            "oos_profitable": bool(oos_df["pnl_net"].sum() > 0),
        })
    return results


def decorrelation_check(trades: pd.DataFrame) -> float:
    """Approximate daily return series for correlation computation later."""
    daily_pnl: dict[pd.Timestamp, float] = {}
    for _, t in trades.iterrows():
        dates = pd.bdate_range(t["entry_date"], t["exit_date"])
        if len(dates) == 0:
            continue
        per_day = t["pnl_net"] / len(dates)
        for d in dates:
            daily_pnl[d] = daily_pnl.get(d, 0) + per_day
    return pd.Series(daily_pnl).sort_index() if daily_pnl else pd.Series(dtype=float)


def main():
    report = [
        "# US Stock Candidates — Walk-Forward + Portfolio Sharpe",
        "",
        "Framework: trades chronologiquement splittées en 5 fenêtres rolling (70/30 IS/OOS).",
        "Portfolio Sharpe: equity curve quotidienne en supposant 10 positions concurrentes.",
        "",
        "## Gate V15.3",
        "- OOS Sharpe avg > 0.5",
        "- OOS/IS ratio > 0.5",
        "- >= 50% fenêtres profitables",
        "- >= 30 OOS trades par fenêtre",
        "",
        "## Résultats",
        "",
    ]

    # Collect daily return series for correlation matrix
    series_by_strat = {}

    summary_rows = []
    for name in CANDIDATES:
        trades = load_trades(name)
        n = len(trades)
        trade_sh = trade_sharpe(trades)
        port_sh = portfolio_sharpe(trades)
        port_mdd = max_dd_portfolio(trades)
        wf = walk_forward(trades)
        n_prof = sum(1 for w in wf if w["oos_profitable"])
        avg_oos_sh = float(np.mean([w["oos_sharpe"] for w in wf])) if wf else 0.0
        avg_is_sh = float(np.mean([w["is_sharpe"] for w in wf])) if wf else 0.0
        ratio = avg_oos_sh / avg_is_sh if avg_is_sh > 0 else 0.0
        min_oos_n = min((w["oos_n"] for w in wf), default=0)

        gate_oos = avg_oos_sh > 0.5
        gate_ratio = ratio > 0.5
        gate_prof = n_prof >= len(wf) / 2 if wf else False
        gate_n = min_oos_n >= 30
        passed = sum([gate_oos, gate_ratio, gate_prof, gate_n])
        verdict = "GO" if passed == 4 else ("BORDERLINE" if passed == 3 else "KILL")

        report.append(f"### {name}")
        report.append(f"- N trades: **{n}**")
        report.append(f"- Trade-level Sharpe (annualized): **{trade_sh:.2f}**")
        report.append(f"- Portfolio Sharpe (10 concurrent): **{port_sh:.2f}** (plus realiste)")
        report.append(f"- Portfolio MaxDD: **{port_mdd*100:.1f}%**")
        report.append(f"- WF IS avg Sharpe: {avg_is_sh:.2f}")
        report.append(f"- WF OOS avg Sharpe: {avg_oos_sh:.2f}")
        report.append(f"- OOS/IS ratio: {ratio:.2f}")
        report.append(f"- Profitable windows: {n_prof}/{len(wf)}")
        report.append("")
        if wf:
            report.append("| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL% | OOS WR |")
            report.append("|---|---:|---:|---:|---:|---:|---:|")
            for w in wf:
                report.append(
                    f"| {w['window']} | {w['is_n']} | {w['oos_n']} | "
                    f"{w['is_sharpe']:.2f} | {w['oos_sharpe']:.2f} | "
                    f"{w['oos_pnl_pct']:.1f}% | {w['oos_wr']:.0%} |"
                )
        report.append("")
        gates_str = (
            f"OOS>0.5:{gate_oos} OOS/IS>0.5:{gate_ratio} "
            f"Prof≥50%:{gate_prof} OOSn≥30:{gate_n}"
        )
        report.append(f"**Verdict: {verdict}** — gates {passed}/4 ({gates_str})")
        report.append("")

        summary_rows.append({
            "strat": name,
            "n_trades": n,
            "trade_sharpe": round(trade_sh, 2),
            "port_sharpe": round(port_sh, 2),
            "port_mdd_pct": round(port_mdd * 100, 1),
            "wf_is_sh": round(avg_is_sh, 2),
            "wf_oos_sh": round(avg_oos_sh, 2),
            "wf_ratio": round(ratio, 2),
            "wf_prof": f"{n_prof}/{len(wf)}",
            "verdict": verdict,
        })
        series_by_strat[name] = decorrelation_check(trades)

    # Correlation matrix
    report.append("## Corrélation inter-strats (daily PnL)")
    report.append("")
    combined = pd.DataFrame(series_by_strat).fillna(0)
    if len(combined) > 30:
        corr = combined.corr().round(2)
        report.append("| | " + " | ".join(corr.columns) + " |")
        report.append("|---|" + "---|" * len(corr.columns))
        for idx, row in corr.iterrows():
            report.append(f"| **{idx}** | " + " | ".join(f"{v:.2f}" for v in row) + " |")
    report.append("")

    report.append("## Summary")
    report.append("")
    report.append("| Strat | N | Trade Sh | **Port Sh** | MDD | OOS Sh | OOS/IS | Prof | Verdict |")
    report.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in summary_rows:
        report.append(
            f"| {r['strat']} | {r['n_trades']} | {r['trade_sharpe']} | "
            f"**{r['port_sharpe']}** | {r['port_mdd_pct']}% | "
            f"{r['wf_oos_sh']} | {r['wf_ratio']} | {r['wf_prof']} | {r['verdict']} |"
        )

    OUT.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
