"""P3-01: Cost Model Audit — systematic audit of modeled vs real costs.

Compares the cost assumptions used in backtests with actual broker costs.
For each broker/instrument pair, measures spread, commission, and slippage delta.

Output: reports/cost_audit.json
Action: if delta > 50% -> recalibrate cost model in backtester.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


@dataclass
class CostModel:
    """Modeled cost assumptions for a broker/instrument."""
    broker: str
    instrument: str
    instrument_type: str
    commission_per_trade: float  # $ or %
    commission_type: str  # "fixed" or "pct"
    spread_bps: float
    slippage_bps: float


# Modeled costs from CLAUDE.md and config
MODELED_COSTS: dict[str, CostModel] = {
    # IBKR FX
    "ibkr:EURUSD": CostModel("ibkr", "EURUSD", "fx", 2.0, "fixed", 1.0, 1.0),
    "ibkr:GBPUSD": CostModel("ibkr", "GBPUSD", "fx", 2.0, "fixed", 1.2, 1.0),
    "ibkr:USDJPY": CostModel("ibkr", "USDJPY", "fx", 2.0, "fixed", 1.0, 1.0),
    "ibkr:AUDUSD": CostModel("ibkr", "AUDUSD", "fx", 2.0, "fixed", 1.5, 1.5),
    "ibkr:EURGBP": CostModel("ibkr", "EURGBP", "fx", 2.0, "fixed", 1.5, 1.0),
    "ibkr:EURJPY": CostModel("ibkr", "EURJPY", "fx", 2.0, "fixed", 1.5, 1.5),
    "ibkr:AUDJPY": CostModel("ibkr", "AUDJPY", "fx", 2.0, "fixed", 2.0, 2.0),
    "ibkr:NZDUSD": CostModel("ibkr", "NZDUSD", "fx", 2.0, "fixed", 2.0, 1.5),
    # IBKR EU Equities
    "ibkr:MC.PA": CostModel("ibkr", "MC.PA", "eu_equity", 0.0005, "pct", 3.0, 3.0),
    "ibkr:SAP.DE": CostModel("ibkr", "SAP.DE", "eu_equity", 0.0005, "pct", 2.0, 2.0),
    "ibkr:ASML.AS": CostModel("ibkr", "ASML.AS", "eu_equity", 0.0005, "pct", 2.5, 2.5),
    # Binance Crypto (with BNB -25%)
    "binance:BTCUSDC": CostModel("binance", "BTCUSDC", "crypto", 0.075, "pct_bp", 1.0, 2.0),
    "binance:ETHUSDC": CostModel("binance", "ETHUSDC", "crypto", 0.075, "pct_bp", 1.5, 3.0),
    "binance:BNBUSDC": CostModel("binance", "BNBUSDC", "crypto", 0.075, "pct_bp", 3.0, 5.0),
    "binance:SOLUSDC": CostModel("binance", "SOLUSDC", "crypto", 0.075, "pct_bp", 3.0, 5.0),
    # Alpaca US (PFOF — $0 commission but wider spread)
    "alpaca:SPY": CostModel("alpaca", "SPY", "us_equity", 0.0, "fixed", 1.0, 2.0),
    "alpaca:QQQ": CostModel("alpaca", "QQQ", "us_equity", 0.0, "fixed", 1.0, 2.0),
    "alpaca:AAPL": CostModel("alpaca", "AAPL", "us_equity", 0.0, "fixed", 1.5, 2.0),
}


@dataclass
class CostAuditResult:
    """Result of auditing one instrument's costs."""
    broker: str
    instrument: str
    instrument_type: str
    model_spread_bps: float
    real_spread_bps: float | None
    spread_delta_pct: float | None
    model_slippage_bps: float
    real_slippage_bps: float | None
    slippage_delta_pct: float | None
    model_commission: float
    real_commission: float | None
    commission_delta_pct: float | None
    recalibration_needed: bool = False
    note: str = ""


@dataclass
class CostAuditReport:
    """Full audit report across all brokers."""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    results: dict[str, CostAuditResult] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def add_result(self, key: str, result: CostAuditResult):
        self.results[key] = result

    def compute_summary(self):
        total = len(self.results)
        needs_recalib = sum(1 for r in self.results.values() if r.recalibration_needed)
        by_broker: dict[str, list] = {}
        for key, r in self.results.items():
            by_broker.setdefault(r.broker, []).append(r)

        self.summary = {
            "total_instruments": total,
            "needs_recalibration": needs_recalib,
            "recalibration_pct": round(needs_recalib / total * 100, 1) if total else 0,
            "by_broker": {
                broker: {
                    "total": len(results),
                    "needs_recalib": sum(1 for r in results if r.recalibration_needed),
                    "avg_spread_delta_pct": _safe_avg([
                        r.spread_delta_pct for r in results
                        if r.spread_delta_pct is not None
                    ]),
                }
                for broker, results in by_broker.items()
            },
        }

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "summary": self.summary,
            "results": {
                k: {
                    "broker": r.broker,
                    "instrument": r.instrument,
                    "instrument_type": r.instrument_type,
                    "model_spread_bps": r.model_spread_bps,
                    "real_spread_bps": r.real_spread_bps,
                    "spread_delta_pct": r.spread_delta_pct,
                    "model_slippage_bps": r.model_slippage_bps,
                    "real_slippage_bps": r.real_slippage_bps,
                    "slippage_delta_pct": r.slippage_delta_pct,
                    "model_commission": r.model_commission,
                    "real_commission": r.real_commission,
                    "commission_delta_pct": r.commission_delta_pct,
                    "recalibration_needed": r.recalibration_needed,
                    "note": r.note,
                }
                for k, r in self.results.items()
            },
        }

    def save(self, path: Path | None = None):
        path = path or (REPORTS_DIR / "cost_audit.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Cost audit report saved to %s", path)


