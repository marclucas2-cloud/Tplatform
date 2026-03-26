# MISSION MAÎTRE — TRADING PLATFORM V2
# Synthèse Claude Opus + ChatGPT + IA Tierce
# Date : 27 mars 2026 | Exécution : multi-agents parallèle
# Objectif : passer de 17 stratégies Alpaca-only à 25+ stratégies multi-broker

---

## ARCHITECTURE MULTI-AGENTS

```
Ce document est conçu pour être exécuté par PLUSIEURS agents Claude Code 
en parallèle. Chaque agent prend UN TRACK et l'exécute de A à Z.

┌─────────────────────────────────────────────────────────────┐
│                    MARC (PO / Décideur)                      │
│                                                               │
│  Ouvre le compte IBKR paper pendant que les agents bossent    │
│  Valide les résultats de chaque track                         │
│  Décide des déploiements                                      │
└───────┬───────────┬───────────┬───────────┬─────────────────┘
        │           │           │           │
   ┌────▼────┐ ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
   │ AGENT 1 │ │ AGENT 2 │ │ AGENT 3 │ │ AGENT 4 │
   │  INFRA  │ │ STRATS  │ │  RISK   │ │ OPTIM   │
   │  IBKR   │ │ SHORT   │ │  ALLOC  │ │ EXIST   │
   └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

RÈGLE CRITIQUE : Chaque agent travaille dans son propre dossier/branch.
Pas de conflit de fichiers entre les agents.

---

## TRACK 1 — AGENT INFRA : SETUP IBKR + MULTI-BROKER

```
DOSSIER DE TRAVAIL : core/ibkr_client/
DURÉE ESTIMÉE : 4-6 heures
PRÉREQUIS : Marc ouvre un compte IBKR paper (TWS ou Gateway)
```

### 1.1 Objectif

Intégrer Interactive Brokers au pipeline existant pour pouvoir trader :
- Options US (SPY, QQQ 0DTE et weeklies)
- Futures (ES, NQ)
- Forex (EUR/USD, USD/JPY, AUD/JPY)
- Actions EU (via IBKR)

### 1.2 Étapes

```
ÉTAPE 1 : Installer et configurer ib_insync (2h)

  pip install ib_insync

  Créer core/ibkr_client/__init__.py
  Créer core/ibkr_client/client.py :
  
  Le client IBKR doit :
  - Se connecter à IB Gateway (port 4002 paper, 4001 live)
  - Supporter les ordres : Stock, Option, Future, Forex
  - Implémenter les bracket orders (comme Alpaca)
  - Avoir le même guard _authorized_by que le client Alpaca
  - Avoir un guard PAPER_ONLY (vérifier que port = 4002)
  - Gérer les reconnexions automatiques
  - Logger tous les ordres
  
  Structure du client :
  
  class IBKRClient:
      def __init__(self):
          self.ib = IB()
          self._authorized_by = None
          
      def connect(self, host='127.0.0.1', port=4002, client_id=1):
          if port != 4002:
              raise RuntimeError("PAPER ONLY - port must be 4002")
          self.ib.connect(host, port, clientId=client_id)
          
      def place_stock_order(self, symbol, qty, side, 
                            stop_loss=None, take_profit=None,
                            authorized_by=None):
          if authorized_by is None:
              raise RuntimeError("Order must be authorized by pipeline")
          self._authorized_by = authorized_by
          
          contract = Stock(symbol, 'SMART', 'USD')
          # Bracket order si SL/TP fournis
          ...
          
      def place_option_order(self, symbol, expiry, strike, right, 
                              qty, side, authorized_by=None):
          ...
          
      def place_futures_order(self, symbol, expiry, qty, side,
                               authorized_by=None):
          ...
          
      def place_forex_order(self, pair, qty_base, side,
                             authorized_by=None):
          ...
          
      def get_positions(self) -> list:
          ...
          
      def get_account_summary(self) -> dict:
          ...
          
      def get_options_chain(self, symbol) -> list:
          ...
          
      def close_all_positions(self, authorized_by=None):
          ...


