"""
Strategie FUT-004 : MNQ Micro E-mini Nasdaq Mean Reversion (EXTREME moves only)

EDGE : Le Nasdaq a ~1.3x la volatilite du SPX. Les overshoots sur timeframe 1h
sont plus frequents et plus profonds que sur le SPX. Les market makers restaurent
l'ordre → mean reversion. Ce n'est PAS du mean reversion 5-min (mort). C'est de la
reversion d'extremes sur un timeframe plus long (1h).

Regles :
- Signal : prix devie > 2 ATR(14) de la moyenne 20 periodes sur barres 1h
- Long si deviation negative (oversold), Short si positive (overbought)
- Stop : 1.5 ATR au-dela de l'entree (au-dela de l'extreme)
- Take Profit : retour a la moyenne 20 periodes
- Instrument : MNQ (multiplier 2, ~$1800 margin)
- Sizing : 1 contrat MNQ par signal
- Holding : 2 heures a 2 jours (exit force apres 2 jours)
- ~50-70 trades/an
- Filtre : pas de trade si VIX > 35 (chaos, pas de mean reversion)
- Filtre : pas de trade dans l'heure precedant FOMC/CPI/NFP
"""
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import time as dt_time, date as dt_date, datetime, timedelta
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

MNQ_MULTIPLIER = 2          # $2 per point for Micro E-mini Nasdaq
MNQ_MARGIN = 1_800.0        # Approximate margin per contract
MNQ_COSTS_RT_PCT = 0.003    # Round-trip costs ~0.003% for futures

# ── Strategy parameters ─────────────────────────────────────────────────

MEAN_PERIOD = 20             # 20-bar mean on 1h chart
ATR_PERIOD = 14
DEVIATION_ATR_MULT = 2.0    # Entry: price > 2 ATR from mean
STOP_ATR_MULT = 1.5         # Stop: 1.5 ATR beyond entry
VIX_MAX = 35.0              # Skip if VIX > 35 (chaos)
MAX_CONTRACTS = 1
CAPITAL = 25_000.0
MAX_HOLD_BARS = 48          # Force exit after ~2 days (48 x 1h bars)

# Signal evaluation window (avoid first 30 min and last 30 min)
SIGNAL_WINDOW_START = dt_time(10, 0)
SIGNAL_WINDOW_END = dt_time(15, 30)

# ── FOMC/CPI/NFP calendar ───────────────────────────────────────────────
# Major macro event dates for 2026 (times in ET)
# FOMC decisions: typically 14:00 ET
# CPI releases: typically 08:30 ET
# NFP releases: typically 08:30 ET (first Friday of month)
# Updated annually — these are the 2026 dates

MACRO_EVENTS_2026 = {
    # FOMC decision dates (announcement at 14:00 ET)
    dt_date(2026, 1, 28): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 3, 18): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 5, 6): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 6, 17): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 7, 29): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 9, 16): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 11, 4): ("FOMC", dt_time(14, 0)),
    dt_date(2026, 12, 16): ("FOMC", dt_time(14, 0)),
    # CPI releases (08:30 ET)
    dt_date(2026, 1, 14): ("CPI", dt_time(8, 30)),
    dt_date(2026, 2, 12): ("CPI", dt_time(8, 30)),
    dt_date(2026, 3, 11): ("CPI", dt_time(8, 30)),
    dt_date(2026, 4, 14): ("CPI", dt_time(8, 30)),
    dt_date(2026, 5, 12): ("CPI", dt_time(8, 30)),
    dt_date(2026, 6, 10): ("CPI", dt_time(8, 30)),
    dt_date(2026, 7, 15): ("CPI", dt_time(8, 30)),
    dt_date(2026, 8, 12): ("CPI", dt_time(8, 30)),
    dt_date(2026, 9, 11): ("CPI", dt_time(8, 30)),
    dt_date(2026, 10, 13): ("CPI", dt_time(8, 30)),
    dt_date(2026, 11, 12): ("CPI", dt_time(8, 30)),
    dt_date(2026, 12, 10): ("CPI", dt_time(8, 30)),
    # NFP releases (first Friday, 08:30 ET)
    dt_date(2026, 1, 2): ("NFP", dt_time(8, 30)),
    dt_date(2026, 2, 6): ("NFP", dt_time(8, 30)),
    dt_date(2026, 3, 6): ("NFP", dt_time(8, 30)),
    dt_date(2026, 4, 3): ("NFP", dt_time(8, 30)),
    dt_date(2026, 5, 1): ("NFP", dt_time(8, 30)),
    dt_date(2026, 6, 5): ("NFP", dt_time(8, 30)),
    dt_date(2026, 7, 2): ("NFP", dt_time(8, 30)),
    dt_date(2026, 8, 7): ("NFP", dt_time(8, 30)),
    dt_date(2026, 9, 4): ("NFP", dt_time(8, 30)),
    dt_date(2026, 10, 2): ("NFP", dt_time(8, 30)),
    dt_date(2026, 11, 6): ("NFP", dt_time(8, 30)),
    dt_date(2026, 12, 4): ("NFP", dt_time(8, 30)),
}


