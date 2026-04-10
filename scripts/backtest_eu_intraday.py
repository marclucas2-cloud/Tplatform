"""
BACKTEST EU INTRADAY — 6 strategies on DAX/CAC40/ESTX50 5min/15min.

Source data: data/eu_intraday/{SYM}_{TF}.parquet (downloaded from IBKR Index)
Period: 2021-01-04 -> 2026-04-10 (~5 ans)

Note importante : data Index IBKR -> volume = 0. Tous les filtres volume
sont remplaces par des filtres ATR/range-based.

Strategies :
  EU-01 : ORB DAX 5min (15min opening range breakout)
  EU-02 : Mean Reversion RSI 15min ESTX50
  EU-03 : Lunch Effect 11:30-13:30 CET (DAX)
  EU-04 : US Open Impact 15:30 CET (ESTX50 + MES)
  EU-05 : Pairs DAX/ESTX50 z-score 15min (market-neutral)
  EU-06 : Macro Event ECB momentum (5min around 14:15 CET)

Approche :
  - Function-based (pas StrategyBase) pour la phase de validation rapide
  - Chaque strat -> list[Trade] avec entry/exit/pnl
  - Walk-forward 5 fenetres (60% IS, 40% OOS reparti)
  - Portfolio combiner avec max 3 positions

Couts en USD (futures EU convertis EUR->USD @ 1.07) :
  ESTX50 (FESX) : $10.7/pt mult, $4 commission RT, $5 slippage RT = ~$10/trade
  DAX (FDXM)    : $5.35/pt mult, $4 commission RT, $2.5 slippage RT = ~$7/trade
  CAC40 (FCE)   : $10.7/pt mult, $4 commission RT, $5 slippage RT = ~$10/trade
  MES           : $5/pt mult, $1.24 commission, $1.25 slippage RT = ~$2.5/trade

Cible :
  - WF >= 3/5 PASS
  - Avg trade > $30 apres couts
  - >= 30 trades sur 5 ans

Usage :
  python scripts/backtest_eu_intraday.py                    # toutes les strats
  python scripts/backtest_eu_intraday.py --strat eu01       # une seule
  python scripts/backtest_eu_intraday.py --no-wf            # backtest simple, pas de WF
  python scripts/backtest_eu_intraday.py --portfolio        # ajout portfolio combine
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import time as dtime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "eu_intraday"
CALENDAR_BCE = ROOT / "data" / "calendar_bce.csv"
REPORTS_DIR = ROOT / "reports" / "research"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# === COSTS (USD per round-trip) ===
COSTS = {
    "ESTX50": {"point_value": 10.7, "comm_rt": 4.0, "slip_pts": 0.5, "slip_usd": 5.35},
    "DAX":    {"point_value": 5.35, "comm_rt": 4.0, "slip_pts": 0.5, "slip_usd": 2.68},
    "CAC40":  {"point_value": 10.7, "comm_rt": 4.0, "slip_pts": 0.5, "slip_usd": 5.35},
    "MES":    {"point_value": 5.0,  "comm_rt": 1.24, "slip_pts": 0.25, "slip_usd": 1.25},
}


def cost_per_trade(symbol: str) -> float:
    """Cout fixe par trade round-trip en USD."""
    c = COSTS[symbol]
    return c["comm_rt"] + c["slip_usd"]


# =============================================================================
# DATA LOADING
# =============================================================================

def load_intraday(symbol: str, tf: str) -> pd.DataFrame:
    """Charge un parquet intraday EU. tf in {'5M', '15M'}."""
    f = DATA_DIR / f"{symbol}_{tf}.parquet"
    if not f.exists():
        raise FileNotFoundError(f"{f} introuvable")
    df = pd.read_parquet(f)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    # Add session features in CET local
    cet = df.index.tz_convert("Europe/Paris")
    df["cet_hour"] = cet.hour
    df["cet_minute"] = cet.minute
    df["cet_time_min"] = df["cet_hour"] * 60 + df["cet_minute"]
    df["date"] = cet.date
    df["dayofweek"] = cet.dayofweek
    return df


def load_mes_5m() -> pd.DataFrame:
    """Charge MES 5min depuis data/futures."""
    f = ROOT / "data" / "futures" / "MES_5M.parquet"
    if not f.exists():
        raise FileNotFoundError(f"{f} introuvable")
    df = pd.read_parquet(f)
    df.columns = [c.lower() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def load_bce_calendar() -> pd.DataFrame:
    """Charge le calendar BCE (42 events 2021-2026)."""
    df = pd.read_csv(CALENDAR_BCE)
    df["dt_local"] = pd.to_datetime(df["date"] + " " + df["time_local"])
    df["dt_utc"] = df["dt_local"].dt.tz_localize("Europe/Paris").dt.tz_convert("UTC")
    return df


# =============================================================================
# TRADE DATACLASS
# =============================================================================

@dataclass
class Trade:
    strategy: str
    symbol: str
    side: str  # BUY or SELL
    entry_dt: pd.Timestamp
    entry_price: float
    sl: float
    tp: float
    exit_dt: pd.Timestamp = None
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0  # USD net of costs
    bars_held: int = 0

    @property
    def entry_date(self) -> str:
        return str(self.entry_dt.date())

    @property
    def exit_date(self) -> str:
        return str(self.exit_dt.date()) if self.exit_dt is not None else ""

    def close(self, exit_dt, exit_price, reason, bars_held):
        self.exit_dt = exit_dt
        self.exit_price = exit_price
        self.exit_reason = reason
        self.bars_held = bars_held
        c = COSTS[self.symbol]
        if self.side == "BUY":
            gross = (exit_price - self.entry_price) * c["point_value"]
        else:
            gross = (self.entry_price - exit_price) * c["point_value"]
        self.pnl = gross - cost_per_trade(self.symbol)


# =============================================================================
# COMMON HELPERS
# =============================================================================

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR sur OHLC."""
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI Wilder."""
    delta = series.diff()
    up = delta.where(delta > 0, 0)
    down = -delta.where(delta < 0, 0)
    avg_up = up.ewm(alpha=1/period, min_periods=period).mean()
    avg_down = down.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_vwap_session(df: pd.DataFrame) -> pd.Series:
    """VWAP par session (reset chaque jour). Volume=0 -> utilise (h+l+c)/3 cumul."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    # Pas de volume -> equally weighted intraday
    return typical.groupby(df["date"]).expanding().mean().reset_index(level=0, drop=True)


