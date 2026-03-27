# Infrastructure — Monitoring & Healthcheck

## Healthcheck Endpoint

Le script `scripts/healthcheck_endpoint.py` expose un serveur HTTP leger pour le monitoring externe.

### Lancement

```bash
# Port par defaut (8080)
python scripts/healthcheck_endpoint.py

# Port custom
python scripts/healthcheck_endpoint.py --port 9090

# Via variable d'environnement
HEALTHCHECK_PORT=9090 python scripts/healthcheck_endpoint.py
```

### Endpoints

| Endpoint   | Methode | Description                    |
|-----------|---------|--------------------------------|
| `/health` | GET     | Status JSON complet            |
| `/`       | GET     | Page de confirmation (texte)   |

### Reponse `/health`

```json
{
  "status": "healthy",
  "timestamp": 1711540800.0,
  "timestamp_iso": "2026-03-27T12:00:00Z",
  "issues": [],
  "worker": {
    "alive": true,
    "last_run": "2026-03-27T11:55:00+00:00",
    "age_seconds": 300.0
  },
  "alpaca": {
    "connected": true,
    "equity": 100414.46,
    "positions": 5
  },
  "kill_switch": {
    "active": false,
    "disabled_strategies": []
  }
}
```

### Codes HTTP

| Code | Status      | Signification                                |
|------|-------------|----------------------------------------------|
| 200  | healthy     | Tout fonctionne normalement                  |
| 200  | degraded    | Kill switch actif, mais worker/broker OK     |
| 503  | unhealthy   | Worker stale (>10min) ou Alpaca deconnecte   |

---

## Configuration UptimeRobot (gratuit)

### Etape 1 : Creer un compte

