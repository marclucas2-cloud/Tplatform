# Deliverables Consistency Review

**As of** : 2026-04-19T14:33Z (post iter3-fix2)
**Scope** : audit des 6 livrables docs + script Alpaca vs verite runtime.
**Mandat** : ne gonfle rien. Si un doc ment, le dire. Si un fix est incertain, le dire.

---

## 1. Ce qui etait juste (conserve)

| Item | Pourquoi c'etait juste | Fichier |
|---|---|---|
| 2 strats ACTIVE (CAM + GOR) sur IBKR futures | Confirme VPS runtime_audit + positions_live.json | live_readiness_scoreboard.md |
| Capital occupancy 1.09% | Calcul exact $228 / $20,856 | roc_capital_usage.md |
| Decision sleeve Binance = alt_rel_strength_14_60_7 | Analyse comparative solide (decorrelation, bull/bear, runner production-ready) | ib_binance_live_plan.md |
| Gate Alpaca script fonctionne | Exit codes 0/1/2 corrects, logique evaluation implementee | scripts/alpaca_go_25k_gate.py |
| Runtime audit VPS clean | Confirme 2026-04-19T14:33Z exit 0 | tous |
| pytest green 0 fail | Confirme 2026-04-19T14:33Z (3669 pass) | tous |
| Registries alignes (0 incoherence) | Confirme | tous |

---

## 2. Ce qui etait faux / date / optimiste (corrige iter3-fix2)

### 2.1 Confusion repo local vs VPS

**Avant** : tous les docs melaient implicitement "la plateforme" (repo + VPS) sans distinguer.
**Probleme reel** : `runtime_audit.py --strict` **local** exit 3 FAIL (equity_state absent), tandis que **VPS** exit 0 OK. Un lecteur non averti concluait a un blocker P0 ou ignorait le FAIL.
**Fix iter3-fix2** :
- live_readiness_scoreboard.md : encadre explicite "VPS pilote pour business decisions, local pilote pour qualite code/tests"
- Chaque tableau ajoute colonnes `source_of_truth` (local repo / VPS runtime / config / user decision) et `confidence` (high / med / low)
- deep_audit_current.md : section `truth commands` liste les 3 commandes avec exit code + as_of

### 2.2 Verdict Alpaca gate incorrect

**Avant (alpaca_go_25k_rule.md)** : "**Verdict courant : NO_GO_paper_too_short** (attendu, debut paper 1 jour)."
**Script dit reellement** : `NO_GO_paper_journal_missing` (exit 2). L'ordre d'evaluation verifie `journal_found` **avant** `paper_days < min_days`.
**Fix iter3-fix2** :
- Doc liste desormais les 9 criteres d'evaluation dans l'ordre **exact** du script
- "Verdict courant reel" = NO_GO_paper_journal_missing avec output brut verbatim
- Section 8 "Honnetete auditeur" liste les anciens erreurs

### 2.3 Claim "promotion_gate vert" non verifie par script

**Avant** : le doc listait "promotion_gate vert" comme critere GO, laissant croire que le script le checkait.
**Realite** : le script `alpaca_go_25k_gate.py` ne fait AUCUN check formel `core.governance.promotion_gate`.
**Fix iter3-fix2** : clarifie "condition operationnelle uniquement, a verifier manuellement au moment du depot capital".

### 2.4 Scores 9.5/10 repetes

**Avant** : iter0 (8.8), iter1 (9.2), iter2 (9.5), iter3 (pre-fix 9.5) — melaient plateforme + live readiness + ROC.
**Realite** : 3 dimensions orthogonales avec resolution differente.
**Fix iter3-fix2** :
- iteration_log.md : ajoute as_of + environment + evidence par iter + note explicite "score 9.5 iter2 ≠ current" pour chaque iteration historique
- deep_audit_current.md : section `historical context` separee
- Refus explicite de faire une moyenne ponderee des 4 dimensions

### 2.5 B2/B5 listes comme blockers post-fix

**Avant (live_readiness_scoreboard.md iter3)** : "B2 : gold_trend_mgc V1 WF manifest a produire" + "B5 : btc_asia variante long-only non wiree" listes dans Top 3 risques.
**Realite post iter3-fix** : B2 livre (grade A VALIDATED), B5 wire livre (paper_start 2026-04-20).
**Fix iter3-fix2** : retire de "Top 10 risques residuels", ajoute en section "actions livrees iter3-fix".

### 2.6 Classification gold_trend_mgc "paper_only stricte tant que WF pas livre"

**Avant (ib_binance_live_plan.md iter3)** : ligne gold_trend_mgc = "paper_only stricte tant que WF V1 pas livre. **B2** : lancer scripts/wf_gold_trend_mgc_v1.py."
**Realite post iter3-fix** : WF V1 livre, grade A, READY dans quant_registry.
**Fix iter3-fix2** : Classification corrigee en "live_probation_scheduled 2026-05-16 (iter3-fix B2 resolu)".

### 2.7 Coverage 65/72 reutilise sans re-mesure

