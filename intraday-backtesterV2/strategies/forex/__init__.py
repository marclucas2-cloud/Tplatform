"""Forex strategies package."""
from .audjpy_carry import AUDJPYCarryStrategy
from .gbpusd_trend import GBPUSDTrendStrategy
from .usdchf_mr import USDCHFMeanReversionStrategy

FOREX_STRATEGIES = [
    AUDJPYCarryStrategy,
    GBPUSDTrendStrategy,
    USDCHFMeanReversionStrategy,
]
