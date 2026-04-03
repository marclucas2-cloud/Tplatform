"""
Indicateurs techniques pour les stratégies intraday.
"""
import pandas as pd
import numpy as np


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calcule le VWAP cumulatif intraday.
    Reset chaque jour.
    """
    df = df.copy()
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical_price"] * df["volume"]
    df["date"] = df.index.date

    vwap_values = []
    for _, day_df in df.groupby("date"):
        cum_tp_vol = day_df["tp_vol"].cumsum()
        cum_vol = day_df["volume"].cumsum()
        day_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        vwap_values.append(day_vwap)

    return pd.concat(vwap_values)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI classique."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.5):
    """Retourne (upper, middle, lower) Bollinger Bands."""
    middle = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index."""
    high, low, close = df["high"], df["low"], df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period).mean()


def orb_range(df: pd.DataFrame, minutes: int = 5):
    """
    Calcule le Opening Range (high/low) des N premières minutes pour chaque jour.
    Retourne un dict {date: {"high": float, "low": float}}
    """
    df = df.copy()
    df["date"] = df.index.date
    df["time"] = df.index.time

    from datetime import time as dt_time
    open_time = dt_time(9, 30)
    end_time = dt_time(9, 30 + minutes) if minutes < 30 else dt_time(10, minutes - 30)

    ranges = {}
    for date, day_df in df.groupby("date"):
        mask = (day_df["time"] >= open_time) & (day_df["time"] < end_time)
        orb_bars = day_df[mask]
        if len(orb_bars) >= 1:
            ranges[date] = {
                "high": orb_bars["high"].max(),
                "low": orb_bars["low"].min(),
                "volume": orb_bars["volume"].sum(),
            }
    return ranges


def gap_pct(df: pd.DataFrame, daily_df: pd.DataFrame) -> dict:
    """
    Calcule le gap d'ouverture en % pour chaque jour.
    Gap = (open_today - close_yesterday) / close_yesterday * 100
    """
    df = df.copy()
    df["date"] = df.index.date

    gaps = {}
    prev_close = None
    for date, day_df in df.groupby("date"):
        today_open = day_df.iloc[0]["open"]
        if prev_close is not None:
            gaps[date] = ((today_open - prev_close) / prev_close) * 100
        prev_close = day_df.iloc[-1]["close"]
    return gaps


def zscore_spread(series_a: pd.Series, series_b: pd.Series, lookback: int = 20) -> pd.Series:
    """
    Calcule le z-score du spread normalisé entre deux séries.
    Utilisé pour la stratégie de corrélation breakdown.
    """
    # Normaliser par le premier prix de chaque jour
    ratio = series_a / series_b
    mean = ratio.rolling(lookback).mean()
    std = ratio.rolling(lookback).std()
    return (ratio - mean) / std.replace(0, np.nan)


def volume_ratio(volume: pd.Series, lookback: int = 20) -> pd.Series:
    """Ratio du volume actuel vs moyenne mobile."""
    avg = volume.rolling(lookback).mean()
    return volume / avg.replace(0, np.nan)
