# INT-B - Discovery batch validation

**Run** : 2026-04-18 07:14 UTC
**Scope** : validation of the strongest T3-A research candidates only

## Gates

- walk-forward: at least 3/5 OOS windows with Sharpe > 0.2
- Monte Carlo: P(DD > 30%) < 30%

## Summary

| Candidate | Sharpe | MaxDD | WF OOS pass | MC P(DD>30%) | Overall |
|---|---:|---:|---|---:|---|
| `mcl_overnight_mon_trend10` | +0.80 | -4.3% | 4/5 | 0.0% | **VALIDATED** |
| `btc_asia_mes_leadlag_q70_v80` | +1.07 | -7.7% | 4/5 | 0.0% | **VALIDATED** |
| `eu_relmom_40_3` | +0.71 | -0.8% | 4/5 | 0.0% | **VALIDATED** |

## Details

### `mcl_overnight_mon_trend10` - VALIDATED

**Standalone** : Sharpe=+0.80, MaxDD=-4.3%, Total=$+4,751, days=2833

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | -0.92 | +0.43 | -1.8% | +67 | yes |
| 2 | 339 | 227 | -0.58 | +0.82 | -0.7% | +78 | yes |
| 3 | 339 | 227 | +0.14 | +0.87 | -0.8% | +116 | yes |
| 4 | 339 | 227 | +1.11 | -0.96 | -4.0% | -302 | no |
| 5 | 339 | 227 | +0.56 | +0.54 | -1.2% | +80 | yes |

**Monte Carlo**

- Median DD : -4.4%
- P(DD > 20%) : 0.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+4,714

### `btc_asia_mes_leadlag_q70_v80` - VALIDATED

**Standalone** : Sharpe=+1.07, MaxDD=-7.7%, Total=$+2,357, days=489

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 58 | 39 | -1.25 | +1.45 | -1.8% | +210 | yes |
| 2 | 58 | 39 | -0.29 | +2.40 | -1.2% | +550 | yes |
| 3 | 58 | 39 | +2.94 | +2.88 | -2.2% | +354 | yes |
| 4 | 58 | 39 | +4.20 | +1.63 | -2.9% | +501 | yes |
| 5 | 58 | 39 | +2.13 | -3.73 | -6.6% | -554 | no |

**Monte Carlo**

- Median DD : -7.8%
- P(DD > 20%) : 1.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+2,296

### `eu_relmom_40_3` - VALIDATED

**Standalone** : Sharpe=+0.71, MaxDD=-0.8%, Total=$+315, days=1346

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 161 | 108 | -1.04 | +0.82 | -0.2% | +22 | yes |
| 2 | 161 | 108 | +0.62 | -0.27 | -0.7% | -14 | no |
| 3 | 161 | 108 | -0.47 | +0.84 | -0.5% | +31 | yes |
| 4 | 161 | 108 | +0.28 | +0.38 | -0.4% | +13 | yes |
| 5 | 161 | 108 | +0.61 | +0.41 | -0.3% | +11 | yes |

**Monte Carlo**

- Median DD : -1.3%
- P(DD > 20%) : 0.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+315
