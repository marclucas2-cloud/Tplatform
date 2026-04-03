"""FX-008 Bollinger Squeeze Breakout strategy for BacktesterV2.

Hypothesis: Periods of low FX volatility (BB width < 10th percentile over
100 bars) precede directional breakouts because volatility clusters
(Mandelbrot). The direction is determined by the breakout direction.

Entry (Squeeze Detection + Breakout):
  1. BB(20, 2.0) standard Bollinger Bands
  2. BB_width = (upper - lower) / middle
  3. BB_width_pctile = rank of current BB_width vs last 100 bars
  4. SQUEEZE when BB_width_pctile < squeeze_pctile (10th percentile)
  5. After squeeze:
     - LONG: close > upper BB AND volume > volume_filter * avg_volume(20)
     - SHORT: close < lower BB AND volume > volume_filter * avg_volume(20)
  6. Confirmation: next bar must close in breakout direction
  7. ADX(14) < 20 at squeeze (confirms compression)

Exit:
  - Stop loss: middle BB at entry time
  - Take profit: 2x BB width from entry
  - Trailing stop: once profit > 1.5x BB width, trail at 1x BB width
  - Max holding: 5 days (20 bars of 4H)

Pairs: EUR/USD, GBP/USD, USD/JPY (3 most liquid)
Timeframe: 4H bars
Expected: ~8-12 trades/month across 3 pairs, Sharpe target 1.2-2.0
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

# Supported pairs
SUPPORTED_PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]

# Max holding period in bars (5 days * 6 bars/day for 4H)
_MAX_HOLDING_BARS = 30


class FXBollingerSqueeze(StrategyBase):
    """FX-008 Bollinger Squeeze Breakout — volatility compression then breakout."""

    def __init__(self, symbol: str = "EURUSD") -> None:
        if symbol not in SUPPORTED_PAIRS:
            raise ValueError(
                f"Unsupported pair {symbol}. Must be one of {SUPPORTED_PAIRS}"
            )
        self._symbol = symbol

        # Bollinger Band parameters
        self.bb_period: int = 20
        self.bb_std: float = 2.0

        # Squeeze detection
        self.squeeze_lookback: int = 100
        self.squeeze_pctile: float = 10.0  # percentile threshold

        # ADX filter at squeeze
        self.adx_period: int = 14
        self.adx_max_at_squeeze: float = 20.0

        # Volume confirmation
        self.volume_avg_period: int = 20
        self.volume_filter: float = 1.2  # volume must be > 1.2x average

        # Risk management
        self.tp_bb_width_mult: float = 2.0  # TP = 2x BB width from entry
        self.trailing_activate_mult: float = 1.5  # activate trailing at 1.5x BB width
        self.trailing_distance_mult: float = 1.0  # trail at 1x BB width

        # State
        self._squeeze_detected: bool = False
        self._squeeze_direction: str | None = None  # pending breakout direction
        self._awaiting_confirmation: bool = False
        self._pending_side: str | None = None
        self._pending_entry_price: float | None = None
        self._pending_middle_bb: float | None = None
        self._pending_bb_width: float | None = None
        self._bars_since_squeeze: int = 0

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"fx_bbsqueeze_{self._symbol.lower()}"

    @property
    def asset_class(self) -> str:
        return "fx"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        sym = self._symbol

        # --- Get bars for BB calculation ---
        bars_df = self.data_feed.get_bars(sym, self.squeeze_lookback + self.bb_period)
        if bars_df is None or len(bars_df) < self.bb_period + 10:
            return None

        close = bars_df["close"]
        volume = bars_df["volume"]

        # --- Calculate Bollinger Bands ---
        sma = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        upper_bb = sma + self.bb_std * std
        lower_bb = sma - self.bb_std * std
        middle_bb = sma

        if sma.iloc[-1] is None or np.isnan(sma.iloc[-1]):
            return None

        current_upper = float(upper_bb.iloc[-1])
        current_lower = float(lower_bb.iloc[-1])
        current_middle = float(middle_bb.iloc[-1])

        if current_middle <= 0:
            return None

        # --- Calculate BB width and percentile ---
        bb_width = (upper_bb - lower_bb) / middle_bb
        bb_width_clean = bb_width.dropna()
        if len(bb_width_clean) < 20:
            return None

        # Rank current width in recent history
        lookback = min(self.squeeze_lookback, len(bb_width_clean))
        recent_widths = bb_width_clean.iloc[-lookback:]
        current_width = float(bb_width_clean.iloc[-1])

        # Percentile rank: % of historical values that are <= current
        pctile_rank = float(
            (recent_widths <= current_width).sum() / len(recent_widths) * 100
        )

        # --- Volume check ---
        avg_vol = float(volume.iloc[-self.volume_avg_period:].mean())
        current_vol = float(volume.iloc[-1])
        volume_ok = avg_vol > 0 and current_vol > self.volume_filter * avg_vol

        # --- ADX check ---
        adx = self.data_feed.get_indicator(sym, "adx", self.adx_period)

        # --- Step 1: Confirmation bar (if we're awaiting one) ---
        if self._awaiting_confirmation:
            self._awaiting_confirmation = False
            confirmed = False

            if (self._pending_side == "BUY" and bar.close > self._pending_entry_price) or (self._pending_side == "SELL" and bar.close < self._pending_entry_price):
                confirmed = True

            if confirmed and self._pending_middle_bb is not None:
                side = self._pending_side
                entry = bar.close
                middle = self._pending_middle_bb
                bb_w = self._pending_bb_width or current_width

                if side == "BUY":
                    sl = middle
                    tp = entry + self.tp_bb_width_mult * bb_w * entry
                else:
                    sl = middle
                    tp = entry - self.tp_bb_width_mult * bb_w * entry

                self._reset_state()
                return Signal(
                    symbol=sym,
                    side=side,
                    strategy_name=self.name,
                    stop_loss=sl,
                    take_profit=tp,
                    strength=min((100 - pctile_rank) / 100.0, 1.0),
                )

            # Confirmation failed — reset
            self._reset_state()
            return None

        # --- Step 2: Detect squeeze ---
        is_squeeze = pctile_rank < self.squeeze_pctile
        adx_ok = adx is not None and adx < self.adx_max_at_squeeze

        if is_squeeze and adx_ok:
            self._squeeze_detected = True
            self._bars_since_squeeze = 0

        # Decay squeeze after too many bars
        if self._squeeze_detected:
            self._bars_since_squeeze += 1
            if self._bars_since_squeeze > 10:
                self._squeeze_detected = False

        # --- Step 3: Breakout after squeeze ---
        if self._squeeze_detected and volume_ok:
            # LONG breakout: close > upper BB
            if bar.close > current_upper:
                self._awaiting_confirmation = True
                self._pending_side = "BUY"
                self._pending_entry_price = bar.close
                self._pending_middle_bb = current_middle
                self._pending_bb_width = current_width
                self._squeeze_detected = False
                return None  # wait for confirmation bar

            # SHORT breakout: close < lower BB
            if bar.close < current_lower:
                self._awaiting_confirmation = True
                self._pending_side = "SELL"
                self._pending_entry_price = bar.close
                self._pending_middle_bb = current_middle
                self._pending_bb_width = current_width
                self._squeeze_detected = False
                return None  # wait for confirmation bar

        return None

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _reset_state(self) -> None:
        """Reset breakout detection state."""
        self._squeeze_detected = False
        self._awaiting_confirmation = False
        self._pending_side = None
        self._pending_entry_price = None
        self._pending_middle_bb = None
        self._pending_bb_width = None
        self._bars_since_squeeze = 0

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self._symbol,
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "squeeze_lookback": self.squeeze_lookback,
            "squeeze_pctile": self.squeeze_pctile,
            "adx_period": self.adx_period,
            "adx_max_at_squeeze": self.adx_max_at_squeeze,
            "volume_avg_period": self.volume_avg_period,
            "volume_filter": self.volume_filter,
            "tp_bb_width_mult": self.tp_bb_width_mult,
            "trailing_activate_mult": self.trailing_activate_mult,
            "trailing_distance_mult": self.trailing_distance_mult,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "squeeze_pctile": [5.0, 10.0, 15.0],
            "volume_filter": [1.0, 1.2, 1.5],
            "adx_max_at_squeeze": [15.0, 20.0, 25.0],
            "tp_bb_width_mult": [1.5, 2.0, 2.5],
            "trailing_activate_mult": [1.0, 1.5, 2.0],
            "trailing_distance_mult": [0.5, 1.0, 1.5],
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_eod(self, timestamp: pd.Timestamp) -> None:
        """Keep squeeze state across days (multi-day holding allowed)."""
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def detect_squeeze(self, bars_df: pd.DataFrame) -> dict:
        """Public helper for testing: detect squeeze from a DataFrame.

        Returns dict with squeeze info for testability.
        """
        close = bars_df["close"]
        sma = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        upper_bb = sma + self.bb_std * std
        lower_bb = sma - self.bb_std * std
        middle_bb = sma

        bb_width = (upper_bb - lower_bb) / middle_bb
        bb_width_clean = bb_width.dropna()

        if len(bb_width_clean) < 20:
            return {"squeeze": False, "pctile": 100.0, "bb_width": 0.0}

        lookback = min(self.squeeze_lookback, len(bb_width_clean))
        recent_widths = bb_width_clean.iloc[-lookback:]
        current_width = float(bb_width_clean.iloc[-1])
        pctile_rank = float(
            (recent_widths <= current_width).sum() / len(recent_widths) * 100
        )

        return {
            "squeeze": pctile_rank < self.squeeze_pctile,
            "pctile": pctile_rank,
            "bb_width": current_width,
            "upper": float(upper_bb.iloc[-1]),
            "lower": float(lower_bb.iloc[-1]),
            "middle": float(middle_bb.iloc[-1]),
        }
