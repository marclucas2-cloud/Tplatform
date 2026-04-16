# T1-B — Futures intraday mean reversion MES/MGC

**Run date** : 2026-04-16 06:14 UTC
**Instruments** : MES (S&P 500 micro), MGC (micro gold)
**Methodologie** : fade les excess moves (|close-open| > kxATR14), holding 1 day
**Historique** : MES 2015-01-02 -> 2026-04-09 (2834 days)
**Baseline** : 7 strats (3 futures + 4 crypto post-S0)
**Couts** : MES $6.7 RT, MGC $5.7 RT

## Standalone stats

| Variant | Trades | Total PnL $ | Win rate |
|---|---:|---:|---:|
| `mes_fade_1.5atr` | 64 | +1,019 | 42.2% |
| `mes_fade_2.0atr` | 22 | +924 | 50.0% |
| `mes_fade_2.5atr` | 11 | +848 | 63.6% |
| `mes_fade_3.0atr` | 4 | +43 | 25.0% |
| `mes_fade_2atr_trend_filter` | 19 | +96 | 47.4% |
| `mgc_fade_1.5atr` | 112 | -4,214 | 48.2% |
| `mgc_fade_2.0atr` | 40 | -2,516 | 55.0% |
| `mgc_fade_2.5atr` | 7 | -23 | 57.1% |
| `mgc_fade_2atr_trend_filter` | 33 | -102 | 60.6% |

## Scorecards (marginal vs baseline 7 strats)

| Variant | Verdict | Score | dSharpe | dCAGR | dMaxDD | Corr | Tail | Penalties |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `mes_fade_2.0atr` | **PROMOTE_PAPER** | +0.212 | +0.008 | +0.12% | -3.95pp | +0.04 | 10% | - |
| `mes_fade_2.5atr` | **PROMOTE_PAPER** | +0.165 | +0.011 | +0.11% | +1.81pp | +0.01 | 7% | - |
| `mes_fade_2atr_trend_filter` | **PROMOTE_PAPER** | +0.165 | +0.001 | +0.01% | -3.95pp | -0.01 | 7% | - |
| `mes_fade_3.0atr` | **PROMOTE_PAPER** | +0.148 | +0.000 | +0.00% | +0.37pp | +0.01 | 3% | - |
| `mgc_fade_2atr_trend_filter` | **KEEP_FOR_RESEARCH** | +0.110 | -0.001 | -0.01% | +2.15pp | -0.04 | 0% | - |
| `mgc_fade_2.5atr` | **KEEP_FOR_RESEARCH** | +0.106 | -0.002 | +0.00% | -0.34pp | +0.01 | 0% | - |
| `mes_fade_1.5atr` | **DROP** | -0.013 | +0.009 | +0.14% | -13.08pp | -0.00 | 7% | maxdd_worsens=-13.1pp, HARD_DROP: maxdd_degrades=-13.1pp |
| `mgc_fade_2.0atr` | **KEEP_FOR_RESEARCH** | -0.028 | -0.037 | -0.35% | +2.71pp | -0.02 | 0% | - |
| `mgc_fade_1.5atr` | **DROP** | -0.166 | -0.069 | -0.63% | +4.41pp | -0.02 | 3% | - |

## Verdict summary

- **PROMOTE_PAPER** : `mes_fade_2.0atr`, `mes_fade_2.5atr`, `mes_fade_2atr_trend_filter`, `mes_fade_3.0atr`
- **KEEP_FOR_RESEARCH** : `mgc_fade_2atr_trend_filter`, `mgc_fade_2.5atr`, `mgc_fade_2.0atr`
- **DROP** : `mes_fade_1.5atr`, `mgc_fade_1.5atr`