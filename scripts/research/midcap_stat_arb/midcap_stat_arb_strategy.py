"""
MidCap Statistical Arbitrage — Strategy

Market-neutral pairs trading strategy on US mid-cap stocks ($10-50B).
Trades cointegrated pairs within the same GICS industry group.

Architecture:
    1. Weekly: PairScanner identifies top 15-20 cointegrated pairs
    2. Daily: Signal generator computes z-scores, generates LONG/SHORT/CLOSE
    3. Continuous: Position manager tracks open pairs, enforces stops

Broker: Alpaca (commission $0, fractional shares)
Capital: $30K allocation, $3K max per pair ($1.5K per leg)
Holding: 1-20 days (mean reversion horizon)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import logging
import json

logger = logging.getLogger("stat_arb.strategy")


# ============================================================
# Configuration
# ============================================================

@dataclass
class StatArbConfig:
    """Strategy configuration — all tunable parameters."""

    # Universe
    universe: str = "SP400"
    formation_period_days: int = 120
    rebalance_frequency: str = "weekly"  # "weekly" or "monthly"

    # Entry/Exit thresholds
    z_entry: float = 2.0           # Enter when |z| > z_entry
    z_exit: float = 0.5            # Exit when |z| < z_exit (mean reversion)
    z_stop: float = 4.0            # Stop loss when |z| > z_stop (divergence)
    z_add: float = 3.0             # Optional: add to position if z > z_add

    # Time management
    max_holding_days: int = 20     # Time stop
    min_holding_hours: int = 4     # Don't exit within 4h (avoid noise)

    # Pair quality filters
    adf_pvalue_max: float = 0.05
    half_life_max_days: float = 30.0
    half_life_min_days: float = 1.0
    hurst_max: float = 0.50
    min_spread_sharpe: float = 0.3
    min_daily_volume_usd: float = 5_000_000

    # Sizing
    max_pairs: int = 10            # Max simultaneous pairs
    position_per_leg_usd: float = 1500.0  # $1.5K per leg
    max_portfolio_pct: float = 0.80       # Max 80% of capital deployed

    # Risk
    max_correlation_between_pairs: float = 0.50  # Diversification
    daily_loss_limit_pct: float = 0.03           # -3% daily → stop
    pair_loss_limit_pct: float = 0.05            # -5% per pair → stop
    max_net_exposure_pct: float = 0.05           # Market neutral: net < 5%

    # Regime integration
    regime_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "TREND_STRONG": 0.5,     # Pairs trading suffers in trends
        "MEAN_REVERT": 1.0,      # Best regime
        "HIGH_VOL": 0.7,         # Wider spreads, more opportunity but risk
        "PANIC": 0.3,            # Reduce heavily
        "LOW_LIQUIDITY": 0.3,    # Spreads unreliable
        "UNKNOWN": 0.6,          # Conservative default
    })


# ============================================================
# Position Tracking
# ============================================================

class PairPositionStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"

@dataclass
class PairPosition:
    """Tracks an open pair trade."""
    pair_id: str
    ticker_a: str
    ticker_b: str
    direction: str            # "LONG_SPREAD" or "SHORT_SPREAD"
    gamma: float              # Hedge ratio
    entry_z: float            # Z-score at entry
    entry_time: datetime
    entry_price_a: float
    entry_price_b: float
    quantity_a: float         # Signed: positive = long, negative = short
    quantity_b: float         # Signed: opposite of A
    status: PairPositionStatus = PairPositionStatus.OPEN
    exit_z: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price_a: Optional[float] = None
    exit_price_b: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    peak_z: float = 0.0      # Max |z| during holding (for trailing)
    holding_days: int = 0

    @property
    def is_open(self) -> bool:
        return self.status == PairPositionStatus.OPEN

    @property
    def notional(self) -> float:
        """Total notional (absolute value both legs)."""
        return abs(self.quantity_a * self.entry_price_a) + \
               abs(self.quantity_b * self.entry_price_b)

    def update_pnl(self, price_a: float, price_b: float) -> None:
        """Update unrealized PnL."""
        pnl_a = self.quantity_a * (price_a - self.entry_price_a)
        pnl_b = self.quantity_b * (price_b - self.entry_price_b)
        self.pnl = pnl_a + pnl_b
        if self.notional > 0:
            self.pnl_pct = self.pnl / (self.notional / 2)  # PnL as % of one leg

    def to_dict(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "direction": self.direction,
            "entry_z": self.entry_z,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "quantity_a": self.quantity_a,
            "quantity_b": self.quantity_b,
            "status": self.status.value,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "holding_days": self.holding_days,
        }


# ============================================================
# Main Strategy
# ============================================================

class MidCapStatArbStrategy:
    """
    Mid-Cap Statistical Arbitrage Strategy.

    Lifecycle:
        1. __init__() with config
        2. update_pairs() — weekly, reformulate pair universe
        3. generate_signals() — daily, produce entry/exit signals
        4. execute_signals() — submit orders (called by worker)
        5. update_positions() — update PnL, check stops
    """

    def __init__(self, config: StatArbConfig = None):
        self.config = config or StatArbConfig()
        self.active_pairs: List = []        # Current PairCandidate list
        self.open_positions: Dict[str, PairPosition] = {}  # pair_id -> PairPosition
        self.closed_positions: List[PairPosition] = []
        self.last_scan_time: Optional[datetime] = None
        self.daily_pnl: float = 0.0
        self.daily_pnl_reset_date: Optional[datetime] = None

    # --------------------------------------------------------
    # PAIR MANAGEMENT
    # --------------------------------------------------------

    def update_pairs(
        self,
        prices: Dict[str, pd.DataFrame],
        volumes: Optional[Dict[str, pd.Series]] = None,
    ) -> List:
        """
        Weekly pair reformulation.
        Scans the universe and selects the best pairs.
        """
        from strategies_v2.us.midcap_stat_arb_scanner import PairScanner

        scanner = PairScanner(
            z_entry=self.config.z_entry,
            z_exit=self.config.z_exit,
            adf_pvalue_max=self.config.adf_pvalue_max,
            half_life_max=self.config.half_life_max_days,
            min_volume_usd=self.config.min_daily_volume_usd,
            max_pairs=self.config.max_pairs * 2,  # Scan more, filter later
        )

        candidates = scanner.scan(
            prices=prices,
            formation_days=self.config.formation_period_days,
            volumes=volumes,
        )

        # Filter: don't remove pairs that have open positions
        open_pair_ids = set(self.open_positions.keys())
        new_pairs = []
        for c in candidates:
            if len(new_pairs) >= self.config.max_pairs:
                break
            # Keep open positions even if they drop from the scan
            if c.pair_id in open_pair_ids:
                new_pairs.append(c)
                continue
            # Check diversification: new pair not too correlated with existing
            if self._check_diversification(c, new_pairs):
                new_pairs.append(c)

        self.active_pairs = new_pairs
        self.last_scan_time = datetime.now()

        logger.info(f"Pair update: {len(self.active_pairs)} active pairs, "
                     f"{len(self.open_positions)} open positions")

        return self.active_pairs

    def _check_diversification(self, candidate, existing: List) -> bool:
        """Check that a new pair isn't too correlated with existing pairs."""
        # Simple check: no ticker overlap
        candidate_tickers = {candidate.ticker_a, candidate.ticker_b}
        for p in existing:
            existing_tickers = {p.ticker_a, p.ticker_b}
            if candidate_tickers & existing_tickers:
                return False  # Ticker overlap
        # Could add return correlation check here
        return True

    # --------------------------------------------------------
    # SIGNAL GENERATION
    # --------------------------------------------------------

    def generate_signals(
        self,
        prices: Dict[str, pd.DataFrame],
        current_regime: str = "UNKNOWN",
    ) -> List[Dict]:
        """
        Daily signal generation.

        For each active pair:
            - Compute current z-score
            - Generate ENTRY, EXIT, or STOP signal
            - Apply regime multiplier

        Returns list of signal dicts ready for execution.
        """
        signals = []

        # Regime sizing multiplier
        regime_mult = self.config.regime_multipliers.get(current_regime, 0.6)

        for pair in self.active_pairs:
            try:
                signal = self._generate_pair_signal(pair, prices, regime_mult)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"Signal generation error for {pair.pair_id}: {e}")

        # Check existing positions for exits
        exit_signals = self._check_exits(prices)
        signals.extend(exit_signals)

        logger.info(f"Generated {len(signals)} signals "
                     f"(regime={current_regime}, mult={regime_mult})")

        return signals

    def _generate_pair_signal(
        self,
        pair,
        prices: Dict[str, pd.DataFrame],
        regime_mult: float,
    ) -> Optional[Dict]:
        """Generate signal for a single pair."""

        if pair.ticker_a not in prices or pair.ticker_b not in prices:
            return None

        close_a = prices[pair.ticker_a]["close"]
        close_b = prices[pair.ticker_b]["close"]

        # Calculate current spread and z-score
        lookback = self.config.formation_period_days
        log_a = np.log(close_a.iloc[-lookback:])
        log_b = np.log(close_b.iloc[-lookback:])

        common_idx = log_a.index.intersection(log_b.index)
        if len(common_idx) < lookback * 0.8:
            return None

        log_a = log_a.loc[common_idx]
        log_b = log_b.loc[common_idx]

        spread = log_a - pair.cointegration_coeff * log_b
        mu = spread.mean()
        sigma = spread.std()

        if sigma == 0:
            return None

        z = (spread.iloc[-1] - mu) / sigma
        price_a = close_a.iloc[-1]
        price_b = close_b.iloc[-1]

        # Check if we already have a position
        if pair.pair_id in self.open_positions:
            return None  # Exits handled separately in _check_exits

        # Entry signal
        if z > self.config.z_entry:
            # Spread is HIGH → short spread (short A, long B)
            return self._create_entry_signal(
                pair, "SHORT_SPREAD", z, price_a, price_b, regime_mult
            )
        elif z < -self.config.z_entry:
            # Spread is LOW → long spread (long A, short B)
            return self._create_entry_signal(
                pair, "LONG_SPREAD", z, price_a, price_b, regime_mult
            )

        return None

    def _create_entry_signal(
        self,
        pair,
        direction: str,
        z_score: float,
        price_a: float,
        price_b: float,
        regime_mult: float,
    ) -> Dict:
        """Create an entry signal with proper sizing."""

        # Check portfolio limits
        current_deployed = sum(
            p.notional for p in self.open_positions.values() if p.is_open
        )
        max_deploy = self.config.position_per_leg_usd * 2 * self.config.max_pairs * \
                     self.config.max_portfolio_pct

        if current_deployed >= max_deploy:
            logger.info(f"Skip {pair.pair_id}: portfolio limit reached "
                         f"({current_deployed:.0f}/{max_deploy:.0f})")
            return None

        # Daily loss check
        if self.daily_pnl < -(self.config.daily_loss_limit_pct * max_deploy):
            logger.info(f"Skip {pair.pair_id}: daily loss limit reached")
            return None

        # Sizing with regime adjustment
        leg_size = self.config.position_per_leg_usd * regime_mult

        # Calculate quantities
        qty_a = leg_size / price_a
        qty_b = leg_size / price_b * pair.cointegration_coeff

        if direction == "SHORT_SPREAD":
            qty_a = -qty_a  # Short A
            qty_b = abs(qty_b)   # Long B
        else:  # LONG_SPREAD
            qty_a = abs(qty_a)   # Long A
            qty_b = -qty_b  # Short B

        return {
            "type": "ENTRY",
            "pair_id": pair.pair_id,
            "ticker_a": pair.ticker_a,
            "ticker_b": pair.ticker_b,
            "direction": direction,
            "z_score": round(z_score, 4),
            "gamma": pair.cointegration_coeff,
            "quantity_a": round(qty_a, 6),
            "quantity_b": round(qty_b, 6),
            "price_a": price_a,
            "price_b": price_b,
            "leg_size_usd": round(leg_size, 2),
            "regime_mult": regime_mult,
            "quality_score": pair.quality_score,
            "half_life": pair.half_life_days,
            "timestamp": datetime.now().isoformat(),
        }

    def _check_exits(self, prices: Dict[str, pd.DataFrame]) -> List[Dict]:
        """Check all open positions for exit conditions."""
        exit_signals = []

        for pair_id, pos in list(self.open_positions.items()):
            if not pos.is_open:
                continue

            if pos.ticker_a not in prices or pos.ticker_b not in prices:
                continue

            close_a = prices[pos.ticker_a]["close"]
            close_b = prices[pos.ticker_b]["close"]

            price_a = close_a.iloc[-1]
            price_b = close_b.iloc[-1]

            # Update PnL
            pos.update_pnl(price_a, price_b)

            # Calculate current z-score
            lookback = self.config.formation_period_days
            log_a = np.log(close_a.iloc[-lookback:])
            log_b = np.log(close_b.iloc[-lookback:])
            common_idx = log_a.index.intersection(log_b.index)

            if len(common_idx) < 20:
                continue

            log_a = log_a.loc[common_idx]
            log_b = log_b.loc[common_idx]
            spread = log_a - pos.gamma * log_b
            mu = spread.mean()
            sigma = spread.std()

            if sigma == 0:
                continue

            z = (spread.iloc[-1] - mu) / sigma
            pos.peak_z = max(pos.peak_z, abs(z))

            # Update holding days
            if pos.entry_time:
                pos.holding_days = (datetime.now() - pos.entry_time).days

            exit_reason = None

            # 1. Mean reversion achieved
            if abs(z) < self.config.z_exit:
                exit_reason = "MEAN_REVERSION"

            # 2. Stop loss (divergence)
            elif abs(z) > self.config.z_stop:
                exit_reason = "STOP_LOSS"

            # 3. Time stop
            elif pos.holding_days > self.config.max_holding_days:
                exit_reason = "TIME_STOP"

            # 4. Per-pair loss limit
            elif pos.pnl_pct < -self.config.pair_loss_limit_pct:
                exit_reason = "PAIR_LOSS_LIMIT"

            if exit_reason:
                exit_signals.append({
                    "type": "EXIT",
                    "pair_id": pair_id,
                    "ticker_a": pos.ticker_a,
                    "ticker_b": pos.ticker_b,
                    "reason": exit_reason,
                    "z_score": round(z, 4),
                    "pnl": round(pos.pnl, 2),
                    "pnl_pct": round(pos.pnl_pct, 4),
                    "holding_days": pos.holding_days,
                    "price_a": price_a,
                    "price_b": price_b,
                    "timestamp": datetime.now().isoformat(),
                })

        return exit_signals

    # --------------------------------------------------------
    # EXECUTION (called by worker after signal generation)
    # --------------------------------------------------------

    def on_entry_filled(
        self,
        signal: Dict,
        fill_price_a: float,
        fill_price_b: float,
    ) -> PairPosition:
        """Record a new pair position after fills."""
        pos = PairPosition(
            pair_id=signal["pair_id"],
            ticker_a=signal["ticker_a"],
            ticker_b=signal["ticker_b"],
            direction=signal["direction"],
            gamma=signal["gamma"],
            entry_z=signal["z_score"],
            entry_time=datetime.now(),
            entry_price_a=fill_price_a,
            entry_price_b=fill_price_b,
            quantity_a=signal["quantity_a"],
            quantity_b=signal["quantity_b"],
        )
        self.open_positions[signal["pair_id"]] = pos
        logger.info(f"Opened pair {pos.pair_id} {pos.direction} "
                     f"z={pos.entry_z:.2f} notional=${pos.notional:.0f}")
        return pos

    def on_exit_filled(
        self,
        pair_id: str,
        fill_price_a: float,
        fill_price_b: float,
        reason: str,
    ) -> Optional[PairPosition]:
        """Record a pair position closure."""
        if pair_id not in self.open_positions:
            return None

        pos = self.open_positions[pair_id]
        pos.exit_time = datetime.now()
        pos.exit_price_a = fill_price_a
        pos.exit_price_b = fill_price_b
        pos.update_pnl(fill_price_a, fill_price_b)
        pos.status = PairPositionStatus.CLOSED

        # Track daily PnL
        self.daily_pnl += pos.pnl

        # Move to closed
        self.closed_positions.append(pos)
        del self.open_positions[pair_id]

        logger.info(f"Closed pair {pos.pair_id} reason={reason} "
                     f"PnL=${pos.pnl:.2f} ({pos.pnl_pct:.2%}) "
                     f"held {pos.holding_days}d")
        return pos

    # --------------------------------------------------------
    # PORTFOLIO METRICS
    # --------------------------------------------------------

    def get_portfolio_metrics(self) -> Dict:
        """Current portfolio state and metrics."""
        open_pnl = sum(p.pnl for p in self.open_positions.values() if p.is_open)
        closed_pnl = sum(p.pnl for p in self.closed_positions)
        total_notional = sum(p.notional for p in self.open_positions.values() if p.is_open)

        # Net exposure (should be near zero for market neutral)
        net_long = sum(
            p.quantity_a * p.entry_price_a + p.quantity_b * p.entry_price_b
            for p in self.open_positions.values() if p.is_open
        )

        # Win rate
        closed = self.closed_positions
        wins = [p for p in closed if p.pnl > 0]
        win_rate = len(wins) / len(closed) if closed else 0

        # Avg holding
        avg_holding = (
            np.mean([p.holding_days for p in closed]) if closed else 0
        )

        return {
            "open_pairs": len(self.open_positions),
            "total_closed": len(self.closed_positions),
            "open_pnl": round(open_pnl, 2),
            "closed_pnl": round(closed_pnl, 2),
            "total_pnl": round(open_pnl + closed_pnl, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "total_notional": round(total_notional, 2),
            "net_exposure": round(net_long, 2),
            "win_rate": round(win_rate, 4),
            "avg_holding_days": round(avg_holding, 1),
            "active_pairs": len(self.active_pairs),
            "last_scan": self.last_scan_time.isoformat() if self.last_scan_time else None,
        }

    # --------------------------------------------------------
    # STATE PERSISTENCE
    # --------------------------------------------------------

    def save_state(self, path: str = "data/state/stat_arb_state.json") -> None:
        """Save strategy state to JSON."""
        state = {
            "open_positions": {
                pid: pos.to_dict() for pid, pos in self.open_positions.items()
            },
            "closed_count": len(self.closed_positions),
            "daily_pnl": self.daily_pnl,
            "last_scan": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "saved_at": datetime.now().isoformat(),
        }
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp_path, path)

    def reset_daily_pnl(self) -> None:
        """Reset daily PnL counter (called at EOD)."""
        self.daily_pnl = 0.0
        self.daily_pnl_reset_date = datetime.now()
