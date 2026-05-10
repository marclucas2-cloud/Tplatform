# Gold-Oil Rotation - Smarter Trailing Families - 2026-05-06

## Setup
- Data: `2021-01-04 -> 2026-03-30` on `MGC_1D.parquet` / `MCL_1D.parquet`
- Entry logic unchanged: lookback 20, min_edge 2%, next-open entry
- Baseline exits: fixed SL 2%, fixed TP 4%, max hold 10 bars
- All managed exits are applied conservatively: highs/lows update the managed stop only for the next bar, never with same-bar hindsight.

## Variants tested
- `baseline_fixed_2_4`: Fixed SL 2%, fixed TP 4%, max hold 10 bars.
- `simple_trail_2pct_tp4`: Immediate 2% trailing stop from highest high, fixed TP 4%.
- `arm_1R_then_trail_2pct_tp4`: Keep fixed SL/TP until +1R, then 2% trailing stop, TP still 4%.
- `atr2_trail_no_tp`: Fixed initial SL 2%, no TP, trail at highest - 2x ATR(14).
- `breakeven_1R_then_atr1_no_tp`: Move stop to breakeven at +1R, then start 1x ATR trailing at +2R, no TP.

## Summary Table
| label | n | total_pnl | avg_pnl | win_rate | sharpe | profit_factor | max_dd | wf_profitable_windows | wf_oos_total_pnl | wf_oos_mean_sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_fixed_2_4 | 126 | 31712.54 | 251.69 | 53.2 | 6.44 | 3.16 | -1140.73 | 5 | 16721.58 | 7.16 |
| simple_trail_2pct_tp4 | 126 | 27638.6 | 219.35 | 49.2 | 5.71 | 3.04 | -1568.73 | 5 | 14175.58 | 5.76 |
| arm_1R_then_trail_2pct_tp4 | 126 | 30305.06 | 240.52 | 56.3 | 6.22 | 3.19 | -1266.07 | 5 | 15231.69 | 6.32 |
| atr2_trail_no_tp | 126 | 29812.27 | 236.61 | 46.8 | 5.28 | 3.0 | -1889.46 | 5 | 15567.18 | 6.02 |
| breakeven_1R_then_atr1_no_tp | 126 | 31485.73 | 249.89 | 44.4 | 5.24 | 3.22 | -1900.55 | 5 | 17126.14 | 5.54 |

## Baseline vs Best Managed Variant
- Baseline: `baseline_fixed_2_4` total PnL `$31712.54`, Sharpe `6.44`, max DD `$-1140.73`, WF `5/5` profitable windows
- Best managed variant: `breakeven_1R_then_atr1_no_tp` total PnL `$31485.73`, Sharpe `5.24`, max DD `$-1900.55`, WF `5/5` profitable windows

## Exit Mix
- Baseline exits: `{'tp': 39, 'sl': 48, 'time_exit': 29, 'gap_sl': 5, 'gap_tp': 5}`
- Best managed exits: `{'managed_sl': 79, 'managed_gap_sl': 17, 'time_exit': 30}`

## Interpretation
- PnL delta best managed vs baseline: `$-226.81`
- Sharpe delta best managed vs baseline: `-1.21`
- Max DD delta best managed vs baseline: `$-759.82` (less negative is better)
- If even the smarter managed variants underperform the fixed design, that is strong evidence to keep GOR fixed-stop in live and treat manual live trailing as a discretionary override, not a strategy upgrade.
