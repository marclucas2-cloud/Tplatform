# INT-D - Crypto discovery batch validation

**Run** : 2026-04-18 15:50 UTC
**Scope** : validation of crypto sleeves meant to survive both bull and bear tapes

## Gates

- walk-forward: at least 3/5 OOS windows with positive OOS PnL and Sharpe > 0.2
- Monte Carlo: P(DD > 30%) < 30%
- bull/bear robustness: positive bull total PnL and non-negative bear total PnL

## Summary

| Candidate | Sharpe | MaxDD | WF OOS pass | MC P(DD>30%) | Bull PnL | Bear PnL | Overall |
|---|---:|---:|---|---:|---:|---:|---|
| `range_bb_harvest_rebuild` | +0.09 | -21.4% | 3/5 | 32.0% | $-242 | $+2,916 | **NEEDS_WORK** |
| `range_bb_harvest_bb30` | +0.21 | -16.8% | 2/5 | 22.1% | $-1,465 | $+4,297 | **NEEDS_WORK** |
| `crypto_ls_20_7_3` | +0.30 | -12.5% | 2/5 | 12.5% | $+2,252 | $-1,072 | **NEEDS_WORK** |
| `crypto_ls_20_7_2` | +0.18 | -15.2% | 2/5 | 9.6% | $+1,943 | $-1,328 | **NEEDS_WORK** |
| `alt_rel_strength_14_60_7` | +1.11 | -7.8% | 3/5 | 0.5% | $+3,591 | $+515 | **VALIDATED** |
| `alt_rel_strength_14_90_7` | +0.44 | -7.7% | 3/5 | 4.3% | $+1,335 | $+221 | **VALIDATED** |

## Details

### `range_bb_harvest_rebuild` - NEEDS_WORK

**Standalone** : Sharpe=+0.09, MaxDD=-21.4%, Total=$+572, days=1183

**Bull regime** : days=649, total=$-242, sharpe=-0.09, maxDD=-22.8%
**Bear regime** : days=474, total=$+2,916, sharpe=+1.19, maxDD=-11.0%

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 141 | 95 | -1.62 | +1.67 | -3.7% | +667 | yes |
| 2 | 141 | 95 | +1.89 | +1.37 | -2.1% | +339 | yes |
| 3 | 141 | 95 | -0.21 | -0.89 | -9.2% | -394 | no |
| 4 | 141 | 95 | -1.57 | -0.71 | -4.4% | -303 | no |
| 5 | 141 | 95 | -0.62 | +1.76 | -2.5% | +607 | yes |

**Monte Carlo**

- Median DD : -23.3%
- P(DD > 20%) : 60.9%
- P(DD > 30%) : 32.0%
- Median final PnL : $+759

### `range_bb_harvest_bb30` - NEEDS_WORK

**Standalone** : Sharpe=+0.21, MaxDD=-16.8%, Total=$+1,237, days=1183

**Bull regime** : days=649, total=$-1,465, sharpe=-0.56, maxDD=-27.4%
**Bear regime** : days=474, total=$+4,297, sharpe=+1.86, maxDD=-6.8%

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 141 | 95 | -1.15 | +2.66 | -2.6% | +842 | yes |
| 2 | 141 | 95 | +2.53 | +1.21 | -2.6% | +341 | yes |
| 3 | 141 | 95 | +0.91 | -2.23 | -9.7% | -826 | no |
| 4 | 141 | 95 | -1.26 | +0.01 | -4.3% | +3 | no |
| 5 | 141 | 95 | +0.62 | -0.75 | -6.3% | -396 | no |

**Monte Carlo**

- Median DD : -20.0%
- P(DD > 20%) : 50.3%
- P(DD > 30%) : 22.1%
- Median final PnL : $+1,456

### `crypto_ls_20_7_3` - NEEDS_WORK

**Standalone** : Sharpe=+0.30, MaxDD=-12.5%, Total=$+1,180, days=818

