# Deep Audit — Current State

**As of** : 2026-04-19T14:33Z (post iter3-fix + re-run truth commands)
**Mode** : comite senior impitoyable. Aucune complaisance. Honnetete prime sur score.
**Auditeurs virtuels** : CTO + CRO + Quant + Produit + Ops.

**Raison d'etre de ce document** : etat **courant** du repo, recalcule a partir des
3 commandes de verite. Tout ce qui est historique (scores 8.8 → 9.5 iter0-iter2) est
deplace en section `historical context`.

---

## 0. Truth commands — 3 sources executables

| Command | Exit | Sortie resumee | as_of |
|---|---|---|---|
| `python -m pytest -q -o cache_dir=.pytest_cache --basetemp .pytest_tmp` | 0 | **3669 pass, 50 skipped, 0 fail, 2380 warnings** | 2026-04-19T14:33Z |
| `python scripts/runtime_audit.py --strict` (local) | **3** | **FAIL : equity_state::ibkr_futures absent + 4 parquets _1D stales** | 2026-04-19T14:33Z |
| `python scripts/runtime_audit.py --strict` (VPS) | 0 | OK 12/12 preflight + 16 strats + 0 incoherence + data fresh 41h | 2026-04-19T14:33Z |
| `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5` | 2 | **NO_GO_paper_journal_missing** (paper 1j, 0 trades) | 2026-04-19T14:33Z |

---

## 1. Etat actuel — par dimension

### 1.1 Tests (pytest)

**3669 pass, 50 skipped, 0 fail** sur `tests/` (hors `tests/_archive/`).

- 0 collection errors
- Skips restants = 50, dont crypto_new_strategies (modules legacy pas encore quarantaines formellement)
- Pas de regression post iter3-fix (B5+B2) : 53/53 tests iter2+iter3 pass

**Note dimension** : **9.0 / 10** (suite saine, skips restants non bloquants mais documente)

### 1.2 Runtime audit

#### Local Windows (dev)
- **FAIL exit 3** : equity_state::ibkr_futures absent (attendu sur dev), 4 parquets _1D stales 200h+
- **16 strats classees** identiquement au VPS
- **0 incoherence registries**

#### VPS Hetzner (prod)
- **OK exit 0** : 12/12 preflight checks PASS
- Data fresh 41h (cron Mon-Fri 21:30 UTC)
- 2 ACTIVE (CAM + GOR), 11 READY, 1 AUTHORIZED, 2 DISABLED
- 0 runtime/registry incoherence

**Distinction critique** : le FAIL local est **attendu** (dev env ≠ prod env). Pas un P0 business. Le VPS fait foi pour operationnel.

**Note dimension** : **9.0 / 10** (VPS clean, local documente comme dev-only)

### 1.3 Gouvernance fail-closed

Source de verite : code review + pre_order_guard tests + promotion_gate tests.

- pre_order_guard : fail-closed sur erreur critique (A4 + 6b E2 scoped per-strategy)
- promotion_gate : strict sur preuves machine-readable (wf_source fichier physique, paper_start_at)
- boot preflight fail-closed sur registries + equity_state + data freshness
- LiveKillSwitch per-strategy `disable_strategy()` / `enable_strategy()` / `is_strategy_disabled()`
- wf_exempt_reason champ canonique pour meta-strats / WF pending

**Post iter3-fix B2** : gold_trend_mgc n'a plus `wf_exempt_reason` (WF V1 livre physique).

**Note dimension** : **9.5 / 10** (12/12 DoD fermes, pas de fail-open chemin residuel)

### 1.4 Persistance & Recovery

- DDBaselines atomic (tempfile + fsync + replace)
- OrderTracker atomic save_state par transition
- Quant_registry mtime cache
- 4 BootState explicit (C1 iter-ante)
- OSM wire symetrique crypto (iter2 C2) + futures (iter2 G4)

**Note dimension** : **9.5 / 10**

### 1.5 Observabilite

- Dashboard D3 LIVE VPS (widget computed statuses)
- runtime_audit CLI machine-readable
- incident JSONL auto-log timeline
- Telegram V2 anti-panic + JSONL fallback
- metrics SQLite

