# Close-out — Premier live_micro BTCUSDC 2026-04-23

**Sleeve** : `btc_asia_mes_leadlag_q80_v80_long_only`
**Déploiement** : 2026-04-22 (Phase 1 + Phase 2 code merged)
**Go-live** : 2026-04-23 cycle 10h30 Paris (08h30 UTC ete)
**Taille** : $200 USDC notional, kill DD -$50 auto-kill sleeve

---

## 1. Diff config applique

### `config/quant_registry.yaml`
```yaml
  - strategy_id: btc_asia_mes_leadlag_q80_v80_long_only
-   status: paper_only
+   status: live_micro
    paper_start_at: "2026-04-20"
-   live_start_at: null
+   live_start_at: "2026-04-23"
-   is_live: false
+   is_live: true
+   live_micro_config:
+     notional_usd: 200
+     risk_usd: 20
+     kill_dd_usd: 50
+     max_hold_hours: 24
+     symbol: BTCUSDC
+     runner: core.runtime.btc_asia_q80_live_micro_runner.run_live_micro_cycle
```

### `config/live_whitelist.yaml`
Même changement status + max_notional_usd: 200, max_risk_usd: 20, kill_criteria.drawdown_absolute_usd: -50.

### `config/books_registry.yaml` (Phase 1)
`binance_crypto.mode_authorized`: `live_allowed` → `live_micro_allowed`.

---

## 2. Commande de restart VPS

```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74 \
  'cd /opt/trading-platform && git pull --rebase origin main && \
   systemctl restart trading-worker.service && \
   sleep 10 && systemctl status trading-worker --no-pager | head -20'
```

**Vérification service** :
```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74 \
  'systemctl is-active trading-worker.service && \
   journalctl -u trading-worker -n 50 --no-pager | grep -iE "boot|live_micro|btc_asia|error|fail"'
```

---

## 3. Checklist pré-cycle (à valider AVANT 10h30 Paris le 2026-04-23)

### 3.A Boot check
- [ ] `systemctl is-active trading-worker` → `active`
- [ ] `journalctl -u trading-worker | grep "boot_state"` → ligne récente présente
- [ ] `journalctl -u trading-worker | grep "quant_registry loaded"` → present
- [ ] `journalctl -u trading-worker | grep "live_whitelist loaded"` → present

### 3.B Runtime audit strict
```bash
ssh ... 'cd /opt/trading-platform && .venv/bin/python scripts/runtime_audit.py --strict'
```
Attendu : exit 0, `btc_asia_mes_leadlag_q80_v80_long_only` listé en LIVE.

### 3.C Données fraîches
- [ ] `ls -la data/futures/MES_1H_YF2Y.parquet` age ≤ 3 jours
- [ ] `ls -la data/crypto/candles/BTCUSDT_1h.parquet` age ≤ 3 jours