# =============================================================================
# STRATEGY EU-01 : OPENING RANGE BREAKOUT (DAX 5min)
# =============================================================================

def strat_eu01_orb_dax(
    df: pd.DataFrame,
    range_minutes: int = 30,
    atr_min_mult: float = 0.7,
    confirm_close: bool = True,
    min_gap_pct: float = 0.003,  # 0.3% gap minimum
) -> list[Trade]:
    """ORB DAX : range 09:00 + range_minutes CET, breakout SL=opposite, TP=1x range.

    Filtre directionnel : gap overnight (close J-1 vs open J) > min_gap_pct.
    Filtre vol : ATR 14 daily > X% du moyen.
    Time exit : 17:00 CET.
    Max 1 trade/jour.
    """
    trades = []
    open_min = 9 * 60        # 09:00 CET
    range_end = open_min + range_minutes
    eod_min = 17 * 60        # 17:00 CET

    df = df.copy()
    df["atr14"] = compute_atr(df, 14)
    atr_long_mean = df["atr14"].rolling(500).mean()

    # Group by date
    for date, day_df in df.groupby("date"):
        if len(day_df) < 30:
            continue

        # Range bars : 09:00 -> 09:00+range_minutes
        range_bars = day_df[(day_df["cet_time_min"] >= open_min) & (day_df["cet_time_min"] < range_end)]
        if len(range_bars) < 2:
            continue

        range_high = range_bars["high"].max()
        range_low = range_bars["low"].min()
        range_size = range_high - range_low
        if range_size <= 0:
            continue

        # ATR filter (volatility regime)
        first_idx = day_df.index[0]
        atr_val = df["atr14"].loc[first_idx]
        atr_mean = atr_long_mean.loc[first_idx]
        if pd.isna(atr_val) or pd.isna(atr_mean) or atr_val < atr_mean * atr_min_mult:
            continue

        # Gap directional filter : compare day open vs prev day last close
        day_open_bar = day_df[day_df["cet_time_min"] == open_min]
        if day_open_bar.empty:
            day_open_bar = day_df.iloc[:1]
        day_open = day_open_bar["open"].iloc[0]

        # Find prev day close
        prev_dates = df[df["date"] < date]
        if prev_dates.empty:
            continue
        prev_close = prev_dates["close"].iloc[-1]
        gap = (day_open - prev_close) / prev_close

        # Trading bars : after range_end -> eod
        trade_bars = day_df[(day_df["cet_time_min"] >= range_end) & (day_df["cet_time_min"] < eod_min)]
        if trade_bars.empty:
            continue

        # Filtre gap minimum
        if abs(gap) < min_gap_pct:
            continue

        position = None
        for ts, bar in trade_bars.iterrows():
            if position is None:
                # Check breakout
                if gap > 0 and bar["close"] > range_high:
                    if confirm_close and bar["close"] <= range_high:
                        continue
                    sl = range_low
                    tp = range_high + range_size
                    position = Trade(
                        strategy="EU-01_ORB_DAX", symbol="DAX", side="BUY",
                        entry_dt=ts, entry_price=bar["close"], sl=sl, tp=tp,
                    )
                elif gap < 0 and bar["close"] < range_low:
                    if confirm_close and bar["close"] >= range_low:
                        continue
                    sl = range_high
                    tp = range_low - range_size
                    position = Trade(
                        strategy="EU-01_ORB_DAX", symbol="DAX", side="SELL",
                        entry_dt=ts, entry_price=bar["close"], sl=sl, tp=tp,
                    )
            else:
                # Manage exit
                if position.side == "BUY":
                    if bar["low"] <= position.sl:
                        position.close(ts, position.sl, "SL", 0)
                        trades.append(position); position = None; break
                    elif bar["high"] >= position.tp:
                        position.close(ts, position.tp, "TP", 0)
                        trades.append(position); position = None; break
                else:
                    if bar["high"] >= position.sl:
                        position.close(ts, position.sl, "SL", 0)
                        trades.append(position); position = None; break
                    elif bar["low"] <= position.tp:
                        position.close(ts, position.tp, "TP", 0)
                        trades.append(position); position = None; break

        # EOD exit if still open
        if position is not None:
            last = trade_bars.iloc[-1]
            position.close(last.name, last["close"], "EOD", 0)
            trades.append(position)

    return trades


