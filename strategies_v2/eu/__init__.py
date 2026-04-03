"""EU index strategies for BacktesterV2.

7 strategies across DAX, CAC40, and Euro Stoxx 50:
  - EUGapOpen: Gap fade at EU open (ESTX50)
  - EUMeanReversionDAX: ATR extension mean reversion (DAX)
  - EUMeanReversionCAC: ATR extension mean reversion (CAC40)
  - EUMeanReversionSX5E: ATR extension mean reversion (ESTX50)
  - EUORBFrankfurt: Opening range breakout (DAX)
  - EUORBParis: Opening range breakout (CAC40)
  - EUCrossAssetLeadLag: DAX leads ESTX50 during volatile moves
"""

from strategies_v2.eu.eu_bce_press_conference import EUBCEPressConference
from strategies_v2.eu.eu_cross_asset_lead_lag import EUCrossAssetLeadLag
from strategies_v2.eu.eu_ftse_mean_reversion import EUFTSEMeanReversion
from strategies_v2.eu.eu_gap_open import EUGapOpen
from strategies_v2.eu.eu_mean_reversion_cac import EUMeanReversionCAC
from strategies_v2.eu.eu_mean_reversion_dax import EUMeanReversionDAX
from strategies_v2.eu.eu_mean_reversion_sx5e import EUMeanReversionSX5E
from strategies_v2.eu.eu_orb_frankfurt import EUORBFrankfurt
from strategies_v2.eu.eu_orb_paris import EUORBParis
from strategies_v2.eu.eu_sector_rotation import EUSectorRotation

__all__ = [
    "EUBCEPressConference",
    "EUCrossAssetLeadLag",
    "EUFTSEMeanReversion",
    "EUGapOpen",
    "EUMeanReversionCAC",
    "EUMeanReversionDAX",
    "EUMeanReversionSX5E",
    "EUORBFrankfurt",
    "EUORBParis",
    "EUSectorRotation",
]
