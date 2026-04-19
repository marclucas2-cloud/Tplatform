# US Stock Research — 12 strats backtest

Universe: 496 S&P 500 stocks, 5 years daily (2021-03 → 2026-04)
Costs: 3 bps/side ($0 commission Alpaca + 2 bps PFOF + 1 bps slippage)

## Summary

| Strat | Trades | WR | Avg/trade | Total | Sharpe | PF | MaxDD | Avg bars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pead | 6039 | 42% | -0.32% | -1933.0% | -0.99 | 0.90 | -2390.2% | 12.7 |
| sector_rot | 116 | 49% | 1.61% | 187.3% | 2.46 | 1.35 | -60.2% | 30.1 |
| rsi2_mr | 40006 | 59% | 0.04% | 1816.2% | 0.31 | 1.03 | -3814.1% | 3.7 |
| gap_go | 25299 | 45% | -0.35% | -8786.8% | -1.17 | 0.73 | -8844.9% | 1.0 |
| dividend | 7873 | 53% | 0.14% | 1082.3% | 0.77 | 1.12 | -517.3% | 4.0 |
| high_52w | 756 | 47% | 0.46% | 350.5% | 0.99 | 1.15 | -230.7% | 13.4 |
| rs_spy | 600 | 54% | 1.47% | 884.6% | 3.78 | 1.31 | -434.4% | 30.1 |
| vol_contract | 12678 | 50% | -0.10% | -1301.9% | -0.44 | 0.95 | -3896.6% | 8.1 |
| pairs | 562 | 51% | -0.07% | -41.3% | -0.54 | 0.97 | -101.6% | 12.9 |
| tom | 600 | 58% | 1.29% | 772.2% | 6.17 | 1.91 | -118.6% | 4.7 |
| bab | 1100 | 48% | -2.25% | -2469.3% | -5.26 | 0.65 | -2941.5% | 30.1 |
| low_vol | 1160 | 49% | -1.68% | -1943.4% | -3.88 | 0.71 | -2901.7% | 30.1 |

## Notes

- PEAD uses a price-action proxy (vol spike + big move), not real EPS surprise — academic edge is similar but less precise.
- Gap&Go uses daily bars → exit = same-day close (no intraday SL/TP). Realistic only as a first screen.
- Market-neutral strats: sector_rot, rs_spy, pairs, bab, low_vol.
- Each trade = flat unit (1% of notional). Portfolio sizing + WF + slippage stress = next pass.
