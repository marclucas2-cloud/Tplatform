"""P0 broker truth investigation MCL post-checkup 2026-04-27."""
from ib_insync import IB, ExecutionFilter
import time

ib = IB()
ib.connect("127.0.0.1", 4002, clientId=205, timeout=15)
print(f"managed_accounts: {ib.managedAccounts()}")
time.sleep(3)

print("\n=== POSITIONS canonical U25023333 ===")
for acc in ib.managedAccounts():
    for p in ib.positions(acc):
        if p.position != 0:
            print(f"  {acc}: {p.contract.symbol} {p.contract.localSymbol} qty={p.position} avgCost={p.avgCost}")

print("\n=== OPEN TRADES (orders en cours sur le broker) ===")
for t in ib.openTrades():
    o = t.order
    c = t.contract
    s = t.orderStatus
    acct = getattr(o, "account", None) or "(no acct)"
    qty = o.totalQuantity
    aux = getattr(o, "auxPrice", None)
    lmt = getattr(o, "lmtPrice", None)
    print(
        f"  orderId={o.orderId} permId={o.permId} acct={acct} "
        f"{c.symbol} {c.localSymbol} {o.action} {qty} type={type(o).__name__} "
        f"status={s.status} aux={aux} lmt={lmt} tif={getattr(o, 'tif', None)}"
    )

# Fills MCL via reqExecutions
print("\n=== FILLS MCL via reqExecutions (current session view) ===")
flt = ExecutionFilter(symbol="MCL")
fills = ib.reqExecutions(flt)
time.sleep(3)
for f in fills:
    e = f.execution
    cr = f.commissionReport
    print(
        f"  time={e.time} acct={e.acctNumber} side={e.side} qty={e.shares} "
        f"price={e.price} permId={e.permId} orderId={e.orderId} "
        f"comm={getattr(cr, 'commission', None)} realizedPNL={getattr(cr, 'realizedPNL', None)}"
    )

# Account summary for NAV/realized PnL
print("\n=== ACCOUNT SUMMARY ===")
for r in ib.accountSummary():
    if r.tag in ("NetLiquidation", "TotalCashValue", "RealizedPnL", "UnrealizedPnL", "GrossPositionValue"):
        print(f"  {r.tag}: {r.value} {r.currency}")

ib.disconnect()
