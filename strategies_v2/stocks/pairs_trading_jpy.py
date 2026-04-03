"""JPY Pairs Trading — Statistical arbitrage on Japanese equity sectors.

Pairs: Toyota/Honda (auto), Mitsubishi/Sumitomo (trading houses),
       Sony/Panasonic (electronics).

EDGE: Japanese sector pairs exhibit strong cointegration due to shared
macro exposure (JPY, BoJ policy, export dependency). When the spread
Z-score exceeds 2 standard deviations, mean reversion is reliable
over 5-15 day horizons. This is classic Engle-Granger stat-arb applied
to TSE-listed equities.

Rules:
- Spread: log(A) - hedge_ratio * log(B) - alpha (OLS residual)
- Signal: Z-score of spread > 2.0 or < -2.0
- Entry: sell outperformer / buy underperformer (mean reversion)
- Exit: Z-score returns to 0.0 (spread mean)
- Emergency exit: |Z-score| > 4.0 (structural break)
- Stationarity: daily ADF test, exit if p-value > 0.10 (pair broke)
- Delta neutral: equal JPY exposure on both legs
- PnL: calculated in JPY, converted to USD at prevailing rate
- Session: TSE 09:00-15:00 JST (00:00-06:00 UTC)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal
from core.data.pairs import adf_test, compute_hedge_ratio, compute_spread

logger = logging.getLogger(__name__)


# -- Strategy configuration ---------------------------------------------------

STRATEGY_CONFIG = {
    "pairs": [
        {
            "name": "toyota_honda",
            "symbol_a": "7203",       # Toyota Motor
            "symbol_b": "7267",       # Honda Motor
            "exchange": "TSE",
            "sector": "auto",
            "description": "Toyota vs Honda — Japanese auto sector pair",
        },
        {
            "name": "mitsubishi_sumitomo",
            "symbol_a": "8058",       # Mitsubishi Corp
            "symbol_b": "8053",       # Sumitomo Corp
            "exchange": "TSE",
            "sector": "trading_house",
            "description": "Mitsubishi vs Sumitomo — sogo shosha pair",
        },
        {
            "name": "sony_panasonic",
            "symbol_a": "6758",       # Sony Group
            "symbol_b": "6752",       # Panasonic Holdings
            "exchange": "TSE",
            "sector": "electronics",
            "description": "Sony vs Panasonic — Japanese electronics pair",
        },
    ],
    "ibkr": {
        "exchange": "TSEJ",           # IBKR exchange code for TSE
        "currency": "JPY",
        "sec_type": "STK",
        # IBKR symbols: use the numeric TSE code (e.g. "7203")
        # conId lookup required at connection time
    },
    "session": {
        "open_jst": "09:00",
        "close_jst": "15:00",
        "open_utc": "00:00",
        "close_utc": "06:00",
        "timezone": "Asia/Tokyo",
        # TSE has a lunch break 11:30-12:30 JST but we ignore for daily bars
    },
    "sizing": {
        # Japanese stocks trade in lots of 100 shares (tangen)
        "lot_size": 100,
        # Minimum order: 1 lot = 100 shares
        "min_order_shares": 100,
    },
    "costs": {
        # IBKR Japan: ~0.08% commission + stamp duty
        "commission_pct": 0.0008,
        # Slippage estimate for liquid TSE large caps
        "slippage_pct": 0.0005,
        # Total round-trip cost per leg
        "total_cost_pct": 0.0013,
    },
}


@dataclass
class PairState:
    """Runtime state for a single pair being monitored."""
    symbol_a: str
    symbol_b: str
    pair_name: str
    hedge_ratio: float
    ols_alpha: float
    spread_mean: float
    spread_std: float
    last_zscore: float
    last_adf_pvalue: float
    is_cointegrated: bool
    in_position: bool
    position_direction: str   # "long_a_short_b" | "short_a_long_b" | "flat"
    bars_in_position: int


class JPYPairsTrading(StrategyBase):
    """Japanese equity pairs trading with Engle-Granger cointegration.

    Trades three pairs of highly correlated TSE stocks. Uses rolling
    OLS hedge ratio, Z-score entry/exit, and daily ADF stationarity
    monitoring. Delta neutral with equal JPY notional on each leg.
    """

    def __init__(self) -> None:
        # Z-score thresholds
        self.z_entry: float = 2.0
        self.z_exit: float = 0.0
        self.z_emergency: float = 4.0

        # Lookback windows
        self.lookback: int = 60             # bars for spread stats
        self.hedge_ratio_window: int = 120  # bars for OLS estimation
        self.zscore_window: int = 20        # rolling Z-score window

        # Stationarity monitoring
        self.adf_pvalue_threshold: float = 0.10
        self.adf_check_interval: int = 1    # check every N bars (daily)

        # Sizing
        self.position_pct: float = 0.30     # % of capital per pair
        self.lot_size: int = 100            # TSE lot size

        # FX conversion
        self.usdjpy_rate: float = 150.0     # default, updated from data

        # Pairs to trade (symbol_a, symbol_b, pair_name)
        self.pairs: List[tuple] = [
            ("7203", "7267", "toyota_honda"),
            ("8058", "8053", "mitsubishi_sumitomo"),
            ("6758", "6752", "sony_panasonic"),
        ]

        # Internal state per pair
        self._pair_states: Dict[str, PairState] = {}
        self._bar_count: int = 0

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "jpy_pairs_trading"

    @property
    def asset_class(self) -> str:
        return "equity"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    # -- Core logic -----------------------------------------------------------

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        """Process a new bar across all tracked pairs.

        Returns a signal for the first pair that triggers, or None.
        The engine should call on_bar once per bar period; internally
        we iterate over all pairs.
        """
        if self.data_feed is None:
            return None

        self._bar_count += 1

        # Update USDJPY rate if available
        self._update_fx_rate()

        # Check each pair for signals
        for sym_a, sym_b, pair_name in self.pairs:
            signal = self._process_pair(sym_a, sym_b, pair_name, portfolio_state)
            if signal is not None:
                return signal

        return None

    def _process_pair(
        self,
        sym_a: str,
        sym_b: str,
        pair_name: str,
        portfolio_state: PortfolioState,
    ) -> Signal | None:
        """Evaluate a single pair for entry/exit signals."""
        # Get price data for both legs
        bars_a = self.data_feed.get_bars(sym_a, self.hedge_ratio_window + 10)
        bars_b = self.data_feed.get_bars(sym_b, self.hedge_ratio_window + 10)

        if bars_a is None or bars_b is None:
            return None
        if len(bars_a) < self.lookback or len(bars_b) < self.lookback:
            return None

        # Compute hedge ratio and spread
        close_a = bars_a["close"].values
        close_b = bars_b["close"].values

        # Align lengths
        min_len = min(len(close_a), len(close_b))
        close_a = close_a[-min_len:]
        close_b = close_b[-min_len:]

        if min_len < self.lookback:
            return None
        if np.any(close_a <= 0) or np.any(close_b <= 0):
            return None

        log_a = np.log(close_a)
        log_b = np.log(close_b)

        # Rolling OLS hedge ratio on full window
        hedge_window = min(self.hedge_ratio_window, min_len)
        beta, alpha = compute_hedge_ratio(
            log_a[-hedge_window:], log_b[-hedge_window:]
        )

        # Compute spread
        spread = compute_spread(log_a, log_b, beta, alpha)

        # Z-score on recent window
        zscore = self._compute_zscore(spread)
        if zscore is None:
            return None

        # ADF stationarity check
        adf_pvalue = 1.0
        is_cointegrated = True
        if self._bar_count % self.adf_check_interval == 0:
            spread_for_adf = spread[-max(60, self.lookback):]
            _, adf_pvalue = adf_test(spread_for_adf)
            is_cointegrated = adf_pvalue < self.adf_pvalue_threshold

        # Update pair state
        state = self._get_or_create_state(sym_a, sym_b, pair_name)
        state.hedge_ratio = beta
        state.ols_alpha = alpha
        state.spread_mean = float(np.mean(spread[-self.zscore_window:]))
        state.spread_std = float(np.std(spread[-self.zscore_window:], ddof=1))
        state.last_zscore = zscore
        state.last_adf_pvalue = adf_pvalue
        state.is_cointegrated = is_cointegrated

        current_price_a = float(close_a[-1])
        current_price_b = float(close_b[-1])

        # -- Exit logic (checked first) --
        if state.in_position:
            state.bars_in_position += 1
            exit_signal = self._check_exit(state, current_price_a, current_price_b)
            if exit_signal is not None:
                state.in_position = False
                state.position_direction = "flat"
                state.bars_in_position = 0
                return exit_signal

        # -- Entry logic --
        if not state.in_position and is_cointegrated:
            entry_signal = self._check_entry(
                state, zscore, current_price_a, current_price_b,
                portfolio_state,
            )
            if entry_signal is not None:
                state.in_position = True
                state.bars_in_position = 0
                return entry_signal

        return None

    def _compute_zscore(self, spread: np.ndarray) -> float | None:
        """Compute Z-score of the latest spread value over rolling window."""
        if len(spread) < self.zscore_window:
            return None

        window = spread[-self.zscore_window:]
        mean = np.mean(window)
        std = np.std(window, ddof=1)

        if std <= 0 or np.isnan(std):
            return None

        return float((spread[-1] - mean) / std)

    def _check_entry(
        self,
        state: PairState,
        zscore: float,
        price_a: float,
        price_b: float,
        portfolio_state: PortfolioState,
    ) -> Signal | None:
        """Check if Z-score warrants a new entry.

        Z > +entry: A is rich vs B -> short A, long B
        Z < -entry: A is cheap vs B -> long A, short B
        """
        if abs(zscore) < self.z_entry:
            return None

        # Do not enter if already in emergency zone
        if abs(zscore) > self.z_emergency:
            return None

        if zscore > self.z_entry:
            # A is overvalued relative to B -> short A, long B
            state.position_direction = "short_a_long_b"
            return Signal(
                symbol=state.symbol_a,
                side="SELL",
                strategy_name=self.name,
                stop_loss=price_a * (1 + 0.05),   # 5% hard stop
                take_profit=price_a * (1 - 0.03),  # 3% target
                strength=min((abs(zscore) - self.z_entry) / 2.0, 1.0),
            )

        if zscore < -self.z_entry:
            # A is undervalued relative to B -> long A, short B
            state.position_direction = "long_a_short_b"
            return Signal(
                symbol=state.symbol_a,
                side="BUY",
                strategy_name=self.name,
                stop_loss=price_a * (1 - 0.05),
                take_profit=price_a * (1 + 0.03),
                strength=min((abs(zscore) - self.z_entry) / 2.0, 1.0),
            )

        return None

    def _check_exit(
        self,
        state: PairState,
        price_a: float,
        price_b: float,
    ) -> Signal | None:
        """Check exit conditions for an open position.

        Exits:
        1. Z-score returned to mean (0.0)
        2. Emergency: |Z-score| > 4.0 (structural break)
        3. Stationarity lost: ADF p-value > 0.10
        """
        zscore = state.last_zscore
        reason = None

        # Mean reversion achieved
        if (state.position_direction == "short_a_long_b" and zscore <= self.z_exit) or (state.position_direction == "long_a_short_b" and zscore >= -self.z_exit):
            reason = "mean_reversion"

        # Emergency exit: structural break
        if abs(zscore) > self.z_emergency:
            reason = "emergency_structural_break"

        # Stationarity lost
        if not state.is_cointegrated:
            reason = "stationarity_lost"

        if reason is None:
            return None

        logger.info(
            "EXIT %s: %s (z=%.2f, adf_p=%.3f, bars=%d)",
            state.pair_name, reason, zscore,
            state.last_adf_pvalue, state.bars_in_position,
        )

        # Reverse the entry to close
        if state.position_direction == "short_a_long_b":
            return Signal(
                symbol=state.symbol_a,
                side="BUY",
                strategy_name=self.name,
                strength=1.0,
            )
        elif state.position_direction == "long_a_short_b":
            return Signal(
                symbol=state.symbol_a,
                side="SELL",
                strategy_name=self.name,
                strength=1.0,
            )
        return None

    # -- Position sizing ------------------------------------------------------

    def compute_delta_neutral_sizes(
        self,
        price_a: float,
        price_b: float,
        capital_jpy: float,
    ) -> tuple[int, int]:
        """Compute lot-adjusted share counts for delta-neutral entry.

        Each leg gets position_pct of capital in JPY notional.
        Sizes are rounded down to the nearest lot (100 shares).

        Args:
            price_a: Current price of stock A in JPY.
            price_b: Current price of stock B in JPY.
            capital_jpy: Portfolio capital in JPY.

        Returns:
            (shares_a, shares_b) — both multiples of lot_size.
        """
        notional_per_leg = capital_jpy * self.position_pct

        raw_shares_a = notional_per_leg / price_a if price_a > 0 else 0
        raw_shares_b = notional_per_leg / price_b if price_b > 0 else 0

        # Round down to nearest lot
        shares_a = int(raw_shares_a // self.lot_size) * self.lot_size
        shares_b = int(raw_shares_b // self.lot_size) * self.lot_size

        return shares_a, shares_b

    # -- PnL conversion -------------------------------------------------------

    def convert_pnl_jpy_to_usd(
        self, pnl_jpy: float, usdjpy_rate: float | None = None
    ) -> float:
        """Convert PnL from JPY to USD.

        Args:
            pnl_jpy: Profit/loss in Japanese yen.
            usdjpy_rate: USD/JPY exchange rate. If None, uses the
                         instance default (updated from data feed).

        Returns:
            PnL in USD.
        """
        rate = usdjpy_rate if usdjpy_rate is not None else self.usdjpy_rate
        if rate <= 0:
            return 0.0
        return pnl_jpy / rate

    # -- Helpers ---------------------------------------------------------------

    def _update_fx_rate(self) -> None:
        """Try to read USDJPY from the data feed."""
        if self.data_feed is None:
            return
        try:
            bar = self.data_feed.get_latest_bar("USDJPY")
            if bar is not None and bar.close > 0:
                self.usdjpy_rate = bar.close
        except (KeyError, RuntimeError):
            pass  # USDJPY not in data feed; use default

    def _get_or_create_state(
        self, sym_a: str, sym_b: str, pair_name: str
    ) -> PairState:
        """Lazily create pair state on first access."""
        if pair_name not in self._pair_states:
            self._pair_states[pair_name] = PairState(
                symbol_a=sym_a,
                symbol_b=sym_b,
                pair_name=pair_name,
                hedge_ratio=1.0,
                ols_alpha=0.0,
                spread_mean=0.0,
                spread_std=1.0,
                last_zscore=0.0,
                last_adf_pvalue=0.0,
                is_cointegrated=True,
                in_position=False,
                position_direction="flat",
                bars_in_position=0,
            )
        return self._pair_states[pair_name]

    def get_pair_states(self) -> Dict[str, Dict[str, Any]]:
        """Expose pair states for monitoring/dashboard."""
        return {
            name: {
                "pair": f"{s.symbol_a}/{s.symbol_b}",
                "hedge_ratio": round(s.hedge_ratio, 4),
                "zscore": round(s.last_zscore, 3),
                "adf_pvalue": round(s.last_adf_pvalue, 4),
                "is_cointegrated": s.is_cointegrated,
                "in_position": s.in_position,
                "direction": s.position_direction,
                "bars_in_position": s.bars_in_position,
            }
            for name, s in self._pair_states.items()
        }

    # -- StrategyBase interface ------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "z_entry": self.z_entry,
            "z_exit": self.z_exit,
            "z_emergency": self.z_emergency,
            "lookback": self.lookback,
            "hedge_ratio_window": self.hedge_ratio_window,
            "zscore_window": self.zscore_window,
            "adf_pvalue_threshold": self.adf_pvalue_threshold,
            "position_pct": self.position_pct,
            "lot_size": self.lot_size,
            "usdjpy_rate": self.usdjpy_rate,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "z_entry": [1.5, 2.0, 2.5],
            "z_exit": [-0.5, 0.0, 0.5],
            "z_emergency": [3.5, 4.0, 5.0],
            "lookback": [40, 60, 80],
            "zscore_window": [15, 20, 30],
            "adf_pvalue_threshold": [0.05, 0.10],
            "position_pct": [0.20, 0.30, 0.40],
        }
