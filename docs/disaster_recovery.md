# INFRA-003 : Backup et Disaster Recovery

> Date : 2026-03-27
> Objectif : restauration complete en < 1h

---

## Quoi sauvegarder

### Donnees critiques (perte = perte d'argent)

| Donnee | Localisation | Frequence backup | Methode |
|--------|-------------|------------------|---------|
| Code source | GitHub `trading-platform` | Chaque commit | Git push |
| Configuration allocation | `config/allocation.yaml` | Chaque modif | Git |
| Configuration limites | `config/limits.yaml` | Chaque modif | Git |
| State portefeuille | `paper_portfolio_state.json` | Apres chaque trade | Git + Alpaca API |
| State strategies | `paper_*_state.json` | Apres chaque trade | Git |
| Historique trades | `output/session_*/trades_*.csv` | Quotidien | Git + local |
| Features ML | `data_cache/ml_features.db` | Hebdomadaire | Copie manuelle |

### Donnees reconstructibles (perte = temps perdu)

| Donnee | Localisation | Reconstruction |
|--------|-------------|----------------|
| Positions ouvertes | Alpaca API | `GET /v2/positions` — source de verite |
| Ordres actifs | Alpaca API | `GET /v2/orders` — source de verite |
| Equity / balance | Alpaca API | `GET /v2/account` — source de verite |
| Cache OHLCV | `data_cache/` | Re-telechargement via yfinance/Alpaca |
| Resultats backtests | `output/` | Re-execution des backtests |
| Walk-forward results | `output/walk_forward_results.json` | Re-execution |
| Kill switch calibration | `output/kill_switch_calibration.json` | Re-calcul Monte Carlo |

### Donnees non critiques

| Donnee | Localisation | Action |
|--------|-------------|--------|
| Logs | `logs/` | Ephemeres, conserves 7 jours max |
| `__pycache__` | Partout | Ignorees, auto-regenerees |
| Dashboard builds | `dashboard/` | Rebuild `npm run build` |

---

## Ou sauvegarder

### Source de verite par type

```
Code + Config    →  GitHub (privé)
Positions live   →  Alpaca API (broker = source de vérité)
State fichiers   →  GitHub + copie locale
Features ML      →  Copie locale + GitHub LFS (si > 100MB)
```

### Architecture de backup

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Railway Worker │────>│  GitHub Repo │<────│  PC Local    │
│  (production)   │     │  (backup 1)  │     │  (backup 2)  │
└────────┬────────┘     └──────────────┘     └──────────────┘
         │
         v
┌──────────────────┐
│  Alpaca API      │
│  (source verite  │
│   positions)     │
└──────────────────┘
```

---

## Comment restaurer en < 1h

### Runbook step-by-step

#### Scenario A : Worker Railway corrompu / supprime

**Temps estime : 20 minutes**

```
ETAPE 1 (5 min) — Evaluer la situation
  □ Se connecter a Alpaca Dashboard : https://app.alpaca.markets
  □ Verifier les positions ouvertes (rien a faire si bracket orders actifs)
  □ Verifier les ordres pendants (annuler si douteux)

ETAPE 2 (5 min) — Deployer un nouveau worker
  □ Railway Dashboard > New Project > Deploy from GitHub
  □ Selectionner le repo trading-platform
  □ Variables d'env : copier depuis le backup local (.env non commite)
    - ALPACA_API_KEY
    - ALPACA_SECRET_KEY
    - PAPER_TRADING=true
    - TELEGRAM_BOT_TOKEN
    - TELEGRAM_CHAT_ID
  □ Start command : python worker.py

ETAPE 3 (5 min) — Restaurer le state
  □ Le state des positions est reconstruit depuis Alpaca API
  □ paper_portfolio_state.json : dernier commit GitHub
  □ Si divergence : prioriser Alpaca API (positions reelles)

ETAPE 4 (5 min) — Verifier
  □ python scripts/paper_portfolio.py --status
  □ python scripts/reconciliation.py
  □ Verifier le heartbeat Telegram
  □ Verifier que le scheduler reprend les crons
