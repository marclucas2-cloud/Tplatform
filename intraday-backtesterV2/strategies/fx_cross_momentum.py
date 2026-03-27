"""
FX-005 : Cross-Pair Momentum FX

Edge:
Cross-sectional momentum in FX is a well-documented factor
(Lustig, Roussanov, Verdelhan 2011). Currencies that have appreciated
over the past month continue to outperform, while losers continue to
underperform. Overweight winners, underweight losers on a weekly basis.

Signal:
  - Weekly rebalance (Monday 00:00 UTC)
  - Calculate 20-day returns for all FX pairs
  - Rank pairs by 20d return
  - Top 2 pairs: LONG signal (strongest momentum)
  - Bottom 2 pairs: SHORT signal (weakest momentum)
  - Middle pairs: no signal

Risk:
  - Stop: 2 ATR(14) per pair
  - No fixed TP (momentum capture = let it run to weekly rebalance)
  - TP set at 6 ATR(14) as a safety cap (3:1 R/R)
  - Holding: 1 week (Monday open to Friday close)
  - Costs: ~0.01% round-trip (FX IBKR)

Filters:
  - Skip if average FX vol (ATR) > 3x normal (crisis regime)
  - No trade during FX rollover window (22:00-23:00 UTC)
  - Only generate signals on Mondays

Instruments: EUR/USD, EUR/GBP, EUR/JPY, AUD/JPY, GBP/USD, USD/CHF, NZD/USD

Walk-forward expectations:
  - Target Sharpe OOS: 0.8-2.0
  - Min 50% OOS windows profitable
  - ~50 round-trip trades/year per pair (~350 total)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# ─── Constants ────────────────────────────────────────────────────────────────

FX_PAIRS = ["EURUSD", "EURGBP", "EURJPY", "AUDJPY", "GBPUSD", "USDCHF", "NZDUSD"]

# Momentum
MOMENTUM_LOOKBACK = 20     # 20-day return for ranking
TOP_N = 2                  # Top 2 = LONG
BOTTOM_N = 2               # Bottom 2 = SHORT

# Risk
ATR_PERIOD = 14
STOP_ATR_MULT = 2.0        # Stop = 2 ATR
TP_ATR_MULT = 6.0          # Safety cap TP = 6 ATR (3:1 R/R)

# Crisis filter
ATR_CRISIS_MULT = 3.0      # Skip if avg ATR > 3x normal (rolling 60-bar median)
ATR_NORMAL_LOOKBACK = 60   # Lookback for "normal" ATR baseline

# FX rollover window (22:00-23:00 UTC) — no trade
FX_ROLLOVER_START = dt_time(22, 0)
FX_ROLLOVER_END = dt_time(23, 0)

# FX cost (round-trip)
FX_COST_RT_PCT = 0.0001    # 0.01%

# Minimum pairs required
MIN_PAIRS_REQUIRED = 6


class FXCrossMomentumStrategy(BaseStrategy):
    """
    Cross-Pair Momentum FX — weekly rebalance, rank-based long/short.

    Ranks all available FX pairs by 20-day momentum, goes long the
    top 2 performers and short the bottom 2. Weekly holding period
    (Monday to Friday). Crisis filter skips weeks where average FX
    volatility exceeds 3x normal.
    """

    name = "FX-005 Cross-Pair Momentum"

    def __init__(
        self,
        momentum_lookback: int = MOMENTUM_LOOKBACK,
        top_n: int = TOP_N,
        bottom_n: int = BOTTOM_N,
        atr_period: int = ATR_PERIOD,
        stop_atr_mult: float = STOP_ATR_MULT,
        tp_atr_mult: float = TP_ATR_MULT,
        atr_crisis_mult: float = ATR_CRISIS_MULT,
        min_pairs: int = MIN_PAIRS_REQUIRED,
    ):
        self.momentum_lookback = momentum_lookback
        self.top_n = top_n
        self.bottom_n = bottom_n
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.atr_crisis_mult = atr_crisis_mult
        self.min_pairs = min_pairs

    def get_required_tickers(self) -> list[str]:
        return list(FX_PAIRS)

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Compute Average True Range."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, min_periods=period).mean()

    @staticmethod
    def _is_rollover_window(timestamp: pd.Timestamp) -> bool:
        """Check if timestamp falls in FX rollover window (22:00-23:00 UTC)."""
        if hasattr(timestamp, "tz") and timestamp.tz is not None:
            utc_time = timestamp.tz_convert("UTC").time()
        else:
            utc_time = timestamp.time()
        return FX_ROLLOVER_START <= utc_time < FX_ROLLOVER_END

    @staticmethod
    def _is_monday(date) -> bool:
        """Check if the given date is a Monday (weekday=0)."""
        if hasattr(date, "weekday"):
            return date.weekday() == 0
        try:
            return pd.Timestamp(date).weekday() == 0
        except Exception:
            return False

    def _is_crisis_regime(self, pair_data: dict[str, pd.DataFrame]) -> bool:
        """
        Check if average FX volatility exceeds 3x normal.
        Uses the median ATR over ATR_NORMAL_LOOKBACK bars as baseline.
        """
        atr_values = []
        for ticker, df in pair_data.items():
            if ticker not in FX_PAIRS:
                continue
            if len(df) < self.atr_period + ATR_NORMAL_LOOKBACK:
                continue
            atr_series = self._compute_atr(df, self.atr_period)
            if atr_series.empty:
                continue
            current_atr = atr_series.iloc[-1]
            median_atr = atr_series.iloc[-(ATR_NORMAL_LOOKBACK):].median()
            if pd.notna(current_atr) and pd.notna(median_atr) and median_atr > 0:
                atr_values.append(current_atr / median_atr)

        if not atr_values:
            return False

        avg_ratio = np.mean(atr_values)
        return avg_ratio > self.atr_crisis_mult

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate cross-pair momentum signals on Mondays.

        Ranks all available FX pairs by 20-day return.
        Top 2: LONG, Bottom 2: SHORT.

        Args:
            data: {ticker: DataFrame} with OHLCV columns
            date: current trading date

        Returns:
            list[Signal] — up to top_n + bottom_n signals
        """
        # ── Only trade on Mondays ──
        if not self._is_monday(date):
            return []

        # ── Collect available pairs with enough data ──
        pair_data = {}
        for ticker in FX_PAIRS:
            if ticker in data:
                df = data[ticker]
                min_bars = self.momentum_lookback + self.atr_period + 5
                if len(df) >= min_bars:
                    pair_data[ticker] = df

        # ── Need minimum number of pairs ──
        if len(pair_data) < self.min_pairs:
            return []

        # ── Crisis regime filter ──
        if self._is_crisis_regime(pair_data):
            return []

        # ── Calculate 20-day returns and rank ──
        momentum_scores = {}
        atr_map = {}
        last_price_map = {}
        last_ts_map = {}

        for ticker, df in pair_data.items():
            close = df["close"]
            if len(close) < self.momentum_lookback + 1:
                continue

            ret_20d = (close.iloc[-1] / close.iloc[-self.momentum_lookback - 1]) - 1.0
            if pd.isna(ret_20d):
                continue

            atr_series = self._compute_atr(df, self.atr_period)
            atr_val = atr_series.iloc[-1]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            momentum_scores[ticker] = ret_20d
            atr_map[ticker] = atr_val
            last_price_map[ticker] = close.iloc[-1]
            last_ts_map[ticker] = df.index[-1]

        if len(momentum_scores) < self.min_pairs:
            return []

        # ── Rank by momentum ──
        ranked = sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)
        top_pairs = [t for t, _ in ranked[:self.top_n]]
        bottom_pairs = [t for t, _ in ranked[-self.bottom_n:]]

        signals = []

        # ── LONG signals for top performers ──
        for ticker in top_pairs:
            ts = last_ts_map[ticker]

            # Skip rollover window
            if self._is_rollover_window(ts):
                continue

            entry_price = last_price_map[ticker]
            atr_val = atr_map[ticker]
            stop_distance = self.stop_atr_mult * atr_val
            tp_distance = self.tp_atr_mult * atr_val

            signals.append(Signal(
                action="LONG",
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=entry_price - stop_distance,
                take_profit=entry_price + tp_distance,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "momentum_20d": round(momentum_scores[ticker] * 100, 4),
                    "rank": ranked.index((ticker, momentum_scores[ticker])) + 1,
                    "total_pairs": len(momentum_scores),
                    "atr": round(atr_val, 6),
                    "cost_rt_pct": FX_COST_RT_PCT,
                    "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 1),
                    "holding_period": "weekly",
                },
            ))

        # ── SHORT signals for bottom performers ──
        for ticker in bottom_pairs:
            ts = last_ts_map[ticker]

            if self._is_rollover_window(ts):
                continue

            entry_price = last_price_map[ticker]
            atr_val = atr_map[ticker]
            stop_distance = self.stop_atr_mult * atr_val
            tp_distance = self.tp_atr_mult * atr_val

            signals.append(Signal(
                action="SHORT",
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=entry_price + stop_distance,
                take_profit=entry_price - tp_distance,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "momentum_20d": round(momentum_scores[ticker] * 100, 4),
                    "rank": ranked.index((ticker, momentum_scores[ticker])) + 1,
                    "total_pairs": len(momentum_scores),
                    "atr": round(atr_val, 6),
                    "cost_rt_pct": FX_COST_RT_PCT,
                    "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 1),
                    "holding_period": "weekly",
                },
            ))

        return signals
