# Crypto strategies — Binance France (Margin + Spot + Earn, NO futures perp)

import logging

logger = logging.getLogger(__name__)

# Registry of all Binance France crypto strategies
CRYPTO_STRATEGIES = {}

try:
    from strategies.crypto.btc_eth_dual_momentum import (
        STRATEGY_CONFIG as STRAT_001_CONFIG,
        signal_fn as strat_001_signal,
    )
    CRYPTO_STRATEGIES["STRAT-001"] = {"config": STRAT_001_CONFIG, "signal_fn": strat_001_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-001 (btc_eth_dual_momentum): {e}")

try:
    from strategies.crypto.altcoin_relative_strength import (
        STRATEGY_CONFIG as STRAT_002_CONFIG,
        signal_fn as strat_002_signal,
    )
    CRYPTO_STRATEGIES["STRAT-002"] = {"config": STRAT_002_CONFIG, "signal_fn": strat_002_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-002 (altcoin_relative_strength): {e}")

try:
    from strategies.crypto.btc_mean_reversion import (
        STRATEGY_CONFIG as STRAT_003_CONFIG,
        signal_fn as strat_003_signal,
    )
    CRYPTO_STRATEGIES["STRAT-003"] = {"config": STRAT_003_CONFIG, "signal_fn": strat_003_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-003 (btc_mean_reversion): {e}")

try:
    from strategies.crypto.vol_breakout import (
        STRATEGY_CONFIG as STRAT_004_CONFIG,
        signal_fn as strat_004_signal,
    )
    CRYPTO_STRATEGIES["STRAT-004"] = {"config": STRAT_004_CONFIG, "signal_fn": strat_004_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-004 (vol_breakout): {e}")

try:
    from strategies.crypto.btc_dominance_v2 import (
        STRATEGY_CONFIG as STRAT_005_CONFIG,
        signal_fn as strat_005_signal,
    )
    CRYPTO_STRATEGIES["STRAT-005"] = {"config": STRAT_005_CONFIG, "signal_fn": strat_005_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-005 (btc_dominance_v2): {e}")

try:
    from strategies.crypto.borrow_rate_carry import (
        STRATEGY_CONFIG as STRAT_006_CONFIG,
        signal_fn as strat_006_signal,
    )
    CRYPTO_STRATEGIES["STRAT-006"] = {"config": STRAT_006_CONFIG, "signal_fn": strat_006_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-006 (borrow_rate_carry): {e}")

try:
    from strategies.crypto.liquidation_momentum import (
        STRATEGY_CONFIG as STRAT_007_CONFIG,
        signal_fn as strat_007_signal,
    )
    CRYPTO_STRATEGIES["STRAT-007"] = {"config": STRAT_007_CONFIG, "signal_fn": strat_007_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-007 (liquidation_momentum): {e}")

try:
    from strategies.crypto.weekend_gap import (
        STRATEGY_CONFIG as STRAT_008_CONFIG,
        signal_fn as strat_008_signal,
    )
    CRYPTO_STRATEGIES["STRAT-008"] = {"config": STRAT_008_CONFIG, "signal_fn": strat_008_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-008 (weekend_gap): {e}")

logger.info(f"Loaded {len(CRYPTO_STRATEGIES)}/8 crypto strategies")

# Total allocation: 20+15+12+10+10+13+10+10 = 100%
TOTAL_ALLOCATION = sum(
    s["config"]["allocation_pct"] for s in CRYPTO_STRATEGIES.values()
) if CRYPTO_STRATEGIES else 0
