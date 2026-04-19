# Drills quarterly — Trading Platform (R4 residuel post-XXL, 2026-04-19)

3 drills à executer **chaque trimestre** pour valider que les procedures de
recovery fonctionnent en conditions réelles et que les mécanismes ne se sont
pas dégradés silencieusement.

## Cadence recommandee

| Drill | Cadence | Duree | Risque |
|---|---|---|---|
| DR restore | Quarterly | 30 min | Faible (sandbox isolated) |
| Canary rollback | Quarterly OU apres modif deploy.sh | 15 min | Faible (git tag local) |
| Secrets rotation | 90j BINANCE / annuel autre | 1h pour Binance, plus long si leak | Moyen (downtime worker possible) |

## Drill #1 — DR restore

**Goal**: valider que les backups (`scripts/backup_state.py`) sont
restaurables et que les modules critiques se relisent sans erreur.

```bash
# Dry-run (default - inspecte la structure backup, ne restore pas)
bash scripts/drill_dr_restore.sh

# Restoration reelle dans sandbox (NE TOUCHE PAS le repo prod)
APPLY=true bash scripts/drill_dr_restore.sh
```

### Ce que le drill verifie
- Dernier backup `data/backups/<date>/<ts>/` accessible
- `DDBaselines.load_baselines()` retourne `STATE_RESTORED` ou `STATE_STALE` (pas `STATE_CORRUPT`)
- `OrderTracker.recovery_summary()` decompte les orders correctement
- `live_whitelist.yaml` parse sans erreur
- Diff pre vs post restoration (file count match)

### Critères de succès
- Tous les checks integrity PASS
- RTO mesure < 30 min
- 0 file corrompu

### Procedure prod (apres drill OK)
```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74
cd /opt/trading-platform

# 1. Stop worker
systemctl stop trading-worker

# 2. Backup current state
mv data/state data/state.before_restore.$(date +%Y%m%d_%H%M%S)

# 3. Restore
LATEST_BACKUP=$(ls -1t data/backups/*/*/ | head -1)
cp -a "$LATEST_BACKUP/." ./

# 4. Verify
python3 -c "from core.crypto.dd_baseline_state import load_baselines; from pathlib import Path; print(load_baselines(Path('data/crypto_dd_state.json')))"

# 5. Restart
systemctl start trading-worker
journalctl -u trading-worker -f

# 6. Telegram doit voir 'OrderTracker recovered N orders'
```

---

## Drill #2 — Canary rollback

**Goal**: valider que `deploy.sh --rollback` fonctionne et que le code
post-rollback est runnable.

```bash
# Dry-run (default - check structure deploy.sh, pas de checkout)
bash scripts/drill_canary_rollback.sh

# Reel git checkout test (revenu automatique au commit original via trap)
APPLY=true bash scripts/drill_canary_rollback.sh
```

### Ce que le drill verifie
- `deploy.sh` a bien l'option `--rollback`
- Tag git rollback peut etre cree + checkout
- `python -c "import worker"` PASS apres checkout
- Smoke test critical (test_dd_baseline_persistence) PASS
- Trap auto-restore le commit original au cleanup

### Critères de succès
- `bash drill_canary_rollback.sh` exit 0
- `APPLY=true` mode: worker imports OK + tests PASS
- Repo restore au commit original (git rev-parse HEAD identique)

### Procedure prod (rollback reel suite incident)
```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74
cd /opt/trading-platform

# Liste tags rollback dispo (les plus recents en haut)
git tag -l "rollback-*" --sort=-version:refname | head -5

# Rollback automatique au dernier tag rollback-*
./scripts/deploy.sh --rollback

# OR rollback explicite tag specifique
./scripts/deploy.sh --rollback rollback-20260419-100530
```

---

## Drill #3 — Secrets rotation

**Goal**: verifier que rien n'a leak dans git ET planifier les rotations
manuelles (Binance 90j, autres annuel).

```bash
bash scripts/drill_secrets_rotation.sh
```

### Ce que le drill verifie
- Inventory `.env` (variables names only, no values)
- 7 patterns `.gitignore` protection (`.env`, `*.key`, `*_token*`, etc.)
- Scan tracked files pour patterns secrets hardcoded (BINANCE_API_KEY=..., password=...)
- Scan git history pour `.env` ever committed
- Rotation log `data/governance/secrets_rotation.log`

### Apres drill: actions manuelles
Cf checklist affichee par le script:
1. **Binance** (90j) : create new API key + IP whitelist + restart worker + revoke old
2. **Telegram bot** (180j ou si leak) : BotFather /token + update .env
3. **IBKR password** (annuel) : Client Portal + restart ibgateway
4. **SSH key Hetzner** (annuel) : ssh-keygen + add to authorized_keys + remove old
5. **Alpaca paper** (annuel) : dashboard regenerate

Apres chaque rotation:
```bash
echo "$(date -u +%FT%TZ) BINANCE rotated (signer marc)" >> data/governance/secrets_rotation.log
git add data/governance/secrets_rotation.log
git commit -m "chore(secrets): rotate BINANCE 2026-04-19"
```

---

## Calendar reminder

Suggere d'ajouter ces drills dans calendar:

```
- 2026-07-19 (3mo): DR restore + canary rollback drills
- 2026-07-19 (3mo): BINANCE secrets rotation
- 2026-10-19 (6mo): DR restore + canary rollback drills
- 2027-01-19 (9mo): DR restore + canary rollback drills + IBKR password rotation
- 2027-04-19 (12mo): All drills + Telegram bot + Alpaca + SSH key rotation
```

## Score post-R4

| Drill | Script livre | Test local | Doc | Procedure prod |
|---|---|---|---|---|
| DR restore | OK | OK (dry-run + APPLY) | OK | OK |
| Canary rollback | OK | OK (auto-restore via trap) | OK | OK |
| Secrets rotation | OK | OK (scan only, rotation manual) | OK | OK |

**Action operateur**: planifier le 1er trimestre de drills (e.g. **2026-07-19** premier drill DR + canary).
