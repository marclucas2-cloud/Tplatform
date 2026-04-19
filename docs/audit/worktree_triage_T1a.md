# Worktree Triage — T1a (H1 classification-first)

**As of** : 2026-04-19T15:05Z
**Phase** : T1a (classifier + documenter, **aucune action**). T1b (gitignore propose). T1c (actions).
**Mandat** : precision > debit. Reversibilite totale. Rien de supprime sans validation.
**Source** : `git status --short` + inspection `ls` de chaque dir + `.gitignore` existant.

---

## 1. Snapshot

- **94 changes** observes : 85 untracked (`??`) + 8 modified (`M`) + 1 deleted (`D`).
- **.gitignore existant** : couvre `__pycache__`, `*.parquet`, `data/state/`, `logs/`, `data/monitoring|execution|risk|tax|validation/`, `.pytest_cache/` (mais PAS `.pytest_tmp/`).
- **Buckets definis** :
  - **A** = tracked legit (M ou D reels, a commit ou revert)
  - **B** = untracked research/doc utile (a commit si canonique, a deplacer si mal classe)
  - **C** = generated artifacts (a gitignore, pas versionner)
  - **D** = trash / ephemeral (a gitignore)
  - **E** = scripts productifs non encore commit (decision cas-par-cas)

---

## 2. Legende colonnes

| Colonne | Signification |
|---|---|
| `chemin` | path dans le repo |
| `bucket` | A / B / C / D / E |
| `action proposee` | commit / gitignore / move+commit / revert / keep-untracked / inspect / delete |
| `justification` | pourquoi cette action |
| `risque si ignore (gitignore)` | ce qu'on perd si on gitignore |
| `risque si versionne (commit)` | ce qu'on perd si on commit |

---

## 3. Bucket A — Tracked changes (8 M + 1 D)

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `CLAUDE.md` | A | **inspect diff + commit** | change directive projet (instructions agent), critique | directives agent pas alignees | faible, fichier doctrine |
| `config/health_registry.yaml` | A | **inspect diff + commit** | config runtime, peut impacter boot preflight | health checks pas a jour sur VPS | faible |
| `config/limits_live.yaml` | A | **inspect diff + commit** | limites risk live, critique | limits runtime divergent | faible |
| `dashboard/frontend/src/pages/Crypto.jsx` | A | inspect diff + commit OU revert | UI dashboard, non critique live | UI obsolete VPS | faible |
| `dashboard/frontend/src/pages/Journal.jsx` | A | inspect diff + commit OU revert | UI dashboard | idem | faible |
| `docs/research/wf_reports/T1-02_us_pead.md` | A | inspect diff + commit | research report update | historique incomplet | faible |
| `scripts/backtest_ib_portfolio_v2.py` | A | inspect diff + commit | script recherche | perte amelioration | moderé (verifier non-regression consumers) |
| `scripts/migrate_state_to_book_convention.py` | A | inspect diff + commit | migration ops | script migration desaligne | faible |
| `TODO_XXXL_ROBUSTESSE.md` (deleted) | A | **commit delete** | fichier deja supprime, staging du delete | historique conserve | faible |

---

## 4. Bucket B — Untracked research / docs utile

### 4.1 WF reports (canonique docs/research/wf_reports/)

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `docs/research/wf_reports/INT-B_discovery_batch.md` | B | **commit** | WF batch report deja reference par `live_whitelist.yaml` | source de verite fragile | faible |
| `docs/research/wf_reports/INT-C_us_batch.md` | B | **commit** | idem | idem | faible |
| `docs/research/wf_reports/INT-D_crypto_batch.md` | B | **commit** | idem | idem | faible |
| `docs/research/wf_reports/T3A-01_mcl_overnight.md` | B | **commit** | wf_source `mcl_overnight` manifest references ce doc | strat paper sans docstring canonique | faible |
| `docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md` | B | **commit** | wf_source btc_asia | idem | faible |
| `docs/research/wf_reports/T3A-03_eu_indices_relmom.md` | B | **commit** | wf_source eu_relmom | idem | faible |
| `docs/research/wf_reports/T3B-01_us_sector_ls.md` | B | **commit** | wf_source us_sector_ls | idem | faible |
| `docs/research/wf_reports/T3B-02_pead_market_neutral.md` | B | **commit** | wf_source pead (strat archivée) | perte historique validation | faible |
| `docs/research/wf_reports/T4A-01_crypto_range_harvest.md` | B | **commit** | wf_source crypto range harvest (archivée) | idem | faible |
| `docs/research/wf_reports/T4A-02_crypto_relative_strength.md` | B | **commit** | wf_source alt_rel_strength | critique canonique | faible |

