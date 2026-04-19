# Ops Hygiene Checklist — H9 T9

**As of** : 2026-04-19T16:45Z
**Phase** : H9 TODO XXL hygiene. Exploitation reelle desk $20K, pas audit securite academique.
**Livrable** : ce document. Checklist + commandes + legende OK/WARN/FIX SOON/BLOCKER.
**Usage** : lundi matin, pendant incident, apres reboot, avant allocation capital.

---

## 0. Principe directeur T9

> Cette checklist est un **outil operationnel desk perso solo $20K**, pas un audit securite ISO 27001.
>
> Chaque point doit **vraiment aider** : verifier si le desk tourne, s'il peut trader, s'il y a incident, s'il y a risque fuite / mauvaise manip.
>
> La securite est **proportionnee** : perms 600 sur .env = justifie ; audit supply chain = overkill a $20K.

**Anti-principe** : "checklist cosmetique corporate". Si un point ne pilote aucune action operationnelle, il est retire.

---

## 1. Legende severite

| Label | Signification | Action typique |
|---|---|---|
| **OK** | Conforme, rien a faire | continue |
| **WARNING** | A surveiller dans les 7 jours | note + re-check |
| **FIX SOON** | Correction requise sous 48h | ticket + fix prochain deploy |
| **BLOCKER** | Bloque le live maintenant, action immediate | stop nouvelles allocations + fix |

---

## 2. Routine 5 minutes — lundi matin (ou post-reboot)

### 2.1 Le desk tourne-t-il ?

```bash
# 1. Services systemd actifs (10s)
ssh -i ~/.ssh/id_hetzner root@178.104.125.74 "systemctl is-active trading-worker trading-dashboard trading-telegram ibgateway.service ibgateway-paper.service trading-watchdog.service"
```

**Attendu** : 6x `active` sur une ligne chacun.
**Si 1+ inactive** → BLOCKER (restart service ou investiguer log).

### 2.2 Le worker heartbeat est-il frais ?

```bash
ssh vps "stat -c '%y' /opt/trading-platform/data/monitoring/heartbeat.json; cat /opt/trading-platform/data/monitoring/heartbeat.json"
```

**Attendu** : timestamp < 5 min vs `date -u`.
**Stale > 15 min** → **BLOCKER** (check_heartbeat.sh cron alerte). Worker est fige.

### 2.3 La plateforme est-elle coherente ?

```bash
ssh vps "cd /opt/trading-platform && source .venv/bin/activate && PYTHONPATH=. python scripts/runtime_audit.py --strict" ; echo "exit=$?"
```

**Attendu** : exit 0, `No registry/runtime incoherences detected`.
**exit 3** → **FIX SOON** (identifier incoherence, fixer registry ou state).

### 2.4 Un book est-il BLOCKED ?

```bash
curl -s http://178.104.125.74:8000/api/governance/strategies/status | python -m json.tool | head -50
```

**Attendu** : dashboard dispo, 5 books listees (alpaca_us / binance_crypto / ibkr_eu / ibkr_futures / ibkr_fx).
**Book BLOCKED** → identifier quelle critical_check fail via `book_health.py`.

### 2.5 Kill switch actif ?

```bash
ssh vps "cat /opt/trading-platform/data/kill_switch_state.json /opt/trading-platform/data/crypto_kill_switch_state.json 2>/dev/null | grep -iE 'active|triggered'"
```

**Attendu** : `active: false` (ou fichier absent = never activated).
**active: true** → **BLOCKER** (investiguer raison, ne pas reactiver aveuglement).

### 2.6 Paper runners ont-ils fire (post weekday trigger) ?

```bash
ssh vps "tail -300 /opt/trading-platform/logs/worker/worker.log | grep -iE 'paper_cycle|alt_rel_strength|leadlag|mib_estx50|eu_relmom|us_sector_ls'"
```

**Attendu** (lundi post 23h Paris) : traces de runs weekday.
**Silence** → **FIX SOON** (investigate scheduler ou data stale).

### 2.7 Incidents P0/P1 ouverts ?

```bash
ssh vps "ls -la /opt/trading-platform/data/incidents/; tail -20 /opt/trading-platform/data/incidents/*.jsonl 2>/dev/null | grep -iE 'critical|P0|P1'"
```

**Attendu** : 0 incident critical/P0/P1 dans les 24h.
**>0 incident** → investiguer context, fixer avant nouvelle allocation.

---

## 3. Secrets / .env / credentials

### 3.1 `.env` VPS permissions

**Check** :
```bash
ssh vps "stat -c '%a %U:%G %n' /opt/trading-platform/.env"
```

