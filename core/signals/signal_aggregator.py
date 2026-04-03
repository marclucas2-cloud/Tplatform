"""U8-01: Signal Aggregator — aggregate small signals into tradeable positions.

Instead of SKIP for $50 positions, buffer them and aggregate:
  - BTC Momentum LONG $60 + BTC MeanRev LONG $45 = $105 → TRADE
  - Conflicting signals (LONG + SHORT) cancel out
  - Signals expire after 4h without aggregation
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger("signals.aggregator")


@dataclass
class BufferedSignal:
    """A signal waiting in the aggregation buffer."""
    strategy: str
    symbol: str
    direction: str  # LONG or SHORT
    size_usd: float
    timestamp: datetime
    signal_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedOrder:
    """An aggregated order from multiple signals."""
    symbol: str
    direction: str
    total_size_usd: float
    contributing_strategies: List[str]
    n_signals: int

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "total_size_usd": round(self.total_size_usd, 2),
            "strategies": self.contributing_strategies,
            "n_signals": self.n_signals,
        }


class SignalAggregator:
    """Aggregates sub-minimum signals into tradeable positions.

    Usage:
        agg = SignalAggregator(min_position_usd=100)
        agg.buffer("btc_momentum", "BTC", "LONG", 60)
        agg.buffer("btc_meanrev", "BTC", "LONG", 45)
        orders = agg.aggregate()  # Returns AggregatedOrder for BTC $105
    """

    def __init__(
        self,
        min_position_usd: float = 100,
        expiry_hours: float = 4.0,
    ):
        self._min_size = min_position_usd
        self._expiry = timedelta(hours=expiry_hours)
        self._buffer: Dict[str, List[BufferedSignal]] = defaultdict(list)

    def buffer(
        self,
        strategy: str,
        symbol: str,
        direction: str,
        size_usd: float,
        signal_data: Dict = None,
    ):
        """Add a sub-minimum signal to the buffer."""
        self._buffer[symbol].append(BufferedSignal(
            strategy=strategy,
            symbol=symbol,
            direction=direction.upper(),
            size_usd=size_usd,
            timestamp=datetime.now(),
            signal_data=signal_data or {},
        ))
        logger.info(
            "AGGREGATOR|buffer|%s|%s|%s|$%.0f",
            strategy, symbol, direction, size_usd,
        )

    def aggregate(self) -> List[AggregatedOrder]:
        """Check buffer and aggregate where possible."""
        self._expire_old()
        orders = []

        for symbol, signals in list(self._buffer.items()):
            if not signals:
                continue

            # Separate by direction
            longs = [s for s in signals if s.direction == "LONG"]
            shorts = [s for s in signals if s.direction == "SHORT"]

            # Cancel conflicting signals
            if longs and shorts:
                long_total = sum(s.size_usd for s in longs)
                short_total = sum(s.size_usd for s in shorts)
                net_direction = "LONG" if long_total > short_total else "SHORT"
                net_size = abs(long_total - short_total)

                if net_size >= self._min_size:
                    strats = [s.strategy for s in (longs if net_direction == "LONG" else shorts)]
                    orders.append(AggregatedOrder(
                        symbol=symbol,
                        direction=net_direction,
                        total_size_usd=net_size,
                        contributing_strategies=strats,
                        n_signals=len(longs) + len(shorts),
                    ))
                    self._buffer[symbol] = []
                    logger.info(
                        "AGGREGATOR|net|%s|%s|$%.0f|%d signals",
                        symbol, net_direction, net_size, len(strats),
                    )
                continue

            # All same direction
            active_signals = longs or shorts
            total = sum(s.size_usd for s in active_signals)

            if total >= self._min_size:
                strats = [s.strategy for s in active_signals]
                orders.append(AggregatedOrder(
                    symbol=symbol,
                    direction=active_signals[0].direction,
                    total_size_usd=total,
                    contributing_strategies=strats,
                    n_signals=len(active_signals),
                ))
                self._buffer[symbol] = []
                logger.info(
                    "AGGREGATOR|aggregate|%s|%s|$%.0f|%d signals",
                    symbol, active_signals[0].direction, total, len(strats),
                )

        return orders

    def _expire_old(self):
        """Remove expired signals from buffer."""
        now = datetime.now()
        for symbol in list(self._buffer.keys()):
            before = len(self._buffer[symbol])
            self._buffer[symbol] = [
                s for s in self._buffer[symbol]
                if (now - s.timestamp) < self._expiry
            ]
            expired = before - len(self._buffer[symbol])
            if expired > 0:
                logger.info("AGGREGATOR|expired|%s|%d signals", symbol, expired)

    def get_buffer_status(self) -> Dict[str, Any]:
        return {
            symbol: {
                "n_signals": len(signals),
                "total_usd": round(sum(s.size_usd for s in signals), 2),
                "directions": list(set(s.direction for s in signals)),
            }
            for symbol, signals in self._buffer.items()
            if signals
        }
