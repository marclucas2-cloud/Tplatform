"""Live-micro runner for btc_asia_mes_leadlag_q80_v80_long_only.

Phase 2 desk productif plan 2026-04-22. Premier sleeve live_micro validated
by Marc: $200 USDC notional BTCUSDC, kill DD -$50, max 1 position, no pyramid.

Flow per cycle (10h30 Paris):
  1. Load open positions + kill flag
  2. EXIT: iterate positions, if kill_dd or max_hold_24h -> SELL + alert
  3. ENTRY: if signal_lo.side == "BUY" + 0 open + no kill flag -> BUY + alert
  4. Journal every event (success, rejected, error) -- "vérité live" principle

State files:
  data/state/btc_asia_mes_leadlag_q80_live_micro/
    positions.json              -- open positions {id: {symbol, qty, entry_price, entry_time_utc, entry_cost_usd}}
    journal.jsonl               -- append-only event log
    _kill_switch.json           -- present => skip further entries (rollback path)
    _last_cycle.json            -- debug: last execution timestamp + verdict
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent.parent

STRATEGY_ID = "btc_asia_mes_leadlag_q80_v80_long_only"
SYMBOL = "BTCUSDC"
NOTIONAL_USD = 200.0
RISK_USD = 20.0           # conservative 10% stop convention
KILL_DD_USD = 50.0        # auto-kill sleeve if unrealized DD breaches -$50
MAX_HOLD_HOURS = 24.0     # safety max hold (live_micro simplification, 1 daily cycle)
GRADE = "B"               # current WF grade for cap lookup

STATE_DIR = ROOT / "data" / "state" / "btc_asia_mes_leadlag_q80_live_micro"
POSITIONS_PATH = STATE_DIR / "positions.json"
JOURNAL_PATH = STATE_DIR / "journal.jsonl"
KILL_FLAG_PATH = STATE_DIR / "_kill_switch.json"
LAST_CYCLE_PATH = STATE_DIR / "_last_cycle.json"

logger = logging.getLogger(__name__)


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _load_positions() -> dict[str, dict]:
    if not POSITIONS_PATH.exists():
        return {}
    try:
        return json.loads(POSITIONS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("live_micro q80: positions.json unreadable, treating as empty")
        return {}


def _save_positions(positions: dict[str, dict]) -> None:
    _ensure_state_dir()
    POSITIONS_PATH.write_text(
        json.dumps(positions, indent=2, sort_keys=True), encoding="utf-8",
    )


def _is_kill_switch_on() -> tuple[bool, dict | None]:
    if not KILL_FLAG_PATH.exists():
        return False, None
    try:
        data = json.loads(KILL_FLAG_PATH.read_text(encoding="utf-8"))
        return True, data
    except Exception:
        return True, {"error": "unreadable_flag_file"}


def _trigger_kill_switch(reason: str, detail: dict) -> None:
    """Write kill flag -- next cycle skips entries. Marc must clear manually."""
    _ensure_state_dir()
    payload = {
        "triggered_at_utc": datetime.now(UTC).isoformat(),
        "reason": reason,
        "detail": detail,
        "instruction": (
            "To re-enable: delete data/state/btc_asia_mes_leadlag_q80_live_micro/_kill_switch.json "
            "AND set quant_registry/live_whitelist status back to paper_only OR live_micro."
        ),
    }
    KILL_FLAG_PATH.write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )
    logger.critical(f"live_micro q80: KILL SWITCH TRIGGERED reason={reason} detail={detail}")


def _journal_event(event: dict) -> None:
    _ensure_state_dir()
    event.setdefault("ts_utc", datetime.now(UTC).isoformat())
    event.setdefault("strategy_id", STRATEGY_ID)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def _write_last_cycle(verdict: str, extra: dict | None = None) -> None:
    _ensure_state_dir()
    payload = {
        "ts_utc": datetime.now(UTC).isoformat(),
        "verdict": verdict,
    }
    if extra:
        payload.update(extra)
    LAST_CYCLE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _telegram_alert(level: str, title: str, body: str) -> None:
    """Safe alert. Failure never blocks trading."""
    try:
        from core.worker.alerts import send_alert
        send_alert(f"[live_micro q80] {title}\n{body}", level=level)
    except Exception as e:
        logger.warning(f"live_micro q80: telegram alert failed ({e})")


def _get_current_btc_price(broker) -> float | None:
    """Fetch current BTCUSDC mid price via Binance API. None on failure."""
    try:
        ticker = broker._get("/api/v3/ticker/price", {"symbol": SYMBOL})
        return float(ticker.get("price", 0)) or None
    except Exception as e:
        logger.warning(f"live_micro q80: get_current_btc_price failed: {e}")
        return None


def _extract_fill_details(result: dict) -> tuple[float, float]:
    """From a Binance order response, extract (filled_qty, avg_price)."""
    filled_qty = float(result.get("executedQty", 0))
    fills = result.get("fills", [])
    if not fills or filled_qty <= 0:
        return 0.0, 0.0
    gross = sum(float(f["price"]) * float(f["qty"]) for f in fills)
    qty_sum = sum(float(f["qty"]) for f in fills)
    avg_price = gross / qty_sum if qty_sum > 0 else 0.0
    return filled_qty, avg_price


def run_exit_if_needed(broker, live_start_at_iso: str) -> int:
    """Iterate open positions, exit if kill_dd or max_hold breach. Returns #exits."""
    positions = _load_positions()
    if not positions:
        return 0

    current_price = _get_current_btc_price(broker)
    if current_price is None:
        logger.warning("live_micro q80: no current price, skip exit check this cycle")
        return 0

    now = datetime.now(UTC)
    exits = 0

    for pos_id, pos in list(positions.items()):
        qty = float(pos.get("qty", 0))
        entry_price = float(pos.get("entry_price", 0))
        entry_cost = float(pos.get("entry_cost_usd", 0))
        if qty <= 0 or entry_price <= 0:
            logger.warning(f"live_micro q80: invalid position {pos_id}, skipping")
            continue

        try:
            entry_ts = datetime.fromisoformat(pos["entry_time_utc"])
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.replace(tzinfo=UTC)
        except Exception:
            entry_ts = now

        hours_held = (now - entry_ts).total_seconds() / 3600.0
        unrealized_pnl = qty * (current_price - entry_price) - entry_cost

        should_exit = False
        exit_reason = None
        kill_sleeve = False

        if unrealized_pnl <= -KILL_DD_USD:
            should_exit = True
            exit_reason = "kill_dd_50"
            kill_sleeve = True
        elif hours_held >= MAX_HOLD_HOURS:
            should_exit = True
            exit_reason = "max_hold_24h"

        if not should_exit:
            continue

        try:
            result = broker.create_position(
                symbol=SYMBOL,
                direction="SELL",
                qty=qty,
                _authorized_by=f"{STRATEGY_ID}:live_micro_exit_{exit_reason}",
            )
            filled_qty, avg_exit_price = _extract_fill_details(result)
            realized_pnl = filled_qty * (avg_exit_price - entry_price) - entry_cost
            _journal_event({
                "event": "exit",
                "pos_id": pos_id,
                "exit_reason": exit_reason,
                "qty": filled_qty,
                "entry_price": entry_price,
                "exit_price": avg_exit_price,
                "hours_held": round(hours_held, 2),
                "realized_pnl_usd": round(realized_pnl, 2),
                "raw_result": result,
            })
            _telegram_alert(
                "critical" if kill_sleeve else "warning",
                f"EXIT {exit_reason}",
                (
                    f"Symbol: {SYMBOL}\n"
                    f"Qty: {filled_qty:.6f}\n"
                    f"Entry: ${entry_price:.2f}\n"
                    f"Exit: ${avg_exit_price:.2f}\n"
                    f"PnL: ${realized_pnl:+.2f}\n"
                    f"Hold: {hours_held:.1f}h"
                ),
            )
            del positions[pos_id]
            exits += 1

            if kill_sleeve:
                _trigger_kill_switch(
                    reason="kill_dd_50_triggered_on_exit",
                    detail={
                        "pos_id": pos_id,
                        "realized_pnl_usd": round(realized_pnl, 2),
                    },
                )
                _telegram_alert(
                    "critical",
                    "KILL SLEEVE live_micro q80",
                    (
                        f"Sleeve auto-disabled after DD <= -$50.\n"
                        f"Realized: ${realized_pnl:+.2f}\n"
                        f"Manual action required: review + reset status paper_only."
                    ),
                )
        except Exception as e:
            logger.error(f"live_micro q80: SELL FAILED pos={pos_id}: {e}", exc_info=True)
            _journal_event({
                "event": "exec_error",
                "pos_id": pos_id,
                "attempted_action": "sell_exit",
                "exit_reason": exit_reason,
                "error": f"{type(e).__name__}: {e}",
            })
            _telegram_alert(
                "critical",
                f"EXEC_ERROR exit {exit_reason}",
                f"SELL {SYMBOL} failed: {type(e).__name__}: {str(e)[:200]}",
            )

    _save_positions(positions)
    return exits


