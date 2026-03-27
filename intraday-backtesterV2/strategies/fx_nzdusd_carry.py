"""
FX-004 : NZD/USD Carry + Momentum

Edge :
NZD is a commodity-linked, high-yield currency. When the RBNZ rate
exceeds the Fed rate (positive carry), going long NZD/USD captures
the rate differential. Adding momentum confirmation (20-day price
trend aligns with carry direction) filters out carry trades that
fight the trend, dramatically improving risk-adjusted returns.
Asia-Pacific exposure also provides diversification vs EUR/GBP pairs.

Signal:
  - Carry positive (RBNZ rate > Fed rate) + momentum 20d positive = LONG
  - Carry negative (RBNZ rate < Fed rate) + momentum 20d negative = SHORT
  - Both carry AND momentum must agree for a signal

Risk:
  - Stop: 2 ATR(14) from entry
  - Take profit: 4 ATR(14) from entry (2:1 R/R)
  - Holding: 10-30 days
  - Costs: ~0.01% round-trip (FX IBKR)

Filters:
  - No trade if AUD/NZD spread > 2 std from mean (pair dislocation)
  - No trade during FX rollover window (22:00-23:00 UTC)
  - Carry differential must be > 25bps to generate signal

Walk-forward expectations:
  - Target Sharpe OOS: 0.6-1.8
  - Min 50% OOS windows profitable
  - Estimated 1-2 trades/month (longer swing timeframe)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# ─── Constants ────────────────────────────────────────────────────────────────

TICKER = "NZDUSD"
AUDNZD_TICKER = "AUDNZD"  # For dislocation filter

# FX rollover window (22:00-23:00 UTC)
FX_ROLLOVER_START = dt_time(22, 0)
FX_ROLLOVER_END = dt_time(23, 0)

# Rate differentials (RBNZ - Fed) — updated periodically
# Positive = carry-positive for long NZD/USD
# As of early 2026 estimates:
DEFAULT_CARRY_BPS = 75  # 0.75% differential (RBNZ 4.25% vs Fed 3.50%)

# FX cost (round-trip)
FX_COST_RT_PCT = 0.0001  # 0.01%


class FXNZDUSDCarryStrategy(BaseStrategy):
    """NZD/USD Carry + Momentum — carry differential + trend confirmation."""

    name = "FX-004 NZD/USD Carry"

    def __init__(
        self,
        momentum_period: int = 20,
        atr_period: int = 14,
        stop_atr_mult: float = 2.0,
        tp_atr_mult: float = 4.0,
        carry_bps: float = DEFAULT_CARRY_BPS,
        min_carry_bps: float = 25.0,
        audnzd_lookback: int = 60,
        audnzd_std_threshold: float = 2.0,
        max_trades_per_day: int = 1,
    ):
        self.momentum_period = momentum_period
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.carry_bps = carry_bps
        self.min_carry_bps = min_carry_bps
        self.audnzd_lookback = audnzd_lookback
        self.audnzd_std_threshold = audnzd_std_threshold
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return [TICKER, AUDNZD_TICKER]

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

    def _is_audnzd_dislocated(
        self, audnzd_df: pd.DataFrame, timestamp: pd.Timestamp
    ) -> bool:
        """
        Check if AUD/NZD spread is > 2 std from its rolling mean.
        This indicates pair dislocation — unsafe for NZD carry trades.
        """
        if audnzd_df is None or audnzd_df.empty:
            return False  # No data = assume OK

        audnzd_at = audnzd_df[audnzd_df.index <= timestamp]
        if len(audnzd_at) < self.audnzd_lookback:
            return False  # Not enough data to assess

        close_series = audnzd_at["close"].tail(self.audnzd_lookback)
        mean_val = close_series.mean()
        std_val = close_series.std()

        if std_val == 0 or np.isnan(std_val):
            return False

        current = close_series.iloc[-1]
        z_score = abs(current - mean_val) / std_val

        return z_score > self.audnzd_std_threshold

    def _get_carry_direction(self) -> str:
        """
        Determine carry direction based on rate differential.

        Returns:
            "long" if carry is positive (RBNZ > Fed) — go long NZD/USD
            "short" if carry is negative (RBNZ < Fed) — go short NZD/USD
            "neutral" if carry is too small
        """
        if abs(self.carry_bps) < self.min_carry_bps:
            return "neutral"
        return "long" if self.carry_bps > 0 else "short"

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate carry + momentum signals for NZD/USD.

        Args:
            data: {ticker: DataFrame} with OHLCV columns
            date: current trading date

        Returns:
            list[Signal] — at most max_trades_per_day signals
        """
        if TICKER not in data:
            return []

        df = data[TICKER]
        audnzd_df = data.get(AUDNZD_TICKER)

        # Need enough bars for momentum + ATR warmup
        min_bars = max(self.momentum_period, self.atr_period, self.audnzd_lookback) + 5
        if len(df) < min_bars:
            return []

        # ── Carry direction check ──
        carry_direction = self._get_carry_direction()
        if carry_direction == "neutral":
            return []  # Carry too small to justify a trade

        df = df.copy()

        # ── Compute indicators ──
        df["atr"] = self._compute_atr(df, self.atr_period)
        df["momentum"] = df["close"] - df["close"].shift(self.momentum_period)

        signals = []

        for ts, bar in df.iterrows():
            if len(signals) >= self.max_trades_per_day:
                break

            # Skip NaN rows
            if pd.isna(bar["atr"]) or pd.isna(bar["momentum"]):
                continue

            # ── Rollover window filter ──
            if self._is_rollover_window(ts):
                continue

            # ── AUD/NZD dislocation filter ──
            if self._is_audnzd_dislocated(audnzd_df, ts):
                continue

            atr_val = bar["atr"]
            if atr_val <= 0:
                continue

            entry_price = bar["close"]
            momentum = bar["momentum"]

            # ── LONG: carry positive + momentum positive ──
            if carry_direction == "long" and momentum > 0:
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
                        "carry_bps": self.carry_bps,
                        "carry_direction": carry_direction,
                        "momentum_20d": round(momentum, 6),
                        "atr": round(atr_val, 6),
                        "cost_rt_pct": FX_COST_RT_PCT,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 1),
                    },
                ))

            # ── SHORT: carry negative + momentum negative ──
            elif carry_direction == "short" and momentum < 0:
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
                        "carry_bps": self.carry_bps,
                        "carry_direction": carry_direction,
                        "momentum_20d": round(momentum, 6),
                        "atr": round(atr_val, 6),
                        "cost_rt_pct": FX_COST_RT_PCT,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 1),
                    },
                ))

        return signals[:self.max_trades_per_day]
