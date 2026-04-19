# T4-A1 - Crypto range harvest

**Run** : 2026-04-18 15:50 UTC
**Data** : 2023-01-01 00:00:00 -> 2026-03-28 20:00:00 (7098 4h bars)
**Instrument** : BTCUSDT 4h
**Cost model** : 0.26% round trip

## Thesis

- a crypto sleeve that survives both bull and bear should not rely only on trend beta
- BTC spends long stretches in chop even inside large bull or bear regimes
- low-ADX Bollinger fades are a candidate for a regime-agnostic harvest sleeve

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `range_bb_harvest_rebuild` | 88 | $+572 | +0.09 | -21.4% | **KEEP_FOR_RESEARCH** | +0.241 | -0.018 | -2.31pp | +0.00 |
| `range_bb_harvest_adx18` | 70 | $-633 | -0.11 | -28.2% | **KEEP_FOR_RESEARCH** | +0.167 | -0.057 | -2.31pp | +0.00 |
| `range_bb_harvest_bb30` | 79 | $+1,237 | +0.21 | -16.8% | **PROMOTE_PAPER** | +0.277 | +0.022 | +1.06pp | -0.04 |

## Best candidate

- `range_bb_harvest_bb30`
- Verdict : **PROMOTE_PAPER**
- Marginal score : +0.277
- Delta Sharpe : +0.022
- Delta MaxDD : +1.06pp
- Corr to portfolio : -0.042

## Note

- this is an independent rebuild of the existing `range_bb_harvest` idea, scored against the current portfolio baseline
- the goal here is not to overwrite production logic, only to confirm whether the sleeve still looks additive in 2026 research conditions