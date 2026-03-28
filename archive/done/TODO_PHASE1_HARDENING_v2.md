# TODO PHASE 1 — HARDENING PRÉ-LIVE (V2 AJUSTÉE)
## Corrections critiques + accélération volume de trades
### Date : 27 mars 2026 | Addendum à TODO XXL LIVE 10K
### Tous les items sont P0 sauf mention contraire

---

## INSTRUCTIONS AGENT

```
CE DOCUMENT CORRIGE LES GAPS IDENTIFIÉS DANS LA V6 ET ACCÉLÈRE LE VOLUME LIVE.

CHANGEMENTS VS V1 :
- Futures (MCL, MES) lancés en paper DÈS JOUR 1 → live semaine 2
- 3 stratégies BORDERLINE US ajoutées en live sizing réduit (seizième-Kelly)
- GBP/USD Trend (FX-002) activé → 5ème paire FX
- EU Gap Open inclus dans le live set phase 1
- DRILL-001 (72h) = NON BLOQUANT, tourne en parallèle du live
- DRILL-002 + DRILL-003 = QUASI-BLOQUANTS (2h chacun, risque asymétrique)

OBJECTIF VOLUME :
- V1 : 3-5 trades live/mois (FX swing seul) → gate M1 impossible
- V2 : 40-60 trades live/mois → gate M1 en 2-3 semaines

SÉQUENCE :
1. FIX les bugs logiques (HARDEN-001 à HARDEN-004)
2. CONFIGURER les stratégies additionnelles (BOOST-001 à BOOST-004)
3. TESTER kill switch + backup (DRILL-002, DRILL-003) — QUASI-BLOQUANT
4. LANCER le soft launch live (LAUNCH-001 à LAUNCH-003)
5. DRILL-001 (72h paper) en parallèle — NON BLOQUANT
6. Semaine 2 : futures live si paper technique OK

VALIDATION :
Chaque tâche HARDEN est validée par le skill expert correspondant.
Aucun trade live tant qu'un HARDEN est en échec.
DRILL-002 et DRILL-003 doivent PASS avant le premier trade live.
DRILL-001 tourne en parallèle — si FAIL, kill switch disponible pour couper.
```

---

## CONTEXTE DES GAPS (V1 — inchangé)

```yaml
gap_1_volume_trades:
  problème: |
    Les 4 stratégies FX tier 1 sont toutes du swing (holding 1-30j).
    Volume attendu : 3-5 trades/mois.
    Gate M1 exige 50 trades minimum → IMPOSSIBLE en FX swing seul.
  impact: Impossible de valider le gate M1, scaling bloqué.
  fix_v2: |
    Ajout futures MCL+MES + borderline US + GBP/USD + EU Gap Open.
    Volume cible : 40-60 trades live/mois.

gap_2_levier_fx_notionnel:
  problème: |
    4 positions FX de $25K notionnel = $100K exposure = 1000% du capital.
    Le risk manager live a max_gross_pct: 120%.
    Il doit distinguer margin FX (~$900/position) vs notionnel ($25K/position).
  impact: Soit le risk manager bloque tout FX, soit il laisse passer un levier dangereux.

gap_3_autonome_non_testé:
  problème: |
    Le mode autonome 72h existe en code mais n'a jamais tourné 72h en paper.
    Aucun fire drill réalisé.
  impact: Bug non détecté = perte d'argent réel pendant l'absence de Marc.
  fix_v2: DRILL-001 NON BLOQUANT, tourne en parallèle du live.

gap_4_bracket_fx_weekend:
  problème: |
    Les bracket orders sont testés pour equities/futures.
    Le comportement FX chez IBKR diffère : gap week-end, stop market vs stop limit.
    Pas de test spécifique FX weekend.
  impact: Stop non exécuté ou slippage massif sur un gap week-end FX.

gap_5_live_vs_paper_sync:
  problème: |
    La comparaison live vs paper (COST-003) nécessite que les MÊMES signaux
    soient générés en live ET en paper simultanément.
    Le dual-mode SETUP-003 garantit-il l'identité des signaux ?
  impact: Comparaison faussée = impossible de mesurer la dégradation live.

gap_6_latence_railway_hetzner:
  problème: |
    Worker Railway → IB Gateway Hetzner = ~50-100ms de latence ajoutée.
    OK pour FX swing, problématique pour futures intraday (phase 2+).
  impact: Slippage additionnel sur les futures, invisible en phase 1.

gap_7_restore_non_testé:
  problème: |
    Le backup quotidien existe. La restauration n'a jamais été testée.
  impact: Perte de données irréversible si crash sans restore fonctionnel.

gap_8_sizing_agressif:
  problème: |
    Démarrer en quart-Kelly dès le jour 1 est trop agressif.
    Aucune donnée live n'existe pour calibrer le Kelly.
  impact: Drawdown amplifié pendant la phase d'apprentissage.
```

---

## VOLUME CIBLE V2 — DÉTAIL PAR STRATÉGIE

