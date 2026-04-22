# Canonical Truth Map — H3 T3

**As of** : 2026-04-19T15:45Z
**Phase** : H3 TODO XXL hygiene. Verite canonique + precedence + invariants, pas juste coherence.
**Source** : `config/books_registry.yaml` + `config/live_whitelist.yaml` + `config/quant_registry.yaml` + `config/health_registry.yaml` + `scripts/runtime_audit.py` + `core/governance/*`.
**Verifie** : `python scripts/_canonical_truth_check.py` (ephemere) → **0 divergence statique** sur books + strategies x book x status.

---

## 0. Principe directeur T3

> Les 4 YAML sont **canoniques complementaires**, pas redondants.
> Chaque registre a son **domaine de verite propre**.
> Precedence explicite requise pour resolver les cas limites.

**Anti-principe** : "un YAML unique pour tout". Volontairement refuse — separation of concerns.

---

## 1. Les 4 registres canoniques — domaine de verite

| Registre | Fichier | Owner conceptuel | Question repondue | Role |
|---|---|---|---|---|
| **books_registry** | `config/books_registry.yaml` | Architect + PO | "Ce book a-t-il le droit de tourner en live, avec quel capital, sous quels limits ?" | **doctrine** |
| **live_whitelist** | `config/live_whitelist.yaml` | PO + governance | "Cette strat est-elle autorisee a trader sur ce book, dans quel mode ?" | **autorisation** |
| **quant_registry** | `config/quant_registry.yaml` | Quant + research | "Cette strat est-elle quant-validee (grade, WF manifest, paper_start_at) ?" | **quant validation** |
| **health_registry** | `config/health_registry.yaml` | Ops | "Ce book est-il physiquement GREEN/DEGRADED/BLOCKED maintenant ?" | **runtime health** |

### Regle cle

> Aucun de ces registres **a lui seul** ne repond a "cette strat peut trader maintenant ?".
> La reponse est une **chaine AND** des 4 :
> `books.mode_authorized=live_allowed` AND `whitelist.status in (live_*)` AND `quant.grade in (S,A,B) AND wf_manifest exists` AND `health.status != BLOCKED`.

---

## 2. Hierarchie de precedence (cas de contradiction)

### Regle generale

```
1. books_registry  (doctrine)        → PREVAUT pour : mode_authorized, capital_budget, risk_budget, execution_window, kill_switch_scope
2. live_whitelist  (autorisation)    → PREVAUT pour : strategy status enum (live_core/live_probation/paper_only/disabled), runtime_entrypoint, kill_criteria
3. quant_registry  (quant validation)→ PREVAUT pour : grade, wf_manifest_path, paper_start_at, live_start_at, is_live (observe)
4. health_registry (runtime)         → PREVAUT pour : GREEN/DEGRADED/BLOCKED status, critical_checks, important_checks
```

### Cas limites resolus

#### Conflit 1 : books_registry dit `paper_only` mais live_whitelist dit `live_core` pour une strat
**Resolution** : **books_registry gagne**. Raison : le mode du book est une doctrine superieure. Strat devient de facto paper_only + `VIOLATION` detectee dans `_canonical_truth_check.py`.

#### Conflit 2 : live_whitelist dit `live_core` mais quant_registry dit `grade=REJECTED`
**Resolution** : **quant_registry gagne**. Raison : REJECTED signifie WF fail ; autoriser le live = violation gouvernance. Strat forcee `disabled` en runtime, incident CRITICAL.

#### Conflit 3 : live_whitelist dit `status=paper_only` sans `wf_manifest_path` dans quant_registry
**Resolution** : **quant_registry gagne via `wf_exempt_reason`**. Si `wf_exempt_reason` present (meta-portfolio, V1 recalibration...), tolere. Sinon `PAPER_WITHOUT_WF` warning dans runtime_audit.

#### Conflit 4 : health_registry dit `BLOCKED` mais pre_order_guard a autorise un ordre plus tot
**Resolution** : **health_registry gagne toujours a l'instant T**. Orders en cours restent (position ouverte) mais nouveaux ordres refuses jusqu'a retour GREEN/DEGRADED.