```

#### Scenario B : State fichier corrompu

**Temps estime : 10 minutes**

```
ETAPE 1 (2 min) — Diagnostiquer
  □ cat paper_portfolio_state.json  (est-il valide JSON ?)
  □ python -c "import json; json.load(open('paper_portfolio_state.json'))"

ETAPE 2 (3 min) — Restaurer depuis le dernier commit
  □ git log --oneline paper_portfolio_state.json  (trouver le dernier bon)
  □ git checkout <commit_hash> -- paper_portfolio_state.json
  □ Ne PAS faire git checkout . (ca ecrase tout)

ETAPE 3 (5 min) — Reconcilier avec Alpaca
  □ python scripts/reconciliation.py
  □ Corriger manuellement les divergences si necessaire
  □ Relancer le worker
```

#### Scenario C : Compte Alpaca compromis / suspendu

**Temps estime : 30-60 minutes**

```
ETAPE 1 (immediat) — Securiser
  □ Regenerer les API keys sur Alpaca Dashboard
  □ Mettre a jour les variables d'env Railway
  □ Verifier l'historique des ordres (ordres non autorises ?)

ETAPE 2 (15 min) — Evaluer
  □ Positions ouvertes : fermer tout si compromission confirmee
  □ Contacter le support Alpaca si compte suspendu

ETAPE 3 (15 min) — Restaurer
  □ Nouvelles API keys dans .env + Railway
  □ Redemarrer le worker
  □ Mode dry-run pendant 24h pour valider
```

#### Scenario D : Perte totale (PC + Railway + GitHub)

**Temps estime : 60 minutes**

```
ETAPE 1 — GitHub est le backup ultime
  □ Clone le repo depuis GitHub sur une nouvelle machine
  □ pip install -r requirements.txt

ETAPE 2 — Reconstruire le state depuis Alpaca
  □ Les positions reelles sont dans l'API Alpaca
  □ python scripts/reconciliation.py --rebuild-state

ETAPE 3 — Redeployer Railway
  □ Nouveau projet Railway, connect GitHub
  □ Variables d'env (les API keys sont dans Alpaca Dashboard)

ETAPE 4 — Verifier
  □ Executer tous les tests : python -m pytest tests/ -v
  □ Mode dry-run 24h
```

---

## Checklist de backup periodique

### Quotidien (automatise par le worker)

- [x] State commit apres chaque session de trading
- [x] Heartbeat Telegram toutes les 30 min (monitoring indirect)
- [ ] Backup `data_cache/ml_features.db` (a implementer)

### Hebdomadaire (manuel, vendredi soir)

- [ ] `git pull` sur le PC local (verifier que le repo est a jour)
- [ ] Exporter les positions Alpaca : `python scripts/paper_portfolio.py --status`
- [ ] Verifier UptimeRobot : uptime > 99%
- [ ] Verifier les logs Railway : pas d'erreurs critiques

### Mensuel

- [ ] Tester le scenario A (restauration worker) sur un projet Railway de test
- [ ] Verifier que les API keys Alpaca sont toujours valides
- [ ] Rotation des tokens Telegram si necessaire

---

## Contacts et acces

| Service | URL | Acces |
|---------|-----|-------|
| GitHub | github.com/[repo] | SSH key + token |
| Railway | railway.app | Compte Google |
| Alpaca | app.alpaca.markets | Email + 2FA |
| Telegram Bot | @[bot_name] | Token dans .env |
| UptimeRobot | uptimerobot.com | Compte email |

---

## Temps de restauration cibles

| Scenario | RTO (Recovery Time) | RPO (Recovery Point) |
|----------|--------------------|--------------------|
| Worker crash | < 20 min | Dernier commit (< 1h) |
| State corrompu | < 10 min | Dernier commit |
| Compromission | < 60 min | Dernier etat Alpaca |
| Perte totale | < 60 min | Dernier push GitHub |
