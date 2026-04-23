"""Cleanup DUP573894 paper ghost account after Error 10349 incident 2026-04-23.

Sequence:
  1. Connect IB Gateway live (port 4002, clientId different de worker)
  2. List open orders sur DUP573894 + positions
  3. Cancel bracket OCA MCL orders (permId 1225401853 SL + 1225401854 TP)
  4. Close MCL +1 position via MarketOrder SELL 1 tif=DAY
  5. Verify clean state
"""
from __future__ import annotations
import sys
from ib_insync import IB, Future, MarketOrder

HOST = "127.0.0.1"
PORT = 4003  # paper gateway (DUP573894 is paper ghost mirror)
CLIENT_ID = 88  # avoid worker clientIds (74 paper, 78 live)
TARGET_ACC = "DUP573894"
TARGET_SYMBOL = "MCL"
TARGET_LOCAL = "MCLZ6"


def main():
    ib = IB()
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    except Exception as e:
        print(f"FAIL connect: {e}")
        sys.exit(1)

    print(f"Connected. Managed accounts: {ib.managedAccounts()}")
    print("=" * 70)

    # --- 1. List open orders + positions ---
    print("\n[1/4] Open orders on DUP573894:")
    open_trades = ib.openTrades()
    target_trades = []
    for t in open_trades:
        contract = t.contract
        order = t.order
        acct = (
            getattr(order, "account", None)
            or (t.orderStatus.clientId and "")
        )
        if (
            contract.symbol == TARGET_SYMBOL
            and contract.localSymbol == TARGET_LOCAL
        ):
            target_trades.append(t)
            print(
                f"  orderId={order.orderId} permId={order.permId} "
                f"action={order.action} qty={order.totalQuantity} "
                f"type={type(order).__name__} status={t.orderStatus.status}"
            )

    print("\n[2/4] Positions on DUP573894:")
    target_pos = None
    for pos in ib.positions(TARGET_ACC):
        if pos.contract.symbol == TARGET_SYMBOL:
            target_pos = pos
            print(
                f"  {pos.contract.symbol} {pos.contract.localSymbol} "
                f"qty={pos.position} avgCost={pos.avgCost}"
            )

    if not target_trades and not target_pos:
        print("\nNothing to clean up. Exiting.")
        ib.disconnect()
        return

    # --- 3. Cancel bracket OCA orders ---
    print("\n[3/4] Cancelling bracket orders...")
    for t in target_trades:
        if t.orderStatus.status in ("Cancelled", "Filled", "Inactive"):
            print(f"  SKIP orderId={t.order.orderId} status={t.orderStatus.status}")
            continue
        try:
            ib.cancelOrder(t.order)
            print(f"  CANCEL orderId={t.order.orderId}")
        except Exception as e:
            print(f"  FAIL cancel orderId={t.order.orderId}: {e}")
    ib.sleep(2)

    # --- 4. Close position via SELL MarketOrder tif=DAY ---
    if target_pos and target_pos.position != 0:
        print(
            f"\n[4/4] Closing position MCL qty={target_pos.position} "
            f"via SELL Market DAY ..."
        )
        side = "SELL" if target_pos.position > 0 else "BUY"
        qty = int(abs(target_pos.position))

        # Build a fresh qualified Future with explicit exchange (bug
        # Error 321 if we re-use target_pos.contract which lacks exchange).
        close_contract = Future(
            symbol=TARGET_SYMBOL,
            exchange="NYMEX",
            currency="USD",
            localSymbol=TARGET_LOCAL,
        )
        details = ib.reqContractDetails(close_contract)
        if not details:
            print(f"  FAIL: no contract details for {TARGET_LOCAL}")
            ib.disconnect()
            return
        close_contract = details[0].contract

        close_order = MarketOrder(side, qty)
        close_order.tif = "DAY"  # P0 FIX aligned
        close_order.outsideRth = True  # allow overnight futures fill
        close_order.account = TARGET_ACC  # explicit account routing

        try:
            trade = ib.placeOrder(close_contract, close_order)
            ib.sleep(5)
            print(f"  orderId={trade.order.orderId} status={trade.orderStatus.status}")
            print(f"  fillPrice={trade.orderStatus.avgFillPrice}")
        except Exception as e:
            print(f"  FAIL placeOrder: {e}")

    # --- Verify ---
    ib.sleep(2)
    print("\n[verify] Remaining open trades on MCL:")
    for t in ib.openTrades():
        if t.contract.symbol == TARGET_SYMBOL:
            print(
                f"  orderId={t.order.orderId} status={t.orderStatus.status}"
            )

    print("\n[verify] Positions DUP573894 post-cleanup:")
    for pos in ib.positions(TARGET_ACC):
        if pos.contract.symbol == TARGET_SYMBOL:
            print(
                f"  {pos.contract.symbol} qty={pos.position} "
                f"avgCost={pos.avgCost}"
            )
    else:
        # positions() iterator doesn't have else ; simple check
        mcl_remaining = [
            p for p in ib.positions(TARGET_ACC)
            if p.contract.symbol == TARGET_SYMBOL
        ]
        if not mcl_remaining:
            print("  (none)")

    ib.disconnect()
    print("\nDONE.")


if __name__ == "__main__":
    main()
