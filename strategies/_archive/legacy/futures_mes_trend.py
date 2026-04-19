"""
Strategie FUT-003 : MES Micro E-mini S&P 500 Trend Following

EDGE : Momentum time-series sur indices equity (Moskowitz, Ooi & Pedersen 2012).
MES trade 23h/jour = couverture maximale. On utilise des EMAs sur barres 1h
pour capturer les tendances swing de 2-10 jours.

Regles :
- Long : EMA(10) > EMA(30) AND prix > EMA(10) AND VIX < 25
- Short : EMA(10) < EMA(30) AND prix < EMA(10) AND VIX > 12
- Stop : 2 ATR(14) sur chart 1h
- Take Profit : 3 ATR(14) (R/R 1.5:1)
- Instrument : MES (multiplier 5, ~$1400 margin)
- Sizing : 1 contrat MES par signal
- Holding : 2-10 jours (swing)
- ~60-80 trades/an
- Filtre : Pas de nouvelle position vendredi apres 16:00 ET (risque gap weekend)
"""
from abc import ABC, abstractmethod
from datetime import time as dt_time

import pandas as pd

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

EMA_FAST = 10
EMA_SLOW = 30
ATR_PERIOD = 14
STOP_ATR_MULT = 2.0
TARGET_ATR_MULT = 3.0       # 1.5:1 R/R
VIX_MAX_LONG = 25.0         # No long if VIX > 25
VIX_MIN_SHORT = 12.0        # No short if VIX < 12 (too calm)
MAX_CONTRACTS = 1
CAPITAL = 25_000.0

# Friday weekend filter
FRIDAY_CUTOFF = dt_time(16, 0)  # No new positions after 16:00 ET on Friday

# Signal evaluation window (use afternoon for cleaner signals)
SIGNAL_WINDOW_START = dt_time(10, 0)
SIGNAL_WINDOW_END = dt_time(15, 30)