ÉTAPE 2 : Créer le Multi-Broker Manager (2h)

  Créer core/multi_broker_manager.py :
  
  Ce module orchestre les ordres entre Alpaca et IBKR.
  Chaque stratégie est taguée avec son broker cible.
  
  class MultiBrokerManager:
      def __init__(self):
          self.alpaca = AlpacaClient()
          self.ibkr = IBKRClient()
          
      def submit_order(self, order: Order):
          if order.broker == 'alpaca':
              return self.alpaca.submit_bracket_order(...)
          elif order.broker == 'ibkr':
              return self.ibkr.place_stock_order(...)
              
      def get_all_positions(self) -> dict:
          positions = {}
          positions['alpaca'] = self.alpaca.get_positions()
          positions['ibkr'] = self.ibkr.get_positions()
          return positions
          
      def get_total_equity(self) -> float:
          alpaca_eq = self.alpaca.get_account()['equity']
          ibkr_eq = self.ibkr.get_account_summary()['equity']
          return alpaca_eq + ibkr_eq
          
      def close_all(self, authorized_by):
          self.alpaca.close_all_positions(authorized_by)
          self.ibkr.close_all_positions(authorized_by)


ÉTAPE 3 : Tests (1h)

  Créer tests/test_ibkr_client.py :
  
  - test_paper_only_guard : connexion port 4001 doit échouer
  - test_authorized_by_required : ordre sans authorized_by doit échouer
  - test_stock_order : ordre stock basic
  - test_option_order : ordre option basic
  - test_bracket_order : SL/TP attachés
  - test_get_positions : récupération positions
  - test_reconnection : déconnexion puis reconnexion
  
  NOTE : Si IBKR n'est pas encore configuré par Marc, créer les tests
  en mode mock (simuler les réponses IB Gateway).


ÉTAPE 4 : Documentation (30min)

  Créer docs/IBKR_SETUP.md :
  
  Guide pas-à-pas pour Marc :
  1. Créer un compte IBKR paper sur ibkr.com
  2. Télécharger IB Gateway (pas TWS — plus léger)
  3. Configurer : API Settings → Enable ActiveX and Socket Clients
  4. Port 4002 pour paper
  5. Configurer sur Railway : docker avec IB Gateway headless
  6. Variables d'environnement : IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID


ÉTAPE 5 : Intégration dashboard (1h)

  Ajouter les endpoints multi-broker dans dashboard/api/main.py :
  
  GET /api/brokers → liste des brokers connectés + status
  GET /api/brokers/{broker}/positions → positions par broker
  GET /api/brokers/{broker}/orders → ordres par broker
  GET /api/portfolio/combined → portfolio combiné multi-broker
```

### 1.3 Livrables

```
FICHIERS CRÉÉS :
  core/ibkr_client/__init__.py
  core/ibkr_client/client.py
  core/multi_broker_manager.py
  tests/test_ibkr_client.py
  docs/IBKR_SETUP.md
  
FICHIERS MODIFIÉS :
  dashboard/api/main.py (nouveaux endpoints)
  requirements.txt (ajouter ib_insync)
```

---

## TRACK 2 — AGENT STRATS : NOUVELLES STRATÉGIES SHORT + OPTIONS + FUTURES

```
DOSSIER DE TRAVAIL : strategies/ + intraday-backtesterV2/strategies/
DURÉE ESTIMÉE : 8-10 heures
PRÉREQUIS : Données Alpaca en cache (207 tickers, 6 mois)
```

### 2.1 Objectif

Coder, backtester et valider 15 nouvelles stratégies réparties en 4 catégories.
Objectif : 5-8 winners pour passer de 17 à 22-25 stratégies actives.

### 2.2 Stratégies à implémenter et backtester

```
CATÉGORIE A — SHORT INTRADAY (6 stratégies, Alpaca)
Consensus des 3 IA : le portefeuille est 75% long, il faut rééquilibrer.

A1. Bear Morning Fade (short-only, bear regime)
  - Gap UP > 0.8% en régime bear + SPY en baisse > 0.2% à 9:45
  - La barre 9:40-9:45 est rouge + volume > 1.5x
  - Stock sous SMA(20) daily
  - SHORT. Stop = high du jour + 0.2%. Target = close veille
  - FILTRE : régime DOIT être BEAR. Gap > 3% = skip.
  - 30-50 trades estimés

A2. Breakdown Continuation (LOD post-11h, short)
  - Nouveau LOW OF DAY après 11:00
  - Stock en baisse > 1%, volume > 1.5x, sous VWAP
  - SPY aussi en baisse (confirmation)
  - Stop = précédent LOD + 0.1%. Target = 2x risque trailing
  - FILTRE : 4ème+ LOD = skip. 12:00-12:30 = skip.
  - 30-50 trades estimés

A3. Failed Rally Short (VWAP rejection, bear)
  - Stock en baisse > 0.5%, rally vers VWAP
  - VWAP touché mais pas cassé (close barre SOUS le VWAP)
  - Barre suivante rouge + volume > 1.3x
  - Stop = VWAP + 0.3%. Target = low du jour ou 2x risque
  - FILTRE : prix au-dessus VWAP majorité du temps = skip
  - 30-50 trades estimés

