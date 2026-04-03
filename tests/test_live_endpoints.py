"""
Tests for live trading dashboard API endpoints.

All dependencies are mocked -- no FastAPI server needed, no real broker.
Uses the create_live_router() factory with mock objects, then calls
endpoint functions directly via FastAPI TestClient.
"""

from unittest.mock import MagicMock, patch

import pytest

# We test the create_live_router factory and its endpoints
# FastAPI is needed for TestClient -- skip all tests if not installed
fastapi = pytest.importorskip("fastapi")
from api.live_endpoints import create_live_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

# =========================================================================
# Fixtures
# =========================================================================

def _make_trade_journal(
    pnl_today=None,
    pnl_mtd=None,
    pnl_ytd=None,
    pnl_7d=None,
    pnl_30d=None,
    trades=None,
    open_trades=None,
    daily_summary=None,
):
    """Create a mock TradeJournal with configurable return values."""
    journal = MagicMock()

    default_pnl = {
        "period": "today",
        "pnl_gross": 150.0,
        "pnl_net": 120.0,
        "total_commission": 30.0,
        "n_trades": 5,
        "win_rate": 60.0,
    }

    pnl_map = {
        "today": pnl_today or default_pnl,
        "mtd": pnl_mtd or {**default_pnl, "period": "mtd", "pnl_net": 1200.0},
        "ytd": pnl_ytd or {**default_pnl, "period": "ytd", "pnl_net": 5000.0},
        "7d": pnl_7d or {**default_pnl, "period": "7d", "pnl_net": 350.0},
        "30d": pnl_30d or {**default_pnl, "period": "30d", "pnl_net": 1200.0, "n_trades": 55, "win_rate": 58.0},
    }
    journal.get_pnl.side_effect = lambda period: pnl_map.get(period, default_pnl)

    journal.get_trades.return_value = trades or [
        {"trade_id": "LIVE-001", "strategy": "ORB_5MIN", "instrument": "AAPL",
         "direction": "LONG", "pnl_net": 50.0, "status": "CLOSED"},
        {"trade_id": "LIVE-002", "strategy": "VWAP_MR", "instrument": "MSFT",
         "direction": "SHORT", "pnl_net": -20.0, "status": "CLOSED"},
    ]

    journal.get_open_trades.return_value = open_trades or [
        {"instrument": "TSLA", "strategy": "ORB_5MIN", "timestamp_filled": "2026-03-27T15:35:00",
         "stop_loss": 150.0, "take_profit": 170.0},
    ]

    journal.get_daily_summary.return_value = daily_summary or {
        "date": "2026-03-27",
        "total_trades": 5,
        "closed_trades": 4,
        "winners": 3,
        "losers": 1,
        "win_rate": 75.0,
        "pnl_net": 120.0,
    }

    return journal


def _make_broker(positions=None, account=None, error=False):
    """Create a mock broker."""
    broker = MagicMock()
    if error:
        broker.get_positions.side_effect = ConnectionError("Broker unavailable")
        broker.get_account_info.side_effect = ConnectionError("Broker unavailable")
    else:
        broker.get_positions.return_value = positions or [
            {"symbol": "TSLA", "qty": "10", "avg_entry": "155.00",
             "current_price": "160.00", "unrealized_pl": "50.0",
             "unrealized_plpc": "0.032", "market_val": "1600.0"},
            {"symbol": "NVDA", "qty": "-5", "avg_entry": "800.00",
             "current_price": "790.00", "unrealized_pl": "50.0",
             "unrealized_plpc": "0.0125", "market_val": "-3950.0"},
        ]
        broker.get_account_info.return_value = account or {
            "equity": 100000.0,
            "cash": 50000.0,
            "margin_used": 30000.0,
        }
    return broker


def _make_kill_switch(active=False, armed=True, history=None):
    """Create a mock kill switch."""
    ks = MagicMock()
    ks.is_active = active
    ks.is_armed = armed
    ks.get_status.return_value = {
        "is_active": active,
        "is_armed": armed,
        "activated_at": "2026-03-27T16:00:00Z" if active else None,
        "activation_reason": "Daily loss exceeded" if active else None,
        "activation_trigger": "DAILY_LOSS" if active else None,
        "thresholds": {"daily_loss_pct": 0.015},
        "mc_overrides": {},
        "disabled_strategies": ["ORB_5MIN"] if active else [],
        "total_activations": 1 if active else 0,
    }
    ks.get_history.return_value = history or []
    return ks


