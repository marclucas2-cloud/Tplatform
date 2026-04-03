"""
Dual-Mode Trading Engine -- runs LIVE and PAPER pipelines simultaneously.

Architecture:
  - LivePipeline: trades real money on IBKR (port 4001 live)
    - 4-6 strategies (FX + futures micro)
    - LiveRiskManager (10K calibrated)
    - Separate logs, DB, alerts

  - PaperPipelineUS: Alpaca paper ($100K)
    - 7+ US strategies
    - Standard RiskManager

  - PaperPipelineEU: IBKR paper (port 7497)
    - 5+ EU strategies
    - Standard RiskManager

CRITICAL ISOLATION RULES:
  1. Separate broker connections (different ports)
  2. Separate risk managers (different capital)
  3. Separate logs (logs/live/ vs logs/paper/)
  4. Separate databases (live_trades vs paper_trades)
  5. NO cross-contamination: paper bug cannot affect live
  6. Kill switch live is independent from paper
"""

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List

import yaml

logger = logging.getLogger(__name__)

# Root directory of the trading-platform project
_ROOT = Path(__file__).resolve().parent.parent

# Lock to prevent concurrent env var manipulation in _create_broker()
_broker_init_lock = threading.Lock()


class PipelineConfig:
    """Configuration for a single pipeline (live or paper)."""

    def __init__(
        self,
        mode: str,
        broker_type: str,
        strategies: list,
        capital: float,
        risk_limits_path: str = None,
        broker_port: int = None,
        log_dir: str = None,
    ):
        """
        Args:
            mode: "LIVE" or "PAPER"
            broker_type: "alpaca" or "ibkr"
            strategies: list of strategy names to run
            capital: capital allocation
            risk_limits_path: path to risk limits YAML
            broker_port: override broker port
            log_dir: override log directory
        """
        if mode not in ("LIVE", "PAPER"):
            raise ValueError(f"mode must be 'LIVE' or 'PAPER', got '{mode}'")
        if broker_type not in ("alpaca", "ibkr"):
            raise ValueError(f"broker_type must be 'alpaca' or 'ibkr', got '{broker_type}'")

        self.mode = mode
        self.broker_type = broker_type
        self.strategies = list(strategies)
        self.capital = float(capital)
        self.risk_limits_path = risk_limits_path
        self.broker_port = broker_port
        self.log_dir = log_dir or f"logs/{mode.lower()}"


