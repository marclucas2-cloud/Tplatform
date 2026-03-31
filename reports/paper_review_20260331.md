# REVUE PAPER TRADING — 31 Mars 2026

## Resume executif

- **50 strategies** dans le pipeline (12 VALIDATED, 7 BORDERLINE, 27 REJECTED, 4 CODE)
- **0 fills live** sur la periode (29-31 mars) — worker demarre samedi, pas de marche
- **95 signaux** emis (71 crypto Earn, 23 FX paper, 1 crypto CLOSE)
- **2 jours de data** seulement (worker redeploye samedi 29 mars)
- Allocation : **mal deployee** — 70% du capital dort sur Binance en BEAR

---

## ETAPE 1 — METRIQUES PAR STRATEGIE

### Crypto LIVE (Binance $23,400)

| # | Strategie | Phase | Trades 30j | Sharpe | WR | DD | Signal | Verdict |
|---|-----------|-------|:----------:|:------:|:--:|:--:|--------|:-------:|
| 1 | STRAT-001 BTC/ETH Dual Mom | PAPER | 0 | — | — | — | BEAR regime, attente | KEEP |
| 2 | STRAT-004 Vol Breakout | VALIDATED | 0 | — | — | — | BEAR regime, attente | KEEP |
| 3 | STRAT-005 BTC Dom Rotation | VALIDATED | 0 | — | — | — | BEAR regime, attente | KEEP |
| 4 | STRAT-006 Borrow Carry | VALIDATED | 0 fills | — | — | — | 71 EARN_REBALANCE | WATCH |
| 5 | STRAT-007 Liquidation Mom | VALIDATED | 0 | — | — | — | BEAR regime, attente | KEEP |
| 6 | STRAT-008 Weekend Gap | VALIDATED | 0 | — | — | — | Pas de gap ce weekend | KEEP |
| 7 | STRAT-010 Stablecoin Flow | WF_PENDING | 1 signal | — | — | — | 1x CLOSE BTCUSDT | KEEP |

**Diagnostic crypto** : 0 trades reels. STRAT-006 emet des signaux EARN_REBALANCE toutes les 15min mais aucun fill. Les 6 strats validees attendent un setup BTC hors BEAR — c'est correct mais ca produit $0.

### FX Paper (IBKR port 4003, EUR 1M notional)

| # | Strategie | Phase | Signaux 30j | Sharpe WF | Notional | Verdict |
|---|-----------|-------|:-----------:|:---------:|:--------:|:-------:|
| 8 | FX Carry Vol-Scaled | VALIDATED | 23 signals | 3.59 | $190,946 | PROMOTE |
| 9 | FX Carry Momentum Filter | VALIDATED | 23 signals | 2.17 | $107,130 | PROMOTE |
| 10 | FX G10 Diversified | VALIDATED | 0 | 1.61 | — | KEEP |
| 11 | FX MR Hourly | BORDERLINE | 0 | 0.71 | — | KEEP |

