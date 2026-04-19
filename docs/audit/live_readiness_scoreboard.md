# Live Readiness Scoreboard — Trading Platform

**As of** : 2026-04-19T14:33Z (truth commands re-run + post iter3-fix)
**Mode** : comite senior CTO+CRO+Quant+PO+Ops. Anti-bullshit, anti-inflation.
**Sources de verite machine-readable** :

| Source | Commande | Valeur de retour |
|---|---|---|
| pytest suite | `python -m pytest -q -o cache_dir=.pytest_cache --basetemp .pytest_tmp` | **3669 pass, 50 skipped, 0 fail (exit 0)** |
| runtime audit local | `python scripts/runtime_audit.py --strict` | **exit 3 FAIL** (1 critical preflight + 4 data staleness) |
| runtime audit VPS | VPS `python scripts/runtime_audit.py --strict` | **exit 0 OK** (12/12 preflight, data fresh 41h) |
| alpaca gate | `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5` | **exit 2 NO_GO_paper_journal_missing** |

> **Encadre — si repo local != VPS, quel etat pilote la decision business ?**
>
> **VPS pilote**. Le repo local est un environnement de dev Windows qui ne
> maintient pas les state files live (equity_state, broker positions) ni les
> data feeds fraîches. Le runtime audit local echoue sur `equity_state::ibkr_futures`
> absent et parquets _1D stales — ces failures sont **attendues** sur dev et
> **ne reflètent pas** un blocker live. Le VPS Hetzner fait foi pour toute
> decision operationnelle. Le repo local fait foi pour la qualite code / tests.

---

## 1. Snapshot capital live (VPS, broker APIs)

| Book | Broker | Equity live | Buying power | Positions ouvertes | Unrealized PnL | source_of_truth | confidence |
|---|---|---|---|---|---|---|---|
| ibkr_futures | IBKR U25023333 | **$11,012.79** | $58,073.75 | 1 (MCL 1 contrat via CAM) | **+$295.23** | `data/state/ibkr_futures/equity_state.json` VPS | high (broker API) |
| binance_crypto | Binance France | **$9,843** | $1,000 spot USDT | 0 | $0 | `data/state/binance_crypto/equity_state.json` VPS | high (broker API) |
| alpaca_us (paper) | Alpaca | **$99,495.42** | $397,981 | 0 live (SPY simulation locale) | $0 | worker.log VPS 2026-04-19 13:56 | high (paper API) |
| ibkr_eu | paper-only book | n/a | — | — | — | books_registry.yaml | high |
| ibkr_fx | DISABLED ESMA | 0 | — | — | — | books_registry.yaml | high |

**Total live capital deployable** : **$20,856** (IBKR $11K + Binance $9.8K). Alpaca paper $99K non live.

**Capital au risque actuellement** : 1 position MCL, risk-if-stopped = ($75.85 - $73.57) x 100 = **$228 ≈ 1.09% du capital live**. 98.9% idle (confirme).

---

## 2. Classification honnete par strategie (16 canoniques, post iter3-fix)

### Taxonomie

- **AUTHORIZED** : `books_registry.mode_authorized = live_allowed` ET strat dans whitelist.
- **READY** : preuves machine-readable (WF manifest physique OU `wf_exempt_reason`) + paper_start_at + grade S/A/B + 0 infra_gap bloquant.
- **ACTIVE** : `is_live=true` dans quant_registry ET cycle live tournant (observation VPS).
- **PROMOTABLE** : READY + 30j paper sans divergence > 1-2 sigma + promotion_gate vert.
- **CAPITAL_ALLOCATED** : alloue dans `config/allocation.yaml` ou limits_live.
- **CAPITAL_USED** : observe en position (moyenne 30j).
- **ROC_CONTRIBUTIVE** : contribution CAGR > 0 et Sharpe > 0.

### Tableau (post iter3-fix, source = VPS runtime_audit + registries)