#### Conflit 5 : quant_registry dit `is_live=true` mais aucune position observee sur VPS
**Resolution** : **runtime observation gagne**. `is_live` quant_registry doit etre **derive du runtime**, pas declaratif. Incoherence = bug registry.

### Tableau resume

| Champ | Source canonique prevaut | Source derivee (derivee depuis canonique) |
|---|---|---|
| mode_authorized (book) | **books_registry** | — |
| capital_budget_usd | **books_registry** | — |
| risk_budget limits | **books_registry** | — |
| execution_window | **books_registry** | — |
| kill_switch_scope | **books_registry** | `core/kill_switch_live.py` configure scope |
| state_files paths | **books_registry** | consomme par `book_health.py` + `reconciliation.py` |
| strategy status enum | **live_whitelist** (wins) | `quant_registry.status` doit matcher (invariant) |
| runtime_entrypoint | **live_whitelist** | `scripts/runtime_audit.py` verifie existence |
| kill_criteria | **live_whitelist** | `kill_switch_live.py` lit pour auto-disable |
| sizing_policy | **live_whitelist** | |
| grade (S/A/B/REJECTED) | **quant_registry** | calcule via `core/research/wf_canonical` |
| wf_manifest_path | **quant_registry** | fichier physique verifie par `promotion_gate` |
| paper_start_at | **quant_registry** | consomme par `alpaca_go_25k_gate` + promotion 30j |
| live_start_at | **quant_registry** | historique promotion decision |
| is_live | **quant_registry (derivee)** | **DOIT** reflect runtime observation |
| infra_gaps | **quant_registry** | listes blockers promotion (informatif) |
| wf_exempt_reason | **quant_registry** | tolerance PAPER_WITHOUT_WF runtime_audit |
| critical/important_checks | **health_registry** | consomme par `book_health.py` |
| status runtime (GREEN/DEGRADED/BLOCKED) | **health_registry** | **calcule dynamiquement** par `book_health.py` |
| archived_rejected list | **quant_registry** | strat archives, never-promote |

---

## 3. Invariants statiques (verifies par `_canonical_truth_check.py`)

**Status** : tous verts au 2026-04-19T15:45Z.

### I1 — Books presents dans les 4 registries
**Regle** : chaque `book_id` doit apparaitre dans `books_registry` + `live_whitelist` + `quant_registry` + `health_registry`.

**Verification** : 5/5 books alignees (alpaca_us, binance_crypto, ibkr_eu, ibkr_futures, ibkr_fx). ✅

### I2 — Couplage strat x book coherent
**Regle** : pour toute strat, `live_whitelist.entry.book == quant_registry.entry.book`.

**Verification** : 16/16 strats alignees. ✅

### I3 — Couplage strat x status coherent
**Regle** : pour toute strat, `live_whitelist.entry.status == quant_registry.entry.status` (same enum).

**Verification** : 16/16 strats alignees (live_core, paper_only, disabled). ✅

### I4 — Strat dans whitelist ⇒ strat dans quant
**Regle** : toute strat dans `live_whitelist` doit avoir entree dans `quant_registry.strategies`.

**Verification** : 16 ∩ 16 = 16. 0 orphan whitelist. ✅

### I5 — Strat dans quant ⇒ strat dans whitelist (ou archived)
**Regle** : toute strat dans `quant_registry.strategies` doit etre dans `live_whitelist` (sinon elle doit etre dans `quant_registry.archived_rejected`).

**Verification** : 16/16 strats actives dans whitelist. 15/15 dans archived_rejected (drainees). ✅

### I6 — books.mode_authorized=disabled ⇒ 0 strat live
**Regle** : si `books.mode_authorized=disabled`, aucune strat du book ne peut avoir `status=live_core` ou `live_probation`.

**Verification** : `ibkr_fx mode=disabled strats=1 live=0`. ✅

### I7 — books.mode_authorized=paper_only ⇒ 0 strat live
**Regle** : si `books.mode_authorized=paper_only`, idem I6.

**Verification** :
- `ibkr_eu mode=paper_only strats=2 live=0` ✅
- `alpaca_us mode=paper_only strats=2 live=0` ✅

### I8 — Strat status=paper_only ⇒ paper_start_at NOT NULL
**Regle** : quant_registry entry avec `status=paper_only` doit avoir `paper_start_at` renseigne.

