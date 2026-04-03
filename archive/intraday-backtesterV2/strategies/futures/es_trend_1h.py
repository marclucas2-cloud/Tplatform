"""
FUT-1 : ES/NQ Trend Following 1H (Proxy sur SPY)

Edge structurel :
Le trend-following sur futures est un edge prouve depuis des decennies.
Les tendances de 1H sur ES (S&P 500 futures) persistent grace aux flux
institutionnels et au momentum systematique.

Proxy : On utilise SPY en 1H (pas de donnees futures reelles) pour simuler
le comportement du ES trend-following.

Regles :
- Long si prix > EMA(20) > EMA(50) en 1H
- Short si prix < EMA(20) < EMA(50) en 1H
- Stop = 2x ATR(14) 1H
- Target = 3x ATR(14) 1H
- Un seul trade a la fois, close en fin de journee

Donnees : yfinance SPY 1H (~2 ans max dispo)
"""
import pandas as pd
import numpy as np


INITIAL_CAPITAL = 100_000
COMMISSION_PER_SHARE = 0.005
SLIPPAGE_PCT = 0.0002

# EMA periods
EMA_FAST = 20
EMA_SLOW = 50

# Risk
ATR_PERIOD = 14
ATR_STOP_MULT = 2.0
ATR_TARGET_MULT = 3.0


class ESTrend1HStrategy:
    """
    Standalone 1H strategy — uses its own backtest logic on hourly data.
    Does NOT inherit BaseStrategy (different timeframe).
    """
    name = "ES/NQ Trend Following 1H (SPY Proxy)"

    def backtest(self, spy_1h: pd.DataFrame) -> pd.DataFrame:
        """
        Run the trend-following backtest on SPY 1H data.

        spy_1h: DataFrame with columns [open, high, low, close, volume]
                and a DatetimeIndex at 1H frequency.

        Returns: DataFrame of trades.
        """
        if spy_1h.empty or len(spy_1h) < EMA_SLOW + ATR_PERIOD + 10:
            return pd.DataFrame()

        df = spy_1h.copy()

        # Calculate indicators
        df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

        # ATR calculation
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift(1)),
                abs(df["low"] - df["close"].shift(1)),
            ),
        )
        df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()

        # Drop rows without enough data
        df = df.dropna(subset=["ema_fast", "ema_slow", "atr"])

        if df.empty:
            return pd.DataFrame()

        # ── Simulate trades ──
        trades = []
        position = None  # None, "LONG", or "SHORT"
        entry_price = 0.0
        entry_time = None
        stop_loss = 0.0
        take_profit = 0.0
        entry_atr = 0.0

        capital = INITIAL_CAPITAL

        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            prev_row = df.iloc[i - 1]

            price = row["close"]
            ema_f = row["ema_fast"]
            ema_s = row["ema_slow"]
            atr = row["atr"]

            if atr <= 0:
                continue

            # ── Check if position needs to be closed ──
            if position is not None:
                # Check stop loss
                hit_sl = False
                hit_tp = False

                if position == "LONG":
                    if row["low"] <= stop_loss:
                        hit_sl = True
                    if row["high"] >= take_profit:
                        hit_tp = True
                else:  # SHORT
                    if row["high"] >= stop_loss:
                        hit_sl = True
                    if row["low"] <= take_profit:
                        hit_tp = True

                # Determine exit
                exit_price = None
                exit_reason = None

                if hit_sl and hit_tp:
                    # Ambiguous — use close direction
                    if position == "LONG":
                        exit_price = take_profit if price > entry_price else stop_loss
                    else:
                        exit_price = take_profit if price < entry_price else stop_loss
                    exit_reason = "tp_or_sl"
                elif hit_sl:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif hit_tp:
                    exit_price = take_profit
                    exit_reason = "take_profit"
                # Also check trend reversal
                elif position == "LONG" and ema_f < ema_s:
                    exit_price = price
                    exit_reason = "trend_reversal"
                elif position == "SHORT" and ema_f > ema_s:
                    exit_price = price
                    exit_reason = "trend_reversal"

                if exit_price is not None:
                    # Apply slippage
                    if position == "LONG":
                        actual_exit = exit_price * (1 - SLIPPAGE_PCT)
                        pnl_per_share = actual_exit - entry_price
                    else:
                        actual_exit = exit_price * (1 + SLIPPAGE_PCT)
                        pnl_per_share = entry_price - actual_exit

                    shares = int((capital * 0.05) / entry_price)
                    if shares < 1:
                        shares = 1

                    pnl = pnl_per_share * shares
                    commission = shares * COMMISSION_PER_SHARE * 2  # entry + exit

                    entry_date = entry_time.date() if hasattr(entry_time, 'date') else pd.Timestamp(entry_time).date()

                    trades.append({
                        "ticker": "SPY",
                        "date": entry_date,
                        "direction": position,
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(actual_exit, 4),
                        "shares": shares,
                        "pnl": round(pnl, 2),
                        "commission": round(commission, 2),
                        "net_pnl": round(pnl - commission, 2),
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "exit_reason": exit_reason,
                        "strategy": self.name,
                        "atr_at_entry": round(entry_atr, 4),
                    })

                    capital += (pnl - commission)
                    position = None

            # ── Check for new entry signal ──
            if position is None:
                # LONG signal: price > EMA_fast > EMA_slow
                if price > ema_f > ema_s and atr > 0:
                    position = "LONG"
                    entry_price = price * (1 + SLIPPAGE_PCT)
                    entry_time = ts
                    entry_atr = atr
                    stop_loss = entry_price - ATR_STOP_MULT * atr
                    take_profit = entry_price + ATR_TARGET_MULT * atr

                # SHORT signal: price < EMA_fast < EMA_slow
                elif price < ema_f < ema_s and atr > 0:
                    position = "SHORT"
                    entry_price = price * (1 - SLIPPAGE_PCT)
                    entry_time = ts
                    entry_atr = atr
                    stop_loss = entry_price + ATR_STOP_MULT * atr
                    take_profit = entry_price - ATR_TARGET_MULT * atr

        # Close any remaining position
        if position is not None and len(df) > 0:
            last = df.iloc[-1]
            ts = df.index[-1]
            price = last["close"]

            if position == "LONG":
                actual_exit = price * (1 - SLIPPAGE_PCT)
                pnl_per_share = actual_exit - entry_price
            else:
                actual_exit = price * (1 + SLIPPAGE_PCT)
                pnl_per_share = entry_price - actual_exit

            shares = int((capital * 0.05) / entry_price)
            if shares < 1:
                shares = 1

            pnl = pnl_per_share * shares
            commission = shares * COMMISSION_PER_SHARE * 2

            entry_date = entry_time.date() if hasattr(entry_time, 'date') else pd.Timestamp(entry_time).date()

            trades.append({
                "ticker": "SPY",
                "date": entry_date,
                "direction": position,
                "entry_price": round(entry_price, 4),
                "exit_price": round(actual_exit, 4),
                "shares": shares,
                "pnl": round(pnl, 2),
                "commission": round(commission, 2),
                "net_pnl": round(pnl - commission, 2),
                "entry_time": entry_time,
                "exit_time": ts,
                "exit_reason": "end_of_data",
                "strategy": self.name,
                "atr_at_entry": round(entry_atr, 4),
            })

        return pd.DataFrame(trades)
