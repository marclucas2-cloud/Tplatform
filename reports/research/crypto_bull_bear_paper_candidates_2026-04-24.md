# Crypto Bull/Bear Paper Candidates — 2026-04-24

## Truth Snapshot

- Local pytest: `3873 passed, 1 skipped`.
- Local `runtime_audit --strict`: expected local FAIL because `data/state/ibkr_futures/equity_state.json` is absent and several futures parquets are stale.
- Current crypto canonical snapshot from runtime audit: `btc_asia_mes_leadlag_q80_v80_long_only` ACTIVE, `alt_rel_strength_14_60_7` READY, `btc_dominance_rotation_v2` DISABLED, several historical crypto sleeves archived/rejected.
- This batch is strictly non-prod: no runtime wiring, no registry edits, no whitelist edits, no VPS deploy.

## Skills Used

- `discover`: search → filter → validation pipeline.
- `crypto`: Binance France constraints, spot/margin only, perp data read-only.
- `bt`: anti-lookahead, realistic costs, walk-forward discipline.
- `qr`: bull/bear split, robustness, correlation, anti-overfit.
- `risk`: DD filters, bootstrap DD probability, desk-level viability.
- `review`: keep outputs non-prod and testable.
- `exec`: tradability checks, but no runtime implementation in this mission.

## Bull / Bear Definition

- `bull` = BTC daily close > BTC 200-day SMA.
- `bear` = BTC daily close <= BTC 200-day SMA.
- The same regime definition is reused across every candidate, including ETH and alt sleeves.

## Data Used

- Research Binance daily cache: `C:\Users\barqu\trading-platform\data\research\crypto_daily_cache_2026_04_24.parquet` with 10 symbols from 2020-01-01 onward, common non-NaN range starting 2020-09-22.
- Existing repo data read-only: BTC/ETH/BNB/SOL long daily files, BTC/ETH 4h bars, BTC/ETH funding daily aggregates.
- Costs: 13 bps per side (`0.26%` round trip), plus conservative short borrow proxies for short-capable variants.

## Initial Idea Universe

| Strategy ID | Family | Initial verdict |
|---|---|---|
| `alt_beta_10_120_2` | cross-sectional / beta-adjusted LS | RESEARCH_ONLY |
| `alt_beta_20_60_3` | cross-sectional / beta-adjusted LS | REJECTED |
| `alt_longonly_top1_cash_14_5` | cross-sectional / long-only alt rotation | REJECTED |
| `alt_longonly_top1_cash_40_10` | cross-sectional / long-only alt rotation | REJECTED |
| `core_ls_20_1` | core majors / relative value | REJECTED |
| `core_ls_40_2` | core majors / relative value | RESEARCH_ONLY |
| `core_top1_cash_60` | core majors / trend rotation | REJECTED |
| `btc_range_longonly_30` | mean reversion / BTC 4h | RESEARCH_ONLY |
| `btc_range_regime_30` | mean reversion / BTC 4h | PAPER_READY |
| `eth_range_longonly_20` | mean reversion / ETH 4h | PAPER_READY |
| `eth_range_regime_30` | mean reversion / ETH 4h | PAPER_READY |
| `btc_funding_hybridtrend_1_5_3` | funding + trend hybrid | RESEARCH_ONLY |
| `btc_funding_hybridtrend_2_0_3` | funding + trend hybrid | PAPER_READY |
| `eth_funding_hybridtrend_1_5_7` | funding + trend hybrid | RESEARCH_ONLY |
| `eth_funding_hybridtrend_2_5_3` | funding + trend hybrid | REJECTED |
| `btc_weekend_reversal_5_3` | event-driven / weekend reversal | RESEARCH_ONLY |
| `eth_weekend_reversal_bull_3` | event-driven / weekend reversal | PAPER_READY |
| `basis_carry_direct` | funding carry / direct perp basis | IDEA_ONLY |

## Candidate Results

