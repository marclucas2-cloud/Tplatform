"""Test IBKR account info direct."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from core.broker.ibkr_adapter import IBKRBroker

ibkr = IBKRBroker()
info = ibkr.get_account_info()
print("IBKR account info:")
for k, v in sorted(info.items()):
    print(f"  {k}: {v}")