A4. End-of-Day Sell Pressure (bear close, 14:30-15:55)
  - Régime BEAR obligatoire
  - Stock flat/léger hausse 12:00-14:30, volume augmente à 14:30
  - Prix passe sous EMA(9) 5M avec volume, sous VWAP
  - Stop = high 14:00-14:30 + 0.1%. Target = 15:55
  - FILTRE : stock déjà > -2% = skip. Vendredi OpEx = skip.
  - 25-40 trades estimés

A5. Momentum Exhaustion Short (RSI extreme)
  - RSI(2) > 95 + prix > BB(20,3) upper + volume > 2x + gain 5j > 15%
  - Top 50 stocks liquides uniquement
  - Stop = -2%. Target = RSI < 50 ou +3%
  - Position = 2% capital (réduit car high-vol)
  - 15-25 trades estimés

A6. Crypto Bear Cascade V2
  - COIN en baisse > 2% à 10:00
  - 3+ crypto-proxies aussi en baisse > 1%
  - Shorter le PLUS FORT (pas encore rattrapé)
  - Stop = high 9:30-10:00. Target = 2.5x risque
  - 15-25 trades estimés


CATÉGORIE B — OVERNIGHT (3 stratégies, Alpaca)
Le POC overnight est validé. Les 3 IA convergent sur cet axe.

B1. Overnight Simple SPY
  - Achat SPY à 15:50 chaque jour
  - Vente à 9:35 le lendemain
  - FILTRE : ATR 20j SPY > 2.5% = skip. Vendredi = skip. 
    FOMC/CPI demain = skip. Earnings FAANG/NVDA ce soir = skip.
  - Sizing : 3% du capital (pas 5%)
  - ~80 trades/6 mois

B2. Overnight Sector Winner
  - Le secteur qui surperforme SPY > 0.5% aujourd'hui
  - Acheter ce sector ETF à 15:50, vendre 9:35
  - FILTRE : aucun secteur > 0.5% = skip. Vendredi = skip.
  - Max 1 position. Sizing : 3%
  - ~40 trades/6 mois

B3. Overnight Short Bear (bear-only)
  - Régime BEAR obligatoire
  - SPY en baisse > 0.3% aujourd'hui
  - SHORT SPY à 15:50, cover 9:35
  - FILTRE : régime != BEAR = skip. SPY en hausse = skip. Vendredi = skip.
  - Sizing : 3%
  - ~30-50 trades/6 mois en bear


CATÉGORIE C — OPTIONS (3 stratégies, IBKR — coder la logique, 
backtest sur données proxy si IBKR pas encore connecté)

C1. Weekly Put Credit Spread SPY
  - Vendre put spread 10-delta, 5 points de largeur, 7 DTE
  - Chaque vendredi 14:00 ET
  - Gérer à 50% profit ou 200% loss
  - Win rate attendu : 75-80%
  - ~4 trades/mois
  
  NOTE : Si pas de données options, backtester en proxy :
  simuler le payoff du spread avec les mouvements SPY daily.
  P&L ≈ premium reçu si SPY ne baisse pas de > X%, sinon max loss.

C2. 0DTE Gamma Scalping SPY (simplifié)
  - Acheter straddle ATM SPY à 10:00
  - Delta-hedge toutes les 30 min (pas 15 — trop de frais)
  - Fermer à 15:30
  - Win quand le move intraday > implied vol
  
  NOTE : Backtest en proxy avec les mouvements 5M de SPY.
  Simuler le gamma P&L = (realized_move² - implied_move²) × gamma.

C3. Earnings IV Crush (post-earnings vol selling)
  - La veille d'earnings (AAPL, MSFT, NVDA, META, AMZN, GOOGL, TSLA)
  - Vendre straddle ATM à 15:50
  - Racheter à 9:40 le lendemain (après le gap earnings)
  - Win quand le gap < implied move (ce qui arrive ~60% du temps)
  
  NOTE : Backtest en comparant gap réel vs implied move historique.


CATÉGORIE D — MULTI-ASSET (3 stratégies, IBKR)

D1. Futures Trend Following ES/NQ
  - Timeframe 1H
  - Long si prix > EMA20 > EMA50. Short inverse.
  - Stop = 2x ATR(14). Target = 3x ATR(14).
  - 1-2 contrats max ($50-100K notionnel)
  
  NOTE : Backtester sur SPY/QQQ en 1H comme proxy (corrélation ~0.99).

