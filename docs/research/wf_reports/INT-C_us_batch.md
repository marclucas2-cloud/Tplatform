# INT-C - US sector long/short validation

**Run** : 2026-04-18 07:54 UTC

## Summary

- Candidate : `us_sector_ls_40_5`
- Standalone Sharpe : +0.39
- Standalone MaxDD : -2.1%
- WF OOS pass : 3/5
- MC P(DD>30%) : 0.0%
- Overall : **VALIDATED**

## Walk-forward

| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 152 | 102 | +0.30 | -0.14 | -1.8% | -20 | no |
| 2 | 152 | 102 | +0.78 | +1.14 | -2.2% | +183 | yes |
| 3 | 152 | 102 | +1.04 | +0.27 | -1.4% | +28 | yes |
| 4 | 152 | 102 | -0.35 | +1.13 | -0.7% | +81 | yes |
| 5 | 152 | 102 | -0.64 | +0.18 | -0.7% | +14 | no |

## Monte Carlo

- Median DD : -4.7%
- P(DD > 20%) : 0.0%
- P(DD > 30%) : 0.0%
- Median final PnL : $+575