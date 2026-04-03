"""
Strategie : Crypto Weekend Gap Capture

EDGE : BTC trade 24/7 mais les crypto-proxies (COIN, MARA, etc.) ne tradent
que la semaine. Le gap du lundi matin est souvent excessif et mean-reverts.
On joue la CONTINUATION quand la premiere barre confirme, et on exige
un gap minimum plus selectif.

Regles :
- LUNDI UNIQUEMENT
- Gap > 2% sur un crypto-proxy a l'ouverture (selectif)
- Premiere barre 9:35 confirme la direction
- Stop : 2.0%, Target : 2.5%
- Max 3 positions (diversification parmi les crypto-proxies)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


class CryptoWeekendGapStrategy(BaseStrategy):
    name = "Crypto Weekend Gap Capture"

    CRYPTO_PROXIES = ["COIN", "MARA", "MSTR", "RIOT", "BITF", "CLSK", "HUT"]

    RTH_START = dt_time(9, 35)

    def __init__(
        self,
        min_gap_pct: float = 2.0,
        max_gap_pct: float = 12.0,
        stop_pct: float = 0.020,
        target_pct: float = 0.025,
        max_signals: int = 3,
    ):
        self.min_gap_pct = min_gap_pct
        self.max_gap_pct = max_gap_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_signals = max_signals
        self._prev_closes: dict[str, float] = {}

    def get_required_tickers(self) -> list[str]:
        return self.CRYPTO_PROXIES + ["SPY"]

    def _rth_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.between_time("09:30", "16:00")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        if date.weekday() != 0:
            for ticker in self.CRYPTO_PROXIES:
                if ticker in data and not data[ticker].empty:
                    rth = self._rth_bars(data[ticker])
                    if not rth.empty:
                        self._prev_closes[ticker] = rth.iloc[-1]["close"]
            return signals

        candidates = []
        for ticker in self.CRYPTO_PROXIES:
            if ticker not in data:
                continue

            rth = self._rth_bars(data[ticker])
            if len(rth) < 3:
                continue

            today_open = rth.iloc[0]["open"]

            if ticker not in self._prev_closes:
                self._prev_closes[ticker] = rth.iloc[-1]["close"]
                continue

            prev_close = self._prev_closes[ticker]
            self._prev_closes[ticker] = rth.iloc[-1]["close"]

            if prev_close == 0:
                continue

            gap_pct = ((today_open - prev_close) / prev_close) * 100

            if abs(gap_pct) < self.min_gap_pct or abs(gap_pct) > self.max_gap_pct:
                continue

            # Confirmation bar at 9:35
            confirm_bars = rth[rth.index.time >= self.RTH_START]
            if confirm_bars.empty:
                continue

            first_bar = confirm_bars.iloc[0]
            first_bar_ts = confirm_bars.index[0]
            bullish = first_bar["close"] > first_bar["open"]

            if gap_pct > self.min_gap_pct and bullish:
                direction = "LONG"
            elif gap_pct < -self.min_gap_pct and not bullish:
                direction = "SHORT"
            else:
                continue

            candidates.append({
                "ticker": ticker,
                "gap_pct": gap_pct,
                "direction": direction,
                "entry_price": first_bar["close"],
                "timestamp": first_bar_ts,
            })

        # Sort by absolute gap size and take top N
        candidates.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)

        for c in candidates[:self.max_signals]:
            entry_price = c["entry_price"]
            if c["direction"] == "LONG":
                stop_loss = entry_price * (1 - self.stop_pct)
                take_profit = entry_price * (1 + self.target_pct)
            else:
                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action=c["direction"],
                ticker=c["ticker"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=c["timestamp"],
                metadata={
                    "strategy": self.name,
                    "gap_pct": round(c["gap_pct"], 2),
                },
            ))

        return signals
