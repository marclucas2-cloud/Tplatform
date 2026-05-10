# Gold-Oil Rotation - Trailing Audit (11Y) - 2026-05-06

## Setup
- Data: `MGC_LONG.parquet` and `MCL_LONG.parquet` (2015-01-02 -> 2026-04-09, 4115 calendar days)
- Entry: 20d momentum spread, edge 2%, next-open entry
- Baseline: fixed SL 2%, fixed TP 4%, max hold 10 bars
- Costs: round-trip commission $2.49 + 1-tick slippage per leg ($2 round-trip MGC, $2 round-trip MCL)
- Sharpe annualised by trade frequency: `(mean/std) * sqrt(trades_per_year)`, not flat sqrt(252)

## Variants
- `baseline_fixed_2_4`: fixed SL/TP
- `trail_X.XXpct`: SL ratchets to `highest * (1 - X.XX%)`, TP unchanged
- `arm_1R_then_trail_2pct`: fixed SL/TP until +1R, then 2% trailing
- `atr2_trail_no_tp`: trail at `highest - 2 * ATR(14)`, no TP
- `breakeven_1R_then_atr1_no_tp`: breakeven at +1R, then 1*ATR trail at +2R, no TP

## Summary
| label | n | trades_per_year | total_pnl_net | pnl_per_year | win_rate_pct | sharpe_ann | profit_factor | max_dd | calmar | wf_profitable_windows | wf_oos_total_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_fixed_2_4 | 274 | 24.32 | 31337.02 | 2781.49 | 46.72 | 1.2 | 2.05 | -2449.61 | 1.14 | 4 | 21640.37 |
| trail_0.40pct | 274 | 24.32 | 11542.65 | 1024.53 | 47.08 | 0.66 | 1.74 | -2359.98 | 0.43 | 5 | 11161.55 |
| trail_0.60pct | 274 | 24.32 | 13703.06 | 1216.29 | 48.54 | 0.77 | 1.87 | -2490.35 | 0.49 | 4 | 12466.68 |
| trail_0.80pct | 274 | 24.32 | 16394.13 | 1455.15 | 46.72 | 0.82 | 1.97 | -3131.75 | 0.46 | 5 | 15846.18 |
| trail_1.00pct | 274 | 24.32 | 17661.01 | 1567.6 | 45.62 | 0.83 | 1.95 | -2712.78 | 0.58 | 5 | 17395.61 |
| trail_1.25pct | 274 | 24.32 | 19873.17 | 1763.96 | 44.53 | 0.91 | 2.01 | -2397.67 | 0.74 | 5 | 18128.4 |
| trail_1.50pct | 274 | 24.32 | 20242.02 | 1796.69 | 44.16 | 0.9 | 1.93 | -2237.23 | 0.8 | 5 | 18370.13 |
| trail_2.00pct | 274 | 24.32 | 28096.68 | 2493.88 | 43.07 | 1.12 | 2.17 | -2113.81 | 1.18 | 4 | 21467.43 |
| arm_1R_then_trail_2pct | 274 | 24.32 | 31216.14 | 2770.76 | 49.27 | 1.2 | 2.09 | -2627.73 | 1.05 | 4 | 22099.65 |
| atr2_trail_no_tp | 274 | 24.32 | 27795.83 | 2467.18 | 38.69 | 0.94 | 1.95 | -4470.34 | 0.55 | 4 | 24029.29 |
| breakeven_1R_then_atr1_no_tp | 274 | 24.32 | 33174.55 | 2944.59 | 40.15 | 1.04 | 2.1 | -4017.38 | 0.73 | 4 | 25938.69 |

## Baseline vs Best Managed
- Baseline `baseline_fixed_2_4`: net PnL `$31337.02` / yr `$2781.49` / Sharpe `1.2` / DD `$-2449.61` / Calmar `1.14` / WF `4/5`
- Best managed `breakeven_1R_then_atr1_no_tp`: net PnL `$33174.55` / yr `$2944.59` / Sharpe `1.04` / DD `$-4017.38` / Calmar `0.73` / WF `4/5`
- Delta net PnL: `$1837.53`
- Delta Sharpe: `-0.16`
- Delta MaxDD: `$-1567.77` (less negative = better)

## Exit Mix
- Baseline: `{'sl': 119, 'tp': 79, 'time_exit': 62, 'gap_sl': 9, 'gap_tp': 5}`
- Best managed: `{'sl': 176, 'gap_sl': 19, 'time_exit': 79}`
