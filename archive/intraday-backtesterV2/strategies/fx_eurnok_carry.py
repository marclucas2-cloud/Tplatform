"""
Strategie FX-006 : EUR/NOK Carry Trade

EDGE : Le NOK est correle aux prix du petrole → diversification implicite
en commodites. Le carry trade sur les devises scandinaves est un classique
macro. Decorrelation par rapport aux paires EUR majeures.

Regles :
- Direction carry : si taux Norges Bank > taux BCE → short EUR/NOK (collect NOK carry)
- Filtre momentum : tendance 20 jours doit confirmer la direction carry
- Entree : quand carry + momentum sont alignes
- Stop : 2.5 ATR(14) depuis l'entree
- Take Profit : 4 ATR(14) (R/R 1.6:1)
- Holding : 10-30 jours
- Filtre oil : skip si Brent baisse > 5% en une semaine (risque faiblesse NOK)
- Attendu : ~30-40 trades/an
"""
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import time as dt_time
from typing import Optional


# ── Signal & BaseStrategy (local definitions for standalone use) ─────────

class Signal:
    """Represente un signal de trading."""
    def __init__(
        self,
        action: str,
        ticker: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        timestamp: pd.Timestamp,
        metadata: dict = None,
    ):
        self.action = action
        self.ticker = ticker
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.timestamp = timestamp
        self.metadata = metadata or {}


class BaseStrategy(ABC):
    """Classe abstraite — chaque strategie implemente generate_signals()."""

    name: str = "BaseStrategy"

    @abstractmethod
    def generate_signals(self, data: dict, date) -> list:
        pass

    def get_required_tickers(self) -> list[str]:
        return []


# ── Strategy parameters ─────────────────────────────────────────────────

# Central bank rates (updated manually or via data feed)
# As of 2026-03: Norges Bank ~4.25%, ECB ~3.50%
DEFAULT_NORGES_BANK_RATE = 4.25
DEFAULT_ECB_RATE = 3.50

# Momentum & entry
MOMENTUM_LOOKBACK = 20          # 20-day trend filter
ATR_PERIOD = 14
STOP_ATR_MULT = 2.5             # Stop at 2.5 ATR
TARGET_ATR_MULT = 4.0           # TP at 4 ATR (1.6:1 R/R)

# Oil filter
OIL_DROP_THRESHOLD = -0.05      # Skip if oil drops > 5% in a week
OIL_LOOKBACK_DAYS = 5           # 5 trading days = 1 week

# Data requirements
MIN_BARS = 30                   # Minimum bars for ATR + momentum calc
CAPITAL = 25_000.0
MAX_POSITION_PCT = 0.10         # 10% of capital max

# Signal evaluation window (daily check, mid-session)
SIGNAL_WINDOW_START = dt_time(10, 0)
SIGNAL_WINDOW_END = dt_time(15, 0)


