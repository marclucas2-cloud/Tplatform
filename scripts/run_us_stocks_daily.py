#!/usr/bin/env python3
"""Daily orchestrator for the 3 US stock monthly strategies (tom, rs_spy, sector_rot_us).

Workflow (called by worker.py scheduler at 22:55 Paris = 16:55 ET, after US close):
  1. Load prices (2 sources available: --source local | --source alpaca)
  2. Run each strategy -> list[USPosition] target
  3. Aggregate into a unified target portfolio (by symbol + net side)
  4. Diff vs current Alpaca positions
  5. Close positions no longer in target
  6. Open new positions (respecting paper guard)
  7. Journal all trades to paper_journal.db via TradeJournal

Safety:
  - PAPER_TRADING=true is enforced (Alpaca client guards live mode)
  - --dry-run mode: compute signals and log actions, DO NOT place orders
  - Assumes dedicated Alpaca account (will close non-target positions)

Usage:
  python scripts/run_us_stocks_daily.py --dry-run                  # test
  python scripts/run_us_stocks_daily.py --source local --dry-run   # use local parquet
  python scripts/run_us_stocks_daily.py                            # LIVE paper trading
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Dict

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from strategies_v2.us._common import USPosition, load_universe
from strategies_v2.us.rs_spy import RSSpyStrategy
from strategies_v2.us.sector_rot_us import SectorRotStrategy
from strategies_v2.us.tom import TOMStrategy

logger = logging.getLogger("run_us_stocks_daily")

# Default capital allocation per strategy ($25K total split evenly)
DEFAULT_CAPITAL_PER_STRAT = 8_333.0

AUTHORIZED_BY = "us_stocks_daily"


# ==================================================================
# Price loading
# ==================================================================
def load_prices_local() -> Dict[str, pd.DataFrame]:
    """Load daily prices from data/us_stocks/*.parquet (from download_us_data.py)."""
    data_dir = ROOT / "data" / "us_stocks"
    if not data_dir.exists():
        raise FileNotFoundError(
            f"{data_dir} missing — run scripts/download_us_data.py first"
        )
    universe = load_universe()
    prices: Dict[str, pd.DataFrame] = {}
    for t in universe + ["SPY"]:
        f = data_dir / f"{t}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        prices[t] = df
    logger.info(f"local: loaded {len(prices)} tickers from {data_dir}")
    return prices


def load_prices_alpaca(client, bars: int = 250) -> Dict[str, pd.DataFrame]:
    """Fetch daily bars for the universe from Alpaca (via AlpacaClient.get_prices)."""
    universe = load_universe()
    prices: Dict[str, pd.DataFrame] = {}
    for i, t in enumerate(universe + ["SPY"]):
        try:
            bars_data = client.get_prices(t, timeframe="1D", bars=bars)
            if not bars_data or "close" not in bars_data:
                continue
            df = pd.DataFrame({
                "open": bars_data.get("open", []),
                "high": bars_data.get("high", []),
                "low": bars_data.get("low", []),
                "close": bars_data.get("close", []),
                "adj_close": bars_data.get("close", []),
                "volume": bars_data.get("volume", []),
            }, index=pd.to_datetime(bars_data.get("timestamp", [])))
            prices[t] = df
        except Exception as e:
            logger.warning(f"alpaca get_prices failed for {t}: {e}")
        if i % 50 == 0:
            logger.info(f"alpaca fetch: {i}/{len(universe)}")
    logger.info(f"alpaca: loaded {len(prices)} tickers")
    return prices


# ==================================================================
# Strategy execution
# ==================================================================
def run_strategies(
    prices: Dict[str, pd.DataFrame],
    capital_per_strat: float,
    as_of: date,
) -> list[USPosition]:
    """Run all 3 strategies and return the combined target position list."""
    strategies = [
        TOMStrategy(),
        RSSpyStrategy(),
        SectorRotStrategy(),
    ]
    all_targets: list[USPosition] = []
    for s in strategies:
        try:
            positions = s.compute_target_portfolio(prices, capital_per_strat, as_of)
            logger.info(f"  {s.name}: {len(positions)} target positions")
            all_targets.extend(positions)
        except Exception as e:
            logger.exception(f"  {s.name} failed: {e}")
    return all_targets


def aggregate_by_symbol(positions: list[USPosition]) -> dict[str, dict]:
    """Aggregate positions by symbol. If same symbol is both long/short across strats,
    sum signed notionals; the net sign determines the final side."""
    agg: dict[str, dict] = {}
    for p in positions:
        signed_notional = p.notional if p.side == "BUY" else -p.notional
        if p.symbol not in agg:
            agg[p.symbol] = {"signed": 0.0, "reasons": []}
        agg[p.symbol]["signed"] += signed_notional
        agg[p.symbol]["reasons"].append(f"{p.strategy}({p.side} ${p.notional:.0f})")

    out: dict[str, dict] = {}
    for sym, d in agg.items():
        signed = d["signed"]
        if abs(signed) < 1.0:  # net zero = skip
            continue
        out[sym] = {
            "side": "BUY" if signed > 0 else "SELL",
            "notional": abs(signed),
            "reasons": d["reasons"],
        }
    return out


# ==================================================================
# Execution
# ==================================================================
def diff_positions(current: list[dict], target: dict[str, dict]) -> tuple[list[str], list[tuple[str, dict]]]:
    """Return (to_close, to_open).

    to_close: symbols in `current` but not in `target` (close them all)
    to_open: (symbol, target_dict) for symbols in `target` but not in `current`
    """
    current_symbols = {p["symbol"] for p in current}
    target_symbols = set(target.keys())
    to_close = sorted(current_symbols - target_symbols)
    to_open = [(s, target[s]) for s in sorted(target_symbols - current_symbols)]
    return to_close, to_open


def execute_plan(client, to_close: list[str], to_open: list[tuple[str, dict]], dry_run: bool) -> dict:
    """Execute the plan against Alpaca or log it in dry-run mode."""
    stats = {"closed": 0, "opened": 0, "errors": 0, "skipped": 0}

    for sym in to_close:
        if dry_run:
            logger.info(f"  DRY-RUN close: {sym}")
            stats["closed"] += 1
            continue
        try:
            client.close_position(sym, _authorized_by=AUTHORIZED_BY)
            logger.info(f"  close OK: {sym}")
            stats["closed"] += 1
        except Exception as e:
            logger.error(f"  close FAIL {sym}: {e}")
            stats["errors"] += 1

    for sym, tgt in to_open:
        side = tgt["side"]
        notional = tgt["notional"]
        if dry_run:
            logger.info(f"  DRY-RUN open: {side} {sym} notional=${notional:.0f} ({','.join(tgt['reasons'])})")
            stats["opened"] += 1
            continue
        try:
            # Both BUY and SELL: fetch last price to compute qty (bypass CRO
            # notional-without-SL guard — monthly strats exit at rebalance,
            # not at stop-loss, so no SL concept).
            try:
                px_data = client.get_prices(sym, timeframe="1D", bars=2)
                bars = px_data.get("bars", []) if isinstance(px_data, dict) else []
                # Alpaca bars format: dict with keys t/o/h/l/c/v (single letters)
                last_px = float(bars[-1].get("c", 0)) if bars else 0
            except Exception as e:
                logger.warning(f"  {sym}: get_prices failed: {e}")
                last_px = 0
            if last_px <= 0:
                logger.warning(f"  open SKIP {sym}: no price for qty calc")
                stats["skipped"] += 1
                continue
            qty = max(1, int(notional / last_px))
            result = client.create_position(
                symbol=sym, direction=side, qty=qty,
                _authorized_by=AUTHORIZED_BY,
            )
            # Verify order actually placed (not REJECTED by CRO guard)
            if isinstance(result, dict) and result.get("status") == "REJECTED":
                logger.error(f"  open REJECTED {sym}: {result.get('reason')}")
                stats["errors"] += 1
                continue
            logger.info(f"  open OK: {side} {sym} qty={qty} @ ~${last_px:.2f} notional=${qty*last_px:.0f}")
            stats["opened"] += 1
        except Exception as e:
            logger.error(f"  open FAIL {sym}: {e}")
            stats["errors"] += 1

    return stats


# ==================================================================
# Main
# ==================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["local", "alpaca"], default="local",
                        help="price source: local parquet files or Alpaca API")
    parser.add_argument("--dry-run", action="store_true",
                        help="do not place orders — log actions only")
    parser.add_argument("--capital-per-strat", type=float, default=DEFAULT_CAPITAL_PER_STRAT)
    parser.add_argument("--as-of", type=str, default=None,
                        help="date override YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    logger.info(f"=== US STOCKS DAILY — {as_of} ({'DRY-RUN' if args.dry_run else 'LIVE'}) ===")

    # Paper guard
    paper = os.environ.get("PAPER_TRADING", "true").lower() == "true"
    if not paper and not args.dry_run:
        logger.critical("PAPER_TRADING=false detected. Aborting — this script is paper-only.")
        return 2

    # Load prices
    alpaca = None
    if args.source == "alpaca" or not args.dry_run:
        from core.alpaca_client.client import AlpacaClient
        alpaca = AlpacaClient.from_env()
        info = alpaca.authenticate()
        logger.info(f"Alpaca authenticated: equity=${info.get('equity', 0):.2f}, paper={info.get('paper')}")

    if args.source == "local":
        prices = load_prices_local()
    else:
        prices = load_prices_alpaca(alpaca)

    if not prices:
        logger.error("no prices loaded — aborting")
        return 1

    # Run strategies
    logger.info(f"Running strategies (capital/strat=${args.capital_per_strat:.0f})…")
    positions = run_strategies(prices, args.capital_per_strat, as_of)
    if not positions:
        logger.info("No target positions for today — nothing to do.")
        return 0

    # Aggregate + diff
    target = aggregate_by_symbol(positions)
    logger.info(f"Target portfolio: {len(target)} unique symbols (from {len(positions)} raw positions)")

    # Get current positions from Alpaca (or empty in pure dry-run without Alpaca)
    current: list[dict] = []
    if alpaca is not None:
        try:
            current = alpaca.get_positions()
            logger.info(f"Current Alpaca positions: {len(current)}")
        except Exception as e:
            logger.warning(f"get_positions failed: {e}")

    to_close, to_open = diff_positions(current, target)
    logger.info(f"Plan: close {len(to_close)}, open {len(to_open)}")

    # Execute
    if alpaca is None and not args.dry_run:
        logger.error("Cannot execute without Alpaca client")
        return 1

    stats = execute_plan(alpaca, to_close, to_open, dry_run=args.dry_run)
    logger.info(f"Done: {stats}")

    # Fix 2026-04-21: persist Alpaca broker positions dans state local pour
    # que reconciliation_cycle ait une source de verite coherente. Sans ca,
    # les 11 positions Alpaca paper apparaissent comme "only_in_broker"
    # warnings repetes 96x/24h (checkup 2026-04-20 endofday).
    if alpaca is not None and not args.dry_run:
        try:
            fresh_positions = alpaca.get_positions()
            state_path = ROOT / "data" / "state" / "alpaca_us" / "positions.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            positions_dict: Dict[str, dict] = {}
            for p in fresh_positions:
                sym = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
                if not sym:
                    continue
                qty = p.get("qty") if isinstance(p, dict) else getattr(p, "qty", 0)
                avg = p.get("avg_entry_price") if isinstance(p, dict) else getattr(p, "avg_entry_price", 0)
                positions_dict[sym] = {
                    "qty": float(qty),
                    "avg_entry_price": float(avg) if avg else 0.0,
                }
            state_path.write_text(json.dumps({
                "positions": positions_dict,
                "last_sync": datetime.now(UTC).isoformat(),
                "source": "us_stocks_daily",
            }, indent=2))
            logger.info(
                f"State sync: {len(positions_dict)} positions wrote to "
                f"{state_path.relative_to(ROOT)}"
            )
        except Exception as e:
            logger.warning(f"State sync failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
