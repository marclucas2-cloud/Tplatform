"""
Tests for HARDEN-003: Signal-Once Dual Routing.

Covers:
  1. Signal generated once -> routed to live AND paper
  2. Same signal produces same trade request (price, sizing)
  3. Live rejected by risk but paper accepted -> divergence logged
  4. Both pipelines use same timestamp from market data
  5. Sync report: 100% signals routed to both
  6. Signal for paper-only strategy -> routed to paper ONLY
  7. Signal for live strategy -> routed to BOTH
  8. SignalComparator compare returns correct divergences
  9. SignalComparator get_sync_stats tracks totals
  10. process_signal_dual returns complete result dict
  11. Signal ID format validation
  12. _get_live_strategies returns correct list
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.signal_comparator import SignalComparator
from core.trading_engine import Pipeline, PipelineConfig, TradingEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def live_config():
    """PipelineConfig for a LIVE pipeline with FX strategies."""
    return PipelineConfig(
        mode="LIVE",
        broker_type="ibkr",
        strategies=["fx_eurusd_trend", "fx_eurgbp_mr"],
        capital=10_000,
        risk_limits_path="config/limits_live.yaml",
        broker_port=4001,
        log_dir="logs/test_live",
    )


@pytest.fixture
def paper_us_config():
    """PipelineConfig for a PAPER US pipeline."""
    return PipelineConfig(
        mode="PAPER",
        broker_type="alpaca",
        strategies=["day_of_week_seasonal", "vix_expansion_short", "fx_eurusd_trend"],
        capital=100_000,
        risk_limits_path="config/limits.yaml",
        log_dir="logs/test_paper_us",
    )


@pytest.fixture
def paper_eu_config():
    """PipelineConfig for a PAPER EU pipeline."""
    return PipelineConfig(
        mode="PAPER",
        broker_type="ibkr",
        strategies=["eu_gap_open", "brent_lag_play"],
        capital=1_000_000,
        broker_port=7497,
        log_dir="logs/test_paper_eu",
    )


@pytest.fixture
def mock_broker():
    """A mock broker for paper pipelines."""
    broker = MagicMock()
    broker.name = "mock_paper_broker"
    broker.is_paper = True
    broker.authenticate.return_value = {
        "status": "ok", "equity": 100_000, "cash": 90_000, "paper": True
    }
    broker.get_account_info.return_value = {"equity": 100_000, "cash": 90_000}
    broker.get_positions.return_value = []
    broker.cancel_all_orders.return_value = 0
    broker.create_position.return_value = {
        "orderId": "paper-001", "symbol": "EURUSD", "status": "filled",
        "qty": 25000, "filled_price": 1.0850,
    }
    return broker


@pytest.fixture
def mock_live_broker():
    """A mock broker for live pipeline."""
    broker = MagicMock()
    broker.name = "ibkr_live"
    broker.is_paper = False
    broker.authenticate.return_value = {
        "status": "ok", "equity": 10_000, "cash": 8_000, "paper": False
    }
    broker.get_account_info.return_value = {"equity": 10_000, "cash": 8_000}
    broker.get_positions.return_value = []
    broker.cancel_all_orders.return_value = 0
    broker.create_position.return_value = {
        "orderId": "live-001", "symbol": "EURUSD", "status": "filled",
        "qty": 25000, "filled_price": 1.0850,
    }
    return broker


@pytest.fixture
def mock_risk_manager_accept():
    """A mock risk manager that always accepts."""
    rm = MagicMock()
    rm.validate_order.return_value = (True, "OK")
    return rm


@pytest.fixture
def mock_risk_manager_reject():
    """A mock risk manager that always rejects."""
    rm = MagicMock()
    rm.validate_order.return_value = (False, "Position too large for capital")
    return rm


@pytest.fixture
def comparator_tmpdir():
    """Temporary directory for SignalComparator logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _build_active_pipeline(config, broker, risk_manager):
    """Build a pipeline with mocked broker and risk manager, set active."""
    pipeline = Pipeline(config)
    pipeline._broker = broker
    pipeline._risk_manager = risk_manager
    pipeline._active = True
    return pipeline


def _build_engine(
    live_config, paper_us_config, mock_live_broker, mock_broker,
    live_rm, paper_rm,
):
    """Build a TradingEngine with live + paper pipelines, all active."""
    engine = TradingEngine()

    live_pipeline = _build_active_pipeline(live_config, mock_live_broker, live_rm)
    paper_pipeline = _build_active_pipeline(paper_us_config, mock_broker, paper_rm)

    engine.add_pipeline("live_ibkr", live_pipeline)
    engine.add_pipeline("paper_us", paper_pipeline)

    return engine