class MESTrendStrategy(BaseStrategy):
    """
    MES Micro E-mini S&P 500 Trend Following.

    Edge: Time-series momentum on equity indices is one of the most robust
    factors in finance (Moskowitz et al. 2012). MES trades 23h/day providing
    maximum coverage. We use EMA crossovers on 1h bars to capture swing
    trends lasting 2-10 days.

    Long when EMA(10) > EMA(30) with price above EMA(10), filtered by VIX < 25.
    Short when EMA(10) < EMA(30) with price below EMA(10), filtered by VIX > 12.
    """

    name = "MES Trend Following"

    def __init__(
        self,
        ema_fast: int = EMA_FAST,
        ema_slow: int = EMA_SLOW,
        atr_period: int = ATR_PERIOD,
        stop_atr_mult: float = STOP_ATR_MULT,
        target_atr_mult: float = TARGET_ATR_MULT,
        vix_max_long: float = VIX_MAX_LONG,
        vix_min_short: float = VIX_MIN_SHORT,
        max_contracts: int = MAX_CONTRACTS,
        capital: float = CAPITAL,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.vix_max_long = vix_max_long
        self.vix_min_short = vix_min_short
        self.max_contracts = max_contracts
        self.capital = capital

    def get_required_tickers(self) -> list[str]:
        """MES for trading, ES/SPY for signal if MES data unavailable, VIX for filter."""
        return ["MES", "ES", "SPY", "VIX"]

    def _compute_atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        """Compute Average True Range on OHLCV DataFrame."""
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

    def _compute_ema(self, series: pd.Series, span: int) -> pd.Series:
        """Compute Exponential Moving Average."""
        return series.ewm(span=span, adjust=False).mean()

    def _is_friday_after_cutoff(self, timestamp: pd.Timestamp) -> bool:
        """Check if timestamp is Friday after 16:00 ET (weekend gap risk)."""
        if hasattr(timestamp, 'dayofweek'):
            is_friday = timestamp.dayofweek == 4
        else:
            is_friday = pd.Timestamp(timestamp).dayofweek == 4

        if not is_friday:
            return False

        ts_time = timestamp.time() if hasattr(timestamp, 'time') else None
        if ts_time is None:
            return False

        return ts_time >= FRIDAY_CUTOFF

    def _get_vix_level(self, data: dict[str, pd.DataFrame]) -> float | None:
        """Extract latest VIX level from data."""
        if "VIX" not in data:
            return None

        df_vix = data["VIX"]
        if df_vix.empty:
            return None

        return float(df_vix["close"].iloc[-1])

    def _get_signal_data(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
        """
        Get the best available price data for signal generation.
        Priority: MES > ES > SPY (all track the same index).
        """
        for ticker in ["MES", "ES", "SPY"]:
            if ticker in data and len(data[ticker]) >= self.ema_slow + 5:
                return data[ticker]
        return None

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate trend-following signals on MES.

        Uses EMA crossover on available intraday data (ideally 1h bars).
        Filters by VIX level and weekend risk.

        data: {ticker: DataFrame with intraday OHLCV bars}
        date: trading date

        Returns list of Signal objects (0 or 1 signal per day).
        """
        signals = []

        # ── Get price data for signal ──
        df = self._get_signal_data(data)
        if df is None:
            return signals

        # ── Minimum bars check ──
        min_bars = self.ema_slow + self.atr_period + 5
        if len(df) < min_bars:
            return signals

        # ── VIX filter ──
        vix_level = self._get_vix_level(data)

        # ── Compute indicators ──
        df = df.copy()
        df["ema_fast"] = self._compute_ema(df["close"], self.ema_fast)
        df["ema_slow"] = self._compute_ema(df["close"], self.ema_slow)
        df["atr"] = self._compute_atr(df, self.atr_period)

        # ── Find signal bars in the evaluation window ──
        signal_bars = df[
            (df.index.time >= SIGNAL_WINDOW_START)
            & (df.index.time <= SIGNAL_WINDOW_END)
        ]

        if signal_bars.empty:
            return signals

        # Use the latest bar in the window for signal evaluation
        latest = signal_bars.iloc[-1]
        latest_ts = signal_bars.index[-1]

        # ── Weekend filter ──
        if self._is_friday_after_cutoff(latest_ts):
            return signals

        price = float(latest["close"])
        ema_fast_val = float(latest["ema_fast"])
        ema_slow_val = float(latest["ema_slow"])
        atr_val = float(latest["atr"])

        if pd.isna(atr_val) or atr_val <= 0:
            return signals
        if pd.isna(ema_fast_val) or pd.isna(ema_slow_val):
            return signals

        # ── Trend detection ──
        ema_bullish = ema_fast_val > ema_slow_val
        price_above_ema = price > ema_fast_val
        ema_bearish = ema_fast_val < ema_slow_val
        price_below_ema = price < ema_fast_val

        direction = None

        # LONG condition
        if ema_bullish and price_above_ema:
            if vix_level is not None and vix_level > self.vix_max_long:
                return signals  # VIX too high for longs
            direction = "LONG"

        # SHORT condition
        elif ema_bearish and price_below_ema:
            if vix_level is not None and vix_level < self.vix_min_short:
                return signals  # VIX too low for shorts (market too calm)
            direction = "SHORT"

        if direction is None:
            return signals

        # ── Stop loss & take profit ──
        stop_distance = self.stop_atr_mult * atr_val
        target_distance = self.target_atr_mult * atr_val

        if direction == "LONG":
            stop_loss = price - stop_distance
            take_profit = price + target_distance
        else:
            stop_loss = price + stop_distance
            take_profit = price - target_distance

        # ── Sizing ──
        n_contracts = min(self.max_contracts, max(1, int(self.capital * 0.2 / MES_MARGIN)))

        # ── Signal ──
        signals.append(Signal(
            action=direction,
            ticker="MES",
            entry_price=price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            timestamp=latest_ts,
            metadata={
                "strategy": self.name,
                "instrument": "MES",
                "multiplier": MES_MULTIPLIER,
                "margin": MES_MARGIN,
                "costs_rt_pct": MES_COSTS_RT_PCT,
                "contracts": n_contracts,
                "ema_fast": round(ema_fast_val, 2),
                "ema_slow": round(ema_slow_val, 2),
                "atr_14": round(atr_val, 2),
                "vix": round(vix_level, 2) if vix_level is not None else None,
                "direction": direction,
                "stop_distance": round(stop_distance, 2),
                "target_distance": round(target_distance, 2),
            },
        ))

        return signals
