# Data Audit Report

Date: 2026-03-27 01:17
Strategies auditees: 7
Tickers uniques: 9

## Strategies retenues

| Strategie | Verdict | Tickers |
|-----------|---------|---------|
| Day-of-Week Seasonal | VALIDATED | SPY, QQQ |
| Correlation Regime Hedge | VALIDATED | SPY, TLT, GLD, USO |
| VIX Expansion Short | VALIDATED | SPY, QQQ |
| High-Beta Underperformance Short | VALIDATED | ARKK, TSLA, SPY |
| Late Day Mean Reversion | BORDERLINE | SPY, QQQ, AAPL, MSFT |
| Failed Rally Short | BORDERLINE | SPY, QQQ |
| EOD Sell Pressure V2 | BORDERLINE | SPY, QQQ, AAPL |

## Resultats par ticker

### AAPL
- **Fichier**: `AAPL_5Min_20260222_20260324.parquet`
- **Lignes**: 3,563 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### ARKK
- **Fichier**: Non trouve

### GLD
- **Fichier**: `GLD_5Min_20260222_20260324.parquet`
- **Lignes**: 3,810 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### MSFT
- **Fichier**: `MSFT_5Min_20260222_20260324.parquet`
- **Lignes**: 3,817 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### QQQ
- **Fichier**: `QQQ_5Min_20260222_20260324.parquet`
- **Lignes**: 3,838 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### SPY
- **Fichier**: `SPY_5Min_20260222_20260324.parquet`
- **Lignes**: 3,838 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### TLT
- **Fichier**: `TLT_5Min_20260222_20260324.parquet`
- **Lignes**: 3,612 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### TSLA
- **Fichier**: `TSLA_5Min_20250926_20260325.parquet`
- **Lignes**: 22,197 | **Jours**: 116

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | PASS | OK |
| yfinance_compare | PASS |  |

### USO
- **Fichier**: `USO_5Min_20260222_20260324.parquet`
- **Lignes**: 3,723 | **Jours**: 20

| Check | Status | Detail |
|-------|--------|--------|
| NaN | PASS | {'open': 0, 'high': 0, 'low': 0, 'close': 0, 'volume': 0} |
| splits | PASS | OK |
| gaps_5pct | FAIL | [{'date': '2026-03-02', 'gap_pct': 7.52}, {'date': '2026-03-09', 'gap_pct': 9... |
| yfinance_compare | FAIL |  |

## Resume

- **PASS**: 30
- **FAIL**: 3
- **SKIP**: 0
- **Score**: 30/33 (91%)

**Conclusion: Des anomalies ont ete detectees. Verifier les tickers en FAIL.**