# INT-A — Walk-forward + Monte Carlo + stress (Tier 1 candidates)

**Run** : 2026-04-16 06:21 UTC
**Methode** : pour chaque candidate PROMOTE de T1-A..T1-C, WF 5 windows rolling 60/40,
MC 1000 sims bootstrap daily, stress 2018/2020/2022/2024/2025.

**Gates** :
- WF : >= 3/5 windows OOS Sharpe > 0.2
- MC : P(DD > 30%) < 30%

**Note** : T1-D (US PEAD) et T1-E (crypto L/S) depend de data externe non re-chargee,
WF/MC specifique a lancer dans une session ulterieure avec les series persistees.

## Summary table

| Candidate | Sharpe | MaxDD | WF OOS pass | MC P(DD>30%) | Overall |
|---|---:|---:|---|---:|---|
| `long_mon_oc` | +0.71 | -28.5% | 3/5 | 9.8% | **VALIDATED** |
| `long_wed_oc` | +0.44 | -19.7% | 4/5 | 28.3% | **VALIDATED** |
| `turn_of_month` | +0.25 | -26.1% | 3/5 | 57.1% | **NEEDS_WORK** |
| `pre_holiday_drift` | +0.57 | -4.6% | 5/5 | 0.0% | **VALIDATED** |
| `mes_fade_2.0atr` | +0.18 | -7.7% | 1/5 | 1.1% | **NEEDS_WORK** |
| `mes_fade_2.5atr` | +0.32 | -2.4% | 2/5 | 0.0% | **NEEDS_WORK** |
| `mes_fade_2atr_trend` | +0.02 | -7.7% | 1/5 | 1.8% | **NEEDS_WORK** |
| `basis_carry_always` | +8.20 | -2.1% | 4/5 | 0.0% | **VALIDATED** |
| `basis_carry_funding_gt_5pct` | +14.91 | -0.0% | 5/5 | 0.0% | **VALIDATED** |

## Details par candidate

### `long_mon_oc` — VALIDATED

**Standalone** : Sharpe=+0.71, MaxDD=-28.5%, Total=$+10,831, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | -0.22 | +0.40 | -2.1% | +137 | yes |
| 2 | 339 | 227 | +0.98 | -0.18 | -8.2% | -165 | no |
| 3 | 339 | 227 | +0.12 | -1.25 | -10.5% | -869 | no |
| 4 | 339 | 227 | -0.94 | +1.03 | -19.8% | +2,298 | yes |
| 5 | 339 | 227 | +0.86 | +0.76 | -6.2% | +810 | yes |

**Monte Carlo (1000 sims)** :
- Median DD : -17.0% | p10 DD : -29.8% | p90 DD : -10.5%
- P(DD > 20%) : 34.5%
- P(DD > 30%) : 9.8%
- Median final PnL : $+10,851 | p10 : $+5,277

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | -1,210 | -1.05 | -14.0% |
| 2020_covid | 41 | -450 | -0.52 | -20.6% |
| 2022_bear | 251 | -1,106 | -0.64 | -23.5% |
| 2024_rally | 252 | +946 | +0.80 | -6.0% |
| 2025_latest | 252 | +3,295 | +1.69 | -13.5% |

### `long_wed_oc` — VALIDATED

**Standalone** : Sharpe=+0.44, MaxDD=-19.7%, Total=$+7,507, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | +0.52 | +1.63 | -2.0% | +653 | yes |
| 2 | 339 | 227 | +1.04 | +1.01 | -2.5% | +446 | yes |
| 3 | 339 | 227 | +0.28 | +0.26 | -10.6% | +269 | yes |
| 4 | 339 | 227 | +0.26 | +0.79 | -12.5% | +1,148 | yes |
| 5 | 339 | 227 | +0.73 | -0.38 | -10.1% | -357 | no |

**Monte Carlo (1000 sims)** :
- Median DD : -22.3% | p10 DD : -42.3% | p90 DD : -13.2%
- P(DD > 20%) : 60.7%
- P(DD > 30%) : 28.3%
- Median final PnL : $+7,371 | p10 : $+1,099

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | +362 | +0.31 | -11.8% |
| 2020_covid | 41 | -707 | -1.39 | -13.1% |
| 2022_bear | 251 | +472 | +0.22 | -12.7% |
| 2024_rally | 252 | +309 | +0.17 | -8.8% |
| 2025_latest | 252 | +3,985 | +1.44 | -6.1% |

