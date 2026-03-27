# TODO XXL — EXPANSION MULTI-MARCHÉ & DIVERSIFICATION
## Trading Platform V4 → V5 | Agent Brief
### Date : 27 mars 2026 | Post-audit | 7 stratégies validées → cible 20-22

---

## INSTRUCTIONS AGENT

```
CE DOCUMENT EST UN BRIEF OPÉRATIONNEL POUR CLAUDE.
Il est conçu pour être utilisé de 3 façons :

1. BRIEF UNIQUE : Coller dans une conversation Claude pour exécuter les tâches une par une
2. ORCHESTRATION MULTI-AGENT : Découper en branches parallèles (voir §ORCHESTRATION)
3. MÉTA-PROMPT : Relancer avec la synthèse V5 pour générer la TODO suivante

CONVENTIONS :
- □ = à faire | ✅ = fait | ⏳ = en cours | ❌ = abandonné
- P0 = cette semaine | P1 = ce mois | P2 = 3 mois | P3 = 6+ mois
- Chaque tâche est AUTONOME : un agent peut l'exécuter sans contexte externe
- Les fichiers référencés sont relatifs à la racine du repo GitHub
```

---

## CONTEXTE (pour l'agent)

```yaml
projet: Trading algorithmique quantitatif multi-asset
repo: GitHub privé (marclucas2-cloud/Tplatform)
capital_paper: $100K Alpaca + $1M IBKR
capital_live_cible: $25K-$30K
brokers:
  alpaca: { marché: "US equities/ETFs", coûts: "$0.005/share + 0.02%", api: "REST" }
  ibkr: { marché: "EU equities + FX + Futures + Options", coûts_actions_eu: "0.26% RT", coûts_futures: "~0.003% RT", coûts_fx: "~0.01%", api: "TWS socket" }
infra:
  worker: Railway 24/7
  ci_cd: GitHub Actions
  dashboard: FastAPI + React
  alerting: Telegram
  tests: 433 (17 fichiers, 0 échec)
  code: ~62K lignes, 271 fichiers Python

état_actuel:
  stratégies_validées_wf: 4 (DoW Seasonal, Corr Regime Hedge, VIX Expansion Short, High-Beta Short)
  stratégies_borderline: 3 (Late Day MR, Failed Rally, EOD Sell V2)
  stratégies_eu_actives: 1 (EU Gap Open)
  stratégies_eu_prêtes: 4 (BCE Momentum, Auto Sector German, Brent Lag, EU Close→US)
  stratégies_fx_validées: 4 (EUR/USD, EUR/GBP, EUR/JPY, AUD/JPY)
  stratégies_futures: 0
  allocation: "US 70% / EU 15% / FX 7% / Futures 0% / Cash 8%"
  heures_capital_actif: ~8h/24h
  sharpe_portefeuille: ~2.82

problème_central: |
  70% du capital sur 1 broker (Alpaca), 1 marché (US), 6.5h/jour.
  IBKR massivement sous-exploité : 4 strats EU validées WF en attente,
  FX à 7% malgré 100% taux de survie, 0 futures malgré coûts /100.
  Le portefeuille est solide mais FRAGILE (7 strats) et CONCENTRÉ.

cible_v5:
  stratégies: 20-22
  allocation: "US 40% / EU 25% / FX 18% / Futures 10% / Cash 7%"
  heures_capital_actif: ~18h/24h
  sharpe_cible: "3.5-4.0"
  classes_actifs: 4 (actions, FX, futures, options overlay)

contraintes_absolues:
  - Walk-forward obligatoire sur TOUTE nouvelle stratégie (70/30, 5 fenêtres, OOS/IS > 0.5)
  - "< 30 trades = bruit statistique, pas d'allocation"
  - "Overnight = MORT (Sharpe -0.70 sur 5Y, ne pas reproposer)"
  - "Mean reversion 5M = MORT (0/12 survivent, ne pas reproposer)"
  - "Paper first : 60j positif avant live"
  - "Budget infra : $50-100/mois max"
  - "Opérateur unique (bus factor = 1)"
  - "Capital live $25K-$30K : les stratégies doivent fonctionner à cette échelle"
  - "Coûts EU actions 0.26% RT → TP > 1.5% obligatoire"
  - "Futures 100x moins cher que actions EU → privilégier futures quand possible"
```

---

## ORCHESTRATION MULTI-AGENT

```
Le plan se découpe en 4 BRANCHES PARALLÈLES + 1 BRANCHE TRANSVERSE.
Chaque branche peut être confiée à un agent Claude indépendant.

┌─────────────────────────────────────────────────────────┐
│                    CHEF D'ORCHESTRE                      │
│  (cette conversation ou un agent superviseur)            │
│  Rôle : séquencer, merger les résultats, résoudre       │
│  les conflits d'allocation et de risk                    │
└──────────┬──────────┬──────────┬──────────┬─────────────┘
           │          │          │          │
    ┌──────▼──┐ ┌─────▼───┐ ┌───▼─────┐ ┌─▼──────────┐
    │ AGENT EU│ │AGENT FX │ │AGENT FUT│ │AGENT RISK  │
    │         │ │         │ │         │ │& ALLOC     │
    │ EU-001  │ │ FX-001  │ │ DATA-002│ │ ALLOC-001  │
    │ EU-002  │ │ FX-002  │ │ FUT-001 │ │ FX-001     │
    │ EU-003  │ │ FX-003  │ │ FUT-002 │ │ RISK-003   │
    │ EU-004  │ │ FX-004  │ │ FUT-003 │ │ RISK-004   │
    │ EU-005  │ │ FX-005  │ │ FUT-004 │ │ DASH-002   │
    │ EU-006  │ │ FX-006  │ │ FUT-005 │ │ ROC-002    │
    │ INFRA-5 │ │         │ │ FUT-006 │ │ OPT-004    │
    └─────────┘ └─────────┘ └─────────┘ │ ALLOC-002  │
                                         │ LIVE-003   │
                                         └────────────┘

RÈGLES D'ORCHESTRATION :
1. Les 4 branches P0 (EU-001→004, FX-001, ALLOC-001, INFRA-005) sont PARALLÈLES
2. ALLOC-001 dépend de EU-001→004 (attendre que les strats EU soient déployées)
3. FUT-002→004 dépendent de FUT-001 + DATA-002 (infra futures d'abord)
4. RISK-003 dépend de FUT-001 + ALLOC-001 (intégrer futures dans le risk)
5. Les tâches P2/P3 ne commencent qu'après validation P0+P1

POUR CHAQUE AGENT, fournir :
- Ce fichier TODO (section pertinente)
- La synthèse V4 complète
- Accès au repo GitHub
- Instruction : "Exécute les tâches de ta branche dans l'ordre.
  Pour chaque tâche : code → tests → walk-forward si stratégie → commit."
```

