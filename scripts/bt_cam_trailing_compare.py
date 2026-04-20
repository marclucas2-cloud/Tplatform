"""BT-CAM-TRAILING — Compare H0 (fixed TP) vs H1 (ATR trail) vs H2 (chandelier close).

Standalone vectorized backtest, does not modify BacktesterV2. Replicates the
CrossAssetMomentum rotation logic (20d lookback, 20d rebal, 2% min momentum,
5 assets: MES/MNQ/M2K/MGC/MCL) and applies three exit policies post-entry.

Outputs:
  tmp/backtest_cam_trailing/H0_trades.json
  tmp/backtest_cam_trailing/H1_trades.json
  tmp/backtest_cam_trailing/H2_trades.json
  tmp/backtest_cam_trailing/compare_summary.json
  tmp/backtest_cam_trailing/REPORT.md

Usage:
  python scripts/bt_cam_trailing_compare.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "tmp" / "backtest_cam_trailing"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------- CAM params (match strategies_v2/futures/cross_asset_momentum.py) ----------
UNIVERSE = ["MES", "MNQ", "M2K", "MGC", "MCL"]
LOOKBACK = 20
REBAL_DAYS = 20
MIN_MOMENTUM = 0.02
TP_PCT = 0.08
SL_PCT = 0.03
COST_BPS_ROUND_TRIP = 5  # realistic micros on IBKR (slip + fees)

# Trailing H1/H2 parameters
ATR_PERIOD = 14
H1_ATR_MULT = 2.0      # SL = high - 2*ATR, triggered after TP reached
H2_ATR_MULT = 3.0      # chandelier close-based, after TP reached
MAX_HOLD_MULT = 3      # H1/H2 allowed to run up to 3x REBAL_DAYS if trailing active


# ---------- Data ----------
def load_daily(sym: str) -> pd.DataFrame | None:
    p = ROOT / "data" / "futures" / f"{sym}_1D.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.columns = [c.lower() for c in df.columns]
    if "datetime" in df.columns:
        df.index = pd.to_datetime(df["datetime"])
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    keep = [c for c in ["open", "high", "low", "close"] if c in df.columns]
    return df[keep].astype(float).sort_index()


def compute_atr(df: pd.DataFrame, n: int = ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ---------- Exit simulators (single trade, long only) ----------
@dataclass
class TradeResult:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    gross_return: float
    exit_reason: str


def _conservative_bar_outcome(bar, sl: float, tp: float) -> tuple[str | None, float]:
    """Return ('SL'|'TP'|None, exit_price) using conservative same-bar rule:
    if both SL and TP touched, assume SL hit first (loss > gain bias)."""
    if bar["low"] <= sl:
        return ("SL", sl)
    if bar["high"] >= tp:
        return ("TP", tp)
    return (None, 0.0)


def simulate_H0(df: pd.DataFrame, i_entry: int, entry_price: float) -> TradeResult:
    """Baseline: fixed TP +8% / SL -3% / force-exit at REBAL_DAYS."""
    sl = entry_price * (1 - SL_PCT)
    tp = entry_price * (1 + TP_PCT)
    last_idx = min(i_entry + REBAL_DAYS, len(df) - 1)

    for j in range(i_entry + 1, last_idx + 1):
        bar = df.iloc[j]
        outcome, exit_p = _conservative_bar_outcome(bar, sl, tp)
        if outcome is not None:
            return TradeResult(i_entry, j, entry_price, exit_p,
                               exit_p / entry_price - 1, outcome)

    exit_p = float(df.iloc[last_idx]["close"])
    return TradeResult(i_entry, last_idx, entry_price, exit_p,
                       exit_p / entry_price - 1, "REBAL")


def simulate_H1(df: pd.DataFrame, i_entry: int, entry_price: float) -> TradeResult:
    """After TP +8% touched, switch to trailing SL = max(TP, high - 2*ATR14).
    Ratchet up only. Exit on trailing touched or at MAX_HOLD_MULT * REBAL_DAYS."""
    atr = compute_atr(df)
    sl = entry_price * (1 - SL_PCT)
    tp_trigger = entry_price * (1 + TP_PCT)
    trailing_active = False
    highest = entry_price
    last_idx = min(i_entry + REBAL_DAYS * MAX_HOLD_MULT, len(df) - 1)

    for j in range(i_entry + 1, last_idx + 1):
        bar = df.iloc[j]

        if not trailing_active:
            outcome, exit_p = _conservative_bar_outcome(bar, sl, tp_trigger)
            if outcome == "SL":
                return TradeResult(i_entry, j, entry_price, exit_p,
                                   exit_p / entry_price - 1, "SL")
            if outcome == "TP":
                trailing_active = True
                highest = max(bar["high"], tp_trigger)
                atr_j = atr.iloc[j]
                if pd.isna(atr_j):
                    sl = tp_trigger
                else:
                    sl = max(tp_trigger, highest - H1_ATR_MULT * atr_j)
                if bar["close"] <= sl:
                    return TradeResult(i_entry, j, entry_price, sl,
                                       sl / entry_price - 1, "TRAIL")
        else:
            if bar["high"] > highest:
                highest = float(bar["high"])
            atr_j = atr.iloc[j]
            if not pd.isna(atr_j):
                new_sl = max(tp_trigger, highest - H1_ATR_MULT * atr_j)
                sl = max(sl, new_sl)
            if bar["low"] <= sl:
                return TradeResult(i_entry, j, entry_price, sl,
                                   sl / entry_price - 1, "TRAIL")

    exit_p = float(df.iloc[last_idx]["close"])
    reason = "TIME" if trailing_active else "REBAL_NO_TP"
    return TradeResult(i_entry, last_idx, entry_price, exit_p,
                       exit_p / entry_price - 1, reason)


def simulate_H2(df: pd.DataFrame, i_entry: int, entry_price: float) -> TradeResult:
    """Chandelier close-based: after TP touched, trail = max_close - 3*ATR14.
    Exit on close below trail. Less whipsawy than H1."""
    atr = compute_atr(df)
    sl = entry_price * (1 - SL_PCT)
    tp_trigger = entry_price * (1 + TP_PCT)
    trailing_active = False
    max_close = entry_price
    last_idx = min(i_entry + REBAL_DAYS * MAX_HOLD_MULT, len(df) - 1)

    for j in range(i_entry + 1, last_idx + 1):
        bar = df.iloc[j]

        if not trailing_active:
            outcome, exit_p = _conservative_bar_outcome(bar, sl, tp_trigger)
            if outcome == "SL":
                return TradeResult(i_entry, j, entry_price, exit_p,
                                   exit_p / entry_price - 1, "SL")
            if outcome == "TP":
                trailing_active = True
                max_close = max(float(bar["close"]), tp_trigger)
        else:
            if bar["close"] > max_close:
                max_close = float(bar["close"])
            atr_j = atr.iloc[j]
            if pd.isna(atr_j):
                continue
            trail = max_close - H2_ATR_MULT * atr_j
            if bar["close"] <= trail:
                exit_p = float(bar["close"])
                return TradeResult(i_entry, j, entry_price, exit_p,
                                   exit_p / entry_price - 1, "TRAIL")

    exit_p = float(df.iloc[last_idx]["close"])
    reason = "TIME" if trailing_active else "REBAL_NO_TP"
    return TradeResult(i_entry, last_idx, entry_price, exit_p,
                       exit_p / entry_price - 1, reason)


# ---------- CAM rotation driver ----------
def run_variant(
    data: dict[str, pd.DataFrame],
    simulate_fn: Callable[[pd.DataFrame, int, float], TradeResult],
) -> list[dict]:
    # Align on common index (intersection)
    common_idx = None
    for df in data.values():
        common_idx = df.index if common_idx is None else common_idx.intersection(df.index)
    aligned = {s: data[s].loc[common_idx].copy() for s in data}
    closes = pd.DataFrame({s: aligned[s]["close"] for s in aligned})

    trades: list[dict] = []
    n = len(common_idx)
    i = LOOKBACK + 1
    while i < n - 1:
        # 20d return per asset using closes[i] / closes[i - LOOKBACK]
        returns = {}
        for s in aligned:
            c = closes[s]
            if i - LOOKBACK < 0:
                continue
            r = c.iloc[i] / c.iloc[i - LOOKBACK] - 1
            if not np.isnan(r):
                returns[s] = float(r)

        if not returns:
            i += 1
            continue
        winner = max(returns, key=returns.get)
        if returns[winner] < MIN_MOMENTUM:
            i += 1
            continue

        entry_price = float(closes[winner].iloc[i])
        res = simulate_fn(aligned[winner], i, entry_price)
        net_return = res.gross_return - COST_BPS_ROUND_TRIP / 10_000

        trades.append({
            "entry_date": str(common_idx[res.entry_idx].date()),
            "exit_date": str(common_idx[res.exit_idx].date()),
            "symbol": winner,
            "entry_price": round(res.entry_price, 4),
            "exit_price": round(res.exit_price, 4),
            "gross_return_pct": round(res.gross_return * 100, 4),
            "net_return_pct": round(net_return * 100, 4),
            "bars_held": res.exit_idx - res.entry_idx,
            "exit_reason": res.exit_reason,
            "momentum_at_entry": round(returns[winner] * 100, 2),
        })

        # Skip to next rebalance slot (minimum REBAL_DAYS after entry, OR exit if later)
        next_i = max(res.entry_idx + REBAL_DAYS, res.exit_idx + 1)
        i = next_i

    return trades


# ---------- Metrics ----------
def compute_metrics(trades: list[dict], total_calendar_days: int) -> dict:
    if not trades:
        return {
            "num_trades": 0, "win_rate": 0, "profit_factor": 0,
            "cagr": 0, "sharpe": 0, "max_dd": 0, "avg_hold_days": 0,
            "avg_trade_net_pct": 0, "total_return_pct": 0,
            "avg_gain_post_tp_pct": 0, "tp_triggered_pct": 0,
        }
    rets = np.array([t["net_return_pct"] / 100 for t in trades])
    eq = (1 + rets).cumprod()
    years = total_calendar_days / 365.25
    total_ret = eq[-1] - 1
    cagr = (eq[-1] ** (1 / years) - 1) if years > 0 else 0
    running_max = np.maximum.accumulate(eq)
    dd = (eq - running_max) / running_max
    max_dd = abs(dd.min()) if len(dd) > 0 else 0
    # Per-trade Sharpe (non-overlapping by construction), annualize by trades/yr
    trades_per_year = len(trades) / max(years, 1e-6)
    sharpe = (rets.mean() / rets.std() * np.sqrt(trades_per_year)) if rets.std() > 0 else 0
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    wr = len(wins) / len(rets)
    pf = (wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() < 0 else float("inf")
    avg_hold = float(np.mean([t["bars_held"] for t in trades]))

    # Post-TP gain: for trades that touched TP (exit_reason in TP, TRAIL, TIME), avg net gain
    post_tp_rets = [t["net_return_pct"] / 100 for t in trades
                    if t["exit_reason"] in ("TP", "TRAIL", "TIME")]
    tp_triggered = len(post_tp_rets) / len(trades)
    avg_post_tp = float(np.mean(post_tp_rets)) if post_tp_rets else 0.0

    return {
        "num_trades": len(trades),
        "win_rate": round(wr * 100, 1),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "avg_hold_days": round(avg_hold, 1),
        "avg_trade_net_pct": round(rets.mean() * 100, 3),
        "total_return_pct": round(total_ret * 100, 2),
        "avg_gain_post_tp_pct": round(avg_post_tp * 100, 3),
        "tp_triggered_pct": round(tp_triggered * 100, 1),
    }


def exit_reason_breakdown(trades: list[dict]) -> dict:
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return reasons


# ---------- Walk-forward robustness ----------
def wf_per_variant(
    data: dict[str, pd.DataFrame],
    simulate_fn: Callable,
    n_windows: int = 5,
) -> list[dict]:
    # Align on common index
    common_idx = None
    for df in data.values():
        common_idx = df.index if common_idx is None else common_idx.intersection(df.index)
    n = len(common_idx)
    window_size = n // n_windows
    results = []

    for w in range(n_windows):
        start = w * window_size
        end = min(start + window_size, n)
        if end - start < 100:
            continue
        sub = {s: data[s].loc[common_idx[start:end]] for s in data}
        trades = run_variant(sub, simulate_fn)
        days = (common_idx[end - 1] - common_idx[start]).days
        metrics = compute_metrics(trades, days)
        results.append({
            "window": w,
            "start": str(common_idx[start].date()),
            "end": str(common_idx[end - 1].date()),
            **metrics,
        })
    return results


# ---------- Main ----------
def main() -> None:
    print("=" * 88)
    print("  BT-CAM-TRAILING — H0 (fixed TP) vs H1 (ATR trail) vs H2 (chandelier)")
    print("=" * 88)

    data = {}
    for sym in UNIVERSE:
        df = load_daily(sym)
        if df is None:
            print(f"  WARN: {sym} data missing, skipping")
            continue
        data[sym] = df
        print(f"  {sym}: {len(df)} bars, {df.index.min().date()} -> {df.index.max().date()}")

    if len(data) < 2:
        print("  FATAL: insufficient data (need >=2 assets)")
        return

    common_idx = None
    for df in data.values():
        common_idx = df.index if common_idx is None else common_idx.intersection(df.index)
    total_days = (common_idx[-1] - common_idx[0]).days
    print(f"\n  Common range: {common_idx[0].date()} -> {common_idx[-1].date()} ({total_days}d, ~{total_days/365.25:.1f}Y)")
    print(f"  Cost: {COST_BPS_ROUND_TRIP} bps round-trip")
    print()

    variants = [
        ("H0", "Fixed TP +8% / SL -3% / force exit at REBAL", simulate_H0),
        ("H1", "ATR trailing post-TP (max_high - 2*ATR14)", simulate_H1),
        ("H2", "Chandelier close-based (max_close - 3*ATR14)", simulate_H2),
    ]

    summary = {"params": {
        "universe": list(data.keys()),
        "lookback_days": LOOKBACK,
        "rebal_days": REBAL_DAYS,
        "min_momentum": MIN_MOMENTUM,
        "tp_pct": TP_PCT,
        "sl_pct": SL_PCT,
        "cost_bps_round_trip": COST_BPS_ROUND_TRIP,
        "atr_period": ATR_PERIOD,
        "h1_atr_mult": H1_ATR_MULT,
        "h2_atr_mult": H2_ATR_MULT,
        "max_hold_mult": MAX_HOLD_MULT,
        "total_days": total_days,
        "start": str(common_idx[0].date()),
        "end": str(common_idx[-1].date()),
    }}

    variant_metrics = {}
    for tag, desc, fn in variants:
        trades = run_variant(data, fn)
        metrics = compute_metrics(trades, total_days)
        reasons = exit_reason_breakdown(trades)
        wf = wf_per_variant(data, fn, n_windows=5)
        profitable_windows = sum(1 for w in wf if w.get("total_return_pct", 0) > 0)

        (OUT_DIR / f"{tag}_trades.json").write_text(json.dumps({
            "description": desc,
            "metrics": metrics,
            "exit_reasons": reasons,
            "wf_windows": wf,
            "wf_profitable_windows": f"{profitable_windows}/{len(wf)}",
            "trades": trades,
        }, indent=2))

        variant_metrics[tag] = {
            "description": desc,
            "metrics": metrics,
            "exit_reasons": reasons,
            "wf_profitable_windows": f"{profitable_windows}/{len(wf)}",
            "wf_avg_oos_sharpe": round(np.mean([w.get("sharpe", 0) for w in wf]), 2),
        }
        print(f"  {tag} ({desc}):")
        print(f"     trades={metrics['num_trades']}  Sharpe={metrics['sharpe']}  "
              f"CAGR={metrics['cagr_pct']}%  DD={metrics['max_dd_pct']}%  "
              f"WR={metrics['win_rate']}%  PF={metrics['profit_factor']}")
        print(f"     exit_reasons={reasons}  WF profitable={profitable_windows}/{len(wf)}")
        print()

    summary["variants"] = variant_metrics
    (OUT_DIR / "compare_summary.json").write_text(json.dumps(summary, indent=2))

    # ---- Markdown report ----
    lines = []
    lines.append("# BT-CAM-TRAILING — Results")
    lines.append("")
    lines.append(f"**Généré** : {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Periode** : {common_idx[0].date()} -> {common_idx[-1].date()} ({total_days}d, {total_days/365.25:.1f}Y)")
    lines.append(f"**Univers** : {', '.join(data.keys())}")
    lines.append(f"**Coûts** : {COST_BPS_ROUND_TRIP} bps round-trip (slip + fees micros IBKR)")
    lines.append("")
    lines.append("## Comparatif H0 / H1 / H2")
    lines.append("")
    lines.append("| Variante | Trades | Sharpe | CAGR% | MaxDD% | WR% | PF | AvgHold | AvgPostTP% | WF prof. |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for tag, _, _ in variants:
        m = variant_metrics[tag]["metrics"]
        wf = variant_metrics[tag]["wf_profitable_windows"]
        lines.append(
            f"| **{tag}** | {m['num_trades']} | {m['sharpe']} | {m['cagr_pct']} | "
            f"{m['max_dd_pct']} | {m['win_rate']} | {m['profit_factor']} | "
            f"{m['avg_hold_days']} | {m['avg_gain_post_tp_pct']} | {wf} |"
        )
    lines.append("")
    lines.append("## Exit reasons breakdown")
    lines.append("")
    for tag, _, _ in variants:
        r = variant_metrics[tag]["exit_reasons"]
        lines.append(f"- **{tag}** : {r}")
    lines.append("")
    lines.append("## Legende")
    lines.append("")
    lines.append("- **AvgPostTP%** : gain moyen (net) des trades ayant touche le +8% TP. Mesure directe du manque a gagner / alpha additionnel du trailing.")
    lines.append("- **WF prof.** : fenetres rolling 1/5 profitables (robustesse dans le temps).")
    lines.append("- **H0** : TP/SL fixe, exit force a REBAL (baseline CAM actuel).")
    lines.append("- **H1** : trailing ATR14 x2 depuis high, active apres TP +8%, ratchet up-only.")
    lines.append("- **H2** : chandelier close-based, trail = max_close - 3*ATR14 apres TP +8%, exit sur close sous trail.")
    lines.append("")
    lines.append("## Décision")
    lines.append("")

    h0 = variant_metrics["H0"]["metrics"]
    h1 = variant_metrics["H1"]["metrics"]
    h2 = variant_metrics["H2"]["metrics"]
    best_sharpe_tag = max(variants, key=lambda v: variant_metrics[v[0]]["metrics"]["sharpe"])[0]
    best_cagr_tag = max(variants, key=lambda v: variant_metrics[v[0]]["metrics"]["cagr_pct"])[0]
    best_dd_tag = min(variants, key=lambda v: variant_metrics[v[0]]["metrics"]["max_dd_pct"])[0]

    lines.append(f"- Meilleur **Sharpe** : **{best_sharpe_tag}** ({variant_metrics[best_sharpe_tag]['metrics']['sharpe']})")
    lines.append(f"- Meilleur **CAGR**   : **{best_cagr_tag}** ({variant_metrics[best_cagr_tag]['metrics']['cagr_pct']}%)")
    lines.append(f"- Meilleur **MaxDD**  : **{best_dd_tag}** ({variant_metrics[best_dd_tag]['metrics']['max_dd_pct']}%)")
    lines.append("")
    lines.append("### Proposition de verdict")
    lines.append("")
    lines.append("- Si **H0 domine** (Sharpe + CAGR + DD) : close le ticket FEAT-CAM-TRAIL (no action), log dans `docs/research/dropped_hypotheses.md`.")
    lines.append("- Si **H1 ou H2 domine** sur Sharpe ET CAGR >= H0 ET WF >= 50% : spec de merge dans `cross_asset_momentum.py`, review post-WF 10Y quand data dispo.")
    lines.append("- Si **pattern mixte** (trailing ameliore CAGR mais degrade Sharpe/DD) : diagnostiquer via exit_reasons - probablement overfit sur 1 asset (MCL), pas pour le 5-asset universe.")
    lines.append("")
    lines.append("**Attention N=1 weekend Iran** : ce backtest n'est pas un test sur event geopolitique. Il mesure l'alpha/drag structurel du trailing sur 5Y rotation momentum. L'event du 19 avril 2026 sort de la distribution.")

    (OUT_DIR / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  Report -> {OUT_DIR / 'REPORT.md'}")
    print(f"  Summary -> {OUT_DIR / 'compare_summary.json'}")


if __name__ == "__main__":
    main()
