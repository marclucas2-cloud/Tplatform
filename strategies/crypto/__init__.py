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

# STRAT-002 Altcoin RS — DISABLED (WF: NO_DATA, no validation possible)
# STRAT-003 BTC Mean Reversion — DISABLED (WF: REJECTED, Sharpe OOS=-0.07)

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

# BEAR regime strategies (V11 ROC optim)
try:
    from strategies.crypto.trend_short_v1 import (
        STRATEGY_CONFIG as STRAT_009_CONFIG,
        signal_fn as strat_009_signal,
    )
    CRYPTO_STRATEGIES["STRAT-009"] = {"config": STRAT_009_CONFIG, "signal_fn": strat_009_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-009 (trend_short_v1): {e}")

try:
    from strategies.crypto.mr_scalp_v1 import (
        STRATEGY_CONFIG as STRAT_010_CONFIG,
        signal_fn as strat_010_signal,
    )
    CRYPTO_STRATEGIES["STRAT-010"] = {"config": STRAT_010_CONFIG, "signal_fn": strat_010_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-010 (mr_scalp_v1): {e}")

try:
    from strategies.crypto.liquidation_spike_v1 import (
        STRATEGY_CONFIG as STRAT_011_CONFIG,
        signal_fn as strat_011_signal,
    )
    CRYPTO_STRATEGIES["STRAT-011"] = {"config": STRAT_011_CONFIG, "signal_fn": strat_011_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-011 (liquidation_spike_v1): {e}")

logger.info(f"Loaded {len(CRYPTO_STRATEGIES)} crypto strategies")

# Total allocation raw (may exceed 100% with new strategies)
_RAW_ALLOCATION = sum(
    s["config"]["allocation_pct"] for s in CRYPTO_STRATEGIES.values()
) if CRYPTO_STRATEGIES else 0

# Normalize to 100% if total exceeds — prevents overexposure
if _RAW_ALLOCATION > 1.0 and CRYPTO_STRATEGIES:
    scale_factor = 1.0 / _RAW_ALLOCATION
    for strat_data in CRYPTO_STRATEGIES.values():
        strat_data["config"]["allocation_pct"] *= scale_factor
    logger.warning(
        f"Crypto allocation normalized: {_RAW_ALLOCATION*100:.0f}% -> 100% "
        f"(scale factor {scale_factor:.3f})"
    )

TOTAL_ALLOCATION = sum(
    s["config"]["allocation_pct"] for s in CRYPTO_STRATEGIES.values()
) if CRYPTO_STRATEGIES else 0
