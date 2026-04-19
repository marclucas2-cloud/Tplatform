"""Periodic broker contract validation cycle (Phase 4 XXL).

Runs READ-ONLY API calls against each broker hourly and validates response
shapes against contracts. Detects API breaking changes proactively.

Tolerance (from ContractRunner):
- 1 violation: WARN
- 3 consecutive: CRITICAL alert + BrokerHealthRegistry mark_unhealthy

Integration in worker.py:

    from core.broker.contracts.validation_cycle import run_contract_validation_cycle
    from core.broker.contracts.contract_runner import ContractRunner

    _contract_runner = ContractRunner(
        alert_callback=_send_alert,
        metrics_callback=_metrics.gauge,
    )

    def run_contract_validation_cycle_wrapper():
        run_contract_validation_cycle(
            runner=_contract_runner,
            binance_broker=_binance_broker,
            ibkr_client=_ibkr_client,
            alpaca_client=_alpaca_client,
            health_registry=_broker_health,
        )

    scheduler.add_job(
        run_contract_validation_cycle_wrapper,
        trigger="interval", hours=1, id="contract_validation",
    )
"""
from __future__ import annotations

import logging
from typing import Any

from core.broker.contracts.alpaca_contracts import AlpacaContract
from core.broker.contracts.binance_contracts import BinanceContract
from core.broker.contracts.contract_runner import ContractRunner
from core.broker.contracts.ibkr_contracts import IBKRContract

logger = logging.getLogger(__name__)


def run_contract_validation_cycle(
    runner: ContractRunner,
    binance_broker: Any | None = None,
    ibkr_client: Any | None = None,
    alpaca_client: Any | None = None,
    health_registry: Any | None = None,
) -> dict:
    """Run contract validation against all configured brokers.

    Each broker is independent — failure in one doesn't affect others.
    Returns dict with results per broker.
    """
    results: dict[str, dict] = {}

    # --- Binance ---
    if binance_broker is not None:
        results["binance"] = _validate_binance(runner, binance_broker)

    # --- IBKR ---
    if ibkr_client is not None:
        results["ibkr"] = _validate_ibkr(runner, ibkr_client)

    # --- Alpaca ---
    if alpaca_client is not None:
        results["alpaca"] = _validate_alpaca(runner, alpaca_client)

    # --- Update broker health registry on critical violations ---
    if health_registry is not None:
        for broker_name in results:
            if not runner.is_contract_healthy(broker_name):
                try:
                    health_registry.mark_degraded(
                        broker_name,
                        reason="contract_violations",
                    )
                except (AttributeError, Exception) as exc:
                    logger.warning(
                        f"BrokerHealthRegistry mark_degraded failed for {broker_name}: {exc}"
                    )

    return results


def _validate_binance(runner: ContractRunner, broker: Any) -> dict:
    """Validate Binance API responses (read-only)."""
    out: dict[str, Any] = {"endpoints_tested": []}
    try:
        # account / margin
        if hasattr(broker, "_client"):
            try:
                resp = broker._client.account()
                runner.validate(
                    "binance", "account_balance", resp,
                    BinanceContract.account_balance,
                )
                out["endpoints_tested"].append("account_balance")
            except Exception as e:
                logger.warning(f"Binance contract: account() call failed: {e}")
                out["account_call_error"] = str(e)
    except Exception as exc:
        logger.error(f"Binance contract validation error: {exc}")
        out["error"] = str(exc)
    return out


def _validate_ibkr(runner: ContractRunner, ibkr: Any) -> dict:
    """Validate IBKR responses (via adapter get_account_info())."""
    out: dict[str, Any] = {"endpoints_tested": []}
    try:
        if hasattr(ibkr, "get_account_info"):
            resp = ibkr.get_account_info()
            runner.validate(
                "ibkr", "account_info", resp,
                IBKRContract.account_info,
            )
            out["endpoints_tested"].append("account_info")
    except Exception as exc:
        logger.error(f"IBKR contract validation error: {exc}")
        out["error"] = str(exc)
    return out


def _validate_alpaca(runner: ContractRunner, alpaca: Any) -> dict:
    """Validate Alpaca responses."""
    out: dict[str, Any] = {"endpoints_tested": []}
    try:
        if hasattr(alpaca, "get_account_info"):
            resp = alpaca.get_account_info()
            runner.validate(
                "alpaca", "account", resp,
                AlpacaContract.account,
            )
            out["endpoints_tested"].append("account")
    except Exception as exc:
        logger.error(f"Alpaca contract validation error: {exc}")
        out["error"] = str(exc)
    return out
