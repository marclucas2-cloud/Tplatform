# INT-B — Portfolio allocation optimizer

**Run** : 2026-04-16 06:26 UTC
**Universe** : 7 baseline strats + 5 VALIDATED candidates = 12 total
**Constraint** : max weight per strat = 30.0%

## Summary

| Allocation | Sharpe | MaxDD% | CAGR% | Calmar | Total PnL $ |
|---|---:|---:|---:|---:|---:|
| `inverse_volatility` | +4.14 | -1.2% | +2.56% | +2.133 | +4,603 |
| `risk_parity` | +3.39 | -1.5% | +2.65% | +1.767 | +4,813 |
| `sharpe_weighted` | +2.06 | -3.3% | +3.35% | +1.015 | +6,403 |
| `equal_weight` | +1.25 | -8.7% | +4.71% | +0.541 | +9,953 |
| `hrp_lite` | +0.90 | -8.9% | +3.83% | +0.430 | +7,570 |

## Best by Calmar : `inverse_volatility`

Weights:

| Strategy | Weight |
|---|---:|
| `basis_carry_always` | 42.0% |
| `basis_carry_funding_gt_5pct` | 42.0% |
| `pre_holiday_drift` | 4.8% |
| `gold_oil_rotation` | 1.7% |
| `btc_mean_reversion_rsi` | 1.5% |
| `cross_asset_momentum` | 1.5% |
| `long_mon_oc` | 1.5% |
| `long_wed_oc` | 1.3% |
| `btc_trend_sma50` | 1.1% |
| `gold_trend_mgc` | 1.0% |
| `eth_trend_sma50` | 0.8% |
| `crypto_dual_momentum` | 0.8% |

## Caveats

- Scoring historique utilise la somme simple des PnL, pas la composition
  multi-compte (chaque broker a son capital propre).
- Les weights optimises IS doivent etre stress-testes sur 2018/2022/2024 via INT-A.
- Les candidats `basis_carry_*` utilisent un funding proxy — a re-verifier
  avec funding reel avant deploiement.