### `turn_of_month` — NEEDS_WORK

**Standalone** : Sharpe=+0.25, MaxDD=-26.1%, Total=$+4,782, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | -0.29 | +0.57 | -4.8% | +258 | yes |
| 2 | 339 | 227 | +0.50 | -1.04 | -18.3% | -1,120 | no |
| 3 | 339 | 227 | -0.74 | +1.63 | -5.7% | +1,504 | yes |
| 4 | 339 | 227 | +0.95 | +0.60 | -11.6% | +1,195 | yes |
| 5 | 339 | 227 | +0.58 | -0.25 | -7.7% | -328 | no |

**Monte Carlo (1000 sims)** :
- Median DD : -32.7% | p10 DD : -60.5% | p90 DD : -18.8%
- P(DD > 20%) : 87.1%
- P(DD > 30%) : 57.1%
- Median final PnL : $+4,664 | p10 : $-1,976

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | -1,210 | -0.91 | -19.0% |
| 2020_covid | 41 | -255 | -0.36 | -8.6% |
| 2022_bear | 251 | +2,209 | +0.87 | -10.0% |
| 2024_rally | 252 | -2,362 | -1.30 | -27.5% |
| 2025_latest | 252 | +75 | +0.04 | -15.7% |

### `pre_holiday_drift` — VALIDATED

**Standalone** : Sharpe=+0.57, MaxDD=-4.6%, Total=$+2,653, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | -0.25 | +1.28 | -0.5% | +123 | yes |
| 2 | 339 | 227 | +0.44 | +1.18 | -0.7% | +219 | yes |
| 3 | 339 | 227 | +0.14 | +0.73 | -3.3% | +301 | yes |
| 4 | 339 | 227 | +0.36 | +2.07 | -0.2% | +519 | yes |
| 5 | 339 | 227 | +0.87 | +1.13 | -1.6% | +386 | yes |

**Monte Carlo (1000 sims)** :
- Median DD : -6.5% | p10 DD : -10.9% | p90 DD : -3.9%
- P(DD > 20%) : 0.4%
- P(DD > 30%) : 0.0%
- Median final PnL : $+2,567 | p10 : $+885

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | -94 | -0.24 | -3.9% |
| 2020_covid | 41 | +206 | +2.48 | 0.0% |
| 2022_bear | 251 | +199 | +0.28 | -4.3% |
| 2024_rally | 252 | +487 | +1.01 | -1.5% |
| 2025_latest | 252 | +181 | +0.28 | -2.6% |

### `mes_fade_2.0atr` — NEEDS_WORK

**Standalone** : Sharpe=+0.18, MaxDD=-7.7%, Total=$+924, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | +0.86 | -0.01 | -1.7% | -2 | no |
| 2 | 339 | 227 | +0.07 | -0.41 | -7.7% | -341 | no |
| 3 | 339 | 227 | -0.32 | -1.05 | -1.8% | -184 | no |
| 4 | 339 | 227 | +0.12 | +1.23 | -0.4% | +412 | yes |
| 5 | 339 | 227 | +0.74 | +0.00 | 0.0% | +0 | no |

**Monte Carlo (1000 sims)** :
- Median DD : -9.2% | p10 DD : -19.2% | p90 DD : -3.8%
- P(DD > 20%) : 8.3%
- P(DD > 30%) : 1.1%
- Median final PnL : $+854 | p10 : $-1,027

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | -510 | -0.58 | -7.5% |
| 2020_covid | 41 | +297 | +2.19 | -0.4% |
| 2022_bear | 251 | -351 | -1.40 | -3.5% |
| 2024_rally | 252 | -258 | -1.10 | -2.6% |
| 2025_latest | 252 | +1,357 | +1.28 | -0.7% |

### `mes_fade_2.5atr` — NEEDS_WORK

**Standalone** : Sharpe=+0.32, MaxDD=-2.4%, Total=$+848, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | +0.00 | -0.01 | -1.7% | -2 | no |
| 2 | 339 | 227 | +0.07 | +1.07 | 0.0% | +432 | yes |
| 3 | 339 | 227 | +0.92 | -1.05 | -1.8% | -184 | no |
| 4 | 339 | 227 | +0.12 | +1.05 | 0.0% | +116 | yes |
| 5 | 339 | 227 | +0.29 | +0.00 | 0.0% | +0 | no |

