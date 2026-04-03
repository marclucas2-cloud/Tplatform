"""M2K Russell 2000 Opening Range Breakout for BacktesterV2.

FUT-005: Micro Russell 2000 (M2K) Opening Range Breakout.
CME, $500 margin, tick size $0.10 = $0.50/tick.

EDGE: Small-cap indices have wider opening ranges due to lower institutional
liquidity in the first 30 minutes. The breakout of the 30-minute opening range
predicts intraday direction with ~55-60% accuracy (Crabel, "Day Trading with
Short-Term Price Patterns", 1990). We trade M2K because it amplifies the effect
vs large-cap MES, and micro contract keeps margin low ($500).

Rules:
- Opening range: first 30 minutes (09:30-10:00 ET) high/low
- Long: price breaks above OR high + buffer (0.3 ATR)
- Short: price breaks below OR low - buffer (0.3 ATR)
- Stop: opposite side of opening range
- Take profit: 2x opening range width from entry
- Time exit: 15:45 ET (close before MOC volatility)
- Filter: ADX(14) > 15 (need SOME trend, not dead market)
- Filter: opening range width > 0.3% and < 2% (too narrow = chop, too wide = exhaustion)
- ~12-15 trades/month
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class M2KORB(StrategyBase):
    """M2K Russell 2000 Opening Range Breakout.

    Captures small-cap opening range breakouts. The wider intraday
    ranges of Russell 2000 vs S&P 500 make ORB more reliable on M2K.
    """

    SYMBOL = "M2K"
    TICK_SIZE = 0.10
    TICK_VALUE = 0.50  # $0.50 per tick

    def __init__(self) -> None:
        self.or_minutes: int = 30          # opening range period in minutes
        self.buffer_atr_mult: float = 0.3  # buffer above/below OR for entry
        self.min_range_pct: float = 0.3    # minimum OR width as % of price
        self.max_range_pct: float = 2.0    # maximum OR width as % of price
        self.adx_threshold: float = 15.0   # minimum ADX for trend presence
        self.tp_range_mult: float = 2.0    # TP = 2x OR width from entry
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "m2k_orb"

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

        # Only act after the opening range is defined (after 10:00 ET)
        bar_hour = bar.timestamp.hour if hasattr(bar.timestamp, 'hour') else 0
        bar_minute = bar.timestamp.minute if hasattr(bar.timestamp, 'minute') else 0
        bar_time_min = bar_hour * 60 + bar_minute

        # Need bars after 10:00 ET but before 15:45 ET
        if bar_time_min < 600 or bar_time_min > 945:  # 10:00=600, 15:45=945
            return None

        # Get enough bars to compute OR and indicators
        bars_df = self.data_feed.get_bars(sym, 100)
        if bars_df is None or len(bars_df) < 30:
            return None

        # Compute opening range from bars between 09:30-10:00
        or_mask = []
        for ts in bars_df.index:
            t_min = ts.hour * 60 + ts.minute
            if 570 <= t_min < 600:  # 09:30=570, 10:00=600
                or_mask.append(True)
            else:
                or_mask.append(False)

        or_bars = bars_df.loc[or_mask]
        if len(or_bars) < 2:
            return None

        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())
        or_width = or_high - or_low

        if or_width <= 0:
            return None

        # Filter: OR width as percentage of mid price
        or_mid = (or_high + or_low) / 2.0
        or_width_pct = (or_width / or_mid) * 100.0
        if or_width_pct < self.min_range_pct or or_width_pct > self.max_range_pct:
            return None

        # ADX filter
        adx = self.data_feed.get_indicator(sym, "adx", 14)
        if adx is None or adx < self.adx_threshold:
            return None

        # ATR for buffer
        atr = self.data_feed.get_indicator(sym, "atr", 14)
        if atr is None or atr <= 0:
            return None

        buffer = self.buffer_atr_mult * atr
        entry_long = or_high + buffer
        entry_short = or_low - buffer

        price = bar.close

        # Long breakout
        if price > entry_long:
            stop_loss = or_low  # opposite side of OR
            take_profit = price + self.tp_range_mult * or_width
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strength=min((price - entry_long) / or_width, 1.0),
            )

        # Short breakout
        if price < entry_short:
            stop_loss = or_high  # opposite side of OR
            take_profit = price - self.tp_range_mult * or_width
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strength=min((entry_short - price) / or_width, 1.0),
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "or_minutes": self.or_minutes,
            "buffer_atr_mult": self.buffer_atr_mult,
            "min_range_pct": self.min_range_pct,
            "max_range_pct": self.max_range_pct,
            "adx_threshold": self.adx_threshold,
            "tp_range_mult": self.tp_range_mult,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "or_minutes": [15, 30, 45],
            "buffer_atr_mult": [0.1, 0.2, 0.3, 0.5],
            "min_range_pct": [0.2, 0.3, 0.4],
            "max_range_pct": [1.5, 2.0, 2.5],
            "adx_threshold": [10.0, 15.0, 20.0],
            "tp_range_mult": [1.5, 2.0, 2.5, 3.0],
        }
