# Strategy Inventory Clean — H4 T7

**As of** : 2026-04-19T16:25Z
**Phase** : H4 TODO XXL hygiene. Consolidation decisionnelle post T1-T6.
**Livrable** : ce document. Tableau unique canonique.
**Sources** : `config/quant_registry.yaml` + `live_whitelist.yaml` + `books_registry.yaml` + [canonical_truth_map.md](canonical_truth_map.md) + [roc_reporting_contract.md](roc_reporting_contract.md).

---

## 0. Principe directeur T7

> Ce document **ne fait pas de recherche**. Il consolide decisions deja prises en T1-T6.
>
> Une strat n'apparait comme "interessante" que si elle est :
> - **promouvable** (paper validated + WF manifest + temps paper ≥ 30j visible)
> - **ROC-contributive** (marginal contribution justifie capital)
> - ou **explicitement gardee** pour raison precise (compat reglementaire futur, etc.)

**Anti-principe** : inventaire encyclopedique. Si une strat n'a pas de decision actionnable attachee, elle est **bruit historique** et flagee comme tel.

---

## 1. Synthese quantifiee (2026-04-19)

| Bucket | Count | Capital eligibility | ROC bucket |
|---|---|---|---|
| **LIVE_CORE** (tradent maintenant) | **2** | actuellement deploye | +ROC observe 13j (n=1 position) |
| **LIVE_PROBATION_CANDIDATE** (promo earliest M+1) | **4** | 0 actuel, max $8K post-promotion | projection +$450-900/an |
| **PAPER_ONLY** (en paper avec blockers connus) | **6** | 0 | n/a ou conditional |
| **RESEARCH_ONLY** (pas canonique worker) | **1** (midcap_stat_arb) | 0 | n/a |
| **DISABLED_PERMANENT** (ESMA / REJECTED) | **2** | 0 forever | 0 |
| **ARCHIVED_REJECTED** (drainees) | **15** | 0 forever | 0 |
| **TOTAL VIVANT** | **16 canoniques + 1 research** | $20,856 deployable, $228 at_risk | |

**Ratio signal vs bruit** :
- Strategies qui comptent vraiment pour le desk = **2 ACTIVE + 4 promo candidates = 6** (37.5% des 16)
- Strategies paper avec blockers reels (mib_estx50 capital, mcl friday, mes_wed MC, etc.) = 6 (37.5%)
- Bruit historique / disabled = **2 canoniques + 15 archivees = 17** (55% si on inclut archives)

---

## 2. Tableau canonique unifie — 16 strats + 1 research (consolidation T3/T4/T6)

### Schema colonnes
- `strategy_id` : identifiant canonique
- `book` : book_id
- `status` : enum quant_registry / whitelist
- `grade` : S/A/B/REJECTED/null(meta) — **source** : quant_registry
- `runtime_entrypoint` : lieu d'execution
- `source_of_truth` : manifest WF ou exempt_reason
- `capital_eligibility` : categorie T6 section 5 (ROC-contrib / occupation-seule / 0-alloc / n/a)
- `ROC_bucket` : estimation conservative annualisee
- `blocking_reason` : **le** blocker principal si non-active
- `next_decision_date` : date prochaine decision operative
- `bucket_T7` : classification decisionnelle T7 (LIVE_CORE / LIVE_PROBATION_CANDIDATE / PAPER_ONLY / DISABLED / etc.)

### 2.1 LIVE_CORE (2 strats) — tradent maintenant

| strategy_id | book | status | grade | entrypoint | source verite | capital elig | ROC | blocking | next date |
|---|---|---|---|---|---|---|---|---|---|
| `cross_asset_momentum` | ibkr_futures | live_core | **A** | `worker.py:_run_futures_cycle STRATS LIVE CAPABLE` via `strategies_v2.futures.cross_asset_momentum` | `data/research/wf_manifests/cross_asset_momentum_2026-04-19_backfill.json` | ROC-contrib | +6-7.5% an (haircut) | — (tradant MCL) | continue |
| `gold_oil_rotation` | ibkr_futures | live_core | **S** | `worker.py:_run_futures_cycle` via `strategies_v2.futures.gold_oil_rotation` | `data/research/wf_manifests/gold_oil_rotation_2026-04-19_backfill.json` + `scripts/wf_gold_oil_rotation.py` | ROC-contrib | +5-7% an (haircut) | signal dormant attente spread ≥ 2% | continue |