| strategy_id | book | STATUS runtime | GRADE | READY | ACTIVE | earliest PROMOTABLE | CAPITAL_USED (VPS) | source_of_truth | confidence |
|---|---|---|---|---|---|---|---|---|---|
| cross_asset_momentum | ibkr_futures | ACTIVE | A | ✅ | ✅ **LIVE** | — | $7.9K MCL | quant_registry + positions_live.json | high |
| gold_oil_rotation | ibkr_futures | ACTIVE | S | ✅ | ✅ **LIVE** | — | 0 (signal dormant) | quant_registry + IB API | high |
| gold_trend_mgc | ibkr_futures | **READY** | **A** | ✅ (iter3-fix B2) | ❌ | 2026-05-16 (30j paper) | 0 | wf_manifest + paper_start 2026-04-16 | high |
| mes_monday_long_oc | ibkr_futures | READY | B | ✅ | ❌ | 2026-05-16 | 0 | wf_manifest + paper_start 2026-04-16 | med |
| mes_wednesday_long_oc | ibkr_futures | READY | B | ⚠️ (MC 28.3% limite) | ❌ | 2026-06-01 (surveillance 45j) | 0 | wf_manifest | med |
| mes_pre_holiday_long | ibkr_futures | READY | B | ✅ | ❌ | seul trade rare (8-10/an) | 0 | wf_manifest | low (freq trop basse seul) |
| mcl_overnight_mon_trend10 | ibkr_futures | READY | B | ⚠️ friday re-WF requis | ❌ | 2026-05-30 si re-WF | 0 | wf_manifest + caveat friday_trigger | med |
| btc_dominance_rotation_v2 | binance_crypto | DISABLED | REJECTED | ❌ | ❌ | jamais | 0 | quant_registry | high |
| alt_rel_strength_14_60_7 | binance_crypto | READY | B | ✅ | ❌ | 2026-05-18 | 0 live / $3K gross paper simule | paper_journal.jsonl VPS | med |
| btc_asia_mes_leadlag_q70_v80 | binance_crypto | READY | B | ⚠️ mode=both incompat spot FR | ❌ | 🚫 bloque | config | high |
| btc_asia_mes_leadlag_q80_v80_long_only | binance_crypto | **READY** | **B** (iter3-fix B5) | ✅ | ❌ | 2026-05-20 (30j apres wire) | 0 | config + paper_cycles.py | high |
| eu_relmom_40_3 | ibkr_eu | READY | B | ✅ paper-only book | ❌ | 🚫 shorts sans plan | 0 | quant_registry | high |
| mib_estx50_spread | ibkr_eu | READY | S | ✅ paper-only book | ❌ | 🚫 margin EUR 13.5K > dispo EUR 9.9K | 0 | quant_registry + user capital | high |
| fx_carry_momentum_filter | ibkr_fx | DISABLED | — | ❌ | ❌ | jamais (ESMA) | 0 | books_registry | high |
| us_stocks_daily | alpaca_us | AUTHORIZED (paper-only book) | — (meta, wf_exempt) | ❌ | ❌ | 🚫 PDT waiver $25K | 0 | quant_registry | high |
| us_sector_ls_40_5 | alpaca_us | READY | B | ✅ paper-only book | ❌ | 🚫 shorts PDT + re-WF ETF | 0 | quant_registry | high |

### Synthese cardinal (VPS verite)
- **ACTIVE live** : **2 strats** (CAM + GOR) — confirme par positions_live.json VPS
- **READY live-promotable dans 30j avec blockers 0** : **4 strats**
  - gold_trend_mgc (A, iter3-fix)
  - mes_monday_long_oc (B)
  - alt_rel_strength_14_60_7 (B)
  - btc_asia_mes_leadlag_q80_v80_long_only (B, iter3-fix, earliest 2026-05-20)
- **READY mais blockers** : mes_wednesday (MC limite), mcl_overnight (re-WF friday), mes_pre_holiday (freq basse), mib_estx50 (capital), btc_asia q70 (mode both spot), eu_relmom (shorts), us_sector_ls (PDT+re-WF), us_stocks_daily (PDT)
- **DISABLED** : fx_carry (ESMA), btc_dominance (REJECTED)

---

## 3. Frequence de trades — VPS observation

| Periode | IBKR futures | Binance live | Binance paper | Total live |
|---|---|---|---|---|
| 30 derniers jours | 1-2 entries (MCL 2026-04-17, CAM entries rares) | 0 live | alt_rel_strength 1 cycle (2026-04-19) | ~1-2 trades live |
| Cible user | — | — | — | **~1/jour moyenne 30j** |
| **Gap** | — | — | — | **5-10x sous cible** |