---

## PROMPT AGENT — TEMPLATE

```markdown
# MISSION

Tu es un agent de développement quantitatif spécialisé en [BRANCHE].
Tu travailles sur le repo GitHub Tplatform (trading algorithmique).

## Contexte
[Coller la section CONTEXTE ci-dessus]

## Tes tâches
[Coller les tâches de la branche]

## Règles
1. Pour chaque STRATÉGIE : coder → backtester → walk-forward → documenter résultats
2. Walk-forward : 70% IS / 30% OOS, 5 fenêtres rolling, critère OOS/IS > 0.5
3. Si une stratégie ÉCHOUE au walk-forward : la documenter comme rejetée et passer à la suivante
4. NE PAS modifier les fichiers des autres branches (risk, allocation) sauf tes propres stratégies
5. Chaque fichier créé doit avoir un test unitaire associé
6. Commit après chaque tâche terminée avec message "[BRANCH-ID] description"
7. Si tu rencontres un blocker : documenter dans BLOCKERS.md et passer à la tâche suivante

## Output attendu par tâche
- Fichier stratégie : strategies/[nom].py
- Fichier backtest : backtests/[nom]_results.json
- Fichier test : tests/test_[nom].py
- Walk-forward : output/walk_forward/[nom]_wf.json
- Résumé : 1 paragraphe avec Sharpe IS, Sharpe OOS, ratio, trades, verdict (VALIDATED/REJECTED)
```

---

## SEMAINE 1-2 : P0 — DÉPLOYER CE QUI EST PRÊT

> **Objectif : passer de 7 à 12 stratégies actives, rééquilibrer l'allocation.**
> **Effort total : ~38h | Impact : diversification x2, heures couvertes x1.8**

---

### □ INFRA-005 — Pipeline EU multi-stratégies
```yaml
branche: AGENT_EU
priorité: P0
temps: 10h
dépendances: aucune (prerequis pour EU-001 à EU-004)
```
**Quoi** : Refactorer `paper_portfolio_eu.py` pour gérer 5+ stratégies simultanées. Actuellement il ne gère qu'1 stratégie (EU Gap Open). Le refactorer avec le même pattern que le pipeline US : boucle sur les stratégies enregistrées, allocation par stratégie via `allocation.yaml`, logging individuel par strat, P&L par strat dans le dashboard, gestion des horaires de marché EU (9h-17h30 CET).

**Comment** :
1. Analyser `paper_portfolio.py` (pipeline US) pour comprendre le pattern multi-strat
2. Refactorer `paper_portfolio_eu.py` : registry de stratégies, boucle d'exécution, allocation individuelle
3. Connecter au SmartRouter IBKR existant (`core/broker/factory.py`)
4. Ajouter logging par stratégie (même format que US)
5. Ajouter endpoints dashboard pour les strats EU individuelles
6. Tests : au moins 10 tests (registry, allocation, routing, logging, edge cases horaires)

**Succès** : Pipeline EU gère 5 stratégies simultanées, même architecture que le pipeline US, tests passent.

**Fichiers** :
- `paper_portfolio_eu.py` (refactor)
- `tests/test_pipeline_eu_multi.py` (nouveau)
- `config/strategies_eu.yaml` (nouveau, registry des strats EU)

**Risque si non fait** : Chaque strat EU nécessite du code custom = dette technique immédiate, blocage des EU-001→004.

---

### □ EU-001 — Déployer BCE Momentum Drift v2
```yaml
branche: AGENT_EU
priorité: P0
temps: 6h
dépendances: INFRA-005
```
**Quoi** : Intégrer BCE Momentum Drift v2 dans le pipeline EU. Stratégie déjà codée et walk-forward validée. 99 trades, Sharpe 14.93 (surveiller alpha decay — ce Sharpe reste suspect même avec WF), WR 76.8%, PF 3.93.

**Edge structurel** : Post-décision BCE, les banques EU (BNP, SG, DBK, ING) driftent dans la direction de la surprise pendant 2-4h. Les institutionnels réagissent lentement aux changements de taux → fenêtre d'exploitation mécanique. ~8 événements/an.

**Comment** :
1. Enregistrer la stratégie dans `config/strategies_eu.yaml`
2. Vérifier que le signal utilise bien `core/event_calendar.py` pour les dates BCE
3. Connecter au pipeline EU (INFRA-005)
4. Configurer les checks risk (position size, secteur bancaire)
5. Activer le monitoring alpha decay spécifique (Sharpe rolling 60j)
6. Test : simuler un événement BCE passé et vérifier le flow complet

**Succès** : Stratégie active en paper, premiers trades au prochain événement BCE, alpha decay monitored.

**Fichiers** :
- `config/strategies_eu.yaml` (ajout)
- `config/allocation.yaml` (ajout bucket EU)
- `tests/test_bce_integration.py` (nouveau)

**Risque si non fait** : La stratégie EU avec le plus de trades et le meilleur WF reste sur étagère.

---

### □ EU-002 — Déployer Auto Sector German
```yaml
branche: AGENT_EU
priorité: P0
temps: 5h
dépendances: INFRA-005
```
**Quoi** : Intégrer Auto Sector German (sympathy play). WF validé, 97 trades, Sharpe 13.43, PF 7.27, WR 75.3%.

**Edge structurel** : Quand un constructeur auto allemand (VW, BMW, Mercedes) publie des résultats ou fait une annonce majeure, les sous-traitants (Continental, Schaeffler, ElringKlinger) réagissent en retard de 30-120 min. Le move total sous-traitant est > 1.5% → au-dessus du coût EU 0.26%.

**Comment** :
1. Enregistrer dans `config/strategies_eu.yaml`
2. Vérifier les tickers Xetra (CON, SHA, ZIL) dans le SmartRouter IBKR
3. Connecter au pipeline EU
4. Configurer risk : concentration secteur auto < 25% du bucket EU
5. Test : simuler une annonce BMW passée et vérifier le flow

**Succès** : Active en paper, trade quand un signal constructeur auto se déclenche.

**Fichiers** :
- `strategies/auto_sector_german.py` (vérifier existant)
- `config/strategies_eu.yaml` (ajout)
- `tests/test_auto_sector_integration.py`

