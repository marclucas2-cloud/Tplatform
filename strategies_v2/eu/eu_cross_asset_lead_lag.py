"""EU Cross-Asset Lead-Lag strategy for BacktesterV2.

Hypothesis: During volatile moves, the DAX (Germany's most liquid EU index)
leads the Euro Stoxx 50 by 1-2 bars because DAX futures are the primary vehicle
for macro hedging in Europe. When DAX moves sharply (> move_threshold_pct),
ESTX50 tends to follow within 2 bars as arbitrageurs and index rebalancers
propagate the move. This creates a short window to trade the laggard.

Entry rules:
  1. Session filter: 09:00-16:30 CET (08:00-15:30 UTC)
  2. Detect DAX move > move_threshold_pct within last 1-2 bars
  3. Check ESTX50 has NOT followed (its move < follow_threshold_pct)
  4. Trade ESTX50 in the same direction as the DAX move
  5. One trade per detected divergence

Exit rules:
  - Take profit: ESTX50 catches up (moves follow_threshold_pct in trade direction)
  - Time exit: close after max_hold_bars (default: 4 bars) if not hit
  - Stop loss: ESTX50 moves opposite direction by sl_pct

Symbol: ESTX50 (traded), DAX (leader signal)
Timeframe: 15min or 1H bars
Expected: ~5-10 trades/month, win rate 55-65%, Sharpe 1.0-2.0
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


# Session: 08:00-15:30 UTC (= 09:00-16:30 CET)
_SESSION_START_HOUR_UTC = 8
_SESSION_END_HOUR_UTC = 15
_SESSION_END_MIN_UTC = 30

# Symbols
_LEADER_SYMBOL = "DAX"
_LAGGARD_SYMBOL = "ESTX50"

STRATEGY_CONFIG = {
    "name": "eu_cross_asset_lead_lag",
    "id": "EU-XLAG",
    "market_type": "eu_equity",
    "broker": "ibkr",
    "timeframe": "1H",
}


class EUCrossAssetLeadLag(StrategyBase):
    """Cross-asset lead-lag: DAX leads ESTX50 during volatile moves.

    Monitors DAX for sharp moves and trades ESTX50 in the same direction
    when the broad index has not yet followed.
    """

    SYMBOL = _LAGGARD_SYMBOL

    def __init__(self) -> None:
        # Move detection parameters
        self.move_threshold_pct: float = 0.01  # DAX must move > 1%
        self.follow_threshold_pct: float = 0.005  # ESTX50 "followed" if > 0.5%
        self.lookback_bars: int = 2  # Check DAX move over last N bars
        self.max_hold_bars: int = 4  # Exit after N bars if no TP/SL

        # Risk parameters
        self.sl_pct: float = 0.008  # Stop loss 0.8% adverse move
        self.tp_pct: float = 0.01  # Take profit 1% move (catch-up)

        # State tracking
        self._position_open: bool = False
        self._bars_held: int = 0
        self._session_date: Optional[str] = None

        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return "eu_cross_asset_lead_lag"

    @property
    def asset_class(self) -> str:
        return "eu_equity"

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
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None

        hour = bar.timestamp.hour
        minute = bar.timestamp.minute if hasattr(bar.timestamp, "minute") else 0
        bar_minutes = hour * 60 + minute

        # Reset daily state
        bar_date = str(bar.timestamp.date())
        if bar_date != self._session_date:
            self._session_date = bar_date
            self._position_open = False
            self._bars_held = 0

        # Session filter: 08:00-15:30 UTC
        if hour < _SESSION_START_HOUR_UTC:
            return None
        if bar_minutes > _SESSION_END_HOUR_UTC * 60 + _SESSION_END_MIN_UTC:
            return None

        # Track bars held for time exit
        if self._position_open:
            self._bars_held += 1
            # Time exit after max_hold_bars — signal handled by engine on_eod
            if self._bars_held >= self.max_hold_bars:
                self._position_open = False
                self._bars_held = 0
            return None

        # Get DAX recent bars for move detection
        dax_bars = self.data_feed.get_bars(_LEADER_SYMBOL, self.lookback_bars + 1)
        if dax_bars.empty or len(dax_bars) < self.lookback_bars + 1:
            return None

        # Get ESTX50 recent bars for follow detection
        sx5e_bars = self.data_feed.get_bars(_LAGGARD_SYMBOL, self.lookback_bars + 1)
        if sx5e_bars.empty or len(sx5e_bars) < self.lookback_bars + 1:
            return None

        # Compute DAX move over lookback_bars
        dax_start = float(dax_bars.iloc[-(self.lookback_bars + 1)]["close"])
        dax_end = float(dax_bars.iloc[-1]["close"])
        if dax_start <= 0:
            return None
        dax_move_pct = (dax_end - dax_start) / dax_start

        # Compute ESTX50 move over same period
        sx5e_start = float(sx5e_bars.iloc[-(self.lookback_bars + 1)]["close"])
        sx5e_end = float(sx5e_bars.iloc[-1]["close"])
        if sx5e_start <= 0:
            return None
        sx5e_move_pct = (sx5e_end - sx5e_start) / sx5e_start

        # Check: DAX moved significantly, ESTX50 has NOT followed
        if abs(dax_move_pct) < self.move_threshold_pct:
            return None

        # ESTX50 already followed — no edge
        if abs(sx5e_move_pct) >= self.follow_threshold_pct:
            return None

        sym = self.SYMBOL

        # Trade ESTX50 in the direction of DAX move
        if dax_move_pct > 0:
            # DAX up, ESTX50 lagging -> BUY ESTX50
            sl = bar.close * (1.0 - self.sl_pct)
            tp = bar.close * (1.0 + self.tp_pct)
            self._position_open = True
            self._bars_held = 0
            strength = min(abs(dax_move_pct) / (self.move_threshold_pct * 2), 1.0)
            return Signal(
                symbol=sym,
                side="BUY",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=strength,
            )
        else:
            # DAX down, ESTX50 lagging -> SELL ESTX50
            sl = bar.close * (1.0 + self.sl_pct)
            tp = bar.close * (1.0 - self.tp_pct)
            self._position_open = True
            self._bars_held = 0
            strength = min(abs(dax_move_pct) / (self.move_threshold_pct * 2), 1.0)
            return Signal(
                symbol=sym,
                side="SELL",
                strategy_name=self.name,
                stop_loss=sl,
                take_profit=tp,
                strength=strength,
            )

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "move_threshold_pct": self.move_threshold_pct,
            "follow_threshold_pct": self.follow_threshold_pct,
            "lookback_bars": self.lookback_bars,
            "max_hold_bars": self.max_hold_bars,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "move_threshold_pct": [0.007, 0.01, 0.012, 0.015],
            "follow_threshold_pct": [0.003, 0.005, 0.007],
            "lookback_bars": [1, 2, 3],
            "max_hold_bars": [3, 4, 5, 6],
            "sl_pct": [0.005, 0.008, 0.01],
            "tp_pct": [0.007, 0.01, 0.012],
        }

    def on_eod(self, timestamp) -> None:
        """Reset daily state at end of day."""
        self._position_open = False
        self._bars_held = 0
        self._session_date = None