```
STRATÉGIES LIVE PHASE 1 (semaine 1 — soft launch) :
┌─────────────────────────────────┬──────────┬───────────────┬─────────────────┐
│ Stratégie                       │ Freq/mois│ Sizing        │ Source          │
├─────────────────────────────────┼──────────┼───────────────┼─────────────────┤
│ EUR/USD Trend                   │ 3-4      │ 1/8 Kelly     │ FX tier 1       │
│ EUR/GBP Mean Reversion          │ 2-3      │ 1/8 Kelly     │ FX tier 1       │
│ EUR/JPY Carry                   │ 5-7      │ 1/8 Kelly     │ FX tier 1       │
│ AUD/JPY Carry                   │ 5-7      │ 1/8 Kelly     │ FX tier 1       │
│ GBP/USD Trend (FX-002)          │ 2-3      │ 1/8 Kelly     │ BOOST-001       │
│ EU Gap Open                     │ 10-12    │ 1/8 Kelly     │ BOOST-003       │
│ Late Day Mean Reversion         │ 5-8      │ 1/16 Kelly    │ BOOST-002       │
│ Failed Rally Short              │ 3-5      │ 1/16 Kelly    │ BOOST-002       │
│ EOD Sell Pressure V2            │ 4-6      │ 1/16 Kelly    │ BOOST-002       │
├─────────────────────────────────┼──────────┼───────────────┼─────────────────┤
│ TOTAL SEMAINE 1                 │ 39-55    │               │                 │
└─────────────────────────────────┴──────────┴───────────────┴─────────────────┘

AJOUT SEMAINE 2 (si paper futures OK) :
┌─────────────────────────────────┬──────────┬───────────────┬─────────────────┐
│ MCL Brent Lag Futures           │ 15-20    │ 1/8 Kelly     │ BOOST-004       │
│ MES Trend Following             │ 5-8      │ 1/8 Kelly     │ BOOST-004       │
├─────────────────────────────────┼──────────┼───────────────┼─────────────────┤
│ TOTAL SEMAINE 2+                │ 59-83    │               │                 │
└─────────────────────────────────┴──────────┴───────────────┴─────────────────┘

Gate M1 (15 trades minimum) : atteignable en ~7-10 jours.
```

---

## HARDEN — CORRECTIONS LOGIQUES

---

### □ HARDEN-001 — Fix risk manager : distinction margin FX vs notionnel + futures margin
```yaml
priorité: P0-BLOQUANT
temps: 8h (6h + 2h futures margin intégré)
dépendances: aucune
validation: RISK AUDITOR
```
**Problème** : Le `LiveRiskManager` utilise `max_gross_pct: 120%` basé sur la valeur des positions. Pour les actions et futures, c'est correct (valeur ≈ exposure). Pour le FX, le levier natif IBKR fait que la valeur notionnelle est 10-30x la margin. 4 positions FX de $25K notionnel = $100K = 1000% du capital de $10K.

**Ajout V2** : Intégrer aussi la logique margin futures dès maintenant (MCL $600, MES $1,400) pour ne pas refaire ce fix en semaine 2.

**Fix** :
```python
class LiveRiskManager:
    def check_gross_exposure(self, new_order, portfolio):
        """
        Calculer l'exposure de 3 façons selon l'instrument :
        - Equities : valeur notionnelle
        - FX : margin utilisée (pas notionnel)
        - Futures : margin initiale requise
        
        Le check gross s'applique sur la MARGIN TOTALE.
        Des checks séparés limitent le notionnel FX et la margin futures.
        """
        # Exposure equities : notionnel classique
        equity_exposure = sum(p.notional for p in portfolio if p.type == "EQUITY")
        
        # Exposure FX : margin utilisée
        fx_margin = sum(p.margin_used for p in portfolio if p.type == "FX")
        
        # Exposure Futures : margin initiale
        futures_margin = sum(p.initial_margin for p in portfolio if p.type == "FUTURES")
        
        # Check 1 : margin totale combinée < max_gross
        total_margin_exposure = equity_exposure + fx_margin + futures_margin
        if total_margin_exposure > self.capital * self.limits.max_gross_pct:
            return REJECTED, "Gross margin exposure exceeded"
        
        # Check 2 : notionnel FX total < max_fx_notional
        fx_notional = sum(p.notional for p in portfolio if p.type == "FX")
        if fx_notional > self.capital * self.limits.max_fx_notional_pct:
            return REJECTED, "FX notional exposure exceeded"
        
        # Check 3 : margin futures totale < max_futures_margin
        if futures_margin > self.capital * self.limits.max_futures_margin_pct:
            return REJECTED, "Futures margin exceeded"
        
        return ACCEPTED
```

**Nouvelles limites dans `config/limits_live.yaml`** :
```yaml
# Limites FX spécifiques
fx_limits:
  max_fx_notional_pct: 1500       # $150K notionnel max (15x capital)
  max_fx_margin_pct: 40           # $4,000 de margin FX max (40% du capital)
  max_single_pair_notional: 40000 # $40K max par paire
  max_single_pair_margin_pct: 15  # $1,500 margin max par paire

# Limites Futures spécifiques (AJOUT V2)
futures_limits:
  max_futures_margin_pct: 35      # $3,500 margin futures max (35% du capital)
  max_single_contract_margin_pct: 20  # $2,000 max par contrat type
  allowed_contracts:              # Whitelist phase 1
    - MCL                         # Micro Crude Oil ($600 margin)
    - MES                         # Micro E-mini S&P ($1,400 margin)
  max_contracts_per_symbol: 2     # Max 2 contrats par symbole

# Limites combinées
combined_limits:
  max_total_margin_pct: 80        # Margin totale (equity + FX + futures) < 80%
  min_cash_pct: 20                # Toujours garder 20% de cash libre
```

