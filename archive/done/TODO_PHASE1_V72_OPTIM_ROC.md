# TODO PHASE 1 — V7.2 OPTIMISATION ROC + AUDIT FINAL
## IBKR Only | $10K | 6 stratégies live semaine 1, 8 semaine 2
### Date : 27 mars 2026 | Post-audit V7.1 (27 bugs corrigés)
### Objectif : maximiser le ROC phase 1 sans augmenter le risque

---

## INSTRUCTIONS AGENT

```
CE DOCUMENT OPTIMISE LE ROC DE LA PHASE 1 POST-AUDIT V7.1.

CONTEXTE :
- V7.1 : audit 14 fichiers, 27 bugs corrigés, 10 stratégies archivées
- Broker : IBKR ONLY (pas d'Alpaca en phase 1)
- Capital : $10K
- Objectif : gate M1 en 3-4 semaines avec un ROC optimisé

CHANGEMENTS CLÉS VS V7.1 :
- DROP des 3 borderline US (commissions IBKR les tuent)
- 6 stratégies live semaine 1 : 5 FX + EU Gap Open
- Futures MCL + MES en live dès jour 5 (pas semaine 2)
- Soft launch raccourci : 5 jours au lieu de 7
- EU Gap Open en 1/4 Kelly dès soft launch (intraday, capital libéré chaque soir)
- Trailing stop dynamique FX (boost fréquence trades)
- Signal frequency 1H sur heures de pic FX
- Kill switch calibré par stratégie (pas seuil unique -2%)
- Monitoring latence continu (pas one-shot)
- Tests intégration mode autonome renforcés
- Gate M1 ajusté : 15 trades / 21 jours

SÉQUENCE :
1. OPTIM-001 à 006 : optimisations ROC
2. SAFE-001 à 004 : renforcements sécurité
3. DRILL-002 + DRILL-003 : tests quasi-bloquants
4. LIVE : soft launch jour 4-5
5. DRILL-001 : fire drill 72h en parallèle

VOLUME CIBLE :
- Semaine 1 : 5 FX + EU Gap = 27-36 trades/mois
- Semaine 2+ : + MCL + MES = 47-64 trades/mois
- Gate M1 (15 trades) : atteignable en ~12-15 jours
```

---

## RÉSUMÉ VOLUME LIVE — IBKR ONLY

```
SEMAINE 1 (soft launch, 5 jours) :
┌──────────────────────────┬──────────┬──────────────┬──────────────────────┐
│ Stratégie                │ Freq/mois│ Sizing       │ Notes                │
├──────────────────────────┼──────────┼──────────────┼──────────────────────┤
│ EUR/USD Trend            │ 4-6      │ 1/8 Kelly    │ Signal 1H heures pic │
│ EUR/GBP Mean Reversion   │ 3-4      │ 1/8 Kelly    │ Signal 1H heures pic │
│ EUR/JPY Carry            │ 6-8      │ 1/8 Kelly    │ Signal 1H heures pic │
│ AUD/JPY Carry            │ 6-8      │ 1/8 Kelly    │ Signal 1H Asie       │
│ GBP/USD Trend            │ 3-4      │ 1/8 Kelly    │ Signal 1H heures pic │
│ EU Gap Open              │ 10-12    │ 1/4 Kelly    │ Intraday, capital    │
│                          │          │              │ libéré chaque soir   │
├──────────────────────────┼──────────┼──────────────┼──────────────────────┤
│ TOTAL SEMAINE 1          │ 32-42    │              │                      │
└──────────────────────────┴──────────┴──────────────┴──────────────────────┘

AJOUT JOUR 5+ (si paper futures OK) :
┌──────────────────────────┬──────────┬──────────────┬──────────────────────┐
│ MCL Brent Lag Futures    │ 15-20    │ 1/8 Kelly    │ Margin $600 seulement│
│ MES Trend Following      │ 5-8      │ 1/8 Kelly    │ Margin $1,400        │
├──────────────────────────┼──────────┼──────────────┼──────────────────────┤
│ TOTAL JOUR 5+            │ 52-70    │              │                      │
└──────────────────────────┴──────────┴──────────────┴──────────────────────┘

MARGIN SEMAINE 1 :
  5 FX × ~$360 margin = ~$1,800 (18%)
  EU Gap Open : ~$2,000 intraday (libéré chaque soir)
  Cash libre : ~$8,200 (82%) le soir, ~$6,200 (62%) en journée

MARGIN JOUR 5+ (avec futures) :
  FX : ~$1,800
  EU Gap : ~$2,000 intraday
  MCL : ~$600
  MES : ~$1,400
  Cash libre : ~$6,200 (62%) le soir, ~$4,200 (42%) en journée
  → Conservateur. Min cash 20% respecté.
```