D2. Forex Carry Momentum (simplifié)
  - Long AUD/JPY ou NZD/JPY si momentum positif (prix > EMA20 daily)
  - Carry = ~3-5% annualisé
  - Stop = -2% du capital alloué. Levier 2:1 max (pas 5:1).
  - 4-8 trades/6 mois
  
  NOTE : Backtester sur yfinance AUDJPY=X

D3. Gold/Dollar Macro Hedge
  - Si DXY (dollar) baisse > 1% sur 5 jours : LONG GLD
  - Si DXY monte > 1% sur 5 jours : SHORT GLD (ou skip)
  - Holding 5-10 jours
  - C'est un HEDGE, pas une source d'alpha
  - 10-15 trades/6 mois
```

### 2.3 Process pour chaque stratégie

```
Pour CHAQUE stratégie :
1. Créer le fichier Python (hériter de BaseStrategy)
2. Backtester sur 6 mois, 207 tickers, coûts réels
3. Walk-forward (si trades >= 30)
4. Calculer : Sharpe, WR, PF, DD, trades, commissions
5. Cost sensitivity (2x, 3x slippage)
6. Verdict : VALIDÉ / REJETÉ / PROMETTEUR

CRITÈRES DE VALIDATION :
- Sharpe > 0.5 après coûts
- PF > 1.2
- Trades >= 15
- DD < 10%
- Cost sensitivity : survit à 2x slippage

Pour les stratégies bear-only : backtester SPÉCIFIQUEMENT 
sur les périodes SPY < SMA(200). Sharpe calculé uniquement 
sur ces périodes.

Pour les stratégies overnight : modifier le guard 15:55 
avec un flag overnight_exempt. Backtester sur daily bars 
(close → open).

Pour les stratégies options/futures : backtester en proxy 
(voir notes dans chaque stratégie). Résultats à confirmer 
quand IBKR sera connecté.
```

### 2.4 Livrables

```
FICHIERS CRÉÉS :
  strategies/short/bear_morning_fade.py
  strategies/short/breakdown_continuation.py
  strategies/short/failed_rally_short.py
  strategies/short/eod_sell_pressure.py
  strategies/short/momentum_exhaustion_short.py
  strategies/short/crypto_bear_cascade_v2.py
  strategies/overnight/overnight_simple_spy.py
  strategies/overnight/overnight_sector_winner.py
  strategies/overnight/overnight_short_bear.py
  strategies/options/weekly_put_spread.py
  strategies/options/zero_dte_gamma.py
  strategies/options/earnings_iv_crush.py
  strategies/multi_asset/futures_trend.py
  strategies/multi_asset/forex_carry.py
  strategies/multi_asset/gold_dollar_hedge.py
  
OUTPUT :
  output/session_strats/
    trades_*.csv pour chaque stratégie
    session_report_strats.md
```

---

## TRACK 3 — AGENT RISK : ALLOCATION DYNAMIQUE + VaR + SLIPPAGE

```
DOSSIER DE TRAVAIL : core/ + config/
DURÉE ESTIMÉE : 6-8 heures
PRÉREQUIS : Trades historiques des 17 stratégies actives
```

### 3.1 Objectif

Implémenter le framework de risk management avancé que les 3 IA recommandent :
- Allocation dynamique Risk Parity + Momentum
- VaR/CVaR temps réel
- Slippage dynamique par liquidité
- Corrélation temps réel inter-stratégies
- Nouvelles limites d'exposition

### 3.2 Étapes

```
ÉTAPE 1 : Créer config/allocation.yaml (30min)

  Définir la structure cible du portefeuille :
  
  portfolio:
    target_gross_exposure: 0.80
    min_cash_reserve: 0.15
    target_volatility: 0.12
    rebalance_threshold: 0.20
    
  buckets:
    core_alpha:
      target: 0.45
      strategies: [opex, gap_cont, vwap_micro, crypto_v2, dow]
    diversifiers:
      target: 0.25
      strategies: [overnight, short_strats, futures, forex]
    hedges:
      target: 0.10
      strategies: [gold_fear, corr_hedge, put_spreads]
      
  Mapper chaque stratégie existante dans un bucket.