**Attendu idealement** : `600 root:root`.
**Actuellement observe (2026-04-19)** : `644 root:root` → **FIX SOON**.

**Fix** :
```bash
ssh vps "chmod 600 /opt/trading-platform/.env"
```

**Justification desk $20K** : VPS est root-only, risque single-user. `644` fonctionne (worker lit via EnvironmentFile systemd), mais `600` = principe du moindre privilege.

### 3.2 `.env` jamais committe

**Check** :
```bash
git ls-files | grep -E '\.env$|\.env\..*'
```

**Attendu** : 0 resultat (hors `.env.example` si present).
**Match** → **BLOCKER** (`git rm --cached .env` + rotate keys immediatement).

**Actuellement** : 0 match. ✅ OK.

### 3.3 Variables critiques presentes

```bash
ssh vps "grep -E '^[A-Z_]+=' /opt/trading-platform/.env | cut -d'=' -f1 | sort"
```

**Attendues au minimum** :
- `ALPACA_API_KEY` + `ALPACA_SECRET_KEY`
- `BINANCE_API_KEY` + `BINANCE_API_SECRET`
- `BINANCE_LIVE_CONFIRMED` (= "true" si live)
- `IBKR_HOST` + `IBKR_PORT`
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- `PAPER_TRADING` (= "true" par defaut)

**Manque une variable critique** → **BLOCKER** (worker ne pourra pas auth broker).

### 3.4 Secrets jamais logges

**Check sommaire** :
```bash
ssh vps "grep -iE 'API_KEY|API_SECRET|BOT_TOKEN|PASSWORD' /opt/trading-platform/logs/worker/worker.log | head -5"
```

**Attendu** : 0 resultat OR seulement "API_KEY redacted" / "loaded from .env".
**Clair dans log** → **BLOCKER** (rotate + patch code de logging).

### 3.5 Rotation keys (drill periodique)

`scripts/drill_secrets_rotation.sh` et `scripts/rotate_binance_keys.sh` existent. **Usage recommande** : rotation manuelle **annuelle** ou apres incident.

**Pas urgent $20K** (pas PCI / SOC2). Mais a documenter.

---

## 4. Logs

### 4.1 Volume / rotation

**Check** :
```bash
ssh vps "du -sh /opt/trading-platform/logs/; ls -la /opt/trading-platform/logs/worker/ | head -10"
```

**Observations 2026-04-19** :
- `logs/` = 103M (acceptable $20K desk)
- `worker.log` 10M rotatif (via RotatingFileHandler, maxBytes=10MB, backupCount=5)
- `worker_systemd.log` 500KB
- `worker_stdout.log` 53MB (historique, non-rotative — **FIX SOON**)

**Fix optionnel** : rotation logrotate pour `worker_stdout.log` :
```bash
# /etc/logrotate.d/trading-worker
/opt/trading-platform/logs/worker/worker_stdout.log {
    weekly
    rotate 4
    compress
    missingok
    copytruncate
}
```

### 4.2 Logging double-binding (heritage iter3-fix B7)

**Fixe** 2026-04-19T16:12Z (commit 6f4e77a iter3-fix B7).

**Verification** :
```bash
ssh vps "tail -20 /opt/trading-platform/logs/worker/worker.log | awk '{print \$0}' | sort | uniq -c | sort -rn | head -5"
```

**Attendu** : count 1 pour toutes les lignes. Si count 2+ sur les lignes recentes → regression.
**Regression** → **FIX SOON** (revenir sur idempotency check RotatingFileHandler).

### 4.3 Sensitive data in logs

**Check periodique** :
```bash
ssh vps "grep -iE 'account_number|U25023333|api.*key|secret|password|token' /opt/trading-platform/logs/worker/worker.log | head -5"
```

**Attendu** : seulement `U25023333` (account number IBKR, dans log standard ib_insync — **acceptable**, pas un secret).
**Full API key leaked** → **BLOCKER** rotate + fix code.

---

## 5. Chemins runtime sensibles

### 5.1 Permissions state files critiques

**Check** :
```bash
ssh vps "stat -c '%a %U:%G %n' /opt/trading-platform/data/state/ibkr_futures/equity_state.json /opt/trading-platform/data/state/binance_crypto/equity_state.json /opt/trading-platform/data/kill_switch_state.json /opt/trading-platform/data/crypto_dd_state.json /opt/trading-platform/data/live_risk_dd_state.json"
```

**Actuellement observe** : tous `644 root:root`.
**Severite $20K** :
- **OK tolere** : 644 fonctionne, VPS root-only = pas de lecture par autre user.
- **Ideal 600** : moins de surface attaque si compromise d'un autre service.