### 4.2 Docs audit externes (non canoniquement places)

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `docs/audit/deep_audit_2026-04-17_12-10.md` | B | **commit** | historique audit ChatGPT 17 avr | perte historique critique | faible |
| `docs/audit/deep_audit_2026-04-17_rerun.md` | B | **commit** | re-audit | idem | faible |
| `docs/audit/promotions/2026-04-17_gold_trend_mgc_promotion.md` | B | **commit** | historique promotion decision | perte audit trail | faible |
| `Todo/TODO_XXL_DECORRELATION_ROC_CAPITAL.md` | B | **move** `docs/todos/` + commit | hors convention repo; devrait etre sous `docs/todos/` ou `docs/audit/` | silo organizationnel | faible apres move |
| `Todo/TODO_XXL_DESK_PERSO_10_10_CLAUDE.md` | B | **move** `docs/todos/` + commit | idem | idem | faible |
| `AGENTS.md` | B | inspect contenu + move `docs/` ou commit root | fichier doctrine agent au root | dilution root | faible |
| `PROMPT_STRATEGY_DISCOVERY_ENGINE.md` | B | inspect + move `docs/research/` ou delete | brouillon prompt | retravail si supprime | faible |

### 4.3 Reports research / checkup

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `reports/checkup/checkup_2026-04-17.md` | B | **commit** | audit checkup ChatGPT 17 avr | perte audit historique | faible |
| `reports/checkup/cro_2026-04-17.md` | B | **commit** | idem | idem | faible |
| `reports/research/CLAUDE_CRYPTO_PROD_HANDOFF_2026-04-18.md` | B | **commit** | prod handoff doc | perte decisions go-live crypto | faible |
| `reports/research/CLAUDE_PROD_HANDOFF_2026-04-18.md` | B | **commit** | idem | idem | faible |
| `reports/research/bonds_backtest_2026-04-15.md` | B | **commit** | research bonds | historique research | faible |
| `reports/research/discovery_allocation_synthesis_2026-04-18.md` | B | **commit** | synthese discovery | idem | faible |
| `reports/research/discovery_crypto_synthesis_2026-04-18.md` | B | **commit** | idem | idem | faible |
| `reports/research/intraday_4strats_2026-04-15.md` | B | **commit** | research intraday | idem | faible |
| `reports/research/option3_2026-04-15.md` | B | **commit** | research note | idem | faible |
| `reports/research/overnight_indices_2026-04-15.md` | B | **commit** | research overnight | idem | faible |
| `reports/research/strategy_discovery_engine_2026-04-18.md` | B | **commit** | doc discovery | idem | faible |
| `reports/research/thesis_test_strat.md` | B | inspect + decide | nom ambigu "thesis_test_strat" — test ? | peu clair | faible |
| `reports/research/wf_overnight_mes_real_2026-04-15.md` | B | **commit** | research WF | idem | faible |
| `reports/review/` (dir, 4 fichiers review_2026-04-08/11/13/14.md) | B | **commit dir** | historique reviews weekly | audit trail | faible |
| `reports/us_research/` (dir, gate5_report + report + csv) | B | **commit dir** | US research batch | idem | **MODERE** (csv tracking = .csv gitignored donc exclus) |

