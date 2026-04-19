# Runtime Hygiene Matrix — H6 T5

**As of** : 2026-04-19T16:00Z
**Phase** : H6 TODO XXL hygiene. Autorite + frequence + decision, pas catalogue.
**Livrable** : ce document. Matrice d'autorite + commandes operationnelles.
**Couplage** : consomme les state files P0 definis en T4 ([state_file_contracts.md](state_file_contracts.md)) + registries canoniques T3 ([canonical_truth_map.md](canonical_truth_map.md)).

---

## 0. Principe directeur T5

> Ce document n'est pas un catalogue de scripts. C'est une **matrice d'autorite**.
>
> Chaque script / service / cycle repond a une question precise avec autorite definie.
> **Si deux sources se contredisent, il y a un gagnant ecrit noir sur blanc.**
>
> La ligne de commande VPS fait foi pour toute decision business.

**Anti-principe** : "ce script existe". Insuffisant. On veut : **qui tranche quoi ?**

---

## 1. Les 6 verites runtime canoniques

| Verite | Autorite primaire | Commande | Fail-mode si autorite down |
|---|---|---|---|
| **Etat global plateforme** | `scripts/runtime_audit.py --strict` | `python scripts/runtime_audit.py --strict` | Exit 3 si local/FAIL si VPS → incident P0 |
| **PnL live reel** | `scripts/live_pnl_tracker.py` (VPS cron 22:00 UTC) | `python scripts/live_pnl_tracker.py --summary` | "Insufficient history" tolere < 2j |
| **Alpaca go/no-go depot 25K** | `scripts/alpaca_go_25k_gate.py` | `python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5` | Exit 2 NO_GO si absent prereqs |
| **Promotion paper → live** | `scripts/promotion_check.py {strat_id}` | `python scripts/promotion_check.py alt_rel_strength_14_60_7` | Exit 1 si checks fail |
| **Autorisation ordre live** | `core/governance/pre_order_guard.py` (inline, via cycles) | consomme par worker, pas CLI direct | GuardError = REJECT fail-closed |
| **Health book (GREEN/DEGRADED/BLOCKED)** | `core/governance/book_health.py:check_{book}` | via dashboard `/api/governance/books/health` | BLOCKED = book refuse ordres |

### Regle absolue
> **`scripts/runtime_audit.py --strict` sur VPS** est la **source de verite operationnelle** du desk. Si un doc raconte autre chose, c'est le doc qui a tort.

---

## 2. Hierarchie de precedence (cas de contradiction)

### 2.1 Scripts de verite globale vs docs/audit

| Conflit | Gagnant | Raison |
|---|---|---|
| `runtime_audit --strict` VPS dit **BLOCKED** et scorecard dit "moteur live atteint" | **runtime_audit VPS** | Le doc peut etre stale ou drift narratif. Runtime fait foi. |
| `runtime_audit` dit 16 strats et `quant_registry` dit 15 | **runtime_audit** (calcul derive) | Le derive inclut la vue canonique + cross-checks. Incoherence = bug registry. |
| `live_pnl_tracker summary` dit PnL +2% et dashboard dit +3% | **live_pnl_tracker** | CSV + JSONL source, dashboard peut etre en cache. |

### 2.2 Scripts au sein d'une meme decision

| Question | Autorite finale | Scripts secondaires |
|---|---|---|
| "Strat peut-elle trader maintenant ?" | **`pre_order_guard.py`** (inline) | `promotion_check` = prerequis mais pas decision runtime |
| "Quelle est la verite live cumulative ?" | **`live_pnl_tracker.py` (VPS cron)** | `paper_portfolio.py --status` = snapshot momentary |
| "Alpaca depot 25K justifie ?" | **`alpaca_go_25k_gate.py`** | `promotion_check` general = insuffisant (PDT specifique) |
| "Book est-il tradable maintenant ?" | **`book_health.check_{book}()`** (runtime) | `scripts/preflight_check.py` = snapshot boot |
| "Ordre autorise ?" | **`pre_order_guard()`** | Tout le reste est prerequis |
| "Promotion paper → live legal ?" | **`promotion_check.py {strat}`** | scorecards = info seulement |

### 2.3 Local vs VPS (heritage T3-fix2)

| Decision | Gagnant | Justification |
|---|---|---|
| Business / capital / promotion | **VPS** | Production env, state files frais, broker connect |
| Qualite code / tests | **Local** | Pytest local = meme code base, confiance |
| Debug / WF research | **Local** | Flexibilite scripts ad-hoc |