**Bull regime** : days=649, total=$+2,252, sharpe=+0.78, maxDD=-6.5%
**Bear regime** : days=474, total=$-1,072, sharpe=-0.62, maxDD=-15.3%

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 97 | 66 | +1.71 | -0.11 | -5.5% | -32 | no |
| 2 | 97 | 66 | +1.18 | -2.54 | -9.9% | -860 | no |
| 3 | 97 | 66 | -1.68 | +2.69 | -3.7% | +1,070 | yes |
| 4 | 97 | 66 | +1.77 | +2.10 | -2.7% | +594 | yes |
| 5 | 97 | 66 | +1.44 | -3.58 | -10.7% | -964 | no |

**Monte Carlo**

- Median DD : -17.4%
- P(DD > 20%) : 37.2%
- P(DD > 30%) : 12.5%
- Median final PnL : $+1,165

### `crypto_ls_20_7_2` - NEEDS_WORK

**Standalone** : Sharpe=+0.18, MaxDD=-15.2%, Total=$+615, days=818

**Bull regime** : days=649, total=$+1,943, sharpe=+0.80, maxDD=-6.4%
**Bear regime** : days=474, total=$-1,328, sharpe=-0.89, maxDD=-18.5%

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 97 | 66 | +2.08 | -0.40 | -4.7% | -88 | no |
| 2 | 97 | 66 | +1.20 | -1.78 | -9.8% | -565 | no |
| 3 | 97 | 66 | -1.04 | +3.45 | -2.4% | +1,141 | yes |
| 4 | 97 | 66 | +2.02 | +1.50 | -2.0% | +363 | yes |
| 5 | 97 | 66 | +0.54 | -4.60 | -10.2% | -980 | no |

**Monte Carlo**

- Median DD : -15.7%
- P(DD > 20%) : 31.9%
- P(DD > 30%) : 9.6%
- Median final PnL : $+656

### `alt_rel_strength_14_60_7` - VALIDATED

**Standalone** : Sharpe=+1.11, MaxDD=-7.8%, Total=$+4,105, days=818

**Bull regime** : days=649, total=$+3,591, sharpe=+1.42, maxDD=-4.8%
**Bear regime** : days=474, total=$+515, sharpe=+0.29, maxDD=-10.1%

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 97 | 66 | +1.12 | +0.95 | -4.4% | +309 | yes |
| 2 | 97 | 66 | +0.59 | -0.48 | -5.3% | -160 | no |
| 3 | 97 | 66 | -0.13 | +4.22 | -2.8% | +1,488 | yes |
| 4 | 97 | 66 | +3.15 | +3.68 | -2.3% | +1,067 | yes |
| 5 | 97 | 66 | +2.57 | -2.01 | -10.6% | -686 | no |

**Monte Carlo**

- Median DD : -10.1%
- P(DD > 20%) : 4.9%
- P(DD > 30%) : 0.5%
- Median final PnL : $+4,243

### `alt_rel_strength_14_90_7` - VALIDATED

**Standalone** : Sharpe=+0.44, MaxDD=-7.7%, Total=$+1,556, days=818

**Bull regime** : days=649, total=$+1,335, sharpe=+0.58, maxDD=-6.9%
**Bear regime** : days=474, total=$+221, sharpe=+0.12, maxDD=-7.6%

**Walk-forward**

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 97 | 66 | -0.44 | +1.50 | -3.3% | +422 | yes |
| 2 | 97 | 66 | +1.67 | -1.46 | -7.4% | -570 | no |
| 3 | 97 | 66 | -0.55 | +1.41 | -3.7% | +516 | yes |
| 4 | 97 | 66 | +1.62 | +1.85 | -3.6% | +572 | yes |
| 5 | 97 | 66 | +1.54 | -0.41 | -7.7% | -126 | no |

**Monte Carlo**

- Median DD : -14.0%
- P(DD > 20%) : 22.6%
- P(DD > 30%) : 4.3%
- Median final PnL : $+1,502