### 4.4 Reports research / ambiguous json (nommage peu clair)

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `reports/research/auto_test_strat.json` | B/E | **inspect + decide** (contenu = ?) | nom ambigu | perte data test | faible si petit |
| `reports/research/corr_full_test.json` | B/E | **inspect + decide** | idem | idem | idem |
| `reports/research/costs_full_test.json` | B/E | **inspect + decide** | idem | idem | idem |
| `reports/research/quick_bad_strat.json` | B/E | **inspect + decide** ("bad" = rejected ?) | nom suggere rejet | perte historique rejet | faible |
| `reports/research/quick_full_test.json` | B/E | **inspect + decide** | idem | idem | faible |
| `reports/research/quick_good_strat.json` | B/E | **inspect + decide** | idem | idem | faible |
| `reports/research/sweep_test.json` | B/E | **inspect + decide** | sweep ? | idem | faible |
| `reports/research/wf_eu_indices.json` | B/E | **inspect + decide** | resultat WF | historique validation | faible |
| `reports/research/wf_eu_indices_v2.json` | B/E | **inspect + decide** | idem | idem | faible |
| `reports/research/wf_full_test.json` | B/E | **inspect + decide** | idem | idem | faible |
| `reports/research/wf_mib_estx50_corrected.json` | B | **commit** | source WF canonique referencee par whitelist (`wf_source: reports/research/wf_mib_estx50_corrected.json`) | **CRITIQUE** — whitelist pointe ce fichier, si absent promotion_gate break | faible |

---

## 5. Bucket C — Generated artifacts (runtime output, gitignore candidates)

### 5.1 `data/*` runtime dirs

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `data/alerts/` (alerts.jsonl) | **C** | **gitignore** | alertes runtime auto-log (pas humain) | aucun (incident JSONL = autre path canonique) | leak historique alertes sur git |
| `data/audit/` (orders_YYYY-MM-DD.jsonl) | **C** | **gitignore** | ordre audit trail auto-produce | aucun, fichier reconstruisible | leak info orders |
| `data/backups/` (subdirs dates) | **C** | **gitignore** | backups script auto | aucun, script backup re-produit | poids depot + leak historique |
| `data/incidents/` (2026-04-19.jsonl) | **C** | **gitignore** (garder `.gitkeep`) | JSONL auto incident timeline, 8 entries VPS | audit trail non partage | **MODERE** si leak sensitive info |
| `data/orchestrator/` (state.json) | **C** | **gitignore** | state orchestrator runtime | aucun recovery perso | leak state machine |
| `data/reconciliation/` (binance_crypto_YYYY-MM-DD.json) | **C** | **gitignore** | reconciliation snapshots auto | aucun, re-produit | leak positions |
| `data/research/strategy_discovery_engine_2026-04-18_scout_cards.json` | **C**/B | **inspect** | ambigu : generated ou research artifact ? | possible perte si research | faible si petit |
| `data/research_funnel/` (gold_trend_mgc.json) | **C** | **gitignore** | funnel auto signaux | aucun, re-produit | faible |
| `data/tickets/` (T-YYYY-MM-DD-NNN.json x5) | **C** | **inspect + decide** (humain ou auto ?) | tickets issues ? | possible perte audit | faible |
| `data/us_stocks/` (506 parquets, 30MB) | **C** | **gitignore (deja via *.parquet)** | **deja gitignore via line 28 `*.parquet`** MAIS le dir lui-meme apparait comme `??` | aucun, data re-downloadable via script | **GROS poids repo** si commit |

### 5.2 `docs/audit/promotions/` (existing dir, untracked contents)

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `docs/audit/promotions/2026-04-17_gold_trend_mgc_promotion.md` | **B** | **commit** (documentation decision) | audit trail promotion decision | perte historique promotion | faible |

---

