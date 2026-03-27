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