def is_near_macro_event(trade_date, trade_time: dt_time, buffer_minutes: int = 60) -> bool:
    """
    Check if a given datetime is within buffer_minutes of a known macro event.

    Args:
        trade_date: date object or similar
        trade_time: time of proposed trade
        buffer_minutes: minutes before/after event to block trading

    Returns:
        True if within exclusion zone, False otherwise
    """
    if isinstance(trade_date, pd.Timestamp):
        check_date = trade_date.date()
    elif isinstance(trade_date, datetime):
        check_date = trade_date.date()
    elif isinstance(trade_date, dt_date):
        check_date = trade_date
    else:
        try:
            check_date = pd.Timestamp(trade_date).date()
        except Exception:
            return False

    if check_date not in MACRO_EVENTS_2026:
        return False

    event_name, event_time = MACRO_EVENTS_2026[check_date]

    # Convert to minutes since midnight for easy comparison
    trade_minutes = trade_time.hour * 60 + trade_time.minute
    event_minutes = event_time.hour * 60 + event_time.minute

    return abs(trade_minutes - event_minutes) <= buffer_minutes


class MNQMeanReversionStrategy(BaseStrategy):
    """
    MNQ Micro E-mini Nasdaq Mean Reversion — EXTREME moves only.

    Edge: Nasdaq has ~1.3x SPX volatility. Overshoots on 1h timeframe are
    more frequent and deeper than SPX. Market makers restore order, creating
    reliable mean reversion in extreme moves. This is NOT 5-minute mean
    reversion (which is dead due to HFT). This targets EXTREME deviations
    on longer timeframes (1h bars, 2 ATR threshold).

    Long when price is 2+ ATR below the 20-period mean (oversold extreme).
    Short when price is 2+ ATR above the 20-period mean (overbought extreme).
    Target: return to the 20-period mean.
    """

    name = "MNQ Mean Reversion Extreme"

    def __init__(
        self,
        mean_period: int = MEAN_PERIOD,
        atr_period: int = ATR_PERIOD,
        deviation_atr_mult: float = DEVIATION_ATR_MULT,
        stop_atr_mult: float = STOP_ATR_MULT,
        vix_max: float = VIX_MAX,
        max_contracts: int = MAX_CONTRACTS,
        capital: float = CAPITAL,
        max_hold_bars: int = MAX_HOLD_BARS,
        macro_buffer_minutes: int = 60,
    ):
        self.mean_period = mean_period
        self.atr_period = atr_period
        self.deviation_atr_mult = deviation_atr_mult
        self.stop_atr_mult = stop_atr_mult
        self.vix_max = vix_max
        self.max_contracts = max_contracts
        self.capital = capital
        self.max_hold_bars = max_hold_bars
        self.macro_buffer_minutes = macro_buffer_minutes

    def get_required_tickers(self) -> list[str]:
        """MNQ for trading, NQ/QQQ as fallback for signal, VIX for filter."""
        return ["MNQ", "NQ", "QQQ", "VIX"]

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

    def _get_signal_data(self, data: dict[str, pd.DataFrame]) -> Optional[pd.DataFrame]:
        """
        Get the best available price data for signal generation.
        Priority: MNQ > NQ > QQQ.
        """
        min_bars = self.mean_period + self.atr_period + 5
        for ticker in ["MNQ", "NQ", "QQQ"]:
            if ticker in data and len(data[ticker]) >= min_bars:
                return data[ticker]
        return None

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate mean reversion signals on MNQ for extreme moves.

        Looks for price deviations > 2 ATR from the 20-period mean on 1h bars.
        Enter contrarian, target return to mean.

        data: {ticker: DataFrame with intraday OHLCV bars}
        date: trading date

        Returns list of Signal objects (0 or 1 signal per day).
        """
        signals = []

        # ── Get price data ──
        df = self._get_signal_data(data)
        if df is None:
            return signals

        # ── VIX filter ──
        if "VIX" in data and not data["VIX"].empty:
            vix_level = float(data["VIX"]["close"].iloc[-1])
            if vix_level > self.vix_max:
                return signals
        else:
            vix_level = None

        # ── Compute indicators ──
        df = df.copy()
        df["mean_20"] = df["close"].rolling(self.mean_period, min_periods=self.mean_period).mean()
        df["atr"] = self._compute_atr(df, self.atr_period)
        df["deviation"] = df["close"] - df["mean_20"]
        df["deviation_atr"] = df["deviation"] / df["atr"]

        # ── Find signal bars in evaluation window ──
        signal_bars = df[
            (df.index.time >= SIGNAL_WINDOW_START)
            & (df.index.time <= SIGNAL_WINDOW_END)
        ].dropna(subset=["mean_20", "atr", "deviation_atr"])

        if signal_bars.empty:
            return signals

        # ── Scan for extreme deviation ──
        for ts, bar in signal_bars.iterrows():
            dev_atr = float(bar["deviation_atr"])
            atr_val = float(bar["atr"])
            mean_val = float(bar["mean_20"])
            price = float(bar["close"])

            if pd.isna(dev_atr) or pd.isna(atr_val) or atr_val <= 0:
                continue

            # Need extreme deviation
            if abs(dev_atr) < self.deviation_atr_mult:
                continue

            # ── FOMC/CPI/NFP filter ──
            bar_time = ts.time() if hasattr(ts, 'time') else None
            if bar_time is not None and is_near_macro_event(
                date, bar_time, self.macro_buffer_minutes
            ):
                continue

            # ── Direction: contrarian ──
            if dev_atr < -self.deviation_atr_mult:
                direction = "LONG"  # Oversold extreme
            elif dev_atr > self.deviation_atr_mult:
                direction = "SHORT"  # Overbought extreme
            else:
                continue

            # ── Stop & target ──
            stop_distance = self.stop_atr_mult * atr_val

            if direction == "LONG":
                stop_loss = price - stop_distance
                take_profit = mean_val  # Target: return to mean
            else:
                stop_loss = price + stop_distance
                take_profit = mean_val  # Target: return to mean

            # ── Sizing ──
            n_contracts = min(
                self.max_contracts,
                max(1, int(self.capital * 0.2 / MNQ_MARGIN)),
            )

            signals.append(Signal(
                action=direction,
                ticker="MNQ",
                entry_price=price,
                stop_loss=round(stop_loss, 2),
                take_profit=round(take_profit, 2),
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "instrument": "MNQ",
                    "multiplier": MNQ_MULTIPLIER,
                    "margin": MNQ_MARGIN,
                    "costs_rt_pct": MNQ_COSTS_RT_PCT,
                    "contracts": n_contracts,
                    "deviation_atr": round(dev_atr, 3),
                    "mean_20": round(mean_val, 2),
                    "atr_14": round(atr_val, 2),
                    "vix": round(vix_level, 2) if vix_level is not None else None,
                    "direction": direction,
                    "max_hold_bars": self.max_hold_bars,
                },
            ))

            # Only one signal per day — take the first extreme
            break

        return signals
