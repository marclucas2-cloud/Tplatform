"""MacroECB event-driven futures cycle (extracted from worker.py).

V15.4 LIVE: triggered ~14:50 Paris on ECB Governing Council meeting days.
Fetches DAX/CAC40/ESTX50 5min bars and computes the 30-min move post 14:15
announcement. If |move| > 0.15%, emits BUY/SELL bracket orders to IBKR live
port 4002 via _make_macro_ecb_executor (kill switch + 2-contract hard cap +
dedup-per-future + recalc SL/TP from fill + standalone OCA SL+TP).

Backtest 2021-2026: Sharpe 3.18, +$7,004 / 5 ans, WF 4/6 yearly PASS.

Extracted 2026-04-19 (Phase 2 XXL plan) for worker.py decomposition.
Behavior unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from core.worker.alerts import send_alert as _send_alert

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]


def make_macro_ecb_executor(mode: str, ibkr_lock: threading.Lock | None = None):
    """Factory for MacroECB futures executor — places bracket orders on IBKR.

    Reuses the same execution pattern as _run_futures_cycle (market entry +
    standalone OCA SL/TP, recalc SL/TP from fill, state file, journal, Telegram).
    Applies kill switch check + hard limit 2 contracts + dedup per future.

    Args:
        mode: "LIVE" or "PAPER" — controls state file path and journal DB.
        ibkr_lock: optional lock (unused in executor itself but kept for API parity)

    Returns:
        callable(sig, ib) -> bool — True if order placed, False otherwise.
    """
    state_path = ROOT / "data" / "state" / f"futures_positions_{mode.lower()}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    journal_db = ROOT / "data" / ("live_journal.db" if mode == "LIVE" else "paper_journal.db")
    ks_state_path = ROOT / "data" / "kill_switch_state.json"

    opened_this_cycle: dict = {}
    INDEX_TO_EXCHANGE = {"DAX": "EUREX", "CAC40": "MONEP", "ESTX50": "EUREX"}
    MAX_FUTURES_CONTRACTS = 2

    def _executor(sig, ib) -> bool:
        import uuid as _uuid

        from ib_insync import Future as IbFuture
        from ib_insync import LimitOrder, StopOrder
        from ib_insync import MarketOrder as IbMarketOrder

        try:
            if ks_state_path.exists():
                ks = json.loads(ks_state_path.read_text(encoding="utf-8"))
                if ks.get("active", False):
                    logger.critical(f"    MACRO ECB {mode}: KILL SWITCH ACTIVE — refusing to trade")
                    return False
        except Exception as e:
            logger.warning(f"    MACRO ECB: KS state read error: {e}")

        try:
            real_pos = {p.contract.symbol: p.position for p in ib.positions() if abs(p.position) > 0}
        except Exception as e:
            logger.warning(f"    MACRO ECB: positions() failed: {e}")
            real_pos = {}

        total_real = sum(abs(int(v)) for v in real_pos.values())
        total_fresh = len(opened_this_cycle)
        if total_real + total_fresh >= MAX_FUTURES_CONTRACTS:
            logger.warning(
                f"    MACRO ECB {mode}: HARD LIMIT reached "
                f"({total_real} real + {total_fresh} fresh = {total_real + total_fresh}), skipping {sig.symbol}"
            )
            return False

        future_sym = sig.symbol
        exchange = INDEX_TO_EXCHANGE.get(sig.symbol)
        if not exchange:
            logger.error(f"    MACRO ECB: unknown index {sig.symbol}")
            return False

        if future_sym in real_pos or future_sym in opened_this_cycle:
            logger.warning(f"    MACRO ECB {mode}: already positioned on {future_sym}, skipping")
            return False

        try:
            fut = IbFuture(future_sym, exchange=exchange, currency="EUR")
            details = ib.reqContractDetails(fut)
            if not details:
                logger.error(f"    MACRO ECB: no contract details for {future_sym}@{exchange}")
                return False
            contract = details[0].contract

            qty = 1

            # P0 FIX 2026-04-23: explicit TIF=DAY to prevent IBKR Error 10349
            # "Order TIF was set to DAY based on order preset" which cancels
            # live orders on U25023333 canonical account.
            entry = IbMarketOrder(sig.side, qty)
            entry.tif = "DAY"
            trade = ib.placeOrder(contract, entry)
            time.sleep(4); ib.sleep(2)
            fill_price = trade.orderStatus.avgFillPrice or 0

            if trade.orderStatus.status != "Filled":
                logger.warning(
                    f"    MACRO ECB: {future_sym} entry not filled ({trade.orderStatus.status})"
                )
                try:
                    ib.cancelOrder(trade.order)
                except Exception:
                    pass
                return False

            sl_offset = abs(sig.stop_loss - fill_price) if sig.stop_loss else 20
            tp_offset = abs(sig.take_profit - fill_price) if sig.take_profit else 40
            if sig.side == "BUY":
                real_sl = round(fill_price - sl_offset, 2)
                real_tp = round(fill_price + tp_offset, 2)
            else:
                real_sl = round(fill_price + sl_offset, 2)
                real_tp = round(fill_price - tp_offset, 2)

            exit_side = "SELL" if sig.side == "BUY" else "BUY"
            oca = f"OCA_ECB_{future_sym}_{_uuid.uuid4().hex[:8]}"

            sl_ord = StopOrder(exit_side, qty, real_sl)
            sl_ord.tif = "GTC"; sl_ord.ocaGroup = oca; sl_ord.ocaType = 1; sl_ord.outsideRth = True
            ib.placeOrder(contract, sl_ord)
            time.sleep(1)

            tp_ord = LimitOrder(exit_side, qty, real_tp)
            tp_ord.tif = "GTC"; tp_ord.ocaGroup = oca; tp_ord.ocaType = 1; tp_ord.outsideRth = True
            ib.placeOrder(contract, tp_ord)
            time.sleep(2); ib.sleep(1)

            logger.info(
                f"    MACRO ECB {mode}: {sig.side} {future_sym} @ {fill_price:.2f} "
                f"SL={real_sl} TP={real_tp} [OCA {oca}]"
            )

            fut_positions = {}
            try:
                if state_path.exists():
                    fut_positions = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            fut_positions[future_sym] = {
                "strategy": f"MacroECB_{sig.symbol}",
                "symbol": future_sym,
                "side": sig.side,
                "qty": qty,
                "entry": fill_price,
                "sl": real_sl,
                "tp": real_tp,
                "oca_group": oca,
                "opened_at": datetime.now(UTC).isoformat(),
                "mode": mode,
                "_authorized_by": f"macro_ecb_{mode.lower()}",
            }
            try:
                state_path.write_text(json.dumps(fut_positions, indent=2))
            except Exception as se:
                logger.error(f"    MACRO ECB state write failed: {se}")

            try:
                import sqlite3 as _sql
                _jconn = _sql.connect(str(journal_db))
                _jconn.execute(
                    "INSERT OR IGNORE INTO trades (trade_id, strategy, instrument, direction, "
                    "quantity, entry_price, entry_time, status, broker, asset_class) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 'IBKR', 'futures')",
                    (oca, f"MacroECB_{sig.symbol}", future_sym, sig.side, qty, fill_price,
                     datetime.now(UTC).isoformat()),
                )
                _jconn.commit()
                _jconn.close()
            except Exception as je:
                logger.debug(f"MACRO ECB journal write skip: {je}")

            try:
                _send_alert(
                    f"MACRO ECB {mode}: {sig.side} {future_sym} @ {fill_price:.2f}\n"
                    f"SL={real_sl} TP={real_tp}\n"
                    f"Strat: MacroECB_{sig.symbol}",
                    level="warning" if mode == "LIVE" else "info",
                )
            except Exception:
                pass

            opened_this_cycle[future_sym] = True
            return True

        except Exception as e:
            logger.error(f"    MACRO ECB: order failed for {sig.symbol}: {e}", exc_info=True)
            return False

    return _executor


def run_macro_ecb_live_cycle(ibkr_lock: threading.Lock):
    """MacroECB event-driven cycle — V15.4 LIVE mode.

    Triggered ~14:50 Paris on ECB Governing Council meeting days. Fetches
    DAX/CAC40/ESTX50 5min bars and computes the 30-min move post 14:15
    announcement. If |move| > 0.15%, emits BUY/SELL bracket orders to IBKR
    live port 4002.

    Safety: requires env MACRO_ECB_LIVE_ENABLED=true to actually send orders.
    Otherwise falls back to dry_run (log signals only). This gives a final
    kill switch between code deploy and going live.
    """
    if not ibkr_lock.acquire(blocking=False):
        logger.warning("MACRO ECB SKIP — IBKR lock held")
        return
    try:
        from core.worker.cycles.macro_ecb_cycle import run_macro_ecb_cycle

        live_enabled = os.environ.get("MACRO_ECB_LIVE_ENABLED", "false").lower() == "true"
        mode = "LIVE" if live_enabled else "PAPER"

        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        if live_enabled:
            port = int(os.environ.get("IBKR_PORT", "4002"))
        else:
            port = int(os.environ.get("IBKR_PAPER_PORT", "4003"))

        executor = make_macro_ecb_executor(mode=mode) if live_enabled else None

        result = run_macro_ecb_cycle(
            ibkr_host=host,
            ibkr_port=port,
            dry_run=not live_enabled,
            futures_executor=executor,
        )

        if result.get("skipped"):
            logger.info(f"  MACRO ECB {mode} skipped: {result['skipped']}")
        elif result.get("signals"):
            n_sig = len(result["signals"])
            n_sent = len(result.get("sent_orders", []))
            logger.info(f"  MACRO ECB {mode}: {n_sig} signals, {n_sent} orders sent")
            for s in result["signals"]:
                logger.info(f"    -> {s['side']} {s['index_symbol']} @ {s['entry_price']:.2f} "
                            f"SL={s['stop_loss']:.2f} TP={s['take_profit']:.2f}")
        else:
            logger.info(f"  MACRO ECB {mode}: ECB day but no signal (move below threshold)")
    except Exception as e:
        logger.error(f"MACRO ECB cycle error: {e}", exc_info=True)
    finally:
        ibkr_lock.release()
