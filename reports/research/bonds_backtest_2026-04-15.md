# Bond Futures Intraday Backtest — 2026-04-15

Data: IBKR paper gateway 1h bars, 6 months (Oct 2025 → Apr 2026).
Symbols: ZN (10Y Treasury), ZT (2Y Treasury), ZB (30Y Treasury).
Cost model: micro bond round-trip $2.49 (M2F/M10Y/M30Y).

## Summary (ranked by Sharpe)

| Strat × Sym | N | WR | Avg $ | Total $ | **Sharpe** | PF | MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| gap_ZT | 5 | 0.4 | $1.15 | $6.0 | **0.71** | 1.45 | $-9.0 |
| mr_ZB | 103 | 0.524 | $-2.0 | $-206.0 | **-0.84** | 0.86 | $-676.0 |
| gap_ZN | 16 | 0.438 | $-2.67 | $-43.0 | **-1.37** | 0.54 | $-70.0 |
| gap_ZB | 34 | 0.441 | $-3.02 | $-103.0 | **-1.44** | 0.59 | $-137.0 |
| trend_ZN | 50 | 0.26 | $-7.08 | $-354.0 | **-2.28** | 0.58 | $-416.0 |
| mr_ZT | 72 | 0.444 | $-2.49 | $-179.0 | **-2.42** | 0.61 | $-206.0 |
| trend_ZB | 52 | 0.25 | $-15.81 | $-822.0 | **-2.74** | 0.53 | $-863.0 |
| trend_ZT | 32 | 0.219 | $-6.49 | $-208.0 | **-2.8** | 0.49 | $-174.0 |
| mr_ZN | 89 | 0.427 | $-4.34 | $-386.0 | **-2.98** | 0.55 | $-502.0 |
| fed_tod_ZB | 93 | 0.344 | $-2.78 | $-258.0 | **-3.0** | 0.52 | $-256.0 |
| fed_tod_ZN | 87 | 0.241 | $-3.03 | $-263.0 | **-6.27** | 0.26 | $-261.0 |
| fed_tod_ZT | 62 | 0.161 | $-3.16 | $-196.0 | **-8.77** | 0.1 | $-193.0 |

**Strats positives (Sharpe > 0) : 1/12**
**Strats bonnes (Sharpe > 0.5) : 1/12**

## Limitations

- 6 months seulement (Oct 2025 → Apr 2026) = period limited, biais régime possible
- Période coincide avec Fed rate-cutting → bond trend probablement biaisé bull
- Cost model micro ($2.49) appliqué sur returns du full contract — approximation raisonnable