# =============================================================================
# STRATEGY EU-02 : MEAN REVERSION RSI 15min ESTX50
# =============================================================================

def strat_eu02_mr_rsi_estx50(
    df: pd.DataFrame,
    rsi_period: int = 14,
    rsi_low: float = 25.0,
    rsi_high: float = 75.0,
    sl_atr_mult: float = 1.5,
    max_hold_bars: int = 16,  # 4h sur 15min
) -> list[Trade]:
    """RSI extreme + filtre VWAP intraday. SL ATR-based, TP retour VWAP.

    Filtre horaire : pas d'entry 09:00-09:15 ni 17:15-17:30 CET.
    Max 2 trades/jour, stop apres 2 SL consecutifs.
    """
    trades = []
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"], rsi_period)
    df["atr"] = compute_atr(df, 14)
    df["vwap"] = compute_vwap_session(df)

    # Sessions
    open_block_end = 9 * 60 + 15
    close_block_start = 17 * 60 + 15

    for date, day_df in df.groupby("date"):
        if len(day_df) < 5:
            continue
        day_trades = 0
        consecutive_sl = 0
        position = None
        first_idx_pos = 0

        for ts, bar in day_df.iterrows():
            tmin = bar["cet_time_min"]

            # Manage open position
            if position is not None:
                if position.side == "BUY":
                    if bar["low"] <= position.sl:
                        position.close(ts, position.sl, "SL", position.bars_held)
                        trades.append(position); position = None
                        consecutive_sl += 1
                        continue
                    elif bar["close"] >= position.tp:
                        position.close(ts, bar["close"], "TP_VWAP", position.bars_held)
                        trades.append(position); position = None
                        consecutive_sl = 0
                        continue
                else:
                    if bar["high"] >= position.sl:
                        position.close(ts, position.sl, "SL", position.bars_held)
                        trades.append(position); position = None
                        consecutive_sl += 1
                        continue
                    elif bar["close"] <= position.tp:
                        position.close(ts, bar["close"], "TP_VWAP", position.bars_held)
                        trades.append(position); position = None
                        consecutive_sl = 0
                        continue

                position.bars_held += 1
                if position.bars_held >= max_hold_bars:
                    position.close(ts, bar["close"], "TIMEOUT", position.bars_held)
                    trades.append(position); position = None
                    continue

            # Entry checks
            if position is not None or day_trades >= 2 or consecutive_sl >= 2:
                continue
            if tmin < open_block_end or tmin > close_block_start:
                continue
            rsi = bar["rsi"]
            atr = bar["atr"]
            vwap = bar["vwap"]
            if pd.isna(rsi) or pd.isna(atr) or pd.isna(vwap) or atr <= 0:
                continue

            # LONG : RSI oversold (<low) ET close < VWAP (vraie capitulation, pas continuation)
            #   -> on attend retour vers VWAP (au-dessus), TP > entry
            if rsi < rsi_low and bar["close"] < vwap:
                sl = bar["close"] - sl_atr_mult * atr
                tp = vwap
                if tp > bar["close"]:
                    position = Trade(
                        strategy="EU-02_MR_RSI_ESTX50", symbol="ESTX50", side="BUY",
                        entry_dt=ts, entry_price=bar["close"], sl=sl, tp=tp,
                    )
                    day_trades += 1
            # SHORT : RSI overbought ET close > VWAP -> retour vers VWAP en bas
            elif rsi > rsi_high and bar["close"] > vwap:
                sl = bar["close"] + sl_atr_mult * atr
                tp = vwap
                if tp < bar["close"]:
                    position = Trade(
                        strategy="EU-02_MR_RSI_ESTX50", symbol="ESTX50", side="SELL",
                        entry_dt=ts, entry_price=bar["close"], sl=sl, tp=tp,
                    )
                    day_trades += 1

        # EOD exit
        if position is not None:
            last = day_df.iloc[-1]
            position.close(last.name, last["close"], "EOD", position.bars_held)
            trades.append(position)

    return trades


# =============================================================================
# STRATEGY EU-03 : LUNCH EFFECT 11:30-13:30 CET (DAX)
# =============================================================================