**Gaps** :
- **Capital occupancy tracker** non implemente (impact direct KPI user)
- **ROC par strat 30j/90j** non implemente
- Dashboard widget occupancy non livre (Phase 3)
- live_pnl_tracker historique 1j actuellement ("Insufficient history")

**Note dimension** : **7.0 / 10** (baisse vs 9.0 claim iter2 car metriques business manquantes non vues a l'epoque)

### 1.6 Architecture & Code

- worker.py 5402 LOC (encapsulation OSM + preflight + kill switch per-strat)
- Extraction `core/worker/cycles/*_runner.py` partielle (futures, paper, macro_ecb)
- Registries canoniques (books + whitelist + quant_registry)
- Logging double binding **fixe iter3-fix B7** (guard idempotent)

**Note dimension** : **8.5 / 10** (monolithe residuel acceptable solo-dev, fixes hygiene livres iter3-fix)

### 1.7 Coverage

**Claim historique iter1** : 65% core / 72% critical path.
**Statut iter3-fix2** : **NON re-mesure** post iter2/iter3. Confidence medium.

**Action requise si le nombre doit tenir** : re-run `pytest --cov` + `coverage report`. Estime <15 min.

**Note dimension** : **6.5 / 10** (doute raisonnable sur le nombre, pas de proof recent)

### 1.8 SPOFs / Ops

- Solo dev (structurel, accepte jusqu'a 10x capital)
- VPS unique Hetzner (structurel)
- IBKR Gateway unique (structurel)

**Note dimension** : **6.5 / 10** (inchange, constant structurel $20K capital)

---

## 2. Score plateforme recalcule (post iter3-fix2)

| Dimension | Note | Poids | Contribution |
|---|---|---|---|
| Tests pytest | 9.0 | 15% | 1.35 |
| Runtime audit coherence | 9.0 | 15% | 1.35 |
| Gouvernance fail-closed | 9.5 | 15% | 1.43 |
| Persistance atomic | 9.5 | 10% | 0.95 |
| Observabilite | 7.0 | 15% | 1.05 |
| Architecture / Code | 8.5 | 10% | 0.85 |
| Coverage | 6.5 | 10% | 0.65 |
| SPOFs / Ops | 6.5 | 10% | 0.65 |
| **TOTAL plateforme** | — | 100% | **8.28 / 10** |

**Arrondi : 8.5 / 10 plateforme post iter3-fix2.**

Ecart avec score 9.5 revendique iter2 : l'iter2 "9.5" melait plateforme + live readiness + ne ponderait pas la dimension observabilite business (occupancy, ROC par strat) ni coverage non re-mesure. Clarifie iter3-fix2.

---

## 3. Score live readiness business (honnetement)

Voir `live_readiness_scoreboard.md` pour detail. Synthese :

| Dimension | Note | Source |
|---|---|---|
| Live engine existant | 7.5 | 2 strats ACTIVE, 1 position |
| Diversification promotable 30j | 5.5 | 4 candidates READY (+1 post iter3-fix) |
| Fail-open surface | 9.5 | VPS 0 incoherence |
| Capital occupancy | 3.0 | 1.09% observe |
| Trade frequency | 3.5 | 0.1-0.2/jour vs cible 1/jour |
| Paper signal quality | 5.5 | 1/10 paper strats produit journal daily continu |

**Score live readiness ponderable : 5.5 / 10.**

---

## 4. Score ROC / capital usage

Voir `roc_capital_usage.md` pour detail. Synthese :

| Dimension | Note |
|---|---|
| Capital deploye lisible | 7.0 |
| Occupancy observee | 3.0 |
| ROC mesurable par strat | 3.5 |
| Contribution marginale | 2.0 |
| Alignement capital cible/reel | 5.5 |

**Score ROC : 4.0 / 10.**

---

## 5. Score qualite livrables docs

Voir `deliverables_consistency_review.md`. Synthese :

- Pre iter3-fix2 : 5.0/10 (docs melaient local/VPS/cible, script Alpaca ≠ doc)
- Post iter3-fix2 : **7.5 / 10** (docs alignes sur verite runtime, separation dimensions, sources citees)

---

## 6. Synthese — 4 scores distincts (pas de chiffre agrege)

| Dimension | Score | Confidence |
|---|---|---|
| **Plateforme** (code, gouv, tests) | **8.5 / 10** | high |
| **Live readiness** (paper + diversif + trade freq) | **5.5 / 10** | med |
| **ROC / capital usage** | **4.0 / 10** | high |
| **Qualite livrables docs** | **7.5 / 10** (post iter3-fix2) | high |

**Refus explicite** de faire une moyenne ponderee unique des 4. Les 4 axes sont orthogonaux et ont des rythmes de resolution differents :
- Plateforme : refactor code, rapide si priorite
- Live readiness : **temps paper 30j incompressible** (structurel)
- ROC / capital usage : depend promotions + metriques a livrer
- Docs : rapide si rigueur maintenue

---

## 7. Top 5 risques business (ranked)

1. **Capital occupancy 1.09% durable** — desk vivant mais sous-utilise. Resolution : promotions 3-5 strats (4-8 sem).
2. **Trade frequency 0.1-0.2/jour** — 5-10x sous cible. Resolution : diversification post promotion.
3. **B6r crypto alts parquets stale** (residuel) — bloque alt_rel_strength + btc_asia paper. **Deadline 2026-05-18**.
4. **mib_estx50 capital gap EUR 3.6K** — strat grade S bloquee funding. User decision.
5. **live_pnl_tracker historique insuffisant (1j)** — KPI PnL net pas mesurable avant 30j.

---

## 8. Historical context — scores historiques (point-in-time, archive)

### Iter0 baseline (2026-04-19T~16:00Z)
- Score revendique : **8.8/10** (combine)
- Contexte : debut session audit 9.5, mandat anti-gonflage

### Iter1 phase 1 urgent (2026-04-19T~18:00Z)
- Score revendique : **9.2/10**
- Gains : G1 dashboard VPS + G2 wf_exempt_reason + G3 coverage baseline

### Iter2 phase 2 stretch (2026-04-19T~20:00Z)
- Score revendique : **9.5/10** (plateforme seulement, confusion initiale)
- Gains : G4 OSM wire futures + G5 E2 defense + G6 cleanup

### Iter3 business audit (2026-04-19T~~22:00Z)
- Scores revendiques (premiere separation) : plateforme 9.5, live readiness 6.5, ROC 4.0
- Gains : 6 livrables docs business + alpaca gate script

### Iter3-fix (2026-04-19 PM)
- Scores revendiques (avant fix docs) : plateforme 9.5, live readiness 6.5, ROC 4.0
- Gains : B2 gold_trend_mgc WF V1 grade A + B5 btc_asia long_only wire + B6/B7/B8 hygiene

### Iter3-fix2 (2026-04-19T14:33Z) — CE DOCUMENT
- Scores recalcules : **plateforme 8.5, live readiness 5.5, ROC 4.0, docs 7.5**
- Gains : realignement 6 deliverables sur verite runtime + distinction repo/VPS/cible + alpaca doc-script parity

---

## 9. Honnete auditeur — ce que JE NE PEUX PAS revendiquer

- **Coverage 65/72 historique iter1** : pas re-mesure. Si le chiffre doit apparaitre dans les docs, re-run requis.
- **Paper signal quality** : je ne sais pas si le scheduler va fire correctement lundi 2026-04-20 pour les runners weekday (mes_monday, eu_relmom, us_sector_ls, mib_estx50, btc_asia). L'observation empirique lundi matin confirmera.
- **ROC projection +10-15% annualise post M3** : hypothese haircut -50% backtest. Confidence medium. Un seul signal (+$295 MCL) ne constitue pas preuve statistique.
- **Promotion_gate vert vs alpaca gate** : le script alpaca_go_25k_gate.py **ne verifie PAS** formellement promotion_gate. Clarifie dans alpaca_go_25k_rule.md iter3-fix2.

Ces gaps ne invalident PAS la decision business lundi matin (desk continue inchange), mais ils sont listes pour honnêtete auditeur.