### 2.2 LIVE_PROBATION_CANDIDATE (4 strats) — prochaines promo

| strategy_id | book | status | grade | entrypoint | source verite | capital elig | ROC | blocking | next date |
|---|---|---|---|---|---|---|---|---|---|
| `gold_trend_mgc` | ibkr_futures | paper_only | **A** (iter3-fix B2) | `worker.py:_run_futures_cycle` via `strategies_v2.futures.gold_trend_mgc` | `data/research/wf_manifests/gold_trend_mgc_v1_2026-04-19.json` (OOS Sharpe 2.625, MC 0.15%) | **ROC-contrib** | +7-10% an | 30j paper validation | **2026-05-16 earliest promo** |
| `mes_monday_long_oc` | ibkr_futures | paper_only | B | `scripts/research/backtest_futures_calendar.py:variant_dow_long dow=0` | `data/research/wf_manifests/mes_monday_long_oc_2026-04-19_backfill.json` | ROC-contrib (modeste) | +4-6% an | 30j paper validation | **2026-05-16 earliest** |
| `alt_rel_strength_14_60_7` | binance_crypto | paper_only | B | `worker.py:run_alt_rel_strength_paper_cycle` via `core.runtime.alt_rel_strength_runner` | `data/research/wf_manifests/alt_rel_strength_14_60_7_2026-04-19_backfill.json` | **ROC-contrib** (corr -0.014) | +6-9% an | 30j paper + data freshness BTCUSDT alts (B6r residuel) | **2026-05-18 earliest** |
| `btc_asia_mes_leadlag_q80_v80_long_only` | binance_crypto | paper_only | B (iter3-fix B5) | `worker.py:run_btc_asia_mes_leadlag_paper_cycle` mode=long_only via `strategies.crypto.btc_asia_mes_leadlag` | `data/research/wf_manifests/btc_asia_mes_leadlag_q70_v80_2026-04-19_backfill.json` (variante q80 long-only) | ROC-contrib | +3-5% an | 30j paper (start 2026-04-20 post wire) + data freshness MES_1H_YF2Y (fixe B6) | **2026-05-20 earliest** |

**Total capital probation post-promotion** : max ~$6-8K gross si toutes validees.

### 2.3 PAPER_ONLY avec blockers (6 strats) — ne doivent pas etre promues actuellement

| strategy_id | book | status | grade | blocker principal | capital elig T6 | next date |
|---|---|---|---|---|---|---|
| `mes_wednesday_long_oc` | ibkr_futures | paper_only | B | **MC P(DD>30%)=28.3% limite** | occupation-seule | 2026-06-01 surveillance 45j (vs 30j) |
| `mes_pre_holiday_long` | ibkr_futures | paper_only | B | Trade rare 8-10/an (0.02/jour seul) | occupation-seule seul, OK cohorte | 2026-06-15 apres mes_monday |
| `mcl_overnight_mon_trend10` | ibkr_futures | paper_only | B | **friday_trigger re-WF requis** (signal runtime vendredi vs backtest lundi) | occupation-seule tant que re-WF | re-WF a scaffolder |
| `btc_asia_mes_leadlag_q70_v80` | binance_crypto | paper_only | B | **mode=both incompat Binance France spot** | 0-allocation | conserver paper seulement (variante q80 long_only est promo candidate) |
| `eu_relmom_40_3` | ibkr_eu | paper_only | B | **Shorts EU indices sans plan CFD/futures mini concret** | 0-allocation book paper_only | plan shorts a definir |
| `mib_estx50_spread` | ibkr_eu | paper_only | **S** | **Capital EUR 3.6K gap** (13.5K margin requise, 9.9K dispo) + book paper_only | ROC-contrib **si** funding | **decision user funding 2026-04-20** |

### 2.4 AUTHORIZED meta (1 strat) — sans WF unique

| strategy_id | book | status | grade | blocker | capital elig | next date |
|---|---|---|---|---|---|---|
| `us_stocks_daily` | alpaca_us | paper_only | null (meta) | PDT waiver $25K requis + `wf_exempt_reason=meta_portfolio_aggregate` | 0-allocation tant que gate | Alpaca gate GO_25K (earliest 2026-05-18) |

### 2.5 READY avec blockers structurels (1 strat)