---

## 3. Matrice complete scripts + services + cycles

### 3.1 Scripts de verite operationnelle (autorite decisionnelle)

| Script | Objectif | Inputs | Outputs | Decision pilotee | Frequence | Env | Autorite | Comportement si input ❌ |
|---|---|---|---|---|---|---|---|---|
| `runtime_audit.py --strict` | Verite etat plateforme | 4 YAML + wf_manifests + state files + broker VPS | stdout report + exit code (0 OK / 3 FAIL) | **oui**: decide si plateforme coherente | a la demande + weekly hebdo | Local (info) + **VPS (decision)** | **PRIMAIRE** vs docs | Exit 3 si registries stale/missing |
| `live_pnl_tracker.py` | Verite PnL live | Broker APIs (IBKR + Binance + Alpaca) | `data/live_pnl/daily_equity.csv` + `daily_pnl.jsonl` + `summary.json` | **oui**: mesure performance reelle pour scaling decisions | **Cron VPS daily 22:00 UTC** | VPS uniquement | **PRIMAIRE** PnL | "Insufficient history" tolere ≥ 2j requis |
| `alpaca_go_25k_gate.py --strategy X` | Gate PDT waiver $25K | `quant_registry` + `paper_journal.jsonl` + `wf_manifest` + `data/incidents/*.jsonl` | Exit 0 GO / 1 WATCH / 2 NO_GO + verdict string | **oui**: depot capital $25K | a la demande (hebdo apres 2026-05-18 earliest) | Local + VPS | **PRIMAIRE** Alpaca | Exit 2 NO_GO_paper_journal_missing |
| `promotion_check.py {strat}` | Gate promotion paper → live | `promotion_gate.py` checks (wf_manifest + paper_start + grade + divergence) | stdout checklist + exit 0/1 | **oui**: promotion strat (sauf Alpaca) | a la demande (pre-promotion) | Local + VPS | **PRIMAIRE** promotion | Exit 1 = blocage promotion |
| `preflight_check.py` | Pre-boot validation | registries + state files + broker connectivity | stdout checklist | info boot (non-strict) | a la demande | Local + VPS | SECONDAIRE (`boot_preflight` inline wins) | warnings only |
| `paper_portfolio.py --status` | Snapshot dashboard | state files + broker APIs | stdout table | **non**: info dashboard | a la demande | VPS (pour live snapshot) | INFORMATIONNEL | Partial info si state missing |
| `post_trade_check.py` | Audit post-trade | `data/audit/orders_*.jsonl` + `data/tax/classified_trades.jsonl` | report checklist | info audit | apres chaque trade significatif | Local + VPS | AUDIT ONLY | skip si logs manquants |
| `day1_boot_check.py` | Validation J+1 live | state files + broker | checklist boot | **non**: info initial deploy | une fois | VPS | HISTORIQUE | n/a |
| `pre_deploy_check.py` | Canary pre-deploy | test + lint + registries | exit 0 OK / 1 FAIL | **oui**: deploy autorise | avant chaque deploy VPS | Local | **PRIMAIRE** deploy | Exit 1 = pas de deploy |
| `reconciliation.py` | One-shot reconciliation | state files + broker positions | stdout divergences | info | a la demande (debug) | VPS | SECONDAIRE (`reconciliation_cycle` inline wins) | warn si broker unreachable |

### 3.2 Scripts de refresh data (autorite freshness)

| Script | Objectif | Inputs | Outputs | Decision pilotee | Frequence | Env | Autorite | Comportement si input ❌ |
|---|---|---|---|---|---|---|---|---|
| `refresh_futures_parquet.py` | Refresh MES/MNQ/M2K/MGC/MCL `*_1D.parquet` | yfinance ES=F, NQ=F, RTY=F, GC=F, CL=F | atomic write parquet | **oui**: data freshness preflight | **Cron VPS weekday 21:30 UTC** | VPS | **PRIMAIRE** futures daily | Abort avec warning, keep existing |
| `refresh_mes_1h_yf2y.py` (iter3-fix B6) | Refresh MES_1H_YF2Y 2Y 1h | yfinance ES=F 1h 729j | atomic write parquet | **oui**: data freshness btc_asia paper runner | **Cron VPS weekday 21:35 UTC** | VPS | **PRIMAIRE** MES 1h | Abort, keep existing |
| `collect_crypto_history.py --tier-mode --source spot` | Refresh BTCUSDT + alts candles | Binance spot API | `data/crypto/candles/*.parquet` | **oui**: data freshness alt_rel_strength paper | **Cron VPS daily 00:30 UTC** | VPS | **PRIMAIRE** crypto candles | Abort par symbol, continue autres |
| `backup.sh` + `backup_state.py` | Daily snapshot state critique | `data/state/` + parquets critiques | `data/backups/{date}/` + tar.gz | **non**: recovery manuel | **Cron VPS daily 03:00 UTC** | VPS | **PRIMAIRE** backup | Log error, retry next cron |

