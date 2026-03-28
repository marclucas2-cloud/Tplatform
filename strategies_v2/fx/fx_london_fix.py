"""FX-009 London Fix Flow strategy for BacktesterV2.

The WM/Reuters fix at 16:00 CET generates predictable flow patterns because
corporate treasuries and pension funds execute large hedging orders at this
benchmark. Price overshoots during the fix window (15:55-16:05) and reverts
in the 15-30 minutes after.

Entry: fade the pre-fix move at 16:05-16:15 CET if the move into the fix
was strong (0.5-2.0x ATR_1H). Exit: time-based (30 min) or SL/TP hit.

Pairs: EUR/USD, GBP/USD (highest fix volume pairs).
Cost: ~$2/trade IBKR, spread ~0.8-2.0 bps.
Expected: ~20 trades/month, Sharpe target 1.0-1.8.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

_CET = ZoneInfo("Europe/Paris")

# Fix window in CET: entry allowed between 16:00 and 16:15
_FIX_ENTRY_START = time(16, 0)
_FIX_ENTRY_END = time(16, 15)

# Pre-fix observation window: 15:45 to 16:05 CET
_PRE_FIX_START = time(15, 45)
_PRE_FIX_END = time(16, 5)

# Time exit: 30 minutes after entry (16:35 CET)
_TIME_EXIT = time(16, 35)

# Max holding: 45 minutes
_MAX_HOLD_MINUTES = 45


class FXLondonFix(StrategyBase):
    """London Fix reversion: fade the pre-fix flow overshoot after 16:00 CET."""

    SYMBOLS = ("EURUSD", "GBPUSD")

    def __init__(self) -> None:
        self.atr_period: int = 14
        self.min_move_atr: float = 0.5  # Minimum pre-fix move in ATR units
        self.max_move_atr: float = 2.0  # Maximum pre-fix move (too extreme = skip)
        self.sl_atr: float = 1.0  # Stop loss in ATR units
        self.tp_ratio: float = 0.7  # TP = 70% of pre-fix move (partial reversion)
        self.time_exit_minutes: int = 30  # Close after N minutes
        self.max_hold_minutes: int = 45  # Absolute max holding
        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "fx_london_fix"

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

    def _is_fix_entry_window(self, bar: Bar) -> bool:
        """Check if bar falls within 16:00-16:15 CET entry window."""
        t = self._bar_cet_time(bar)
        return _FIX_ENTRY_START <= t <= _FIX_ENTRY_END

    def _get_pre_fix_move(self, symbol: str) -> Optional[float]:
        """Compute the pre-fix move: close at ~16:05 minus close at ~15:45 CET.

        Looks at the last ~8 bars (5M) or ~2 bars (15M) to find the price
        change over the 20-minute window leading into the fix.

        Returns:
            The signed price change, or None if insufficient data.
        """
        if self.data_feed is None:
            return None

        # Get recent bars to find the pre-fix and fix bars
        bars_df = self.data_feed.get_bars(symbol, 20)
        if len(bars_df) < 4:
            return None

        # Find bars in the pre-fix window (15:45 to 16:05 CET)
        pre_fix_bars = []
        for idx_ts in bars_df.index:
            ts = idx_ts
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            cet_ts = ts.astimezone(_CET)
            t = cet_ts.time()
            if _PRE_FIX_START <= t <= _PRE_FIX_END:
                pre_fix_bars.append((t, float(bars_df.loc[idx_ts, "close"])))

        if len(pre_fix_bars) < 2:
            return None

        # Pre-fix move = last pre-fix close minus first pre-fix close
        return pre_fix_bars[-1][1] - pre_fix_bars[0][1]

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None

        # Only trigger during fix entry window (16:00-16:15 CET)
        if not self._is_fix_entry_window(bar):
            return None

        # Try each symbol
        for sym in self.SYMBOLS:
            signal = self._evaluate_symbol(sym, bar)
            if signal is not None:
                return signal

        return None

    def _evaluate_symbol(self, sym: str, bar: Bar) -> Optional[Signal]:
        """Evaluate fix reversion signal for a single symbol."""
        if self.data_feed is None:
            return None

        atr = self.data_feed.get_indicator(sym, "atr", self.atr_period)
        if atr is None or atr <= 0:
            return None

        pre_fix_move = self._get_pre_fix_move(sym)
        if pre_fix_move is None:
            return None

        move_in_atr = abs(pre_fix_move) / atr

        # Filter: move must be strong enough but not extreme
        if move_in_atr < self.min_move_atr or move_in_atr > self.max_move_atr:
            return None

        # Determine reversion direction
        # Pre-fix move UP -> expect reversion DOWN -> SHORT
        # Pre-fix move DOWN -> expect reversion UP -> LONG
        tp_distance = self.tp_ratio * abs(pre_fix_move)
        sl_distance = self.sl_atr * atr

        if pre_fix_move > 0:
            # Pre-fix was up -> fade with SHORT
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=bar.close + sl_distance,
                take_profit=bar.close - tp_distance,
                strength=min(move_in_atr / self.max_move_atr, 1.0),
            )
        else:
            # Pre-fix was down -> fade with LONG
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close - sl_distance,
                take_profit=bar.close + tp_distance,
                strength=min(move_in_atr / self.max_move_atr, 1.0),
            )

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "atr_period": self.atr_period,
            "min_move_atr": self.min_move_atr,
            "max_move_atr": self.max_move_atr,
            "sl_atr": self.sl_atr,
            "tp_ratio": self.tp_ratio,
            "time_exit_minutes": self.time_exit_minutes,
            "max_hold_minutes": self.max_hold_minutes,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "atr_period": [10, 14, 20],
            "min_move_atr": [0.3, 0.5, 0.7],
            "max_move_atr": [1.5, 2.0, 2.5],
            "sl_atr": [0.7, 1.0, 1.3],
            "tp_ratio": [0.5, 0.7, 0.9],
            "time_exit_minutes": [20, 30, 40],
            "max_hold_minutes": [30, 45, 60],
        }
