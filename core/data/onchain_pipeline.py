"""P5-02: Crypto On-Chain Data Pipeline — free on-chain data for crypto signals.

Sources (free tier):
  1. Blockchain.com API: hash rate, mempool, fees
  2. CoinGecko API: market cap, volume, dominance (already used for BTC.D)
  3. Glassnode/CryptoQuant free tier: MVRV, SOPR, exchange netflow (limited)

Features extracted:
  - Exchange Netflow (24h rolling): positive = sell pressure
  - MVRV Ratio: > 3.5 = overheated, < 1.0 = undervalued
  - Hash Rate Momentum: 30d momentum
  - Stablecoin Supply Ratio: multi-stablecoin

Note: on-chain data is DAILY at best. No intraday edge.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "onchain"


@dataclass
class OnChainMetrics:
    """Daily on-chain metrics for BTC/ETH."""
    timestamp: str
    symbol: str  # BTC or ETH
    exchange_netflow_24h: float | None = None  # Positive = sell pressure
    mvrv_ratio: float | None = None
    hashrate_30d_momentum: float | None = None
    stablecoin_supply_ratio: float | None = None
    fear_greed_index: int | None = None  # 0-100
    active_addresses_24h: int | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "exchange_netflow_24h": self.exchange_netflow_24h,
            "mvrv_ratio": self.mvrv_ratio,
            "hashrate_30d_momentum": self.hashrate_30d_momentum,
            "stablecoin_supply_ratio": self.stablecoin_supply_ratio,
            "fear_greed_index": self.fear_greed_index,
            "active_addresses_24h": self.active_addresses_24h,
        }


@dataclass
class OnChainSignal:
    """Signal derived from on-chain data."""
    metric: str
    signal: str  # "BULLISH", "BEARISH", "NEUTRAL"
    strength: float  # 0.0-1.0
    value: float
    threshold_low: float
    threshold_high: float
    description: str

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "signal": self.signal,
            "strength": round(self.strength, 2),
            "value": round(self.value, 4),
            "description": self.description,
        }


# Signal thresholds
MVRV_OVERHEATED = 3.5
MVRV_UNDERVALUED = 1.0
FEAR_GREED_EXTREME_FEAR = 20
FEAR_GREED_EXTREME_GREED = 80
NETFLOW_SELL_SIGNAL_STD = 2.0  # > 2 std dev netflow = sell signal


class OnChainDataStore:
    """Stores and retrieves on-chain data from JSONL files."""

    def __init__(self, data_dir: Path | None = None):
        self._dir = data_dir or DATA_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, metrics: OnChainMetrics):
        """Append metrics to JSONL file."""
        path = self._dir / f"onchain_{metrics.symbol.lower()}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(metrics.to_dict()) + "\n")

    def load_recent(self, symbol: str, days: int = 30) -> list[OnChainMetrics]:
        """Load recent metrics for a symbol."""
        path = self._dir / f"onchain_{symbol.lower()}.jsonl"
        if not path.exists():
            return []

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        results = []
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("timestamp", "") >= cutoff:
                    results.append(OnChainMetrics(**data))

        return results

    def get_latest(self, symbol: str) -> OnChainMetrics | None:
        """Get the most recent metrics for a symbol."""
        recent = self.load_recent(symbol, days=2)
        return recent[-1] if recent else None


class OnChainPipeline:
    """Processes on-chain data into trading signals.

    Usage:
        pipeline = OnChainPipeline()

        # Ingest data (from API or manual)
        pipeline.ingest(OnChainMetrics(
            timestamp="2026-04-03T00:00:00Z",
            symbol="BTC",
            exchange_netflow_24h=-5000,
            mvrv_ratio=1.8,
            hashrate_30d_momentum=0.05,
            fear_greed_index=35,
        ))

        # Get signals
        signals = pipeline.get_signals("BTC")
        for sig in signals:
            print(sig.metric, sig.signal, sig.strength)
    """

    def __init__(self, data_dir: Path | None = None):
        self._store = OnChainDataStore(data_dir)

    def ingest(self, metrics: OnChainMetrics):
        """Ingest new on-chain data point."""
        self._store.store(metrics)
        logger.debug("On-chain data ingested: %s %s", metrics.symbol, metrics.timestamp)

    def get_signals(self, symbol: str) -> list[OnChainSignal]:
        """Generate signals from latest on-chain data."""
        latest = self._store.get_latest(symbol)
        if not latest:
            return []

        signals = []

        # 1. MVRV Ratio
        if latest.mvrv_ratio is not None:
            signals.append(self._signal_mvrv(latest.mvrv_ratio))

        # 2. Exchange Netflow
        if latest.exchange_netflow_24h is not None:
            history = self._store.load_recent(symbol, days=30)
            signals.append(self._signal_netflow(
                latest.exchange_netflow_24h, history
            ))

        # 3. Hash Rate Momentum
        if latest.hashrate_30d_momentum is not None:
            signals.append(self._signal_hashrate(latest.hashrate_30d_momentum))

        # 4. Fear & Greed Index
        if latest.fear_greed_index is not None:
            signals.append(self._signal_fear_greed(latest.fear_greed_index))

        return [s for s in signals if s is not None]

    def get_composite_score(self, symbol: str) -> float:
        """Get composite on-chain score (-1.0 to 1.0).

        Positive = bullish, negative = bearish, 0 = neutral.
        Can be used as a filter additive in signal_quality_v2.
        """
        signals = self.get_signals(symbol)
        if not signals:
            return 0.0

        score = 0.0
        for sig in signals:
            if sig.signal == "BULLISH":
                score += sig.strength * 0.25
            elif sig.signal == "BEARISH":
                score -= sig.strength * 0.25

        return max(-1.0, min(1.0, score))

    def _signal_mvrv(self, mvrv: float) -> OnChainSignal:
        """MVRV ratio signal: mean-revert toward median."""
        if mvrv > MVRV_OVERHEATED:
            return OnChainSignal(
                metric="mvrv_ratio",
                signal="BEARISH",
                strength=min(1.0, (mvrv - MVRV_OVERHEATED) / 2),
                value=mvrv,
                threshold_low=MVRV_UNDERVALUED,
                threshold_high=MVRV_OVERHEATED,
                description=f"MVRV {mvrv:.2f} > {MVRV_OVERHEATED} — overheated",
            )
        elif mvrv < MVRV_UNDERVALUED:
            return OnChainSignal(
                metric="mvrv_ratio",
                signal="BULLISH",
                strength=min(1.0, (MVRV_UNDERVALUED - mvrv) / 0.5),
                value=mvrv,
                threshold_low=MVRV_UNDERVALUED,
                threshold_high=MVRV_OVERHEATED,
                description=f"MVRV {mvrv:.2f} < {MVRV_UNDERVALUED} — undervalued",
            )
        return OnChainSignal(
            metric="mvrv_ratio",
            signal="NEUTRAL",
            strength=0.0,
            value=mvrv,
            threshold_low=MVRV_UNDERVALUED,
            threshold_high=MVRV_OVERHEATED,
            description=f"MVRV {mvrv:.2f} — neutral zone",
        )

    def _signal_netflow(
        self,
        netflow: float,
        history: list[OnChainMetrics],
    ) -> OnChainSignal:
        """Exchange netflow: contrarian signal on extreme flows."""
        # Compute z-score of current netflow
        flows = [
            m.exchange_netflow_24h for m in history
            if m.exchange_netflow_24h is not None
        ]
        if len(flows) < 5:
            return OnChainSignal(
                metric="exchange_netflow",
                signal="NEUTRAL",
                strength=0.0,
                value=netflow,
                threshold_low=-1, threshold_high=1,
                description="Insufficient netflow history",
            )

        import numpy as np
        mean = np.mean(flows)
        std = np.std(flows)
        if std == 0:
            z = 0
        else:
            z = (netflow - mean) / std

        if z > NETFLOW_SELL_SIGNAL_STD:
            # Large inflow to exchanges = sell pressure
            return OnChainSignal(
                metric="exchange_netflow",
                signal="BEARISH",
                strength=min(1.0, z / 4),
                value=netflow,
                threshold_low=-NETFLOW_SELL_SIGNAL_STD,
                threshold_high=NETFLOW_SELL_SIGNAL_STD,
                description=f"Netflow z={z:.1f} — coins entering exchanges (sell pressure)",
            )
        elif z < -NETFLOW_SELL_SIGNAL_STD:
            # Large outflow = accumulation
            return OnChainSignal(
                metric="exchange_netflow",
                signal="BULLISH",
                strength=min(1.0, abs(z) / 4),
                value=netflow,
                threshold_low=-NETFLOW_SELL_SIGNAL_STD,
                threshold_high=NETFLOW_SELL_SIGNAL_STD,
                description=f"Netflow z={z:.1f} — coins leaving exchanges (accumulation)",
            )

        return OnChainSignal(
            metric="exchange_netflow",
            signal="NEUTRAL",
            strength=0.0,
            value=netflow,
            threshold_low=-NETFLOW_SELL_SIGNAL_STD,
            threshold_high=NETFLOW_SELL_SIGNAL_STD,
            description=f"Netflow z={z:.1f} — normal range",
        )

    def _signal_hashrate(self, momentum_30d: float) -> OnChainSignal:
        """Hash rate momentum: miner confidence indicator."""
        if momentum_30d > 0.10:
            return OnChainSignal(
                metric="hashrate_momentum",
                signal="BULLISH",
                strength=min(1.0, momentum_30d * 5),
                value=momentum_30d,
                threshold_low=-0.10, threshold_high=0.10,
                description=f"Hashrate +{momentum_30d:.1%} — miners bullish",
            )
        elif momentum_30d < -0.10:
            return OnChainSignal(
                metric="hashrate_momentum",
                signal="BEARISH",
                strength=min(1.0, abs(momentum_30d) * 3),
                value=momentum_30d,
                threshold_low=-0.10, threshold_high=0.10,
                description=f"Hashrate {momentum_30d:.1%} — miner capitulation?",
            )
        return OnChainSignal(
            metric="hashrate_momentum",
            signal="NEUTRAL",
            strength=0.0,
            value=momentum_30d,
            threshold_low=-0.10, threshold_high=0.10,
            description=f"Hashrate {momentum_30d:.1%} — stable",
        )

    def _signal_fear_greed(self, index: int) -> OnChainSignal:
        """Fear & Greed Index: contrarian signal at extremes."""
        if index <= FEAR_GREED_EXTREME_FEAR:
            return OnChainSignal(
                metric="fear_greed_index",
                signal="BULLISH",
                strength=min(1.0, (FEAR_GREED_EXTREME_FEAR - index) / 20),
                value=float(index),
                threshold_low=FEAR_GREED_EXTREME_FEAR,
                threshold_high=FEAR_GREED_EXTREME_GREED,
                description=f"F&G {index} — extreme fear (contrarian buy)",
            )
        elif index >= FEAR_GREED_EXTREME_GREED:
            return OnChainSignal(
                metric="fear_greed_index",
                signal="BEARISH",
                strength=min(1.0, (index - FEAR_GREED_EXTREME_GREED) / 20),
                value=float(index),
                threshold_low=FEAR_GREED_EXTREME_FEAR,
                threshold_high=FEAR_GREED_EXTREME_GREED,
                description=f"F&G {index} — extreme greed (contrarian sell)",
            )
        return OnChainSignal(
            metric="fear_greed_index",
            signal="NEUTRAL",
            strength=0.0,
            value=float(index),
            threshold_low=FEAR_GREED_EXTREME_FEAR,
            threshold_high=FEAR_GREED_EXTREME_GREED,
            description=f"F&G {index} — neutral zone",
        )
