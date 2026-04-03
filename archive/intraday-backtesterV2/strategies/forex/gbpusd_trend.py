"""
FX-3 : GBP/USD Trend Following

Edge structurel :
Le cable (GBP/USD) est l'une des paires les plus liquides et
presente des tendances directionnelles prononcees (post-Brexit,
cycles de taux BoE vs Fed). Le trend filter EMA(20)/EMA(50) capte
ces mouvements, et l'ADX > 20 filtre les periodes de range.

Regles :
- Long  : close > EMA(20) ET EMA(20) > EMA(50) ET ADX(14) > 20
- Short : close < EMA(20) ET EMA(20) < EMA(50) ET ADX(14) > 20
- SL  = 2 x ATR(14) depuis l'entree
- TP  = trailing stop a 1.5 x ATR(14) depuis le plus-haut/plus-bas
- Couts FX : 0.005% round-trip (spread + commission)

Donnees : yfinance GBPUSD=X daily 5 ans
"""
import pandas as pd
import numpy as np

INITIAL_CAPITAL = 100_000
EMA_FAST = 20
EMA_SLOW = 50
ADX_PERIOD = 14
ATR_PERIOD = 14
SL_ATR_MULT = 2.0
TRAIL_ATR_MULT = 1.5
POSITION_SIZE_PCT = 0.10       # 10% du capital par trade
FX_COST_PCT = 0.00005          # 0.005% round-trip


def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calcule l'ADX (Average Directional Index)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx


class GBPUSDTrendStrategy:
    """
    Standalone daily strategy for GBP/USD trend following.
    Does NOT inherit BaseStrategy (different asset class / daily yfinance data).
    """
    name = "GBP/USD Trend"

    def backtest(self, gbpusd_df: pd.DataFrame) -> pd.DataFrame:
        """
        Run the trend-following backtest on GBP/USD daily data.

        gbpusd_df: DataFrame with columns [open, high, low, close, volume]
                   and a DatetimeIndex.

        Returns: DataFrame of trades.
        """
        if gbpusd_df.empty or len(gbpusd_df) < EMA_SLOW + ADX_PERIOD + 10:
            return pd.DataFrame()

        df = gbpusd_df.copy()

        # Indicateurs
        df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
        df["adx"] = _compute_adx(df, ADX_PERIOD)

        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        df["atr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(ATR_PERIOD).mean()

        df = df.dropna(subset=["ema_fast", "ema_slow", "adx", "atr"])

        if df.empty:
            return pd.DataFrame()

        # ── Backtest ──
        trades = []
        capital = INITIAL_CAPITAL
        in_position = False
        direction = None  # "LONG" or "SHORT"
        entry_price = 0.0
        entry_time = None
        stop_loss = 0.0
        trail_stop = 0.0
        best_price = 0.0  # track le high/low pour trailing

        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            price = row["close"]
            atr = row["atr"]
            ema_f = row["ema_fast"]
            ema_s = row["ema_slow"]
            adx_val = row["adx"]

            if in_position:
                # ── Check stops ──
                hit_stop = False
                exit_price = 0.0
                exit_reason = ""

                if direction == "LONG":
                    # Update trailing stop (1.5x ATR from highest)
                    if price > best_price:
                        best_price = price
                        trail_stop = best_price - TRAIL_ATR_MULT * atr

                    if row["low"] <= stop_loss:
                        hit_stop = True
                        exit_price = stop_loss
                        exit_reason = "stop_loss"
                    elif row["low"] <= trail_stop and trail_stop > stop_loss:
                        hit_stop = True
                        exit_price = trail_stop
                        exit_reason = "trailing_stop"
                else:  # SHORT
                    if price < best_price:
                        best_price = price
                        trail_stop = best_price + TRAIL_ATR_MULT * atr

                    if row["high"] >= stop_loss:
                        hit_stop = True
                        exit_price = stop_loss
                        exit_reason = "stop_loss"
                    elif row["high"] >= trail_stop and trail_stop < stop_loss:
                        hit_stop = True
                        exit_price = trail_stop
                        exit_reason = "trailing_stop"

                if hit_stop:
                    allocated = capital * POSITION_SIZE_PCT
                    cost = allocated * FX_COST_PCT
                    if direction == "LONG":
                        pnl = (exit_price / entry_price - 1) * allocated
                    else:
                        pnl = (entry_price / exit_price - 1) * allocated
                    net_pnl = pnl - cost

                    entry_date = entry_time.date() if hasattr(entry_time, "date") else pd.Timestamp(entry_time).date()
                    trades.append({
                        "ticker": "GBPUSD",
                        "date": entry_date,
                        "direction": direction,
                        "entry_price": round(entry_price, 5),
                        "exit_price": round(exit_price, 5),
                        "shares": 1,
                        "pnl": round(pnl, 2),
                        "commission": round(cost, 2),
                        "net_pnl": round(net_pnl, 2),
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "exit_reason": exit_reason,
                        "strategy": self.name,
                    })
                    capital += net_pnl
                    in_position = False
                    direction = None

            # ── Check new entry ──
            if not in_position and adx_val > 20:
                if price > ema_f and ema_f > ema_s:
                    # LONG
                    in_position = True
                    direction = "LONG"
                    entry_price = price
                    entry_time = ts
                    stop_loss = price - SL_ATR_MULT * atr
                    trail_stop = stop_loss
                    best_price = price

                elif price < ema_f and ema_f < ema_s:
                    # SHORT
                    in_position = True
                    direction = "SHORT"
                    entry_price = price
                    entry_time = ts
                    stop_loss = price + SL_ATR_MULT * atr
                    trail_stop = stop_loss
                    best_price = price

        # ── Close remaining position ──
        if in_position and len(df) > 0:
            last = df.iloc[-1]
            ts = df.index[-1]
            price = last["close"]
            allocated = capital * POSITION_SIZE_PCT
            cost = allocated * FX_COST_PCT
            if direction == "LONG":
                pnl = (price / entry_price - 1) * allocated
            else:
                pnl = (entry_price / price - 1) * allocated
            net_pnl = pnl - cost

            entry_date = entry_time.date() if hasattr(entry_time, "date") else pd.Timestamp(entry_time).date()
            trades.append({
                "ticker": "GBPUSD",
                "date": entry_date,
                "direction": direction,
                "entry_price": round(entry_price, 5),
                "exit_price": round(price, 5),
                "shares": 1,
                "pnl": round(pnl, 2),
                "commission": round(cost, 2),
                "net_pnl": round(net_pnl, 2),
                "entry_time": entry_time,
                "exit_time": ts,
                "exit_reason": "end_of_data",
                "strategy": self.name,
            })

        return pd.DataFrame(trades)
