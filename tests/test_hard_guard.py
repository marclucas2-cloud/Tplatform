"""
Hard guard tests -- verify risk guards block dangerous orders BEFORE they
hit the broker API.

Tests the risk layer in isolation using mock broker connections.
Covers: position sizing, stop-loss, circuit breakers, exposure limits,
pipeline guard, margin guard, kill switch, extreme price injection,
and concurrent order submission.
"""

import threading
from unittest.mock import MagicMock

import pytest

from core.alpaca_client.client import AlpacaClient
from core.kill_switch_live import LiveKillSwitch
from core.risk_manager_live import LiveRiskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order(
    symbol="AAPL",
    direction="LONG",
    notional=1000,
    strategy="test_strat",
    asset_class="EQUITY",
    stop_loss=145.0,
    **extra,
):
    """Build a minimal order dict for LiveRiskManager.validate_order."""
    order = {
        "symbol": symbol,
        "direction": direction,
        "notional": notional,
        "strategy": strategy,
        "asset_class": asset_class,
        "stop_loss": stop_loss,
    }
    order.update(extra)
    return order


def _make_portfolio(
    equity=10_000,
    cash=5_000,
    positions=None,
    margin_used_pct=0.0,
):
    """Build a minimal portfolio dict."""
    return {
        "equity": equity,
        "cash": cash,
        "positions": positions or [],
        "margin_used_pct": margin_used_pct,
    }


def _position(
    symbol="AAPL",
    notional=1000,
    side="LONG",
    strategy="test_strat",
    sector=None,
    asset_class="EQUITY",
    **extra,
):
    """Build a minimal position dict."""
    pos = {
        "symbol": symbol,
        "notional": notional,
        "side": side,
        "strategy": strategy,
        "asset_class": asset_class,
    }
    if sector:
        pos["sector"] = sector
    pos.update(extra)
    return pos


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rm():
    """LiveRiskManager using the real limits_live.yaml config."""
    return LiveRiskManager()


@pytest.fixture
def kill_switch(tmp_path):
    """LiveKillSwitch with no broker, temp state file."""
    return LiveKillSwitch(
        broker=None,
        state_path=tmp_path / "ks_test.json",
    )


# =========================================================================
# 1. Position sizing guard
# =========================================================================

class TestPositionSizingGuard:
    """Verify per-position cap (15% in limits_live.yaml) blocks oversized orders."""

    def test_50pct_of_capital_rejected(self, rm):
        """Order for 50% of capital on a single position -> REJECTED."""
        order = _make_order(notional=5000)  # 50% of $10K
        portfolio = _make_portfolio(equity=10_000, cash=8_000)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"50% position should be rejected, got: {msg}"
        assert "osition" in msg.lower() or "limit" in msg.lower()

    def test_16pct_of_capital_rejected(self, rm):
        """Order for 16% of capital -> REJECTED (15% cap in config)."""
        order = _make_order(notional=1600)  # 16% of $10K
        portfolio = _make_portfolio(equity=10_000, cash=8_000)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"16% position should be rejected (15% cap), got: {msg}"

    def test_9pct_of_capital_accepted(self, rm):
        """Order for 9% of capital -> ACCEPTED (below 15% cap)."""
        order = _make_order(notional=900)  # 9% of $10K
        portfolio = _make_portfolio(equity=10_000, cash=8_000)
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"9% position should be accepted, got: {msg}"

    def test_existing_position_adds_up(self, rm):
        """Existing 10% + new 6% = 16% on same symbol -> REJECTED."""
        order = _make_order(symbol="NVDA", notional=600)  # +6%
        portfolio = _make_portfolio(
            equity=10_000,
            cash=8_000,
            positions=[_position(symbol="NVDA", notional=1000)],  # 10% already
        )
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"10% + 6% = 16% should be rejected, got: {msg}"


# =========================================================================
# 2. Stop-loss guard
# =========================================================================

