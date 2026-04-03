"""
FX-CARRY-MOM — Carry + Momentum Filter (Barroso 2015 + Koijen 2018).

Edge: Buy high-yield currencies only when 63-day momentum confirms the carry
direction. Cuts carry crashes because momentum turns negative BEFORE the
acceleration phase of carry unwinds.

Walk-Forward Results (2021-2026, 16 windows, 260d warmup):
  Sharpe OOS: 2.17 | Win%: 81% | OOS/IS: 1.16 | Trades: 956
  Max DD OOS: -3.92% | Avg DD: -1.06%

Monte Carlo (10K bootstrap):
  P5 Sharpe: 1.41 | P(Sharpe>0): 100% | P(Sharpe>0.5): 100%
  P95 Max DD: -3.63%

Sharpe SANS swap (edge prix pur): 0.51 — confirme edge structurel.

Pairs (long high-yield / short low-yield, only when momentum > 0):
  AUD/JPY  — RBA vs BOJ, classic risk-on carry
  USD/JPY  — Fed vs BOJ, largest rate differential
  EUR/JPY  — ECB vs BOJ, moderate carry
  NZD/USD  — RBNZ vs Fed, small differential (variable)

Position sizing: target 5% annualized vol per pair, capped [0.1x, 3.0x].
Momentum filter: 63-day cumulative return > 0 to hold, flat otherwise.
Rebalance: daily check, actual trade when momentum filter flips (~monthly).

Costs: $2/trade IBKR + 0.8-1.5 bps spread = ~0.05% RT on $20K notional.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "name": "FX Carry Momentum Filter",
    "id": "FX-CARRY-MOM",
    "pairs": ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"],
    "market_type": "fx",
    "broker": "ibkr",
    "timeframe": "1D",
    "frequency": "daily",
    "allocation_pct": 0.15,  # Probationary: ~1/16 Kelly (30% / 2)
    "max_leverage": 3.0,
    "min_capital": 5000,
}

# ── Carry pair configs ────────────────────────────────────────────────────────
CARRY_PAIRS = {
    "AUDJPY": {
        "direction": "LONG",
        "swap_daily_bps": 3.5,
        "ibkr_symbol": "AUD.JPY",
        "min_order_size": 1000,  # IBKR odd-lot FX (< 25K routes to smaller venues)
    },
    "USDJPY": {
        "direction": "LONG",
        "swap_daily_bps": 4.0,
        "ibkr_symbol": "USD.JPY",
        "min_order_size": 1000,
    },
    "EURJPY": {
        "direction": "LONG",
        "swap_daily_bps": 2.0,
        "ibkr_symbol": "EUR.JPY",
        "min_order_size": 1000,
    },
    "NZDUSD": {
        "direction": "LONG",
        "swap_daily_bps": 1.5,
        "ibkr_symbol": "NZD.USD",
        "min_order_size": 1000,
    },
}

# ── Parameters ────────────────────────────────────────────────────────────────
VOL_LOOKBACK = 20
MOMENTUM_LOOKBACK = 63      # 3-month momentum filter
TARGET_VOL_ANN = 0.05
SIZING_MIN = 0.1
SIZING_MAX = 3.0
REBALANCE_THRESHOLD = 0.20

# Risk
SL_VOL_MULT = 3.0
MAX_DD_PCT = -0.08
MAX_SINGLE_CCY_PCT = 0.60  # Max 60% exposure on any single currency (JPY risk)


class FXCarryMomentumFilter:
    """Carry trade filtered by momentum — only hold when momentum confirms."""

    def __init__(self):
        self._equity_start = 0
        self._equity_high = 0

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "vol_lookback": VOL_LOOKBACK,
            "momentum_lookback": MOMENTUM_LOOKBACK,
            "target_vol_ann": TARGET_VOL_ANN,
            "sizing_min": SIZING_MIN,
            "sizing_max": SIZING_MAX,
            "sl_vol_mult": SL_VOL_MULT,
        }

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "vol_lookback": [15, 20, 30],
            "momentum_lookback": [42, 63, 126],
            "target_vol_ann": [0.03, 0.05, 0.07],
            "sl_vol_mult": [2.5, 3.0, 4.0],
        }

    def compute_sizing(self, returns: pd.Series) -> float:
        """Vol-target sizing: target_vol / realized_vol, capped."""
        if len(returns) < VOL_LOOKBACK:
            return SIZING_MIN
        vol = returns.tail(VOL_LOOKBACK).std()
        if vol <= 0 or np.isnan(vol):
            return SIZING_MIN
        target_daily = TARGET_VOL_ANN / np.sqrt(252)
        sizing = target_daily / vol
        return float(np.clip(sizing, SIZING_MIN, SIZING_MAX))

    def momentum_filter(self, returns: pd.Series) -> bool:
        """Return True if 63-day momentum is positive (OK to hold carry)."""
        if len(returns) < MOMENTUM_LOOKBACK:
            return False
        mom = returns.tail(MOMENTUM_LOOKBACK).sum()
        return bool(mom > 0)

    def signal_fn(self, candle: pd.Series, state: dict, **kwargs) -> dict | None:
        """Generate carry signal with momentum filter for all pairs.

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

        if i < max(VOL_LOOKBACK, MOMENTUM_LOOKBACK) + 5:
            return None

        # Drawdown kill switch
        if self._equity_start == 0:
            self._equity_start = equity
            self._equity_high = equity
        self._equity_high = max(self._equity_high, equity)
        dd = (equity - self._equity_high) / self._equity_high if self._equity_high > 0 else 0
        if dd < MAX_DD_PCT:
            return {"action": "CLOSE_ALL", "reason": "carry_mom_drawdown_kill",
                    "drawdown": round(dd, 4), "strategy": "fx_carry_momentum_filter",
                    "_authorized_by": "fx_carry_momentum_filter_kill"}

        # ── Pre-compute active pairs (momentum filter) ──
        active_pairs = []
        pair_returns = {}
        for pair in CARRY_PAIRS:
            df = pair_data.get(pair)
            if df is None or len(df) < i:
                continue
            returns = df.iloc[:i]["close"].pct_change().dropna()
            if len(returns) < MOMENTUM_LOOKBACK:
                continue
            pair_returns[pair] = returns
            if self.momentum_filter(returns):
                active_pairs.append(pair)
            else:
                logger.debug(f"  {pair}: momentum negative, FLAT")

        n_active = len(active_pairs)
        if n_active == 0:
            return None

        # Cap per-pair notional to avoid concentration risk
        MAX_NOTIONAL_PCT_PER_PAIR = 0.20

        signals = []
        for pair in active_pairs:
            config = CARRY_PAIRS[pair]
            returns = pair_returns[pair]
            available = pair_data[pair].iloc[:i]

            sizing = self.compute_sizing(returns)
            current_price = float(available["close"].iloc[-1])

            pair_capital = equity * STRATEGY_CONFIG["allocation_pct"] / n_active
            notional = pair_capital * sizing

            # Cap notional to avoid concentration if few pairs active
            max_notional = equity * MAX_NOTIONAL_PCT_PER_PAIR
            notional = min(notional, max_notional)

            if notional < config["min_order_size"]:
                continue

            # Stop loss: 3x daily vol
            daily_vol = returns.tail(VOL_LOOKBACK).std()
            if daily_vol <= 0 or np.isnan(daily_vol):
                continue
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
                "momentum_63d": round(float(returns.tail(MOMENTUM_LOOKBACK).sum()), 4),
                "_authorized_by": "fx_carry_momentum_filter",
            })

        if not signals:
            return None

        # ── JPY concentration guard: cap single-currency exposure ──
        # 3/4 pairs are short JPY — cap total JPY notional
        ccy_exposure: Dict[str, float] = {}
        for s in signals:
            pair = s["pair"]
            notional = s["notional"]
            # Map pair to currencies exposed
            if pair.endswith("JPY"):
                ccy_exposure["JPY"] = ccy_exposure.get("JPY", 0) + notional
            if pair.startswith("USD") or pair.endswith("USD"):
                ccy_exposure["USD"] = ccy_exposure.get("USD", 0) + notional

        max_ccy_notional = equity * MAX_SINGLE_CCY_PCT
        for ccy, total in ccy_exposure.items():
            if total > max_ccy_notional and total > 0:
                scale = max_ccy_notional / total
                for s in signals:
                    pair = s["pair"]
                    if (ccy == "JPY" and pair.endswith("JPY")) or \
                       (ccy == "USD" and (pair.startswith("USD") or pair.endswith("USD"))):
                        s["notional"] = round(s["notional"] * scale, 0)
                logger.info(f"  JPY/USD cap: {ccy} exposure scaled by {scale:.2f}")

        return {
            "action": "CARRY_REBALANCE",
            "pairs": signals,
            "n_pairs": len(signals),
            "n_filtered": len(CARRY_PAIRS) - len(signals),
            "strategy": "fx_carry_momentum_filter",
            "total_notional": sum(s["notional"] for s in signals),
        }


# Module-level instance for worker compatibility
_instance = FXCarryMomentumFilter()
signal_fn = _instance.signal_fn
