"""Decision Marc 2026-04-27: fermer MCLZ6 +1 sur U25023333 (option C).

Position nue (bracket disparu sans explication fiable), kill switch actif:
on retourne flat avant tout audit / re-bracket / re-WF.

Sequence:
  1. Connect IBKR live 4002 (canonical U25023333)
  2. Verifier position MCLZ6 +1 toujours presente
  3. Place SELL Market 1 contract MCLZ6 tif=DAY outsideRth=True
     (TIF DAY explicite cohérent fix 72b742c P0 Error 10349)
  4. Wait fill, log status + price + pnl
  5. Verify position now qty=0
  6. Update state file local pour reflecter flat
  7. Garde kill switch actif (pas de reset ici)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ib_insync import IB, Future, MarketOrder

ROOT = Path("/opt/trading-platform")
STATE_LIVE = ROOT / "data" / "state" / "futures_positions_live.json"

ACCOUNT = "U25023333"
SYMBOL = "MCL"
LOCAL_SYMBOL = "MCLZ6"


def main() -> int:
    ib = IB()
    try:
        ib.connect("127.0.0.1", 4002, clientId=210, timeout=15)
    except Exception as e:
        print(f"FAIL connect IBKR live 4002: {e}")
        return 1

    print(f"Connected. Managed accounts: {ib.managedAccounts()}")
    if ACCOUNT not in ib.managedAccounts():
        print(f"FAIL: {ACCOUNT} not in managed accounts {ib.managedAccounts()}")
        ib.disconnect()
        return 1

    ib.sleep(2)

    # 1. Verify position
    target_pos = None
    for p in ib.positions(ACCOUNT):
        if p.contract.symbol == SYMBOL and p.contract.localSymbol == LOCAL_SYMBOL:
            target_pos = p
            print(
                f"Found target: {p.contract.symbol} {p.contract.localSymbol} "
                f"qty={p.position} avgCost={p.avgCost}"
            )
            break

    if target_pos is None or target_pos.position == 0:
        print(f"NOTHING TO CLOSE: position {LOCAL_SYMBOL} not found or qty=0")
        ib.disconnect()
        return 0

    if target_pos.position < 0:
        print(f"UNEXPECTED: position is short ({target_pos.position}). Aborting.")
        ib.disconnect()
        return 1

    qty = int(target_pos.position)
    print(f"\nClosing {qty} {LOCAL_SYMBOL} via SELL Market DAY outsideRth ...")

    # Build qualified contract (NYMEX explicit, fix P0 1217acf)
    contract = Future(symbol=SYMBOL, exchange="NYMEX", currency="USD",
                       localSymbol=LOCAL_SYMBOL)
    details = ib.reqContractDetails(contract)
    if not details:
        print(f"FAIL: no contract details for {LOCAL_SYMBOL}")
        ib.disconnect()
        return 1
    contract = details[0].contract

    # 2. Place SELL Market
    order = MarketOrder("SELL", qty)
    order.tif = "DAY"          # P0 fix 72b742c (Error 10349)
    order.outsideRth = True    # MCL trade ~24h CME Globex, allow off-RTH
    order.account = ACCOUNT    # explicit account routing

    trade = ib.placeOrder(contract, order)
    print(f"Order placed: orderId={trade.order.orderId}")
    ib.sleep(8)  # wait for fill

    status = trade.orderStatus.status
    fill_price = trade.orderStatus.avgFillPrice
    print(f"\nStatus: {status}, avgFillPrice: {fill_price}")
    if status != "Filled":
        # Wait a bit more
        ib.sleep(5)
        status = trade.orderStatus.status
        fill_price = trade.orderStatus.avgFillPrice

    print(f"\nFinal: status={status} fill={fill_price}")
    for f in trade.fills:
        e = f.execution
        print(
            f"  FILL: time={e.time} acct={e.acctNumber} side={e.side} "
            f"qty={e.shares} price={e.price} permId={e.permId}"
        )

    # 3. Verify position now flat
    ib.sleep(2)
    print(f"\n=== Verify positions post-close on {ACCOUNT} ===")
    pos_count = 0
    for p in ib.positions(ACCOUNT):
        if p.position != 0:
            print(
                f"  STILL OPEN: {p.contract.symbol} {p.contract.localSymbol} "
                f"qty={p.position}"
            )
            pos_count += 1
    if pos_count == 0:
        print(f"  ✓ Account {ACCOUNT} is FLAT on all symbols.")

    # 4. Reset state file local to {} (already empty but make explicit)
    if STATE_LIVE.exists():
        try:
            current_state = json.loads(STATE_LIVE.read_text(encoding="utf-8"))
            print(f"\nState file before: {current_state}")
        except Exception as e:
            current_state = {}
            print(f"\nState file unreadable: {e}")
        STATE_LIVE.write_text("{}", encoding="utf-8")
        print(f"State file overwritten to {{}}")

    # 5. Log close event for audit trail
    close_log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": "Marc 2026-04-27 option C — fermer position nue MCLZ6",
        "account": ACCOUNT,
        "action": "SELL_MARKET_CLOSE",
        "symbol": LOCAL_SYMBOL,
        "qty_closed": qty,
        "fill_price": fill_price,
        "status": status,
        "context": (
            "Position nue (bracket SL/TP disparu entre 07:08 UTC et 17:30 UTC), "
            "kill switch live armé depuis 24/04 14:49 UTC. Fermeture preventive."
        ),
    }
    log_path = ROOT / "reports" / "checkup" / "mcl_close_audit_2026_04_27.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(close_log, indent=2, default=str), encoding="utf-8")
    print(f"\nAudit log: {log_path}")

    ib.disconnect()
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