**Tests requis** :
```
□ Ordre FX accepté si margin < 40% ET notionnel < 1500%
□ Ordre FX rejeté si margin OK mais notionnel > 1500%
□ Ordre FX rejeté si notionnel OK mais margin > 40%
□ Ordre equity non affecté par les limites FX/futures
□ Ordre futures accepté si margin < 35%
□ Ordre futures rejeté si margin > 35%
□ Ordre futures rejeté si contrat non whitelisté
□ Mix FX + equity + futures : margin combinée < 80%
□ Mix FX + futures : cash libre > 20%
□ 4 positions FX + 2 MCL + 1 MES : tous les checks passent
□ Position qui ferait dépasser le cash minimum → rejetée
```

**Succès** : Le risk manager gère correctement 3 types d'instruments. 11 tests passent.

**Fichiers** :
- `core/risk_manager_live.py` (modifier : ajouter checks FX + futures)
- `config/limits_live.yaml` (modifier : ajouter fx_limits + futures_limits + combined_limits)
- `tests/test_risk_fx_futures_margin.py` (nouveau, 11+ tests)

---

### □ HARDEN-002 — Fix gate M1 : seuil adapté au profil multi-stratégie
```yaml
priorité: P0-BLOQUANT
temps: 3h
dépendances: aucune
validation: QUANT AUDITOR
```
**Problème** : Le gate M1 exige 50 trades minimum. Même avec le boost de volume (40-60 trades/mois), le gate doit être adapté au mix FX swing + futures + borderline US.

**Fix** : Gate M1 avec critères primaires/secondaires/éliminatoires.

```yaml
# config/scaling_gates.yaml — REMPLACER gate_M1

gate_M1:
  description: "Valider le système en conditions réelles — profil multi-stratégie"
  capital_actuel: 10000
  capital_si_pass: 15000
  
  # Conditions PRIMAIRES (toutes requises)
  conditions_primaires:
    min_calendar_days: 21           # 3 semaines minimum
    min_trades: 20                  # Réaliste avec le boost volume
    min_strategies_active: 3        # Au moins 3 stratégies ont tradé
    max_drawdown_pct: 5.0           # -$500 max
    max_single_loss_pct: 2.0        # -$200 max sur un trade
    bugs_critiques: 0               # 0 bug critique
    reconciliation_errors: 0        # 0 divergence non résolue
  
  # Conditions SECONDAIRES (au moins 3 sur 5 requises)
  conditions_secondaires:
    min_count: 3
    checks:
      - sharpe_period: "> 0.5"      # Sharpe positif sur la période
      - win_rate: "> 0.45"          # Win rate raisonnable
      - profit_factor: "> 1.2"      # PF positif
      - slippage_ratio: "< 3.0"     # Slippage < 3x backtest
      - execution_quality: "> 0.85" # 85%+ signaux exécutés correctement
  
  # Conditions ÉLIMINATOIRES (si une seule est TRUE → ABORT)
  abort_conditions:
    - "max_drawdown_pct > 8.0"      # DD > 8% = stop, retour paper
    - "bugs_critiques > 0"          # Bug critique = stop
    - "3_consecutive_losing_weeks"   # 3 semaines perdantes = review

  decision: |
    ALL primaires PASS + 3/5 secondaires PASS → ajouter $5K, PHASE_2
    ALL primaires PASS + < 3/5 secondaires → maintenir $10K, prolonger 15j
    ANY abort TRUE → retour paper 30 jours, diagnostic
```

**Fichiers** :
- `config/scaling_gates.yaml` (modifier)
- `scripts/scaling_decision.py` (modifier : logique primaire/secondaire/abort)
- `tests/test_scaling_gates.py` (modifier : nouveaux tests)

---

### □ HARDEN-003 — Fix dual-mode : synchronisation signaux live vs paper
```yaml
priorité: P0
temps: 8h
dépendances: aucune
validation: EXECUTION AUDITOR + QUANT AUDITOR
```
**Problème** : Pour que la comparaison live vs paper soit valide, le pipeline paper doit générer EXACTEMENT les mêmes signaux que le pipeline live, au même moment.

**Fix** : Le `TradingEngine` dual-mode calcule le signal UNE SEULE FOIS puis route vers live ET paper.

