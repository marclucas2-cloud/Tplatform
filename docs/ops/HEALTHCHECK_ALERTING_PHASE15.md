# Healthcheck + Alerting (Phase 15 XXL plan, 2026-04-19)

## State actuel

### Heartbeat
- `core/worker/heartbeat.py:log_heartbeat()` : log local + RAM check + Alpaca account
- `core/worker/heartbeat.py:telegram_heartbeat_full()` : enriched multi-broker (Alpaca + Binance + IBKR + RAM) toutes les ~30 min
- `data/monitoring/heartbeat.json` : updated each tick (timestamp + pid)

### Alerting (level-differentiated)
- `core/worker/alerts.py:send_alert(msg, level)` :
  - `critical` -> tg.critical() instant, jamais throttled
  - `warning`  -> tg.warning() 5min throttle par type
  - `info`     -> tg.info() bufferisee dans digest
  - Fallback : `core/telegram_alert.send_alert()` legacy
- `record_signal_fill()` : metriques signal->fill avec auto-alerte si ratio drops

### Reconciliation startup
- `reconcile_positions_at_startup()` : compare positions Alpaca vs state, log
  orphans + missing, send_alert si discrepance

### Dead man's switch
- Pas de cron explicite "alert si silence > 30 min" actuellement.
- Heartbeat write -> external watcher devrait alerter, mais pas de watcher actif.

## Score post-Phase 15

- Heartbeat content: **9/10** (RAM, multi-broker, IBKR socket check, PID)
- Level-differentiated alerting: **9/10** (critical instant, warning throttled,
  info digest, V2 + legacy fallback)
- Startup reconciliation: **8/10** (Alpaca only, manque Binance + IBKR)
- Dead man's switch external: **5/10** (heartbeat.json existe mais pas de
  watcher cron qui alerte si stale > 30 min)

## Recommandation Phase 18 (deploy + watcher)

Ajouter sur Hetzner:
```cron
*/15 * * * * /usr/local/bin/check_heartbeat.sh
```
Script qui:
- mtime data/monitoring/heartbeat.json > 30 min ago -> POST Telegram alert
- Permet de detecter worker bloque/zombie sans wait que l'operateur regarde
