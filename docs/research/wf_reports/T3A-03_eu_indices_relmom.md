# T3-A3 - EU indices relative momentum

**Run** : 2026-04-18 07:14 UTC
**Data** : 2021-01-04 -> 2026-04-02 (1346 days)
**Universe** : DAX, CAC40, ESTX50, MIB
**Sizing** : $1,000 per leg, 0.10% RT cost proxy
**Execution note** : paper-only research proxy, not a live implementation spec

## Thesis

- country-index spreads in Europe can be traded as relative strength rather than outright direction
- the sleeve targets a missing regional relative-value slot without using production routing

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `eu_relmom_40_3` | 1306 | $+315 | +0.71 | -0.8% | **PROMOTE_PAPER** | +0.189 | +0.007 | -0.08pp | +0.00 |
| `eu_relmom_80_10_2v2` | 1266 | $+349 | +0.61 | -1.7% | **PROMOTE_PAPER** | +0.189 | +0.009 | +0.24pp | -0.03 |
| `eu_relmom_20_3` | 1326 | $+102 | +0.23 | -1.1% | **PROMOTE_PAPER** | +0.173 | +0.002 | -0.03pp | -0.01 |

## Best candidate

- `eu_relmom_40_3`
- Verdict : **PROMOTE_PAPER**
- Marginal score : +0.189
- Delta Sharpe : +0.007
- Delta MaxDD : -0.08pp
- Corr to portfolio : +0.003