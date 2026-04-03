"""P6-01: Live Slippage Calibrator — calibrate backtest slippage to live reality.

NEED_LIVE: Requires 50+ trades per broker before activation.

Compares slippage by:
  - Instrument
  - Hour of day
  - Order type (MARKET vs LIMIT)
  - Position size bucket

If live slippage = 2x backtest -> recalibrate the backtester.
"""

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIN_TRADES_ACTIVATE = 50


@dataclass
class SlippageObservation:
    """A single slippage observation from a live fill."""
    trade_id: str
    broker: str
    instrument: str
    order_type: str  # MARKET, LIMIT
    direction: str   # BUY, SELL
    expected_price: float
    fill_price: float
    slippage_bps: float
    notional: float
    hour_utc: int
    timestamp: str


@dataclass
class SlippageCalibration:
    """Calibration result for a broker/instrument pair."""
    broker: str
    instrument: str
    model_slippage_bps: float
    live_slippage_bps: float
    calibration_factor: float  # live / model
    n_trades: int
    confidence: str  # "HIGH" (50+), "MEDIUM" (20-50), "LOW" (<20)
    by_hour: dict[int, float] = field(default_factory=dict)
    by_order_type: dict[str, float] = field(default_factory=dict)
    by_size_bucket: dict[str, float] = field(default_factory=dict)
    recalibrate: bool = False

    def to_dict(self) -> dict:
        return {
            "broker": self.broker,
            "instrument": self.instrument,
            "model_slippage_bps": round(self.model_slippage_bps, 2),
            "live_slippage_bps": round(self.live_slippage_bps, 2),
            "calibration_factor": round(self.calibration_factor, 2),
            "n_trades": self.n_trades,
            "confidence": self.confidence,
            "by_hour": {str(k): round(v, 2) for k, v in self.by_hour.items()},
            "by_order_type": {k: round(v, 2) for k, v in self.by_order_type.items()},
            "by_size_bucket": {k: round(v, 2) for k, v in self.by_size_bucket.items()},
            "recalibrate": self.recalibrate,
        }


# Model slippage from CLAUDE.md
MODEL_SLIPPAGE_BPS = {
    "alpaca": 2.0,    # $0 commission + 0.02% PFOF spread
    "ibkr_fx": 1.0,   # 1 bps
    "ibkr_eu": 3.0,   # 3 bps
    "binance": 2.0,   # 2 bps (with BNB discount)
}

SIZE_BUCKETS = {
    "micro": (0, 500),       # < $500
    "small": (500, 2000),     # $500-$2000
    "medium": (2000, 5000),   # $2000-$5000
    "large": (5000, float("inf")),  # > $5000
}


class SlippageCalibrator:
    """Calibrates live slippage against backtest model.

    NEED_LIVE: Requires 50+ trades per broker.

    Usage:
        cal = SlippageCalibrator()
        cal.record_fill(
            trade_id="T001", broker="binance", instrument="BTCUSDC",
            order_type="MARKET", direction="BUY",
            expected_price=45000, fill_price=45009, notional=500,
        )
        # After 50+ trades:
        results = cal.calibrate()
    """

    def __init__(self):
        self._observations: list[SlippageObservation] = []

    @property
    def is_active(self) -> bool:
        """True if enough trades to produce meaningful calibration."""
        return len(self._observations) >= MIN_TRADES_ACTIVATE

    def record_fill(
        self,
        trade_id: str,
        broker: str,
        instrument: str,
        order_type: str,
        direction: str,
        expected_price: float,
        fill_price: float,
        notional: float,
    ):
        """Record a live fill for slippage tracking."""
        if expected_price <= 0:
            return

        slippage_bps = abs(fill_price - expected_price) / expected_price * 10_000
        # Adjust sign: positive = unfavorable
        if direction == "BUY" and fill_price > expected_price:
            slippage_bps = abs(slippage_bps)
        elif direction == "SELL" and fill_price < expected_price:
            slippage_bps = abs(slippage_bps)
        else:
            slippage_bps = -abs(slippage_bps)  # Favorable

        now = datetime.now(UTC)
        obs = SlippageObservation(
            trade_id=trade_id,
            broker=broker,
            instrument=instrument,
            order_type=order_type.upper(),
            direction=direction.upper(),
            expected_price=expected_price,
            fill_price=fill_price,
            slippage_bps=slippage_bps,
            notional=notional,
            hour_utc=now.hour,
            timestamp=now.isoformat(),
        )
        self._observations.append(obs)

    def calibrate(self) -> dict[str, SlippageCalibration]:
        """Run calibration for all brokers with enough data."""
        if not self.is_active:
            logger.info(
                "SlippageCalibrator: %d/%d trades — not yet active",
                len(self._observations), MIN_TRADES_ACTIVATE,
            )
            return {}

        # Group by broker
        by_broker: dict[str, list[SlippageObservation]] = defaultdict(list)
        for obs in self._observations:
            by_broker[obs.broker].append(obs)

        results = {}
        for broker, obs_list in by_broker.items():
            if len(obs_list) < 10:
                continue

            model_bps = MODEL_SLIPPAGE_BPS.get(broker, 2.0)
            live_bps = sum(o.slippage_bps for o in obs_list) / len(obs_list)
            factor = live_bps / model_bps if model_bps > 0 else 1.0

            # Confidence
            n = len(obs_list)
            if n >= 50:
                confidence = "HIGH"
            elif n >= 20:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            # By hour
            by_hour: dict[int, list[float]] = defaultdict(list)
            for o in obs_list:
                by_hour[o.hour_utc].append(o.slippage_bps)
            hour_avg = {h: sum(v) / len(v) for h, v in by_hour.items()}

            # By order type
            by_type: dict[str, list[float]] = defaultdict(list)
            for o in obs_list:
                by_type[o.order_type].append(o.slippage_bps)
            type_avg = {t: sum(v) / len(v) for t, v in by_type.items()}

            # By size bucket
            by_size: dict[str, list[float]] = defaultdict(list)
            for o in obs_list:
                for bucket_name, (lo, hi) in SIZE_BUCKETS.items():
                    if lo <= o.notional < hi:
                        by_size[bucket_name].append(o.slippage_bps)
                        break
            size_avg = {s: sum(v) / len(v) for s, v in by_size.items()}

            cal = SlippageCalibration(
                broker=broker,
                instrument="ALL",
                model_slippage_bps=model_bps,
                live_slippage_bps=live_bps,
                calibration_factor=factor,
                n_trades=n,
                confidence=confidence,
                by_hour=hour_avg,
                by_order_type=type_avg,
                by_size_bucket=size_avg,
                recalibrate=abs(factor - 1.0) > 0.5,  # > 50% off
            )
            results[broker] = cal

            if cal.recalibrate:
                logger.warning(
                    "RECALIBRATE %s: live=%.1f bps vs model=%.1f bps (%.1fx)",
                    broker, live_bps, model_bps, factor,
                )

        return results

    def get_status(self) -> dict:
        """Get calibrator status."""
        return {
            "active": self.is_active,
            "total_observations": len(self._observations),
            "min_required": MIN_TRADES_ACTIVATE,
            "by_broker": {
                broker: len([o for o in self._observations if o.broker == broker])
                for broker in set(o.broker for o in self._observations)
            } if self._observations else {},
        }
