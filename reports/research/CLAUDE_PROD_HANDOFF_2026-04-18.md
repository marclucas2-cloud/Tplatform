# Claude Prod Handoff - 2026-04-18

## Mission

Ce document est un handoff actionnable pour faire passer les meilleures stratégies de recherche vers une intégration **paper puis prod** dans ce repo, en respectant les garde-fous existants.

Objectif attendu de Claude:

1. relire les preuves existantes
2. choisir l'ordre de promotion réaliste
3. implémenter les stratégies retenues dans le runtime du projet
4. ajouter les tests, le câblage worker, la config et la gouvernance nécessaires
5. ne promouvoir en `live_probation` que ce qui est compatible avec le statut réel du book et les règles de risque

Ce document n'est **pas** une instruction de tout passer live d'un coup.

## Règles non négociables

Claude doit respecter en priorité:

- `no lookahead`
- coûts réels
- walk-forward obligatoire
- paper d'abord
- stop-loss obligatoire sur tout ordre live
- `_authorized_by` obligatoire sur tous les ordres
- shorts en quantité entière, pas en notional

Rappels repo:

- books live-allowed aujourd'hui:
  - `binance_crypto`
  - `ibkr_futures`
- books encore paper-only:
  - `alpaca_us`
  - `ibkr_eu`
- book bloqué:
  - `ibkr_fx`

Conséquence immédiate:

- les candidats `Alpaca` et `IBKR EU` peuvent être **intégrés** proprement, mais pas promus live tant que le statut du book ne change pas
- seuls les candidats `IBKR futures` et `Binance` sont potentiellement éligibles à un vrai chemin `paper -> live_probation`

## Lecture minimale obligatoire

Avant de modifier quoi que ce soit, Claude doit lire:

- [reports/research/discovery_allocation_synthesis_2026-04-18.md](C:/Users/barqu/trading-platform/reports/research/discovery_allocation_synthesis_2026-04-18.md)
- [docs/research/wf_reports/INT-B_discovery_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-B_discovery_batch.md)
- [docs/research/wf_reports/INT-C_us_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-C_us_batch.md)
- [docs/research/hypothesis_registry.md](C:/Users/barqu/trading-platform/docs/research/hypothesis_registry.md)
- [docs/research/dropped_hypotheses.md](C:/Users/barqu/trading-platform/docs/research/dropped_hypotheses.md)
- [config/books_registry.yaml](C:/Users/barqu/trading-platform/config/books_registry.yaml)
- [config/live_whitelist.yaml](C:/Users/barqu/trading-platform/config/live_whitelist.yaml)
- [core/governance/pre_order_guard.py](C:/Users/barqu/trading-platform/core/governance/pre_order_guard.py)
- [worker.py](C:/Users/barqu/trading-platform/worker.py)

Et il doit inspecter les patterns d'intégration déjà en place dans:

- [strategies_v2](C:/Users/barqu/trading-platform/strategies_v2)
- [strategies](C:/Users/barqu/trading-platform/strategies)
- [scripts/research_funnel.py](C:/Users/barqu/trading-platform/scripts/research_funnel.py)
- [scripts/promotion_committee.py](C:/Users/barqu/trading-platform/scripts/promotion_committee.py)
- [tests](C:/Users/barqu/trading-platform/tests)

## Stratégies à considérer

### Priorité A - Candidats les plus actionnables

#### 1. `mcl_overnight_mon_trend10`

- Book cible: `ibkr_futures`
- Statut recherche: **VALIDATED**
- Preuves:
  - [docs/research/wf_reports/T3A-01_mcl_overnight.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3A-01_mcl_overnight.md)
  - [docs/research/wf_reports/INT-B_discovery_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-B_discovery_batch.md)
- Résumé:
  - Sharpe standalone `+0.80`
  - MaxDD `-4.3%`
  - WF `4/5`
  - MC `P(DD>30%) = 0.0%`
- C'est probablement le **meilleur nouveau candidat futures**
- Cible de promotion:
  - intégration runtime
  - `paper`
  - puis `live_probation` si les garde-fous d'exécution sont propres

#### 2. `pre_holiday_drift`

- Book cible: `ibkr_futures`
- Statut recherche: **VALIDATED** existant
- Preuves:
  - [docs/research/wf_reports/INT-A_tier1_validation.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-A_tier1_validation.md)
