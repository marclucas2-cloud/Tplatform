"""U5-01: Always-On Mode for structural carry strategies.

FX Carry is a permanent trade — the carry exists because JPY rate = 0%
and AUD rate = 4.35%. Waiting for a "signal" to enter carry is like
waiting for a "signal" to collect dividends.

Always-on = ALWAYS in position, sizing varies by regime + vol.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("strategies.always_on")


@dataclass
class AlwaysOnConfig:
    """Config for an always-on carry position."""
    name: str
    broker: str
    instrument: str
    direction: str  # LONG or SHORT
    base_allocation_pct: float    # % of broker capital
    min_allocation_pct: float     # Floor even in PANIC
    vol_target_annual: float      # Target vol (Barroso & Santa-Clara 2015)
    rebalance_threshold_pct: float  # Don't rebalance if change < this %

    # Regime multipliers (carry-specific)
    regime_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "TREND_STRONG": 1.0,
        "MEAN_REVERT": 0.8,
        "HIGH_VOL": 0.4,
        "PANIC": 0.2,       # Floor, NOT 0
        "LOW_LIQUIDITY": 0.3,
        "UNKNOWN": 0.6,
    })


# Default FX carry positions
DEFAULT_CARRY_POSITIONS = [
    AlwaysOnConfig(
        name="fx_carry_audjpy", broker="ibkr", instrument="AUDJPY",
        direction="LONG", base_allocation_pct=8, min_allocation_pct=2,
        vol_target_annual=0.05, rebalance_threshold_pct=20,
    ),
    AlwaysOnConfig(
        name="fx_carry_eurjpy", broker="ibkr", instrument="EURJPY",
        direction="LONG", base_allocation_pct=8, min_allocation_pct=2,
        vol_target_annual=0.05, rebalance_threshold_pct=20,
    ),
    AlwaysOnConfig(
        name="fx_carry_usdjpy", broker="ibkr", instrument="USDJPY",
        direction="LONG", base_allocation_pct=6, min_allocation_pct=1,
        vol_target_annual=0.05, rebalance_threshold_pct=20,
    ),
]


@dataclass
class CarryPositionTarget:
    """Target position for an always-on carry strategy."""
    name: str
    instrument: str
    direction: str
    target_notional: float
    current_notional: float
    needs_rebalance: bool
    regime: str
    regime_multiplier: float
    vol_scaling: float
    allocation_pct: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "instrument": self.instrument,
            "direction": self.direction,
            "target_notional": round(self.target_notional, 2),
            "current_notional": round(self.current_notional, 2),
            "delta": round(self.target_notional - self.current_notional, 2),
            "needs_rebalance": self.needs_rebalance,
            "regime": self.regime,
            "regime_mult": self.regime_multiplier,
            "vol_scaling": round(self.vol_scaling, 2),
            "allocation_pct": round(self.allocation_pct, 1),
        }


class AlwaysOnCarrier:
    """Manages always-on carry positions.

    Usage:
        carrier = AlwaysOnCarrier()
        targets = carrier.compute_targets(
            equity_by_broker={"ibkr": 10000},
            current_positions={"fx_carry_audjpy": 0},
            regime="TREND_STRONG",
            vol_by_instrument={"AUDJPY": 0.08},
        )
        for t in targets:
            if t.needs_rebalance:
                submit_order(t.instrument, t.direction, t.target_notional - t.current_notional)
    """

    def __init__(self, configs: List[AlwaysOnConfig] = None):
        self.configs = configs or DEFAULT_CARRY_POSITIONS

    def compute_targets(
        self,
        equity_by_broker: Dict[str, float],
        current_positions: Dict[str, float] = None,
        regime: str = "UNKNOWN",
        vol_by_instrument: Dict[str, float] = None,
    ) -> List[CarryPositionTarget]:
        """Compute target positions for all always-on strategies."""
        current_positions = current_positions or {}
        vol_by_instrument = vol_by_instrument or {}
        targets = []

        for cfg in self.configs:
            broker_equity = equity_by_broker.get(cfg.broker, 0)
            if broker_equity <= 0:
                continue

            # Regime multiplier
            regime_mult = cfg.regime_multipliers.get(regime, 0.6)

            # Vol scaling (Barroso & Santa-Clara 2015)
            realized_vol = vol_by_instrument.get(cfg.instrument, cfg.vol_target_annual)
            if realized_vol > 0:
                vol_scale = min(3.0, max(0.1, cfg.vol_target_annual / realized_vol))
            else:
                vol_scale = 1.0

            # Target allocation
            alloc = cfg.base_allocation_pct / 100 * regime_mult * vol_scale
            alloc = max(cfg.min_allocation_pct / 100, alloc)  # Floor

            target_notional = broker_equity * alloc
            current_notional = current_positions.get(cfg.name, 0)

            # Need rebalance?
            if current_notional == 0:
                needs_rebalance = target_notional > 0
            else:
                change_pct = abs(target_notional - current_notional) / current_notional * 100
                needs_rebalance = change_pct > cfg.rebalance_threshold_pct

            targets.append(CarryPositionTarget(
                name=cfg.name,
                instrument=cfg.instrument,
                direction=cfg.direction,
                target_notional=target_notional,
                current_notional=current_notional,
                needs_rebalance=needs_rebalance,
                regime=regime,
                regime_multiplier=regime_mult,
                vol_scaling=vol_scale,
                allocation_pct=alloc * 100,
            ))

        return targets

    def get_total_deployed(self, targets: List[CarryPositionTarget]) -> float:
        """Total notional deployed across all carry positions."""
        return sum(t.target_notional for t in targets)