### 3.D Broker health
```bash
ssh ... 'cd /opt/trading-platform && .venv/bin/python -c "
from core.broker.binance_broker import BinanceBroker
b = BinanceBroker()
print(b.get_account_info())
"'
```
Attendu : balance USDC spot > $200 (couvrir l'ordre).

### 3.E Pas de kill flag résiduel
```bash
ssh ... 'ls /opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/_kill_switch.json 2>&1 || echo "OK no kill flag"'
```
Attendu : `OK no kill flag`.

### 3.F Telegram test
- [ ] Envoyer une commande `/status` via Telegram → bot répond
- [ ] Channel critical actif et non throttled

---

## 4. Monitoring 2026-04-23 matin

### 4.A Logs à surveiller (pendant cycle 10h30 Paris)

**Log worker principal** :
```bash
ssh ... 'journalctl -u trading-worker -f | grep -iE "btc_asia|live_micro"'
```

**Journal live_micro dédié** :
```bash
ssh ... 'tail -f /opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/journal.jsonl'
```

**État positions** :
```bash
ssh ... 'cat /opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/positions.json'
```

**Dernier cycle** (debug) :
```bash
ssh ... 'cat /opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/_last_cycle.json'
```

### 4.B Scénarios attendus

| Scenario | Indicateur | Action |
|---|---|---|
| **Signal BUY + fill OK** | Event `entry` dans journal + Telegram "ENTRY live_micro q80" + positions.json = 1 position | Laisser tourner, check exit le lendemain |
| **Signal NONE/SELL** | Event `entry_skipped` avec reason `signal_side=NONE` ou `SELL` | Normal, c'est la majorité des jours |
| **Kill DD -$50 atteint** | Event `exit` avec `exit_reason=kill_dd_50` + `_kill_switch.json` créé + Telegram CRITICAL | **ROLLBACK** : suivre instructions dans `_kill_switch.json` |
| **Max hold 24h** | Event `exit` avec `exit_reason=max_hold_24h` | Normal si PnL positif ou petit négatif |
| **Exec error** | Event `exec_error` + Telegram CRITICAL | **Diagnostic urgent** : Binance API issue, credentials, balance |

### 4.C Commandes rollback urgence

Si exécution anormale → rollback immédiat à paper_only :

```bash
ssh ... 'cd /opt/trading-platform && \
  sed -i "s/status: live_micro/status: paper_only/" config/quant_registry.yaml && \
  sed -i "s/status: live_micro/status: paper_only/" config/live_whitelist.yaml && \
  rm -f data/state/btc_asia_mes_leadlag_q80_live_micro/_kill_switch.json && \
  systemctl restart trading-worker.service'
```

**Attention** : le sed ci-dessus remet TOUT live_micro en paper_only. Si d'autres sleeves sont en live_micro, faire un rollback ciblé manuel.

Rollback ciblé (uniquement q80) :
```bash
ssh ... 'cd /opt/trading-platform && \
  python3 -c "
import yaml, pathlib
for p in [\"config/quant_registry.yaml\", \"config/live_whitelist.yaml\"]:
    # manual YAML edit, preserve structure — do by hand si besoin"
```

Ou plus simplement : éditer à la main les 2 fichiers via `vim` puis restart worker.

---

## 5. Chemins exacts à surveiller

| Type | Chemin absolu VPS |
|---|---|
| **Journal live_micro** | `/opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/journal.jsonl` |
| **Positions live_micro** | `/opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/positions.json` |
| **Kill flag** | `/opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/_kill_switch.json` |
| **Last cycle debug** | `/opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/_last_cycle.json` |
| **Worker logs** | `journalctl -u trading-worker` |
| **Paper journal q80 (baseline parallele)** | `/opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_long_only/paper_journal.jsonl` |
| **Signal funnel crypto** | `/opt/trading-platform/data/crypto/signal_funnel.jsonl` |

---

## 6. Criteres de succès 2026-04-23

Minimum acceptable :
- Cycle 10h30 Paris tourne sans crash
- Event journaled (entry OU entry_skipped)
- Pas d'exec_error Telegram
- État state files cohérent

**Si 0 signal BUY** : pas d'erreur, pas d'action, attendre jour suivant. Le signal long_only q80 fire ~1x tous les 10 jours statistiquement.

**Si signal BUY + fill OK** : premier vrai trade live_micro réussi, suivre position 24h, exit le 2026-04-24 10h30 Paris (max_hold_24h) sauf si kill avant.

---

## 7. Contact urgence

Si incident critique pendant cycle :
1. Telegram channel `critical` → notification immédiate avec contexte
2. Event `exec_error` dans journal
3. Worker ne crash pas (exception catchée)
4. Marc peut rollback manuel via commandes §4.C

---

**Document généré** : 2026-04-22
**Auteur** : Phase 2 deploy desk productif plan
**Revue** : à mettre à jour 2026-04-23 soir avec résultat premier cycle