**Verification** : 13 paper_only / 13 avec paper_start_at. ✅ (voir `promotion_gate.py`)

### I9 — Strat status=live_core/live_probation ⇒ live_start_at NOT NULL
**Regle** : quant_registry entry avec `status=live_*` doit avoir `live_start_at`.

**Verification** : 2 live_core (CAM + GOR) / 2 avec live_start_at. ✅

### I10 — Strat grade=REJECTED ⇒ status=disabled (sinon warning)
**Regle** : si `quant.grade=REJECTED`, `status` doit etre `disabled`.

**Verification** : `btc_dominance_rotation_v2 grade=REJECTED status=disabled`. ✅

### I11 — wf_manifest_path existe OU wf_exempt_reason renseigne (si status != disabled)
**Regle** : strats non-disabled doivent avoir `wf_manifest_path` (fichier physique) OR `wf_exempt_reason`.

**Verification** : 15 strats non-disabled :
- 14 avec wf_manifest_path (fichiers presents `data/research/wf_manifests/*.json`)
- 1 avec wf_exempt_reason (`us_stocks_daily: meta_portfolio_aggregate`)
- post iter3-fix B2 : `gold_trend_mgc` a recupere wf_manifest_path (v1_2026-04-19.json).
✅

### I12 — Archived_rejected ⇒ absent de whitelist
**Regle** : toute strat dans `quant_registry.archived_rejected` doit etre **absente** de `live_whitelist`.

**Verification** : 15 archived / 0 within whitelist. ✅

---

## 4. Matrice des statuts (canoniques derives)

> Ces statuts ne sont **pas** un champ unique dans un YAML. Ils sont **derives dynamiquement** par `runtime_audit.py` a partir des 4 registries + observation runtime.

| Statut canonique | books.mode_authorized | whitelist.status | quant grade | wf_manifest | paper_start_at | runtime is_live | Decision |
|---|---|---|---|---|---|---|---|
| **AUTHORIZED** | `live_allowed` | paper_only OU live_* | n/a | n/a | n/a | false | book+whitelist OK mais pas quant-valide |
| **READY** | `live_allowed` | paper_only | S/A/B | exists (OR exempt) | NOT NULL | false | quant-valide, attend 30j paper |
| **PROMOTABLE** | `live_allowed` | paper_only | S/A/B | exists | paper_days ≥ 30 ET pas de divergence > 1-2σ ET promotion_gate OK | false | candidate immediate live |
| **ACTIVE** | `live_allowed` | live_core / live_probation | S/A/B | exists | NOT NULL | true + position observee | trade maintenant |
| **DISABLED** | n/a | disabled | n/a ou REJECTED | n/a | n/a | false | interdit |
| **ARCHIVED** | n/a (absent whitelist) | absent | n/a | n/a | n/a | false | dans quant_registry.archived_rejected |

### Etat courant des 16 strats canoniques (as_of 2026-04-19T14:33Z VPS)

| strategy_id | book | Statut canonique | Justification |
|---|---|---|---|
| cross_asset_momentum | ibkr_futures | **ACTIVE** | live_core + position MCL observee + grade A |
| gold_oil_rotation | ibkr_futures | **ACTIVE** | live_core + grade S + observed dormant signal |
| gold_trend_mgc | ibkr_futures | **READY (grade A iter3-fix B2)** | paper_only + wf_manifest_v1 present + paper_start 2026-04-16 |
| mes_monday_long_oc | ibkr_futures | **READY** | grade B + WF 3/5 + MC 9.8% + paper 2026-04-16 |
| mes_wednesday_long_oc | ibkr_futures | **READY (MC limite)** | grade B + MC 28.3% flagged |
| mes_pre_holiday_long | ibkr_futures | **READY (trade rare)** | grade B + WF 5/5 mais 8-10 trades/an |
| mcl_overnight_mon_trend10 | ibkr_futures | **READY (re-WF requis)** | grade B + friday_trigger re-WF pending |
| alt_rel_strength_14_60_7 | binance_crypto | **READY** | grade B + paper 2026-04-18 |
| btc_asia_mes_leadlag_q70_v80 | binance_crypto | **READY (mode both)** | grade B + mode both incompatible spot FR |
| btc_asia_mes_leadlag_q80_v80_long_only | binance_crypto | **READY (iter3-fix B5)** | grade B + long_only compat FR + paper 2026-04-20 |
| btc_dominance_rotation_v2 | binance_crypto | **DISABLED** | grade REJECTED + logic broken |
| eu_relmom_40_3 | ibkr_eu | **READY (book paper-only)** | grade B mais book mode=paper_only |
| mib_estx50_spread | ibkr_eu | **READY (capital gap)** | grade S mais book paper_only + margin EUR 3.6K manque |
| fx_carry_momentum_filter | ibkr_fx | **DISABLED** | ESMA reglementaire |
| us_stocks_daily | alpaca_us | **INFRA_ORCHESTRATOR** (2026-04-22 degrade PO) | meta-wrapper, exclu du scoreboard strat canoniques |
| us_sector_ls_40_5 | alpaca_us | **READY** | grade B + re-WF ETF pending + book paper_only |

