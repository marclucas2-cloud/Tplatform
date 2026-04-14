#!/usr/bin/env python3
"""Binance isolated margin cleanup — release idle USDC to spot.

Transfers free USDC from isolated margin pairs (ETHUSDC, SOLUSDC) back
to the spot wallet. Skips BTCUSDC if there's an active borrow (real
leveraged position managed by a strategy).

Usage:
  python scripts/margin_cleanup.py          # dry-run: report only
  python scripts/margin_cleanup.py --execute  # actually transfer
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from core.broker.binance_broker import BinanceBroker


# Minimum to keep in margin per pair (avoids closing the isolated pair)
MIN_MARGIN_BUFFER_USDC = 10.0


def get_margin_state(broker):
    info = broker._get("/sapi/v1/margin/isolated/account", signed=True, weight=10)
    pairs = []
    for a in info.get("assets", []):
        sym = a.get("symbol", "?")
        base = a.get("baseAsset", {})
        quote = a.get("quoteAsset", {})
        pairs.append({
            "symbol": sym,
            "base_net": float(base.get("netAsset", 0)),
            "base_borrowed": float(base.get("borrowed", 0)),
            "quote_free": float(quote.get("free", 0)),
            "quote_borrowed": float(quote.get("borrowed", 0)),
            "quote_net": float(quote.get("netAsset", 0)),
        })
    return pairs


def transfer_margin_to_spot(broker, symbol: str, asset: str, amount: float) -> dict:
    """Transfer asset from isolated margin pair to spot wallet."""
    params = {
        "asset": asset,
        "symbol": symbol,
        "transFrom": "ISOLATED_MARGIN",
        "transTo": "SPOT",
        "amount": str(amount),
    }
    return broker._post("/sapi/v1/margin/isolated/transfer", params, weight=4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually transfer")
    args = parser.parse_args()

    broker = BinanceBroker()
    pairs = get_margin_state(broker)

    total_recoverable = 0.0
    actions = []

    print("=== ISOLATED MARGIN STATE ===")
    for p in pairs:
        sym = p["symbol"]
        print(f"\n{sym}:")
        print(f"  base: net={p['base_net']:.6f}, borrowed={p['base_borrowed']:.6f}")
        print(f"  quote: free=${p['quote_free']:.2f}, borrowed=${p['quote_borrowed']:.2f}, net=${p['quote_net']:.2f}")

        # Skip if active borrow (real position)
        if p["base_borrowed"] > 0.00001 or p["quote_borrowed"] > 1:
            print(f"  → SKIP: active borrow, position managed by strategy")
            continue

        # Recoverable amount (keep buffer)
        recoverable = p["quote_free"] - MIN_MARGIN_BUFFER_USDC
        if recoverable <= 0:
            print(f"  → SKIP: nothing to recover (free={p['quote_free']}, buffer={MIN_MARGIN_BUFFER_USDC})")
            continue

        print(f"  → RECOVERABLE: ${recoverable:.2f} USDC")
        total_recoverable += recoverable
        actions.append({
            "symbol": sym,
            "asset": "USDC",
            "amount": round(recoverable, 2),
        })

    print()
    print(f"Total recoverable to spot: ${total_recoverable:.2f}")
    print()

    if not actions:
        print("Nothing to do.")
        return

    if not args.execute:
        print("DRY-RUN (use --execute to transfer):")
        for a in actions:
            print(f"  transfer {a['amount']} {a['asset']} from {a['symbol']} margin → spot")
        return

    print("EXECUTING transfers...")
    for a in actions:
        try:
            result = transfer_margin_to_spot(broker, a["symbol"], a["asset"], a["amount"])
            print(f"  OK: {a['amount']} {a['asset']} {a['symbol']} → spot (txid={result.get('tranId', '?')})")
        except Exception as e:
            print(f"  FAIL: {a['symbol']}: {e}")

    print()
    print("=== POST-EXECUTION STATE ===")
    for p in get_margin_state(broker):
        sym = p["symbol"]
        if p["quote_borrowed"] > 1:
            continue
        print(f"  {sym}: quote_free=${p['quote_free']:.2f}")


if __name__ == "__main__":
    main()