| strategy_id | book | status | grade | blocker | capital elig | next date |
|---|---|---|---|---|---|---|
| `us_sector_ls_40_5` | alpaca_us | paper_only | B | **Shorts sectors PDT + re-WF ETF data requis** | 0-allocation | re-WF ETF + gate GO_25K |

### 2.6 DISABLED_PERMANENT (2 strats)

| strategy_id | book | status | grade | raison | action |
|---|---|---|---|---|---|
| `btc_dominance_rotation_v2` | binance_crypto | disabled | **REJECTED** | Logic historically broken | archived_rejected list, **jamais promouvoir** |
| `fx_carry_momentum_filter` | ibkr_fx | disabled | — | **ESMA EU leverage limits reglementaire** | conserver code pour re-enable futur (si ESMA change), **0 capital** |

### 2.7 RESEARCH_ONLY (1 strat, pas canonique worker)

| strategy_id | source | status | blocker | next decision |
|---|---|---|---|---|
| `midcap_stat_arb` (extrait temp/ iter3-fix T1c-B3) | `scripts/research/midcap_stat_arb/` | **research** | Pas integre au worker, pas de WF canonique, pas dans quant_registry | N/A — recherche pool, integration future = decision user explicite |

### 2.8 ARCHIVED_REJECTED (15 strats)

| # | strategy_id | bucket drain | raison |
|---|---|---|---|
| 1 | basis_carry_crypto | A 2026-04-19 | REJECTED WF |
| 2 | btc_eth_dual_momentum | A | REJECTED |
| 3 | borrow_rate_carry | A | REJECTED |
| 4 | funding_rate_arb | A | REJECTED |
| 5 | ld_earn_yield_harvest | A | REJECTED |
| 6 | liquidation_momentum | A | REJECTED |
| 7 | liquidation_spike | A | INSUFFICIENT_TRADES |
| 8 | mr_scalp_btc | A | NEEDS_RE_WF never traite |
| 9 | trend_short_btc | A | idem |
| 10 | triangular_arb | A | REJECTED |
| 11 | weekend_gap | A | REJECTED |
| 12 | eu_gap_open | C | REJECTED (2026-03-31 WF) |
| 13 | vix_mean_reversion | C | INSUFFICIENT_TRADES 0/5 |
| 14 | gold_equity_divergence | C | INSUFFICIENT_TRADES 0/5 |
| 15 | sector_rotation_eu | C | INSUFFICIENT_TRADES 1/5 |

**Regle** : aucune reactivation sans **nouveau WF VALIDATED complet**. Source : `config/quant_registry.yaml#archived_rejected`.

---

## 3. Classification decisionnelle par bucket T7

### 3.1 Strategies qui COMPTENT pour le desk

**LIVE_CORE + LIVE_PROBATION_CANDIDATE** = **6 strats** (37.5% des 16 canoniques).

**Cumul ROC projection 30j post toutes promotions validees** :
- CAM + GOR live : +12-15% annualise blended portefeuille
- +mes_monday + gold_trend_mgc V1 : +4-10% additif
- +alt_rel_strength : +6-9%
- +btc_asia q80 long_only : +3-5%

**Total conservateur** : ~15-25% annualise blended sur $15-20K deployed post-promotions.

### 3.2 Strategies qui peuvent devenir interessantes sous conditions

**PAPER_ONLY avec blockers leves** = **3 strats** (mes_wednesday si MC recalibre, mcl_overnight si re-WF friday, mib_estx50 si funding).

**Conditions explicites** :
- mes_wednesday : MC additionnel re-run avec plus de data + seuil P(DD>30%) < 15%
- mcl_overnight : scaffolder `scripts/research/re_wf_mcl_friday_trigger.py` + grade confirme
- mib_estx50 : decision user funding +EUR 3.6K (capital-dependent, hors tech)

### 3.3 Strategies qui sont juste du bruit historique

**DISABLED + ARCHIVED_REJECTED = 17 strats** (53% total si on inclut les 15 archives).

**Ne pas leur donner attention** :
- 15 archived → dans `quant_registry.archived_rejected` liste + `strategies/_archive/`
- 2 DISABLED → conserve code pour re-enable eventuel (fx_carry ESMA, btc_dominance REJECTED)
- Pas de place dans le dashboard courant (filtre out).

### 3.4 Strategies "joli statut sans verite"

