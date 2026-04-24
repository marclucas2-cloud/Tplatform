"""Runner paper pour macro_top1_rotation (research 2026-04-24).

Paper pur local: pas d'ordres broker. Journal JSONL dedie + state file.
Appele une fois par jour (weekday) depuis worker.py.

Pattern:
  1. Load or refresh ETF prices (yfinance cache 24h non-prod path)
  2. Load state (last_rebal_date, current_symbol, last_cycle_ts)
  3. Call strategy.decide(prices, now, last_rebal, current)
  4. If action=rebalance: journal signal_emit + update state
     If action=hold:      journal hold
     If action=no_signal:  journal no_signal
  5. Persist state
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from strategies_v2.us.macro_top1_rotation import MacroTop1Rotation, UNIVERSE

logger = logging.getLogger("worker")

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / "data" / "state" / "macro_top1_rotation"
STATE_FILE = STATE_DIR / "state.json"
JOURNAL_FILE = STATE_DIR / "journal.jsonl"
PRICE_CACHE = ROOT / "data" / "research" / "target_alpha_us_sectors_2026_04_24_prices.parquet"


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "last_rebal_date": None,
            "current_symbol": None,
            "last_cycle_utc": None,
            "rebal_count": 0,
        }
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"macro_top1_rotation: state corrupted ({e}), resetting")
        return {
            "last_rebal_date": None,
            "current_symbol": None,
            "last_cycle_utc": None,
            "rebal_count": 0,
        }


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _append_journal(event: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _load_prices() -> pd.DataFrame:
    """Load ETF prices from the non-prod research cache.

    If cache is stale > 7d, we log a warning but do NOT auto-refresh here
    (keeps the runner deterministic and offline-friendly for paper).
    Freshness update happens via manual rerun of the research script.
    """
    if not PRICE_CACHE.exists():
        raise FileNotFoundError(
            f"Price cache missing: {PRICE_CACHE}. "
            f"Run scripts/research/target_alpha_us_sectors_and_new_assets_2026_04_24.py first."
        )
    df = pd.read_parquet(PRICE_CACHE)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def _check_freshness(prices: pd.DataFrame, max_age_days: int = 7) -> dict[str, Any]:
    last_date = prices.index.max()
    now = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    age_days = int((now - last_date).days)
    return {
        "last_price_date": str(last_date.date()),
        "age_days": age_days,
        "stale": age_days > max_age_days,
    }


def run_macro_top1_rotation_cycle() -> None:
    """Daily paper cycle for macro_top1_rotation.

    Call once per weekday (schedule at 16:00 Paris = 10:00 ET, after US open).
    Pure local simulation — no broker orders.
    """
    logger.info("=== MACRO_TOP1_ROTATION PAPER CYCLE ===")
    now = pd.Timestamp.now(tz="UTC")
    now_local = now.tz_localize(None).normalize()

    try:
        prices = _load_prices()
    except Exception as e:
        logger.error(f"  macro_top1_rotation: price load FAIL — {e}")
        _append_journal({
            "ts_utc": now.isoformat(),
            "strategy_id": "macro_top1_rotation",
            "event": "skip_reason",
            "reason": f"price_load_fail:{e}",
        })
        return

    freshness = _check_freshness(prices)
    if freshness["stale"]:
        logger.warning(
            f"  macro_top1_rotation: price cache stale "
            f"(last={freshness['last_price_date']}, age={freshness['age_days']}d). "
            f"Proceeding with stale data (paper mode)."
        )

    state = _load_state()
    last_rebal = pd.Timestamp(state["last_rebal_date"]) if state.get("last_rebal_date") else None
    current_sym = state.get("current_symbol")

    strat = MacroTop1Rotation()
    decision = strat.decide(
        prices=prices,
        now_date=now_local,
        last_rebal_date=last_rebal,
        current_symbol=current_sym,
    )

    event = {
        "ts_utc": now.isoformat(),
        "strategy_id": "macro_top1_rotation",
        "bar_date": str(prices.index.max().date()),
        "freshness": freshness,
        "state_before": {
            "last_rebal_date": state.get("last_rebal_date"),
            "current_symbol": state.get("current_symbol"),
            "rebal_count": state.get("rebal_count", 0),
        },
        "decision": {
            "action": decision.action,
            "target_symbol": decision.target_symbol,
            "previous_symbol": decision.previous_symbol,
            "rebalance_due": decision.rebalance_due,
            "reason": decision.reason,
            "top3": decision.top3,
        },
    }

    if decision.action == "rebalance":
        prev = current_sym
        target = decision.target_symbol
        logger.info(
            f"  macro_top1_rotation (paper): REBAL {prev or 'CASH'} -> {target} "
            f"(top3 {decision.top3})"
        )
        state["last_rebal_date"] = str(now_local.date())
        state["current_symbol"] = target
        state["rebal_count"] = int(state.get("rebal_count", 0)) + 1
        state["last_cycle_utc"] = now.isoformat()
        event["event"] = "signal_emit"
    elif decision.action == "hold":
        logger.info(
            f"  macro_top1_rotation (paper): HOLD {current_sym} "
            f"({decision.reason})"
        )
        state["last_cycle_utc"] = now.isoformat()
        event["event"] = "hold"
    else:
        logger.info(
            f"  macro_top1_rotation (paper): NO_SIGNAL ({decision.reason})"
        )
        state["last_cycle_utc"] = now.isoformat()
        event["event"] = "no_signal"

    try:
        _save_state(state)
    except Exception as e:
        logger.warning(f"  macro_top1_rotation: state save failed — {e}")

    try:
        _append_journal(event)
    except Exception as e:
        logger.warning(f"  macro_top1_rotation: journal write failed — {e}")