# ---------------------------------------------------------------------------
# Test 1: Signal generated once -> routed to live AND paper
# ---------------------------------------------------------------------------

class TestSignalOnceRouting:
    def test_signal_generated_once_routed_to_both(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """A single signal generation routes to both live and paper pipelines."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        test_signal = {
            "symbol": "EURUSD",
            "direction": "BUY",
            "qty": 25000,
            "stop_loss": 1.0800,
            "take_profit": 1.0950,
            "timestamp": "2026-03-27T15:00:00Z",
        }

        # Override signal generation to return our test signal
        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend",
                {"close": 1.0850},
                "intraday",
            )

        # Signal was routed to live
        assert result["live_result"] is not None
        assert result["live_result"]["signal_id"] == result["signal_id"]
        assert result["live_result"]["mode"] == "LIVE"

        # Signal was routed to paper
        assert len(result["paper_results"]) >= 1
        assert result["paper_results"][0]["mode"] == "PAPER"


# ---------------------------------------------------------------------------
# Test 2: Same signal produces same trade request
# ---------------------------------------------------------------------------

    def test_same_signal_same_trade_request(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """Both pipelines receive the exact same signal dict."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        test_signal = {
            "symbol": "EURUSD",
            "direction": "BUY",
            "qty": 25000,
            "stop_loss": 1.0800,
            "take_profit": 1.0950,
        }

        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend", {"close": 1.0850}, "intraday"
            )

        # Both brokers received the same symbol and direction
        live_call = mock_live_broker.create_position.call_args
        paper_call = mock_broker.create_position.call_args

        assert live_call is not None
        assert paper_call is not None
        assert live_call.kwargs.get("symbol") == paper_call.kwargs.get("symbol")
        assert live_call.kwargs.get("direction") == paper_call.kwargs.get("direction")
        assert live_call.kwargs.get("qty") == paper_call.kwargs.get("qty")


# ---------------------------------------------------------------------------
# Test 3: Live rejected by risk but paper accepted -> divergence logged
# ---------------------------------------------------------------------------

    def test_live_rejected_paper_accepted_divergence(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept, mock_risk_manager_reject,
    ):
        """When live risk rejects but paper accepts, a divergence is logged."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_reject,  # live rejects
            mock_risk_manager_accept,  # paper accepts
        )

        test_signal = {
            "symbol": "EURUSD",
            "direction": "BUY",
            "qty": 25000,
        }

        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend", {"close": 1.0850}, "intraday"
            )

        # Live was rejected
        assert result["live_result"]["passed_risk"] is False

        # Paper was accepted
        assert any(pr["passed_risk"] is True for pr in result["paper_results"])

        # Comparison should show divergence
        assert result["comparison"]["match"] is False
        assert len(result["comparison"]["divergences"]) > 0
        assert any("risk_divergence" in d for d in result["comparison"]["divergences"])


# ---------------------------------------------------------------------------
# Test 4: Both pipelines use same timestamp from market data
# ---------------------------------------------------------------------------

    def test_both_pipelines_use_same_timestamp(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """The market data timestamp in the signal is identical for both."""
        market_timestamp = "2026-03-27T15:35:00Z"
        test_signal = {
            "symbol": "EURUSD",
            "direction": "BUY",
            "qty": 25000,
            "timestamp": market_timestamp,
        }

        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend", {"close": 1.0850, "timestamp": market_timestamp}
            )

        # The signal stored in result is the SAME object
        assert result["signal"]["timestamp"] == market_timestamp
        # Same signal_id in both live and paper results
        assert result["live_result"]["signal_id"] == result["signal_id"]
        for pr in result["paper_results"]:
            assert pr["signal_id"] == result["signal_id"]


# ---------------------------------------------------------------------------
# Test 5: Sync report: 100% signals routed to both
# ---------------------------------------------------------------------------

    def test_sync_report_all_signals_routed(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """After multiple signals, sync stats show 100% match rate."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        signals = [
            {"symbol": "EURUSD", "direction": "BUY", "qty": 25000},
            {"symbol": "EURUSD", "direction": "SELL", "qty": 25000},
            {"symbol": "EURGBP", "direction": "BUY", "qty": 20000},
        ]

        strategies = ["fx_eurusd_trend", "fx_eurusd_trend", "fx_eurgbp_mr"]

        for sig, strat in zip(signals, strategies):
            with patch.object(
                engine, "_generate_signal_from_data", return_value=sig
            ):
                engine.process_signal_dual(strat, {"close": 1.0}, "intraday")

        stats = engine._signal_comparator.get_sync_stats()
        assert stats["total_signals"] == 3
        assert stats["matched"] == 3
        assert stats["diverged"] == 0
        assert stats["match_rate"] == 1.0


