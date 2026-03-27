# Plan de Scaling V2 — Trading Platform

> Mise a jour : 2026-03-27
> Remplace : docs/scaling_plan.md (V1)
> Statut : Phase Paper — 14 strategies actives, capital simule $100K

---

## Vue d'ensemble

| Niveau | Capital | Sizing | Strategies | Delai min |
|--------|---------|--------|-----------|-----------|
| **Paper** | $100K simule | Full (simulation) | 14 actives | J+0 |
| **L1** | $25K | Quart-Kelly | 7 validees (Tier S+A) | J+60 |
| **L2** | $50K | Half-Kelly | +3 borderline (Tier B) | J+120 |
| **L3** | $100K | Full-Kelly | +strategies event-driven | J+240 |

---

## Niveau L1 : $25K — Quart-Kelly

### Capital et sizing

- **Capital total** : $25,000
- **Sizing** : quart-Kelly (Kelly / 4)
  - Risque par trade : ~0.5-1% du capital ($125-250)
  - Position max : 10% du capital ($2,500)
- **Cash reserve** : 30% minimum ($7,500)
- **Strategies actives** : 7 (les plus robustes uniquement)

### Strategies deployees

| # | Strategie | Sharpe paper | Tier | Allocation L1 |
|---|-----------|-------------|------|---------------|
| 1 | OpEx Gamma Pin | 10.41 | S | 15% |
| 2 | Overnight Gap Continuation | 5.22 | S | 12% |
| 3 | Gold Fear Gauge | 5.01 | S | 12% |
| 4 | Crypto-Proxy Regime V2 | 3.49 | A | 10% |
| 5 | Day-of-Week Seasonal | 3.42 | A | 8% |
| 6 | VWAP Micro-Deviation | 3.08 | A | 8% |
| 7 | Momentum 25 ETFs | N/A (mensuel) | A | 5% |
| | **Cash reserve** | | | **30%** |

### Conditions d'entree

Voir docs/live_checklist.md (toutes les conditions doivent etre remplies).

### Conditions de revert vers Paper

- Drawdown > 5% ($1,250) sur 7 jours glissants
- Sharpe 30j < 0.5
- 3+ strategies avec kill switch declenche
- Bug d'execution detecte (ordre non conforme)
- Reconciliation avec divergence > $100

**Procedure de revert** : fermer toutes les positions, basculer PAPER_TRADING=true, notifier via Telegram.

---

## Niveau L2 : $50K — Half-Kelly

### Capital et sizing

- **Capital total** : $50,000
- **Sizing** : half-Kelly (Kelly / 2)
  - Risque par trade : ~1-2% du capital ($500-1,000)
  - Position max : 10% du capital ($5,000)
- **Cash reserve** : 25% minimum ($12,500)
- **Strategies actives** : 10

### Strategies ajoutees (borderline)

| # | Strategie | Sharpe paper | Tier | Allocation L2 |
|---|-----------|-------------|------|---------------|
| 8 | ORB 5-Min V2 | 2.28 | B | 6% |
| 9 | Mean Reversion V2 | 1.44 | B | 5% |
| 10 | Pairs MU/AMAT | N/A (daily) | B | 4% |

### Ajustements vs L1

- **Market impact model active** : surveiller le slippage reel vs estime
- **Crypto-Proxy** : plafonner MARA/RIOT a 3% max, privilegier COIN
- **ORB 5-Min** : limiter aux tickers avec ADV > $100M
- **Short interest data** integre (FINRA bi-mensuel)

### Conditions de passage L1 -> L2

- [ ] Sharpe 90j > 1.0 en conditions live L1
- [ ] Max drawdown < 4% en live
- [ ] Market impact reel < 50% d'ecart vs modele
- [ ] >= 500 trades live executes
- [ ] Fill rate > 99%
- [ ] 90 jours minimum en L1

### Conditions de revert vers L1

- Drawdown > 4% ($2,000) sur 7 jours
- Sharpe 60j < 0.8
- Market impact reel > 2x le modele
- Reduire le capital a $25K, retirer les strategies borderline

---

## Niveau L3 : $100K — Full-Kelly

### Capital et sizing

- **Capital total** : $100,000
- **Sizing** : full-Kelly
  - Risque par trade : ~2-4% du capital ($2,000-4,000)
  - Position max : 10% du capital ($10,000)
- **Cash reserve** : 20% minimum ($20,000)
- **Strategies actives** : 14+ (toutes les strategies validees)

### Strategies ajoutees

| # | Strategie | Type | Prerequis |
|---|-----------|------|-----------|
| 11 | Correlation Regime Hedge | Mean reversion | Walk-forward 60% OOS |
| 12 | Triple EMA Pullback | Momentum | Walk-forward 60% OOS |
| 13 | Late Day Mean Reversion | Mean reversion | Walk-forward 60% OOS |
| 14 | VRP SVXY/SPY/TLT | Monthly | 6 mois paper |
| 15+ | Strategies event-driven (FOMC, CPI) | Event | Backtest 5 ans + paper 3 mois |
| 16+ | Covered calls overlay | Options | IBKR options live |

### Ajustements vs L2

- **ML signal filter active** (si >= 200 trades/strategie)
- **Multi-broker** : Alpaca intraday + IBKR daily/options
- **Regime detector HMM** : allocation regime-conditional active
- **Correlation-aware sizing** : reduction automatique si cluster > 3 positions correlees
- **Worker redondant** : backup sur VPS Hetzner

### Conditions de passage L2 -> L3

- [ ] Sharpe 180j > 1.0 en conditions live L2
- [ ] Max drawdown < 3.5% en live
- [ ] >= 1,000 trades live executes
- [ ] ML filter entraine sur au moins 3 strategies
- [ ] Audit CRO score >= 9/10
- [ ] 120 jours minimum en L2

### Conditions de revert vers L2

- Drawdown > 3.5% ($3,500) sur 7 jours
- Sharpe 90j < 0.8
- Alpha decay detecte sur > 3 strategies
- Reduire le capital a $50K, desactiver les strategies nouvelles

---

## Metriques de suivi par niveau

| Metrique | L1 | L2 | L3 |
|----------|----|----|-----|
| Sharpe min rolling | 30j > 0.5 | 60j > 0.8 | 90j > 0.8 |
| Max DD seuil revert | 5% | 4% | 3.5% |
| Fill rate min | 95% | 99% | 99.5% |
| Slippage max | 2x modele | 1.5x modele | 1.2x modele |
| Frequence review | Quotidienne | Hebdomadaire | Hebdomadaire |
| Audit CRO | Mensuel | Mensuel | Bimestriel |

---

## Calendrier previsionnel

```
Mars 2026     : Paper trading actif (14 strategies)
Mai 2026      : J+60 — Evaluation Live L1 (si checklist OK)
Septembre 2026: J+120 — Evaluation Live L2
Janvier 2027  : J+240 — Evaluation Live L3
```

**Important** : ces dates sont indicatives. Le passage est conditionne aux criteres objectifs, pas au calendrier. Si les conditions ne sont pas remplies, on reste au niveau actuel.
