# Overnight Drift Backtest — Index Futures Diversification (2026-04-15)

Strategy: BUY close T, SELL open T+1.
- NAIVE: every day
- FILTERED: only when close > EMA20 (production filter from backtest_portfolio_v153.py)

## Summary (naive vs filtered)

| Instrument | Variant | Trades | WR | Avg | Net | **Sharpe** | PF | MaxDD | Burn% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| MES | naive | 1297 | 0.524 | $0.99 | $1290.0 | **0.21** | 1.06 | $-2098.0 | 71.5% |
| MES | filtered | 866 | 0.508 | $0.89 | $769.0 | **0.27** | 1.07 | $-1764.0 | 73.7% |
| M2K | naive | 1297 | 0.494 | $0.05 | $65.0 | **0.02** | 1.01 | $-1419.0 | 97.2% |
| M2K | filtered | 708 | 0.465 | $-0.55 | $-390.0 | **-0.34** | 0.92 | $-1021.0 | 146.4% |
| MNQ | naive | 1297 | 0.515 | $5.09 | $6600.0 | **0.6** | 1.17 | $-3467.0 | 25.5% |
| MNQ | filtered | 825 | 0.495 | $2.62 | $2161.0 | **0.43** | 1.11 | $-3180.0 | 39.9% |
| NIY | naive | 1237 | 0.486 | $62.99 | $77919.0 | **0.46** | 1.1 | $-53233.0 | 30.3% |
| NIY | filtered | 745 | 0.474 | $62.41 | $46496.0 | **0.47** | 1.11 | $-48239.0 | 30.5% |

## Walk-Forward (filtered variant)

### MES (filtered)
- Profitable OOS windows: **3/5**
- IS avg Sharpe: 0.71 | OOS avg Sharpe: **0.01** | Ratio: 0.02

### M2K (filtered)
- Profitable OOS windows: **1/5**
- IS avg Sharpe: -0.20 | OOS avg Sharpe: **-0.97** | Ratio: 0.00

### MNQ (filtered)
- Profitable OOS windows: **4/5**
- IS avg Sharpe: 0.35 | OOS avg Sharpe: **0.52** | Ratio: 1.48

### NIY (filtered)
- Profitable OOS windows: **2/5**
- IS avg Sharpe: -0.42 | OOS avg Sharpe: **0.35** | Ratio: 0.00

## Correlation Matrix (daily PnL$)

| | MES | M2K | MNQ | NIY |
|---|---|---|---|---|
| **MES** | 1.0 | 0.575 | 0.829 | 0.124 |
| **M2K** | 0.575 | 1.0 | 0.475 | 0.061 |
| **MNQ** | 0.829 | 0.475 | 1.0 | 0.119 |
| **NIY** | 0.124 | 0.061 | 0.119 | 1.0 |

## Verdict (filtered variant)

**MES baseline filtered Sharpe: 0.27**

- **M2K**: Sharpe -0.34 (vs MES 0.27) | OOS prof 1/5 | corr vs MES 0.57 → **KILL**
- **MNQ**: Sharpe 0.43 (vs MES 0.27) | OOS prof 4/5 | corr vs MES 0.83 → **REPLACE_MES**
- **NIY**: Sharpe 0.47 (vs MES 0.27) | OOS prof 2/5 | corr vs MES 0.12 → **KILL**
