# Iteration Log — Audit Trading Platform

Journal strict des iterations audit -> correction -> re-audit.
Chaque iteration = snapshot point-in-time. Les scores sont valides **a la date indiquee**.
Pour l'etat actuel du repo : voir `deep_audit_current.md` + `live_readiness_scoreboard.md`.

---

## ITERATION 0 — Baseline (2026-04-19 PM)

**as_of** : 2026-04-19T~16:00Z (debut session audit 9.5/10)
**environment** : local Windows + VPS Hetzner runtime
**evidence** :
- `pytest tests/ --ignore=tests/_archive` : 3667 pass, 80 skipped, 0 fail
- `python scripts/runtime_audit.py --strict` : VPS exit 0 (2 warnings PAPER_WITHOUT_WF)
- cross-check registres : alignes
- scan fail-open patterns : 0 dans governance/execution/worker

**Score historique revendique** : **8.8/10** (combine plateforme + live readiness, non separes)

**Gaps identifies** (G1-G6) :
- G1 Dashboard D3 deploy VPS
- G2 wf_exempt_reason pour meta/pending
- G3 coverage.py integration
- G4 OSM wire futures (parite)
- G5 E2 scoped disable crypto cycle
- G6 commentaires obsoletes cleanup

**Commits** : 2a7b477 (C2+E2 code), 7a2d392 (docs baseline)

---

## ITERATION 1 — Phase 1 urgent (2026-04-19 PM)

**as_of** : 2026-04-19T~18:00Z
**environment** : local + VPS
**evidence** :
- pytest : 3667 pass 0 fail
- runtime_audit VPS exit 0, 0 incoherence
- curl `/api/governance/strategies/status` VPS : 15 strats classees
- dashboard widget D3 LIVE observed

**Actions** :
- G2 `wf_exempt_reason` champ + runtime_audit tolerance
- G3 coverage.py baseline measure : claim **65% core / 72% critical**
- G1 dashboard deploy VPS + fix systemd ExecStart uvicorn

**Score historique revendique** : **9.2/10** (combine, single number)

**Commits** : c25df15, 6b9c92f, 30fa2d5, 719efac, 3654c88

**Note iter3-fix2 (2026-04-19T14:33Z)** : coverage 65%/72% **n'a pas ete re-mesure** post iter2/iter3. Confidence sur ce nombre = medium. A re-run si claim doit tenir dans docs actuelles.

---

## ITERATION 2 — Phase 2 stretch (2026-04-19 PM)

**as_of** : 2026-04-19T~20:00Z
**environment** : local + VPS
**evidence** :
- pytest : 3674 pass 0 fail (+7 iter2 tests)
- worker import OK post modifications
- futures_runner OSM wire via code audit TestG4FuturesRunnerOSMWire

**Actions** :
- G6 Commentaires obsoletes cleanup
- G5 E2 defense-en-profondeur run_crypto_cycle
- G4 OSM wire futures parite
- 7 tests regression iter2 ajoutes

**Score historique revendique** : **9.5/10** (combine)

**Commits** : 3973ab1, cfa7b1c

**Note iter3-fix2 (2026-04-19T14:33Z)** : le score 9.5 represente la qualite **plateforme** a cet instant T. Il ne reflete PAS le score **live readiness** (qui necessite temps paper + diversification) ni le score **ROC / capital usage** (massive gap 1.09% occupancy). Ces dimensions distinctes ont ete fusionnees a tort dans le "9.5" iter2.

---

## ITERATION 3 — Business audit (2026-04-19 PM)

**as_of** : 2026-04-19T~14:05Z (PM post iter2 push)
**environment** : local + VPS
**evidence** :
- runtime_audit VPS exit 0, 0 incoherence, 15 strats
- pytest : 3674 pass, 50 skipped, 0 fail (post B8 quarantine)
- VPS state files : alt_rel_strength paper journal actif 1j, autres paper silencieux (dim)
- live position MCL +$295 unrealized