class EURNOKCarryStrategy(BaseStrategy):
    """
    EUR/NOK Carry Trade.

    Edge: NOK is correlated to oil prices providing implicit commodity
    diversification. Carry trade on Scandinavian currencies is a classic
    macro play, decoupled from EUR major pairs.

    Direction: if Norges Bank rate > ECB rate → short EUR/NOK (collect carry).
    Entry: carry + 20-day momentum must align.
    Filter: skip if Brent drops > 5% in a week (NOK weakness risk).
    """

    name = "EUR/NOK Carry"

    def __init__(
        self,
        norges_rate: float = DEFAULT_NORGES_BANK_RATE,
        ecb_rate: float = DEFAULT_ECB_RATE,
        momentum_lookback: int = MOMENTUM_LOOKBACK,
        atr_period: int = ATR_PERIOD,
        stop_atr_mult: float = STOP_ATR_MULT,
        target_atr_mult: float = TARGET_ATR_MULT,
        oil_drop_threshold: float = OIL_DROP_THRESHOLD,
        oil_lookback_days: int = OIL_LOOKBACK_DAYS,
        capital: float = CAPITAL,
        max_position_pct: float = MAX_POSITION_PCT,
    ):
        self.norges_rate = norges_rate
        self.ecb_rate = ecb_rate
        self.momentum_lookback = momentum_lookback
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.oil_drop_threshold = oil_drop_threshold
        self.oil_lookback_days = oil_lookback_days
        self.capital = capital
        self.max_position_pct = max_position_pct

    def get_required_tickers(self) -> list[str]:
        """EUR/NOK pair + oil proxy for filter."""
        return ["EURNOK", "FXE", "USO", "BNO", "CL"]

    def _compute_atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        """Compute Average True Range."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)

        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        return tr.rolling(period, min_periods=max(1, period // 2)).mean()

    def _get_carry_direction(self) -> Optional[str]:
        """Determine carry trade direction based on rate differential.

        If Norges Bank rate > ECB rate → short EUR/NOK (collect NOK carry).
        If ECB rate > Norges Bank rate → long EUR/NOK (collect EUR carry).

        Returns:
            "SHORT" if Norges > ECB, "LONG" if ECB > Norges, None if equal.
        """
        rate_diff = self.norges_rate - self.ecb_rate

        if rate_diff > 0.25:  # Meaningful carry differential
            return "SHORT"  # Short EUR/NOK = long NOK = collect NOK carry
        elif rate_diff < -0.25:
            return "LONG"   # Long EUR/NOK = long EUR = collect EUR carry
        return None

    def _check_momentum_alignment(
        self,
        df: pd.DataFrame,
        carry_direction: str,
    ) -> bool:
        """Check if 20-day momentum agrees with carry direction.

        For SHORT EUR/NOK: price should be trending down (NOK strengthening).
        For LONG EUR/NOK: price should be trending up (EUR strengthening).
        """
        if len(df) < self.momentum_lookback:
            return False

        closes = df["close"]
        lookback_price = float(closes.iloc[-self.momentum_lookback])
        current_price = float(closes.iloc[-1])

        momentum = (current_price - lookback_price) / lookback_price

        if carry_direction == "SHORT":
            return momentum < 0  # EUR/NOK trending down = NOK strength
        elif carry_direction == "LONG":
            return momentum > 0  # EUR/NOK trending up = EUR strength
        return False

    def _check_oil_filter(self, data: dict[str, pd.DataFrame]) -> bool:
        """Check if oil has dropped > 5% in the last week.

        If oil drops sharply, NOK tends to weaken → dangerous for carry.

        Returns:
            True if oil is OK (no major drop), False if oil filter triggers.
        """
        # Try multiple oil proxies
        for oil_ticker in ["CL", "BNO", "USO"]:
            if oil_ticker not in data:
                continue

            df = data[oil_ticker]
            if len(df) < self.oil_lookback_days + 1:
                continue

            closes = df["close"]
            lookback_price = float(closes.iloc[-self.oil_lookback_days - 1])
            current_price = float(closes.iloc[-1])

            if lookback_price > 0:
                oil_change = (current_price - lookback_price) / lookback_price
                if oil_change < self.oil_drop_threshold:
                    return False  # Oil dropped too much — filter active

            return True  # Oil is fine

        # No oil data available — pass the filter (no info = no block)
        return True

    def _get_eurnok_data(
        self,
        data: dict[str, pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        """Get EUR/NOK price data.

        Priority: EURNOK direct > synthetic from FXE/other proxies.
        """
        if "EURNOK" in data and len(data["EURNOK"]) >= MIN_BARS:
            return data["EURNOK"]

        # Fallback: use FXE (EUR ETF) as a rough proxy
        # In real trading, use IBKR FX data
        if "FXE" in data and len(data["FXE"]) >= MIN_BARS:
            return data["FXE"]

        return None

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate EUR/NOK carry trade signals.

        Entry conditions:
        1. Carry direction determined by rate differential
        2. 20-day momentum must confirm carry direction
        3. Oil must not have dropped > 5% in a week

        data: {ticker: DataFrame with OHLCV bars}
        date: trading date

        Returns list of Signal objects (0 or 1 signal per day).
        """
        signals = []

        # ── Step 1: Determine carry direction ──
        carry_direction = self._get_carry_direction()
        if carry_direction is None:
            return signals

        # ── Step 2: Get EUR/NOK data ──
        eurnok_df = self._get_eurnok_data(data)
        if eurnok_df is None:
            return signals

        # ── Step 3: Check oil filter ──
        if not self._check_oil_filter(data):
            return signals  # Oil dropped too much — skip

        # ── Step 4: Check momentum alignment ──
        if not self._check_momentum_alignment(eurnok_df, carry_direction):
            return signals  # Momentum doesn't confirm carry

        # ── Step 5: Find signal bar in evaluation window ──
        signal_bars = eurnok_df[
            (eurnok_df.index.time >= SIGNAL_WINDOW_START)
            & (eurnok_df.index.time <= SIGNAL_WINDOW_END)
        ]

        if signal_bars.empty:
            return signals

        latest_bar = signal_bars.iloc[-1]
        latest_ts = signal_bars.index[-1]
        entry_price = float(latest_bar["close"])

        # ── Step 6: Compute ATR for stops ──
        atr = self._compute_atr(eurnok_df, self.atr_period)
        latest_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0

        if latest_atr <= 0:
            return signals

        # ── Step 7: Calculate SL/TP ──
        stop_distance = self.stop_atr_mult * latest_atr
        target_distance = self.target_atr_mult * latest_atr

        if carry_direction == "SHORT":
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - target_distance
        else:
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + target_distance

        # ── Step 8: Rate differential for metadata ──
        rate_diff = self.norges_rate - self.ecb_rate
        daily_carry = abs(rate_diff) / 365.0 * entry_price  # Approx daily carry in pips

        # ── Momentum ──
        closes = eurnok_df["close"]
        lookback_price = float(closes.iloc[-self.momentum_lookback])
        momentum = (entry_price - lookback_price) / lookback_price

        signals.append(Signal(
            action=carry_direction,
            ticker="EURNOK",
            entry_price=round(entry_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5),
            timestamp=latest_ts,
            metadata={
                "strategy": self.name,
                "signal_type": "carry_momentum",
                "carry_direction": carry_direction,
                "norges_rate": self.norges_rate,
                "ecb_rate": self.ecb_rate,
                "rate_differential": round(rate_diff, 2),
                "daily_carry_approx": round(daily_carry, 6),
                "momentum_20d": round(momentum * 100, 2),
                "atr_14": round(latest_atr, 5),
                "stop_distance": round(stop_distance, 5),
                "target_distance": round(target_distance, 5),
                "rr_ratio": round(target_distance / stop_distance, 2) if stop_distance > 0 else 0,
                "market": "fx",
                "holding_days_expected": "10-30",
            },
        ))

        return signals

    def update_rates(self, norges_rate: float, ecb_rate: float):
        """Update central bank rates (call when rates change)."""
        self.norges_rate = norges_rate
        self.ecb_rate = ecb_rate
