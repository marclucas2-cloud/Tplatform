"""
Walk-Forward Backtest CORRIGE — MIB/ESTX50 Spread.

Fixes vs scripts/wf_eu_indices_v2.py (audit 2026-04-18):

1. SHARPE = MtM daily equity curve (pas exit_date aggregation).
   Bug original: agregation par exit_date collapse 2 legs en 1 date,
   std() quasi nul sur 2-4 dates uniques -> Sharpe artificiel 14-37.

2. HEDGE RATIO notional-based dollar-neutral.
   Bug original: 1:1 contrats avec FIB (€5/pt, €190K notionnel) vs
   FESX (€10/pt, €55K notionnel) = net long €135K, pas un spread.

3. SLIPPAGE 4 legs (entry A + entry B + exit A + exit B) modelise.
   Bug original: COST_RT = €8 par leg, pas de slippage.

4. ENTRY/EXIT par z-score uniquement (pas SL prix MIB).
   Bug original: live code emettait SL = bar.close * 0.97 sur leg MIB
   sans rapport avec z-score qui pilote la strategie.

Usage:
    python scripts/wf_mib_estx50_corrected.py
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = Path("data/eu")
REPORT_DIR = Path("reports/research")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

CONTRACT_SPECS = {
    "ESTX50": {"mult": 10, "tick": 1.0,  "comm": 2.0, "slip_ticks": 0.5},
    "MIB":    {"mult": 5,  "tick": 5.0,  "comm": 2.5, "slip_ticks": 0.5},
}


@dataclass
class SpreadTrade:
    entry_date: str
    exit_date: str
    direction: str  # "LONG_SPREAD" (long A short B) or "SHORT_SPREAD"
    n_a: int
    n_b: int
    entry_a: float
    entry_b: float
    exit_a: float
    exit_b: float
    pnl_gross: float
    pnl_net: float
    holding_days: int
    exit_reason: str


def load(name: str) -> pd.DataFrame:
    f = DATA_DIR / f"{name}_1D.parquet"
    if not f.exists():
        raise FileNotFoundError(f)
    df = pd.read_parquet(f)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def hedge_ratio_notional(price_a: float, price_b: float) -> tuple[int, int]:
    """Dollar-neutral hedge ratio (notional-based).

    Returns (n_a, n_b) such that n_a * price_a * mult_a ~= n_b * price_b * mult_b.
    Anchor n_a = 1, scale n_b proportionally.
    """
    spec_a = CONTRACT_SPECS["MIB"]
    spec_b = CONTRACT_SPECS["ESTX50"]
    notional_a = price_a * spec_a["mult"]
    notional_b = price_b * spec_b["mult"]
    n_b_raw = notional_a / notional_b
    n_b = max(1, round(n_b_raw))
    return 1, n_b


def trade_costs(n_a: int, n_b: int) -> float:
    """Round-trip cost: 2 legs * 2 sides * (commission + slippage)."""
    spec_a = CONTRACT_SPECS["MIB"]
    spec_b = CONTRACT_SPECS["ESTX50"]
    comm = 2 * (n_a * spec_a["comm"] + n_b * spec_b["comm"])
    slip_a = 2 * n_a * spec_a["slip_ticks"] * spec_a["tick"] * spec_a["mult"]
    slip_b = 2 * n_b * spec_b["slip_ticks"] * spec_b["tick"] * spec_b["mult"]
    return comm + slip_a + slip_b


def strat_mib_estx50_spread(
    data: pd.DataFrame,
    lookback: int = 60,
    z_entry: float = 2.0,
    z_exit: float = 0.0,
    z_stop: float = 3.5,
    max_hold: int = 60,
) -> tuple[list[SpreadTrade], pd.Series]:
    """Spread MIB/ESTX50 mean reversion avec hedge ratio + MtM daily curve.

    Returns (trades, daily_pnl_series).
    daily_pnl_series indexe par date avec PnL change journalier (somme MtM
    sur tous les spreads ouverts ce jour-la).
    """
    if "MIB" not in data.columns or "ESTX50" not in data.columns:
        return [], pd.Series(dtype=float)

    spec_a = CONTRACT_SPECS["MIB"]
    spec_b = CONTRACT_SPECS["ESTX50"]

    log_ratio = np.log(data["MIB"] / data["ESTX50"])

    trades: list[SpreadTrade] = []
    daily_pnl = pd.Series(0.0, index=data.index)

    position = 0  # +1 long spread (long MIB short ESTX), -1 short spread
    entry_date = None
    entry_idx = None
    entry_a = entry_b = 0.0
    n_a = n_b = 0
    hold_count = 0
    last_mtm = 0.0

    for i in range(lookback, len(data)):
        date = data.index[i]
        window = log_ratio.iloc[i - lookback:i]
        mu = window.mean()
        sigma = window.std()
        if sigma == 0 or pd.isna(sigma):
            continue
        z = (log_ratio.iloc[i] - mu) / sigma

        price_a = data["MIB"].iloc[i]
        price_b = data["ESTX50"].iloc[i]

        if position != 0:
            # Compute current MtM
            sign_a = 1 if position == 1 else -1
            sign_b = -1 if position == 1 else 1
            mtm = (
                (price_a - entry_a) * spec_a["mult"] * n_a * sign_a
                + (price_b - entry_b) * spec_b["mult"] * n_b * sign_b
            )
            daily_pnl.iloc[i] += mtm - last_mtm
            last_mtm = mtm
            hold_count += 1

            # Exit conditions
            exit_now = False
            exit_reason = ""
            if position == 1 and z >= z_exit:
                exit_now = True
                exit_reason = "tp_z_revert"
            elif position == -1 and z <= -z_exit:
                exit_now = True
                exit_reason = "tp_z_revert"
            elif position == 1 and z < -z_stop:
                exit_now = True
                exit_reason = "sl_z_blow"
            elif position == -1 and z > z_stop:
                exit_now = True
                exit_reason = "sl_z_blow"
            elif hold_count >= max_hold:
                exit_now = True
                exit_reason = "max_hold"

            if exit_now:
                pnl_gross = mtm
                cost = trade_costs(n_a, n_b)
                pnl_net = pnl_gross - cost
                # Subtract cost from this day's daily_pnl too
                daily_pnl.iloc[i] -= cost

                trades.append(SpreadTrade(
                    entry_date=str(entry_date.date()),
                    exit_date=str(date.date()),
                    direction="LONG_SPREAD" if position == 1 else "SHORT_SPREAD",
                    n_a=n_a, n_b=n_b,
                    entry_a=entry_a, entry_b=entry_b,
                    exit_a=price_a, exit_b=price_b,
                    pnl_gross=pnl_gross, pnl_net=pnl_net,
                    holding_days=hold_count,
                    exit_reason=exit_reason,
                ))
                position = 0
                last_mtm = 0.0
        else:
            # Entry
            if z < -z_entry:
                position = 1  # long spread (long MIB, short ESTX)
                entry_date = date
                entry_idx = i
                entry_a = price_a
                entry_b = price_b
                n_a, n_b = hedge_ratio_notional(price_a, price_b)
                hold_count = 0
                last_mtm = 0.0
            elif z > z_entry:
                position = -1  # short spread
                entry_date = date
                entry_idx = i
                entry_a = price_a
                entry_b = price_b
                n_a, n_b = hedge_ratio_notional(price_a, price_b)
                hold_count = 0
                last_mtm = 0.0

    return trades, daily_pnl


def compute_metrics(trades: list[SpreadTrade], daily_pnl: pd.Series) -> dict:
    """Sharpe sur daily MtM equity curve (pas exit_date aggregation)."""
    if not trades or daily_pnl.empty:
        return {
            "pnl": 0.0, "sharpe": 0.0, "wr": 0.0, "max_dd": 0.0,
            "n_trades": 0, "avg_hold": 0.0, "avg_cost_per_trade": 0.0,
        }

    pnl_total = float(daily_pnl.sum())
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_net > 0)
    wr = wins / n if n > 0 else 0.0

    # Sharpe sur daily PnL non-zero (jours actifs uniquement)
    active_days = daily_pnl[daily_pnl != 0]
    if len(active_days) > 1 and active_days.std() > 0:
        sharpe = float(active_days.mean() / active_days.std() * np.sqrt(252))
    else:
        sharpe = 0.0

    # Equity curve + max DD
    equity = daily_pnl.cumsum()
    peak = equity.cummax()
    dd = equity - peak
    max_dd = float(dd.min()) if len(dd) > 0 else 0.0

    avg_hold = sum(t.holding_days for t in trades) / n
    avg_cost = sum((t.pnl_gross - t.pnl_net) for t in trades) / n

    return {
        "pnl": pnl_total,
        "sharpe": sharpe,
        "wr": wr,
        "max_dd": max_dd,
        "n_trades": n,
        "avg_hold": avg_hold,
        "avg_cost_per_trade": avg_cost,
    }


def walk_forward(data: pd.DataFrame, n_windows: int = 5,
                 is_pct: float = 0.6) -> list[dict]:
    n = len(data)
    oos_size = int(n * (1 - is_pct) / n_windows)
    results = []

    for w in range(n_windows):
        is_end = int(n * is_pct) + w * oos_size
        oos_start = is_end
        oos_end = min(oos_start + oos_size, n)
        if oos_end <= oos_start or is_end < 100:
            break

        full = data.iloc[:oos_end]
        oos_start_date = data.index[oos_start]
        oos_end_date = data.index[oos_end - 1]

        all_trades, all_daily = strat_mib_estx50_spread(full)

        # Filter trades + daily_pnl to OOS window only
        oos_trades = [
            t for t in all_trades
            if str(oos_start_date.date()) <= t.entry_date <= str(oos_end_date.date())
        ]
        oos_daily = all_daily.loc[oos_start_date:oos_end_date]

        m = compute_metrics(oos_trades, oos_daily)
        results.append({
            "window": w + 1,
            "period": f"{oos_start_date.date()} to {oos_end_date.date()}",
            **m,
        })

    return results


def main():
    print("=" * 80)
    print("  WF MIB/ESTX50 SPREAD — VERSION CORRIGEE (4 fixes)")
    print("=" * 80)

    print("\nLoading data...")
    mib = load("MIB")
    estx = load("ESTX50")
    print(f"  MIB:    {len(mib)} bars, {mib.index[0].date()} -> {mib.index[-1].date()}")
    print(f"  ESTX50: {len(estx)} bars, {estx.index[0].date()} -> {estx.index[-1].date()}")

    closes = pd.DataFrame({
        "MIB": mib["close"],
        "ESTX50": estx["close"],
    }).dropna()
    print(f"  Aligned: {len(closes)} days")

    # Sample hedge ratio at midpoint
    mid = len(closes) // 2
    p_a = closes["MIB"].iloc[mid]
    p_b = closes["ESTX50"].iloc[mid]
    n_a, n_b = hedge_ratio_notional(p_a, p_b)
    notional_a = p_a * CONTRACT_SPECS["MIB"]["mult"] * n_a
    notional_b = p_b * CONTRACT_SPECS["ESTX50"]["mult"] * n_b
    cost = trade_costs(n_a, n_b)
    print(f"\n  Hedge ratio @ {closes.index[mid].date()} (price MIB={p_a:.0f}, ESTX={p_b:.0f}):")
    print(f"    {n_a} FIB ({notional_a/1000:.0f}K EUR notional) vs {n_b} FESX ({notional_b/1000:.0f}K EUR)")
    print(f"    Cost RT (2 legs): EUR {cost:.0f}")

    # Walk-forward
    print(f"\n{'='*80}")
    print(f"  WALK-FORWARD 5 WINDOWS")
    print(f"{'='*80}")

    results = walk_forward(closes, n_windows=5, is_pct=0.6)

    total_pnl = 0.0
    total_trades = 0
    profit_w = 0
    sharpes = []

    for r in results:
        tag = "PROFIT" if r["pnl"] > 0 else "LOSS"
        print(f"  W{r['window']} [{r['period']}] : {r['n_trades']:3d} trades | "
              f"PnL EUR{r['pnl']:+,.0f} | WR {r['wr']:.0%} | "
              f"Sharpe {r['sharpe']:.2f} | DD EUR{r['max_dd']:,.0f} | "
              f"avg_hold {r['avg_hold']:.0f}j | cost/trade EUR{r['avg_cost_per_trade']:.0f} | {tag}")
        total_pnl += r["pnl"]
        total_trades += r["n_trades"]
        sharpes.append(r["sharpe"])
        if r["pnl"] > 0:
            profit_w += 1

    nw = len(results)
    avg_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
    wf_ratio = f"{profit_w}/{nw}"
    verdict = "PASS" if profit_w >= nw * 0.5 and avg_sharpe > 0.5 else "FAIL"

    print(f"  {'-'*78}")
    print(f"  TOTAL: {total_trades} trades | PnL EUR{total_pnl:+,.0f} | "
          f"avg Sharpe {avg_sharpe:.2f} | WF {wf_ratio}")
    print(f"  VERDICT: {verdict}")

    # Comparison with original buggy version
    print(f"\n{'='*80}")
    print(f"  COMPARAISON vs original (buggy)")
    print(f"{'='*80}")
    print(f"  Original: PnL +$57,231 | Sharpe 14.35 | WF 4/5")
    print(f"  Corrected: PnL EUR{total_pnl:+,.0f} | Sharpe {avg_sharpe:.2f} | WF {wf_ratio}")
    delta = (total_pnl - 57231) / 57231 * 100 if total_pnl != 0 else -100
    sharpe_delta = (avg_sharpe - 14.35) / 14.35 * 100
    print(f"  Delta PnL: {delta:+.0f}% | Delta Sharpe: {sharpe_delta:+.0f}%")

    # Save report
    report = {
        "strategy": "mib_estx50_spread_corrected",
        "params": {
            "lookback": 60, "z_entry": 2.0, "z_exit": 0.0,
            "z_stop": 3.5, "max_hold": 60,
            "hedge_ratio_mode": "notional",
            "cost_model": "comm + slip_0.5_ticks_per_leg",
        },
        "windows": results,
        "total": {
            "pnl": total_pnl, "trades": total_trades,
            "avg_sharpe": avg_sharpe, "wf_ratio": wf_ratio,
            "verdict": verdict,
        },
        "fixes_applied": [
            "Sharpe MtM daily equity curve (not exit_date aggregation)",
            "Hedge ratio notional-based (dollar-neutral)",
            "Slippage 0.5 ticks per leg modelled (4 crossings RT)",
            "Exit by z-score only (no price-based SL on single leg)",
        ],
    }
    report_path = REPORT_DIR / "wf_mib_estx50_corrected.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved: {report_path}")


if __name__ == "__main__":
    main()
