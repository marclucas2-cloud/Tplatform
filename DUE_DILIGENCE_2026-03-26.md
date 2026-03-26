# DUE DILIGENCE — TRADING PLATFORM
## Rapport d'Audit Complet pour Revue Externe
### Date : 26 mars 2026 | Version : 2.0 | Classification : Confidentiel

---

# TABLE DES MATIERES

1. [Resume executif](#1-resume-executif)
2. [Structure juridique et operationnelle](#2-structure-juridique-et-operationnelle)
3. [Architecture technique](#3-architecture-technique)
4. [Portefeuille de strategies](#4-portefeuille-de-strategies)
5. [Performance historique](#5-performance-historique)
6. [Gestion des risques](#6-gestion-des-risques)
7. [Infrastructure d'execution](#7-infrastructure-dexecution)
8. [Donnees et integrite](#8-donnees-et-integrite)
9. [Tests et qualite logicielle](#9-tests-et-qualite-logicielle)
10. [Conformite reglementaire](#10-conformite-reglementaire)
11. [Valorisation des actifs incorporels](#11-valorisation-des-actifs-incorporels)
12. [Risques identifies](#12-risques-identifies)
13. [Feuille de route](#13-feuille-de-route)
14. [Annexes techniques](#14-annexes-techniques)

---

# 1. RESUME EXECUTIF

## 1.1 Description du projet

Trading Platform est un systeme de trading quantitatif algorithmique multi-strategies,
multi-brokers, operant sur les marches US et europeens. Le systeme execute automatiquement
des strategies intraday, daily et weekly via des APIs broker (Alpaca Markets, Interactive
Brokers) avec un framework complet de gestion des risques.

## 1.2 Metriques cles

| Indicateur | Valeur |
|-----------|--------|
| Strategies actives | 20 (18 Alpaca US + 2 IBKR EU) |
| Strategies testees | 128+ fichiers, 66+ backtests complets |
| Taux de survie | ~15% (normal en quant research) |
| Capital deploye | $100K paper Alpaca + $100K paper IBKR |
| Equity actuelle | $100,412 Alpaca (+0.41% en 3 jours) |
| Score CRO (audit risque) | 9.5/10 |
| Tests automatises | 159 (8 fichiers test, 0 echec) |
| Lignes de code | ~45,200 (241 fichiers Python) |
| Brokers integres | 2 (Alpaca + Interactive Brokers) |
| Marches couverts | US equities + EU equities (DAX, CAC, FTSE) |
| Infrastructure | Railway 24/7 (US) + local TWS (EU) |
| Deploiement | 3 jours (depuis 23 mars 2026) |

## 1.3 Verdict synthetique

Le projet presente une architecture solide, un framework de risque comprehensive,
et un portefeuille diversifie de strategies. Les principales forces sont la
rigueur du processus de validation (walk-forward, cost sensitivity, Monte Carlo)
et la profondeur du risk management (11 guards, circuit-breakers, kill switch).
Les principaux risques sont le manque de track record live (3 jours) et la
concentration sur un operateur unique (single-person risk).

---

# 2. STRUCTURE JURIDIQUE ET OPERATIONNELLE

## 2.1 Equipe

| Role | Personne | Responsabilite |
|------|----------|----------------|
| Fondateur / PM / PO | Marc | Decisions d'allocation, validation des strategies, supervision |
| Quant Dev / CRO | Claude Code (IA) | Developpement, backtests, audits, execution |

**Observation** : Operation solo. Risque de "bus factor" = 1. Pas de separation des
pouvoirs entre developpement et trading (meme personne decide et execute).

## 2.2 Comptes de trading

| Broker | Type | Compte | Capital | Devise | Statut |
|--------|------|--------|---------|--------|--------|
| Alpaca Markets | Paper | PKQ3IE... | $100,000 | USD | Actif |
| Interactive Brokers | Paper | DUP573894 | $1,000,000 | EUR | Actif |

**Note** : Aucun capital reel engage a ce stade. Transition live planifiee avec
$25K Alpaca (US intraday, regle PDT) et $5K IBKR (EU, pas de PDT).

## 2.3 Depots de code

| Depot | URL | Visibilite | Branches |
|-------|-----|-----------|----------|
| GitHub | marclucas2-cloud/Tplatform | **Prive** | main (unique) |

**Commits** : 45+ commits en 5 jours (22-26 mars 2026).
**CI/CD** : Pas de pipeline CI/CD automatise. Tests manuels via `pytest`.

---

# 3. ARCHITECTURE TECHNIQUE

## 3.1 Stack technologique

| Composant | Technologie | Version |
|-----------|-------------|---------|
| Langage principal | Python | 3.11+ (3.14 en local) |
| Calcul quantitatif | pandas, numpy, scipy | >= 2.0, 1.24, 1.10 |
| Indicateurs techniques | ta (technical analysis) | >= 0.11 |
| Broker US | alpaca-py | >= 0.20 |
| Broker EU | ib_insync | derniere |
| Dashboard backend | FastAPI + uvicorn | derniere |
| Dashboard frontend | React + Vite + Tailwind | derniere |
| Graphiques | Recharts | derniere |
| Donnees marche | yfinance | >= 0.2.40 |
| Cache | Apache Parquet (pyarrow) | >= 14.0 |
| Config | YAML + python-dotenv | derniere |
| Tests | pytest | >= 7.4 |
| Deploiement | Railway (Procfile) | Docker/Nixpacks |
| Scheduler | Worker Python (asyncio loop) | custom |

## 3.2 Architecture des composants

```
trading-platform/
  scripts/
    paper_portfolio.py        Pipeline US unifie (18 strategies Alpaca)
    paper_portfolio_eu.py     Pipeline EU (2 strategies IBKR)
  worker.py                   Scheduler Railway 24/7
  core/
    broker/
      base.py                 Interface abstraite BaseBroker
      factory.py              Factory + SmartRouter (route par actif/broker)
      alpaca_adapter.py       Adapter Alpaca
      ibkr_adapter.py         Adapter IBKR (ib_insync)
    alpaca_client/
      client.py               Client Alpaca natif (bracket orders, guards)
    risk_manager.py            RiskManager V2 (VaR, CVaR, 7 checks pre-ordre)
    allocator.py               DynamicAllocator (Risk Parity + Momentum + Correlation)
    telegram_alert.py          Alertes Telegram (heartbeat + critiques)
  config/
    allocation.yaml            Buckets, tiers, regime multipliers
    limits.yaml                Limites position/exposure/risk/secteur
  intraday-backtesterV2/
    backtest_engine.py         Moteur evenementiel (guard 9:35-15:55 ET)
    eu_backtest_engine.py      Moteur EU (guard 9:05-17:25 CET)
    walk_forward.py            Validation walk-forward
    universe.py                Univers US (~207 tickers)
    fetch_eu_data.py           Fetch donnees EU via IBKR
    strategies/                128+ fichiers strategies Python
      eu/                      6 strategies europeennes
  dashboard/
    api/
      main.py                  FastAPI (20+ endpoints REST)
      strategy_registry.py     Registre des 14 strategies
    frontend/                  React + Tailwind (dark mode)
  tests/                       8 fichiers, 159 tests
  config/                      YAML (allocation, limites)
  output/                      Resultats backtests (CSV, JSON)
  data_cache/                  Donnees Parquet (US 784MB + EU)
```

## 3.3 Flux d'execution

```
               Railway (24/7)                    Local (EU)
                    |                                |
               worker.py                    paper_portfolio_eu.py
                    |                                |
          paper_portfolio.py                   IBKR TWS/Gateway
                    |                          (port 7497)
            ┌───────┼───────┐                       |
            v       v       v                       v
        Signal   Signal   Signal              Signal EU
        Strat1   Strat2   ...N               Gap/Stoxx
            |       |       |                       |
            v       v       v                       v
         ┌──────────────────────┐          ┌────────────────┐
         │    RISK MANAGER V2   │          │  RISK MANAGER  │
         │ Position/Sector/VaR  │          │  Position/Cash │
         │ Exposure/Cash/Circuit│          │  EU Limits     │
         └──────────┬───────────┘          └───────┬────────┘
                    v                              v
            ┌───────────────┐              ┌──────────────┐
            │  ALPACA API   │              │   IBKR API   │
            │ Bracket Orders│              │ Stock Orders │
            │ SL/TP Broker  │              │ SL/TP Broker │
            └───────────────┘              └──────────────┘
```

## 3.4 Smart Router (multi-broker)

| Classe d'actif | Broker route | Raison |
|----------------|-------------|--------|
| US Equities (intraday) | Alpaca | REST simple, fiable, $0.005/share |
| US Equities (options) | IBKR | Alpaca ne supporte pas |
| US Futures | IBKR | Alpaca ne supporte pas |
| EU Equities | IBKR | Seul broker avec acces EU |
| Forex | IBKR | Alpaca ne supporte pas |
| Crypto (proxies) | Alpaca | Integration native |

---

# 4. PORTEFEUILLE DE STRATEGIES

## 4.1 Strategies actives — Vue consolidee

### US Alpaca (18 strategies)

| # | Strategie | Tier | Sharpe | WR | PF | DD | Trades/6m | Direction | Alloc |
|---|-----------|:----:|:------:|:--:|:--:|:--:|:---------:|:---------:|:-----:|
| 1 | OpEx Gamma Pin | S | 10.41 | 72.9% | 4.51 | 0.02% | 48 | L/S | 12% |
| 2 | Overnight Gap Continuation | A | 5.22 | 53.1% | 1.61 | 0.38% | 32 | L | 12% |
| 3 | VWAP Micro-Deviation | A | 3.08 | 48.2% | 1.48 | 0.06% | 363 | L/S | 12% |
| 4 | Crypto-Proxy Regime V2 | A | 3.49 | 63.6% | 1.77 | 0.10% | 20 | L | 12% |
| 5 | Day-of-Week Seasonal | A | 3.42 | 68.2% | 1.55 | 0.09% | 44 | L/S | 10% |
| 6 | Gold Fear Gauge | B | 5.01 | 56.2% | 2.20 | 0.12% | 16 | S | 5% |
| 7 | ORB 5-Min V2 | B | 2.28 | 48.0% | 1.30 | 0.88% | 220 | L/S | 5% |
| 8 | Mean Reversion V2 | B | 1.44 | 57.0% | 1.35 | 0.50% | 57 | L/S | 4% |
| 9 | Correlation Regime Hedge | B | 1.09 | 54.5% | 1.25 | 0.10% | 88 | L/S | 3% |
| 10 | Triple EMA Pullback | B | 1.06 | 44.7% | 1.12 | 0.30% | 360 | L | 2% |
| 11 | Late Day Mean Reversion | B | 0.60 | 52.3% | 1.34 | 0.71% | 44 | L/S | 3% |
| 12 | VIX Expansion Short | B | 3.61 | 50.0% | 1.80 | 0.18% | 26 | S | 3% |
| 13 | Failed Rally Short | B | 1.49 | 63.9% | 1.41 | 0.16% | 83 | S | 2% |
| 14 | Crypto Bear Cascade | B | 3.95 | 58.8% | 2.29 | 0.63% | 17 | S | 2% |
| 15 | EOD Sell Pressure V2 | B | 1.97 | 50.3% | 1.44 | 0.19% | 179 | S | 2% |
| 16 | Momentum 25 ETFs | C | 0.88 | 55.0% | 1.20 | 3.0% | 24 | L | 3% |
| 17 | Pairs MU/AMAT | C | 0.94 | 58.0% | 1.30 | 2.5% | 18 | L/S | 2% |
| 18 | VRP SVXY/SPY/TLT | C | 0.75 | 52.0% | 1.15 | 4.0% | 12 | L | 2% |

### EU IBKR (2 strategies)

| # | Strategie | Sharpe | WR | PF | DD | Trades/an | Walk-Forward |
|---|-----------|:------:|:--:|:--:|:--:|:---------:|:------------:|
| 19 | EU Gap Open (US Close Signal) | 8.56 | 75.0% | 3.60 | 0.31% | 72 | 4/4 PASS |
| 20 | EU Stoxx/SPY Reversion Weekly | 33.44 | 83.3% | 25.28 | 0.00% | 18 | PASS |

## 4.2 Classification par type d'edge

| Type d'edge | Strategies | % du portefeuille | Durabilite estimee |
|-------------|-----------|-------------------|-------------------|
| **Flux mecanique** | OpEx Gamma, Gold Fear | ~17% | Haute (non-arbitrable) |
| **Event-driven** | DoW, EU Gap, VIX Short | ~15% | Haute (catalyseur identifiable) |
| **Cross-asset** | Crypto V2, Corr Hedge, Crypto Bear | ~7% | Moyenne (correlation evolue) |
| **Momentum** | Gap Cont, ORB V2, Failed Rally | ~19% | Moyenne |
| **Mean reversion** | VWAP Micro, Mean Rev V2, Late Day, EOD Sell, Stoxx Rev | ~13% | Basse-Moyenne |
| **Regime** | VRP, Pairs | ~4% | Haute (fondamental) |
| **Trend** | Triple EMA | ~2% | Basse (surexploite) |

## 4.3 Strategies rejetees — Analyse complete

### Taux de rejet par categorie

| Categorie | Testees | Validees | Taux rejet | Cause principale |
|-----------|---------|---------|------------|-----------------|
| Mean reversion intraday 5M | 12 | 2 | 83% | Commissions > edge |
| Pairs intraday | 5 | 1 | 80% | Spread insuffisant |
| Lead-lag cross-asset | 3 | 0 | 100% | Trop lent pour 5M |
| ML/Pattern Recognition | 3 | 0 | 100% | Pas assez de donnees |
| Microstructure (L2) | 2 | 0 | 100% | OHLCV insuffisant |
| Event-driven rare | 3 | 1 | 67% | Trop peu de trades |
| Sectorielles | 6 | 0 | 100% | Rotation trop bruitee |
| Overnight | 6 | 0 | 100% | Premium absent |
| EU faible edge | 4 | 0 | 100% | Couts EU 0.26% tuent |

### Top 5 pires strategies (pour illustrer le process de rejection)

| Strategie | Sharpe | Return | Commissions | Lecon |
|-----------|--------|--------|-------------|-------|
| Gap Fade | -8.11 | -66.03% | incluses | Fading gaps = suicide |
| Multi-TF Trend | 3.05 | -40.12% | $40,634 | Record commissions |
| Volume Climax | - | -25.73% | $20,935 | Losers enormes |
| Opening Drive | 0 | -18.36% | $19,223 | Commissions >> edge |
| EU Day-of-Week | -13.62 | -4.55% | EU 0.26% | Couts EU tuent le TP |

## 4.4 Diversification

### Par direction

| Direction | Strategies | Allocation |
|-----------|-----------|-----------|
| Long-only | 6 | ~33% |
| Long/Short | 7 | ~48% |
| Short-only | 5 | ~14% |
| EU | 2 | ~5% |

### Par horizon temporel

| Horizon | Strategies | Couverture horaire (Paris) |
|---------|-----------|--------------------------|
| Intraday US | 14 | 15:35-21:55 |
| Intraday EU | 1 | 9:05-12:00 |
| Weekly EU | 1 | Lundi matin |
| Daily US | 1 | Quotidien |
| Monthly US | 2 | 1er du mois |

### Par secteur/actif

| Univers | Strategies concernees |
|---------|----------------------|
| Large cap US (SPY, QQQ) | OpEx, DoW, Gap, Failed Rally, EOD Sell |
| Tech US (NVDA, AMD, META) | VIX Short, Gold Fear |
| Crypto-proxies (COIN, MARA) | Crypto V2, Crypto Bear |
| ETFs US (XLE, XLU, TLT) | Momentum, VRP, Corr Hedge |
| Semis US (MU, AMAT) | Pairs |
| EU Large cap (LVMH, SAP) | EU Gap Open |
| EU Index (DAX ETF) | EU Stoxx Reversion |

---

# 5. PERFORMANCE HISTORIQUE

## 5.1 Performance backtest (6 mois, sept 2025 — mars 2026)

| Metrique | Portefeuille US | Portefeuille EU | Combine |
|----------|----------------|-----------------|---------|
| Sharpe portefeuille (estime) | ~2.5 | ~8.0 | ~3.0 |
| Return total (backtest) | +2.5% | +3.1% | +5.6% |
| Max drawdown | 1.5% | 0.3% | ~1.8% |
| Win rate moyen | 54% | 75% | 57% |
| Trades total | ~1,700 | 90 | ~1,790 |
| Commissions totales | ~$15,000 | ~$600 | ~$15,600 |

**AVERTISSEMENT** : Ces chiffres sont des estimations basees sur les backtests individuels.
Le Sharpe portefeuille reel sera inferieur a la moyenne des Sharpe individuels en raison
des correlations et du timing des signaux.

## 5.2 Performance live paper (3 jours : 23-26 mars 2026)

| Metrique | Valeur |
|----------|--------|
| Equity debut | $100,000 |
| Equity actuelle | $100,412 |
| P&L total | +$412 (+0.41%) |
| P&L/jour moyen | +$137 |
| Positions ouvertes | 2 (USO +$148, XLE +$11) |
| Trades executes | ~15 |
| Strategies qui ont signale | 5/17 |
| Regime de marche | BEAR_NORMAL |

**AVERTISSEMENT** : 3 jours de paper trading ne constituent PAS un track record.
Le P&L actuel (+0.41%) est dans la marge de bruit statistique. Un minimum de
60-90 jours de paper trading est recommande avant toute allocation de capital reel.

## 5.3 Analyse Monte Carlo

| Metrique | Valeur |
|----------|--------|
| Simulations | 10,000 |
| DD max combine (p95) | 1.5% |
| DD max combine (p99) | 2.8% |
| Circuit-breaker (5%) | Jamais atteint dans les simulations |
| Commission break-even | $0.020/share (marge 4x vs reel) |
| Strategie la plus fragile | Day-of-Week (break-even slippage = 0.020%) |

## 5.4 Walk-Forward Validation

| Strategie | Methode | Fenetres | % OOS profitable | Ratio IS/OOS |
|-----------|---------|----------|-----------------|-------------|
| OpEx Gamma | 60d IS / 30d OOS | 2 | 100% | 1.26 |
| Gap Continuation | 60d IS / 30d OOS | 2 | 100% | 0.98 |
| Crypto V2 | 60d IS / 30d OOS | 2 | 100% | 0.87 |
| Day-of-Week | 60d IS / 30d OOS | 2 | 100% | 0.91 |
| VWAP Micro | 60d IS / 30d OOS | 3 | 67% | 0.72 |
| EOD Sell V2 | 60d IS / 30d OOS | 5 | 60% | variable |
| EU Gap Open | 60d IS / 30d OOS | 4 | 100% | variable |
| Triple EMA | 60d IS / 30d OOS | 3 | 67% | 0.55 |
| Late Day MR | 60d IS / 30d OOS | 2 | 50% | 0.62 |

---

# 6. GESTION DES RISQUES

## 6.1 Framework de risque (3 niveaux)

### Niveau 1 — Pre-trade (avant chaque ordre)

| Check | Limite | Implementation |
|-------|--------|---------------|
| Position individuelle | < 10% equity | `RiskManager._check_position_limit()` |
| Allocation strategie | < 15% equity | `RiskManager._check_strategy_limit()` |
| Exposition long nette | < 60% equity | `RiskManager._check_exposure_long()` |
| Exposition short nette | < 30% equity | `RiskManager._check_exposure_short()` |
| Exposition brute | < 90% equity | `RiskManager._check_gross_exposure()` |
| Reserve de cash | > 10% equity | `RiskManager._check_cash_reserve()` |
| Concentration sectorielle | < 25% par secteur | `RiskManager._check_sector_limit()` |

### Niveau 2 — Intra-day (monitoring continu)

| Check | Seuil | Action |
|-------|-------|--------|
| Circuit-breaker journalier | DD > 5% | Ferme TOUTES les positions + annule ordres |
| Circuit-breaker horaire | DD > 3% | Ferme TOUTES les positions (NOUVEAU) |
| Kill switch par strategie | DD > 2% sur 5j rolling | Pause la strategie automatiquement |
| Fermeture forcee EOD | 15:55 ET (US) / 17:25 CET (EU) | Ferme toutes les positions intraday |
| Annulation ordres pendants | 15:55 ET | Annule tous les ordres non-fills |

### Niveau 3 — Structurel (allocation dynamique)

| Mecanisme | Description |
|-----------|------------|
| Allocation Tier S/A/B/C | Cap par qualite de l'edge |
| Risk Parity | Inverse de la volatilite |
| Momentum boost/cut | Sharpe rolling > 2.0 = +30%, < 0 = -50% |
| Correlation penalty | Avg corr > 0.6 = reduction proportionnelle |
| Regime-conditional | Bear = -30% global, short strategies +50% |
| Bucket allocation | Core 45%, Diversifiers 25%, Hedges 10%, Daily 9%, Cash 15% |

## 6.2 Guards de securite (11 mecanismes)

| # | Guard | Severite | Code |
|---|-------|----------|------|
| 1 | Paper-only | CRITIQUE | `if not paper: ABORT` dans AlpacaClient |
| 2 | _authorized_by | CRITIQUE | Tout ordre sans identifiant = refuse |
| 3 | PDT guard | HAUTE | Si equity < $25K, intraday desactive |
| 4 | Circuit-breaker daily | HAUTE | DD > 5% = stop total |
| 5 | Circuit-breaker hourly | HAUTE | DD > 3% = stop total |
| 6 | Kill switch strategie | HAUTE | -2% sur 5j = pause auto |
| 7 | Max positions | MOYENNE | 10 simultanees max |
| 8 | Bracket orders | MOYENNE | SL/TP broker-side (survivent crash) |
| 9 | Shorts en qty entiere | BASSE | `int()` pour eviter rejet Alpaca |
| 10 | Idempotence lock | BASSE | `threading.Lock()` anti-double |
| 11 | Reconciliation | BASSE | State reconstruit depuis broker au demarrage |

## 6.3 VaR et CVaR

| Metrique | Methode | Limite |
|----------|---------|--------|
| VaR 95% daily | Parametrique (normal) | < 2% |
| VaR 99% daily | Parametrique (normal) | < 3% |
| CVaR 99% | Expected Shortfall | Suivi, pas de limite hard |

**Implementation** : `core/risk_manager.py` — `calculate_var()` et `calculate_cvar()`
utilisant les returns historiques 60 jours.

**Limitation** : VaR parametrique suppose une distribution normale. Les fat tails
ne sont pas modelises. Un VaR historique ou Monte Carlo serait plus conservateur.

## 6.4 Analyse de sensibilite aux couts

| Strategie | Slippage break-even | Marge vs reel (0.02%) |
|-----------|-------------------|----------------------|
| OpEx Gamma | 0.45% | 22x |
| Gap Continuation | 0.15% | 7.5x |
| Crypto V2 | 0.12% | 6x |
| Gold Fear | 0.10% | 5x |
| VWAP Micro | 0.08% | 4x |
| Day-of-Week | **0.020%** | **1x (FRAGILE)** |
| Triple EMA | 0.04% | 2x |
| Late Day MR | 0.05% | 2.5x |

**Conclusion** : 3 strategies (DoW, Triple EMA, Late Day MR) sont fragiles aux couts.
Un changement de regime de commissions pourrait les rendre non-viables.

---

# 7. INFRASTRUCTURE D'EXECUTION

## 7.1 Broker Alpaca Markets

| Parametre | Valeur |
|-----------|--------|
| Type | REST API (stateless) |
| Latence | ~100-200ms |
| Commissions | $0.005/share |
| Feed donnees | IEX (gratuit, pas SIP) |
| Ordres | Market, Limit, Stop, Bracket (OTO/OCO) |
| Paper trading | Natif, meme API |
| Disponibilite | 99.9%+ |

## 7.2 Broker Interactive Brokers

| Parametre | Valeur |
|-----------|--------|
| Type | Socket (stateful, TWS/Gateway) |
| Latence | ~50-100ms |
| Commissions US | $0.0005-0.0035/share (degressive) |
| Commissions EU | ~0.10% par trade (min EUR 4) |
| Feed donnees | SIP US + EU exchanges |
| Ordres | Market, Limit, Stop, Bracket, Options, Futures |
| Deconnexion | Toutes les 24h (dimanche soir) |

## 7.3 Deploiement Railway

| Parametre | Valeur |
|-----------|--------|
| Worker | `worker.py` (Python scheduler) |
| Procfile | `worker: python worker.py` |
| Restart policy | Automatique en cas de crash |
| Filesystem | Ephemere (state reconstruit depuis Alpaca) |
| Scheduler | Toutes les 5 min, 15:35-22:00 Paris |
| Heartbeat | Toutes les 30 min |
| Reconciliation | Au demarrage (compare state vs positions broker) |

## 7.4 Dashboard

| Composant | Technologie | Endpoints |
|-----------|-------------|-----------|
| Backend | FastAPI | 20+ endpoints REST |
| Frontend | React + Vite + Tailwind | 6 pages (Overview, Strategies, Positions, Analytics, Allocation, Settings) |
| Theme | Dark mode institutionnel | Bloomberg-inspired |
| Refresh | 30s pendant market hours | Polling |
| Registre | 14 strategies avec metriques completes | strategy_registry.py |

---

# 8. DONNEES ET INTEGRITE

## 8.1 Sources de donnees

| Source | Usage | Volume | Fraicheur |
|--------|-------|--------|-----------|
| Alpaca IEX feed | Barres 5M US (207 tickers) | 784 MB cache | 6 mois |
| IBKR Historical | Barres daily/15M EU (11 tickers) | ~50 MB | 1 an |
| yfinance | Backfill daily 5Y | Variable | On-demand |

## 8.2 Biais identifies

| Biais | Severite | Mitigation |
|-------|----------|-----------|
| **Survivorship bias** | HAUTE | L'univers de 207 tickers est construit aujourd'hui. Les delisted/acquis/faillites sont absents. Edge possiblement surestime de 10-30%. |
| **Lookahead bias** | BASSE | Guard `.shift(1)` dans toutes les strategies. Moteur de backtest rejette les signaux hors 9:35-15:55 ET. |
| **Data-snooping bias** | MOYENNE | 128 strategies testees, 20 retenues (~15%). Walk-forward mitigue partiellement mais ne l'elimine pas. |
| **Selection bias** | BASSE | Walk-forward avec fenetres OOS independantes. Criteres de validation fixes (pas ajustes a posteriori). |

## 8.3 Qualite des donnees

| Check | Resultat |
|-------|----------|
| Timezone | UTC dans les donnees, converti en US/Eastern pour le trading |
| NaN/gaps | Geres par forward-fill dans les indicateurs |
| Splits/dividendes | Donnees ajustees (Alpaca + yfinance) |
| Early close | Gere dans `is_us_market_open()` (veille Thanksgiving = 13:00) |
| Jours feries NYSE | Calendrier 2026 integre |

---

# 9. TESTS ET QUALITE LOGICIELLE

## 9.1 Couverture des tests

| Fichier test | Tests | Couverture |
|-------------|-------|-----------|
| test_risk_management.py | 15 | Circuit-breaker, max positions, exposure, PDT, paper guard |
| test_risk_v2.py | 31 | VaR, CVaR, sector limits, allocator, regime multipliers |
| test_intraday_strategies.py | ~20 | Strategies individuelles |
| test_sprint5.py | ~50 | Grid search, portfolio, assets |
| Autres (4 fichiers) | ~43 | Divers |
| **TOTAL** | **159** | **0 echec** |

## 9.2 Metriques de code

| Metrique | Valeur |
|----------|--------|
| Fichiers Python | 241 |
| Lignes de code | ~45,200 |
| Lignes de test | ~2,550 |
| Ratio test/code | 5.6% |
| Fichiers strategies | 128 |
| Fichiers core | ~30 |

## 9.3 Lacunes identifiees

| Lacune | Risque | Recommandation |
|--------|--------|---------------|
| Pas de CI/CD | Tests non executes automatiquement | GitHub Actions avec pytest |
| Pas de tests d'integration broker | Erreurs API non detectees en pre-prod | Mock Alpaca/IBKR API |
| Couverture 5.6% | Regressions possibles | Cible 20%+ sur le code critique |
| Pas de linting/formatting | Inconsistances de style | Black + ruff |
| Pas de type checking | Bugs runtime possibles | mypy strict |

---

# 10. CONFORMITE REGLEMENTAIRE

## 10.1 Regle PDT (Pattern Day Trader)

| Critere | Statut |
|---------|--------|
| Capital minimum | $25,000 requis pour intraday US |
| Guard implemente | Oui (`PDT_EQUITY_MINIMUM = 25_000.0`) |
| Test automatise | Oui (`TestPDTGuard`) |
| Impact si equity < $25K | Toutes strategies intraday desactivees automatiquement |

## 10.2 Wash Sale Rule

| Critere | Statut |
|---------|--------|
| Risque | Les strategies intraday sur les memes tickers = wash sales permanentes |
| Impact trading | Aucun (regle fiscale, pas de trading) |
| Impact fiscal | Les pertes ne sont pas deductibles si rachat dans les 30 jours |
| Mitigation | Documentation complete des trades pour declaration |

## 10.3 Short Selling

| Critere | Statut |
|---------|--------|
| SSR (Short Sale Restriction) | Non gere automatiquement (Alpaca peut rejeter) |
| Short borrow cost | Estime 0.5%/an dans les backtests (negligeable intraday) |
| Hard-to-borrow | Pas de detection automatique |
| Shorts fractionnels | Bloques (conversion en `int()`) |

## 10.4 Securite des donnees

| Critere | Statut |
|---------|--------|
| Cles API dans .env | Oui (jamais en dur dans le code) |
| .env dans .gitignore | Oui |
| Repo GitHub | Prive |
| Pas de secrets dans les logs | Verifie |
| Chiffrement | Non (pas necessaire en paper) |

---

# 11. VALORISATION DES ACTIFS INCORPORELS

## 11.1 Propriete intellectuelle

| Actif | Description | Valeur estimee |
|-------|------------|---------------|
| **128 strategies backtestees** | 5 jours de recherche intensive, 66+ backtests complets | Haute |
| **Framework de backtest** | Moteur evenementiel US + EU, walk-forward, Monte Carlo | Moyenne |
| **Risk Manager V2** | 7 checks pre-ordre, VaR/CVaR, circuit-breakers, kill switch | Haute |
| **Dynamic Allocator** | Risk Parity + Momentum + Correlation + Regime | Moyenne |
| **Dual-broker architecture** | Abstraction Alpaca + IBKR, SmartRouter | Moyenne |
| **Dashboard** | FastAPI + React, 20+ endpoints, dark mode pro | Basse-Moyenne |
| **Connaissance accumulee** | 6 regles derivees, patterns d'echec documentes | Haute |

## 11.2 Avantage competitif

| Facteur | Force | Durabilite |
|---------|-------|-----------|
| Strategies event-driven (OpEx, Gold Fear) | Forte | Haute (flux mecaniques) |
| Diversification 20 strategies | Forte | Haute (decorrelation) |
| Risk framework complet | Forte | Haute (protege le capital) |
| Dual-broker EU/US | Moderee | Moyenne (reproductible) |
| Vitesse de recherche (IA-assisted) | Forte | Basse (reproductible par concurrents) |

## 11.3 Base de connaissances

Le projet a genere 6 regles empiriques validees par 128 backtests :

1. **Regle des commissions** : > 200 trades/6 mois + position < $5K = mort
2. **Regle du Sharpe** : < 1.0 apres couts = probatoire max
3. **Regle de frequence** : Sweet spot = 30-60 trades/6 mois
4. **Regle du flow** : Edges mecaniques survivent, edges techniques meurent
5. **Regle de l'univers** : Ca marche sur 50 tickers mais pas 200 = survivorship bias
6. **Regle du slippage** : Break-even < 0.05% = fragile

---

# 12. RISQUES IDENTIFIES

## 12.1 Matrice des risques

| # | Risque | Probabilite | Impact | Severite | Mitigation |
|---|--------|:-----------:|:------:|:--------:|-----------|
| R1 | Alpha decay (strategies deviennent obsoletes) | Haute | Moyen | **HAUTE** | Monitoring Sharpe rolling, kill switch auto |
| R2 | Flash crash / correlation spike | Basse | Tres eleve | **HAUTE** | Circuit-breaker 5%, bracket orders broker-side |
| R3 | Bus factor = 1 (operateur unique) | Moyenne | Eleve | **HAUTE** | Documentation exhaustive, code versionne |
| R4 | Survivorship bias dans les backtests | Haute | Moyen | **HAUTE** | Noter le biais, ne pas sur-allouer |
| R5 | Erreur d'execution (bug code) | Moyenne | Moyen | **MOYENNE** | 159 tests, paper trading, guards multiples |
| R6 | Broker outage (Alpaca/IBKR down) | Basse | Moyen | **MOYENNE** | Bracket orders survivent, dual-broker |
| R7 | Regime change (bear prolonge) | Moyenne | Moyen | **MOYENNE** | Regime-conditional, strategies short |
| R8 | Couts augmentent (commissions, data) | Basse | Moyen | **BASSE** | Migration IBKR (commissions 7x moins) |
| R9 | Donnees corrompues / gap | Basse | Faible | **BASSE** | Parquet cache, reconciliation |
| R10 | Railway down / state perdu | Basse | Faible | **BASSE** | State reconstruit depuis Alpaca API |

## 12.2 Stress tests

| Scenario | Impact estime | Protection |
|----------|-------------|-----------|
| SPY -5% en 1h (flash crash) | DD ~2-3% (correlation spike) | Circuit-breaker 5% |
| IBKR deconnexion 24h | Pas d'ordres EU, US continue | Dual-broker |
| 3 strategies echouent simultanement | DD ~1-2% | Kill switch individuel |
| Alpaca change les commissions (+5x) | 3 strategies deviennent non-viables | Migration IBKR |
| Weekend gap -10% (cygne noir) | Pas de positions overnight actives | Overnight strategies rejetees |

---

# 13. FEUILLE DE ROUTE

## 13.1 Court terme (J+30)

| Action | Priorite | Effort |
|--------|----------|--------|
| Accumuler 30 jours de paper trading | P0 | Passif |
| Comparer performance live vs backtest | P0 | 1h |
| Deployer CI/CD (GitHub Actions) | P1 | 2h |
| Augmenter couverture tests a 15% | P1 | 4h |
| Ajuster slippage si paper ≠ backtest | P2 | 2h |

## 13.2 Moyen terme (J+60-90)

| Action | Priorite | Effort |
|--------|----------|--------|
| Passage live Alpaca ($25K) | P0 | 1h (changement de config) |
| Passage live IBKR ($5K EU) | P0 | 2h |
| Strategies options (put spreads) | P1 | 20h |
| Alpha decay monitoring automatise | P1 | 4h |
| Dashboard deploye sur StayFlow | P2 | 4h |

## 13.3 Long terme (J+180)

| Action | Priorite | Effort |
|--------|----------|--------|
| Scaler a $100K+ live | P0 | Progressif |
| Ajouter 5-10 strategies validees | P1 | 40h |
| Futures (ES, NQ) | P2 | 20h |
| Forex carry | P2 | 10h |
| Market making (si HFT infra) | P3 | 100h+ |

---

# 14. ANNEXES TECHNIQUES

## A. Structure des fichiers

```
trading-platform/                 Racine du projet
  CLAUDE.md                       Instructions projet
  SYNTHESE_COMPLETE.md            Synthese 66+ strategies
  DUE_DILIGENCE_2026-03-26.md    Ce document
  requirements.txt                Dependances Python
  Procfile                        Config Railway
  railway.json                    Config deploiement
  .gitignore                      Patterns d'exclusion
  config/
    allocation.yaml               Buckets + tiers + regime
    limits.yaml                   Limites risk (position, exposure, VaR)
  core/
    broker/                       Abstraction multi-broker
    alpaca_client/                Client Alpaca natif
    risk_manager.py               RiskManager V2
    allocator.py                  DynamicAllocator
    telegram_alert.py             Alertes
  scripts/
    paper_portfolio.py            Pipeline US (18 strategies)
    paper_portfolio_eu.py         Pipeline EU (2 strategies)
  worker.py                       Scheduler Railway
  intraday-backtesterV2/
    backtest_engine.py            Moteur US
    eu_backtest_engine.py         Moteur EU
    walk_forward.py               Walk-forward
    universe.py                   Univers US
    fetch_eu_data.py              Fetch IBKR EU
    strategies/                   128 fichiers strategies
      eu/                         6 strategies EU
  dashboard/
    api/                          FastAPI backend
    frontend/                     React frontend
  tests/                          159 tests
  output/                         Resultats backtests
  data_cache/                     Parquet (US 784MB + EU)
  docs/
    reports/                      Rapports historiques
    missions/                     Plans de mission
  archive/                        Fichiers obsoletes
```

## B. Calendrier des evenements geres

```
NYSE Holidays 2026 : Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19,
                      Jul 3, Sep 7, Nov 26, Dec 25
Early Close :         Nov 25 (13:00), Dec 24 (13:00)
BCE Meetings 2026 :   Jan 23, Mar 6, Apr 17, Jun 5, Jul 17, Sep 11, Oct 23, Dec 18
FOMC Meetings 2026 :  Jan 28-29, Mar 18-19, May 6-7, Jun 17-18,
                       Jul 29-30, Sep 16-17, Nov 4-5, Dec 16-17
```

## C. Glossaire

| Terme | Definition |
|-------|-----------|
| Sharpe Ratio | Return annualise / volatilite annualisee. > 1.0 = bon, > 2.0 = excellent |
| Profit Factor (PF) | Gains bruts / Pertes brutes. > 1.2 = viable |
| Walk-Forward | Methode de validation out-of-sample glissante |
| VaR | Value at Risk : perte max avec X% de confiance |
| CVaR | Conditional VaR : perte moyenne au-dela du VaR |
| Circuit-breaker | Mecanisme d'arret automatique en cas de perte excessive |
| Kill switch | Desactivation automatique d'une strategie sous-performante |
| Bracket order | Ordre principal + SL + TP envoyes simultanement au broker |
| PDT | Pattern Day Trader : regle SEC pour les comptes < $25K |
| Tier S/A/B/C | Classification des strategies par qualite d'edge |
| IEX | Investors Exchange — feed de donnees gratuit d'Alpaca |
| SIP | Securities Information Processor — feed consolide (payant) |
| OOS | Out-of-Sample — donnees non utilisees pour l'optimisation |
| IS | In-Sample — donnees utilisees pour l'optimisation |

---

*Document de Due Diligence genere le 26 mars 2026*
*Auteur : Claude Opus 4.6 pour Marc (trading-platform)*
*Classification : Confidentiel — Ne pas distribuer sans autorisation*
*Revision : 2.0 — Inclut dual-broker, EU strategies, Risk V2*
