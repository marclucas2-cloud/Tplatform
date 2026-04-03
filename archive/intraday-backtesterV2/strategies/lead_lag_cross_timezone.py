"""
Strategie STRAT-010 : Lead-Lag Cross-Timezone Systematic

EDGE : L'information circule entre les fuseaux horaires avec des lags
previsibles. Les marches reagissent aux mouvements des marches qui
ferment avant eux.

Sources de signaux :
1. US close → EU open : SPY cloture > +1% → long EU ETFs a l'ouverture (continuation)
2. EU close → US afternoon : forte cloture EU predit le drift US afternoon
3. VIX spike → DAX : spike VIX en session US → baisse DAX a l'ouverture EU

Pour chaque relation de lag :
- Signal : mesurer le mouvement du leader a la cloture
- Entree : ouverture du marche suiveur
- Sortie : cloture du meme jour du suiveur (intraday only)
- Stop : 1.5% depuis l'entree
- Take profit : 2% depuis l'entree
- Filtre : seulement quand le mouvement leader > 1 std du range journalier
- Attendu : ~100-150 trades/an sur toutes les relations
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

# Thresholds for leader moves
SPY_STRONG_MOVE = 0.01            # 1% SPY move = strong signal
VIX_SPIKE_PCT = 0.10              # 10% VIX intraday spike
EU_STRONG_MOVE = 0.008            # 0.8% EU move = strong signal
DXY_STRONG_MOVE = 0.005           # 0.5% DXY move

# Standard deviation filter
STD_FILTER_LOOKBACK = 20          # 20-day lookback for std filter
STD_FILTER_MULT = 1.0             # Leader move must be > 1 std

# Entry/exit
STOP_PCT = 0.015                  # 1.5% stop
TARGET_PCT = 0.020                # 2.0% target

# EU market hours (in ET for Alpaca)
EU_OPEN_ET = dt_time(3, 0)       # ~9:00 CET = 3:00 ET
EU_CLOSE_ET = dt_time(11, 30)    # ~17:30 CET = 11:30 ET
US_OPEN_ET = dt_time(9, 30)
US_CLOSE_ET = dt_time(16, 0)
US_AFTERNOON_ET = dt_time(13, 0)  # Afternoon drift starts ~13:00 ET

# Evaluation windows
SIGNAL_WINDOW_START = dt_time(9, 35)
SIGNAL_WINDOW_END = dt_time(15, 55)

CAPITAL = 50_000.0
MAX_SIGNALS_PER_DAY = 3


class LeadLagCrossTimezoneStrategy(BaseStrategy):
    """
    Lead-Lag Cross-Timezone Systematic.

    Edge: Information flows across timezones with predictable lags.
    Captures continuation and reversal effects between US, EU, and
    cross-asset markets.

    Three primary relationships:
    1. US close → EU/US next day (SPY > +1% → long continuation)
    2. EU close → US afternoon drift (strong EU close → US drift)
    3. VIX spike → short EU proxies (risk aversion propagation)

    Plus DXY → commodity inverse relationship.
    """

    name = "Lead-Lag Cross-Timezone"

    def __init__(
        self,
        spy_threshold: float = SPY_STRONG_MOVE,
        vix_spike_pct: float = VIX_SPIKE_PCT,
        eu_threshold: float = EU_STRONG_MOVE,
        dxy_threshold: float = DXY_STRONG_MOVE,
        std_lookback: int = STD_FILTER_LOOKBACK,
        std_mult: float = STD_FILTER_MULT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        capital: float = CAPITAL,
    ):
        self.spy_threshold = spy_threshold
        self.vix_spike_pct = vix_spike_pct
        self.eu_threshold = eu_threshold
        self.dxy_threshold = dxy_threshold
        self.std_lookback = std_lookback
        self.std_mult = std_mult
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.capital = capital

    def get_required_tickers(self) -> list[str]:
        """All tickers needed for cross-timezone signals."""
        return [
            "SPY", "QQQ",           # US leaders
            "EWG", "EWQ", "FEZ",   # EU followers (Germany, France, Eurozone)
            "VIX",                   # Volatility
            "UUP",                   # Dollar index proxy
            "GLD", "USO", "DBA",    # Commodities (followers of DXY)
        ]

    def _compute_daily_move(
        self,
        df: pd.DataFrame,
        start_time: dt_time = None,
        end_time: dt_time = None,
    ) -> Optional[float]:
        """Compute return from start_time to end_time on current day's data.

        If times are not provided, uses full-day open-to-close.
        """
        if df.empty or len(df) < 2:
            return None

        if start_time is not None and end_time is not None:
            window = df[(df.index.time >= start_time) & (df.index.time <= end_time)]
        else:
            window = df

        if len(window) < 2:
            return None

        start_price = float(window.iloc[0]["open"])
        end_price = float(window.iloc[-1]["close"])

        if start_price <= 0:
            return None

        return (end_price - start_price) / start_price

    def _compute_rolling_std(
        self,
        df: pd.DataFrame,
        lookback: int,
    ) -> float:
        """Compute rolling standard deviation of daily returns."""
        if len(df) < lookback + 1:
            return 0.01  # Default 1% std

        closes = df["close"]
        returns = closes.pct_change().dropna()
        if len(returns) < lookback:
            return 0.01

        return float(returns.tail(lookback).std())

    def _passes_std_filter(
        self,
        move: float,
        data: dict[str, pd.DataFrame],
        ticker: str,
    ) -> bool:
        """Check if leader move exceeds 1 std of its daily range.

        Only trade when the leader move is significantly above normal.
        """
        if ticker not in data:
            return abs(move) > 0.005  # Fallback: 0.5% minimum

        rolling_std = self._compute_rolling_std(data[ticker], self.std_lookback)
        return abs(move) > rolling_std * self.std_mult

    def _generate_us_to_eu_signals(
        self,
        data: dict[str, pd.DataFrame],
        date,
    ) -> list[Signal]:
        """Signal 1: US close → EU open continuation.

        Strong US session (SPY > +1%) → long EU ETFs at US open
        (proxy for EU open continuation effect).
        """
        signals = []

        if "SPY" not in data:
            return signals

        spy_df = data["SPY"]

        # Use morning SPY data as proxy for "previous close" effect
        # In real trading, we'd use previous day's close
        morning_move = self._compute_daily_move(
            spy_df, dt_time(9, 30), dt_time(11, 0)
        )

        if morning_move is None:
            return signals

        if abs(morning_move) < self.spy_threshold:
            return signals

        if not self._passes_std_filter(morning_move, data, "SPY"):
            return signals

        # Trade EU proxies after observing US morning strength/weakness
        eu_tickers = ["EWG", "EWQ", "FEZ"]
        entry_time_start = dt_time(11, 0)
        entry_time_end = dt_time(11, 30)

        for eu_ticker in eu_tickers:
            if eu_ticker not in data:
                continue

            eu_df = data[eu_ticker]
            entry_bars = eu_df[
                (eu_df.index.time >= entry_time_start)
                & (eu_df.index.time <= entry_time_end)
            ]

            if entry_bars.empty:
                continue

            entry_bar = entry_bars.iloc[0]
            entry_ts = entry_bars.index[0]
            entry_price = float(entry_bar["close"])

            # Direction: continuation of US move
            action = "LONG" if morning_move > 0 else "SHORT"

            if action == "LONG":
                stop_loss = entry_price * (1 - self.stop_pct)
                take_profit = entry_price * (1 + self.target_pct)
            else:
                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action=action,
                ticker=eu_ticker,
                entry_price=round(entry_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "signal_type": "us_to_eu_continuation",
                    "leader": "SPY",
                    "leader_move_pct": round(morning_move * 100, 2),
                    "follower": eu_ticker,
                    "lag_relationship": "US_close→EU_open",
                },
            ))

        return signals

    def _generate_eu_to_us_afternoon_signals(
        self,
        data: dict[str, pd.DataFrame],
        date,
    ) -> list[Signal]:
        """Signal 2: EU close → US afternoon drift.

        Strong EU morning predicts US afternoon drift direction.
        """
        signals = []

        # Use EU proxy ETFs morning performance
        eu_tickers = ["EWG", "EWQ", "FEZ"]
        eu_moves = []

        for eu_ticker in eu_tickers:
            if eu_ticker not in data:
                continue
            eu_df = data[eu_ticker]
            move = self._compute_daily_move(
                eu_df, dt_time(9, 30), dt_time(12, 0)
            )
            if move is not None:
                eu_moves.append(move)

        if not eu_moves:
            return signals

        avg_eu_move = np.mean(eu_moves)

        if abs(avg_eu_move) < self.eu_threshold:
            return signals

        # Check std filter on at least one EU ticker
        eu_check_ticker = next(
            (t for t in eu_tickers if t in data), None
        )
        if eu_check_ticker and not self._passes_std_filter(
            avg_eu_move, data, eu_check_ticker
        ):
            return signals

        # Trade SPY/QQQ in the afternoon
        for us_ticker in ["SPY", "QQQ"]:
            if us_ticker not in data:
                continue

            us_df = data[us_ticker]
            entry_bars = us_df[
                (us_df.index.time >= US_AFTERNOON_ET)
                & (us_df.index.time <= dt_time(13, 30))
            ]

            if entry_bars.empty:
                continue

            entry_bar = entry_bars.iloc[0]
            entry_ts = entry_bars.index[0]
            entry_price = float(entry_bar["close"])

            action = "LONG" if avg_eu_move > 0 else "SHORT"

            if action == "LONG":
                stop_loss = entry_price * (1 - self.stop_pct)
                take_profit = entry_price * (1 + self.target_pct)
            else:
                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action=action,
                ticker=us_ticker,
                entry_price=round(entry_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "signal_type": "eu_to_us_afternoon",
                    "leader": "EU_aggregate",
                    "leader_move_pct": round(avg_eu_move * 100, 2),
                    "follower": us_ticker,
                    "lag_relationship": "EU_close→US_afternoon",
                },
            ))

        return signals

    def _generate_vix_to_eu_signals(
        self,
        data: dict[str, pd.DataFrame],
        date,
    ) -> list[Signal]:
        """Signal 3: VIX spike → short EU proxies.

        A VIX spike during US session predicts EU weakness.
        """
        signals = []

        if "VIX" not in data:
            return signals

        vix_df = data["VIX"]

        # Check for VIX spike in the morning session
        vix_move = self._compute_daily_move(
            vix_df, dt_time(9, 30), dt_time(11, 0)
        )

        if vix_move is None:
            return signals

        if vix_move < self.vix_spike_pct:
            return signals  # No spike

        # VIX spiked — short EU proxies (propagation effect)
        eu_tickers = ["EWG", "FEZ"]
        entry_time_start = dt_time(11, 30)
        entry_time_end = dt_time(12, 0)

        for eu_ticker in eu_tickers:
            if eu_ticker not in data:
                continue

            eu_df = data[eu_ticker]
            entry_bars = eu_df[
                (eu_df.index.time >= entry_time_start)
                & (eu_df.index.time <= entry_time_end)
            ]

            if entry_bars.empty:
                continue

            entry_bar = entry_bars.iloc[0]
            entry_ts = entry_bars.index[0]
            entry_price = float(entry_bar["close"])

            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action="SHORT",
                ticker=eu_ticker,
                entry_price=round(entry_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "signal_type": "vix_spike_to_eu",
                    "leader": "VIX",
                    "vix_spike_pct": round(vix_move * 100, 2),
                    "follower": eu_ticker,
                    "lag_relationship": "VIX_spike→EU_drop",
                },
            ))

        return signals

    def _generate_dxy_to_commodities_signals(
        self,
        data: dict[str, pd.DataFrame],
        date,
    ) -> list[Signal]:
        """Signal 4: DXY move → commodity inverse relationship.

        Dollar strength → commodity weakness (and vice versa).
        """
        signals = []

        if "UUP" not in data:
            return signals

        uup_df = data["UUP"]
        dxy_move = self._compute_daily_move(
            uup_df, dt_time(9, 30), dt_time(12, 0)
        )

        if dxy_move is None:
            return signals

        if abs(dxy_move) < self.dxy_threshold:
            return signals

        if not self._passes_std_filter(dxy_move, data, "UUP"):
            return signals

        # DXY up → short commodities, DXY down → long commodities
        commodity_tickers = ["GLD", "USO", "DBA"]
        entry_time_start = dt_time(12, 30)
        entry_time_end = dt_time(13, 0)

        for comm_ticker in commodity_tickers:
            if comm_ticker not in data:
                continue

            comm_df = data[comm_ticker]
            entry_bars = comm_df[
                (comm_df.index.time >= entry_time_start)
                & (comm_df.index.time <= entry_time_end)
            ]

            if entry_bars.empty:
                continue

            entry_bar = entry_bars.iloc[0]
            entry_ts = entry_bars.index[0]
            entry_price = float(entry_bar["close"])

            # Inverse: DXY up → short commodities
            action = "SHORT" if dxy_move > 0 else "LONG"

            if action == "LONG":
                stop_loss = entry_price * (1 - self.stop_pct)
                take_profit = entry_price * (1 + self.target_pct)
            else:
                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action=action,
                ticker=comm_ticker,
                entry_price=round(entry_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "signal_type": "dxy_to_commodity",
                    "leader": "UUP",
                    "dxy_move_pct": round(dxy_move * 100, 2),
                    "follower": comm_ticker,
                    "lag_relationship": "DXY→Commodity_inverse",
                },
            ))

        return signals

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate cross-timezone lead-lag signals.

        Combines all four lag relationships, caps at MAX_SIGNALS_PER_DAY.

        data: {ticker: DataFrame with intraday OHLCV bars}
        date: trading date

        Returns list of Signal objects.
        """
        all_signals = []

        # Collect signals from all relationships
        all_signals.extend(self._generate_us_to_eu_signals(data, date))
        all_signals.extend(self._generate_eu_to_us_afternoon_signals(data, date))
        all_signals.extend(self._generate_vix_to_eu_signals(data, date))
        all_signals.extend(self._generate_dxy_to_commodities_signals(data, date))

        # Cap to max signals per day
        return all_signals[:MAX_SIGNALS_PER_DAY]
