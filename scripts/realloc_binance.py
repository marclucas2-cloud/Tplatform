"""
Binance Reallocation Script — Reduce portfolio from ~$23.6K to $10K target.

Steps:
  1. CHECK   — Display current balances (earn, spot, fiat)
  2. REDEEM  — Redeem BTC + USDC from Earn Flexible
  3. SELL    — Sell excess BTC -> USDC (BTCUSDC pair)
  4. CONVERT — Convert surplus USDC -> EUR (via Binance Convert)
  5. SUMMARY — Print manual SEPA withdrawal instructions

Usage:
  python scripts/realloc_binance.py                  # DRY RUN (default)
  python scripts/realloc_binance.py --execute         # EXECUTE for real
  python scripts/realloc_binance.py --target 10000    # Custom target (default 10K)
"""
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.broker.binance_broker import BinanceBroker, BrokerError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────
TARGET_KEEP_USD = 10_000      # Keep $10K on Binance
MIN_USDC_BUFFER = 500         # Keep $500 USDC liquid after realloc
BTC_STEP_SIZE = 0.00001       # BTCUSDC lot step
USDC_EUR_RATE = 0.92          # Approximate USDC -> EUR rate (1 USDC ~ 0.92 EUR)


def floor_qty(qty: float, step: float) -> float:
    """Floor quantity to Binance lot step size."""
    return int(qty / step) * step


def get_full_state(broker: BinanceBroker) -> dict:
    """Get comprehensive account state: spot + earn + fiat."""
    # Spot balances
    account = broker._get("/api/v3/account", signed=True, weight=10)
    spot = {}
    for b in account.get("balances", []):
        free = float(b["free"])
        locked = float(b["locked"])
        total = free + locked
        if total > 0:
            spot[b["asset"]] = {"free": free, "locked": locked, "total": total}

    # Earn positions
    earn = []
    try:
        resp = broker._get("/sapi/v1/simple-earn/flexible/position", signed=True, weight=10)
        for r in resp.get("rows", []):
            amt = float(r.get("totalAmount", 0))
            if amt > 0:
                earn.append({
                    "asset": r.get("asset", ""),
                    "amount": amt,
                    "product_id": r.get("productId", ""),
                    "apy": float(r.get("latestAnnualPercentageRate", 0)),
                    "rewards": float(r.get("totalRewards", 0)),
                })
    except BrokerError as e:
        logger.warning(f"Earn positions error: {e}")

    # Prices
    prices = {}
    for symbol in ["BTCUSDC", "ETHUSDC", "BTCUSDT", "EURUSDT"]:
        try:
            t = broker._get("/api/v3/ticker/price", {"symbol": symbol})
            prices[symbol] = float(t["price"])
        except BrokerError:
            pass

    return {"spot": spot, "earn": earn, "prices": prices}


def display_state(state: dict):
    """Pretty print current portfolio state."""
    prices = state["prices"]
    btc_price = prices.get("BTCUSDC", prices.get("BTCUSDT", 0))
    eur_usd = prices.get("EURUSDT", 1.08)

    print("\n" + "=" * 70)
    print("  BINANCE PORTFOLIO — ETAT ACTUEL")
    print("=" * 70)

    # Spot
    print("\n  SPOT WALLET:")
    spot_total_usd = 0
    for asset, bal in sorted(state["spot"].items()):
        if bal["total"] < 0.0001 and asset not in ("USDC", "USDT", "EUR", "BTC", "ETH", "BNB"):
            continue
        usd_val = 0
        if asset in ("USDC", "USDT", "BUSD"):
            usd_val = bal["total"]
        elif asset == "EUR":
            usd_val = bal["total"] * eur_usd
        elif asset == "BTC":
            usd_val = bal["total"] * btc_price
        else:
            try:
                sym = asset + "USDT"
                if sym in prices:
                    usd_val = bal["total"] * prices[sym]
            except Exception:
                pass
        spot_total_usd += usd_val
        if usd_val > 1 or asset in ("BTC", "USDC", "EUR"):
            print(f"    {asset:6s}  {bal['total']:>14.6f}  (~${usd_val:,.0f})")

    print(f"    {'':6s}  {'TOTAL':>14s}   ${spot_total_usd:,.0f}")

    # Earn
    print("\n  EARN FLEXIBLE:")
    earn_total_usd = 0
    for e in state["earn"]:
        usd_val = 0
        if e["asset"] in ("USDC", "USDT"):
            usd_val = e["amount"]
        elif e["asset"] == "BTC":
            usd_val = e["amount"] * btc_price
        else:
            pass  # skip small amounts
        earn_total_usd += usd_val
        if usd_val > 1:
            print(f"    {e['asset']:6s}  {e['amount']:>14.6f}  (~${usd_val:,.0f})  APY={e['apy']*100:.1f}%  pid={e['product_id']}")
    print(f"    {'':6s}  {'TOTAL':>14s}   ${earn_total_usd:,.0f}")

    total = spot_total_usd + earn_total_usd
    print(f"\n  PORTFOLIO TOTAL:  ${total:,.0f}")
    print(f"  BTC price:        ${btc_price:,.0f}")
    print(f"  EUR/USD:          {eur_usd:.4f}")
    print("=" * 70)

    return {"spot_total": spot_total_usd, "earn_total": earn_total_usd, "total": total, "btc_price": btc_price, "eur_usd": eur_usd}


