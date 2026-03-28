# TODO V8.0 FINAL — DUAL TRACK PARALLÈLE
## Track 1 : LIVE (Marc) | Track 2 : FONDATIONS (Claude sessions de nuit)
## ~250h de travail | 8 agents | Semaines 1-12
### Date : 27 mars 2026 | Post V7.6 | $10K IBKR + $15K Binance

---

## MANIFESTE

```
CE DOCUMENT ORGANISE ~250h DE TRAVAIL EN DEUX TRACKS PARALLÈLES.

TRACK 1 — LIVE (priorité absolue, bloque tout le reste) :
  Marc pilote, Claude supporte. Objectif : premier trade live J4.
  Setup Hetzner, IBKR Gateway, drills, soft launch, crypto.
  ~30h de travail Marc + Claude en mode support.

TRACK 2 — FONDATIONS V8 (Claude en sessions de nuit) :
  Ne touche PAS au code live (branche dev séparée).
  Prépare les outils pour les mois 2-6.
  ~220h réparties en 11 sessions intensives.

RÈGLE D'OR : Track 2 ne retarde JAMAIS Track 1.
Si un conflit de temps → Track 1 gagne TOUJOURS.

BRANCHES GIT :
  main → code live, déployé, ne casse jamais
  dev/backtester-v2 → C1 (backtester)
  dev/ml-pipeline → C2 (ML)
  dev/alpha-research → C3 (nouvelles stratégies)
  dev/hardening → C6 (fuzzing, stress tests)
  dev/infra → C5 (PostgreSQL, Grafana)
  Merge vers main UNIQUEMENT après : review + tous tests PASS + validation Marc

AGENTS :
┌─────────────────────┬──────────┬──────────────────────────────────────────┐
│ Agent               │ ID       │ Track / Sessions                         │
├─────────────────────┼──────────┼──────────────────────────────────────────┤
│ BACKTEST ARCHITECT  │ BT-ARCH  │ Track 2 — Sessions 1-3                   │
│ QUANT RESEARCHER    │ QR       │ Track 2 — Sessions 6-8                   │
│ ML ENGINEER         │ ML-ENG   │ Track 2 — Sessions 4-5, 9               │
│ RISK ENGINEER       │ RISK-ENG │ Track 2 — Sessions 1, 3, 7              │
│ DATA ENGINEER       │ DATA-ENG │ Track 2 — Session 10                     │
│ SECURITY AUDITOR    │ SEC-AUD  │ Track 2 — Sessions 2-3                   │
│ CODE REVIEWER       │ CODE-REV │ Track 2 — TOUTES les sessions            │
│ EXECUTION ENGINEER  │ EXEC-ENG │ Track 1 — Support live                   │
└─────────────────────┴──────────┴──────────────────────────────────────────┘

QUALITÉ NON NÉGOCIABLE :
  - Type hints sur toutes les fonctions publiques
  - Docstrings Google-style sur toutes les classes
  - Max 200 lignes par fichier, max 20 lignes par fonction
  - Coverage > 80% sur chaque nouveau fichier
  - Ruff clean (0 erreur)
  - TOUS les tests existants passent après chaque session
  - Pas de TODO/FIXME/HACK dans le code livré
```

---

## TRACK 1 — LIVE (Marc pilote)

```
Ce track est documenté dans les TODO V7.2-V7.6.
Résumé séquentiel pour référence :

J1-2 : Setup Hetzner CPX32 + IB Gateway + SSH
J3   : DRILL-002 (backup) + DRILL-003 (kill switch) → Go/No-Go
J4   : Premier trade IBKR live (5 FX 1/8 Kelly + EU Gap 1/4 Kelly)
J5   : Futures MCL + MES live si paper OK
J6-9 : Monitoring soft launch + passage 1/4 Kelly si clean
J10+ : Setup Binance margin + paper crypto 7j
J17+ : Soft launch crypto ($10K, spot+earn, pas de margin)
J24+ : Crypto phase 2 ($12.5K, +margin)
J31+ : Crypto phase 3 ($15K, toutes strats WF-validées)

Gate M1 IBKR : semaine 3-4 (15 trades, DD < 5%, 0 bug)
Gate M1 Crypto : semaine 6-7 (20 trades, DD < 12%, 0 bug)
```

---

## TRACK 2 — FONDATIONS V8 (Claude sessions de nuit)

---

## SESSION 1 (~25h) — BACKTESTER V2 : ARCHITECTURE + ANTI-LOOKAHEAD
```
AGENT LEAD : BT-ARCH
AGENTS SUPPORT : RISK-ENG, CODE-REV
TIMING : Semaine 1 (pendant que Marc fait le setup Hetzner)
BRANCHE : dev/backtester-v2
```

### S1.1 — Engine event-driven
```yaml
priorité: P0
temps: 8h
```

```python
# core/backtester_v2/engine.py

class BacktesterV2:
    """
    Moteur de backtest event-driven multi-asset.
    
    DIFFÉRENCES vs V1 :
    1. Event-driven (pas vectorisé) — ordre chronologique strict
    2. Anti-lookahead by design — DataFeed contrôle l'accès aux données
    3. Multi-asset natif — FX, equities, futures, crypto dans le même run
    4. Coûts réalistes intégrés — pas des paramètres fixes
    5. Risk checks identiques au live — même LiveRiskManager
    """
    
    def __init__(self, config: BacktestConfig):
        self.event_queue = EventQueue()
        self.data_feed = DataFeed(config.data_sources)
        self.execution_sim = ExecutionSimulator(config.execution)
        self.portfolio = PortfolioTracker(config.initial_capital)
        self.risk_manager = BacktestRiskManager(config.risk_limits)  # Même logique que live
        self.cost_models = CostModelFactory.create(config.brokers)
        self.calendars = CalendarFactory.create(config.asset_classes)
        self.results = BacktestResults()
    
    def run(self, strategies: List[StrategyBase], 
            start: datetime, end: datetime) -> BacktestResults:
        """
        Pipeline principal :
        1. Charger les données et créer les événements MARKET_DATA
        2. Boucler sur chaque événement chronologiquement
        3. Router l'événement vers le handler approprié
        4. Collecter les résultats
        """
        # 1. Initialiser les événements de marché
        self._load_market_events(start, end)
        
        # 2. Ajouter les événements périodiques
        self._schedule_periodic_events(start, end)  # EOD, funding, interest, rebalance
        
        # 3. Boucle principale
        while not self.event_queue.is_empty():
            event = self.event_queue.pop()
            
            if event.timestamp > end:
                break
            
            self._handle_event(event, strategies)
        
        # 4. Fermer les positions restantes
        self._close_all_positions(end)
        
        # 5. Calculer les métriques finales
        return self.results.finalize()
    
    def _handle_event(self, event: Event, strategies: List[StrategyBase]):
        """Router chaque type d'événement."""
        handlers = {
            EventType.MARKET_DATA: self._on_market_data,
            EventType.SIGNAL: self._on_signal,
            EventType.ORDER: self._on_order,
            EventType.FILL: self._on_fill,
            EventType.FUNDING: self._on_funding,         # Crypto funding (lecture)
            EventType.BORROW_INTEREST: self._on_interest, # Margin interest
            EventType.SWAP: self._on_swap,                # FX swap overnight
            EventType.EOD: self._on_eod,
            EventType.REBALANCE: self._on_rebalance,
            EventType.MARGIN_CHECK: self._on_margin_check,
            EventType.ROLL: self._on_roll,                # Futures roll
            EventType.CIRCUIT_BREAKER: self._on_circuit_breaker,
        }
        handler = handlers.get(event.type)
        if handler:
            handler(event, strategies)
    
    def _on_market_data(self, event: Event, strategies: List[StrategyBase]):
        """
        Nouvelle candle fermée disponible.
        1. Mettre à jour le DataFeed
        2. Mettre à jour le portfolio (mark-to-market)
        3. Vérifier les stops/TP
        4. Générer les signaux pour chaque stratégie
        """
        bar = event.data
        self.data_feed.update(bar)
        self.portfolio.mark_to_market(bar)
        
        # Vérifier les stops et TP
        triggered = self.portfolio.check_stops(bar)
        for stop_event in triggered:
            self.event_queue.push(stop_event)
        
        # Générer les signaux
        for strategy in strategies:
            if not self.calendars.is_active(strategy.asset_class, event.timestamp):
                continue  # Marché fermé pour cette stratégie
            
            signal = strategy.on_bar(bar, self.portfolio.get_state())
            if signal:
                self.event_queue.push(Event(
                    timestamp=event.timestamp,
                    type=EventType.SIGNAL,
                    data=signal
                ))
    
    def _on_signal(self, event: Event, strategies: List[StrategyBase]):
        """
        Signal reçu → vérifier risk → créer un ordre.
        """
        signal = event.data
        
        # Risk check (même logique que le LiveRiskManager)
        risk_result = self.risk_manager.validate(signal, self.portfolio.get_state())
        if risk_result.rejected:
            self.results.log_rejection(signal, risk_result.reason)
            return
        
        # Calculer le sizing
        size = self.risk_manager.calculate_size(signal, self.portfolio.get_state())
        
        # Créer l'ordre
        order = Order(
            symbol=signal.symbol,
            side=signal.side,
            quantity=size,
            order_type=signal.order_type,
            timestamp=event.timestamp,
            strategy=signal.strategy_name,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )
        
        self.event_queue.push(Event(
            timestamp=event.timestamp,
            type=EventType.ORDER,
            data=order
        ))
    
    def _on_order(self, event: Event, strategies: List[StrategyBase]):
        """
        Ordre soumis → simuler l'exécution.
        """
        order = event.data
        fill = self.execution_sim.simulate_fill(
            order, 
            self.data_feed.get_market_state(order.symbol, event.timestamp),
            self.cost_models.get(order.symbol)
        )
        
        if fill.rejected:
            self.results.log_rejection(order, fill.reason)
            return
        
        # Le fill arrive avec un délai (latence simulée)
        self.event_queue.push(Event(
            timestamp=fill.timestamp,  # timestamp + latence
            type=EventType.FILL,
            data=fill
        ))
    
    def _on_fill(self, event: Event, strategies: List[StrategyBase]):
        """
        Ordre exécuté → mettre à jour le portfolio.
        """
        fill = event.data
        self.portfolio.apply_fill(fill)
        self.results.log_trade(fill)
        
        # Notifier la stratégie
        for strategy in strategies:
            if strategy.name == fill.strategy_name:
                strategy.on_fill(fill)
```

