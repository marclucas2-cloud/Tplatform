"""
FX-002 : GBP/USD Trend Following

Edge :
BoE vs Fed policy divergence creates persistent GBP/USD trends.
When monetary policy paths diverge (rate differentials widen),
GBP/USD trends are more directional and sustained than in convergent
regimes. EMA crossover captures the trend, ADX confirms strength,
and momentum 20d provides direction agreement.

Signal:
  - EMA 20/50 crossover + ADX > 25 filter + momentum 20d confirmation
  - LONG: EMA20 crosses above EMA50, ADX > 25, close > close[20]
  - SHORT: EMA20 crosses below EMA50, ADX > 25, close < close[20]

Risk:
  - Stop: 1.5 ATR(14) from entry
  - Take profit: 3.0 ATR(14) from entry (2:1 R/R)
  - Holding: 1-10 days (swing)
  - Costs: ~0.01% round-trip (FX IBKR)

Filters:
  - No trade during BoE/Fed announcement days (EventCalendar)
  - No trade during FX rollover window (22:00-23:00 UTC)
  - ADX must be > 25 (confirmed trend environment)

Walk-forward expectations:
  - Target Sharpe OOS: 1.0-2.5
  - Min 50% OOS windows profitable
  - Estimated 2-5 trades/month (swing timeframe)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, datetime
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx


# ─── Constants ────────────────────────────────────────────────────────────────

TICKER = "GBPUSD"

# Central bank announcement dates to avoid (2026 calendar — extendable)
# BoE MPC dates + Fed FOMC dates
BOE_DATES = {
    "2026-02-05", "2026-03-19", "2026-05-07", "2026-06-18",
    "2026-08-06", "2026-09-17", "2026-11-05", "2026-12-17",
}
FED_DATES = {
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
}
EVENT_BLACKOUT_DATES = BOE_DATES | FED_DATES

# FX rollover window (22:00-23:00 UTC) — no trade
FX_ROLLOVER_START = dt_time(22, 0)
FX_ROLLOVER_END = dt_time(23, 0)

# FX cost (round-trip)
FX_COST_RT_PCT = 0.0001  # 0.01%


class FXGBPUSDTrendStrategy(BaseStrategy):
    """GBP/USD Trend Following — EMA crossover + ADX + momentum confirmation."""

    name = "FX-002 GBP/USD Trend"

    def __init__(
        self,
        ema_fast: int = 20,
        ema_slow: int = 50,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        momentum_period: int = 20,
        atr_period: int = 14,
        stop_atr_mult: float = 1.5,
        tp_atr_mult: float = 3.0,
        max_trades_per_day: int = 1,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.momentum_period = momentum_period
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return [TICKER]

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Compute Average True Range for FX data."""
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
    def _is_event_blackout(date) -> bool:
        """Check if date is a central bank announcement day."""
        date_str = str(date)[:10]
        return date_str in EVENT_BLACKOUT_DATES

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate trend-following signals for GBP/USD.

        Args:
            data: {ticker: DataFrame} with OHLCV columns
            date: current trading date

        Returns:
            list[Signal] — at most max_trades_per_day signals
        """
        if TICKER not in data:
            return []

        df = data[TICKER]

        # Need enough bars for EMA slow + warmup
        min_bars = self.ema_slow + self.momentum_period + 5
        if len(df) < min_bars:
            return []

        # ── Event blackout filter ──
        if self._is_event_blackout(date):
            return []

        df = df.copy()

        # ── Compute indicators ──
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()
        df["atr"] = self._compute_atr(df, self.atr_period)
        df["adx"] = adx(df, self.adx_period)
        df["momentum"] = df["close"] - df["close"].shift(self.momentum_period)

        # ── Detect EMA crossover ──
        df["ema_diff"] = df["ema_fast"] - df["ema_slow"]
        df["ema_diff_prev"] = df["ema_diff"].shift(1)

        signals = []

        for ts, bar in df.iterrows():
            if len(signals) >= self.max_trades_per_day:
                break

            # Skip NaN rows (warmup)
            if pd.isna(bar["adx"]) or pd.isna(bar["momentum"]) or pd.isna(bar["ema_diff_prev"]):
                continue

            # ── Rollover window filter ──
            if self._is_rollover_window(ts):
                continue

            # ── ADX filter: trend must be strong enough ──
            if bar["adx"] < self.adx_threshold:
                continue

            atr_val = bar["atr"]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            entry_price = bar["close"]
            ema_diff = bar["ema_diff"]
            ema_diff_prev = bar["ema_diff_prev"]

            # ── LONG signal: bullish crossover ──
            if ema_diff > 0 and ema_diff_prev <= 0 and bar["momentum"] > 0:
                stop_loss = entry_price - self.stop_atr_mult * atr_val
                take_profit = entry_price + self.tp_atr_mult * atr_val

                signals.append(Signal(
                    action="LONG",
                    ticker=TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "adx": round(bar["adx"], 1),
                        "momentum_20d": round(bar["momentum"], 6),
                        "atr": round(atr_val, 6),
                        "ema_fast": round(bar["ema_fast"], 6),
                        "ema_slow": round(bar["ema_slow"], 6),
                        "cost_rt_pct": FX_COST_RT_PCT,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 1),
                    },
                ))

            # ── SHORT signal: bearish crossover ──
            elif ema_diff < 0 and ema_diff_prev >= 0 and bar["momentum"] < 0:
                stop_loss = entry_price + self.stop_atr_mult * atr_val
                take_profit = entry_price - self.tp_atr_mult * atr_val

                signals.append(Signal(
                    action="SHORT",
                    ticker=TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "adx": round(bar["adx"], 1),
                        "momentum_20d": round(bar["momentum"], 6),
                        "atr": round(atr_val, 6),
                        "ema_fast": round(bar["ema_fast"], 6),
                        "ema_slow": round(bar["ema_slow"], 6),
                        "cost_rt_pct": FX_COST_RT_PCT,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 1),
                    },
                ))

        return signals[:self.max_trades_per_day]
