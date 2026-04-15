#!/usr/bin/env python3
"""Backtest portefeuille IB LIVE — V2 Option 1: Cross-Asset first-refusal.

Difference vs V1 (backtest_ib_portfolio_live.py):
  - Cross-Asset Mom a "first refusal" sur TOUS ses symboles d'univers.
  - Concretement: on calcule chaque jour le top pick de Cross-Asset (regardless
    of rebal cooldown). Si le top pick de Cross-Asset = MGC et que son momentum
    est > 2%, alors Gold Trend ET Gold-Oil sont bloques sur MGC (reserved).
  - Cross-Asset reste soumise a son rebal cooldown 20j pour l'execution, mais
    elle BLOQUE les autres strats qui voudraient prendre "ses" symboles.
  - Objectif: donner plus d'opportunites a Cross-Asset et reduire la
    concentration MGC.

Comparaison avec V1 imprimee a la fin.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    # mult = contract multiplier
    # cost = round-trip commission + fees (IBKR + exchange)
    # slip = round-trip slippage estimate: 2 ticks entry + 2 ticks exit
    #   tick sizes & dollar values:
    #   MES 0.25pt*$5=$1.25  -> 4 ticks = $5
    #   MNQ 0.25pt*$2=$0.50  -> 4 ticks = $2
    #   M2K 0.10pt*$5=$0.50  -> 4 ticks = $2
    #   MGC 0.10pt*$10=$1.00 -> 4 ticks = $4
    #   MCL 0.01pt*$100=$1.00-> 4 ticks = $4
    "MES": {"mult": 5.0,   "cost": 2.49, "slip": 5.0},
    "MNQ": {"mult": 2.0,   "cost": 1.74, "slip": 2.0},
    "M2K": {"mult": 5.0,   "cost": 1.74, "slip": 2.0},
    "MGC": {"mult": 10.0,  "cost": 2.49, "slip": 4.0},
    "MCL": {"mult": 100.0, "cost": 2.49, "slip": 4.0},
}

INITIAL_EQUITY = 10_000.0
RISK_BUDGET_PCT = 0.05
MAX_SYMBOLS = 4


def load(sym):
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def sim_exit(df, eidx, side, epx, sl_pct, tp_pct, mh):
    sl = epx * (1 - sl_pct) if side == "BUY" else epx * (1 + sl_pct)
    tp = epx * (1 + tp_pct) if side == "BUY" else epx * (1 - tp_pct)
    for j in range(eidx, min(eidx + mh, len(df))):
        h = float(df["high"].iloc[j]); l = float(df["low"].iloc[j]); o = float(df["open"].iloc[j])
        if j > eidx:
            if side == "BUY":
                if o <= sl: return j, o
                if o >= tp: return j, o
            else:
                if o >= sl: return j, o
                if o <= tp: return j, o
        if side == "BUY":
            if l <= sl: return j, sl
            if h >= tp: return j, tp
        else:
            if h >= sl: return j, sl
            if l <= tp: return j, tp
    end = min(eidx + mh - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


def cross_asset_top_pick(dfs, common, i, lookback=20, min_mom=0.02):
    """Return the symbol Cross-Asset Mom WOULD pick today (ignoring cooldown).

    Used by other strats for first-refusal check.
    Returns None if no symbol meets the min_mom threshold.
    """
    if i < lookback:
        return None
    rets = {}
    for sym, df in dfs.items():
        d = common[i]
        if d not in df.index:
            continue
        di = df.index.get_loc(d)
        prev_d = common[i - lookback]
        if prev_d not in df.index:
            continue
        prev = df.index.get_loc(prev_d)
        rets[sym] = float(df["close"].iloc[di] / df["close"].iloc[prev] - 1)
    if not rets:
        return None
    winner = max(rets, key=rets.get)
    if rets[winner] < min_mom:
        return None
    return winner


def signal_cross_asset(dfs, common, i, lookback=20, min_mom=0.02, last_rebal=None, rebal_days=20):
    if last_rebal is not None and (i - last_rebal) < rebal_days:
        return None
    if i < lookback:
        return None
    rets = {}
    for sym, df in dfs.items():
        d = common[i]
        if d not in df.index:
            continue
        di = df.index.get_loc(d)
        prev_d = common[i - lookback]
        if prev_d not in df.index:
            continue
        prev = df.index.get_loc(prev_d)
        rets[sym] = float(df["close"].iloc[di] / df["close"].iloc[prev] - 1)
    if not rets:
        return None
    winner = max(rets, key=rets.get)
    if rets[winner] < min_mom:
        return None
    wdf = dfs[winner]
    wi = wdf.index.get_loc(common[i])
    entry_px = float(wdf["close"].iloc[wi])
    return {
        "strat": "cross_asset",
        "symbol": winner,
        "side": "BUY",
        "entry_idx": wi + 1,
        "entry_px_est": float(wdf["open"].iloc[wi + 1]) if wi + 1 < len(wdf) else entry_px,
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "mh": 20,
    }


def signal_gold_trend(mgc, i, ema_period=20):
    if i < ema_period + 1:
        return None
    close = mgc["close"].astype(float)
    ema = close.ewm(span=ema_period).mean()
    if not np.isfinite(ema.iloc[i]):
        return None
    if close.iloc[i] <= ema.iloc[i]:
        return None
    if i + 1 >= len(mgc):
        return None
    return {
        "strat": "gold_trend",
        "symbol": "MGC",
        "side": "BUY",
        "entry_idx": i + 1,
        "entry_px_est": float(mgc["open"].iloc[i + 1]),
        "sl_pct": 0.015,
        "tp_pct": 0.03,
        "mh": 10,
    }


def signal_gold_oil(mgc, mcl, common, i, lookback=20, min_edge=0.02, last_entry=None, cooldown=10):
    if last_entry is not None and (i - last_entry) < cooldown:
        return None
    if i < lookback:
        return None
    mgc_c = mgc["close"].reindex(common)
    mcl_c = mcl["close"].reindex(common)
    if not (np.isfinite(mgc_c.iloc[i]) and np.isfinite(mcl_c.iloc[i])):
        return None
    mgc_ret = float(mgc_c.iloc[i] / mgc_c.iloc[i - lookback] - 1)
    mcl_ret = float(mcl_c.iloc[i] / mcl_c.iloc[i - lookback] - 1)
    spread = mgc_ret - mcl_ret
    if abs(spread) < min_edge:
        return None
    if spread > 0:
        sym = "MGC"; df = mgc
    else:
        sym = "MCL"; df = mcl
    d = common[i]
    if d not in df.index:
        return None
    di = df.index.get_loc(d)
    if di + 1 >= len(df):
        return None
    return {
        "strat": "gold_oil",
        "symbol": sym,
        "side": "BUY",
        "entry_idx": di + 1,
        "entry_px_est": float(df["open"].iloc[di + 1]),
        "sl_pct": 0.02,
        "tp_pct": 0.04,
        "mh": 10,
    }


def run_portfolio(dfs, common_all, use_first_refusal: bool, label: str, disable_gold_trend: bool = False, apply_slippage: bool = False):
    mgc = dfs["MGC"]; mcl = dfs["MCL"]
    equity = INITIAL_EQUITY
    open_positions = {}
    trades_log = []
    last_rebal_cam = None
    last_entry_gor = None

    signals_total = 0
    signals_accepted = 0
    signals_blocked_guard2 = 0
    signals_blocked_risk = 0
    signals_blocked_maxsym = 0
    signals_blocked_firstrefusal = 0

    for i, d in enumerate(common_all):
        # Process exits
        to_close = [sym for sym, pos in open_positions.items() if pos["exit_date"] <= d]
        for sym in to_close:
            pos = open_positions[sym]
            equity += pos["pnl"]
            trades_log.append(pos)
            del open_positions[sym]

        # Compute Cross-Asset top pick (for first-refusal check)
        cam_top = cross_asset_top_pick(dfs, common_all, i) if use_first_refusal else None

        # Gather signals — Cross-Asset ALWAYS first
        signals_today = []
        sig = signal_cross_asset(dfs, common_all, i, last_rebal=last_rebal_cam)
        if sig:
            signals_today.append(sig)
        if not disable_gold_trend:
            sig = signal_gold_trend(mgc, mgc.index.get_loc(d) if d in mgc.index else -1)
            if sig:
                signals_today.append(sig)
        sig = signal_gold_oil(mgc, mcl, common_all, i, last_entry=last_entry_gor)
        if sig:
            signals_today.append(sig)

        for sig in signals_today:
            signals_total += 1
            sym = sig["symbol"]

            # First-refusal: if cross_asset would want this symbol today (and
            # the signal is NOT from cross_asset), block it — reserve for CAM
            if use_first_refusal and sig["strat"] != "cross_asset":
                if cam_top is not None and sym == cam_top:
                    signals_blocked_firstrefusal += 1
                    continue

            if sym in open_positions:
                signals_blocked_guard2 += 1
                continue

            if len(open_positions) >= MAX_SYMBOLS:
                signals_blocked_maxsym += 1
                continue

            df_sym = dfs[sym]
            eidx = sig["entry_idx"]
            if eidx >= len(df_sym):
                continue
            entry_px = float(df_sym["open"].iloc[eidx])
            spec = SPECS[sym]
            qty = 1
            risk_this = entry_px * sig["sl_pct"] * spec["mult"] * qty

            current_risk = sum(p["risk"] for p in open_positions.values())
            risk_budget = equity * RISK_BUDGET_PCT
            if current_risk + risk_this > risk_budget:
                signals_blocked_risk += 1
                continue

            exit_idx, exit_px = sim_exit(df_sym, eidx, sig["side"], entry_px, sig["sl_pct"], sig["tp_pct"], sig["mh"])
            pnl = (exit_px - entry_px) * spec["mult"] - spec["cost"]
            if apply_slippage:
                pnl -= spec.get("slip", 0.0)
            open_positions[sym] = {
                "strat": sig["strat"],
                "symbol": sym,
                "side": sig["side"],
                "entry_date": df_sym.index[eidx],
                "entry_px": entry_px,
                "exit_date": df_sym.index[exit_idx],
                "exit_px": exit_px,
                "pnl": pnl,
                "risk": risk_this,
                "sl_pct": sig["sl_pct"],
                "tp_pct": sig["tp_pct"],
            }
            signals_accepted += 1

            if sig["strat"] == "cross_asset":
                last_rebal_cam = i
            elif sig["strat"] == "gold_oil":
                last_entry_gor = i

    # Close remaining positions
    for sym, pos in list(open_positions.items()):
        equity += pos["pnl"]
        trades_log.append(pos)

    # Metrics
    df_trades = pd.DataFrame(trades_log)
    if df_trades.empty:
        return None

    df_trades["exit_date"] = pd.to_datetime(df_trades["exit_date"])
    df_trades["entry_date"] = pd.to_datetime(df_trades["entry_date"])
    daily = pd.Series(0.0, index=common_all)
    for _, t in df_trades.iterrows():
        d_ = t["exit_date"]
        if d_ in daily.index:
            daily.loc[d_] = daily.loc[d_] + t["pnl"]

    eq_curve = INITIAL_EQUITY + daily.cumsum()
    peak = eq_curve.cummax()
    dd = (eq_curve - peak) / peak
    max_dd = dd.min()

    total_pnl = df_trades["pnl"].sum()
    n_trades = len(df_trades)
    wr = (df_trades["pnl"] > 0).mean()
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    years = (common_all[-1] - common_all[0]).days / 365.25
    final_equity = INITIAL_EQUITY + total_pnl
    roc_annual = (final_equity / INITIAL_EQUITY) ** (1 / years) - 1

    df_trades["year"] = df_trades["exit_date"].dt.year
    per_year = df_trades.groupby("year").agg(
        n=("pnl", "size"),
        total=("pnl", "sum"),
        wr=("pnl", lambda x: (x > 0).mean()),
    ).round(2)
    per_year["roc_pct"] = (per_year["total"] / INITIAL_EQUITY * 100).round(1)

    per_strat = df_trades.groupby("strat").agg(
        n=("pnl", "size"),
        total=("pnl", "sum"),
        wr=("pnl", lambda x: (x > 0).mean()),
    ).round(2)

    per_symbol = df_trades.groupby("symbol").agg(
        n=("pnl", "size"),
        total=("pnl", "sum"),
    ).round(2)

    return {
        "label": label,
        "df_trades": df_trades,
        "final_equity": final_equity,
        "total_pnl": total_pnl,
        "roc_annual": roc_annual,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "n_trades": n_trades,
        "wr": wr,
        "per_year": per_year,
        "per_strat": per_strat,
        "per_symbol": per_symbol,
        "signals_total": signals_total,
        "signals_accepted": signals_accepted,
        "signals_blocked_guard2": signals_blocked_guard2,
        "signals_blocked_risk": signals_blocked_risk,
        "signals_blocked_maxsym": signals_blocked_maxsym,
        "signals_blocked_firstrefusal": signals_blocked_firstrefusal,
    }


def print_result(r):
    print(f"\n{'='*72}")
    print(f"  {r['label']}")
    print(f"{'='*72}")
    print(f"Capital final            ${r['final_equity']:>12,.0f}")
    print(f"PnL total                ${r['total_pnl']:>12,.0f}")
    print(f"ROC annualise (CAGR)     {r['roc_annual']*100:>12.1f}%/an")
    print(f"Sharpe                   {r['sharpe']:>12.2f}")
    print(f"Max Drawdown             {r['max_dd']*100:>12.1f}%")
    print(f"Trades                   {r['n_trades']:>12d}")
    print(f"Win rate                 {r['wr']*100:>12.1f}%")
    print(f"Signals: total={r['signals_total']}, accept={r['signals_accepted']} "
          f"({r['signals_accepted']/r['signals_total']*100:.0f}%), "
          f"guard2={r['signals_blocked_guard2']}, risk={r['signals_blocked_risk']}, "
          f"first_refusal={r['signals_blocked_firstrefusal']}")
    print("\nPar strategie:")
    print(r['per_strat'].to_string())
    print("\nPar symbole:")
    print(r['per_symbol'].to_string())
    print("\nPar annee:")
    print(r['per_year'].to_string())


def main():
    print("Chargement data...")
    dfs = {sym: load(sym) for sym in SPECS.keys()}
    common_all = dfs["MES"].index
    for df in dfs.values():
        common_all = common_all.intersection(df.index)

    print(f"Period: {common_all[0].date()} - {common_all[-1].date()} ({len(common_all)} bars)")

    # V1: baseline
    r1 = run_portfolio(dfs, common_all, use_first_refusal=False, label="V1 BASELINE (current live)")
    # V2: first-refusal
    r2 = run_portfolio(dfs, common_all, use_first_refusal=True, label="V2 OPTION 1 (CrossAsset first-refusal)")
    # V3: first-refusal + no gold_trend
    r3 = run_portfolio(dfs, common_all, use_first_refusal=True, disable_gold_trend=True,
                       label="V3 OPTION 2 (first-refusal + NO gold_trend)")

    print_result(r1)
    print_result(r2)
    print_result(r3)

    # Diff table
    print("\n" + "=" * 72)
    print("  COMPARAISON V1 vs V2 vs V3")
    print("=" * 72)
    def fmt_year(r, y):
        if y in r['per_year'].index:
            return f"${r['per_year'].loc[y,'total']:+,.0f}"
        return "n/a"

    rows = [
        ("PnL total",
         f"${r1['total_pnl']:+,.0f}", f"${r2['total_pnl']:+,.0f}", f"${r3['total_pnl']:+,.0f}"),
        ("CAGR",
         f"{r1['roc_annual']*100:.1f}%", f"{r2['roc_annual']*100:.1f}%", f"{r3['roc_annual']*100:.1f}%"),
        ("Sharpe",
         f"{r1['sharpe']:.2f}", f"{r2['sharpe']:.2f}", f"{r3['sharpe']:.2f}"),
        ("Max DD",
         f"{r1['max_dd']*100:.1f}%", f"{r2['max_dd']*100:.1f}%", f"{r3['max_dd']*100:.1f}%"),
        ("Trades",
         f"{r1['n_trades']}", f"{r2['n_trades']}", f"{r3['n_trades']}"),
        ("Win rate",
         f"{r1['wr']*100:.1f}%", f"{r2['wr']*100:.1f}%", f"{r3['wr']*100:.1f}%"),
        ("2021",
         fmt_year(r1,2021), fmt_year(r2,2021), fmt_year(r3,2021)),
        ("2022 BEAR",
         fmt_year(r1,2022), fmt_year(r2,2022), fmt_year(r3,2022)),
        ("2023",
         fmt_year(r1,2023), fmt_year(r2,2023), fmt_year(r3,2023)),
        ("2024",
         fmt_year(r1,2024), fmt_year(r2,2024), fmt_year(r3,2024)),
        ("2025",
         fmt_year(r1,2025), fmt_year(r2,2025), fmt_year(r3,2025)),
        ("2026 YTD",
         fmt_year(r1,2026), fmt_year(r2,2026), fmt_year(r3,2026)),
    ]
    print(f"{'':20s} {'V1 baseline':>14s} {'V2 Opt1':>14s} {'V3 NoGoldTrend':>16s}")
    for row in rows:
        label = row[0]
        print(f"{label:20s} {row[1]:>14s} {row[2]:>14s} {row[3]:>16s}")

    # Save
    out = ROOT / "reports" / "research"
    out.mkdir(parents=True, exist_ok=True)
    r2['df_trades'].to_csv(out / "ib_portfolio_v2_trades.csv", index=False)
    r3['df_trades'].to_csv(out / "ib_portfolio_v3_trades.csv", index=False)
    print(f"\n[ok] CSVs saved to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