**Source** : `data/tax/classified_trades.jsonl` VPS + `data/state/ibkr_futures/positions_live.json` VPS.

---

## 4. Blockers par objectif (post iter3-fix)

### Objectif A : 1 moteur live vraiment exploitable IBKR futures
**STATUT (VPS)** : ✅ **ATTEINT** (CAM + GOR ACTIVE, MCL position +$295).
**STATUT (repo local)** : runtime_audit FAIL (equity_state absent) — **dev-env only, pas P0**.

Prochaine expansion 30j :
- Si paper 30j OK : **mes_monday_long_oc** (2026-05-16) + **gold_trend_mgc V1** (2026-05-16 si paper reste stable).
- Source : quant_registry.yaml + `data/state/{strategy}/paper_journal.jsonl` (VPS).

### Objectif B : 1 sleeve Binance candidate live_probation
**STATUT** : 🟡 **EN COURS**. 2 candidates paper :
- `alt_rel_strength_14_60_7` paper_start 2026-04-18 → earliest live 2026-05-18
- `btc_asia_mes_leadlag_q80_v80_long_only` paper_start 2026-04-20 → earliest live 2026-05-20 (post B5 wire)

Blockers residuels :
- **B6r** : BTCUSDT_1h.parquet + alts parquets stale (VPS observed ~21j) → crypto paper fails on data_is_fresh guard. **Non fixe par iter3** (MES_1H_YF2Y seul fixe).
- `btc_asia q70 v80 both` conserve mais **ne remplace pas** la q80_v80_long_only pour live FR.

### Objectif C : Pas de fail-open
**STATUT** : ✅ **OK** runtime_audit VPS 0 incoherence.

Fixes iter3 livres :
- B7 logging double binding (guard idempotent RotatingFileHandler)
- B8 legacy crypto tests quarantaine formelle (tests/_archive)
- B9 paper runners audit (root cause = B6)

### Objectif D : Capital usage lisible + PnL live visible
**STATUT** : 🟡 **PARTIEL**.
- `scripts/live_pnl_tracker.py --summary` = "Insufficient history (need >=2 days)" (source VPS, 2026-04-19 run).
- Build-up 30j requis. Pas de dashboard occupancy par strat implemente.

### Objectif E : Runtime == whitelist == registries == dashboard
**STATUT (VPS)** : ✅ **OK** (runtime_audit exit 0, 0 incoherence).
**STATUT (local)** : ⚠️ runtime_audit FAIL pour equity_state absent — **attendu sur dev**.

---

## 5. Score — 2 niveaux distincts (post iter3-fix)

### Score **plateforme** (code qualite + gouvernance, indifferent de paper time)

| Dimension | Note | Source | confidence |
|---|---|---|---|
| Tests pytest | 9.5 | 3669 pass 0 fail | high |
| Runtime audit incoherences | 9.5 | VPS exit 0, 0 incoherence | high |
| Runtime audit preflight local | 6.5 | exit 3 FAIL (mais attendu dev) | high |
| Gouvernance fail-closed | 9.5 | promotion_gate + pre_order_guard + boot preflight | high |
| Persistance atomic | 9.5 | DDBaselines + OrderTracker + atomic tempfile+fsync | high |
| Observabilite | 8.5 | dashboard D3 LIVE VPS + runtime_audit CLI + incident JSONL. Gap : pas de occupancy tracker. | med |
| Coverage core | 6.5 | 65% core / 72% critical (non re-mesure iter3) | med |

**Note plateforme ponderee** : **8.5 / 10** (baisse vs 9.5 claim historique iter2 car : coverage pas re-mesure, preflight local FAIL non documente, dashboard occupancy non livre).

### Score **live readiness business** (temps paper + diversification + trade freq + ROC)

| Dimension | Note | Source | confidence |
|---|---|---|---|
| Live engine existant | 7.5 | 2 strats ACTIVE, 1 position MCL +$295 | high |
| Diversification promotable 30j | 5.5 | 4 candidates READY dans 30j (+1 vs iter3 pre-fix) | med |
| Fail-open surface | 9.5 | Confirme par runtime_audit VPS exit 0 | high |
| Capital occupancy | 3.0 | 1.09% observe. Gap massif vs cible 15-30% M1. | high |
| Trade frequency observee | 3.5 | ~0.1-0.2/jour vs cible ~1/jour | high |
| Paper signal quality | 5.5 | alt_rel_strength seul produit journal continu. Autres weekday ou bloques par data. | med |