**Cardinal** (post 2026-04-22 PO cleanup): 2 ACTIVE + 11 READY (dont 5 blockers infra) + 1 INFRA_ORCHESTRATOR (us_stocks_daily) + 1 DISABLED reglementaire (fx_carry) + 1 DISABLED REJECTED (btc_dominance).

**Note** : 16 archives REJECTED (bucket A drain 11 + bucket C 4 + bucket D 2026-04-22 btc_asia_mes_leadlag_q70_v80 duplicate).

---

## 5. Consumers par registry (qui lit quoi)

| Fichier / Module | Lit books_reg | Lit live_wl | Lit quant_reg | Lit health_reg | Role |
|---|---|---|---|---|---|
| `scripts/runtime_audit.py` | ✅ | ✅ | ✅ | ✅ | full audit |
| `core/governance/pre_order_guard.py` | ✅ | ✅ | - | (via book_health) | check 1-6b |
| `core/governance/promotion_gate.py` | - | ✅ | ✅ | - | paper -> live gate |
| `core/governance/book_health.py` | ✅ | - | - | ✅ | GREEN/DEGRADED/BLOCKED |
| `core/governance/registry_loader.py` | ✅ | ✅ | - | ✅ | boot loader |
| `core/governance/live_whitelist.py` | - | ✅ | - | - | integrity check |
| `core/governance/quant_registry.py` | - | - | ✅ | - | parse entries |
| `core/governance/strategy_status.py` | ✅ | ✅ | ✅ | ✅ | status enum calcule |
| `core/kill_switch_live.py` | ✅ (scope) | ✅ (kill_criteria) | - | - | kill + scoped disable |
| `worker.py` main + cycles | ✅ | ✅ | - | ✅ (boot) | runtime entry |
| `scripts/alpaca_go_25k_gate.py` | - | - | ✅ (paper_start, book) | - | Alpaca go/no-go |
| Dashboard `/api/governance/strategies/status` | ✅ | ✅ | ✅ | ✅ | UI display |

**Observation** : `runtime_audit.py` est le **unique consumer** qui calcule le statut canonique derive. C'est l'autorite runtime pour :
```
  (STRATEGY_ID) -> (AUTHORIZED | READY | ACTIVE | PROMOTABLE | DISABLED | ARCHIVED)
```

---

## 6. Risques si divergence entre registries

### Divergence books_registry vs live_whitelist (mode vs status)
**Impact live** : HAUT. Ex : book mode=paper_only mais strat status=live_core → worker pourrait tenter ordre live sur book non autorise.
**Detection** : `_canonical_truth_check.py` invariants I6/I7.
**Reponse** : `pre_order_guard` check 3 fail-closed + incident CRITICAL.
**Mitigation** : tests `test_live_whitelist.py` + runtime_audit cycle.

### Divergence live_whitelist vs quant_registry (status)
**Impact live** : HAUT. Ex : whitelist=live_core, quant=REJECTED → trade autorise sur strat cassee.
**Detection** : invariant I3 + I10.
**Reponse** : `promotion_gate` block + runtime_audit incoherence detected.
**Mitigation** : test_promotion_gate + test_strategy_status.

