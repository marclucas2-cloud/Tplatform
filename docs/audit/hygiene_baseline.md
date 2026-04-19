# Hygiene Baseline — H0 TODO XXL

**As of** : 2026-04-19T15:01Z
**Role** : snapshot canonique avant execution H1-H10. Toute mesure d'hygiene ulterieure se compare a ce baseline.
**Source** : `docs/audit/TODO_XXL_HYGIENE.md` directive user 2026-04-19.

---

## 1. Commandes de verite — output brut

### 1.1 pytest (suite complete)

**Commande** :
```
python -m pytest -q -o cache_dir=.pytest_cache --basetemp .pytest_tmp
```

**Resultat** :
```
3669 passed, 50 skipped, 2380 warnings in 204.60s
```

**Exit code** : 0
**Interpretation** : suite saine, 0 fail. 50 skips restent (cf H2).
**Warnings 2380** : non diagnostique ici (cf H2).

### 1.2 runtime_audit local (dev Windows)

**Commande** :
```
python scripts/runtime_audit.py --strict
```

**Resultat essentiel** :
- Boot preflight : **FAIL (1 critical failures)**
  - FAIL `equity_state::ibkr_futures` absent (attendu sur dev, pas P0 live)
  - FAIL warnings data MES_1D / MES_LONG / MGC_1D / MCL_1D stales 200h-466h (non rafraichis localement)
  - OK registries (books + live_whitelist + quant_registry)
  - OK ibkr_gateway TCP 178.104.125.74:4002 reachable
- Strategies : **16 total** (2 ACTIVE + 11 READY + 1 AUTHORIZED + 2 DISABLED)
- **No registry/runtime incoherences detected**

**Exit code** : **3** (FAIL attendu, dev-env)
**Interpretation** : local n'a pas de state files live (normal). Preflight FAIL NE bloque PAS la decision business. VPS fait foi.

### 1.3 runtime_audit VPS (production)

**Commande** (executee via SSH) :
```
ssh vps "cd /opt/trading-platform && PYTHONPATH=. .venv/bin/python scripts/runtime_audit.py --strict"
```

**Resultat essentiel** :
- Boot preflight : **OK (0 critical failures)** — 12/12 preflight PASS
- Data fresh : MES_1D 41h / MGC_1D 41h / MCL_1D 41h (cron Mon-Fri 21:30 UTC)
- Strategies : **16 total** (identique au local)
- **0 registry/runtime incoherences**

**Exit code** : **0**
**Interpretation** : VPS clean, production prete.

### 1.4 alpaca_go_25k_gate

**Commande** :
```
python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5
```

**Resultat brut** :
```
Paper start    : 2026-04-18
Paper days     : 1
Trades ferme   : 0
PnL net paper  : $0.00
Max DD paper   : 0.00%
WR paper       : 0.0%
Incidents P0/P1: 0
Verdict        : NO_GO_paper_journal_missing
Reasons        : paper_journal_missing
Recommendation : Paper journal absent. Verifier que le paper runner ecrit bien sur VPS.
```

**Exit code** : **2** (NO_GO)
**Interpretation** : paper journal non present localement. A re-run post premier cycle weekday `run_us_sector_ls_paper_cycle` VPS (lundi 2026-04-20 23h30 Paris).

---

## 2. Git status — snapshot worktree

### 2.1 Distribution par type

| Type | Count | Description |
|---|---|---|
| `??` untracked | **85** | fichiers non tracked (research reports, generated data, TODO, temp...) |
| `M` modified | **8** | fichiers tracked modifies non stashed |
| `D` deleted | **1** | `TODO_XXXL_ROBUSTESSE.md` supprime non commit |
| **TOTAL changes** | **94** | |

### 2.2 Classification 4 buckets (draft)

#### Bucket A : tracked legit (a commit ou reverter)
- `M CLAUDE.md` — changement directive projet, a reviewer
- `M config/health_registry.yaml` — modification runtime
- `M config/limits_live.yaml` — modification runtime
- `M dashboard/frontend/src/pages/Crypto.jsx` — UI
- `M dashboard/frontend/src/pages/Journal.jsx` — UI
- `M docs/research/wf_reports/T1-02_us_pead.md` — research update
- `M scripts/backtest_ib_portfolio_v2.py` — research
- `M scripts/migrate_state_to_book_convention.py` — ops
- `D TODO_XXXL_ROBUSTESSE.md` — deja supprime, a commiter le delete

