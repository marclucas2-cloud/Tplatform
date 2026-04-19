# T3-B1 - US sector long/short rotation

**Run** : 2026-04-18 07:54 UTC
**Universe** : 11 sectors, 1274 daily observations
**Sizing** : $1,000 per leg, 0.10% RT cost

## Thesis

- sector leadership rotates slower than single-stock noise
- a long/short sector sleeve is a cleaner US market-neutral candidate than raw single-name PEAD

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `us_sector_ls_20_5` | 1255 | $+679 | +0.50 | -4.4% | **PROMOTE_PAPER** | +0.188 | +0.017 | +1.02pp | +0.01 |
| `us_sector_ls_40_5` | 1235 | $+533 | +0.39 | -2.1% | **PROMOTE_PAPER** | +0.190 | +0.014 | +1.32pp | +0.02 |
| `us_sector_ls_40_10` | 1235 | $+341 | +0.25 | -4.2% | **PROMOTE_PAPER** | +0.183 | +0.009 | +2.01pp | +0.01 |

## Best candidate

- `us_sector_ls_40_5`
- Verdict : **PROMOTE_PAPER**
- Marginal score : +0.190
- Delta Sharpe : +0.014
- Delta MaxDD : +1.32pp
- Corr to portfolio : +0.020