def strat_eu03_lunch_effect(
    df: pd.DataFrame,
    lunch_start_min: int = 11 * 60 + 30,
    lunch_end_min: int = 13 * 60 + 30,
    deviation_pct: float = 0.0025,  # 0.25% (relaxed)
    sl_pct: float = 0.004,           # 0.4% wider SL
) -> list[Trade]:
    """Mean reversion lunch DAX. Fade mouvements > 0.15% du VWAP intraday.

    SL serre 0.25%, TP retour VWAP, time exit fin lunch.
    """
    trades = []
    df = df.copy()
    df["vwap"] = compute_vwap_session(df)
    df["atr"] = compute_atr(df, 14)

    for date, day_df in df.groupby("date"):
        # Pre-lunch ATR check : moves dans 09:30-11:30
        morning = day_df[(day_df["cet_time_min"] >= 9 * 60 + 30) & (day_df["cet_time_min"] < lunch_start_min)]
        if len(morning) < 10:
            continue
        morning_range = morning["high"].max() - morning["low"].min()
        atr_mean = day_df["atr"].iloc[-1] if not day_df["atr"].isna().all() else 0
        if morning_range < atr_mean * 0.5:
            continue  # journee trop calme

        lunch_df = day_df[(day_df["cet_time_min"] >= lunch_start_min) & (day_df["cet_time_min"] < lunch_end_min)]
        if lunch_df.empty:
            continue

        position = None
        for ts, bar in lunch_df.iterrows():
            vwap = bar["vwap"]
            if pd.isna(vwap) or vwap <= 0:
                continue

            if position is None:
                deviation = (bar["close"] - vwap) / vwap
                if deviation > deviation_pct:
                    # Prix au-dessus VWAP -> SHORT (fade up)
                    sl = bar["close"] * (1 + sl_pct)
                    tp = vwap
                    position = Trade(
                        strategy="EU-03_Lunch_DAX", symbol="DAX", side="SELL",
                        entry_dt=ts, entry_price=bar["close"], sl=sl, tp=tp,
                    )
                elif deviation < -deviation_pct:
                    sl = bar["close"] * (1 - sl_pct)
                    tp = vwap
                    position = Trade(
                        strategy="EU-03_Lunch_DAX", symbol="DAX", side="BUY",
                        entry_dt=ts, entry_price=bar["close"], sl=sl, tp=tp,
                    )
            else:
                if position.side == "BUY":
                    if bar["low"] <= position.sl:
                        position.close(ts, position.sl, "SL", 0)
                        trades.append(position); position = None
                    elif bar["close"] >= position.tp:
                        position.close(ts, bar["close"], "TP_VWAP", 0)
                        trades.append(position); position = None
                else:
                    if bar["high"] >= position.sl:
                        position.close(ts, position.sl, "SL", 0)
                        trades.append(position); position = None
                    elif bar["close"] <= position.tp:
                        position.close(ts, bar["close"], "TP_VWAP", 0)
                        trades.append(position); position = None

        # Time exit at end of lunch
        if position is not None:
            last = lunch_df.iloc[-1]
            position.close(last.name, last["close"], "LUNCH_END", 0)
            trades.append(position)

    return trades


# =============================================================================
# STRATEGY EU-04 : US OPEN IMPACT 15:30 CET (ESTX50 + MES)
# =============================================================================

def strat_eu04_us_open_impact(
    estx_df: pd.DataFrame,
    mes_df: pd.DataFrame,
    obs_minutes: int = 15,
    mes_threshold: float = 0.0015,  # 0.15%
    sl_atr_mult: float = 1.0,
    tp_atr_mult: float = 1.5,
) -> list[Trade]:
    """Trade ESTX50 dans la direction du momentum MES post 15:30 CET.

    1. Observer MES 15:30-15:45 CET
    2. Si move > 0.15% -> trade ESTX50 dans la direction
    3. SL/TP ATR-based, exit 17:15 CET.
    """
    trades = []
    estx_df = estx_df.copy()
    estx_df["atr"] = compute_atr(estx_df, 14)

    us_open_min = 15 * 60 + 30
    obs_end_min = us_open_min + obs_minutes
    eod_min = 17 * 60 + 15

    # Convert MES to CET features for filtering (mes_df is in UTC)
    mes_cet = mes_df.copy()
    mes_cet_idx = mes_cet.index.tz_convert("Europe/Paris")
    mes_cet["cet_time_min"] = mes_cet_idx.hour * 60 + mes_cet_idx.minute
    mes_cet["date"] = mes_cet_idx.date

    for date, day_df in estx_df.groupby("date"):
        # Get MES bars for same date
        mes_day = mes_cet[mes_cet["date"] == date]
        if mes_day.empty or len(day_df) < 10:
            continue

        mes_obs = mes_day[(mes_day["cet_time_min"] >= us_open_min) & (mes_day["cet_time_min"] < obs_end_min)]
        if mes_obs.empty:
            continue

        mes_open = mes_obs["open"].iloc[0]
        mes_close = mes_obs["close"].iloc[-1]
        mes_move = (mes_close - mes_open) / mes_open

        if abs(mes_move) < mes_threshold:
            continue

        # Trade ESTX50 from obs_end_min in MES direction
        trade_bars = day_df[(day_df["cet_time_min"] >= obs_end_min) & (day_df["cet_time_min"] < eod_min)]
        if trade_bars.empty:
            continue

        first_bar = trade_bars.iloc[0]
        atr = first_bar["atr"]
        if pd.isna(atr) or atr <= 0:
            continue

        if mes_move > 0:
            side = "BUY"
            sl = first_bar["close"] - sl_atr_mult * atr
            tp = first_bar["close"] + tp_atr_mult * atr
        else:
            side = "SELL"
            sl = first_bar["close"] + sl_atr_mult * atr
            tp = first_bar["close"] - tp_atr_mult * atr

        position = Trade(
            strategy="EU-04_US_Open_ESTX50", symbol="ESTX50", side=side,
            entry_dt=first_bar.name, entry_price=first_bar["close"], sl=sl, tp=tp,
        )

        for ts, bar in trade_bars.iloc[1:].iterrows():
            if position.side == "BUY":
                if bar["low"] <= position.sl:
                    position.close(ts, position.sl, "SL", 0); break
                elif bar["high"] >= position.tp:
                    position.close(ts, position.tp, "TP", 0); break
            else:
                if bar["high"] >= position.sl:
                    position.close(ts, position.sl, "SL", 0); break
                elif bar["low"] <= position.tp:
                    position.close(ts, position.tp, "TP", 0); break

        if position.exit_dt is None:
            last = trade_bars.iloc[-1]
            position.close(last.name, last["close"], "EOD", 0)
        trades.append(position)

    return trades