**Risque si non fait** : Edge structurel sympathy play non exploité.

---

### □ EU-003 — Déployer Brent Lag Play (proxy actions)
```yaml
branche: AGENT_EU
priorité: P0
temps: 5h
dépendances: INFRA-005
```
**Quoi** : Intégrer Brent Lag Play en version proxy actions. **La stratégie la plus robuste statistiquement du projet** : 729 trades, 4/5 WF PASS, Sharpe 4.08, WR 57.9%, PF 2.03.

**Edge structurel** : Le pétrole Brent trade à Londres. Les energy stocks US (XLE, XOM, CVX) réagissent en retard au mouvement du Brent pendant l'overlap EU/US. Le lag est de 15-60 min. C'est un edge de fuseau horaire — structurellement durable tant que les marchés sont dans des timezones différentes.

**Note** : Version proxy actions d'abord (coûts 0.26% mais déjà codée), migration futures CL en P1 (FUT-002).

**Comment** :
1. Enregistrer dans `config/strategies_eu.yaml`
2. Vérifier les tickers energy EU (BP., SHEL, TotalEnergies) dans le SmartRouter
3. Connecter au pipeline EU
4. Configurer risk : concentration secteur energy < 25%
5. Test : vérifier le lag historique sur une journée passée

**Succès** : Active en paper, premiers trades dès la prochaine divergence Brent/energy stocks.

**Fichiers** :
- `strategies/brent_lag_play.py` (vérifier existant)
- `config/strategies_eu.yaml` (ajout)

**Risque si non fait** : La stratégie avec le meilleur échantillon statistique du projet (729 trades) reste inactive.

---

### □ EU-004 — Déployer EU Close → US Afternoon
```yaml
branche: AGENT_EU
priorité: P0
temps: 5h
dépendances: INFRA-005
```
**Quoi** : Intégrer EU Close → US Afternoon. WF validé, 113 trades, Sharpe 2.43, WR 60.2%, PF 1.50.

**Edge structurel** : Le momentum de la clôture EU (17h30 CET) se propage dans les ETFs US pendant l'après-midi US (17h30-20h CET). Les market makers US ajustent leurs positions en réaction aux signaux EU avec un retard de 30-90 min.

**Créneau couvert** : 15h30-17h30 CET (overlap), exactement quand le portefeuille actuel a un trou.

**Comment** :
1. Enregistrer dans `config/strategies_eu.yaml`
2. Cette stratégie est CROSS-BROKER : signal EU (IBKR) → exécution US (Alpaca). Vérifier que le SmartRouter gère ce cas.
3. Si cross-broker pas supporté : exécuter sur Alpaca côté US uniquement (signal interne)
4. Configurer risk : pas de position overnight (close avant 22h CET)
5. Test : simuler un close EU fort et vérifier le signal US

**Succès** : Active en paper, trade pendant l'overlap EU/US.

**Fichiers** :
- `strategies/eu_close_us_afternoon.py` (vérifier existant)
- `config/strategies_eu.yaml` (ajout)
- `tests/test_cross_timezone.py`

**Risque si non fait** : Créneau overlap 15h30-17h30 sous-exploité.

---

### □ ALLOC-001 — Rebalancer l'allocation globale
```yaml
branche: AGENT_RISK_ALLOC
priorité: P0
temps: 4h
dépendances: EU-001, EU-002, EU-003, EU-004
```
**Quoi** : Modifier `config/allocation.yaml` pour passer de l'allocation actuelle concentrée à une allocation diversifiée :

| Bucket | Actuel | Cible V5 |
|--------|:------:|:--------:|
| US Alpaca | 70% | 40% |
| EU IBKR | 15% | 25% |
| FX IBKR | 7% | 18% |
| Futures IBKR | 0% | 10% |
| Cash | 8% | 7% |

**Comment** :
1. Mettre à jour `config/allocation.yaml` avec les nouveaux poids
2. Recalculer les pondérations Sharpe-weighted à l'intérieur de chaque bucket
3. Mettre à jour les multiplicateurs de régime (BULL/BEAR/RANGE) pour les nouveaux buckets
4. Mettre à jour l'allocation cross-timezone :
   - 9h-15h30 CET : EU 25% + FX 5%
   - 15h30-17h30 CET : EU 15% + US 30% + FX 5%
   - 17h30-22h CET : US 40% + Shorts 15% + FX 5%
   - 22h-9h CET : FX 15% + Futures 5% + Cash 80%
5. Vérifier qu'aucun bucket ne dépasse 45% et aucun broker ne dépasse 60%
6. Documenter les changements

**Succès** : Nouvelle allocation active, aucune concentration > 45% par bucket, > 60% par broker.

**Fichiers** :
- `config/allocation.yaml` (modifier)
- `docs/allocation_v5.md` (nouveau)

**Risque si non fait** : Capital reste concentré sur US malgré la disponibilité de marchés décorrélés.

---

### □ FX-001 — Augmenter l'allocation FX de 7% à 18%
```yaml
branche: AGENT_RISK_ALLOC
priorité: P0
temps: 3h
dépendances: ALLOC-001
```
**Quoi** : Dans le cadre de ALLOC-001, s'assurer que le FX passe bien de 7% à 18%. Recalculer le Kelly par stratégie FX. Les 4 stratégies FX validées (EUR/USD, EUR/GBP, EUR/JPY, AUD/JPY) ont un taux de survie de 100% et des coûts quasi nuls.

**Comment** :
1. Recalculer Kelly pour chaque strat FX avec les données actuelles
2. Répartir les 18% entre les 4 paires (Sharpe-weighted)
3. Vérifier que le sizing par paire est cohérent avec $25K live (lot size minimum FX IBKR)
4. S'assurer que le FX couvre les heures 22h-9h CET (quand US/EU dorment)

**Succès** : FX à 18%, Kelly recalculé, sizing viable à $25K.

**Fichiers** :
- `config/allocation.yaml` (dans ALLOC-001)
- `core/kelly_calculator.py` (mise à jour FX)

---

## SEMAINE 3-6 : P1 — EXPANSION FX + FUTURES

> **Objectif : passer de 12 à 18-20 stratégies, ouvrir les futures, enrichir le FX.**
> **Effort total : ~136h | Impact : couverture 18h/24h, 4 classes d'actifs**

---

### □ FX-002 — Backtester GBP/USD Trend Following
```yaml
branche: AGENT_FX
priorité: P1
temps: 10h
dépendances: aucune
```
**Quoi** : Répliquer le framework EUR/USD Trend Following (Sharpe 4.62, 47 trades, validé) sur GBP/USD. Même logique : trend-following macro basé sur divergences de politique monétaire (BoE vs Fed). Holding 1-10 jours.