### S1.2 — DataFeed anti-lookahead strict
```yaml
priorité: P0-CRITIQUE
temps: 6h
```

```python
# core/backtester_v2/data_feed.py

class DataFeed:
    """
    Fournit les données de marché SANS lookahead.
    C'est le composant le plus critique du backtester.
    
    RÈGLES :
    1. get_latest_bar() retourne la DERNIÈRE candle FERMÉE
       La candle 14:00-15:00 n'est disponible qu'à 15:00:00
    2. get_bars() retourne les N dernières candles FERMÉES
    3. Aucune méthode ne permet d'accéder aux données futures
    4. Le timestamp courant est géré par l'engine, pas par le DataFeed
    """
    
    def __init__(self, data_sources: Dict[str, pd.DataFrame]):
        self._data = {}
        for symbol, df in data_sources.items():
            # Vérifier que les données sont triées chronologiquement
            assert df.index.is_monotonic_increasing, f"{symbol} data not sorted"
            # Vérifier les colonnes requises
            assert all(c in df.columns for c in ['open','high','low','close','volume'])
            self._data[symbol] = df
        
        self._current_timestamp = None
        self._bar_cache = {}
    
    def set_timestamp(self, timestamp: datetime):
        """Appelé par l'engine à chaque événement."""
        self._current_timestamp = timestamp
        self._bar_cache.clear()  # Invalidate cache
    
    def get_latest_bar(self, symbol: str) -> Optional[Bar]:
        """
        Retourne la dernière candle FERMÉE avant le timestamp courant.
        
        ANTI-LOOKAHEAD : Si le timestamp est 14:30, la candle 14:00-15:00
        n'est PAS retournée (elle n'est pas encore fermée).
        Seule la candle 13:00-14:00 (ou avant) est retournée.
        """
        if symbol not in self._data:
            return None
        
        df = self._data[symbol]
        # Candles dont le close_time est STRICTEMENT AVANT le timestamp courant
        mask = df.index < self._current_timestamp
        if not mask.any():
            return None
        
        row = df.loc[mask].iloc[-1]
        return Bar(
            symbol=symbol,
            timestamp=row.name,
            open=row['open'], high=row['high'],
            low=row['low'], close=row['close'],
            volume=row['volume']
        )
    
    def get_bars(self, symbol: str, n: int) -> pd.DataFrame:
        """Retourne les N dernières candles FERMÉES."""
        df = self._data[symbol]
        mask = df.index < self._current_timestamp
        return df.loc[mask].tail(n)
    
    def get_indicator(self, symbol: str, indicator: str, 
                      period: int, **kwargs) -> float:
        """
        Calcule un indicateur technique sur les données disponibles.
        Ex: get_indicator("BTCUSDT", "ema", 50)
        
        ANTI-LOOKAHEAD : l'indicateur est calculé UNIQUEMENT sur les
        candles fermées disponibles à ce moment.
        """
        bars = self.get_bars(symbol, period * 3)  # Buffer pour le calcul
        if len(bars) < period:
            return float('nan')
        return self._calculate_indicator(bars, indicator, period, **kwargs)
    
    # INTERDIT : ces méthodes n'existent PAS
    # def get_future_bar(self, ...): ...  # N'EXISTE PAS
    # def get_current_candle(self, ...): ... # N'EXISTE PAS (candle en cours)
    # def peek_next(self, ...): ...  # N'EXISTE PAS
```

**Test anti-lookahead (le test le plus important du projet)** :
```python
# tests/test_backtester_v2/test_anti_lookahead.py

class TestAntiLookahead:
    """
    Ces tests PROUVENT que le backtester ne peut pas tricher.
    Si un seul de ces tests échoue, le backtester est INUTILISABLE.
    """
    
    def test_latest_bar_is_closed(self):
        """La dernière bar retournée est toujours FERMÉE."""
        feed = DataFeed({"BTC": btc_data_1h})
        # À 14:30, la candle 14:00 n'est PAS fermée
        feed.set_timestamp(datetime(2025, 1, 1, 14, 30))
        bar = feed.get_latest_bar("BTC")
        assert bar.timestamp == datetime(2025, 1, 1, 13, 0)  # 13:00, pas 14:00
    
    def test_cannot_access_future_data(self):
        """Impossible d'accéder aux données futures."""
        feed = DataFeed({"BTC": btc_data_1h})
        feed.set_timestamp(datetime(2025, 1, 1, 12, 0))
        bars = feed.get_bars("BTC", 1000)
        assert bars.index.max() < datetime(2025, 1, 1, 12, 0)
    
    def test_indicator_uses_only_past_data(self):
        """Les indicateurs n'utilisent que les données passées."""
        feed = DataFeed({"BTC": btc_data_1h})
        feed.set_timestamp(datetime(2025, 6, 1, 0, 0))
        ema = feed.get_indicator("BTC", "ema", 50)
        
        # Calculer manuellement l'EMA sur les données avant la date
        manual_ema = btc_data_1h.loc[:datetime(2025, 5, 31, 23, 0)]['close'].ewm(span=50).mean().iloc[-1]
        assert abs(ema - manual_ema) < 0.01
    
    def test_signal_timing(self):
        """Un signal généré à 14:00 utilise la candle 13:00 (pas 14:00)."""
        engine = BacktesterV2(config)
        # Injecter une stratégie qui log les bars qu'elle reçoit
        spy_strategy = SpyStrategy()
        engine.run([spy_strategy], start, end)
        
        for signal in spy_strategy.signals_generated:
            bar_used = signal.bar_timestamp
            signal_time = signal.signal_timestamp
            assert bar_used < signal_time, \
                f"LOOKAHEAD: signal at {signal_time} used bar from {bar_used}"
    
    def test_no_future_leak_in_indicators(self):
        """
        Test exhaustif : pour chaque indicateur, vérifier qu'il ne
        change PAS quand on ajoute des données futures.
        """
        feed1 = DataFeed({"BTC": btc_data_1h[:100]})  # 100 candles
        feed2 = DataFeed({"BTC": btc_data_1h[:200]})  # 200 candles (100 de plus)
        
        feed1.set_timestamp(btc_data_1h.index[99])
        feed2.set_timestamp(btc_data_1h.index[99])  # Même timestamp
        
        for indicator in ["ema", "rsi", "atr", "adx", "bollinger"]:
            val1 = feed1.get_indicator("BTC", indicator, 14)
            val2 = feed2.get_indicator("BTC", indicator, 14)
            assert val1 == val2, \
                f"FUTURE LEAK: {indicator} changes with future data ({val1} vs {val2})"
```

### S1.3 — Execution Simulator réaliste
```yaml
priorité: P0
temps: 6h
```