---

## OPTIM — OPTIMISATIONS ROC

---

### □ OPTIM-001 — Drop borderline US, IBKR only
```yaml
priorité: P0
temps: 1h
dépendances: aucune
validation: QUANT AUDITOR
agent: QUANT EXPERT
```
**Problème** : Les 3 stratégies borderline US (Late Day MR, Failed Rally Short,
EOD Sell Pressure) étaient prévues sur Alpaca ($0 commission). Sur IBKR, les
commissions US equity ($1 min RT) tuent le P&L sur des positions de $500
(seizième-Kelly). Règle empirique #1 : "> 200 trades/6m + position < $5K = mort".

**Décision** : Retirer les 3 borderline US du live set phase 1.
Elles restent en paper pour collecte de données.
Réactivation possible en phase 2 si Alpaca ajouté ou si sizing augmente.

**Impact** :
- Volume : -12 à -19 trades/mois → compensé par OPTIM-002 et OPTIM-003
- Margin libérée : +$1,500 disponible pour futures
- Complexité : -3 stratégies à monitorer, 1 seul broker
- Risk manager : plus besoin de gérer SIZING_OVERRIDES seizième-Kelly

**Fichiers** :
- `config/strategies_live.yaml` (modifier : borderline → paper only)
- `core/leverage_manager.py` (simplifier : retirer seizième-Kelly du live)

---

### □ OPTIM-002 — Signal frequency 1H sur heures de pic FX
```yaml
priorité: P0
temps: 4h
dépendances: aucune
validation: QUANT AUDITOR
agent: FX EXPERT
```
**Problème** : Les 5 stratégies FX évaluent les signaux toutes les 4H uniformément.
Chaque paire a des heures de pic de volatilité différentes. En évaluant toutes
les 4H, on rate des points d'entrée pendant les heures les plus actives.

**Fix** : Passer en évaluation 1H pendant les heures de pic, garder 4H le reste.

```yaml
# config/fx_signal_schedule.yaml
fx_signal_frequency:
  EUR_USD:
    peak_hours: "07:00-17:00 CET"    # Session Londres + overlap NY
    peak_frequency: "1H"
    off_peak_frequency: "4H"
  EUR_GBP:
    peak_hours: "08:00-16:30 CET"    # Session Londres
    peak_frequency: "1H"
    off_peak_frequency: "4H"
  EUR_JPY:
    peak_hours: "07:00-16:00 CET"    # Tokyo PM + Londres
    peak_frequency: "1H"
    off_peak_frequency: "4H"
  AUD_JPY:
    peak_hours: "00:00-08:00 CET"    # Session Asie
    peak_frequency: "1H"
    off_peak_frequency: "4H"
  GBP_USD:
    peak_hours: "08:00-17:00 CET"    # Londres + overlap NY
    peak_frequency: "1H"
    off_peak_frequency: "4H"
```

**Impact estimé** : +30-50% de signaux évalués pendant les heures les plus
volatiles → +3-5 trades/mois par paire → +15-25 trades/mois total FX.

**Risque** : Plus de signaux ≠ meilleurs signaux. Il faut vérifier en backtest
rapide que la fréquence 1H ne génère pas plus de faux signaux.

**Validation agent FX EXPERT** :
```
□ Backtest rapide 6 mois : comparer signal 4H uniforme vs 1H heures de pic
□ Vérifier que le win rate ne baisse pas de plus de 5%
□ Vérifier que le nombre de trades augmente de > 20%
□ Si win rate baisse > 5% → garder 4H, rejeter l'optimisation
□ Si nombre de trades n'augmente pas > 20% → pas d'impact, rejeter
```

**Fichiers** :
- `config/fx_signal_schedule.yaml` (nouveau)
- `core/fx_live_adapter.py` (modifier : schedule-aware signal evaluation)
- `tests/test_fx_signal_schedule.py` (nouveau)

---

### □ OPTIM-003 — Trailing stop dynamique FX
```yaml
priorité: P0
temps: 6h
dépendances: aucune
validation: QUANT AUDITOR + RISK AUDITOR
agent: FX EXPERT + RISK EXPERT
```
**Problème** : Les stratégies FX swing ont des TP/SL fixes. Si le trade va dans
le bon sens (+80 pips) mais que le TP est à 120 pips, le trade peut revenir
à 0 ou toucher le SL. Un trailing stop verrouille les gains partiels.

**Fix** : Trailing stop ATR-based qui s'active après un profit minimum.