# =============================================================================
# STRATEGY EU-05 : PAIRS DAX/ESTX50 15min (market-neutral)
# =============================================================================

def strat_eu05_pairs_dax_estx50(
    dax_df: pd.DataFrame,
    estx_df: pd.DataFrame,
    z_lookback: int = 30,
    z_entry: float = 2.5,
    z_exit: float = 0.3,
    z_sl: float = 3.5,
    max_hold_bars: int = 32,  # 8h sur 15min
) -> list[Trade]:
    """Pairs DAX/ESTX50 z-score sur log_ratio rolling.

    Long spread : LONG ESTX50, SHORT DAX (z > +entry)
    Short spread: SHORT ESTX50, LONG DAX (z < -entry)
    Exit : |z| < exit OU |z| > sl OU max_hold_bars OU EOD.

    Renvoie 2 trades par signal (un par leg) pour pouvoir tracker
    le PnL combine.
    """
    trades = []
    # Aligner les indices
    common = dax_df.index.intersection(estx_df.index)
    if len(common) < z_lookback + 10:
        return trades

    dax = dax_df.loc[common, ["open", "high", "low", "close"]].copy()
    estx = estx_df.loc[common, ["open", "high", "low", "close"]].copy()

    # log ratio ESTX/DAX
    log_ratio = np.log(estx["close"] / dax["close"])
    rolling_mean = log_ratio.rolling(z_lookback).mean()
    rolling_std = log_ratio.rolling(z_lookback).std()
    zscore = (log_ratio - rolling_mean) / rolling_std

    # CET time features for EOD
    cet_idx = common.tz_convert("Europe/Paris")
    cet_time_min = cet_idx.hour * 60 + cet_idx.minute
    cet_date = cet_idx.date
    eod_min = 17 * 60

    position_estx = None
    position_dax = None
    bars_held = 0
    entry_z = 0.0

    for i in range(len(common)):
        ts = common[i]
        z = zscore.iloc[i]
        if pd.isna(z):
            continue

        tmin = cet_time_min[i]
        date = cet_date[i]

        # Manage open positions
        if position_estx is not None:
            bars_held += 1
            should_exit = False
            reason = ""

            # Exit conditions
            if abs(z) < z_exit:
                should_exit = True; reason = "Z_EXIT"
            elif abs(z) > z_sl:
                should_exit = True; reason = "Z_SL"
            elif bars_held >= max_hold_bars:
                should_exit = True; reason = "TIMEOUT"
            elif tmin >= eod_min:
                should_exit = True; reason = "EOD"

            if should_exit:
                position_estx.close(ts, estx["close"].iloc[i], reason, bars_held)
                position_dax.close(ts, dax["close"].iloc[i], reason, bars_held)
                trades.append(position_estx)
                trades.append(position_dax)
                position_estx = None
                position_dax = None
                bars_held = 0
            continue

        # Entry
        if tmin < 9 * 60 + 30 or tmin > eod_min - 60:  # pas d'entry derniere heure
            continue

        if z > z_entry:
            # ESTX overpriced -> SHORT ESTX, LONG DAX
            position_estx = Trade(
                strategy="EU-05_Pairs_DAX_ESTX50", symbol="ESTX50", side="SELL",
                entry_dt=ts, entry_price=estx["close"].iloc[i], sl=0, tp=0,
            )
            position_dax = Trade(
                strategy="EU-05_Pairs_DAX_ESTX50", symbol="DAX", side="BUY",
                entry_dt=ts, entry_price=dax["close"].iloc[i], sl=0, tp=0,
            )
            entry_z = z
            bars_held = 0
        elif z < -z_entry:
            position_estx = Trade(
                strategy="EU-05_Pairs_DAX_ESTX50", symbol="ESTX50", side="BUY",
                entry_dt=ts, entry_price=estx["close"].iloc[i], sl=0, tp=0,
            )
            position_dax = Trade(
                strategy="EU-05_Pairs_DAX_ESTX50", symbol="DAX", side="SELL",
                entry_dt=ts, entry_price=dax["close"].iloc[i], sl=0, tp=0,
            )
            entry_z = z
            bars_held = 0

    return trades


# =============================================================================
# STRATEGY EU-06 : MACRO EVENT ECB MOMENTUM (ESTX50 5min)
# =============================================================================

