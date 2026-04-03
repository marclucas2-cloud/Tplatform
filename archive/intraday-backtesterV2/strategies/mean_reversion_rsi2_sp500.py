"""
Mean Reversion RSI2 — swing 2-5j sur S&P 500.

HYPOTHESE : Les actions S&P 500 avec RSI(2) < 10 rebondissent en 3-5 jours.
Filtre trend : prix > SMA(200) pour eviter les couteaux qui tombent.

BASE ACADEMIQUE : Connors RSI, Larry Connors "Short Term Trading Strategies
That Work" (2008). Edge mesure sur 15+ ans de donnees.

SIGNAL :
  - Daily scan S&P 500 (ou top 200 par volume)
  - RSI(2) < 10 ET close > SMA(200) → LONG
  - Exit : RSI(2) > 70 OU 5 jours max OU SL -3%
  - Max 5 positions simultanées

EDGE : Oversold bounce, market-making, mean-reversion large caps.
CORRELATION : Faible — contre-trend (achete les perdants).
COUT : Alpaca $0. Holding 2-5j = pas de PDT.
FREQUENCE : ~15-25 trades/mois.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# S&P 500 top constituents by liquidity (subset for efficiency)
# Full S&P 500 would use universe.py eligible filter
SP500_LIQUID = [
    # Tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "ORCL",
    "CRM", "AMD", "ADBE", "INTC", "QCOM", "CSCO", "IBM",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "OXY",
    # Industrials
    "CAT", "GE", "HON", "UNP", "RTX", "DE", "BA", "LMT",
    # Consumer
    "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "SBUX",
    "TGT", "LOW", "TJX",
    # Communication
    "DIS", "NFLX", "CMCSA", "T", "VZ",
    # Utilities + REIT
    "NEE", "DUK", "SO", "PLD", "AMT",
    # Materials
    "LIN", "APD", "FCX", "NEM",
    # ETFs as benchmark
    "SPY", "QQQ",
]

# Parameters
RSI_PERIOD = 2
RSI_ENTRY_THRESHOLD = 10    # RSI(2) < 10 = oversold
RSI_EXIT_THRESHOLD = 70     # RSI(2) > 70 = exit
SMA_TREND_PERIOD = 200      # Only buy above SMA(200)
MAX_HOLD_DAYS = 5           # Max holding period
STOP_PCT = 0.03             # 3% stop loss
MAX_POSITIONS = 5           # Max concurrent positions
ENTRY_TIME = dt_time(10, 0) # Entry at 10:00 (avoid open noise)


def compute_rsi(series: pd.Series, period: int = 2) -> pd.Series:
    """Compute RSI on a price series."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


class MeanReversionRSI2Strategy(BaseStrategy):
    name = "Mean Reversion RSI2 S&P500"

    def __init__(
        self,
        rsi_period: int = RSI_PERIOD,
        rsi_entry: float = RSI_ENTRY_THRESHOLD,
        rsi_exit: float = RSI_EXIT_THRESHOLD,
        sma_period: int = SMA_TREND_PERIOD,
        max_hold_days: int = MAX_HOLD_DAYS,
        stop_pct: float = STOP_PCT,
        max_positions: int = MAX_POSITIONS,
    ):
        self.rsi_period = rsi_period
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit
        self.sma_period = sma_period
        self.max_hold_days = max_hold_days
        self.stop_pct = stop_pct
        self.max_positions = max_positions

    def get_required_tickers(self) -> list[str]:
        return list(SP500_LIQUID)

    def get_parameters(self) -> dict:
        return {
            "rsi_period": self.rsi_period,
            "rsi_entry": self.rsi_entry,
            "rsi_exit": self.rsi_exit,
            "sma_period": self.sma_period,
            "max_hold_days": self.max_hold_days,
            "stop_pct": self.stop_pct,
        }

    def set_parameters(self, params: dict):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    @staticmethod
    def get_parameter_grid() -> dict:
        return {
            "rsi_period": [2, 3],
            "rsi_entry": [5, 10, 15],
            "rsi_exit": [60, 70, 80],
            "sma_period": [100, 200],
            "max_hold_days": [3, 5, 7],
            "stop_pct": [0.02, 0.03, 0.05],
        }

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker in SP500_LIQUID:
            if ticker in ("SPY", "QQQ"):
                continue  # Benchmarks, not traded
            if ticker not in data or data[ticker].empty:
                continue

            df = data[ticker]
            closes = df["close"]

            if len(closes) < max(self.sma_period, 20):
                continue

            # Compute RSI(2)
            rsi = compute_rsi(closes, self.rsi_period)
            current_rsi = rsi.iloc[-1]

            if np.isnan(current_rsi):
                continue

            # Trend filter: price > SMA(200)
            sma = closes.rolling(self.sma_period).mean()
            current_sma = sma.iloc[-1]
            current_price = closes.iloc[-1]

            if np.isnan(current_sma) or current_price <= current_sma:
                continue  # Below SMA = downtrend, skip

            # RSI(2) < threshold = oversold
            if current_rsi < self.rsi_entry:
                # Score by how oversold (lower RSI = better)
                score = self.rsi_entry - current_rsi
                candidates.append({
                    "ticker": ticker,
                    "price": current_price,
                    "rsi": current_rsi,
                    "sma200": current_sma,
                    "score": score,
                })

        # Sort by most oversold, take top N
        candidates.sort(key=lambda x: x["score"], reverse=True)
        for c in candidates[:self.max_positions]:
            price = c["price"]
            sl = round(price * (1 - self.stop_pct), 2)
            # TP based on RSI exit — use a reasonable price target
            # Estimate: if RSI bounces to 70, price moves ~2-4%
            tp = round(price * 1.04, 2)  # 4% target

            entry_ts = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp(date)

            signals.append(Signal(
                action="LONG",
                ticker=c["ticker"],
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                timestamp=entry_ts,
                metadata={
                    "strategy": "mean_reversion_rsi2",
                    "rsi2": round(c["rsi"], 1),
                    "sma200": round(c["sma200"], 2),
                    "score": round(c["score"], 1),
                    "max_hold_days": self.max_hold_days,
                },
            ))

        return signals