#### Bucket B : untracked research utile (a commit ou a deplacer)
Scripts de backtest non commit :
- `scripts/backtest_bonds.py`, `backtest_final_portfolio.py`, `backtest_full_portfolio.py`, `backtest_fx_micro.py`, `backtest_intraday_4strats.py`, `backtest_killswitch_10y.py`, `backtest_option3.py`, `backtest_overnight_indices.py`, `backtest_portfolio_3y.py`, `backtest_portfolio_clean.py`, `backtest_week_compare.py`
- `scripts/discover_*.py`, `scripts/explore_bear_capable_v2.py`
- `scripts/research/backtest_t3a_*.py`, `backtest_t3b_*.py`, `backtest_t4_*.py`, `int_b_discovery_wf_mc.py`, etc.
- `scripts/wf_eu_indices.py`, `wf_eu_indices_v2.py`, `wf_gold_oil_rotation.py`, `wf_overnight_mes_real.py`
- `scripts/sweep_overnight_mes_mnq.py`
- `scripts/download_mes_5y.py`
- `scripts/corr_gold_oil_vs_live.py`
- `scripts/_audit_ib_executions.py` (underscore prefix = private ?)

Reports de recherche :
- `reports/research/*.json` (multiple) + `.md` (multiple)
- `reports/checkup/checkup_2026-04-17.md`, `cro_2026-04-17.md`
- `reports/research/CLAUDE_*.md`

WF reports :
- `docs/research/wf_reports/INT-B_*`, `INT-C_*`, `INT-D_*`, `T3A-*`, `T3B-*`, `T4A-*` (~10 fichiers)
- `docs/audit/deep_audit_2026-04-17_*.md` (re-audits historiques)
- `docs/audit/promotions/` (sous-dossier)

Docs root-level potentiellement mal places :
- `AGENTS.md`
- `PROMPT_STRATEGY_DISCOVERY_ENGINE.md`

TODOs hors docs/audit :
- `Todo/TODO_XXL_DECORRELATION_ROC_CAPITAL.md`
- `Todo/TODO_XXL_DESK_PERSO_10_10_CLAUDE.md`

#### Bucket C : generated artifacts (data produite par scripts, pas de commit humain)
- `data/alerts/` (nouveau dir)
- `data/audit/`
- `data/backups/`
- `data/incidents/` (JSONL auto-log)
- `data/orchestrator/`
- `data/reconciliation/`
- `data/research/strategy_discovery_engine_2026-04-18_scout_cards.json`
- `data/research_funnel/`
- `data/tickets/`
- `data/us_stocks/`

