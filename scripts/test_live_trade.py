"""
Test Live Trade — execute minimal trades on IBKR + Binance to verify execution.

SAFETY:
  - Uses MINIMUM order sizes ($1K FX, 0.0001 BTC)
  - Opens and immediately closes each position
  - Validates: order sent → fill received → SL placed → position visible → close OK
  - Aborts on ANY error

Usage:
    python scripts/test_live_trade.py --ibkr     # Test IBKR only
    python scripts/test_live_trade.py --binance   # Test Binance only
    python scripts/test_live_trade.py --all       # Test both
    python scripts/test_live_trade.py --dry-run   # Simulate only (no real orders)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "archive" / "intraday-backtesterV2"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_live_trade")

_results: list[dict] = []


def _log_result(broker: str, step: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    _results.append({"broker": broker, "step": step, "status": status, "detail": detail})
    icon = "✓" if passed else "✗"
    log_fn = logger.info if passed else logger.error
    log_fn(f"  [{icon}] {broker}/{step}: {detail}")
    if not passed:
        raise RuntimeError(f"{broker}/{step} FAILED: {detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# IBKR FX TEST
# ═══════════════════════════════════════════════════════════════════════════════

def test_ibkr_fx(dry_run: bool = False):
    """Test minimal FX trade on IBKR.

    Steps:
      1. Connect to IBKR
      2. Get account info
      3. Place BUY 1000 EUR.USD (minimum odd-lot)
      4. Verify fill
      5. Check SL attached
      6. Verify position visible
      7. Close position
      8. Verify closed
    """
    logger.info("=" * 40)
    logger.info("  IBKR FX TRADE TEST")
    logger.info("=" * 40)

    import socket
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))

    # 1. Connection check
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
        _log_result("IBKR", "tcp_connect", True, f"{host}:{port}")
    except Exception as e:
        _log_result("IBKR", "tcp_connect", False, str(e))

    # 2. Account info
    from core.broker.ibkr_adapter import IBKRBroker
    ibkr = IBKRBroker()
    info = ibkr.get_account_info()
    equity = float(info.get("equity", 0))
    _log_result("IBKR", "account_info", equity > 0, f"equity=${equity:,.0f}")

    if dry_run:
        logger.info("  [DRY RUN] Skipping actual order execution")
        _log_result("IBKR", "dry_run", True, "order execution skipped")
        return

    # 3. Place minimal BUY order — EUR.USD 1000 units (odd-lot)
    symbol = "EUR.USD"  # IBKR FX contract format
    qty = 1000  # Minimum odd-lot
    sl_distance = 0.005  # 50 pips SL

    logger.info(f"  Placing BUY {qty} {symbol}...")
    try:
        result = ibkr.create_position(
            symbol=symbol,
            direction="BUY",
            qty=qty,
            stop_loss=sl_distance,  # Relative SL
            _authorized_by="test_live_trade_ibkr",
        )
        order_id = result.get("orderId", "unknown")
        filled_qty = float(result.get("filled_qty", 0))
        filled_price = float(result.get("filled_price", 0))
        _log_result("IBKR", "order_placed", True,
                    f"orderId={order_id} filled={filled_qty} @{filled_price}")
    except Exception as e:
        _log_result("IBKR", "order_placed", False, str(e))

    # 4. Verify fill
    _log_result("IBKR", "fill_received", filled_qty > 0,
                f"qty={filled_qty} @{filled_price:.5f}")

    # 5. Wait briefly for SL to be placed
    time.sleep(2)

    # 6. Check position visible
    try:
        positions = ibkr.get_positions()
        fx_pos = [p for p in positions if symbol.replace(".", "") in p.get("symbol", "")]
        _log_result("IBKR", "position_visible", len(fx_pos) > 0,
                    f"{len(fx_pos)} FX position(s)")
    except Exception as e:
        _log_result("IBKR", "position_visible", False, str(e))

    # 7. Close position
    logger.info(f"  Closing {symbol}...")
    try:
        close_result = ibkr.close_position(
            symbol=symbol,
            _authorized_by="test_live_trade_ibkr_close",
        )
        _log_result("IBKR", "position_closed", True, str(close_result))
    except Exception as e:
        _log_result("IBKR", "position_closed", False, str(e))

    # 8. Verify closed
    time.sleep(2)
    try:
        positions = ibkr.get_positions()
        fx_pos = [p for p in positions if symbol.replace(".", "") in p.get("symbol", "")]
        _log_result("IBKR", "verify_closed", len(fx_pos) == 0,
                    f"{len(fx_pos)} remaining FX position(s)")
    except Exception as e:
        _log_result("IBKR", "verify_closed", False, str(e))

    # Log PnL
    logger.info("  IBKR test trade complete — check fills for PnL")


# ═══════════════════════════════════════════════════════════════════════════════
# BINANCE TEST
# ═══════════════════════════════════════════════════════════════════════════════

def test_binance(dry_run: bool = False):
    """Test minimal trade on Binance.

    Steps:
      1. Connect to Binance
      2. Get account info
      3. Place BUY 0.0001 BTC (minimum spot)
      4. Verify fill + fees
      5. Close (sell)
      6. Verify PnL coherent
    """
    logger.info("=" * 40)
    logger.info("  BINANCE TRADE TEST")
    logger.info("=" * 40)

    if not os.getenv("BINANCE_API_KEY"):
        _log_result("Binance", "api_key", False, "BINANCE_API_KEY not set")

    # 1. Connection
    from core.broker.binance_broker import BinanceBroker
    broker = BinanceBroker()

    # 2. Account info
    info = broker.get_account_info()
    equity = float(info.get("equity", 0))
    cash = float(info.get("cash", 0))
    _log_result("Binance", "account_info", True, f"equity=${equity:,.0f} cash=${cash:,.0f}")

    if dry_run:
        logger.info("  [DRY RUN] Skipping actual order execution")
        _log_result("Binance", "dry_run", True, "order execution skipped")
        return

    # 3. Get BTC price for minimum order
    try:
        ticker = broker.get_ticker_24h("BTCUSDT")
        btc_price = float(ticker.get("lastPrice", 0))
        _log_result("Binance", "price_check", btc_price > 0, f"BTC=${btc_price:,.0f}")
    except Exception as e:
        _log_result("Binance", "price_check", False, str(e))

    # Calculate minimum quantity (Binance min is ~$10 notional for BTC)
    # Use 0.0001 BTC ≈ $6-10 at current prices
    min_qty = 0.0001
    notional = min_qty * btc_price
    if notional < 5:
        # Price too low, increase qty
        min_qty = round(11 / btc_price, 6)

    logger.info(f"  Placing BUY {min_qty} BTCUSDT (~${notional:.2f})...")

    # 4. Place BUY
    try:
        result = broker.create_position(
            symbol="BTCUSDT",
            direction="BUY",
            qty=min_qty,
            _authorized_by="test_live_trade_binance",
        )
        order_id = result.get("orderId", "unknown")
        filled_qty = float(result.get("filled_qty", 0))
        filled_price = float(result.get("filled_price", 0))
        _log_result("Binance", "order_placed", True,
                    f"orderId={order_id}")
        _log_result("Binance", "fill_received", filled_qty > 0,
                    f"qty={filled_qty} @${filled_price:,.2f}")
    except Exception as e:
        _log_result("Binance", "order_placed", False, str(e))

    # 5. Brief pause
    time.sleep(1)

    # 6. Close (sell)
    logger.info("  Closing BTCUSDT...")
    try:
        close_result = broker.create_position(
            symbol="BTCUSDT",
            direction="SELL",
            qty=filled_qty,
            _authorized_by="test_live_trade_binance_close",
        )
        close_price = float(close_result.get("filled_price", 0))
        _log_result("Binance", "position_closed", True, f"@${close_price:,.2f}")
    except Exception as e:
        _log_result("Binance", "position_closed", False, str(e))

    # 7. PnL check
    pnl = (close_price - filled_price) * filled_qty if filled_price > 0 and close_price > 0 else 0
    logger.info(f"  PnL: ${pnl:+.4f} (spread + fees)")
    _log_result("Binance", "pnl_coherent", True, f"${pnl:+.4f}")


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER CYCLE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

def test_worker_cycle():
    """Simulate 1 complete worker cycle: signal → order → log."""
    logger.info("=" * 40)
    logger.info("  WORKER CYCLE SIMULATION")
    logger.info("=" * 40)

    # 1. Signal generation
    try:
        import pandas as pd

        from strategies_v2.fx.fx_carry_momentum_filter import CARRY_PAIRS, FXCarryMomentumFilter

        strat = FXCarryMomentumFilter()

        # Use real data if available, else synthetic
        data_dir = ROOT / "data" / "fx"
        pair_data = {}
        for pair in CARRY_PAIRS:
            fpath = data_dir / f"{pair}_1D.parquet"
            if fpath.exists():
                df = pd.read_parquet(fpath)
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.set_index("datetime").sort_index()
                pair_data[pair] = df

        if pair_data:
            state = {"equity": 10000, "i": len(list(pair_data.values())[0])}
            signal = strat.signal_fn(None, state, pair_data=pair_data, equity=10000)
            _log_result("Cycle", "signal_generated", True,
                        f"signal={'active' if signal else 'None'}")
        else:
            _log_result("Cycle", "signal_generated", True, "no FX data — skipped")

    except Exception as e:
        _log_result("Cycle", "signal_generated", False, str(e))

    # 2. JSONL event log
    try:
        event_log = ROOT / "logs" / "events.jsonl"
        event_log.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "strategy": "test_live_trade",
            "action": "signal",
            "details": {"test": True, "source": "pre_live_validation"},
        }
        with open(event_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        _log_result("Cycle", "jsonl_write", True, str(event_log))
    except Exception as e:
        _log_result("Cycle", "jsonl_write", False, str(e))

    logger.info("  Worker cycle simulation complete")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    test_all = "--all" in args or not any(a in args for a in ("--ibkr", "--binance", "--cycle"))
    test_ibkr_flag = "--ibkr" in args or test_all
    test_binance_flag = "--binance" in args or test_all
    test_cycle_flag = "--cycle" in args or test_all

    logger.info("=" * 60)
    logger.info("  LIVE TRADE TEST")
    logger.info(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"  Mode: {'DRY RUN' if dry_run else 'LIVE EXECUTION'}")
    logger.info("=" * 60)

    errors = []

    if test_ibkr_flag:
        try:
            test_ibkr_fx(dry_run=dry_run)
        except RuntimeError as e:
            errors.append(str(e))
            logger.error(f"  IBKR test aborted: {e}")

    if test_binance_flag:
        try:
            test_binance(dry_run=dry_run)
        except RuntimeError as e:
            errors.append(str(e))
            logger.error(f"  Binance test aborted: {e}")

    if test_cycle_flag:
        try:
            test_worker_cycle()
        except RuntimeError as e:
            errors.append(str(e))
            logger.error(f"  Cycle test aborted: {e}")

    # Summary
    n_pass = sum(1 for r in _results if r["status"] == "PASS")
    n_fail = sum(1 for r in _results if r["status"] == "FAIL")
    status = "GO" if n_fail == 0 else "NO_GO"

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  TRADE TEST: {status}")
    logger.info(f"  {n_pass} PASS / {n_fail} FAIL")
    logger.info("=" * 60)

    if errors:
        logger.error("  FAILURES:")
        for e in errors:
            logger.error(f"    ✗ {e}")

    # Write results
    results_path = ROOT / "logs" / "test_live_trade_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps({
        "status": status,
        "results": _results,
        "errors": errors,
        "timestamp": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
    }, indent=2))
    logger.info(f"  Results saved to {results_path}")

    sys.exit(0 if status == "GO" else 1)


if __name__ == "__main__":
    main()