**Action T9** : documenter comme **WARNING** (non bloquant, mais a aligner).

### 5.2 Chemins symboliques dangereux

**Check** :
```bash
ssh vps "find /opt/trading-platform/data/state/ -type l 2>&1"
```

**Attendu** : 0 symlinks (vraies files).
**Symlink decouvert** → investiguer (potentiellement malicieux ou mauvaise migration).

### 5.3 Chemins legacy vs actuels

Heritage T4 gap matrix (section 6) :
- `books_registry.yaml notes` mentionne `binance_crypto/positions.json` et `binance_crypto/dd_state.json` → **obsoletes** (realite : `data/crypto_dd_state.json` root).

**Severite** : **WARNING** P3 (pas bloquant, clarifie docs).

### 5.4 Backups localisation

```bash
ssh vps "ls -la /opt/trading-platform/data/backups/ 2>&1 | head -5"
```

**Attendu** : au moins 1 backup recent (cron daily 03:00 UTC).
**Absent** → **FIX SOON** (cron fail ? disk full ?).

**Note** : pas de backups off-site (user directive, acceptable $20K).

---

## 6. Incidents — creation, rotation, lisibilite, usage

### 6.1 Creation automatique

**Source** : `core/monitoring/incident_report.py:log_incident_auto()` cree `data/incidents/YYYY-MM-DD.jsonl`.

**Schema** :
```json
{"timestamp":"...", "category":"...", "severity":"critical|P0|P1|warning", "source":"...", "message":"...", "context":{}}
```

### 6.2 Rotation

**Naturelle** : 1 fichier par jour (date UTC). Pas de rotation explicite.

**Observation volume** :
```bash
ssh vps "wc -l /opt/trading-platform/data/incidents/*.jsonl"
```

**Actuellement 2026-04-19** : 8 lignes (8 incidents warning SPY alpaca paper). Acceptable.

**Si > 1000 lignes/jour** → **FIX SOON** (identifier producteur spammy).

### 6.3 Lisibilite

**Test manuel** :
```bash
ssh vps "tail -3 /opt/trading-platform/data/incidents/*.jsonl | python -m json.tool"
```

**Attendu** : JSON valide, pas de lignes corrompues.

### 6.4 Usage reel

Incidents sont consommes par :
- `alpaca_go_25k_gate.py` (filter `incidents_open_p0p1`)
- Post-mortem manuel (absent, cf gap section 10)

**Important** : pour audit trail partage, **ne pas versionner** les JSONL (T1b gitignore + data/incidents/README.md explique : utiliser `docs/audit/post_mortems/` pour synthese humaine).

---

## 7. Fichiers d'etat sensibles — matrice permissions

Heritage T4 state_file_contracts.

| Fichier | Permissions observees | Perms ideales | Severite $20K | Action |
|---|---|---|---|---|
| `data/state/ibkr_futures/equity_state.json` | 644 | 600 | WARNING | FIX SOON (chmod 600) |
| `data/state/binance_crypto/equity_state.json` | 644 | 600 | WARNING | FIX SOON |
| `data/state/alpaca_us/equity_state.json` | 644 | 600 | WARNING | FIX SOON |
| `data/state/ibkr_futures/positions_live.json` | 644 | 600 | WARNING | FIX SOON |
| `data/kill_switch_state.json` | 644 | 600 | WARNING | FIX SOON |
| `data/crypto_kill_switch_state.json` | 644 | 600 | WARNING | FIX SOON |
| `data/crypto_dd_state.json` | 644 | 600 | WARNING | FIX SOON |
| `data/live_risk_dd_state.json` | 644 | 600 | WARNING | FIX SOON |
| `data/incidents/*.jsonl` | 644 | 644 | OK | n/a (lecture externe potentielle) |
| `data/audit/*.jsonl` | 644 | 644 | OK | n/a |
| `.env` | 644 | **600** | **FIX SOON** | **prioritaire** |

**Fix groupe** (recommande une seule fois) :
```bash
ssh vps "cd /opt/trading-platform && chmod 600 .env data/state/ibkr_futures/equity_state.json data/state/binance_crypto/equity_state.json data/state/alpaca_us/equity_state.json data/state/ibkr_futures/positions_live.json data/kill_switch_state.json data/crypto_kill_switch_state.json data/crypto_dd_state.json data/live_risk_dd_state.json"
```

**Attention** : worker.py doit ecrire ces fichiers → permissions doivent rester writable par `root` (user systemd). `600 root:root` = OK car le worker run en root (cf `User=root` dans trading-worker.service).