**Edge structurel** : Les divergences de politique monétaire BoE vs Fed créent des tendances persistantes sur GBP/USD. La BoE a historiquement réagi avec retard par rapport à la Fed → tendances exploitables. Corrélation attendue ~0.3-0.4 avec EUR/USD.

**Comment** :
1. Adapter le code `strategies/fx_eurusd_trend.py` pour GBP/USD
2. Télécharger 5Y de données GBP/USD (IBKR Historical Data API ou source gratuite)
3. Backtest complet avec coûts réalistes (~0.01% RT)
4. Walk-forward : 70/30, 5 fenêtres, critère OOS/IS > 0.5
5. Calculer corrélation avec EUR/USD sur la période de backtest
6. Si validé : intégrer dans le pipeline FX

**Succès** : WF validé (OOS/IS > 0.5), Sharpe > 1.5, > 40 trades sur 5Y, corrélation < 0.5 avec EUR/USD.

**Fichiers** :
- `strategies/fx_gbpusd_trend.py` (nouveau)
- `backtests/fx_gbpusd_results.json` (nouveau)
- `tests/test_fx_gbpusd.py` (nouveau)
- `output/walk_forward/fx_gbpusd_wf.json` (nouveau)

**Si REJECTED** : Documenter dans `docs/rejected_strategies.md` et passer à la suivante.

---

### □ FX-003 — Backtester USD/CHF Mean Reversion
```yaml
branche: AGENT_FX
priorité: P1
temps: 10h
dépendances: aucune
```
**Quoi** : Stratégie mean reversion sur USD/CHF. Edge : le CHF est un safe haven, USD/CHF mean-reverts après des spikes de risk-off. Holding 5-15 jours.

**Edge structurel** : La SNB intervient (historiquement) quand le CHF se renforce trop. Même sans intervention directe, les flux refuge sont temporaires → mean reversion naturelle. Le floor SNB 2011-2015 et les interventions post-2015 documentent ce comportement. Décorrélé des carry trades (EUR/JPY, AUD/JPY).

**Comment** :
1. Coder la stratégie : signal = déviation > 2 ATR du prix moyen 20j, entrée contrarian
2. Filtres : pas de trade pendant les annonces SNB (ajouter au calendar), pas de trade si VIX > 30 (risk-off extrême = pas de mean reversion)
3. Backtest 5Y + walk-forward
4. Calculer corrélation avec les 4 paires FX existantes

**Succès** : WF validé, Sharpe > 1.0, corrélation < 0.3 avec le reste du FX book.

**Fichiers** :
- `strategies/fx_usdchf_mr.py` (nouveau)
- `backtests/fx_usdchf_results.json`
- `tests/test_fx_usdchf.py`
- `output/walk_forward/fx_usdchf_wf.json`

---

### □ FX-004 — Backtester NZD/USD Carry + Momentum
```yaml
branche: AGENT_FX
priorité: P1
temps: 8h
dépendances: aucune
```
**Quoi** : Répliquer le framework EUR/JPY Carry + Momentum sur NZD/USD. Différentiel de taux RBNZ vs Fed + trend. Holding 10-30j.

**Edge structurel** : Le NZD est sensible aux commodities (dairy, agriculture) — diversification sectorielle implicite. Le carry trade NZD est un classique macro. Décorrélé des paires EUR-centric. Diversification géographique Asie-Pacifique.

**Comment** :
1. Adapter `strategies/fx_eurjpy_carry.py` pour NZD/USD
2. Intégrer les données de taux RBNZ (source : RBNZ website, scraping ou manual)
3. Signal : carry positif + momentum 20j positif = long, inverse = short
4. Backtest 5Y + walk-forward

**Succès** : WF validé, Sharpe > 1.0, > 50 trades sur 5Y, corrélation < 0.3 avec EUR/JPY.

**Fichiers** :
- `strategies/fx_nzdusd_carry.py` (nouveau)
- `backtests/fx_nzdusd_results.json`
- `tests/test_fx_nzdusd.py`

---

### □ DATA-002 — Données futures historiques IBKR
```yaml
branche: AGENT_FUTURES
priorité: P1
temps: 8h
dépendances: aucune (prerequis pour FUT-001→004)
```
**Quoi** : Télécharger et stocker 5 ans de données historiques pour ES, NQ, CL via IBKR Historical Data API. Gérer les rolls de contrats (continuous contract adjustment).

**Détails techniques** :
- Instruments : ES (E-mini S&P 500), NQ (E-mini Nasdaq 100), CL (Crude Oil WTI)
- Timeframes : 1min, 5min, 1h, daily
- Période : 2021-01-01 → aujourd'hui
- Continuous contract : back-adjusted (ratio method ou difference method)
- Attention : IBKR limite les requêtes historiques (pacing). Prévoir un script avec retry + sleep.

**Comment** :
1. Script de téléchargement avec l'API IBKR Historical Data (via `ib_insync` ou TWS API directe)
2. Gestion des rolls : identifier les dates de roll (volume crossover), construire le continuous contract
3. Stockage : CSV par instrument et timeframe dans `data/futures_historical/`
4. Validation : comparer les close daily avec Yahoo Finance pour les ETF équivalents (SPY, QQQ, USO)
5. Documenter les gaps, anomalies, et la méthode d'ajustement

**Succès** : 5Y de données propres pour ES/NQ/CL, validation croisée < 0.5% de divergence.

**Fichiers** :
- `scripts/download_futures_data.py` (nouveau)
- `data/futures_historical/ES_1min.csv`, `ES_5min.csv`, `ES_1h.csv`, `ES_daily.csv`
- `data/futures_historical/NQ_*.csv`, `CL_*.csv`
- `data/futures_historical/rolls.json` (dates de roll)
- `tests/test_futures_data.py`
- `docs/futures_data_quality.md`

**Risque si non fait** : Bloque TOUS les backtests futures.

---

### □ FUT-001 — Infrastructure futures IBKR
```yaml
branche: AGENT_FUTURES
priorité: P1
temps: 20h
dépendances: aucune (parallèle avec DATA-002)
```
**Quoi** : Construire le module de trading futures dans le SmartRouter IBKR. C'est l'infrastructure fondamentale pour toute stratégie futures.