```python
# core/backtester_v2/execution_simulator.py

class ExecutionSimulator:
    """
    Simule l'exécution des ordres de manière réaliste.
    Modélise : latence, slippage, spread, rejection, partial fills.
    """
    
    # Latence par broker (millisecondes)
    LATENCY = {
        "IBKR": {"mean": 80, "std": 30, "min": 20, "max": 500},
        "BINANCE": {"mean": 40, "std": 15, "min": 10, "max": 200},
    }
    
    # Spread de base par asset class (bps)
    BASE_SPREAD = {
        "FX_MAJOR": 1.0,      # EUR/USD, GBP/USD
        "FX_CROSS": 2.0,      # EUR/JPY, AUD/JPY
        "EQUITY_LARGE": 1.5,  # SPY, QQQ
        "EQUITY_MID": 3.0,    # Mid-caps
        "FUTURES_MICRO": 2.0, # MES, MCL
        "CRYPTO_BTC": 2.0,    # BTC/USDT
        "CRYPTO_ETH": 3.0,    # ETH/USDT
        "CRYPTO_ALT_T2": 5.0, # SOL, BNB
        "CRYPTO_ALT_T3": 8.0, # AVAX, LINK
    }
    
    def simulate_fill(self, order: Order, market: MarketState, 
                      cost_model: CostModel) -> Fill:
        """
        1. Simuler la latence
        2. Vérifier que le marché est ouvert
        3. Calculer le spread
        4. Calculer le slippage (impact de marché)
        5. Calculer le prix de fill
        6. Appliquer les commissions
        7. Vérifier la margin
        8. Retourner le fill ou une rejection
        """
        # 1. Latence
        latency_ms = self._simulate_latency(order.broker)
        fill_time = order.timestamp + timedelta(milliseconds=latency_ms)
        
        # 2. Marché ouvert ?
        if not market.is_open:
            return Fill.rejected("Market closed", order)
        
        # 3. Spread
        spread_bps = self._calculate_spread(order.symbol, market)
        half_spread = spread_bps / 2 / 10000 * market.mid_price
        
        # 4. Slippage (market impact)
        impact = self._calculate_impact(order, market)
        
        # 5. Prix de fill
        if order.side == "BUY":
            fill_price = market.mid_price + half_spread + impact
        else:
            fill_price = market.mid_price - half_spread - impact
        
        # Pour les limit orders : vérifier que le prix est atteint
        if order.order_type == "LIMIT":
            if order.side == "BUY" and fill_price > order.limit_price:
                return Fill.rejected("Limit not reached", order)
            if order.side == "SELL" and fill_price < order.limit_price:
                return Fill.rejected("Limit not reached", order)
            fill_price = order.limit_price  # Fill au prix limit
        
        # 6. Commissions
        commission = cost_model.calculate_commission(order, fill_price)
        
        # 7. Margin check
        if not self._check_margin(order, fill_price):
            return Fill.rejected("Insufficient margin", order)
        
        slippage_bps = abs(fill_price - market.mid_price) / market.mid_price * 10000
        
        return Fill(
            order=order,
            price=fill_price,
            quantity=order.quantity,
            commission=commission,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            timestamp=fill_time,
        )
    
    def _calculate_spread(self, symbol: str, market: MarketState) -> float:
        """
        Spread = base * vol_adj * liquidity_adj * time_adj
        
        vol_adj : spread plus large quand la vol récente est élevée
        liquidity_adj : spread plus large quand le volume est bas
        time_adj : spread plus large hors heures de pointe
        """
        base = self.BASE_SPREAD[market.asset_class]
        vol_adj = max(0.5, min(3.0, market.vol_1h / market.vol_30d))
        liq_adj = max(0.5, min(3.0, market.avg_volume / max(market.current_volume, 1)))
        time_adj = self._time_spread_multiplier(market.asset_class, market.hour)
        return base * vol_adj * liq_adj * time_adj
    
    def _calculate_impact(self, order: Order, market: MarketState) -> float:
        """
        Market impact = sigma * sqrt(Q / ADV) * coefficient
        Almgren-Chriss simplifié.
        """
        sigma = market.vol_1d
        q_over_adv = order.notional / max(market.adv_20d, 1)
        coefficient = 0.1  # Calibré empiriquement
        return market.mid_price * sigma * math.sqrt(q_over_adv) * coefficient
    
    def _time_spread_multiplier(self, asset_class: str, hour: int) -> float:
        """
        Le spread varie selon l'heure :
        - FX : minimal pendant l'overlap Londres-NY (13-17 UTC)
        - Crypto : minimal pendant les heures US (14-22 UTC)
        - Equities : plus large à l'ouverture et la fermeture
        """
        multipliers = {
            "FX_MAJOR": {
                "peak": (8, 16), "peak_mult": 0.8,  # Londres
                "off_peak_mult": 1.5,
                "dead": (22, 6), "dead_mult": 2.5,   # Asie basse liquidité
            },
            "CRYPTO_BTC": {
                "peak": (14, 22), "peak_mult": 0.8,  # US hours
                "off_peak_mult": 1.2,
                "dead": (4, 8), "dead_mult": 1.8,    # Early morning
            },
        }
        # Simplified — return based on hour
        config = multipliers.get(asset_class, {"peak": (8, 18), "peak_mult": 1.0, "off_peak_mult": 1.3, "dead": (0, 6), "dead_mult": 2.0})
        if config["dead"][0] <= hour < config["dead"][1]:
            return config["dead_mult"]
        elif config["peak"][0] <= hour < config["peak"][1]:
            return config["peak_mult"]
        return config["off_peak_mult"]
```

### S1.4 — Cost Models par broker
```yaml
priorité: P0
temps: 5h
```

```python
# core/backtester_v2/cost_models/ibkr_costs.py

class IBKRCostModel(CostModel):
    """
    Modèle de coûts IBKR réaliste.
    """
    
    def calculate_commission(self, order: Order, fill_price: float) -> float:
        """
        IBKR commission structure (mars 2026) :
        
        FX : $2 par trade (minimum) ou 0.2 bps du notionnel
        US Equities : $0.005/share (min $1, max 1% du trade)
        EU Equities : 0.05% du notionnel (min €3)
        Futures micro : $0.62/contrat (MES), $0.62 (MCL)
        
        + Exchange fees (variable)
        + Regulatory fees (US equities seulement)
        """
        if order.asset_class == "FX":
            return max(2.0, order.notional * 0.00002)
        elif order.asset_class == "US_EQUITY":
            per_share = order.quantity * 0.005
            return max(1.0, min(per_share, order.notional * 0.01))
        elif order.asset_class == "EU_EQUITY":
            return max(3.0, order.notional * 0.0005)
        elif order.asset_class == "FUTURES_MICRO":
            return order.quantity * 0.62
        return 0
    
    def calculate_swap(self, position, timestamp):
        """
        FX swap overnight :
        Calculé à 17h ET chaque jour (sauf week-end).
        Le mercredi = triple swap (week-end).
        Le swap dépend du différentiel de taux entre les deux devises.
        """
        if position.asset_class != "FX":
            return 0
        if timestamp.hour != 22 or timestamp.weekday() >= 5:  # 17h ET = 22h UTC
            return 0
        multiplier = 3 if timestamp.weekday() == 2 else 1  # Mercredi = triple
        swap_rate = self.get_swap_rate(position.symbol, position.side)
        return position.notional * swap_rate * multiplier


# core/backtester_v2/cost_models/binance_costs.py

class BinanceCostModel(CostModel):
    """
    Modèle de coûts Binance France (spot + margin).
    """
    
    # Commissions par tier (volume 30 jours)
    COMMISSION_TIERS = {
        "VIP0": {"maker": 0.001, "taker": 0.001},     # < $1M/30j
        "VIP1": {"maker": 0.0009, "taker": 0.001},    # $1M-$5M
        "VIP2": {"maker": 0.0008, "taker": 0.001},    # $5M-$10M
        # Marc sera VIP0 au départ
    }
    
    # BNB discount (25% off si payé en BNB)
    BNB_DISCOUNT = 0.75
    
    def calculate_commission(self, order: Order, fill_price: float) -> float:
        tier = self.get_current_tier()
        rate = self.COMMISSION_TIERS[tier]
        
        if order.order_type == "LIMIT":
            base_rate = rate["maker"]
        else:
            base_rate = rate["taker"]
        
        if self.bnb_payment_enabled:
            base_rate *= self.BNB_DISCOUNT
        
        return order.notional * base_rate
    
    def calculate_borrow_interest(self, position, timestamp):
        """
        Intérêts d'emprunt margin — facturés TOUTES LES HEURES.
        Le taux varie selon l'offre/demande.
        Utiliser les données historiques réelles.
        """
        if not position.is_margin_borrow:
            return 0
        
        hourly_rate = self.get_historical_borrow_rate(
            position.borrowed_asset, timestamp
        )
        return position.borrowed_amount * hourly_rate
    
    def calculate_earn_yield(self, earn_position, timestamp):
        """
        Rendement Binance Earn — crédité quotidiennement.
        """
        daily_rate = self.get_historical_earn_rate(
            earn_position.asset, timestamp
        )
        return earn_position.amount * daily_rate
```

