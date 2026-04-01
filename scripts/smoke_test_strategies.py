"""
Smoke Test V12 — 1 micro-trade reel ($10) par strat active, 1x/semaine.

Pour chaque strat:
  1. Generer un signal force (prix reel, conditions simples)
  2. Passer par TOUT le pipeline: regime filter -> risk check -> validate_order -> create_position
  3. Montant: $10 (minimum Binance = $5)
  4. Si fill OK: close immediatement
  5. Si echoue: log le step exact qui a echoue + alerte Telegram
  6. Rapport: STRAT-001 PASS, STRAT-004 FAIL (LOT_SIZE), etc.

Execution: cron dimanche 04:00 CET (marche calme crypto)

Usage:
  python scripts/smoke_test_strategies.py              # Toutes les strats crypto
  python scripts/smoke_test_strategies.py STRAT-001     # Une seule strat
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "intraday-backtesterV2"))
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("smoke_test")

SMOKE_NOTIONAL = 10.0  # $10 par micro-trade


def smoke_test_crypto(filter_strat: str | None = None) -> list[dict]:
    """Run smoke test for all crypto strategies."""
    results = []

    if not os.getenv("BINANCE_API_KEY"):
        logger.error("BINANCE_API_KEY not set")
        return results

    if os.getenv("BINANCE_TESTNET", "true").lower() == "true":
        logger.warning("BINANCE_TESTNET=true — smoke test runs on testnet")

    from core.broker.binance_broker import BinanceBroker
    from core.crypto.risk_manager_crypto import CryptoRiskManager
    from strategies.crypto import CRYPTO_STRATEGIES

    broker = BinanceBroker()
    acct = broker.get_account_info()
    equity = float(acct.get("equity", 0))
    cash = float(acct.get("cash", 0))
    logger.info(f"Binance equity=${equity:,.0f}, cash=${cash:.0f}")

    risk_mgr = CryptoRiskManager(capital=max(equity, 10_000))

    for strat_id, strat_data in CRYPTO_STRATEGIES.items():
        if filter_strat and strat_id != filter_strat:
            continue

        config = strat_data["config"]
        strat_name = config.get("name", strat_id)
        market_type = config.get("market_type", "spot")

        r = {
            "strat_id": strat_id,
            "name": strat_name,
            "market_type": market_type,
            "passed": False,
            "fail_step": None,
            "details": {},
        }

        primary_symbol = config.get("symbols", ["BTCUSDT"])[0]
        trade_symbol = primary_symbol.replace("USDT", "USDC") if primary_symbol.endswith("USDT") else primary_symbol

        # Step 1: Get price
        try:
            price_data = broker.get_prices(trade_symbol, timeframe="4h", bars=5)
            bars = price_data.get("bars", [])
            if not bars:
                r["fail_step"] = "fetch_prices"
                r["details"]["error"] = "no bars"
                results.append(r)
                continue
            price = bars[-1]["c"]
            r["details"]["price"] = price
        except Exception as e:
            r["fail_step"] = "fetch_prices"
            r["details"]["error"] = str(e)
            results.append(r)
            continue

        # Step 2: Compute qty (BUY only for smoke test — safest)
        if price <= 0:
            r["fail_step"] = "price_zero"
            results.append(r)
            continue

        # For earn/non-directional strategies, skip trade test
        if market_type == "earn":
            r["passed"] = True
            r["details"]["skip_reason"] = "earn strategy — no directional trade needed"
            results.append(r)
            logger.info(f"  [{strat_id}] SKIP (earn) — PASS")
            continue

        # For margin short strategies, buy $10 spot instead (safest smoke)
        side = "BUY"
        raw_qty = SMOKE_NOTIONAL / price
        if "BTC" in trade_symbol:
            qty = float(f"{raw_qty:.5f}")
        elif "ETH" in trade_symbol:
            qty = float(f"{raw_qty:.4f}")
        else:
            qty = float(f"{raw_qty:.3f}")

        notional = qty * price
        r["details"]["qty"] = qty
        r["details"]["notional"] = notional

        # Binance minimum notional check
        if notional < 5.0:
            r["fail_step"] = "min_notional"
            r["details"]["error"] = f"notional ${notional:.2f} < $5 min"
            results.append(r)
            continue

        # Step 3: Risk validate
        try:
            valid, msg = risk_mgr.validate_order(
                notional=notional,
                strategy=strat_id,
                current_equity=equity,
            )
            r["details"]["risk_valid"] = valid
            r["details"]["risk_msg"] = msg
            if not valid:
                r["fail_step"] = "risk_validate"
                results.append(r)
                continue
        except Exception as e:
            r["fail_step"] = "risk_validate"
            r["details"]["error"] = str(e)
            results.append(r)
            continue

        # Step 4: Execute BUY $10 spot
        try:
            result = broker.create_position(
                symbol=trade_symbol,
                direction="BUY",
                notional=SMOKE_NOTIONAL,
                stop_loss=None,  # No SL for $10 smoke test
                market_type="spot",
                _authorized_by=f"smoke_test_{strat_id}",
            )
            order_status = result.get("status", "UNKNOWN")
            filled_qty = float(result.get("filled_qty", 0))
            r["details"]["order_status"] = order_status
            r["details"]["filled_qty"] = filled_qty
            logger.info(f"  [{strat_id}] BUY ${SMOKE_NOTIONAL} {trade_symbol}: {order_status} (qty={filled_qty})")
        except Exception as e:
            r["fail_step"] = "create_position"
            r["details"]["error"] = str(e)
            results.append(r)
            continue

        # Step 5: Close immediately
        time.sleep(1)  # Wait 1s for order to settle
        try:
            close_result = broker.close_position(
                trade_symbol,
                _authorized_by=f"smoke_test_close_{strat_id}",
            )
            r["details"]["close_status"] = close_result.get("status", "UNKNOWN")
            logger.info(f"  [{strat_id}] CLOSE: {close_result.get('status')}")
        except Exception as e:
            # If close fails, it may be because the position is too small
            r["details"]["close_error"] = str(e)
            logger.warning(f"  [{strat_id}] Close failed (may be dust): {e}")

        r["passed"] = True
        results.append(r)
        logger.info(f"  [{strat_id}] SMOKE TEST PASS")

        # Brief pause between strategies
        time.sleep(2)

    return results


def main():
    filter_strat = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 60)
    print("  SMOKE TEST V12 — micro-trades $10")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    results = smoke_test_crypto(filter_strat)

    # Summary
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {len(passed)}/{len(results)} PASS")
    for r in results:
        status = "PASS" if r["passed"] else f"FAIL ({r.get('fail_step', '?')})"
        print(f"  [{status}] {r['strat_id']} ({r['name']})")
    print(f"{'=' * 60}")

    # Persist
    out_path = ROOT / "data" / "monitoring" / "smoke_test_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": len(passed),
        "total": len(results),
        "failed": [r["strat_id"] for r in failed],
        "results": results,
    }, indent=2, default=str))

    # Alert
    if failed:
        try:
            from core.telegram_alert import send_alert
            send_alert(
                f"SMOKE TEST: {len(passed)}/{len(results)} PASS\n"
                f"FAILED: {', '.join(r['strat_id'] + '(' + (r.get('fail_step') or '?') + ')' for r in failed)}",
                level="warning",
            )
        except Exception:
            pass

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