def _make_leverage_manager():
    """Create a mock leverage manager."""
    lm = MagicMock()
    lm.get_status.return_value = {
        "current_phase": "PHASE_1",
        "max_leverage": 1.5,
        "days_in_phase": 12,
        "min_duration_days": 30,
        "duration_met": False,
        "advance_conditions": {"sharpe_30d": 1.0, "drawdown_pct": 0.05},
        "next_phase": "PHASE_2",
        "next_max_leverage": 2.0,
        "phase_start_date": "2026-03-15",
        "history": [],
    }
    return lm


def _make_slippage_tracker(summary=None, alerts=None):
    """Create a mock slippage tracker."""
    st = MagicMock()
    st.get_summary.return_value = summary or {
        "by_strategy": {"ORB_5MIN": 1.5, "VWAP_MR": 2.1},
        "by_instrument_type": {"EQUITY": 1.8},
        "by_order_type": {"MARKET": 2.0, "LIMIT": 0.8},
        "ratio_real_vs_backtest": 0.9,
        "worst_trades": [
            {"trade_id": "T-001", "slippage_bps": 5.2, "strategy": "ORB_5MIN"}
        ],
        "total_cost_from_slippage": 12.50,
    }
    st.check_alerts.return_value = alerts or []
    return st


def _make_cost_tracker(report=None):
    """Create a mock cost tracker."""
    ct = MagicMock()
    ct.get_cost_report.return_value = report or {
        "total_commission": 85.0,
        "total_pnl_gross": 1200.0,
        "total_pnl_net": 1115.0,
        "cost_ratio": 0.071,
        "avg_commission_per_trade": 1.70,
        "by_strategy": {
            "ORB_5MIN": {"commission": 50.0, "pnl_gross": 800.0, "cost_ratio": 0.0625},
        },
    }
    return ct


def _make_var_calculator(history=None):
    """Create a mock VaR calculator."""
    vc = MagicMock()
    vc.get_var_history.return_value = history or [
        {"date": "2026-03-26", "portfolio_var_95": 1500.0, "var_pct_of_capital": 0.015},
        {"date": "2026-03-27", "portfolio_var_95": 1400.0, "var_pct_of_capital": 0.014},
    ]
    return vc


def _make_reconciliation(stats=None):
    """Create a mock reconciliation."""
    recon = MagicMock()
    recon.get_stats.return_value = stats or {
        "total_runs": 100,
        "total_divergences": 2,
        "divergence_rate": 0.02,
        "avg_divergences_per_run": 0.02,
        "last_run": "2026-03-27T16:00:00Z",
        "last_divergence": "2026-03-25T14:30:00Z",
        "orphan_count": 1,
        "phantom_count": 0,
    }
    return recon


def _create_app(**kwargs):
    """Helper to create a FastAPI app with the live router."""
    app = FastAPI()
    router = create_live_router(**kwargs)
    assert router is not None, "Router should not be None when FastAPI is installed"
    app.include_router(router)
    return TestClient(app)


# =========================================================================
# Tests
# =========================================================================

