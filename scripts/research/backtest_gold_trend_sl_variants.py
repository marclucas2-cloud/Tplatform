"""Backtest GoldTrendMGC SL variants — find optimal SL given deleveraging constraint.

Question: le SL natif (1.5%) est jamais touche en prod car deleveraging level_3
ferme la position a -1.8% NAV (= ~-0.4% MGC pour 1 contrat sur 10K NAV).

Variantes testees:
  V0_baseline : SL 1.5%, TP 3.0% (params strat actuels, backtest reference)
  V1_match_dlv: SL 0.4%, TP 0.8% (matche deleveraging, R/R 2:1 maintenu)
  V2_match_dlv_long: SL 0.4%, TP 3.0% (matche deleveraging, TP large pour asymetrie)
  V3_baseline_emule: SL 1.5%, TP 3.0% MAIS close si DD intra-trade > 1.8% NAV emule
                     (= ce qui se passe REELLEMENT en prod avec deleveraging)

Output: comparison table + recommandation.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

MGC_PATH = ROOT / "data" / "futures" / "MGC_LONG.parquet"

# Setup
INITIAL_NAV = 10_000.0  # €10K = approx live NAV
CONTRACT_NOTIONAL_PT = 10.0  # MGC = $10/pt
MAX_HOLD_DAYS = 10
EMA_PERIOD = 20
COST_RT = 5.70  # 2 ticks slippage + commission RT

DELEVERAGING_LEVEL3_PCT = 0.018  # 1.8% NAV = position close


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def backtest(df: pd.DataFrame, sl_pct: float, tp_pct: float, emulate_deleveraging: bool = False, label: str = "") -> dict:
    """Backtest GoldTrendMGC with given SL/TP. Optional deleveraging emulation."""
    df = df.copy()
    df["ema20"] = ema(df["close"], EMA_PERIOD)

    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    sl_level = 0.0
    tp_level = 0.0
    trades = []

    for i in range(EMA_PERIOD + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if not in_pos:
            # Entry: close > EMA20 (use prev bar to avoid lookahead)
            if prev["close"] > prev["ema20"]:
                # Enter at today's open
                entry_price = float(row["open"])
                sl_level = entry_price * (1 - sl_pct)
                tp_level = entry_price * (1 + tp_pct)
                entry_idx = i
                in_pos = True
        else:
            hi = float(row["high"])
            lo = float(row["low"])
            cl = float(row["close"])
            days_held = i - entry_idx

            exit_price = None
            exit_reason = None

            # Check TP first (intraday touch)
            if hi >= tp_level:
                exit_price = tp_level
                exit_reason = "TP"
            elif lo <= sl_level:
                exit_price = sl_level
                exit_reason = "SL"
            elif emulate_deleveraging:
                # Check if DD on this position > 1.8% NAV
                # PnL pt = (lo - entry) per pt; loss = (entry-lo)*$10
                worst_loss_usd = (entry_price - lo) * CONTRACT_NOTIONAL_PT
                if worst_loss_usd / INITIAL_NAV > DELEVERAGING_LEVEL3_PCT:
                    # Close at deleveraging trigger price
                    deleverage_price = entry_price - (DELEVERAGING_LEVEL3_PCT * INITIAL_NAV / CONTRACT_NOTIONAL_PT)
                    exit_price = deleverage_price
                    exit_reason = "DELEVERAGE"

            if exit_price is None and days_held >= MAX_HOLD_DAYS:
                exit_price = cl
                exit_reason = "TIME"

            if exit_price is not None:
                pnl_usd = (exit_price - entry_price) * CONTRACT_NOTIONAL_PT - COST_RT
                trades.append({
                    "entry_dt": df.index[entry_idx],
                    "exit_dt": df.index[i],
                    "entry": entry_price,
                    "exit": exit_price,
                    "pnl_usd": pnl_usd,
                    "exit_reason": exit_reason,
                    "days_held": days_held,
                })
                in_pos = False

    if not trades:
        return {"label": label, "n_trades": 0, "total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0}

    t = pd.DataFrame(trades)
    total_pnl = t["pnl_usd"].sum()
    win_rate = (t["pnl_usd"] > 0).mean()

    # Sharpe (annualized) - daily PnL aggregated
    daily_pnl = t.set_index("exit_dt")["pnl_usd"].resample("1D").sum().fillna(0)
    sharpe = daily_pnl.mean() / daily_pnl.std() * np.sqrt(252) if daily_pnl.std() > 0 else 0

    # Max DD
    cum = INITIAL_NAV + daily_pnl.cumsum()
    peak = cum.cummax()
    dd = ((cum - peak) / peak).min() * 100

    # Exit reason breakdown
    by_reason = t["exit_reason"].value_counts().to_dict()

    return {
        "label": label,
        "n_trades": len(t),
        "total_pnl": round(total_pnl, 0),
        "win_rate": round(win_rate * 100, 1),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(dd, 1),
        "by_reason": by_reason,
        "avg_days_held": round(t["days_held"].mean(), 1),
        "best_trade": round(t["pnl_usd"].max(), 0),
        "worst_trade": round(t["pnl_usd"].min(), 0),
    }


def main():
    print("=== Backtest GoldTrendMGC SL variants ===\n")
    df = pd.read_parquet(MGC_PATH)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index()
    print(f"MGC: {len(df)} rows, {df.index.min().date()} -> {df.index.max().date()}\n")

    variants = [
        ("V0_baseline", 0.015, 0.03, False),
        ("V1_tight_sl_RR2", 0.004, 0.008, False),
        ("V2_tight_sl_largeTP", 0.004, 0.03, False),
        ("V3_baseline_emule_dlv", 0.015, 0.03, True),
        ("V4_baseline_TP6", 0.015, 0.06, False),
        ("V5_baseline_TP10", 0.015, 0.10, False),
    ]

    results = []
    for label, sl, tp, emul in variants:
        r = backtest(df, sl_pct=sl, tp_pct=tp, emulate_deleveraging=emul, label=label)
        r["sl_pct"] = sl * 100
        r["tp_pct"] = tp * 100
        r["dlv_emul"] = emul
        results.append(r)

    print(f"{'Variant':<28s} {'SL%':>5s} {'TP%':>5s} {'DLV':>4s} {'Trades':>7s} {'TotPnL$':>10s} {'WR%':>6s} {'Sharpe':>7s} {'MaxDD%':>7s}")
    print("-" * 100)
    for r in results:
        print(f"{r['label']:<28s} {r['sl_pct']:>5.2f} {r['tp_pct']:>5.2f} "
              f"{'Y' if r['dlv_emul'] else 'N':>4s} {r['n_trades']:>7d} "
              f"{r['total_pnl']:>+10.0f} {r['win_rate']:>6.1f} "
              f"{r['sharpe']:>+7.2f} {r['max_dd_pct']:>+7.1f}")

    print(f"\n{'Variant':<28s} {'best':>8s} {'worst':>8s} {'avg_days':>8s} {'exits':>40s}")
    for r in results:
        exits_str = ", ".join(f"{k}={v}" for k, v in sorted(r['by_reason'].items()))
        print(f"{r['label']:<28s} {r['best_trade']:>+8.0f} {r['worst_trade']:>+8.0f} "
              f"{r['avg_days_held']:>8.1f} {exits_str:>40s}")

    print("\n=== INTERPRETATION ===")
    print("V0 (baseline): SL 1.5% strat actuel, sans deleveraging")
    print("V1 (tight 0.4%): match deleveraging, TP 0.8% (R/R 2:1)")
    print("V2 (tight 0.4%): SL serre, TP 3% (asymetrique)")
    print("V3 (baseline + dlv emul): comportement REEL en prod aujourd'hui")
    print("V4-V5: variantes TP plus large pour voir profit factor")
    print("\nQuestion clé: V3 (baseline emule) vs V0 (baseline pur) — combien le")
    print("deleveraging cut la perf historique ?")


if __name__ == "__main__":
    main()