## 6. Bucket D — Ephemeral / trash

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `.claude/` | **D** | **gitignore** | Claude agent workspace (worktrees, memory, chat) | aucun | leak convos agent |
| `.pytest_tmp/` | **D** | **gitignore** | pytest tempfiles (ajout `--basetemp`) | aucun | pollution |
| `temp/` (24 fichiers .py "bt_*", "check_*", "midcap_stat_arb_*") | **D**/**E** | **gitignore dir** puis **inspect content** pour extractions utiles | scripts brouillon hors convention scripts/ | **MODERE** — 24 scripts peuvent contenir idees research | duplication non-canonique |

---

## 7. Bucket E — Scripts productifs non encore commit

Tous dans `scripts/` ou `scripts/research/`. Tables comparatives.

### 7.1 Scripts racine `scripts/*.py` untracked

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `scripts/_audit_ib_executions.py` | E | **inspect** (underscore = debug ?) | nom suggere debug | possible outil utile | faible |
| `scripts/backtest_bonds.py` | E | **commit** | script research reproductible | perte si dev machine crash | faible |
| `scripts/backtest_final_portfolio.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_full_portfolio.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_fx_micro.py` | E | **commit** | idem (FX disabled mais historique) | idem | faible |
| `scripts/backtest_intraday_4strats.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_killswitch_10y.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_option3.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_overnight_indices.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_portfolio_3y.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_portfolio_clean.py` | E | **commit** | idem | idem | faible |
| `scripts/backtest_week_compare.py` | E | **commit** | idem | idem | faible |
| `scripts/corr_gold_oil_vs_live.py` | E | **commit** | diagnostic corr (utile ops) | idem | faible |
| `scripts/discover_backtest.py` | E | **commit** | discovery engine | idem | faible |
| `scripts/discover_backtest_long.py` | E | **commit** | idem | idem | faible |
| `scripts/discover_scan.py` | E | **commit** | idem | idem | faible |
| `scripts/download_mes_5y.py` | E | **commit** | data refresh MES 5Y | idem | faible |
| `scripts/explore_bear_capable_v2.py` | E | **commit** | research explore | idem | faible |
| `scripts/sweep_overnight_mes_mnq.py` | E | **commit** | sweep research | idem | faible |
| `scripts/wf_eu_indices.py` | E | **commit** | WF executable canonique | idem | faible |
| `scripts/wf_eu_indices_v2.py` | E | **commit** (v2 plus recent) | idem | idem | faible |
| `scripts/wf_gold_oil_rotation.py` | E | **commit** | WF executable canonique (deja reference dans iter3 B2 comme template) | **CRITIQUE** si re-run requis | faible |
| `scripts/wf_overnight_mes_real.py` | E | **commit** | WF executable | idem | faible |

### 7.2 `scripts/research/*.py` untracked

| chemin | bucket | action proposee | justification | risque si ignore | risque si versionne |
|---|---|---|---|---|---|
| `scripts/research/backtest_t3a_eu_indices_relmom.py` | E | **commit** | backtest T3A-A3 referenced by whitelist | critique canonique | faible |
| `scripts/research/backtest_t3a_mcl_overnight.py` | E | **commit** | backtest T3A-01 referenced | idem | faible |
| `scripts/research/backtest_t3a_mes_btc_leadlag.py` | E | **commit** | backtest T3A-02 referenced | idem | faible |
| `scripts/research/backtest_t3b_pead_market_neutral.py` | E | **commit** | backtest T3B-02 (strat archivee) | historique | faible |
| `scripts/research/backtest_t3b_us_sector_ls.py` | E | **commit** | backtest T3B-01 referenced | idem | faible |
| `scripts/research/backtest_t4_crypto_range_harvest.py` | E | **commit** | backtest T4A-01 (strat archivee) | historique | faible |
| `scripts/research/backtest_t4_crypto_relative_strength.py` | E | **commit** | backtest T4A-02 canonique (alt_rel_strength) | **CRITIQUE** reference paper strat live-candidate | faible |
| `scripts/research/int_b_discovery_wf_mc.py` | E | **commit** | WF discovery batch B | historique | faible |
| `scripts/research/int_c_us_batch_wf_mc.py` | E | **commit** | WF batch C | idem | faible |
| `scripts/research/int_d_crypto_batch_wf_mc.py` | E | **commit** | WF batch D | idem | faible |

---

## 8. Synthese quantifiee

| Bucket | Count | Action groupee | Risque |
|---|---|---|---|
| **A** tracked | 9 | inspect diff + commit/revert (9 decisions) | faible |
| **B** untracked research | ~32 | commit apres validation; 2 moves (Todo/) | faible |
| **B** reports/research json ambigus | 10 | inspect contenu cas-par-cas | faible |
| **C** generated artifacts | 10 dirs | **gitignore global** + `.gitkeep` dans `data/incidents/` | leak historique |
| **D** trash/ephemeral | 3 | gitignore (`.claude/`, `.pytest_tmp/`, `temp/`) | faible |
| **E** scripts research | 33 | **commit massif** (batch research) avec review noms | faible |

**Total decisions individuelles** : ~97 (somme).
**Total actions groupees** : ~5 (gitignore categories) + 2 moves + commit batches.

---

## 9. Sous-phases T1b et T1c (non executees ici)

### T1b — Propose `.gitignore` extensions (a valider par user)

Additions minimales pour couvrir bucket C+D sans effet secondaire :

```gitignore
# === Claude Code workspace (agent state, worktrees, memory) ===
.claude/
.pytest_tmp/

# === Temp scripts / experiments ===
temp/

# === Runtime data (auto-generated, non-reproductible humainement) ===
data/alerts/
data/audit/
data/backups/
data/incidents/
data/orchestrator/
data/reconciliation/
data/research_funnel/
data/tickets/
data/us_stocks/

# Note : *.parquet deja ignore globalement. `data/us_stocks/` dir
# apparait comme ?? car le dir n'etait pas track ; gitignore resout.
```

**A valider** : user choisit si `data/incidents/` doit garder un `.gitkeep` (pour audit trail partage) ou pas.

### T1c — Actions d'execution (apres T1b valide)

1. **Appliquer gitignore** : patch `.gitignore` puis `git rm -r --cached` si necessaire (ne devrait pas : dirs sont untracked).
2. **Bucket A** : `git diff` chaque fichier, user valide commit/revert.
3. **Bucket B** : commit en 3 batches :
   - batch 1 : WF reports (10 files) — low risk
   - batch 2 : audit/research reports (15 files) — low risk
   - batch 3 : moves Todo/ + AGENTS.md/PROMPT decisions (inspect content first) — requires user input
4. **Bucket B reports json ambigus** : `head -1 fichier.json` + decision cas par cas.
5. **Bucket E scripts** : commit massif en 2 batches (root scripts/ + scripts/research/).
6. **Bucket D temp/** : inspect 24 fichiers, extraire ceux utiles vers `scripts/research/`, gitignore le reste.

**Post T1c** : re-run `git status` cible **0 files modified + 0 files untracked** (hors gitignore et artefacts generes).

---

## 10. Risques majeurs identifies (a valider)

| # | Risque | Mitigation |
|---|---|---|
| 1 | Commit `scripts/_audit_ib_executions.py` si deprecated | inspect contenu avant commit |
| 2 | `reports/research/*_test.json` potentiels artefacts test accidentels | head chaque fichier |
| 3 | `data/us_stocks/` 30MB : gitignore evite pollution repo | deja couvert par `*.parquet` mais dir visible |
| 4 | `data/incidents/` gitignore = perte audit trail partage entre dev/prod | decision explicite user requise |
| 5 | `Todo/*.md` move pourrait casser des references markdown | grep references avant move |
| 6 | `temp/` 24 fichiers : certains peuvent contenir WF ou backtest non sauve ailleurs | inspect avant delete |
| 7 | `CLAUDE.md` M : change directive projet = impact AI | diff complet avant commit |

---

## 11. Ce que T1a NE fait PAS

- Aucune modification fichier.
- Aucune commande git mv / rm / add.
- Aucun `.gitignore` edit.
- Aucun commit.

**T1a = observation pure**. Le user valide le plan avant T1b (gitignore propose) puis T1c (actions execution).

---

## 12. Demande de validation user

1. **Bucket C gitignore** : OK pour la liste section 9 T1b ? (10 dirs)
2. **`data/incidents/` .gitkeep** : garder (audit trail partage) ou ignorer entierement ?
3. **Bucket A (8 M + 1 D)** : procedure inspect-diff-puis-commit acceptable ?
4. **Scripts `_audit_ib_executions.py`** et `temp/*` : inspect ligne-par-ligne ou bulk inspect contenu + decision ?
5. **Todo/ move vers `docs/todos/`** : OK cette convention ? ou prefere `docs/audit/todos/` ?
6. **`AGENTS.md` + `PROMPT_STRATEGY_DISCOVERY_ENGINE.md`** : garder root, mover docs/, ou delete ?
7. **Batch commits B et E** : granularite 3 batches (WF / audit+research / research scripts) acceptable ?

Apres ces reponses je prepare T1b (gitignore patch reel) puis T1c (execution).