**Aucune identifee en T7**. Les iters precedents (T1-T6) ont deja :
- Quarantaine des tests pointant archived (iter3-fix B8 + T2 H2)
- Sync registries 0 divergence (T3 H3)
- WF manifests physiques pour toutes les actives non-exempt (iter3-fix B2)
- Filtre quant_registry propre (16 actives + 15 archivees clair)

**Verifie** via `scripts/runtime_audit.py --strict` → `No registry/runtime incoherences detected`.

---

## 4. DoD — 5 questions user (reponses < 2 min)

### Q1 : Combien de strategies existent vraiment encore ?

**16 canoniques** (section 2.1 + 2.2 + 2.3 + 2.4 + 2.5 + 2.6) = toutes les strats dans `quant_registry.strategies` actives.

Plus 1 research_only (`midcap_stat_arb`, non canonique worker).

Plus 15 archived_rejected (section 2.8).

**Total historique** : 16 + 1 + 15 = **32 references** mais seulement **16 vivantes dans le systeme** + 1 research offline.

### Q2 : Combien comptent vraiment pour le desk ?

**6 strats** (LIVE_CORE + LIVE_PROBATION_CANDIDATE, section 3.1) :
1. cross_asset_momentum (live)
2. gold_oil_rotation (live)
3. gold_trend_mgc (probation 2026-05-16)
4. mes_monday_long_oc (probation 2026-05-16)
5. alt_rel_strength_14_60_7 (probation 2026-05-18)
6. btc_asia_mes_leadlag_q80_v80_long_only (probation 2026-05-20)

Ces 6 = 37.5% de 16 canoniques. Le reste = paper blockers, disabled, archives.

### Q3 : Lesquelles meritent du capital ?

Section 3.1 + classification T6 section 5.1. Ordre priorite funding :

| Strat | Justification capital | Capital max recommande |
|---|---|---|
| cross_asset_momentum | ACTIVE, grade A, corr faible portfolio | inchangee ($500 risk/entry) |
| gold_oil_rotation | ACTIVE, grade S | inchangee ($500 risk/entry) |
| gold_trend_mgc V1 post 2026-05-16 | grade A, MC 0.15% exceptional | $500 risk (1 contrat MGC) |
| alt_rel_strength post 2026-05-18 | grade B, corr -0.014, bull+bear robust | $3K gross (6 legs × $500) |
| mes_monday post 2026-05-16 | grade B, WF 3/5, MC 9.8% | $300 risk (1 contrat MES) |
| btc_asia q80 long_only post 2026-05-20 | grade B | $500-800 notional |

**Conditional** : `mib_estx50_spread` (grade S) merite EUR 13.5K margin **si** user funde +EUR 3.6K.

### Q4 : Lesquelles doivent rester a 0 ?

**17 strats** (cumul) :
- 2 DISABLED canoniques : `fx_carry_momentum_filter` (ESMA), `btc_dominance_rotation_v2` (REJECTED)
- 15 archived_rejected (section 2.8)
- **Plus tant que blockers non resolus** :
  - `mes_wednesday_long_oc` (MC limite)
  - `mes_pre_holiday_long` seul (trade rare)
  - `mcl_overnight_mon_trend10` (re-WF friday)
  - `btc_asia_mes_leadlag_q70_v80` mode=both (incompat FR)
  - `eu_relmom_40_3` (shorts sans plan)
  - `us_stocks_daily` + `us_sector_ls_40_5` (PDT + re-WF ETF)

### Q5 : Lesquelles sont juste du bruit historique ?

**15 archived_rejected** (section 2.8). Plus les 2 DISABLED (fx_carry + btc_dominance) dans une moindre mesure.

Ces strats :
- N'apparaissent pas dans dashboard live
- Sont filtrees par `runtime_audit.py` (section archived separee)
- Leurs tests sont dans `tests/_archive/` (T2 quarantine)
- Leur code est dans `strategies/_archive/`

**Action** : ne pas les reactiver sans nouveau WF VALIDATED. Ignorer pour operation.

---

## 5. Next decision dates — calendrier consolide