```python
class TradingEngine:
    def process_signal(self, strategy, market_data):
        """
        Signal calculé UNE SEULE FOIS à partir des mêmes données.
        Puis routé vers live ET paper.
        """
        # 1. Calcul du signal (une seule fois)
        signal = strategy.generate_signal(market_data)
        
        if signal is None:
            return
        
        # 2. Log le signal avec un ID unique
        signal_id = self.generate_signal_id()
        self.log_signal(signal_id, strategy.name, signal, market_data.timestamp)
        
        # 3. Router vers LIVE (si la stratégie est dans le live set)
        if strategy.name in self.live_strategies:
            live_result = self.live_pipeline.execute(signal, signal_id)
            self.log_execution(signal_id, "LIVE", live_result)
        
        # 4. Router vers PAPER (toujours)
        paper_result = self.paper_pipeline.execute(signal, signal_id)
        self.log_execution(signal_id, "PAPER", paper_result)
        
        # 5. Comparer
        if strategy.name in self.live_strategies:
            self.compare_executions(signal_id, live_result, paper_result)
```

**Tests requis** :
```
□ Signal FX généré → routé vers live ET paper
□ Signal identique produit le même trade (prix demandé, sizing)
□ Si live rejeté par risk mais paper accepté → divergence logguée
□ Si data feed retardé → les deux pipelines utilisent le même timestamp
□ Rapport de synchronisation : 100% des signaux routés vers les deux
□ Signal pour strat paper-only → routé vers paper UNIQUEMENT
□ Signal pour strat live → routé vers les DEUX
□ Signal futures MCL → dual routing correct
□ Signal borderline US (sizing réduit) → sizing live ≠ sizing paper, logué
```

**Fichiers** :
- `core/trading_engine.py` (modifier : signal unique + routing dual)
- `core/signal_comparator.py` (nouveau : comparaison live vs paper)
- `tests/test_signal_sync.py` (nouveau, 10+ tests)

---

### □ HARDEN-004 — Fix bracket orders FX + futures : test spécifique week-end
```yaml
priorité: P0
temps: 5h (4h FX + 1h futures brackets)
dépendances: aucune
validation: RISK AUDITOR + EXECUTION AUDITOR
```
**Problème** : Les bracket orders FX chez IBKR ont des spécificités non testées (gap week-end, stop market vs stop limit, IDEALPRO). Les futures micro ont aussi des spécificités (heures de trading, maintenance margin, limite de prix).

**Fix FX** :
```python
class FXBracketHandler:
    def create_fx_bracket(self, entry_order, stop_pips, tp_pips):
        """
        Bracket OCA pour FX avec spécificités IBKR :
        - Stop = STOP LIMIT (pas STOP MARKET) — anti-slippage week-end
        - Stop limit avec offset 5 pips vs stop price
        - TP = LIMIT
        - OCA group pour annulation automatique
        - TIF = GTC (survit au week-end)
        """
        stop_price = self.calculate_stop_price(entry_order, stop_pips)
        stop_limit_price = stop_price - (0.0005 if entry_order.side == "BUY" else -0.0005)
        tp_price = self.calculate_tp_price(entry_order, tp_pips)
        
        return {
            "parent": entry_order,
            "stop": {
                "type": "STP LMT",
                "stop_price": stop_price,
                "limit_price": stop_limit_price,
                "tif": "GTC"
            },
            "take_profit": {
                "type": "LMT",
                "limit_price": tp_price,
                "tif": "GTC"
            },
            "oca_group": f"FX_BRACKET_{entry_order.id}"
        }
    
    def pre_weekend_check(self):
        """
        Vendredi 16h ET : vérifier que TOUTES les positions FX ont des brackets actifs.
        """
        for position in self.get_fx_positions():
            if not self.has_active_bracket(position):
                self.alert_critical(
                    f"🔴 POSITION FX SANS BRACKET AVANT WEEK-END : {position.pair}"
                )
```

**Fix Futures (ajout V2)** :
```python
class FuturesBracketHandler:
    def create_futures_bracket(self, entry_order, stop_ticks, tp_ticks):
        """
        Bracket OCA pour futures micro avec spécificités :
        - Stop = STOP LIMIT (anti-slippage session overnight)
        - Buffer : 2 ticks pour MCL, 4 points pour MES
        - TIF = GTC
        - Vérification maintenance margin avant entry
        """
        tick_size = self.get_tick_size(entry_order.symbol)  # MCL=0.01, MES=0.25
        buffer = self.get_buffer(entry_order.symbol)
        
        stop_price = entry_order.price - (stop_ticks * tick_size * entry_order.direction)
        stop_limit_price = stop_price - (buffer * entry_order.direction)
        tp_price = entry_order.price + (tp_ticks * tick_size * entry_order.direction)
        
        return {
            "parent": entry_order,
            "stop": {"type": "STP LMT", "stop_price": stop_price,
                     "limit_price": stop_limit_price, "tif": "GTC"},
            "take_profit": {"type": "LMT", "limit_price": tp_price, "tif": "GTC"},
            "oca_group": f"FUT_BRACKET_{entry_order.id}"
        }
    
    def pre_maintenance_check(self):
        """
        Vérifier avant chaque session overnight que la maintenance margin
        est couverte. CME maintenance < initial margin.
        """
        for position in self.get_futures_positions():
            if self.available_cash < position.maintenance_margin * 1.2:
                self.alert_warning(
                    f"⚠️ MAINTENANCE MARGIN SERRÉE : {position.symbol}"
                )
```