def strat_eu06_macro_ecb(
    df: pd.DataFrame,
    bce_calendar: pd.DataFrame,
    momentum_threshold: float = 0.0015,  # 0.15% (mean abs move 30min = 0.245%)
    obs_minutes: int = 30,                # ECB press conf @ 14:45 -> obs jusqu'a 14:45+
    sl_pct_of_move: float = 0.5,
    tp_mult_of_move: float = 2.0,
    max_hold_bars: int = 36,              # 3h en 5min
) -> list[Trade]:
    """Trade le momentum post-annonce BCE.

    1. A T+30min apres annonce 14:15 CET (couvre press conference 14:45),
       mesurer le move ESTX50
    2. Si |move| > 0.15% -> trade dans la direction (momentum follow-through)
    3. SL = 50% du move, TP = 2x move, time exit 3h
    """
    trades = []
    df = df.copy()

    for _, event in bce_calendar.iterrows():
        event_utc = event["dt_utc"]

        # Find the bar at or just after event_utc
        post_event = df[df.index >= event_utc]
        if len(post_event) < obs_minutes // 5 + 5:
            continue

        # Bar at announcement (T0)
        t0_bar = post_event.iloc[0]
        # obs_bars = nb of bars over obs_minutes
        n_obs_bars = max(1, obs_minutes // 5)
        obs_bars = post_event.iloc[:n_obs_bars]
        if obs_bars.empty:
            continue

        t0_open = t0_bar["open"]
        t_obs_close = obs_bars["close"].iloc[-1]
        move = (t_obs_close - t0_open) / t0_open

        if abs(move) < momentum_threshold:
            continue

        # Entry at obs_bar close, in direction of move
        entry_idx = obs_bars.index[-1]
        entry_pos = df.index.get_loc(entry_idx)
        if entry_pos + 1 >= len(df):
            continue

        entry_bar = df.iloc[entry_pos + 1]
        entry_price = entry_bar["open"]

        if move > 0:
            side = "BUY"
            sl = entry_price * (1 - abs(move) * sl_pct_of_move)
            tp = entry_price * (1 + abs(move) * tp_mult_of_move)
        else:
            side = "SELL"
            sl = entry_price * (1 + abs(move) * sl_pct_of_move)
            tp = entry_price * (1 - abs(move) * tp_mult_of_move)

        position = Trade(
            strategy="EU-06_Macro_ECB_ESTX50", symbol="ESTX50", side=side,
            entry_dt=entry_bar.name, entry_price=entry_price, sl=sl, tp=tp,
        )

        # Manage exit
        future_bars = df.iloc[entry_pos + 2:entry_pos + 2 + max_hold_bars]
        for ts, bar in future_bars.iterrows():
            if position.side == "BUY":
                if bar["low"] <= position.sl:
                    position.close(ts, position.sl, "SL", 0); break
                elif bar["high"] >= position.tp:
                    position.close(ts, position.tp, "TP", 0); break
            else:
                if bar["high"] >= position.sl:
                    position.close(ts, position.sl, "SL", 0); break
                elif bar["low"] <= position.tp:
                    position.close(ts, position.tp, "TP", 0); break

        if position.exit_dt is None and not future_bars.empty:
            last = future_bars.iloc[-1]
            position.close(last.name, last["close"], "TIMEOUT", 0)
        elif position.exit_dt is None:
            continue

        trades.append(position)

    return trades


# =============================================================================
# METRICS + WALK-FORWARD
# =============================================================================

def compute_metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0, "pnl": 0, "wr": 0, "sharpe": 0, "max_dd": 0, "pf": 0, "avg": 0}
    pnls = np.array([t.pnl for t in trades])
    n = len(pnls)
    wins = int((pnls > 0).sum())
    pnl_total = float(pnls.sum())
    cum = np.cumsum(pnls)
    max_dd = float((cum - np.maximum.accumulate(cum)).min())
    sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252)) if np.std(pnls) > 0 else 0.0
    pos_pnl = pnls[pnls > 0].sum()
    neg_pnl = abs(pnls[pnls < 0].sum())
    pf = float(pos_pnl / neg_pnl) if neg_pnl > 0 else 0.0
    return {
        "n": n, "pnl": pnl_total, "wr": wins / n, "sharpe": sharpe,
        "max_dd": max_dd, "pf": pf, "avg": pnl_total / n,
    }


def walk_forward(
    strat_fn: Callable,
    data: pd.DataFrame | tuple,
    n_windows: int = 5,
    is_pct: float = 0.6,
    label: str = "",
    **strat_kwargs,
) -> dict:
    """Walk-forward 5 fenetres rolling. data peut etre 1 DataFrame ou tuple."""
    # Flatten to single index for splitting
    if isinstance(data, tuple):
        primary = data[0]
    else:
        primary = data

    n = len(primary)
    if n < 1000:
        return {"verdict": "SKIP_TOO_SMALL", "n_bars": n}

    oos_size = int(n * (1 - is_pct) / n_windows)
    is_size = int(n * is_pct)

    windows_results = []
    for w in range(n_windows):
        oos_start = is_size + w * oos_size
        oos_end = min(oos_start + oos_size, n)
        if oos_end <= oos_start:
            break

        # Slice : on donne IS+OOS jusqu'a oos_end (le strat verra IS pour ses indicateurs)
        if isinstance(data, tuple):
            sliced = tuple(d.iloc[:oos_end] if isinstance(d, pd.DataFrame) else d for d in data)
            all_trades = strat_fn(*sliced, **strat_kwargs)
        else:
            sliced = data.iloc[:oos_end]
            all_trades = strat_fn(sliced, **strat_kwargs)

        # Filter to OOS only
        oos_start_dt = primary.index[oos_start]
        oos_end_dt = primary.index[oos_end - 1]
        oos_trades = [t for t in all_trades if oos_start_dt <= t.entry_dt <= oos_end_dt]

        m = compute_metrics(oos_trades)
        windows_results.append({
            "w": w + 1,
            "period": f"{oos_start_dt.date()} -> {oos_end_dt.date()}",
            **m,
        })

    profit_w = sum(1 for w in windows_results if w["pnl"] > 0)
    nw = len(windows_results)
    total_pnl = sum(w["pnl"] for w in windows_results)
    total_trades = sum(w["n"] for w in windows_results)
    avg_sharpe = float(np.mean([w["sharpe"] for w in windows_results])) if windows_results else 0
    verdict = "PASS" if profit_w >= nw * 0.5 and total_trades >= 30 else "FAIL"

    return {
        "label": label,
        "n_windows": nw,
        "profitable_windows": profit_w,
        "wf_ratio": f"{profit_w}/{nw}",
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "avg_trade": total_pnl / max(total_trades, 1),
        "avg_sharpe": avg_sharpe,
        "verdict": verdict,
        "windows": windows_results,
    }


