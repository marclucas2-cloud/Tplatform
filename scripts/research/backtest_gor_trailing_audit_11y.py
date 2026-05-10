#!/usr/bin/env python3
"""Audit backtest: trailing-vs-fixed on Gold-Oil Rotation, 11-year window.

This run extends the 2026-05-06 trailing studies (5Y window, 126 trades) to
the long parquets (2015-2026) and tightens three things found in the audit:

1. Realistic costs: commission + 1-tick slippage per leg, per symbol. Without
   it the baseline gets a free entry/exit and trailing variants pay no extra
   for their additional fills (trail SL hits triple the stop count vs fixed).
2. Sharpe annualisation: per-trade frequency, not a flat sqrt(252). With ~24
   trades/year the legacy formula was inflating Sharpe by ~3.2x. The relative
   ranking is unchanged, but the absolute numbers are now interpretable.
3. Same five families as the smarter-variants study (fixed, simple trail,
   arm-1R then trail, ATR2 trail no TP, breakeven-then-ATR no TP), tested in
   one harness so the comparison is apples-to-apples on the same trade list.

Conservative path assumptions (mirrors the 5Y studies):
- Same-day high/low does NOT tighten the stop within the same candle. The
  ratchet is applied at the close and active from the next bar onwards.
- Trade exit priority within a bar: gap fill at open > intrabar SL/TP > trail
  update > time exit on bar `entry + HOLD_DAYS - 1`.

Output: reports/research/gor_trailing_audit_11y_2026-05-06.md
        data/research/gor_trailing_audit_11y_2026-05-06.json
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "reports" / "research"
JSON_DIR = ROOT / "data" / "research"

# Contract specs. `cost` is round-trip commission (matches the 5Y study).
# `tick` and `mult` give the slippage cost per leg: 1 tick * multiplier.
SPECS = {
    "MGC": {"mult": 10.0, "tick": 0.10, "cost": 2.49},
    "MCL": {"mult": 100.0, "tick": 0.01, "cost": 2.49},
}

LOOKBACK = 20
MIN_EDGE = 0.02
SL_PCT = 0.02
TP_PCT = 0.04
HOLD_DAYS = 10
ATR_LOOKBACK = 14
TRADING_DAYS_PER_YEAR = 252

TRAIL_GRID = [0.004, 0.006, 0.008, 0.010, 0.0125, 0.015, 0.02]


@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_gross: float
    pnl_net: float
    exit_reason: str
    bars_held: int


# ---------------------------------------------------------------------------
# Data and indicators
# ---------------------------------------------------------------------------

def load_long(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_LONG.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    # Keep only the columns we use; drop NaN rows.
    return df[["open", "high", "low", "close"]].dropna()


def compute_atr(df: pd.DataFrame, n: int = ATR_LOOKBACK) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


# ---------------------------------------------------------------------------
# Exit families
# ---------------------------------------------------------------------------

class ExitFamily:
    """Encapsulates the per-bar stop/TP update rules.

    Each family receives the bar context and the running state, and returns
    either an exit (idx, price, reason) or None to keep the position open.
    The harness applies gap-fill priority and intra-bar SL/TP before calling
    the family's `update_after_bar` to refresh state for the next bar.
    """

    label: str

    def initial_state(self, entry_price: float, atr_at_entry: float) -> dict:
        return {
            "sl": entry_price * (1 - SL_PCT),
            "tp": entry_price * (1 + TP_PCT),
            "highest": entry_price,
            "atr": atr_at_entry,
            "armed": False,
        }

    def update_after_bar(self, state: dict, bar_high: float, bar_low: float, bar_close: float) -> None:
        state["highest"] = max(state["highest"], bar_high)


class FixedExits(ExitFamily):
    label = "baseline_fixed_2_4"


class SimpleTrailFamily(ExitFamily):
    """Initial 2% SL becomes a `highest * (1 - trail_pct)` ratchet."""

    def __init__(self, trail_pct: float):
        self.trail_pct = trail_pct
        self.label = f"trail_{trail_pct*100:.2f}pct"

    def update_after_bar(self, state, bar_high, bar_low, bar_close):
        super().update_after_bar(state, bar_high, bar_low, bar_close)
        proposed = state["highest"] * (1 - self.trail_pct)
        state["sl"] = max(state["sl"], proposed)


class ArmThenTrail(ExitFamily):
    """Stay on fixed SL/TP until +1R; then activate `trail_pct` ratchet, TP unchanged."""
    label = "arm_1R_then_trail_2pct"

    def __init__(self, trail_pct: float = 0.02):
        self.trail_pct = trail_pct

    def update_after_bar(self, state, bar_high, bar_low, bar_close):
        super().update_after_bar(state, bar_high, bar_low, bar_close)
        entry = state.get("entry")
        if entry is None:
            return
        one_R = entry * SL_PCT  # initial risk per unit
        if not state["armed"] and bar_close >= entry + one_R:
            state["armed"] = True
        if state["armed"]:
            proposed = state["highest"] * (1 - self.trail_pct)
            state["sl"] = max(state["sl"], proposed)


class ATRTrailNoTP(ExitFamily):
    """Initial 2% SL, then trail at `highest - mult * ATR(14)`. No TP cap."""
    label = "atr2_trail_no_tp"

    def __init__(self, mult: float = 2.0):
        self.mult = mult

    def initial_state(self, entry_price, atr_at_entry):
        s = super().initial_state(entry_price, atr_at_entry)
        s["tp"] = math.inf
        return s

    def update_after_bar(self, state, bar_high, bar_low, bar_close):
        super().update_after_bar(state, bar_high, bar_low, bar_close)
        if not math.isfinite(state["atr"]) or state["atr"] <= 0:
            return
        proposed = state["highest"] - self.mult * state["atr"]
        state["sl"] = max(state["sl"], proposed)


class BreakevenThenATR(ExitFamily):
    """Move stop to breakeven at +1R, start 1*ATR trailing at +2R, no TP cap."""
    label = "breakeven_1R_then_atr1_no_tp"

    def initial_state(self, entry_price, atr_at_entry):
        s = super().initial_state(entry_price, atr_at_entry)
        s["tp"] = math.inf
        s["stage"] = 0  # 0 = initial, 1 = breakeven, 2 = ATR-trail
        return s

    def update_after_bar(self, state, bar_high, bar_low, bar_close):
        super().update_after_bar(state, bar_high, bar_low, bar_close)
        entry = state.get("entry")
        if entry is None:
            return
        one_R = entry * SL_PCT
        if state["stage"] < 1 and bar_close >= entry + one_R:
            state["sl"] = max(state["sl"], entry)
            state["stage"] = 1
        if state["stage"] < 2 and bar_close >= entry + 2 * one_R:
            state["stage"] = 2
        if state["stage"] >= 2 and math.isfinite(state["atr"]) and state["atr"] > 0:
            proposed = state["highest"] - 1.0 * state["atr"]
            state["sl"] = max(state["sl"], proposed)


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate(
    df: pd.DataFrame,
    atr: pd.Series,
    entry_idx: int,
    family: ExitFamily,
) -> tuple[int, float, str]:
    entry_price = float(df["open"].iloc[entry_idx])
    atr_at_entry = float(atr.iloc[entry_idx]) if entry_idx < len(atr) else float("nan")
    state = family.initial_state(entry_price, atr_at_entry)
    state["entry"] = entry_price

    last_idx = min(entry_idx + HOLD_DAYS, len(df))

    for j in range(entry_idx, last_idx):
        opn = float(df["open"].iloc[j])
        high = float(df["high"].iloc[j])
        low = float(df["low"].iloc[j])
        close = float(df["close"].iloc[j])

        # Gap priority on bars after entry
        if j > entry_idx:
            if opn <= state["sl"]:
                return j, opn, "gap_sl"
            if math.isfinite(state["tp"]) and opn >= state["tp"]:
                return j, opn, "gap_tp"

        # Intrabar SL/TP. SL takes priority over TP — conservative under
        # uncertain intra-day path (we cannot prove TP came first).
        if low <= state["sl"]:
            return j, state["sl"], "sl"
        if math.isfinite(state["tp"]) and high >= state["tp"]:
            return j, state["tp"], "tp"

        family.update_after_bar(state, high, low, close)

        if j == last_idx - 1:
            return j, close, "time_exit"

    end_idx = min(entry_idx + HOLD_DAYS - 1, len(df) - 1)
    return end_idx, float(df["close"].iloc[end_idx]), "time_exit"


def run_strategy(
    mgc: pd.DataFrame,
    mcl: pd.DataFrame,
    family: ExitFamily,
) -> list[Trade]:
    common = mgc.index.intersection(mcl.index)
    mgc_c = mgc["close"].reindex(common)
    mcl_c = mcl["close"].reindex(common)
    mgc_ret = mgc_c.pct_change(LOOKBACK)
    mcl_ret = mcl_c.pct_change(LOOKBACK)
    atrs = {"MGC": compute_atr(mgc), "MCL": compute_atr(mcl)}

    trades: list[Trade] = []
    last_signal_idx = -10**9

    for i in range(LOOKBACK, len(common) - HOLD_DAYS - 1):
        if i - last_signal_idx < HOLD_DAYS:
            continue
        mgc_r = float(mgc_ret.iloc[i])
        mcl_r = float(mcl_ret.iloc[i])
        if not (np.isfinite(mgc_r) and np.isfinite(mcl_r)):
            continue
        spread = mgc_r - mcl_r
        if abs(spread) < MIN_EDGE:
            continue

        sym = "MGC" if spread > 0 else "MCL"
        df_sym = mgc if sym == "MGC" else mcl
        signal_date = common[i]
        try:
            entry_idx = df_sym.index.get_loc(signal_date) + 1
        except KeyError:
            continue
        if entry_idx >= len(df_sym):
            break
        atr_sym = atrs[sym]
        exit_idx, exit_price, exit_reason = simulate(
            df_sym, atr_sym, entry_idx, family
        )
        spec = SPECS[sym]
        slippage = spec["tick"] * spec["mult"] * 2  # 1 tick per leg
        commission = spec["cost"]  # already round-trip per the 5Y study
        entry_price = float(df_sym["open"].iloc[entry_idx])
        gross = (exit_price - entry_price) * spec["mult"]
        net = gross - commission - slippage
        trades.append(
            Trade(
                symbol=sym,
                entry_date=str(df_sym.index[entry_idx].date()),
                exit_date=str(df_sym.index[exit_idx].date()),
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                pnl_gross=round(gross, 4),
                pnl_net=round(net, 4),
                exit_reason=exit_reason,
                bars_held=int(exit_idx - entry_idx + 1),
            )
        )
        last_signal_idx = i

    return trades


# ---------------------------------------------------------------------------
# Stats — sharpe annualised by trade frequency
# ---------------------------------------------------------------------------

def compute_stats(trades: list[Trade], total_calendar_days: int) -> dict:
    if not trades:
        return {"n": 0, "total_pnl_net": 0.0}

    pnl = np.array([t.pnl_net for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    cum = np.cumsum(pnl)
    peaks = np.maximum.accumulate(cum)
    drawdowns = cum - peaks

    pf = float(wins.sum() / abs(losses.sum())) if losses.size else float("inf")

    years = max(total_calendar_days / 365.25, 1e-6)
    trades_per_year = len(trades) / years
    if pnl.std(ddof=1) > 0 and trades_per_year > 0:
        sharpe_ann = (pnl.mean() / pnl.std(ddof=1)) * math.sqrt(trades_per_year)
    else:
        sharpe_ann = 0.0

    cagr_pnl = pnl.sum() / years
    calmar = cagr_pnl / abs(drawdowns.min()) if drawdowns.min() < 0 else float("inf")

    return {
        "n": len(trades),
        "trades_per_year": round(trades_per_year, 2),
        "total_pnl_gross": round(float(sum(t.pnl_gross for t in trades)), 2),
        "total_pnl_net": round(float(pnl.sum()), 2),
        "pnl_per_year": round(cagr_pnl, 2),
        "avg_pnl_net": round(float(pnl.mean()), 2),
        "median_pnl_net": round(float(np.median(pnl)), 2),
        "win_rate_pct": round(float((pnl > 0).mean()) * 100, 2),
        "sharpe_ann": round(sharpe_ann, 2),
        "profit_factor": round(pf, 2) if math.isfinite(pf) else "inf",
        "max_dd": round(float(drawdowns.min()), 2),
        "calmar": round(calmar, 2) if math.isfinite(calmar) else "inf",
        "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 2),
    }


def walk_forward_stats(trades: list[Trade], n_windows: int = 5) -> dict:
    if not trades:
        return {"windows": [], "oos_profitable_windows": 0}
    dates = sorted({t.entry_date for t in trades})
    starts = pd.to_datetime(pd.Series(dates))
    span_days = (starts.iloc[-1] - starts.iloc[0]).days
    window_days = span_days // n_windows
    is_frac = 0.6
    is_days = int(window_days * is_frac)
    windows = []
    base = starts.iloc[0]
    for w in range(n_windows):
        oos_start = base + pd.Timedelta(days=w * window_days + is_days)
        oos_end = base + pd.Timedelta(days=(w + 1) * window_days)
        oos_trades = [
            t for t in trades
            if oos_start.date() <= pd.Timestamp(t.entry_date).date() <= oos_end.date()
        ]
        if not oos_trades:
            windows.append({
                "window": w + 1,
                "oos_n": 0, "oos_total_pnl": 0.0, "oos_profitable": False,
            })
            continue
        pnl = np.array([t.pnl_net for t in oos_trades])
        windows.append({
            "window": w + 1,
            "oos_period": f"{oos_start.date()}..{oos_end.date()}",
            "oos_n": len(oos_trades),
            "oos_total_pnl": round(float(pnl.sum()), 2),
            "oos_profitable": bool(pnl.sum() > 0),
        })
    prof = sum(1 for w in windows if w["oos_profitable"])
    return {
        "windows": windows,
        "oos_profitable_windows": prof,
        "oos_total_pnl": round(sum(w["oos_total_pnl"] for w in windows), 2),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    mgc = load_long("MGC")
    mcl = load_long("MCL")
    common = mgc.index.intersection(mcl.index)
    span_days = int((common.max() - common.min()).days)

    families: list[ExitFamily] = [FixedExits()]
    families += [SimpleTrailFamily(p) for p in TRAIL_GRID]
    families += [
        ArmThenTrail(0.02),
        ATRTrailNoTP(2.0),
        BreakevenThenATR(),
    ]

    out = []
    for fam in families:
        trades = run_strategy(mgc, mcl, fam)
        st = compute_stats(trades, total_calendar_days=span_days)
        wf = walk_forward_stats(trades)
        exit_mix: dict[str, int] = {}
        for t in trades:
            exit_mix[t.exit_reason] = exit_mix.get(t.exit_reason, 0) + 1
        out.append({
            "label": fam.label,
            "n": st["n"],
            "trades_per_year": st.get("trades_per_year"),
            "total_pnl_net": st["total_pnl_net"],
            "pnl_per_year": st.get("pnl_per_year"),
            "win_rate_pct": st.get("win_rate_pct"),
            "sharpe_ann": st.get("sharpe_ann"),
            "profit_factor": st.get("profit_factor"),
            "max_dd": st.get("max_dd"),
            "calmar": st.get("calmar"),
            "avg_bars_held": st.get("avg_bars_held"),
            "wf_profitable_windows": wf["oos_profitable_windows"],
            "wf_oos_total_pnl": wf.get("oos_total_pnl"),
            "exit_mix": exit_mix,
        })

    table = pd.DataFrame(out)
    cols = [
        "label", "n", "trades_per_year", "total_pnl_net", "pnl_per_year",
        "win_rate_pct", "sharpe_ann", "profit_factor", "max_dd", "calmar",
        "wf_profitable_windows", "wf_oos_total_pnl",
    ]

    baseline = next(r for r in out if r["label"] == FixedExits.label)
    others = [r for r in out if r["label"] != FixedExits.label]
    best = max(others, key=lambda r: (r["total_pnl_net"], r["sharpe_ann"]))

    report = REPORT_DIR / "gor_trailing_audit_11y_2026-05-06.md"
    js = JSON_DIR / "gor_trailing_audit_11y_2026-05-06.json"

    def md_table(df, cols):
        df = df[cols].copy()
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in df.itertuples(index=False):
            lines.append("| " + " | ".join(str(x) for x in row) + " |")
        return "\n".join(lines)

    body = [
        "# Gold-Oil Rotation - Trailing Audit (11Y) - 2026-05-06",
        "",
        "## Setup",
        f"- Data: `MGC_LONG.parquet` and `MCL_LONG.parquet` ({common.min().date()} -> {common.max().date()}, {span_days} calendar days)",
        f"- Entry: 20d momentum spread, edge {MIN_EDGE:.0%}, next-open entry",
        f"- Baseline: fixed SL {SL_PCT:.0%}, fixed TP {TP_PCT:.0%}, max hold {HOLD_DAYS} bars",
        "- Costs: round-trip commission $2.49 + 1-tick slippage per leg ($2 round-trip MGC, $2 round-trip MCL)",
        f"- Sharpe annualised by trade frequency: `(mean/std) * sqrt(trades_per_year)`, not flat sqrt(252)",
        "",
        "## Variants",
        "- `baseline_fixed_2_4`: fixed SL/TP",
        "- `trail_X.XXpct`: SL ratchets to `highest * (1 - X.XX%)`, TP unchanged",
        "- `arm_1R_then_trail_2pct`: fixed SL/TP until +1R, then 2% trailing",
        "- `atr2_trail_no_tp`: trail at `highest - 2 * ATR(14)`, no TP",
        "- `breakeven_1R_then_atr1_no_tp`: breakeven at +1R, then 1*ATR trail at +2R, no TP",
        "",
        "## Summary",
        md_table(table, cols),
        "",
        "## Baseline vs Best Managed",
        f"- Baseline `{baseline['label']}`: net PnL `${baseline['total_pnl_net']}` / yr `${baseline['pnl_per_year']}` / Sharpe `{baseline['sharpe_ann']}` / DD `${baseline['max_dd']}` / Calmar `{baseline['calmar']}` / WF `{baseline['wf_profitable_windows']}/5`",
        f"- Best managed `{best['label']}`: net PnL `${best['total_pnl_net']}` / yr `${best['pnl_per_year']}` / Sharpe `{best['sharpe_ann']}` / DD `${best['max_dd']}` / Calmar `{best['calmar']}` / WF `{best['wf_profitable_windows']}/5`",
        f"- Delta net PnL: `${round(best['total_pnl_net'] - baseline['total_pnl_net'], 2)}`",
        f"- Delta Sharpe: `{round(best['sharpe_ann'] - baseline['sharpe_ann'], 2)}`",
        f"- Delta MaxDD: `${round(best['max_dd'] - baseline['max_dd'], 2)}` (less negative = better)",
        "",
        "## Exit Mix",
        f"- Baseline: `{baseline['exit_mix']}`",
        f"- Best managed: `{best['exit_mix']}`",
    ]

    report.write_text("\n".join(body) + "\n", encoding="utf-8")
    js.write_text(json.dumps({"results": out, "span_days": span_days}, indent=2), encoding="utf-8")
    print(f"REPORT {report}")
    print(table[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