```python
class FXTrailingStop:
    """
    Trailing stop dynamique pour FX swing.
    S'active après un profit minimum de 1.5x ATR.
    Trail distance = 1.0x ATR (plus serré que le SL initial de 2x ATR).
    """
    def __init__(self, activation_atr=1.5, trail_atr=1.0):
        self.activation_atr = activation_atr
        self.trail_atr = trail_atr
    
    def update(self, position, current_price, current_atr):
        profit_pips = self.calc_profit_pips(position, current_price)
        activation_pips = self.activation_atr * current_atr
        
        if profit_pips >= activation_pips:
            # Trailing stop activé
            trail_distance = self.trail_atr * current_atr
            new_stop = current_price - (trail_distance * position.direction)
            
            # Ne jamais reculer le stop
            if self.is_better_stop(new_stop, position.current_stop, position.direction):
                self.update_bracket_stop(position, new_stop)
                self.log_trail_update(position, new_stop, profit_pips)
```

**Impact estimé** :
- +30-50% de fréquence de trades (sorties plus fréquentes → re-entries)
- Win rate augmente (gains partiels verrouillés)
- P&L moyen par trade diminue légèrement (sorties avant TP)
- P&L total augmente (plus de trades gagnants)

**Validation agent FX EXPERT** :
```
□ Backtest 12 mois EUR/USD : trailing stop vs SL/TP fixes
□ Comparer : nombre de trades, win rate, P&L total, max drawdown
□ Le trailing stop doit produire : plus de trades ET P&L total >= fixe
□ Si P&L total < 80% du P&L fixe → rejeter l'optimisation
□ Tester 3 paramétrisations : (1.0, 0.8), (1.5, 1.0), (2.0, 1.2)
□ Choisir la meilleure sur les 5 paires FX
```

**Validation agent RISK EXPERT** :
```
□ Le trailing stop ne doit pas créer de position sans protection
□ Pendant la mise à jour du bracket IBKR, la position garde l'ancien stop
□ Si la mise à jour échoue → alerte + garder l'ancien stop
□ Pre-weekend check compatible avec trailing stop
```

**Fichiers** :
- `core/fx_trailing_stop.py` (nouveau)
- `core/broker/ibkr_bracket.py` (modifier : update_stop method)
- `tests/test_fx_trailing_stop.py` (nouveau, 10+ tests)
- `backtests/fx_trailing_vs_fixed.py` (nouveau, validation)

---

### □ OPTIM-004 — EU Gap Open en 1/4 Kelly dès soft launch
```yaml
priorité: P0
temps: 1h
dépendances: aucune
validation: RISK AUDITOR
agent: RISK EXPERT
```
**Problème** : EU Gap Open est en 1/8 Kelly comme toutes les stratégies du soft
launch. Mais c'est une stratégie intraday — le capital est libéré chaque soir.
Le risque overnight est zéro. Avec un Sharpe de 8.56 et WF 4/4 PASS, c'est
la stratégie la plus validée du portefeuille.

**Fix** : Passer EU Gap Open en 1/4 Kelly dès le soft launch.

```yaml
# config/leverage_schedule.yaml — modifier
SOFT_LAUNCH:
  default_kelly: 0.125          # 1/8 Kelly pour FX swing
  overrides:
    eu_gap_open:
      kelly_fraction: 0.25      # 1/4 Kelly — intraday, pas de risque overnight
      max_position_pct: 8.0     # $800 max (vs $400 en 1/8)
      rationale: "Intraday only, WF 4/4, capital libéré chaque soir"
```

**Validation agent RISK EXPERT** :
```
□ Vérifier que la position EU Gap est fermée CHAQUE SOIR (EOD check)
□ Si une position EU Gap est encore ouverte à 17:35 CET → alerte CRITICAL
□ Le 1/4 Kelly sur $10K donne ~$4,000 position → margin ~$400 intraday
□ Impact margin : +$200 intraday (de $200 en 1/8 à $400 en 1/4)
□ Le risk manager intraday doit vérifier le close EOD
```

**Fichiers** :
- `config/leverage_schedule.yaml` (modifier)

---

### □ OPTIM-005 — Accélérer futures à jour 5
```yaml
priorité: P0
temps: 2h (planification) + exécution BOOST-004 existant
dépendances: HARDEN-001, HARDEN-004
validation: EXECUTION AUDITOR + QUANT AUDITOR
agent: FUTURES EXPERT
```
**Problème** : Le plan V7.1 prévoit futures en live "semaine 2". Avec 6 stratégies
IBKR only, chaque jour compte pour atteindre le gate M1.
MCL Brent Lag a 729 trades backtest, WF 4/5 PASS, margin $600. C'est le
meilleur ratio edge/margin du portefeuille.

**Fix** : Futures paper dès jour 1. Si 3 trades MCL + 2 trades MES passent proprement
en paper sur 4 jours → live dès jour 5.