---

## 8. Commandes exploitation minimales (runbook)

### 8.1 Verifier si le desk tourne

```bash
# one-liner
ssh vps "systemctl is-active trading-worker && cat data/monitoring/heartbeat.json | python -c 'import json,sys,datetime; h=json.load(sys.stdin); t=datetime.datetime.fromisoformat(h[\"timestamp\"].replace(\"Z\",\"+00:00\")); age=(datetime.datetime.now(datetime.timezone.utc)-t).total_seconds(); print(f\"heartbeat age={age:.0f}s (OK if <300)\")'"
```

### 8.2 Verifier si un book est bloque

```bash
# via API dashboard
curl -s http://178.104.125.74:8000/api/governance/strategies/status | python -c "import json,sys; d=json.load(sys.stdin); [print(f'{b}: {info}') for b,info in d.get('books',{}).items()]"
```

### 8.3 Verifier si les runners ont tire

```bash
ssh vps "grep -iE '$(date -u +%Y-%m-%d).*paper_cycle' /opt/trading-platform/logs/worker/worker.log | tail -10"
```

### 8.4 Verifier si kill switch actif

```bash
ssh vps "cat /opt/trading-platform/data/kill_switch_state.json /opt/trading-platform/data/crypto_kill_switch_state.json 2>/dev/null | python -c 'import json,sys; [print(f\"active={d.get(\\\"active\\\",False)} reason={d.get(\\\"reason\\\",\\\"none\\\")}\") for d in [json.loads(l) for l in sys.stdin.read().split(\"}{\")] if isinstance(d,dict)]' 2>/dev/null || echo 'kill_switch: inactive'"
```

### 8.5 Drift local vs VPS

```bash
# check last git commit both sides
echo "LOCAL:"; git log -1 --oneline
echo "VPS:"; ssh vps "cd /opt/trading-platform && git log -1 --oneline"
```

**Attendu** : meme hash. **Si drift** → `git pull` sur VPS ou identifier commits non pushes local.

### 8.6 Activer/desactiver une strat manuellement

```bash
# Via Telegram /disable {strat_id} ou via CLI :
ssh vps "cd /opt/trading-platform && .venv/bin/python -c 'from core.kill_switch_live import LiveKillSwitch; ks=LiveKillSwitch(); ks.disable_strategy(\"btc_asia_mes_leadlag_q80_v80_long_only\", reason=\"user investigation\"); print(ks.is_strategy_disabled(\"btc_asia_mes_leadlag_q80_v80_long_only\"))'"
```

---

## 9. Scenarios d'usage

### 9.1 Lundi matin (5 min)

Checklist section 2 (7 verifs 5 min).
Plus : section T5 runtime_hygiene_matrix section 5 commandes lundi matin.

### 9.2 Pendant un incident

1. **Systemd services actifs** ? (section 2.1)
2. **Heartbeat frais** ? (section 2.2)
3. **Kill switch actif** ? (section 2.5)
4. **Incidents recents JSONL** (section 2.7 + 6.4)
5. **Logs dernieres 10 min** :
   ```bash
   ssh vps "tail -500 /opt/trading-platform/logs/worker/worker.log | grep -iE 'error|critical|P0|P1'"
   ```
6. **Broker API connected** ?
   ```bash
   ssh vps "grep -E 'API connection (ready|broken)' /opt/trading-platform/logs/worker/worker.log | tail -5"
   ```

### 9.3 Apres un redemarrage (VPS reboot ou trading-worker restart)

1. **Services actifs** apres 30s (section 2.1)
2. **Boot preflight OK** :
   ```bash
   ssh vps "grep -iE 'boot preflight' /opt/trading-platform/logs/worker/worker.log | tail -5"
   ```
3. **Runtime audit exit 0** (section 2.3)
4. **Positions reconciliation** :
   ```bash
   ssh vps "grep -iE 'RECONCILED_AT_BOOT|reconcile_positions' /opt/trading-platform/logs/worker/worker.log | tail -5"
   ```

### 9.4 Avant d'allouer plus de capital

1. **Runtime audit exit 0** (section 2.3)
2. **Live PnL tracker summary** :
   ```bash
   ssh vps "cd /opt/trading-platform && source .venv/bin/activate && python scripts/live_pnl_tracker.py --summary"
   ```
3. **Alpaca gate (si applicable)** :
   ```bash
   ssh vps "cd /opt/trading-platform && source .venv/bin/activate && python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5"; echo "exit=$?"
   ```
4. **Promotion check strat concernee** :
   ```bash
   ssh vps "cd /opt/trading-platform && source .venv/bin/activate && python scripts/promotion_check.py {strategy_id}"; echo "exit=$?"
   ```