- Résumé:
  - WF `5/5`
  - queue faible
  - bonne diversification calendrier
- Cible de promotion:
  - intégrer comme petite sleeve explicite
  - taille modeste
  - ne pas surpondérer face aux autres sleeves MES

#### 3. `long_wed_oc`

- Book cible: `ibkr_futures`
- Statut recherche: **VALIDATED** existant
- Preuves:
  - [docs/research/wf_reports/INT-A_tier1_validation.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-A_tier1_validation.md)
- Résumé:
  - WF `4/5`
  - bonne contribution marginale
- Cible de promotion:
  - petite sleeve calendrier
  - à intégrer avec prudence pour éviter le clustering MES

#### 4. `btc_asia_mes_leadlag_q70_v80`

- Book cible: `binance_crypto`
- Statut recherche: **VALIDATED**
- Preuves:
  - [docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md)
  - [docs/research/wf_reports/INT-B_discovery_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-B_discovery_batch.md)
- Résumé:
  - Sharpe standalone `+1.07`
  - WF `4/5`
  - MC `0.0%`
- Cible de promotion:
  - d'abord intégration paper/log-only ou probation très contrôlée
  - vérifier horaires, latence, logique de sizing, frais spot/margin réels

### Priorité B - Bons candidats mais pas live-eligibles immédiatement

#### 5. `us_sector_ls_40_5`

- Book cible: `alpaca_us`
- Statut recherche: **VALIDATED**
- Preuves:
  - [docs/research/wf_reports/T3B-01_us_sector_ls.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3B-01_us_sector_ls.md)
  - [docs/research/wf_reports/INT-C_us_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-C_us_batch.md)
- Résumé:
  - Sharpe standalone `+0.39`
  - MaxDD `-2.1%`
  - WF `3/5`
  - MC `0.0%`
- Cible de promotion:
  - intégration `paper_only`
  - pas de live tant que `alpaca_us` reste paper-only

#### 6. `eu_relmom_40_3`

- Book cible: `ibkr_eu`
- Statut recherche: **VALIDATED**
- Preuves:
  - [docs/research/wf_reports/T3A-03_eu_indices_relmom.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3A-03_eu_indices_relmom.md)
  - [docs/research/wf_reports/INT-B_discovery_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-B_discovery_batch.md)
- Résumé:
  - Sharpe standalone `+0.71`
  - MaxDD `-0.8%`
  - WF `4/5`
  - MC `0.0%`
- Cible de promotion:
  - intégration `paper_only`
  - pas de live tant que `ibkr_eu` reste paper-only

### Priorité C - Réévaluer avant intégration sérieuse

#### 7. `us_pead`

- Book cible: `alpaca_us`
- Statut: **near-promotion**
- Pourquoi pas plus haut:
  - bon score marginal historique
  - mais pas revalidé fraîchement dans le batch de cette session
- Consigne:
  - rafraîchir WF/MC avant de l'intégrer profondément dans le runtime

#### 8. `crypto_long_short`

- Book cible: `binance_crypto`
- Statut: **near-promotion**
- Pourquoi pas plus haut:
  - bon candidat dispersion
  - mais pas de WF frais dans cette session
- Consigne:
  - rafraîchir validation avant promotion runtime sérieuse

## À ne pas promouvoir en prod

Claude ne doit **pas** passer en prod, dans leur forme actuelle:

- `PEAD market-neutral`
  - preuves:
    - [docs/research/wf_reports/T3B-02_pead_market_neutral.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3B-02_pead_market_neutral.md)
  - raison:
    - dégradation du `delta MaxDD` portefeuille
    - verdict `DROP`

- `basis_carry_funding_gt_5pct`
  - raison:
    - blocage produit/broker

- `crypto regime-reactive dominance filter`
  - raison:
    - dataset dominance BTC cassé / inutilisable

- `FX cross-sectional carry`
  - raison:
    - book et contraintes réglementaires actuelles

## Ordre de travail recommandé pour Claude

### Phase 1 - Productionisation low-risk sur books live-allowed

1. intégrer `mcl_overnight_mon_trend10` dans le book `ibkr_futures`
2. intégrer `pre_holiday_drift` comme sleeve calendrier explicite
3. intégrer `long_wed_oc` comme sleeve calendrier explicite
4. intégrer `btc_asia_mes_leadlag_q70_v80` dans `binance_crypto`