# ---------------------------------------------------------------------------
# Test 6: Signal for paper-only strategy -> routed to paper ONLY
# ---------------------------------------------------------------------------

    def test_paper_only_strategy_not_routed_to_live(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """A strategy only in paper pipelines is NOT routed to live."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        test_signal = {
            "symbol": "SPY",
            "direction": "BUY",
            "qty": 100,
        }

        # day_of_week_seasonal is paper-only (not in live_config strategies)
        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "day_of_week_seasonal", {"close": 450.0}, "intraday"
            )

        # live_result should be None (not routed to live)
        assert result["live_result"] is None

        # Should be routed to paper
        assert len(result["paper_results"]) >= 1


# ---------------------------------------------------------------------------
# Test 7: Signal for live strategy -> routed to BOTH
# ---------------------------------------------------------------------------

    def test_live_strategy_routed_to_both(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """A strategy in the live set is routed to BOTH live and paper."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        test_signal = {
            "symbol": "EURUSD",
            "direction": "BUY",
            "qty": 25000,
        }

        # fx_eurusd_trend is in both live and paper configs
        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend", {"close": 1.0850}, "intraday"
            )

        assert result["live_result"] is not None
        assert result["live_result"]["mode"] == "LIVE"
        assert len(result["paper_results"]) >= 1
        assert any(pr["mode"] == "PAPER" for pr in result["paper_results"])


# ---------------------------------------------------------------------------
# Test 8: SignalComparator compare returns correct divergences
# ---------------------------------------------------------------------------

