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
# === V2 strategies (filtres assouplis) ===
from .initial_balance_extension_v2 import InitialBalanceExtensionV2Strategy
from .volume_climax_reversal_v2 import VolumeClimaxReversalV2Strategy
from .vwap_bounce_v2 import VWAPBounceV2Strategy
from .correlation_breakdown_v2 import CorrelationBreakdownV2Strategy

# === Phase 2 — P0 nouvelles strategies (mission nuit) ===
from .volatility_squeeze_breakout import VolatilitySqueezeBreakoutStrategy
from .rsi_divergence import RSIDivergenceStrategy
from .opening_volume_surge import OpeningVolumeSurgeStrategy
from .vwap_micro_reversion import VWAPMicroReversionStrategy
from .intraday_momentum_persistence import IntradayMomentumPersistenceStrategy

# === Phase 2 — P1 structural strategies (mission nuit) ===
from .range_compression_breakout import RangeCompressionBreakoutStrategy
from .volume_dry_up_reversal import VolumeDryUpReversalStrategy
from .midday_reversal import MiddayReversalStrategy
from .atr_breakout_filter import ATRBreakoutFilterStrategy
from .ema_crossover_5m import EMACrossover5MStrategy
from .high_of_day_breakout import HighOfDayBreakoutStrategy
from .gap_and_go_momentum import GapAndGoMomentumStrategy
from .afternoon_trend_follow import AfternoonTrendFollowStrategy

# === Phase 4 — P2 calendar/swing strategies ===
from .close_auction_imbalance import CloseAuctionImbalanceStrategy
from .first_hour_range_retest import FirstHourRangeRetestStrategy
from .sector_leader_follow import SectorLeaderFollowStrategy
from .vwap_trend_day import VWAPTrendDayStrategy
from .morning_star_reversal import MorningStarReversalStrategy
from .relative_volume_breakout import RelativeVolumeBreakoutStrategy
from .mean_reversion_rsi2 import MeanReversionRSI2Strategy
from .spread_compression_pairs import SpreadCompressionPairsStrategy
from .opening_gap_fill import OpeningGapFillStrategy
from .double_bottom_top import DoubleBottomTopStrategy
from .momentum_ignition import MomentumIgnitionStrategy

# === Phase 5 — P3 overnight/market-neutral strategies ===
from .mean_reversion_3sigma import MeanReversion3SigmaStrategy
from .volume_profile_poc import VolumeProfilePOCStrategy
from .macd_divergence import MACDDivergenceStrategy
from .hammer_engulfing import HammerEngulfingStrategy
from .range_bound_scalp import RangeBoundScalpStrategy
from .pre_market_volume_leader import PreMarketVolumeLeaderStrategy
from .triple_ema_pullback import TripleEMAPullbackStrategy
from .overnight_range_breakout import OvernightRangeBreakoutStrategy
from .tlt_spy_divergence import TLTSPYDivergenceStrategy
from .consecutive_bar_reversal import ConsecutiveBarReversalStrategy
from .intraday_mean_reversion_etf import IntradayMeanReversionETFStrategy

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
    # === Phase 2 — P0 strategies ===
    VolatilitySqueezeBreakoutStrategy,
    RSIDivergenceStrategy,
    OpeningVolumeSurgeStrategy,
    VWAPMicroReversionStrategy,
    IntradayMomentumPersistenceStrategy,
    # === Phase 3 — Batch 11 strategies ===
    MeanReversion3SigmaStrategy,
    VolumeProfilePOCStrategy,
    MACDDivergenceStrategy,
    HammerEngulfingStrategy,
    RangeBoundScalpStrategy,
    PreMarketVolumeLeaderStrategy,
    TripleEMAPullbackStrategy,
    OvernightRangeBreakoutStrategy,
    TLTSPYDivergenceStrategy,
    ConsecutiveBarReversalStrategy,
    IntradayMeanReversionETFStrategy,
]