def compute_realloc(state: dict, totals: dict, target: float) -> dict:
    """Compute the reallocation plan."""
    btc_price = totals["btc_price"]
    surplus = totals["total"] - target

    if surplus <= 0:
        print(f"\n  Pas de surplus: total ${totals['total']:,.0f} <= target ${target:,.0f}")
        return {"surplus": 0, "steps": []}

    # Find BTC in earn
    btc_earn = 0
    btc_product_id = None
    usdc_earn = 0
    usdc_product_id = None
    for e in state["earn"]:
        if e["asset"] == "BTC":
            btc_earn = e["amount"]
            btc_product_id = e["product_id"]
        elif e["asset"] == "USDC":
            usdc_earn = e["amount"]
            usdc_product_id = e["product_id"]

    # BTC in spot
    btc_spot = state["spot"].get("BTC", {}).get("free", 0)
    btc_total = btc_earn + btc_spot
    btc_value = btc_total * btc_price

    # USDC in spot
    usdc_spot = state["spot"].get("USDC", {}).get("free", 0)
    usdc_total = usdc_spot + usdc_earn

    # EUR in spot
    eur_spot = state["spot"].get("EUR", {}).get("free", 0)
    eur_value = eur_spot * totals["eur_usd"]

    print("\n  PLAN DE REALLOCATION:")
    print(f"    Total actuel:     ${totals['total']:,.0f}")
    print(f"    Target garder:    ${target:,.0f}")
    print(f"    Surplus a retirer: ${surplus:,.0f}")
    print(f"    EUR deja dispo:   {eur_spot:,.0f} EUR (~${eur_value:,.0f})")
    print(f"    BTC total:        {btc_total:.6f} BTC (~${btc_value:,.0f})")
    print(f"    USDC total:       ${usdc_total:,.0f}")

    steps = []

    # Step 1: Redeem BTC from Earn
    if btc_earn > 0 and btc_product_id:
        steps.append({
            "action": "REDEEM_EARN",
            "asset": "BTC",
            "amount": btc_earn,
            "product_id": btc_product_id,
            "usd_value": btc_earn * btc_price,
            "desc": f"Redeem {btc_earn:.6f} BTC from Earn (~${btc_earn * btc_price:,.0f})",
        })

    # Step 2: Redeem USDC from Earn
    if usdc_earn > 0 and usdc_product_id:
        steps.append({
            "action": "REDEEM_EARN",
            "asset": "USDC",
            "amount": usdc_earn,
            "product_id": usdc_product_id,
            "usd_value": usdc_earn,
            "desc": f"Redeem {usdc_earn:,.0f} USDC from Earn",
        })

    # Step 3: Calculate BTC to sell
    # After redeem, all BTC is in spot. We want to keep some for strategies.
    # Target: keep ~$3K BTC for directional strategies, rest of $10K in USDC
    btc_keep_usd = 3000  # Keep $3K BTC for STRAT-001, 004, 005 etc.
    btc_keep_qty = btc_keep_usd / btc_price
    btc_to_sell = btc_total - btc_keep_qty

    if btc_to_sell > 0:
        btc_to_sell = floor_qty(btc_to_sell, BTC_STEP_SIZE)
        sell_value = btc_to_sell * btc_price
        steps.append({
            "action": "SELL_BTC",
            "symbol": "BTCUSDC",
            "qty": btc_to_sell,
            "usd_value": sell_value,
            "desc": f"Sell {btc_to_sell:.5f} BTC -> USDC (~${sell_value:,.0f})",
        })

    # Step 4: After selling BTC, compute USDC available
    usdc_after_sell = usdc_total + (btc_to_sell * btc_price if btc_to_sell > 0 else 0)
    # We need to keep: $10K - $3K BTC - EUR already on account
    usdc_to_keep = target - btc_keep_usd - eur_value
    usdc_to_keep = max(usdc_to_keep, MIN_USDC_BUFFER)
    usdc_to_convert = usdc_after_sell - usdc_to_keep

    if usdc_to_convert > 100:
        eur_to_get = usdc_to_convert * USDC_EUR_RATE
        steps.append({
            "action": "CONVERT_EUR",
            "from_asset": "USDC",
            "to_asset": "EUR",
            "amount_usdc": usdc_to_convert,
            "expected_eur": eur_to_get,
            "desc": f"Convert {usdc_to_convert:,.0f} USDC -> ~{eur_to_get:,.0f} EUR",
        })

    # Step 5: Re-subscribe remaining USDC to Earn
    usdc_earn_target = usdc_to_keep - MIN_USDC_BUFFER
    if usdc_earn_target > 100 and usdc_product_id:
        steps.append({
            "action": "SUBSCRIBE_EARN",
            "asset": "USDC",
            "amount": usdc_earn_target,
            "product_id": usdc_product_id,
            "desc": f"Re-subscribe {usdc_earn_target:,.0f} USDC to Earn Flexible",
        })

    # Summary
    total_eur_withdraw = eur_spot + (usdc_to_convert * USDC_EUR_RATE if usdc_to_convert > 0 else 0)
    print("\n  RESULTAT ATTENDU:")
    print(f"    BTC garde:      {btc_keep_qty:.5f} BTC (~${btc_keep_usd:,.0f})")
    print(f"    USDC garde:     ~${usdc_to_keep:,.0f} (buffer + earn)")
    print(f"    EUR pour retrait: ~{total_eur_withdraw:,.0f} EUR")
    print(f"    Total Binance:  ~${target:,.0f}")

    for i, s in enumerate(steps):
        print(f"\n    Step {i+1}: {s['desc']}")

    return {"surplus": surplus, "steps": steps, "eur_withdraw": total_eur_withdraw}


