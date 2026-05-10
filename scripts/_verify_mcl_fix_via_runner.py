"""Verify the helper _make_future_contract resolves MCL correctly via IBKR."""
import sys
from ib_insync import IB
from core.worker.cycles.futures_runner import _make_future_contract, _FUTURES_EXCHANGE_MAP

print("_FUTURES_EXCHANGE_MAP:")
for sym, exch in sorted(_FUTURES_EXCHANGE_MAP.items()):
    print(f"  {sym}: {exch}")

ib = IB()
try:
    ib.connect("127.0.0.1", 4002, clientId=99, timeout=15)
except Exception as e:
    print(f"FAIL connect: {e}")
    sys.exit(1)

print(f"\nConnected. Testing contract resolution via _make_future_contract:")
print("=" * 60)
for symbol in ("MCL", "MGC", "MES", "MNQ"):
    fut = _make_future_contract(symbol)
    try:
        details = ib.reqContractDetails(fut)
        if details:
            c = details[0].contract
            print(f"  {symbol}: OK via {fut.exchange} - conId={c.conId} localSymbol={c.localSymbol} expiry={c.lastTradeDateOrContractMonth}")
        else:
            print(f"  {symbol}: FAIL via {fut.exchange} - no details")
    except Exception as e:
        print(f"  {symbol}: EXCEPTION - {e}")

ib.disconnect()