```
JOUR 1-4 :
  LIVE : 5 FX + EU Gap Open (soft launch)
  PAPER : MCL + MES (validation technique)
  Checklist paper :
    □ 3+ trades MCL paper exécutés et réconciliés
    □ 2+ trades MES paper exécutés et réconciliés
    □ Brackets futures survivent à la maintenance window (16:00-17:00 CT)
    □ Margin tracker cohérent avec IBKR paper
    □ Roll manager simulé (MCL mensuel)
    □ 0 bug critique

JOUR 5 (si checklist PASS) :
  LIVE : 5 FX + EU Gap Open + MCL + MES
  Volume : 52-70 trades/mois
  Margin totale : ~$4,200 (42%) en journée, ~$3,800 (38%) le soir
```

**Validation agent FUTURES EXPERT** :
```
□ MCL contract front month correct pour la date
□ MES contract front month correct
□ Tick value MCL = $10/tick, MES = $1.25/tick
□ Bracket futures : STP LMT avec buffer correct
□ Maintenance margin < initial margin vérifié
□ Le roll manager ne roll pas pendant une position ouverte
```

**Fichiers** :
- `docs/launch_plan_v3.md` (modifier : jour 5 au lieu de semaine 2)
- `config/strategies_live.yaml` (modifier : MCL + MES enabled day 5)

---

### □ OPTIM-006 — Soft launch raccourci à 5 jours
```yaml
priorité: P0
temps: 1h
dépendances: aucune
validation: RISK AUDITOR
agent: RISK EXPERT
```
**Problème** : Le soft launch de 7 jours retarde le passage en 1/4 Kelly.
Avec 6 stratégies IBKR, on peut avoir 5+ trades en 3-4 jours.

**Fix** : Soft launch = 5 jours minimum (au lieu de 7), passage automatique
si les conditions sont remplies.

```yaml
# config/leverage_schedule.yaml — modifier
SOFT_LAUNCH:
  duration_min_days: 5            # Réduit de 7 → 5
  condition: |
    trades >= 5 AND
    max_dd < 2% AND
    bugs_critiques == 0 AND
    reconciliation_errors == 0 AND
    calendar_days >= 5
  auto_upgrade: true              # Passage automatique si conditions remplies
```

**Gain** : 2 jours de plus en 1/4 Kelly = ~2 jours de ROC supplémentaire.
Sur un mois, ça représente ~7% de temps en plus à pleine capacité.

**Fichiers** :
- `config/leverage_schedule.yaml` (modifier)


---

## SAFE — RENFORCEMENTS SÉCURITÉ

---

### □ SAFE-001 — Kill switch calibré par stratégie
```yaml
priorité: P0
temps: 4h
dépendances: aucune
validation: RISK AUDITOR
agent: RISK EXPERT + QUANT EXPERT
```
**Problème** : Les stratégies live utilisent un seuil kill switch par défaut de
-2%. Ce seuil unique est inadapté :
- FX swing : trop serré (un move de 130 pips = -1.5%, normal en swing)
- EU Gap intraday : trop lâche (-2% en intraday = catastrophe)
- Futures micro : pas calibré du tout
- Si 3 stratégies atteignent -2% simultanément = -6% drawdown > gate M1 max 5%

**Fix** : Seuils kill switch différenciés par type de stratégie + kill switch
global portefeuille.

```yaml
# config/kill_switch_thresholds.yaml
strategy_thresholds:
  # FX Swing — holding 1-30j, mouvements larges normaux
  fx_swing:
    strategies: [eurusd_trend, eurgbp_mr, eurjpy_carry, audjpy_carry, gbpusd_trend]
    per_strategy_max_loss_pct: 3.0    # -$300 par stratégie
    rationale: "Move de 200 pips = -$250 sur $12K notionnel. Normal en swing."
  
  # EU Intraday — holding < 1 jour, drawdown doit être limité
  eu_intraday:
    strategies: [eu_gap_open]
    per_strategy_max_loss_pct: 1.5    # -$150 par stratégie
    rationale: "Intraday. Si -1.5% en une session → quelque chose ne va pas."
  
  # Futures Micro — calibré par contrat
  futures_micro:
    strategies: [mcl_brent_lag, mes_trend]
    per_strategy_max_loss_pct: 2.5    # -$250 par stratégie
    rationale: "MCL = 25 ticks adverse ($250). MES = 20 points ($25). Raisonnable."

# Kill switch GLOBAL — portefeuille entier
portfolio_kill_switch:
  daily_max_loss_pct: 4.0             # -$400/jour → fermer tout
  weekly_max_loss_pct: 5.0            # -$500/semaine → fermer tout (= gate M1 abort)
  hourly_max_loss_pct: 2.5            # -$250/heure → pause 1h

# Interaction kill switch stratégie vs portfolio :
# Si kill switch stratégie → fermer CETTE stratégie, les autres continuent
# Si kill switch portfolio → fermer TOUT, alerte CRITICAL, intervention manuelle requise
```