### 3.3 Watchdogs / heartbeats (autorite sante systeme)

| Script/Service | Objectif | Inputs | Outputs | Decision pilotee | Frequence | Env | Autorite |
|---|---|---|---|---|---|---|---|
| `check_heartbeat.sh` | Cron watchdog worker vivant | Last update `data/monitoring/heartbeat.json` | alert Telegram si stale | **oui**: restart worker si mort | **Cron VPS */15 min** | VPS | **PRIMAIRE** liveness |
| `ibgateway_watchdog.py` | Monitor IB Gateway auth | TCP 127.0.0.1:4002/4003 | Telegram + restart si down | **oui**: restart gateway | continuous | VPS | **PRIMAIRE** gateway |
| `healthcheck_endpoint.py` | Endpoint externe monitoring | worker state | HTTP 200/503 | **non**: info externe | continuous | VPS (si deploye) | INFORMATIONNEL |

### 3.4 Systemd services VPS (24/7)

| Service | Role | Etat | Autorite |
|---|---|---|---|
| `trading-worker.service` | Scheduler 24/7 cycles | active running | **PRIMAIRE** runtime |
| `trading-dashboard.service` | FastAPI + React SPA | active running | SERVING (UI) |
| `trading-telegram.service` | Bot Telegram commandes | active running | ALERTES + commandes |
| `trading-watchdog.service` | Watchdog systeme | active running | HEALTH |
| `ibgateway.service` | IB Gateway 4002 LIVE | active running | **PRIMAIRE** broker IBKR live |
| `ibgateway-paper.service` | IB Gateway 4003 paper | active running | SECONDAIRE paper tests |

### 3.5 Worker cycles (implicites dans `trading-worker.service`)

| Cycle | Objectif | Frequence | Autorite |
|---|---|---|---|
| `boot_preflight` | Validation boot fail-closed | 1x au boot | **PRIMAIRE** boot gate |
| `run_crypto_cycle` | Trading crypto live | toutes ~30min | EXECUTION crypto |
| `run_futures_live_cycle` | Trading futures IBKR live | weekday 16h Paris | EXECUTION futures |
| `run_futures_paper_cycle` | Paper MES/MCL/MGC | weekday 16h Paris | PAPER futures |
| `run_mib_estx50_spread_paper_cycle` | Paper EU spread | weekday 17h45 Paris | PAPER EU |
| `run_alt_rel_strength_paper_cycle` | Paper crypto alt rel | **daily 03h Paris** | PAPER crypto |
| `run_btc_asia_mes_leadlag_paper_cycle` | Paper BTC Asia (q70 + q80 variants) | daily 10h30 Paris | PAPER crypto |
| `run_eu_relmom_paper_cycle` | Paper EU indices | weekday 18h Paris | PAPER EU |
| `run_us_sector_ls_paper_cycle` | Paper US sectors | weekday 23h30 Paris | PAPER US |
| `run_cross_asset_momentum_cycle` | CAM signal check | weekday 16h15 Paris | SIGNAL CAM |
| `run_bracket_watchdog_cycle` | Verify OCA brackets | toutes 5 min | SAFETY |
| `run_crypto_watchdog_cycle` | Verify crypto SL active | toutes 5 min | SAFETY |
| `run_live_risk_cycle` | DD check + auto-disable | toutes 5-10 min | **PRIMAIRE** live risk |
| `reconciliation_cycle` | Broker vs local | toutes 30 min | **PRIMAIRE** reconciliation |
| `heartbeat_cycle` | Ecrit heartbeat state | toutes 5 min | LIVENESS |

### 3.6 Governance inline modules (appeles depuis cycles, non CLI)

