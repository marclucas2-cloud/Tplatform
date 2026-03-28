"""FX-010 Session Overlap Momentum strategy for BacktesterV2.

The London-New York session overlap (14:00-17:00 CET) concentrates ~40% of
daily FX volume. If a trend is established in the London morning session
(08:00-13:00 CET), the overlap amplifies it because US institutional flow
reinforces London's direction.

Entry: at 14:00 CET (overlap start), if morning trend + EMA alignment.
Exit: time-based (17:00 CET end of overlap) or SL/TP hit.

Pairs: EUR/USD, GBP/USD, USD/JPY (3 most liquid during overlap).
Cost: ~$2/trade IBKR, spread ~0.8-2.0 bps.
Expected: ~10-15 trades/month across 3 pairs, Sharpe target 1.0-1.8.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

_CET = ZoneInfo("Europe/Paris")

# London morning session: 08:00-13:00 CET
_MORNING_START = time(8, 0)
_MORNING_END = time(13, 0)

# Overlap entry window: 14:00-14:15 CET (small window around overlap start)
_OVERLAP_ENTRY_START = time(14, 0)
_OVERLAP_ENTRY_END = time(14, 15)

# Overlap session end: 17:00 CET (time exit)
_OVERLAP_END = time(17, 0)


class FXSessionOverlap(StrategyBase):
    """Session overlap momentum: ride the London trend into NY overlap."""

    SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")

    def __init__(self) -> None:
        self.atr_period: int = 14
        self.ema_fast: int = 8
        self.ema_slow: int = 21
        self.min_move_atr: float = 0.5  # Morning move must be > 0.5 ATR
        self.sl_atr_buffer: float = 0.3  # SL buffer beyond morning extreme
        self.tp_multiplier: float = 1.5  # TP = 1.5x morning move from entry
        self.vix_max: float = 30.0  # Skip if VIX > 30
        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "fx_session_overlap"

    @property
    def asset_class(self) -> str:
        return "fx"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def _bar_cet_time(self, bar: Bar) -> time:
        """Convert bar timestamp to CET/CEST time component."""
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        cet_ts = ts.astimezone(_CET)
        return cet_ts.time()

    def _is_overlap_entry_window(self, bar: Bar) -> bool:
        """Check if bar falls within 14:00-14:15 CET overlap entry window."""
        t = self._bar_cet_time(bar)
        return _OVERLAP_ENTRY_START <= t <= _OVERLAP_ENTRY_END

    def _get_morning_data(self, symbol: str) -> Optional[Dict[str, float]]:
        """Compute morning session metrics: move, low, high (08:00-13:00 CET).

        Returns:
            Dict with 'move', 'low', 'high', 'close_08', 'close_13'
            or None if insufficient data.
        """
        if self.data_feed is None:
            return None

        bars_df = self.data_feed.get_bars(symbol, 50)
        if len(bars_df) < 5:
            return None

        # Filter bars in the morning session (08:00-13:00 CET)
        morning_bars = []
        for idx_ts in bars_df.index:
            ts = idx_ts
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            cet_ts = ts.astimezone(_CET)
            t = cet_ts.time()
            if _MORNING_START <= t <= _MORNING_END:
                morning_bars.append({
                    "time": t,
                    "close": float(bars_df.loc[idx_ts, "close"]),
                    "high": float(bars_df.loc[idx_ts, "high"]),
                    "low": float(bars_df.loc[idx_ts, "low"]),
                })

        if len(morning_bars) < 2:
            return None

        close_08 = morning_bars[0]["close"]
        close_13 = morning_bars[-1]["close"]
        morning_move = close_13 - close_08
        morning_low = min(b["low"] for b in morning_bars)
        morning_high = max(b["high"] for b in morning_bars)

        return {
            "move": morning_move,
            "low": morning_low,
            "high": morning_high,
            "close_08": close_08,
            "close_13": close_13,
        }

    def _check_vix_filter(self) -> bool:
        """Return False if VIX is above threshold (should skip trade)."""
        if self.data_feed is None:
            return True

        try:
            bars_df = self.data_feed.get_bars("VIX", 5)
            if len(bars_df) > 0:
                vix_close = float(bars_df.iloc[-1]["close"])
                if vix_close > self.vix_max:
                    return False
        except (KeyError, Exception):
            pass  # VIX data not available — allow trade

        return True

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None

        # Only trigger during overlap entry window (14:00-14:15 CET)
        if not self._is_overlap_entry_window(bar):
            return None

        # VIX filter
        if not self._check_vix_filter():
            return None

        # Try each symbol
        for sym in self.SYMBOLS:
            signal = self._evaluate_symbol(sym, bar)
            if signal is not None:
                return signal

        return None

    def _evaluate_symbol(self, sym: str, bar: Bar) -> Optional[Signal]:
        """Evaluate overlap momentum signal for a single symbol."""
        if self.data_feed is None:
            return None

        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        ema_f = self.data_feed.get_indicator(sym, "ema", self.ema_fast)
        ema_s = self.data_feed.get_indicator(sym, "ema", self.ema_slow)

        if any(v is None for v in (atr, ema_f, ema_s)):
            return None
        if atr <= 0:
            return None

        morning = self._get_morning_data(sym)
        if morning is None:
            return None

        morning_move = morning["move"]
        move_in_atr = abs(morning_move) / atr

        # Filter: morning move must be meaningful
        if move_in_atr < self.min_move_atr:
            return None

        tp_distance = self.tp_multiplier * abs(morning_move)
        sl_buffer = self.sl_atr_buffer * atr

        # LONG: positive morning move + EMA8 > EMA21
        if morning_move > 0 and ema_f > ema_s:
            sl = morning["low"] - sl_buffer  # Below morning low
            tp = bar.close + tp_distance
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=min(move_in_atr / 2.0, 1.0),
            )

        # SHORT: negative morning move + EMA8 < EMA21
        if morning_move < 0 and ema_f < ema_s:
            sl = morning["high"] + sl_buffer  # Above morning high
            tp = bar.close - tp_distance
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=min(move_in_atr / 2.0, 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "atr_period": self.atr_period,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "min_move_atr": self.min_move_atr,
            "sl_atr_buffer": self.sl_atr_buffer,
            "tp_multiplier": self.tp_multiplier,
            "vix_max": self.vix_max,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "atr_period": [10, 14, 20],
            "ema_fast": [5, 8, 13],
            "ema_slow": [15, 21, 30],
            "min_move_atr": [0.3, 0.5, 0.7],
            "sl_atr_buffer": [0.2, 0.3, 0.5],
            "tp_multiplier": [1.0, 1.5, 2.0],
            "vix_max": [25.0, 30.0, 35.0],
        }
