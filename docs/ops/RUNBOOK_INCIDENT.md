# RUNBOOK Incident — Trading Platform (2026-04-19)

Operateur unique: Marc. Telegram bot pour alerting. VPS Hetzner Frankfurt.

## Severite & SLA

| Niveau    | Description                                | SLA reaction |
|-----------|--------------------------------------------|--------------|
| P0 critique | Kill switch trip / broker down / data corruption | < 5 min      |
| P1 high   | Divergence reconciliation / data stale     | < 30 min     |
| P2 medium | Cycle latency / RAM warning                | < 2h         |
| P3 info   | Daily summary / heartbeat normal           | next session |

---

## P0 — Worker / heartbeat down

**Symptomes**: pas d'alerte Telegram depuis > 30 min, dashboard frozen,
`check_heartbeat.sh` cron tire.

**Triage** (5 min) :
```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74
systemctl status trading-worker
journalctl -u trading-worker -n 200 --no-pager
ls -la /opt/trading-platform/data/monitoring/heartbeat.json
```

**Actions** :
1. Si systemctl `failed` -> `systemctl restart trading-worker` + check journalctl
2. Si zombie (active mais heartbeat stale) -> `kill -SIGTERM <pid>` puis restart
3. Si crash repetitif -> `git log --oneline -10` + `git checkout <last-known-good>` puis restart
4. Verifier OrderTracker recovery: log doit montrer "OrderTracker recovered N orders"
5. Verifier kill switch state non-stale apres reboot (cf Phase 1: BootState)

---

## P0 — Kill switch ACTIVE

**Symptomes**: `CRYPTO KILL SWITCH ACTIVATED` ou `IBKR KILL SWITCH ACTIVATED`
sur Telegram. Aucun nouveau trade.

**Triage** (5 min) :
- Quel trigger ? (`daily_loss`, `drawdown`, `margin_critical`, `borrow_spike`)
- DD reel ou faux positif (cf bug dc16858 historique : peak/current denominator) ?

**Actions** :
1. `cat data/crypto_kill_switch_state.json` pour lire trigger_reason + trigger_time
2. Si > 24h -> auto-reset deja code dans worker.py:4490 (`_ks_age_h > 24`)
3. Si < 24h et faux positif (sanity rebaseline DD vs current_equity) :
   - Examiner DD baselines persistees: `cat data/crypto_dd_state.json`
   - Si peak vs current ratio > 3x = sanity rebase auto (cf Phase 1)
   - Sinon: `python -c "from core.crypto.risk_manager_crypto import CryptoKillSwitch; ks=CryptoKillSwitch(); ks._active=False; ks._save_persisted_state()"`
4. Si vrai DD -> NE PAS reset. Investiguer causes (audit_trail, journal trades),
   reduire sizing avant relance.

---

## P0 — Broker DOWN (Binance / IBKR / Alpaca)

**Symptomes**: `RECONCILIATION [<book>] broker query failed` ou cycles qui
loggent `unable to connect`.

**Triage** :
```bash
# IBKR Gateway
nc -zv 178.104.125.74 4002
ssh root@178.104.125.74 "systemctl status ibgateway"

# Binance
curl -s https://api.binance.com/api/v3/ping

# Alpaca
curl -s https://paper-api.alpaca.markets/v2/clock
```

**Actions IBKR Gateway down** (frequent) :
1. SSH VPS puis `systemctl restart ibgateway`
2. Si 2FA bloque -> ouvrir VNC (cf reference_vps_credentials)
3. Trade automatique reprend dans 1-2 cycles. Verifier `_run_futures_cycle` log.

**Actions Binance API** :
1. Verifier IP whitelist Binance (whitelist VPS Hetzner)
2. Si rate limit -> attendre 1h, code doit gerer 429 graceful

---

## P1 — Reconciliation divergence

**Symptomes**: Telegram `RECONCILIATION CRITICAL [<book>] only_in_broker:
[BTCUSDT]` (Phase 6).

**Triage** :
- `only_in_broker`: position broker, pas dans local state -> orphan, ordre
  passe sans tracker (worker etait down lors du fill ?)
- `only_in_local`: position local, pas dans broker -> phantom, etat corrompu
  ou ordre rejete silencieux

**Actions** :
```bash
# Read latest report
ls -lt data/reconciliation/ | head -5
cat data/reconciliation/<book>_<date>.json | jq .
```
1. Pour orphan broker -> ajouter manuellement dans state JSON OU close manuel
2. Pour phantom local -> retirer manuellement du state JSON (apres backup)
3. Toujours: log incident dans `docs/incidents/`

---

## P1 — Data stale (parquet)

**Symptomes**: `book health DEGRADED on critical checks: data::*` ou
strategie skip "data stale" (alt_rel_strength, btc_asia_mes_leadlag, MCL).

**Triage** :
```bash
# Verifier mtime parquets
ls -la data/futures/*.parquet data/crypto/candles/*.parquet | sort -k 6,7
# Cron yfinance
ssh root@VPS "crontab -l | grep refresh"
ssh root@VPS "tail /var/log/data_refresh.log"
```

**Actions** :
1. Run cron manuellement: `bash scripts/refresh_data_yfinance.sh`
2. Si yfinance 429 -> attendre 1h
3. Si fix permanent: ajuster cron schedule ou switch to ib_insync data

---

## P1 — OrderTracker recovery flag at boot

**Symptomes**: log `OrderTracker recovered N orders, M still active. Active IDs:
[...]`.

**Actions** :
1. Pour chaque `active_order_id`, verifier broker:
   - Binance: order id dans broker.get_open_orders()
   - IBKR: dans ib.orders()
2. Si broker dit FILLED mais tracker dit SUBMITTED -> `tracker.fill(order_id)`
3. Si broker dit CANCELLED -> `tracker.cancel(order_id)`
4. Si broker n'a rien -> probablement deja terminal, marquer ERROR pour cleanup

---

## Backup state restoration (si state corrompu)

```bash
# Local
ls data/backups/
# Restore le dernier backup propre
tar xzvf data/backups/state_<date>.tar.gz -C /tmp/restore/
# Compare avec etat actuel avant remplacer
diff -r /tmp/restore/data/state/ data/state/
# Si OK, replace
cp -a /tmp/restore/data/state/. data/state/
```

---

## Annexe: contacts + URLs

- Telegram bot: TRADING_PLATFORM_BOT (token in .env)
- VPS SSH: `ssh -i ~/.ssh/id_hetzner root@178.104.125.74`
- VNC fallback: Hetzner Console (cf reference_vps_credentials.md)
- Dashboard: https://<dashboard-url>/
- IBKR client portal: https://www.interactivebrokers.com/portal/
- Binance: https://www.binance.com/en/my/dashboard