### S1.5 — Portfolio Tracker + Risk Manager Backtest
```yaml
priorité: P0
temps: 5h
```

```python
# core/backtester_v2/portfolio_tracker.py

class PortfolioTracker:
    """
    Suit le portefeuille pendant le backtest.
    Calcule les mêmes métriques que le live.
    """
    
    def __init__(self, initial_capital: float, risk_limits: dict):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions = {}           # symbol → Position
        self.equity_curve = []        # (timestamp, equity)
        self.trade_log = []           # Tous les trades
        self.risk_manager = BacktestRiskManager(risk_limits)
        
        # Métriques rolling
        self.peak_equity = initial_capital
        self.max_drawdown = 0
        self.daily_pnl = {}
        self.hourly_pnl = {}
    
    def mark_to_market(self, bar: Bar):
        """Mettre à jour la valeur de marché de toutes les positions."""
        for symbol, position in self.positions.items():
            if symbol == bar.symbol:
                position.update_price(bar.close)
        
        equity = self.cash + sum(p.market_value for p in self.positions.values())
        self.equity_curve.append((bar.timestamp, equity))
        
        # Drawdown
        self.peak_equity = max(self.peak_equity, equity)
        current_dd = (self.peak_equity - equity) / self.peak_equity
        self.max_drawdown = max(self.max_drawdown, current_dd)
    
    def apply_fill(self, fill: Fill):
        """Appliquer un fill au portefeuille."""
        if fill.side == "BUY":
            self._open_or_add(fill)
        else:
            self._close_or_reduce(fill)
        
        self.cash -= fill.commission
        self.trade_log.append(fill.to_trade_record())
    
    def check_stops(self, bar: Bar) -> List[Event]:
        """Vérifier si des stops ou TP sont touchés."""
        events = []
        for symbol, position in list(self.positions.items()):
            if symbol != bar.symbol:
                continue
            
            # Stop loss
            if position.stop_loss:
                if (position.side == "LONG" and bar.low <= position.stop_loss) or \
                   (position.side == "SHORT" and bar.high >= position.stop_loss):
                    events.append(self._create_stop_event(position, bar))
            
            # Take profit
            if position.take_profit:
                if (position.side == "LONG" and bar.high >= position.take_profit) or \
                   (position.side == "SHORT" and bar.low <= position.take_profit):
                    events.append(self._create_tp_event(position, bar))
        
        return events
    
    def get_state(self) -> PortfolioState:
        """État courant pour les stratégies et le risk manager."""
        equity = self.cash + sum(p.market_value for p in self.positions.values())
        return PortfolioState(
            cash=self.cash,
            equity=equity,
            positions=dict(self.positions),
            drawdown=self.max_drawdown,
            daily_pnl=self._get_daily_pnl(),
            exposure_long=sum(p.market_value for p in self.positions.values() if p.side == "LONG"),
            exposure_short=sum(abs(p.market_value) for p in self.positions.values() if p.side == "SHORT"),
        )
    
    def get_results(self) -> Dict:
        """Métriques finales du backtest."""
        equity_series = pd.Series(dict(self.equity_curve))
        returns = equity_series.pct_change().dropna()
        
        return {
            "total_return": (equity_series.iloc[-1] / self.initial_capital) - 1,
            "sharpe": returns.mean() / returns.std() * math.sqrt(252) if returns.std() > 0 else 0,
            "max_drawdown": self.max_drawdown,
            "total_trades": len(self.trade_log),
            "win_rate": sum(1 for t in self.trade_log if t.pnl > 0) / max(len(self.trade_log), 1),
            "profit_factor": self._calc_profit_factor(),
            "avg_trade_pnl": np.mean([t.pnl for t in self.trade_log]) if self.trade_log else 0,
            "avg_winner": np.mean([t.pnl for t in self.trade_log if t.pnl > 0]) if any(t.pnl > 0 for t in self.trade_log) else 0,
            "avg_loser": np.mean([t.pnl for t in self.trade_log if t.pnl < 0]) if any(t.pnl < 0 for t in self.trade_log) else 0,
            "max_consecutive_losses": self._max_consecutive(lambda t: t.pnl < 0),
            "total_commission": sum(t.commission for t in self.trade_log),
            "total_slippage": sum(t.slippage_cost for t in self.trade_log),
            "equity_curve": equity_series,
            "trade_log": pd.DataFrame([t.__dict__ for t in self.trade_log]),
        }
```

**Fichiers Session 1** :
```
core/backtester_v2/
├── engine.py                      # ~300 lignes
├── event_queue.py                 # ~80 lignes
├── data_feed.py                   # ~150 lignes
├── execution_simulator.py         # ~250 lignes
├── portfolio_tracker.py           # ~300 lignes
├── risk_manager_backtest.py       # ~200 lignes
├── models.py                      # Dataclasses (Bar, Event, Fill, Order, Signal, etc.)
├── config.py                      # BacktestConfig dataclass
├── cost_models/
│   ├── __init__.py
│   ├── base.py                    # CostModel ABC
│   ├── ibkr_costs.py              # ~150 lignes
│   ├── binance_costs.py           # ~200 lignes
│   └── factory.py                 # CostModelFactory
├── calendars/
│   ├── __init__.py
│   ├── base.py                    # Calendar ABC
│   ├── us_calendar.py             # NYSE/NASDAQ
│   ├── eu_calendar.py             # Euronext
│   ├── fx_calendar.py             # FX 24/5
│   ├── futures_calendar.py        # CME
│   ├── crypto_calendar.py         # 24/7
│   └── factory.py
└── __init__.py

tests/test_backtester_v2/
├── test_engine.py                 # 20 tests
├── test_data_feed.py              # 15 tests
├── test_anti_lookahead.py         # 15 tests (CRITIQUE)
├── test_execution.py              # 20 tests
├── test_portfolio.py              # 15 tests
├── test_cost_ibkr.py              # 10 tests
├── test_cost_binance.py           # 12 tests
├── test_calendars.py              # 10 tests
└── __init__.py

TOTAL SESSION 1 : ~25 fichiers, ~2,200 lignes code, 117 tests
```


---

## SESSION 2 (~22h) — BACKTESTER V2 : WF + MC + MIGRATION STRATÉGIES
```
AGENT LEAD : BT-ARCH + QR
AGENTS SUPPORT : CODE-REV
TIMING : Semaine 1-2 (pendant soft launch IBKR)
BRANCHE : dev/backtester-v2
```

### S2.1 — Walk-Forward intégré
```yaml
temps: 6h
```

```python
# core/backtester_v2/walk_forward.py

class WalkForwardEngine:
    """
    Walk-forward INTÉGRÉ au backtester (pas un module externe).
    Supporte 3 modes : classique, expanding window, anchored.
    """
    
    def run(self, strategy_class, data, config: WFConfig) -> WFResult:
        """
        Pour chaque fenêtre train/test :
        1. Créer une instance fraîche de la stratégie
        2. Optimiser les paramètres sur train (grid search ou bayesian)
        3. Backtester avec les paramètres optimaux sur test
        4. Enregistrer les métriques OOS
        5. Agréger et verdict
        """
        windows = self._generate_windows(data, config)
        results = []
        
        for i, (train_data, test_data) in enumerate(windows):
            # 1. Optimiser sur train
            best_params = self._optimize(strategy_class, train_data, config)
            
            # 2. Backtester sur test avec les paramètres optimaux
            strategy = strategy_class(**best_params)
            bt = BacktesterV2(config.backtest_config)
            test_result = bt.run([strategy], test_data.index[0], test_data.index[-1])
            
            # 3. Backtester sur train pour comparaison IS vs OOS
            train_bt = BacktesterV2(config.backtest_config)
            train_result = train_bt.run([strategy_class(**best_params)], 
                                        train_data.index[0], train_data.index[-1])
            
            results.append(WFWindowResult(
                window=i,
                train_sharpe=train_result.sharpe,
                test_sharpe=test_result.sharpe,
                test_trades=test_result.total_trades,
                test_pnl=test_result.total_return,
                test_max_dd=test_result.max_drawdown,
                best_params=best_params,
            ))
        
        return self._aggregate(results, config)
    
    def _optimize(self, strategy_class, train_data, config) -> Dict:
        """
        Optimisation des paramètres.
        Méthode : grid search (exhaustif) pour < 100 combinaisons,
        bayesian (optuna) pour > 100 combinaisons.
        
        OBJECTIF : maximiser le Sharpe (pas le rendement total).
        CONTRAINTE : min 20 trades dans le train.
        """
        param_grid = strategy_class.get_parameter_grid()
        
        if self._grid_size(param_grid) < 100:
            return self._grid_search(strategy_class, train_data, param_grid, config)
        else:
            return self._bayesian_search(strategy_class, train_data, param_grid, config)
    
    def _aggregate(self, results: List[WFWindowResult], config) -> WFResult:
        """
        Agrégation et verdict.
        """
        oos_sharpes = [r.test_sharpe for r in results]
        is_sharpes = [r.train_sharpe for r in results]
        
        avg_oos = np.mean(oos_sharpes)
        avg_is = np.mean(is_sharpes)
        ratio = avg_oos / avg_is if avg_is > 0 else 0
        pct_profitable = sum(1 for s in oos_sharpes if s > 0) / len(oos_sharpes)
        
        if ratio >= config.min_ratio and pct_profitable >= config.min_profitable_pct:
            verdict = "VALIDATED"
        elif ratio >= config.min_ratio * 0.7 or pct_profitable >= 0.4:
            verdict = "BORDERLINE"
        else:
            verdict = "REJECTED"
        
        return WFResult(
            verdict=verdict,
            windows=results,
            avg_oos_sharpe=avg_oos,
            avg_is_sharpe=avg_is,
            oos_is_ratio=ratio,
            pct_profitable=pct_profitable,
        )
```

