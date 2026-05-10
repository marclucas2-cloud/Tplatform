# Gold-Oil Rotation — Trailing Stop Backtest — 2026-05-06

## Setup
- Data: `2021-01-04 -> 2026-03-30` on `MGC_1D.parquet` / `MCL_1D.parquet`
- Entry logic unchanged: lookback 20, min_edge 2%, next-open entry
- Baseline exits: fixed SL 2%, fixed TP 4%, max hold 10 bars
- Trailing overlay: same initial SL/TP, but SL ratchets off the highest high and becomes active from the next bar only
- Conservative assumption: no intrabar hindsight; a same-day high does not tighten the stop for the same candle

## Summary Table
| label | trail_pct | n | total_pnl | avg_pnl | win_rate | sharpe | profit_factor | max_dd | wf_profitable_windows | wf_oos_total_pnl | wf_oos_mean_sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_fixed_2_4 | nan | 126 | 31712.54 | 251.69 | 53.2 | 6.44 | 3.16 | -1140.73 | 5 | 16721.58 | 7.16 |
| trail_0.40pct | 0.4 | 126 | 11332.14 | 89.94 | 50.0 | 3.26 | 2.2 | -1828.32 | 3 | 5061.14 | -0.5 |
| trail_0.60pct | 0.6 | 126 | 12856.16 | 102.03 | 49.2 | 3.64 | 2.35 | -1552.53 | 3 | 5386.77 | 1.79 |
| trail_0.80pct | 0.8 | 126 | 17272.16 | 137.08 | 49.2 | 4.32 | 2.77 | -1276.34 | 4 | 10468.85 | 4.22 |
| trail_1.00pct | 1.0 | 126 | 19876.2 | 157.75 | 50.0 | 4.76 | 2.98 | -1355.22 | 4 | 12926.45 | 5.65 |
| trail_1.25pct | 1.25 | 126 | 20969.92 | 166.43 | 46.0 | 4.86 | 2.86 | -1571.86 | 4 | 13800.33 | 5.95 |
| trail_1.50pct | 1.5 | 126 | 21379.76 | 169.68 | 49.2 | 4.9 | 2.77 | -1891.22 | 4 | 13521.76 | 5.95 |
| trail_2.00pct | 2.0 | 126 | 27638.6 | 219.35 | 49.2 | 5.71 | 3.04 | -1568.73 | 5 | 14175.58 | 5.76 |

## Baseline vs Best Trailing
- Baseline: `baseline_fixed_2_4` total PnL `$31712.54`, Sharpe `6.44`, max DD `$-1140.73`, WF `5/5` profitable windows
- Best trailing by total PnL: `trail_2.00pct` total PnL `$27638.60`, Sharpe `5.71`, max DD `$-1568.73`, WF `5/5` profitable windows

## Exit Mix
- Baseline exits: `{'tp': 39, 'sl': 48, 'time_exit': 29, 'gap_sl': 5, 'gap_tp': 5}`
- Best trailing exits: `{'tp': 28, 'trail_sl': 68, 'trail_gap_sl': 12, 'time_exit': 13, 'gap_tp': 5}`

## Interpretation
- PnL delta best trailing vs baseline: `$-4073.94`
- Sharpe delta best trailing vs baseline: `-0.74`
- Max DD delta best trailing vs baseline: `$-428.00` (less negative is better)
- If all trailing variants underperform the baseline, that is a strong argument against wiring a generic trailing stop into `gold_oil_rotation` live without a strategy-specific redesign.
