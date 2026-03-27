# TEST-003 : Stress Tests Infrastructure — Chaos Testing Report

> Date : 2026-03-27
> Statut : Protocoles definis, a executer avant passage Live L1

---

## Objectif

Valider la resilience de la plateforme face a 4 scenarios de defaillance :
1. Railway down (worker cloud)
2. Alpaca timeout (broker API)
3. Reconnexion apres deconnexion prolongee
4. Donnees corrompues (feed data)

---

## Scenario 1 : Railway down — Alerting externe

### Protocole de test

1. **Setup** :
   - Worker Railway actif, heartbeat Telegram toutes les 30 min
   - UptimeRobot configure sur `/health` endpoint (5 min interval)
   - Positions ouvertes (au moins 2 strategies actives)

2. **Execution** :
   - Stopper le service Railway manuellement (Railway Dashboard > Stop Service)
   - Attendre 10 minutes

3. **Verifications** :
   - [ ] Alerte Telegram "heartbeat manquant" recue dans les 10 min
   - [ ] UptimeRobot envoie une alerte "DOWN" dans les 10 min
   - [ ] Le healthcheck endpoint retourne 503 (ou timeout)
   - [ ] Aucune nouvelle position ouverte pendant le downtime
   - [ ] Les ordres bracket existants (SL/TP) restent actifs cote Alpaca

4. **Resultat attendu** :
   - Les bracket orders sont broker-side : ils survivent au crash du worker
   - L'alerte externe est la premiere ligne de defense
   - Marc est notifie dans les 10 min pour intervention manuelle

5. **Recuperation** :
   - Redemarrer le service Railway
   - Verifier que le worker reprend le scheduler sans double execution (lock idempotence)
   - Verifier que le heartbeat Telegram reprend
   - Verifier la reconciliation des positions

### Risque residuel

Le pire scenario est un crash Railway pendant la fermeture forcee 15:55 ET. Les positions resteraient ouvertes apres heures. **Mitigation** : les bracket orders SL limitent la perte. Un cron externe (Hetzner VPS ou PC local) pourrait servir de backup pour la fermeture.

---

## Scenario 2 : Alpaca timeout — Retry logic

### Protocole de test

1. **Setup** :
   - Worker actif, marche ouvert
   - Au moins 1 signal en cours de traitement

2. **Execution** (simulation) :
   - Dans `core/alpaca_client/client.py`, ajouter temporairement un delay de 31s avant l'appel API (depasser le timeout par defaut de 30s)
   - Ou : utiliser un proxy HTTP qui simule un delai (mitmproxy)

3. **Verifications** :
   - [ ] Le client retry automatiquement (max 3 tentatives)
   - [ ] Les retries utilisent un backoff exponentiel (1s, 2s, 4s)
   - [ ] Apres 3 echecs, le signal est abandonne (pas de crash du worker)
   - [ ] Alerte Telegram envoyee apres l'echec final
   - [ ] Le worker continue a traiter les signaux suivants
   - [ ] Aucun ordre duplique (idempotence key)

4. **Resultat attendu** :
   - Timeout isole = le worker survit et continue
   - L'ordre echoue est logue avec tous les details
   - Le prochain cycle reprend normalement

5. **Recuperation** :
   - Retirer le delay artificiel
   - Verifier que l'ordre abandonne n'a pas ete partiellement execute cote Alpaca
   - Lancer la reconciliation manuelle

### Cas edge a tester

- Timeout pendant la soumission de l'ordre (ordre peut etre soumis mais la reponse perdue)
- Timeout pendant l'annulation d'un ordre (ordre peut rester actif)
- **Mitigation** : toujours verifier les ordres ouverts apres un timeout

---

## Scenario 3 : Deconnexion prolongee — Reconnexion backoff

### Protocole de test

1. **Setup** :
   - Worker actif
   - Connexion reseau fonctionnelle

2. **Execution** :
   - Couper la connexion Internet du serveur pendant 5 minutes
   - Ou : bloquer les DNS pour `api.alpaca.markets` via firewall