### S2.2 — Monte Carlo intégré
```yaml
temps: 4h
```

```python
# core/backtester_v2/monte_carlo.py

class MonteCarloEngine:
    """
    Monte Carlo intégré — permutation des trades pour estimer la robustesse.
    """
    
    def run(self, trade_log: pd.DataFrame, 
            n_simulations: int = 10000,
            initial_capital: float = 10000) -> MCResult:
        """
        1. Extraire les P&L de chaque trade
        2. Pour chaque simulation : permuter aléatoirement l'ordre des trades
        3. Calculer equity curve, Sharpe, max DD pour chaque permutation
        4. Construire les distributions
        """
        pnls = trade_log['pnl'].values
        
        results = {
            'sharpe': [], 'max_dd': [], 'final_equity': [],
            'max_consecutive_loss': [], 'calmar': [],
        }
        
        rng = np.random.default_rng(seed=42)  # Reproductible
        
        for _ in range(n_simulations):
            shuffled = rng.permutation(pnls)
            equity = initial_capital + np.cumsum(shuffled)
            
            # Métriques
            returns = np.diff(equity) / equity[:-1]
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
            peak = np.maximum.accumulate(equity)
            dd = (peak - equity) / peak
            max_dd = dd.max()
            
            results['sharpe'].append(sharpe)
            results['max_dd'].append(max_dd)
            results['final_equity'].append(equity[-1])
        
        return MCResult(
            median_sharpe=np.median(results['sharpe']),
            p5_sharpe=np.percentile(results['sharpe'], 5),
            p95_sharpe=np.percentile(results['sharpe'], 95),
            median_max_dd=np.median(results['max_dd']),
            p95_max_dd=np.percentile(results['max_dd'], 95),
            prob_profitable=sum(1 for e in results['final_equity'] if e > initial_capital) / n_simulations,
            prob_ruin=sum(1 for e in results['final_equity'] if e < initial_capital * 0.5) / n_simulations,
            distributions=results,
        )
```

### S2.3 — StrategyBase interface + migration des 16 stratégies
```yaml
temps: 12h
```

```python
# core/backtester_v2/strategy_base.py

class StrategyBase(ABC):
    """
    Interface commune pour TOUTES les stratégies.
    Compatible avec le BacktesterV2 ET le pipeline live.
    """
    
    def __init__(self, name: str, asset_class: str, broker: str):
        self.name = name
        self.asset_class = asset_class
        self.broker = broker
        self.data_feed = None  # Injecté par l'engine
    
    @abstractmethod
    def on_bar(self, bar: Bar, portfolio: PortfolioState) -> Optional[Signal]:
        """
        Appelé à chaque nouvelle candle FERMÉE.
        DOIT être déterministe et sans side effects.
        """
        ...
    
    @abstractmethod
    def get_parameters(self) -> Dict[str, Any]:
        """Paramètres actuels."""
        ...
    
    @abstractmethod
    def set_parameters(self, params: Dict[str, Any]):
        """Mettre à jour les paramètres (pour le WF optimizer)."""
        ...
    
    @classmethod
    def get_parameter_grid(cls) -> Dict[str, List]:
        """Grille de paramètres pour l'optimisation WF."""
        return {}
    
    def on_fill(self, fill: Fill):
        """Callback optionnel quand un ordre est exécuté."""
        pass
    
    def on_eod(self, timestamp: datetime):
        """Callback optionnel end-of-day."""
        pass
    
    def set_data_feed(self, feed: DataFeed):
        """Injecté par l'engine. Accès aux données historiques (anti-lookahead)."""
        self.data_feed = feed


# Exemple de migration : EUR/USD Trend
class EURUSDTrend(StrategyBase):
    """EUR/USD Trend Following — migré de la V1."""
    
    def __init__(self, ema_fast=20, ema_slow=50, adx_threshold=25, 
                 rsi_low=45, rsi_high=75):
        super().__init__(
            name="eurusd_trend", 
            asset_class="FX_MAJOR", 
            broker="IBKR"
        )
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.adx_threshold = adx_threshold
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
    
    def on_bar(self, bar: Bar, portfolio: PortfolioState) -> Optional[Signal]:
        if bar.symbol != "EURUSD":
            return None
        
        # Utiliser le data_feed (anti-lookahead garanti)
        ema_fast = self.data_feed.get_indicator("EURUSD", "ema", self.ema_fast)
        ema_slow = self.data_feed.get_indicator("EURUSD", "ema", self.ema_slow)
        adx = self.data_feed.get_indicator("EURUSD", "adx", 14)
        rsi = self.data_feed.get_indicator("EURUSD", "rsi", 14)
        
        if any(math.isnan(v) for v in [ema_fast, ema_slow, adx, rsi]):
            return None
        
        # Signal long
        if (bar.close > ema_slow and ema_fast > ema_slow and 
            adx > self.adx_threshold and self.rsi_low < rsi < self.rsi_high):
            return Signal(
                symbol="EURUSD", side="BUY", strategy_name=self.name,
                stop_loss=bar.close - 2.5 * self.data_feed.get_indicator("EURUSD", "atr", 14),
                take_profit=bar.close + 4 * self.data_feed.get_indicator("EURUSD", "atr", 14),
            )
        
        return None
    
    def get_parameters(self) -> Dict:
        return {
            "ema_fast": self.ema_fast, "ema_slow": self.ema_slow,
            "adx_threshold": self.adx_threshold,
            "rsi_low": self.rsi_low, "rsi_high": self.rsi_high,
        }
    
    def set_parameters(self, params: Dict):
        for k, v in params.items():
            setattr(self, k, v)
    
    @classmethod
    def get_parameter_grid(cls) -> Dict[str, List]:
        return {
            "ema_fast": [10, 15, 20, 25],
            "ema_slow": [40, 50, 60],
            "adx_threshold": [20, 25, 30],
            "rsi_low": [40, 45, 50],
            "rsi_high": [70, 75, 80],
        }
```

**Migration des 16 stratégies** :
```
IBKR (8) :
  □ EURUSDTrend        → strategies_v2/fx/eurusd_trend.py
  □ EURGBPMeanReversion → strategies_v2/fx/eurgbp_mr.py
  □ EURJPYCarry        → strategies_v2/fx/eurjpy_carry.py
  □ AUDJPYCarry        → strategies_v2/fx/audjpy_carry.py
  □ GBPUSDTrend        → strategies_v2/fx/gbpusd_trend.py
  □ EUGapOpen          → strategies_v2/eu/eu_gap_open.py
  □ MCLBrentLag        → strategies_v2/futures/mcl_brent_lag.py
  □ MESTrend           → strategies_v2/futures/mes_trend.py

Crypto Binance (8) :
  □ BTCETHDualMomentum → strategies_v2/crypto/btc_eth_momentum.py
  □ AltcoinRS          → strategies_v2/crypto/altcoin_rs.py
  □ BTCMeanReversion   → strategies_v2/crypto/btc_mr.py
  □ VolBreakout        → strategies_v2/crypto/vol_breakout.py
  □ BTCDominance       → strategies_v2/crypto/btc_dominance.py
  □ BorrowRateCarry    → strategies_v2/crypto/borrow_carry.py
  □ LiquidationMomentum → strategies_v2/crypto/liquidation_momentum.py
  □ WeekendGap         → strategies_v2/crypto/weekend_gap.py

POUR CHAQUE MIGRATION :
  □ Adapter à l'interface StrategyBase
  □ Tester que le backtest V2 produit des résultats similaires au V1 (±5%)
  □ Walk-forward V2 → résultat >= V1
  □ 5+ tests unitaires par stratégie
```

**Fichiers Session 2** :
```
TOTAL SESSION 2 : ~20 fichiers, ~3,000 lignes code, 95 tests
  walk_forward.py + monte_carlo.py + strategy_base.py = ~700 lignes
  16 stratégies migrées = ~2,300 lignes (moyenne 140 lignes/strat)
  Tests : 10 (WF) + 10 (MC) + 75 (16 strats * ~5 tests) = 95
```

