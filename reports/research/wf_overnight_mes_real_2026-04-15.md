# Walk-Forward Backtest — Overnight MES (REAL production logic) — 2026-04-15

**Strategy** : reproduit EXACTEMENT la logique de strategies_v2/futures/overnight_buy_close.py
- Signal : close[T-1] > EMA20[T-1]
- Entry : open[T] (approximation du fill ~10 ET)
- SL : entry - 30 points ($150)
- TP : entry + 50 points ($250)
- Exit : premier bar avec SL ou TP touche, cap 10 jours (safety)

**Data** : MES_1D.parquet (1318 bars, 2021-01-04 → 2026-03-30)
**Costs** : $1.24 commission + 1 tick slippage ($1.25) = $2.49 round-trip

## Summary

| Metric | Value |
|---|---:|
| Total trades | **319** |
| Win rate | 38% |
| Avg PnL/trade | $-1.57 |
| Total net PnL | **$-499.0** |
| **Sharpe (annualized)** | **-0.06** |
| Profit factor | 0.98 |
| Max DD | $-3857.0 |
| Avg bars held | 1.9 |

## Exit breakdown

| Exit reason | Count | % |
|---|---:|---:|
| SL | 188 | 58.9% |
| TP | 114 | 35.7% |
| SL_PESSIMISTIC | 8 | 2.5% |
| TP_GAP | 4 | 1.3% |
| TIME | 3 | 0.9% |
| SL_GAP | 2 | 0.6% |

## Walk-Forward

- Profitable OOS windows: **3/5**
- IS avg Sharpe: 0.78
- OOS avg Sharpe: **-0.68**
- OOS/IS ratio: -0.87

| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL |
|---|---:|---:|---:|---:|---:|
| 1 | 53 | 53 | 2.21 | -4.22 | $-2482.0 |
| 2 | 79 | 53 | -2.69 | 2.31 | $1541.0 |
| 3 | 79 | 53 | 1.2 | 1.15 | $759.0 |
| 4 | 79 | 53 | 2.82 | 0.4 | $263.0 |
| 5 | 79 | 53 | 0.35 | -3.02 | $-1894.0 |

## Verdict

**❌ KILL — pas d'edge demontre, arreter la strat**

Comparison aux claims historiques :
- overnight_buy_close.py docstring : **Sharpe 3.85** (208 trades, +$13,546)
- paper_review_20260331 : **-0.70** (WF, mais test d'une AUTRE strat = signal_overnight_momentum)
- backtest_overnight_indices.py filtered : **0.27** (close→open, pas la vraie logique)
- **CE backtest (real prod logic)** : **-0.06**