1. Aller sur https://uptimerobot.com/
2. Creer un compte gratuit (jusqu'a 50 moniteurs)

### Etape 2 : Ajouter un moniteur

1. **Dashboard** > **Add New Monitor**
2. **Monitor Type** : HTTP(s)
3. **Friendly Name** : `Trading Platform Worker`
4. **URL** : `http://<votre-ip-ou-domaine>:8080/health`
   - Sur Railway : utiliser l'URL publique du service
   - En local : utiliser un tunnel (ngrok, cloudflared)
5. **Monitoring Interval** : 5 minutes
6. **Monitor Timeout** : 30 seconds

### Etape 3 : Configurer les alertes

1. **My Settings** > **Alert Contacts**
2. Ajouter un contact :
   - **SMS** : numero de telephone (limites sur le plan gratuit)
   - **Email** : adresse email
   - **Telegram** : via le bot @UptimeRobot_Bot
   - **Webhook** : URL custom pour integration

### Configuration Telegram pour UptimeRobot

1. Chercher `@UptimeRobot_Bot` sur Telegram
2. Envoyer `/start`
3. Copier le lien de connexion fourni
4. Coller dans UptimeRobot > Alert Contacts > Telegram

### Etape 4 : Deploiement Railway

Si le worker tourne sur Railway :

1. Ajouter un service supplementaire dans le projet Railway
2. **Start Command** : `python scripts/healthcheck_endpoint.py`
3. Railway genere une URL publique automatiquement
4. Utiliser cette URL dans UptimeRobot

Alternativement, lancer le healthcheck en thread dans le worker (ajouter a `worker.py` si besoin futur).

---

## Reconciliation des positions

Le script `scripts/reconciliation.py` compare les positions internes vs les brokers.

### Lancement

```bash
# Mode console
python scripts/reconciliation.py

# Mode JSON (pour integration CI/cron)
python scripts/reconciliation.py --json
```

### Integration cron

Ajouter dans le worker ou en tache planifiee :

```bash
# Toutes les heures pendant les heures de trading
0 15-22 * * 1-5 python scripts/reconciliation.py --json >> logs/reconciliation.log
```

### Seuils d'alerte

| Type                | Severite  | Condition                          |
|---------------------|-----------|------------------------------------|
| Orphan (>$100)      | critical  | Position broker sans state         |
| Orphan (<$100)      | warning   | Petite position broker sans state  |
| Missing             | critical  | Position state sans broker         |
| Direction mismatch  | critical  | Long/short inverse                 |
| API error           | critical  | Broker inaccessible                |

---

## INFRA-004 : Evaluation Migration VPS

> Date : 2026-03-27

### Comparaison Railway vs VPS dedie

| Critere | Railway (actuel) | VPS Hetzner ($5/mois) |
|---------|-----------------|----------------------|
| **Cout** | ~$5/mois (Hobby plan) | $4.15/mois (CX22: 2 vCPU, 4GB RAM) |
| **Setup** | Zero config, deploy via GitHub | Config manuelle (OS, Python, systemd) |
| **Scaling** | Auto-scale (inutile pour nous) | Manuel (upgrade plan) |
| **Uptime SLA** | 99.9% (constate ~99.5%) | 99.9% (Hetzner historique excellent) |
| **Localisation** | US-West ou EU | Falkenstein/Nuremberg/Helsinki (EU) |
| **Latence Alpaca** | ~20ms (US-West) | ~120ms (EU vers Alpaca US) |
| **Latence IBKR EU** | ~120ms (US vers EU) | ~5ms (EU vers IBKR EU) |
| **Persistance disque** | Ephemere (redeploy = perte) | Persistant (SSD 40GB) |
| **Cron natif** | Non (scheduler Python) | Oui (crontab systeme) |
| **SSH acces** | Non | Oui (debug en direct) |
| **Monitoring** | Railway Metrics (basique) | Libre (Grafana, htop, journalctl) |
| **CI/CD** | GitHub Actions -> Railway | GitHub Actions -> rsync/docker |
| **Securite** | Geree par Railway | A configurer (firewall, SSH keys, fail2ban) |
| **Backup** | Pas de volume persistant | Snapshots Hetzner ($1.20/mois) |
| **Multi-process** | 1 service = 1 process | Illimite (worker + healthcheck + cron) |

### Pour Railway (garder l'actuel)

- **Zero maintenance** : pas de mise a jour OS, pas de securite a gerer
- **Deploy instantane** : git push = deploy en 30s
- **Ideal pour la phase paper** : on ne veut pas perdre du temps sur l'infra
- **Rollback facile** : revenir a un deploy precedent en 1 clic

### Pour VPS Hetzner (migrer)

- **Persistance** : les fichiers state et SQLite survivent aux redeploys
- **Latence IBKR** : critique pour le live EU (5ms vs 120ms)
- **Multi-process natif** : worker + healthcheck + cron + dashboard en parallele
- **SSH debug** : inspecter les logs en temps reel, attacher un debugger
- **Cron natif** : plus fiable que le scheduler Python APScheduler
- **Cout fixe** : pas de surprise de facturation
- **Snapshots** : backup complet du serveur en 1 clic

### Contre Railway

- **Pas de volume persistant** : le SQLite (ml_features.db) est perdu a chaque redeploy
- **Pas de cron systeme** : depend du scheduler Python (single point of failure)
- **Pas de SSH** : impossible de debugger en direct
- **Latence EU** : penalisante pour IBKR EU

### Contre Hetzner VPS

- **Maintenance OS** : mises a jour, securite, firewall a gerer soi-meme
- **CI/CD a configurer** : pas de deploy automatique sans effort
- **Latence Alpaca** : 120ms vs 20ms pour les ordres intraday US
- **Temps de setup** : 2-4h pour la premiere configuration

### Recommandation

| Phase | Recommandation | Raison |
|-------|---------------|--------|
| **Paper (actuel)** | Rester sur Railway | Zero maintenance, focus sur les strategies |
| **Live L1** ($25K) | Rester sur Railway | Le risque infra est faible a ce capital |
| **Live L2** ($50K) | **Migrer vers Hetzner** | Persistance + latence EU + multi-process |
| **Live L3+** ($100K) | Hetzner + Railway backup | Redundance : VPS principal + Railway fallback |

### Plan de migration (quand le moment viendra)

1. Commander un CX22 chez Hetzner (Falkenstein, EU)
2. Installer Ubuntu 22.04 LTS, Python 3.11, pip, git
3. Cloner le repo, installer les dependances
4. Configurer systemd services : `trading-worker.service`, `trading-healthcheck.service`
5. Configurer le firewall (ufw) : ouvrir uniquement SSH + port healthcheck
6. Tester pendant 7 jours en parallele avec Railway
7. Basculer le monitoring UptimeRobot vers le VPS
8. Desactiver le worker Railway (garder comme backup froid)
