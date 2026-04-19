# T3-A2 - MES to BTC Asia lead-lag

**Run** : 2026-04-18 07:14 UTC
**Data** : 2024-04-16 -> 2026-03-27 (489 days)
**Signal** : previous MES US session return proxy from 15:00-21:59 UTC
**Execution** : BTC Asia session 00:00-07:59 UTC next day
**Cost model** : 0.10% round trip on $10,000 notional

## Thesis

- late US equity futures tone can propagate into crypto during the following Asia session
- the edge is fragile without threshold and volatility filters

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `btc_asia_mes_leadlag_q70_v80` | 97 | $+2,357 | +1.07 | -7.7% | **PROMOTE_PAPER** | +0.338 | +0.155 | -2.53pp | -0.00 |
| `btc_asia_mes_longonly_q80_v80` | 25 | $+822 | +1.08 | -2.5% | **PROMOTE_PAPER** | +0.205 | +0.053 | -0.86pp | +0.06 |
| `btc_asia_mes_shortonly_q85_v80` | 22 | $+1,852 | +1.31 | -1.8% | **PROMOTE_LIVE** | +0.310 | +0.137 | +0.96pp | -0.04 |

## Best candidate

- `btc_asia_mes_leadlag_q70_v80`
- Verdict : **PROMOTE_PAPER**
- Marginal score : +0.338
- Delta Sharpe : +0.155
- Delta MaxDD : -2.53pp
- Corr to portfolio : -0.004