class TestOverview:
    """Tests for GET /api/live/overview."""

    def test_overview_all_components(self):
        """Overview returns all fields when all components are configured."""
        client = _create_app(
            trade_journal=_make_trade_journal(),
            broker=_make_broker(),
            leverage_manager=_make_leverage_manager(),
            kill_switch=_make_kill_switch(),
        )
        resp = client.get("/api/live/overview")
        assert resp.status_code == 200
        data = resp.json()

        assert data["mode"] == "LIVE"
        assert data["pnl_today"] == 120.0
        assert data["pnl_mtd"] == 1200.0
        assert data["pnl_ytd"] == 5000.0
        assert data["positions_count"] == 2
        assert data["equity"] == 100000.0
        assert data["margin_used_pct"] == 0.3
        assert data["leverage_max"] == 1.5
        assert data["phase"] == "PHASE_1"
        assert data["kill_switch_active"] is False
        assert data["system_status"] == "OK"
        assert "timestamp" in data

    def test_overview_broker_error_graceful(self):
        """Overview degrades gracefully when broker is down."""
        client = _create_app(
            trade_journal=_make_trade_journal(),
            broker=_make_broker(error=True),
            kill_switch=_make_kill_switch(),
        )
        resp = client.get("/api/live/overview")
        assert resp.status_code == 200
        data = resp.json()

        assert data["positions_count"] == -1
        assert "broker_error" in data
        assert data["system_status"] == "WARNING"

    def test_overview_no_components(self):
        """Overview works with no components (minimal response)."""
        client = _create_app()
        resp = client.get("/api/live/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "LIVE"
        assert data["system_status"] == "OK"


class TestPositions:
    """Tests for GET /api/live/positions."""

    def test_positions_from_broker(self):
        """Positions returns broker data enriched with journal info."""
        client = _create_app(
            broker=_make_broker(),
            trade_journal=_make_trade_journal(),
        )
        resp = client.get("/api/live/positions")
        assert resp.status_code == 200
        data = resp.json()

        assert data["count"] == 2
        assert len(data["positions"]) == 2

        tsla = data["positions"][0]
        assert tsla["symbol"] == "TSLA"
        assert tsla["direction"] == "LONG"
        assert tsla["quantity"] == 10.0

        nvda = data["positions"][1]
        assert nvda["direction"] == "SHORT"
        assert nvda["quantity"] == 5.0

    def test_positions_no_broker(self):
        """Positions returns empty list when broker not connected."""
        client = _create_app()
        resp = client.get("/api/live/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["positions"] == []
        assert data["count"] == 0
        assert "error" in data

    def test_positions_broker_error_503(self):
        """Positions returns 503 when broker raises exception."""
        client = _create_app(broker=_make_broker(error=True))
        resp = client.get("/api/live/positions")
        assert resp.status_code == 503


class TestPnl:
    """Tests for GET /api/live/pnl."""

    def test_pnl_today(self):
        """P&L today returns correct data."""
        client = _create_app(trade_journal=_make_trade_journal())
        resp = client.get("/api/live/pnl?period=today")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "today"
        assert data["pnl_net"] == 120.0
        assert data["pnl_gross"] == 150.0
        assert data["n_trades"] == 5
        assert "daily_summary" in data

    def test_pnl_different_periods(self):
        """P&L works for all valid periods."""
        client = _create_app(trade_journal=_make_trade_journal())
        for period in ["today", "7d", "30d", "mtd", "ytd"]:
            resp = client.get(f"/api/live/pnl?period={period}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["period"] == period
            assert "pnl_net" in data

    def test_pnl_invalid_period(self):
        """P&L rejects invalid period."""
        client = _create_app(trade_journal=_make_trade_journal())
        resp = client.get("/api/live/pnl?period=invalid")
        assert resp.status_code == 422  # FastAPI validation error

    def test_pnl_no_journal(self):
        """P&L returns 0 when journal not configured."""
        client = _create_app()
        resp = client.get("/api/live/pnl?period=today")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pnl_net"] == 0
        assert "error" in data


class TestExecution:
    """Tests for GET /api/live/execution."""

    def test_execution_slippage_and_costs(self):
        """Execution returns both slippage and cost data."""
        client = _create_app(
            slippage_tracker=_make_slippage_tracker(),
            cost_tracker=_make_cost_tracker(),
        )
        resp = client.get("/api/live/execution?period=7d")
        assert resp.status_code == 200
        data = resp.json()

        assert "slippage" in data
        assert "costs" in data
        assert data["slippage"]["by_strategy"]["ORB_5MIN"] == 1.5
        assert data["costs"]["total_commission"] == 85.0


class TestRisk:
    """Tests for GET /api/live/risk."""

    def test_risk_all_components(self):
        """Risk returns VaR, kill switch, and reconciliation data."""
        client = _create_app(
            var_calculator=_make_var_calculator(),
            kill_switch=_make_kill_switch(),
            reconciliation=_make_reconciliation(),
        )
        resp = client.get("/api/live/risk")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["var_history"]) == 2
        assert data["kill_switch"]["is_active"] is False
        assert data["reconciliation"]["total_runs"] == 100
        assert data["reconciliation"]["divergence_rate"] == 0.02


class TestKpi:
    """Tests for GET /api/live/kpi."""

    def test_kpi_conditions_evaluation(self):
        """KPI evaluates conditions correctly."""
        client = _create_app(
            trade_journal=_make_trade_journal(),
            leverage_manager=_make_leverage_manager(),
            kill_switch=_make_kill_switch(),
        )
        resp = client.get("/api/live/kpi")
        assert resp.status_code == 200
        data = resp.json()

        assert "conditions" in data
        assert len(data["conditions"]) >= 3

        # Check min_trades condition (2 trades < 50 threshold)
        min_trades = next(c for c in data["conditions"] if c["name"] == "min_trades")
        assert min_trades["threshold"] == 50
        assert min_trades["passed"] is False  # Only 2 mock trades

        # Check pnl_30d_positive
        pnl_cond = next(c for c in data["conditions"] if c["name"] == "pnl_30d_positive")
        assert pnl_cond["passed"] is True  # 1200 > 0

        # Kill switch condition
        ks_cond = next(c for c in data["conditions"] if c["name"] == "no_kill_switch_activations")
        assert ks_cond["passed"] is True  # 0 activations

        assert "leverage" in data
        assert data["overall"] == "FAIL"  # min_trades not met


class TestTrades:
    """Tests for GET /api/live/trades."""

    def test_trades_list_with_limit(self):
        """Trades returns list respecting limit."""
        client = _create_app(trade_journal=_make_trade_journal())
        resp = client.get("/api/live/trades?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["trades"]) == 2
        assert data["count"] == 2

    def test_trades_with_strategy_filter(self):
        """Trades passes strategy filter to journal."""
        journal = _make_trade_journal()
        client = _create_app(trade_journal=journal)
        resp = client.get("/api/live/trades?strategy=ORB_5MIN&limit=10")
        assert resp.status_code == 200
        journal.get_trades.assert_called_once_with(
            strategy="ORB_5MIN", status=None, limit=10
        )

    def test_trades_no_journal(self):
        """Trades returns empty when journal not configured."""
        client = _create_app()
        resp = client.get("/api/live/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trades"] == []


class TestAlerts:
    """Tests for GET /api/live/alerts."""

    def test_alerts_from_kill_switch_history(self):
        """Alerts includes kill switch activation events."""
        ks = _make_kill_switch(history=[
            {"action": "ACTIVATE", "reason": "Daily loss exceeded -1.5%",
             "timestamp": "2026-03-27T16:00:00Z"},
            {"action": "DEACTIVATE", "reason": "Manual resume",
             "timestamp": "2026-03-27T17:00:00Z"},
        ])
        client = _create_app(kill_switch=ks)
        resp = client.get("/api/live/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 2
        assert data["alerts"][0]["source"] == "kill_switch"

    def test_alerts_from_slippage(self):
        """Alerts includes slippage warnings."""
        st = _make_slippage_tracker(alerts=[
            {"strategy": "ORB_5MIN", "level": "warning",
             "avg_slippage_bps": 4.5, "avg_ratio": 2.25, "n_trades": 10},
        ])
        client = _create_app(slippage_tracker=st)
        resp = client.get("/api/live/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["source"] == "slippage"
        assert data["alerts"][0]["level"] == "warning"


class TestComparison:
    """Tests for GET /api/live/comparison."""

    def test_comparison_live_vs_paper(self):
        """Comparison returns data for all periods."""
        live_journal = _make_trade_journal()
        paper_journal = _make_trade_journal(
            pnl_today={"pnl_net": 100.0, "n_trades": 4, "win_rate": 50.0,
                        "pnl_gross": 130.0, "total_commission": 30.0, "period": "today"},
        )
        client = _create_app(trade_journal=live_journal, paper_journal=paper_journal)
        resp = client.get("/api/live/comparison")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["comparison"]) == 3  # today, 7d, 30d

    def test_comparison_missing_journal(self):
        """Comparison returns error when journals missing."""
        client = _create_app(trade_journal=_make_trade_journal())
        resp = client.get("/api/live/comparison")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


class TestHealth:
    """Tests for GET /api/live/health."""

    def test_health_all_ok(self):
        """Health returns OK when all components are healthy."""
        client = _create_app(
            broker=_make_broker(),
            kill_switch=_make_kill_switch(),
            reconciliation=_make_reconciliation(),
            trade_journal=_make_trade_journal(),
            var_calculator=_make_var_calculator(),
            leverage_manager=_make_leverage_manager(),
        )
        resp = client.get("/api/live/health")
        assert resp.status_code == 200
        data = resp.json()

        assert data["overall"] == "OK"
        assert data["components"]["broker"]["status"] == "OK"
        assert data["components"]["kill_switch"]["status"] == "ARMED"
        assert data["components"]["reconciliation"]["status"] == "OK"
        assert data["components"]["trade_journal"]["status"] == "OK"
        assert data["components"]["var_calculator"]["status"] == "OK"
        assert data["components"]["leverage_manager"]["status"] == "OK"

    def test_health_broker_down_degraded(self):
        """Health returns DEGRADED when broker is down."""
        client = _create_app(
            broker=_make_broker(error=True),
            kill_switch=_make_kill_switch(),
        )
        resp = client.get("/api/live/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"] == "DEGRADED"
        assert data["components"]["broker"]["status"] == "ERROR"

    def test_health_kill_switch_active_critical(self):
        """Health returns CRITICAL when kill switch is active."""
        client = _create_app(
            broker=_make_broker(),
            kill_switch=_make_kill_switch(active=True),
        )
        resp = client.get("/api/live/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"] == "CRITICAL"
        assert data["components"]["kill_switch"]["status"] == "ACTIVE"

    def test_health_reconciliation_warning(self):
        """Health shows WARNING when reconciliation divergence is high."""
        recon = _make_reconciliation(stats={
            "total_runs": 100,
            "total_divergences": 10,
            "divergence_rate": 0.10,  # > 0.05 threshold
            "avg_divergences_per_run": 0.1,
            "last_run": "2026-03-27T16:00:00Z",
            "last_divergence": "2026-03-27T15:00:00Z",
            "orphan_count": 5,
            "phantom_count": 2,
        })
        client = _create_app(
            broker=_make_broker(),
            reconciliation=recon,
        )
        resp = client.get("/api/live/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["components"]["reconciliation"]["status"] == "WARNING"
        assert data["overall"] == "WARNING"

    def test_health_no_components(self):
        """Health returns NO_COMPONENTS when nothing configured."""
        client = _create_app()
        resp = client.get("/api/live/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"] == "NO_COMPONENTS"


class TestSystemStatus:
    """Tests for _determine_system_status logic via overview endpoint."""

    def test_status_ok(self):
        """System status OK when margin < 70%."""
        client = _create_app(
            broker=_make_broker(account={"equity": 100000, "cash": 50000, "margin_used": 30000}),
            kill_switch=_make_kill_switch(active=False),
        )
        resp = client.get("/api/live/overview")
        assert resp.json()["system_status"] == "OK"

    def test_status_warning_high_margin(self):
        """System status WARNING when margin 70-85%."""
        client = _create_app(
            broker=_make_broker(account={"equity": 100000, "cash": 20000, "margin_used": 75000}),
            kill_switch=_make_kill_switch(active=False),
        )
        resp = client.get("/api/live/overview")
        assert resp.json()["system_status"] == "WARNING"

    def test_status_critical_margin_over_85(self):
        """System status CRITICAL when margin > 85%."""
        client = _create_app(
            broker=_make_broker(account={"equity": 100000, "cash": 5000, "margin_used": 90000}),
            kill_switch=_make_kill_switch(active=False),
        )
        resp = client.get("/api/live/overview")
        assert resp.json()["system_status"] == "CRITICAL"

    def test_status_critical_kill_switch(self):
        """System status CRITICAL when kill switch is active."""
        client = _create_app(
            broker=_make_broker(),
            kill_switch=_make_kill_switch(active=True),
        )
        resp = client.get("/api/live/overview")
        assert resp.json()["system_status"] == "CRITICAL"


class TestRouterCreation:
    """Tests for the router factory itself."""

    def test_router_returns_none_without_fastapi(self):
        """Router returns None when FastAPI is not importable."""
        with patch.dict("sys.modules", {"fastapi": None}):
            # Reimport to test the ImportError path
            import importlib

            import api.live_endpoints as mod
            importlib.reload(mod)

            # Force ImportError by patching
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

            def mock_import(name, *args, **kwargs):
                if name == "fastapi":
                    raise ImportError("No module named 'fastapi'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = mod.create_live_router()
                assert result is None

            # Reload to restore normal behavior
            importlib.reload(mod)
