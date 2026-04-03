"""
D5-01 — Shadow Trade Logger.

For each strategy signal (live or paper), logs:
  - Signal timestamp and price
  - Fill price (from broker)
  - Spread at signal time
  - Latency signal → fill
  - Slippage = fill_price - signal_price

Aggregation per strategy:
  - Mean/median/p95 slippage
  - Comparison with backtested slippage
  - Alert if slippage_live > 2x slippage_backtest

Stored in data/validation/shadow_trades.jsonl.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "data" / "validation" / "shadow_trades.jsonl"

# Expected slippage from backtests (bps)
BACKTEST_SLIPPAGE_BPS = {
    "fx_carry_vol_scaled": 2.0,
    "fx_carry_momentum": 2.0,
    "crypto_dual_momentum": 10.0,
    "crypto_vol_breakout": 15.0,
    "crypto_btc_dom_rotation": 10.0,
    "crypto_liq_momentum": 20.0,
    "crypto_weekend_gap": 12.0,
    "crypto_range_bb": 8.0,
    "mes_trend": 5.0,
    "mes_mnq_pairs": 5.0,
    "dow_seasonal": 3.0,
    "corr_hedge": 3.0,
    "vix_short": 5.0,
    "high_beta_short": 5.0,
    "late_day_mr": 4.0,
}


class ShadowTradeLogger:
    """Logs signal-to-fill details for backtest-live gap analysis.

    Usage::

        logger = ShadowTradeLogger()
        # At signal generation:
        logger.log_signal(
            strategy="fx_carry_vol_scaled",
            ticker="EURUSD",
            side="BUY",
            signal_price=1.0850,
        )
        # At fill:
        logger.log_fill(
            strategy="fx_carry_vol_scaled",
            ticker="EURUSD",
            fill_price=1.0853,
            fill_qty=10000,
            spread=0.00015,
        )
    """

    def __init__(self, alert_callback=None):
        self._alert = alert_callback
        self._pending_signals: dict[str, dict] = {}
        self._slippage_history: dict[str, list[float]] = defaultdict(list)
        self._trade_count = 0

    def log_signal(
        self,
        strategy: str,
        ticker: str,
        side: str,
        signal_price: float,
        spread: float = 0.0,
    ) -> None:
        """Log a signal before execution."""
        key = f"{strategy}:{ticker}:{side}"
        self._pending_signals[key] = {
            "strategy": strategy,
            "ticker": ticker,
            "side": side,
            "signal_price": signal_price,
            "spread_at_signal": spread,
            "signal_time": time.time(),
            "signal_timestamp": datetime.now(UTC).isoformat(),
        }

    def log_fill(
        self,
        strategy: str,
        ticker: str,
        fill_price: float,
        fill_qty: float = 0,
        side: str = "",
        spread: float = 0.0,
        broker: str = "",
    ) -> dict | None:
        """Log a fill and compute slippage.

        Returns trade record if matching signal found, None otherwise.
        """
        # Find matching signal — exact match strategy + ticker + side
        matching_key = None
        if side:
            # Prefer exact match with side
            exact_key = f"{strategy}:{ticker}:{side.upper()}"
            if exact_key in self._pending_signals:
                matching_key = exact_key
        if not matching_key:
            # Fallback: match by strategy + ticker (oldest signal first)
            for key, signal in self._pending_signals.items():
                if signal["strategy"] == strategy and signal["ticker"] == ticker:
                    matching_key = key
                    break

        if not matching_key:
            # Fill without signal — log anyway
            record = {
                "timestamp": datetime.now(UTC).isoformat(),
                "strategy": strategy,
                "ticker": ticker,
                "fill_price": fill_price,
                "fill_qty": fill_qty,
                "signal_price": None,
                "slippage_bps": None,
                "latency_ms": None,
                "spread": spread,
                "broker": broker,
                "matched": False,
            }
            self._save(record)
            return record

        signal = self._pending_signals.pop(matching_key)
        now = time.time()

        # Calculate slippage
        signal_price = signal["signal_price"]
        if signal_price > 0:
            if signal["side"].upper() in ("BUY", "LONG"):
                slippage_bps = (fill_price - signal_price) / signal_price * 10_000
            else:
                slippage_bps = (signal_price - fill_price) / signal_price * 10_000
        else:
            slippage_bps = 0.0

        latency_ms = (now - signal["signal_time"]) * 1000

        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "strategy": strategy,
            "ticker": ticker,
            "side": signal["side"],
            "signal_price": signal_price,
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "slippage_bps": round(slippage_bps, 2),
            "latency_ms": round(latency_ms, 1),
            "spread_at_signal": signal["spread_at_signal"],
            "spread_at_fill": spread,
            "broker": broker,
            "matched": True,
        }

        # Track slippage history
        self._slippage_history[strategy].append(slippage_bps)
        # Keep bounded
        if len(self._slippage_history[strategy]) > 1000:
            self._slippage_history[strategy] = self._slippage_history[strategy][-500:]

        self._trade_count += 1

        # Check for excessive slippage
        self._check_slippage_alert(strategy)

        self._save(record)
        return record

    def _check_slippage_alert(self, strategy: str) -> None:
        """Alert if live slippage exceeds 2x backtested slippage."""
        history = self._slippage_history.get(strategy, [])
        if len(history) < 10:
            return

        expected = BACKTEST_SLIPPAGE_BPS.get(strategy, 10.0)
        actual_mean = float(np.mean(history[-20:]))

        if actual_mean > expected * 2:
            msg = (
                f"SLIPPAGE ALERT: {strategy}\n"
                f"Live avg: {actual_mean:.1f} bps (last 20 trades)\n"
                f"Backtest: {expected:.1f} bps\n"
                f"Ratio: {actual_mean/expected:.1f}x"
            )
            logger.warning(msg)
            if self._alert:
                self._alert(msg, level="warning")

    def get_stats(self, strategy: str | None = None) -> dict:
        """Get slippage stats per strategy."""
        if strategy:
            h = self._slippage_history.get(strategy, [])
            if not h:
                return {"strategy": strategy, "trades": 0}
            arr = np.array(h)
            return {
                "strategy": strategy,
                "trades": len(h),
                "mean_bps": round(float(np.mean(arr)), 2),
                "median_bps": round(float(np.median(arr)), 2),
                "p95_bps": round(float(np.percentile(arr, 95)), 2),
                "expected_bps": BACKTEST_SLIPPAGE_BPS.get(strategy),
            }

        # All strategies
        stats = {}
        for strat, h in self._slippage_history.items():
            if h:
                arr = np.array(h)
                stats[strat] = {
                    "trades": len(h),
                    "mean_bps": round(float(np.mean(arr)), 2),
                    "median_bps": round(float(np.median(arr)), 2),
                    "p95_bps": round(float(np.percentile(arr, 95)), 2),
                }
        return {"total_trades": self._trade_count, "strategies": stats}

    def _save(self, record: dict) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.error("Shadow trade log write failed: %s", e)
