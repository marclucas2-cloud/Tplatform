# T1-C — Crypto basis / funding carry

**Run date** : 2026-04-16 06:16 UTC
**Note importante** : funding rate **approxime** via proxy (BTC 60d momentum +
 base 8.7%/an). Historique funding API non telecharge. Session T1-C sera re-lancee
 avec funding reel avant toute decision PROMOTE_PAPER.

## Standalone stats

| Variant | Active days | Total PnL $ |
|---|---:|---:|
| `basis_carry_always` | 3021 | +3,268 |
| `basis_carry_bullish` | 1529 | +4,516 |
| `basis_carry_funding_gt_5pct` | 1727 | +4,607 |
| `basis_carry_funding_gt_10pct` | 1507 | +4,502 |

## Scorecards (marginal vs 7-strat baseline)

| Variant | Verdict | Score | dSharpe | dCAGR | dMaxDD | Corr |
|---|---|---:|---:|---:|---:|---:|
| `basis_carry_always` | **PROMOTE_PAPER** | +0.193 | +0.041 | +0.32% | -0.69pp | +0.10 |
| `basis_carry_funding_gt_5pct` | **PROMOTE_PAPER** | +0.192 | +0.058 | +0.45% | +0.94pp | +0.11 |
| `basis_carry_bullish` | **PROMOTE_PAPER** | +0.190 | +0.057 | +0.44% | +0.72pp | +0.10 |
| `basis_carry_funding_gt_10pct` | **PROMOTE_PAPER** | +0.189 | +0.057 | +0.44% | +0.71pp | +0.10 |

## Caveat data

Les resultats ci-dessus utilisent un funding **proxy** base sur le momentum BTC 60d
et la mediane historique 8.7%/an. Une session T1-C' devra :
1. Telecharger le funding historique reel via Binance API `/fapi/v1/fundingRate` BTCUSDT.
2. Recomputer chaque variant avec le funding reel.
3. Verifier correlation STRAT-006 `borrow_rate_carry` existant (doctrine doublon = DROP).