**Composants à construire** :
1. **Contract manager** : symbole + expiration + multiplier + exchange
   - ES : multiplier 50, CME, quarterly (H/M/U/Z)
   - NQ : multiplier 20, CME, quarterly
   - CL : multiplier 1000, NYMEX, monthly
   - Micro alternatives si sizing trop gros pour $25K : MES (mult 5), MNQ (mult 2), MCL (mult 100)
2. **Roll manager** : roll automatique front month → next, 5 jours avant expiry
   - Détection de la date d'expiry par contrat
   - Fermeture position front month + ouverture next month
   - Logging du roll (prix, slippage)
3. **Margin tracker** : initial margin + maintenance margin par contrat
   - ES initial margin ~$12,000 (trop pour $25K → utiliser MES ~$1,400)
   - NQ initial margin ~$16,000 (→ MNQ ~$1,800)
   - CL initial margin ~$6,000 (→ MCL ~$600)
4. **P&L converter** : P&L en points → P&L en $ (via multiplier)
5. **Integration SmartRouter** : le router dirige les ordres futures vers le module IBKR futures

**Choix micro vs mini pour $25K** :
```
Capital $25K, max 10 positions, max 10% par position = $2,500 max/position
→ MES ($2,500 / $1,400 margin = 1 contrat OK)
→ MNQ ($2,500 / $1,800 margin = 1 contrat OK)
→ MCL ($2,500 / $600 margin = 4 contrats max)
Recommandation : commencer avec les MICRO, pas les MINI
```

**Comment** :
1. Créer `core/broker/ibkr_futures.py` : contract specs, order placement, position tracking
2. Créer `core/futures_roll.py` : roll logic, scheduling, logging
3. Créer `core/futures_margin.py` : margin monitoring, alerts si margin < 150% maintenance
4. Intégrer dans `core/broker/factory.py` (SmartRouter)
5. Tests : mock IBKR API, tester roll, margin, P&L conversion
6. Paper test : passer un ordre MES en paper sur IBKR et vérifier le flow complet

**Succès** : Module capable de passer des ordres futures micro en paper sur IBKR, roll automatique testé, margin tracking actif.

**Fichiers** :
- `core/broker/ibkr_futures.py` (nouveau)
- `core/futures_roll.py` (nouveau)
- `core/futures_margin.py` (nouveau)
- `core/broker/factory.py` (modifier : ajouter route futures)
- `config/futures_contracts.yaml` (nouveau : specs par contrat)
- `tests/test_futures_infra.py` (nouveau, 15+ tests)

**Risque si non fait** : Bloque toute expansion futures — le levier de ROC le plus important du projet.

---

### □ FUT-002 — Migrer Brent Lag Play vers futures CL
```yaml
branche: AGENT_FUTURES
priorité: P1
temps: 12h
dépendances: FUT-001, EU-003 (version proxy active pour comparaison)
```
**Quoi** : Prendre le Brent Lag Play (729 trades, 4/5 WF, Sharpe 4.08) et le migrer de proxy actions EU (0.26% RT) vers futures MCL micro crude (0.003% RT). Même logique, mêmes signaux, exécution sur futures.

**Calcul d'impact** :
```
Version proxy : 729 trades × 0.26% RT × ~$3,000 position = ~$5,680 commissions/5Y
Version futures : 729 trades × ~$1.50 RT (MCL) = ~$1,094 commissions/5Y
Économie : ~$4,586 sur 5Y = ~$917/an
À $25K de capital, l'économie = ~3.7% du capital par an (!)
```

**Comment** :
1. Adapter le signal de `strategies/brent_lag_play.py` pour trader MCL au lieu d'actions energy
2. Le signal reste identique (lag entre Brent spot et réaction US) — seul l'instrument d'exécution change
3. Backtest sur données futures CL (DATA-002) avec les mêmes paramètres
4. Comparer P&L brut proxy vs futures (doit être quasi identique)
5. Comparer P&L net (proxy - 0.26% vs futures - 0.003%)
6. Walk-forward sur la version futures (doit passer si la version proxy passe)
7. Si validé : désactiver la version proxy, activer la version futures

**Succès** : Même PnL brut ±5%, coûts réduits > 90%, WF validé.

**Fichiers** :
- `strategies/brent_lag_futures.py` (nouveau)
- `backtests/brent_lag_futures_results.json`
- `tests/test_brent_lag_futures.py`

---

### □ FUT-003 — Backtester ES micro trend-following swing
```yaml
branche: AGENT_FUTURES
priorité: P1
temps: 16h
dépendances: FUT-001, DATA-002
```
**Quoi** : Stratégie trend-following sur MES (Micro E-mini S&P 500). Holding 2-10 jours. Signal : croisement EMA 10/30 + filtre VIX.

**Edge structurel** : Le momentum time-series sur les indices actions est un des edges les mieux documentés en finance académique (Moskowitz, Ooi, Pedersen 2012 — "Time Series Momentum", Journal of Financial Economics). Le MES trade 23h/jour (dimanche 18h → vendredi 17h ET) → couverture maximale.

**Paramètres de départ** :
```
Instrument : MES (Micro E-mini S&P 500, multiplier 5)
Signal long : EMA10 > EMA30 + prix > EMA10
Signal short : EMA10 < EMA30 + prix < EMA10
Filtre : pas de long si VIX > 25, pas de short si VIX < 12
Stop : 2 ATR(14)
Take profit : 3 ATR(14)
Sizing : 1 contrat MES par signal (~$1,400 margin)
```

**Comment** :
1. Coder la stratégie avec les paramètres ci-dessus
2. Backtest sur 5Y de données ES (DATA-002)
3. Walk-forward 70/30, 5 fenêtres
4. Calculer corrélation avec les stratégies US intraday
5. Tester la sensibilité aux paramètres EMA (8/21, 10/30, 13/34) — vérifier que l'edge n'est pas parameter-dependent

**Succès** : WF validé, Sharpe > 1.5, > 60 trades sur 5Y, corrélation < 0.4 avec US intraday.

**Fichiers** :
- `strategies/futures_mes_trend.py` (nouveau)
- `backtests/futures_mes_results.json`
- `tests/test_mes_trend.py`
- `output/walk_forward/mes_trend_wf.json`

---

### □ FUT-004 — Backtester NQ micro mean reversion
```yaml
branche: AGENT_FUTURES
priorité: P1
temps: 14h
dépendances: FUT-001, DATA-002
```
**Quoi** : Mean reversion sur MNQ (Micro E-mini Nasdaq) après des moves extrêmes intraday (> 2 ATR). Holding 2h-2 jours.