### Divergence quant_registry.is_live vs runtime observe
**Impact live** : MOYEN. Ex : is_live=true mais 0 position observee → dashboard ment.
**Detection** : reconciliation_cycle + positions_live.json observation.
**Reponse** : reconciliation warning (paper_only) OR critical (live-allowed).
**Mitigation** : `is_live` doit etre **derive**, pas saisie manuelle.

### Divergence health_registry vs runtime
**Impact live** : HAUT. Ex : health-registry definit check critical non implemente dans book_health.py → faux GREEN.
**Detection** : code review + tests book_runtime.
**Reponse** : ajouter check ou retirer entry registry.
**Mitigation** : test_book_runtime + test_health_and_state_hardening.

---

## 7. Champs canoniques vs derives (clarifier la nature)

| Champ | Nature | Source | Consommateur |
|---|---|---|---|
| `books.mode_authorized` | **canonique** (declare) | humain PR books_registry | pre_order_guard, runtime_audit |
| `books.capital_budget_usd` | **canonique** | humain PR | allocator, dashboard |
| `whitelist.status` | **canonique** | humain PR live_whitelist | runtime_audit, promotion_gate |
| `whitelist.kill_criteria` | **canonique** | humain PR | kill_switch_live |
| `quant.grade` | **derive** | `core/research/wf_canonical` calcule depuis WF manifest | promotion_gate, runtime_audit |
| `quant.wf_manifest_path` | **canonique** | humain PR (pointe fichier produit par script WF) | promotion_gate verify_physical |
| `quant.paper_start_at` | **canonique** | humain PR (date debut paper) | alpaca_gate, promotion 30j |
| `quant.live_start_at` | **canonique** | humain PR (date promotion) | historique |
| `quant.is_live` | **derive** | observation runtime (positions_live.json + cycle tournant) | dashboard |
| `quant.infra_gaps` | **canonique** (narratif) | humain PR | dashboard info |
| `quant.wf_exempt_reason` | **canonique** (narratif) | humain PR | runtime_audit tolerance |
| `health.critical_checks` | **canonique** | humain PR | book_health.py |
| book_health status (GREEN/DEGRADED/BLOCKED) | **derive** | `book_health.py` aggregation | pre_order_guard, dashboard |
| statut canonique strat (ACTIVE/READY/...) | **derive** | `runtime_audit.py` + `strategy_status.py` | dashboard, reports, audit |

**Principe** : tout champ `derive` ne doit **JAMAIS** etre ecrit manuellement dans un YAML.

---

## 8. Tests couvrant les invariants I1-I12

| Invariant | Tests couvrant |
|---|---|
| I1 (books presents 4 registries) | `test_book_runtime.py`, `test_boot_preflight.py` |
| I2 (strat x book coherent) | `test_live_whitelist.py`, `test_quant_registry.py` |
| I3 (strat x status coherent) | `test_strategy_status.py` |
| I4-I5 (strat presence in both) | `test_live_whitelist.py`, `test_quant_registry.py` |
| I6-I7 (book mode vs strat status) | `test_live_whitelist.py`, `test_book_runtime.py` |
| I8 (paper_only ⇒ paper_start_at) | `test_promotion_gate.py`, `test_quant_registry.py` |
| I9 (live_* ⇒ live_start_at) | `test_promotion_gate.py` |
| I10 (grade REJECTED ⇒ disabled) | `test_live_whitelist.py`, `test_promotion_gate.py` |
| I11 (wf_manifest OR exempt) | `test_promotion_gate.py` |
| I12 (archived ⇒ absent whitelist) | `test_live_whitelist.py` |

**Gap test identifie** : `_canonical_truth_check.py` est ephemere (supprime a la fin T3). Il serait **valuable de formaliser en test** `test_canonical_truth_invariants.py` pour verifier les 12 invariants en CI. **P2 backlog**.

---

## 9. Definition of Done — 4 questions user (reponses < 2 min)

### Q1 : Qui dit si une strat a le droit d'etre live ?

**Chaine AND** dans cet ordre (tout doit passer) :
1. `books_registry.{book}.mode_authorized == "live_allowed"` (sinon DENY)
2. `live_whitelist[{book}].{strat}.status in ("live_core", "live_probation")` (sinon DENY)
3. `quant_registry.{strat}.grade in ("S", "A", "B")` (sinon DENY)
4. `quant_registry.{strat}.wf_manifest_path` existe physiquement OU `wf_exempt_reason` present (sinon DENY)