**Validation agent RISK EXPERT** :
```
□ Simuler 3 stratégies FX atteignant -3% chacune simultanément
  → kill switch stratégie les ferme individuellement
  → le portfolio est à -9% si les 3 ne sont pas corrélées (improbable)
  → mais le kill switch portfolio à -4% daily intervient AVANT
□ Simuler EU Gap Open à -1.5% en 30 minutes → kill switch intraday
□ Simuler MCL à -2.5% → kill switch futures
□ Vérifier que le kill switch portfolio overrride les seuils stratégie
□ Vérifier que le hourly kill switch est bien vérifié (bug V7.1 corrigé)
```

**Validation agent QUANT EXPERT** :
```
□ Monte Carlo 10K simulations sur les 6 stratégies live
□ Calibrer les seuils pour < 5% de faux positifs chacun
□ Si les seuils proposés donnent > 5% FP → ajuster
□ Rapport : seuil optimal, FP rate, tail risk
```

**Fichiers** :
- `config/kill_switch_thresholds.yaml` (nouveau)
- `core/kill_switch_live.py` (modifier : seuils par stratégie + portfolio)
- `tests/test_kill_switch_calibrated.py` (nouveau, 8+ tests)

---

### □ SAFE-002 — Monitoring latence continu
```yaml
priorité: P0
temps: 2h
dépendances: VPS Hetzner provisionné
validation: INFRASTRUCTURE AUDITOR
agent: INFRA EXPERT
```
**Problème** : LAUNCH-003 mesure la latence une seule fois. La latence
Railway → Hetzner varie selon l'heure et la charge réseau. Un pic de
latence invisible = slippage invisible.

**Fix** : Ping continu toutes les 5 minutes intégré au healthcheck existant.

```python
# core/monitoring.py — ajouter au healthcheck existant

class LatencyMonitor:
    """
    Ping Railway → Hetzner toutes les 5 minutes.
    Log latence moyenne, P95, P99 sur des fenêtres de 1h.
    Alerte si latence dépasse les seuils.
    """
    WINDOW_SIZE = 12  # 12 mesures = 1h (toutes les 5 min)
    
    def __init__(self):
        self.history = deque(maxlen=self.WINDOW_SIZE * 24)  # 24h d'historique
    
    def measure(self):
        start = time.time()
        response = self.ping_ib_gateway()
        latency_ms = (time.time() - start) * 1000
        self.history.append(latency_ms)
        
        # Calculer les stats sur la dernière heure
        recent = list(self.history)[-self.WINDOW_SIZE:]
        p95 = np.percentile(recent, 95)
        p99 = np.percentile(recent, 99)
        
        if p95 > 300:
            self.alert_critical(f"Latence P95 = {p95:.0f}ms > 300ms")
        elif p95 > 150:
            self.alert_warning(f"Latence P95 = {p95:.0f}ms > 150ms")
        
        return {"mean": np.mean(recent), "p95": p95, "p99": p99}
```

**Fichiers** :
- `core/monitoring.py` (modifier : ajouter LatencyMonitor)
- `tests/test_latency_monitor.py` (nouveau, 5 tests)

---

### □ SAFE-003 — Auto-disable stratégie si Sharpe < 0 après 10 trades
```yaml
priorité: P0
temps: 3h
dépendances: aucune
validation: QUANT AUDITOR + RISK AUDITOR
agent: QUANT EXPERT
```
**Problème** : Les Sharpe EU backtest sont suspects (8-15). Si une stratégie
a un Sharpe réel de -0.5 après 10 trades live, elle dilue le portefeuille.
Il n'y a pas de mécanisme automatique de désactivation basé sur la performance live.

**Fix** : Auto-disable qui met une stratégie en paper-only si sa performance
live est mauvaise.

```python
class LivePerformanceGuard:
    """
    Surveille la performance live par stratégie.
    Auto-disable si les conditions sont remplies.
    """
    THRESHOLDS = {
        "min_trades_for_eval": 10,      # Évaluer après 10 trades
        "sharpe_disable": 0.0,          # Sharpe < 0 → disable
        "win_rate_disable": 0.30,       # Win rate < 30% → disable
        "max_consecutive_losses": 5,    # 5 pertes consécutives → alerte + review
        "slippage_disable": 5.0,        # Slippage > 5x backtest → disable
    }
    
    def evaluate(self, strategy_name, live_trades):
        if len(live_trades) < self.THRESHOLDS["min_trades_for_eval"]:
            return CONTINUE
        
        sharpe = self.calc_sharpe(live_trades)
        win_rate = self.calc_win_rate(live_trades)
        slippage_ratio = self.calc_slippage_ratio(live_trades)
        
        if sharpe < self.THRESHOLDS["sharpe_disable"]:
            return DISABLE, f"Sharpe live = {sharpe:.2f} < 0 après {len(live_trades)} trades"
        
        if win_rate < self.THRESHOLDS["win_rate_disable"]:
            return DISABLE, f"Win rate = {win_rate:.1%} < 30%"
        
        if slippage_ratio > self.THRESHOLDS["slippage_disable"]:
            return DISABLE, f"Slippage = {slippage_ratio:.1f}x backtest"
        
        return CONTINUE
```