**Actions** : pivot audit business-focused
- Ecriture `live_readiness_scoreboard.md` (score 6.5/10 **live readiness** clairement separe)
- Ecriture `ib_binance_live_plan.md` + decision sleeve `alt_rel_strength_14_60_7`
- Ecriture `roc_capital_usage.md` (diagnostic occupancy 1.09%)
- Ecriture `alpaca_go_25k_rule.md` + implementation `scripts/alpaca_go_25k_gate.py`

**Scores revendiques** (premiere tentative de separation) :
- Plateforme : **9.5/10** (inchange, contexte iter2)
- Live readiness : **6.5/10**
- ROC / capital usage : **4.0/10**

**Commits** : 6e0ee7f (docs iter3 + gate)

**Note iter3-fix2 (2026-04-19T14:33Z)** : les scores iter3 n'ont pas integre les **corrections iter3-fix** (B2/B5/B6/B7/B8). Apres iter3-fix, les 2 blockers code principaux (B2 gold_trend_mgc WF, B5 btc_asia long_only wire) sont leves, ce qui porte les strats READY de 9 a 11 et ajoute 1 grade A (gold_trend_mgc).

---

## ITERATION 3-FIX — Fix des risques iter3 (2026-04-19 PM)

**as_of** : 2026-04-19T~14:00Z
**environment** : local + VPS
**evidence** :
- `python scripts/wf_gold_trend_mgc_v1.py` : grade A VALIDATED (4/5 OOS, Sharpe 2.625, MC P(DD>30%)=0.15%)
- `python scripts/refresh_mes_1h_yf2y.py` VPS : 11737 rows, last bar 2026-04-17 20:00
- runtime_audit VPS post-fix : 16 strats, 2 ACTIVE + 11 READY + 1 AUTHORIZED + 2 DISABLED, 0 incoherence
- pytest regression : 53/53 pass (boot_preflight + iter2 + kill_switch + promotion_gate + reconciliation)

**Actions** :
- **B7** : guard idempotent RotatingFileHandler (fix logging double binding)
- **B8** : move `tests/test_crypto_strategies.py` -> `tests/_archive/` (50 skipped vs 80)
- **B6** : cron VPS MES_1H_YF2Y deploye (weekday 21:35 UTC)
- **B9** : audit confirme scheduler OK, root cause = B6 (data stale crypto alts residuel)
- **B5** : wire btc_asia variante long_only q80_v80 en parallele paper (nouveau strat_id)
- **B2** : WF gold_trend_mgc V1 livre + manifest + grade A, wf_exempt_reason retire

**Scores revendiques** (avant fix docs) :
- Plateforme : 9.5/10 (stable)
- Live readiness : 6.5/10 (stable, les fixes activent des strats pour PLUS TARD, pas maintenant)
- ROC / capital usage : 4.0/10 (stable, pas de metriques nouvelles)

**Commits** : fdcb50d (B6+B7+B8), 386e45e (B5+B2)

---

## ITERATION 3-FIX2 — Consistency review docs (2026-04-19 PM)

**as_of** : 2026-04-19T14:33Z (re-run 3 commandes de verite)
**environment** : local Windows + VPS
**evidence** :
- `python -m pytest -q -o cache_dir=.pytest_cache --basetemp .pytest_tmp` : **3669 pass, 50 skipped, 0 fail (exit 0)**
- `python scripts/runtime_audit.py --strict` LOCAL : **exit 3 FAIL** (equity_state::ibkr_futures absent + 4 parquets _1D stales)
- `python scripts/runtime_audit.py --strict` VPS : **exit 0 OK** (12/12 preflight, data fresh 41h)
- `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5` : **exit 2 NO_GO_paper_journal_missing**

**Actions** : realignement des 6 livrables sur la verite runtime
- `live_readiness_scoreboard.md` : separation stricte repo local / VPS / cible, colonnes source_of_truth + confidence, encadre dev vs prod
- `ib_binance_live_plan.md` : etapes marquees `ready now` / `blocked by infra` / `blocked by paper time` / `blocked by missing artifact` ; question centrale "qu'est-ce qui peut trader lundi matin ?" explicite
- `roc_capital_usage.md` : taxonomie 4 niveaux capital (alloue / deployable / utilise / a risque), ROC realiste 20K vs cible 100K, separation ROC-contributive vs occupation
- `alpaca_go_25k_rule.md` : alignement EXACT avec script (ordre evaluation, exit codes, verdict courant reel NO_GO_paper_journal_missing)
- `deliverables_consistency_review.md` : nouveau doc, audit des docs + meta-recommandation source of truth
- `iteration_log.md` : cette version ajoutee avec as_of/environment/evidence par iter
- `deep_audit_current.md` : re-calcul score base sur pytest courant + runtime_audit courant, historique separe

