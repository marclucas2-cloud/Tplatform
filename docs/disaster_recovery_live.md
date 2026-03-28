# Disaster Recovery Plan -- Live Trading

## Scenarios et procedures

### 1. Worker Railway crash
- **Detection** : Healthcheck externe (UptimeRobot), alerte Telegram CRITICAL
- **Impact** : Pas de nouveaux trades, positions protegees par bracket orders
- **Action** : Railway auto-restart. Si non : restart manuel via dashboard.
- **Temps de recovery** : < 5 min

### 2. VPS Hetzner / IB Gateway crash
- **Detection** : Alerte "IBKR DISCONNECTED"
- **Impact** : Worker ne peut pas envoyer d'ordres, bracket orders actifs chez IBKR
- **Action** : SSH vers VPS, restart IB Gateway. Si VPS down : restart panel Hetzner.
- **Temps de recovery** : < 10 min

### 3. Perte de donnees (corruption DB SQLite)
- **Detection** : Erreurs dans les logs, reconciliation echoue
- **Action** : Restaurer depuis le dernier backup
  ```
  ./scripts/restore_live.sh backup/backup_YYYYMMDD.tar.gz
  ```
- **Temps de recovery** : < 30 min
- **Perte max** : 24h de donnees (backup quotidien)

### 4. Erreur de trading (ordre errone)
- **Detection** : Alerte slippage, reconciliation mismatch
- **Action** : /close [ticker] CONFIRM ou /kill CONFIRM si critique
- **Temps de recovery** : < 1 min

### 5. Tout est down (Railway + Hetzner + Telegram)
- **Action** : IBKR Mobile -> fermer manuellement
- **Backup ultime** : Appeler IBKR desk
- **Les bracket orders protegent meme si tout est down**

## Backups
- Quotidien 23h CET -> backup/backup_YYYYMMDD.tar.gz
- Retention : 30 jours
- Restauration testee : < 30 min
- Donnees perdues max : 24h

## Tests de DR (mensuel)
- [ ] Restauration backup testee
- [ ] Kill switch Telegram teste
- [ ] IBKR Mobile fermeture testee
- [ ] Healthcheck externe verifie
