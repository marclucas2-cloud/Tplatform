"""P2-03: Entry Timing Optimization — improve execution price via timing.

Three mechanisms:
  1. LIMIT_PATIENCE: Place LIMIT at mid-price, wait N seconds, fallback to MARKET
  2. INTRADAY_TIMING: Delay signal to preferred liquidity window
  3. FADE_IN: Scale into position over 2-3 tranches

Config: config/entry_timing.yaml
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "entry_timing.yaml"


class TimingMethod(str, Enum):
    IMMEDIATE = "IMMEDIATE"         # Market order now
    LIMIT_PATIENCE = "LIMIT_PATIENCE"  # Limit order with timeout
    INTRADAY_TIMING = "INTRADAY_TIMING"  # Wait for optimal window
    FADE_IN = "FADE_IN"             # Scale in over tranches


class OrderUrgency(str, Enum):
    HIGH = "HIGH"       # Regime change, kill switch, SL -> MARKET
    NORMAL = "NORMAL"   # Standard signal -> LIMIT or timing
    LOW = "LOW"         # Rebalance, adjustment -> patient LIMIT


@dataclass
class TimingConfig:
    """Per-strategy timing configuration."""
    method: TimingMethod = TimingMethod.IMMEDIATE
    patience_seconds: int = 300     # LIMIT patience timer
    fallback: str = "MARKET"        # What to do after patience expires
    preferred_window_cet: str = ""  # e.g., "15:45-16:30"
    delay_if_outside: bool = False  # Delay if outside preferred window
    fade_tranches: int = 1          # Number of tranches for FADE_IN
    fade_interval_minutes: int = 60
    min_position_for_fade: float = 500.0  # Only fade positions > $500


@dataclass
class TimingDecision:
    """Output of the timing optimizer."""
    order_type: str           # "MARKET" or "LIMIT"
    price: float | None       # LIMIT price (None for MARKET)
    delay_seconds: int = 0    # Seconds to wait before sending
    tranches: list[dict] = field(default_factory=list)  # [{pct, delay_s}]
    urgency: OrderUrgency = OrderUrgency.NORMAL
    reason: str = ""
    original_method: str = ""

    def to_dict(self) -> dict:
        return {
            "order_type": self.order_type,
            "price": self.price,
            "delay_seconds": self.delay_seconds,
            "tranches": self.tranches,
            "urgency": self.urgency.value,
            "reason": self.reason,
            "original_method": self.original_method,
        }


class EntryTimingOptimizer:
    """Optimizes entry timing for better execution prices.

    Usage:
        optimizer = EntryTimingOptimizer()
        decision = optimizer.decide(
            strategy="fx_carry_vs",
            symbol="EURUSD",
            direction="BUY",
            signal_strength=0.75,
            mid_price=1.0850,
            spread_bps=1.2,
            notional=5000,
            regime="TREND_STRONG",
        )
        if decision.order_type == "LIMIT":
            place_limit_order(price=decision.price, timeout=300)
    """

    def __init__(self, config_path: Path | None = None):
        self._configs: dict[str, TimingConfig] = {}
        self._load_config(config_path or CONFIG_PATH)

    def _load_config(self, path: Path):
        if not path.exists():
            logger.debug("No entry_timing.yaml found, using defaults")
            return

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        for strat, cfg in data.get("strategies", {}).items():
            self._configs[strat] = TimingConfig(
                method=TimingMethod(cfg.get("method", "IMMEDIATE")),
                patience_seconds=cfg.get("patience_seconds", 300),
                fallback=cfg.get("fallback", "MARKET"),
                preferred_window_cet=cfg.get("preferred_window", ""),
                delay_if_outside=cfg.get("delay_if_outside", False),
                fade_tranches=cfg.get("fade_tranches", 1),
                fade_interval_minutes=cfg.get("fade_interval_minutes", 60),
                min_position_for_fade=cfg.get("min_position_for_fade", 500),
            )

    def decide(
        self,
        strategy: str,
        symbol: str,
        direction: str,
        signal_strength: float = 1.0,
        mid_price: float = 0.0,
        spread_bps: float = 1.0,
        notional: float = 0.0,
        regime: str = "UNKNOWN",
        is_kill_switch: bool = False,
        is_sl: bool = False,
        timestamp: datetime | None = None,
    ) -> TimingDecision:
        """Decide the optimal entry timing.

        Returns a TimingDecision with order type, price, delay, and tranches.
        """
        config = self._configs.get(strategy, TimingConfig())

        # Determine urgency
        urgency = self._classify_urgency(
            signal_strength, regime, is_kill_switch, is_sl
        )

        # HIGH urgency overrides everything -> MARKET NOW
        if urgency == OrderUrgency.HIGH:
            return TimingDecision(
                order_type="MARKET",
                price=None,
                urgency=urgency,
                reason="High urgency — immediate execution",
                original_method=config.method.value,
            )

        # Route by method
        if config.method == TimingMethod.LIMIT_PATIENCE:
            return self._limit_patience(config, mid_price, spread_bps, direction, urgency)

        elif config.method == TimingMethod.INTRADAY_TIMING:
            return self._intraday_timing(config, mid_price, direction, urgency, timestamp)

        elif config.method == TimingMethod.FADE_IN:
            return self._fade_in(config, mid_price, direction, notional, urgency)

        # Default: IMMEDIATE
        return TimingDecision(
            order_type="MARKET",
            price=None,
            urgency=urgency,
            reason="Immediate execution (default)",
            original_method="IMMEDIATE",
        )

    def _classify_urgency(
        self,
        signal_strength: float,
        regime: str,
        is_kill_switch: bool,
        is_sl: bool,
    ) -> OrderUrgency:
        """Classify order urgency."""
        if is_kill_switch or is_sl:
            return OrderUrgency.HIGH

        if regime in ("PANIC", "HIGH_VOL") and signal_strength > 0.8:
            return OrderUrgency.HIGH

        if signal_strength < 0.5:
            return OrderUrgency.LOW

        return OrderUrgency.NORMAL

    def _limit_patience(
        self,
        config: TimingConfig,
        mid_price: float,
        spread_bps: float,
        direction: str,
        urgency: OrderUrgency,
    ) -> TimingDecision:
        """LIMIT at mid-price with patience timer."""
        if mid_price <= 0:
            return TimingDecision(
                order_type="MARKET",
                price=None,
                urgency=urgency,
                reason="No mid-price available, using MARKET",
                original_method="LIMIT_PATIENCE",
            )

        # Place LIMIT slightly better than mid
        half_spread = mid_price * spread_bps / 10_000 / 2

        if direction.upper() == "BUY":
            limit_price = mid_price - half_spread * 0.3  # Bid side
        else:
            limit_price = mid_price + half_spread * 0.3  # Ask side

        patience = config.patience_seconds
        if urgency == OrderUrgency.LOW:
            patience = min(900, patience * 3)  # More patient for low urgency

        return TimingDecision(
            order_type="LIMIT",
            price=round(limit_price, 6),
            urgency=urgency,
            reason=f"LIMIT at {limit_price:.6f}, patience {patience}s, fallback {config.fallback}",
            original_method="LIMIT_PATIENCE",
            tranches=[{
                "pct": 1.0,
                "type": "LIMIT",
                "price": round(limit_price, 6),
                "timeout_s": patience,
                "fallback": config.fallback,
            }],
        )

    def _intraday_timing(
        self,
        config: TimingConfig,
        mid_price: float,
        direction: str,
        urgency: OrderUrgency,
        timestamp: datetime | None,
    ) -> TimingDecision:
        """Delay signal to preferred liquidity window."""
        if not config.preferred_window_cet or not config.delay_if_outside:
            return TimingDecision(
                order_type="MARKET",
                price=None,
                urgency=urgency,
                reason="No preferred window configured",
                original_method="INTRADAY_TIMING",
            )

        now = timestamp or datetime.now(timezone.utc)
        cet_hour = (now.hour + 1) % 24  # UTC -> CET approximation

        # Parse window "HH:MM-HH:MM"
        parts = config.preferred_window_cet.split("-")
        if len(parts) != 2:
            return TimingDecision(
                order_type="MARKET", price=None, urgency=urgency,
                reason="Invalid window format",
                original_method="INTRADAY_TIMING",
            )

        start_h = int(parts[0].split(":")[0])
        end_h = int(parts[1].split(":")[0])

        in_window = start_h <= cet_hour < end_h

        if in_window:
            return TimingDecision(
                order_type="MARKET",
                price=None,
                urgency=urgency,
                reason=f"In preferred window ({config.preferred_window_cet} CET)",
                original_method="INTRADAY_TIMING",
            )

        # Calculate delay to window start
        if cet_hour < start_h:
            delay_hours = start_h - cet_hour
        else:
            delay_hours = (24 - cet_hour) + start_h

        delay_seconds = delay_hours * 3600

        # Don't delay more than 8 hours
        if delay_seconds > 8 * 3600:
            return TimingDecision(
                order_type="MARKET",
                price=None,
                urgency=urgency,
                reason=f"Delay too long ({delay_hours}h), executing now",
                original_method="INTRADAY_TIMING",
            )

        return TimingDecision(
            order_type="MARKET",
            price=None,
            delay_seconds=delay_seconds,
            urgency=urgency,
            reason=f"Delayed {delay_hours}h to {config.preferred_window_cet} CET",
            original_method="INTRADAY_TIMING",
        )

    def _fade_in(
        self,
        config: TimingConfig,
        mid_price: float,
        direction: str,
        notional: float,
        urgency: OrderUrgency,
    ) -> TimingDecision:
        """Scale into position over multiple tranches."""
        if notional < config.min_position_for_fade or config.fade_tranches <= 1:
            return TimingDecision(
                order_type="MARKET",
                price=None,
                urgency=urgency,
                reason=f"Position ${notional:.0f} too small for fade-in",
                original_method="FADE_IN",
            )

        n = config.fade_tranches
        interval = config.fade_interval_minutes * 60

        # Tranche schedule: 50%, 25%, 25% for 3 tranches
        if n == 2:
            pcts = [0.60, 0.40]
        elif n == 3:
            pcts = [0.50, 0.25, 0.25]
        else:
            pcts = [1.0 / n] * n

        tranches = []
        for i, pct in enumerate(pcts):
            tranches.append({
                "pct": round(pct, 2),
                "delay_s": i * interval,
                "type": "MARKET",
            })

        return TimingDecision(
            order_type="MARKET",
            price=None,
            urgency=urgency,
            reason=f"Fade-in: {n} tranches over {(n-1) * config.fade_interval_minutes}min",
            original_method="FADE_IN",
            tranches=tranches,
        )
