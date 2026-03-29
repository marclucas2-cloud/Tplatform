"""Debug script — test strategies.crypto import from dashboard context."""
import sys
sys.path.insert(0, "/opt/trading-platform")
sys.path.insert(0, "/opt/trading-platform/dashboard/api")
sys.path.insert(0, "/opt/trading-platform/intraday-backtesterV2")
import main
print("ROOT:", main.ROOT)
print("sys.path[:5]:", sys.path[:5])
try:
    from strategies.crypto import CRYPTO_STRATEGIES
    print("OK:", len(CRYPTO_STRATEGIES), "strats")
except Exception as e:
    print("FAIL:", e)
    import traceback
    traceback.print_exc()