def execute_step(broker: BinanceBroker, step: dict, dry_run: bool) -> bool:
    """Execute a single realloc step."""
    action = step["action"]
    desc = step["desc"]

    if dry_run:
        print(f"  [DRY RUN] {desc}")
        return True

    print(f"  [EXECUTE] {desc}")

    try:
        if action == "REDEEM_EARN":
            result = broker.redeem_earn(step["product_id"])
            logger.info(f"Redeemed {step['asset']}: {result}")
            time.sleep(5)  # Wait for Earn to process

        elif action == "SELL_BTC":
            result = broker.create_position(
                symbol=step["symbol"],
                direction="SELL",
                qty=step["qty"],
                _authorized_by="realloc_binance_script",
            )
            logger.info(f"Sold BTC: {result}")
            if result.get("status") != "FILLED":
                logger.warning(f"Order not fully filled: {result}")
                return False

        elif action == "CONVERT_EUR":
            # Binance Convert API (quote -> accept)
            # Step 1: Get quote
            quote_params = {
                "fromAsset": step["from_asset"],
                "toAsset": step["to_asset"],
                "fromAmount": str(int(step["amount_usdc"])),
            }
            quote = broker._post("/sapi/v1/convert/getQuote", quote_params)
            quote_id = quote.get("quoteId")
            if not quote_id:
                # Fallback: manual instruction
                logger.warning("Convert API not available. Do manually on Binance web.")
                print(f"  ⚠ Convert {step['amount_usdc']:,.0f} USDC -> EUR manuellement sur Binance")
                return True

            # Step 2: Accept quote
            logger.info(f"Convert quote: {quote}")
            accept = broker._post("/sapi/v1/convert/acceptQuote", {"quoteId": quote_id})
            logger.info(f"Convert accepted: {accept}")
            time.sleep(3)

        elif action == "SUBSCRIBE_EARN":
            result = broker.subscribe_earn(step["product_id"], step["amount"])
            logger.info(f"Subscribed to Earn: {result}")

        print(f"  [OK] {desc}")
        return True

    except BrokerError as e:
        logger.error(f"Step failed: {e}")
        print(f"  [FAIL] {desc} — {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        print(f"  [FAIL] {desc} — {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Binance Reallocation Script")
    parser.add_argument("--execute", action="store_true", help="Execute for real (default: dry run)")
    parser.add_argument("--target", type=float, default=TARGET_KEEP_USD, help=f"Target USD to keep (default: {TARGET_KEEP_USD})")
    args = parser.parse_args()

    dry_run = not args.execute
    target = args.target

    print("\n" + "=" * 70)
    print(f"  BINANCE REALLOCATION — {'DRY RUN' if dry_run else '🔴 EXECUTION REELLE'}")
    print(f"  Target: garder ${target:,.0f} sur Binance")
    print("  Surplus -> EUR pour virement SEPA")
    print("=" * 70)

    if not dry_run:
        print("\n  ⚠  MODE EXECUTION REELLE ⚠")
        print("  Les ordres seront passes pour de vrai.")
        confirm = input("  Continuer? (oui/non): ").strip().lower()
        if confirm != "oui":
            print("  Annule.")
            return

    # Init broker
    broker = BinanceBroker()
    info = broker.authenticate()
    if info.get("paper"):
        print("\n  ⚠  ATTENTION: Connecte au TESTNET, pas au LIVE!")
        if not dry_run:
            print("  Set BINANCE_TESTNET=false pour le live.")
            return

    # Step 1: Get current state
    print("\n  Lecture des balances...")
    state = get_full_state(broker)
    totals = display_state(state)

    # Step 2: Compute realloc plan
    plan = compute_realloc(state, totals, target)

    if plan["surplus"] <= 0:
        print("\n  Rien a faire.")
        return

    if not plan["steps"]:
        print("\n  Aucune etape calculee.")
        return

    # Step 3: Execute
    print(f"\n{'=' * 70}")
    print(f"  EXECUTION — {len(plan['steps'])} etapes")
    print(f"{'=' * 70}")

    results = []
    for i, step in enumerate(plan["steps"]):
        print(f"\n  --- Etape {i+1}/{len(plan['steps'])} ---")
        ok = execute_step(broker, step, dry_run)
        results.append({"step": step["desc"], "ok": ok})
        if not ok and not dry_run:
            print(f"\n  ARRET: etape {i+1} a echoue. Verifier manuellement.")
            break

    # Step 4: Final state (if executed)
    if not dry_run and all(r["ok"] for r in results):
        print("\n  Relecture des balances apres realloc...")
        time.sleep(5)
        state_after = get_full_state(broker)
        display_state(state_after)

    # Step 5: Manual withdrawal instructions
    eur_total = plan.get("eur_withdraw", 0)
    print(f"\n{'=' * 70}")
    print("  INSTRUCTIONS RETRAIT SEPA")
    print(f"{'=' * 70}")
    print("  1. Aller sur https://www.binance.com/fr/my/wallet/account/main/withdrawal/fiat/EUR")
    print(f"  2. Montant: ~{eur_total:,.0f} EUR")
    print("  3. Methode: SEPA (gratuit, 1-2 jours ouvres)")
    print("  4. Destination: compte bancaire enregistre")
    print("  5. Repartition prevue:")
    print("     - IBKR: +5,000 EUR (pour atteindre 15K)")
    print(f"     - Alpaca: 25,000 USD (~{25000 / totals['eur_usd']:,.0f} EUR)")
    print("     - Reserve cash: le reste")
    print(f"{'=' * 70}")

    # Log the realloc
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": "binance_realloc",
        "dry_run": dry_run,
        "target_usd": target,
        "portfolio_before": totals["total"],
        "surplus": plan["surplus"],
        "steps": len(plan["steps"]),
        "eur_withdraw": eur_total,
        "results": results,
    }
    log_path = Path(__file__).parent.parent / "data" / "realloc_log.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    print(f"\n  Log sauvegarde: {log_path}")


if __name__ == "__main__":
    main()
