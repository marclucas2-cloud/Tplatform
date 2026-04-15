#!/usr/bin/env python3
"""Backtest du portefeuille IB LIVE avec limites actuelles.

Simule les 3 strats LIVE qui tournent sur IBKR (port 4002):
  1. Cross-Asset Momentum (rotate MES/MNQ/M2K/MGC/MCL)
  2. Gold Trend MGC (long MGC > EMA20)
  3. Gold-Oil Rotation (rotate MGC/MCL spread 20d > 2%)

Avec les limites exactes du worker:
  - Risk Budget Framework: sum(risk_if_stopped) <= 5% equity
  - Max 4 symboles distincts simultanes
  - 1 contrat par symbole
  - Pas de double position meme symbole (GUARD 2)
  - Priorite d'ordre: Cross-Asset > Gold Trend > Gold-Oil (ordre append)

Metriques calculees:
  - PnL total & par annee
  - Sharpe, max DD
  - ROC annualise sur equity initiale $10,000
  - Nombre de trades, WR, positions moyennes
  - Utilisation du risk budget
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    "MES": {"mult": 5.0,   "cost": 2.49},
    "MNQ": {"mult": 2.0,   "cost": 1.74},
    "M2K": {"mult": 5.0,   "cost": 1.74},
    "MGC": {"mult": 10.0,  "cost": 2.49},
    "MCL": {"mult": 100.0, "cost": 2.49},
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
    """Simulate SL/TP exit path. Returns (exit_idx, exit_price)."""
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


# ============================================================
# Strategy signal generators (at each date, return Signal or None)
# ============================================================

def signal_cross_asset(dfs, common, i, lookback=20, min_mom=0.02, last_rebal=None, rebal_days=20):
    """Cross-asset momentum: pick best 20d momentum, need >2%, rebalance every 20d."""
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
    entry_px = float(wdf["close"].iloc[wi])  # signal price = close
    return {
        "strat": "cross_asset",
        "symbol": winner,
        "side": "BUY",
        "entry_idx": wi + 1,   # enter next open
        "entry_px_est": float(wdf["open"].iloc[wi + 1]) if wi + 1 < len(wdf) else entry_px,
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "mh": 20,
    }


def signal_gold_trend(mgc, i, ema_period=20):
    """Gold Trend: long MGC if close > EMA20."""
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
    """Gold-Oil rotation: rotate MGC/MCL based on 20d momentum spread."""
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


# ============================================================
# Portfolio loop
# ============================================================

def run_portfolio_backtest():
    print("=" * 72)
    print("BACKTEST PORTEFEUILLE IB LIVE — 3 strats + Risk Budget Framework")
    print("=" * 72)

    # Load data
    print("\nChargement data (5Y daily)…")
    dfs = {sym: load(sym) for sym in SPECS.keys()}
    mgc = dfs["MGC"]; mcl = dfs["MCL"]
    full = dfs["MES"].index
    common_all = full
    for df in dfs.values():
        common_all = common_all.intersection(df.index)

    print(f"Period: {common_all[0].date()} -> {common_all[-1].date()} ({len(common_all)} bars)")

    # State
    equity = INITIAL_EQUITY
    equity_curve = [INITIAL_EQUITY]
    dates_curve = [common_all[0]]
    open_positions = {}  # sym -> position dict
    trades_log = []
    daily_pnl = pd.Series(0.0, index=common_all)

    # Strategy state
    last_rebal_cam = None
    last_entry_gor = None

    # Stats
    signals_total = 0
    signals_accepted = 0
    signals_blocked_guard2 = 0
    signals_blocked_risk = 0
    signals_blocked_maxsym = 0

    # Active positions tracker per-day (for closure processing)
    for i, d in enumerate(common_all):
        # 1. Process exits due today (positions with exit_date == d)
        to_close = [sym for sym, pos in open_positions.items() if pos["exit_date"] <= d]
        for sym in to_close:
            pos = open_positions[sym]
            pnl = pos["pnl"]
            equity += pnl
            daily_pnl.loc[pos["exit_date"]] = daily_pnl.loc[pos["exit_date"]] + pnl if pos["exit_date"] in daily_pnl.index else pnl
            trades_log.append(pos)
            del open_positions[sym]

        # 2. Gather signals from all 3 strats (in priority order)
        signals_today = []
        sig = signal_cross_asset(dfs, common_all, i, last_rebal=last_rebal_cam)
        if sig:
            signals_today.append(sig)
        sig = signal_gold_trend(mgc, mgc.index.get_loc(d) if d in mgc.index else -1)
        if sig:
            signals_today.append(sig)
        sig = signal_gold_oil(mgc, mcl, common_all, i, last_entry=last_entry_gor)
        if sig:
            signals_today.append(sig)

        # 3. Apply guards + risk budget
        for sig in signals_today:
            signals_total += 1
            sym = sig["symbol"]

            # GUARD 2: already in position on this symbol
            if sym in open_positions:
                signals_blocked_guard2 += 1
                continue

            # Max symbols
            if len(open_positions) >= MAX_SYMBOLS:
                signals_blocked_maxsym += 1
                continue

            # Compute actual entry price and risk
            df_sym = dfs[sym]
            eidx = sig["entry_idx"]
            if eidx >= len(df_sym):
                continue
            entry_px = float(df_sym["open"].iloc[eidx])
            spec = SPECS[sym]
            qty = 1
            risk_this = entry_px * sig["sl_pct"] * spec["mult"] * qty

            # Risk budget check
            current_risk = sum(p["risk"] for p in open_positions.values())
            risk_budget = equity * RISK_BUDGET_PCT
            if current_risk + risk_this > risk_budget:
                signals_blocked_risk += 1
                continue

            # ACCEPT the signal: simulate the trade
            exit_idx, exit_px = sim_exit(df_sym, eidx, sig["side"], entry_px, sig["sl_pct"], sig["tp_pct"], sig["mh"])
            pnl = (exit_px - entry_px) * spec["mult"] - spec["cost"]
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

            # Update strategy state
            if sig["strat"] == "cross_asset":
                last_rebal_cam = i
            elif sig["strat"] == "gold_oil":
                last_entry_gor = i

        # Track equity curve (mark-to-mark at end-of-day close — approximate with entry+unrealized)
        equity_curve.append(equity)
        dates_curve.append(d)

    # Close any remaining positions
    for sym, pos in list(open_positions.items()):
        equity += pos["pnl"]
        trades_log.append(pos)
        daily_pnl.loc[pos["exit_date"]] = daily_pnl.loc[pos["exit_date"]] + pos["pnl"] if pos["exit_date"] in daily_pnl.index else pos["pnl"]
        del open_positions[sym]

    # ============================================================
    # Report
    # ============================================================
    eq_series = pd.Series(equity_curve, index=[pd.Timestamp(common_all[0])] + list(common_all))
    eq_series = eq_series.iloc[1:]  # drop initial
    df_trades = pd.DataFrame(trades_log)
    if df_trades.empty:
        print("\n[KO] NO TRADES"); return 1
    df_trades["entry_date"] = pd.to_datetime(df_trades["entry_date"])
    df_trades["exit_date"] = pd.to_datetime(df_trades["exit_date"])

    # Build daily PnL from trades
    daily = pd.Series(0.0, index=common_all)
    for _, t in df_trades.iterrows():
        d = t["exit_date"]
        if d in daily.index:
            daily.loc[d] = daily.loc[d] + t["pnl"]

    # Equity curve from daily PnL
    eq_curve = INITIAL_EQUITY + daily.cumsum()
    peak = eq_curve.cummax()
    dd = (eq_curve - peak) / peak
    max_dd = dd.min()

    total_pnl = df_trades["pnl"].sum()
    n_trades = len(df_trades)
    wr = (df_trades["pnl"] > 0).mean()
    avg_win = df_trades[df_trades.pnl > 0]["pnl"].mean() if (df_trades.pnl > 0).any() else 0
    avg_loss = df_trades[df_trades.pnl < 0]["pnl"].mean() if (df_trades.pnl < 0).any() else 0

    # Sharpe (daily -> annualized)
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0

    # ROC annualized
    years = (common_all[-1] - common_all[0]).days / 365.25
    final_equity = INITIAL_EQUITY + total_pnl
    total_return = final_equity / INITIAL_EQUITY - 1
    roc_annual = (final_equity / INITIAL_EQUITY) ** (1 / years) - 1

    # Per-strat breakdown
    per_strat = df_trades.groupby("strat").agg(
        n=("pnl", "size"),
        total=("pnl", "sum"),
        wr=("pnl", lambda x: (x > 0).mean()),
        avg=("pnl", "mean"),
    ).round(2)

    # Per-year breakdown
    df_trades["year"] = df_trades["exit_date"].dt.year
    per_year = df_trades.groupby("year").agg(
        n=("pnl", "size"),
        total=("pnl", "sum"),
        wr=("pnl", lambda x: (x > 0).mean()),
    ).round(2)
    per_year["roc_pct"] = (per_year["total"] / INITIAL_EQUITY * 100).round(1)

    # ============================================================
    print("\n" + "=" * 72)
    print("METRIQUES PORTEFEUILLE")
    print("=" * 72)
    print(f"Capital initial          ${INITIAL_EQUITY:>12,.0f}")
    print(f"Capital final            ${final_equity:>12,.0f}")
    print(f"PnL total                ${total_pnl:>12,.0f}")
    print(f"Return total             {total_return*100:>12.1f}%")
    print(f"ROC annualise (CAGR)     {roc_annual*100:>12.1f}%/an")
    print(f"Sharpe ratio             {sharpe:>12.2f}")
    print(f"Max Drawdown             {max_dd*100:>12.1f}%")
    print()
    print(f"Trades total             {n_trades:>12d}")
    print(f"Win rate                 {wr*100:>12.1f}%")
    print(f"Avg win                  ${avg_win:>12,.0f}")
    print(f"Avg loss                 ${avg_loss:>12,.0f}")
    print(f"Profit factor            {abs(avg_win * (df_trades.pnl>0).sum()) / abs(avg_loss * (df_trades.pnl<0).sum()) if (df_trades.pnl<0).any() else float('inf'):>12.2f}")
    print()
    print(f"Signals generes          {signals_total:>12d}")
    print(f"Signals acceptes         {signals_accepted:>12d}  ({signals_accepted/signals_total*100:.0f}%)")
    print(f"Blocked GUARD2 (dup sym) {signals_blocked_guard2:>12d}")
    print(f"Blocked MAX_SYMBOLS      {signals_blocked_maxsym:>12d}")
    print(f"Blocked RISK BUDGET      {signals_blocked_risk:>12d}")

    print("\n" + "=" * 72)
    print("PAR STRAT")
    print("=" * 72)
    print(per_strat.to_string())

    print("\n" + "=" * 72)
    print("PAR ANNEE")
    print("=" * 72)
    print(per_year.to_string())

    # Average concurrent positions
    conc = pd.Series(0, index=common_all)
    for _, t in df_trades.iterrows():
        mask = (common_all >= t["entry_date"]) & (common_all <= t["exit_date"])
        conc[mask] += 1
    print(f"\nPositions simultanees: moyenne={conc.mean():.2f}, max={conc.max()}, 0pos={conc.eq(0).sum()} jours ({conc.eq(0).sum()/len(conc)*100:.0f}%)")

    # Save CSVs
    out = ROOT / "reports" / "research"
    out.mkdir(parents=True, exist_ok=True)
    df_trades.to_csv(out / "ib_portfolio_trades.csv", index=False)
    per_year.to_csv(out / "ib_portfolio_per_year.csv")
    per_strat.to_csv(out / "ib_portfolio_per_strat.csv")
    print(f"\n[ok] CSVs saved to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(run_portfolio_backtest())