def print_wf_result(name: str, wf: dict, full_metrics: dict) -> None:
    print(f"\n{'='*78}")
    print(f"  {name}")
    print(f"{'='*78}")
    print(f"  FULL: {full_metrics['n']:>4} trades | "
          f"PnL ${full_metrics['pnl']:>+8,.0f} | WR {full_metrics['wr']:.0%} | "
          f"Avg ${full_metrics['avg']:>+6.0f} | Sharpe {full_metrics['sharpe']:>5.2f} | "
          f"PF {full_metrics['pf']:.2f} | MaxDD ${full_metrics['max_dd']:,.0f}")
    if "windows" not in wf:
        print(f"  WF: {wf.get('verdict', 'SKIP')}")
        return
    for w in wf["windows"]:
        tag = "PROFIT" if w["pnl"] > 0 else "LOSS"
        print(f"  W{w['w']} [{w['period']}]: {w['n']:3d} tr | "
              f"${w['pnl']:>+7,.0f} | WR {w['wr']:.0%} | Sh {w['sharpe']:>5.2f} | {tag}")
    print(f"  WF {wf['wf_ratio']} | Avg trade ${wf['avg_trade']:+.1f} | "
          f"Avg Sharpe {wf['avg_sharpe']:.2f} | VERDICT: {wf['verdict']}")


# =============================================================================
# PORTFOLIO COMBINER (with existing 4 LIVE strats)
# =============================================================================

