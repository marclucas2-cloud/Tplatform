"""Debug script — show all Binance wallet balances."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()
from core.broker.binance_broker import BinanceBroker

bnb = BinanceBroker()
info = bnb.get_account_info()
print(f"SPOT total USD: ${info['spot_total_usd']:,.2f}")
print(f"SPOT USDT: ${info['spot_usdt']:,.2f}")
print(f"Equity (spot only): ${info['equity']:,.2f}")
print()

# Spot details
account = bnb._get("/api/v3/account", signed=True, weight=10)
print("--- Spot assets ---")
for b in account.get("balances", []):
    total = float(b["free"]) + float(b["locked"])
    if total > 0.001:
        print(f"  {b['asset']}: {total:.8f}")
print()

# Margin
print("--- Margin ---")
try:
    margin = bnb._get("/sapi/v1/margin/account", signed=True, weight=10)
    print(f"  totalAssetOfBtc: {margin.get('totalAssetOfBtc')}")
    print(f"  totalLiabilityOfBtc: {margin.get('totalLiabilityOfBtc')}")
    print(f"  totalNetAssetOfBtc: {margin.get('totalNetAssetOfBtc')}")
    for a in margin.get("userAssets", []):
        net = float(a.get("netAsset", 0))
        if abs(net) > 0.001:
            print(f"  {a['asset']}: net={net}, free={a['free']}, borrowed={a['borrowed']}")
except Exception as e:
    print(f"  Error: {e}")
print()

# Earn
print("--- Earn ---")
try:
    resp = bnb._get("/sapi/v1/simple-earn/flexible/position", signed=True, weight=10)
    rows = resp.get("rows", []) if isinstance(resp, dict) else []
    for r in rows:
        amt = float(r.get("totalAmount", 0))
        if amt > 0:
            print(f"  FLEX: {r['asset']} = {amt}")
except Exception as e:
    print(f"  Flexible: {e}")
try:
    resp = bnb._get("/sapi/v1/simple-earn/locked/position", signed=True, weight=10)
    rows = resp.get("rows", []) if isinstance(resp, dict) else []
    for r in rows:
        amt = float(r.get("totalAmount", 0))
        if amt > 0:
            print(f"  LOCKED: {r['asset']} = {amt}")
except Exception as e:
    print(f"  Locked: {e}")
print()

# BTC price for margin conversion
try:
    btc_ticker = bnb._get("/api/v3/ticker/price", {"symbol": "BTCUSDT"})
    btc_price = float(btc_ticker["price"])
    print(f"BTC price: ${btc_price:,.2f}")
    margin_data = bnb._get("/sapi/v1/margin/account", signed=True, weight=10)
    margin_net_btc = float(margin_data.get("totalNetAssetOfBtc", 0))
    margin_net_usd = margin_net_btc * btc_price
    print(f"Margin net USD: ${margin_net_usd:,.2f}")
    print(f"TOTAL (spot+margin): ${info['spot_total_usd'] + margin_net_usd:,.2f}")
except Exception as e:
    print(f"Conversion error: {e}")
