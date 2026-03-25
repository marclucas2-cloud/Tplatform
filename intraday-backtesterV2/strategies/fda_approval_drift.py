"""
Strategie : FDA Approval Drift (Big Gap Momentum)

EDGE : Les biotech/pharma et autres stocks bougent violemment sur FDA/earnings.
Le drift intraday post-evenement (gap > 4%) persiste car les analystes mettent
des heures a updater. Le momentum intraday est fort.

Regles :
- Scan TOUT l'univers pour des gaps > 4%
- Volume premiere barre > 2x moyenne
- Premiere barre confirme la direction
- Stop : 1.5%, Target : 4.0%
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


class FDAApprovalDriftStrategy(BaseStrategy):
    name = "FDA Approval Drift"

    PHARMA_BIOTECH = [
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
        "AMGN", "GILD", "VRTX", "REGN", "ISRG", "MDT", "SYK", "BDX", "ZTS",
        "MRNA", "CRSP", "XLV",
    ]

    def __init__(
        self,
        min_gap_pct: float = 5.0,
        min_vol_ratio: float = 2.5,
        stop_pct: float = 0.012,
        target_pct: float = 0.035,
    ):
        self.min_gap_pct = min_gap_pct
        self.min_vol_ratio = min_vol_ratio
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self._prev_closes: dict[str, float] = {}
        self._first_bar_vol_avg: dict[str, float] = {}

    def get_required_tickers(self) -> list[str]:
        return self.PHARMA_BIOTECH + ["SPY"]

    def _rth(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.between_time("09:30", "16:00")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # Skip list: benchmarks, leveraged ETFs, inverse ETFs, low-price stocks
        SKIP_PREFIXES = ("SPY", "QQQ", "IWM", "DIA", "XL", "SOXL", "SOXS",
                         "TQQQ", "SQQQ", "UVXY", "UVIX", "VXX", "SVIX",
                         "TSLL", "TSLS", "TSDD", "TSLQ", "TSLG",
                         "NVDL", "NVDX", "SCO", "UCO", "ZSL",
                         "TNA", "TZA", "RWM", "SH", "PSQ", "SPXS", "SPXU",
                         "JDST", "SMCL", "SPYM")

        for ticker, df in data.items():
            if ticker in ("SPY", "QQQ", "IWM", "DIA"):
                rth = self._rth(df)
                if not rth.empty:
                    self._prev_closes[ticker] = rth.iloc[-1]["close"]
                continue

            # Skip leveraged/inverse ETFs and known noise
            if ticker in SKIP_PREFIXES:
                continue

            rth = self._rth(df)
            if len(rth) < 5:
                continue

            today_open = rth.iloc[0]["open"]

            # Price filter: skip penny stocks (< $5)
            if today_open < 5.0:
                continue

            # Use 9:35 bar (first bar after 9:30) for confirmation
            confirm_bars = rth[rth.index.time >= dt_time(9, 35)]
            if confirm_bars.empty:
                continue
            first_bar = confirm_bars.iloc[0]
            first_bar_ts = confirm_bars.index[0]
            first_bar_vol = first_bar["volume"]

            # Update first-bar volume average (EMA)
            if ticker in self._first_bar_vol_avg:
                self._first_bar_vol_avg[ticker] = self._first_bar_vol_avg[ticker] * 0.9 + first_bar_vol * 0.1
            else:
                self._first_bar_vol_avg[ticker] = first_bar_vol

            if ticker not in self._prev_closes:
                self._prev_closes[ticker] = rth.iloc[-1]["close"]
                continue

            prev_close = self._prev_closes[ticker]
            self._prev_closes[ticker] = rth.iloc[-1]["close"]

            if prev_close == 0:
                continue

            gap_pct = ((today_open - prev_close) / prev_close) * 100

            if abs(gap_pct) < self.min_gap_pct:
                continue

            # Volume check vs running average
            avg_vol = self._first_bar_vol_avg.get(ticker, 1)
            vol_ratio = first_bar_vol / avg_vol if avg_vol > 0 else 0

            if vol_ratio < self.min_vol_ratio:
                continue

            entry_price = first_bar["close"]

            if gap_pct > self.min_gap_pct:
                if first_bar["close"] <= first_bar["open"]:
                    continue

                stop_loss = entry_price * (1 - self.stop_pct)
                take_profit = entry_price * (1 + self.target_pct)

                signals.append(Signal(
                    action="LONG",
                    ticker=ticker,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=first_bar_ts,
                    metadata={
                        "strategy": self.name,
                        "gap_pct": round(gap_pct, 2),
                        "vol_ratio": round(vol_ratio, 2),
                    },
                ))

            elif gap_pct < -self.min_gap_pct:
                if first_bar["close"] >= first_bar["open"]:
                    continue

                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

                signals.append(Signal(
                    action="SHORT",
                    ticker=ticker,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=first_bar_ts,
                    metadata={
                        "strategy": self.name,
                        "gap_pct": round(gap_pct, 2),
                        "vol_ratio": round(vol_ratio, 2),
                    },
                ))

        return signals
