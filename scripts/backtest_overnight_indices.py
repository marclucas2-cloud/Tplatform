#!/usr/bin/env python3
"""Backtest overnight drift strategy on 4 index futures (MES baseline + M2K, MNQ, NIY).

Strategy: buy at close T, sell at open T+1. Documented edge (Bessembinder-Kalcheva
2013, Lou-Polk-Skouras 2019): US equity markets drift positively from close to
next-day open. The edge is strongest on broad indices, weaker/reversed intraday.

This script tests 4 instruments to answer: can we diversify beyond MES?
  1. MES — baseline (already LIVE)
  2. M2K (Russell 2000) — small cap US, different investor base
  3. MNQ (Nasdaq-100) — tech-heavy momentum, reconsideration
  4. NIY (Nikkei 225, Tokyo) — US→Asia info leakage session

Metrics per instrument:
  - N trades, win rate, avg PnL, Sharpe (annualized via sqrt(252))
  - Max DD, profit factor
  - Walk-forward: 5 rolling windows 70/30 IS/OOS
  - Correlation matrix vs MES (are the edges decorrelated?)

Costs modeled:
  - Futures commission: $1.24 per round-trip (IBKR micro, realistic)
  - Slippage: 0.5 tick per side (1 tick round-trip)
  - M2K/MES: 0.25 tick = $1.25/side → 2.50/rt
  - MNQ: 0.25 tick = $0.50/side → $1.00/rt
  - NIY: 5 pt tick = $25/side → $50/rt (!! check this)

Output: reports/research/overnight_indices_{DATE}.md
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("overnight_indices")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# === Instrument specs (contract multiplier + realistic costs) ===
SPECS = {
    "MES": {
        "mult": 5.0,           # $5 per index point
        "tick": 0.25,          # min tick $1.25
        "commission": 1.24,    # IBKR micro futures round trip
        "slip_ticks_rt": 1,    # 1 tick round trip (0.5 per side)
    },
    "M2K": {
        "mult": 5.0,
        "tick": 0.10,
        "commission": 1.24,
        "slip_ticks_rt": 1,
    },
    "MNQ": {
        "mult": 2.0,
        "tick": 0.25,
        "commission": 1.24,
        "slip_ticks_rt": 1,
    },
    "NIY": {
        "mult": 5.0,           # Nikkei 225 Yen Micro on CME, $5 per index pt
        "tick": 5.0,
        "commission": 2.40,    # slightly higher for JPY
        "slip_ticks_rt": 1,
    },
}


@dataclass
class OvernightTrade:
    symbol: str
    date_close: str
    date_open: str
    close_px: float
    open_px: float
    raw_pts: float          # index points gained/lost
    pnl_gross_usd: float    # pts * multiplier
    pnl_net_usd: float      # after commission + slippage


# ==================================================================
# Data loaders
# ==================================================================
def load_local_futures(symbol: str) -> pd.DataFrame:
    """Load a local parquet file (data/futures/<SYM>_1D.parquet)."""
    f = ROOT / "data" / "futures" / f"{symbol}_1D.parquet"
    if not f.exists():
        raise FileNotFoundError(f)
    df = pd.read_parquet(f)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def load_nikkei_yfinance() -> pd.DataFrame:
    """Download Nikkei 225 daily via yfinance (NIY=F) as proxy for NIY micro futures."""
    import yfinance as yf
    logger.info("Downloading NIY=F via yfinance (~5y)")
    df = yf.download("NIY=F", period="5y", interval="1d", progress=False, auto_adjust=False)
    if df.empty:
        logger.warning("NIY=F empty — trying ^N225 (cash index) as fallback")
        df = yf.download("^N225", period="5y", interval="1d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


# ==================================================================
# Backtest engine
# ==================================================================
def backtest_overnight(df: pd.DataFrame, symbol: str, ema_filter: bool = False) -> list[OvernightTrade]:
    """Buy close T, sell open T+1. One trade per day.

    If ema_filter=True, only take LONG trades when close > EMA20 (same filter
    as backtest_portfolio_v153.py "Overnight MES"). This is the real production
    filter for the LIVE strat.
    """
    spec = SPECS[symbol]
    trades: list[OvernightTrade] = []
    closes = df["close"].astype(float)
    opens = df["open"].astype(float)
    ema20 = closes.ewm(span=20, adjust=False).mean()
    closes_v = closes.values
    opens_v = opens.values
    ema_v = ema20.values
    dates = df.index.tolist()
    slip_per_rt = spec["slip_ticks_rt"] * spec["tick"] * spec["mult"]
    cost_per_trade = spec["commission"] + slip_per_rt

    for i in range(20, len(df) - 1):
        c = float(closes_v[i])
        o = float(opens_v[i + 1])
        e = float(ema_v[i])
        if not (np.isfinite(c) and np.isfinite(o) and np.isfinite(e)) or c <= 0:
            continue
        # Production filter: only LONG when close > EMA20 (uptrend)
        if ema_filter and c <= e:
            continue
        raw_pts = o - c
        gross = raw_pts * spec["mult"]
        net = gross - cost_per_trade
        trades.append(OvernightTrade(
            symbol=symbol,
            date_close=str(dates[i].date()),
            date_open=str(dates[i + 1].date()),
            close_px=c,
            open_px=o,
            raw_pts=raw_pts,
            pnl_gross_usd=gross,
            pnl_net_usd=net,
        ))
    return trades


def compute_stats(trades: list[OvernightTrade]) -> dict:
    if not trades:
        return {"n_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    df["date_open"] = pd.to_datetime(df["date_open"])
    df = df.sort_values("date_open")
    net = df["pnl_net_usd"].values
    gross = df["pnl_gross_usd"].values
    n = len(df)
    wins = int((net > 0).sum())
    wr = wins / n
    total_net = float(net.sum())
    total_gross = float(gross.sum())
    avg_net = float(net.mean())
    std = float(net.std())
    sharpe = avg_net / std * np.sqrt(252) if std > 0 else 0
    # Profit factor
    pos = net[net > 0].sum()
    neg = -net[net < 0].sum()
    pf = float(pos / neg) if neg > 0 else float("inf")
    # Max DD
    cum = net.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {
        "n_trades": n,
        "win_rate": round(wr, 3),
        "avg_net_usd": round(avg_net, 2),
        "total_net_usd": round(total_net, 0),
        "total_gross_usd": round(total_gross, 0),
        "cost_burn_pct": round((total_gross - total_net) / abs(total_gross) * 100, 1) if total_gross != 0 else 0,
        "sharpe": round(sharpe, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else 999,
        "max_dd_usd": round(mdd, 0),
    }


def walk_forward(trades: list[OvernightTrade], n_windows: int = 5) -> list[dict]:
    """Split trades chronologically into 5 rolling windows (60/40 IS/OOS each)."""
    if len(trades) < 50:
        return []
    df = pd.DataFrame([asdict(t) for t in trades])
    df["date_open"] = pd.to_datetime(df["date_open"])
    df = df.sort_values("date_open").reset_index(drop=True)
    n = len(df)
    slice_size = n // (n_windows + 1)
    is_size = int(slice_size * 1.5)
    results = []
    for i in range(n_windows):
        oos_start = (i + 1) * slice_size
        is_start = max(0, oos_start - is_size)
        oos_end = oos_start + slice_size
        if oos_end > n:
            break
        is_slice = df.iloc[is_start:oos_start]
        oos_slice = df.iloc[oos_start:oos_end]
        is_net = is_slice["pnl_net_usd"].values
        oos_net = oos_slice["pnl_net_usd"].values

        def _sharpe(arr):
            if len(arr) < 2 or arr.std() == 0:
                return 0.0
            return float(arr.mean() / arr.std() * np.sqrt(252))

        results.append({
            "window": i + 1,
            "is_n": len(is_slice),
            "oos_n": len(oos_slice),
            "is_sharpe": round(_sharpe(is_net), 2),
            "oos_sharpe": round(_sharpe(oos_net), 2),
            "is_pnl": round(float(is_net.sum()), 0),
            "oos_pnl": round(float(oos_net.sum()), 0),
            "oos_profitable": bool(oos_net.sum() > 0),
        })
    return results


def correlation_matrix(trades_by_sym: dict[str, list[OvernightTrade]]) -> pd.DataFrame:
    """Build daily return series per symbol and compute correlation."""
    series_list = {}
    for sym, trades in trades_by_sym.items():
        if not trades:
            continue
        df = pd.DataFrame([asdict(t) for t in trades])
        df["date_open"] = pd.to_datetime(df["date_open"])
        series_list[sym] = df.set_index("date_open")["pnl_net_usd"]
    combined = pd.DataFrame(series_list).fillna(0)
    return combined.corr().round(3)


# ==================================================================
# Main
# ==================================================================
def main() -> int:
    logger.info("Loading data…")
    data: dict[str, pd.DataFrame] = {}

    # Local parquet for MES/M2K/MNQ
    for sym in ["MES", "M2K", "MNQ"]:
        try:
            df = load_local_futures(sym)
            logger.info(f"  {sym}: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")
            data[sym] = df
        except Exception as e:
            logger.warning(f"  {sym}: {e}")

    # yfinance for Nikkei
    try:
        df = load_nikkei_yfinance()
        logger.info(f"  NIY: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")
        data["NIY"] = df
    except Exception as e:
        logger.warning(f"  NIY: {e}")

    # Run backtests — BOTH naive AND with production EMA20 filter (close > EMA20 long-only)
    trades_by_sym: dict[str, list[OvernightTrade]] = {}
    stats_by_sym: dict[str, dict] = {}
    wf_by_sym: dict[str, list[dict]] = {}

    for sym, df in data.items():
        for variant, ema in [("naive", False), ("filtered", True)]:
            key = f"{sym}_{variant}"
            trades = backtest_overnight(df, sym, ema_filter=ema)
            trades_by_sym[key] = trades
            stats = compute_stats(trades)
            wf = walk_forward(trades)
            stats_by_sym[key] = stats
            wf_by_sym[key] = wf
            logger.info(f"{key}: n={stats.get('n_trades')} WR={stats.get('win_rate')} "
                        f"net=${stats.get('total_net_usd')} Sh={stats.get('sharpe')} "
                        f"MDD=${stats.get('max_dd_usd')} burn={stats.get('cost_burn_pct')}%")

    # Correlation matrix (filtered variants only — those are the ones we'd run live)
    filtered_trades = {k.replace("_filtered", ""): v for k, v in trades_by_sym.items() if k.endswith("_filtered")}
    corr = correlation_matrix(filtered_trades)

    # Build report
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        f"# Overnight Drift Backtest — Index Futures Diversification ({today})",
        "",
        "Strategy: BUY close T, SELL open T+1.",
        "- NAIVE: every day",
        "- FILTERED: only when close > EMA20 (production filter from backtest_portfolio_v153.py)",
        "",
        "## Summary (naive vs filtered)",
        "",
        "| Instrument | Variant | Trades | WR | Avg | Net | **Sharpe** | PF | MaxDD | Burn% |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sym in ["MES", "M2K", "MNQ", "NIY"]:
        for variant in ["naive", "filtered"]:
            key = f"{sym}_{variant}"
            if key not in stats_by_sym:
                continue
            s = stats_by_sym[key]
            lines.append(
                f"| {sym} | {variant} | {s.get('n_trades')} | {s.get('win_rate','')} | "
                f"${s.get('avg_net_usd','')} | ${s.get('total_net_usd','')} | "
                f"**{s.get('sharpe','')}** | {s.get('profit_factor','')} | "
                f"${s.get('max_dd_usd','')} | {s.get('cost_burn_pct','')}% |"
            )
    lines.append("")

    # Walk-forward per instrument (filtered variant only)
    lines.append("## Walk-Forward (filtered variant)")
    lines.append("")
    for sym in ["MES", "M2K", "MNQ", "NIY"]:
        key = f"{sym}_filtered"
        if key not in wf_by_sym or not wf_by_sym[key]:
            continue
        wf = wf_by_sym[key]
        n_prof = sum(1 for w in wf if w["oos_profitable"])
        avg_oos_sh = np.mean([w["oos_sharpe"] for w in wf])
        avg_is_sh = np.mean([w["is_sharpe"] for w in wf])
        ratio = (avg_oos_sh / avg_is_sh) if avg_is_sh > 0 else 0
        lines.append(f"### {sym} (filtered)")
        lines.append(f"- Profitable OOS windows: **{n_prof}/{len(wf)}**")
        lines.append(f"- IS avg Sharpe: {avg_is_sh:.2f} | OOS avg Sharpe: **{avg_oos_sh:.2f}** | Ratio: {ratio:.2f}")
        lines.append("")

    # Correlation matrix
    lines.append("## Correlation Matrix (daily PnL$)")
    lines.append("")
    lines.append("| | " + " | ".join(corr.columns) + " |")
    lines.append("|---|" + "---|" * len(corr.columns))
    for idx, row in corr.iterrows():
        lines.append(f"| **{idx}** | " + " | ".join(str(v) for v in row) + " |")
    lines.append("")

    # Verdict (filtered)
    lines.append("## Verdict (filtered variant)")
    lines.append("")
    mes_sharpe = stats_by_sym.get("MES_filtered", {}).get("sharpe", 0)
    lines.append(f"**MES baseline filtered Sharpe: {mes_sharpe}**")
    lines.append("")
    for sym in ["M2K", "MNQ", "NIY"]:
        key = f"{sym}_filtered"
        if key not in stats_by_sym:
            continue
        s = stats_by_sym[key]
        wf = wf_by_sym.get(key, [])
        n_prof = sum(1 for w in wf if w["oos_profitable"])
        oos_ok = len(wf) > 0 and n_prof >= len(wf) / 2
        corr_vs_mes = float(corr.loc[sym, "MES"]) if sym in corr.index and "MES" in corr.columns else 1.0
        beats_mes = s.get("sharpe", 0) > mes_sharpe
        # GO if: beats MES standalone AND decorrelated enough (<0.7) AND OOS robust
        verdict = "GO" if (beats_mes and corr_vs_mes < 0.7 and oos_ok) else \
                  "REPLACE_MES" if (beats_mes and corr_vs_mes >= 0.7) else "KILL"
        lines.append(
            f"- **{sym}**: Sharpe {s.get('sharpe',0)} (vs MES {mes_sharpe}) | "
            f"OOS prof {n_prof}/{len(wf)} | corr vs MES {corr_vs_mes:.2f} → **{verdict}**"
        )
    lines.append("")

    out_file = OUT_DIR / f"overnight_indices_{today}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Report: {out_file}")

    # Print summary to stdout
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
