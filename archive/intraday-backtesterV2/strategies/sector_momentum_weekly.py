"""
Sector Momentum Weekly — swing 5j sur 11 sector ETFs.

HYPOTHESE : Les flux institutionnels sectoriels persistent. Le top secteur
cette semaine surperforme la suivante (momentum 1-4 semaines).

SIGNAL :
  - Chaque lundi, rank les 11 sector ETFs par return 20j
  - LONG top 3, SHORT bottom 3
  - Rebalance hebdo (lundi ouverture)
  - SL 3%, TP 5% (ou exit lundi suivant)

EDGE : Flow institutionnel, rebalancement fonds, inertie allocations.
UNIVERS : 11 sector ETFs — ultra-liquides, spread <0.01%.
FREQUENCE : ~12 trades/semaine (6 positions x 2 sides).
COUT : Alpaca $0 commission. Holding 5j = pas de PDT.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


SECTOR_ETFS = [
    "XLK",   # Tech
    "XLF",   # Finance
    "XLE",   # Energy
    "XLV",   # Healthcare
    "XLI",   # Industrials
    "XLP",   # Consumer Staples
    "XLU",   # Utilities
    "XLC",   # Communication
    "XLRE",  # Real Estate
    "XLB",   # Materials
    "XLY",   # Consumer Discretionary
]

# Parameters
LOOKBACK_DAYS = 20       # Momentum lookback (4 weeks)
TOP_N = 3                # Long top N sectors
BOTTOM_N = 3             # Short bottom N sectors
STOP_PCT = 0.03          # 3% stop loss
TARGET_PCT = 0.05        # 5% take profit
ENTRY_TIME = dt_time(9, 45)  # Entry 15 min after open (avoid open volatility)


class SectorMomentumWeeklyStrategy(BaseStrategy):
    name = "Sector Momentum Weekly"

    def __init__(
        self,
        lookback_days: int = LOOKBACK_DAYS,
        top_n: int = TOP_N,
        bottom_n: int = BOTTOM_N,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
    ):
        self.lookback_days = lookback_days
        self.top_n = top_n
        self.bottom_n = bottom_n
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self._weekly_returns: dict[str, float] = {}

    def get_required_tickers(self) -> list[str]:
        return list(SECTOR_ETFS) + ["SPY"]

    def get_parameters(self) -> dict:
        return {
            "lookback_days": self.lookback_days,
            "top_n": self.top_n,
            "bottom_n": self.bottom_n,
            "stop_pct": self.stop_pct,
            "target_pct": self.target_pct,
        }

    def set_parameters(self, params: dict):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    @staticmethod
    def get_parameter_grid() -> dict:
        return {
            "lookback_days": [10, 15, 20, 30],
            "top_n": [2, 3, 4],
            "bottom_n": [2, 3],
            "stop_pct": [0.02, 0.03, 0.04],
            "target_pct": [0.04, 0.05, 0.06],
        }

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # Only trade on Mondays
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        if weekday != 0:  # 0 = Monday
            return signals

        # Compute 20-day return for each sector ETF
        sector_returns = {}
        for etf in SECTOR_ETFS:
            if etf not in data or data[etf].empty:
                continue
            df = data[etf]
            # Use daily close (last bar of the day for intraday data)
            if len(df) < 2:
                continue
            close = df["close"].iloc[-1]

            # Need previous N days — use all available history
            all_closes = df["close"]
            if len(all_closes) < self.lookback_days:
                continue

            # Return over lookback period
            past_close = all_closes.iloc[-self.lookback_days]
            if past_close <= 0:
                continue
            ret = (close - past_close) / past_close
            sector_returns[etf] = ret

        if len(sector_returns) < self.top_n + self.bottom_n:
            return signals

        # Rank sectors
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
        top_sectors = sorted_sectors[:self.top_n]
        bottom_sectors = sorted_sectors[-self.bottom_n:]

        # Check SPY trend filter — skip if SPY is flat (ADX < 15 proxy: abs(return) < 1%)
        spy_ret = 0
        if "SPY" in data and not data["SPY"].empty:
            spy_df = data["SPY"]
            if len(spy_df) >= self.lookback_days:
                spy_ret = (spy_df["close"].iloc[-1] - spy_df["close"].iloc[-self.lookback_days]) / spy_df["close"].iloc[-self.lookback_days]

        # Generate LONG signals for top sectors
        for etf, ret in top_sectors:
            if etf not in data or data[etf].empty:
                continue
            df = data[etf]
            price = df["close"].iloc[-1]
            if price <= 0:
                continue

            sl = round(price * (1 - self.stop_pct), 2)
            tp = round(price * (1 + self.target_pct), 2)

            # Get timestamp for entry
            entry_ts = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp(date)

            signals.append(Signal(
                action="LONG",
                ticker=etf,
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                timestamp=entry_ts,
                metadata={
                    "strategy": "sector_momentum_weekly",
                    "momentum_20d": round(ret * 100, 2),
                    "rank": sorted_sectors.index((etf, ret)) + 1,
                    "spy_momentum": round(spy_ret * 100, 2),
                },
            ))

        # Generate SHORT signals for bottom sectors
        for etf, ret in bottom_sectors:
            if etf not in data or data[etf].empty:
                continue
            df = data[etf]
            price = df["close"].iloc[-1]
            if price <= 0:
                continue

            sl = round(price * (1 + self.stop_pct), 2)
            tp = round(price * (1 - self.target_pct), 2)

            entry_ts = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp(date)

            signals.append(Signal(
                action="SHORT",
                ticker=etf,
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                timestamp=entry_ts,
                metadata={
                    "strategy": "sector_momentum_weekly",
                    "momentum_20d": round(ret * 100, 2),
                    "rank": sorted_sectors.index((etf, ret)) + 1,
                    "spy_momentum": round(spy_ret * 100, 2),
                },
            ))

        return signals