class CostAuditor:
    """Audits modeled vs real costs for all broker/instrument pairs.

    Usage:
        auditor = CostAuditor()
        # Register real cost observations
        auditor.register_real_spread("ibkr", "EURUSD", 0.8)
        auditor.register_real_slippage("ibkr", "EURUSD", 1.2)
        # Run audit
        report = auditor.run_audit()
        report.save()
    """

    RECALIB_THRESHOLD = 0.50  # 50% delta triggers recalibration

    def __init__(self):
        self._real_spreads: dict[str, float] = {}
        self._real_slippages: dict[str, float] = {}
        self._real_commissions: dict[str, float] = {}

    def register_real_spread(self, broker: str, instrument: str, spread_bps: float):
        key = f"{broker}:{instrument}"
        self._real_spreads[key] = spread_bps

    def register_real_slippage(self, broker: str, instrument: str, slippage_bps: float):
        key = f"{broker}:{instrument}"
        self._real_slippages[key] = slippage_bps

    def register_real_commission(self, broker: str, instrument: str, commission: float):
        key = f"{broker}:{instrument}"
        self._real_commissions[key] = commission

    def run_audit(self) -> CostAuditReport:
        """Run full cost audit comparing modeled vs real costs."""
        report = CostAuditReport()

        for key, model in MODELED_COSTS.items():
            real_spread = self._real_spreads.get(key)
            real_slip = self._real_slippages.get(key)
            real_comm = self._real_commissions.get(key)

            spread_delta = _delta_pct(model.spread_bps, real_spread)
            slip_delta = _delta_pct(model.slippage_bps, real_slip)
            comm_delta = _delta_pct(model.commission_per_trade, real_comm)

            needs_recalib = any(
                d is not None and abs(d) > self.RECALIB_THRESHOLD * 100
                for d in [spread_delta, slip_delta]
            )

            notes = []
            if spread_delta is not None and spread_delta > 50:
                notes.append(f"spread {spread_delta:+.0f}% vs model")
            if slip_delta is not None and slip_delta > 50:
                notes.append(f"slippage {slip_delta:+.0f}% vs model")

            result = CostAuditResult(
                broker=model.broker,
                instrument=model.instrument,
                instrument_type=model.instrument_type,
                model_spread_bps=model.spread_bps,
                real_spread_bps=real_spread,
                spread_delta_pct=spread_delta,
                model_slippage_bps=model.slippage_bps,
                real_slippage_bps=real_slip,
                slippage_delta_pct=slip_delta,
                model_commission=model.commission_per_trade,
                real_commission=real_comm,
                commission_delta_pct=comm_delta,
                recalibration_needed=needs_recalib,
                note="; ".join(notes),
            )
            report.add_result(key, result)

            if needs_recalib:
                logger.warning(
                    "RECALIBRATION NEEDED: %s — %s", key, result.note
                )

        report.compute_summary()
        return report

    def audit_from_execution_db(self, db_path: Path | None = None) -> CostAuditReport:
        """Run audit using data from the execution metrics SQLite DB.

        Reads real slippage and commission data from cost_log table.
        """
        import sqlite3

        db_path = db_path or Path(__file__).parent.parent.parent / "data" / "execution_metrics.db"
        if not db_path.exists():
            logger.warning("Execution DB not found at %s — running with modeled data only", db_path)
            return self.run_audit()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT instrument, instrument_type, "
                "       AVG(commission / notional_value * 10000) as avg_comm_bps, "
                "       COUNT(*) as n_trades "
                "FROM cost_log "
                "WHERE notional_value > 0 "
                "GROUP BY instrument"
            ).fetchall()

            for row in rows:
                instrument = row["instrument"]
                avg_bps = row["avg_comm_bps"]
                # Try to match with known brokers
                for broker in ["ibkr", "binance", "alpaca"]:
                    key = f"{broker}:{instrument}"
                    if key in MODELED_COSTS:
                        self.register_real_commission(broker, instrument, avg_bps)
                        break
        finally:
            conn.close()

        return self.run_audit()


def _delta_pct(model: float, real: float | None) -> float | None:
    """Calculate percentage delta between model and real."""
    if real is None:
        return None
    if model == 0:
        return 100.0 if real > 0 else 0.0
    return round((real - model) / model * 100, 1)


def _safe_avg(values: list[float]) -> float | None:
    filtered = [v for v in values if v is not None]
    return round(sum(filtered) / len(filtered), 1) if filtered else None
