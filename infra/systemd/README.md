# infra/systemd/

Unit files systemd versionnes pour le VPS Hetzner.

## Fichiers

| Unit | Role |
|---|---|
| `trading-weekly-review.service` | Phase 3.4 desk productif 2026-04-22. OneShot: genere report hebdo + push Telegram. |
| `trading-weekly-review.timer` | Declencheur dimanche 22h UTC. |

## Installation sur VPS (une fois)

```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74 \
  'cp /opt/trading-platform/infra/systemd/trading-weekly-review.service /etc/systemd/system/ && \
   cp /opt/trading-platform/infra/systemd/trading-weekly-review.timer /etc/systemd/system/ && \
   systemctl daemon-reload && \
   systemctl enable --now trading-weekly-review.timer && \
   systemctl list-timers --all | grep weekly'
```

## Verification

```bash
systemctl status trading-weekly-review.timer
systemctl list-timers --all | grep weekly

# Next trigger:
systemctl show trading-weekly-review.timer --property=NextElapseUSecRealtime

# Test manuel (force execution now, sans attendre dimanche):
systemctl start trading-weekly-review.service
tail -30 /opt/trading-platform/logs/weekly_review.log
```

## Rollback

```bash
systemctl disable --now trading-weekly-review.timer
rm /etc/systemd/system/trading-weekly-review.{service,timer}
systemctl daemon-reload
```
