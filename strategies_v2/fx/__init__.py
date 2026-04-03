"""FX strategies for BacktesterV2.

15 strategies across G10 currency pairs:
  - Carry (vol-scaled, momentum filter, G10 diversified)
  - Trend (EURUSD, GBPUSD)
  - Mean-reversion (EURGBP, hourly)
  - Session-based (Asian breakout, London fix, session overlap, EOM flow)
  - Momentum breakout (Donchian channel)
  - Bollinger squeeze
"""

from strategies_v2.fx.fx_asian_range_breakout import FXAsianRangeBreakout
from strategies_v2.fx.fx_carry_g10_diversified import FXCarryG10Diversified
from strategies_v2.fx.fx_carry_momentum_filter import FXCarryMomentumFilter
from strategies_v2.fx.fx_carry_vol_scaled import FXCarryVolScaled
from strategies_v2.fx.fx_mean_reversion_hourly import FXMeanReversionHourly
from strategies_v2.fx.fx_momentum_breakout import FXMomentumBreakout

__all__ = [
    "FXAsianRangeBreakout",
    "FXCarryG10Diversified",
    "FXCarryMomentumFilter",
    "FXCarryVolScaled",
    "FXMeanReversionHourly",
    "FXMomentumBreakout",
]
