"""Runner paper pour pead_long_only_v1 (research 2026-04-30).

Paper pur local : pas d'ordres broker. Journal JSONL dedie + state file.
Appele une fois par jour weekday a 22:30 Paris (apres close US 16:00 ET / 22:00 UTC ete).

Pattern reutilise depuis macro_top1_rotation_runner.py.

Logique :
  1. Refresh earnings calendar (yfinance, TTL 24h)
  2. Load US prices cache (sp500_prices_cache.parquet)
  3. Pour chaque ticker dans UNIVERSE :
     - Verifier earnings event dans fenetre [J-2, J] avec surprise >= 5%
     - Verifier gap up >= 1% (long-only, donc surprise > 0 only)
     - Si signal et capacite (max_concurrent) : journal entry + update state
  4. Pour positions actives :
     - Time-exit apres hold 20j
     - TP +8% / SL -3%
     - Journal exit + update state
  5. Persist state + journal

Config validee par WF discoverer 2026-04-30 :
  surprise_threshold = 5%, gap_threshold = 1%, hold_days = 20
  TP = 8%, SL = 3%, notional = $2K, max_concurrent = 5
  Sharpe 1.01, MaxDD -10.3%, 5/5 WF pass
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from core.data.earnings_calendar import get_recent_earnings, refresh_earnings

logger = logging.getLogger("worker")

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / "data" / "state" / "pead_long_only_v1"
STATE_FILE = STATE_DIR / "state.json"
JOURNAL_FILE = STATE_DIR / "journal.jsonl"
PRICE_CACHE = ROOT / "data" / "us_research" / "sp500_prices_cache.parquet"

# Config validee WF discoverer 2026-04-30
UNIVERSE = [
    "AAPL", "ABBV", "ADBE", "AMZN", "AVGO", "BAC", "COST", "CRM", "CSCO", "CVX",
    "DIS", "GOOGL", "HD", "JNJ", "JPM", "KO", "LLY", "MA", "META", "MSFT",
    "NFLX", "NVDA", "ORCL", "PEP", "PG", "TSLA", "UNH", "V", "WMT", "XOM",
]
SURPRISE_THRESHOLD_PCT = 5.0
GAP_THRESHOLD_PCT = 1.0
HOLD_DAYS = 20
TP_PCT = 8.0
SL_PCT = 3.0
NOTIONAL_PER_TRADE = 2000.0
MAX_CONCURRENT = 5
EARNINGS_WINDOW_DAYS = 2


def _empty_state() -> dict[str, Any]:
    return {
        "active_positions": {},  # ticker -> {entry_date, entry_price, side, sl, tp, hold_days}
        "last_cycle_utc": None,
        "cycle_count": 0,
    }


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _empty_state()
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"pead state corrupted ({exc}), resetting")
        return _empty_state()


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _append_journal(event: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _load_prices() -> pd.DataFrame:
    """Load US prices cache (close adjusted). Returns wide df indexed by date."""
    if not PRICE_CACHE.exists():
        raise FileNotFoundError(
            f"Price cache missing: {PRICE_CACHE}. "
            f"Run scripts/research/_alpaca_discovery_pead_2026-04-30.py to populate."
        )
    df = pd.read_parquet(PRICE_CACHE)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def _compute_gap_pct(prices: pd.DataFrame, ticker: str, target_date: date) -> float | None:
    """Open vs previous close gap. None si pas de donnee."""
    if ticker not in prices.columns:
        return None
    series = prices[ticker].dropna()
    target_ts = pd.Timestamp(target_date)
    series = series[series.index <= target_ts]
    if len(series) < 2:
        return None
    today_close = float(series.iloc[-1])  # proxy open ~= close prev (US close cache)
    prev_close = float(series.iloc[-2])
    if prev_close == 0:
        return None
    # Note: cache contient close-only, donc on approxime gap par close-to-close
    # En live reel avec open intraday, ameliorer en tirant l'open separement.
    return ((today_close - prev_close) / prev_close) * 100.0


def _check_exits(
    state: dict[str, Any], prices: pd.DataFrame, target_date: date
) -> list[dict[str, Any]]:
    """Check time-exit / TP / SL pour chaque position active. Retourne events exit."""
    exits = []
    target_ts = pd.Timestamp(target_date)
    for ticker, pos in list(state["active_positions"].items()):
        pos["hold_days"] = pos.get("hold_days", 0) + 1
        exit_reason = None
        exit_price = None

        # current close
        if ticker in prices.columns:
            series = prices[ticker].dropna()
            series = series[series.index <= target_ts]
            if len(series) > 0:
                exit_price = float(series.iloc[-1])

        if exit_price is not None and pos["side"] == "BUY":
            entry = pos["entry_price"]
            if exit_price >= entry * (1 + TP_PCT / 100.0):
                exit_reason = "take_profit"
            elif exit_price <= entry * (1 - SL_PCT / 100.0):
                exit_reason = "stop_loss"

        if exit_reason is None and pos["hold_days"] >= HOLD_DAYS:
            exit_reason = "time_exit"

        if exit_reason:
            entry = pos["entry_price"]
            if exit_price is None:
                exit_price = entry  # fallback : pas de prix dispo
            pnl_pct = ((exit_price - entry) / entry * 100.0) if pos["side"] == "BUY" else ((entry - exit_price) / entry * 100.0)
            pnl_usd = NOTIONAL_PER_TRADE * pnl_pct / 100.0
            exits.append({
                "event": "exit",
                "ticker": ticker,
                "side": pos["side"],
                "entry_date": pos["entry_date"],
                "entry_price": entry,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 4),
                "pnl_usd": round(pnl_usd, 2),
                "hold_days": pos["hold_days"],
                "reason": exit_reason,
            })
            del state["active_positions"][ticker]
    return exits


def _check_entries(
    state: dict[str, Any], prices: pd.DataFrame, target_date: date
) -> list[dict[str, Any]]:
    """Check earnings + gap + capacity pour signaux d'entree. Retourne events entry."""
    entries = []
    if len(state["active_positions"]) >= MAX_CONCURRENT:
        return entries  # cap atteint

    earnings = get_recent_earnings(UNIVERSE, target_date=target_date, window_days=EARNINGS_WINDOW_DAYS)
    target_ts = pd.Timestamp(target_date)

    for ticker, info in earnings.items():
        if len(state["active_positions"]) >= MAX_CONCURRENT:
            break
        if ticker in state["active_positions"]:
            continue  # deja en position

        surprise = info["surprise_pct"]
        if surprise < SURPRISE_THRESHOLD_PCT:
            continue  # long-only : skip surprises negatives ou faibles

        gap = _compute_gap_pct(prices, ticker, target_date)
        if gap is None or gap < GAP_THRESHOLD_PCT:
            continue  # gap doit confirmer la surprise

        # Generate signal
        if ticker not in prices.columns:
            continue
        series = prices[ticker].dropna()
        series = series[series.index <= target_ts]
        if len(series) == 0:
            continue
        entry_price = float(series.iloc[-1])
        sl_price = entry_price * (1 - SL_PCT / 100.0)
        tp_price = entry_price * (1 + TP_PCT / 100.0)

        state["active_positions"][ticker] = {
            "entry_date": str(target_date),
            "entry_price": entry_price,
            "side": "BUY",
            "sl_price": round(sl_price, 4),
            "tp_price": round(tp_price, 4),
            "hold_days": 0,
            "surprise_pct": surprise,
            "gap_pct": round(gap, 4),
        }
        entries.append({
            "event": "entry",
            "ticker": ticker,
            "side": "BUY",
            "entry_price": entry_price,
            "sl_price": round(sl_price, 4),
            "tp_price": round(tp_price, 4),
            "surprise_pct": surprise,
            "gap_pct": round(gap, 4),
            "earnings_date": info["date"],
            "notional_usd": NOTIONAL_PER_TRADE,
        })
    return entries


