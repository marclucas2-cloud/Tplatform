"""Trailing stop manager for futures positions.

Monitors open futures positions and ratchets the SL upward as price
moves in favor. Uses the bracket watchdog's IBKR connection to modify
SL orders in-place via modify_stop_loss or cancel+replace.

Variant B (gold_trend_mgc V2):
  - Initial SL: 0.4% below entry (same as V1)
  - Trailing: 0.4% below highest price since entry
  - TP: 0.8% above entry (same as V1, fixed)
  - SL only ratchets UP, never down

Configuration per strategy is stored in the state file alongside
the position. Each position tracks `highest_since_entry` for the
trailing calculation.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("trailing_stop_futures")

ROOT = Path(__file__).resolve().parent.parent.parent

# Per-strategy trailing config. Strategies not listed here use fixed SL (no trailing).
TRAILING_CONFIG = {
    "gold_trend_mgc": {
        "trail_pct": 0.004,   # 0.4% trailing distance from high
        "tp_pct": 0.008,      # 0.8% fixed TP (unchanged from V1)
        "min_move_ticks": 1.0,  # minimum SL change to submit (avoid noise)
        "tick_size": 0.10,    # MGC tick size
    },
}


def compute_trailing_sl(
    entry_price: float,
    highest_price: float,
    current_price: float,
    trail_pct: float,
    current_sl: float,
    side: str = "BUY",
) -> float | None:
    """Compute new trailing SL price. Returns None if no change needed.

    The SL only ratchets in the profitable direction:
    - LONG: SL moves UP as highest_price increases
    - SHORT: SL moves DOWN as lowest_price decreases (not implemented yet)
    """
    if side != "BUY":
        return None  # SHORT trailing not implemented yet

    # New trailing SL = highest * (1 - trail_pct)
    new_sl = round(highest_price * (1 - trail_pct), 2)

    # Ratchet: never move SL down
    if new_sl <= current_sl:
        return None

    return new_sl


def update_trailing_stops(
    positions: dict[str, dict],
    current_prices: dict[str, float],
) -> list[dict]:
    """Check all positions and return list of SL modifications needed.

    Args:
        positions: {symbol: position_dict} from state file
        current_prices: {symbol: latest_price} from IBKR

    Returns:
        List of {symbol, old_sl, new_sl, highest, reason} for modifications.
    """
    modifications = []

    for sym, pos in positions.items():
        strategy = pos.get("strategy", "")
        # Find matching trailing config
        config = None
        for strat_id, cfg in TRAILING_CONFIG.items():
            if strat_id in strategy.lower() or strat_id == strategy:
                config = cfg
                break

        if config is None:
            continue  # No trailing for this strategy

        current_price = current_prices.get(sym)
        if current_price is None or current_price <= 0:
            continue

        entry = float(pos.get("entry", 0))
        current_sl = float(pos.get("sl", 0))
        side = pos.get("side", "BUY")
        highest = float(pos.get("highest_since_entry", entry))

        if entry <= 0 or current_sl <= 0:
            continue

        # Update highest price
        if current_price > highest:
            highest = current_price

        # Compute new trailing SL
        new_sl = compute_trailing_sl(
            entry_price=entry,
            highest_price=highest,
            current_price=current_price,
            trail_pct=config["trail_pct"],
            current_sl=current_sl,
            side=side,
        )

        if new_sl is not None:
            # Check minimum move (avoid noise)
            tick_size = config.get("tick_size", 0.1)
            min_move = config.get("min_move_ticks", 1.0) * tick_size
            if new_sl - current_sl < min_move:
                continue

            modifications.append({
                "symbol": sym,
                "old_sl": current_sl,
                "new_sl": round(new_sl, 2),
                "highest": round(highest, 2),
                "entry": entry,
                "current_price": current_price,
                "reason": f"trailing {config['trail_pct']*100:.1f}% from high {highest:.2f}",
            })

        # Always update highest in position (even if SL didn't change)
        pos["highest_since_entry"] = round(highest, 2)

    return modifications


def apply_modifications_ibkr(
    modifications: list[dict],
    ib,
    state: dict,
    state_path: Path,
) -> int:
    """Apply SL modifications to IBKR and update state file.

    Args:
        modifications: from update_trailing_stops()
        ib: connected ib_insync.IB instance
        state: mutable positions dict
        state_path: path to persist state

    Returns:
        Number of successful modifications.
    """
    from ib_insync import Future as IbFuture, Order

    applied = 0
    for mod in modifications:
        sym = mod["symbol"]
        pos = state.get(sym)
        if pos is None:
            continue

        oca_group = pos.get("oca_group", "")
        if not oca_group:
            logger.warning(f"TRAILING: {sym} no OCA group, skip modify")
            continue

        try:
            from ib_insync import Future as IbFuture, Order as IbOrder

            # Find the existing STP order on IBKR
            all_orders = ib.reqAllOpenOrders()
            stp_order = None
            for trade in all_orders:
                if (trade.contract.symbol == sym
                        and trade.order.orderType in ("STP", "STOP")
                        and trade.order.ocaGroup == oca_group
                        and trade.orderStatus.status not in ("Cancelled", "Filled")):
                    stp_order = trade
                    break

            if stp_order is None:
                logger.warning(f"TRAILING: {sym} STP order not found on IBKR (OCA={oca_group})")
                continue

            old_aux = stp_order.order.auxPrice

            # Cancel old STP then place new one (works across clientIds)
            ib.cancelOrder(stp_order.order)
            time.sleep(1)

            # Place new STP with same OCA
            from ib_insync import StopOrder
            new_stp = StopOrder(
                stp_order.order.action,
                int(stp_order.order.totalQuantity),
                mod["new_sl"],
            )
            new_stp.tif = "GTC"
            new_stp.ocaGroup = oca_group
            new_stp.ocaType = 1
            new_stp.outsideRth = True
            ib.placeOrder(stp_order.contract, new_stp)
            time.sleep(1)

            logger.info(
                f"TRAILING MODIFIED: {sym} SL {old_aux:.2f} -> {mod['new_sl']:.2f} "
                f"(high={mod['highest']:.2f}, {mod['reason']})"
            )

            # Update state
            pos["sl"] = mod["new_sl"]
            pos["highest_since_entry"] = mod["highest"]
            pos["trailing_last_modified"] = datetime.now(timezone.utc).isoformat()
            applied += 1

        except Exception as e:
            logger.error(f"TRAILING: {sym} modify failed: {e}")

    # Persist state
    if applied > 0:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.error(f"TRAILING: state persist failed: {e}")

    return applied
