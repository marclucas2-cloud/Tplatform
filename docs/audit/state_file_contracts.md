# State File Contracts — H5 T4

**As of** : 2026-04-19T15:55Z
**Phase** : H5 TODO XXL hygiene. Comportement + severite, pas catalogue.
**Livrable** : ce document. Contracts machine-readable section 3.
**Verification** : inventaire local + VPS (2026-04-19T15:50Z).

---

## 0. Principe directeur T4

> Ce document n'est pas un inventaire de fichiers. C'est un **contrat comportemental**.
>
> Pour chaque state file : **que doit faire le systeme si le fichier est absent / stale / corrompu** ?
>
> La verite runtime (VPS) doit toujours primer sur la verite locale (dev) quand il s'agit de decisions business.

**Anti-principe** : "ce fichier existe, OK". Insuffisant.

---

## 1. Taxonomie criticite — 4 niveaux

| Niveau | Definition comportementale | Decision si absent sur VPS | Decision si absent en local |
|---|---|---|---|
| **P0 live-critical** | Absence/stale/corrupt → **worker refuse de boot OR book passe BLOCKED** → 0 ordre live | BOOT FAIL fail-closed (exit 2) | dev gap expected, tolere |
| **P1 runtime-important** | Absence/stale → warning + degrade mode (DEGRADED health) → ordres passent mais log | warning, cycle continue avec fallback | tolere |
| **P2 audit/reporting** | Absence → pas de blocage, mais audit trail incomplet | info log, pas d'action | ignore |
| **P3 derived/cache** | Absence → regeneration automatique au prochain cycle | pas d'action | ignore |

**Regle de gouvernance** : aucun fichier P0 ne doit etre facile a produire manuellement. Si un fichier est "P0 pour trader", il doit etre **ecrit par le worker au fonctionnement normal**.

---

## 2. Qui a le droit de bloquer le live

**Autorite** :
- `core/runtime/preflight.py:boot_preflight(fail_closed=True)` → bloque le boot du worker si un fichier P0 critique absent
- `core/governance/book_health.py:check_{book}()` → passe un book a BLOCKED si critical_check failure runtime
- `core/governance/pre_order_guard.py:check 5` (book health) → refuse ordre si book BLOCKED

**Regle locale vs VPS** :
- **VPS** : fichier P0 absent → `boot_preflight` exit 2. Systemd restart loop, Telegram alert. **Worker ne trade pas**.
- **Local (dev Windows)** : fichier P0 absent → `runtime_audit.py --strict` exit 3 **mais n'empeche pas** le dev (pas de worker live en local). Comportement **attendu et documente**.

**Exemple canonique** :
- `data/state/ibkr_futures/equity_state.json` **absent sur VPS** = BOOT FAIL, live futures BLOCKED.
- `data/state/ibkr_futures/equity_state.json` **absent en local** = dev gap **EXPECTED** (dev Windows ne run pas IBKR worker).

---

## 3. Matrice contracts state files — les fichiers qui comptent

### 3.1 P0 live-critical (9 fichiers)

#### 3.1.1 `config/books_registry.yaml`
| Champ | Valeur |
|---|---|
| Producer | humain via PR |
| Consumers | `preflight.py`, `pre_order_guard.py`, `book_health.py`, `runtime_audit.py`, worker main |
| Write frequency | sur changement doctrine (jours/semaines) |
| Criticity | **P0** |
| Si absent | `boot_preflight` FAIL critical `registry::books_registry` → BOOT EXIT 2 |
| Si stale (vs doc latest) | n/a (pas de timestamp canonique) |
| Si corrompu (yaml invalid) | Parse error → FAIL critical |
| Local vs VPS | **Identique obligatoire** (synced via git) |

#### 3.1.2 `config/live_whitelist.yaml`
Idem `books_registry.yaml`. Tout champ absent → BOOT FAIL.

#### 3.1.3 `config/quant_registry.yaml`
Idem `books_registry.yaml`. Loader `core/governance/quant_registry.py`.