ÉTAPE 2 : Créer config/limits.yaml (30min)

  Nouvelles limites (consensus des 3 IA) :
  
  position_limits:
    max_single_position: 0.10      # maintenu
    max_single_strategy: 0.15      # réduit de 25% → 15%
    max_sector_exposure: 0.25      # NOUVEAU
    max_single_broker: 0.70        # NOUVEAU
    
  exposure_limits:
    max_long_net: 0.60             # augmenté de 40% → 60%
    max_short_net: 0.30            # augmenté de 20% → 30%
    max_gross: 0.90                # NOUVEAU (objectif 80%, max 90%)
    min_cash: 0.10                 # NOUVEAU (10% minimum toujours en cash)
    
  risk_limits:
    max_var_95_daily: 0.02         # NOUVEAU
    max_var_99_daily: 0.03         # NOUVEAU
    max_correlation_pair: 0.60     # NOUVEAU
    circuit_breaker_daily_dd: 0.05 # maintenu
    circuit_breaker_hourly_dd: 0.03# NOUVEAU
    kill_switch_strategy_dd: 0.02  # maintenu
    kill_switch_lookback_days: 5   # maintenu


ÉTAPE 3 : Implémenter core/risk_manager.py (2h)

  class RiskManager:
      def __init__(self, limits: dict):
          self.limits = limits
          
      def validate_order(self, order, portfolio) -> tuple[bool, str]:
          """Valide un ordre contre TOUTES les limites."""
          checks = [
              self._check_position_limit(order, portfolio),
              self._check_strategy_limit(order, portfolio),
              self._check_exposure_long(order, portfolio),
              self._check_exposure_short(order, portfolio),
              self._check_gross_exposure(order, portfolio),
              self._check_cash_reserve(order, portfolio),
              self._check_sector_limit(order, portfolio),
              self._check_var_impact(order, portfolio),
              self._check_correlation(order, portfolio),
          ]
          for passed, msg in checks:
              if not passed:
                  return False, msg
          return True, "OK"
          
      def calculate_var(self, portfolio, confidence=0.99, horizon=1):
          """VaR paramétrique. Utilise les returns historiques 60j."""
          returns = portfolio.daily_returns(60)
          mean = returns.mean()
          std = returns.std()
          z = stats.norm.ppf(1 - confidence)
          var = -(mean + z * std) * np.sqrt(horizon)
          return var
          
      def calculate_cvar(self, portfolio, confidence=0.99):
          """CVaR (Expected Shortfall). Plus conservateur que VaR."""
          returns = portfolio.daily_returns(60)
          var = self.calculate_var(portfolio, confidence)
          cvar = -returns[returns < -var].mean()
          return cvar
          
      def check_sector_exposure(self, portfolio) -> dict:
          """Calcule l'exposition par secteur."""
          sector_map = {
              'XLK': ['AAPL','MSFT','NVDA','AMD','AVGO','CRM','ADBE'],
              'XLF': ['JPM','BAC','WFC','GS','MS','C'],
              'XLE': ['XOM','CVX','COP','EOG','SLB'],
              'XLV': ['UNH','JNJ','LLY','ABBV','MRK','PFE'],
              'XLC': ['META','GOOGL','NFLX','DIS'],
              'CRYPTO': ['COIN','MARA','MSTR','RIOT','BITF'],
          }
          exposures = {}
          for sector, tickers in sector_map.items():
              sector_exposure = sum(
                  abs(pos.notional) for pos in portfolio.positions
                  if pos.ticker in tickers
              ) / portfolio.equity
              exposures[sector] = sector_exposure
          return exposures


ÉTAPE 4 : Implémenter core/allocator.py (2h)

  class DynamicAllocator:
      def __init__(self, config: dict):
          self.config = config
          self.target_vol = config['target_volatility']
          self.min_cash = config['min_cash_reserve']
          self.max_strategy = 0.15
          
      def calculate_weights(self, strategies: list) -> dict:
          """
          Risk Parity + Momentum + Correlation-adjusted.
          Consensus des 3 analyses.
          """
          weights = {}
          
          # Step 1: Risk Parity (inverse vol)
          for strat in strategies:
              vol = strat.rolling_volatility(60)
              weights[strat.name] = 1/vol if vol > 0 else 0
          total = sum(weights.values()) or 1
          weights = {k: v/total for k,v in weights.items()}
          
          # Step 2: Momentum boost/cut
          for strat in strategies:
              sharpe_20d = strat.rolling_sharpe(20)
              if sharpe_20d > 1.0:
                  weights[strat.name] *= 1.3
              elif sharpe_20d < 0:
                  weights[strat.name] *= 0.5
                  
          # Step 3: Correlation penalty
          corr_matrix = self._calc_correlation(strategies)
          for strat in strategies:
              avg_corr = np.mean([
                  corr_matrix.get((strat.name, s.name), 0) 
                  for s in strategies if s.name != strat.name
              ])
              if avg_corr > 0.6:
                  weights[strat.name] *= (1 - avg_corr)
                  
          # Step 4: Regime multipliers
          regime = self._detect_regime()
          for strat in strategies:
              mult = REGIME_MULTIPLIERS[regime].get(strat.edge_type, 1.0)
              weights[strat.name] *= mult
              
          # Step 5: Caps
          for name in weights:
              weights[name] = min(weights[name], self.max_strategy)
              
          # Step 6: Scale to target vol
          portfolio_vol = self._portfolio_vol(weights, strategies)
          if portfolio_vol > 0:
              scale = self.target_vol / portfolio_vol
              weights = {k: v*scale for k,v in weights.items()}
              
          # Step 7: Cash reserve
          total_alloc = sum(weights.values())
          if total_alloc > (1 - self.min_cash):
              scale = (1 - self.min_cash) / total_alloc
              weights = {k: v*scale for k,v in weights.items()}
              
          return weights


