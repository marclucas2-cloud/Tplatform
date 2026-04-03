"""
Strategie FUT-002 : Brent Lag Play on MCL (Micro Crude Oil Futures)

EDGE : Le Brent crude trade a Londres. Les futures US WTI/energy reagissent
avec un retard de 15-60 minutes pendant le chevauchement EU/US (15:30-20:00 CET).
On mesure le move du Brent depuis l'ouverture de Londres jusqu'au debut du overlap,
puis on entre sur MCL dans la meme direction si le move depasse le seuil.

Migration de la version proxy (actions energy, 0.26% RT) vers futures (MCL, 0.003% RT).
Meme signal, execution 86x moins chere.

Regles :
- Signal : Brent move > 0.5% depuis le London open jusqu'au overlap
- Entry : 15:35-16:00 CET (= 09:35-10:00 ET), start of EU/US overlap
- Exit : 20:00 CET (= 14:00 ET) ou target/stop touche
- Stop : 1.5 ATR(14) sur chart 5-min CL
- Take Profit : 2.5 ATR(14)
- Instrument : MCL (multiplier 100, ~$600 margin/contrat)
- Sizing : max 4 contrats MCL a $25K capital
- Filtre : Skip si VIX > 30 (correlations commodity cassent)
- ~150 trades/an, holding 1-4 heures
"""
from abc import ABC, abstractmethod
from datetime import time as dt_time

import pandas as pd

# ── Signal & BaseStrategy (local definitions for standalone use) ─────────
# When running inside the backtester, these are imported from backtest_engine.
# For futures strategies that run standalone, we define them here.

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

MCL_MULTIPLIER = 100       # $100 per point for Micro Crude Oil
MCL_MARGIN = 600.0         # Approximate margin per contract
MCL_COSTS_RT_PCT = 0.003   # Round-trip costs ~0.003% for futures

# ── Strategy parameters ─────────────────────────────────────────────────

BRENT_MIN_MOVE_PCT = 0.5     # Brent must move > 0.5% from London open
VIX_MAX = 30.0               # Skip if VIX > 30
ATR_PERIOD = 14
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 2.5
MAX_CONTRACTS = 4
CAPITAL = 25_000.0

# EU/US overlap window in ET (CET 15:35-16:00 = ET 09:35-10:00)
ENTRY_WINDOW_START = dt_time(9, 35)
ENTRY_WINDOW_END = dt_time(10, 0)
# Exit deadline in ET (CET 20:00 = ET 14:00)
EXIT_DEADLINE = dt_time(14, 0)