3. **Verifications** :
   - [ ] Le client detecte la deconnexion dans les 30s
   - [ ] Backoff exponentiel : retry a 1s, 2s, 4s, 8s, 16s, 30s (cap)
   - [ ] Alerte Telegram "broker disconnect" envoyee apres 1 min de deconnexion
   - [ ] Le worker ne crash pas (boucle de retry dans le scheduler)
   - [ ] A la reconnexion : reconciliation automatique des positions
   - [ ] Aucun signal perdu pendant la deconnexion (mis en queue)

4. **Resultat attendu** :
   - La reconnexion est automatique et transparente
   - Les signaux generes pendant le downtime sont soit expires (si trop vieux), soit executes
   - La reconciliation post-reconnexion detecte toute divergence

5. **Recuperation** :
   - Restaurer la connexion
   - Verifier le log de reconnexion
   - Lancer `python scripts/reconciliation.py` pour valider

### Note IBKR (futur)

IBKR utilise des connexions TCP persistantes via TWS/Gateway. La reconnexion est plus complexe :
- TWS/Gateway peut necessiter un restart manuel
- L'API IBKR a un mecanisme de reconnexion automatique (mais pas toujours fiable)
- **Recommandation** : superviser avec un healthcheck dedie IBKR

---

## Scenario 4 : Donnees corrompues — Rejet et fallback

### Protocole de test

1. **Setup** :
   - Worker actif, marche ouvert
   - Pipeline de signaux en cours

2. **Execution** (simulation) :
   - Injecter des donnees invalides dans le flux de prix :
     - Prix negatif (close = -100)
     - Volume = 0 sur toutes les barres
     - Timestamp dans le futur (+1 jour)
     - NaN dans les colonnes OHLCV
     - DataFrame vide (0 rows)

3. **Verifications** :
   - [ ] Les donnees invalides sont rejetees AVANT le calcul des indicateurs
   - [ ] Le FeatureStore retourne NaN pour les features non calculables
   - [ ] Les strategies ne generent PAS de signal sur des donnees corrompues
   - [ ] Alerte Telegram "data quality" envoyee
   - [ ] Le worker continue avec les autres strategies/symboles
   - [ ] Le backtest engine rejette les donnees avec guard explicite

4. **Resultat attendu** :
   - Aucun trade base sur des donnees corrompues
   - Le pipeline est "fail-safe" : en cas de doute, ne pas trader
   - Les strategies individuelles ont des guards de validite

5. **Recuperation** :
   - Identifier la source de la corruption (API Alpaca, cache local, parsing)
   - Purger le cache local (`data_cache/`)
   - Relancer le worker

### Validations supplementaires

| Type de corruption | Attendu | Verifie |
|-------------------|---------|---------|
| Prix negatif | Rejet | [ ] |
| Volume zero | Warning, pas de rejet (arrive en pre-market) | [ ] |
| Timestamp futur | Rejet (lookahead) | [ ] |
| NaN dans close | Rejet | [ ] |
| DataFrame vide | Skip, pas de crash | [ ] |
| Spread > 5% | Warning, sizing reduit | [ ] |
| Prix identiques H=L=O=C | Warning (possible apres split) | [ ] |

---

## Matrice de priorite

| Scenario | Impact | Probabilite | Priorite test | Automatisable |
|----------|--------|-------------|---------------|---------------|
| Railway down | Eleve | Moyen (1x/mois) | P0 | Partiellement |
| Alpaca timeout | Moyen | Eleve (quotidien) | P0 | Oui |
| Deconnexion prolongee | Eleve | Faible (1x/trimestre) | P1 | Non |
| Donnees corrompues | Moyen | Moyen | P1 | Oui |

---

## Prochaines etapes

1. **Avant Live L1** : executer les scenarios 1 et 2 manuellement
2. **Avant Live L2** : scenarios 3 et 4 + automatiser les tests de donnees corrompues
3. **Integrer en CI** : tests unitaires pour le scenario 4 (donnees invalides)
4. **Monitoring continu** : metriques de retry/timeout dans les logs structures