def run_pead_paper_cycle() -> None:
    """Daily paper cycle for PEAD long-only v1.

    Call once per weekday at 22:30 Paris (after US close 16:00 ET).
    Pure local simulation, no broker orders.
    """
    logger.info("=== PEAD PAPER CYCLE ===")
    cycle_ts = datetime.now(UTC).isoformat()
    today = date.today()

    # 1. Refresh earnings (TTL 24h)
    try:
        n_new = refresh_earnings(UNIVERSE)
        if n_new > 0:
            logger.info(f"  PEAD earnings refresh: +{n_new} events")
    except Exception as exc:
        logger.warning(f"PEAD earnings refresh failed: {exc} (using stale cache)")

    # 2. Load prices
    try:
        prices = _load_prices()
    except Exception as exc:
        logger.error(f"PEAD price load failed: {exc}")
        _append_journal({
            "event": "cycle_error",
            "ts": cycle_ts,
            "reason": "price_load_failed",
            "error": str(exc),
        })
        return

    state = _load_state()

    # 3. Check exits first (free up slots)
    exits = _check_exits(state, prices, today)
    for ev in exits:
        ev["ts"] = cycle_ts
        _append_journal(ev)
        logger.info(f"  PEAD exit {ev['ticker']}: {ev['reason']} pnl={ev['pnl_usd']:+.2f} ({ev['pnl_pct']:+.2f}%)")

    # 4. Check entries
    entries = _check_entries(state, prices, today)
    for ev in entries:
        ev["ts"] = cycle_ts
        _append_journal(ev)
        logger.info(
            f"  PEAD entry {ev['ticker']} BUY @{ev['entry_price']:.2f} "
            f"surprise={ev['surprise_pct']:.1f}% gap={ev['gap_pct']:.2f}%"
        )

    # 5. Cycle summary
    state["last_cycle_utc"] = cycle_ts
    state["cycle_count"] = state.get("cycle_count", 0) + 1
    _save_state(state)

    n_active = len(state["active_positions"])
    logger.info(
        f"=== PEAD PAPER CYCLE TERMINE: {len(entries)} entry, {len(exits)} exit, "
        f"{n_active}/{MAX_CONCURRENT} positions actives ==="
    )
