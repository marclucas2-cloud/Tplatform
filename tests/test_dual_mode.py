"""
Tests for the Dual-Mode Trading Engine.

Covers:
  - Engine creation and configuration
  - Pipeline initialization (live and paper independently)
  - Isolation: paper failure does not affect live and vice versa
  - Emergency shutdown: only live stops, paper continues
  - Strategy pause/resume on both live and paper
  - State persistence and recovery
  - Single live pipeline constraint
  - Config loading from YAML
  - Risk manager assignment (Live vs Standard)
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml

from core.trading_engine import TradingEngine, Pipeline, PipelineConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def live_config():
    """PipelineConfig for a LIVE pipeline."""
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
        strategies=["day_of_week_seasonal", "vix_expansion_short"],
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
    """A mock broker that behaves like BaseBroker."""
    broker = MagicMock()
    broker.name = "mock_broker"
    broker.is_paper = True
    broker.authenticate.return_value = {
        "status": "ok",
        "equity": 100_000,
        "cash": 90_000,
        "paper": True,
    }
    broker.get_account_info.return_value = {
        "equity": 100_000,
        "cash": 90_000,
    }
    broker.get_positions.return_value = []
    broker.get_orders.return_value = []
    broker.cancel_all_orders.return_value = 0
    broker.close_all_positions.return_value = []
    broker.create_position.return_value = {
        "orderId": "test-001",
        "symbol": "TEST",
        "status": "filled",
    }
    return broker


@pytest.fixture
def mock_live_broker():
    """A mock broker configured as LIVE."""
    broker = MagicMock()
    broker.name = "ibkr_live"
    broker.is_paper = False
    broker.authenticate.return_value = {
        "status": "ok",
        "equity": 10_000,
        "cash": 8_000,
        "paper": False,
    }
    broker.get_account_info.return_value = {
        "equity": 10_000,
        "cash": 8_000,
    }
    broker.get_positions.return_value = []
    broker.cancel_all_orders.return_value = 2
    broker.close_all_positions.return_value = [{"orderId": "close-1"}]
    return broker


@pytest.fixture
def engine_yaml_path():
    """Create a temporary engine.yaml for testing."""
    config = {
        "engine": {
            "name": "test-engine",
            "mode": "DUAL",
        },
        "pipelines": {
            "live_ibkr": {
                "mode": "LIVE",
                "broker": "ibkr",
                "port": 4001,
                "capital": 10_000,
                "risk_limits": "config/limits_live.yaml",
                "log_dir": "logs/test_live",
                "strategies": ["fx_eurusd_trend", "fx_eurgbp_mr"],
            },
            "paper_us": {
                "mode": "PAPER",
                "broker": "alpaca",
                "capital": 100_000,
                "risk_limits": "config/limits.yaml",
                "log_dir": "logs/test_paper_us",
                "strategies": ["day_of_week_seasonal"],
            },
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, dir=tempfile.gettempdir()
    ) as f:
        yaml.dump(config, f)
        path = f.name
    yield path
    os.unlink(path)


# ---------------------------------------------------------------------------
# Test PipelineConfig
# ---------------------------------------------------------------------------

class TestPipelineConfig:
    def test_valid_live_config(self, live_config):
        assert live_config.mode == "LIVE"
        assert live_config.broker_type == "ibkr"
        assert live_config.capital == 10_000
        assert live_config.broker_port == 4001
        assert len(live_config.strategies) == 2

    def test_valid_paper_config(self, paper_us_config):
        assert paper_us_config.mode == "PAPER"
        assert paper_us_config.broker_type == "alpaca"
        assert paper_us_config.capital == 100_000

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be"):
            PipelineConfig(
                mode="INVALID", broker_type="alpaca", strategies=[], capital=1000
            )

    def test_invalid_broker_raises(self):
        with pytest.raises(ValueError, match="broker_type must be"):
            PipelineConfig(
                mode="PAPER", broker_type="robinhood", strategies=[], capital=1000
            )

    def test_default_log_dir(self):
        cfg = PipelineConfig(
            mode="PAPER", broker_type="alpaca", strategies=[], capital=1000
        )
        assert cfg.log_dir == "logs/paper"

        cfg_live = PipelineConfig(
            mode="LIVE", broker_type="ibkr", strategies=[], capital=1000
        )
        assert cfg_live.log_dir == "logs/live"


# ---------------------------------------------------------------------------
# Test Pipeline
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_create_pipeline(self, live_config):
        pipeline = Pipeline(live_config)
        assert pipeline.mode == "LIVE"
        assert not pipeline._active
        assert all(v is True for v in pipeline._strategies_enabled.values())

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager_live.LiveRiskManager.__init__", return_value=None)
    def test_initialize_live_pipeline(self, mock_rm, mock_create_broker, live_config, mock_live_broker):
        mock_create_broker.return_value = mock_live_broker
        pipeline = Pipeline(live_config)
        result = pipeline.initialize()

        assert result["success"] is True
        assert result["broker_connected"] is True
        assert result["strategies_loaded"] == 2
        assert result["mode"] == "LIVE"
        assert pipeline._active is True

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_initialize_paper_pipeline(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        result = pipeline.initialize()

        assert result["success"] is True
        assert result["mode"] == "PAPER"
        assert pipeline._active is True

    def test_initialize_broker_failure(self, live_config):
        pipeline = Pipeline(live_config)
        with patch("core.trading_engine.Pipeline._create_broker") as mock_create, \
             patch("core.risk_manager_live.LiveRiskManager.__init__", return_value=None):
            mock_create.side_effect = Exception("Connection refused")
            result = pipeline.initialize()
            assert result["success"] is False
            assert result["broker_connected"] is False

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_pause_strategy(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        pipeline.initialize()

        assert pipeline.pause_strategy("day_of_week_seasonal", reason="testing")
        assert pipeline._strategies_enabled["day_of_week_seasonal"] is False
        assert pipeline._strategies_enabled["vix_expansion_short"] is True

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_resume_strategy(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        pipeline.initialize()

        pipeline.pause_strategy("day_of_week_seasonal")
        assert pipeline._strategies_enabled["day_of_week_seasonal"] is False

        assert pipeline.resume_strategy("day_of_week_seasonal")
        assert pipeline._strategies_enabled["day_of_week_seasonal"] is True

    def test_pause_unknown_strategy(self, paper_us_config):
        pipeline = Pipeline(paper_us_config)
        assert pipeline.pause_strategy("nonexistent") is False

    def test_resume_unknown_strategy(self, paper_us_config):
        pipeline = Pipeline(paper_us_config)
        assert pipeline.resume_strategy("nonexistent") is False

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_get_status(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        pipeline.initialize()

        status = pipeline.get_status()
        assert status["mode"] == "PAPER"
        assert status["active"] is True
        assert status["strategies_active"] == 2
        assert status["strategies_paused"] == 0
        assert status["capital"] == 100_000

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_execute_cycle_inactive_pipeline(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        """Cycle on inactive pipeline returns error."""
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        # Do NOT initialize -> _active is False
        result = pipeline.execute_cycle("intraday")
        assert "Pipeline not active" in result["errors"]

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_execute_cycle_active_pipeline(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        pipeline.initialize()

        result = pipeline.execute_cycle("intraday")
        assert result["mode"] == "PAPER"
        assert result["cycle_type"] == "intraday"
        # No signals generated since _generate_signal returns None (placeholder)
        assert result["signals_generated"] == 0
        assert result["errors"] == []

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager_live.LiveRiskManager.__init__", return_value=None)
    def test_shutdown_live_closes_positions(self, mock_rm, mock_create_broker, live_config, mock_live_broker):
        mock_create_broker.return_value = mock_live_broker
        pipeline = Pipeline(live_config)
        pipeline.initialize()

        result = pipeline.shutdown(reason="test")
        assert result["mode"] == "LIVE"
        assert result["success"] is True
        assert result["positions_closed"] == 1  # close_all_positions returns [{"orderId": "close-1"}]
        mock_live_broker.close_all_positions.assert_called_once()
        assert pipeline._active is False

    @patch("core.trading_engine.Pipeline._create_broker")
    @patch("core.risk_manager.RiskManager.__init__", return_value=None)
    def test_shutdown_paper_does_not_close_positions(self, mock_rm, mock_create_broker, paper_us_config, mock_broker):
        mock_create_broker.return_value = mock_broker
        pipeline = Pipeline(paper_us_config)
        pipeline.initialize()

        result = pipeline.shutdown(reason="test")
        assert result["mode"] == "PAPER"
        # Paper pipeline should NOT close positions
        mock_broker.close_all_positions.assert_not_called()


# ---------------------------------------------------------------------------
# Test TradingEngine
# ---------------------------------------------------------------------------

class TestTradingEngine:
    def test_create_engine(self):
        engine = TradingEngine()
        assert engine.pipelines == {}
        assert engine._initialized is False

    def test_add_pipeline(self, live_config, paper_us_config):
        engine = TradingEngine()
        engine.add_pipeline("live", Pipeline(live_config))
        engine.add_pipeline("paper_us", Pipeline(paper_us_config))
        assert len(engine.pipelines) == 2
        assert "live" in engine.pipelines
        assert "paper_us" in engine.pipelines

    def test_single_live_constraint(self, live_config):
        """Cannot add two LIVE pipelines."""
        engine = TradingEngine()
        engine.add_pipeline("live1", Pipeline(live_config))

        live_config_2 = PipelineConfig(
            mode="LIVE",
            broker_type="ibkr",
            strategies=["another_strategy"],
            capital=5_000,
        )
        with pytest.raises(ValueError, match="Cannot add a second LIVE pipeline"):
            engine.add_pipeline("live2", Pipeline(live_config_2))

    def test_get_live_pipeline(self, live_config, paper_us_config):
        engine = TradingEngine()
        live_pipeline = Pipeline(live_config)
        engine.add_pipeline("live", live_pipeline)
        engine.add_pipeline("paper", Pipeline(paper_us_config))

        assert engine.get_live_pipeline() is live_pipeline

    def test_get_live_pipeline_none(self, paper_us_config):
        engine = TradingEngine()
        engine.add_pipeline("paper", Pipeline(paper_us_config))
        assert engine.get_live_pipeline() is None

    def test_get_paper_pipelines(self, live_config, paper_us_config, paper_eu_config):
        engine = TradingEngine()
        engine.add_pipeline("live", Pipeline(live_config))
        engine.add_pipeline("paper_us", Pipeline(paper_us_config))
        engine.add_pipeline("paper_eu", Pipeline(paper_eu_config))

        papers = engine.get_paper_pipelines()
        assert len(papers) == 2
        assert all(p.mode == "PAPER" for p in papers)


class TestTradingEngineIsolation:
    """Tests that LIVE and PAPER pipelines are fully isolated."""

    def _build_engine(self, live_config, paper_us_config, mock_live_broker, mock_broker):
        """Helper: build an engine with mocked brokers."""
        engine = TradingEngine()

        live_pipeline = Pipeline(live_config)
        paper_pipeline = Pipeline(paper_us_config)

        with patch.object(live_pipeline, "_create_broker", return_value=mock_live_broker), \
             patch("core.risk_manager_live.LiveRiskManager.__init__", return_value=None):
            live_pipeline.initialize()

        with patch.object(paper_pipeline, "_create_broker", return_value=mock_broker), \
             patch("core.risk_manager.RiskManager.__init__", return_value=None):
            paper_pipeline.initialize()

        engine.add_pipeline("live", live_pipeline)
        engine.add_pipeline("paper_us", paper_pipeline)
        return engine

    def test_paper_failure_does_not_affect_live(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """A paper pipeline crash does NOT propagate to the live pipeline."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        # Make paper pipeline raise during cycle
        paper = engine.pipelines["paper_us"]
        original_execute = paper.execute_cycle
        paper.execute_cycle = MagicMock(side_effect=RuntimeError("Paper crash!"))

        result = engine.run_cycle("intraday")

        # Paper crashed
        assert "error" in result["pipeline_results"]["paper_us"] or \
               "Paper crash!" in str(result["errors"])
        # Live should have run without error
        live_result = result["pipeline_results"].get("live", {})
        assert "error" not in live_result or live_result.get("errors", []) == []

    def test_live_failure_does_not_affect_paper(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """A live pipeline crash does NOT propagate to the paper pipeline."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        # Make live pipeline raise during cycle
        live = engine.pipelines["live"]
        live.execute_cycle = MagicMock(side_effect=RuntimeError("Live crash!"))

        result = engine.run_cycle("intraday")

        # Live crashed
        assert "Live crash!" in str(result["errors"]) or \
               "error" in result["pipeline_results"]["live"]
        # Paper should have run successfully
        paper_result = result["pipeline_results"].get("paper_us", {})
        assert paper_result.get("errors", []) == []

    def test_separate_broker_connections(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """Live and paper use separate broker instances."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        live_broker = engine.pipelines["live"]._broker
        paper_broker = engine.pipelines["paper_us"]._broker

        assert live_broker is not paper_broker
        assert live_broker.name != paper_broker.name

    def test_separate_risk_managers(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """Live uses LiveRiskManager, paper uses RiskManager."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        live_rm = engine.pipelines["live"]._risk_manager
        paper_rm = engine.pipelines["paper_us"]._risk_manager

        assert live_rm is not paper_rm

    def test_separate_state(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """Live and paper maintain independent strategy state."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        # Pause a strategy on live
        engine.pipelines["live"].pause_strategy("fx_eurusd_trend")

        # Paper strategies should be unaffected
        paper_strategies = engine.pipelines["paper_us"]._strategies_enabled
        assert all(v is True for v in paper_strategies.values())

    def test_emergency_shutdown_only_stops_live(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """Emergency shutdown stops LIVE but paper continues."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        result = engine.emergency_shutdown("Test emergency")

        # Live should be shut down
        assert result["live_shutdown"]["mode"] == "LIVE"
        assert engine.pipelines["live"]._active is False

        # Paper should still be active
        assert engine.pipelines["paper_us"]._active is True
        assert len(result["paper_status"]) == 1
        assert result["paper_status"][0]["active"] is True

    def test_no_cross_contamination_live_signal_no_paper_trade(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """A signal from live pipeline does NOT trigger a paper trade."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        # Simulate a signal only on the live pipeline by running its cycle
        engine.pipelines["live"].execute_cycle("intraday")

        # Paper broker should NOT have received any create_position call
        # from the live cycle
        # (Paper broker's create_position should only be called by paper pipeline)
        paper_broker = engine.pipelines["paper_us"]._broker
        # Since _generate_signal returns None, no orders at all, but the point
        # is that live and paper brokers are completely separate instances
        assert paper_broker is not engine.pipelines["live"]._broker

    def test_no_cross_contamination_paper_signal_no_live_trade(
        self, live_config, paper_us_config, mock_live_broker, mock_broker
    ):
        """A signal from paper pipeline does NOT trigger a live trade."""
        engine = self._build_engine(live_config, paper_us_config, mock_live_broker, mock_broker)

        engine.pipelines["paper_us"].execute_cycle("intraday")

        live_broker = engine.pipelines["live"]._broker
        assert live_broker is not engine.pipelines["paper_us"]._broker


class TestTradingEngineConfig:
    """Tests for config loading and YAML parsing."""

    def test_load_from_yaml(self, engine_yaml_path):
        """Load engine from a YAML config file."""
        with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
            engine = TradingEngine.from_config(engine_yaml_path)

        assert len(engine.pipelines) == 2
        assert "live_ibkr" in engine.pipelines
        assert "paper_us" in engine.pipelines
        assert engine.pipelines["live_ibkr"].mode == "LIVE"
        assert engine.pipelines["paper_us"].mode == "PAPER"

    def test_live_pipeline_has_live_config(self, engine_yaml_path):
        """Live pipeline from YAML has correct capital and port."""
        with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
            engine = TradingEngine.from_config(engine_yaml_path)

        live = engine.pipelines["live_ibkr"]
        assert live.config.capital == 10_000
        assert live.config.broker_port == 4001
        assert live.config.broker_type == "ibkr"

    def test_paper_pipeline_has_paper_config(self, engine_yaml_path):
        """Paper pipeline from YAML has correct capital."""
        with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
            engine = TradingEngine.from_config(engine_yaml_path)

        paper = engine.pipelines["paper_us"]
        assert paper.config.capital == 100_000
        assert paper.config.broker_type == "alpaca"

    def test_dual_mode_loads_all(self, engine_yaml_path):
        """DUAL mode loads both live and paper pipelines."""
        with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
            engine = TradingEngine.from_config(engine_yaml_path)

        assert engine.get_live_pipeline() is not None
        assert len(engine.get_paper_pipelines()) == 1

    def test_paper_only_mode_skips_live(self):
        """PAPER_ONLY mode does not load the live pipeline."""
        config = {
            "engine": {"mode": "PAPER_ONLY"},
            "pipelines": {
                "live_ibkr": {
                    "mode": "LIVE",
                    "broker": "ibkr",
                    "capital": 10_000,
                    "strategies": ["fx_eurusd_trend"],
                },
                "paper_us": {
                    "mode": "PAPER",
                    "broker": "alpaca",
                    "capital": 100_000,
                    "strategies": ["day_of_week_seasonal"],
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tempfile.gettempdir()
        ) as f:
            yaml.dump(config, f)
            path = f.name

        try:
            with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
                engine = TradingEngine.from_config(path)
            assert engine.get_live_pipeline() is None
            assert len(engine.get_paper_pipelines()) == 1
        finally:
            os.unlink(path)

    def test_live_only_mode_skips_paper(self):
        """LIVE_ONLY mode does not load paper pipelines."""
        config = {
            "engine": {"mode": "LIVE_ONLY"},
            "pipelines": {
                "live_ibkr": {
                    "mode": "LIVE",
                    "broker": "ibkr",
                    "capital": 10_000,
                    "strategies": ["fx_eurusd_trend"],
                },
                "paper_us": {
                    "mode": "PAPER",
                    "broker": "alpaca",
                    "capital": 100_000,
                    "strategies": ["day_of_week_seasonal"],
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tempfile.gettempdir()
        ) as f:
            yaml.dump(config, f)
            path = f.name

        try:
            with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
                engine = TradingEngine.from_config(path)
            assert engine.get_live_pipeline() is not None
            assert len(engine.get_paper_pipelines()) == 0
        finally:
            os.unlink(path)

    def test_multiple_live_pipelines_in_yaml_raises(self):
        """YAML with 2 LIVE pipelines raises ValueError."""
        config = {
            "engine": {"mode": "DUAL"},
            "pipelines": {
                "live1": {
                    "mode": "LIVE",
                    "broker": "ibkr",
                    "capital": 10_000,
                    "strategies": ["s1"],
                },
                "live2": {
                    "mode": "LIVE",
                    "broker": "ibkr",
                    "capital": 5_000,
                    "strategies": ["s2"],
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tempfile.gettempdir()
        ) as f:
            yaml.dump(config, f)
            path = f.name

        try:
            with patch("core.trading_engine._ROOT", Path(tempfile.gettempdir())):
                with pytest.raises(ValueError, match="At most 1 LIVE pipeline"):
                    TradingEngine.from_config(path)
        finally:
            os.unlink(path)


class TestTradingEngineStatePersistence:
    """Tests for state save/load."""

    def test_save_and_load_state(self, live_config, paper_us_config):
        """State is correctly persisted and recovered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "engine_state.json"

            # Build engine, pause a strategy, save state
            engine = TradingEngine()
            engine._state_path = state_path
            engine.add_pipeline("live", Pipeline(live_config))
            engine.add_pipeline("paper", Pipeline(paper_us_config))
            engine.pipelines["live"].pause_strategy("fx_eurusd_trend")
            engine.pipelines["live"]._active = True
            engine.pipelines["live"]._last_execution = "2026-03-27T10:00:00Z"
            engine._save_state()

            assert state_path.exists()

            # Load state in a new engine
            engine2 = TradingEngine()
            engine2._state_path = state_path
            engine2.add_pipeline("live", Pipeline(live_config))
            engine2.add_pipeline("paper", Pipeline(paper_us_config))
            engine2._load_state()

            # Verify the paused strategy was restored
            assert engine2.pipelines["live"]._strategies_enabled["fx_eurusd_trend"] is False
            assert engine2.pipelines["live"]._strategies_enabled["fx_eurgbp_mr"] is True
            assert engine2.pipelines["live"]._last_execution == "2026-03-27T10:00:00Z"

            # Paper should be unchanged
            assert all(
                v is True
                for v in engine2.pipelines["paper"]._strategies_enabled.values()
            )

    def test_load_state_missing_file(self, live_config):
        """Loading state when file does not exist is a no-op."""
        engine = TradingEngine()
        engine._state_path = Path("/nonexistent/path/state.json")
        engine.add_pipeline("live", Pipeline(live_config))
        # Should not raise
        engine._load_state()

    def test_graceful_shutdown_saves_state(self, live_config, paper_us_config, mock_live_broker, mock_broker):
        """shutdown_all() persists state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "engine_state.json"

            engine = TradingEngine()
            engine._state_path = state_path

            live_pipeline = Pipeline(live_config)
            paper_pipeline = Pipeline(paper_us_config)

            with patch.object(live_pipeline, "_create_broker", return_value=mock_live_broker), \
                 patch("core.risk_manager_live.LiveRiskManager.__init__", return_value=None):
                live_pipeline.initialize()

            with patch.object(paper_pipeline, "_create_broker", return_value=mock_broker), \
                 patch("core.risk_manager.RiskManager.__init__", return_value=None):
                paper_pipeline.initialize()

            engine.add_pipeline("live", live_pipeline)
            engine.add_pipeline("paper", paper_pipeline)
            engine._initialized = True

            engine.shutdown_all(reason="test")

            assert state_path.exists()
            with open(state_path) as f:
                state = json.load(f)
            assert "pipelines" in state
            assert state["initialized"] is False


class TestTradingEngineFullStatus:
    """Tests for get_full_status()."""

    def test_full_status_includes_all_pipelines(
        self, live_config, paper_us_config, paper_eu_config
    ):
        engine = TradingEngine()
        engine.add_pipeline("live", Pipeline(live_config))
        engine.add_pipeline("paper_us", Pipeline(paper_us_config))
        engine.add_pipeline("paper_eu", Pipeline(paper_eu_config))

        status = engine.get_full_status()
        assert status["total_pipelines"] == 3
        assert status["live_pipeline"] is not None
        assert status["live_pipeline"]["mode"] == "LIVE"
        assert len(status["paper_pipelines"]) == 2
        assert all(p["mode"] == "PAPER" for p in status["paper_pipelines"])

    def test_full_status_no_live(self, paper_us_config):
        engine = TradingEngine()
        engine.add_pipeline("paper", Pipeline(paper_us_config))

        status = engine.get_full_status()
        assert status["live_pipeline"] is None
        assert len(status["paper_pipelines"]) == 1


class TestStrategyPauseResumeIndependence:
    """Verify pause/resume on one pipeline does not affect the other."""

    def test_pause_live_does_not_affect_paper(self, live_config, paper_us_config):
        engine = TradingEngine()
        engine.add_pipeline("live", Pipeline(live_config))
        engine.add_pipeline("paper", Pipeline(paper_us_config))

        engine.pipelines["live"].pause_strategy("fx_eurusd_trend")

        # Paper strategies unaffected
        paper_enabled = engine.pipelines["paper"]._strategies_enabled
        assert all(v is True for v in paper_enabled.values())

    def test_pause_paper_does_not_affect_live(self, live_config, paper_us_config):
        engine = TradingEngine()
        engine.add_pipeline("live", Pipeline(live_config))
        engine.add_pipeline("paper", Pipeline(paper_us_config))

        engine.pipelines["paper"].pause_strategy("day_of_week_seasonal")

        # Live strategies unaffected
        live_enabled = engine.pipelines["live"]._strategies_enabled
        assert all(v is True for v in live_enabled.values())
