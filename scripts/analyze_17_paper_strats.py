#!/usr/bin/env python3
"""Mini-backtest des 17 strats paper IBKR sur les 3 dernieres semaines.

Objectif: simuler ce que chaque strat aurait fait pendant la periode ou le
paper cycle etait bloque par HARD LIMIT. Donne une baseline directionnelle
pour identifier les candidats potentiels live.

Data:
  - 1D files pour historique (jusqu'a 2026-03-30)
  - 5M IBKR resampled to 1D pour les derniers jours (mars 31 -> avril 15)

Limitation: 3 semaines = 15 trading days = sample TRES petit (bruit).
Les conclusions sont DIRECTIONNELLES, pas statistiques.
"""
from __future__ import annotations

import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Cost model per instrument (IBKR micros)
COSTS = {
    "MES": {"mult": 5.0, "tick": 0.25, "cost_rt": 2.49},
    "MNQ": {"mult": 2.0, "tick": 0.25, "cost_rt": 1.74},
    "M2K": {"mult": 5.0, "tick": 0.10, "cost_rt": 1.74},
    "MCL": {"mult": 100.0, "tick": 0.01, "cost_rt": 2.49},
    "MGC": {"mult": 10.0, "tick": 0.10, "cost_rt": 2.49},
    "ESTX50": {"mult": 10.0, "tick": 1.0, "cost_rt": 3.0},
    "DAX": {"mult": 1.0, "tick": 1.0, "cost_rt": 6.0},
    "CAC40": {"mult": 1.0, "tick": 1.0, "cost_rt": 6.0},
    "MIB": {"mult": 5.0, "tick": 5.0, "cost_rt": 5.0},
    "VIX": {"mult": 1.0, "tick": 0.05, "cost_rt": 2.0},
}


@dataclass
class SimTrade:
    strat: str
    sym: str
    entry_date: str
    exit_date: str
    side: str
    entry_px: float
    exit_px: float
    bars_held: int
    exit_reason: str
    pnl_usd: float


def load_combined_daily(sym: str) -> pd.DataFrame:
    """Load 1D file + resample 5M if available for recent data."""
    df_1d = None
    path_1d = ROOT / "data" / "futures" / f"{sym}_1D.parquet"
    if path_1d.exists():
        df_1d = pd.read_parquet(path_1d)
        df_1d.columns = [c.lower() for c in df_1d.columns]
        df_1d.index = pd.to_datetime(df_1d.index)
        if df_1d.index.tz is not None:
            df_1d.index = df_1d.index.tz_localize(None)
        df_1d = df_1d.sort_index()

    # Try to extend with 5M IBKR data
    path_5m = ROOT / "data" / "futures" / f"{sym}_5M_IBKR6M.parquet"
    if path_5m.exists():
        df_5m = pd.read_parquet(path_5m)
        df_5m.columns = [c.lower() for c in df_5m.columns]
        df_5m.index = pd.to_datetime(df_5m.index)
        if df_5m.index.tz is not None:
            df_5m.index = df_5m.index.tz_localize(None)
        # Resample to daily
        daily = df_5m.resample("1D").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
        if df_1d is not None and not daily.empty:
            last_1d = df_1d.index[-1]
            new_days = daily[daily.index > last_1d]
            if not new_days.empty:
                df_1d = pd.concat([df_1d, new_days])
        elif df_1d is None:
            df_1d = daily
    return df_1d


