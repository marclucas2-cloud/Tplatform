# Runbook Live Trading — V1

## Derniere mise a jour : 27 mars 2026

> Document de reference pour operer le systeme au quotidien avec de l'argent reel.
> Marc est seul operateur. Ce runbook couvre tous les scenarios.

---

## Table des matieres

1. [Routine quotidienne](#1-routine-quotidienne-15-minjour)
2. [Que faire si...](#2-que-faire-si)
3. [Commandes Telegram](#3-commandes-telegram)
4. [Contacts d'urgence](#4-contacts-durgence)
5. [Procedure "tout fermer en 2 min"](#5-procedure-tout-fermer-en-2-min)
6. [Maintenance hebdomadaire](#6-maintenance-hebdomadaire-dimanche-30-min)
7. [Maintenance mensuelle](#7-maintenance-mensuelle-1er-dimanche-du-mois-1h)
8. [Architecture systeme](#8-architecture-systeme)
9. [Limites live ($10K)](#9-limites-live-10k)
10. [Strategies live (mois 1)](#10-strategies-live-mois-1)
11. [Objectifs mois 1](#11-objectifs-mois-1)
12. [Niveaux d'alerte](#12-niveaux-dalerte)
13. [Deleveraging progressif](#13-deleveraging-progressif)
14. [Mode autonome (absence 48-72h)](#14-mode-autonome-absence-48-72h)
15. [Scaling gates](#15-scaling-gates)

---

## 1. ROUTINE QUOTIDIENNE (~15 min/jour)

### Matin (8h30 CET)

- [ ] `/status` sur Telegram — tout vert ?
- [ ] Verifier les trades FX de la nuit (sessions Asian/London)
- [ ] `/margin` — utilisation < 70% ?
- [ ] Alertes Telegram : aucune WARNING/CRITICAL non-traitee ?

### Midi (12h)

- [ ] `/pnl` — check rapide P&L
- [ ] Verifier aucune alerte WARNING/CRITICAL en attente
- [ ] Si session EU ouverte : positions EU dans les limites ?

### Soir (18h)

- [ ] Lire le rapport journalier automatique Telegram
- [ ] Verifier reconciliation (0 divergence attendue)
- [ ] Positions overnight : bracket orders (SL/TP) en place ?
- [ ] `/leverage` — levier dans les limites de PHASE_1 (max 1.5x) ?

### Nuit (22h)

- [ ] `/health` — infra OK ?
- [ ] Si tout vert : laisser tourner, rien a faire

### Dimanche soir (20h, ~30 min)

- [ ] Lire le rapport hebdomadaire
- [ ] Comparer live vs paper (memes strategies)
- [ ] Verifier alpha decay des strategies live
- [ ] Walk-forward continu : pas de degradation ?
- [ ] Decision : ajuster sizing ? Pauser une strat ?
- [ ] Voir [section 6](#6-maintenance-hebdomadaire-dimanche-30-min) pour la checklist complete

---

## 2. QUE FAIRE SI...

### IBKR est deconnecte

1. `/health` — verifier detail connexion
2. Si worker up mais IBKR down :
   - Les bracket orders broker-side **protegent les positions** (SL/TP restent actifs chez IBKR)
   - Verifier sur IBKR Mobile que les positions sont intactes
   - SSH sur le VPS Hetzner, verifier IB Gateway
   - Redemarrer IB Gateway si necessaire : `sudo systemctl restart ibgateway`
3. Si > 30 min sans reconnexion : le mode autonome ferme tout automatiquement
4. Si > 2h : intervention manuelle via IBKR Mobile (voir [section 5](#5-procedure-tout-fermer-en-2-min))

### Le worker Railway crash

1. Alerte Telegram CRITICAL automatique
2. Les bracket orders broker-side **protegent les positions**
3. Verifier Railway dashboard — restart automatique ?
4. Si pas de restart auto : trigger manual restart depuis le dashboard Railway
5. Apres redemarrage : verifier reconciliation (`/status`)
6. Si Railway down > 1h et positions ouvertes sans stops : fermer manuellement via IBKR Mobile

### Kill switch declenche

1. **NE PAS paniquer** — c'est concu pour ca
2. Sequence automatique deja executee :
   - Tous les ordres ouverts annules
   - Toutes les positions fermees (market orders)
   - Toutes les strategies live desactivees
   - Alerte Telegram envoyee
   - Paper trading continue normalement
3. Lire la raison dans l'alerte Telegram
4. Analyser la cause :

| Cause | Action |
|-------|--------|
| DD normal (marche adverse) | Attendre 24h, analyser, `/resume` si confiance |
| Bug d'execution | Fixer le bug, tester en paper, puis `/resume` |
| Flash crash marche | Attendre stabilisation, `/resume` quand VIX normalise |
| Slippage extreme | Verifier la strategie, pauser si structurel |

5. Pour reprendre : `/resume` (requiert CONFIRM)

### Margin call

1. Alerte CRITICAL automatique a 70% margin
2. Le systeme bloque les nouveaux trades a 85% margin
3. Si margin call reel : IBKR liquidera les positions les moins rentables automatiquement
4. Action immediate : `/reduce 50%` pour liberer de la margin
5. Analyser pourquoi la margin est trop elevee :
   - Trop de positions simultanees ?
   - Mouvement adverse sur FX (lot 25K = levier eleve) ?
   - Roll de futures mal gere ?
6. Si recurrent : reduire le levier dans `config/leverage_schedule.yaml`

### Slippage anormal

1. Alerte WARNING automatique quand slippage > 2x backtest
2. Verifier : evenement marche ? (NFP, BCE, FOMC, NFP, CPI...)
3. Si lie a un evenement : ignorer, le `SlippageTracker` enregistre tout
4. Si structurel (pas d'evenement) : `/pause [strat]` pour la strategie concernee
5. Analyser : spread trop large ? Liquidite insuffisante ?
6. Seuils de slippage par paire dans `config/fx_live_sizing.yaml` :
   - EURUSD : 0.5 bps
   - EURGBP : 0.7 bps
   - EURJPY : 1.0 bps
   - AUDJPY : 1.2 bps

### Reconciliation avec divergence

1. Alerte CRITICAL automatique
2. Source de verite = **broker IBKR** (pas le state interne)
3. Verifier IBKR Mobile : positions reelles vs state interne
4. Si divergence < $100 : corriger le state, documenter
5. Si divergence > $100 : `/kill CONFIRM`, investiguer, corriger, `/resume`
6. Script de reconciliation : `python scripts/reconciliation.py`

### Erreur d'execution (ordre rejete)

1. Alerte WARNING automatique
2. Causes frequentes :
   - Lot minimum non respecte (IBKR FX : 25,000 unites)
   - Margin insuffisante
   - Marche ferme (verifier horaires)
   - Symbole incorrect
3. Verifier les logs : `logs/live/`
4. Corriger la cause, le systeme reessaiera au prochain cycle

### Je suis indisponible 48-72h

Voir [section 14 (Mode autonome)](#14-mode-autonome-absence-48-72h).

---

## 3. COMMANDES TELEGRAM

> Authentification par `chat_id` — seul Marc peut envoyer des commandes.
> Commandes destructives : confirmation requise + cooldown 60 sec.

### Commandes de consultation

| Commande | Action |
|----------|--------|
| `/status` | Vue d'ensemble : positions, P&L, margin, strategies actives |
| `/positions` | Detail chaque position (entree, PnL, duree) |
| `/pnl` | P&L today, MTD, YTD (live uniquement) |
| `/paper` | P&L paper separe |
| `/margin` | Utilisation margin detaillee |
| `/leverage` | Levier par classe d'actif |
| `/health` | Statut infra (IBKR, Railway, VPS, healthcheck) |
| `/help` | Liste de toutes les commandes |

### Commandes d'action (requierent CONFIRM)

| Commande | Action | Exemple |
|----------|--------|---------|
| `/kill` | KILL SWITCH — ferme TOUT | `/kill` puis `/kill CONFIRM` |
| `/pause [strat]` | Pause une strategie (stop signaux, garde positions) | `/pause fx_eurusd_trend` |
| `/resume [strat]` | Reprend une strategie | `/resume fx_eurusd_trend` |
| `/reduce [pct%]` | Reduit toutes les positions du pourcentage | `/reduce 50%` puis CONFIRM |
| `/close [ticker]` | Ferme une position specifique | `/close EURUSD` puis CONFIRM |

### Rate limiting

- Max 1 commande destructive par minute
- Token de confirmation valide 60 secondes
- Toutes les commandes loggees avec timestamp

---

## 4. CONTACTS D'URGENCE

| Service | Contact | Quand |
|---------|---------|-------|
| **IBKR desk EU** | +41-41-726-9500 | Positions bloquees, margin call, probleme compte |
| **IBKR desk US** | +1-877-442-2757 | Idem, heures US (14h30-22h CET) |
| **IBKR Mobile** | App mobile iOS/Android | Fermer positions manuellement, verifier compte |
| **Hetzner** | Panel web + SSH | IB Gateway down, VPS unreachable |
| **Railway** | Dashboard web (railway.app) | Worker down, crash, redeploy |
| **Telegram** | @BotFather | Bot ne repond plus, reconfigurer |
| **UptimeRobot** | uptimerobot.com | Verifier historique uptime |

---

## 5. PROCEDURE "TOUT FERMER EN 2 MIN"

> En cas d'urgence absolue, 3 methodes par ordre de rapidite.

### Methode 1 — Telegram (10 sec)

```
/kill
→ Bot demande confirmation
/kill CONFIRM
→ Ferme tout via le worker (ordres annules + positions fermees)
```

### Methode 2 — IBKR Mobile (30 sec)

1. Ouvrir IBKR Mobile
2. Portfolio → voir toutes les positions
3. Select All → Close All (market orders)
4. Confirmer

### Methode 3 — Appel IBKR (2-5 min)

1. Appeler **+41-41-726-9500** (EU) ou **+1-877-442-2757** (US)
2. Donner le numero de compte : `UXXXXXX`
3. Demander : "Close all positions immediately, market orders"
4. Confirmer par ecrit (email) dans la foulee

### Methode 4 — TWS/IB Gateway direct (si SSH disponible)

1. SSH sur VPS Hetzner
2. Script d'urgence : `python scripts/emergency_close.py`

---

## 6. MAINTENANCE HEBDOMADAIRE (dimanche, 30 min)

- [ ] Rapport hebdo lu et analyse
- [ ] Sharpe rolling 7j et 30j verifie (seuil : > 0.5 sur 30j)
- [ ] Slippage moyen par strategie verifie vs backtest
- [ ] Comparaison live vs paper (ratio Sharpe live/paper > 0.5)
- [ ] Alpha decay check via `core/alpha_decay_monitor.py`
- [ ] Walk-forward continu : pas de degradation (>= 50% fenetres OOS profitables)
- [ ] Kill switch calibration : seuils MC toujours pertinents ?
- [ ] Backup verifie (dernier commit GitHub < 7j)
- [ ] VPS Hetzner : disk, RAM, uptime OK ?
- [ ] Railway : uptime, aucun redeploy non-planifie ?

---

## 7. MAINTENANCE MENSUELLE (1er dimanche du mois, 1h)

- [ ] Rapport mensuel complet genere
- [ ] KPI vs gates de scaling evalues (voir [section 15](#15-scaling-gates))
- [ ] Stress test mensuel lance (`pytest tests/test_stress_multi_market.py`)
- [ ] Decision scaling : +$5K ou maintien ? (voir gates)
- [ ] Walk-forward refresh complet sur toutes les strategies live
- [ ] VaR live vs VaR backtest compare (`core/var_live.py`)
- [ ] Cout total (commissions + slippage) vs rendement brut
- [ ] Rapport fiscal PFU verifie (30% flat tax sur PV)
- [ ] Kill switch calibration refresh (Monte Carlo)
- [ ] Review trade journal (`core/trade_journal.py`) : patterns, erreurs recurrentes ?

---

## 8. ARCHITECTURE SYSTEME

```
VPS Hetzner (5 EUR/mois)           Railway ($5/mois)            Dashboard
+-------------------+              +-------------------+         +-----------+
| IB Gateway        |<------------>| Worker 24/7       |-------->| FastAPI   |
| Port 4001 (live)  |              | TradingEngine     |         | React UI  |
| Port 7497 (paper) |              | LiveRiskManager   |         +-----------+
| Auto-restart      |              | KillSwitch        |
+-------------------+              | Reconciliation    |
                                   | AlertingLive      |
                                   | AutonomousMode    |
                                   | VaRLive           |
                                   | TradeJournal      |
                                   +--------+----------+
                                            |
                                            v
                                   +-------------------+
                                   | Telegram Bot      |
                                   | - Alertes 3 niv.  |
                                   | - Commandes       |
                                   | - Heartbeat 30min |
                                   +-------------------+
```

### Pipelines actifs (`config/engine.yaml`)

| Pipeline | Mode | Broker | Port | Capital | Strategies |
|----------|------|--------|:----:|--------:|-----------:|
| `live_ibkr` | LIVE | IBKR | 4001 | $10K | 4 FX |
| `paper_us` | PAPER | Alpaca | — | $100K | 7 US |
| `paper_eu` | PAPER | IBKR | 7497 | $1M | 5 EU |

### Modules critiques

| Module | Fichier | Role |
|--------|---------|------|
| LiveRiskManager | `core/risk_manager_live.py` | Limites position/margin/levier |
| LiveKillSwitch | `core/kill_switch_live.py` | Fermeture d'urgence (3 methodes) |
| ReconciliationLive | `core/reconciliation_live.py` | Verif state vs broker |
| LiveAlertManager | `core/alerting_live.py` | Alertes 3 niveaux |
| TelegramCommands | `core/telegram_commands.py` | Controle depuis le telephone |
| AutonomousMode | `core/autonomous_mode.py` | Operation sans supervision (72h) |
| VaRLive | `core/var_live.py` | Value at Risk temps reel |
| TradeJournal | `core/trade_journal.py` | Historique et analyse des trades |
| SlippageTracker | `core/slippage_tracker.py` | Suivi slippage reel vs backtest |
| AlphaDecayMonitor | `core/alpha_decay_monitor.py` | Detection de degradation alpha |
| FxLiveAdapter | `core/fx_live_adapter.py` | Adaptateur FX pour IBKR live |

---

## 9. LIMITES LIVE ($10K)

> Source : `config/limits_live.yaml`

### Positions

| Parametre | Valeur | Montant |
|-----------|:------:|--------:|
| Max par position | 15% | $1,500 |
| Max par strategie | 20% | $2,000 |
| Max positions simultanees | 6 | — |
| Max long | 60% | $6,000 |
| Max short | 40% | $4,000 |
| Max gross (levier) | 120% | $12,000 |
| Cash minimum | 15% | $1,500 |

### Margin

| Parametre | Valeur | Action |
|-----------|:------:|--------|
| Margin alerte | 70% | WARNING Telegram |
| Margin block | 85% | Bloque tout nouveau trade |

### Circuit breakers

| Declencheur | Seuil | Action |
|-------------|:-----:|--------|
| Perte journaliere | -1.5% (-$150) | Stop trading pour la journee |
| Perte horaire | -1.0% (-$100) | Pause 30 min |
| Perte hebdomadaire | -3.0% (-$300) | Reduce sizing 50% |

### Kill switch

| Declencheur | Seuil | Action |
|-------------|:-----:|--------|
| Rolling 5 jours | -3.0% (-$300) | Tout fermer, strategies desactivees |
| Mensuel | -5.0% (-$500) | Tout fermer, review obligatoire |
| Par strategie | -2.0% (ou MC) | Strategie desactivee |

### Secteur

| Parametre | Valeur |
|-----------|:------:|
| Max par secteur | 30% |
| Max par paire FX | 25% |
| Max par instrument futures | 25% |

### Levier par phase

| Phase | Levier max | Duree min | Condition suivante |
|-------|:----------:|:---------:|-------------------|
| PHASE_1 (actuelle) | 1.5x | 30j | Sharpe 30j > 1.0, DD < 3%, 50+ trades |
| PHASE_2 | 2.0x | 30j | Sharpe 60j > 1.0, DD < 5%, 100+ trades |
| PHASE_3 | 2.5x | 30j | Sharpe 90j > 1.2, capital > $20K |
| PHASE_4 | 3.0x | — | Capital > $25K, Sharpe 90j > 1.5 |

---

## 10. STRATEGIES LIVE (MOIS 1)

> Source : `config/live_strategies.yaml` + `config/fx_live_sizing.yaml`
> Seul le Tier 1 (FX) est actif au mois 1. Tier 2 (futures) en attente de FUT-001.

| Strategie | Paire | Sharpe | Poids | Notional | Margin |
|-----------|-------|:------:|:-----:|:--------:|-------:|
| EUR/USD Trend | EUR.USD | 4.62 | 35% | $30K | $900 |
| EUR/GBP Mean Reversion | EUR.GBP | 3.65 | 25% | $25K | $750 |
| EUR/JPY Carry | EUR.JPY | 2.50 | 22% | $25K | $750 |
| AUD/JPY Carry | AUD.JPY | 1.58 | 18% | $20K | $600 |
| | | | **Total** | **$100K** | **$3,000** |

**Note importante** : avec $10K de capital et un lot minimum IBKR de 25K, la margin totale est ~$3,000 (30% du capital). Seules 1-3 strategies peuvent trader simultanement selon les conditions de marche.

### Strategies en attente (activation conditionnelle)

| Tier | Strategie | Condition d'activation |
|------|-----------|----------------------|
| Tier 2 | Brent Lag (MCL) | FUT-001 valide, 2 contrats x $600 margin |
| Tier 2 | MES Trend | FUT-001 valide, 1 contrat x $1,400 margin |
| Tier 3 | Day-of-Week Seasonal | Capital > $15K |
| Tier 3 | VIX Expansion Short | Capital > $15K |

---

## 11. OBJECTIFS MOIS 1

> **L'objectif n'est PAS le rendement. C'est la validation du systeme.**

### Criteres de succes

| Objectif | Seuil | Mesure |
|----------|:-----:|--------|
| Bugs critiques | 0 | Logs + alertes |
| Slippage reel | < 3x backtest | `SlippageTracker` |
| Reconciliation | 0 divergence sur 14j | `reconciliation_live.py` |
| Kill switch | Teste (>= 1 trigger simule) | Log kill switch |
| Trades sans intervention | 50+ | Trade journal |
| Max perte acceptable | -$300 (-3%) | Rolling 5j |
| Margin max observee | < 70% | Daily check |
| Uptime worker | > 99% | UptimeRobot |

### Critere d'echec (retour paper)

| Situation | Action |
|-----------|--------|
| Perte > $500 (-5%) | Retour paper, review 30 jours |
| Bug critique d'execution | Retour paper, fix, re-test |
| 3+ strategies kill-switchees | Retour paper, review |
| Reconciliation divergence > $100 | Retour paper, investigation |

---

## 12. NIVEAUX D'ALERTE

> Source : `core/alerting_live.py`
> Toutes les alertes live sont prefixees `[LIVE]`. Throttling : 1 alerte par type par 5 min.

### INFO (Telegram, pas de son)

- Trade ouvert/ferme
- Rapport journalier automatique
- Heartbeat 30 min (preuve de vie du worker)

### WARNING (Telegram avec son)

- Slippage > 2x moyenne backtest
- Margin > 70%
- Drawdown > 1% journalier
- Signal de strategie ignore (filtre de risque)
- Reconnexion broker apres deconnexion

### CRITICAL (Telegram + channel backup)

- Kill switch active
- Broker deconnecte > 5 min
- Reconciliation avec divergence
- Drawdown > 2% journalier
- Margin > 85%
- Worker crash
- Mode autonome : reduction automatique executee

---

## 13. DELEVERAGING PROGRESSIF

> Source : `config/limits_live.yaml` section `deleveraging`
> Activiation automatique par `LiveRiskManager`.

| Niveau | Seuil DD | Action | Montant ($10K) |
|:------:|:--------:|--------|:--------------:|
| 1 | -1.0% | Reduire 30% toutes positions | Apres -$100 |
| 2 | -1.5% | Reduire 50% toutes positions | Apres -$150 |
| 3 | -2.0% | Fermer TOUT + kill switch | Apres -$200 |

**En mode autonome** (voir section 14), les seuils sont plus agressifs :

| Niveau | Seuil DD | Action |
|:------:|:--------:|--------|
| 1 | -1.0% | Reduire 30% |
| 2 | -1.5% | Reduire 50% |
| 3 | -2.0% | Fermer tout + kill switch |

---

## 14. MODE AUTONOME (absence 48-72h)

> Source : `core/autonomous_mode.py`
> Pour les weekends prolonges, ski, maladie, voyage.

### Activation

Via Telegram ou code :
```
/autonomous ON 72h
```

### Ce que fait le mode autonome

1. Continue le trading avec des limites **plus strictes**
2. Auto-reduce sur drawdown (plus agressif que le mode normal)
3. Auto-pause les strategies sur anomalie
4. Verifie que **toutes les positions ont des bracket orders** (SL/TP broker-side)
5. Bloque les nouveaux trades si une alerte CRITICAL est non-resolue
6. Genere un rapport detaille pour review au retour

### Pre-checklist avant activation

- [ ] `/health` — tout vert
- [ ] Toutes les positions ont des bracket orders
- [ ] Kill switch arme (pas desactive)
- [ ] Pas d'alerte CRITICAL en cours
- [ ] Pas d'evenement marche majeur prevu (FOMC, ECB, NFP)

### Au retour

1. Lire le rapport autonome complet
2. Verifier les events : trades executes, reductions, pauses
3. Verifier reconciliation
4. Desactiver le mode autonome : `/autonomous OFF`

---

## 15. SCALING GATES

> Source : `config/scaling_gates.yaml`
> Evaluation a la fin de chaque mois.

### Gate M1 ($10K → $15K)

| KPI | Seuil | Statut |
|-----|:-----:|:------:|
| Trades executes | >= 50 | _ |
| Sharpe 30j | >= 0.5 | _ |
| Max drawdown | < 5% | _ |
| Max single loss | < 2% | _ |
| Slippage ratio | < 3x | _ |
| Bugs critiques | 0 | _ |
| Erreurs reconciliation | 0 | _ |
| Qualite execution | >= 90% | _ |

**PASS** : ajouter $5K, avancer a PHASE_2 (levier 2x)
**FAIL** : maintenir $10K, identifier et corriger les problemes
**ABORT** : si perte > $500 ou bug critique → retour paper 30 jours

### Gate M2 ($15K → $20K)

| KPI | Seuil |
|-----|:-----:|
| Trades executes | >= 100 |
| Sharpe 60j | >= 0.8 |
| Max drawdown | < 5% |
| Ratio cout/rendement | < 25% |
| Ratio Sharpe live/paper | >= 0.5 |

**PASS** : ajouter $5K, activer strategies US (Day-of-Week, VIX Short)

### Gate M3 ($20K → $25K)

| KPI | Seuil |
|-----|:-----:|
| Trades executes | >= 150 |
| Sharpe 90j | >= 1.0 |
| Max drawdown | < 5% |
| Strategies live | >= 6 |

**PASS** : capital $25-30K, levier 2.5x, plus de contrainte PDT

---

## RAPPELS CRITIQUES

> **Ne jamais violer ces regles, meme sous pression.**

1. **PAPER_TRADING=true** doit etre la valeur par defaut. Le mode live est une activation deliberee.
2. **Tout ordre passe par le pipeline** (`_authorized_by` guard dans `AlpacaClient` et `IBKRAdapter`).
3. **Bracket orders obligatoires** : SL/TP broker-side sur chaque position live.
4. **Reconciliation quotidienne** : state interne = broker. Si divergence, broker = verite.
5. **Kill switch toujours arme** : ne jamais le desactiver sauf pour maintenance planifiee.
6. **Pas de trading live pendant les evenements macro majeurs** (FOMC, ECB rate decision, NFP) sauf strategies specifiquement concues pour.
7. **Logs d'audit** dans `logs/risk_audit/` : chaque decision de risque est enregistree.
8. **Ne jamais augmenter le levier** sans avoir passe la gate correspondante.
9. **En cas de doute, fermer.** Mieux vaut rater un gain que subir une perte non-maitrisee.