**Tests requis** :
```
FX :
□ Bracket FX créé avec STP LMT (pas STP MKT)
□ Bracket FX avec TIF GTC (survit au week-end)
□ OCA group correctement lié
□ Pre-weekend check : position sans bracket → alerte CRITIQUE
□ Pre-weekend check : vendredi 16h ET → exécuté automatiquement
□ Vérification IDEALPRO : ordres STP LMT supportés
□ Simulation gap week-end : stop exécuté avec buffer
□ Position FX ouverte vendredi → bracket toujours actif lundi

Futures (AJOUT V2) :
□ Bracket MCL créé avec tick size 0.01
□ Bracket MES créé avec tick size 0.25
□ Maintenance margin check : alerte si < 120%
□ Futures bracket TIF GTC survit à la session overnight
```

**Test live paper obligatoire** :
```
FX :
1. Ouvrir EUR/USD paper vendredi 15h ET
2. Placer bracket (SL 50 pips, TP 100 pips)
3. NE RIEN TOUCHER pendant le week-end
4. Lundi matin : vérifier bracket actif
→ PASS/FAIL

Futures :
1. Ouvrir MCL paper pendant une session
2. Placer bracket
3. Vérifier la survie du bracket après la maintenance window (16:00-17:00 CT)
→ PASS/FAIL
```

**Fichiers** :
- `core/broker/ibkr_bracket.py` (modifier : ajouter FXBracketHandler + FuturesBracketHandler)
- `tests/test_fx_brackets.py` (nouveau, 8+ tests)
- `tests/test_futures_brackets.py` (nouveau, 4+ tests)

---

## BOOST — STRATÉGIES ADDITIONNELLES POUR VOLUME

---

### □ BOOST-001 — Activer GBP/USD Trend (FX-002) en live
```yaml
priorité: P0
temps: 3h
dépendances: HARDEN-001
validation: QUANT AUDITOR
```
**Quoi** : Le code `fx_gbpusd_trend.py` existe déjà (V5). L'activer dans le pipeline live avec les mêmes paramètres que EUR/USD Trend. Sharpe estimé 2.0.

**Implémentation** :
```yaml
# config/strategies_live.yaml — ajouter
gbpusd_trend:
  enabled: true
  pipeline: live
  instrument: GBP.USD
  type: FX
  sizing: eighth_kelly     # 1/8 Kelly en soft launch
  bracket:
    stop_pips: 60
    tp_pips: 120
  schedule:
    market_hours: "SUN 17:00 - FRI 17:00 ET"
    signal_frequency: "4H"
```

**Tests requis** :
```
□ Signal GBP/USD généré et routé vers live + paper
□ Sizing correct (1/8 Kelly)
□ Bracket FX créé avec les bons paramètres
□ Risk manager accepte la 5ème paire FX
□ Margin totale FX avec 5 paires < 40%
```

**Fichiers** :
- `config/strategies_live.yaml` (modifier)
- `tests/test_gbpusd_live.py` (nouveau, 5 tests)

---

### □ BOOST-002 — Activer 3 stratégies BORDERLINE US en sizing réduit
```yaml
priorité: P0
temps: 4h
dépendances: HARDEN-001
validation: QUANT AUDITOR + RISK AUDITOR
```
**Quoi** : Late Day Mean Reversion, Failed Rally Short, EOD Sell Pressure V2 sont codées et backtestées mais pas WF-validées à 100%. Les mettre en live avec un sizing seizième-Kelly (moitié du soft launch) pour collecter des données live sans risque significatif.

**Implémentation** :
```yaml
# config/strategies_live.yaml — ajouter
borderline_strategies:
  late_day_mr:
    enabled: true
    pipeline: live
    sizing: sixteenth_kelly   # 1/16 Kelly — probatoire
    tag: BORDERLINE
    max_loss_per_trade_pct: 0.5  # $50 max par trade
  failed_rally_short:
    enabled: true
    pipeline: live
    sizing: sixteenth_kelly
    tag: BORDERLINE
    max_loss_per_trade_pct: 0.5
  eod_sell_pressure:
    enabled: true
    pipeline: live
    sizing: sixteenth_kelly
    tag: BORDERLINE
    max_loss_per_trade_pct: 0.5

# Règle globale borderline
borderline_rules:
  max_combined_exposure_pct: 10   # Max 10% du capital en borderline
  review_after_trades: 20         # Review obligatoire après 20 trades
  auto_disable_if:
    - "sharpe < -0.5 after 15 trades"
    - "max_dd > 3% on borderline bucket"
```

**Logique seizième-Kelly** :
```python
# core/leverage_manager.py — ajouter

SIZING_OVERRIDES = {
    "BORDERLINE": {
        "kelly_fraction": 0.0625,    # 1/16 Kelly
        "max_position_pct": 3.0,     # Max 3% du capital par position
        "max_loss_per_trade_pct": 0.5 # Max $50 par trade sur $10K
    }
}
```

**Tests requis** :
```
□ Borderline sizing = 1/16 Kelly (pas 1/8)
□ Borderline max loss per trade respecté ($50)
□ Borderline combined exposure < 10%
□ Auto-disable si Sharpe < -0.5 après 15 trades
□ Review trigger après 20 trades borderline
□ Borderline trades taggés dans le journal
□ Borderline P&L séparé dans le dashboard
```

