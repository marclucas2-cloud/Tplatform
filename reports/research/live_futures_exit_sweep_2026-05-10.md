# Live Futures Exit Sweep - 2026-05-10

## Scope
- Strategies: `cross_asset_momentum` and `gold_oil_rotation`.
- Parameter swept: relative-gain threshold (`min_momentum` for CAM, `min_edge` for GOR).
- Exit policies: native fixed, 48h cap, 48h + Friday close, trailing SL, trailing TP, trailing SL+TP.
- Research only: no runtime, broker, state, or config changes.

## Baselines
| strategy | rel_gain_threshold | exit_policy | n | total_pnl | win_rate_pct | sharpe | max_dd | wf_profitable_windows | wf_mean_sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cross_asset_momentum | 0.02 | fixed_48h | 63 | 4198.62 | 54.0 | 0.54 | -1591.51 | 3 | 0.23 |
| gold_oil_rotation | 0.02 | native_fixed | 126 | 31712.54 | 53.2 | 2.04 | -1140.73 | 5 | 1.91 |

## Top CAM Configs
| rel_gain_threshold | exit_policy | n | total_pnl | win_rate_pct | sharpe | max_dd | avg_bars_held | wf_profitable_windows | wf_mean_sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.02 | native_fixed | 63 | 32786.96 | 52.4 | 2.25 | -3407.32 | 10.11 | 4 | 2.02 |
| 0.01 | native_fixed | 64 | 31237.84 | 54.7 | 2.13 | -3143.07 | 10.41 | 4 | 1.94 |
| 0.0 | native_fixed | 64 | 25330.01 | 48.4 | 1.72 | -3891.82 | 9.67 | 4 | 1.57 |
| 0.03 | native_fixed | 60 | 20799.78 | 45.0 | 1.59 | -4236.62 | 9.82 | 4 | 1.24 |
| 0.05 | native_fixed | 58 | 13511.95 | 39.7 | 1.02 | -4992.31 | 9.62 | 4 | 0.93 |
| 0.05 | fixed_48h | 58 | 2190.42 | 55.2 | 0.43 | -2384.72 | 1.48 | 4 | 0.41 |
| 0.02 | fixed_48h_friday | 63 | 5806.3 | 50.8 | 0.8 | -1390.31 | 1.33 | 3 | 0.44 |
| 0.04 | native_fixed | 59 | 9509.93 | 37.3 | 0.73 | -3064.82 | 9.59 | 3 | 0.49 |
| 0.02 | trail_tp_48h_friday | 63 | 3339.58 | 49.2 | 0.66 | -1621.94 | 1.35 | 3 | 0.24 |
| 0.02 | fixed_48h | 63 | 4198.62 | 54.0 | 0.54 | -1591.51 | 1.54 | 3 | 0.23 |
| 0.02 | trail_sl_48h_friday | 63 | 1628.51 | 47.6 | 0.46 | -1612.79 | 1.25 | 3 | 0.46 |
| 0.05 | trail_sl_48h_friday | 58 | 1412.63 | 37.9 | 0.37 | -1473.6 | 1.03 | 3 | 0.34 |

## Top GOR Configs
| rel_gain_threshold | exit_policy | n | total_pnl | win_rate_pct | sharpe | max_dd | avg_bars_held | wf_profitable_windows | wf_mean_sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.02 | native_fixed | 126 | 31712.54 | 53.2 | 2.04 | -1140.73 | 4.0 | 5 | 1.91 |
| 0.03 | native_fixed | 121 | 26603.87 | 53.7 | 1.67 | -1485.96 | 4.28 | 5 | 1.58 |
| 0.05 | native_fixed | 108 | 22389.68 | 50.0 | 1.56 | -1529.47 | 4.0 | 5 | 1.3 |
| 0.04 | native_fixed | 117 | 24738.73 | 55.6 | 1.55 | -1778.33 | 4.26 | 5 | 1.53 |
| 0.04 | fixed_48h | 117 | 13358.2 | 58.1 | 1.42 | -1116.52 | 1.4 | 5 | 1.34 |
| 0.02 | fixed_48h | 126 | 14618.08 | 50.8 | 1.27 | -1176.41 | 1.29 | 5 | 1.1 |
| 0.02 | fixed_48h_friday | 126 | 10280.01 | 53.2 | 1.19 | -1411.18 | 0.95 | 5 | 1.11 |
| 0.02 | trail_sl_48h_friday | 126 | 9638.61 | 50.0 | 1.12 | -1543.54 | 0.93 | 5 | 1.06 |
| 0.02 | trail_tp_48h_friday | 126 | 8402.49 | 52.4 | 0.99 | -2148.55 | 0.99 | 5 | 0.98 |
| 0.02 | trail_sl_tp_48h_friday | 126 | 7818.35 | 49.2 | 0.96 | -1822.47 | 0.96 | 5 | 0.93 |
| 0.01 | trail_sl_48h_friday | 128 | 5678.59 | 52.3 | 0.79 | -2143.17 | 1.02 | 5 | 0.85 |
| 0.01 | fixed_48h | 128 | 6211.57 | 52.3 | 0.77 | -2511.34 | 1.34 | 5 | 0.88 |

## Interpretation Guardrails
- Same-bar ordering is conservative for long trades: SL is assumed hit before TP if both are inside the daily range.
- CAM dollar PnL is one contract per trade across heterogeneous micro futures; compare policies within CAM, not absolute dollars vs GOR.
- GOR next-open entry and CAM same-close entry intentionally match their existing research harnesses.
- Any trailing rule that wins here still needs live-runner implementation review before production.
