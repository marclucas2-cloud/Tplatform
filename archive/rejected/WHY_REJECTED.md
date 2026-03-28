# Strategies Rejetees — Archive

Date d'archivage : 27 mars 2026
Motif : Walk-forward rejet, monitoring-only sans perspective live, ou code mort.

## WF-REJECTED (9 strategies)

| Strategie | Fichier | Sharpe Backtest | Sharpe OOS | % OOS Profitable | Motif |
|-----------|---------|:-:|:-:|:-:|-------|
| OpEx Gamma Pin | opex_gamma_pin.py | 10.41 | -3.99 | 0% | Overfitting severe — edge illusoire |
| Mean Reversion V2 | mean_reversion_v2.py | 1.44 | -11.08 | 0% | Overfitting + tue par commissions |
| VWAP Micro-Deviation | vwap_micro_reversion.py | 3.08 | -1.00 | 20% | Overfitting |
| ORB 5-Min V2 | orb_5min_v2.py | 2.28 | -0.96 | 20% | Overfitting |
| Triple EMA Pullback | triple_ema_pullback.py | 1.06 | -0.05 | ratio 0.07 | Quasi-zero edge OOS |
| Overnight Gap Cont. | overnight_gap_continuation.py | 5.22 | -0.85 | ratio 0.21 | Overfitting |
| Crypto-Proxy V2 | crypto_proxy_regime_v2.py | 3.49 | 0.00 | N/A | 11 trades — bruit statistique |
| Gold Fear Gauge | gold_fear_gauge.py | 5.01 | 1.30 | N/A | 16 trades — bruit statistique |
| Crypto Bear Cascade | crypto_bear_cascade.py | 3.95 | -10.78 | N/A | 17 trades — bruit statistique |

## MONITORING-ONLY (3 strategies, 0% allocation)

| Strategie | Localisation | Motif |
|-----------|---------|-------|
| Pairs MU/AMAT | scripts/paper_portfolio.py (embedded) | < 30 trades, pas de perspective live |
| Momentum 25 ETFs | scripts/paper_portfolio.py (embedded) | < 30 trades, pas de perspective live |
| VRP SVXY/SPY/TLT | scripts/paper_portfolio.py (embedded) | < 30 trades, pas de perspective live |

## CODE MORT (1)

| Strategie | Fichier | Motif |
|-----------|---------|-------|
| EU Stoxx Reversion | eu_stoxx_spy_reversion.py | Supprimee — edge mort |

## Lecon

Les strategies avec les Sharpe les plus spectaculaires en backtest (OpEx 10.41, Gap 5.22)
sont les plus severement rejetees en OOS. C'est le signe classique de l'overfitting.
Regle : < 30 trades = bruit statistique. Walk-forward obligatoire.