**Fichiers** :
- `config/strategies_live.yaml` (modifier)
- `core/leverage_manager.py` (modifier : ajouter SIZING_OVERRIDES)
- `tests/test_borderline_sizing.py` (nouveau, 7 tests)

---

### □ BOOST-003 — Activer EU Gap Open en live
```yaml
priorité: P0
temps: 2h
dépendances: HARDEN-001
validation: QUANT AUDITOR
```
**Quoi** : EU Gap Open est la meilleure stratégie EU (Sharpe 8.56, 72 trades, WF 4/4 PASS). Elle doit être dans le live set phase 1. C'est une stratégie intraday EU → ~10-12 trades/mois, gros boost de volume.

**Implémentation** :
```yaml
# config/strategies_live.yaml — ajouter
eu_gap_open:
  enabled: true
  pipeline: live
  broker: ibkr
  market: EU
  sizing: eighth_kelly
  schedule:
    market_open: "09:00 CET"
    signal_window: "09:00-09:30 CET"
    max_holding: "17:30 CET"    # Fermeture EOD obligatoire
  bracket:
    stop_pct: 0.5
    tp_pct: 1.5
```

**Tests requis** :
```
□ Signal EU Gap Open généré à 09:00-09:30 CET
□ Position fermée avant 17:30 CET
□ Sizing correct (1/8 Kelly)
□ Bracket actions IBKR (pas FX)
□ Dual routing live + paper
```

**Fichiers** :
- `config/strategies_live.yaml` (modifier)
- `tests/test_eu_gap_live.py` (nouveau, 5 tests)

---

### □ BOOST-004 — Setup futures MCL + MES en paper (live semaine 2)
```yaml
priorité: P0
temps: 6h
dépendances: HARDEN-001, HARDEN-004
validation: EXECUTION AUDITOR + RISK AUDITOR
```
**Quoi** : Configurer MCL (Brent Lag) et MES (Trend Following) en paper dès jour 1. Objectif : valider l'intégration technique en 5-7 jours, puis passer en live semaine 2.

**Checklist technique paper** :
```
□ Contract manager : MCL front month correct
□ Contract manager : MES front month correct
□ Bracket futures : STP LMT + OCA fonctionnel en paper
□ Margin tracker : MCL $600, MES $1,400 affichés correctement
□ Roll manager : simulation roll MCL (mensuel)
□ Roll manager : simulation roll MES (trimestriel)
□ Réconciliation : positions futures dans le modèle interne
□ P&L futures : tick value MCL ($10/tick), MES ($1.25/tick)
□ Signal Brent Lag : adapté de la version actions → futures
□ Signal MES Trend : backtest rapide 6 mois minimum
□ 5 trades MCL paper exécutés et réconciliés
□ 3 trades MES paper exécutés et réconciliés
```

**Condition de passage paper → live** :
```
□ 5+ trades MCL paper sans divergence de réconciliation
□ 3+ trades MES paper sans divergence
□ Bracket futures survit à la maintenance window
□ Margin tracker cohérent avec IBKR paper
□ 0 bug critique
→ Si tout PASS après 5-7 jours → LIVE MCL + MES
```

**Fichiers** :
- `config/strategies_live.yaml` (modifier : ajouter MCL + MES paper)
- `strategies/brent_lag_futures.py` (adapter de brent_lag_play.py)
- `strategies/futures_mes_trend.py` (valider)
- `tests/test_futures_paper_integration.py` (nouveau, 12 tests)

---

## DRILL — TESTS EN CONDITIONS RÉALISTES

---

### □ DRILL-001 — Fire drill mode autonome 72h (paper) — NON BLOQUANT
```yaml
priorité: P0 mais NON BLOQUANT pour le live
temps: 4h setup + 72h d'observation passive
dépendances: HARDEN-001
validation: OPS AUDITOR + RISK AUDITOR
note: |
  Tourne en PARALLÈLE du soft launch live.
  Si FAIL, le kill switch (testé dans DRILL-003) permet de couper le live.
```
**Protocole** :
```
PRÉPARATION (vendredi soir) :
1. Activer le mode autonome sur le pipeline paper FX + futures paper
2. Configurer les alertes Telegram (mais NE PAS intervenir)
3. Configurer les auto-reducers et safety checks
4. Ouvrir 2-3 positions FX paper + 1 MCL paper

OBSERVATION (samedi → lundi matin) :
- Logger TOUTES les alertes reçues
- Logger TOUTES les actions automatiques
- NE PAS INTERVENIR (sauf bug qui corrompt les données)

ANALYSE (lundi matin) :
□ Combien d'alertes reçues ? Légitimes ?
□ Pre-weekend check FX fonctionnel ?
□ Brackets FX + futures intacts après le week-end ?
□ Worker stable 72h sans crash ?
□ Réconciliation sans divergence ?
□ Healthcheck externe : 0 downtime ?
□ Logs complets et lisibles ?
□ Backup quotidien fonctionnel (samedi + dimanche) ?

VERDICT :
  PASS = 0 bug critique, 0 divergence, worker stable
  FAIL = identifier, fixer. Le live continue avec surveillance renforcée.
```

