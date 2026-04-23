"""Verification rapide : MCL contract resolution via NYMEX fonctionne."""
from ib_insync import IB, Future
import sys

ib = IB()
try:
    ib.connect("127.0.0.1", 4002, clientId=99, timeout=15)
except Exception as e:
    print(f"FAIL connect IBKR: {e}")
    sys.exit(1)

print(f"Connected. Managed accounts: {ib.managedAccounts()}")
print("=" * 60)

for symbol, exchange in [
    ("MCL", "CME"),     # old (bug)
    ("MCL", "NYMEX"),   # new (fix)
    ("MGC", "CME"),     # old
    ("MGC", "COMEX"),   # new
    ("MES", "CME"),     # ok
    ("MNQ", "CME"),     # ok
]:
    try:
        fut = Future(symbol=symbol, exchange=exchange, currency="USD")
        details = ib.reqContractDetails(fut)
        if details:
            c = details[0].contract
            print(f"  {symbol} @ {exchange}: OK - conId={c.conId} localSymbol={c.localSymbol} expiry={c.lastTradeDateOrContractMonth}")
        else:
            print(f"  {symbol} @ {exchange}: FAIL - no contract details")
    except Exception as e:
        print(f"  {symbol} @ {exchange}: EXCEPTION - {e}")

ib.disconnect()
