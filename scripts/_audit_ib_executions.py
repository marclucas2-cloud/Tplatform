"""Read-only IB audit via ib_insync async. No orders placed."""
import asyncio, os, sys
from datetime import datetime, timezone, timedelta

HOST = os.getenv("IBKR_HOST", "178.104.125.74")
PORT = int(os.getenv("IBKR_PORT", "4002"))
CLIENT_ID = 98

async def main():
    from ib_insync import IB, ExecutionFilter
    ib = IB()
    print(f"Connecting {HOST}:{PORT} clientId={CLIENT_ID} ...")
    try:
        await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    except Exception as e:
        print(f"CONNECTION FAILED: {type(e).__name__}: {e}")
        return

    print(f"Connected. Accounts: {ib.wrapper.accounts}")

    since = datetime.now(timezone.utc) - timedelta(days=7)
    fills = await ib.reqExecutionsAsync(ExecutionFilter())

    print(f"\n=== EXECUTIONS last 7d ===")
    found = 0
    for fill in fills:
        ex = fill.execution
        try:
            dt = datetime.strptime(ex.time.strip(), "%Y%m%d  %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt >= since:
            found += 1
            print(f"  {dt.date()} {dt.strftime('%H:%M')} | {ex.side:4} {ex.shares:>8} "
                  f"{fill.contract.symbol:<12} @ {ex.price:>10.4f} | acct={ex.acctNumber}")
    if not found:
        print(f"  0 executions in last 7 days (total returned by IB: {len(fills)})")

    positions = ib.positions()
    print(f"\n=== OPEN POSITIONS ({len(positions)}) ===")
    for p in positions:
        print(f"  {p.account} | {p.contract.symbol:<12} qty={p.position:>10.2f} avgCost={p.avgCost:.4f}")
    if not positions:
        print("  (none)")

    for item in ib.accountSummary():
        if item.tag in ("NetLiquidation", "TotalCashValue"):
            print(f"  {item.tag}: {item.value} {item.currency}")

    ib.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
