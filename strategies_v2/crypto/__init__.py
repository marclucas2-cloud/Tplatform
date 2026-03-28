"""Binance France crypto strategies for BacktesterV2."""

from strategies_v2.crypto.altcoin_rs import AltcoinRelativeStrength
from strategies_v2.crypto.borrow_carry import BorrowRateCarry
from strategies_v2.crypto.btc_dominance import BTCDominance
from strategies_v2.crypto.btc_eth_momentum import BTCETHDualMomentum
from strategies_v2.crypto.btc_mr import BTCMeanReversion
from strategies_v2.crypto.liquidation_momentum import LiquidationMomentum
from strategies_v2.crypto.vol_breakout import VolBreakout
from strategies_v2.crypto.weekend_gap import WeekendGap

__all__ = [
    "AltcoinRelativeStrength",
    "BorrowRateCarry",
    "BTCDominance",
    "BTCETHDualMomentum",
    "BTCMeanReversion",
    "LiquidationMomentum",
    "VolBreakout",
    "WeekendGap",
]