**Edge structurel** : Le Nasdaq a une volatilité ~1.3x celle du S&P. Les overshoots intraday sont plus fréquents et plus profonds. Les market makers reprennent le contrôle après les spikes → mean reversion mécanique. Ce n'est PAS de la mean reversion 5M (morte) — c'est de la mean reversion sur des mouvements EXTRÊMES (> 2 ATR) avec un holding plus long.

**Distinction avec la mean reversion 5M rejetée** :
```
Mean reversion 5M (MORTE) : RSI/BB sur bougies 5min, holding minutes, TP tiny
Mean reversion NQ (NOUVELLE) : déviation > 2 ATR daily, holding heures/jours, TP large
→ Structurellement différent : échelle temporelle, taille du move, instrument (futures vs actions)
```

**Paramètres** :
```
Signal : prix dévie de > 2 ATR(14) de la moyenne 20 périodes (1h)
Entrée : contrarian (long si déviation négative, short si positive)
Stop : 1.5 ATR au-delà de l'entrée
TP : retour à la moyenne 20 périodes
Holding max : 2 jours (forcer la sortie)
Filtre : pas de trade si VIX > 35 (chaos, pas de mean reversion)
```

**Succès** : WF validé, Sharpe > 1.0, > 50 trades sur 5Y, corrélation < 0.3 avec ES trend.

**Fichiers** :
- `strategies/futures_mnq_mr.py` (nouveau)
- `backtests/futures_mnq_results.json`
- `tests/test_mnq_mr.py`

---

### □ STRAT-009 — Compléter et déployer FOMC Reaction
```yaml
branche: AGENT_EU (ou AGENT_RISK si plus approprié)
priorité: P1
temps: 10h
dépendances: aucune
```
**Quoi** : La stratégie FOMC Reaction existe (Sharpe 1.74, 28 trades, "prometteur"). Compléter le backtest pour atteindre > 40 trades (6 ans × 8 meetings = 48 possibles). Walk-forward. Si validée, déployer dans le pipeline US.

**Edge** : Vol compression pré-FOMC → explosion post-décision. Les 2h post-annonce ont un biais de continuation (la réaction initiale se poursuit dans 65% des cas historiquement). 8 événements/an. Pas exploitable par les HFT (edge directionnel, pas de vitesse).

**Comment** :
1. Étendre le backtest à 2019-2025 (48+ meetings)
2. Vérifier : la strat trade-t-elle la DIRECTION ou la VOLATILITÉ ? (Les deux sont des edges différents)
3. Walk-forward sur les 48+ trades
4. Si validé : intégrer dans le pipeline US, connecter à `core/event_calendar.py`

**Succès** : > 40 trades, WF validé, Sharpe > 1.0.

**Fichiers** :
- `strategies/fomc_reaction.py` (compléter)
- `backtests/fomc_results.json` (nouveau)
- `tests/test_fomc.py`

---

### □ EU-005 — Backtester BCE Press Conference Drift
```yaml
branche: AGENT_EU
priorité: P1
temps: 8h
dépendances: EU-001
```
**Quoi** : Extension de BCE Momentum Drift. Au-delà du mouvement initial post-décision (45 min), backtester le drift supplémentaire pendant la conférence de presse Lagarde (45 min après la décision). Holding 1-3h post-conférence.

**Edge** : Chaque réunion BCE produit 2 événements séparés : (1) la décision de taux (13h45 CET), (2) la conférence de presse (14h30 CET). Le marché réagit différemment aux deux. La conférence de presse peut INVERSER la réaction à la décision (quand Lagarde nuance).

**Comment** :
1. Isoler le signal post-conférence (14h30-17h CET) vs post-décision (13h45-14h30)
2. Backtest séparé sur 5Y (8 BCE/an × 5Y = 40 événements)
3. Walk-forward
4. Vérifier l'indépendance avec BCE Momentum Drift base (les signaux sont-ils corrélés ?)

**Succès** : WF validé, > 30 trades, PnL incrémental vs BCE base.

**Fichiers** :
- `strategies/bce_press_conference.py` (nouveau)
- `backtests/bce_press_results.json`

---

### □ RISK-003 — Intégrer futures et FX renforcé dans le risk framework
```yaml
branche: AGENT_RISK_ALLOC
priorité: P1
temps: 12h
dépendances: FUT-001, ALLOC-001
```
**Quoi** : Mettre à jour le risk manager pour gérer les spécificités futures et le FX à 18%.

**Composants** :
1. **Futures dans le VaR** : intégrer les rendements futures dans la matrice de corrélation portfolio. Attention au multiplier (1 point ES = $5 en micro, pas $50).
2. **Margin monitoring** : alerte si margin utilisée > 70% de margin disponible
3. **Roll risk** : pendant un roll (2 contrats ouverts temporairement), doubler le margin check
4. **FX sizing** : lot size minimum IBKR FX = pas de contrainte pour $25K (flexible)
5. **VaR stressed** : recalculer avec corrélations mars 2020 incluant les futures
6. **Limites** : ajouter dans `config/limits.yaml` les limites futures (max contrats, max margin, max exposure notionnelle)

**Succès** : VaR portfolio recalculé avec 3 classes d'actifs, stress tests incluant futures, margin monitoring actif.

**Fichiers** :
- `core/risk_manager.py` (modifier)
- `config/limits.yaml` (modifier)
- `tests/test_risk_futures.py` (nouveau, 15+ tests)

---

## SEMAINE 7-12 : P2 — OPTIMISATION

> **Objectif : stratégies de 2ème ordre, cross-asset, fine-tuning.**
> **Effort total : ~82h | Impact : Sharpe +0.5, robustesse++**

---

### □ FX-005 — Cross-pair momentum FX
```yaml
branche: AGENT_FX
priorité: P2
temps: 14h
dépendances: FX-002, FX-003, FX-004 (besoin de 6+ paires)
```
**Quoi** : Momentum cross-sectionnel sur les 6-8 paires FX. Chaque semaine, surpondérer les 2 meilleures paires (momentum 20j) et sous-pondérer les 2 pires.

**Edge** : Le carry + momentum cross-sectionnel est un des factors les mieux documentés en FX (Lustig, Roussanov, Verdelhan 2011). C'est une stratégie de 2ème ordre : elle exploite la diversification FX existante sans nouvel instrument.

**Succès** : WF validé, Sharpe > 1.0, amélioration du Sharpe du book FX > 15%.

**Fichiers** : `strategies/fx_cross_momentum.py`, `backtests/fx_cross_results.json`

---