class TestSignalComparator:
    def test_compare_returns_divergences_on_risk_mismatch(self, comparator_tmpdir):
        """compare() detects risk acceptance divergence."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        result = comparator.compare(
            signal_id="SIG_test_001",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD", "direction": "BUY", "qty": 25000},
            live_result={"passed_risk": False, "error": "too large"},
            paper_results=[{"passed_risk": True, "mode": "paper_us", "order_result": {}}],
        )

        assert result["match"] is False
        assert len(result["divergences"]) > 0
        assert any("risk_divergence" in d for d in result["divergences"])

    def test_compare_returns_match_when_both_accept(self, comparator_tmpdir):
        """compare() returns match=True when both accept."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        result = comparator.compare(
            signal_id="SIG_test_002",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD", "direction": "BUY", "qty": 25000},
            live_result={
                "passed_risk": True,
                "order_result": {"qty": 25000, "filled_price": 1.0850},
            },
            paper_results=[{
                "passed_risk": True,
                "mode": "paper_us",
                "order_result": {"qty": 25000, "filled_price": 1.0850},
            }],
        )

        assert result["match"] is True
        assert result["divergences"] == []

    def test_compare_detects_sizing_divergence(self, comparator_tmpdir):
        """compare() detects when live and paper have different qty."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        result = comparator.compare(
            signal_id="SIG_test_003",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD", "direction": "BUY"},
            live_result={
                "passed_risk": True,
                "order_result": {"qty": 25000, "filled_price": 1.0850},
            },
            paper_results=[{
                "passed_risk": True,
                "mode": "paper_us",
                "order_result": {"qty": 50000, "filled_price": 1.0850},
            }],
        )

        assert result["match"] is False
        assert any("sizing_divergence" in d for d in result["divergences"])

    def test_compare_detects_price_divergence(self, comparator_tmpdir):
        """compare() detects when fill prices differ significantly (> $0.01)."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        result = comparator.compare(
            signal_id="SIG_test_004",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD", "direction": "BUY"},
            live_result={
                "passed_risk": True,
                "order_result": {"qty": 25000, "filled_price": 1.0850},
            },
            paper_results=[{
                "passed_risk": True,
                "mode": "paper_us",
                "order_result": {"qty": 25000, "filled_price": 1.1000},
            }],
        )

        assert result["match"] is False
        assert any("price_divergence" in d for d in result["divergences"])

    def test_compare_paper_only_no_live(self, comparator_tmpdir):
        """compare() with no live result (paper-only strategy) still works."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        result = comparator.compare(
            signal_id="SIG_test_005",
            strategy="day_of_week_seasonal",
            signal={"symbol": "SPY", "direction": "BUY", "qty": 100},
            live_result=None,
            paper_results=[{
                "passed_risk": True,
                "mode": "paper_us",
                "order_result": {"qty": 100, "filled_price": 450.0},
            }],
        )

        # No live result -> no divergence possible
        assert result["match"] is True
        assert result["divergences"] == []


# ---------------------------------------------------------------------------
# Test 9: SignalComparator get_sync_stats tracks totals
# ---------------------------------------------------------------------------

    def test_get_sync_stats_tracks_totals(self, comparator_tmpdir):
        """get_sync_stats() correctly tracks matched and diverged counts."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        # Two matched signals
        for i in range(2):
            comparator.compare(
                signal_id=f"SIG_match_{i}",
                strategy="fx_eurusd_trend",
                signal={"symbol": "EURUSD"},
                live_result={"passed_risk": True, "order_result": {"qty": 25000}},
                paper_results=[{"passed_risk": True, "mode": "paper", "order_result": {"qty": 25000}}],
            )

        # One diverged signal
        comparator.compare(
            signal_id="SIG_diverge_1",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD"},
            live_result={"passed_risk": False},
            paper_results=[{"passed_risk": True, "mode": "paper", "order_result": {}}],
        )

        stats = comparator.get_sync_stats()
        assert stats["total_signals"] == 3
        assert stats["matched"] == 2
        assert stats["diverged"] == 1
        assert abs(stats["match_rate"] - 2 / 3) < 0.01

    def test_get_sync_stats_empty(self, comparator_tmpdir):
        """get_sync_stats() on a fresh comparator returns zeroes."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)
        stats = comparator.get_sync_stats()
        assert stats["total_signals"] == 0
        assert stats["matched"] == 0
        assert stats["diverged"] == 0
        assert stats["match_rate"] == 0.0

    def test_comparisons_persisted_to_jsonl(self, comparator_tmpdir):
        """Comparisons are written to a JSONL file."""
        comparator = SignalComparator(log_dir=comparator_tmpdir)

        comparator.compare(
            signal_id="SIG_persist_001",
            strategy="fx_eurusd_trend",
            signal={"symbol": "EURUSD"},
            live_result={"passed_risk": True, "order_result": {}},
            paper_results=[{"passed_risk": True, "mode": "paper", "order_result": {}}],
        )

        log_path = Path(comparator_tmpdir) / "comparisons.jsonl"
        assert log_path.exists()

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["signal_id"] == "SIG_persist_001"
        assert record["match"] is True


# ---------------------------------------------------------------------------
# Test 10: process_signal_dual returns complete result dict
# ---------------------------------------------------------------------------

class TestProcessSignalDualResult:
    def test_result_contains_all_keys(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """process_signal_dual() returns a dict with all expected keys."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        test_signal = {"symbol": "EURUSD", "direction": "BUY", "qty": 25000}

        with patch.object(
            engine, "_generate_signal_from_data", return_value=test_signal
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend", {"close": 1.0850}, "intraday"
            )

        # All top-level keys present
        assert "signal_id" in result
        assert "strategy" in result
        assert "signal" in result
        assert "live_result" in result
        assert "paper_results" in result
        assert "comparison" in result

        # signal_id format validation
        assert result["signal_id"].startswith("SIG_")
        assert "fx_eurusd_trend" in result["signal_id"]

        # strategy matches
        assert result["strategy"] == "fx_eurusd_trend"

        # signal is the original signal
        assert result["signal"] == test_signal

        # comparison has required keys
        assert "match" in result["comparison"]
        assert "divergences" in result["comparison"]

    def test_result_with_no_signal(
        self, live_config, paper_us_config, mock_live_broker, mock_broker,
        mock_risk_manager_accept,
    ):
        """When no signal is generated, result has signal=None and empty results."""
        engine = _build_engine(
            live_config, paper_us_config, mock_live_broker, mock_broker,
            mock_risk_manager_accept, mock_risk_manager_accept,
        )

        with patch.object(
            engine, "_generate_signal_from_data", return_value=None
        ):
            result = engine.process_signal_dual(
                "fx_eurusd_trend", {"close": 1.0850}, "intraday"
            )

        assert result["signal"] is None
        assert result["live_result"] is None
        assert result["paper_results"] == []
        assert result["comparison"] is None


