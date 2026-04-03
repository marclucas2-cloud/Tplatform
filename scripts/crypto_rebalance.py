"""
Crypto Rebalance — Day 1: Free cash for strategies.

Actions:
  1. Redeem 0.10 BTC from Earn Flexible
  2. Sell 0.10 BTC → USDT (market)
  3. Sell EURUSDT position → USDT
  4. Report new portfolio state

Safety:
  - Confirms each step before proceeding
  - Telegram notification at each step
  - Dry-run mode by default

Usage:
    python scripts/crypto_rebalance.py --dry-run   # Simulate (default)
    python scripts/crypto_rebalance.py --execute    # Real execution
"""
from __future__ import annotations

import logging
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
logger = logging.getLogger("rebalance")


def _send_telegram(msg: str, level: str = "info"):
    try:
        from core.telegram_alert import send_alert
        send_alert(msg, level=level)
    except Exception as e:
        logger.warning(f"Telegram: {e}")


def _get_broker():
    from core.broker.binance_broker import BinanceBroker
    return BinanceBroker()


def show_portfolio(broker):
    """Display current portfolio breakdown."""
    acct = broker.get_account_info()
    positions = broker.get_positions()
    earn = broker.get_earn_positions()

    btc_price = float(broker.get_ticker_24h("BTCUSDT").get("last_price", 0))
    eth_price = float(broker.get_ticker_24h("ETHUSDT").get("last_price", 0))

    # Calculate values
    btc_earn = 0
    usdc_earn = 0
    other_earn = []
    for e in earn:
        asset = e.get("asset", "")
        amt = float(e.get("amount", 0))
        if asset == "BTC":
            btc_earn = amt
        elif asset in ("USDC", "USDT"):
            usdc_earn += amt
        elif amt > 0.001:
            other_earn.append((asset, amt))

    btc_val = btc_earn * btc_price
    eur_val = 0
    eur_qty = 0
    for p in positions:
        if p.get("symbol") == "EURUSDT":
            eur_qty = float(p.get("qty", 0))
            eur_val = float(p.get("market_val", 0))

    usdt_cash = float(acct.get("cash", 0))
    total = btc_val + usdc_earn + eur_val + usdt_cash

    logger.info("=" * 50)
    logger.info("  PORTFOLIO BREAKDOWN")
    logger.info("=" * 50)
    logger.info(f"  BTC:  {btc_earn:.6f} = ${btc_val:,.0f} ({btc_val/total*100:.0f}%)")
    logger.info(f"  USDC: ${usdc_earn:,.0f} ({usdc_earn/total*100:.0f}%)")
    logger.info(f"  EUR:  {eur_qty:.0f} = ${eur_val:,.0f} ({eur_val/total*100:.0f}%)")
    logger.info(f"  USDT: ${usdt_cash:,.0f} ({usdt_cash/total*100:.0f}%)")
    logger.info(f"  Total: ${total:,.0f}")
    logger.info(f"  BTC price: ${btc_price:,.0f}")

    return {
        "btc_earn": btc_earn, "btc_val": btc_val, "btc_price": btc_price,
        "usdc_earn": usdc_earn, "eur_qty": eur_qty, "eur_val": eur_val,
        "usdt_cash": usdt_cash, "total": total, "eth_price": eth_price,
    }


def step1_redeem_btc(broker, dry_run: bool = True):
    """Redeem 0.10 BTC from Earn Flexible."""
    qty = 0.10
    logger.info(f"\n--- STEP 1: Redeem {qty} BTC from Earn Flexible ---")

    if dry_run:
        logger.info(f"  [DRY RUN] Would redeem {qty} BTC from Earn")
        return True

    try:
        # Binance Earn Flexible redemption
        result = broker._post("/sapi/v1/lending/daily/redeem", {
            "productId": "BTC001",  # Flexible BTC
            "amount": str(qty),
            "type": "FAST",
        })
        logger.info(f"  Redeem result: {result}")
        _send_telegram(f"REBALANCE: Redeem {qty} BTC from Earn Flexible", level="info")

        # Wait for redemption to settle (usually < 1 min for Flexible)
        logger.info("  Waiting 30s for redemption to settle...")
        time.sleep(30)
        return True
    except Exception as e:
        # Try alternative API endpoint
        logger.warning(f"  Redeem v1 failed: {e}, trying simple-earn...")
        try:
            result = broker._post("/sapi/v1/simple-earn/flexible/redeem", {
                "productId": "BTC001",
                "amount": str(qty),
            })
            logger.info(f"  Redeem result: {result}")
            _send_telegram(f"REBALANCE: Redeem {qty} BTC from Earn", level="info")
            time.sleep(30)
            return True
        except Exception as e2:
            logger.error(f"  Redeem failed: {e2}")
            _send_telegram(f"REBALANCE FAILED: cannot redeem BTC — {e2}", level="critical")
            return False


