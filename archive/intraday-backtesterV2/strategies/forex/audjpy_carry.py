"""
FX-1 : Carry Trade AUD/JPY

Edge structurel :
Le carry trade AUD/JPY est un classique :
- AUD offre un taux directeur plus eleve (~4-5%)
- JPY est la devise de financement a taux bas (~0-0.5%)
- Le differentiel de taux (~3-5% annuel) est empoche chaque jour de holding
- Le trend filter (EMA 20) evite les periodes de risk-off violentes

Regles :
- Long si prix > EMA(20) daily. Close si prix < EMA(20).
- Carry annuel ~3-5% -> +3%/252 par jour de holding
- Stop = -2% du capital alloue
- Levier 2:1 max
- Commission/spread : 3 pips (0.03%)

Donnees : yfinance AUDJPY=X daily 5 ans
"""
import pandas as pd
import numpy as np


INITIAL_CAPITAL = 100_000
CARRY_ANNUAL_PCT = 0.03     # 3% annual carry rate
CARRY_DAILY = CARRY_ANNUAL_PCT / 252
EMA_PERIOD = 20
MAX_LOSS_PCT = 0.02         # -2% stop on allocated capital
LEVERAGE = 2.0              # 2:1 leverage
POSITION_SIZE_PCT = 0.10    # 10% of capital per trade
SPREAD_COST_PCT = 0.0003    # 3 pips ~ 0.03%


class AUDJPYCarryStrategy:
    """
    Standalone daily strategy for FX carry trade.
    Does NOT inherit BaseStrategy (different asset class).
    """
    name = "Carry Trade AUD/JPY"

    def backtest(self, audjpy_df: pd.DataFrame) -> pd.DataFrame:
        """
        Run the carry trade backtest on AUD/JPY daily data.

        audjpy_df: DataFrame with columns [open, high, low, close, volume]
                   and a DatetimeIndex.

        Returns: DataFrame of trades.
        """
        if audjpy_df.empty or len(audjpy_df) < EMA_PERIOD + 10:
            return pd.DataFrame()

        df = audjpy_df.copy()

        # Calculate EMA
        df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
        df = df.dropna(subset=["ema"])

        if df.empty:
            return pd.DataFrame()

        # ── Simulate trades ──
        trades = []
        in_position = False
        entry_price = 0.0
        entry_time = None
        allocated_capital = 0.0
        total_carry = 0.0
        holding_days = 0
        capital = INITIAL_CAPITAL

        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            price = row["close"]
            ema = row["ema"]

            if in_position:
                holding_days += 1
                # Accumulate daily carry
                daily_carry = allocated_capital * LEVERAGE * CARRY_DAILY
                total_carry += daily_carry

                # Check stop: -2% of allocated capital
                price_pnl = (price / entry_price - 1) * allocated_capital * LEVERAGE
                total_pnl = price_pnl + total_carry

                if total_pnl <= -allocated_capital * MAX_LOSS_PCT:
                    # Stop loss hit
                    spread_cost = allocated_capital * LEVERAGE * SPREAD_COST_PCT
                    net_pnl = total_pnl - spread_cost

                    entry_date = entry_time.date() if hasattr(entry_time, 'date') else pd.Timestamp(entry_time).date()

                    trades.append({
                        "ticker": "AUDJPY",
                        "date": entry_date,
                        "direction": "LONG",
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(price, 4),
                        "shares": 1,
                        "pnl": round(total_pnl, 2),
                        "commission": round(spread_cost, 2),
                        "net_pnl": round(net_pnl, 2),
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "exit_reason": "stop_loss",
                        "strategy": self.name,
                        "holding_days": holding_days,
                        "carry_earned": round(total_carry, 2),
                        "price_pnl": round(price_pnl, 2),
                    })

                    capital += net_pnl
                    in_position = False
                    continue

                # Check EMA exit: close below EMA
                if price < ema:
                    spread_cost = allocated_capital * LEVERAGE * SPREAD_COST_PCT
                    net_pnl = total_pnl - spread_cost

                    entry_date = entry_time.date() if hasattr(entry_time, 'date') else pd.Timestamp(entry_time).date()

                    trades.append({
                        "ticker": "AUDJPY",
                        "date": entry_date,
                        "direction": "LONG",
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(price, 4),
                        "shares": 1,
                        "pnl": round(total_pnl, 2),
                        "commission": round(spread_cost, 2),
                        "net_pnl": round(net_pnl, 2),
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "exit_reason": "ema_exit",
                        "strategy": self.name,
                        "holding_days": holding_days,
                        "carry_earned": round(total_carry, 2),
                        "price_pnl": round(price_pnl, 2),
                    })

                    capital += net_pnl
                    in_position = False

            else:
                # Entry: price above EMA
                if price > ema:
                    in_position = True
                    entry_price = price
                    entry_time = ts
                    allocated_capital = capital * POSITION_SIZE_PCT
                    total_carry = 0.0
                    holding_days = 0

        # Close any remaining position
        if in_position and len(df) > 0:
            last = df.iloc[-1]
            ts = df.index[-1]
            price = last["close"]

            price_pnl = (price / entry_price - 1) * allocated_capital * LEVERAGE
            total_pnl = price_pnl + total_carry
            spread_cost = allocated_capital * LEVERAGE * SPREAD_COST_PCT
            net_pnl = total_pnl - spread_cost

            entry_date = entry_time.date() if hasattr(entry_time, 'date') else pd.Timestamp(entry_time).date()

            trades.append({
                "ticker": "AUDJPY",
                "date": entry_date,
                "direction": "LONG",
                "entry_price": round(entry_price, 4),
                "exit_price": round(price, 4),
                "shares": 1,
                "pnl": round(total_pnl, 2),
                "commission": round(spread_cost, 2),
                "net_pnl": round(net_pnl, 2),
                "entry_time": entry_time,
                "exit_time": ts,
                "exit_reason": "end_of_data",
                "strategy": self.name,
                "holding_days": holding_days,
                "carry_earned": round(total_carry, 2),
                "price_pnl": round(price_pnl, 2),
            })

        return pd.DataFrame(trades)