**Scores recalcules honnetement** (post iter3-fix2) :
- **Plateforme** : **8.5/10** (baisse vs 9.5 iter2 car : coverage non re-mesure, preflight local FAIL non documente historiquement, occupancy tracker non livre)
- **Live readiness** : **5.5/10** (baisse vs 6.5 iter3 car : paper signal quality reevalue honnetement, trade freq insuffisante, B6r residuel)
- **ROC / capital usage** : **4.0/10** (stable, iter3-fix ne livre pas de metriques nouvelles)
- **Qualite livrables docs** : **7.5/10** (post cette correction, vs 5.0/10 pre-correction)

**Commits prevus** : (ce commit) docs(iter3-fix2): realign 6 deliverables sur runtime reel

---

## Synthese — historique vs etat courant

| Claim historique | Date | Reste valide aujourd'hui ? |
|---|---|---|
| "3674 tests pass 0 fail" (iter2) | 2026-04-19T20:00Z | ⚠️ non — post B8 quarantine = 3669 pass (mathematiquement correct) |
| "coverage 65% core / 72% critical" | iter1 2026-04-19T18:00Z | ⚠️ non re-mesure, confidence medium |
| "Score 9.5/10 plateforme" | iter2 | ✅ plateforme seule oui a cette date. **Score combine confus** maintenant recadre : plateforme 8.5, live readiness 5.5, ROC 4.0 |
| "Runtime audit VPS 12/12 preflight OK" | iter2 | ✅ confirme 2026-04-19T14:33Z |
| "0 incoherence registries" | iter1+ | ✅ confirme 2026-04-19T14:33Z |
| "Dashboard widget D3 LIVE verifie" | iter1 | ✅ probable, non re-verifie specifiquement iter3-fix2 |
| "Capital occupancy 1.09%" | iter3 | ✅ inchange 2026-04-19T14:33Z |
| "gold_trend_mgc grade A VALIDATED" | iter3-fix | ✅ confirme wf_manifest present |
| "btc_asia q80_long_only READY" | iter3-fix | ✅ confirme runtime_audit 16 strats |

---

## Historique commits (ordre chronologique)

**Plan 9.0 (non inclus dans session audit)** : 11 commits pre-session 9.5, deja pushes.

**Session 9.5/iter0-2** :
- 2a7b477 feat(execution+risk): C2+E2
- 7a2d392 docs(audit): iteration 0 baseline
- c25df15 feat(governance): ITER1 G2 wf_exempt_reason
- 6b9c92f docs(audit): ITER1 G3 coverage baseline
- 30fa2d5 fix(dashboard): ITER1 G1 route collision
- 719efac fix(ops): ITER1 G1 systemd ExecStart uvicorn
- 3654c88 docs(audit): iteration 1 complete
- 3973ab1 feat(iter2): G4 + G5 + G6
- cfa7b1c test(iter2): 7 regression tests
- 526ea19 docs(audit): final_verdict.md + iteration log update

**Session iter3 business audit** :
- 6e0ee7f docs(iter3): business-focused audit + alpaca 25K gate

**Session iter3-fix** :
- fdcb50d fix(iter3): B6 cron parquet + B7 logging double + B8 test quarantine
- 386e45e feat(iter3): B5 btc_asia long_only + B2 gold_trend_mgc V1 WF grade A

**Session iter3-fix2** (ce commit) :
- [pending] docs(iter3-fix2): realign 6 deliverables sur runtime reel

**Total pushes origin/main** : 13 commits session 9.5/iter3/iter3-fix. iter3-fix2 pending push validation user.
