# T3-A1 - MCL overnight drift

**Run** : 2026-04-18 07:14 UTC
**Data** : 2015-01-02 -> 2026-04-09 (2833 days)
**Instrument** : MCL micro crude oil
**Cost model** : $3.70 round trip (IBKR commission + 1 tick slippage per side)

## Thesis

- crude oil reprices overnight on macro, OPEC and geopolitics more than during the US day session
- a weekday + trend filter may isolate the cleaner part of the drift

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `mcl_overnight_mon_trend10` | 286 | $+4,751 | +0.80 | -4.3% | **PROMOTE_LIVE** | +0.340 | +0.059 | -0.81pp | +0.07 |
| `mcl_overnight_mon_wed_trend10` | 618 | $+5,094 | +0.73 | -7.3% | **PROMOTE_PAPER** | +0.351 | +0.062 | -3.18pp | +0.07 |
| `mcl_overnight_mon_trend40` | 281 | $+4,715 | +0.81 | -3.9% | **PROMOTE_LIVE** | +0.341 | +0.057 | +0.74pp | +0.08 |

## Best candidate

- `mcl_overnight_mon_wed_trend10`
- Verdict : **PROMOTE_PAPER**
- Marginal score : +0.351
- Delta Sharpe : +0.062
- Delta MaxDD : -3.18pp
- Corr to portfolio : +0.067