**Avant** : claim 65% core / 72% critical repete dans 3 docs (deep_audit_current, iteration_log, live_readiness_scoreboard).
**Realite** : dernier run coverage datait iter1 (2026-04-19 AM). Non re-mesure post iter2 (+7 tests) ni iter3 (+fixtures).
**Fix iter3-fix2** :
- deep_audit_current.md : coverage note `NON re-mesure post iter2/iter3, confidence medium`
- Score dimension coverage rabaisse a 6.5/10 (doute raisonnable)

### 2.8 "Deja atteint" pour Objectif A (live engine)

**Avant (live_readiness_scoreboard.md iter3)** : "Objectif A : 1 moteur live vraiment exploitable IBKR futures — STATUT : DEJA ATTEINT."
**Realite** : vrai sur VPS, **faux sur local** (runtime_audit FAIL). La formulation laissait croire repo local cible atteinte, alors que dev ne maintient pas state files.
**Fix iter3-fix2** : "STATUT (VPS) : ATTEINT. STATUT (repo local) : runtime_audit FAIL — dev-env only, pas P0."

---

## 3. Ce qui a ete corrige iter3-fix2

Recapitulatif actionable :

| Fichier | Changement principal | Impact lecteur |
|---|---|---|
| `live_readiness_scoreboard.md` | +encadre VPS vs local, +colonnes source_of_truth + confidence, scores separes | Lecteur sait lequel etat pilote decision business |
| `ib_binance_live_plan.md` | +question centrale "qu'est-ce qui trade lundi matin ?", +marquage `ready now` vs `blocked by *`, classification gold_trend post-fix | Lecteur a roadmap executable + liste etats lundi |
| `roc_capital_usage.md` | +taxonomie 4 niveaux capital, +ROC 20K vs 100K, +sleeves ROC-contributive vs occupation seule | Lecteur sait ce qui scale ROC vs remplit inutile |
| `alpaca_go_25k_rule.md` | Alignement EXACT sur script (ordre 1-10 criteres), verdict courant = output brut du script | Lecteur ne peut plus etre induit en erreur par le doc |
| `iteration_log.md` | +as_of / environment / evidence par iter, +Synthese historique vs courant | Lecteur distingue "revendique a date X" de "valide aujourd'hui" |
| `deep_audit_current.md` | +section `truth commands`, scores recalcules (8.5 / 5.5 / 4.0 / 7.5), section historical context separee | Lecteur a un etat courant net, recalcule, et l'histoire preservee |

---

## 4. Ce qui reste incertain (honnêtete auditeur)

### 4.1 Paper runners weekday empiriques
Le scheduler worker a les runners wires (confirme par audit code iter3 B9), mais l'observation empirique du lundi 2026-04-20 confirmera si mes_monday, eu_relmom, us_sector_ls, mib_estx50, btc_asia q80_long_only tournent sans erreur silencieuse.
**Action suggeree lundi matin** : `ssh vps "grep -iE 'paper_cycle|runner' logs/worker/worker.log | tail -50"`.

### 4.2 Crypto alts parquets (B6r)
MES_1H_YF2Y.parquet est fixe (cron VPS Mon-Fri 21:35 UTC). Mais les alts (XRP, ETH, BNB, SOL, ADA, AVAX, DOT, LINK, NEAR, SUI) + BTCUSDT_1h doivent etre check sur VPS pour verifier freshness. L'observation iter3 montre qu'ils sont fresh aujourd'hui (2026-04-19 06:27 UTC). A continuer de surveiller.
**Action suggeree** : check hebdo `stat -c '%y' data/crypto/candles/*.parquet`.

### 4.3 live_pnl_tracker 1j data
Historique insuffisant pour attribution ROC. Build-up passif 30j requis. Pas un blocker code, un blocker temps.

### 4.4 Promotion gate integration dans alpaca_go_25k_gate
Le script actuel ne verifie PAS formellement `promotion_gate`. Le doc le documente clairement, mais une amelioration Phase 2 serait d'ajouter l'appel gate pour cloturer definitivement la surface.
**Option** : ajouter `from core.governance.promotion_gate import check; gate_result = check(...)` dans `compute_metrics()`, condition GO additionnelle.

### 4.5 Scores futurs
Les scores actuels (8.5 / 5.5 / 4.0 / 7.5) peuvent varier :
- Plateforme : +1.0 possible si coverage re-mesure >= 80%
- Live readiness : +2.0 possible post 3-4 promotions live (2026-06-30 realiste)
- ROC : +3.0 possible post implementation occupancy tracker + ROC par strat
- Docs : +1.0 possible post 1 cycle review (peer ou self-review 2 semaines)

---

## 5. Source of truth — meilleure reference par dimension

