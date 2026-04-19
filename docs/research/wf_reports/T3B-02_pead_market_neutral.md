# T3-B2 - PEAD market-neutral

**Run** : 2026-04-18 07:54 UTC
**Goal** : test whether PEAD can survive in a portfolio-neutral form

## Conclusion

- the tested market-neutral PEAD variants do not clear the portfolio hard gates
- this batch should be treated as a rejection of the current market-neutral PEAD design

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `pead_spy_hedged_p5_n3_h5` | 922 | $+7,235 | +0.31 | -30.2% | **DROP** | +0.175 | +0.030 | -37.21pp | +0.01 |
| `pead_spy_hedged_p8_n3_h5` | 822 | $+5,832 | +0.24 | -43.3% | **DROP** | +0.205 | -0.001 | -26.26pp | +0.01 |
| `pead_xs_topbot_h5` | 627 | $-1,102 | -0.07 | -39.0% | **DROP** | +0.078 | -0.055 | -29.31pp | -0.01 |

## Reject note

- worst variant: `pead_xs_topbot_h5`
- verdict: **DROP**
- delta maxDD: -29.31pp
- comment: current neutralization scheme damages drawdown profile too much