ÉTAPE 5 : Slippage dynamique par liquidité (1h)

  Créer core/slippage_model.py :
  
  class LiquiditySlippage:
      """
      Slippage = f(order_size, ADV, bid_ask_spread)
      Recommandé par les 3 analyses.
      """
      def estimate_slippage(self, ticker, shares, side, market_data):
          adv = market_data.get('avg_daily_volume', 1_000_000)
          spread = market_data.get('bid_ask_spread', 0.01)
          price = market_data.get('last_price', 100)
          
          # Part of volume
          order_notional = shares * price
          pov = (shares / (adv / 78))  # 78 barres 5M par jour
          
          # Almgren-Chriss simplified
          temporary_impact = spread / 2  # Half spread
          permanent_impact = 0.1 * np.sqrt(pov) * price * 0.0001
          
          total_slippage_pct = (temporary_impact + permanent_impact) / price
          
          # Minimum = 0.02% (baseline backtest)
          return max(total_slippage_pct, 0.0002)
          
  Intégrer dans le backtest engine ET dans le pipeline live.


ÉTAPE 6 : Tests (1h)

  Créer tests/test_risk_management_v2.py :
  
  - test_var_calculation
  - test_cvar_calculation
  - test_sector_exposure_limit
  - test_correlation_penalty
  - test_dynamic_allocation
  - test_regime_multipliers
  - test_slippage_model
  - test_cash_reserve_enforced
  - test_gross_exposure_limit
  - test_position_cap_reduced (15% pas 25%)

  Créer tests/test_allocator.py :
  
  - test_risk_parity_weights
  - test_momentum_boost
  - test_correlation_penalty_applied
  - test_target_vol_scaling
  - test_rebalance_threshold
```

### 3.3 Livrables

```
FICHIERS CRÉÉS :
  config/allocation.yaml
  config/limits.yaml
  core/risk_manager.py
  core/allocator.py
  core/slippage_model.py
  tests/test_risk_management_v2.py
  tests/test_allocator.py
  
FICHIERS MODIFIÉS :
  scripts/paper_portfolio.py (intégrer RiskManager + Allocator)
  dashboard/api/main.py (endpoints VaR, allocation, sector exposure)
```

---

## TRACK 4 — AGENT OPTIM : OPTIMISATION DES 17 STRATÉGIES EXISTANTES

```
DOSSIER DE TRAVAIL : intraday-backtesterV2/ + output/
DURÉE ESTIMÉE : 6-8 heures
PRÉREQUIS : Données Alpaca en cache + trades historiques
```

### 4.1 Objectif

Optimiser les 17 stratégies actives sur 5 axes :
1. Paramètres bear-specific
2. Stops adaptatifs ATR
3. Stationnarité et alpha decay
4. Bear monitoring des trades récents
5. Réduction de l'allocation OpEx

### 4.2 Étapes

```
ÉTAPE 1 : Bear-specific parameter tuning (3h)

  Pour CHAQUE stratégie active intraday (11 stratégies) :
  
  1. Filtrer les trades en période SPY < SMA(200)
  2. Calculer Sharpe_bear, WR_bear, PF_bear
  3. Si Sharpe_bear < 0 → recommander pause en bear
  4. Si Sharpe_bear > 0 mais < Sharpe_bull :
     → Grid search sur les paramètres UNIQUEMENT sur données bear
     → Comparer les paramètres optimaux bear vs bull
     → Si différents de > 20% : créer un jeu de paramètres bear
  
  Paramètres à optimiser par stratégie :
  
  OpEx Gamma Pin :
    Bear : deviation [0.3, 0.35, 0.4, 0.45], stop [0.5, 0.6, 0.7, 0.8]
    
  Gap Continuation :
    Bear : gap_min [0.8, 1.0, 1.1, 1.3], vol_mult [1.5, 1.8, 2.0, 2.5]
    
  Day-of-Week :
    Bear : INVERSER le pattern ? Lundi = skip (pas de short si bear), 
    Vendredi = SHORT au lieu de LONG ?
    
  Crypto V2 :
    Bear : leader_perf [0.5, 0.7, 1.0], z_score_entry [-1.0, -1.2, -1.5]
    
  Late Day MR :
    Bear : min_day_move [2.5, 3.0, 3.5, 4.0], vwap_distance [1.0, 1.5, 2.0]
    
  VWAP Micro :
    Bear : z_score_threshold [2.0, 2.5, 3.0], stop adapté vol bear
    
  OUTPUT : Tableau comparatif paramètres bull vs bear par stratégie


