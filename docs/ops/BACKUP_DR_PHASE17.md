# Backup + DR Strategy (Phase 17 XXL plan, 2026-04-19)

## Backup local — `scripts/backup_state.py`

Backup automatique des fichiers critiques + rotation 7 jours.

### Files snapshotted (post-Phase 17 update)

**Equity & DD state**:
- data/state/{book}/equity_state.json
- data/state/{book}/dd_state.json
- data/crypto_dd_state.json (Phase 1 XXL DDBaselines schema v1)
- data/state/global/live_risk_dd_state.json

**Positions**:
- data/state/{book}/positions_*.json
- data/state/futures_positions_{live,paper}.json
- data/state/paper_*_state.json

**Order tracking**:
- data/state/order_tracker.json (Phase 3 XXL crash recovery)

**Kill switches**:
- data/kill_switch_state.json
- data/crypto_kill_switch_state.json
- data/state/global/kill_switch_state.json
- data/state/kill_switches/ (dir)

**Governance**:
- config/live_whitelist.yaml
- config/books_registry.yaml + strategies_registry.yaml + risk_registry.yaml
- config/health_registry.yaml
- config/{allocation,crypto_allocation,limits_live,crypto_limits}.yaml
- data/governance/greenlights/ (Phase 7 XXL signed tokens)

**Audit**:
- data/audit/ (Phase 5 XXL audit_trail JSONL per day)
- data/reconciliation/ (Phase 6 XXL daily reports)

**Risk**:
- data/risk/{monte_carlo_report,unified_portfolio,last_known_broker_state}.json

### Rotation
- 7 dernier daily backups conserves
- 1 backup par execution (timestamp UTC), classes par date_str/ts/

### Cron VPS recommande
```cron
# Daily backup 04:00 UTC
0 4 * * * cd /opt/trading-platform && /usr/bin/python3 scripts/backup_state.py >> /var/log/backup_state.log 2>&1
```

## Restoration

### Workflow standard
```bash
# 1. Lister backups disponibles
python scripts/backup_state.py --list

# 2. Inspect backup avant restore
ls data/backups/<date>/<ts>/

# 3. STOP worker (eviter writes concurrents)
ssh root@VPS "systemctl stop trading-worker"

# 4. Backup current state (au cas ou)
mv data/state data/state.before_restore.$(date +%Y%m%d_%H%M%S)

# 5. Restore
cp -a data/backups/<date>/<ts>/data/state ./data/state
cp -a data/backups/<date>/<ts>/data/audit ./data/audit
# ... autres dirs au besoin

# 6. Verify integrity
python -c "from core.crypto.dd_baseline_state import load_baselines; from pathlib import Path; print(load_baselines(Path('data/crypto_dd_state.json')))"

# 7. Restart worker
ssh root@VPS "systemctl start trading-worker"
journalctl -u trading-worker -f
```

## DR Strategy

### Single-VPS limitation
Pas de VPS secondaire actuellement. Risque single point of failure :
- Hetzner data center incident -> service down N heures
- Disk corruption -> perte state

### Mitigations
1. **Backup remote**: rsync quotidien vers cloud storage (recommande):
   ```cron
   30 4 * * * rsync -az /opt/trading-platform/data/backups/ user@remote:/backups/trading-platform/
   ```
2. **Git push state non-secrets**: configs + audit JSONL deja commits via worker.
3. **DDBaselines schema v1 atomic write** -> jamais de partial state, recovery
   propre meme apres crash kernel.

### RTO / RPO actuels
- RPO (donnees perdues): max 24h (backup daily)
- RTO (temps recovery): ~30 min (restore + restart + reconcile)

### Plan RPO < 1h (futur)
- Cron backup horaire (au lieu de daily)
- Sync incremental rsync vers S3 / cloud apres chaque backup
- Cf Phase 18 deploy canary pour limiter incidents code-induced

## Score post-Phase 17

- Backup script: **9/10** (fichiers critiques inclus apres add Phase 1/3/5/7)
- Rotation policy: **9/10** (7 daily + horodate)
- Restoration playbook: **9/10** (workflow documente)
- Remote backup: **5/10** (pas de S3/secondaire actuellement, recommande)
- DR drill teste: **3/10** (pas de drill effectue, recommande quarterly)