**Comportement** :
- DISABLE = la stratégie passe en paper-only automatiquement
- Alerte Telegram avec les stats
- Marc peut la réactiver manuellement après diagnostic
- Log dans le trade journal pour la trace

**Validation agent QUANT EXPERT** :
```
□ Simuler une stratégie avec Sharpe -0.5 après 12 trades → DISABLE
□ Simuler une stratégie avec win rate 25% après 10 trades → DISABLE
□ Simuler une stratégie avec slippage 6x backtest → DISABLE
□ Vérifier que l'évaluation ne se fait pas avant 10 trades
□ Vérifier que la réactivation manuelle fonctionne
□ Vérifier que la stratégie continue en paper après disable
```

**Fichiers** :
- `core/live_performance_guard.py` (nouveau)
- `tests/test_live_performance_guard.py` (nouveau, 8+ tests)

---

### □ SAFE-004 — Tests intégration mode autonome renforcés
```yaml
priorité: P0
temps: 4h
dépendances: aucune
validation: OPS AUDITOR + RISK AUDITOR
agent: INFRA EXPERT + RISK EXPERT
```
**Problème** : Le mode autonome 72h a 48 tests unitaires mais seulement
5 tests d'intégration. Les interactions entre AutoReducer, AnomalyDetector
et SafetyChecker ne sont pas couvertes.

**Scénarios à tester** :

```python
# tests/test_autonomous_integration.py — 8 nouveaux tests

def test_conflict_reducer_vs_anomaly():
    """AutoReducer dit réduire 30% mais AnomalyDetector dit normal → qui gagne ?"""
    # Attendu : SafetyChecker arbitre, AutoReducer a priorité (conservateur)
    
def test_kill_switch_during_reduce():
    """SafetyChecker kill switch pendant que AutoReducer réduit → deadlock ?"""
    # Attendu : kill switch override tout, pas de deadlock
    
def test_telegram_down_during_autonomous():
    """Perte de connexion Telegram → alertes bufferisées ?"""
    # Attendu : alertes dans un buffer local, retry toutes les 5 min

def test_worker_crash_restart_autonomous():
    """Worker crash + systemd restart → état restauré correctement ?"""
    # Attendu : état autonome restauré depuis le fichier atomique
    
def test_72h_accelerated_random_events():
    """Simulation 72h accélérée avec événements aléatoires"""
    # Drawdown aléatoire, spike latence, déconnexion broker, crash worker
    # Attendu : le système gère tout sans intervention humaine
    
def test_multiple_strategies_disable_autonomous():
    """3 stratégies auto-disabled pendant le mode autonome"""
    # Attendu : les stratégies restantes continuent, pas de cascade
    
def test_margin_call_during_autonomous():
    """Margin call IBKR pendant le mode autonome"""
    # Attendu : AutoReducer ferme les positions les plus coûteuses en margin
    
def test_weekend_gap_autonomous():
    """Gap week-end adverse sur toutes les positions FX"""
    # Attendu : brackets exécutés, réconciliation correcte lundi matin
```

**Validation agent RISK EXPERT** :
```
□ Tous les 8 scénarios passent
□ Pas de deadlock entre les 3 composants
□ L'état est toujours restaurable après un crash
□ Le kill switch n'est jamais bloqué
```

**Fichiers** :
- `tests/test_autonomous_integration.py` (nouveau, 8 tests)


---

## RECALIBRATION — ATTENTES RÉALISTES

---

### □ RECAL-001 — Recalibrer Sharpe cible et gate M1
```yaml
priorité: P0
temps: 2h
dépendances: aucune
validation: QUANT AUDITOR
agent: QUANT EXPERT
```
**Problème** : Le Sharpe cible portefeuille de 3.5 est irréaliste en live.
Dégradation typique : 40-60% du backtest. Les meilleurs hedge funds quant
font 1.5-2.5. Le gate M1 demande un Sharpe > 0.5 en secondaire — c'est
réaliste mais pas statistiquement significatif sur 15-20 trades.

**Fix** :

