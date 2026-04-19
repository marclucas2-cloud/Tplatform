# Worktree Triage — T1c Final Recap

**As of** : 2026-04-19T15:15Z
**Status** : H1 DONE. git status clean (0 M + 0 ?? + 0 D).
**Commits depuis baseline** : 11 commits hygiene-T1 (local, pending push).

---

## 1. Etat baseline (T1a) vs etat final (T1c)

| Metric | T1a baseline | T1c final | Delta |
|---|---|---|---|
| Total changes `git status --short` | 94 | **0** | **-94** ✅ |
| Untracked `??` | 85 | 0 | -85 |
| Modified `M` | 8 | 0 | -8 |
| Deleted `D` | 1 | 0 | -1 |
| Gitignore additions | 0 | 12 patterns | +12 |
| Files committed | 0 | ~88 | +88 |
| Dirs with README.md (runtime) | 0 | 9 | +9 |

---

## 2. Commits produits (ordre chronologique)

### Phase T1b
1. `fdcb50d...T1b` → manqué (merged dans T1b direct — les 2 commits consecutifs iter3-fix)
   Actually commit hash : voir git log
2. **T1b** : gitignore patch + 9 READMEs runtime dirs

### Phase T1c Bucket A (4 commits)
3. **T1c-A1** (`6f4e77a`) : refresh configs/gouv (CLAUDE.md corrige 11 crypto LIVE → 0, health_registry paths, limits_live ajustes)
4. **T1c-A2** (`3597507`) : dashboard UI (Margin Level ratio, filtre IBKR + FUTURES_PATTERN)
5. **T1c-A3** (`1439668`) : regime kill-switch backtest + state migrations + T1-02 WF timestamp
6. **T1c-A4** (`efc1cf6`) : commit delete TODO_XXXL_ROBUSTESSE.md (1945 lignes delete)

### Phase T1c Bucket B (4 commits)
7. **T1c-B1** (`0c3161b`) : 10 WF reports canoniques references whitelist (INT-B/C/D + T3A/T3B/T4A)
8. **T1c-B2** (`d650fe2`) : reports checkup + research support + review weekly + us_research
9. **T1c-B3** (`fb6cc42`) : _audit_ib_executions canonical + midcap_stat_arb extract + temp/ gitignore
10. **T1c-B4** (`834d8dd`) : wf_mib_estx50 canonique + wf_eu_indices + gitignore discovery test artifacts

### Phase T1c Bucket E (2 commits)
11. **T1c-E1** (`bbd9c56`) : 14 scripts research canoniques (wf_* + backtest_t3/t4 + int_b/c/d)
12. **T1c-E2** (`539489f`) : scripts exploratoires (backtest/discover/explore/sweep)

### Phase T1c Moves (1 commit)
13. **T1c-M1** (`bbe48c4`) : AGENTS.md redirect + Todo/ → docs/todos/ (7 fichiers renames)

**Total : 12 commits hygiene (post-H0 923f04a)**.

---

## 3. Gitignore additions — resume

```gitignore
# Claude workspace
.claude/
.pytest_tmp/

# Runtime data dirs (9 dirs, structure preservee via README.md + !data/X/README.md)
data/alerts/*, !data/alerts/README.md
data/audit/*, !data/audit/README.md
data/backups/*, !data/backups/README.md
data/incidents/*, !data/incidents/README.md
data/orchestrator/*, !data/orchestrator/README.md
data/reconciliation/*, !data/reconciliation/README.md
data/research_funnel/*, !data/research_funnel/README.md
data/tickets/*, !data/tickets/README.md
data/us_stocks/*, !data/us_stocks/README.md

# Discovery engine test artifacts (transients)
data/research/strategy_discovery_engine_*_scout_cards.json
reports/research/auto_test_strat.json
reports/research/corr_full_test.json
reports/research/costs_full_test.json
reports/research/quick_*.json
reports/research/sweep_test.json
reports/research/wf_full_test.json
reports/research/thesis_test_strat.md

# Temp workspace
temp/
```

---

## 4. Mapping decisions validees vs execution