5. **0 incident P0/P1 ouvert** (section 6.4)
6. **Check occupancy actuel** (gap script `capital_occupancy_report.py` absent, cf T6 backlog)

---

## 10. Etat audit 2026-04-19 (application de la checklist)

| Point | Etat observe | Severite |
|---|---|---|
| Systemd services actifs | 6/6 active | **OK** |
| Heartbeat frais | 2026-04-19T16:04Z (< 5min lors check) | **OK** |
| Runtime audit VPS | exit 0, 0 incoherence | **OK** |
| Books BLOCKED | 0 (tous OK dashboard) | **OK** |
| Kill switch actif | inactive (global + crypto) | **OK** |
| Paper runners (dimanche) | alt_rel_strength seul (normal weekend) | **OK** (a re-check lundi) |
| Incidents P0/P1 ouverts | 0 critical, 8 warnings (SPY alpaca) | **OK** |
| `.env` perms | **644** (devrait 600) | **FIX SOON** |
| `.env` committe | non | **OK** |
| Variables critiques presentes | a verifier | non verifie ce audit |
| Secrets dans logs | 0 observed | **OK** |
| Volume logs | 103M total | **OK** (non bloquant) |
| Logging double binding | fixe iter3-fix B7 | **OK** |
| State files perms | 644 x 9 fichiers | **FIX SOON** (chmod 600) |
| `worker_stdout.log` rotation | absent, 53MB | **FIX SOON** (logrotate) |
| Backups recents | cron daily 03:00 UTC | **OK** supposé |
| books_registry notes paths obsoletes | binance_crypto/positions.json + dd_state.json | **WARNING** P3 |

**Verdict global T9 application** : plateforme **operationnellement saine**. Ameliorations FIX SOON (non-bloquantes) :
- `.env` + state files perms 644 → 600 (1 commande chmod)
- `worker_stdout.log` logrotate config (5 min)

---

## 11. Gaps ops identifies (backlog)

### 11.1 Scripts absents (heritage T5/T6)

- `scripts/weekly_truth_review.py` : consolidation hebdo des commandes section 2 → rapport dim soir
- `scripts/post_mortem_template.py` : template initial pour `docs/audit/post_mortems/YYYY-MM-DD_*.md`

### 11.2 Automations non implementees

- Alert si `.env` perms drift post-deploy
- Alert si log file > 1GB
- Alert si heartbeat > 5 min stale (actuellement `check_heartbeat.sh */15min`, peut manquer alert rapide)
- Dashboard widget "ops health" consolidant section 2 (OK/WARN/FIX/BLOCKER par point)

### 11.3 Proces manuel

- Rotation keys annuelle (script existe, pas de rappel automatique)
- Audit permissions trimestriel
- Review post-mortem mensuel

**Tous P3** desk $20K. Pas urgents.

---

## 12. DoD — 4 questions user (5 min verifiable)

### Q1 : Est-ce que le systeme tourne ?

Section 2.1 + 2.2. **Si 6 systemd active + heartbeat < 5 min** = OUI.

### Q2 : Peut-il trader ?

Section 2.3 + 2.4 + 2.5. **Si runtime_audit exit 0 + 0 book BLOCKED + kill switch inactive** = OUI.

### Q3 : Y a-t-il un incident ?

Section 2.7 + 6.4. **Si incidents/*.jsonl = 0 critical/P0/P1 depuis 24h** = NON.

### Q4 : Y a-t-il un risque de fuite ou mauvaise manip ?

Section 3 + 7. Actuellement :
- **Perms 644** sur `.env` et state files → **FIX SOON** mais pas fuite active
- **0 secret logge** → OK
- **Pas de git secrets committe** → OK
- **Pas de drift local vs VPS** → a re-verifier (section 8.5)

**Risque actuel** : **bas** sur desk $20K solo-user VPS. Ameliorations documentees.

---

## 13. Ligne rouge T9 respectee

- ✅ Checklist exploitation reelle, pas ISO 27001 cosplay
- ✅ Chaque point pilote une action (OK / WARN / FIX SOON / BLOCKER)
- ✅ 5 min routine lundi matin + scenarios incident/reboot/pre-allocation
- ✅ Securite proportionnee $20K (pas overkill)
- ✅ Commandes concretes operationnelles section 8
- ✅ Etat audit current (section 10) applicable immediatement
- ✅ DoD 4 questions user repondues (section 12)

**Prochain** : T10 H10 desk_operating_truth synthese finale. 1-page operateur consolidant T1-T9.
