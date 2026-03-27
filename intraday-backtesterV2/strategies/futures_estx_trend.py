"""
EU-006 : EURO STOXX 50 Futures Trend Following

Edge:
Same trend-following framework as MES/ES (FUT-003) applied to the
European index. EURO STOXX 50 trends are driven by ECB policy, European
macro data, and global risk sentiment. Costs ~EUR 2/contract RT = ~0.005%.
Replaces expensive EU equity exposure with cheap futures exposure.

Signal:
  - LONG: EMA(10) > EMA(30) AND price > EMA(10) on 1h STOXX bars
  - SHORT: EMA(10) < EMA(30) AND price < EMA(10) on 1h STOXX bars
  - Filter: ECB rate decision days → skip (too volatile/random)

Risk:
  - Stop: 2 ATR(14) on 1h chart
  - Take profit: 3 ATR(14) (1.5:1 R/R)
  - Holding: 2-10 days (swing, same as MES)

Instrument:
  - Mini STOXX 50 (ESTX50), multiplier 10, Eurex exchange, ~EUR 1,200 margin
  - Proxy: FEZ ETF (SPDR EURO STOXX 50 ETF) for backtesting
  - Market hours: 08:00-22:00 CET (02:00-16:00 ET)

Costs:
  - Commission: ~EUR 2/contract RT (~0.005%)
  - Slippage: ~0.5 index point per trade

Filters:
  - ECB rate decision days → skip
  - No trade before 09:00 CET or after 17:30 CET (core session)
    Mapped to ET: ~03:00-11:30 ET

Walk-forward expectations:
  - Target Sharpe OOS: 0.8-1.5
  - Min 50% OOS windows profitable
  - ~50-70 trades/year
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# ─── Constants ────────────────────────────────────────────────────────────────

# Proxy ticker for backtesting
STOXX_TICKER = "FEZ"       # SPDR EURO STOXX 50 ETF (proxy)

# EMA periods (1h chart)
EMA_FAST = 10
EMA_SLOW = 30

# Risk
ATR_PERIOD = 14
STOP_ATR_MULT = 2.0        # Stop = 2 ATR
TP_ATR_MULT = 3.0          # TP = 3 ATR (1.5:1 R/R)

# Instrument specs
ESTX_MULTIPLIER = 10        # EUR 10 per index point
ESTX_MARGIN = 1200.0        # ~EUR 1,200 margin per contract
ESTX_COMMISSION_RT = 2.0    # ~EUR 2 round-trip per contract
ESTX_COST_RT_PCT = 0.00005  # ~0.005%

# EU core session mapped to ET (CET 09:00-17:30 = ET 03:00-11:30)
# Since backtester runs in ET, we use ET times
EU_SESSION_START_ET = dt_time(3, 0)
EU_SESSION_END_ET = dt_time(11, 30)

# Since we're using US-listed FEZ ETF, restrict to US market overlap
# FEZ trades 09:30-16:00 ET — use the overlap window 09:30-15:55 ET
US_SESSION_START = dt_time(9, 35)
US_SESSION_END = dt_time(15, 55)

# ECB rate decision dates 2026 — skip trading on these days
ECB_DATES = {
    "2026-01-22",
    "2026-03-05",
    "2026-04-16",
    "2026-06-04",
    "2026-07-16",
    "2026-09-10",
    "2026-10-22",
    "2026-12-03",
}


class FuturesESTXTrendStrategy(BaseStrategy):
    """
    EURO STOXX 50 Futures Trend Following — EMA crossover on 1h bars.

    Goes long when EMA(10) > EMA(30) and price > EMA(10), short when
    the reverse. ECB rate decision days are filtered out. Uses FEZ
    (SPDR EURO STOXX 50 ETF) as proxy for backtesting.

    Same framework as ES/MES trend strategy (FUT-003) applied to EU.
    """

    name = "EU-006 ESTX Trend"

    def __init__(
        self,
        ema_fast: int = EMA_FAST,
        ema_slow: int = EMA_SLOW,
        atr_period: int = ATR_PERIOD,
        stop_atr_mult: float = STOP_ATR_MULT,
        tp_atr_mult: float = TP_ATR_MULT,
        max_trades_per_day: int = 1,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return [STOXX_TICKER]

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Compute Average True Range."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, min_periods=period).mean()

    @staticmethod
    def _is_ecb_day(date) -> bool:
        """Check if date is an ECB rate decision day."""
        date_str = str(date)[:10]
        return date_str in ECB_DATES

    @staticmethod
    def _in_trading_session(timestamp: pd.Timestamp) -> bool:
        """
        Check if timestamp is within the US-listed FEZ trading window.
        FEZ is US-listed: 09:35-15:55 ET (aligned with engine guard).
        """
        t = timestamp.time() if hasattr(timestamp, "time") else None
        if t is None:
            return False
        return US_SESSION_START <= t <= US_SESSION_END

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate trend-following signals for EURO STOXX 50 (via FEZ proxy).

        Args:
            data: {ticker: DataFrame} with OHLCV columns
            date: current trading date

        Returns:
            list[Signal] — at most max_trades_per_day signals
        """
        if STOXX_TICKER not in data:
            return []

        # ── ECB filter ──
        if self._is_ecb_day(date):
            return []

        df = data[STOXX_TICKER]

        # Need enough bars for EMA slow + warmup
        min_bars = self.ema_slow + self.atr_period + 5
        if len(df) < min_bars:
            return []

        df = df.copy()

        # ── Compute indicators ──
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()
        df["atr"] = self._compute_atr(df, self.atr_period)

        signals = []

        for ts, bar in df.iterrows():
            if len(signals) >= self.max_trades_per_day:
                break

            # ── Session filter ──
            if not self._in_trading_session(ts):
                continue

            # Skip NaN rows (warmup)
            if pd.isna(bar["ema_fast"]) or pd.isna(bar["ema_slow"]) or pd.isna(bar["atr"]):
                continue

            atr_val = bar["atr"]
            if atr_val <= 0:
                continue

            price = bar["close"]
            ema_f = bar["ema_fast"]
            ema_s = bar["ema_slow"]

            stop_distance = self.stop_atr_mult * atr_val
            tp_distance = self.tp_atr_mult * atr_val

            # ── LONG signal: EMA fast > EMA slow, price > EMA fast ──
            if ema_f > ema_s and price > ema_f:
                entry_price = price
                stop_loss = entry_price - stop_distance
                take_profit = entry_price + tp_distance

                signals.append(Signal(
                    action="LONG",
                    ticker=STOXX_TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "instrument": "Mini STOXX 50 (ESTX50)",
                        "proxy": STOXX_TICKER,
                        "exchange": "Eurex",
                        "ema_fast": round(ema_f, 2),
                        "ema_slow": round(ema_s, 2),
                        "atr": round(atr_val, 4),
                        "multiplier": ESTX_MULTIPLIER,
                        "margin_per_contract": ESTX_MARGIN,
                        "commission_rt_eur": ESTX_COMMISSION_RT,
                        "cost_rt_pct": ESTX_COST_RT_PCT,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 2),
                        "expected_holding_days": "2-10",
                    },
                ))

            # ── SHORT signal: EMA fast < EMA slow, price < EMA fast ──
            elif ema_f < ema_s and price < ema_f:
                entry_price = price
                stop_loss = entry_price + stop_distance
                take_profit = entry_price - tp_distance

                signals.append(Signal(
                    action="SHORT",
                    ticker=STOXX_TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "instrument": "Mini STOXX 50 (ESTX50)",
                        "proxy": STOXX_TICKER,
                        "exchange": "Eurex",
                        "ema_fast": round(ema_f, 2),
                        "ema_slow": round(ema_s, 2),
                        "atr": round(atr_val, 4),
                        "multiplier": ESTX_MULTIPLIER,
                        "margin_per_contract": ESTX_MARGIN,
                        "commission_rt_eur": ESTX_COMMISSION_RT,
                        "cost_rt_pct": ESTX_COST_RT_PCT,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 2),
                        "expected_holding_days": "2-10",
                    },
                ))

        return signals[:self.max_trades_per_day]
