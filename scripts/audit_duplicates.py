"""Audit — check for duplicate orders from Railway+Hetzner double-worker period."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOTENV_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from core.alpaca_client.client import AlpacaClient
from core.broker.binance_broker import BinanceBroker

print("=" * 60)
print("  AUDIT DOUBLONS — Railway + Hetzner")
print("=" * 60)

# Binance
print("\n--- BINANCE ---")
bnb = BinanceBroker()
symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
           "ADAUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "MATICUSDT"]
total = 0
for sym in symbols:
    try:
        orders = bnb._get("/api/v3/allOrders", {"symbol": sym, "limit": 20}, signed=True, weight=10)
        filled = [o for o in orders if o.get("status") == "FILLED"]
        for o in filled[-5:]:
            total += 1
            ts = o["time"]
            from datetime import datetime
            dt = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {dt} {o['symbol']} {o['side']} qty={o['origQty']} price={o.get('price','mkt')} type={o['type']}")
    except Exception:
        pass

# Margin orders
for sym in ["BTCUSDT", "ETHUSDT"]:
    try:
        orders = bnb._get("/sapi/v1/margin/allOrders", {"symbol": sym, "limit": 20}, signed=True, weight=10)
        filled = [o for o in orders if o.get("status") == "FILLED"]
        for o in filled[-5:]:
            total += 1
            ts = o["time"]
            dt = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [MARGIN] {dt} {o['symbol']} {o['side']} qty={o['origQty']} status={o['status']}")
    except Exception:
        pass

if total == 0:
    print("  Aucun ordre filled trouve")

# Alpaca
print("\n--- ALPACA ---")
try:
    client = AlpacaClient.from_env()
    print(f"Mode: {'PAPER' if client._paper else 'LIVE'}")
    orders = client.list_orders(status="all", limit=20)
    if not orders:
        print("  Aucun ordre recent")
    else:
        for o in orders[:10]:
            print(f"  {o.get('created_at','')[:19]} {o.get('symbol')} {o.get('side')} qty={o.get('qty')} status={o.get('status')}")

    positions = client.get_positions()
    print(f"\n  Positions ouvertes: {len(positions)}")
    for p in positions:
        print(f"  {p['symbol']} qty={p.get('qty')} pnl=${p.get('unrealized_pl',0)}")
except Exception as e:
    print(f"  Erreur: {e}")

# Worker logs
print("\n--- WORKER UPTIME ---")
import subprocess

r = subprocess.run(["systemctl", "show", "trading-worker", "-p", "ActiveEnterTimestamp"], capture_output=True, text=True)
print(f"  {r.stdout.strip()}")

print("\n" + "=" * 60)
print("  FIN AUDIT")
print("=" * 60)
