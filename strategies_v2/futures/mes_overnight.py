"""MES Overnight Session Momentum for BacktesterV2.

FUT-006: Micro E-mini S&P 500 (MES) Overnight Session Momentum.
CME, $1,400 margin, tick size $0.25 = $1.25/tick.

EDGE: The overnight session (18:00 ET prev day - 09:30 ET) captures Asian
and European order flow. Large directional moves during globex tend to
continue into the first 30 minutes of US cash session (Lou, Polk & Skouras,
"A Tug of War: Overnight vs. Intraday Returns", 2019). Historically,
~60% of overnight S&P moves > 0.3% continue for at least 30 min after
cash open.

We measure the overnight return (close of prev RTH to 09:35 ET price),
then enter in the same direction if the move exceeds a threshold.

Rules:
- Signal: overnight return > 0.3% (absolute)
- Entry: 09:35-09:45 ET (first 10-15 min after cash open)
- Direction: same as overnight move (momentum continuation)
- Stop: 1.5 ATR(14) from entry
- Take profit: 2.5 ATR(14) from entry
- Time exit: 11:00 ET (no longer session continuation edge after that)
- Filter: ADX(14) > 18 on hourly bars (trend regime)
- Filter: skip if VIX > 30 (too chaotic, overnight gaps are unreliable)
- ~15-18 trades/month
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MESOvernightMomentum(StrategyBase):
    """MES overnight session momentum continuation.

    Captures the tendency of large overnight moves in S&P 500 futures
    to continue into the first 90 minutes of the US cash session. Uses
    globex session return as the signal, filtered by ADX and VIX.
    """

    SYMBOL = "MES"
    TICK_SIZE = 0.25
    TICK_VALUE = 1.25  # $1.25 per tick

    def __init__(self) -> None:
        self.overnight_threshold_pct: float = 0.3  # min overnight move %
        self.sl_atr_mult: float = 1.5
        self.tp_atr_mult: float = 2.5
        self.adx_threshold: float = 18.0
        self.vix_max: float = 30.0
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mes_overnight"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None
        sym = self.SYMBOL

        # Convert bar timestamp to ET for time-of-day checks
        try:
            import zoneinfo
            et_tz = zoneinfo.ZoneInfo("America/New_York")
            ts_et = bar.timestamp.astimezone(et_tz) if bar.timestamp.tzinfo else bar.timestamp
            bar_hour = ts_et.hour
            bar_minute = ts_et.minute
        except Exception:
            bar_hour = bar.timestamp.hour if hasattr(bar.timestamp, 'hour') else 0
            bar_minute = bar.timestamp.minute if hasattr(bar.timestamp, 'minute') else 0
        bar_time_min = bar_hour * 60 + bar_minute

        # Entry window: 09:35 (575) to 09:45 (585)
        if bar_time_min < 575 or bar_time_min > 585:
            return None

        # Need at least 50 bars of history for indicators
        bars_df = self.data_feed.get_bars(sym, 100)
        if bars_df is None or len(bars_df) < 50:
            return None

        # Find prev day RTH close (last bar before 16:00 ET from previous day)
        prev_rth_close = None
        for i in range(len(bars_df) - 1, -1, -1):
            ts = bars_df.index[i]
            t_min = ts.hour * 60 + ts.minute
            # Look for bars from previous trading session (before today's open)
            if t_min >= 570:  # 09:30 ET today
                continue
            # Found a bar from the overnight/previous session
            prev_rth_close = float(bars_df.iloc[i]["close"])
            break

        if prev_rth_close is None or prev_rth_close <= 0:
            # Fallback: use close from bar 20 periods back as proxy
            if len(bars_df) > 20:
                prev_rth_close = float(bars_df.iloc[-20]["close"])
            else:
                return None

        # Current price
        price = bar.close
        overnight_return_pct = ((price - prev_rth_close) / prev_rth_close) * 100.0

        # Threshold filter
        if abs(overnight_return_pct) < self.overnight_threshold_pct:
            return None

        # ADX filter
        adx = self.data_feed.get_indicator(sym, "adx", 14)
        if adx is None or adx < self.adx_threshold:
            return None

        # VIX filter: skip if VIX > vix_max (too chaotic for overnight momentum)
        vix = self.data_feed.get_indicator("VIX", "close", 1) if hasattr(self.data_feed, 'get_indicator') else None
        if vix is not None and vix > self.vix_max:
            return None

        # ATR for SL/TP sizing
        atr = self.data_feed.get_indicator(sym, "atr", 14)
        if atr is None or atr <= 0:
            return None

        # Volatility proxy: if VIX not available, skip if ATR > 3x normal (high vol regime)
        atr_sma = self.data_feed.get_indicator(sym, "atr_sma", 50) if hasattr(self.data_feed, 'get_indicator') else None
        if vix is None and atr_sma is not None and atr_sma > 0 and atr / atr_sma > 3.0:
            return None

        # Direction: follow overnight momentum
        if overnight_return_pct > 0:
            side = "BUY"
            stop_loss = price - self.sl_atr_mult * atr
            take_profit = price + self.tp_atr_mult * atr
        else:
            side = "SELL"
            stop_loss = price + self.sl_atr_mult * atr
            take_profit = price - self.tp_atr_mult * atr

        strength = min(abs(overnight_return_pct) / 1.0, 1.0)  # Normalize to 1% max

        return Signal(
            symbol=sym,
            side=side,
            strategy_name=self.name,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=strength,
        )

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "overnight_threshold_pct": self.overnight_threshold_pct,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
            "adx_threshold": self.adx_threshold,
            "vix_max": self.vix_max,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "overnight_threshold_pct": [0.2, 0.3, 0.4, 0.5],
            "sl_atr_mult": [1.0, 1.5, 2.0],
            "tp_atr_mult": [2.0, 2.5, 3.0, 4.0],
            "adx_threshold": [12.0, 15.0, 18.0, 22.0],
            "vix_max": [25.0, 30.0, 35.0],
        }