#### 3.1.4 `data/state/ibkr_futures/equity_state.json`
| Champ | Valeur |
|---|---|
| Producer | `core/broker/ibkr_adapter.py:authenticate()` (persist apres chaque connect) |
| Consumers | `preflight.py:_check_equity_state`, `book_health.py`, worker risk boot, DDBaselines |
| Write frequency | chaque boot worker + chaque cycle futures live (~daily) |
| Criticity | **P0** |
| Si absent (VPS) | `preflight` FAIL critical → BOOT EXIT 2. Worker peut pas calculer DD baseline |
| Si absent (local) | dev gap **expected** (dev n'a pas IBKR live creds) |
| Si stale (> 24h) | warning `_state_file_age_check_any` dans book_health → DEGRADED |
| Si corrompu | `_state_file_age_check_any` retourne DEGRADED + error message |
| Schema | `{equity, cash, buying_power, currency, paper, account_number, source, updated_at}` |

**Actuellement VPS** : present $11,012.79 equity (2026-04-19T13:52Z). ✅

#### 3.1.5 `data/state/binance_crypto/equity_state.json`
| Champ | Valeur |
|---|---|
| Producer | `core/broker/binance_broker.py:get_account_info()` → `_persist_equity_state()` |
| Consumers | `book_health.py:check_binance_crypto` + DDBaselines + `alpaca_gate` (autres strats ref) |
| Write frequency | chaque cycle crypto live (~toutes les 30min) |
| Criticity | **P0** (si mode=live_allowed et strats actives) |
| Si absent (VPS) | `preflight` FAIL → BOOT EXIT 2 |
| Si absent (local) | dev gap expected |
| Si stale (> 1h) | `_state_file_age_check_any` max_age=1.0h → DEGRADED |
| Si corrompu | DEGRADED |
| Schema | `{equity, cash, buying_power, spot_usdt, earn_total_usd, margin_level, source, updated_at, paper}` |

**Actuellement VPS** : present $9,843 equity. ✅ (local aussi pour dev).

#### 3.1.6 `data/state/alpaca_us/equity_state.json`
| Champ | Valeur |
|---|---|
| Producer | `core/alpaca_client/client.py:authenticate()` |
| Consumers | book_health alpaca + dashboard |
| Write frequency | chaque authenticate (boot + cycle intraday US) |
| Criticity | **P0** si mode=live_allowed. Actuellement mode=paper_only → P1 (tolerant) |
| Si absent (VPS) | preflight : P0 `ibkr_equity` → blocking si live. Pour alpaca_us paper_only : warning only |
| Local vs VPS | VPS present (post authenticate), local absent OK |

**Note** : books_registry dit `alpaca_us.mode_authorized=paper_only` → severite actuelle **abaissee a P1**. Redeviendra P0 si mode -> live_allowed.

#### 3.1.7 `data/state/ibkr_futures/positions_live.json`
| Champ | Valeur |
|---|---|
| Producer | `worker.py:reconcile_positions_at_startup()` + updates par cycle futures |
| Consumers | `book_health.py`, `reconciliation.py`, worker recovery boot |
| Write frequency | chaque modif position live |
| Criticity | **P0** |
| Si absent (VPS) | BOOT FAIL futures_state check (book_health critical). Workwr refuse trade |
| Si stale | book_health.py max_age_hours=24 → DEGRADED |
| Si corrompu | Parse error → DEGRADED + incident CRITICAL |
| Schema | `{SYMBOL: {strategy, symbol, side, qty, entry, sl, tp, oca_group, opened_at, mode, _authorized_by}}` |

**Actuellement VPS** : 1 MCL position. ✅

#### 3.1.8 `data/kill_switch_state.json` (global) ET `data/crypto_kill_switch_state.json` (scoped crypto)
| Champ | Valeur |
|---|---|
| Producer | `core/kill_switch_live.py` + `core/crypto/risk_manager_crypto.py` |
| Consumers | `book_health.py`, `pre_order_guard.py` check 6, tous les cycles live |
| Write frequency | chaque transition active/inactive + chaque heartbeat |
| Criticity | **P0** |
| Si absent | OK si absent (interprete comme "never activated") |
| Si active=true | **BLOCK TOUT LIVE** sur le scope concerne |
| Si corrompu | **Fail-closed** : assume active=true (paranoia) |
| Schema global | `{active: bool, triggered_at, reason, ...}` |
| Schema scoped | + `disabled_strategies: set` per-strategy E2 |

**Actuellement VPS** : inactive. ✅ Fail-closed comportement = si parsing fails, on bloque.

#### 3.1.9 `data/crypto_dd_state.json` (DDBaselines)
| Champ | Valeur |
|---|---|
| Producer | `core/crypto/risk_manager_crypto.py:DDBaselines._save()` atomic tempfile+fsync |
| Consumers | `risk_manager_crypto.check()` au boot + chaque cycle |
| Write frequency | chaque cycle crypto (check_daily/weekly/monthly) |
| Criticity | **P0** |
| Si absent (VPS) | **PREMIER BOOT**: C1 BootState=FIRST_BOOT, baselines = equity courante. Sinon corrupte. |
| Si absent (local) | expected |
| Si stale | baselines peuvent etre de J-7, 14, 30 — tolere (daily_anchor, weekly_anchor) |
| Si corrompu | **STATE_CORRUPT** → fail-closed trade block OR reinit (selon config) |
| Schema | `{session_id, schema_version, daily_anchor, daily_start_equity, weekly_*, monthly_*, peak_equity, total_equity, last_check_ts}` |

#### 3.1.10 `data/live_risk_dd_state.json` (daily anchor futures)
| Champ | Valeur |
|---|---|
| Producer | `core/risk_manager_live.py` (worker cycle) |
| Consumers | risk checks + kill_switch trigger |
| Write frequency | chaque cycle live_risk (~5-10 min) |
| Criticity | **P0** (si IBKR futures actif) |
| Si absent (VPS) | reset baseline a equity courante (J0 anchor) |
| Si stale | max 1j → rotation daily au J+1 |
| Si corrompu | **Fail-closed** : bloque futures jusqu'a reinit manuel |
| Schema | `{daily_start_equity, date}` (simple) |

### 3.2 P1 runtime-important (6 familles)

#### 3.2.1 `data/state/{strategy}/paper_journal.jsonl` (JSONL append-only)
| Champ | Valeur |
|---|---|
| Producer | `core/worker/cycles/paper_cycles.py` + `core/runtime/alt_rel_strength_runner.py` |
| Consumers | `alpaca_go_25k_gate.py`, `promotion_gate.py`, dashboard paper perf |
| Write frequency | chaque paper cycle (weekday ou daily selon strat) |
| Criticity | **P1** |
| Si absent (VPS) | aucun blocage trade. Alpaca gate → `NO_GO_paper_journal_missing` |
| Si absent (local) | expected dev gap |
| Si stale (> 7j) | warning paper inactif — investigate scheduler |
| Si corrompu | ligne corrompue → skip, autres OK (JSONL resilient) |

**Strats affectees** : alt_rel_strength (actif), btc_asia_mes_leadlag_q70 + q80 (pending), eu_relmom, us_sector_ls, mib_estx50_spread, mes_monday/wed/pre_holiday, mcl_overnight, gold_trend_mgc.

**Actuellement VPS** : alt_rel_strength seul ecrit (1 cycle). Autres attendus lundi 2026-04-20.

#### 3.2.2 `data/state/{strategy}/state.json` (runner state)
| Champ | Valeur |
|---|---|
| Producer | runners (ex `alt_rel_strength_runner`) |
| Consumers | runner au re-entry (idempotency, resume) |
| Write frequency | chaque cycle |
| Criticity | **P1** |
| Si absent | runner reinit (acceptable) |
| Si corrompu | runner reinit avec warning |

#### 3.2.3 `data/safety_mode_state.json`
| Champ | Valeur |
|---|---|
| Producer | `core/risk/safety_mode.py` |
| Consumers | worker cycles, dashboard |
| Criticity | **P1** |
| Si absent | assume safety_mode=false (normal) |

#### 3.2.4 `data/engine_state.json`
| Champ | Valeur |
|---|---|
| Producer | generic engine state persist |
| Consumers | worker |
| Criticity | **P1** |
| Si absent | reinit |

#### 3.2.5 `data/state/ibkr_futures/positions_paper.json`
Comme positions_live.json mais pour paper mode (port 4003). **P1** seulement car paper.

#### 3.2.6 OrderTracker state (ephemerine, atomic persist)
`core/execution/order_tracker.py` persist atomic via tempfile+fsync+replace. Par session, recharge au boot.
| Critere | Valeur |
|---|---|
| Criticity | **P1** |
| Si absent | re-boot ignore OSM recovery, nouvelle session |
| Si corrompu | atomic ensures never corrupt mid-write — mais si catastrophe : reinit |

### 3.3 P2 audit/reporting (5 familles)

#### 3.3.1 `data/incidents/*.jsonl` (append-only timeline)
| Champ | Valeur |
|---|---|
| Producer | `core/monitoring/incident_report.py:log_incident_auto()` |
| Consumers | `alpaca_go_25k_gate.py` (filter P0/P1 open), post-mortem manuel |
| Write frequency | chaque incident detecte (reconciliation divergence, preflight fail, promotion_gate block...) |
| Criticity | **P2** (pas de blocage trade si absent) |
| Si absent | 0 incident log — **acceptable**, pas de blocage |
| Si VPS incidents open > 0 | Alpaca gate → `NO_GO_incident_open` (filtre book/date) |
| Schema JSONL | `{timestamp, category, severity, source, message, context}` |

**Note gitignore T1b** : contenu ignore, structure versionnee via `data/incidents/README.md`.

#### 3.3.2 `data/alerts/alerts.jsonl`
Append-only alerts log (Telegram fallback). P2.

#### 3.3.3 `data/audit/orders_*.jsonl`
Audit trail orders. P2. 1 fichier/jour.

#### 3.3.4 `data/reconciliation/*.json` (daily snapshots)
| Champ | Valeur |
|---|---|
| Producer | `core/governance/reconciliation_cycle.py` |
| Consumers | post-mortem, debug divergences |
| Criticity | **P2** |
| Si absent | aucune perte (reprod par prochain cycle) |

#### 3.3.5 `data/monitoring/heartbeat.json`
Heartbeat worker. **P2**. Si absent → pas de health monitoring externe mais trade continue.

### 3.4 P3 derived/cache (4 familles)

#### 3.4.1 `data/research_funnel/*.json`
Cache research signals. **P3** regen.

#### 3.4.2 `data/orchestrator/state.json`
Orchestrator state (si actif). **P3**.

#### 3.4.3 `data/tickets/*.json`
Local issue tracking. **P3**.

#### 3.4.4 `data/backups/*` + `reports/coverage.json`
Backups + coverage cache. **P3** regenerable.

---

## 4. Vue tabulaire par severite

| Fichier | P | Producer | Consumer primaire | Absent VPS = | Absent local = | Stale = | Corrupt = |
|---|---|---|---|---|---|---|---|
| `config/books_registry.yaml` | P0 | humain PR | preflight + all | BOOT FAIL | n/a (synced) | n/a | FAIL |
| `config/live_whitelist.yaml` | P0 | humain PR | preflight + all | BOOT FAIL | n/a | n/a | FAIL |
| `config/quant_registry.yaml` | P0 | humain PR | preflight + all | BOOT FAIL | n/a | n/a | FAIL |
| `data/state/ibkr_futures/equity_state.json` | P0 | ibkr_adapter | preflight + risk | **BOOT FAIL** | expected | DEGRADED | DEGRADED |
| `data/state/binance_crypto/equity_state.json` | P0 | binance_broker | book_health + risk | **BOOT FAIL** | OK (present local) | DEGRADED 1h | DEGRADED |
| `data/state/alpaca_us/equity_state.json` | P0→P1 (mode paper_only) | alpaca_client | book_health | warning | expected | warning | warning |
| `data/state/ibkr_futures/positions_live.json` | P0 | worker reconcile | book_health + reconciliation | BOOT FAIL | expected | DEGRADED | CRITICAL |
| `data/kill_switch_state.json` | P0 | kill_switch_live | pre_order_guard | OK (absent = not active) | OK | n/a | **fail-closed** |
| `data/crypto_kill_switch_state.json` | P0 | crypto risk mgr | book_health | OK | OK | n/a | **fail-closed** |
| `data/crypto_dd_state.json` | P0 | risk_manager_crypto | risk boot | FIRST_BOOT init | expected | tolere (rollover) | STATE_CORRUPT |
| `data/live_risk_dd_state.json` | P0 | risk_manager_live | daily DD | reset J0 | expected | rotation J+1 | **fail-closed** |
| `data/state/{strat}/paper_journal.jsonl` | P1 | paper runners | alpaca_gate + promo | `NO_GO_paper_journal_missing` | expected | investigate scheduler | skip ligne |
| `data/state/{strat}/state.json` | P1 | runners | resume logic | reinit OK | OK | OK | reinit |
| `data/safety_mode_state.json` | P1 | safety_mode.py | worker | assume false | OK | n/a | warning |
| `data/engine_state.json` | P1 | engine | worker | reinit | OK | n/a | reinit |
| `data/state/ibkr_futures/positions_paper.json` | P1 | worker paper | book_health paper | warning only | OK | n/a | warning |
| OrderTracker state | P1 | order_tracker atomic | OSM recovery boot | fresh session | OK | n/a | atomic-safe |
| `data/incidents/*.jsonl` | P2 | incident_report | alpaca_gate, post-mortem | 0 incidents = OK | OK | n/a | skip ligne |
| `data/alerts/alerts.jsonl` | P2 | worker alerts | post-mortem | OK | OK | n/a | skip |
| `data/audit/orders_*.jsonl` | P2 | worker audit | audit trail | OK | OK | n/a | skip |
| `data/reconciliation/*.json` | P2 | recon_cycle | debug | OK | OK | n/a | regen |
| `data/monitoring/heartbeat.json` | P2 | heartbeat | external monitoring | warning | OK | warning | regen |
| `data/research_funnel/*.json` | P3 | research | cache | regen | OK | regen | regen |
| `data/orchestrator/state.json` | P3 | orchestrator (si actif) | resume | OK | OK | n/a | reinit |
| `data/tickets/*.json` | P3 | local issues | browse | OK | OK | n/a | skip |
| `data/backups/*` | P3 | backup.sh cron | recovery manuel | cron recreate | OK | n/a | skip/next backup |
| `reports/coverage.json` | P3 | pytest --cov | dashboard | regen | OK | regen | regen |

---

## 5. Differences comportementales local vs VPS — explicites

### Locaux attendus absents (dev Windows, jamais P0)

- `data/state/ibkr_futures/equity_state.json` → dev Windows n'a pas IBKR live creds
- `data/state/alpaca_us/equity_state.json` → dev n'authentifie pas Alpaca
- `data/state/*/paper_journal.jsonl` (sauf exceptions) → dev ne tourne pas le worker en continu
- `data/crypto_dd_state.json`, `data/live_risk_dd_state.json` → baselines runtime uniquement
- `data/incidents/*.jsonl`, `data/alerts/alerts.jsonl`, `data/audit/*.jsonl` → append-only runtime
- `data/reconciliation/*.json` → runtime uniquement

**Consequence** : `runtime_audit.py --strict` **local** exit 3 est **attendu et documente**. Ne pas paniquer.

### VPS obligatoires (absent = incident)

- Tous les P0 au sens strict du tableau section 3.1.
- Particulierement : les `equity_state.json` des books avec `mode_authorized=live_allowed` (ibkr_futures + binance_crypto).

### Les 2 qui passent `P0 → P1` par config

- `data/state/alpaca_us/equity_state.json` : P0 en doctrine, **P1 actuellement** parce que `books.alpaca_us.mode_authorized=paper_only`.
- `data/state/ibkr_eu/*.json` : idem, `ibkr_eu.mode_authorized=paper_only`.

Si le mode change → severite P0 retablie automatiquement.

---

## 6. Gap matrix — fichiers mentionnes mais non existants / incoherents

| Fichier | Status observation | Gap |
|---|---|---|
| `data/state/global/kill_switch_state.json` | referenced book_health.py mais absent | **Fallback OK** (multiple paths candidates, `data/kill_switch_state.json` seul present) |
| `data/state/binance_crypto/positions.json` | referenced books_registry.yaml mais absent | **Notes stale** (books_registry.yaml lists un fichier non-existant). A corriger P3. |
| `data/state/binance_crypto/dd_state.json` | referenced books_registry.yaml mais absent (actuellement `data/crypto_dd_state.json`) | **Inconsistence path** books_registry notes vs reality. P3 cleanup. |
| `data/state/ibkr_eu/equity_state.json` | absent, paper_only accepte | **OK** (book paper_only) |
| `data/state/btc_asia_mes_leadlag/paper_journal.jsonl` | absent VPS (dir empty) | **Expected** (run weekday, 2026-04-20 sera premier fire apres B5+B6 fix) |
| `data/state/mib_estx50_spread/` | absent VPS | **Expected** jusqu'a first Monday trigger (weekday 17h45 Paris) |

**Gap P3 identifie** : `books_registry.yaml` notes mentionnent `positions.json` et `dd_state.json` dans `binance_crypto/` subdir alors que la realite est `data/crypto_dd_state.json` au root + pas de positions.json binance local. P3 cleanup to align (pas bloquant).

---

## 7. DoD — 5 questions user (< 2 min)

### Q1 : Quels fichiers sont critiques pour trader ?

**11 fichiers P0** (tableau 3.1 + 3.4). Dont les 5 les plus critiques :
- `data/state/ibkr_futures/equity_state.json` (DD baseline live futures)
- `data/state/binance_crypto/equity_state.json` (DD baseline live crypto)
- `data/state/ibkr_futures/positions_live.json` (reconcile + open positions)
- `data/kill_switch_state.json` (global kill switch state)
- `data/crypto_dd_state.json` (DDBaselines crypto, C1 BootState)

### Q2 : Lesquels sont seulement utiles pour auditer ?

**P2 uniquement** (5 familles) :
- `data/incidents/*.jsonl`
- `data/alerts/alerts.jsonl`
- `data/audit/orders_*.jsonl`
- `data/reconciliation/*.json`
- `data/monitoring/heartbeat.json`

### Q3 : Lesquels peuvent manquer sans bloquer ?

**Tous P2 + P3 + certains P1** (paper_journal.jsonl, engine_state, safety_mode_state).

Liste explicite : incidents, alerts, audit trails, reconciliation snapshots, heartbeat, paper_journals individuels, runner states, research_funnel cache, orchestrator state, tickets, backups, coverage.

### Q4 : Lesquels doivent faire passer un book en BLOCKED ?

**Tous P0 pour le book concerne** :
- `equity_state.json` absent → book BLOCKED via preflight+book_health
- `positions_live.json` absent + broker reports positions → book DEGRADED (via book_health.py logic)
- `kill_switch.active=true` → book BLOCKED via pre_order_guard
- `crypto_dd_state.json` corrompu (STATE_CORRUPT) → book BLOCKED fail-closed
- `live_risk_dd_state.json` corrompu → futures BLOCKED fail-closed

### Q5 : Lesquels sont des artefacts derives sans pouvoir decisionnel ?

**Tous P3** :
- `data/research_funnel/*.json`
- `data/orchestrator/state.json`
- `data/tickets/*.json`
- `data/backups/*`
- `reports/coverage.json`

Plus les fichiers de cache comme `data/monitoring/wf_swing_results.json`, `data/risk/stress_test_report.json`.

---

## 8. Tests couvrant contracts

| Test | Couverture contract |
|---|---|
| `test_boot_preflight.py` | preflight equity_state check P0 + registries |
| `test_dd_baseline_persistence.py` | DDBaselines atomic save (P0 crypto + futures) |
| `test_crypto_risk_boot_state.py` | C1 BootState (FIRST_BOOT / STATE_RESTORED / STATE_STALE / STATE_CORRUPT) |
| `test_order_state_machine.py` + `test_order_tracker_recovery.py` | OrderTracker P1 atomic + recovery |
| `test_kill_switch_*` (5 tests) | kill_switch_state P0 + scoped disable |
| `test_book_runtime.py` + `test_health_and_state_hardening.py` | book_health critical/important/derived |
| `test_incident_report.py` + `test_incident_auto_log.py` | incidents JSONL P2 append + schema |
| `test_reconciliation_*` (4 tests) | reconciliation P2 per book + severity distinction paper/live |
| `test_state_corruption.py` | tolerance corrupt state |
| `test_backup_restore.py` | backups P3 integrite restore |

**Gap** : aucun test **explicite** sur "absent vs stale vs corrupt behavior" pour chaque fichier P0. Backlog **P2** (systemic, pas urgent).

---

## 9. Actions immediates identifiees

### Aucune action P0 requise 2026-04-19

Aucun fichier P0 manquant sur VPS. Preflight VPS exit 0 confirme.

### P3 cleanup (hors scope T4, backlog)

1. `config/books_registry.yaml` notes mentionnent paths `binance_crypto/positions.json` et `binance_crypto/dd_state.json` obsoletes → align avec realite (`data/crypto_dd_state.json` au root).
2. `config/books_registry.yaml` notes reduce snapshot operatoires (equity en dur dans notes = drift potential).

### P2 gap tests identifie

- Scaffold `tests/test_state_file_contracts.py` verifiant comportement absent/stale/corrupt pour chaque P0. Ajouter en CI.

### P1 backlog existant reitere

- `tests/test_alpaca_go_25k_gate.py` absent (T2 finding)
- 5 modules governance/execution a 0% coverage (T2)

---

## 10. Ligne rouge T4 respectee

- ✅ Contrat comportemental, pas catalogue
- ✅ Severite explicite P0/P1/P2/P3
- ✅ Comportement absent/stale/corrupt par fichier critique
- ✅ Distinction locale vs VPS pour chaque P0
- ✅ "Qui a le droit de bloquer le live" ecrit noir sur blanc (section 2)
- ✅ DoD 5 questions user repondues (section 7)
- ✅ Gap matrix des incoherences mineures (section 6)
- ✅ Pas de rewrite massif, juste contracts + P3 backlog

**Prochain** : T5 H6 runtime/ops matrix. Couplage naturel avec T4 (les state files P0 sont consumes par les scripts de verite runtime).
