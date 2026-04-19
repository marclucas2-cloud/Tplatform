# Gap list vers 9.5/10 — live document

**Derniere iteration** : 0 (baseline audit, 2026-04-19)
**Score courant** : 8.8/10
**Score cible** : 9.5/10
**Gap** : 0.7 pt

---

## Legende

- `[ ]` TODO
- `[~]` in-progress
- `[x]` DONE (avec preuve)
- `[-]` BLOQUE structurellement (justifie)

---

## Phase 1 URGENT — iteration 1 (~0.4 pt)

### G1 — Dashboard D3 widget deploy VPS
- [ ] Build frontend vite `dashboard/frontend/npm run build`
- [ ] Copy `dashboard/frontend/dist/*` vers `/opt/trading-platform/dashboard/frontend/dist/` VPS
- [ ] Reload nginx / dashboard server
- [ ] Verify via curl: `/api/strategies/status` returns JSON + widget visible
- **Impact** : 0.2 pt
- **Risque** : faible (frontend isolé du worker trading)
- **Preuve requise** : screenshot ou curl de /api/strategies/status + dashboard index.html contient "Statuts calcules"

### G2 — Distinction meta vs incoherence dans quant_registry
- [ ] Ajouter champ `wf_exempt_reason` dans quant_registry.yaml pour strats sans WF legitime
  - `us_stocks_daily` : `wf_exempt_reason: "meta_portfolio_aggregate"` (aggregate de sous-strats)
  - `gold_trend_mgc` : `wf_exempt_reason: "wf_v1_recalibration_in_progress"`
- [ ] runtime_audit.py check `PAPER_WITHOUT_WF` ignore les entrees avec wf_exempt_reason
- [ ] 0 incoherence "warning" restantes sur runtime_audit
- **Impact** : 0.1 pt
- **Risque** : tres faible
- **Preuve requise** : `python scripts/runtime_audit.py --strict` exit code 0

### G3 — Coverage.py integration + seuil minimum
- [ ] `pip install coverage pytest-cov`
- [ ] Run `coverage run -m pytest tests/ --ignore=tests/_archive` puis `coverage report`
- [ ] Documenter baseline coverage % dans `docs/audit/coverage_baseline.md`
- [ ] Ajouter seuil dans `pyproject.toml` addopts : `--cov=core --cov=scripts --cov-fail-under=60`
  (seuil volontairement modeste, augmentable plus tard)
- **Impact** : 0.1 pt
- **Risque** : faible (peut reveler tests insuffisants, a gerer)
- **Preuve requise** : coverage report dans docs + config pyproject active

---

## Phase 2 stabilisation — iteration 2 (~0.3 pt)

### G4 — OSM wire futures_runner (parité crypto)
- [ ] Dans `core/worker/cycles/futures_runner.py` : idem pattern C2 autour des broker.place_order
- [ ] Tests futures OSM path
- **Impact** : 0.15 pt
- **Risque** : moyen (touche hot path futures live)
- **Preuve requise** : test integration + runtime audit montre orders futures traces

### G5 — E2 scoped disable visible dans run_crypto_cycle
- [ ] Avant broker.create_position crypto, ajouter check `LiveKillSwitch().is_strategy_disabled(strat_id)` -> skip signal
- [ ] Tests regression
- **Impact** : 0.1 pt
- **Risque** : faible
- **Preuve requise** : test montre signal crypto bloque si strat disabled

### G6 — Commentaires obsoletes cleanup
- [ ] Grep "Railway" dans core/ scripts/ -> retirer ou remplacer
- [ ] Grep "Phase 1.1" / "TODO XXL" obsoletes -> retirer
- [ ] Grep dates anciennes "2026-04-16" dans notes registres -> verifier toujours pertinents
- **Impact** : 0.05 pt
- **Risque** : tres faible (commentaires only)

---

## Phase 3 post-9.5 (hors scope session)

### G7 — worker.run_crypto_cycle extraction -> crypto_runner.py
### G8 — Multi-channel alerting (Slack / Email)
### G9 — Coverage CI gate strict (>80%)

---

## Blocages structurels documentes

### BS1 — Monolithe worker.py 5402 LOC
**Raison** : Phase 2 ChatGPT stabilisation. Extractions XXL deja faites (paper_cycles, futures_runner, macro_ecb). Reste: run_crypto_cycle ~900 LOC. Extraction possible mais necessite 2-3h de refacto + tests. **Ne bloque pas 9.5** car: encapsulation OSM + E2 fait autour des hot paths.

### BS2 — Dependance solo dev / VPS unique
**Raison** : directive Marc `feedback_prove_profitability_first` : pas de $2M-setup sur $20K. Pas de VPS redondant, pas de deputy ops, pas de Slack alerting. Accepte comme constraint structurel tant que capital < $100K.

### BS3 — 2 strats AUTHORIZED sans WF structure
**Raison** :
- `gold_trend_mgc` : V1 recalibration (SL 0.4% / TP 0.8%) en cours, WF + MC pending. Trade ouvert V0 en live a laisser se terminer avant promotion V1.
- `us_stocks_daily` : meta-portfolio aggregat de strats US (momentum_25etf, dow_seasonal, lateday_meanrev, etc.). Pas WF unique par design — chaque sous-strat a son propre backtest.

**Fix G2 proposé** : annoter `wf_exempt_reason` pour les distinguer des vraies incoherences.

---

## Score progression (mis a jour a chaque iteration)

| Iteration | Actions | Score | Gap 9.5 |
|---|---|---|---|
| 0 | Baseline audit | 8.8 | -0.7 |
| 1 (cible) | G1 + G2 + G3 | 9.2 | -0.3 |
| 2 (cible) | G4 + G5 + G6 | 9.5 | 0 |

---

## Definition of Done 9.5/10

Les 12 criteres du mandat auditeur :

1. [x] Suite de tests verte, collectable (3667 pass 0 fail, 3688 collected 0 err)
2. [x] Tests orphelins quarantines formellement (tests/_archive/README.md)
3. [x] pre_order_guard fail-closed sur erreur critique (A4 + book_health exception)
4. [x] promotion_gate preuve machine-readable physique (A2 wf_source strict)
5. [x] live_whitelist + books_registry + quant_registry disent la meme chose (cross-check OK)
6. [x] Chaque strat promouvable: strategy_id + status + preuve quant + paper_start_at + kill_criteria
7. [ ] Aucun book presente comme live-ready s'il n'est pas GREEN **(2 warnings restants G2)**
8. [ ] Dashboard/API reflete statuts calcules **(G1 deploy manquant)**
9. [x] Risque redemarrage / recovery / DD state : 4 BootState + warmup explicit (C1)
10. [x] Monolithe principal reduit ou encapsule chemins risques (OSM wire C2 crypto)
11. [ ] Commentaires critiques obsoletes nettoyes **(G6 pending)**
12. [x] Score justifie par preuves

**Blocking pour 9.5** : points 7, 8, 11 -> iterations 1 + 2.
