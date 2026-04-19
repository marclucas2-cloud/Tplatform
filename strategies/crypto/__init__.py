# Crypto strategies — Binance France (Margin + Spot + Earn, NO futures perp)
#
# 2026-04-19 (Bucket A post-XXL): 11 strats demotees P0.2 18 avril ont ete
# archivees vers strategies/_archive/crypto/ (REJECTED ou NEEDS_RE_WF jamais
# traite). Voir strategies/_archive/README.md.
#
# Strategies actives restantes : seulement btc_dominance_v2 (DISABLED in
# whitelist mais code preserve pour potentielle reactivation post-fix).

import logging

logger = logging.getLogger(__name__)

# Registry of all Binance France crypto strategies
CRYPTO_STRATEGIES = {}

# STRAT-001 btc_eth_dual_momentum    — ARCHIVED (REJECTED Sharpe -6.08, P0.2 audit)
# STRAT-002 Altcoin RS               — DISABLED (WF: NO_DATA, no validation possible)
# STRAT-003 BTC Mean Reversion       — DISABLED (WF: REJECTED, Sharpe OOS=-0.07)
# STRAT-004 vol_breakout             — ARCHIVED (INSUFFICIENT_TRADES 0/5, P0.2 audit)
# STRAT-006 borrow_rate_carry        — ARCHIVED (beta cachee non clarifiee)
# STRAT-007 liquidation_momentum     — ARCHIVED (NEEDS_RE_WF jamais traite)
# STRAT-008 weekend_gap_reversal     — ARCHIVED (NEEDS_RE_WF jamais traite)
# STRAT-009 trend_short_v1           — ARCHIVED (preuves OOS fragmentees)
# STRAT-010 mr_scalp_v1              — ARCHIVED (preuves OOS fragmentees)
# STRAT-011 liquidation_spike_v1     — ARCHIVED (doublon partiel STRAT-007)
# STRAT-012 vol_expansion_bear       — ARCHIVED (NEEDS_RE_WF jamais traite)
# STRAT-014 range_bb_harvest         — ARCHIVED (NEEDS_RE_WF jamais traite)
# STRAT-015 bb_mr_short              — ARCHIVED (REJECTED Sharpe -12.45, P0.2 audit)

try:
    from strategies.crypto.btc_dominance_v2 import (
        STRATEGY_CONFIG as STRAT_005_CONFIG,
    )
    from strategies.crypto.btc_dominance_v2 import (
        signal_fn as strat_005_signal,
    )
    CRYPTO_STRATEGIES["STRAT-005"] = {"config": STRAT_005_CONFIG, "signal_fn": strat_005_signal}
except Exception as e:
    logger.warning(f"Failed to load STRAT-005 (btc_dominance_v2): {e}")

logger.info(f"Loaded {len(CRYPTO_STRATEGIES)} crypto strategies")

# Total allocation raw (may exceed 100% with new strategies)
_RAW_ALLOCATION = sum(
    s["config"]["allocation_pct"] for s in CRYPTO_STRATEGIES.values()
) if CRYPTO_STRATEGIES else 0

# P0 FIX 2026-04-16 (audit ChatGPT): la normalisation silencieuse rendait
# le sizing dependant des imports reussis/echoues -> non-deterministe.
# Au-dessus de 100%, on RENORMALISE EXPLICITEMENT mais avec WARNING ECLATANT
# pour forcer revue. En env strict (CRYPTO_ALLOC_FAIL_CLOSED=true), on RAISE
# pour interdire le boot.
import os as _os
if _RAW_ALLOCATION > 1.0 and CRYPTO_STRATEGIES:
    if _os.environ.get("CRYPTO_ALLOC_FAIL_CLOSED", "").lower() == "true":
        raise ValueError(
            f"FAIL-CLOSED: crypto allocation {_RAW_ALLOCATION*100:.0f}% > 100%. "
            f"Reduire allocation_pct dans les strats individuelles ou desactiver "
            f"strats. Boot refuse pour eviter sizing non-deterministe. "
            f"Strats actives: {list(CRYPTO_STRATEGIES.keys())}"
        )
    scale_factor = 1.0 / _RAW_ALLOCATION
    for strat_data in CRYPTO_STRATEGIES.values():
        strat_data["config"]["allocation_pct"] *= scale_factor
    logger.error(  # ERROR pas warning -> visible dans alertes
        f"!!! CRYPTO ALLOCATION DRIFT: {_RAW_ALLOCATION*100:.0f}% > 100%, "
        f"renormalise a 100% (scale factor {scale_factor:.3f}). "
        f"NON-DETERMINISTE selon imports, FIX REQUIS. "
        f"Strats actives: {list(CRYPTO_STRATEGIES.keys())}"
    )

TOTAL_ALLOCATION = sum(
    s["config"]["allocation_pct"] for s in CRYPTO_STRATEGIES.values()
) if CRYPTO_STRATEGIES else 0