| Module | Role | Autorite |
|---|---|---|
| `core/governance/pre_order_guard.py:pre_order_guard()` | Check 1-6b avant chaque ordre | **PRIMAIRE** autorisation ordre |
| `core/governance/promotion_gate.py:check()` | Paper → live promotion | **PRIMAIRE** promotion (back-end de `promotion_check.py`) |
| `core/governance/book_health.py:check_{book}()` | Health per book | **PRIMAIRE** book status |
| `core/governance/reconciliation_cycle.py` | Broker vs local divergence | **PRIMAIRE** reconciliation |
| `core/kill_switch_live.py:LiveKillSwitch` | Kill scope global + per-strategy | **PRIMAIRE** kill |
| `core/monitoring/incident_report.py:log_incident_auto()` | Timeline incidents | **PRIMAIRE** audit trail |
| `core/runtime/preflight.py:boot_preflight()` | Fail-closed boot | **PRIMAIRE** boot |

---

## 4. DoD — 6 questions user (reponses < 2 min)

### Q1 : Quelle commande donne la verite live actuelle ?

```bash
# SUR VPS (autorite business)
ssh -i ~/.ssh/id_hetzner root@178.104.125.74 "cd /opt/trading-platform && source .venv/bin/activate && PYTHONPATH=. python scripts/runtime_audit.py --strict"
```

Exit code 0 = plateforme coherente. Exit 3 = incoherence detectee.

**En local** (info dev, FAIL attendu) :
```bash
python scripts/runtime_audit.py --strict
```
Exit 3 local = dev gap attendu (state files live absents), **pas un P0**.

### Q2 : Quelle commande donne la verite PnL ?

```bash
# SUR VPS uniquement (VPS cron 22:00 UTC auto-run daily)
ssh vps "cd /opt/trading-platform && python scripts/live_pnl_tracker.py --summary"
```

Source : `data/live_pnl/summary.json` (mise a jour quotidienne).

Status actuel (2026-04-19) : "Insufficient history (need ≥ 2 days)" — baseline 1j.

### Q3 : Quelle commande donne la verite Alpaca go/no-go ?

```bash
python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5
```

Exit 0 = GO_25K (depot recommande). Exit 1 = WATCH. Exit 2 = NO_GO.

Status actuel (2026-04-19) : **Exit 2 NO_GO_paper_journal_missing** (paper 1j).
Earliest theorique GO : 2026-05-18.

### Q4 : Quelle commande donne la verite des books ?

**Full plateforme** : `runtime_audit.py --strict` (section strategies + books).

**Par book specifique** :
```bash
# API dashboard (VPS)
curl http://localhost:8000/api/governance/books/health
# Ou  
curl http://localhost:8000/api/governance/strategies/status
```

**Via Python** :
```python
from core.governance.book_health import check_ibkr_futures
print(check_ibkr_futures())  # GREEN | DEGRADED | BLOCKED + checks details
```

### Q5 : Laquelle est locale seulement ?

| Script | Local only |
|---|---|
| `scripts/runtime_audit.py` | NON (VPS canonique) |
| `scripts/alpaca_go_25k_gate.py` | NON (marche sur VPS aussi) |
| `scripts/promotion_check.py` | NON |
| `scripts/live_pnl_tracker.py` | **OUI en pratique** (besoin broker live creds = VPS only) |
| `scripts/pre_deploy_check.py` | **OUI** (pre-deploy local avant push) |
| `scripts/backup.sh` | **OUI VPS** (cron only) |
| `scripts/refresh_*` | **OUI VPS** (cron only) |

**Regle** : pour toute decision business → VPS. Pour tous les scripts **marked PRIMAIRE** dans la matrice section 3, la reference VPS gagne.

### Q6 : Laquelle est valable pour decider d'allouer du capital ?

**Decision allouer capital** necessite **3 autorites alignees** :

1. **Plateforme coherente** : `runtime_audit.py --strict` VPS exit 0
2. **PnL positif / stable** : `live_pnl_tracker.py --summary` VPS (PnL net >= 0 sur 30j+)
3. **Gate specifique** :
   - Alpaca : `alpaca_go_25k_gate.py` exit 0 GO_25K
   - Promotion strat : `promotion_check.py {strat}` exit 0
   - mib_estx50 funding : decision user (pas de gate automatise)

**Si divergence** : VPS gagne. Aucun "scorecard" ou "audit doc" ne remplace ces 3 commandes.

---

## 5. Commandes lundi matin 2026-04-20 (operationnel)

Ordre recommande (15 min totales) :