---

## SESSION 3 (~20h) — HARDENING : FUZZING + STRESS TESTS
```
AGENT LEAD : SEC-AUD
AGENTS SUPPORT : RISK-ENG, INFRA, CODE-REV
TIMING : Semaine 2 (pendant le soft launch IBKR)
BRANCHE : dev/hardening
```

### S3.1 — Fuzzer de trading (28 scénarios)
```yaml
temps: 10h
```

```python
# core/hardening/trading_fuzzer.py

class TradingFuzzer:
    """
    Injecte des conditions extrêmes et vérifie la résilience.
    28 scénarios couvrant : prix, volume, connectivité, ordres, margin, système, data.
    """
    
    SCENARIOS = {
        # PRIX (7 scénarios)
        "price_spike_50pct": {
            "inject": lambda s: s.set_price("BTCUSDT", s.current_price * 1.5),
            "expect": "circuit_breaker_triggered OR position_stopped",
            "must_not": "crash OR unhandled_exception",
        },
        "flash_crash_30pct": {
            "inject": lambda s: s.set_price("BTCUSDT", s.current_price * 0.7),
            "expect": "kill_switch_triggered AND all_positions_closed",
            "must_not": "crash",
        },
        "price_zero": {
            "inject": lambda s: s.set_price("ETHUSDT", 0),
            "expect": "error_logged AND trade_rejected",
            "must_not": "division_by_zero OR crash",
        },
        "price_negative": {
            "inject": lambda s: s.set_price("EURUSD", -1.0),
            "expect": "error_logged AND trade_rejected",
            "must_not": "crash",
        },
        "price_nan": {
            "inject": lambda s: s.set_price("BTCUSDT", float('nan')),
            "expect": "error_logged AND signal_skipped",
            "must_not": "crash OR nan_propagation",
        },
        "price_inf": {
            "inject": lambda s: s.set_price("BTCUSDT", float('inf')),
            "expect": "error_logged AND trade_rejected",
            "must_not": "crash",
        },
        "price_unchanged_24h": {
            "inject": lambda s: s.freeze_price("BTCUSDT", hours=24),
            "expect": "no_trades_generated (volume filter catches it)",
            "must_not": "crash",
        },
        
        # CONNECTIVITÉ (6 scénarios)
        "ibkr_disconnect_5min": {
            "inject": lambda s: s.disconnect_broker("IBKR", minutes=5),
            "expect": "warning_alert AND reconnect_within_60s",
            "must_not": "orders_sent_during_disconnect",
        },
        "binance_disconnect_30min": {
            "inject": lambda s: s.disconnect_broker("BINANCE", minutes=30),
            "expect": "critical_alert AND strategies_paused AND reconnect",
            "must_not": "crash OR data_loss",
        },
        "binance_ws_down_rest_up": {
            "inject": lambda s: s.disconnect_ws("BINANCE"),
            "expect": "fallback_to_rest_polling",
            "must_not": "crash OR missed_signals",
        },
        "latency_spike_5s": {
            "inject": lambda s: s.set_latency("IBKR", ms=5000),
            "expect": "warning_alert AND order_timeout_handled",
            "must_not": "duplicate_orders",
        },
        "dns_failure": {
            "inject": lambda s: s.block_dns(seconds=60),
            "expect": "reconnect_with_backoff",
            "must_not": "crash OR permanent_failure",
        },
        "rate_limit_429": {
            "inject": lambda s: s.trigger_rate_limit("BINANCE"),
            "expect": "backoff_and_retry",
            "must_not": "ban OR crash",
        },
        
        # ORDRES (5 scénarios)
        "partial_fill_30pct": {
            "inject": lambda s: s.set_fill_rate(0.3),
            "expect": "partial_position_tracked AND remainder_cancelled_or_retried",
            "must_not": "position_size_mismatch",
        },
        "double_fill": {
            "inject": lambda s: s.duplicate_fill("order_123"),
            "expect": "second_fill_ignored (idempotence)",
            "must_not": "double_position",
        },
        "order_rejected_margin": {
            "inject": lambda s: s.reject_order("INSUFFICIENT_MARGIN"),
            "expect": "rejection_logged AND no_position_opened",
            "must_not": "phantom_position",
        },
        "order_timeout_60s": {
            "inject": lambda s: s.timeout_order(seconds=60),
            "expect": "order_cancelled AND alert_sent",
            "must_not": "zombie_order",
        },
        "stop_loss_rejected": {
            "inject": lambda s: s.reject_stop_loss("BTCUSDT"),
            "expect": "critical_alert AND position_closed_market",
            "must_not": "position_without_stop",
        },
        
        # MARGIN (4 scénarios)
        "margin_call_binance": {
            "inject": lambda s: s.set_margin_level("BINANCE", 1.05),
            "expect": "emergency_reduce AND critical_alert",
            "must_not": "liquidation (we should close before Binance does)",
        },
        "borrow_rate_spike_10x": {
            "inject": lambda s: s.set_borrow_rate("SOL", rate=0.5),  # 50%/jour
            "expect": "short_closed AND alert_sent",
            "must_not": "continued_borrowing_at_insane_rate",
        },
        "borrow_unavailable": {
            "inject": lambda s: s.set_borrow_available("ETH", 0),
            "expect": "short_signal_skipped AND logged",
            "must_not": "failed_borrow_attempt",
        },
        "earn_rate_zero": {
            "inject": lambda s: s.set_earn_rate("USDT", 0),
            "expect": "earn_rebalance_triggered",
            "must_not": "crash",
        },
        
        # SYSTÈME (3 scénarios)
        "worker_crash_restart": {
            "inject": lambda s: s.kill_worker(restart_after=30),
            "expect": "state_restored AND reconciliation_ok",
            "must_not": "data_loss OR duplicate_orders",
        },
        "disk_full": {
            "inject": lambda s: s.fill_disk(remaining_mb=5),
            "expect": "critical_alert AND graceful_degradation",
            "must_not": "crash OR silent_data_loss",
        },
        "memory_95pct": {
            "inject": lambda s: s.consume_memory(pct=95),
            "expect": "warning_alert AND gc_triggered",
            "must_not": "OOM_kill",
        },
        
        # DATA (3 scénarios)
        "data_gap_2h": {
            "inject": lambda s: s.remove_candles("BTCUSDT", hours=2),
            "expect": "gap_detected AND signals_paused_until_data_resumes",
            "must_not": "stale_signal OR crash",
        },
        "duplicate_candle": {
            "inject": lambda s: s.duplicate_candle("EURUSD"),
            "expect": "duplicate_filtered AND single_signal",
            "must_not": "double_signal",
        },
        "corrupted_candle": {
            "inject": lambda s: s.corrupt_candle("BTCUSDT", field="close", value=-999),
            "expect": "validation_catch AND candle_dropped",
            "must_not": "crash OR signal_on_bad_data",
        },
    }
    
    def run_all(self, system) -> FuzzReport:
        """
        Exécute les 28 scénarios et produit un rapport.
        CRITÈRE : 28/28 PASS requis.
        """
        results = []
        for name, scenario in self.SCENARIOS.items():
            result = self._run_scenario(system, name, scenario)
            results.append(result)
            if not result.passed:
                logger.critical(f"FUZZING FAIL: {name} — {result.error}")
        
        return FuzzReport(
            total=len(results),
            passed=sum(1 for r in results if r.passed),
            failed=[r for r in results if not r.passed],
        )
```

### S3.2 — Stress tests sur 9 crises historiques
```yaml
temps: 8h
```