| Candidate | Family | Trades | Sharpe | MaxDD | Bull PnL | Bear PnL | WF | MC P(DD<-25%) | Score | Verdict |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| `eth_range_longonly_20` | mean reversion / ETH 4h | 42 | +1.03 | -12.1% | $+2,982 | $+1,603 | 3/5 | 0.3% | +0.343 | **PAPER_READY** |
| `btc_range_regime_30` | mean reversion / BTC 4h | 32 | +0.90 | -4.9% | $+1,081 | $+1,714 | 4/5 | 0.2% | +0.291 | **PAPER_READY** |
| `eth_range_regime_30` | mean reversion / ETH 4h | 30 | +1.03 | -6.9% | $+2,408 | $+1,633 | 4/5 | 0.3% | +0.277 | **PAPER_READY** |
| `btc_funding_hybridtrend_2_0_3` | funding + trend hybrid | 108 | +0.58 | -15.6% | $+9,285 | $+735 | 3/5 | 25.0% | +0.211 | **PAPER_READY** |
| `eth_weekend_reversal_bull_3` | event-driven / weekend reversal | 22 | +0.75 | -16.3% | $+9,508 | $+3,088 | 4/5 | 6.5% | +0.107 | **PAPER_READY** |
| `alt_beta_10_120_2` | cross-sectional / beta-adjusted LS | 274 | +0.97 | -37.7% | $+41,434 | $+3,765 | 4/5 | 80.1% | +0.762 | **RESEARCH_ONLY** |
| `core_top1_cash_60` | core majors / trend rotation | 140 | +1.26 | -16.1% | $+76,205 | $-13,035 | 4/5 | 65.1% | +0.379 | **REJECTED** |
| `eth_funding_hybridtrend_1_5_7` | funding + trend hybrid | 215 | +0.88 | -16.0% | $+28,668 | $+3,199 | 3/5 | 48.7% | +0.284 | **RESEARCH_ONLY** |
| `btc_range_longonly_30` | mean reversion / BTC 4h | 30 | +0.81 | -8.7% | $+1,626 | $+1,193 | 3/5 | 0.9% | +0.273 | **RESEARCH_ONLY** |
| `alt_beta_20_60_3` | cross-sectional / beta-adjusted LS | 276 | +0.50 | -100.3% | $+23,645 | $+2,250 | 4/5 | 88.0% | +0.235 | **REJECTED** |
| `btc_funding_hybridtrend_1_5_3` | funding + trend hybrid | 207 | +0.57 | -16.2% | $+10,148 | $+2,010 | 2/5 | 40.9% | +0.192 | **RESEARCH_ONLY** |
| `btc_weekend_reversal_5_3` | event-driven / weekend reversal | 19 | +0.23 | -27.6% | $+1,389 | $+2,127 | 2/5 | 49.1% | +0.134 | **RESEARCH_ONLY** |
| `eth_funding_hybridtrend_2_5_3` | funding + trend hybrid | 85 | +0.42 | -27.1% | $+10,908 | $-2,190 | 2/5 | 52.9% | +0.109 | **REJECTED** |
| `core_ls_40_2` | core majors / relative value | 273 | +0.66 | -21.4% | $+29,404 | $-93 | 4/5 | 86.2% | +0.070 | **RESEARCH_ONLY** |
| `core_ls_20_1` | core majors / relative value | 655 | +0.61 | -33.0% | $+51,152 | $-8,087 | 4/5 | 96.1% | -0.267 | **REJECTED** |
| `alt_longonly_top1_cash_14_5` | cross-sectional / long-only alt rotation | 153 | +0.72 | -23.5% | $+60,427 | $-13,074 | 2/5 | 93.8% | -0.368 | **REJECTED** |
| `alt_longonly_top1_cash_40_10` | cross-sectional / long-only alt rotation | 76 | +0.47 | -72.1% | $+44,655 | $-15,186 | 2/5 | 97.5% | -0.436 | **REJECTED** |

## Final Top

### #1 `eth_range_longonly_20` — PAPER_READY

