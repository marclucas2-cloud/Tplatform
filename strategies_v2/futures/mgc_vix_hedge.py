"""MGC Gold VIX Hedge for BacktesterV2.

FUT-007: Micro Gold (MGC) Flight-to-Quality Volatility Hedge.
COMEX, $1,000 margin, tick size $0.10 = $1.00/tick.

EDGE: Gold is the canonical flight-to-quality asset. When equity volatility
spikes (VIX > threshold) AND gold breaks above its Bollinger upper band,
we have a confirmed fear bid in gold (Baur & Lucey, "Is Gold a Hedge or
a Safe Haven?", 2010). This strategy captures the acceleration phase of
gold rallies during risk-off episodes. Conversely, when VIX collapses
below 15 and gold breaks below Bollinger lower band, risk-on pressure
causes gold liquidation.

The key insight is that the VIX spike CONFIRMS the gold breakout is
structural (flight-to-quality) rather than just noise.

Rules:
- Long: VIX RSI(14) > 60 AND gold close > Bollinger Upper(20,2)
- Short: VIX RSI(14) < 35 AND gold close < Bollinger Lower(20,2)
- Stop: 2.5 ATR(14) from entry
- Take profit: 4.0 ATR(14) from entry (R/R ~1.6:1)
- Filter: ADX(14) > 20 on gold (trend confirmed)
- Holding: 1-5 days (mean revert once fear subsides)
- ~4-6 trades/month (selective — only trades confirmed fear/greed extremes)
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MGCVixHedge(StrategyBase):
    """MGC Gold flight-to-quality volatility hedge.

    Long gold when VIX spikes and gold breaks Bollinger upper (fear bid).
    Short gold when VIX collapses and gold breaks Bollinger lower (risk-on).
    Uses VIX RSI as the volatility regime filter, not raw VIX level,
    because it captures the CHANGE in fear more reliably than the level.
    """

    SYMBOL = "MGC"
    VIX_SYMBOL = "VIX"
    TICK_SIZE = 0.10
    TICK_VALUE = 1.00  # $1.00 per tick (10 troy oz * $0.10)

    def __init__(self) -> None:
        self.bb_period: int = 20
        self.bb_std: float = 2.0
        self.vix_rsi_long: float = 60.0    # VIX RSI > 60 = rising fear
        self.vix_rsi_short: float = 35.0   # VIX RSI < 35 = collapsing fear
        self.adx_threshold: float = 20.0
        self.sl_atr_mult: float = 2.5
        self.tp_atr_mult: float = 4.0
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mgc_vix_hedge"

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

        # Gold indicators
        bb_upper = self.data_feed.get_indicator(sym, "bollinger_upper", self.bb_period)
        bb_lower = self.data_feed.get_indicator(sym, "bollinger_lower", self.bb_period)
        adx = self.data_feed.get_indicator(sym, "adx", 14)
        atr = self.data_feed.get_indicator(sym, "atr", 14)

        if any(v is None for v in (bb_upper, bb_lower, adx, atr)):
            return None

        if atr <= 0:
            return None

        # ADX filter: need a trend in gold
        if adx < self.adx_threshold:
            return None

        # VIX RSI as fear/greed gauge
        vix_rsi = self.data_feed.get_indicator(self.VIX_SYMBOL, "rsi", 14)
        if vix_rsi is None:
            return None

        price = bar.close

        # LONG: rising fear (VIX RSI > 60) + gold breakout above Bollinger upper
        if vix_rsi > self.vix_rsi_long and price > bb_upper:
            stop_loss = price - self.sl_atr_mult * atr
            take_profit = price + self.tp_atr_mult * atr
            strength = min((vix_rsi - self.vix_rsi_long) / 30.0, 1.0)
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strength=strength,
            )

        # SHORT: collapsing fear (VIX RSI < 35) + gold breakdown below Bollinger lower
        if vix_rsi < self.vix_rsi_short and price < bb_lower:
            stop_loss = price + self.sl_atr_mult * atr
            take_profit = price - self.tp_atr_mult * atr
            strength = min((self.vix_rsi_short - vix_rsi) / 25.0, 1.0)
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strength=strength,
            )

        return None

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "vix_rsi_long": self.vix_rsi_long,
            "vix_rsi_short": self.vix_rsi_short,
            "adx_threshold": self.adx_threshold,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "vix_rsi_long": [55.0, 60.0, 65.0, 70.0],
            "vix_rsi_short": [30.0, 35.0, 40.0],
            "adx_threshold": [15.0, 20.0, 25.0],
            "sl_atr_mult": [2.0, 2.5, 3.0],
            "tp_atr_mult": [3.0, 4.0, 5.0],
        }