```python
# core/hardening/stress_tests.py

class HistoricalStressTest:
    """
    Rejouer les pires jours sur le portefeuille actuel.
    """
    
    CRISES = {
        "covid_2020_03_16": {
            "assets": {"SPY": -0.12, "EURUSD": -0.01, "BTC": -0.25},
            "vix": 82, "duration_days": 5,
            "expected_max_loss_ibkr_pct": 8,
            "expected_max_loss_crypto_pct": 18,
        },
        "flash_crash_2010_05_06": {
            "assets": {"SPY": -0.09},
            "duration_hours": 0.5, "recovery_hours": 1,
            "expected": "circuit_breaker_pause, not kill_switch",
        },
        "volmageddon_2018_02_05": {
            "assets": {"SPY": -0.04, "VIX": +1.15},
            "expected_max_loss_ibkr_pct": 5,
        },
        "luna_crash_2022_05_09": {
            "assets": {"BTC": -0.20, "ETH": -0.25, "SOL": -0.40},
            "duration_days": 3,
            "expected_max_loss_crypto_pct": 15,
            "expected": "kill_switch_crypto_triggered",
        },
        "ftx_collapse_2022_11_08": {
            "assets": {"BTC": -0.25, "ETH": -0.30},
            "duration_days": 7,
            "expected": "kill_switch_crypto_triggered",
        },
        "btc_flash_2021_05_19": {
            "assets": {"BTC": -0.30},
            "duration_hours": 24,
            "expected_max_loss_crypto_pct": 12,
        },
        "snb_shock_2015_01_15": {
            "assets": {"EURCHF": -0.30, "EURUSD": -0.03},
            "duration_minutes": 10,
            "expected": "fx_stops_executed, slippage_tracked",
        },
        "gbp_flash_2016_10_07": {
            "assets": {"GBPUSD": -0.06},
            "duration_minutes": 2,
            "expected": "stop_executed_with_slippage",
        },
        "correlation_spike": {
            "all_assets_correlation": 0.9,
            "duration_days": 7,
            "expected": "cross_portfolio_guard_alert",
        },
    }
    
    def run_all(self, current_portfolio) -> StressReport:
        """
        Pour chaque crise :
        1. Configurer le portefeuille comme aujourd'hui
        2. Appliquer les mouvements de la crise
        3. Laisser le risk manager et les kill switches agir
        4. Mesurer : perte, temps de réaction, positions fermées
        5. Vérifier vs les attendus
        """
        ...
```

### S3.3 — Tests de résilience (5 scénarios end-to-end)
```yaml
temps: 4h
```

Les 5 tests R1-R5 décrits dans la V8.0 initiale : worker restart, PostgreSQL crash, réseau intermittent, disk full, multi-kill switch simultané.

**Fichiers Session 3** :
```
core/hardening/
├── trading_fuzzer.py          # 28 scénarios
├── stress_tests.py            # 9 crises
├── resilience_tests.py        # 5 tests E2E
├── scenario_injector.py       # Injection de conditions
└── reports.py                 # Génération rapports

tests/test_hardening/
├── test_fuzzer.py             # 28 tests (1 par scénario)
├── test_stress.py             # 9 tests (1 par crise)
├── test_resilience.py         # 5 tests
└── test_injector.py           # 10 tests

TOTAL SESSION 3 : ~10 fichiers, ~2,500 lignes, 52 tests
```

---

## SESSION 4 (~22h) — ML PIPELINE : FEATURES + SELECTION
```
AGENT LEAD : ML-ENG
AGENTS SUPPORT : QR, RISK-ENG, CODE-REV
TIMING : Semaine 3-4 (après 50+ trades live)
BRANCHE : dev/ml-pipeline
```

### S4.1 — Feature Engine (86 features)
```yaml
temps: 14h
```

L'intégralité du FeatureEngine décrit dans le C2.1 de la TODO V8.0 initiale.
86 features en 9 catégories : price (15), volume (10), volatility (12), momentum (10), microstructure (8), cross-asset (8), calendar (8), strategy-specific (5), crypto-specific (10).

**POINT CLÉ** : même si le ML ne sera pas entraîné avant le mois 3, le feature engine COLLECTE les features dès le premier trade live. Chaque signal génère un vecteur de 86 features stocké dans `data/ml/features/`. Quand on aura 200+ trades, les données seront prêtes.

```python
# core/ml/feature_collector.py

class FeatureCollector:
    """
    S'intègre dans le pipeline live dès le jour 1.
    Collecte les features pour chaque signal SANS affecter l'exécution.
    
    MODE : collecte passive (pas de filtrage, pas de prédiction).
    Le ML filter sera activé en Session 9 quand les données seront prêtes.
    """
    
    def on_signal(self, signal, market_state, portfolio_state):
        features = self.feature_engine.compute(signal, market_state, portfolio_state)
        self.store.save(signal.id, signal.strategy, features)
        # NE PAS filtrer — juste collecter
    
    def on_trade_close(self, trade):
        """Mettre à jour le target (profitable ou non) quand le trade se ferme."""
        self.store.update_target(trade.signal_id, trade.pnl > 0)
```

### S4.2 — Feature Selection
```yaml
temps: 8h
```

Pipeline de sélection en 4 étapes (corrélation, MI, Boruta, WF stability) comme décrit dans C2.2. Inclut l'anti-lookahead validator qui vérifie que CHAQUE feature est calculée avec des données passées uniquement.

**Fichiers Session 4** :
```
core/ml/
├── feature_engine.py          # 86 features, 9 catégories
├── feature_store.py           # Stockage Parquet
├── feature_selection.py       # 4 étapes de sélection
├── feature_collector.py       # Collecte passive live
├── target_builder.py          # Construction des targets
├── anti_lookahead_validator.py # Validation critique
└── indicators.py              # Calcul d'indicateurs techniques

tests/test_ml/
├── test_feature_engine.py     # 25 tests
├── test_feature_selection.py  # 12 tests
├── test_anti_lookahead_ml.py  # 15 tests
├── test_collector.py          # 8 tests
└── test_target_builder.py     # 10 tests

TOTAL SESSION 4 : ~12 fichiers, ~2,800 lignes, 70 tests
```

---

## SESSION 5 (~22h) — ML PIPELINE : LIGHTGBM + FILTER LIVE
```
AGENT LEAD : ML-ENG
TIMING : Semaine 4-5 (après 100+ trades live, données collectées par S4)
BRANCHE : dev/ml-pipeline
```

### S5.1 — Walk-Forward ML + LightGBM Training
```yaml
temps: 12h
```

WalkForwardML complet comme décrit dans C2.3 : TimeSeriesSplit (pas random), hyperparameter tuning sur train uniquement, AUC OOS > 0.55, ratio AUC train/test < 1.3.

### S5.2 — Trade Filter + Drift Detection + Intégration live
```yaml
temps: 10h
```

MLTradeFilter + MLDriftDetector comme décrits dans C2.4. Le filter s'insère entre le signal generator et l'order executor. Le drift detector désactive automatiquement le ML si AUC < 0.50.

**Fichiers Session 5** :
```
core/ml/
├── walk_forward_ml.py         # WF ML engine
├── lgbm_trainer.py            # LightGBM training
├── hyperparameter_tuner.py    # Optuna/grid search
├── trade_filter.py            # MLTradeFilter live
├── drift_detector.py          # Monitoring dégradation
├── model_registry.py          # Versioning modèles
└── ml_monitor.py              # Dashboard métriques ML

tests/test_ml/
├── test_wf_ml.py              # 15 tests
├── test_lgbm.py               # 10 tests
├── test_trade_filter.py       # 12 tests
├── test_drift.py              # 8 tests
└── test_integration_ml.py     # 10 tests

TOTAL SESSION 5 : ~12 fichiers, ~2,500 lignes, 55 tests
```

---

## SESSION 6 (~22h) — ALPHA RESEARCH : 5 PREMIÈRES STRATÉGIES
```
AGENT LEAD : QR
AGENTS SUPPORT : BT-ARCH, RISK-ENG, CODE-REV
TIMING : Mois 2 (après Gate M1, avec le backtester V2 validé)
BRANCHE : dev/alpha-research
```

### S6.1 — Recherche et implémentation (5 edges)
```yaml
temps: 22h (4-5h par stratégie)
```

Les 5 stratégies les plus prometteuses du C3.1 :

```
1. ETH/BTC Ratio Mean Reversion (spot Binance)
   - Le ratio oscille entre 0.03 et 0.08 avec mean reversion
   - Spot only : long ETH quand ratio bas, long BTC quand ratio haut
   - ~40 trades/an, Sharpe cible 1.0-1.5

2. Funding-Momentum Divergence (lecture perp, trade margin)
   - Prix monte mais funding rate baisse = divergence bearish
   - Signal contrarian via les données perp en lecture seule
   - ~30 trades/an, Sharpe cible 1.0-2.0

3. Term Structure Momentum (futures IBKR)
   - Contango MCL → short, backwardation → long
   - Front vs next month ratio comme signal
   - ~20 trades/an, Sharpe cible 0.8-1.5

4. Intraday Volume Profile VWAP (equities, futur Alpaca)
   - Acheter sous VWAP pendant le mid-day lull
   - Tenir jusqu'au retour vers VWAP
   - ~100 trades/an, Sharpe cible 1.0-1.5

5. Stablecoin Supply Ratio (crypto)
   - Stablecoins sur exchanges / BTC market cap
   - Ratio élevé = fuel pour la hausse
   - Signal macro mensuel, ~12 trades/an
```

Pour chaque stratégie : code StrategyBase, backtest V2, walk-forward, Monte Carlo, 5+ tests.