### □ FUT-005 — Backtester Gold (GC micro) momentum
```yaml
branche: AGENT_FUTURES
priorité: P2
temps: 12h
dépendances: FUT-001, DATA-002 (ajouter MGC aux données)
```
**Quoi** : Trend-following sur MGC (Micro Gold futures). Holding 5-20 jours. Safe haven → hedge naturel.

**Edge** : Gold momentum time-series est documenté. En crise actions, le gold tend à monter → corrélation négative avec le portefeuille actions. Diversification matière première non-énergie. Coûts micro ~$1.50 RT.

**Succès** : WF validé, Sharpe > 1.0, corrélation < 0.1 avec stratégies actions.

**Fichiers** : `strategies/futures_mgc_trend.py`, `backtests/futures_mgc_results.json`

---

### □ EU-006 — Backtester EURO STOXX 50 futures trend
```yaml
branche: AGENT_EU (ou AGENT_FUTURES)
priorité: P2
temps: 14h
dépendances: FUT-001, DATA-002
```
**Quoi** : Trend-following swing sur EURO STOXX 50 futures via IBKR. Même framework que MES trend (FUT-003) appliqué à l'indice EU. Coûts ~€2/contrat RT = ~0.005%. Remplace l'exposition actions EU coûteuse par une exposition indice à coûts /50.

**Succès** : WF validé, Sharpe > 1.0, coûts < 0.01% RT.

**Fichiers** : `strategies/futures_estx_trend.py`, `backtests/futures_estx_results.json`

---

### □ OPT-004 — Confluence cross-asset
```yaml
branche: AGENT_RISK_ALLOC
priorité: P2
temps: 10h
dépendances: FUT-003, FX-003
```
**Quoi** : Enrichir `core/confluence_detector.py` avec des signaux cross-asset. Quand un signal short US + un signal risk-off FX arrivent ensemble → amplifier (x1.3). Quand un signal long US + un signal short futures ES → conflit = réduire les deux.

**Règles de confluence cross-asset** :
```
SHORT US + SHORT FX (risk-off) → x1.3 (convergence)
LONG US + LONG Futures ES → x1.2 (convergence)
LONG US + SHORT Futures ES → x0.7 (conflit)
LONG Gold + SHORT US → x1.2 (risk-off cohérent)
```

**Succès** : Sharpe portefeuille amélioré > 5% en simulation, 0 conflit non détecté.

**Fichiers** : `core/confluence_detector.py` (modifier), `tests/test_cross_asset_confluence.py`

---

### □ DASH-002 — Dashboard multi-marché
```yaml
branche: AGENT_RISK_ALLOC
priorité: P2
temps: 14h
dépendances: ALLOC-001, RISK-003
```
**Quoi** : Étendre le dashboard avec : P&L par marché (US/EU/FX/Futures), heatmap 24h montrant quand le capital travaille, corrélation matrix live entre classes d'actifs, VaR portfolio avec contribution par classe, onglet "Market Hours".

**Fichiers** : `dashboard/` (modifier), `api/endpoints/` (ajouter)

---

### □ RISK-004 — Stress test multi-marché
```yaml
branche: AGENT_RISK_ALLOC
priorité: P2
temps: 12h
dépendances: RISK-003
```
**Quoi** : 4 scénarios :
1. Crash US + contagion EU (corrélation spike 0.9)
2. Crise pétrolière (CL ±20%)
3. Flash crash FX (type CHF Jan 2015)
4. Dislocation cross-asset (actions down + gold down + USD up = 2008)

**Succès** : Drawdown max < 8% dans chaque scénario.

**Fichiers** : `tests/test_stress_multi_market.py`, `docs/stress_scenarios_v2.md`

---

### □ ROC-002 — Analyse heures de capital actif
```yaml
branche: AGENT_RISK_ALLOC
priorité: P2
temps: 6h
dépendances: 30+ jours de paper multi-marché
```
**Quoi** : Mesurer les heures/jour de capital engagé par marché. Calculer ROC horaire. Identifier les créneaux morts.

**Cible** : Passer de ~8h à ~18h de capital actif/24h.

**Fichiers** : `scripts/roc_analysis.py`, `output/roc_hourly_report.md`

---

## SEMAINE 13+ : P3 — EXCELLENCE

> **Objectif : stratégies avancées, options, scaling.**
> **Effort total : ~100h | Impact : robustesse institutionnelle**

---

### □ FUT-006 — Calendar spread ES (market neutral)
```yaml
priorité: P3 | temps: 20h | dépendances: FUT-001, DATA-002
```
Spread calendaire front month vs next month ES. 100% market neutral. Edge : contango/backwardation mean reversion. Corrélation ~0 avec le portefeuille directionnel.

---

### □ OPT-005 — Protective puts overlay
```yaml
priorité: P3 | temps: 16h | dépendances: FUT-001
```
Quand VIX < 15, acheter des puts OTM (delta -0.10) sur les positions longues ETF. Protection tail events. Coût cible < 1% annuel.

---

### □ FX-006 — EUR/NOK carry
```yaml
priorité: P3 | temps: 10h | dépendances: aucune
```
Carry trade EUR/NOK. NOK corrélée au pétrole → diversification commodity implicite. Décorrélé des paires majeures.

---

### □ STRAT-010 — Lead-lag cross-timezone systématique
```yaml
priorité: P3 | temps: 30h | dépendances: DATA-002, FUT-001
```
Étude quantitative des lead-lags : close US → open EU, close EU → FX Asie, VIX → DAX, DXY → commodities. Systématiser les 2-3 meilleurs signaux.

---

### □ LIVE-003 — Checklist live multi-marché (17 points)
```yaml
priorité: P3 | temps: 4h | dépendances: RISK-003, RISK-004
```
Étendre la checklist de 11 à 17 points : réconciliation IBKR futures, margin monitoring, roll testé, stress multi-marché, heures trading validées, alerting par marché.

---

### □ ALLOC-002 — Allocation dynamique cross-asset
```yaml
priorité: P3 | temps: 16h | dépendances: ALLOC-001, régime HMM existant
```
Allocation dynamique selon le régime HMM : bear → FX/gold/shorts, bull → actions/trend futures, range → mean reversion. Cible : Sharpe +10% vs allocation statique.

---

## ANNEXE A — PORTEFEUILLE CIBLE V5 (20-22 STRATÉGIES)

