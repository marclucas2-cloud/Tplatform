"""Fix #5: Signal Funnel Logger — structured logging at each funnel layer.

Every signal traverses 14 layers. This module logs each step with a
consistent format:
    FUNNEL|{strategy}|{layer}|{action}|{details}

Usage:
    grep "FUNNEL" logs/worker.log | grep "KILL\\|REJECT\\|SKIP"
    -> immediately see where signals die

    grep "FUNNEL|fx_carry" logs/worker.log
    -> trace a specific strategy through the funnel
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("signal_funnel")

# Ensure the funnel logger is configured
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class FunnelLayer:
    """Layer names for the 14-layer funnel."""
    MARKET_HOURS = "market_hours"
    KILL_SWITCH = "kill_switch"
    REGIME = "regime"
    ACTIVATION_MATRIX = "activation_matrix"
    SIGNAL_QUALITY = "signal_quality"
    CONFLUENCE = "confluence"
    COOLDOWN = "cooldown"
    RISK_MANAGER = "risk_manager"
    KELLY_SIZING = "kelly_sizing"
    MIN_SIZE = "min_size"
    SPREAD_CHECK = "spread_check"
    CAPITAL_CHECK = "capital_check"
    BROKER_SUBMIT = "broker_submit"
    FILL = "fill"
    SL_VERIFY = "sl_verify"


class FunnelAction:
    """Possible actions at each layer."""
    PASS = "PASS"
    KILL = "KILL"
    SKIP = "SKIP"
    REJECT = "REJECT"
    REDUCE = "REDUCE"  # Pass but with reduced size
    WAIT = "WAIT"
    FAIL = "FAIL"
    FILLED = "FILLED"


class FunnelStats:
    """Tracks funnel statistics for reporting."""

    def __init__(self):
        self._counts: dict[str, dict[str, int]] = {}  # layer -> {action: count}
        self._total_signals = 0
        self._total_trades = 0

    def record(self, layer: str, action: str):
        if layer not in self._counts:
            self._counts[layer] = {}
        self._counts[layer][action] = self._counts[layer].get(action, 0) + 1

        if layer == FunnelLayer.REGIME and action == FunnelAction.PASS:
            self._total_signals += 1
        if layer == FunnelLayer.FILL and action == FunnelAction.FILLED:
            self._total_trades += 1

    @property
    def conversion_rate(self) -> float:
        if self._total_signals == 0:
            return 0.0
        return self._total_trades / self._total_signals

    def get_bottlenecks(self) -> list[dict]:
        """Identify the layers that kill the most signals."""
        bottlenecks = []
        for layer, actions in self._counts.items():
            kills = actions.get(FunnelAction.KILL, 0) + actions.get(FunnelAction.REJECT, 0) + actions.get(FunnelAction.SKIP, 0)
            passes = actions.get(FunnelAction.PASS, 0) + actions.get(FunnelAction.REDUCE, 0) + actions.get(FunnelAction.FILLED, 0)
            total = kills + passes
            if total > 0 and kills > 0:
                bottlenecks.append({
                    "layer": layer,
                    "kills": kills,
                    "passes": passes,
                    "kill_rate": round(kills / total, 2),
                })
        return sorted(bottlenecks, key=lambda x: -x["kill_rate"])

    def summary(self) -> dict:
        return {
            "total_signals": self._total_signals,
            "total_trades": self._total_trades,
            "conversion_rate": round(self.conversion_rate, 3),
            "by_layer": dict(self._counts),
            "bottlenecks": self.get_bottlenecks(),
        }


# Global stats instance
_stats = FunnelStats()


def get_funnel_stats() -> FunnelStats:
    return _stats


def log_funnel(
    strategy: str,
    layer: str,
    action: str,
    **details,
):
    """Log a funnel event with structured format.

    Usage:
        log_funnel("fx_carry_vs", FunnelLayer.REGIME, FunnelAction.PASS,
                    regime="TREND_STRONG", multiplier=1.0)

        log_funnel("btc_momentum", FunnelLayer.MIN_SIZE, FunnelAction.SKIP,
                    size=43, minimum=100)
    """
    detail_str = "|".join(f"{k}={v}" for k, v in details.items())
    msg = f"FUNNEL|{strategy}|{layer}|{action}"
    if detail_str:
        msg += f"|{detail_str}"

    if action in (FunnelAction.KILL, FunnelAction.REJECT, FunnelAction.FAIL):
        logger.warning(msg)
    elif action == FunnelAction.SKIP:
        logger.info(msg)
    else:
        logger.info(msg)

    _stats.record(layer, action)


# Convenience functions for each layer
def log_market_hours(strategy: str, is_open: bool, market: str = ""):
    action = FunnelAction.PASS if is_open else FunnelAction.KILL
    log_funnel(strategy, FunnelLayer.MARKET_HOURS, action, market=market)


def log_kill_switch(strategy: str, is_active: bool, reason: str = ""):
    action = FunnelAction.KILL if is_active else FunnelAction.PASS
    log_funnel(strategy, FunnelLayer.KILL_SWITCH, action, reason=reason)


def log_regime(strategy: str, regime: str, multiplier: float):
    action = FunnelAction.KILL if multiplier <= 0 else (
        FunnelAction.REDUCE if multiplier < 1.0 else FunnelAction.PASS
    )
    log_funnel(strategy, FunnelLayer.REGIME, action,
               regime=regime, multiplier=multiplier)


def log_activation_matrix(strategy: str, regime: str, multiplier: float):
    action = FunnelAction.KILL if multiplier <= 0 else FunnelAction.PASS
    log_funnel(strategy, FunnelLayer.ACTIVATION_MATRIX, action,
               regime=regime, mult=multiplier)


def log_signal_quality(strategy: str, score: float, threshold: float, verdict: str):
    action = FunnelAction.PASS if verdict == "TRADE" else (
        FunnelAction.REDUCE if verdict == "REDUCE" else FunnelAction.SKIP
    )
    log_funnel(strategy, FunnelLayer.SIGNAL_QUALITY, action,
               score=f"{score:.2f}", threshold=f"{threshold:.2f}")


def log_risk_check(strategy: str, passed: bool, check_name: str = "", reason: str = ""):
    action = FunnelAction.PASS if passed else FunnelAction.REJECT
    log_funnel(strategy, FunnelLayer.RISK_MANAGER, action,
               check=check_name, reason=reason)


def log_sizing(strategy: str, raw_size: float, kelly_size: float, viable: bool):
    action = FunnelAction.PASS if viable else FunnelAction.SKIP
    log_funnel(strategy, FunnelLayer.KELLY_SIZING, action,
               raw=f"${raw_size:.0f}", kelly=f"${kelly_size:.0f}")


def log_min_size(strategy: str, size: float, minimum: float, viable: bool):
    action = FunnelAction.PASS if viable else FunnelAction.SKIP
    log_funnel(strategy, FunnelLayer.MIN_SIZE, action,
               size=f"${size:.0f}", min=f"${minimum:.0f}")


def log_spread(strategy: str, spread_bps: float, avg_bps: float, action_str: str):
    action = FunnelAction.PASS if action_str == "OK" else (
        FunnelAction.WAIT if action_str == "WAIT" else FunnelAction.SKIP
    )
    log_funnel(strategy, FunnelLayer.SPREAD_CHECK, action,
               spread=f"{spread_bps:.1f}bps", avg=f"{avg_bps:.1f}bps")


def log_submit(strategy: str, symbol: str, side: str, qty: float, price: float):
    log_funnel(strategy, FunnelLayer.BROKER_SUBMIT, FunnelAction.PASS,
               symbol=symbol, side=side, qty=qty, price=f"{price:.4f}")


def log_fill(strategy: str, symbol: str, fill_price: float, qty: float):
    log_funnel(strategy, FunnelLayer.FILL, FunnelAction.FILLED,
               symbol=symbol, price=f"{fill_price:.4f}", qty=qty)


def log_fill_fail(strategy: str, symbol: str, reason: str):
    log_funnel(strategy, FunnelLayer.FILL, FunnelAction.FAIL,
               symbol=symbol, reason=reason)