**Fichiers Session 6** :
```
strategies_v2/research/
├── eth_btc_ratio_mr.py
├── funding_divergence.py
├── term_structure_momentum.py
├── vwap_reversion.py
├── stablecoin_ratio.py

tests/test_research/
├── test_eth_btc_ratio.py      # 8 tests
├── test_funding_divergence.py # 8 tests
├── test_term_structure.py     # 6 tests
├── test_vwap_reversion.py     # 8 tests
├── test_stablecoin_ratio.py   # 6 tests

backtests/research/
├── wf_results_all.json        # Résultats WF pour les 5 strats
└── mc_results_all.json        # Résultats MC

TOTAL SESSION 6 : ~15 fichiers, ~2,000 lignes, 36 tests
```

---

## SESSION 7 (~20h) — ALPHA RESEARCH : 5 STRATS SUIVANTES + ATTRIBUTION
```
AGENT LEAD : QR + PERF-AN
TIMING : Mois 2-3
BRANCHE : dev/alpha-research
```

### S7.1 — 5 stratégies suivantes
```yaml
temps: 12h
```

```
6. PEAD (Post-Earnings Announcement Drift)
   - Nécessite données earnings (estimations vs actuals)
   - Source : Financial Modeling Prep API ou Alpha Vantage
   - Acheter après surprise > 2σ, tenir 20-60j

7. Cross-Asset Lead-Lag (bonds → equities)
   - TLT move → SPY suit avec 1-4h de retard
   - Signal : TLT drop > 0.5% en 4h → short SPY

8. Miner Flow Analysis (crypto on-chain)
   - CryptoQuant API pour les flux mineurs
   - Mineurs envoient BTC aux exchanges = selling pressure

9. Weekday Seasonality V2 (crypto améliorée)
   - Lundi = plus volatil, vendredi = drift
   - Ajuster le sizing par jour de la semaine

10. BTC Halving Cycle (macro crypto)
    - Pattern 4 ans documenté : accumulation → euphorie → crash → flat
    - Position sizing macro basé sur la phase du cycle
```

### S7.2 — Performance Attribution + Factor Analysis
```yaml
temps: 8h
```

Module complet : décomposition factorielle, Sharpe par stratégie/asset/direction, analyse par régime. Comme décrit dans C3.2.

**Fichiers Session 7** :
```
strategies_v2/research/ (5 nouvelles strats)
core/analytics/ (attribution, factors, regime)
tests/ (36 tests strats + 30 tests analytics)

TOTAL SESSION 7 : ~15 fichiers, ~2,500 lignes, 66 tests
```

---

## SESSION 8 (~20h) — OPTIONS OVERLAY (IBKR)
```
AGENT LEAD : OPT-SPEC
TIMING : Mois 4+ (quand capital IBKR > $25K via scaling)
BRANCHE : dev/options
```

Infrastructure options IBKR + 3 stratégies (protective puts, covered calls, vol selling) comme décrits dans C4. Inclut : adapter options, Greeks calculator, IV surface builder, 3 strategies StrategyBase, backtest avec le V2.

**Fichiers Session 8** :
```
core/broker/ibkr_options.py
core/options/
├── greeks_calculator.py
├── iv_surface.py
├── options_risk.py
strategies_v2/options/
├── protective_puts.py
├── covered_calls.py
├── vol_selling.py
tests/test_options/ (40 tests)

TOTAL SESSION 8 : ~12 fichiers, ~2,000 lignes, 40 tests
```

---

## SESSION 9 (~18h) — ML ACTIVATION + MONITORING AVANCÉ
```
AGENT LEAD : ML-ENG + INFRA
TIMING : Mois 3 (quand 200+ trades collectés)
BRANCHE : dev/ml-pipeline (merge final)
```

### S9.1 — Entraînement ML sur données live réelles
```yaml
temps: 8h
```

Utiliser les features collectées (S4) sur les 200+ trades live réels. Entraîner le LightGBM avec WF ML (S5). Calibrer les seuils de filtrage. Valider que l'AUC OOS > 0.55 sur les données live.

### S9.2 — Activation du ML Filter en production
```yaml
temps: 4h
```

Intégrer le MLTradeFilter dans le pipeline live. Mode shadow d'abord (1 semaine — le filter prédit mais n'agit pas, on compare). Puis activation progressive.

### S9.3 — Dashboard avancé ML + Performance
```yaml
temps: 6h
```

Dashboard React avec : AUC rolling, feature importance, taux de filtrage, comparaison trades passés vs filtrés, drift detection status. S'intègre au dashboard existant.

**Fichiers Session 9** :
```
core/ml/ (compléments entraînement live)
dashboard/ml_panel.py
tests/ (20 tests)

TOTAL SESSION 9 : ~8 fichiers, ~1,500 lignes, 20 tests
```

---

## SESSION 10 (~18h) — INFRASTRUCTURE POSTGRESQL + GRAFANA
```
AGENT LEAD : DATA-ENG + INFRA
TIMING : Mois 3-4 (quand SQLite montre ses limites)
BRANCHE : dev/infra
```

### S10.1 — Migration PostgreSQL
```yaml
temps: 10h
```

Schéma SQL complet (comme décrit dans C5.1), scripts de migration, adapter pattern pour transition SQLite → PostgreSQL, rollback possible pendant 30 jours.

### S10.2 — Monitoring Grafana + Prometheus
```yaml
temps: 8h
```

5 dashboards (Overview, Risk, Execution, ML, Stratégies) + exporteur de métriques Prometheus + docker-compose monitoring.

**Fichiers Session 10** :
```
core/database/
├── postgres_adapter.py
├── migration_scripts/
├── schema.sql
monitoring/
├── prometheus_exporter.py
├── grafana_dashboards/
├── docker-compose.monitoring.yml

TOTAL SESSION 10 : ~15 fichiers, ~2,000 lignes, 25 tests
```

---

## SESSION 11 (~15h) — INTÉGRATION FINALE + CI/CD + DOCUMENTATION
```
AGENT LEAD : CODE-REV + INFRA
TIMING : Après toutes les sessions
BRANCHE : merge vers main
```

### S11.1 — Merge et intégration
```yaml
temps: 6h
```

Merge toutes les branches dev vers main. Résoudre les conflits. Vérifier que TOUS les tests passent (3,000+).

### S11.2 — CI/CD amélioré
```yaml
temps: 4h
```

GitHub Actions : pytest + coverage 80% + ruff + mypy + bandit security scan. Tests d'intégration en paper mode.

### S11.3 — Documentation
```yaml
temps: 5h
```

README principal mis à jour, architecture diagram, onboarding guide, runbook V2 mis à jour, API documentation.

---

## RÉSUMÉ GLOBAL

```
┌──────────┬─────────────────────────────────────────┬───────┬────────┬──────────────┐
│ Session  │ Contenu                                 │ Heures│ Tests  │ Timing       │
├──────────┼─────────────────────────────────────────┼───────┼────────┼──────────────┤
│ S1       │ Backtester V2 : architecture + anti-LA  │ 25h   │ 117    │ Semaine 1    │
│ S2       │ Backtester V2 : WF + MC + migration     │ 22h   │ 95     │ Semaine 1-2  │
│ S3       │ Hardening : fuzzing + stress tests       │ 20h   │ 52     │ Semaine 2    │
│ S4       │ ML : features + selection                │ 22h   │ 70     │ Semaine 3-4  │
│ S5       │ ML : LightGBM + filter + drift           │ 22h   │ 55     │ Semaine 4-5  │
│ S6       │ Alpha : 5 premières stratégies           │ 22h   │ 36     │ Mois 2       │
│ S7       │ Alpha : 5 strats + attribution           │ 20h   │ 66     │ Mois 2-3     │
│ S8       │ Options overlay (IBKR)                   │ 20h   │ 40     │ Mois 4+      │
│ S9       │ ML activation live + dashboard           │ 18h   │ 20     │ Mois 3       │
│ S10      │ PostgreSQL + Grafana                     │ 18h   │ 25     │ Mois 3-4     │
│ S11      │ Intégration + CI/CD + docs               │ 15h   │ 0      │ Après tout   │
├──────────┼─────────────────────────────────────────┼───────┼────────┼──────────────┤
│ TOTAL    │                                         │ 224h  │ 576    │ 12 semaines  │
└──────────┴─────────────────────────────────────────┴───────┴────────┴──────────────┘

TESTS TOTAL APRÈS V8.0 : 1,700 (existants) + 576 (nouveaux) = ~2,276

CODE TOTAL APRÈS V8.0 : ~118K (existant) + ~25K (nouveau) = ~143K lignes

MODULES TOTAL : 47 (existants) + ~20 (nouveaux) = ~67 modules
```

---

*TODO V8.0 FINAL — Dual Track Parallèle*
*11 sessions | 224h | 576 tests | 8 agents*
*Track 1 : LIVE dès J4 | Track 2 : Fondations en parallèle*
*"Le meilleur moment pour planter un arbre c'est il y a 20 ans. Le deuxième meilleur moment c'est maintenant — mais sans retarder la récolte."*
