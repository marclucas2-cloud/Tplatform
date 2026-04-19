# Intraday 4-Strategies Backtest — IBKR Futures — 2026-04-15

Data: 2 years of 1h bars via yfinance ETF proxies (SPY/QQQ/IWM/USO/GLD + ^VIX).
Costs: realistic IBKR micro futures round-trip (MES $2.49, MNQ $1.74, etc).

## Summary

| Strat | Symbol | N | WR | Avg $ | Total $ | **Sharpe** | PF | MaxDD | WF prof | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| gap | M2K | 60 | 0.617 | $-0.03 | $-2.0 | **-0.01** | 1.0 | $-793.0 | 3/5 | KILL |
| gap | MCL | 64 | 0.594 | $-42.47 | $-2718.0 | **-2.94** | 0.44 | $-3284.0 | 4/5 | KILL |
| gap | MES | 49 | 0.592 | $6.57 | $322.0 | **0.6** | 1.11 | $-1366.0 | 2/5 | MARGINAL |
| gap | MGC | 55 | 0.527 | $-28.64 | $-1575.0 | **-1.53** | 0.75 | $-1993.0 | 4/5 | KILL |
| gap | MNQ | 53 | 0.528 | $-12.18 | $-646.0 | **-0.64** | 0.9 | $-2555.0 | 3/5 | KILL |
| orb | M2K | 42 | 0.452 | $-4.67 | $-196.0 | **-0.7** | 0.84 | $-710.0 | 2/5 | KILL |
| orb | MES | 52 | 0.442 | $-15.94 | $-829.0 | **-1.46** | 0.71 | $-1751.0 | 2/5 | KILL |
| orb | MNQ | 54 | 0.444 | $-27.87 | $-1505.0 | **-1.43** | 0.74 | $-3397.0 | 2/5 | KILL |
| tod | M2K | 63 | 0.413 | $-9.28 | $-585.0 | **-3.77** | 0.49 | $-646.0 | 0/5 | KILL |
| tod | MES | 64 | 0.344 | $-17.23 | $-1103.0 | **-3.44** | 0.5 | $-1244.0 | 0/5 | KILL |
| tod | MNQ | 64 | 0.391 | $-31.85 | $-2038.0 | **-3.74** | 0.48 | $-2209.0 | 0/5 | KILL |
| vix | MES | 33 | 0.515 | $-76.88 | $-2537.0 | **-2.5** | 0.62 | $-4785.0 | 0/0 | KILL |
| vix | MNQ | 33 | 0.515 | $-111.25 | $-3671.0 | **-2.11** | 0.67 | $-8124.0 | 0/0 | KILL |

## Walk-Forward Detail

### gap_m2k
- IS avg Sharpe: 3.11 | OOS avg: **1.72** | Ratio: 0.55
- Profitable OOS: **3/5**

### gap_mcl
- IS avg Sharpe: 1.11 | OOS avg: **0.95** | Ratio: 0.86
- Profitable OOS: **4/5**

### gap_mes
- IS avg Sharpe: 6.94 | OOS avg: **2.26** | Ratio: 0.33
- Profitable OOS: **2/5**

### gap_mgc
- IS avg Sharpe: -0.58 | OOS avg: **0.62** | Ratio: 0
- Profitable OOS: **4/5**

### gap_mnq
- IS avg Sharpe: -0.82 | OOS avg: **3.27** | Ratio: 0
- Profitable OOS: **3/5**

### orb_m2k
- IS avg Sharpe: -6.05 | OOS avg: **-0.37** | Ratio: 0
- Profitable OOS: **2/5**

### orb_mes
- IS avg Sharpe: -7.18 | OOS avg: **-3.59** | Ratio: 0
- Profitable OOS: **2/5**

### orb_mnq
- IS avg Sharpe: -7.56 | OOS avg: **-1.82** | Ratio: 0
- Profitable OOS: **2/5**

### tod_m2k
- IS avg Sharpe: -4.4 | OOS avg: **-6.64** | Ratio: 0
- Profitable OOS: **0/5**

### tod_mes
- IS avg Sharpe: -4.02 | OOS avg: **-6.57** | Ratio: 0
- Profitable OOS: **0/5**

### tod_mnq
- IS avg Sharpe: -3.66 | OOS avg: **-5.95** | Ratio: 0
- Profitable OOS: **0/5**

## Notes

- ETF proxies used (SPY for MES, QQQ for MNQ, IWM for M2K, USO for MCL, GLD for MGC). Corrélation >0.95 avec futures, mais timing open/close peut différer de quelques min vs vrais futures CME.
- Hourly bars → ORB tests a 1h opening range, pas le classique 30min. Plus grossier mais directionnel.
- Costs model: US$ futures round-trip applied directement sur notional ETF × multiplier futures. Approximation raisonnable pour un first screen.
- Pour toute strat PASS ou MARGINAL → refaire avec vraie data futures CME intraday avant deploy.