def portfolio_combine(all_trades: list[Trade], max_pos: int = 3) -> dict:
    """Combine plusieurs strats avec max N positions simultanees.

    Tri par entry_dt, FIFO simple. Si pos pleine, skip le trade.
    """
    sorted_trades = sorted(all_trades, key=lambda t: t.entry_dt)
    open_positions = []  # list of (exit_dt, trade)
    accepted = []
    rejected = 0

    for t in sorted_trades:
        # Close past positions
        open_positions = [(ed, tr) for ed, tr in open_positions if ed > t.entry_dt]
        if len(open_positions) >= max_pos:
            rejected += 1
            continue
        accepted.append(t)
        open_positions.append((t.exit_dt, t))

    return {
        "accepted": accepted,
        "rejected": rejected,
        "n_accepted": len(accepted),
        "metrics": compute_metrics(accepted),
    }


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strat", choices=["eu01", "eu02", "eu03", "eu04", "eu05", "eu06", "all"], default="all")
    parser.add_argument("--no-wf", action="store_true", help="Skip walk-forward")
    parser.add_argument("--portfolio", action="store_true", help="Run portfolio combine")
    parser.add_argument("--save", action="store_true", help="Save results JSON")
    args = parser.parse_args()

    print("=" * 78)
    print("  BACKTEST EU INTRADAY — 6 strategies")
    print("=" * 78)

    # Load data
    print("\nLoading data...")
    dax_5m = load_intraday("DAX", "5M")
    dax_15m = load_intraday("DAX", "15M")
    cac_5m = load_intraday("CAC40", "5M")
    estx_5m = load_intraday("ESTX50", "5M")
    estx_15m = load_intraday("ESTX50", "15M")
    bce_cal = load_bce_calendar()
    print(f"  DAX 5M:    {len(dax_5m):>6} bars  ({dax_5m.index.min().date()} -> {dax_5m.index.max().date()})")
    print(f"  DAX 15M:   {len(dax_15m):>6} bars")
    print(f"  CAC40 5M:  {len(cac_5m):>6} bars")
    print(f"  ESTX50 5M: {len(estx_5m):>6} bars")
    print(f"  ESTX50 15M:{len(estx_15m):>6} bars")
    print(f"  BCE events: {len(bce_cal)}")

    try:
        mes_5m = load_mes_5m()
        print(f"  MES 5M:    {len(mes_5m):>6} bars  ({mes_5m.index.min().date()} -> {mes_5m.index.max().date()})")
    except Exception as e:
        mes_5m = None
        print(f"  MES 5M:    NOT LOADED ({e})")

    results = {}

    # === EU-01 : ORB DAX ===
    if args.strat in ("eu01", "all"):
        print("\n[1/6] EU-01 ORB DAX 5min...")
        trades = strat_eu01_orb_dax(dax_5m)
        m = compute_metrics(trades)
        wf = {} if args.no_wf else walk_forward(strat_eu01_orb_dax, dax_5m, label="EU-01_ORB_DAX")
        print_wf_result("EU-01 ORB DAX", wf, m)
        results["eu01"] = {"trades": trades, "metrics": m, "wf": wf}

    # === EU-02 : MR RSI ESTX50 ===
    if args.strat in ("eu02", "all"):
        print("\n[2/6] EU-02 MR RSI ESTX50 15min...")
        trades = strat_eu02_mr_rsi_estx50(estx_15m)
        m = compute_metrics(trades)
        wf = {} if args.no_wf else walk_forward(strat_eu02_mr_rsi_estx50, estx_15m, label="EU-02_MR_RSI_ESTX50")
        print_wf_result("EU-02 MR RSI ESTX50", wf, m)
        results["eu02"] = {"trades": trades, "metrics": m, "wf": wf}

    # === EU-03 : Lunch Effect DAX ===
    if args.strat in ("eu03", "all"):
        print("\n[3/6] EU-03 Lunch Effect DAX 5min...")
        trades = strat_eu03_lunch_effect(dax_5m)
        m = compute_metrics(trades)
        wf = {} if args.no_wf else walk_forward(strat_eu03_lunch_effect, dax_5m, label="EU-03_Lunch_DAX")
        print_wf_result("EU-03 Lunch Effect DAX", wf, m)
        results["eu03"] = {"trades": trades, "metrics": m, "wf": wf}

    # === EU-04 : US Open Impact ===
    if args.strat in ("eu04", "all"):
        if mes_5m is None:
            print("\n[4/6] EU-04 US Open Impact - SKIP (no MES data)")
        else:
            print("\n[4/6] EU-04 US Open Impact ESTX50 5min...")
            trades = strat_eu04_us_open_impact(estx_5m, mes_5m)
            m = compute_metrics(trades)
            wf = {} if args.no_wf else walk_forward(
                strat_eu04_us_open_impact, (estx_5m, mes_5m), label="EU-04_US_Open"
            )
            print_wf_result("EU-04 US Open Impact", wf, m)
            results["eu04"] = {"trades": trades, "metrics": m, "wf": wf}

    # === EU-05 : Pairs DAX/ESTX50 ===
    if args.strat in ("eu05", "all"):
        print("\n[5/6] EU-05 Pairs DAX/ESTX50 15min...")
        trades = strat_eu05_pairs_dax_estx50(dax_15m, estx_15m)
        m = compute_metrics(trades)
        wf = {} if args.no_wf else walk_forward(
            strat_eu05_pairs_dax_estx50, (dax_15m, estx_15m), label="EU-05_Pairs"
        )
        print_wf_result("EU-05 Pairs DAX/ESTX50", wf, m)
        results["eu05"] = {"trades": trades, "metrics": m, "wf": wf}

    # === EU-06 : Macro Event ECB ===
    if args.strat in ("eu06", "all"):
        print("\n[6/6] EU-06 Macro Event ECB 5min...")
        trades = strat_eu06_macro_ecb(estx_5m, bce_cal)
        m = compute_metrics(trades)
        # WF impossible avec si peu d'events, on print direct
        print_wf_result("EU-06 Macro Event ECB", {"verdict": "N/A_SMALL_N"}, m)
        results["eu06"] = {"trades": trades, "metrics": m, "wf": {"verdict": "N/A_SMALL_N"}}

    # === SUMMARY TABLE ===
    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    print(f"{'Strat':<28} {'N':>5} {'PnL':>10} {'Avg':>8} {'WR':>5} {'Shrp':>6} {'WF':>6} {'Verdict':>10}")
    print("-" * 78)
    for k, r in results.items():
        m = r["metrics"]
        wf = r.get("wf", {})
        verdict = wf.get("verdict", "N/A")
        wf_ratio = wf.get("wf_ratio", "-")
        print(f"{k.upper():<28} {m['n']:>5} ${m['pnl']:>+8,.0f} ${m['avg']:>+6.0f} {m['wr']*100:>4.0f}% {m['sharpe']:>5.2f} {wf_ratio:>6} {verdict:>10}")

    # === PORTFOLIO COMBINE ===
    if args.portfolio:
        print("\n" + "=" * 78)
        print("  PORTFOLIO COMBINE — only PASS strats")
        print("=" * 78)
        all_trades = []
        for k, r in results.items():
            wf = r.get("wf", {})
            if wf.get("verdict") in ("PASS", "N/A_SMALL_N") and r["metrics"]["avg"] > 30:
                all_trades.extend(r["trades"])
                print(f"  Including {k.upper()}: {r['metrics']['n']} trades, avg ${r['metrics']['avg']:+.0f}")
        if all_trades:
            combo = portfolio_combine(all_trades, max_pos=3)
            print(f"\n  Combined: {combo['n_accepted']} trades accepted, {combo['rejected']} rejected (max_pos=3)")
            cm = combo["metrics"]
            print(f"  Total PnL: ${cm['pnl']:+,.0f} | Avg ${cm['avg']:+.0f} | "
                  f"WR {cm['wr']*100:.0f}% | Sharpe {cm['sharpe']:.2f} | MaxDD ${cm['max_dd']:,.0f}")
        else:
            print("  No strat eligible for portfolio")

    # === SAVE ===
    if args.save:
        out = {}
        for k, r in results.items():
            out[k] = {
                "metrics": r["metrics"],
                "wf": {kk: vv for kk, vv in r.get("wf", {}).items() if kk != "windows"} | {
                    "windows": r.get("wf", {}).get("windows", [])
                },
                "n_trades": len(r["trades"]),
            }
        outfile = REPORTS_DIR / "backtest_eu_intraday.json"
        with open(outfile, "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n  Saved: {outfile}")


if __name__ == "__main__":
    main()