**Diagnostic FX** : Les 2 carry strategies emettent des signaux consistants (4 paires, $190K+$107K notional) mais en mode PAPER-ONLY (pas d'ordres executes). Le WF est solide (Sharpe 3.59 et 2.17). **Ce sont les candidates #1 pour promotion live.**

### EU Paper (IBKR port 4003)

| # | Strategie | Phase | Trades | Sharpe WF | Verdict |
|---|-----------|-------|:------:|:---------:|:-------:|
| 12 | BCE Momentum Drift | VALIDATED | 0 (weekend) | 14.93 | KEEP |
| 13 | Auto Sector German | VALIDATED | 0 (weekend) | 13.43 | KEEP |
| 14 | EU Gap Open | VALIDATED | 0 (weekend) | 8.56 | KEEP |
| 15 | Brent Lag Play | VALIDATED | 0 (weekend) | 4.08 | KEEP |
| 16 | EU Close→US | VALIDATED | 0 (weekend) | 2.43 | KEEP |
| 17 | BCE Press Conference | BORDERLINE | 0 | 0.79 | KEEP |
| 18 | EU Sector Rotation | BORDERLINE | 0 | 0.59 | KEEP |

**Diagnostic EU** : Tout est en weekend/paper. 0 trades. Pas evaluable.

### US Paper (Alpaca $100K)

| # | Strategie | Phase | Trades | Sharpe WF | Verdict |
|---|-----------|-------|:------:|:---------:|:-------:|
| 19 | DoW Seasonal | VALIDATED | 1 (SPY SHORT) | 1.50 | KEEP |
| 20 | Corr Regime Hedge | VALIDATED | 0 | 1.30 | KEEP |
| 21 | VIX Expansion Short | VALIDATED | 0 | 1.80 | KEEP |
| 22 | High-Beta Short | VALIDATED | 0 | 1.00 | KEEP |
| 23 | Late Day MR | BORDERLINE | 0 | 0.60 | KEEP |

**Diagnostic US** : 1 seul trade execute (SPY SHORT le 29 mars). Trop peu pour evaluer.

### Futures Paper (non actif)

| # | Strategie | Phase | Trades | Sharpe WF | Verdict |
|---|-----------|-------|:------:|:---------:|:-------:|
| 24 | MES Trend | BORDERLINE | 0 | 1.46 (daily) | KEEP |
| 25 | MES/MNQ Pairs | **VALIDATED** | 0 | **0.80** | KEEP |
| 26 | MGC VIX Hedge | BORDERLINE | 0 | 0.45 | KEEP |
| 27 | MES Overnight | REJECTED | — | -0.70 | DEMOTE |
| 28 | M2K ORB | REJECTED | — | -0.27 | DEMOTE |
| 29 | MCL Brent Lag | REJECTED | — | -0.70 | DEMOTE |

---

## ETAPE 2 — VERDICTS

### Promotions recommandees

| Strategie | Motif | Action |
|-----------|-------|--------|
| **FX Carry Vol-Scaled** | Sharpe WF 3.59, 688 trades, signaux live actifs | **PROMOTE live 1/16 Kelly** |
| **FX Carry Momentum Filter** | Sharpe WF 2.17, 400 trades, signaux actifs | **PROMOTE live 1/16 Kelly** |

### A garder en paper (donnees insuffisantes)

Toutes les autres strategies : 0-1 trade sur 2 jours. **Revoir dans 2 semaines** quand les marches sont ouverts et qu'on a 10+ jours de data.

### Demotions

| Strategie | Motif | Action |
|-----------|-------|--------|
| MES Overnight | Sharpe -0.70 WF, REJECTED | DEMOTE → archiver |
| M2K ORB | Sharpe -0.27 WF, REJECTED | DEMOTE → archiver |
| MCL Brent Lag | Sharpe -0.70 WF, 0% win, REJECTED | DEMOTE → archiver |

---

## ETAPE 3 — VERIFICATION ALLOCATION

### Allocation actuelle vs cible

| Bucket | Cible | Actuel | Status |
|--------|-------|--------|--------|
| Crypto (Binance) | 40% | **70%** ($23.4K / $33.4K) | **SURPONDERE** |
| FX (IBKR) | 35% | **1.5%** ($500 / $33.4K) | **SOUS-PONDERE critique** |
| US (Alpaca) | 15% | **0%** ($0 live) | **ABSENT** |
| EU (IBKR) | 10% | **0%** (paper only) | Paper OK |
| Cash | 7% | ~0% | Pas de reserve |

**ALLOCATION NON CONFORME.** Le capital est massivement mal deploye.

### Check cross-portfolio

- Exposition combinee : ~0% (rien ne trade activement)
- Correlation inter-portefeuille : N/A (0 positions)
- Kill switches : independants (Binance + IBKR separes) ✓

---

## ETAPE 4 — MATRICE DE MATURITE

| Phase | Critere | Nombre | Strategies |
|-------|---------|:------:|------------|
| CODE | Codee, pas WF | 4 | FX Asian Breakout, FX London Fix, FX Bollinger Squeeze, FX Session Overlap |
| WF_PENDING | WF pas execute | 3 | STRAT-009/010/011/012 crypto |
| PAPER | WF OK, en paper | 19 | 6 crypto + 5 EU + 5 US + 3 FX (hors carry) |
| PROBATION | Live < 30j | 0 | — |
| **VALIDATED** | Live > 30j, KPIs OK | **2** | FX Carry VS, FX Carry Mom (a promouvoir!) |

**Bottleneck : 0 strategies en PROBATION.** Le pipeline est bloque entre PAPER et PROBATION car aucune strat n'a ete promue live malgre des WF excellents.

---

## ETAPE 5 — ACTIONS CONCRETES

### Immediat (cette semaine)

1. **PROMOUVOIR FX Carry Vol-Scaled** → live sur IBKR port 4002, 1/16 Kelly
   - Prerequis : capital IBKR > $5K (transfert Binance → IBKR)
   - Config : `fx_live_sizing.yaml` allocation 15%

2. **PROMOUVOIR FX Carry Momentum Filter** → live sur IBKR port 4002, 1/16 Kelly
   - Meme prerequis capital
   - Config : allocation 10%

3. **TRANSFERER $10-15K Binance → IBKR**
   - BTC Earn → USDC → retrait EUR → virement IBKR
   - Delai : 5 jours ouvres
   - Objectif : activer FX carry live avec levier

4. **DEMOTE 3 futures** (MES Overnight, M2K ORB, MCL Brent Lag)
   - Archiver dans `archive/demoted/`
   - Retirer des configs

### Semaine 2

5. **Deposer $5K sur Alpaca** pour activer US strats live
6. **Revoir les 19 strats paper** avec 10+ jours de donnees marche

### Mois 1

7. **Lancer WF sur 4 strats FX CODE** (Asian Breakout, London Fix, Bollinger, Session Overlap)
8. **Objectif PROBATION** : 4 strats (2 FX carry live + 2 US live)

---

## RESUME

```
REVUE PAPER TRADING — 31 Mars 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
50 strategies analysees
 2 a promouvoir (FX Carry VS + Mom) — BLOQUE par capital IBKR insuffisant
 3 a retirer (3 futures REJECTED)
19 a garder en paper (donnees insuffisantes, 2 jours)
 4 en CODE (FX intraday, pas encore WF)

PROBLEME #1 : Capital mal deploye (70% Binance, 1.5% IBKR)
PROBLEME #2 : Pipeline bloquee (0 strats en PROBATION)
PROBLEME #3 : 0 fills sur 2 jours (weekend + regime BEAR crypto)

ACTION PRIORITAIRE : Transferer $15K Binance → IBKR pour debloquer FX carry live
ROC ESTIME APRES ACTION : +1.9% → +10-15% annuel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
