# US Stock Candidates — Walk-Forward + Portfolio Sharpe

Framework: trades chronologiquement splittées en 5 fenêtres rolling (70/30 IS/OOS).
Portfolio Sharpe: equity curve quotidienne en supposant 10 positions concurrentes.

## Gate V15.3
- OOS Sharpe avg > 0.5
- OOS/IS ratio > 0.5
- >= 50% fenêtres profitables
- >= 30 OOS trades par fenêtre

## Résultats

### rs_spy
- N trades: **600**
- Trade-level Sharpe (annualized): **0.97**
- Portfolio Sharpe (10 concurrent): **3.83** (plus realiste)
- Portfolio MaxDD: **-31.0%**
- WF IS avg Sharpe: 1.15
- WF OOS avg Sharpe: 0.91
- OOS/IS ratio: 0.79
- Profitable windows: 3/5

| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL% | OOS WR |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 100 | 2.67 | 1.72 | 263.7% | 52% |
| 2 | 200 | 100 | 2.12 | -0.83 | -171.6% | 49% |
| 3 | 233 | 100 | 0.73 | -0.69 | -98.6% | 49% |
| 4 | 233 | 100 | -0.41 | 1.93 | 297.2% | 60% |
| 5 | 233 | 100 | 0.62 | 2.41 | 274.6% | 54% |

**Verdict: GO** — gates 4/4 (OOS>0.5:True OOS/IS>0.5:True Prof≥50%:True OOSn≥30:True)

### sector_rot
- N trades: **116**
- Trade-level Sharpe (annualized): **0.50**
- Portfolio Sharpe (10 concurrent): **2.53** (plus realiste)
- Portfolio MaxDD: **-5.6%**
- WF IS avg Sharpe: 0.20
- WF OOS avg Sharpe: 0.68
- OOS/IS ratio: 3.39
- Profitable windows: 5/5

| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL% | OOS WR |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 19 | 19 | -0.87 | 0.28 | 21.0% | 42% |
| 2 | 38 | 19 | -0.18 | 1.92 | 150.2% | 58% |
| 3 | 44 | 19 | 0.78 | 0.42 | 35.1% | 47% |
| 4 | 44 | 19 | 0.91 | 0.09 | 3.9% | 53% |
| 5 | 44 | 19 | 0.37 | 0.69 | 30.7% | 53% |

**Verdict: BORDERLINE** — gates 3/4 (OOS>0.5:True OOS/IS>0.5:True Prof≥50%:True OOSn≥30:False)

### tom
- N trades: **600**
- Trade-level Sharpe (annualized): **2.42**
- Portfolio Sharpe (10 concurrent): **6.18** (plus realiste)
- Portfolio MaxDD: **-11.9%**
- WF IS avg Sharpe: 2.24
- WF OOS avg Sharpe: 2.48
- OOS/IS ratio: 1.11
- Profitable windows: 5/5

| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL% | OOS WR |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 100 | 3.28 | 3.47 | 185.4% | 67% |
| 2 | 200 | 100 | 3.30 | 1.47 | 88.0% | 47% |
| 3 | 233 | 100 | 2.01 | 1.63 | 95.2% | 57% |
| 4 | 233 | 100 | 1.43 | 2.80 | 118.5% | 61% |
| 5 | 233 | 100 | 1.17 | 3.03 | 145.9% | 53% |

**Verdict: GO** — gates 4/4 (OOS>0.5:True OOS/IS>0.5:True Prof≥50%:True OOSn≥30:True)

### high_52w
- N trades: **756**
- Trade-level Sharpe (annualized): **0.80**
- Portfolio Sharpe (10 concurrent): **1.46** (plus realiste)
- Portfolio MaxDD: **-18.7%**
- WF IS avg Sharpe: 1.22
- WF OOS avg Sharpe: 0.78
- OOS/IS ratio: 0.64
- Profitable windows: 4/5

| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL% | OOS WR |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 126 | 126 | 0.19 | 1.22 | 82.4% | 47% |
| 2 | 252 | 126 | 0.63 | 0.41 | 27.2% | 44% |
| 3 | 294 | 126 | 1.17 | 3.73 | 272.2% | 58% |
| 4 | 294 | 126 | 2.59 | 0.89 | 81.8% | 45% |
| 5 | 294 | 126 | 1.52 | -2.35 | -130.5% | 40% |

**Verdict: GO** — gates 4/4 (OOS>0.5:True OOS/IS>0.5:True Prof≥50%:True OOSn≥30:True)

## Corrélation inter-strats (daily PnL)

| | rs_spy | sector_rot | tom | high_52w |
|---|---|---|---|---|
| **rs_spy** | 1.00 | 0.30 | 0.06 | 0.09 |
| **sector_rot** | 0.30 | 1.00 | -0.00 | 0.12 |
| **tom** | 0.06 | -0.00 | 1.00 | 0.08 |
| **high_52w** | 0.09 | 0.12 | 0.08 | 1.00 |

## Summary

| Strat | N | Trade Sh | **Port Sh** | MDD | OOS Sh | OOS/IS | Prof | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| rs_spy | 600 | 0.97 | **3.83** | -31.0% | 0.91 | 0.79 | 3/5 | GO |
| sector_rot | 116 | 0.5 | **2.53** | -5.6% | 0.68 | 3.39 | 5/5 | BORDERLINE |
| tom | 600 | 2.42 | **6.18** | -11.9% | 2.48 | 1.11 | 5/5 | GO |
| high_52w | 756 | 0.8 | **1.46** | -18.7% | 0.78 | 0.64 | 4/5 | GO |