**Note live readiness ponderee** : **5.5 / 10** (baisse vs 6.5 claim iter3 initial car : paper signal quality reevalue, capital occupancy inchange, trade freq observe insuffisante).

### Score **livrables docs** (consistency vs runtime reel)

Voir `docs/audit/deliverables_consistency_review.md` pour detail. **5.0 / 10** pre-correction iter3-fix2 (ce document), **7.5 / 10** post-correction.

---

## 6. Top 10 risques residuels (ranked par impact business)

| # | Risque | Source observation | Fix scope |
|---|---|---|---|
| 1 | Capital occupancy 1.09% durable | VPS positions_live.json | Promouvoir 3-4 paper probation (30-45j) |
| 2 | Trade freq 0.1-0.2/jour vs cible 1/jour | VPS classified_trades.jsonl | Diversification post paper validation |
| 3 | Crypto alts parquets stale (B6r) | paper_cycles.py warnings | Cron crypto refresh 15 min VPS |
| 4 | mes_wednesday MC 28.3% P(DD>30%) | wf_manifest | MC re-run post more data OR sizing reduit |
| 5 | mib_estx50 capital gap EUR 3.6K | user funding decision | Decision explicite 2026-04-20 |
| 6 | live_pnl_tracker historique insuffisant (1j) | live_pnl_tracker --summary | Build-up passif 30j |
| 7 | mcl_overnight friday re-WF pending | quant_registry notes | Scaffold re-WF script friday trigger |
| 8 | Coverage non re-mesure post iter3 | pas de run coverage recent | Re-run coverage.py si claim 65/72 actuel |
| 9 | Dashboard occupancy widget non livre | code base | Scope Phase 3 post-9.5 |
| 10 | Solo dev + VPS unique | structurel | Accepte jusqu'a capital 10x |

---

## 7. Actions lundi matin 2026-04-20 (concretes, executables)

| # | Action | Durée | Blocker resolu | Source verifiable |
|---|---|---|---|---|
| 1 | `ssh vps "tail -300 logs/worker/worker.log \| grep -iE 'paper_cycle\|runner'"` → confirmer que weekday triggers fired pour mes_monday + eu_relmom + us_sector_ls + btc_asia | 10 min | B9 | worker.log VPS |
| 2 | Verifier cron refresh MES_1H_YF2Y a tourne 21:35 UTC dim soir (devrait être samedi dernier puisque 21:35 Mon-Fri, donc pas dim mais lun 20-04 21:35) | 5 min | B6 (partie MES) | crontab + mes_1h_cron.log |
| 3 | Scaffolder cron crypto alts refresh 15min ou 1h (BTCUSDT alts) — fix residuel B6r | 30 min | B6r | proposer script à user avant deploy |
| 4 | User decision funding IBKR +EUR 3.6K mib_estx50 (OUI/NON) | 5 min | funding mib_estx50 | user |
| 5 | Re-run `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5` apres un premier cycle paper us_sector_ls lundi 23h30 Paris | 2 min | alpaca gate evolution | gate output exit code |
| 6 | Re-mesurer coverage si claim "65/72" doit tenir dans docs | 15 min | doc consistency | coverage.py report |

---

## 8. Honnêteté auditeur — ce qui reste incertain

1. **Paper signal quality observee** : seul alt_rel_strength tourne 7j/7. mes_monday, eu_relmom, us_sector_ls, mib_estx50 tournent seulement weekday — on saura lundi 2026-04-20 si le scheduler fire correctement. Gap **B9 n'est pas strictement "resolu"** — on a identifie le root cause (B6 data stale), mais l'observation empirique lundi confirmera.
2. **Promotion gate checker (doc Alpaca)** : le gate script `alpaca_go_25k_gate.py` **ne verifie PAS** formellement `promotion_gate` vert, seulement les metriques paper + incidents. La condition "promotion gate vert" du doc est **operationnelle uniquement** (a verifier manuellement au moment du depot). **Documente dans alpaca_go_25k_rule.md corrige**.
3. **Coverage** : claim "65% core / 72% critical" date de l'iter1. **Non re-mesure post iter3**. Inclusion dans score plateforme = confidence medium.