**Fichiers** :
- `docs/fire_drill_72h_report.md` (à remplir après le drill)

---

### □ DRILL-002 — Test restauration backup complet — QUASI-BLOQUANT
```yaml
priorité: P0-QUASI-BLOQUANT
temps: 2h
dépendances: aucune
validation: INFRASTRUCTURE AUDITOR
```
**Protocole** :
```
1. Faire un backup frais
2. Copier la DB dans un répertoire temporaire (sécurité)
3. SUPPRIMER la DB (simuler crash)
4. Lancer la restauration
5. Vérifier :
   □ Trade journal restauré
   □ Configs restaurées
   □ Features ML restaurées
   □ Positions restaurées
   □ Worker redémarre et se reconnecte
6. Chronométrer : cible < 30 minutes
7. Remettre la DB originale

VERDICT :
  PASS = restauration complète < 30 min, 0 donnée perdue
  FAIL = fixer le script de restore AVANT le live
```

**Fichiers** :
- `docs/backup_restore_test_report.md` (à remplir)

---

### □ DRILL-003 — Test kill switch end-to-end (paper) — QUASI-BLOQUANT
```yaml
priorité: P0-QUASI-BLOQUANT
temps: 2h
dépendances: HARDEN-001
validation: RISK AUDITOR
```
**Protocole** :
```
PRÉPARATION :
1. Ouvrir 3 positions FX paper + 1 MCL paper
2. Vérifier dans le modèle interne ET chez IBKR paper

TEST 1 — Kill switch automatique (drawdown) :
1. Simuler drawdown > seuil
2. Vérifier : toutes positions fermées < 30s
3. Vérifier : alerte Telegram reçue
4. Vérifier : stratégies désactivées
→ ROUVRIR pour test suivant

TEST 2 — Kill switch Telegram :
1. /kill → confirmation → /kill CONFIRM
2. Vérifier : fermeture < 30s + alerte
→ ROUVRIR pour test suivant

TEST 3 — Kill switch via TWS :
1. Fermer positions manuellement via TWS
2. Vérifier : réconciliation détecte les fermetures
3. Vérifier : modèle interne mis à jour

TEST 4 — Kill switch avec worker down :
1. Ouvrir positions avec brackets
2. ARRÊTER le worker
3. Vérifier : brackets actifs chez IBKR
4. Simuler prix qui atteint le stop
5. Vérifier : stop exécuté SANS le worker
6. Redémarrer worker → réconciliation détecte la fermeture

VERDICT :
  PASS = 4 tests réussis → live autorisé
  FAIL = fixer AVANT le live (risque asymétrique trop élevé)
```

**Fichiers** :
- `docs/kill_switch_test_report.md` (à remplir)

---

## LAUNCH — AJUSTEMENTS DU PLAN DE DÉMARRAGE

---

### □ LAUNCH-001 — Soft launch : huitième-Kelly + seizième-Kelly borderline
```yaml
priorité: P0
temps: 3h
dépendances: HARDEN-001, BOOST-001 à 003
validation: RISK AUDITOR
```
**Sizing soft launch ($10K)** :
```
TIER 1 — 1/8 Kelly (stratégies validées WF) :
  EUR/USD Trend  : $15,000 notionnel (margin ~$450)
  EUR/GBP MR     : $12,000 notionnel (margin ~$360)
  EUR/JPY Carry  : $12,000 notionnel (margin ~$360)
  AUD/JPY Carry  : $10,000 notionnel (margin ~$300)
  GBP/USD Trend  : $12,000 notionnel (margin ~$360)
  EU Gap Open    : ~$2,000 position (pas de margin FX)
  Sous-total margin : ~$1,830 (18% du capital)

TIER 2 — 1/16 Kelly (stratégies borderline) :
  Late Day MR        : ~$500 position
  Failed Rally Short : ~$500 position  
  EOD Sell Pressure  : ~$500 position
  Sous-total : ~$1,500 max (15% du capital)

TOTAL MARGIN SEMAINE 1 : ~$3,330 (33% du capital)
CASH LIBRE : ~$6,670 (67%) — très conservateur

Perte max estimée si tout va mal : ~$250 (-2.5%)
```

**Condition de passage SOFT_LAUNCH → PHASE_1** :
```
Au moins 5 trades exécutés ET
Max drawdown < 2% ET
0 bug critique ET
Réconciliation 0 divergence sur 7 jours
→ Passage en quart-Kelly (tier 1) et huitième-Kelly (tier 2)
```

**Fichiers** :
- `core/leverage_manager.py` (modifier : ajouter SOFT_LAUNCH + BORDERLINE)
- `config/leverage_schedule.yaml` (modifier)
- `tests/test_soft_launch_v2.py` (nouveau)

---