def run_entry_if_needed(broker, signal_side: str, signal_details: dict, live_start_at_iso: str) -> bool:
    """If signal=BUY + 0 open + kill flag clear: place BUY. Returns True if fill OK."""
    kill_on, kill_data = _is_kill_switch_on()
    if kill_on:
        _journal_event({
            "event": "entry_skipped",
            "reason": "kill_switch_active",
            "kill_detail": kill_data,
            "signal": signal_details,
        })
        return False

    if signal_side != "BUY":
        _journal_event({
            "event": "entry_skipped",
            "reason": f"signal_side={signal_side}",
            "signal": signal_details,
        })
        return False

    positions = _load_positions()
    open_count = sum(1 for p in positions.values() if float(p.get("qty", 0)) > 0)
    if open_count > 0:
        _journal_event({
            "event": "entry_skipped",
            "reason": f"already_open_positions={open_count}_no_pyramid_until_j14",
            "signal": signal_details,
        })
        return False

    try:
        from core.governance.live_micro_sizing import (
            LiveMicroViolation,
            can_pyramid,
            enforce_sizing,
        )
        enforce_sizing(GRADE, NOTIONAL_USD, risk_usd=RISK_USD)
        ok_pyr, reason_pyr = can_pyramid(live_start_at_iso, open_count)
        if not ok_pyr:
            _journal_event({
                "event": "entry_rejected",
                "reason": f"pyramid_check_fail: {reason_pyr}",
                "signal": signal_details,
            })
            return False
    except LiveMicroViolation as lmv:
        _journal_event({
            "event": "entry_rejected",
            "reason": f"sizing_violation: {lmv.reason}",
            "detail": lmv.detail,
            "signal": signal_details,
        })
        _telegram_alert(
            "critical",
            "ENTRY REJECTED sizing_violation",
            f"{lmv.reason} | detail={lmv.detail}",
        )
        return False

    try:
        result = broker.create_position(
            symbol=SYMBOL,
            direction="BUY",
            notional=NOTIONAL_USD,
            _authorized_by=f"{STRATEGY_ID}:live_micro_entry",
        )
        filled_qty, avg_price = _extract_fill_details(result)
        if filled_qty <= 0 or avg_price <= 0:
            _journal_event({
                "event": "exec_error",
                "attempted_action": "buy_entry",
                "error": "zero_fill_returned",
                "raw_result": result,
            })
            _telegram_alert(
                "critical",
                "EXEC_ERROR entry",
                f"BUY {SYMBOL} returned 0 fill. raw={str(result)[:200]}",
            )
            return False

        pos_id = f"q80_lm_{datetime.now(UTC).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
        positions[pos_id] = {
            "pos_id": pos_id,
            "symbol": SYMBOL,
            "qty": filled_qty,
            "entry_price": avg_price,
            "entry_time_utc": datetime.now(UTC).isoformat(),
            "entry_cost_usd": NOTIONAL_USD * 0.001,  # Binance spot fee ~0.1%
            "signal_details": signal_details,
        }
        _save_positions(positions)
        _journal_event({
            "event": "entry",
            "pos_id": pos_id,
            "qty": filled_qty,
            "entry_price": avg_price,
            "notional_usd": NOTIONAL_USD,
            "signal": signal_details,
            "raw_result": result,
        })
        _telegram_alert(
            "warning",
            "ENTRY live_micro q80",
            (
                f"Symbol: {SYMBOL}\n"
                f"Qty: {filled_qty:.6f}\n"
                f"Entry: ${avg_price:.2f}\n"
                f"Notional: ${NOTIONAL_USD:.2f}\n"
                f"Kill DD: -${KILL_DD_USD:.0f}"
            ),
        )
        return True
    except Exception as e:
        logger.error(f"live_micro q80: BUY FAILED: {e}", exc_info=True)
        _journal_event({
            "event": "exec_error",
            "attempted_action": "buy_entry",
            "error": f"{type(e).__name__}: {e}",
            "signal": signal_details,
        })
        _telegram_alert(
            "critical",
            "EXEC_ERROR entry",
            f"BUY {SYMBOL} failed: {type(e).__name__}: {str(e)[:200]}",
        )
        return False


def run_live_micro_cycle(signal_side: str, signal_details: dict, live_start_at_iso: str) -> dict:
    """Entry point called from paper_cycles when q80 status=live_micro.

    Returns summary dict for caller journaling.
    """
    _ensure_state_dir()
    summary: dict[str, Any] = {"ts_utc": datetime.now(UTC).isoformat()}
    try:
        from core.broker.binance_broker import BinanceBroker
        broker = BinanceBroker()
    except Exception as e:
        logger.error(f"live_micro q80: broker init failed: {e}")
        _journal_event({
            "event": "exec_error",
            "attempted_action": "broker_init",
            "error": f"{type(e).__name__}: {e}",
        })
        _telegram_alert("critical", "BROKER_INIT_FAIL", f"{type(e).__name__}: {e}")
        summary["verdict"] = "broker_init_failed"
        _write_last_cycle(summary["verdict"], summary)
        return summary

    summary["exits"] = run_exit_if_needed(broker, live_start_at_iso)
    summary["entered"] = run_entry_if_needed(broker, signal_side, signal_details, live_start_at_iso)
    summary["kill_switch_on"], _ = _is_kill_switch_on()
    summary["verdict"] = "ok"
    _write_last_cycle(summary["verdict"], summary)
    return summary