### Phase 2 - Intégration paper-only

5. intégrer `us_sector_ls_40_5` dans `alpaca_us` en `paper_only`
6. intégrer `eu_relmom_40_3` dans `ibkr_eu` en `paper_only`

### Phase 3 - Refresh de validation avant promotion

7. relancer WF/MC de `us_pead`
8. relancer WF/MC de `crypto_long_short`

## Ce que Claude doit probablement modifier

Selon l'architecture actuelle, Claude devra probablement toucher:

- `strategies_v2/futures/` pour les nouvelles sleeves futures
- `strategies_v2/crypto/` ou l'emplacement crypto cohérent avec le repo
- `strategies_v2/us/` ou équivalent pour `us_sector_ls_40_5`
- `strategies_v2/eu/` ou équivalent pour `eu_relmom_40_3`
- `config/strategies/` pour les configs de stratégie
- `worker.py` et/ou les runners par book pour le câblage runtime
- `tests/` pour tests unitaires + tests d'intégration
- `config/live_whitelist.yaml` **uniquement** pour les sleeves qu'il décide de promouvoir réellement

Il doit éviter les contournements ad hoc dans `worker.py` si un pattern de stratégie déclarative existe déjà.

## Exigences d'intégration

Pour chaque stratégie retenue, Claude doit produire:

1. une implémentation runtime claire
2. une config dédiée
3. des tests
4. un coût model cohérent avec le broker réel
5. un passage paper ou live_probation compatible avec `books_registry.yaml`
6. une décision de sizing compatible avec l'allocation ci-dessous

Et pour toute activation live:

1. ordre toujours tracé avec `_authorized_by`
2. stop-loss présent si la stratégie produit des ordres live
3. respect des guards `pre_order_guard`
4. aucune stratégie paper-only ne doit être promue live sans changement explicite du statut du book

## Allocation cible proposée

Claude peut utiliser cette allocation comme point de départ, **sans la figer aveuglément**.

| Sleeve / stratégie | Poids cible |
|---|---:|
| Futures core existant | 30% |
| `mcl_overnight_mon_trend10` | 10% |
| `pre_holiday_drift` | 5% |
| `long_wed_oc` | 5% |
| `btc_asia_mes_leadlag_q70_v80` | 10% |
| `crypto_long_short` | 10% |
| `us_sector_ls_40_5` | 12% |
| `us_pead` | 8% |
| `eu_relmom_40_3` | 10% |

Interprétation:

- ne pas dépasser `20%` cumulé sur les nouvelles sleeves futures discrétionnaires
- conserver les books paper-only comme sleeves paper, pas live
- garder une marge de sécurité si le runtime réel impose un sizing inférieur

## Chemins de preuve à conserver

Pour toute PR ou changement proposé, Claude doit citer ces artefacts:

- [reports/research/discovery_allocation_synthesis_2026-04-18.md](C:/Users/barqu/trading-platform/reports/research/discovery_allocation_synthesis_2026-04-18.md)
- [docs/research/wf_reports/T3A-01_mcl_overnight.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3A-01_mcl_overnight.md)
- [docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md)
- [docs/research/wf_reports/T3A-03_eu_indices_relmom.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3A-03_eu_indices_relmom.md)
- [docs/research/wf_reports/T3B-01_us_sector_ls.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3B-01_us_sector_ls.md)
- [docs/research/wf_reports/T3B-02_pead_market_neutral.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T3B-02_pead_market_neutral.md)
- [docs/research/wf_reports/INT-B_discovery_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-B_discovery_batch.md)
- [docs/research/wf_reports/INT-C_us_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-C_us_batch.md)

## Résumé exécutif

Si Claude doit aller au plus efficace:

1. promouvoir d'abord `mcl_overnight_mon_trend10`
2. ajouter `pre_holiday_drift` et `long_wed_oc` en petites sleeves
3. mettre `btc_asia_mes_leadlag_q70_v80` en chemin `paper -> live_probation`
4. intégrer `us_sector_ls_40_5` et `eu_relmom_40_3` en paper-only
5. ne pas perdre du temps à productioniser `PEAD market-neutral`

Le repo a déjà assez de matière pour passer du stade recherche au stade intégration contrôlée. Claude n'a pas besoin de repartir en exploration d'idées; il doit surtout faire le travail d'intégration, de test, de gouvernance et de sizing.
