"""
Strategie : Crude-Equity Lag Play

EDGE : Le crude (USO) bouge avant les energy stocks. Les equity traders
reagissent avec un retard de 15-45 min. On detecte ce lag a 10:00 ET
et on trade le rattrapage. On ne prend QUE les setups ou le lag est
clair et la direction est confirmee par le stock commencant a bouger.

Regles :
- USO comme signal, energy stocks comme trades
- Timing : 9:35-12:00 ET, entry vers 10:00-10:30
- LONG energy : USO up > 0.5% a 10:00, energy stock up < 0.2% a 10:00
  MAIS up > 0 a 10:30 (commence a bouger = confirmation)
- Stop : 0.7%, Target : 1.2%
- Skip si marche (SPY) move > 1.5%
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


class CrudeEquityLagStrategy(BaseStrategy):
    name = "Crude-Equity Lag Play"

    SIGNAL_TICKER = "USO"
    ENERGY_TICKERS = ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "DVN"]

    def __init__(
        self,
        uso_min_move_pct: float = 0.5,
        energy_max_at_check: float = 0.2,
        spy_max_move_pct: float = 1.5,
        stop_pct: float = 0.007,
        target_pct: float = 0.012,
        check_time: tuple = (10, 0),
        entry_time: tuple = (10, 30),
    ):
        self.uso_min_move_pct = uso_min_move_pct
        self.energy_max_at_check = energy_max_at_check
        self.spy_max_move_pct = spy_max_move_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.check_time = dt_time(*check_time)
        self.entry_time = dt_time(*entry_time)

    def get_required_tickers(self) -> list[str]:
        return [self.SIGNAL_TICKER] + self.ENERGY_TICKERS + ["SPY"]

    def _rth(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.between_time("09:30", "16:00")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        if self.SIGNAL_TICKER not in data:
            return signals

        uso_rth = self._rth(data[self.SIGNAL_TICKER])
        if len(uso_rth) < 10:
            return signals

        # SPY macro filter
        if "SPY" in data:
            spy_rth = self._rth(data["SPY"])
            if len(spy_rth) >= 5:
                spy_open = spy_rth.iloc[0]["open"]
                spy_bars = spy_rth[spy_rth.index.time <= self.entry_time]
                if not spy_bars.empty:
                    spy_ret = ((spy_bars.iloc[-1]["close"] - spy_open) / spy_open) * 100
                    if abs(spy_ret) > self.spy_max_move_pct:
                        return signals

        # USO return at check_time
        uso_open = uso_rth.iloc[0]["open"]
        uso_bars_check = uso_rth[uso_rth.index.time <= self.check_time]
        if uso_bars_check.empty:
            return signals

        uso_ret_check = ((uso_bars_check.iloc[-1]["close"] - uso_open) / uso_open) * 100

        if abs(uso_ret_check) < self.uso_min_move_pct:
            return signals

        # USO return at entry_time (must still be in the same direction)
        uso_bars_entry = uso_rth[uso_rth.index.time <= self.entry_time]
        if uso_bars_entry.empty:
            return signals
        uso_ret_entry = ((uso_bars_entry.iloc[-1]["close"] - uso_open) / uso_open) * 100

        # Confirm USO direction is still holding
        if uso_ret_check > 0 and uso_ret_entry < 0.2:
            return signals
        if uso_ret_check < 0 and uso_ret_entry > -0.2:
            return signals

        direction = "LONG" if uso_ret_check > 0 else "SHORT"

        # Find energy stocks that lagged at check_time but started moving at entry_time
        candidates = []
        for ticker in self.ENERGY_TICKERS:
            if ticker not in data:
                continue

            edf = self._rth(data[ticker])
            if len(edf) < 10:
                continue

            stock_open = edf.iloc[0]["open"]

            # Return at check time
            stock_check = edf[edf.index.time <= self.check_time]
            if stock_check.empty:
                continue
            stock_ret_check = ((stock_check.iloc[-1]["close"] - stock_open) / stock_open) * 100

            # Return at entry time
            stock_entry = edf[edf.index.time <= self.entry_time]
            if stock_entry.empty:
                continue
            stock_ret_entry = ((stock_entry.iloc[-1]["close"] - stock_open) / stock_open) * 100

            # Lag detection at check_time
            if direction == "LONG":
                if stock_ret_check > self.energy_max_at_check:
                    continue  # Already followed
                # Confirmation: stock starts moving up by entry_time
                if stock_ret_entry <= stock_ret_check:
                    continue  # Still not moving
                momentum = stock_ret_entry - stock_ret_check
            else:
                if stock_ret_check < -self.energy_max_at_check:
                    continue
                if stock_ret_entry >= stock_ret_check:
                    continue
                momentum = stock_ret_check - stock_ret_entry

            if momentum < 0.05:
                continue

            lag = abs(uso_ret_check) - abs(stock_ret_check)

            candidates.append({
                "ticker": ticker,
                "lag": lag,
                "momentum": momentum,
                "stock_ret_check": stock_ret_check,
                "stock_ret_entry": stock_ret_entry,
                "entry_bar": stock_entry.iloc[-1],
                "entry_ts": stock_entry.index[-1],
            })

        if not candidates:
            return signals

        # Pick the stock with the biggest lag that shows momentum
        candidates.sort(key=lambda x: x["lag"], reverse=True)
        best = candidates[0]

        entry_price = best["entry_bar"]["close"]

        if direction == "LONG":
            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.target_pct)
        else:
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

        signals.append(Signal(
            action=direction,
            ticker=best["ticker"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=best["entry_ts"],
            metadata={
                "strategy": self.name,
                "uso_ret": round(uso_ret_check, 2),
                "stock_ret_check": round(best["stock_ret_check"], 2),
                "stock_ret_entry": round(best["stock_ret_entry"], 2),
                "lag": round(best["lag"], 2),
                "momentum": round(best["momentum"], 2),
            },
        ))

        return signals
