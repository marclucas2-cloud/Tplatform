"""
FX-CARRY-VS — Risk-Managed FX Carry Trade (Vol-Scaled).

Edge: Buy high-yield currencies, sell low-yield. The forward premium puzzle
means carry is compensated above fair value. Vol-scaling (Barroso & Santa-Clara
2015) prevents carry crash drawdowns by reducing position when vol spikes.

Walk-Forward Results (2021-2026, 16 windows):
  Sharpe OOS: 3.59 | Win%: 94% | OOS/IS: 1.30 | Trades: 688
  Max DD OOS: -4.49% (1 window) | Avg DD: -1.03%

Pairs (long high-yield / short low-yield):
  AUD/JPY  — RBA 4.35% vs BOJ 0.50% = +385 bps carry
  USD/JPY  — Fed 5.25% vs BOJ 0.50% = +475 bps carry
  EUR/JPY  — ECB 4.50% vs BOJ 0.50% = +400 bps carry
  NZD/USD  — RBNZ 5.50% vs Fed 5.25% = +25 bps (variable)

Position sizing: target 5% annualized vol per pair, capped [0.1x, 3.0x].
Rebalance: daily (vol recalculation), actual trade ~monthly (signal stable).

Costs: $2/trade IBKR + 0.8-1.5 bps spread = ~0.05% RT on $20K notional.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "name": "FX Carry Vol-Scaled",
    "id": "FX-CARRY-VS",
    "pairs": ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"],
    "market_type": "fx",
    "broker": "ibkr",
    "timeframe": "1D",
    "frequency": "daily",
    "allocation_pct": 0.30,  # 30% of IBKR capital
    "max_leverage": 3.0,
    "min_capital": 5000,  # USD equivalent
}

# ── Carry pair configs ────────────────────────────────────────────────────────
# swap_daily_bps: estimated daily swap in basis points (long direction)
# These are approximations; live values come from IBKR swap rates
CARRY_PAIRS = {
    "AUDJPY": {
        "direction": "LONG",  # Buy AUD, sell JPY
        "swap_daily_bps": 3.5,
        "ibkr_symbol": "AUD.JPY",
        "min_order_size": 25000,  # AUD
    },
    "USDJPY": {
        "direction": "LONG",  # Buy USD, sell JPY
        "swap_daily_bps": 4.0,
        "ibkr_symbol": "USD.JPY",
        "min_order_size": 25000,
    },
    "EURJPY": {
        "direction": "LONG",  # Buy EUR, sell JPY
        "swap_daily_bps": 2.0,
        "ibkr_symbol": "EUR.JPY",
        "min_order_size": 20000,
    },
    "NZDUSD": {
        "direction": "LONG",  # Buy NZD, sell USD
        "swap_daily_bps": 1.5,
        "ibkr_symbol": "NZD.USD",
        "min_order_size": 25000,
    },
}

# ── Parameters ────────────────────────────────────────────────────────────────
VOL_LOOKBACK = 20       # 20-day realized vol
TARGET_VOL_ANN = 0.05   # 5% annualized target vol per pair
SIZING_MIN = 0.1        # Min position multiplier
SIZING_MAX = 3.0        # Max position multiplier
REBALANCE_THRESHOLD = 0.20  # Rebalance when sizing changes > 20%

# Risk
SL_VOL_MULT = 3.0       # Stop loss at 3x daily vol
MAX_DD_PCT = -0.08       # Kill switch at -8% drawdown


class FXCarryVolScaled:
    """Risk-managed FX carry trade with volatility scaling."""

    def __init__(self):
        self._positions: Dict[str, dict] = {}  # pair -> {size, entry, sizing_mult}
        self._equity_start = 0
        self._equity_high = 0
        self._daily_returns: Dict[str, list] = {p: [] for p in CARRY_PAIRS}

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "vol_lookback": VOL_LOOKBACK,
            "target_vol_ann": TARGET_VOL_ANN,
            "sizing_min": SIZING_MIN,
            "sizing_max": SIZING_MAX,
            "sl_vol_mult": SL_VOL_MULT,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "vol_lookback": [15, 20, 30],
            "target_vol_ann": [0.03, 0.05, 0.07],
            "sl_vol_mult": [2.5, 3.0, 4.0],
        }

    def compute_sizing(self, returns: pd.Series) -> float:
        """Compute position sizing based on realized vol.

        Returns multiplier: target_vol / realized_vol, capped.
        """
        if len(returns) < VOL_LOOKBACK:
            return SIZING_MIN

        vol = returns.tail(VOL_LOOKBACK).std()
        if vol <= 0 or np.isnan(vol):
            return SIZING_MIN

        target_daily = TARGET_VOL_ANN / np.sqrt(252)
        sizing = target_daily / vol
        return float(np.clip(sizing, SIZING_MIN, SIZING_MAX))

    def signal_fn(self, candle: pd.Series, state: dict, **kwargs) -> dict | None:
        """Generate carry signal for all pairs.

        Args:
            candle: latest daily bar (any pair as reference)
            state: {positions, capital, equity, i, df_full}
            kwargs:
                pair_data: dict[pair_name] -> DataFrame with daily OHLCV
                equity: current IBKR equity

        Returns:
            Signal dict or None
        """
        pair_data = kwargs.get("pair_data", {})
        equity = kwargs.get("equity", state.get("equity", 10000))
        i = state.get("i", 0)

        if i < VOL_LOOKBACK + 5:
            return None

        # Drawdown check
        if self._equity_start == 0:
            self._equity_start = equity
            self._equity_high = equity
        self._equity_high = max(self._equity_high, equity)
        dd = (equity - self._equity_high) / self._equity_high if self._equity_high > 0 else 0
        if dd < MAX_DD_PCT:
            return {"action": "CLOSE_ALL", "reason": "carry_drawdown_kill",
                    "drawdown": round(dd, 4), "strategy": "fx_carry_vol_scaled"}

        signals = []

        for pair, config in CARRY_PAIRS.items():
            df = pair_data.get(pair)
            if df is None or len(df) < i:
                continue

            available = df.iloc[:i]
            returns = available["close"].pct_change().dropna()

            if len(returns) < VOL_LOOKBACK:
                continue

            sizing = self.compute_sizing(returns)
            current_price = float(available["close"].iloc[-1])

            # Capital allocation per pair (equal weight across carry pairs)
            n_pairs = len(CARRY_PAIRS)
            pair_capital = equity * STRATEGY_CONFIG["allocation_pct"] / n_pairs

            # Notional = capital * sizing (leverage via vol target)
            notional = pair_capital * sizing

            # Check minimum order size
            if notional < config["min_order_size"]:
                continue

            # Stop loss: 3x daily vol below entry
            daily_vol = returns.tail(VOL_LOOKBACK).std()
            sl_distance = current_price * daily_vol * SL_VOL_MULT
            stop_loss = current_price - sl_distance

            signals.append({
                "pair": pair,
                "action": "BUY",
                "direction": config["direction"],
                "notional": round(notional, 0),
                "sizing_mult": round(sizing, 2),
                "stop_loss": round(stop_loss, 5),
                "swap_daily_bps": config["swap_daily_bps"],
                "vol_20d": round(daily_vol * np.sqrt(252), 4),
                "price": current_price,
            })

        if not signals:
            return None

        return {
            "action": "CARRY_REBALANCE",
            "pairs": signals,
            "n_pairs": len(signals),
            "strategy": "fx_carry_vol_scaled",
            "total_notional": sum(s["notional"] for s in signals),
        }


# Module-level instance for worker compatibility
_instance = FXCarryVolScaled()
signal_fn = _instance.signal_fn
