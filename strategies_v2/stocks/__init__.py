"""Single stock strategies for BacktesterV2.

Alpha-pure strategies with low beta correlation:
  - EarningsDrift: PEAD (Post-Earnings Announcement Drift)
  - JPYPairsTrading: Japanese sector pairs (stat-arb)
"""

from strategies_v2.stocks.earnings_drift import EarningsDrift
from strategies_v2.stocks.pairs_trading_jpy import JPYPairsTrading

__all__ = [
    "EarningsDrift",
    "JPYPairsTrading",
]