def simulate_exit(
    df: pd.DataFrame,
    entry_idx: int,
    entry_px: float,
    side: str,
    sl_px: float | None,
    tp_px: float | None,
    max_hold_days: int = 10,
) -> tuple[int, float, str]:
    """Scan forward bars for SL/TP hit. Returns (exit_idx, exit_px, reason)."""
    end = min(entry_idx + max_hold_days, len(df) - 1)
    for j in range(entry_idx + 1, end + 1):
        h = float(df["high"].iloc[j])
        l = float(df["low"].iloc[j])
        if side == "BUY":
            sl_hit = sl_px is not None and l <= sl_px
            tp_hit = tp_px is not None and h >= tp_px
            if sl_hit and tp_hit:
                return j, sl_px, "SL_PESSIMISTIC"
            if sl_hit:
                return j, sl_px, "SL"
            if tp_hit:
                return j, tp_px, "TP"
        else:  # SELL
            sl_hit = sl_px is not None and h >= sl_px
            tp_hit = tp_px is not None and l <= tp_px
            if sl_hit and tp_hit:
                return j, sl_px, "SL_PESSIMISTIC"
            if sl_hit:
                return j, sl_px, "SL"
            if tp_hit:
                return j, tp_px, "TP"
    # Time exit
    return end, float(df["close"].iloc[end]), "TIME"


def trade_pnl(side: str, entry_px: float, exit_px: float, sym: str, qty: float = 1.0) -> float:
    spec = COSTS.get(sym, {"mult": 1.0, "cost_rt": 5.0})
    if side == "BUY":
        gross = (exit_px - entry_px) * spec["mult"] * qty
    else:
        gross = (entry_px - exit_px) * spec["mult"] * qty
    return gross - spec["cost_rt"]


# ==================================================================
# Strategy implementations (mini — reproduces key logic)
# ==================================================================

