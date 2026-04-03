"""
FX-003 : USD/CHF Mean Reversion

Edge :
SNB intervention history and safe-haven flow reversion create
predictable mean-reverting dynamics in USD/CHF. When the pair
deviates significantly from its 20-day mean (> 2 ATR), it tends
to revert — especially in low-vol environments where extreme
risk-off flows are absent.

Signal:
  - Price deviation > 2 ATR(14) from 20-day SMA
  - LONG: Price < SMA20 - 2*ATR (oversold deviation)
  - SHORT: Price > SMA20 + 2*ATR (overbought deviation)

Risk:
  - Stop: 1 ATR(14) beyond entry (away from mean)
  - Take profit: Return to 20-day SMA
  - Holding: 5-15 days
  - Costs: ~0.01% round-trip (FX IBKR)

Filters:
  - No trade if VIX > 30 (extreme risk-off = no mean reversion)
  - No trade during SNB announcement days
  - No trade during FX rollover window (22:00-23:00 UTC)

Walk-forward expectations:
  - Target Sharpe OOS: 0.8-2.0
  - Min 50% OOS windows profitable
  - Estimated 1-3 trades/month (swing timeframe)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# ─── Constants ────────────────────────────────────────────────────────────────

TICKER = "USDCHF"
VIX_TICKER = "VIX"  # Or ^VIX — depends on data source

# SNB announcement dates (2026 calendar — extendable)
SNB_DATES = {
    "2026-03-19", "2026-06-18", "2026-09-17", "2026-12-10",
}

# FX rollover window (22:00-23:00 UTC)
FX_ROLLOVER_START = dt_time(22, 0)
FX_ROLLOVER_END = dt_time(23, 0)

# VIX threshold for risk-off filter
VIX_MAX = 30.0

# FX cost (round-trip)
FX_COST_RT_PCT = 0.0001  # 0.01%


class FXUSDCHFMeanReversionStrategy(BaseStrategy):
    """USD/CHF Mean Reversion — deviation from 20-day mean + VIX filter."""

    name = "FX-003 USD/CHF MR"

    def __init__(
        self,
        sma_period: int = 20,
        atr_period: int = 14,
        deviation_atr_mult: float = 2.0,
        stop_atr_mult: float = 1.0,
        vix_max: float = VIX_MAX,
        max_trades_per_day: int = 1,
    ):
        self.sma_period = sma_period
        self.atr_period = atr_period
        self.deviation_atr_mult = deviation_atr_mult
        self.stop_atr_mult = stop_atr_mult
        self.vix_max = vix_max
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return [TICKER, VIX_TICKER]

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
    def _is_snb_blackout(date) -> bool:
        """Check if date is an SNB announcement day."""
        date_str = str(date)[:10]
        return date_str in SNB_DATES

    @staticmethod
    def _get_vix_level(vix_data: pd.DataFrame, timestamp: pd.Timestamp) -> float:
        """Get VIX level at or before the given timestamp."""
        if vix_data is None or vix_data.empty:
            return 0.0  # No VIX data = assume low vol (allow trading)
        vix_at = vix_data[vix_data.index <= timestamp]
        if vix_at.empty:
            return 0.0
        return float(vix_at.iloc[-1]["close"])

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate mean-reversion signals for USD/CHF.

        Args:
            data: {ticker: DataFrame} with OHLCV columns
            date: current trading date

        Returns:
            list[Signal] — at most max_trades_per_day signals
        """
        if TICKER not in data:
            return []

        df = data[TICKER]
        vix_df = data.get(VIX_TICKER)

        # Need enough bars for SMA + ATR warmup
        min_bars = max(self.sma_period, self.atr_period) + 5
        if len(df) < min_bars:
            return []

        # ── SNB blackout filter ──
        if self._is_snb_blackout(date):
            return []

        df = df.copy()

        # ── Compute indicators ──
        df["sma"] = df["close"].rolling(self.sma_period).mean()
        df["atr"] = self._compute_atr(df, self.atr_period)
        df["deviation"] = df["close"] - df["sma"]

        signals = []

        for ts, bar in df.iterrows():
            if len(signals) >= self.max_trades_per_day:
                break

            # Skip NaN rows
            if pd.isna(bar["sma"]) or pd.isna(bar["atr"]):
                continue

            # ── Rollover window filter ──
            if self._is_rollover_window(ts):
                continue

            # ── VIX filter: no trade in extreme risk-off ──
            if vix_df is not None and not vix_df.empty:
                vix_level = self._get_vix_level(vix_df, ts)
                if vix_level > self.vix_max:
                    continue

            atr_val = bar["atr"]
            if atr_val <= 0:
                continue

            entry_price = bar["close"]
            sma_val = bar["sma"]
            deviation = bar["deviation"]
            deviation_threshold = self.deviation_atr_mult * atr_val

            # ── LONG signal: price below SMA - 2*ATR (oversold) ──
            if deviation < -deviation_threshold:
                stop_loss = entry_price - self.stop_atr_mult * atr_val
                take_profit = sma_val  # Target = return to mean

                signals.append(Signal(
                    action="LONG",
                    ticker=TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "deviation_atr": round(deviation / atr_val, 2),
                        "sma20": round(sma_val, 6),
                        "atr": round(atr_val, 6),
                        "vix": round(self._get_vix_level(vix_df, ts), 1) if vix_df is not None else None,
                        "cost_rt_pct": FX_COST_RT_PCT,
                    },
                ))

            # ── SHORT signal: price above SMA + 2*ATR (overbought) ──
            elif deviation > deviation_threshold:
                stop_loss = entry_price + self.stop_atr_mult * atr_val
                take_profit = sma_val  # Target = return to mean

                signals.append(Signal(
                    action="SHORT",
                    ticker=TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "deviation_atr": round(deviation / atr_val, 2),
                        "sma20": round(sma_val, 6),
                        "atr": round(atr_val, 6),
                        "vix": round(self._get_vix_level(vix_df, ts), 1) if vix_df is not None else None,
                        "cost_rt_pct": FX_COST_RT_PCT,
                    },
                ))

        return signals[:self.max_trades_per_day]