```yaml
# Sharpe cibles recalibrées
sharpe_targets:
  backtest_portfolio: 3.5          # Inchangé (référence)
  live_expected_range: [1.0, 2.0]  # Réaliste après dégradation
  live_minimum_acceptable: 0.5     # En dessous = problème
  
# Gate M1 ajusté
gate_M1:
  conditions_primaires:
    min_calendar_days: 21
    min_trades: 15                  # Réduit de 20 → 15 (IBKR only)
    min_strategies_active: 3
    max_drawdown_pct: 5.0
    max_single_loss_pct: 2.0
    bugs_critiques: 0
    reconciliation_errors: 0
  
  conditions_secondaires:
    min_count: 3                    # 3 sur 5 requises
    checks:
      - sharpe_period: "> 0.3"     # Réduit de 0.5 → 0.3 (réaliste sur 15 trades)
      - win_rate: "> 0.42"         # Réduit de 0.45 → 0.42
      - profit_factor: "> 1.1"     # Réduit de 1.2 → 1.1
      - slippage_ratio: "< 3.0"    # Inchangé
      - execution_quality: "> 0.85" # Inchangé
  
  # NOTE : Le Sharpe n'est PAS un critère primaire.
  # Sur 15 trades, un Sharpe de 0.3 n'est pas significatif.
  # Les critères primaires fiables sur petit échantillon :
  # max_drawdown, bugs, réconciliation, qualité d'exécution.
```

**Validation agent QUANT EXPERT** :
```
□ Simuler 1000 portfolios avec les 6 stratégies live
□ Distribution des Sharpe sur 21 jours avec 15 trades
□ Vérifier que Sharpe > 0.3 est atteignable avec > 60% de probabilité
□ Vérifier que le gate M1 ne bloque pas un portefeuille sain
□ Vérifier que le gate M1 bloque un portefeuille en perte
```

**Fichiers** :
- `config/scaling_gates.yaml` (modifier)
- `docs/sharpe_calibration_live.md` (nouveau : analyse de dégradation)

---

## DRILL — TESTS PRÉ-LIVE (INCHANGÉS VS V7.1)

---

### □ DRILL-002 — Test restauration backup complet — QUASI-BLOQUANT
```yaml
priorité: P0-QUASI-BLOQUANT
temps: 2h
dépendances: aucune
validation: INFRASTRUCTURE AUDITOR
agent: INFRA EXPERT
```
Protocole inchangé vs V7.1. Cible : restauration < 30 min, 0 donnée perdue.

---

### □ DRILL-003 — Test kill switch end-to-end (paper) — QUASI-BLOQUANT
```yaml
priorité: P0-QUASI-BLOQUANT
temps: 2h
dépendances: SAFE-001
validation: RISK AUDITOR
agent: RISK EXPERT
```
Protocole inchangé vs V7.1. 4 tests (auto, Telegram, TWS, worker down).
**Ajout V7.2** : tester avec les seuils calibrés par stratégie (SAFE-001).

---

### □ DRILL-001 — Fire drill 72h paper — NON BLOQUANT
```yaml
priorité: P0 mais NON BLOQUANT
temps: 4h setup + 72h observation
dépendances: SAFE-004
validation: OPS AUDITOR + RISK AUDITOR
agent: INFRA EXPERT
```
Protocole inchangé vs V7.1. Tourne en parallèle du live.
**Ajout V7.2** : inclure les 8 scénarios SAFE-004 comme checks supplémentaires.

---

## CHECKLIST COMPLÈTE V7.2

```
OPTIM (amélioration ROC) :
□ OPTIM-001  Drop borderline US, IBKR only                    (1h)
□ OPTIM-002  Signal frequency 1H sur heures de pic FX         (4h)
□ OPTIM-003  Trailing stop dynamique FX                        (6h)
□ OPTIM-004  EU Gap Open en 1/4 Kelly dès soft launch          (1h)
□ OPTIM-005  Accélérer futures à jour 5                        (2h)
□ OPTIM-006  Soft launch raccourci à 5 jours                   (1h)

SAFE (renforcements sécurité) :
□ SAFE-001   Kill switch calibré par stratégie                 (4h)
□ SAFE-002   Monitoring latence continu                        (2h)
□ SAFE-003   Auto-disable stratégie si Sharpe < 0              (3h)
□ SAFE-004   Tests intégration mode autonome renforcés         (4h)

RECALIBRATION :
□ RECAL-001  Recalibrer Sharpe cible et gate M1                (2h)

DRILL (tests pré-live) :
□ DRILL-002  Test backup restore                               (2h)
□ DRILL-003  Test kill switch E2E (avec seuils calibrés)       (2h)
□ DRILL-001  Fire drill 72h paper (non bloquant)               (4h+72h)

TOTAL : 14 tâches | ~38h code + 72h drill passif
```

---

## SÉQUENCE TEMPORELLE V7.2

