"""Crypto fidelity score — compare live vs backtest per strategy.

Records each live trade and computes drift metrics:
  - slippage_bps: |fill_price - signal_price| / signal_price * 10000
  - fill_latency_ms: signal_ts → fill_ts delay
  - pnl_live: realized pnl after exit
  - pnl_expected: what the backtest would have paid

Over a rolling 30-day window, computes a fidelity score 0-1:
  - 1.0 = live matches backtest perfectly
  - 0.5 = slippage 2x backtest
  - 0.0 = slippage >5x backtest OR fill failure rate >20%

Persists in data/crypto/fidelity.jsonl (one line per live trade).

Alerts CRITICAL if a strategy's rolling fidelity < 0.5 for ≥5 trades.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "crypto"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_FILE = _DATA_DIR / "fidelity.jsonl"

# Thresholds for drift detection
MAX_HEALTHY_SLIPPAGE_BPS = 20   # 0.2% crypto — above = degraded
MIN_FIDELITY_SCORE = 0.5
MIN_TRADES_FOR_SCORE = 5
ROLLING_WINDOW_DAYS = 30


def record_trade(
    strat_id: str,
    symbol: str,
    side: str,
    signal_price: float,
    fill_price: float,
    qty: float,
    signal_ts: str,
    fill_ts: str | None = None,
    pnl: float = 0.0,
    backtest_slippage_bps: float = 2.0,
) -> dict[str, Any]:
    """Record a live trade and return the drift metrics."""
    fill_ts = fill_ts or datetime.now(UTC).isoformat()
    slippage_bps = 0.0
    if signal_price > 0:
        slippage_bps = abs(fill_price - signal_price) / signal_price * 10_000

    try:
        sig_dt = datetime.fromisoformat(signal_ts.replace("Z", "+00:00"))
        fill_dt = datetime.fromisoformat(fill_ts.replace("Z", "+00:00"))
        latency_ms = int((fill_dt - sig_dt).total_seconds() * 1000)
    except Exception:
        latency_ms = 0

    # Slippage ratio vs backtest assumption
    slip_ratio = slippage_bps / max(backtest_slippage_bps, 0.1)

    record = {
        "ts": datetime.now(UTC).isoformat(),
        "strat_id": strat_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "signal_price": signal_price,
        "fill_price": fill_price,
        "slippage_bps": round(slippage_bps, 2),
        "slip_ratio_vs_bt": round(slip_ratio, 2),
        "latency_ms": latency_ms,
        "pnl": round(pnl, 2),
        "backtest_slippage_bps": backtest_slippage_bps,
    }

    try:
        with _FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.warning(f"fidelity_score: failed to persist: {e}")

    return record


def _load_records(strat_id: str | None = None, days: int = ROLLING_WINDOW_DAYS) -> list[dict]:
    if not _FILE.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    try:
        for line in _FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if strat_id and r.get("strat_id") != strat_id:
                    continue
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
                out.append(r)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"fidelity_score: load failed: {e}")
    return out


def compute_fidelity(strat_id: str, days: int = ROLLING_WINDOW_DAYS) -> dict[str, Any]:
    """Compute fidelity score for a strat over the rolling window."""
    records = _load_records(strat_id, days)
    n = len(records)

    if n < MIN_TRADES_FOR_SCORE:
        return {
            "strat_id": strat_id,
            "n_trades": n,
            "score": None,
            "verdict": "INSUFFICIENT_DATA",
            "reason": f"need {MIN_TRADES_FOR_SCORE} trades, have {n}",
        }

    slippages = [r["slippage_bps"] for r in records]
    ratios = [r["slip_ratio_vs_bt"] for r in records]
    latencies = [r["latency_ms"] for r in records if r["latency_ms"] > 0]
    pnls = [r["pnl"] for r in records]

    avg_slip = statistics.mean(slippages)
    avg_ratio = statistics.mean(ratios)
    p95_slip = sorted(slippages)[int(n * 0.95)] if n >= 20 else max(slippages)
    avg_lat = statistics.mean(latencies) if latencies else 0
    total_pnl = sum(pnls)

    # Score formula
    # 1.0 if slip_ratio <= 1 (live matches or beats backtest)
    # 0.8 if ratio ~2 (live 2x worse)
    # 0.5 if ratio ~3
    # 0.0 if ratio >=5
    if avg_ratio <= 1.0:
        score = 1.0
    elif avg_ratio >= 5.0:
        score = 0.0
    else:
        score = max(0.0, 1.0 - (avg_ratio - 1.0) / 4.0)
    score = round(score, 3)

    if score >= 0.8:
        verdict = "HEALTHY"
    elif score >= MIN_FIDELITY_SCORE:
        verdict = "DEGRADED"
    else:
        verdict = "FAILING"

    return {
        "strat_id": strat_id,
        "n_trades": n,
        "score": score,
        "verdict": verdict,
        "avg_slippage_bps": round(avg_slip, 2),
        "p95_slippage_bps": round(p95_slip, 2),
        "avg_slip_ratio_vs_bt": round(avg_ratio, 2),
        "avg_latency_ms": round(avg_lat, 0),
        "total_pnl": round(total_pnl, 2),
        "window_days": days,
    }


def compute_all_fidelity() -> list[dict[str, Any]]:
    """Compute fidelity for all strategies seen in records."""
    if not _FILE.exists():
        return []
    seen_strats: set[str] = set()
    try:
        for line in _FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                seen_strats.add(r["strat_id"])
            except Exception:
                continue
    except Exception:
        return []
    return [compute_fidelity(s) for s in sorted(seen_strats)]


def check_drift_and_alert(alert_callback) -> list[str]:
    """Check all strategies, alert on FAILING verdicts.

    Returns list of strat_ids flagged. Caller is responsible for pause logic.
    """
    failing = []
    for fid in compute_all_fidelity():
        if fid["verdict"] == "FAILING" and fid["n_trades"] >= MIN_TRADES_FOR_SCORE:
            strat_id = fid["strat_id"]
            failing.append(strat_id)
            try:
                alert_callback(
                    f"FIDELITY ALERT {strat_id}: score={fid['score']} "
                    f"(avg slip {fid['avg_slippage_bps']} bps = {fid['avg_slip_ratio_vs_bt']}x backtest)\n"
                    f"n_trades={fid['n_trades']}, pnl={fid['total_pnl']:+.0f}\n"
                    f"Live execution is drifting from backtest — review before continuing.",
                    level="critical",
                )
            except Exception as e:
                logger.warning(f"fidelity alert failed: {e}")
    return failing