class BrentLagFuturesStrategy(BaseStrategy):
    """
    Brent Lag Play on MCL — Micro Crude Oil Futures.

    Edge: Brent crude trades in London hours. US WTI futures react with a
    15-60 minute lag during the EU/US overlap session. We measure the Brent
    move from London open to the overlap start. If the move exceeds 0.5%,
    we enter MCL in the same direction and ride the catch-up.

    This is a migration from the equity-proxy version (energy stocks, 0.26% RT)
    to futures (MCL, 0.003% RT). Same signal, 86x cheaper execution.
    """

    name = "Brent Lag MCL"

    def __init__(
        self,
        brent_min_move_pct: float = BRENT_MIN_MOVE_PCT,
        vix_max: float = VIX_MAX,
        atr_period: int = ATR_PERIOD,
        stop_atr_mult: float = STOP_ATR_MULT,
        target_atr_mult: float = TARGET_ATR_MULT,
        max_contracts: int = MAX_CONTRACTS,
        capital: float = CAPITAL,
    ):
        self.brent_min_move_pct = brent_min_move_pct
        self.vix_max = vix_max
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.max_contracts = max_contracts
        self.capital = capital

    def get_required_tickers(self) -> list[str]:
        """CL = WTI continuous (proxy for Brent lag), MCL = micro crude, VIX for filter."""
        return ["CL", "MCL", "VIX"]

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

    def _get_london_open_price(self, df: pd.DataFrame) -> float | None:
        """
        Get the price at London open equivalent.
        London opens at 08:00 GMT = 03:00 ET.
        If we don't have overnight data, use previous day close or first available bar.
        For backtesting with RTH-only data, we use the first bar of the day as proxy.
        """
        if df.empty:
            return None
        return float(df.iloc[0]["open"])

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate signals based on Brent lag detection.

        data: {ticker: DataFrame with intraday OHLCV bars}
        date: trading date

        Returns list of Signal objects (0 or 1 signal per day).
        """
        signals = []

        # ── Guard: need CL data for signal ──
        if "CL" not in data:
            return signals

        df_cl = data["CL"]
        if len(df_cl) < self.atr_period + 5:
            return signals

        # ── VIX filter ──
        if "VIX" in data:
            df_vix = data["VIX"]
            if not df_vix.empty:
                latest_vix = df_vix["close"].iloc[-1] if "close" in df_vix.columns else None
                if latest_vix is not None and latest_vix > self.vix_max:
                    return signals

        # ── Calculate Brent/CL move from open to overlap start ──
        cl_open_price = self._get_london_open_price(df_cl)
        if cl_open_price is None or cl_open_price <= 0:
            return signals

        # Find bars in entry window (09:35-10:00 ET)
        entry_bars = df_cl[
            (df_cl.index.time >= ENTRY_WINDOW_START)
            & (df_cl.index.time <= ENTRY_WINDOW_END)
        ]
        if entry_bars.empty:
            return signals

        # CL move from open to entry window
        cl_price_at_entry = float(entry_bars.iloc[0]["close"])
        cl_move_pct = ((cl_price_at_entry - cl_open_price) / cl_open_price) * 100

        if abs(cl_move_pct) < self.brent_min_move_pct:
            return signals

        # ── Direction ──
        direction = "LONG" if cl_move_pct > 0 else "SHORT"

        # ── ATR on CL for stop/target sizing ──
        atr_series = self._compute_atr(df_cl, self.atr_period)
        # Get ATR value at or before entry time
        atr_at_entry = atr_series[atr_series.index <= entry_bars.index[0]]
        if atr_at_entry.empty or pd.isna(atr_at_entry.iloc[-1]):
            return signals

        atr_val = float(atr_at_entry.iloc[-1])
        if atr_val <= 0:
            return signals

        # ── MCL entry price (use CL price — MCL tracks CL tick-for-tick) ──
        # In live, MCL will have its own feed. For backtesting, CL price = MCL price
        if "MCL" in data and not data["MCL"].empty:
            mcl_df = data["MCL"]
            mcl_entry_bars = mcl_df[
                (mcl_df.index.time >= ENTRY_WINDOW_START)
                & (mcl_df.index.time <= ENTRY_WINDOW_END)
            ]
            if not mcl_entry_bars.empty:
                entry_price = float(mcl_entry_bars.iloc[0]["close"])
                entry_ts = mcl_entry_bars.index[0]
            else:
                entry_price = cl_price_at_entry
                entry_ts = entry_bars.index[0]
        else:
            entry_price = cl_price_at_entry
            entry_ts = entry_bars.index[0]

        # ── Stop loss & take profit ──
        stop_distance = self.stop_atr_mult * atr_val
        target_distance = self.target_atr_mult * atr_val

        if direction == "LONG":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + target_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - target_distance

        # ── Sizing: max contracts within capital constraint ──
        margin_per_contract = MCL_MARGIN
        max_by_capital = int(self.capital * 0.4 / margin_per_contract)  # Max 40% capital
        n_contracts = min(self.max_contracts, max(1, max_by_capital))

        # ── Signal ──
        signals.append(Signal(
            action=direction,
            ticker="MCL",
            entry_price=entry_price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            timestamp=entry_ts,
            metadata={
                "strategy": self.name,
                "instrument": "MCL",
                "multiplier": MCL_MULTIPLIER,
                "margin": MCL_MARGIN,
                "costs_rt_pct": MCL_COSTS_RT_PCT,
                "contracts": n_contracts,
                "cl_move_pct": round(cl_move_pct, 3),
                "atr_14": round(atr_val, 4),
                "stop_distance": round(stop_distance, 4),
                "target_distance": round(target_distance, 4),
                "direction": direction,
            },
        ))

        return signals
