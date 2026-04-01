"""
Earnings Momentum Drift — swing 5-20j post-earnings.

HYPOTHESE : Les actions qui surprennent positivement aux earnings (gap up > 3%
+ volume > 3x) continuent a drifter pendant 20 jours. Le marche sous-reagit
aux bonnes nouvelles fondamentales (PEAD).

BASE ACADEMIQUE : Post-Earnings Announcement Drift (Ball & Brown 1968,
Bernard & Thomas 1989). Un des edges les plus documentes en finance.

SIGNAL :
  - Scanner quotidien : gap > 3% AND volume > 3x avg 20j → LONG
  - Entry : next day open (ou meme jour 10:30 si gap detected pre-market)
  - Exit : 20 jours OU SL -5% OU TP +10%
  - Max 5 positions simultanées

EDGE : Sous-reaction institutionnelle, analysts upgrades decales, flow momentum.
CORRELATION : Faible — event-driven, stock-specific, pas market-directional.
COUT : Alpaca $0. Hold 5-20j. ~8-12 trades/mois (earnings season = plus).
UNIVERS : Top 200 par volume (large+mid caps, liquides).
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# Top 200 US stocks by market cap (covering major earnings)
EARNINGS_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "ORCL",
    "CRM", "AMD", "ADBE", "INTC", "QCOM", "CSCO", "IBM", "NFLX",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP",
    "USB", "PNC", "TFC", "COF", "AIG",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT",
    "DHR", "BMY", "AMGN", "GILD", "ISRG", "MDT", "REGN",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY",
    # Industrials
    "CAT", "GE", "HON", "UNP", "RTX", "DE", "BA", "LMT", "UPS", "FDX",
    # Consumer
    "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "SBUX",
    "TGT", "LOW", "TJX", "DG", "DLTR",
    # Retail / E-commerce
    "TSLA", "F", "GM",
    # Communication
    "DIS", "CMCSA", "T", "VZ",
    # Semis
    "TSM", "ASML", "LRCX", "AMAT", "KLAC", "MRVL", "MU", "ON",
    # Software
    "NOW", "SNOW", "DDOG", "ZS", "CRWD", "PANW", "FTNT",
    # High-beta / meme-adjacent
    "COIN", "MARA", "MSTR", "RIVN", "LCID", "PLTR", "SOFI",
]

# Parameters
MIN_GAP_PCT = 3.0            # Minimum gap up/down at open (%)
MIN_VOLUME_RATIO = 3.0       # Volume must be 3x 20-day average
MAX_HOLD_DAYS = 20           # Max holding period
STOP_PCT = 0.05              # 5% stop loss (earnings = volatile)
TARGET_PCT = 0.10            # 10% target (drift is slow but persistent)
MAX_POSITIONS = 5            # Max concurrent positions
ENTRY_DELAY_BARS = 6         # Wait 30 min (6x 5min) after open to confirm direction
AVG_VOLUME_PERIOD = 20       # Days for average volume calculation


class EarningsDriftSwingStrategy(BaseStrategy):
    name = "Earnings Momentum Drift (Swing)"

    def __init__(
        self,
        min_gap_pct: float = MIN_GAP_PCT,
        min_volume_ratio: float = MIN_VOLUME_RATIO,
        max_hold_days: int = MAX_HOLD_DAYS,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        max_positions: int = MAX_POSITIONS,
    ):
        self.min_gap_pct = min_gap_pct
        self.min_volume_ratio = min_volume_ratio
        self.max_hold_days = max_hold_days
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_positions = max_positions
        self._prev_day_closes: dict[str, float] = {}
        self._prev_day_volumes: dict[str, list[float]] = {}

    def get_required_tickers(self) -> list[str]:
        return list(EARNINGS_UNIVERSE) + ["SPY"]

    def get_parameters(self) -> dict:
        return {
            "min_gap_pct": self.min_gap_pct,
            "min_volume_ratio": self.min_volume_ratio,
            "max_hold_days": self.max_hold_days,
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
            "min_gap_pct": [2.0, 3.0, 4.0, 5.0],
            "min_volume_ratio": [2.0, 3.0, 4.0],
            "max_hold_days": [10, 15, 20],
            "stop_pct": [0.03, 0.05, 0.07],
            "target_pct": [0.06, 0.08, 0.10],
        }

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker in EARNINGS_UNIVERSE:
            if ticker not in data or data[ticker].empty:
                continue

            df = data[ticker]
            if len(df) < 10:
                continue

            closes = df["close"]
            volumes = df["volume"]

            today_open = df["open"].iloc[0] if "open" in df.columns else closes.iloc[0]
            today_close = closes.iloc[-1]
            today_volume = volumes.sum()  # Total volume today (intraday bars summed)

            # Previous day close (need to track across days)
            # Use the first bar's open as proxy if we don't have prev day
            prev_close = self._prev_day_closes.get(ticker, today_open)

            # Gap calculation
            if prev_close <= 0:
                continue
            gap_pct = (today_open - prev_close) / prev_close * 100

            # Volume ratio
            prev_volumes = self._prev_day_volumes.get(ticker, [])
            if len(prev_volumes) < 5:
                # Not enough history, track and skip
                continue
            avg_vol = np.mean(prev_volumes[-AVG_VOLUME_PERIOD:])
            if avg_vol <= 0:
                continue
            vol_ratio = today_volume / avg_vol

            # Earnings drift signal: large gap + volume surge
            if abs(gap_pct) >= self.min_gap_pct and vol_ratio >= self.min_volume_ratio:
                # Direction follows the gap (momentum, not contrarian)
                direction = "LONG" if gap_pct > 0 else "SHORT"

                # Confirmation: price should hold direction intraday
                # (if gap up but closes below open = failed gap, skip)
                if direction == "LONG" and today_close < today_open:
                    continue  # Gap up but reversed — not PEAD
                if direction == "SHORT" and today_close > today_open:
                    continue  # Gap down but reversed

                score = abs(gap_pct) * vol_ratio  # Higher gap + volume = stronger signal

                candidates.append({
                    "ticker": ticker,
                    "direction": direction,
                    "price": today_close,
                    "gap_pct": gap_pct,
                    "vol_ratio": vol_ratio,
                    "score": score,
                })

        # Update tracking for next day
        for ticker in EARNINGS_UNIVERSE:
            if ticker in data and not data[ticker].empty:
                self._prev_day_closes[ticker] = data[ticker]["close"].iloc[-1]
                day_vol = data[ticker]["volume"].sum()
                if ticker not in self._prev_day_volumes:
                    self._prev_day_volumes[ticker] = []
                self._prev_day_volumes[ticker].append(day_vol)
                # Keep only last 30 days
                if len(self._prev_day_volumes[ticker]) > 30:
                    self._prev_day_volumes[ticker] = self._prev_day_volumes[ticker][-30:]

        # Sort by score, take top N
        candidates.sort(key=lambda x: x["score"], reverse=True)
        for c in candidates[:self.max_positions]:
            price = c["price"]

            if c["direction"] == "LONG":
                sl = round(price * (1 - self.stop_pct), 2)
                tp = round(price * (1 + self.target_pct), 2)
            else:
                sl = round(price * (1 + self.stop_pct), 2)
                tp = round(price * (1 - self.target_pct), 2)

            entry_ts = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp(date)

            signals.append(Signal(
                action=c["direction"],
                ticker=c["ticker"],
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                timestamp=entry_ts,
                metadata={
                    "strategy": "earnings_drift_swing",
                    "gap_pct": round(c["gap_pct"], 2),
                    "volume_ratio": round(c["vol_ratio"], 1),
                    "score": round(c["score"], 1),
                    "max_hold_days": self.max_hold_days,
                },
            ))

        return signals