class Pipeline:
    """A single trading pipeline (live or paper).

    Each pipeline has its own broker, risk manager, state, and logs.
    Pipelines are completely isolated from each other.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.mode = config.mode
        self._active = False
        self._strategies_enabled: Dict[str, bool] = {s: True for s in config.strategies}
        self._state: dict = {}
        self._last_execution: str | None = None
        self._broker = None
        self._risk_manager = None
        self._logger = logging.getLogger(f"pipeline.{config.mode.lower()}")

        # Ensure log directory exists
        log_dir = _ROOT / config.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> dict:
        """Initialize the pipeline: connect broker, load state.

        Returns:
            {success: bool, broker_connected: bool, strategies_loaded: int, mode: str}
        """
        result = {
            "success": False,
            "broker_connected": False,
            "strategies_loaded": 0,
            "mode": self.mode,
        }

        # --- Risk Manager ---
        try:
            if self.mode == "LIVE":
                from core.risk_manager_live import LiveRiskManager
                limits_path = self.config.risk_limits_path
                if limits_path:
                    limits_path = _ROOT / limits_path
                self._risk_manager = LiveRiskManager(limits_path=limits_path)
                self._logger.info(
                    f"LiveRiskManager loaded (capital=${self.config.capital:,.0f})"
                )
            else:
                from core.risk_manager import RiskManager
                limits_path = self.config.risk_limits_path
                if limits_path:
                    limits_path = _ROOT / limits_path
                self._risk_manager = RiskManager(limits_path=limits_path)
                self._logger.info(
                    f"RiskManager loaded (capital=${self.config.capital:,.0f})"
                )
        except Exception as exc:
            self._logger.error(f"Failed to load risk manager: {exc}", exc_info=True)
            return result

        # --- Broker ---
        try:
            self._broker = self._create_broker()
            auth_info = self._broker.authenticate()
            result["broker_connected"] = True
            self._logger.info(
                f"Broker '{self._broker.name}' connected "
                f"(paper={self._broker.is_paper}, equity=${auth_info.get('equity', 0):,.2f})"
            )
        except Exception as exc:
            self._logger.error(f"Failed to connect broker: {exc}", exc_info=True)
            return result

        # --- Strategies ---
        result["strategies_loaded"] = len(self.config.strategies)
        self._active = True
        result["success"] = True

        self._logger.info(
            f"Pipeline [{self.mode}] initialized: "
            f"{result['strategies_loaded']} strategies, "
            f"broker={self.config.broker_type}"
        )
        return result

    def _create_broker(self):
        """Create a broker instance with pipeline-specific configuration.

        Uses environment variable overrides for port when broker_port is set.
        IBKR env var manipulation is protected by _broker_init_lock to prevent
        race conditions when two pipelines initialize concurrently.
        """
        if self.config.broker_type == "alpaca":
            from core.broker.alpaca_adapter import AlpacaBroker
            return AlpacaBroker()
        elif self.config.broker_type == "ibkr":
            from core.broker.ibkr_adapter import IBKRBroker

            with _broker_init_lock:
                # Temporarily override env vars for this broker's port
                original_port = os.environ.get("IBKR_PORT")
                original_paper = os.environ.get("IBKR_PAPER")

                try:
                    if self.config.broker_port is not None:
                        os.environ["IBKR_PORT"] = str(self.config.broker_port)
                    if self.mode == "LIVE":
                        os.environ["IBKR_PAPER"] = "false"
                    else:
                        os.environ["IBKR_PAPER"] = "true"
                    return IBKRBroker()
                finally:
                    # Restore original env vars
                    if original_port is not None:
                        os.environ["IBKR_PORT"] = original_port
                    elif "IBKR_PORT" in os.environ:
                        del os.environ["IBKR_PORT"]
                    if original_paper is not None:
                        os.environ["IBKR_PAPER"] = original_paper
                    elif "IBKR_PAPER" in os.environ:
                        del os.environ["IBKR_PAPER"]
        else:
            raise ValueError(f"Unknown broker type: {self.config.broker_type}")

    def execute_cycle(self, cycle_type: str = "intraday") -> dict:
        """Execute one trading cycle (generate signals, validate risk, submit orders).

        Args:
            cycle_type: "intraday" or "daily" or "monthly"

        Returns:
            {
                mode: str,
                cycle_type: str,
                signals_generated: int,
                signals_validated: int,
                orders_submitted: int,
                orders_filled: int,
                errors: list
            }
        """
        result = {
            "mode": self.mode,
            "cycle_type": cycle_type,
            "signals_generated": 0,
            "signals_validated": 0,
            "orders_submitted": 0,
            "orders_filled": 0,
            "errors": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if not self._active:
            result["errors"].append("Pipeline not active")
            return result

        # FIX CRO H-5 : LIVE pipeline REFUSE de trader sans risk manager
        if self.mode == "LIVE" and self._risk_manager is None:
            error = "ABORT: LIVE pipeline cannot trade without LiveRiskManager"
            self._logger.critical(error)
            result["errors"].append(error)
            return result

        active_strategies = [
            s for s in self.config.strategies if self._strategies_enabled.get(s, False)
        ]

        for strategy_name in active_strategies:
            try:
                signal = self._generate_signal(strategy_name, cycle_type)
                if signal is None:
                    continue
                result["signals_generated"] += 1

                # Validate through risk manager
                if self._risk_manager is not None and self._broker is not None:
                    portfolio = self._get_portfolio_snapshot()
                    passed, msg = self._risk_manager.validate_order(signal, portfolio)
                    if not passed:
                        self._logger.info(
                            f"[{self.mode}] Signal rejected by risk manager: "
                            f"{strategy_name} -> {msg}"
                        )
                        continue
                result["signals_validated"] += 1

                # Submit order
                order_result = self._submit_order(signal)
                if order_result:
                    result["orders_submitted"] += 1
                    if order_result.get("status") in ("filled", "accepted", "new"):
                        result["orders_filled"] += 1

            except Exception as exc:
                error_msg = f"Strategy {strategy_name}: {exc}"
                result["errors"].append(error_msg)
                self._logger.error(
                    f"[{self.mode}] Error in {strategy_name}: {exc}", exc_info=True
                )

        self._last_execution = result["timestamp"]
        self._logger.info(
            f"[{self.mode}] Cycle {cycle_type} complete: "
            f"{result['signals_generated']} signals, "
            f"{result['orders_submitted']} orders, "
            f"{len(result['errors'])} errors"
        )
        return result

    def execute_signal(self, signal: dict, signal_id: str) -> dict:
        """Execute a pre-generated signal (skip signal generation).

        Used by TradingEngine.process_signal_dual() to route a single
        signal to multiple pipelines without re-generating it.

        Validates through risk manager, submits order if passed.

        Args:
            signal: pre-generated signal dict with keys like
                    {symbol, direction, qty, stop_loss, take_profit, ...}
            signal_id: unique signal identifier for tracing

        Returns:
            {
                signal_id: str,
                mode: str,
                passed_risk: bool,
                order_result: dict or None,
                error: str or None,
            }
        """
        result = {
            "signal_id": signal_id,
            "mode": self.mode,
            "passed_risk": False,
            "order_result": None,
            "error": None,
        }

        if not self._active:
            result["error"] = f"Pipeline {self.mode} not active"
            return result

        # LIVE pipeline REFUSES to trade without risk manager
        if self.mode == "LIVE" and self._risk_manager is None:
            result["error"] = "ABORT: LIVE pipeline cannot trade without LiveRiskManager"
            self._logger.critical(result["error"])
            return result

        try:
            # Validate through risk manager
            if self._risk_manager is not None and self._broker is not None:
                portfolio = self._get_portfolio_snapshot()
                passed, msg = self._risk_manager.validate_order(signal, portfolio)
                if not passed:
                    self._logger.info(
                        f"[{self.mode}] Signal {signal_id} rejected by risk manager: {msg}"
                    )
                    result["error"] = f"Risk rejected: {msg}"
                    return result

            result["passed_risk"] = True

            # Submit order
            order_result = self._submit_order(signal)
            result["order_result"] = order_result

            self._logger.info(
                f"[{self.mode}] Signal {signal_id} executed: "
                f"passed_risk=True, order={order_result}"
            )

        except Exception as exc:
            result["error"] = str(exc)
            self._logger.error(
                f"[{self.mode}] Signal {signal_id} execution error: {exc}",
                exc_info=True,
            )

        return result

    def _generate_signal(self, strategy_name: str, cycle_type: str) -> dict | None:
        """Generate a trading signal from a strategy.

        Returns None if the strategy has no signal for this cycle.
        In production, this delegates to the actual strategy implementation.
        """
        # Placeholder: actual strategies are loaded dynamically from
        # intraday-backtesterV2/strategies/ or scripts/paper_portfolio.py
        self._logger.debug(f"[{self.mode}] Generating signal: {strategy_name} ({cycle_type})")
        return None

    def _get_portfolio_snapshot(self) -> dict:
        """Build a portfolio snapshot for risk validation."""
        try:
            account = self._broker.get_account_info()
            positions = self._broker.get_positions()
            return {
                "equity": float(account.get("equity", 0)),
                "cash": float(account.get("cash", 0)),
                "positions": positions,
            }
        except Exception as exc:
            self._logger.error(f"[{self.mode}] Failed to get portfolio snapshot: {exc}")
            return {"equity": self.config.capital, "cash": self.config.capital, "positions": []}

    def _submit_order(self, signal: dict) -> dict | None:
        """Submit an order to the broker."""
        if self._broker is None:
            return None
        try:
            return self._broker.create_position(
                symbol=signal.get("symbol", ""),
                direction=signal.get("direction", "BUY"),
                qty=signal.get("qty"),
                notional=signal.get("notional"),
                stop_loss=signal.get("stop_loss"),
                take_profit=signal.get("take_profit"),
                _authorized_by=f"engine_{self.mode.lower()}",
            )
        except Exception as exc:
            self._logger.error(f"[{self.mode}] Order submission failed: {exc}")
            return None

    def pause_strategy(self, strategy_name: str, reason: str = "") -> bool:
        """Pause a specific strategy (stops signals, keeps positions)."""
        if strategy_name not in self._strategies_enabled:
            self._logger.warning(
                f"[{self.mode}] Cannot pause unknown strategy: {strategy_name}"
            )
            return False
        self._strategies_enabled[strategy_name] = False
        self._logger.info(
            f"[{self.mode}] Strategy PAUSED: {strategy_name}"
            + (f" (reason: {reason})" if reason else "")
        )
        return True

    def resume_strategy(self, strategy_name: str) -> bool:
        """Resume a paused strategy."""
        if strategy_name not in self._strategies_enabled:
            self._logger.warning(
                f"[{self.mode}] Cannot resume unknown strategy: {strategy_name}"
            )
            return False
        self._strategies_enabled[strategy_name] = True
        self._logger.info(f"[{self.mode}] Strategy RESUMED: {strategy_name}")
        return True

    def get_status(self) -> dict:
        """Pipeline status: mode, active strategies, positions, P&L."""
        active_count = sum(1 for v in self._strategies_enabled.values() if v)
        paused_count = sum(1 for v in self._strategies_enabled.values() if not v)

        status = {
            "mode": self.mode,
            "active": self._active,
            "broker_type": self.config.broker_type,
            "capital": self.config.capital,
            "strategies_active": active_count,
            "strategies_paused": paused_count,
            "strategies_detail": dict(self._strategies_enabled),
            "last_execution": self._last_execution,
        }

        # Add broker info if available
        if self._broker is not None and self._active:
            try:
                account = self._broker.get_account_info()
                positions = self._broker.get_positions()
                status["equity"] = float(account.get("equity", 0))
                status["cash"] = float(account.get("cash", 0))
                status["positions_count"] = len(positions)
                status["unrealized_pnl"] = sum(
                    float(p.get("unrealized_pl", 0)) for p in positions
                )
            except Exception as exc:
                status["broker_error"] = str(exc)

        return status

    def shutdown(self, reason: str = "normal") -> dict:
        """Graceful shutdown: close positions if live, save state."""
        result = {
            "mode": self.mode,
            "reason": reason,
            "positions_closed": 0,
            "orders_cancelled": 0,
            "success": True,
        }

        self._logger.info(f"[{self.mode}] Shutting down pipeline (reason: {reason})")

        if self._broker is not None and self._active:
            try:
                # Cancel all pending orders
                cancelled = self._broker.cancel_all_orders(
                    _authorized_by=f"engine_{self.mode.lower()}_shutdown"
                )
                result["orders_cancelled"] = cancelled
            except Exception as exc:
                self._logger.error(f"[{self.mode}] Failed to cancel orders: {exc}")
                result["success"] = False

            # For live pipeline, close all positions on shutdown
            if self.mode == "LIVE":
                try:
                    closed = self._broker.close_all_positions(
                        _authorized_by="engine_live_shutdown"
                    )
                    result["positions_closed"] = len(closed)
                except Exception as exc:
                    self._logger.error(f"[LIVE] Failed to close positions: {exc}")
                    result["success"] = False

        self._active = False
        self._logger.info(
            f"[{self.mode}] Pipeline shutdown complete: "
            f"cancelled={result['orders_cancelled']}, "
            f"closed={result['positions_closed']}"
        )
        return result


class TradingEngine:
    """Orchestrates multiple pipelines (live + paper) simultaneously.

    Usage:
        engine = TradingEngine.from_config("config/engine.yaml")
        engine.initialize()
        engine.run_cycle("intraday")  # runs all pipelines
    """

    def __init__(self):
        self.pipelines: Dict[str, Pipeline] = {}
        self._initialized = False
        self._state_path = _ROOT / "data" / "engine_state.json"

    @classmethod
    def from_config(cls, config_path: str = None) -> "TradingEngine":
        """Create engine from YAML config file.

        Args:
            config_path: path to engine.yaml (relative to project root or absolute).
                         Defaults to config/engine.yaml.

        Returns:
            Configured TradingEngine instance.
        """
        engine = cls()

        if config_path is None:
            config_path = _ROOT / "config" / "engine.yaml"
        else:
            config_path = Path(config_path)
            if not config_path.is_absolute():
                config_path = _ROOT / config_path

        with open(config_path) as f:
            config = yaml.safe_load(f)

        engine_cfg = config.get("engine", {})
        engine_mode = engine_cfg.get("mode", "DUAL")

        pipelines_cfg = config.get("pipelines", {})

        # Validate: at most one LIVE pipeline
        live_count = sum(
            1 for p in pipelines_cfg.values() if p.get("mode", "").upper() == "LIVE"
        )
        if live_count > 1:
            raise ValueError(
                f"At most 1 LIVE pipeline allowed, found {live_count}"
            )

        for name, pcfg in pipelines_cfg.items():
            mode = pcfg.get("mode", "PAPER").upper()

            # Skip pipelines based on engine mode
            if engine_mode == "LIVE_ONLY" and mode != "LIVE":
                continue
            if engine_mode == "PAPER_ONLY" and mode != "PAPER":
                continue

            pipeline_config = PipelineConfig(
                mode=mode,
                broker_type=pcfg.get("broker", "alpaca"),
                strategies=pcfg.get("strategies", []),
                capital=pcfg.get("capital", 100_000),
                risk_limits_path=pcfg.get("risk_limits"),
                broker_port=pcfg.get("port"),
                log_dir=pcfg.get("log_dir"),
            )
            engine.add_pipeline(name, Pipeline(pipeline_config))

        logger.info(
            f"TradingEngine created from {config_path}: "
            f"mode={engine_mode}, pipelines={list(engine.pipelines.keys())}"
        )
        return engine

    def add_pipeline(self, name: str, pipeline: Pipeline):
        """Add a pipeline to the engine.

        Args:
            name: unique pipeline identifier
            pipeline: Pipeline instance

        Raises:
            ValueError: if a second LIVE pipeline is added
        """
        # Enforce single live pipeline constraint
        if pipeline.mode == "LIVE":
            existing_live = self.get_live_pipeline()
            if existing_live is not None:
                raise ValueError(
                    "Cannot add a second LIVE pipeline. "
                    "Only one LIVE pipeline is allowed."
                )

        self.pipelines[name] = pipeline
        logger.info(f"Pipeline added: '{name}' (mode={pipeline.mode})")

    def initialize(self) -> dict:
        """Initialize all pipelines.

        Returns:
            {pipelines: {name: {success, mode, strategies}}, all_ok: bool}
        """
        results = {"pipelines": {}, "all_ok": True}

        self._load_state()

        for name, pipeline in self.pipelines.items():
            try:
                init_result = pipeline.initialize()
                results["pipelines"][name] = init_result
                if not init_result.get("success", False):
                    results["all_ok"] = False
                    logger.warning(f"Pipeline '{name}' failed to initialize")
            except Exception as exc:
                results["pipelines"][name] = {
                    "success": False,
                    "error": str(exc),
                    "mode": pipeline.mode,
                }
                results["all_ok"] = False
                logger.error(f"Pipeline '{name}' init exception: {exc}", exc_info=True)

        self._initialized = True
        self._save_state()

        logger.info(
            f"TradingEngine initialized: "
            f"{sum(1 for r in results['pipelines'].values() if r.get('success'))}/"
            f"{len(self.pipelines)} pipelines OK"
        )
        return results

    def run_cycle(self, cycle_type: str = "intraday") -> dict:
        """Run a trading cycle on ALL active pipelines.

        Each pipeline executes independently. A paper failure
        does NOT affect the live pipeline.

        Args:
            cycle_type: "intraday", "daily", or "monthly"

        Returns:
            {pipeline_results: {name: result}, errors: list}
        """
        overall = {
            "pipeline_results": {},
            "errors": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        for name, pipeline in self.pipelines.items():
            try:
                cycle_result = pipeline.execute_cycle(cycle_type)
                overall["pipeline_results"][name] = cycle_result
            except Exception as exc:
                error_msg = f"Pipeline '{name}' cycle failed: {exc}"
                overall["errors"].append(error_msg)
                overall["pipeline_results"][name] = {
                    "mode": pipeline.mode,
                    "cycle_type": cycle_type,
                    "error": str(exc),
                }
                # CRITICAL: log but do NOT propagate -- other pipelines must continue
                logger.error(error_msg, exc_info=True)

        self._save_state()
        return overall

    def process_signal_dual(
        self, strategy_name: str, market_data: dict, cycle_type: str = "intraday"
    ) -> dict:
        """Generate signal ONCE, route to live AND paper pipelines.

        This is the core of HARDEN-003: signals are generated once from
        market data and then routed to the appropriate pipelines, ensuring
        live and paper never diverge due to separate signal generation.

        Routing rules:
          - If strategy is in the live pipeline's strategy list -> route to LIVE + all PAPER
          - If strategy is paper-only -> route to all PAPER pipelines only

        Args:
            strategy_name: name of the strategy generating the signal
            market_data: market data dict passed to the strategy
            cycle_type: "intraday", "daily", or "monthly"

        Returns:
            {
                signal_id: str,
                strategy: str,
                signal: dict or None,
                live_result: dict or None,
                paper_results: list[dict],
                comparison: dict,
            }
        """
        from core.signal_comparator import SignalComparator

        signal_id = self._generate_signal_id(strategy_name)
        live_strategies = self._get_live_strategies()

        result = {
            "signal_id": signal_id,
            "strategy": strategy_name,
            "signal": None,
            "live_result": None,
            "paper_results": [],
            "comparison": None,
        }

        # Generate signal ONCE from market data
        signal = self._generate_signal_from_data(strategy_name, market_data, cycle_type)
        result["signal"] = signal

        if signal is None:
            logger.debug(
                f"Signal {signal_id}: no signal from {strategy_name} ({cycle_type})"
            )
            return result

        logger.info(
            f"Signal {signal_id}: {strategy_name} generated "
            f"{signal.get('direction', '?')} {signal.get('symbol', '?')}"
        )

        # Route to LIVE pipeline if strategy is in live set
        # Each pipeline gets its own copy of the signal to prevent cross-contamination
        is_live_strategy = strategy_name in live_strategies
        if is_live_strategy:
            live_pipeline = self.get_live_pipeline()
            if live_pipeline is not None and live_pipeline._active:
                result["live_result"] = live_pipeline.execute_signal(signal.copy(), signal_id)

        # Route to ALL paper pipelines (always)
        for name, pipeline in self.pipelines.items():
            if pipeline.mode == "PAPER" and pipeline._active:
                # Only route if the strategy is in this paper pipeline's strategy list
                # OR if it's a live strategy (mirror to paper for comparison)
                if (
                    strategy_name in pipeline.config.strategies
                    or is_live_strategy
                ):
                    paper_result = pipeline.execute_signal(signal.copy(), signal_id)
                    result["paper_results"].append(paper_result)

        # Compare results if we have a comparator
        if not hasattr(self, "_signal_comparator"):
            self._signal_comparator = SignalComparator()

        comparison = self._signal_comparator.compare(
            signal_id=signal_id,
            strategy=strategy_name,
            signal=signal,
            live_result=result["live_result"],
            paper_results=result["paper_results"],
        )
        result["comparison"] = comparison

        return result

    def _generate_signal_id(self, strategy_name: str) -> str:
        """Generate a unique signal ID for tracing.

        Format: SIG_{timestamp}_{strategy}_{uuid4_short}

        Args:
            strategy_name: name of the strategy

        Returns:
            Unique signal ID string.
        """
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        return f"SIG_{ts}_{strategy_name}_{short_uuid}"

    def _get_live_strategies(self) -> List[str]:
        """Return list of strategy names from the live pipeline config.

        Returns:
            List of strategy names. Empty if no live pipeline exists.
        """
        live_pipeline = self.get_live_pipeline()
        if live_pipeline is None:
            return []
        return list(live_pipeline.config.strategies)

    def _generate_signal_from_data(
        self, strategy_name: str, market_data: dict, cycle_type: str
    ) -> dict | None:
        """Generate a trading signal from market data for a strategy.

        This is the single point of signal generation -- called ONCE
        per strategy per cycle, then routed to all relevant pipelines.

        In production, this delegates to the actual strategy implementation
        loaded from intraday-backtesterV2/strategies/.

        Args:
            strategy_name: name of the strategy
            market_data: dict with OHLCV data, indicators, etc.
            cycle_type: "intraday", "daily", or "monthly"

        Returns:
            Signal dict or None if no signal.
        """
        # Placeholder: actual strategies are loaded dynamically.
        # This will be connected to the strategy registry in a future ticket.
        logger.debug(
            f"Generating signal from data: {strategy_name} ({cycle_type})"
        )
        return None

    def get_live_pipeline(self) -> Pipeline | None:
        """Get the live pipeline (there should be at most one)."""
        for pipeline in self.pipelines.values():
            if pipeline.mode == "LIVE":
                return pipeline
        return None

    def get_paper_pipelines(self) -> List[Pipeline]:
        """Get all paper pipelines."""
        return [p for p in self.pipelines.values() if p.mode == "PAPER"]

    def get_full_status(self) -> dict:
        """Status of all pipelines + engine health."""
        status = {
            "engine_initialized": self._initialized,
            "total_pipelines": len(self.pipelines),
            "live_pipeline": None,
            "paper_pipelines": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        for name, pipeline in self.pipelines.items():
            pipeline_status = pipeline.get_status()
            pipeline_status["name"] = name
            if pipeline.mode == "LIVE":
                status["live_pipeline"] = pipeline_status
            else:
                status["paper_pipelines"].append(pipeline_status)

        return status

    def emergency_shutdown(self, reason: str) -> dict:
        """Emergency: shutdown LIVE pipeline only. Paper continues.

        Args:
            reason: description of the emergency

        Returns:
            {live_shutdown: dict, paper_status: list[dict]}
        """
        result = {
            "live_shutdown": None,
            "paper_status": [],
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        logger.critical(f"EMERGENCY SHUTDOWN: {reason}")

        # Shutdown LIVE only
        live = self.get_live_pipeline()
        if live is not None:
            result["live_shutdown"] = live.shutdown(reason=f"EMERGENCY: {reason}")
        else:
            result["live_shutdown"] = {"message": "No live pipeline to shutdown"}

        # Paper pipelines continue -- just report their status
        for pipeline in self.get_paper_pipelines():
            result["paper_status"].append(pipeline.get_status())

        self._save_state()
        return result

    def shutdown_all(self, reason: str = "normal") -> dict:
        """Graceful shutdown of all pipelines.

        Args:
            reason: shutdown reason

        Returns:
            {results: {name: shutdown_result}}
        """
        results = {
            "results": {},
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        logger.info(f"Shutting down all pipelines (reason: {reason})")

        # Shutdown LIVE first, then paper
        for name, pipeline in sorted(
            self.pipelines.items(),
            key=lambda x: 0 if x[1].mode == "LIVE" else 1,
        ):
            try:
                results["results"][name] = pipeline.shutdown(reason=reason)
            except Exception as exc:
                results["results"][name] = {
                    "mode": pipeline.mode,
                    "success": False,
                    "error": str(exc),
                }
                logger.error(f"Shutdown failed for '{name}': {exc}", exc_info=True)

        self._initialized = False
        self._save_state()

        logger.info("All pipelines shutdown complete")
        return results

    def _save_state(self):
        """Persist engine state to JSON (atomic write via temp file + os.replace)."""
        state = {
            "initialized": self._initialized,
            "pipelines": {},
            "last_saved": datetime.now(UTC).isoformat(),
        }

        for name, pipeline in self.pipelines.items():
            state["pipelines"][name] = {
                "mode": pipeline.mode,
                "active": pipeline._active,
                "strategies_enabled": dict(pipeline._strategies_enabled),
                "last_execution": pipeline._last_execution,
            }

        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: write to temp file, then rename
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_path.parent),
                suffix='.tmp'
            )
            try:
                with os.fdopen(tmp_fd, 'w') as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp_path, str(self._state_path))
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.error(f"Failed to save engine state: {exc}")

    def _load_state(self):
        """Load engine state from JSON (if it exists)."""
        if not self._state_path.exists():
            return

        try:
            with open(self._state_path) as f:
                state = json.load(f)

            for name, pstate in state.get("pipelines", {}).items():
                if name in self.pipelines:
                    pipeline = self.pipelines[name]
                    # Restore strategy enabled states
                    saved_strategies = pstate.get("strategies_enabled", {})
                    for sname, enabled in saved_strategies.items():
                        if sname in pipeline._strategies_enabled:
                            pipeline._strategies_enabled[sname] = enabled
                    pipeline._last_execution = pstate.get("last_execution")

            logger.info(f"Engine state loaded from {self._state_path}")
        except Exception as exc:
            logger.warning(f"Failed to load engine state: {exc}")