# ---------------------------------------------------------------------------
# Test 11: Signal ID format validation
# ---------------------------------------------------------------------------

class TestSignalIdGeneration:
    def test_signal_id_format(self):
        """_generate_signal_id returns SIG_{timestamp}_{strategy}_{uuid}."""
        engine = TradingEngine()
        signal_id = engine._generate_signal_id("fx_eurusd_trend")

        assert signal_id.startswith("SIG_")
        parts = signal_id.split("_", 3)  # SIG, timestamp, rest
        assert parts[0] == "SIG"
        # Timestamp part is 14 chars (YYYYMMDDHHMMSS)
        assert len(parts[1]) == 14
        # Strategy name is in the ID
        assert "fx_eurusd_trend" in signal_id

    def test_signal_ids_unique(self):
        """Two signal IDs for the same strategy are different."""
        engine = TradingEngine()
        id1 = engine._generate_signal_id("fx_eurusd_trend")
        id2 = engine._generate_signal_id("fx_eurusd_trend")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Test 12: _get_live_strategies returns correct list
# ---------------------------------------------------------------------------

class TestGetLiveStrategies:
    def test_returns_live_strategies(self, live_config, paper_us_config):
        """_get_live_strategies returns the live pipeline's strategy list."""
        engine = TradingEngine()
        engine.add_pipeline("live", Pipeline(live_config))
        engine.add_pipeline("paper", Pipeline(paper_us_config))

        live_strats = engine._get_live_strategies()
        assert live_strats == ["fx_eurusd_trend", "fx_eurgbp_mr"]

    def test_returns_empty_when_no_live(self, paper_us_config):
        """_get_live_strategies returns [] when there is no live pipeline."""
        engine = TradingEngine()
        engine.add_pipeline("paper", Pipeline(paper_us_config))

        assert engine._get_live_strategies() == []


# ---------------------------------------------------------------------------
# Test 13: Pipeline execute_signal method
# ---------------------------------------------------------------------------

class TestPipelineExecuteSignal:
    def test_execute_signal_passes_risk_and_submits(
        self, paper_us_config, mock_broker, mock_risk_manager_accept,
    ):
        """execute_signal validates risk, submits order, returns result."""
        pipeline = _build_active_pipeline(
            paper_us_config, mock_broker, mock_risk_manager_accept,
        )

        signal = {"symbol": "SPY", "direction": "BUY", "qty": 100}
        result = pipeline.execute_signal(signal, "SIG_test_exec_001")

        assert result["signal_id"] == "SIG_test_exec_001"
        assert result["mode"] == "PAPER"
        assert result["passed_risk"] is True
        assert result["order_result"] is not None
        assert result["error"] is None

    def test_execute_signal_rejected_by_risk(
        self, paper_us_config, mock_broker, mock_risk_manager_reject,
    ):
        """execute_signal returns passed_risk=False when risk rejects."""
        pipeline = _build_active_pipeline(
            paper_us_config, mock_broker, mock_risk_manager_reject,
        )

        signal = {"symbol": "SPY", "direction": "BUY", "qty": 10000}
        result = pipeline.execute_signal(signal, "SIG_test_reject_001")

        assert result["passed_risk"] is False
        assert result["order_result"] is None
        assert "Risk rejected" in result["error"]

    def test_execute_signal_inactive_pipeline(self, paper_us_config, mock_broker):
        """execute_signal on an inactive pipeline returns an error."""
        pipeline = Pipeline(paper_us_config)
        # Not active
        signal = {"symbol": "SPY", "direction": "BUY", "qty": 100}
        result = pipeline.execute_signal(signal, "SIG_test_inactive")

        assert result["error"] is not None
        assert "not active" in result["error"]
        assert result["passed_risk"] is False

    def test_execute_signal_live_no_risk_manager(
        self, live_config, mock_live_broker,
    ):
        """LIVE pipeline refuses to trade without risk manager."""
        pipeline = Pipeline(live_config)
        pipeline._broker = mock_live_broker
        pipeline._active = True
        pipeline._risk_manager = None  # No risk manager!

        signal = {"symbol": "EURUSD", "direction": "BUY", "qty": 25000}
        result = pipeline.execute_signal(signal, "SIG_test_no_rm")

        assert result["passed_risk"] is False
        assert "ABORT" in result["error"]
        assert "LiveRiskManager" in result["error"]