```
JOUR 1 :
  □ OPTIM-001 (drop borderline, 1h)
  □ OPTIM-004 (EU Gap 1/4 Kelly, 1h)
  □ OPTIM-006 (soft launch 5j, 1h)
  □ SAFE-001 (kill switch calibré, 4h)
  □ RECAL-001 (gate M1, 2h)
  □ Futures paper commence (MCL + MES)
  → 10h de travail

JOUR 2 :
  □ OPTIM-002 (signal frequency FX, 4h)
  □ OPTIM-003 (trailing stop FX, 6h) — dont backtest validation
  □ SAFE-002 (latence monitoring, 2h)
  → 12h de travail (le plus gros jour)

JOUR 3 :
  □ SAFE-003 (auto-disable, 3h)
  □ SAFE-004 (tests autonome, 4h)
  □ OPTIM-005 (plan futures jour 5, 2h)
  □ DRILL-002 (backup restore, 2h) — QUASI-BLOQUANT
  □ DRILL-003 (kill switch E2E, 2h) — QUASI-BLOQUANT
  □ Go/No-Go decision le soir
  → 13h de travail

JOUR 4 — si DRILL-002 + DRILL-003 PASS :
  □ PREMIER TRADE LIVE (soft launch)
  □ 5 FX (1/8 Kelly) + EU Gap Open (1/4 Kelly)
  □ DRILL-001 démarre en parallèle (72h paper)
  □ Futures paper continue

JOUR 5 — si 3+ MCL + 2+ MES paper OK :
  □ FUTURES LIVE (MCL + MES en 1/8 Kelly)
  □ 8 stratégies live, 52-70 trades/mois

JOUR 9 (fin soft launch si conditions remplies) :
  □ Passage 1/4 Kelly sur FX
  □ EU Gap passe en 1/2 Kelly (intraday)
  □ Analyse DRILL-001

SEMAINE 3 :
  □ Gate M1 : 15+ trades cumulés
  □ Évaluation critères primaires + secondaires

SEMAINE 4 :
  □ Décision gate M1 : +$5K ou prolonger 15j
```

---

## AGENTS REQUIS

```
┌─────────────────┬──────────────────────────────────────────────────────┐
│ Agent           │ Tâches                                              │
├─────────────────┼──────────────────────────────────────────────────────┤
│ QUANT EXPERT    │ OPTIM-002 validation, OPTIM-003 validation,         │
│                 │ SAFE-001 calibration MC, SAFE-003 seuils,           │
│                 │ RECAL-001 simulation                                │
├─────────────────┼──────────────────────────────────────────────────────┤
│ FX EXPERT       │ OPTIM-002 backtest, OPTIM-003 backtest + paramétrage│
├─────────────────┼──────────────────────────────────────────────────────┤
│ RISK EXPERT     │ OPTIM-003 risk, OPTIM-004, OPTIM-006,              │
│                 │ SAFE-001, SAFE-004, DRILL-003                       │
├─────────────────┼──────────────────────────────────────────────────────┤
│ FUTURES EXPERT  │ OPTIM-005 validation technique                      │
├─────────────────┼──────────────────────────────────────────────────────┤
│ INFRA EXPERT    │ SAFE-002, SAFE-004, DRILL-001, DRILL-002            │
├─────────────────┼──────────────────────────────────────────────────────┤
│ EXECUTION AGENT │ Implémentation code de toutes les tâches            │
└─────────────────┴──────────────────────────────────────────────────────┘
```

---

## MÉTRIQUES DE SUCCÈS V7.2

```
SEMAINE 1 (soft launch) :
  □ 8+ trades live exécutés
  □ Max drawdown < 2%
  □ 0 bug critique
  □ 0 divergence réconciliation
  □ Latence P95 < 150ms
  □ Kill switch testé et fonctionnel

SEMAINE 2 (futures live) :
  □ 15+ trades live cumulés
  □ 8 stratégies live opérationnelles
  □ Futures margin tracking correct
  □ DRILL-001 PASS

SEMAINE 3-4 (gate M1) :
  □ 15+ trades live
  □ 3+ stratégies ont tradé
  □ Max drawdown < 5%
  □ 3/5 critères secondaires PASS
  □ 0 stratégie auto-disabled (ou disabled avec diagnostic)
  → Si PASS : +$5K → $15K, début phase 2
  → Si FAIL : prolonger 15 jours ou retour paper
```

---

*TODO V7.2 — Optimisation ROC Phase 1 — 27 mars 2026*
*14 tâches | ~38h code + 72h drill | IBKR only | $10K*
*6 stratégies live semaine 1, 8 semaine 2 | 52-70 trades/mois*
*"Chaque dollar de capital et chaque jour de temps doivent travailler."*
