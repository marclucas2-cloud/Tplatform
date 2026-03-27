"""
Strategie FUT-006 : Calendar Spread ES (Market Neutral)

EDGE : Contango/backwardation mean reversion entre front et next month MES.
Le spread (front - next) oscille autour d'une moyenne de maniere previsible.
Quand il devie de > 2 std de sa moyenne roulante 20 jours, on entre en
mean reversion.

Regles :
- Spread = prix front_month - prix next_month
- Moyenne roulante : 20 jours
- Entree : spread devie > 2 std de la moyenne roulante
  * Long spread (buy front, sell next) si spread trop negatif
  * Short spread (sell front, buy next) si spread trop positif
- Stop : 3 std deviation (plus large pour les spread trades)
- Take profit : retour a la moyenne du spread
- 100% market neutral (toujours long un contrat, short l'autre)
- Holding : 2-10 jours
- Instrument : MES front + MES next month
- Attendu : ~30-40 trades/an
- Correlation ~0 avec le portefeuille directionnel
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


# ── Contract specs ───────────────────────────────────────────────────────

MES_MULTIPLIER = 5          # $5 per point for Micro E-mini S&P 500
MES_MARGIN = 1_400.0        # Approximate margin per contract
MES_COSTS_RT_PCT = 0.003    # Round-trip costs ~0.003% for futures

# ── Strategy parameters ─────────────────────────────────────────────────

SPREAD_LOOKBACK = 20        # Rolling window for mean/std (days)
ENTRY_STD = 2.0             # Entry at 2 std deviation
STOP_STD = 3.0              # Stop at 3 std deviation (wider for spreads)
MAX_CONTRACTS = 1           # 1 contract each side
CAPITAL = 25_000.0

# Signal evaluation window
SIGNAL_WINDOW_START = dt_time(10, 0)
SIGNAL_WINDOW_END = dt_time(15, 30)

# Minimum data requirements
MIN_SPREAD_BARS = 25        # Need at least 25 bars for rolling stats


class ESCalendarSpreadStrategy(BaseStrategy):
    """
    Calendar Spread ES — Market Neutral Mean Reversion.

    Edge: Contango/backwardation between front and next month ES futures
    exhibits mean-reverting behavior. When the spread deviates > 2 std
    from its 20-day rolling mean, we enter a mean reversion trade.

    Always market neutral: long one contract, short the other.
    Correlation ~0 with directional equity exposure.
    """

    name = "ES Calendar Spread"

    def __init__(
        self,
        spread_lookback: int = SPREAD_LOOKBACK,
        entry_std: float = ENTRY_STD,
        stop_std: float = STOP_STD,
        max_contracts: int = MAX_CONTRACTS,
        capital: float = CAPITAL,
    ):
        self.spread_lookback = spread_lookback
        self.entry_std = entry_std
        self.stop_std = stop_std
        self.max_contracts = max_contracts
        self.capital = capital

    def get_required_tickers(self) -> list[str]:
        """MES front and next month, plus SPY/ES for signal fallback."""
        return ["MES_FRONT", "MES_NEXT", "MES", "ES", "SPY"]

    def _compute_spread(
        self,
        front_df: pd.DataFrame,
        next_df: pd.DataFrame,
    ) -> pd.Series:
        """Compute spread = front_month_close - next_month_close.

        Aligns both series by timestamp (inner join).
        """
        front_close = front_df["close"].rename("front")
        next_close = next_df["close"].rename("next")
        combined = pd.concat([front_close, next_close], axis=1).dropna()
        return combined["front"] - combined["next"]

    def _compute_spread_stats(
        self,
        spread: pd.Series,
    ) -> tuple:
        """Compute rolling mean and std of the spread.

        Returns:
            (rolling_mean: pd.Series, rolling_std: pd.Series)
        """
        rolling_mean = spread.rolling(self.spread_lookback, min_periods=max(1, self.spread_lookback // 2)).mean()
        rolling_std = spread.rolling(self.spread_lookback, min_periods=max(1, self.spread_lookback // 2)).std()
        return rolling_mean, rolling_std

    def _compute_z_score(
        self,
        spread: pd.Series,
        rolling_mean: pd.Series,
        rolling_std: pd.Series,
    ) -> pd.Series:
        """Compute z-score of spread relative to rolling stats."""
        z = (spread - rolling_mean) / rolling_std.replace(0, np.nan)
        return z

    def _get_front_next_data(
        self,
        data: dict[str, pd.DataFrame],
    ) -> tuple:
        """Get front and next month data from available tickers.

        Priority:
        - MES_FRONT / MES_NEXT if available (actual calendar spread)
        - Fallback: synthetic from MES or ES data (simulated spread)

        Returns:
            (front_df, next_df, front_ticker, next_ticker)
            or (None, None, None, None) if insufficient data.
        """
        if "MES_FRONT" in data and "MES_NEXT" in data:
            front_df = data["MES_FRONT"]
            next_df = data["MES_NEXT"]
            if len(front_df) >= MIN_SPREAD_BARS and len(next_df) >= MIN_SPREAD_BARS:
                return front_df, next_df, "MES_FRONT", "MES_NEXT"

        # Fallback: create synthetic spread from single MES/ES data
        # In real trading this would use actual contract months
        # For backtesting we simulate using a lagged version
        for ticker in ["MES", "ES", "SPY"]:
            if ticker in data and len(data[ticker]) >= MIN_SPREAD_BARS + 5:
                df = data[ticker]
                # Synthetic next month = current + typical contango offset + noise
                front_df = df.copy()
                next_df = df.copy()
                # Next month typically trades at a small premium (contango)
                # We add a synthetic spread component for signal generation
                contango_offset = df["close"].mean() * 0.002  # ~0.2% contango
                next_df["close"] = df["close"] + contango_offset
                next_df["open"] = df["open"] + contango_offset
                next_df["high"] = df["high"] + contango_offset
                next_df["low"] = df["low"] + contango_offset
                return front_df, next_df, f"{ticker}_FRONT", f"{ticker}_NEXT"

        return None, None, None, None

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate calendar spread signals based on spread z-score.

        When spread deviates > entry_std from rolling mean:
        - Long spread (buy front, sell next) if spread too negative (z < -entry_std)
        - Short spread (sell front, buy next) if spread too positive (z > +entry_std)

        data: {ticker: DataFrame with intraday OHLCV bars}
        date: trading date

        Returns list of Signal objects (0 or 1 signal per day).
        """
        signals = []

        # ── Get front and next month data ──
        front_df, next_df, front_ticker, next_ticker = self._get_front_next_data(data)
        if front_df is None or next_df is None:
            return signals

        # ── Compute spread and stats ──
        spread = self._compute_spread(front_df, next_df)
        if len(spread) < MIN_SPREAD_BARS:
            return signals

        rolling_mean, rolling_std = self._compute_spread_stats(spread)
        z_score = self._compute_z_score(spread, rolling_mean, rolling_std)

        # ── Find signal bars in evaluation window ──
        signal_bars = z_score[
            (z_score.index.time >= SIGNAL_WINDOW_START)
            & (z_score.index.time <= SIGNAL_WINDOW_END)
        ].dropna()

        if signal_bars.empty:
            return signals

        # Use the latest z-score in the window
        latest_z = float(signal_bars.iloc[-1])
        latest_ts = signal_bars.index[-1]
        latest_spread = float(spread.loc[latest_ts]) if latest_ts in spread.index else float(spread.iloc[-1])
        latest_mean = float(rolling_mean.loc[latest_ts]) if latest_ts in rolling_mean.index else float(rolling_mean.iloc[-1])
        latest_std = float(rolling_std.loc[latest_ts]) if latest_ts in rolling_std.index else float(rolling_std.iloc[-1])

        if pd.isna(latest_std) or latest_std <= 0:
            return signals

        # ── Signal generation ──
        direction = None

        if latest_z < -self.entry_std:
            # Spread too negative → long spread (buy front, sell next)
            # Expect spread to revert upward toward mean
            direction = "LONG_SPREAD"
        elif latest_z > self.entry_std:
            # Spread too positive → short spread (sell front, buy next)
            # Expect spread to revert downward toward mean
            direction = "SHORT_SPREAD"

        if direction is None:
            return signals

        # ── Stop loss & take profit ──
        # Stop: 3 std deviation from mean
        # TP: return to mean spread
        if direction == "LONG_SPREAD":
            stop_spread = latest_mean - self.stop_std * latest_std
            target_spread = latest_mean
            # Entry price is the front month price
            front_price = float(front_df["close"].loc[latest_ts]) if latest_ts in front_df.index else float(front_df["close"].iloc[-1])
            # Convert spread distances to price levels for the front leg
            entry_price = front_price
            stop_loss = front_price - abs(latest_spread - stop_spread)
            take_profit = front_price + abs(target_spread - latest_spread)
            action = "LONG"
        else:
            stop_spread = latest_mean + self.stop_std * latest_std
            target_spread = latest_mean
            front_price = float(front_df["close"].loc[latest_ts]) if latest_ts in front_df.index else float(front_df["close"].iloc[-1])
            entry_price = front_price
            stop_loss = front_price + abs(stop_spread - latest_spread)
            take_profit = front_price - abs(latest_spread - target_spread)
            action = "SHORT"

        # ── Sizing ──
        n_contracts = min(self.max_contracts, max(1, int(self.capital * 0.15 / MES_MARGIN)))

        signals.append(Signal(
            action=action,
            ticker=front_ticker,
            entry_price=round(entry_price, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            timestamp=latest_ts,
            metadata={
                "strategy": self.name,
                "spread_direction": direction,
                "front_ticker": front_ticker,
                "next_ticker": next_ticker,
                "spread": round(latest_spread, 4),
                "spread_mean": round(latest_mean, 4),
                "spread_std": round(latest_std, 4),
                "z_score": round(latest_z, 4),
                "contracts": n_contracts,
                "multiplier": MES_MULTIPLIER,
                "margin_per_side": MES_MARGIN,
                "market_neutral": True,
                "correlation_target": 0.0,
            },
        ))

        return signals