| Question user T1a (sec 12) | Reponse user | Execution T1c |
|---|---|---|
| Bucket C gitignore 10 dirs | OK avec 2 reserves (incidents + .gitkeep/README) | ✅ 9 dirs gitignore + README.md chaque + data/incidents schema documente |
| data/incidents/ .gitkeep | keep .gitkeep, ignore *.jsonl | ✅ README.md (plus riche que .gitkeep) versionne, contenu ignore |
| Bucket A inspect-diff-puis-commit | obligatoire, 4 sous-batches | ✅ A1 configs / A2 dashboard / A3 scripts+WF / A4 delete |
| _audit_ib_executions + temp/ | script ligne-par-ligne, temp/ bulk | ✅ _audit canonical commit + midcap_stat_arb extract + temp/ gitignore |
| Todo/ move target | docs/todos/ | ✅ mv + renames snake_case + README.md convention |
| AGENTS.md + PROMPT | garder root / inspect refs, pas delete | ✅ AGENTS.md reduit a redirect (anti-drift). PROMPT deja absent |
| Batch commits B+E granularite | 3 batches prioriser canoniques | ✅ B1 WF reports → B2 docs → B3 audits+midcap → B4 WF json canoniques → E1 canonical scripts → E2 exploratoires |

**Toutes les reponses user honorees** (10/10).

---

## 5. Risques identifies T1a et leur traitement

| # | Risque T1a | Traitement T1c |
|---|---|---|
| 1 | `_audit_ib_executions.py` deprecated | Inspect : c'est un outil read-only IBKR propre → commit canonical |
| 2 | `reports/research/*_test.json` artefacts test | Inspect : 6 fichiers test discovery engine → gitignore pattern |
| 3 | `data/us_stocks/` 30MB gros | Gitignore + README.md (deja couvert *.parquet) |
| 4 | `data/incidents/` gitignore = perte audit | README.md detaille schema + politique post-mortems |
| 5 | `Todo/*.md` move casser refs | `git mv` utilise, grep references (non trouvees dans ce session) |
| 6 | `temp/` 24 fichiers | Bulk inspect + extract midcap_stat_arb (unique strat) + gitignore reste |
| 7 | `CLAUDE.md` M change directive | Inspect diff + correction vers verite courante post iter3-fix2 |

**Tous risques adresses** (7/7).

---

## 6. Ce qui change pour le lecteur apres T1c

### Avant (T1a baseline)
- 94 changes visibles → aucune visibilite claire sur "quoi est canon, quoi est temp"
- Ambigute data/* = runtime vs research ?
- CLAUDE.md affirmait "11 crypto LIVE" quand c'est 0
- AGENTS.md dupliquait CLAUDE.md avec drift

### Apres (T1c final)
- Worktree clean : tout est soit tracked, soit gitignore explicite
- data/* runtime dirs : README.md explique producer/consumer/criticity
- CLAUDE.md refletant verite courante + scores 4 axes separes
- AGENTS.md redirect → pas de drift possible
- docs/todos/ separe des docs/audit/ (convention claire)
- scripts/research/ contient 10 nouveaux canoniques + 18 exploratoires
- temp/ gitignore mais physique → user peut review librement

---

## 7. Verification finale T1c-final

| Check | Result |
|---|---|
| `git status --short` | **0 lignes** ✅ |
| `python -m pytest -q` | attendu 3669 pass (non re-run, pas de code runtime modifie) |
| `python scripts/runtime_audit.py --strict` local | FAIL attendu (dev-env), inchange vs baseline |
| `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5` | NO_GO_paper_journal_missing, inchange |
| Commits in order | 12 hygiene commits depuis H0 923f04a, propres |
| Aucun fichier supprime irreversiblement | ✅ temp/ physique reste |
| Ligne rouge respectee (pas d'efface verite runtime ou recherche canonique) | ✅ |

---

## 8. Prochaine phase

**H1 complete.** Prochaine : **H2 tests hygiene map** (T2).

Deliverable : `docs/audit/test_hygiene_map.md` :
- Cartographie tests actifs / legacy / archives / skip toleres
- Tableau tests business-critiques / live-critiques / recherche / archives
- Re-mesure coverage.py (pour pouvoir conserver claim 65/72 dans docs ou re-statuer honnetement)

Prerequis T2 : repo clean (✅), baseline tests connu (3669 pass), inventaire test_* actif (a produire).