| Dimension | Meilleure source | Pourquoi |
|---|---|---|
| Live readiness | **`live_readiness_scoreboard.md` (iter3-fix2)** | Seul doc separant explicitement repo / VPS / cible + table classement par source_of_truth |
| ROC / capital usage | **`roc_capital_usage.md` (iter3-fix2)** | Seul doc avec taxonomie 4 niveaux capital + projection honnete +10-15% vs 30% |
| Alpaca 25K go/no-go | **`scripts/alpaca_go_25k_gate.py`** (executable) puis **`alpaca_go_25k_rule.md`** (doc alignee) | Script = verite executable. Doc doit suivre. |
| Etat global du desk | **`deep_audit_current.md` (iter3-fix2)** + **VPS runtime_audit exit code** | Section 0 truth commands + section 6 scores 4-axes separes |
| Etat strategies par book | **VPS `python scripts/runtime_audit.py --strict`** | Machine-readable, 0 incoherence verifie |
| Historique iterations | **`iteration_log.md` (iter3-fix2)** | Ajoute as_of / environment / evidence par iter |
| Consistency des docs entre eux | **`deliverables_consistency_review.md`** (ce document) | Meta-audit des docs |

---

## 6. Decision flow — comment le lecteur doit utiliser ces docs

**Scenario 1 : "Combien de strats live lundi matin ?"**
→ `ib_binance_live_plan.md` section 0 "Question centrale" : **2** (CAM + GOR), inchange.

**Scenario 2 : "Est-ce qu'on peut deposer $25K sur Alpaca ?"**
→ Run `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5`. Exit code = reponse.
→ Doc = reference des regles uniquement.

**Scenario 3 : "Quel est le score plateforme ?"**
→ `deep_audit_current.md` section 6 : **8.5 / 10**.
→ Attention : ne pas combiner avec live readiness (5.5) — axes orthogonaux.

**Scenario 4 : "Quel est le plan d'action semaine prochaine ?"**
→ `ib_binance_live_plan.md` section 6 check-list executable.
→ `live_readiness_scoreboard.md` section 7 actions lundi matin.

**Scenario 5 : "Capital occupancy a 1% est-il acceptable ?"**
→ `roc_capital_usage.md` section 1 taxonomie + section 8 diagnostic.
→ Reponse : non pas acceptable durable, resolution = promotion paper -> live (4-8 sem), pas plus de capital a risque.

**Scenario 6 : "Cette claim de score 9.5/10 iter2 est-elle encore vraie ?"**
→ `iteration_log.md` section Synthese : le 9.5 etait **plateforme seule** au 2026-04-19T20:00Z. Aujourd'hui plateforme = 8.5. L'ecart vient de coverage non re-mesure + preflight local non distingue + observabilite business revisitee.

---

## 7. Score qualite livrables

### Methodologie de notation

| Critere | Pre iter3-fix2 | Post iter3-fix2 | Impact |
|---|---|---|---|
| Distinction repo / VPS / cible | 3/10 | 9/10 | encadre + colonnes explicites |
| Coherence doc-script | 4/10 | 9/10 | alpaca doc = script exactement |
| Sources citees (source_of_truth) | 5/10 | 9/10 | colonnes ajoutees + paths files |
| Scores recalculables | 4/10 | 8/10 | truth commands + breakdown dimension |
| Honnetete incertitudes | 6/10 | 9/10 | section "ce qui reste incertain" |
| Actionabilite lundi matin | 7/10 | 9/10 | check-list executable + grep commands |
| Historique preserve | 5/10 | 9/10 | section `historical context` separee |

**Moyenne** : pre = 4.9/10, post = **8.9/10** (arrondi 7.5 pour pondération conservative).

**Note dimension docs** : **7.5 / 10** (post iter3-fix2).

---

## 8. Definition of Done — check final

- [x] Chaque doc dit vrai pour l'environnement qu'il decrit (repo local / VPS / cible distingues)
- [x] Aucun doc ne melange historique et etat courant sans l'indiquer
- [x] Le doc Alpaca et le script Alpaca racontent exactement la même chose
- [x] Le lecteur sait ce qui trade vraiment maintenant (section 0 ib_binance_live_plan.md)
- [x] Le lecteur sait ce qui est bloque (tableaux classifies `ready now` / `blocked by *`)
- [x] Le lecteur sait quoi faire lundi matin (section 7 live_readiness_scoreboard.md + section 6 ib_binance_live_plan.md)
- [x] Scores baisses vs iter3 initial, justifies par re-audit honnete

**Mandat respecte** : aucun score artificiellement gonfle. Scores actuels **8.5 / 5.5 / 4.0 / 7.5** refletent un repo sain mais avec des axes business non encore livres (metriques occupancy, ROC par strat) et des blockers temporels (paper 30j) incompressibles.

---

## 9. Actions post iter3-fix2

- [ ] Commit + push `docs(iter3-fix2): realign 6 deliverables sur runtime reel`
- [ ] User review des corrections
- [ ] Lundi matin 2026-04-20 : verif empirique paper runners (actions live_readiness_scoreboard.md section 7)
- [ ] Optionnel : re-run coverage.py si claim 65/72 doit tenir dans les docs
- [ ] Optionnel Phase 2 : integrer promotion_gate formel dans alpaca_go_25k_gate.py
