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
]
