from .orb_5min import ORB5MinStrategy
from .vwap_bounce import VWAPBounceStrategy
from .gap_fade import GapFadeStrategy
from .correlation_breakdown import CorrelationBreakdownStrategy
from .power_hour import PowerHourStrategy
from .mean_reversion import MeanReversionStrategy
from .fomc_cpi_drift import FOMCDriftStrategy
from .opex_gamma_pin import OpExGammaPinStrategy
from .tick_imbalance import TickImbalanceStrategy
from .dark_pool_blocks import DarkPoolBlockStrategy
from .ml_volume_cluster import VolumeProfileClusterStrategy
from .cross_asset_lead_lag import CrossAssetLeadLagStrategy
from .pattern_recognition import PatternRecognitionStrategy
from .earnings_drift import EarningsDriftStrategy
# === Nouvelles stratégies (batch 2) ===
from .initial_balance_extension import InitialBalanceExtensionStrategy
from .volume_climax_reversal import VolumeClimaxReversalStrategy
from .sector_rotation_momentum import SectorRotationMomentumStrategy
from .etf_nav_premium import ETFNavPremiumStrategy
from .momentum_exhaustion import MomentumExhaustionStrategy
from .crypto_proxy_regime import CryptoProxyRegimeStrategy
from .moc_imbalance import MOCImbalanceStrategy
from .opening_drive import OpeningDriveStrategy
from .relative_strength_pairs import RelativeStrengthPairsStrategy
from .vwap_sd_reversal import VWAPSDReversalStrategy
from .day_of_week_seasonal import DayOfWeekSeasonalStrategy
from .multi_timeframe_trend import MultiTimeframeTrendStrategy
# === Nouvelles stratégies (batch 3 — validated) ===
from .overnight_gap_continuation import OvernightGapContinuationStrategy
from .late_day_mean_reversion import LateDayMeanReversionStrategy

ALL_STRATEGIES = [
    # === Classiques ===
    ORB5MinStrategy,
    VWAPBounceStrategy,
    GapFadeStrategy,
    CorrelationBreakdownStrategy,
    PowerHourStrategy,
    MeanReversionStrategy,
    # === Macro / Event-Driven ===
    FOMCDriftStrategy,
    OpExGammaPinStrategy,
    EarningsDriftStrategy,
    # === Microstructure ===
    TickImbalanceStrategy,
    DarkPoolBlockStrategy,
    # === AI/ML ===
    VolumeProfileClusterStrategy,
    PatternRecognitionStrategy,
    # === Cross-Asset ===
    CrossAssetLeadLagStrategy,
    # === Initial Balance / Breakout ===
    InitialBalanceExtensionStrategy,
    OpeningDriveStrategy,
    # === Mean Reversion ===
    VolumeClimaxReversalStrategy,
    MomentumExhaustionStrategy,
    VWAPSDReversalStrategy,
    # === Sector / Pairs ===
    SectorRotationMomentumStrategy,
    ETFNavPremiumStrategy,
    RelativeStrengthPairsStrategy,
    CryptoProxyRegimeStrategy,
    # === Flow / Seasonal ===
    MOCImbalanceStrategy,
    DayOfWeekSeasonalStrategy,
    # === Multi-Timeframe ===
    MultiTimeframeTrendStrategy,
    # === Batch 3 — Validated Winners ===
    OvernightGapContinuationStrategy,
    LateDayMeanReversionStrategy,
]