```
 #  | Stratégie                  | Marché  | Classe   | Dir | Holding    | Statut
----|----------------------------|---------|----------|-----|------------|--------
 1  | Day-of-Week Seasonal       | US      | Actions  | L/S | Intraday   | ✅ ACTIF
 2  | Correlation Regime Hedge   | US      | Actions  | L/S | Intraday   | ✅ ACTIF
 3  | VIX Expansion Short        | US      | Actions  | S   | Intraday   | ✅ ACTIF
 4  | High-Beta Underperf Short  | US      | Actions  | S   | Intraday   | ✅ ACTIF
 5  | Late Day Mean Reversion    | US      | Actions  | L/S | Intraday   | ⚠️ BORDERLINE
 6  | Failed Rally Short         | US      | Actions  | S   | Intraday   | ⚠️ BORDERLINE
 7  | EOD Sell Pressure V2       | US      | Actions  | S   | Intraday   | ⚠️ BORDERLINE
 8  | EU Gap Open                | EU      | Actions  | L/S | Intraday   | ✅ ACTIF
 9  | BCE Momentum Drift v2      | EU      | Actions  | L   | Event      | 🔜 EU-001
10  | Auto Sector German         | EU      | Actions  | L   | Event      | 🔜 EU-002
11  | Brent Lag Play             | EU→Fut  | Energy   | L/S | Intraday   | 🔜 EU-003→FUT-002
12  | EU Close → US Afternoon    | EU/US   | Actions  | L/S | Cross-tz   | 🔜 EU-004
13  | EUR/USD Trend              | FX      | Forex    | L/S | Swing      | ✅ ACTIF
14  | EUR/GBP Mean Reversion     | FX      | Forex    | L/S | Swing      | ✅ ACTIF
15  | EUR/JPY Carry              | FX      | Forex    | L   | Swing      | ✅ ACTIF
16  | AUD/JPY Carry              | FX      | Forex    | L   | Swing      | ✅ ACTIF
17  | GBP/USD Trend              | FX      | Forex    | L/S | Swing      | 🆕 FX-002
18  | USD/CHF Mean Reversion     | FX      | Forex    | L/S | Swing      | 🆕 FX-003
19  | MES Trend Following        | Futures | Index    | L/S | Swing      | 🆕 FUT-003
20  | MCL Brent Lag (futures)    | Futures | Energy   | L/S | Intraday   | 🆕 FUT-002
21  | FOMC Reaction              | US      | Actions  | L/S | Event      | 🆕 STRAT-009
22  | MNQ Mean Reversion         | Futures | Index    | L/S | Intraday   | 🆕 FUT-004
```

---

## ANNEXE B — MÉTRIQUES DE SUCCÈS

```
Métrique                          | Actuel  | S2     | S6      | S12
----------------------------------|---------|--------|---------|--------
Stratégies actives (alloc > 0)    | 7       | 12     | 18      | 22+
Classes d'actifs                  | 2       | 3      | 4       | 4
Allocation US/EU/FX/Fut/Cash      | 70/15/7/0/8 | 45/25/15/8/7 | 40/25/18/10/7 | 35/25/20/15/5
Heures capital actif /24h         | ~8h     | ~14h   | ~18h    | ~20h
Sharpe portefeuille               | ~2.82   | ~3.0   | ~3.5    | ~4.0
Corrélation moy inter-stratégies  | N/A     | <0.35  | <0.30   | <0.25
Coût moyen /trade pondéré         | ~0.15%  | <0.08% | <0.05%  | <0.03%
Max drawdown attendu              | ~3%     | <4%    | <5%     | <5%
```

---

## ANNEXE C — GRAPHE DE DÉPENDANCES

```
PARALLÈLE P0 (semaine 1-2) :
  INFRA-005 ──→ EU-001 + EU-002 + EU-003 + EU-004 ──→ ALLOC-001 ──→ FX-001

PARALLÈLE P1 (semaine 3-6) :
  Branche FX :  FX-002 | FX-003 | FX-004  (indépendants)
  Branche FUT : DATA-002 ──→ FUT-001 ──→ FUT-002 | FUT-003 | FUT-004
  Branche EVT : STRAT-009 | EU-005 (←EU-001)
  Transverse :  RISK-003 (←FUT-001 + ALLOC-001)

SÉQUENTIEL P2 (semaine 7-12) :
  FX-005 (←FX-002+003+004)
  FUT-005 + EU-006 (←FUT-001 + DATA-002)
  OPT-004 (←FUT-003 + FX-003)
  DASH-002 + RISK-004 (←RISK-003)
  ROC-002 (←30j paper)

P3 (semaine 13+) :
  FUT-006, OPT-005, FX-006, STRAT-010, LIVE-003, ALLOC-002
```

---

## ANNEXE D — CHECKLIST RAPIDE PAR PHASE

### Semaine 1-2 (P0) — Cocher quand fait
```
□ INFRA-005  Pipeline EU multi-strat
□ EU-001     BCE Momentum Drift v2
□ EU-002     Auto Sector German
□ EU-003     Brent Lag Play (proxy)
□ EU-004     EU Close → US Afternoon
□ ALLOC-001  Rebalancer allocation
□ FX-001     FX 7% → 18%
```

### Semaine 3-6 (P1) — Cocher quand fait
```
□ FX-002     GBP/USD Trend
□ FX-003     USD/CHF Mean Reversion
□ FX-004     NZD/USD Carry
□ DATA-002   Données futures historiques
□ FUT-001    Infrastructure futures IBKR
□ FUT-002    Brent Lag → futures CL
□ FUT-003    MES Trend Following
□ FUT-004    MNQ Mean Reversion
□ STRAT-009  FOMC Reaction
□ EU-005     BCE Press Conference
□ RISK-003   Risk framework futures+FX
```

### Semaine 7-12 (P2) — Cocher quand fait
```
□ FX-005     Cross-pair momentum FX
□ FUT-005    Gold (MGC) momentum
□ EU-006     STOXX 50 futures trend
□ OPT-004    Confluence cross-asset
□ DASH-002   Dashboard multi-marché
□ RISK-004   Stress test multi-marché
□ ROC-002    Analyse heures capital
```

### Semaine 13+ (P3) — Cocher quand fait
```
□ FUT-006    Calendar spread ES
□ OPT-005    Protective puts overlay
□ FX-006     EUR/NOK carry
□ STRAT-010  Lead-lag systématique
□ LIVE-003   Checklist live v2
□ ALLOC-002  Allocation dynamique
```

---

*TODO XXL Expansion V5 — 27 mars 2026*
*30 tâches | 4 branches parallèles | Cible : 22 stratégies, 4 classes d'actifs, 18h/24h*
*"La diversification est le seul repas gratuit en finance." — Harry Markowitz*
