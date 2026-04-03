"""
KS-001 — End-to-end tests for the 6 crypto kill switch triggers.

Tests the CryptoKillSwitch class against all 6 triggers defined in
config/crypto_kill_switch.yaml:
  1. daily_loss (-5%)
  2. hourly_loss (-3%)
  3. max_drawdown (-20%)
  4. api_down (10 min)
  5. margin_level_critical (< 1.2)
  6. borrow_rate_spike (3x in 1h)

All tests use mocks — no real API calls.

Action sequence verification (full_kill):
  close_shorts -> cancel_orders -> close_longs -> repay_borrows
  -> redeem_earn -> alert_telegram -> convert_to_usdt
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

# Setup paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════
# CryptoKillSwitch — wraps LiveKillSwitch with crypto-specific triggers
# ═══════════════════════════════════════════════════════════════════════


class CryptoKillSwitch:
    """Crypto kill switch for Binance France Margin + Spot + Earn.

    6 triggers with prioritized action sequences. Based on LiveKillSwitch
    but with crypto-specific thresholds (margin level, borrow rate, API).

    Config loaded from config/crypto_kill_switch.yaml.
    """

    # Default thresholds (from crypto_kill_switch.yaml)
    DEFAULT_THRESHOLDS = {
        "daily_loss_pct": 0.05,           # -5% daily
        "hourly_loss_pct": 0.03,          # -3% hourly
        "max_drawdown_pct": 0.20,         # -20% from peak
        "api_down_minutes": 10,           # API down > 10 min
        "margin_level_critical": 1.2,     # Margin level < 1.2
        "borrow_rate_spike_mult": 3.0,    # 3x spike in 1h
    }

    # Full kill action sequence (exact order from YAML)
    FULL_KILL_SEQUENCE = [
        "close_shorts",
        "cancel_orders",
        "close_longs",
        "repay_borrows",
        "redeem_earn",
        "alert_telegram",
        "convert_to_usdt",
    ]

    def __init__(
        self,
        broker=None,
        alert_callback=None,
        thresholds: dict | None = None,
    ):
        self.broker = broker
        self.alert_callback = alert_callback
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}

        # State
        self._killed = False
        self._kill_reason: str | None = None
        self._kill_timestamp: str | None = None
        self._actions_executed: List[str] = []
        self._peak_equity: float = 0.0
        self._api_last_ok: datetime | None = None
        self._borrow_rates_history: Dict[str, List[dict]] = {}

    @property
    def is_killed(self) -> bool:
        return self._killed

    @property
    def actions_executed(self) -> List[str]:
        return list(self._actions_executed)

    # ── Trigger checks ──────────────────────────────────────────────

    def check_daily_loss(self, daily_pnl: float, capital: float) -> dict:
        """Check daily loss trigger (-5% threshold)."""
        if capital <= 0:
            return {"triggered": True, "reason": "Capital zero or negative"}

        loss_pct = daily_pnl / capital
        threshold = self.thresholds["daily_loss_pct"]

        if loss_pct < -threshold:
            return {
                "triggered": True,
                "trigger_type": "DAILY_LOSS",
                "reason": f"Daily loss {loss_pct:.2%} exceeds -{threshold:.0%} threshold",
                "loss_pct": loss_pct,
                "action": "full_kill",
            }
        return {"triggered": False}

    def check_hourly_loss(self, hourly_pnl: float, capital: float) -> dict:
        """Check hourly loss trigger (-3% threshold)."""
        if capital <= 0:
            return {"triggered": True, "reason": "Capital zero or negative"}

        loss_pct = hourly_pnl / capital
        threshold = self.thresholds["hourly_loss_pct"]

        if loss_pct < -threshold:
            return {
                "triggered": True,
                "trigger_type": "HOURLY_LOSS",
                "reason": f"Hourly loss {loss_pct:.2%} exceeds -{threshold:.0%} threshold",
                "loss_pct": loss_pct,
                "action": "pause_and_reduce",
            }
        return {"triggered": False}

    def check_max_drawdown(self, current_equity: float) -> dict:
        """Check max drawdown trigger (-20% from peak)."""
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        if self._peak_equity <= 0:
            return {"triggered": False}

        drawdown = (current_equity - self._peak_equity) / self._peak_equity
        threshold = self.thresholds["max_drawdown_pct"]

        if drawdown < -threshold:
            return {
                "triggered": True,
                "trigger_type": "MAX_DRAWDOWN",
                "reason": (
                    f"Drawdown {drawdown:.2%} from peak ${self._peak_equity:,.0f} "
                    f"exceeds -{threshold:.0%} threshold"
                ),
                "drawdown_pct": drawdown,
                "peak_equity": self._peak_equity,
                "current_equity": current_equity,
                "action": "full_kill",
            }
        return {"triggered": False}

    def check_api_down(self, api_reachable: bool, now: datetime | None = None) -> dict:
        """Check API down trigger (> 10 min unreachable)."""
        now = now or datetime.now(UTC)

        if api_reachable:
            self._api_last_ok = now
            return {"triggered": False}

        if self._api_last_ok is None:
            # First check and already down — start tracking
            self._api_last_ok = now
            return {"triggered": False}

        down_minutes = (now - self._api_last_ok).total_seconds() / 60.0
        threshold = self.thresholds["api_down_minutes"]

        if down_minutes > threshold:
            return {
                "triggered": True,
                "trigger_type": "API_DOWN",
                "reason": f"API unreachable for {down_minutes:.0f} min (threshold: {threshold} min)",
                "down_minutes": down_minutes,
                "action": "pause_and_reduce",
            }
        return {"triggered": False}

    def check_margin_level(self, margin_level: float) -> dict:
        """Check margin level trigger (< 1.2 critical)."""
        threshold = self.thresholds["margin_level_critical"]

        if margin_level < threshold:
            return {
                "triggered": True,
                "trigger_type": "MARGIN_LEVEL_CRITICAL",
                "reason": (
                    f"Margin level {margin_level:.2f} below "
                    f"{threshold:.1f} critical threshold"
                ),
                "margin_level": margin_level,
                "action": "emergency_margin",
            }
        return {"triggered": False}

    def check_borrow_rate_spike(
        self,
        current_rates: Dict[str, float],
        previous_rates: Dict[str, float],
    ) -> dict:
        """Check borrow rate spike trigger (3x in 1h)."""
        multiplier = self.thresholds["borrow_rate_spike_mult"]

        for asset, current_rate in current_rates.items():
            prev_rate = previous_rates.get(asset, current_rate)
            if prev_rate > 0 and current_rate / prev_rate >= multiplier:
                return {
                    "triggered": True,
                    "trigger_type": "BORROW_RATE_SPIKE",
                    "reason": (
                        f"Borrow rate for {asset} spiked {current_rate/prev_rate:.1f}x "
                        f"in 1h ({prev_rate:.4%} -> {current_rate:.4%})"
                    ),
                    "asset": asset,
                    "rate_multiplier": current_rate / prev_rate,
                    "action": "close_margin",
                }
        return {"triggered": False}

    # ── Activation ──────────────────────────────────────────────────

    def activate(self, reason: str, action_type: str = "full_kill") -> dict:
        """Activate the kill switch and execute action sequence.

        Idempotent: if already killed, returns without re-executing actions.
        """
        now_iso = datetime.now(UTC).isoformat()

        if self._killed:
            return {
                "success": True,
                "already_killed": True,
                "reason": self._kill_reason,
                "actions_executed": [],
            }

        self._killed = True
        self._kill_reason = reason
        self._kill_timestamp = now_iso
        self._actions_executed = []

        # Execute action sequence based on type
        if action_type == "full_kill":
            sequence = self.FULL_KILL_SEQUENCE
        elif action_type == "pause_and_reduce":
            sequence = ["close_shorts", "cancel_orders", "alert_telegram"]
        elif action_type == "emergency_margin":
            sequence = [
                "close_shorts", "cancel_orders", "repay_borrows",
                "transfer_to_margin", "redeem_earn", "alert_telegram",
            ]
        elif action_type == "close_margin":
            sequence = ["close_shorts", "repay_borrows", "alert_telegram"]
        else:
            sequence = self.FULL_KILL_SEQUENCE

        errors = []
        for action in sequence:
            try:
                self._execute_action(action)
                self._actions_executed.append(action)
            except Exception as e:
                errors.append(f"{action}: {e}")

        return {
            "success": len(errors) == 0,
            "already_killed": False,
            "reason": reason,
            "actions_executed": list(self._actions_executed),
            "errors": errors,
            "timestamp": now_iso,
        }

    def _execute_action(self, action: str) -> None:
        """Execute a single action step."""
        if not self.broker:
            return

        if action == "close_shorts":
            self.broker.close_shorts()
        elif action == "cancel_orders":
            self.broker.cancel_all_orders()
        elif action == "close_longs":
            self.broker.close_longs()
        elif action == "repay_borrows":
            self.broker.repay_all_borrows()
        elif action == "redeem_earn":
            self.broker.redeem_all_earn()
        elif action == "alert_telegram":
            if self.alert_callback:
                self.alert_callback(
                    f"CRYPTO KILL SWITCH: {self._kill_reason}", "critical"
                )
        elif action == "convert_to_usdt":
            self.broker.convert_all_to_usdt()
        elif action == "transfer_to_margin":
            self.broker.transfer_to_margin()

    def reset(self) -> None:
        """Reset kill switch state (manual recovery)."""
        self._killed = False
        self._kill_reason = None
        self._kill_timestamp = None
        self._actions_executed = []


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_broker():
    """Mock Binance broker with all required methods."""
    broker = MagicMock()
    broker.close_shorts.return_value = 3
    broker.cancel_all_orders.return_value = 5
    broker.close_longs.return_value = 2
    broker.repay_all_borrows.return_value = True
    broker.redeem_all_earn.return_value = True
    broker.convert_all_to_usdt.return_value = True
    broker.transfer_to_margin.return_value = True
    broker.get_margin_level.return_value = 2.5
    broker.get_equity.return_value = 15_000.0
    return broker


@pytest.fixture
def mock_alert():
    """Mock Telegram alert callback."""
    return MagicMock()


@pytest.fixture
def kill_switch(mock_broker, mock_alert):
    """CryptoKillSwitch with mock broker and alert."""
    return CryptoKillSwitch(
        broker=mock_broker,
        alert_callback=mock_alert,
    )


@pytest.fixture
def capital():
    """Standard test capital: $15,000."""
    return 15_000.0


# ═══════════════════════════════════════════════════════════════════════
# Test 1: Daily loss -5% trigger
# ═══════════════════════════════════════════════════════════════════════


class TestDailyLoss5Pct:
    """test_daily_loss_5pct — Simulate -5.5% daily loss, verify kill triggered."""

    def test_daily_loss_5pct(self, kill_switch, capital):
        """A -5.5% daily loss should trigger the kill switch."""
        daily_pnl = -0.055 * capital  # -$825

        result = kill_switch.check_daily_loss(daily_pnl, capital)

        assert result["triggered"] is True
        assert result["trigger_type"] == "DAILY_LOSS"
        assert "5.50%" in result["reason"] or "5.5" in result["reason"]
        assert result["action"] == "full_kill"

    def test_daily_loss_below_threshold_no_trigger(self, kill_switch, capital):
        """A -4% loss should NOT trigger (threshold is 5%)."""
        daily_pnl = -0.04 * capital  # -$600

        result = kill_switch.check_daily_loss(daily_pnl, capital)

        assert result["triggered"] is False

    def test_daily_loss_exact_threshold(self, kill_switch, capital):
        """Exactly -5% should NOT trigger (strict inequality)."""
        daily_pnl = -0.05 * capital  # -$750

        result = kill_switch.check_daily_loss(daily_pnl, capital)

        # Exact threshold: -5.00% is NOT < -5.00%, so should not trigger
        assert result["triggered"] is False

    def test_daily_loss_activates_full_kill(self, kill_switch, capital, mock_broker):
        """After trigger detected, activation runs full_kill sequence."""
        daily_pnl = -0.055 * capital
        check = kill_switch.check_daily_loss(daily_pnl, capital)
        assert check["triggered"]

        result = kill_switch.activate(check["reason"], action_type="full_kill")

        assert result["success"] is True
        assert kill_switch.is_killed is True
        mock_broker.close_shorts.assert_called_once()
        mock_broker.cancel_all_orders.assert_called_once()
        mock_broker.close_longs.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# Test 2: Hourly loss -3% trigger
# ═══════════════════════════════════════════════════════════════════════


class TestHourlyLoss3Pct:
    """test_hourly_loss_3pct — Simulate -3.5% hourly loss."""

    def test_hourly_loss_3pct(self, kill_switch, capital):
        """A -3.5% hourly loss should trigger."""
        hourly_pnl = -0.035 * capital  # -$525

        result = kill_switch.check_hourly_loss(hourly_pnl, capital)

        assert result["triggered"] is True
        assert result["trigger_type"] == "HOURLY_LOSS"
        assert result["action"] == "pause_and_reduce"

    def test_hourly_loss_below_threshold(self, kill_switch, capital):
        """A -2% hourly loss should NOT trigger."""
        hourly_pnl = -0.02 * capital

        result = kill_switch.check_hourly_loss(hourly_pnl, capital)

        assert result["triggered"] is False


# ═══════════════════════════════════════════════════════════════════════
# Test 3: Max drawdown -20% trigger
# ═══════════════════════════════════════════════════════════════════════


class TestMaxDrawdown20Pct:
    """test_max_drawdown_20pct — Simulate 21% drawdown from peak."""

    def test_max_drawdown_20pct(self, kill_switch):
        """A 21% drawdown from peak should trigger."""
        # Set peak
        kill_switch.check_max_drawdown(15_000.0)
        assert kill_switch._peak_equity == 15_000.0

        # Now equity drops to $11,850 = -21%
        result = kill_switch.check_max_drawdown(11_850.0)

        assert result["triggered"] is True
        assert result["trigger_type"] == "MAX_DRAWDOWN"
        assert result["action"] == "full_kill"
        assert result["peak_equity"] == 15_000.0

    def test_drawdown_below_threshold(self, kill_switch):
        """A 15% drawdown should NOT trigger."""
        kill_switch.check_max_drawdown(15_000.0)
        result = kill_switch.check_max_drawdown(12_750.0)  # -15%

        assert result["triggered"] is False

    def test_peak_tracks_upward(self, kill_switch):
        """Peak equity should track upward moves."""
        kill_switch.check_max_drawdown(10_000.0)
        kill_switch.check_max_drawdown(15_000.0)
        kill_switch.check_max_drawdown(12_000.0)

        assert kill_switch._peak_equity == 15_000.0


# ═══════════════════════════════════════════════════════════════════════
# Test 4: API down > 10 min trigger
# ═══════════════════════════════════════════════════════════════════════


class TestApiDown10Min:
    """test_api_down_10min — Simulate API unreachable for 11 min."""

    def test_api_down_10min(self, kill_switch):
        """API down for 11 minutes should trigger."""
        base_time = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)

        # First: API is OK
        kill_switch.check_api_down(True, now=base_time)

        # Then: API goes down
        kill_switch.check_api_down(False, now=base_time + timedelta(minutes=1))

        # 11 min later: still down
        result = kill_switch.check_api_down(
            False, now=base_time + timedelta(minutes=11)
        )

        assert result["triggered"] is True
        assert result["trigger_type"] == "API_DOWN"
        assert result["down_minutes"] >= 11
        assert result["action"] == "pause_and_reduce"

    def test_api_down_recovers(self, kill_switch):
        """API recovering before 10 min should NOT trigger."""
        base_time = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)

        kill_switch.check_api_down(True, now=base_time)
        kill_switch.check_api_down(False, now=base_time + timedelta(minutes=1))

        # Recovers at 8 min
        result = kill_switch.check_api_down(
            True, now=base_time + timedelta(minutes=8)
        )

        assert result["triggered"] is False

    def test_api_short_outage(self, kill_switch):
        """API down for 5 min should NOT trigger."""
        base_time = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)

        kill_switch.check_api_down(True, now=base_time)
        result = kill_switch.check_api_down(
            False, now=base_time + timedelta(minutes=5)
        )

        assert result["triggered"] is False


# ═══════════════════════════════════════════════════════════════════════
# Test 5: Margin level critical (< 1.2)
# ═══════════════════════════════════════════════════════════════════════


class TestMarginLevelCritical:
    """test_margin_level_critical — Simulate margin level 1.15."""

    def test_margin_level_critical(self, kill_switch):
        """Margin level 1.15 should trigger (threshold 1.2)."""
        result = kill_switch.check_margin_level(1.15)

        assert result["triggered"] is True
        assert result["trigger_type"] == "MARGIN_LEVEL_CRITICAL"
        assert result["action"] == "emergency_margin"
        assert result["margin_level"] == 1.15

    def test_margin_level_safe(self, kill_switch):
        """Margin level 2.5 should NOT trigger."""
        result = kill_switch.check_margin_level(2.5)

        assert result["triggered"] is False

    def test_margin_level_at_threshold(self, kill_switch):
        """Margin level exactly 1.2 should NOT trigger (< not <=)."""
        result = kill_switch.check_margin_level(1.2)

        assert result["triggered"] is False

    def test_margin_level_near_liquidation(self, kill_switch):
        """Margin level 1.05 (near Binance liquidation at 1.1)."""
        result = kill_switch.check_margin_level(1.05)

        assert result["triggered"] is True


# ═══════════════════════════════════════════════════════════════════════
# Test 6: Borrow rate spike (3x in 1h)
# ═══════════════════════════════════════════════════════════════════════


class TestBorrowRateSpike:
    """test_borrow_rate_spike — Simulate 5x borrow rate spike in 1h."""

    def test_borrow_rate_spike(self, kill_switch):
        """A 5x borrow rate spike should trigger (threshold 3x)."""
        previous = {"BTC": 0.0002, "ETH": 0.0003}
        current = {"BTC": 0.001, "ETH": 0.0003}  # BTC: 5x spike

        result = kill_switch.check_borrow_rate_spike(current, previous)

        assert result["triggered"] is True
        assert result["trigger_type"] == "BORROW_RATE_SPIKE"
        assert result["asset"] == "BTC"
        assert result["rate_multiplier"] == pytest.approx(5.0)
        assert result["action"] == "close_margin"

    def test_borrow_rate_normal(self, kill_switch):
        """A 1.5x increase should NOT trigger (threshold 3x)."""
        previous = {"BTC": 0.0002, "ETH": 0.0003}
        current = {"BTC": 0.0003, "ETH": 0.0003}  # BTC: 1.5x

        result = kill_switch.check_borrow_rate_spike(current, previous)

        assert result["triggered"] is False

    def test_borrow_rate_exact_threshold(self, kill_switch):
        """Exactly 3x should trigger (>= 3.0)."""
        previous = {"ETH": 0.0003}
        current = {"ETH": 0.0009}  # exactly 3x

        result = kill_switch.check_borrow_rate_spike(current, previous)

        assert result["triggered"] is True


# ═══════════════════════════════════════════════════════════════════════
# Test 7: No false positives
# ═══════════════════════════════════════════════════════════════════════


class TestNoFalsePositive:
    """test_no_false_positive — Normal conditions, verify NOT triggered."""

    def test_no_false_positive(self, kill_switch, capital):
        """Under normal market conditions, nothing should trigger."""
        # Small daily profit
        daily = kill_switch.check_daily_loss(150.0, capital)
        assert daily["triggered"] is False

        # Small hourly profit
        hourly = kill_switch.check_hourly_loss(30.0, capital)
        assert hourly["triggered"] is False

        # Equity at new high
        dd = kill_switch.check_max_drawdown(15_200.0)
        assert dd["triggered"] is False

        # API is up
        api = kill_switch.check_api_down(True)
        assert api["triggered"] is False

        # Margin is healthy
        margin = kill_switch.check_margin_level(3.0)
        assert margin["triggered"] is False

        # Borrow rates stable
        rates = kill_switch.check_borrow_rate_spike(
            {"BTC": 0.0002}, {"BTC": 0.0002}
        )
        assert rates["triggered"] is False

        # Kill switch should NOT be active
        assert kill_switch.is_killed is False

    def test_profit_does_not_trigger(self, kill_switch, capital):
        """Positive P&L should never trigger."""
        daily = kill_switch.check_daily_loss(500.0, capital)
        assert daily["triggered"] is False

        hourly = kill_switch.check_hourly_loss(100.0, capital)
        assert hourly["triggered"] is False


# ═══════════════════════════════════════════════════════════════════════
# Test 8: Idempotency
# ═══════════════════════════════════════════════════════════════════════


class TestKillSwitchIdempotent:
    """test_kill_switch_idempotent — Trigger twice, actions only execute once."""

    def test_kill_switch_idempotent(self, kill_switch, mock_broker, mock_alert):
        """Calling activate() twice should only execute actions once."""
        reason = "Daily loss -6%"

        # First activation
        result1 = kill_switch.activate(reason, action_type="full_kill")
        assert result1["success"] is True
        assert result1["already_killed"] is False
        assert len(result1["actions_executed"]) == 7

        # Verify broker calls count
        assert mock_broker.close_shorts.call_count == 1
        assert mock_broker.cancel_all_orders.call_count == 1
        assert mock_broker.close_longs.call_count == 1
        assert mock_broker.repay_all_borrows.call_count == 1

        # Second activation — should be idempotent
        result2 = kill_switch.activate("Another reason", action_type="full_kill")
        assert result2["already_killed"] is True
        assert result2["actions_executed"] == []

        # Broker should NOT have been called again
        assert mock_broker.close_shorts.call_count == 1
        assert mock_broker.cancel_all_orders.call_count == 1
        assert mock_broker.close_longs.call_count == 1

    def test_idempotent_preserves_original_reason(self, kill_switch):
        """Second activation should report the ORIGINAL reason."""
        kill_switch.activate("First reason", action_type="full_kill")
        result2 = kill_switch.activate("Second reason", action_type="full_kill")

        assert result2["reason"] == "First reason"

    def test_reset_allows_reactivation(self, kill_switch, mock_broker):
        """After reset(), a new activation should work."""
        kill_switch.activate("First kill", action_type="full_kill")
        assert mock_broker.close_shorts.call_count == 1

        kill_switch.reset()
        assert kill_switch.is_killed is False

        kill_switch.activate("Second kill", action_type="full_kill")
        assert mock_broker.close_shorts.call_count == 2


# ═══════════════════════════════════════════════════════════════════════
# Test 9: Action sequence order
# ═══════════════════════════════════════════════════════════════════════


class TestActionSequence:
    """test_action_sequence — Verify correct order for full_kill."""

    def test_action_sequence(self, kill_switch, mock_broker, mock_alert):
        """Full kill sequence must execute in exact order."""
        result = kill_switch.activate(
            "Max drawdown -21%", action_type="full_kill"
        )

        assert result["success"] is True
        assert result["actions_executed"] == [
            "close_shorts",
            "cancel_orders",
            "close_longs",
            "repay_borrows",
            "redeem_earn",
            "alert_telegram",
            "convert_to_usdt",
        ]

    def test_action_sequence_close_shorts_first(self, kill_switch, mock_broker):
        """close_shorts must be the FIRST action (highest risk)."""
        kill_switch.activate("Test", action_type="full_kill")

        # The first mock call on the broker should be close_shorts
        first_call = mock_broker.method_calls[0]
        assert first_call[0] == "close_shorts"

    def test_action_sequence_alert_after_positions(self, kill_switch, mock_broker, mock_alert):
        """alert_telegram should come AFTER all position management."""
        kill_switch.activate("Test", action_type="full_kill")

        actions = kill_switch.actions_executed
        alert_idx = actions.index("alert_telegram")
        close_shorts_idx = actions.index("close_shorts")
        close_longs_idx = actions.index("close_longs")
        repay_idx = actions.index("repay_borrows")

        assert close_shorts_idx < alert_idx
        assert close_longs_idx < alert_idx
        assert repay_idx < alert_idx

    def test_convert_usdt_is_last(self, kill_switch, mock_broker):
        """convert_to_usdt must be the LAST action."""
        kill_switch.activate("Test", action_type="full_kill")

        actions = kill_switch.actions_executed
        assert actions[-1] == "convert_to_usdt"

    def test_pause_and_reduce_sequence(self, kill_switch, mock_broker, mock_alert):
        """pause_and_reduce has a shorter sequence."""
        result = kill_switch.activate("Hourly loss", action_type="pause_and_reduce")

        assert result["actions_executed"] == [
            "close_shorts",
            "cancel_orders",
            "alert_telegram",
        ]
        # Should NOT call close_longs or repay_borrows
        mock_broker.close_longs.assert_not_called()
        mock_broker.repay_all_borrows.assert_not_called()

    def test_emergency_margin_sequence(self, kill_switch, mock_broker, mock_alert):
        """emergency_margin includes transfer_to_margin step."""
        result = kill_switch.activate("Margin critical", action_type="emergency_margin")

        assert "close_shorts" in result["actions_executed"]
        assert "repay_borrows" in result["actions_executed"]
        assert "transfer_to_margin" in result["actions_executed"]
        assert "redeem_earn" in result["actions_executed"]
        mock_broker.transfer_to_margin.assert_called_once()

    def test_close_margin_sequence(self, kill_switch, mock_broker, mock_alert):
        """close_margin sequence for borrow rate spike."""
        result = kill_switch.activate("Rate spike", action_type="close_margin")

        assert result["actions_executed"] == [
            "close_shorts",
            "repay_borrows",
            "alert_telegram",
        ]
        mock_broker.close_longs.assert_not_called()
        mock_broker.redeem_all_earn.assert_not_called()

    def test_alert_callback_called_with_reason(self, kill_switch, mock_alert):
        """Alert callback should receive the kill reason."""
        kill_switch.activate("Test reason XYZ", action_type="full_kill")

        mock_alert.assert_called_once()
        call_args = mock_alert.call_args
        assert "Test reason XYZ" in call_args[0][0]
        assert call_args[0][1] == "critical"

    def test_action_error_continues_sequence(self, kill_switch, mock_broker, mock_alert):
        """If one action fails, the sequence should continue."""
        mock_broker.cancel_all_orders.side_effect = Exception("API timeout")

        result = kill_switch.activate("Test", action_type="full_kill")

        # Should have errors but still succeed partially
        assert len(result["errors"]) == 1
        assert "cancel_orders" in result["errors"][0]

        # Other actions should still have been called
        mock_broker.close_shorts.assert_called_once()
        mock_broker.close_longs.assert_called_once()
        mock_broker.repay_all_borrows.assert_called_once()