| Date | Evenement | Strats concernees | Action user |
|---|---|---|---|
| **2026-04-20 (lundi)** | Premier weekday post iter3-fix | mes_monday, mes_wed, eu_relmom, us_sector_ls, mib_estx50, btc_asia | Verif paper runners fire (section T5 commandes lundi matin) |
| **2026-04-20** | Decision funding mib_estx50 | mib_estx50_spread grade S | User : OUI/NON +EUR 3.6K |
| **2026-04-27 (semaine 2)** | Mid-checkpoint paper divergence | alt_rel_strength J+9 | runtime_audit hebdo |
| **2026-05-16** | 30j paper mes_monday + gold_trend_mgc | Les 2 ibkr_futures | Decision promotion live_core / live_probation |
| **2026-05-18** | 30j paper alt_rel_strength + Alpaca gate check | alt_rel_strength + us_sector_ls | `alpaca_go_25k_gate.py` re-run + promotion_check |
| **2026-05-20** | 30j paper btc_asia q80 | btc_asia long_only | Decision promotion |
| **2026-06-01** | Surveillance etendue mes_wednesday (45j) | mes_wednesday MC limite | Decision promotion ou extend |
| **2026-06-30** | Bilan mensuel post Mai | Toutes | Reporting live_performance_may2026.md |

---

## 6. Champs ecrits noir sur blanc (consolidation T3-T6 verifiee)

| Champ strategie | Ou il vit | Qui l'ecrit | Qui le lit |
|---|---|---|---|
| `strategy_id` | live_whitelist + quant_registry | humain PR | runtime_audit + promotion_gate + dashboard |
| `book` | live_whitelist + quant_registry | humain PR | pre_order_guard + runtime_audit |
| `status` | live_whitelist + quant_registry | humain PR | pre_order_guard + promotion_gate |
| `grade` | quant_registry | calcule WF | promotion_gate |
| `runtime_entrypoint` | live_whitelist | humain PR | verification existence |
| `wf_manifest_path` (source_of_truth) | quant_registry | humain PR (physical file) | promotion_gate verify_physical |
| `paper_start_at` | quant_registry | humain PR | alpaca_gate + promotion_gate |
| `live_start_at` | quant_registry | humain PR (promotion day) | historique |
| `kill_criteria` | live_whitelist | humain PR | kill_switch_live |
| `infra_gaps` | quant_registry | humain PR (narratif) | dashboard info |
| `wf_exempt_reason` | quant_registry | humain PR | runtime_audit tolerance |
| **capital_eligibility** (T7) | **ce document** | **derive** T7 de T6 section 5 | allocation decision |
| **ROC_bucket** (T7) | **ce document** | **derive** T7 de T6 | allocation decision |
| **blocking_reason** (T7) | **ce document** | **derive** T7 de quant/runtime | dashboard + priorisation |
| **next_decision_date** (T7) | **ce document** | **derive** T7 de paper_start + 30j | reporting hebdo |

**Note** : les 4 derniers champs (T7-specific) sont **derives** — pas canoniques dans aucun YAML. Ils sont calcules depuis les sources canoniques + T6 regles. Ils ne doivent **pas** etre encodes dans les YAML (violation principe T3 canonique vs derive).

---

## 7. Actions immediates post-T7

### Aucune action code requise T7

T7 est une **consolidation** sans nouveau code. Tous les gaps identifies en T2-T6 restent :
- P1 : `scripts/capital_occupancy_report.py` + `roc_per_strategy.py` + `marginal_contribution.py`
- P1 : `tests/test_alpaca_go_25k_gate.py`
- P2 : `tests/test_state_file_contracts.py` + `tests/test_canonical_truth_invariants.py`
- P2 : `scripts/weekly_truth_review.py`
- P2 : 5 modules governance/execution a 0% coverage (T2)

### Actions operationnelles lundi matin 2026-04-20

Heritage direct de T5 section 5 + T6 section 6. Pas de nouveau.

---

## 8. Ligne rouge T7 respectee

- ✅ **Consolidation, pas recherche nouvelle**
- ✅ Tableau unique consolide 16 canoniques + 15 archivees + 1 research
- ✅ Chaque strat a : status, grade, source_of_truth, capital_eligibility, blocking_reason, next_decision_date
- ✅ 4 buckets decisionnels (LIVE_CORE / PROBATION_CANDIDATE / PAPER_BLOCKED / 0_ALLOC / ARCHIVED)
- ✅ Pas de "statut joli" sans verite quant + runtime + allocation
- ✅ DoD 5 questions user repondues (section 4)
- ✅ Calendrier decisions explicite (section 5)
- ✅ Champs derives vs canoniques distingues (section 6)

**Prochain** : T8 H8 scoring policy. Formalisation regles de notation (date, environnement, sources, formule) pour tous scores futurs. Puis T9 H9 ops hygiene + T10 H10 desk_operating_truth synthese finale.