def strat_mes_trend(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MES Trend: EMA20/EMA50 crossover (simplified)."""
    df = data.get("MES")
    if df is None:
        return []
    close = df["close"]
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()
    trades = []
    for i in range(max(start_idx, 50), end_idx):
        prev_bull = ema20.iloc[i - 1] > ema50.iloc[i - 1]
        curr_bull = ema20.iloc[i] > ema50.iloc[i]
        if not prev_bull and curr_bull:
            # BUY
            entry_px = float(df["close"].iloc[i])
            sl = entry_px * 0.98
            tp = entry_px * 1.03
            ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, "BUY", sl, tp)
            trades.append(SimTrade(
                "mes_trend", "MES", str(df.index[i].date()), str(df.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - i, reason, trade_pnl("BUY", entry_px, ex_px, "MES"),
            ))
    return trades


def strat_mes_trend_mr(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MES Trend+MR hybrid: RSI2 < 10 + close > SMA50."""
    df = data.get("MES")
    if df is None:
        return []
    close = df["close"]
    sma50 = close.rolling(50).mean()
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    avg_up = up.rolling(2).mean()
    avg_dn = dn.rolling(2).mean()
    rsi2 = 100 - 100 / (1 + avg_up / avg_dn.replace(0, np.nan))
    trades = []
    last = -100
    for i in range(max(start_idx, 50), end_idx):
        if i - last < 3:
            continue
        if rsi2.iloc[i] < 10 and close.iloc[i] > sma50.iloc[i]:
            entry_px = float(close.iloc[i])
            sl = entry_px * 0.985
            tp = entry_px * 1.025
            ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, "BUY", sl, tp, max_hold_days=5)
            trades.append(SimTrade(
                "mes_trend_mr", "MES", str(df.index[i].date()), str(df.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - i, reason, trade_pnl("BUY", entry_px, ex_px, "MES"),
            ))
            last = i
    return trades


def strat_mes_3day_stretch(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """3 down days in a row → mean reversion long."""
    df = data.get("MES")
    if df is None:
        return []
    ret = df["close"].pct_change()
    trades = []
    for i in range(max(start_idx, 5), end_idx):
        if ret.iloc[i] < 0 and ret.iloc[i - 1] < 0 and ret.iloc[i - 2] < 0:
            entry_px = float(df["close"].iloc[i])
            sl = entry_px * 0.98
            tp = entry_px * 1.015
            ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, "BUY", sl, tp, max_hold_days=3)
            trades.append(SimTrade(
                "mes_3day_stretch", "MES", str(df.index[i].date()), str(df.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - i, reason, trade_pnl("BUY", entry_px, ex_px, "MES"),
            ))
    return trades


def strat_overnight_mnq(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MNQ close > EMA20 → BUY next open, SL -30 / TP +50 (in points)."""
    df = data.get("MNQ")
    if df is None:
        return []
    close = df["close"]
    ema20 = close.ewm(span=20).mean()
    trades = []
    for i in range(max(start_idx, 20), end_idx - 1):
        if close.iloc[i] > ema20.iloc[i]:
            entry_px = float(df["open"].iloc[i + 1])
            sl = entry_px - 30
            tp = entry_px + 50
            ex_idx, ex_px, reason = simulate_exit(df, i + 1, entry_px, "BUY", sl, tp, max_hold_days=10)
            trades.append(SimTrade(
                "overnight_mnq", "MNQ", str(df.index[i + 1].date()), str(df.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - (i + 1), reason, trade_pnl("BUY", entry_px, ex_px, "MNQ"),
            ))
    return trades


def strat_tsmom(data: dict, sym: str, start_idx: int, end_idx: int) -> list[SimTrade]:
    """TSMOM 63d return > 0 → LONG, < 0 → SHORT, rebalance 21d."""
    df = data.get(sym)
    if df is None:
        return []
    close = df["close"]
    trades = []
    last_rebal = -100
    for i in range(max(start_idx, 63), end_idx):
        if i - last_rebal < 21:
            continue
        ret63 = close.iloc[i] / close.iloc[i - 63] - 1
        if abs(ret63) < 0.01:
            continue
        side = "BUY" if ret63 > 0 else "SELL"
        entry_px = float(close.iloc[i])
        if side == "BUY":
            sl = entry_px * 0.95
            tp = entry_px * 1.10
        else:
            sl = entry_px * 1.05
            tp = entry_px * 0.90
        ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, side, sl, tp, max_hold_days=21)
        trades.append(SimTrade(
            f"tsmom_{sym.lower()}", sym, str(df.index[i].date()), str(df.index[ex_idx].date()),
            side, entry_px, ex_px, ex_idx - i, reason, trade_pnl(side, entry_px, ex_px, sym),
        ))
        last_rebal = i
    return trades


def strat_m2k_orb(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """M2K daily momentum approximation (ORB not possible on daily)."""
    df = data.get("M2K")
    if df is None:
        return []
    ret = df["close"].pct_change()
    trades = []
    for i in range(max(start_idx, 20), end_idx):
        if abs(ret.iloc[i]) > 0.01:
            side = "BUY" if ret.iloc[i] > 0 else "SELL"
            entry_px = float(df["close"].iloc[i])
            if side == "BUY":
                sl = entry_px * 0.98
                tp = entry_px * 1.015
            else:
                sl = entry_px * 1.02
                tp = entry_px * 0.985
            ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, side, sl, tp, max_hold_days=3)
            trades.append(SimTrade(
                "m2k_orb", "M2K", str(df.index[i].date()), str(df.index[ex_idx].date()),
                side, entry_px, ex_px, ex_idx - i, reason, trade_pnl(side, entry_px, ex_px, "M2K"),
            ))
    return trades


def strat_mcl_brent_lag(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MCL lagged momentum approximation."""
    df = data.get("MCL")
    if df is None:
        return []
    ret = df["close"].pct_change(3)  # 3-day lag
    trades = []
    for i in range(max(start_idx, 5), end_idx):
        if abs(ret.iloc[i]) > 0.02:
            side = "BUY" if ret.iloc[i] > 0 else "SELL"
            entry_px = float(df["close"].iloc[i])
            if side == "BUY":
                sl = entry_px * 0.98
                tp = entry_px * 1.03
            else:
                sl = entry_px * 1.02
                tp = entry_px * 0.97
            ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, side, sl, tp, max_hold_days=5)
            trades.append(SimTrade(
                "mcl_brent_lag", "MCL", str(df.index[i].date()), str(df.index[ex_idx].date()),
                side, entry_px, ex_px, ex_idx - i, reason, trade_pnl(side, entry_px, ex_px, "MCL"),
            ))
    return trades


def strat_mgc_vix_hedge(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MGC long when VIX > 25 (flight-to-quality)."""
    mgc = data.get("MGC")
    vix = data.get("VIX")
    if mgc is None or vix is None:
        return []
    trades = []
    last = -100
    for i in range(max(start_idx, 5), end_idx):
        date = mgc.index[i]
        if date not in vix.index:
            continue
        vix_val = float(vix["close"].loc[date])
        if vix_val > 25 and i - last > 5:
            entry_px = float(mgc["close"].iloc[i])
            sl = entry_px * 0.98
            tp = entry_px * 1.03
            ex_idx, ex_px, reason = simulate_exit(mgc, i, entry_px, "BUY", sl, tp, max_hold_days=7)
            trades.append(SimTrade(
                "mgc_vix_hedge", "MGC", str(mgc.index[i].date()), str(mgc.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - i, reason, trade_pnl("BUY", entry_px, ex_px, "MGC"),
            ))
            last = i
    return trades


def strat_vix_mr(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """VIX spike > 25 + RSI < 30 → LONG MES."""
    mes = data.get("MES")
    vix = data.get("VIX")
    if mes is None or vix is None:
        return []
    trades = []
    last = -100
    # VIX RSI
    vix_delta = vix["close"].diff()
    vix_up = vix_delta.clip(lower=0).rolling(14).mean()
    vix_dn = (-vix_delta.clip(upper=0)).rolling(14).mean()
    vix_rsi = 100 - 100 / (1 + vix_up / vix_dn.replace(0, np.nan))
    for i in range(max(start_idx, 30), end_idx):
        date = mes.index[i]
        if date not in vix.index:
            continue
        if i - last < 7:
            continue
        vix_val = float(vix["close"].loc[date])
        vix_r = float(vix_rsi.loc[date]) if date in vix_rsi.index else 50
        if vix_val > 25 and vix_r < 30:
            entry_px = float(mes["close"].iloc[i])
            sl = entry_px * 0.985
            tp = entry_px * 1.03
            ex_idx, ex_px, reason = simulate_exit(mes, i, entry_px, "BUY", sl, tp, max_hold_days=7)
            trades.append(SimTrade(
                "vix_mr", "MES", str(mes.index[i].date()), str(mes.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - i, reason, trade_pnl("BUY", entry_px, ex_px, "MES"),
            ))
            last = i
    return trades


def strat_eu_gap(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """ESTX50 gap > 1% → fade (SHORT if gap up, BUY if gap down)."""
    df = data.get("ESTX50")
    if df is None:
        return []
    gap = (df["open"] - df["close"].shift(1)) / df["close"].shift(1)
    trades = []
    for i in range(max(start_idx, 5), end_idx):
        g = float(gap.iloc[i])
        if abs(g) < 0.01 or abs(g) > 0.05:
            continue
        entry_px = float(df["open"].iloc[i])
        if g > 0:
            side = "SELL"
            sl = entry_px * 1.015
            tp = entry_px * 0.985
        else:
            side = "BUY"
            sl = entry_px * 0.985
            tp = entry_px * 1.015
        ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, side, sl, tp, max_hold_days=1)
        trades.append(SimTrade(
            "eu_gap", "ESTX50", str(df.index[i].date()), str(df.index[ex_idx].date()),
            side, entry_px, ex_px, ex_idx - i, reason, trade_pnl(side, entry_px, ex_px, "ESTX50"),
        ))
    return trades


def strat_sector_rot_eu(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """Weekly Monday DAX vs CAC40 relative momentum."""
    dax = data.get("DAX")
    cac = data.get("CAC40")
    if dax is None or cac is None:
        return []
    dax_mom = dax["close"].pct_change(20)
    cac_mom = cac["close"].pct_change(20)
    trades = []
    for i in range(max(start_idx, 20), end_idx):
        date = dax.index[i]
        if date.weekday() != 0:  # Monday
            continue
        dm = float(dax_mom.iloc[i])
        cm = float(cac_mom.iloc[i]) if date in cac.index else None
        if cm is None or pd.isna(dm) or pd.isna(cm):
            continue
        if dm > cm + 0.02:
            sym = "DAX"
            df = dax
            entry_px = float(df["close"].iloc[i])
        elif cm > dm + 0.02:
            sym = "CAC40"
            df = cac
            idx = cac.index.get_loc(date)
            entry_px = float(df["close"].iloc[idx])
            i_sym = idx
        else:
            continue
        if "i_sym" not in locals():
            i_sym = i
        sl = entry_px * 0.96
        tp = entry_px * 1.08
        ex_idx, ex_px, reason = simulate_exit(df, i_sym, entry_px, "BUY", sl, tp, max_hold_days=5)
        trades.append(SimTrade(
            "sector_rot_eu", sym, str(df.index[i_sym].date()), str(df.index[ex_idx].date()),
            "BUY", entry_px, ex_px, ex_idx - i_sym, reason, trade_pnl("BUY", entry_px, ex_px, sym),
        ))
        del i_sym
    return trades


def strat_gold_equity_div(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MES vs MGC divergence 5 days."""
    mes = data.get("MES")
    mgc = data.get("MGC")
    if mes is None or mgc is None:
        return []
    mes_ret5 = mes["close"].pct_change(5)
    mgc_ret5 = mgc["close"].pct_change(5)
    trades = []
    last = -100
    for i in range(max(start_idx, 10), end_idx):
        if i - last < 5:
            continue
        date = mes.index[i]
        if date not in mgc.index:
            continue
        mr5 = float(mes_ret5.iloc[i])
        gr5 = float(mgc_ret5.loc[date])
        side = None
        if mr5 > 0.02 and gr5 < -0.01:
            side = "SELL"
        elif mr5 < -0.02 and gr5 > 0.01:
            side = "BUY"
        if side is None:
            continue
        entry_px = float(mes["close"].iloc[i])
        if side == "BUY":
            sl = entry_px - 60
            tp = entry_px + 40
        else:
            sl = entry_px + 60
            tp = entry_px - 40
        ex_idx, ex_px, reason = simulate_exit(mes, i, entry_px, side, sl, tp, max_hold_days=5)
        trades.append(SimTrade(
            "gold_equity_div", "MES", str(mes.index[i].date()), str(mes.index[ex_idx].date()),
            side, entry_px, ex_px, ex_idx - i, reason, trade_pnl(side, entry_px, ex_px, "MES"),
        ))
        last = i
    return trades


def strat_commodity_season(data: dict, sym: str, start_idx: int, end_idx: int) -> list[SimTrade]:
    """Seasonal windows (simplified — use calendar months, not actual seasonal ML)."""
    # Crude: buy in Feb/Mar, sell in Sep/Oct. Gold: buy in Aug-Dec.
    df = data.get(sym)
    if df is None:
        return []
    trades = []
    if sym == "MCL":
        buy_months = [2, 3]
        sell_months = [9, 10]
    else:  # MGC
        buy_months = [8, 9, 10]
        sell_months = [1, 2]
    for i in range(max(start_idx, 1), end_idx):
        month = df.index[i].month
        if month in buy_months and df.index[i - 1].month not in buy_months:
            entry_px = float(df["close"].iloc[i])
            sl = entry_px * 0.97
            tp = entry_px * 1.05
            ex_idx, ex_px, reason = simulate_exit(df, i, entry_px, "BUY", sl, tp, max_hold_days=30)
            trades.append(SimTrade(
                f"season_{sym.lower()}", sym, str(df.index[i].date()), str(df.index[ex_idx].date()),
                "BUY", entry_px, ex_px, ex_idx - i, reason, trade_pnl("BUY", entry_px, ex_px, sym),
            ))
    return trades


def strat_mes_mnq_pairs(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MES/MNQ pairs z-score trade."""
    mes = data.get("MES")
    mnq = data.get("MNQ")
    if mes is None or mnq is None:
        return []
    # Log-ratio z-score
    common = mes.index.intersection(mnq.index)
    ratio = np.log(mes["close"].reindex(common)) - np.log(mnq["close"].reindex(common))
    mean = ratio.rolling(20).mean()
    std = ratio.rolling(20).std()
    z = (ratio - mean) / std
    trades = []
    last = -100
    for i in range(max(start_idx, 20), end_idx):
        if i >= len(common):
            break
        zi = float(z.iloc[i])
        if abs(zi) < 2.0 or i - last < 3:
            continue
        date = common[i]
        if date not in mes.index:
            continue
        side = "SELL" if zi > 0 else "BUY"  # revert
        entry_px = float(mes["close"].loc[date])
        if side == "BUY":
            sl = entry_px * 0.98
            tp = entry_px * 1.015
        else:
            sl = entry_px * 1.02
            tp = entry_px * 0.985
        mes_idx = mes.index.get_loc(date)
        ex_idx, ex_px, reason = simulate_exit(mes, mes_idx, entry_px, side, sl, tp, max_hold_days=5)
        trades.append(SimTrade(
            "mes_mnq_pairs", "MES", str(mes.index[mes_idx].date()), str(mes.index[ex_idx].date()),
            side, entry_px, ex_px, ex_idx - mes_idx, reason, trade_pnl(side, entry_px, ex_px, "MES"),
        ))
        last = i
    return trades


def strat_mib_estx50_spread(data: dict, start_idx: int, end_idx: int) -> list[SimTrade]:
    """MIB vs ESTX50 spread."""
    mib = data.get("MIB")
    estx = data.get("ESTX50")
    if mib is None or estx is None:
        return []
    common = mib.index.intersection(estx.index)
    if len(common) < 30:
        return []
    ratio = np.log(mib["close"].reindex(common)) - np.log(estx["close"].reindex(common))
    mean = ratio.rolling(20).mean()
    std = ratio.rolling(20).std()
    z = (ratio - mean) / std
    trades = []
    last = -100
    for i in range(max(start_idx, 20), min(end_idx, len(common))):
        zi = float(z.iloc[i])
        if abs(zi) < 2.0 or i - last < 5:
            continue
        date = common[i]
        if date not in mib.index:
            continue
        side = "SELL" if zi > 0 else "BUY"
        entry_px = float(mib["close"].loc[date])
        if side == "BUY":
            sl = entry_px * 0.98
            tp = entry_px * 1.02
        else:
            sl = entry_px * 1.02
            tp = entry_px * 0.98
        mib_idx = mib.index.get_loc(date)
        ex_idx, ex_px, reason = simulate_exit(mib, mib_idx, entry_px, side, sl, tp, max_hold_days=5)
        trades.append(SimTrade(
            "mib_estx50_spread", "MIB", str(mib.index[mib_idx].date()), str(mib.index[ex_idx].date()),
            side, entry_px, ex_px, ex_idx - mib_idx, reason, trade_pnl(side, entry_px, ex_px, "MIB"),
        ))
        last = i
    return trades


# ==================================================================
# Stats
# ==================================================================
def compute_stats(trades: list[SimTrade]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0, "total": 0, "avg": 0, "sharpe": 0, "mdd": 0}
    pnls = np.array([t.pnl_usd for t in trades])
    n = len(pnls)
    wr = float((pnls > 0).mean())
    total = float(pnls.sum())
    mu = float(pnls.mean())
    sd = float(pnls.std())
    if sd > 0 and n >= 2:
        # Annualize: 15 bars -> trades / 15 * 252
        span = max(1, n)
        sharpe = mu / sd * np.sqrt(252)
    else:
        sharpe = 0
    cum = pnls.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {"n": n, "wr": round(wr, 2), "total": round(total, 0), "avg": round(mu, 2),
            "sharpe": round(sharpe, 2), "mdd": round(mdd, 0)}


def main() -> int:
    print("Loading data (1D + 5M resample merge)…")
    data = {}
    for sym in ["MES", "MNQ", "M2K", "MGC", "MCL", "VIX", "ESTX50", "DAX", "CAC40", "MIB"]:
        df = load_combined_daily(sym)
        if df is not None and not df.empty:
            data[sym] = df
            print(f"  {sym}: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")

    # Find the common end date (last available across majors)
    end_dates = {sym: data[sym].index[-1] for sym in ["MES", "MNQ", "MGC"] if sym in data}
    common_end = min(end_dates.values())
    print(f"\nCommon end date: {common_end.date()}")

    # Define 3-week window: last 21 calendar days ≈ 15 trading days
    start_date = common_end - pd.Timedelta(days=21)
    print(f"3-week window: {start_date.date()} → {common_end.date()}")
    print()

    # For each strat, slice start_idx and end_idx on MES (reference)
    ref = data["MES"]
    start_idx = int((ref.index >= start_date).argmax())
    end_idx = len(ref)

    # Run all 17 strats
    strats = {
        # Already active (6)
        "eu_gap": strat_eu_gap,
        "sector_rot_eu": strat_sector_rot_eu,
        "gold_equity_div": strat_gold_equity_div,
        "season_mcl": lambda d, s, e: strat_commodity_season(d, "MCL", s, e),
        "season_mgc": lambda d, s, e: strat_commodity_season(d, "MGC", s, e),
        "mes_mnq_pairs": strat_mes_mnq_pairs,
        "mib_estx50_spread": strat_mib_estx50_spread,
        # New paper only (9)
        "mes_trend": strat_mes_trend,
        "mes_trend_mr": strat_mes_trend_mr,
        "mes_3day_stretch": strat_mes_3day_stretch,
        "overnight_mnq": strat_overnight_mnq,
        "tsmom_mes": lambda d, s, e: strat_tsmom(d, "MES", s, e),
        "tsmom_mnq": lambda d, s, e: strat_tsmom(d, "MNQ", s, e),
        "m2k_orb": strat_m2k_orb,
        "mcl_brent_lag": strat_mcl_brent_lag,
        "mgc_vix_hedge": strat_mgc_vix_hedge,
        "vix_mr": strat_vix_mr,
    }

    results = {}
    for name, fn in strats.items():
        try:
            trades = fn(data, start_idx, end_idx)
            stats = compute_stats(trades)
            stats["trades"] = trades[:3]  # preview
            results[name] = stats
            print(f"{name}: n={stats['n']} WR={stats['wr']} total=${stats['total']} "
                  f"avg=${stats['avg']} Sharpe={stats['sharpe']} MDD=${stats['mdd']}")
        except Exception as e:
            print(f"{name}: ERROR {e}")
            results[name] = {"n": 0, "error": str(e)}

    print()
    print("=== RANKING BY TOTAL PnL ===")
    sorted_results = sorted(results.items(), key=lambda x: x[1].get("total", 0), reverse=True)
    for name, s in sorted_results:
        if s.get("n", 0) > 0:
            print(f"  {name:25s} {s['n']:3d} trades | ${s['total']:+6.0f} | Sharpe {s['sharpe']:+5.2f} | WR {s['wr']:.0%}")
    print()
    print("=== STRATS SANS TRADE (window trop courte) ===")
    for name, s in sorted_results:
        if s.get("n", 0) == 0 and "error" not in s:
            print(f"  {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