ÉTAPE 2 : Stops adaptatifs ATR (1h)

  Implémenter un stop dynamique basé sur l'ATR au lieu de % fixe.
  
  Pour chaque stratégie, remplacer :
    stop_loss = entry * (1 - 0.005)  # fixe 0.5%
  Par :
    atr = data['ATR_14'].iloc[-1]
    stop_loss = entry - (atr * multiplier)
    
  Multipliers par stratégie (à calibrer via grid search) :
    OpEx : 1.0-1.5 × ATR
    Gap Cont : le range d'ouverture EST le stop (déjà adaptatif)
    VWAP Micro : 1.5-2.0 × ATR
    Crypto V2 : 2.0-2.5 × ATR (crypto plus volatile)
    DoW : 1.0-1.5 × ATR
    
  Backtester chaque stratégie avec stops ATR vs stops fixes.
  Si Sharpe s'améliore : adopter les stops ATR.


ÉTAPE 3 : Alpha decay analysis (1h)

  Pour chaque stratégie active :
  
  1. Calculer le Sharpe par fenêtre de 30 jours (rolling)
  2. Fitter une régression linéaire sur le Sharpe rolling
  3. Si la pente est négative ET significative (p < 0.1) :
     → ALERTE : alpha en déclin
     → Estimer la date où le Sharpe croise 0 (extrapolation)
  
  OUTPUT :
    Graphique : Sharpe rolling par stratégie avec trend line
    Tableau : pente, p-value, date estimée de crossing 0
    
  PRÉDICTIONS ATTENDUES (basées sur l'analyse des 3 IA) :
    OpEx Gamma : stable (edge mécanique, pas exploité par les retail)
    VWAP Micro : déclin 6-12 mois (edge technique)
    Triple EMA : déclin 3-6 mois (surexploité)
    Gold Fear Gauge : stable (edge macro, peu exploité)


ÉTAPE 4 : Bear monitoring live trades (1h)

  Analyser les trades des 3 derniers jours (23-26 mars) :
  
  1. Le monitoring bear de la session précédente a montré que 
     TOUTES les stratégies sont "BEAR LOSER". Confirmer sur 
     les trades les plus récents.
     
  2. Pour chaque trade perdant :
     - Contexte marché (SPY direction, VIX, secteur)
     - Le signal était-il correct ?
     - Le stop a-t-il été touché trop tôt (vol bear > backtest) ?
     - Les commissions ont-elles mangé un trade marginal ?
     
  3. Comparer le slippage réel (paper Alpaca) vs slippage backtest (0.02%)
     Si slippage réel > 0.03% → ajuster le modèle
     
  OUTPUT : Diagnostic des pertes en bear + recommandations


ÉTAPE 5 : Réduction allocation OpEx (30min)

  Les 3 IA sont d'accord : OpEx à 25% c'est trop.
  
  Plan :
  1. Réduire de 25% → 12%
  2. Redistribuer les 13% libérés :
     - +5% aux diversifiers (overnight quand validé)
     - +5% aux short strategies (VIX Expansion, Failed Rally, Crypto Bear)
     - +3% aux hedges (Gold Fear, Corr Hedge)
  3. Backtester le portefeuille avec la nouvelle allocation
  4. Comparer Sharpe portefeuille avant/après
  
  ATTENTION : Le Sharpe portefeuille va probablement BAISSER (OpEx 
  contribuait beaucoup). Mais le MAX DD et le tail risk vont 
  s'améliorer significativement. C'est le bon trade-off.


ÉTAPE 6 : Portefeuille simulation combinée (1h)

  Simuler 4 scénarios d'allocation :
  
  A. Allocation actuelle (Tier S/A/B/C, OpEx 25%)
  B. Allocation réduite OpEx (12%) + redistribution
  C. Allocation Risk Parity (inverse vol)
  D. Allocation avec nouvelles stratégies short/overnight (si validées)
  
  Pour chaque scénario, calculer :
  - Sharpe portefeuille
  - Max DD
  - Return total
  - Corrélation moyenne
  - VaR 99%
  - Performance en bear vs bull
  
  OUTPUT : 
    4 equity curves sur le même graphique
    Tableau comparatif des 4 scénarios
    Recommandation finale d'allocation
```

### 4.3 Livrables

```
OUTPUT :
  output/session_optim/
    bear_parameters.json (paramètres bear par stratégie)
    alpha_decay_analysis.png (graphique Sharpe rolling)
    alpha_decay_report.md
    bear_monitoring_report.md
    allocation_comparison.png (4 equity curves)
    portfolio_simulation.csv
    session_report_optim.md
```

---

## RAPPORT FINAL CONSOLIDÉ

```
Quand les 4 agents ont terminé, MARC consolide les résultats :

1. AGENT INFRA : IBKR client prêt ? Tests OK ? Docs ?
2. AGENT STRATS : Combien de winners sur 15 ? Quels déployer ?
3. AGENT RISK : VaR implémenté ? Nouvelles limites testées ?
4. AGENT OPTIM : Paramètres bear calibrés ? OpEx réduit ? 
   Alpha decay détecté sur quelles strats ?

DÉCISIONS À PRENDRE PAR MARC :
- Valider la nouvelle allocation
- Choisir les nouvelles stratégies à déployer
- Confirmer la réduction OpEx 25% → 12%
- Activer les limites VaR/CVaR
- Planifier le setup IBKR live

LIVRABLE FINAL : 
  RAPPORT_V2.md avec :
  - Nouveau portefeuille (22-25 stratégies)
  - Nouvelle allocation par bucket
  - Nouvelles limites de risque
  - Planning setup IBKR
  - Métriques cibles 30/60/90 jours
```

---

## MÉTRIQUES DE SUCCÈS

```
| Métrique              | Actuel    | Cible 30j | Cible 90j |
|-----------------------|-----------|-----------|-----------|
| Stratégies actives    | 17        | 22        | 28        |
| Investissement moyen  | 40%       | 65%       | 80%       |
| Ratio Long/Short      | 75/25     | 55/30/15  | 50/30/20  |
| Sharpe portfolio      | ~1.5 est  | 1.8       | 2.2       |
| Max DD                | N/A       | < 5%      | < 5%      |
| Corrélation moyenne   | ~0.3 est  | < 0.4     | < 0.3     |
| Classes d'actifs      | 1         | 2         | 4         |
| Brokers               | 1         | 2         | 2         |
| VaR 99% daily         | N/A       | < 3%      | < 3%      |
| Sharpe en bear        | ~0        | > 0.5     | > 1.0     |
```

---

## SETUP IBKR — GUIDE RAPIDE POUR MARC

```
PENDANT QUE LES AGENTS BOSSENT, MARC FAIT :

1. Aller sur https://www.interactivebrokers.com/
2. "Open Account" → Paper Trading Account
3. Télécharger IB Gateway (PAS TWS — plus léger)
   https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
4. Installer IB Gateway
5. Lancer IB Gateway → Login avec les credentials paper
6. Aller dans Configure → Settings → API → Settings :
   ✅ Enable ActiveX and Socket Clients
   ✅ Port: 4002 (paper)
   ✅ Allow connections from localhost only
   ✅ Read-Only API = NON (on veut passer des ordres)
7. Noter les credentials dans .env :
   IBKR_HOST=127.0.0.1
   IBKR_PORT=4002
   IBKR_CLIENT_ID=1

TEMPS ESTIMÉ : 15-30 minutes

NOTE : Pour Railway, il faudra un container Docker avec IB Gateway 
headless. C'est le travail de l'Agent 1 (Track Infra).
```

---

*Document préparé par Claude Opus 4.6 — 27 mars 2026*
*Synthèse de 3 analyses IA indépendantes (Claude + ChatGPT + IA tierce)*
*4 tracks parallèles, 15 nouvelles stratégies, Risk Parity + VaR, IBKR setup*
*Durée totale estimée : 8-10 heures multi-agents*
