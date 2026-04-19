# Gate 5 — Portfolio V15.3 + US Stock Candidates

Baseline: V15.3 (4 LIVE + EU-06 MacroECB), $10K capital, 3 ans (2023-04 → 2026-04).
US candidates: 3 strats ($8,333 chacune, total $25K).
Combined capital: $35K.

## Gate 5 critère

**PASS** si la combinaison ameliore Sharpe OU reduit MaxDD (%) vs baseline.

## Résultats

| Configuration | Days | Total PnL | Total Ret | ROC/an | Sharpe | MaxDD $ | MaxDD % |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline only | 708 | $+9,726 | +97.3% | +34.6% | **1.24** | $-3,267 | -32.7% |
| baseline + tom | 719 | $+12,175 | +66.4% | +23.3% | **1.49** | $-3,632 | -19.8% |
| baseline + rs_spy | 789 | $+13,705 | +74.8% | +23.9% | **1.63** | $-3,870 | -21.1% |
| baseline + sector_rot | 789 | $+17,860 | +97.4% | +31.1% | **2.09** | $-4,115 | -22.4% |
| baseline + tom + rs_spy | 789 | $+16,154 | +60.6% | +19.3% | **1.86** | $-4,123 | -15.5% |
| baseline + tom + rs_spy + sector_rot (ALL) | 789 | $+24,288 | +69.4% | +22.2% | **2.70** | $-4,937 | -14.1% |

## Verdict par candidat

### tom
- Sharpe: 1.24 → 1.49 (+0.25) — ✓ mieux
- MaxDD%: -32.7% → -19.8% — ✓ mieux
- **Verdict: PASS**

### rs_spy
- Sharpe: 1.24 → 1.63 (+0.39) — ✓ mieux
- MaxDD%: -32.7% → -21.1% — ✓ mieux
- **Verdict: PASS**

### sector_rot
- Sharpe: 1.24 → 2.09 (+0.85) — ✓ mieux
- MaxDD%: -32.7% → -22.4% — ✓ mieux
- **Verdict: PASS**

## Combinaison des 3 candidats

- Sharpe: 1.24 → **2.70**
- MaxDD%: -32.7% → **-14.1%**
- Total PnL: $9,726 → **$24,288**
- ROC/an: 34.6%/an → **22.2%/an**