Status : doivent probablement etre **gitignored** (generes a l'execution).

#### Bucket D : trash / ephemeral
- `.claude/` — agent workspace, gitignore
- `.pytest_tmp/` — test tempfiles, gitignore
- `temp/` — temp dir, gitignore
- Scripts `scripts/_*.py` (underscore = private debug ?) : `_audit_ib_executions.py`, `_debug_log_handlers.py` (je l'avais cree iter3-fix, je pensais supprime — a verifier)

### 2.3 Risques identifies dans worktree

| Risque | Instance observee | Impact live / decision |
|---|---|---|
| **Brouillons research dans docs/ critiques** | `docs/research/wf_reports/INT-*` non tracked | Faux signal si doc canonique manque |
| **Scripts backtest non committes** | 11 scripts `backtest_*` en ?? | Possibilite perte si machine crash |
| **Generated data tracked via git status** | `data/audit/`, `data/backups/`, `data/incidents/` | Risque commit accidentel de state runtime |
| **TODOs hors convention docs/audit/** | `Todo/*.md` | Lecteur peut louper directive user |
| **Scripts "_prefixes" debugueurs** | `scripts/_audit_ib_executions.py` | Possible mort / obsolete |
| **Reports json non nommes canoniquement** | `reports/research/auto_test_strat.json`, `quick_bad_strat.json` | Noms ne disent pas leur provenance |

---

## 3. Strategies — snapshot canonique (verifie vs 3 registries)

**Source** : `python scripts/runtime_audit.py --strict` local + VPS.

| ID | Book | Status runtime | Grade | infra_gaps |
|---|---|---|---|---|
| cross_asset_momentum | ibkr_futures | **ACTIVE** | A | 0 |
| gold_oil_rotation | ibkr_futures | **ACTIVE** | S | 0 |
| gold_trend_mgc | ibkr_futures | READY | A (post iter3-fix B2) | 0 |
| mes_monday_long_oc | ibkr_futures | READY | B | 0 |
| mes_wednesday_long_oc | ibkr_futures | READY | B | MC 28.3% limite |
| mes_pre_holiday_long | ibkr_futures | READY | B | trade rare 8-10/an |
| mcl_overnight_mon_trend10 | ibkr_futures | READY | B | re-WF friday trigger |
| alt_rel_strength_14_60_7 | binance_crypto | READY | B | data stale alts + strat hebdo |
| btc_asia_mes_leadlag_q70_v80 | binance_crypto | READY | B | mode both incompat spot FR |
| btc_asia_mes_leadlag_q80_v80_long_only | binance_crypto | READY | B (iter3-fix B5) | 0 |
| btc_dominance_rotation_v2 | binance_crypto | DISABLED | REJECTED | logic broken |
| eu_relmom_40_3 | ibkr_eu | READY | B | shorts sans plan |
| mib_estx50_spread | ibkr_eu | READY | S | margin EUR gap |
| us_sector_ls_40_5 | alpaca_us | READY | B | shorts PDT + re-WF ETF |
| us_stocks_daily | alpaca_us | AUTHORIZED | meta | PDT waiver requis |
| fx_carry_momentum_filter | ibkr_fx | DISABLED | — | ESMA |

**Cardinal** : 16 strats = 2 ACTIVE + 11 READY + 1 AUTHORIZED + 2 DISABLED + 0 contradictions.
**Archived REJECTED** : 15 strats dans `strategies/_archive/` (bucket A + bucket C drain).

---

## 4. VPS etat live (broker observation 2026-04-19T14:08Z)

| Book | Equity | Positions | Unrealized | Source |
|---|---|---|---|---|
| ibkr_futures | $11,012.79 | MCL 1 contrat (CAM) | +$295.23 | `data/state/ibkr_futures/equity_state.json` + `positions_live.json` |
| binance_crypto | $9,843 | 0 | $0 | `data/state/binance_crypto/equity_state.json` |
| alpaca_us | $99,495.42 paper | 0 live | $0 | Alpaca API via worker log |

**Total capital deployable live** : **$20,856** ($11K IBKR + $9.8K Binance).
**Capital a risque actuellement** : $228 (MCL SL-entry) = **1.09%**. **98.9% idle**.

---

## 5. Scores 4 dimensions (post iter3-fix2, pre-hygiene XXL)

| Dimension | Score | Source of truth |
|---|---|---|
| Plateforme (code + gouv + tests) | **8.5 / 10** | `deep_audit_current.md` section 2 |
| Live readiness (paper + diversif + freq) | **5.5 / 10** | `live_readiness_scoreboard.md` section 5 |
| ROC / capital usage | **4.0 / 10** | `roc_capital_usage.md` section 9 |
| Qualite livrables docs | **7.5 / 10** | `deliverables_consistency_review.md` section 7 |

**Refus moyenne ponderee unique** : axes orthogonaux.

---

## 6. Gel effectif H0

**Depuis 2026-04-19T15:01Z** :
- **Gel promotion live** : aucune nouvelle promotion de strat paper -> live avant fermeture TODO XXL H0-H10.
- **Gel refactor non lie runtime** : aucun grand refactor code ne lie pas directement a : live trading / promotion / verite runtime / capital occupancy / PnL / recovery.

**Exceptions autorisees** :
- Hotfix live si incident P0 / P1 survient sur VPS (avec post-mortem).
- Micro-fixes hygiene qui font partie de H1-H10 et avancent la TODO.

**Duree gel** : jusqu'a Definition of Done TODO XXL atteinte OU user explicitly revoque.

---

## 7. Sequencement d'execution propose (ordre mandate user)

### Phase A — Nettoyage structurel (H1, H2, H3)
1. **H1 worktree** (gros impact, risque faible) :
   - Commit ou revert les 8 M
   - Classer + commit les 85 ?? selon les 4 buckets
   - `.gitignore` : `data/alerts/`, `data/audit/`, `data/backups/`, `data/incidents/`, `data/orchestrator/`, `data/reconciliation/`, `data/research_funnel/`, `data/tickets/`, `temp/`, `.pytest_tmp/` (si pas deja)
   - Creer `docs/audit/worktree_conventions.md` (annexe H1)
   - Deplacer `Todo/*.md` vers `docs/audit/` ou `docs/todos/`

2. **H2 tests** :
   - Cartographie actifs / legacy / archives / skips
   - Quarantaine formelle des crypto_new_strategies skips restants
   - Re-measure coverage.py
   - Produire `test_hygiene_map.md`

3. **H3 registries** :
   - Audit coherence 4 YAML (live_whitelist + books + quant + health)
   - Produire matrice canonique + `canonical_truth_map.md`

### Phase B — Runtime + donnees (H5, H6)
4. **H5 state files contracts** :
   - Matrice chemins / producteurs / consommateurs
   - Clean legacy / dupliques
   - Produire `state_file_contracts.md`

5. **H6 runtime ops** :
   - Verifier scripts verite (deja largement fait)
   - Audit cron VPS
   - Produire `runtime_hygiene_matrix.md`

### Phase C — Business (H7, H4)
6. **H7 ROC/capital** :
   - Definitions + metriques contractuelles
   - Reporting hebdo template
   - Produire `roc_reporting_contract.md`

7. **H4 strategy inventory clean** (apres H1-H5) :
   - Consolide inventaire post nettoyage
   - Produire `strategy_inventory_clean.md`

### Phase D — Meta (H8, H9, H10)
8. **H8 scoring policy** :
   - Regles date/env/source/formule
   - Produire `scoring_policy.md`

9. **H9 securite/ops** :
   - Audit secrets + logs + incidents + chemins
   - Produire `ops_hygiene_checklist.md`

10. **H10 desk operating truth** (synthese finale) :
    - Point d'entree operateur 1-page
    - Produire `desk_operating_truth.md`

---

## 8. Execution proposee — phasage realiste

Compte tenu du volume (10 livrables docs + nettoyage worktree + audits), execution en **multiples turns** :

| Turn | Phase | Livrables | Complexity |
|---|---|---|---|
| H0 (ce turn) | baseline | `TODO_XXL_HYGIENE.md` + `hygiene_baseline.md` | faible |
| T1 | H1 worktree | cleanup + `worktree_conventions.md` + commits selectifs | eleve (decisions triage) |
| T2 | H2 tests | `test_hygiene_map.md` + coverage re-measure | moyen |
| T3 | H3 registries | `canonical_truth_map.md` + audit YAML | faible (mostly done iter3-fix2) |
| T4 | H5 state | `state_file_contracts.md` | moyen |
| T5 | H6 runtime | `runtime_hygiene_matrix.md` + cron audit | moyen |
| T6 | H7 ROC | `roc_reporting_contract.md` | faible |
| T7 | H4 inventory | `strategy_inventory_clean.md` | faible (consolidation) |
| T8 | H8 scoring | `scoring_policy.md` | faible |
| T9 | H9 securite | `ops_hygiene_checklist.md` | moyen |
| T10 | H10 synthese | `desk_operating_truth.md` | eleve (synthese) |

**Ce que le user doit valider maintenant** : demarrer T1 (H1 worktree cleanup) ? **H1 implique des decisions de triage** (garder / archiver / ignorer) sur 85 fichiers untracked + 8 M + 1 D. Je propose approche agressive mais reversible (rien supprime hors gitignore / moves avec git mv).

---

## 9. Ce baseline reste la reference

Toute iteration d'hygiene ulterieure se compare a ces chiffres :

- pytest : 3669 pass / 50 skipped / 0 fail
- runtime_audit local : exit 3 (dev-env, equity absent + parquets stales)
- runtime_audit VPS : exit 0 (production clean)
- alpaca gate : exit 2 NO_GO_paper_journal_missing
- worktree : 94 changes (85 ?? + 8 M + 1 D)
- strategies : 16 (2A / 11R / 1A / 2D), 0 incoherence
- capital deploy : $20,856 live, 1.09% at risk
- scores : plateforme 8.5 / live 5.5 / ROC 4.0 / docs 7.5

**Progression hygiene mesurable** post chaque phase.