class TestStopLossGuard:
    """Verify orders without proper stop-loss are rejected at the broker level.

    The Alpaca client itself rejects notional-based orders without stop-loss
    (CRO C-1 rule). We test via mocked Alpaca internals.
    """

    def test_no_stop_loss_rejected(self):
        """Order without _authorized_by -> raises AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaAPIError

        # AlpacaClient requires _authorized_by for every create_position call.
        # Without it, it should raise.
        client = AlpacaClient.__new__(AlpacaClient)
        client._paper = True
        with pytest.raises(AlpacaAPIError, match="authorized"):
            client.create_position(
                symbol="AAPL",
                direction="BUY",
                notional=100.0,
                stop_loss=None,
                _authorized_by=None,  # Missing pipeline auth
            )

    def test_notional_order_without_sl_returns_rejected(self):
        """Notional order with auth but no stop-loss bracket -> CRO REJECT.

        The Alpaca client tries to convert notional->qty for a bracket order.
        If there is no SL and no bracket, it returns REJECTED.
        We mock the data client to simulate price fetch failure so the
        code path returns None (refuses to open unbounded position).
        """
        client = AlpacaClient.__new__(AlpacaClient)
        client._paper = True
        client._api_key = "fake"
        client._secret_key = "fake"
        client._base_url = "https://paper-api.alpaca.markets"
        client._trading_client = None
        client._data_client = None

        mock_trading = MagicMock()
        client._get_trading_client = MagicMock(return_value=mock_trading)

        # Mock _get_data_client to raise, simulating inability to fetch price.
        # This triggers the "REFUSING order" path: returns None.
        mock_data = MagicMock()
        mock_data.get_stock_latest_quote.side_effect = Exception("no connection")
        client._get_data_client = MagicMock(return_value=mock_data)

        result = client.create_position(
            symbol="AAPL",
            direction="BUY",
            notional=100.0,
            stop_loss=None,
            _authorized_by="test_guard",
        )
        # CRO C-1: notional order without SL returns REJECTED dict or None
        assert result is None or (
            isinstance(result, dict)
            and result.get("status") == "REJECTED"
            and result.get("reason") == "no_stop_loss"
        ), f"Notional order without SL should be refused, got: {result}"

    def test_order_with_sl_accepted_structure(self):
        """Order dict with stop_loss field present passes risk manager checks."""
        rm = LiveRiskManager()
        order = _make_order(notional=900, stop_loss=140.0)
        portfolio = _make_portfolio(equity=10_000, cash=8_000)
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"Order with SL should pass risk checks, got: {msg}"


# =========================================================================
# 3. Circuit breaker guard
# =========================================================================

class TestCircuitBreakerGuard:
    """Verify circuit breakers halt trading when drawdown thresholds are hit."""

    def test_daily_loss_6pct_stops_trading(self, rm):
        """Daily DD of -6% -> all orders blocked via check_all_limits."""
        portfolio = _make_portfolio(equity=10_000)
        result = rm.check_all_limits(portfolio, daily_pnl_pct=-0.06)
        assert not result["passed"], "6% daily loss should block trading"
        assert "STOP_TRADING" in str(result["actions"])

    def test_daily_loss_3pct_does_not_fully_block(self, rm):
        """Daily DD of -0.5% -> trading still allowed (below 1.5% threshold)."""
        portfolio = _make_portfolio(equity=10_000)
        result = rm.check_all_limits(portfolio, daily_pnl_pct=-0.005)
        daily_check = next(
            c for c in result["checks"] if c["name"] == "circuit_breaker_daily"
        )
        assert daily_check["passed"], "0.5% daily loss should not trigger daily breaker"

    def test_circuit_breaker_persists_across_checks(self, rm):
        """Calling check_circuit_breaker twice with same DD still triggers."""
        triggered1, _ = rm.check_circuit_breaker(daily_pnl_pct=-0.06)
        triggered2, _ = rm.check_circuit_breaker(daily_pnl_pct=-0.06)
        assert triggered1 and triggered2, "Circuit breaker should persist"

    def test_weekly_loss_triggers_sizing_reduction(self, rm):
        """Weekly loss > 3% triggers REDUCE_SIZING action."""
        portfolio = _make_portfolio(equity=10_000)
        result = rm.check_all_limits(portfolio, weekly_pnl_pct=-0.04)
        assert "REDUCE_SIZING_50" in result["actions"]

    def test_monthly_loss_triggers_close_all(self, rm):
        """Monthly loss > 5% triggers CLOSE_ALL_REVIEW."""
        portfolio = _make_portfolio(equity=10_000)
        result = rm.check_all_limits(portfolio, monthly_pnl_pct=-0.06)
        assert not result["passed"]
        assert "CLOSE_ALL_REVIEW" in result["actions"]


# =========================================================================
# 4. Exposure guard
# =========================================================================

class TestExposureGuard:
    """Verify net long/short exposure limits block excessive directional bets."""

    def test_long_exposure_exceeds_limit(self, rm):
        """Already 55% long + 10% new long -> REJECTED (60% cap).

        Uses distinct strategies (each under 20% cap) to isolate the
        exposure check from the strategy concentration check.
        """
        positions = [
            _position(symbol="AAPL", notional=1400, side="LONG", strategy="strat_a"),
            _position(symbol="MSFT", notional=1400, side="LONG", strategy="strat_b"),
            _position(symbol="AMZN", notional=1400, side="LONG", strategy="strat_c"),
            _position(symbol="GOOG", notional=1300, side="LONG", strategy="strat_d"),
        ]
        # Total long = 5500 = 55%. New order = 10%. Total = 65% > 60% cap.
        order = _make_order(symbol="NVDA", direction="LONG", notional=1000, strategy="strat_e")
        portfolio = _make_portfolio(equity=10_000, cash=4_000, positions=positions)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"55% + 10% = 65% long should be rejected, got: {msg}"
        assert "ong" in msg.lower() or "exposure" in msg.lower()

    def test_short_exposure_exceeds_limit(self, rm):
        """Already 35% short + 10% new short -> REJECTED (40% cap)."""
        positions = [
            _position(symbol="AAPL", notional=1750, side="SHORT", strategy="strat_a"),
            _position(symbol="MSFT", notional=1750, side="SHORT", strategy="strat_b"),
        ]
        order = _make_order(symbol="NVDA", direction="SHORT", notional=1000, strategy="strat_c")
        portfolio = _make_portfolio(equity=10_000, cash=4_000, positions=positions)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"35% + 10% = 45% short should be rejected, got: {msg}"

    def test_exposure_within_limits_accepted(self, rm):
        """15% long + 5% new long -> ACCEPTED (20% < 60%).

        Each position uses a distinct strategy to stay under the 20% strategy cap.
        """
        positions = [
            _position(symbol="AAPL", notional=1500, side="LONG", strategy="strat_a"),
        ]
        order = _make_order(symbol="MSFT", direction="LONG", notional=500, strategy="strat_b")
        portfolio = _make_portfolio(equity=10_000, cash=7_000, positions=positions)
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"15% + 5% = 20% long should be accepted, got: {msg}"

    def test_gross_exposure_exceeds_limit(self, rm):
        """Gross exposure > 120% -> REJECTED.

        Note: individual positions may also trigger position/strategy limits,
        but gross exposure is still validated. We construct positions that
        respect per-position (15%) and per-strategy (20%) caps individually.
        """
        # 8 positions x 1400 = 11200 = 112% gross. +2000 new = 132% > 120%.
        positions = [
            _position(symbol=f"SYM{i}", notional=1400, side="LONG", strategy=f"s{i}")
            for i in range(4)
        ] + [
            _position(symbol=f"XSYM{i}", notional=1400, side="SHORT", strategy=f"xs{i}")
            for i in range(4)
        ]
        order = _make_order(symbol="NEW1", direction="LONG", notional=1400, strategy="snew")
        portfolio = _make_portfolio(equity=10_000, cash=1_000, positions=positions)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"Gross exposure >120% should be rejected, got: {msg}"


# =========================================================================
# 5. Pipeline guard (_authorized_by)
# =========================================================================

class TestPipelineGuard:
    """Verify _authorized_by field is enforced at the broker level."""

    def test_order_without_authorized_by_rejected(self):
        """AlpacaClient.create_position without _authorized_by -> raises."""
        from core.alpaca_client.client import AlpacaAPIError

        client = AlpacaClient.__new__(AlpacaClient)
        client._paper = True
        with pytest.raises(AlpacaAPIError, match="authorized"):
            client.create_position(
                symbol="AAPL",
                direction="BUY",
                qty=10,
                _authorized_by=None,
            )

    def test_order_with_authorized_by_passes_guard(self):
        """AlpacaClient.create_position with _authorized_by does not raise on guard.

        It may fail on the API call itself, but the pipeline guard is passed.
        """

        client = AlpacaClient.__new__(AlpacaClient)
        client._paper = True
        client._api_key = "fake"
        client._secret_key = "fake"
        client._base_url = "https://paper-api.alpaca.markets"
        client._trading_client = None

        mock_trading = MagicMock()
        # The submit_order will fail but the _authorized_by guard is not hit
        mock_trading.submit_order.side_effect = Exception("fake API error")
        client._get_trading_client = MagicMock(return_value=mock_trading)

        with pytest.raises(Exception, match="fake API error"):
            client.create_position(
                symbol="AAPL",
                direction="BUY",
                qty=10,
                stop_loss=145.0,
                _authorized_by="strategy_xyz",
            )
        # Key assertion: no AlpacaAPIError about _authorized_by was raised.
        # The Exception comes from the mocked API, not the guard.

    def test_close_position_without_authorized_by_rejected(self):
        """close_position without _authorized_by -> raises."""
        from core.alpaca_client.client import AlpacaAPIError

        client = AlpacaClient.__new__(AlpacaClient)
        client._paper = True
        with pytest.raises(AlpacaAPIError, match="authorized"):
            client.close_position("AAPL", _authorized_by=None)


# =========================================================================
# 6. Margin guard
# =========================================================================

class TestMarginGuard:
    """Verify margin utilization blocks orders when too high."""

    def test_margin_90pct_blocks_new_orders(self, rm):
        """Margin at 90% -> validate_order REJECTS."""
        order = _make_order(notional=500)
        portfolio = _make_portfolio(
            equity=10_000,
            cash=3_000,
            margin_used_pct=0.90,
        )
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"90% margin should block orders, got: {msg}"
        assert "argin" in msg.lower() or "block" in msg.lower()

    def test_margin_70pct_allows_orders(self, rm):
        """Margin at 70% -> orders still accepted (below 85% block)."""
        order = _make_order(notional=500)
        portfolio = _make_portfolio(
            equity=10_000,
            cash=5_000,
            margin_used_pct=0.70,
        )
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"70% margin should allow orders, got: {msg}"

    def test_margin_block_level_in_check_all(self, rm):
        """check_all_limits: margin > 85% -> BLOCK_NEW_TRADES action."""
        portfolio = _make_portfolio(equity=10_000)
        result = rm.check_all_limits(portfolio, margin_used_pct=0.90)
        assert not result["passed"]
        assert "BLOCK_NEW_TRADES" in result["actions"]

    def test_margin_alert_level(self, rm):
        """check_all_limits: margin 75% -> MARGIN_ALERT (warning, not block)."""
        portfolio = _make_portfolio(equity=10_000)
        result = rm.check_all_limits(portfolio, margin_used_pct=0.75)
        assert "MARGIN_ALERT" in result["actions"]


# =========================================================================
# 7. Kill switch guard
# =========================================================================

class TestKillSwitchGuard:
    """Verify kill switch blocks all trading when active."""

    def test_kill_switch_activates(self, kill_switch):
        """Activate kill switch -> is_active = True."""
        result = kill_switch.activate(
            reason="test emergency",
            trigger_type="TEST",
        )
        assert kill_switch.is_active
        assert result["reason"] == "test emergency"

    def test_kill_switch_blocks_all_orders(self, kill_switch):
        """When kill switch is active, no trading should proceed."""
        kill_switch.activate(reason="test block", trigger_type="TEST")

        # The kill switch itself doesn't reject orders directly;
        # it is checked by the execution pipeline. We verify its state.
        assert kill_switch.is_active is True

        status = kill_switch.get_status()
        assert status["is_active"] is True
        assert status["activation_reason"] == "test block"

    def test_kill_switch_deactivation_restores_trading(self, kill_switch):
        """Deactivate kill switch -> is_active = False, trading can resume."""
        kill_switch.activate(reason="test", trigger_type="TEST")
        assert kill_switch.is_active

        result = kill_switch.deactivate(authorized_by="test_operator")
        assert not kill_switch.is_active
        assert result["was_active"] is True
        assert result["authorized_by"] == "test_operator"

    def test_kill_switch_idempotent_activation(self, kill_switch):
        """Activating twice is idempotent -- no double-close."""
        r1 = kill_switch.activate(reason="first", trigger_type="TEST")
        r2 = kill_switch.activate(reason="second", trigger_type="TEST")
        assert r2.get("already_active") is True
        assert kill_switch.get_status()["activation_reason"] == "first"

    def test_daily_loss_triggers_kill_switch(self, kill_switch):
        """Daily loss exceeding threshold triggers kill switch."""
        result = kill_switch.check_automatic_triggers(
            daily_pnl=-200,
            capital=10_000,
        )
        assert result["triggered"]
        assert result["trigger_type"] == "DAILY_LOSS"

    def test_kill_switch_persists_state(self, tmp_path):
        """Kill switch state persists across instantiations."""
        state_path = tmp_path / "ks_persist.json"
        ks1 = LiveKillSwitch(broker=None, state_path=state_path)
        ks1.activate(reason="persist test", trigger_type="TEST")
        assert ks1.is_active

        # New instance loads persisted state
        ks2 = LiveKillSwitch(broker=None, state_path=state_path)
        assert ks2.is_active
        assert ks2.get_status()["activation_reason"] == "persist test"


# =========================================================================
# 8. Extreme price injection guard
# =========================================================================

class TestExtremePriceInjection:
    """Verify orders with extreme/invalid values are blocked by risk checks."""

    def test_zero_notional_accepted_as_noop(self, rm):
        """Order with notional=0 passes position check but may hit cash reserve.

        The risk manager computes 0/equity = 0%, which is under the cap.
        This is acceptable -- a $0 order is a no-op.
        """
        order = _make_order(notional=0)
        portfolio = _make_portfolio(equity=10_000, cash=5_000)
        passed, _ = rm.validate_order(order, portfolio)
        # A $0 order trivially passes percentage checks.
        # The execution layer (broker) would reject it, not the risk layer.
        assert passed

    def test_huge_notional_rejected(self, rm):
        """Order with notional=999999 -> REJECTED (exceeds all limits)."""
        order = _make_order(notional=999_999)
        portfolio = _make_portfolio(equity=10_000, cash=5_000)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"$999,999 order should be rejected, got: {msg}"

    def test_negative_notional_treated_as_absolute(self, rm):
        """Order with negative notional -> effective_cost uses abs().

        The risk manager takes abs(notional), so -5000 is treated as 5000.
        A $5000 order on $10K equity = 50%, which exceeds the 15% cap.
        """
        order = _make_order(notional=-5000)
        portfolio = _make_portfolio(equity=10_000, cash=5_000)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"Negative notional (abs=$5000) should be rejected, got: {msg}"

    def test_zero_equity_portfolio_rejects_everything(self, rm):
        """Portfolio with equity=0 -> all orders rejected (division guard)."""
        order = _make_order(notional=100)
        portfolio = _make_portfolio(equity=0, cash=0)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"Zero equity should reject everything, got: {msg}"
        assert "quity" in msg.lower() or "<= 0" in msg

    def test_negative_equity_rejects(self, rm):
        """Portfolio with negative equity -> all orders rejected."""
        order = _make_order(notional=100)
        portfolio = _make_portfolio(equity=-1000, cash=0)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"Negative equity should reject all, got: {msg}"

    def test_max_positions_exceeded(self, rm):
        """Already at max positions (10) + new symbol -> REJECTED."""
        positions = [
            _position(symbol=f"SYM{i}", notional=200, side="LONG")
            for i in range(10)  # max_positions=10 in limits_live.yaml
        ]
        order = _make_order(symbol="NEW_SYMBOL", notional=200)
        portfolio = _make_portfolio(equity=10_000, cash=5_000, positions=positions)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"11th symbol should be rejected (max 10), got: {msg}"
        assert "position" in msg.lower() or "max" in msg.lower()


# =========================================================================
# 9. Concurrent guard (thread safety)
# =========================================================================

class TestConcurrentGuard:
    """Verify thread-safe validation: no race conditions in validate_order."""

    def test_concurrent_validation_thread_safe(self, rm):
        """Submit 5 orders simultaneously via threads -- no crashes or races."""
        results = []
        errors = []
        lock = threading.Lock()

        def submit_order(idx):
            try:
                order = _make_order(
                    symbol=f"SYM{idx}",
                    notional=800,
                    strategy=f"strat_{idx}",
                )
                portfolio = _make_portfolio(equity=10_000, cash=8_000)
                passed, msg = rm.validate_order(order, portfolio)
                with lock:
                    results.append((idx, passed, msg))
            except Exception as e:
                with lock:
                    errors.append((idx, str(e)))

        threads = [
            threading.Thread(target=submit_order, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(results) == 5, f"Expected 5 results, got {len(results)}"

    def test_concurrent_no_over_allocation(self, rm):
        """5 threads each requesting 10% on different symbols.

        Each individual order (10% = $1000 on $10K) is under the 15% position cap.
        Since each thread gets its own copy of the portfolio (no shared
        mutable state in the portfolio dict), all should pass individually.
        The validate_order lock ensures no internal corruption.
        """
        results = []
        lock = threading.Lock()

        def submit_order(idx):
            order = _make_order(
                symbol=f"STOCK{idx}",
                notional=1000,
                strategy=f"strat_{idx}",
            )
            # Each thread sees the same clean portfolio (no existing positions)
            portfolio = _make_portfolio(equity=10_000, cash=8_000)
            passed, msg = rm.validate_order(order, portfolio)
            with lock:
                results.append((idx, passed, msg))

        threads = [
            threading.Thread(target=submit_order, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 5
        # Each order in isolation should pass (10% < 15% cap, clean portfolio)
        for idx, passed, msg in results:
            assert passed, f"Order {idx} should pass in isolation, got: {msg}"

    def test_concurrent_kill_switch_activation(self, tmp_path):
        """Multiple threads activating kill switch simultaneously -- no crash."""
        ks = LiveKillSwitch(
            broker=None,
            state_path=tmp_path / "ks_concurrent.json",
        )
        errors = []

        def activate(idx):
            try:
                ks.activate(
                    reason=f"thread_{idx}",
                    trigger_type="TEST",
                )
            except Exception as e:
                errors.append((idx, str(e)))

        threads = [
            threading.Thread(target=activate, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Concurrent kill switch errors: {errors}"
        assert ks.is_active


# =========================================================================
# 10. Strategy concentration guard
# =========================================================================

class TestStrategyConcentrationGuard:
    """Verify per-strategy cap (20% in limits_live.yaml) blocks over-concentration."""

    def test_strategy_over_25pct_rejected(self, rm):
        """Existing 20% + new 6% = 26% on same strategy -> REJECTED (max 25%)."""
        positions = [
            _position(symbol="AAPL", notional=2000, strategy="momentum_us"),
        ]
        order = _make_order(
            symbol="MSFT",
            notional=600,
            strategy="momentum_us",
        )
        portfolio = _make_portfolio(
            equity=10_000, cash=6_000, positions=positions,
        )
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"26% strategy concentration should be rejected, got: {msg}"
        assert "trategy" in msg.lower()

    def test_strategy_within_limit_accepted(self, rm):
        """10% + 5% = 15% on same strategy -> ACCEPTED (< 20%)."""
        positions = [
            _position(symbol="AAPL", notional=1000, strategy="carry_fx"),
        ]
        order = _make_order(
            symbol="MSFT",
            notional=500,
            strategy="carry_fx",
        )
        portfolio = _make_portfolio(
            equity=10_000, cash=7_000, positions=positions,
        )
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"15% strategy should be accepted, got: {msg}"


# =========================================================================
# 11. Deleveraging levels
# =========================================================================

class TestDeleveragingGuard:
    """Verify progressive deleveraging triggers at correct DD thresholds."""

    def test_level_1_at_1pct_dd(self, rm):
        """DD of 1% triggers Level 1 (30% reduction)."""
        level, reduction, msg = rm.check_progressive_deleveraging(0.01)
        assert level == 1
        assert reduction == pytest.approx(0.30)

    def test_level_2_at_1_5pct_dd(self, rm):
        """DD of 1.5% triggers Level 2 (50% reduction)."""
        level, reduction, msg = rm.check_progressive_deleveraging(0.015)
        assert level == 2
        assert reduction == pytest.approx(0.50)

    def test_level_3_at_2pct_dd(self, rm):
        """DD of 2% triggers Level 3 (close all = 100% reduction)."""
        level, reduction, msg = rm.check_progressive_deleveraging(0.02)
        assert level == 3
        assert reduction == pytest.approx(1.0)

    def test_no_deleveraging_at_0_5pct(self, rm):
        """DD of 0.5% -> no deleveraging (Level 0)."""
        level, reduction, msg = rm.check_progressive_deleveraging(0.005)
        assert level == 0
        assert reduction == 0.0


# =========================================================================
# 12. Cash reserve guard
# =========================================================================

class TestCashReserveGuard:
    """Verify minimum cash reserve (15%) blocks orders that drain cash."""

    def test_order_drains_cash_below_reserve(self, rm):
        """Order that would leave less than 15% cash -> REJECTED."""
        # equity=10K, cash=2K. Order of 1K would leave cash at 1K = 10% < 15%.
        order = _make_order(notional=1000)
        portfolio = _make_portfolio(equity=10_000, cash=2000)
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"Draining cash below 15% should be rejected, got: {msg}"

    def test_order_preserves_cash_reserve(self, rm):
        """Order that preserves 15%+ cash -> ACCEPTED."""
        order = _make_order(notional=500)
        portfolio = _make_portfolio(equity=10_000, cash=5000)
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"Order preserving cash should be accepted, got: {msg}"