```bash
# 1. Verif plateforme VPS exit 0 (30s)
ssh vps "cd /opt/trading-platform && source .venv/bin/activate && PYTHONPATH=. python scripts/runtime_audit.py --strict"

# 2. Verif paper runners weekday fired (1 min) — critique post iter3-fix B6/B9
ssh vps "cd /opt/trading-platform && tail -300 logs/worker/worker.log | grep -iE 'paper_cycle|runner|leadlag'"

# 3. Verif cron refresh MES_1H_YF2Y a run dim soir (10s)
ssh vps "tail -5 /opt/trading-platform/logs/data_refresh/mes_1h_cron.log"

# 4. Alpaca gate re-check (20s)
ssh vps "cd /opt/trading-platform && python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5"

# 5. Live PnL snapshot si >= 2j history (10s)
ssh vps "cd /opt/trading-platform && python scripts/live_pnl_tracker.py --summary"

# 6. Reconciliation rapide (via API dashboard, 5s)
curl -s http://vps-ip:8000/api/governance/strategies/status | head -30

# 7. Verif systemd services health (10s)
ssh vps "systemctl is-active trading-worker trading-dashboard trading-telegram ibgateway.service"
```

**Si une commande echoue** :
- Runtime audit FAIL → regarder output, identifier incoherence, fixer (registry, state file, broker)
- Paper runners silent → data stale check, runner code audit, scheduler verify
- Cron missed → re-run manuel + debug cron env
- Alpaca gate NO_GO toujours paper_journal_missing → re-check post premier lundi cycle

---

## 6. Precedence en cas de conflit doc vs script

Scenario exemple : document `live_readiness_scoreboard.md` dit "Objectif A ATTEINT" mais `runtime_audit` VPS retourne exit 3 BLOCKED ?

**Resolution** :
1. Runtime fait foi → doc a **tort**.
2. Fixer le doc immediatement (process iter3-fix2 deja applique).
3. Investigate pourquoi doc a drift (souvent : copie obsolete, refresh manquant).
4. Commit correction doc avec timestamp as_of explicite.

Scenario exemple : `alpaca_go_25k_gate` dit GO mais `promotion_check` dit FAIL pour la meme strat ?

**Resolution** :
- Les 2 gates sont **independants**. Alpaca gate est capital-specific (PDT $25K). promotion_check est canonical-gate.
- Pour PROMOTION : `promotion_check` gagne (autorite canonique).
- Pour DEPOT $25K : `alpaca_go_25k_gate` gagne (ses criteres sont specifiques PDT).
- Une strat peut etre OK promotion mais pas OK $25K, et inverse impossible (alpaca_go verifie aussi promotion implicitement via paper_days + divergence).

---

## 7. Gaps identifies (backlog)

### 7.1 Scripts absents mais utiles (P2 backlog)

1. **`scripts/weekly_truth_review.py`** : consolidation hebdo runtime_audit + live_pnl + alpaca_gate + promotion_check → genere rapport dim soir. Utile pour discipline hebdo.
2. **`scripts/capital_occupancy_report.py`** : metrique occupancy par strat (H7 T6 topic, backlog T6).
3. **Tests `test_runtime_audit.py`** : le script n'a pas de test unitaire dedie (comme alpaca_gate). **P2**.

### 7.2 Ambiguites d'autorite restantes

- `paper_portfolio.py --status` vs dashboard UI : les 2 montrent similaire. A clarifier si overlap.
- `day1_boot_check.py` vs `pre_deploy_check.py` vs `preflight_check.py` : 3 scripts boot-validation. Role distinct a documenter precisement (non bloquant).

### 7.3 Heritage P1 rappel

- `tests/test_alpaca_go_25k_gate.py` absent (T2 finding)
- `tests/test_state_file_contracts.py` absent (T4 finding)
- 5 modules governance/execution a 0% coverage (T2 finding)

---

## 8. Ligne rouge T5 respectee

- ✅ Matrice d'autorite, pas catalogue de scripts
- ✅ Chaque script : objectif + inputs + outputs + decision + frequence + env + autorite + comportement si echec
- ✅ Precedence explicite (section 2) pour 3 types de conflit : script vs doc, script vs script, local vs VPS
- ✅ Commandes lundi matin operationnelles concretes (section 5)
- ✅ DoD 6 questions user repondues (section 4)
- ✅ "Ce script existe" remplace par "ce script sert a X, il lit Y, il a autorite sur Z, s'il echoue alors W"
- ✅ Gaps identifies (section 7) sans rewrite

**Prochain** : T6 H7 ROC reporting contract. Couplage avec T5 : `live_pnl_tracker.py` etait deja identifie comme PRIMAIRE PnL. T6 ira plus loin sur occupancy + ROC par strat + marginal contribution.
