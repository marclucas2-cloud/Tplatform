"""Options proxy strategies package."""
from .put_spread_weekly import PutSpreadWeeklyStrategy
from .earnings_iv_crush import EarningsIVCrushStrategy

OPTIONS_STRATEGIES = [
    PutSpreadWeeklyStrategy,
    EarningsIVCrushStrategy,
]
