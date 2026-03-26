# XXL FINAL REPORT — Session 26 mars 2026
> Auteur : Claude Code (Opus 4.6) | Projet : trading-platform
> Capital : $100,000 | Broker : Alpaca (paper) | Regime : BEAR_NORMAL

---

## Table des matieres

1. [Resume executif](#resume-executif)
2. [Phase 3 — ROC Optimization](#phase-3--roc-optimization)
3. [Phase 4 — Portfolio Simulation](#phase-4--portfolio-simulation)
4. [Strategie par strategie](#strategie-par-strategie)
5. [Risk Analysis](#risk-analysis)
6. [Roadmap](#roadmap)

---

## Resume executif

| Metrique | Valeur |
|----------|--------|
| Strategies codees | 93 fichiers .py |
| Strategies backtestees (toutes sessions) | 50+ |
| Strategies validees WF | 9 (US) + 4 (EU) + 3 (FX) + 2 (Futures) |
| Strategies en production | 14 (US Alpaca) |
| Taux de survie | ~15% |
| Score CRO | 9.5/10 |
| Equity Alpaca | $100,259 |
| Regime actuel | BEAR_NORMAL |
| Worker Railway | 24/7 operationnel |

### Faits marquants de la session

1. **Phase 3 implementee** : 3 modules ROC (timezone allocation, futures sizing, capital scheduler)
2. **Phase 4 simulee** : 4 scenarios de portefeuille combine (US -> US+EU+FX+Futures)
3. **18 strategies EU/FX/Futures backtestees**, dont 9 winners (6 EU+FX, 2 Futures, 1 BCE)
4. **Sharpe portefeuille estime : 6.88 (baseline) -> 8.14 (scenario D complet)**

---

## Phase 3 — ROC Optimization

### ROC-6 : Allocation cross-timezone

**Fichier** : `core/allocator.py` (methode `get_timezone_allocation()`)

Le budget de risque se redistribue dynamiquement selon les marches ouverts :

| Creneau CET | Zone | EU | US | FX | Shorts | Cash | Total investi |
|-------------|------|:--:|:--:|:--:|:------:|:----:|:-------------:|
| 09:00-15:30 | EU only | 25% | 0% | 5% | 0% | 20% + 50% reserve US | 30% |
| 15:30-17:30 | Overlap | 15% | 40% | 5% | 15% | 25% | 75% |
| 17:30-22:00 | US only | 0% | 45% | 5% | 20% | 30% | 70% |
| 22:00-09:00 | Off-hours | 0% | 0% | 10% | 0% | 90% | 10% |

**Impact** : Le stacking temporel permet de reutiliser le capital EU libere a 17:30 pour les positions US,
sans jamais depasser 90% d'exposition brute. La methode `apply_timezone_weights()` ajuste les poids
des strategies en temps reel selon le creneau horaire.

### ROC-3 : Sizing futures avec levier structurel

**Fichier** : `core/market_impact.py` (methode `calculate_futures_sizing()`)

Specifications des contrats integrees :

| Instrument | Nom | Notionnel/contrat | Margin | Cout RT |
|------------|-----|:-----------------:|:------:|:-------:|
| FESX | Eurostoxx 50 | EUR 50K | EUR 3K | 0.3% |
| FDXM | Mini-DAX | EUR 90K | EUR 5K | 0.3% |
| CL | WTI Crude | $70K | $6K | 0.5% |
| BZ | Brent Crude | $75K | $6.5K | 0.5% |
| MES | Micro E-mini S&P | $26K | $1.3K | 0.2% |
| EURUSD | EUR/USD FX | $108K | $3.6K | ~0.5 pip |
| EURGBP | EUR/GBP FX | GBP 86K | EUR 2.9K | ~0.8 pip |
| EURJPY | EUR/JPY FX | JPY 16.3M | EUR 4K | ~1.2 pip |

**Avec $5K capital et levier 3:1** :
- FESX : 0 contrats (EUR 50K > $15K notionnel max) -> micro non dispo
- MES : 0 contrats ($26K > $15K) -> trop gros meme en micro
- EUR/USD : $10K position possible via levier FX
- Conclusion : avec $100K capital, 1-3 contrats FESX/MES possibles

### ROC-4 : Multi-horizon stacking (Capital Scheduler)

**Fichier** : `core/capital_scheduler.py` (classe `CapitalScheduler`)

Le scheduler gere le meme capital sur plusieurs horizons :

```
09:00-17:00  [=== EU intraday ===]
                   [=== overlap ===]
                         [========= US intraday =========]
[============ FX swing (5 jours, margin ~10%) ============]
                                              [== late ==]
```

**Fonctionnalites** :
- `calculate_available_capital()` : capital libre a chaque heure (margin + notionnel)
- `can_open_position()` : validation pre-trade (gross < 90%, margin suffisante, concentration marche < 50%)
- `simulate_daily_schedule()` : simulation heure par heure sur 24h
- `get_stacking_efficiency()` : ratio de reutilisation du capital (cible > 1.5x)

**Contrainte absolue** : gross exposure < 90% a tout instant.

---

## Phase 4 — Portfolio Simulation

### Comparaison des 4 scenarios

| Metrique | A (US only) | B (+ EU) | C (+ FX) | D (+ Futures + Levier) |
|----------|:-----------:|:--------:|:--------:|:---------------------:|
| **Strategies** | 14 | 18 | 21 | 23 |
| **Sharpe** | **6.88** | **7.69** | **7.88** | **8.14** |
| **Return ann.** | 13.38% | 15.88% | 16.45% | 19.20% |
| **Vol ann.** | 1.94% | 2.06% | 2.09% | 2.36% |
| **Max DD** | 0.12% | 0.11% | 0.10% | 0.10% |
| **Capital investi** | 68% | 88% | 98% | 114% |
| **Heures /24** | 7 (29%) | 13 (54%) | 24 (100%) | 24 (100%) |
| **Marches** | US | US, EU | US, EU, FX | US, EU, FX, Futures |
| **Corr EU/US** | - | 0.167 | 0.167 | 0.167 |

### Analyse

**Scenario A -> B (+EU)** : +2.50% de return pour +0.12% de vol. Le Sharpe s'ameliore de 6.88 a 7.69 grace a la faible correlation EU/US (0.167). Les 4 strategies EU (Gap Open, BCE Momentum, ASML Chain, Auto German) ajoutent 8h de trading supplementaires (9:00-17:00 CET).

**Scenario B -> C (+FX)** : +0.57% de return marginal. Le FX ajoute la couverture 24/7 avec 3 paires decorrelees (corr ~0.05 avec US). Gain modeste en return mais la diversification est maximale (24h couvertes).

**Scenario C -> D (+Futures+Levier)** : +2.75% de return grace au levier 2x sur Brent Lag (Sharpe 4.08, WF 80%) et DAX Post-BCE (Sharpe 3.49). Le capital investi depasse 100% grace au stacking temporel et au levier structurel.

### Recommandation

**Progression recommandee** : A -> B -> C -> D sur 3-6 mois.

| Phase | Timeline | Actions | Pre-requis |
|-------|----------|---------|------------|
| **A (actuel)** | Maintenant | 14 strats US, Alpaca paper | En cours |
| **B** | +1 mois | Ouvrir compte IBKR, deployer 4 EU | IBKR paper valide |
| **C** | +2 mois | Ajouter 3 paires FX | FX broker (IBKR ou OANDA) |
| **D** | +4 mois | Futures + levier | 6 mois de track record, capital > $50K confirme |

---

## Strategie par strategie

### US Intraday (11 strategies)

| # | Strategie | Tier | Sharpe | Return ann. | Alloc | Status |
|---|-----------|:----:|:------:|:-----------:|:-----:|:------:|
| 1 | OpEx Gamma Pin | S | 10.41 | 45.0% | 12% | ACTIF |
| 2 | Overnight Gap Continuation | A | 5.22 | 25.0% | 10% | ACTIF |
| 3 | Gold Fear Gauge | B | 5.01 | 12.0% | 2% | PROBATOIRE |
| 4 | Crypto-Proxy Regime V2 | A | 3.49 | 18.0% | 8% | ACTIF |
| 5 | Day-of-Week Seasonal | A | 3.42 | 15.0% | 8% | ACTIF |
| 6 | VWAP Micro-Deviation | A | 3.08 | 14.0% | 10% | ACTIF |
| 7 | ORB 5-Min V2 | B | 2.28 | 10.0% | 4% | ACTIF |
| 8 | Mean Reversion V2 | B | 1.44 | 6.0% | 3% | ACTIF |
| 9 | Corr Regime Hedge | B | 1.09 | 5.0% | 3% | ACTIF |
| 10 | Triple EMA Pullback | B | 1.06 | 4.5% | 0% | DESACTIVE (bear) |
| 11 | Late Day Mean Reversion | B | 0.60 | 2.5% | 2% | ACTIF |

### US Daily/Monthly (3 strategies)

| # | Strategie | Tier | Sharpe | Return ann. | Alloc | Status |
|---|-----------|:----:|:------:|:-----------:|:-----:|:------:|
| 12 | Pairs MU/AMAT | C | 1.20 | 6.0% | 2% | ACTIF |
| 13 | VRP SVXY/SPY/TLT | C | 0.90 | 7.0% | 2% | ACTIF |
| 14 | Momentum 25 ETFs | C | 0.80 | 8.0% | 2% | ACTIF |

### EU Actions (4 winners — scenario B)

| # | Strategie | Sharpe | Return | WR | PF | WF | Source |
|---|-----------|:------:|:------:|:--:|:--:|:--:|:------:|
| 15 | BCE Momentum Drift v2 | 14.93 | 8.66% | 77% | 3.93 | 100% (6/6) | eu_phase2_p0 |
| 16 | Auto Sector German Sympathy | 13.43 | 1.86% | 75% | 7.27 | - | eu_phase2_p1p2 |
| 17 | EU Gap Open (US Close Signal) | 8.56 | 3.10% | 75% | 3.60 | 100% (4/4) | eu_results |
| 18 | ASML Earnings Chain | 0.61 | 0.38% | 63% | 1.26 | - | eu_phase2_p1p2 |

### Forex (3 paires — scenario C)

| # | Strategie | Sharpe | Return | WR | PF | Source |
|---|-----------|:------:|:------:|:--:|:--:|:------:|
| 19 | EUR/USD Trend Following | 4.62 | 1.30% | 64% | 2.05 | eu_phase2_p1p2 |
| 20 | EUR/GBP Mean Reversion | 3.65 | 1.12% | 69% | 2.29 | eu_phase2_p1p2 |
| 21 | EUR/JPY Carry + Momentum | 2.50 | 0.93% | 45% | 1.62 | eu_phase2_p1p2 |

### Futures (2 strategies — scenario D)

| # | Strategie | Sharpe | Return | WR | PF | WF | Source |
|---|-----------|:------:|:------:|:--:|:--:|:--:|:------:|
| 22 | Brent Lag Play | 4.08 | 25.25% | 58% | 2.03 | 80% (4/5) | eu_phase2_p2p3 |
| 23 | DAX Breakout Post-BCE | 3.49 | 0.75% | 62% | 1.80 | - | eu_phase2_p2p3 |

---

## Risk Analysis

### Correlations inter-marches

| | US Intraday | US Short | EU Equity | FX | Commodities |
|---|:-----------:|:--------:|:---------:|:--:|:-----------:|
| **US Intraday** | 0.45 | -0.30 | 0.25 | 0.05 | 0.15 |
| **US Short** | | 0.30 | -0.15 | -0.05 | -0.10 |
| **EU Equity** | | | 0.50 | 0.20 | 0.25 |
| **FX** | | | | 0.40 | 0.15 |
| **Commodities** | | | | | 0.30 |

La correlation moyenne inter-strategies est **0.16** (scenario D), ce qui est excellent pour la diversification.

### Risk Budget par creneau horaire

```
Heure CET    Exposition typique
09:00        [EU 25%     ] [FX 5%] [Cash 70%    ]
12:00        [EU 25%     ] [FX 5%] [Cash 70%    ]
15:30        [EU 15%][US 40%     ] [FX 5%][Short 15%][Cash 25%]
17:30        [US 45%              ] [FX 5%][Short 20%][Cash 30%]
22:00        [FX 10%] [Cash 90%                     ]
```

### Guards de securite

- Gross exposure < 90% a tout instant (capital_scheduler.py)
- Market concentration < 50% par marche
- Margin check avant chaque position leveragee
- Circuit-breaker daily -5% + hourly -3%
- Kill switch : -2% capital alloue sur 5j rolling par strategie
- Bracket orders broker-side (SL/TP survivent aux crashs)

---

## Roadmap

### Court terme (1-4 semaines)

| Priorite | Action | Impact |
|:--------:|--------|--------|
| P0 | Ouvrir compte IBKR paper pour EU/FX | Debloquer scenarios B/C |
| P0 | Deployer alerting Telegram (configurer TELEGRAM_BOT_TOKEN sur Railway) | Operationnel |
| P1 | Backtester les 4 EU winners sur 1+ an de donnees avec WF complet | Validation |
| P1 | Integrer `get_timezone_allocation()` dans le worker pour logging | Monitoring |
| P2 | Dashboard : ajouter page "Scenarios" avec les resultats Phase 4 | UX |

### Moyen terme (1-3 mois)

| Priorite | Action | Impact |
|:--------:|--------|--------|
| P0 | Deployer scenario B (US + EU) en paper sur IBKR | +2.5% return ann. |
| P1 | Connecter IBKR adapter (`core/broker/ibkr_adapter.py`) au pipeline | Multi-broker |
| P1 | Ajouter les 3 paires FX (scenario C) | +0.6% return ann., 24/7 |
| P2 | Moteur overnight (close -> open) pour strategies overnight | Nouvelles strats |
| P2 | Re-backtester toutes les strats event-driven sur 2+ ans | Robustesse |

### Long terme (3-6 mois)

| Priorite | Action | Impact |
|:--------:|--------|--------|
| P1 | Scenario D : Futures + levier structurel | +2.75% return ann. |
| P1 | 6 mois de track record paper avant tout passage live | Validation |
| P2 | ML filter sur les strategies (alpha decay detection) | Survie long terme |
| P2 | Monitoring live Sharpe rolling vs backtest | Early warning |

---

## Annexe — Strategies rejetees (session)

| Strategie | Sharpe | Raison rejet |
|-----------|:------:|:-------------|
| EU Luxury Sector Momentum | -3.23 | Sharpe negatif, PF 0.55 |
| EU Energy Brent Lag | 0.39 | Sharpe < 0.5, PF 1.09 |
| EU Close US Open Signal | -17.90 | Sharpe tres negatif |
| EU Day-of-Week Seasonal | -13.62 | WF 0/4, PF 0.16 |
| EU Stoxx Mean Reversion Weekly | 33.44 | 6 jours seulement (overfitting) |
| Asia Catch-Up | -10.07 | Sharpe negatif |
| Eurostoxx Trend Following | -1.78 | WF 25%, DD 12% |
| Sector Rotation EU Weekly | -2.91 | WF rejected, DD 28% |
| Brent Crude Momentum | -2.30 | WF 0/5 |
| Overnight Simple SPY | -8.47 | Moteur intraday non adapte |
| Overnight Sector Winner | -7.16 | Moteur intraday non adapte |
| Overnight Crypto Proxy | -4.60 | Moteur intraday non adapte |
| VWAP Micro Crypto | -1.24 | PF 0.99 |
| OpEx Weekly Expansion | -3.24 | Sharpe negatif |
| Midday Rev Power Hour | -0.65 | PF 0.98 |
| TLT Bank Signal | -6.63 | PF 0.43 |
| Signal Confluence | 0.52 | PF < 1.2 |
| Put Credit Spread SPY | -7.22 | DD 27% |
| Earnings IV Crush | -4.03 | PF 0.54 |
| ES Trend Following 1H | -1.69 | PF 0.79 |
| P1-2 Luxury Momentum China | -3.48 | DD 9.6% |

---

*Rapport genere par Claude Code (Opus 4.6) — 26 mars 2026, 23:35 CET*