**Monte Carlo (1000 sims)** :
- Median DD : -3.3% | p10 DD : -6.4% | p90 DD : -1.7%
- P(DD > 20%) : 0.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+819 | p10 : $-149

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | +242 | +0.52 | -1.8% |
| 2020_covid | 41 | +0 | +0.00 | 0.0% |
| 2022_bear | 251 | +0 | +0.00 | 0.0% |
| 2024_rally | 252 | -24 | -1.00 | -0.2% |
| 2025_latest | 252 | +491 | +1.00 | 0.0% |

### `mes_fade_2atr_trend` — NEEDS_WORK

**Standalone** : Sharpe=+0.02, MaxDD=-7.7%, Total=$+96, days=2834

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 339 | 227 | +0.86 | -0.01 | -1.7% | -2 | no |
| 2 | 339 | 227 | +0.07 | -0.41 | -7.7% | -341 | no |
| 3 | 339 | 227 | -0.32 | -1.05 | -1.8% | -184 | no |
| 4 | 339 | 227 | +0.12 | +0.93 | -0.4% | +297 | yes |
| 5 | 339 | 227 | +1.11 | +0.00 | 0.0% | +0 | no |

**Monte Carlo (1000 sims)** :
- Median DD : -9.6% | p10 DD : -19.6% | p90 DD : -3.7%
- P(DD > 20%) : 9.8%
- P(DD > 30%) : 1.8%
- Median final PnL : $+158 | p10 : $-1,385

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 251 | -510 | -0.58 | -7.5% |
| 2020_covid | 41 | +297 | +2.19 | -0.4% |
| 2022_bear | 251 | -351 | -1.40 | -3.5% |
| 2024_rally | 252 | -258 | -1.10 | -2.6% |
| 2025_latest | 252 | +419 | +0.84 | -0.7% |

### `basis_carry_always` — VALIDATED

**Standalone** : Sharpe=+8.20, MaxDD=-2.1%, Total=$+3,268, days=3021

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 362 | 242 | -2.93 | +18.55 | -0.4% | +531 | yes |
| 2 | 362 | 242 | +8.47 | +6.02 | -0.9% | +207 | yes |
| 3 | 362 | 242 | +5.83 | +37.49 | -0.1% | +722 | yes |
| 4 | 362 | 242 | +20.38 | +8.39 | -0.9% | +297 | yes |
| 5 | 362 | 242 | +6.11 | -8.53 | -1.7% | -159 | no |

**Monte Carlo (1000 sims)** :
- Median DD : -0.1% | p10 DD : -0.1% | p90 DD : -0.1%
- P(DD > 20%) : 0.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+3,263 | p10 : $+3,120

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 365 | -89 | -3.04 | -1.2% |
| 2020_covid | 61 | +33 | +3.78 | -0.4% |
| 2022_bear | 365 | -212 | -6.82 | -2.1% |
| 2024_rally | 366 | +657 | +15.55 | -0.4% |
| 2025_latest | 365 | +338 | +7.70 | -0.7% |

### `basis_carry_funding_gt_5pct` — VALIDATED

**Standalone** : Sharpe=+14.91, MaxDD=-0.0%, Total=$+4,607, days=3021

**Walk-forward** :

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 362 | 242 | +8.12 | +24.95 | -0.0% | +579 | yes |
| 2 | 362 | 242 | +15.36 | +13.53 | -0.0% | +352 | yes |
| 3 | 362 | 242 | +12.92 | +42.98 | -0.0% | +734 | yes |
| 4 | 362 | 242 | +26.61 | +16.35 | -0.0% | +431 | yes |
| 5 | 362 | 242 | +13.68 | +5.99 | -0.0% | +68 | yes |

**Monte Carlo (1000 sims)** :
- Median DD : -0.0% | p10 DD : -0.0% | p90 DD : -0.0%
- P(DD > 20%) : 0.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+4,602 | p10 : $+4,501

**Stress periods** :

| Period | Days | Total $ | Sharpe | DD% |
|---|---:|---:|---:|---:|
| 2018_crypto_bear | 365 | +150 | +8.08 | -0.0% |
| 2020_covid | 61 | +76 | +11.71 | -0.0% |
| 2022_bear | 365 | +125 | +6.01 | -0.0% |
| 2024_rally | 366 | +710 | +19.11 | -0.0% |
| 2025_latest | 365 | +491 | +14.54 | -0.0% |