**Autorite finale** : `core/governance/pre_order_guard.py` execute cette chaine a chaque ordre.

### Q2 : Qui dit si une strat est quant-validee ?

**`quant_registry` seul**. Champs canoniques :
- `grade != REJECTED`
- `wf_manifest_path` pointe vers `data/research/wf_manifests/*.json` physique
- `paper_start_at` renseigne si `status=paper_only`

**Verifie par** : `core/governance/promotion_gate.py:check()`.

### Q3 : Qui dit si un book peut tourner ?

**Chaine** :
1. `books_registry.{book}.mode_authorized != "disabled"` (doctrine)
2. `health_registry.{book}.critical_checks` tous PASS en runtime

**Autorite runtime** : `core/governance/book_health.py:check_{book}()` retourne `BLOCKED` si critical fail.

### Q4 : Qui dit si une strat est reellement prete (PROMOTABLE) ?

**Chaine** (derive via `runtime_audit.py` + `promotion_gate.py`) :
1. Statut canonique courant = READY (definition section 4)
2. `paper_days = (today - quant.paper_start_at) ≥ 30` (configurable via `--min-days`)
3. `paper_pnl_net >= 0` sur la periode (via journal)
4. Divergence paper vs backtest `<= 1σ-2σ` (selon gate)
5. `incidents_open_p0p1 == 0` pour ce book sur la periode
6. Pour Alpaca : `scripts/alpaca_go_25k_gate.py` retourne exit 0 (GO_25K)

**Autorite** : `promotion_gate.check()` + `alpaca_go_25k_gate.py` (Alpaca specifique).

---

## 10. Corrections minimales identifiees T3

### T3d status : 0 correction majeure requise

Les 4 registries sont statiquement coherents (verification `_canonical_truth_check.py`). Toutes les corrections vraiment necessaires ont deja ete livrees par iter3-fix + iter3-fix2 :

- `quant_registry.gold_trend_mgc` : `wf_exempt_reason` retire, `grade=A`, `wf_manifest_path` assigne (iter3-fix B2)
- `live_whitelist` + `quant_registry` : btc_asia q80_long_only ajoute (iter3-fix B5)
- 5 books alignes dans les 4 registries

### Ameliorations mineures optionnelles (P3 backlog, pas urgent)

1. **Retirer commentaires narratifs vivants dans live_whitelist.yaml** (v2, v3, v4, v5, v6, v7 dans metadata.notes) → deplacer dans `docs/audit/whitelist_history.md`.
2. **Formaliser `_canonical_truth_check.py` en test** `tests/test_canonical_truth_invariants.py` (CI gate).
3. **Reduire champs snapshot operatoires** dans `books_registry.yaml` notes (equity $9,843, $11,013) → derive dynamiquement depuis VPS.

### Recommandation T3d

**Ne PAS faire** rewrite massif YAML maintenant (ligne rouge user). Les 3 ameliorations sont tracees comme P3, peuvent attendre Phase 2 post-T10.

---

## 11. Ligne rouge T3 respectee

- ✅ Pas de rewrite massif YAML
- ✅ Cartographie avant propositions
- ✅ Contradictions detectees (0 trouvees) **avant** toute correction
- ✅ Precedence definie noir sur blanc (section 2)
- ✅ Invariants documentes (section 3)
- ✅ Ordre de precedence explicite (section 2 + section 7)
- ✅ Corrections minimales uniquement (section 10)

**Livrable** : ce document + verification script ephemere (supprime post-commit).

---

## 12. Prochaine phase

**Recommendation user** : T4 = H5 data/state, T5 = H6 runtime/ops, T6 = H7 ROC, T7 = H4 inventaire, puis H8-H10.

**Couplage T3 ↔ T4 H5** : les 9 `data/*/README.md` produits en T1b sont des **contrats state files preview**. H5 les formalisera en matrice complete `state_file_contracts.md`.

**Bonus P2** : le gap identifie `test_alpaca_go_25k_gate.py` absent + 5 modules governance/execution a 0% coverage restent au backlog P1/P2.
