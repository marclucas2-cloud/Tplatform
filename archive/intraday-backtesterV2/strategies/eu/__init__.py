"""EU strategies package."""
from .eu_gap_open import EUGapOpenStrategy
from .eu_luxury_momentum import EULuxuryMomentumStrategy
from .eu_energy_brent_lag import EUEnergyBrentLagStrategy
from .eu_close_us_open import EUCloseUSOpenStrategy
from .eu_day_of_week import EUDayOfWeekStrategy
# ARCHIVED (dead code): from .eu_stoxx_spy_reversion import EUStoxxSPYReversionStrategy

EU_STRATEGIES = [
    EUGapOpenStrategy,
    EULuxuryMomentumStrategy,
    EUEnergyBrentLagStrategy,
    EUCloseUSOpenStrategy,
    EUDayOfWeekStrategy,
    # EUStoxxSPYReversionStrategy,  # ARCHIVED
]