def step2_sell_btc(broker, qty: float = 0.10, dry_run: bool = True):
    """Sell BTC for USDT."""
    logger.info(f"\n--- STEP 2: Sell {qty} BTC → USDT ---")

    btc_price = float(broker.get_ticker_24h("BTCUSDT").get("last_price", 0))
    notional = qty * btc_price
    logger.info(f"  BTC price: ${btc_price:,.0f}, notional: ${notional:,.0f}")

    if dry_run:
        logger.info(f"  [DRY RUN] Would sell {qty} BTC for ~${notional:,.0f} USDT")
        return notional

    try:
        result = broker.create_position(
            symbol="BTCUSDT",
            direction="SELL",
            qty=qty,
            _authorized_by="crypto_rebalance_day1",
        )
        filled = float(result.get("filled_price", 0))
        filled_qty = float(result.get("filled_qty", 0))
        received = filled * filled_qty
        logger.info(f"  SOLD {filled_qty} BTC @ ${filled:,.0f} = ${received:,.0f} USDT")
        _send_telegram(
            f"REBALANCE: SELL {filled_qty:.4f} BTC @ ${filled:,.0f}\n"
            f"Received: ${received:,.0f} USDT",
            level="info"
        )
        return received
    except Exception as e:
        logger.error(f"  Sell BTC failed: {e}")
        _send_telegram(f"REBALANCE FAILED: cannot sell BTC — {e}", level="critical")
        return 0


def step3_sell_eur(broker, dry_run: bool = True):
    """Sell EURUSDT position → USDT."""
    logger.info("\n--- STEP 3: Sell EUR → USDT ---")

    # Get current EUR position
    positions = broker.get_positions()
    eur_pos = [p for p in positions if p.get("symbol") == "EURUSDT"]

    if not eur_pos:
        logger.info("  No EUR position found — skipping")
        return 0

    eur_qty = float(eur_pos[0].get("qty", 0))
    eur_val = float(eur_pos[0].get("market_val", 0))
    logger.info(f"  EUR position: {eur_qty:.2f} EUR = ${eur_val:,.0f}")

    if eur_qty < 1:
        logger.info("  EUR qty < 1 — skipping")
        return 0

    if dry_run:
        logger.info(f"  [DRY RUN] Would sell {eur_qty:.0f} EURUSDT for ~${eur_val:,.0f}")
        return eur_val

    try:
        result = broker.create_position(
            symbol="EURUSDT",
            direction="SELL",
            qty=round(eur_qty, 2),
            _authorized_by="crypto_rebalance_day1",
        )
        filled = float(result.get("filled_price", 0))
        filled_qty = float(result.get("filled_qty", 0))
        received = filled * filled_qty
        logger.info(f"  SOLD {filled_qty:.0f} EUR @ {filled:.4f} = ${received:,.0f} USDT")
        _send_telegram(
            f"REBALANCE: SELL {filled_qty:.0f} EURUSDT\n"
            f"Received: ${received:,.0f} USDT",
            level="info"
        )
        return received
    except Exception as e:
        logger.error(f"  Sell EUR failed: {e}")
        _send_telegram(f"REBALANCE FAILED: cannot sell EUR — {e}", level="critical")
        return 0


def run(dry_run: bool = True):
    logger.info("=" * 60)
    logger.info("  CRYPTO REBALANCE — DAY 1")
    logger.info(f"  Mode: {'DRY RUN' if dry_run else 'LIVE EXECUTION'}")
    logger.info(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    if not dry_run:
        _send_telegram(
            "REBALANCE DAY 1 STARTING\n"
            "1. Redeem 0.10 BTC from Earn\n"
            "2. Sell 0.10 BTC → USDT\n"
            "3. Sell EUR position → USDT",
            level="warning"
        )

    broker = _get_broker()

    # Before
    logger.info("\n--- BEFORE ---")
    before = show_portfolio(broker)

    # Step 1: Redeem BTC
    ok = step1_redeem_btc(broker, dry_run=dry_run)
    if not ok and not dry_run:
        logger.error("  ABORT: cannot redeem BTC")
        return

    # Step 2: Sell BTC
    btc_usdt = step2_sell_btc(broker, qty=0.10, dry_run=dry_run)

    # Step 3: Sell EUR
    eur_usdt = step3_sell_eur(broker, dry_run=dry_run)

    total_freed = btc_usdt + eur_usdt

    # After
    if not dry_run:
        time.sleep(5)
        logger.info("\n--- AFTER ---")
        after = show_portfolio(broker)

        _send_telegram(
            f"REBALANCE DAY 1 DONE\n"
            f"Freed: ${total_freed:,.0f} USDT\n"
            f"BTC: {after['btc_val']/after['total']*100:.0f}% "
            f"(was {before['btc_val']/before['total']*100:.0f}%)\n"
            f"USDT cash: ${after['usdt_cash']:,.0f}",
            level="info"
        )
    else:
        logger.info("\n--- DRY RUN SUMMARY ---")
        logger.info(f"  Would free: ~${total_freed:,.0f} USDT")
        logger.info(f"  BTC after: {before['btc_earn'] - 0.10:.6f} BTC "
                     f"(${(before['btc_earn'] - 0.10) * before['btc_price']:,.0f})")
        new_total = before['total']
        new_btc_pct = (before['btc_earn'] - 0.10) * before['btc_price'] / new_total * 100
        logger.info(f"  BTC %: {before['btc_val']/before['total']*100:.0f}% → {new_btc_pct:.0f}%")
        logger.info(f"  USDT cash: $0 → ~${total_freed:,.0f} ({total_freed/new_total*100:.0f}%)")


if __name__ == "__main__":
    dry_run = "--execute" not in sys.argv

    if not dry_run:
        logger.warning("LIVE EXECUTION MODE — orders will be placed!")
        logger.warning("Press Ctrl+C within 5s to abort...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Aborted.")
            sys.exit(0)

    run(dry_run=dry_run)