### □ LAUNCH-002 — Futures live semaine 2 (MCL + MES)
```yaml
priorité: P0
temps: 2h planification — exécution = BOOST-004 en cours
dépendances: BOOST-004 PASS
validation: QUANT AUDITOR + RISK AUDITOR
```
**Plan** :
```
SEMAINE 1 (soft launch) :
  LIVE : 5 FX + EU Gap Open + 3 borderline US (1/8 et 1/16 Kelly)
  PAPER : MCL + MES (validation technique)
  Volume attendu live : 35-50 trades

SEMAINE 2 (si BOOST-004 paper PASS + soft launch OK) :
  LIVE : tout semaine 1 + MCL + MES (1/8 Kelly)
  Volume attendu live : 55-75 trades
  Margin totale estimée : ~$5,330 (53% du capital)

SEMAINE 3 (évaluation gate M1) :
  20+ trades live cumulés → évaluation gate M1
  Si PASS → +$5K, début phase 2

SEMAINE 4+ (si gate M1 prolongé) :
  Passage FX + futures en quart-Kelly
  Borderline passent en huitième-Kelly ou sont désactivées selon data
```

**Fichiers** :
- `docs/launch_plan_v2.md` (nouveau)

---

### □ LAUNCH-003 — Latence Railway-Hetzner : mesure et plan de migration
```yaml
priorité: P1 (pas bloquant FX swing, à surveiller pour futures)
temps: 4h
dépendances: VPS Hetzner provisionné
validation: INFRASTRUCTURE AUDITOR
```
**Protocole** :
```
1. Depuis Railway, ping Hetzner (1000 pings)
2. Mesurer : latence moyenne, P95, P99, jitter
3. Simuler roundtrip API : Railway → Hetzner → IBKR → retour
4. Mesurer temps total signal → fill

SEUILS :
  < 100ms → OK pour tout
  100-200ms → OK pour FX + futures swing, borderline intraday
  > 200ms → Migration worker vers Hetzner OBLIGATOIRE
```

**Fichiers** :
- `scripts/measure_latency.py` (nouveau)
- `docs/latency_report.md` (à remplir)

---

## CHECKLIST COMPLÈTE PHASE 1 HARDENING V2

```
HARDEN (avant tout) :
□ HARDEN-001  Fix risk manager : FX margin + futures margin
□ HARDEN-002  Fix gate M1 (seuil trades multi-stratégie)
□ HARDEN-003  Fix signal sync live vs paper
□ HARDEN-004  Fix brackets FX week-end + futures

BOOST (en parallèle des HARDEN) :
□ BOOST-001   Activer GBP/USD Trend (FX-002)
□ BOOST-002   Activer 3 borderline US (1/16 Kelly)
□ BOOST-003   Activer EU Gap Open
□ BOOST-004   Setup futures MCL + MES paper

DRILL (après les HARDEN) :
□ DRILL-002   Test backup restore (QUASI-BLOQUANT)
□ DRILL-003   Test kill switch 4 méthodes (QUASI-BLOQUANT)
□ DRILL-001   Fire drill 72h paper (NON BLOQUANT, en parallèle)

LAUNCH :
□ LAUNCH-001  Configurer soft launch (1/8 + 1/16 Kelly)
□ LAUNCH-002  Plan futures live semaine 2
□ LAUNCH-003  Mesurer latence Railway-Hetzner

TOTAL : 14 tâches (~50h code + 72h drill passif)
Volume live cible : 40-60 trades/mois (semaine 1), 55-75 (semaine 2+)
```

---

## SÉQUENCE TEMPORELLE V2

```
JOUR 1-2 (samedi-dimanche) :
  □ HARDEN-001 à 004 (fixes code, ~24h)
  □ BOOST-001 à 003 (activation stratégies, ~9h)
  □ BOOST-004 commence (setup futures paper, 6h)
  □ LAUNCH-001 (config soft launch, 3h)
  □ Provisionnement Hetzner (Marc, 10min)
  □ Configuration IBKR (Marc, 30min)

JOUR 3 (lundi) :
  □ DRILL-002 (test backup, 2h) — QUASI-BLOQUANT
  □ DRILL-003 (test kill switch, 2h) — QUASI-BLOQUANT
  □ LAUNCH-003 (mesure latence, 4h)
  □ Configuration Hetzner (2-3h)
  □ Go/No-Go decision le soir

JOUR 4 (mardi) — si DRILL-002 + DRILL-003 PASS :
  □ PREMIER TRADE LIVE (soft launch)
  □ 5 FX + EU Gap Open + 3 borderline US
  □ DRILL-001 démarre en parallèle (72h paper)
  □ Futures paper continue (BOOST-004)

JOUR 5-7 (mercredi-vendredi) :
  □ Monitoring soft launch live
  □ DRILL-001 en cours (72h paper)
  □ Futures paper : 5+ trades MCL, 3+ trades MES

JOUR 8 (lundi suivant) :
  □ Analyse DRILL-001
  □ Si BOOST-004 paper PASS → FUTURES LIVE (MCL + MES)
  □ Volume live cible : 55-75 trades/mois

SEMAINE 3 :
  □ Passage quart-Kelly (tier 1) si soft launch clean
  □ Évaluation gate M1 si 20+ trades cumulés

SEMAINE 4 :
  □ Décision gate M1 : scale $15K ou prolonger
```

---

*Phase 1 Hardening V2 — 27 mars 2026*
*14 tâches | ~50h code + 72h drill | 40-60 trades live/mois dès semaine 1*
*"Le volume de trades n'est pas un objectif en soi, c'est la condition pour que la validation statistique ait un sens."*