- Family: mean reversion / ETH 4h
- Notes: ETH low-ADX Bollinger fade, long-only
- Trades: 42
- Standalone: total $+4,585, Sharpe +1.03, MaxDD -12.1%
- Bull: $+2,982, Sharpe +0.71
- Bear: $+1,603, Sharpe +0.56
- WF: 3/5 windows passed
- Desk score: +0.343, corr to portfolio +0.03

### #2 `btc_range_regime_30` — PAPER_READY

- Family: mean reversion / BTC 4h
- Notes: BTC Bollinger fade: long in bull, short in bear
- Trades: 32
- Standalone: total $+2,795, Sharpe +0.90, MaxDD -4.9%
- Bull: $+1,081, Sharpe +0.36
- Bear: $+1,714, Sharpe +0.94
- WF: 4/5 windows passed
- Desk score: +0.291, corr to portfolio +0.01

### #3 `eth_range_regime_30` — PAPER_READY

- Family: mean reversion / ETH 4h
- Notes: ETH Bollinger fade: long in bull, short in bear
- Trades: 30
- Standalone: total $+4,041, Sharpe +1.03, MaxDD -6.9%
- Bull: $+2,408, Sharpe +0.58
- Bear: $+1,633, Sharpe +0.94
- WF: 4/5 windows passed
- Desk score: +0.277, corr to portfolio +0.03

### #4 `btc_funding_hybridtrend_2_0_3` — PAPER_READY

- Family: funding + trend hybrid
- Notes: stricter BTC funding z-score threshold
- Trades: 108
- Standalone: total $+10,020, Sharpe +0.58, MaxDD -15.6%
- Bull: $+9,285, Sharpe +0.75
- Bear: $+735, Sharpe +0.43
- WF: 3/5 windows passed
- Desk score: +0.211, corr to portfolio +0.31

### #5 `eth_weekend_reversal_bull_3` — PAPER_READY

- Family: event-driven / weekend reversal
- Notes: ETH buy after weekend flush only when ETH trend is positive
- Trades: 22
- Standalone: total $+12,596, Sharpe +0.75, MaxDD -16.3%
- Bull: $+9,508, Sharpe +0.93
- Bear: $+3,088, Sharpe +0.49
- WF: 4/5 windows passed
- Desk score: +0.107, corr to portfolio +0.16

## Correlation Matrix — PAPER_READY

|  | eth_range_longonly_20 | btc_range_regime_30 | eth_range_regime_30 | btc_funding_hybridtrend_2_0_3 | eth_weekend_reversal_bull_3 |
| --- | --- | --- | --- | --- | --- |
| eth_range_longonly_20 | +1.00 | +0.01 | +0.39 | -0.01 | -0.00 |
| btc_range_regime_30 | +0.01 | +1.00 | +0.08 | -0.00 | -0.01 |
| eth_range_regime_30 | +0.39 | +0.08 | +1.00 | -0.00 | -0.00 |
| btc_funding_hybridtrend_2_0_3 | -0.01 | -0.00 | -0.00 | +1.00 | +0.03 |
| eth_weekend_reversal_bull_3 | -0.00 | -0.01 | -0.00 | +0.03 | +1.00 |

## Illusions Rejected

- Worst illusion rejected: `alt_beta_20_60_3`. It looked attractive on one dimension, but failed the combined bull/bear + DD + WF bar.
- Best bull-only reject: `core_top1_cash_60` with bull PnL $+76,205 but bear PnL $-13,035.
- Direct basis carry remains rejected for this mission because it depends on a live perp expression that Binance France cannot execute directly.
- Extended 2020+ alt-universe dispersion degraded much more than the 2024-2026 local snapshot suggested; that family is weaker than its recent short-sample optics imply.

## Executive Summary

- Ideas generated: 18
- Seriously tested: 17
- Rejected: 6
- Research-only: 6
- PAPER_READY: 5
- Best overall candidate: `eth_range_longonly_20`
- Best bear-resistant candidate: `alt_beta_10_120_2` with bear PnL $+3,765
- Worst illusion rejected: `alt_beta_20_60_3`

## Honest Conclusion

The batch found five PAPER_READY crypto sleeves without touching production files. They are not all the same motor, and each one cleared separate bull and bear checks.