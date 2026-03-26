# TODO LIST V3 — TRADING PLATFORM
## Ancree dans la Due Diligence du 26 mars 2026
### 10 axes, 52 items, 4 phases

---

## AXE 1 : CORRIGER LE BIAIS LONG (75% -> 50%)

```
□ [SHORT-1] Sector Relative Weakness Short
  Description : Shorter l'ETF sectoriel (XLK, XLF, XLE) quand il sous-performe
    SPY de > 1% a 10:30 ET. 1 seul trade sur l'ETF (pas les composants).
  Impact : Ajoute ~3% d'allocation short, edge large sur ETF liquide
  Priorite : P0
  Temps : 3h (code + backtest + WF)
  Dependances : []
  Succes : Sharpe > 0.5, PF > 1.2, trades >= 20
  Fichiers : intraday-backtesterV2/strategies/sector_relative_weakness_short.py

□ [SHORT-2] High-Beta Underperformance Short
  Description : Quand SPY baisse > 0.5% a 10:30, shorter le stock high-beta
    (TSLA, COIN, MARA, AMD, NVDA) qui est encore flat/haussier.
    Edge = convergence beta vers le marche.
  Impact : Exploite le lag de convergence mecanique des high-beta
  Priorite : P0
  Temps : 3h
  Dependances : []
  Succes : Sharpe > 0.5, PF > 1.2, trades >= 15
  Fichiers : intraday-backtesterV2/strategies/high_beta_underperf_short.py

□ [SHORT-3] Intraday Trend Exhaustion Short
  Description : Stock monte > 3% depuis open ET volume des 6 dernieres barres
    < 70% des 6 precedentes = momentum epuise. SHORT. Pas d'indicateurs
    techniques (RSI/BB), uniquement prix + volume.
  Impact : Edge different de Momentum Exhaustion (rejete car RSI+BB)
  Priorite : P0
  Temps : 3h
  Dependances : []
  Succes : Sharpe > 0.5, PF > 1.2, trades >= 15
  Fichiers : intraday-backtesterV2/strategies/trend_exhaustion_short.py

□ [SHORT-4] Late Day Bear Acceleration
  Description : En BEAR, SPY baisse > 0.3% a 14:00 ET + volume croissant
    14:00-14:30 = continuation. Shorter SPY/QQQ pour la derniere heure.
    Different de EOD Sell (rejete) : ici on shorte les stocks DEJA en baisse.
  Impact : Capture le selling institutionnel EOD en bear
  Priorite : P0
  Temps : 3h
  Dependances : []
  Succes : Sharpe > 0.5, PF > 1.2, trades >= 20
  Fichiers : intraday-backtesterV2/strategies/late_day_bear_acceleration.py

□ [SHORT-5] Cross-Asset Risk-Off Confirmation
  Description : GLD ET TLT montent > 0.3% simultanement le matin = double
    signal risk-off. Shorter les high-beta l'apres-midi. Difference vs Gold
    Fear : DOUBLE signal GLD+TLT = plus de conviction, moins de faux positifs.
  Impact : Version amelioree du Gold Fear avec filtre supplementaire
  Priorite : P1
  Temps : 3h
  Dependances : []
  Succes : Sharpe > 0.5, PF > 1.2, trades >= 15
  Fichiers : intraday-backtesterV2/strategies/cross_asset_riskoff_short.py

□ [SHORT-6] OpEx Short Extension
  Description : Les vendredis OpEx, quand le prix est AU-DESSUS du round number
    de > 0.3%, shorter vers le round number. Version SHORT-only du OpEx Gamma.
    Separer long/short permet au kill switch de desactiver un cote.
  Impact : Amplifie le cote short du pinning en bear
  Priorite : P1
  Temps : 2h (variation de l'existant)
  Dependances : []
  Succes : Sharpe > 0.5, PF > 1.2, trades >= 15
  Fichiers : intraday-backtesterV2/strategies/opex_short_extension.py

□ [SHORT-DEPLOY] Deployer les winners short
  Description : Ajouter les strategies short validees au pipeline
    paper_portfolio.py. Allocation cible : 20-25% du capital en short.
  Impact : Ratio long/short passe de 75/20/5 a ~55/30/15
  Priorite : P0 (apres validation)
  Temps : 1h
  Dependances : [SHORT-1 a SHORT-6]
  Succes : Allocation short >= 20%, Sharpe bear portefeuille > 0.3
  Fichiers : scripts/paper_portfolio.py, config/allocation.yaml
```

## AXE 2 : OVERNIGHT (18h/jour de capital dormant)

```
□ [OVERNIGHT-1] Buy SPY 15:50, sell 9:35 — ZERO FILTRE
  Description : Version la plus simple possible. Backtest sur 5 ans daily
    (yfinance). Close = entree, Open next day = sortie. Slippage 0.05%.
    Si Sharpe > 0.3 : ajouter filtre VIX seulement.
  Impact : Exploite 18h de capital dormant
  Priorite : P0
  Temps : 2h
  Dependances : []
  Succes : Sharpe > 0.3 sur 5 ans daily
  Fichiers : intraday-backtesterV2/strategies/overnight_spy_simple.py

□ [OVERNIGHT-2] Buy best sector ETF 15:50, sell 9:35
  Description : Acheter le sector ETF le plus fort vs SPY a 15:45.
    Backtest daily 5 ans. Version ultra-simple sans filtres complexes.
  Impact : Decorrelation sectorielle overnight
  Priorite : P0
  Temps : 2h
  Dependances : []
  Succes : Sharpe > 0.3, trades >= 40/an
  Fichiers : intraday-backtesterV2/strategies/overnight_sector_simple.py

□ [OVERNIGHT-3] Short SPY overnight en bear uniquement
  Description : En bear (SPY < SMA200), short SPY 15:50, cover 9:35.
    Backtest sur periodes bear 5 ans.
  Impact : Hedge overnight en bear market
  Priorite : P1
  Temps : 2h
  Dependances : []
  Succes : Sharpe > 0.3 en bear, trades >= 15
  Fichiers : intraday-backtesterV2/strategies/overnight_short_simple.py

□ [OVERNIGHT-ENGINE] Modifier le pipeline pour supporter overnight
  Description : Ajouter flag overnight_exempt dans BaseStrategy.
    Les strategies avec ce flag ne sont PAS fermees a 15:55.
    Guard anti-double : max 1 position overnight a la fois.
    Sizing overnight = 3% max.
  Impact : Prerequis pour toute strategie overnight
  Priorite : P0 (si un overnight est valide)
  Temps : 3h
  Dependances : [OVERNIGHT-1 ou 2 ou 3 valide]
  Succes : Pipeline supporte les positions overnight sans fermeture 15:55
  Fichiers : scripts/paper_portfolio.py, intraday-backtesterV2/strategies/base_strategy.py
```

## AXE 3 : RISK MANAGEMENT AVANCE (CRO 9.5 -> 10)

```
□ [RISK-1] VaR historique bootstrap
  Description : Implementer le VaR bootstrap (resampling des vrais returns)
    en plus du parametrique. Utiliser le MAX des deux comme limite.
    Le bootstrap capture les fat tails ignores par le parametrique.
  Impact : Protection tail risk amelioree
  Priorite : P1
  Temps : 2h
  Dependances : []
  Succes : VaR bootstrap implemente, test unitaire pass
  Fichiers : core/risk_manager.py, tests/test_risk_v2.py

□ [RISK-2] Cap sectoriel enforced (pre-ordre)
  Description : Le RiskManager calcule deja l'exposition sectorielle.
    Ajouter un check PRE-ORDRE qui REJETTE l'ordre si un secteur
    depasserait 25%. Actuellement calcule mais pas enforce.
  Impact : Empeche la concentration sectorielle (ex: 100% tech)
  Priorite : P0
  Temps : 1h
  Dependances : []
  Succes : Test : ordre qui ferait passer tech > 25% = rejete
  Fichiers : core/risk_manager.py, scripts/paper_portfolio.py

□ [RISK-3] Drawdown-based deleveraging progressif
  Description : Au lieu du circuit-breaker binaire (5% = close all),
    implementer un deleveraging progressif :
    DD > 50% du max DD backtest (0.9%) = reduire 30%
    DD > 75% (1.35%) = reduire 50%
    DD > 100% (1.8%) = circuit-breaker complet
  Impact : Moins de whipsaws, sortie progressive au lieu de brutale
  Priorite : P1
  Temps : 3h
  Dependances : []
  Succes : 3 niveaux de deleveraging implementes et testes
  Fichiers : core/risk_manager.py, scripts/paper_portfolio.py

□ [RISK-4] Correlation stress monitoring
  Description : Calculer la correlation rolling 5j entre les top 5 strategies.
    Si correlation moyenne passe de ~0.1 a > 0.5 = alerte Telegram +
    reduction automatique 30%.
  Impact : Detection precoce des stress events
  Priorite : P2
  Temps : 3h
  Dependances : [60+ jours de data live]
  Succes : Alerte declenchee quand correlation > 0.5
  Fichiers : core/risk_manager.py, core/telegram_alert.py

□ [RISK-5] Pre-market stop pour overnight
  Description : Si une position overnight gap < -3% en pre-market
    (4:00-9:30 ET), envoyer un market sell en extended hours.
    Alpaca supporte les ordres extended hours.
  Impact : Protection contre les gaps overnight catastrophiques
  Priorite : P2 (quand overnight deploye)
  Temps : 2h
  Dependances : [OVERNIGHT-ENGINE]
  Succes : Ordre extended hours envoye si gap > -3%
  Fichiers : scripts/paper_portfolio.py

□ [RISK-6] CI/CD GitHub Actions
  Description : Creer .github/workflows/test.yml.
    Lancer pytest a chaque push sur main.
    Verifier PAPER_TRADING=true.
    Bloquer le deploy Railway si tests echouent.
  Impact : Detecte les regressions avant deploiement
  Priorite : P1
  Temps : 2h
  Dependances : []
  Succes : Badge vert sur GitHub, deploy bloque si test fail
  Fichiers : .github/workflows/test.yml

□ [RISK-7] Augmenter couverture tests 5.6% -> 15%
  Description : Ajouter des tests pour les fichiers critiques :
    paper_portfolio.py (0% -> 30%), risk_manager.py (existant -> 50%),
    alpaca_client/client.py (existant -> 40%), allocator.py (existant -> 40%).
  Impact : Reduit le risque de regression
  Priorite : P2
  Temps : 8h
  Dependances : []
  Succes : pytest --cov > 15%
  Fichiers : tests/test_pipeline.py, tests/test_broker_mock.py
```

## AXE 4 : ALLOCATION DYNAMIQUE

```
□ [ALLOC-1] Rebalancing automatique EOD
  Description : Check EOD : si une strategie a drifte de > 20% de son
    poids cible, ajuster les prochains trades (pas de vente forcee).
  Impact : Maintient l'allocation cible sans intervention
  Priorite : P1
  Temps : 3h
  Dependances : []
  Succes : L'allocation ne derive pas de > 20% sur 30 jours
  Fichiers : scripts/paper_portfolio.py, core/allocator.py

□ [ALLOC-2] Momentum overlay
  Description : Surponderer les strategies Sharpe rolling 20j > 2.0 (+30%).
    Sous-ponderer celles < 0 (-50%). Recalculer chaque matin.
  Impact : Amplifie les winners, coupe les losers automatiquement
  Priorite : P1
  Temps : 2h
  Dependances : [30+ jours de data live]
  Succes : Sharpe portefeuille ameliore de > 10% vs allocation fixe
  Fichiers : core/allocator.py

□ [ALLOC-4] Multi-bucket allocation
  Description : Passer de Tier S/A/B/C a Buckets fonctionnels :
    Core Alpha 45%, Shorts/Bear 20%, Diversifiers 15%,
    Hedges 5%, Satellite 5%, Cash 10%.
  Impact : Structure professionnelle, investissement passe de 40% a 75%
  Priorite : P1
  Temps : 3h
  Dependances : [SHORT-DEPLOY]
  Succes : Investissement total > 70%, structure par bucket
  Fichiers : config/allocation.yaml, core/allocator.py, scripts/paper_portfolio.py

□ [ALLOC-5] Allocation bear-specific
  Description : En BEAR_NORMAL : Core x0.6, Shorts x1.5, Hedges x2.0,
    Satellite x0.3. Direction passe de 75% long a 45% long / 25% short.
  Impact : Le portefeuille survit en bear au lieu de perdre
  Priorite : P1
  Temps : 2h
  Dependances : [ALLOC-4]
  Succes : Sharpe bear portefeuille > 0.5
  Fichiers : core/allocator.py, config/allocation.yaml
```

## AXE 5 : CLASSES D'ACTIFS NOUVELLES

```
□ [OPT-1] Weekly put credit spreads SPY (backtest proxy)
  Description : Simuler le payoff d'un put spread 10-delta, 5pts, 7 DTE.
    Chaque vendredi 14:00 ET. Gerer a 50% profit ou 200% loss.
    Proxy backtest : si SPY ne baisse pas de > X% en 7j = win.
  Impact : Theta income passif, win rate ~75%
  Priorite : P2
  Temps : 4h
  Dependances : [IBKR stable]
  Succes : Sharpe > 0.8, win rate > 70%
  Fichiers : intraday-backtesterV2/strategies/options/put_spread_weekly.py

□ [OPT-2] Earnings IV crush (backtest proxy)
  Description : Comparer gap reel vs implied move pour FAANG+NVDA+TSLA.
    Si gap < implied move 60% du temps = edge exploitable.
    Proxy : historical gap vs average move.
  Impact : Exploitation du vol premium earnings
  Priorite : P2
  Temps : 4h
  Dependances : [IBKR stable, DATA-1]
  Succes : Win rate > 55%, Sharpe > 0.5
  Fichiers : intraday-backtesterV2/strategies/options/earnings_iv_crush.py

□ [FUT-1] ES/NQ trend following 1H (backtest proxy sur SPY)
  Description : Long si prix > EMA20 > EMA50 en 1H. Short inverse.
    Stop 2x ATR(14). Target 3x ATR(14). Proxy sur SPY/QQQ 1H.
  Impact : Trading 23h/24, pas de PDT, decorrelation temporelle
  Priorite : P2
  Temps : 4h
  Dependances : [IBKR stable]
  Succes : Sharpe > 0.5, trades >= 20
  Fichiers : intraday-backtesterV2/strategies/futures/es_trend_1h.py

□ [FX-1] Carry trade AUD/JPY momentum
  Description : Long AUD/JPY si prix > EMA20 daily. Carry ~3-5%/an.
    Stop -2% capital. Levier 2:1 max. Backtest yfinance AUDJPY=X.
  Impact : Decorrelation totale avec equities
  Priorite : P2
  Temps : 3h
  Dependances : [IBKR stable]
  Succes : Return > carry (3%), Sharpe > 0.3
  Fichiers : intraday-backtesterV2/strategies/forex/audjpy_carry.py
```

## AXE 6 : STRATEGIES EU (expansion prudente)

```
□ [EU-1] Valider EU Stoxx Reversion en paper 30j
  Description : Monitorer la strategie pendant 30 jours.
    Sharpe 33 sur 18 trades est SUSPECT. Si Sharpe live < 5 : retirer.
  Impact : Validation ou invalidation de la strategie la plus performante
  Priorite : P0 (monitoring passif)
  Temps : 0h (passif, check hebdomadaire)
  Dependances : []
  Succes : Sharpe live > 5.0 apres 30 jours ou retrait
  Fichiers : output/eu_monitoring/

□ [EU-2] BCE Rate Decision Drift
  Description : Coder et backtester la strategie BCE (8 meetings/an).
    Move > 1-2% sur banques EU post-decision. Couts 0.26% absorbes.
    Donnees IBKR daily 5 ans pour BNP, GLE, DBK.
  Impact : Event-driven EU avec edge large
  Priorite : P1
  Temps : 4h
  Dependances : [IBKR stable]
  Succes : Sharpe > 0.5, edge > 1% par trade
  Fichiers : intraday-backtesterV2/strategies/eu/eu_bce_drift.py

□ [EU-3] ASML Earnings Chain
  Description : ASML reporte 4x/an. Les followers EU (IFX, STMicro)
    reagissent avec lag intraday. Move > 3-5% le jour des earnings.
  Impact : Cross-asset event-driven EU
  Priorite : P2
  Temps : 3h
  Dependances : [IBKR stable, DATA-1]
  Succes : Sharpe > 0.5, edge > 2% par trade
  Fichiers : intraday-backtesterV2/strategies/eu/eu_asml_chain.py

□ [EU-4] Calendrier events EU automatique
  Description : Scraper/hardcoder les dates BCE, earnings EU majeurs
    (LVMH, ASML, SAP, Siemens), PMI/CPI zone euro.
    Stocker dans config/eu_events.json.
  Impact : Prerequis pour les strategies event-driven EU
  Priorite : P1
  Temps : 2h
  Dependances : []
  Succes : Fichier JSON avec 50+ events 2026
  Fichiers : config/eu_events.json, scripts/fetch_eu_events.py
```

## AXE 7 : OPTIMISATION DES 20 STRATEGIES EXISTANTES

```
□ [OPT-B1] Bear parameter tuning (11 strategies intraday)
  Description : Pour chaque strategie, filtrer trades en bear (SPY < SMA200).
    Calculer Sharpe_bear. Si < 0 : recommander pause.
    Si > 0 mais < Sharpe_bull : grid search sur donnees bear only.
  Impact : Les strategies ne perdent plus en bear
  Priorite : P1
  Temps : 6h
  Dependances : [60+ jours de data]
  Succes : Sharpe bear > 0 pour chaque strategie active
  Fichiers : output/bear_parameters.json

□ [OPT-B2] Stops ATR adaptatifs
  Description : Remplacer stops fixes (%) par stops en ATR.
    Haute vol = stop large. Basse vol = stop serre.
    Backtester chaque strategie ATR vs fixe.
  Impact : Moins de faux stops en haute volatilite
  Priorite : P1
  Temps : 4h
  Dependances : []
  Succes : Sharpe ameliore pour >= 3 strategies
  Fichiers : Strategies individuelles

□ [OPT-B3] Alpha decay monitoring automatise
  Description : Sharpe rolling 30 trades par strategie.
    Regression lineaire sur le Sharpe rolling.
    Si pente negative et p < 0.1 : alerte Telegram.
  Impact : Detection precoce de l'obsolescence des strategies
  Priorite : P2
  Temps : 3h
  Dependances : [60+ jours de data live]
  Succes : Dashboard avec trend line par strategie
  Fichiers : core/alpha_decay_monitor.py, dashboard/api/main.py

□ [OPT-B4] Signal confluence amplifier
  Description : Quand 2+ strategies signalent le meme ticker/direction :
    doubler la taille. Si sens oppose : skip le ticker.
    Backtester confluence vs solo.
  Impact : Amplifie les signaux a haute conviction
  Priorite : P1
  Temps : 3h
  Dependances : []
  Succes : Sharpe confluence > Sharpe solo de > 0.5
  Fichiers : scripts/paper_portfolio.py

□ [OPT-B5] Monitoring slippage reel des strategies fragiles
  Description : Comparer prix fill Alpaca vs prix signal pour
    DoW (break-even 0.020%), Triple EMA (0.04%), Late Day MR (0.05%).
    Si slippage reel > break-even : retirer la strategie.
  Impact : Elimine les strategies non-viables en live
  Priorite : P0
  Temps : 1h (check hebdomadaire)
  Dependances : [30+ jours de data live]
  Succes : Slippage reel < break-even pour chaque strategie
  Fichiers : output/slippage_monitoring/
```

## AXE 8 : INFRASTRUCTURE ET QUALITE

```
□ [INFRA-1] CI/CD GitHub Actions
  Description : .github/workflows/test.yml — pytest a chaque push,
    verifier PAPER_TRADING, bloquer deploy si fail.
  Impact : Zero regression en production
  Priorite : P1
  Temps : 2h
  Dependances : []
  Succes : Badge vert, deploy bloque si test fail
  Fichiers : .github/workflows/test.yml

□ [INFRA-2] Tests d'integration broker (mock)
  Description : Mock Alpaca et IBKR API. Tester bracket orders,
    fills partiels, deconnexions, erreurs 429.
  Impact : Detecte les bugs broker avant le live
  Priorite : P1
  Temps : 4h
  Dependances : []
  Succes : 10+ tests d'integration passent
  Fichiers : tests/test_broker_integration.py

□ [INFRA-3] Couverture tests 5.6% -> 15%
  Description : paper_portfolio.py (0% -> 30%), risk_manager.py (-> 50%),
    alpaca_client/client.py (-> 40%), allocator.py (-> 40%).
  Impact : Reduit le risque de regression critique
  Priorite : P2
  Temps : 8h
  Dependances : []
  Succes : pytest --cov > 15%
  Fichiers : tests/test_pipeline.py, tests/test_allocator_full.py

□ [INFRA-4] Monitoring memoire/performance
  Description : Logger RAM toutes les heures, alerter si > 500MB.
    Logger temps d'execution de chaque cycle (cible < 30s).
  Impact : Detecte les memory leaks avant crash
  Priorite : P2
  Temps : 2h
  Dependances : []
  Succes : Pas de degradation memoire sur 7 jours
  Fichiers : worker.py

□ [INFRA-5] IBKR reconnexion automatique
  Description : Gestion deconnexion 24h (dimanche soir).
    Retry avec backoff exponentiel. Failover : strategies EU pausees
    (pas crash) si IBKR down.
  Impact : Resilience EU 24/7
  Priorite : P1
  Temps : 3h
  Dependances : []
  Succes : IBKR se reconnecte automatiquement apres deconnexion
  Fichiers : core/broker/ibkr_adapter.py

□ [INFRA-6] Dashboard deploye accessible mobile
  Description : Deployer le dashboard sur Railway ou StayFlow.
    Accessible depuis mobile pour checks rapides.
    Resume EOD automatique envoye par Telegram.
  Impact : Monitoring sans PC
  Priorite : P2
  Temps : 4h
  Dependances : []
  Succes : Dashboard accessible via URL publique
  Fichiers : dashboard/, Procfile
```

## AXE 9 : DATA ET ML

```
□ [DATA-1] Calendrier d'events enrichi
  Description : Scraper earnings dates (Yahoo), FOMC/CPI/NFP (Fed/BLS),
    BCE meetings, OpEx dates (3eme vendredi). Stocker dans
    config/events_calendar.json, mise a jour hebdomadaire.
  Impact : Les strategies event-driven ont un calendrier fiable
  Priorite : P1
  Temps : 3h
  Dependances : []
  Succes : JSON avec 200+ events 2026
  Fichiers : config/events_calendar.json, scripts/fetch_events.py

□ [DATA-2] Short interest data FINRA
  Description : Donnees FINRA bi-mensuelles. Si short interest baisse
    de > 20% = signal de covering. Filtre pour strategies long momentum.
  Impact : Signal supplementaire pour le momentum
  Priorite : P2
  Temps : 3h
  Dependances : []
  Succes : Donnees short interest pour 50+ tickers
  Fichiers : scripts/fetch_short_interest.py, data_cache/short_interest/

□ [DATA-3] ML signal filter (quand 6+ mois de data live)
  Description : LightGBM avec features (heure, jour, VIX, regime, gap, volume).
    Target : trade profitable oui/non. Si P(profitable) < 0.4 : skip.
    Walk-forward stricte. Forte regularisation.
  Impact : Filtre les trades a faible probabilite
  Priorite : P3
  Temps : 10h
  Dependances : [200+ trades live par strategie]
  Succes : Win rate ameliore de > 5% vs sans filtre
  Fichiers : core/ml_filter.py

□ [DATA-4] Donnees options via IBKR (open interest, IV)
  Description : Fetch open interest et implied vol via IBKR.
    Enrichir OpEx avec le VRAI max pain (calcule depuis l'OI).
  Impact : OpEx passe du round number proxy au max pain reel
  Priorite : P2
  Temps : 4h
  Dependances : [IBKR stable]
  Succes : Max pain calcule et utilise par OpEx
  Fichiers : scripts/fetch_options_data.py, strategies/opex_gamma_pin.py
```

## AXE 10 : SCALING ET PASSAGE LIVE

```
□ [SCALE-1] Paper trading monitoring 60-90 jours
  Description : NE PAS passer live avant J+60. Metriques a suivre :
    Sharpe rolling 30j live vs backtest (ratio > 0.7),
    slippage reel vs theorique, fill rate, kill switch activations.
  Impact : Validation avant engagement de capital reel
  Priorite : P0 (monitoring passif)
  Temps : 0h (passif, check hebdomadaire)
  Dependances : []
  Succes : Sharpe live 60j > 1.0, DD < 5%, slippage < 2x backtest
  Fichiers : output/live_monitoring/

□ [SCALE-2] Passage live Alpaca $25K
  Description : Conditions de passage :
    Sharpe paper 60j > 1.0, DD < 5%, slippage < 2x, toutes strats
    ont signale, CI/CD ok, alerting ok.
    Procedure : PAPER_TRADING=false, deposer $25K, commencer a 50%.
  Impact : Premier capital reel engage
  Priorite : P0 (J+60-90)
  Temps : 1h
  Dependances : [SCALE-1, RISK-6, INFRA-1]
  Succes : Premier trade live execute sans erreur
  Fichiers : .env, scripts/paper_portfolio.py

□ [SCALE-3] Passage live IBKR $5K EU
  Description : Memes conditions que SCALE-2 pour les strategies EU.
    Capital : $5K (strategies EU = 5% du portefeuille global).
  Impact : Portefeuille EU en live
  Priorite : P1 (J+90)
  Temps : 2h
  Dependances : [SCALE-1, IBKR stable]
  Succes : Premier trade EU live execute
  Fichiers : .env, scripts/paper_portfolio_eu.py

□ [SCALE-4] Plan scaling $25K -> $50K -> $100K
  Description : Chaque doubling necessite 60j au niveau precedent,
    Sharpe live > 1.0, verification market impact.
    Exclure tickers < $50M ADV quand capital > $50K.
  Impact : Croissance maitrisee du capital
  Priorite : P2 (J+180)
  Temps : 2h (plan + implementation caps)
  Dependances : [SCALE-2, SCALE-5]
  Succes : Plan documente avec seuils de passage
  Fichiers : docs/scaling_plan.md

□ [SCALE-5] Market impact model
  Description : slippage = f(order_size / ADV, bid_ask_spread).
    Simuler la performance a $50K, $100K, $250K.
    Identifier les strategies qui ne scalent pas (MARA, RIOT).
  Impact : Previent les pertes dues au market impact
  Priorite : P2
  Temps : 4h
  Dependances : [SCALE-2]
  Succes : Modele calibre, strategies non-scalables identifiees
  Fichiers : core/slippage_model.py

□ [SCALE-6] Tax optimization setup
  Description : Wash sale tracking automatique. Tax-loss harvesting
    avant fin d'annee. Documentation trades pour declaration FR.
  Impact : Optimisation fiscale
  Priorite : P3
  Temps : 4h
  Dependances : [SCALE-2]
  Succes : Rapport fiscal generable automatiquement
  Fichiers : scripts/tax_report.py
```

---

## RESUME PAR PRIORITE

### P0 — Cette semaine (12 items)
```
□ SHORT-1  Sector Relative Weakness Short          3h
□ SHORT-2  High-Beta Underperformance Short         3h
□ SHORT-3  Intraday Trend Exhaustion Short          3h
□ SHORT-4  Late Day Bear Acceleration               3h
□ SHORT-DEPLOY  Deployer les winners short          1h
□ OVERNIGHT-1  Overnight SPY simple (5Y daily)      2h
□ OVERNIGHT-2  Overnight Sector simple              2h
□ RISK-2   Cap sectoriel enforced                   1h
□ OPT-B5   Monitoring slippage strategies fragiles  1h
□ EU-1     Valider EU Stoxx Reversion (passif)      0h
□ SCALE-1  Paper trading monitoring (passif)        0h
                                          TOTAL : ~19h
```

### P1 — Ce mois (18 items)
```
□ SHORT-5  Cross-Asset Risk-Off Confirmation        3h
□ SHORT-6  OpEx Short Extension                     2h
□ OVERNIGHT-3  Overnight Short Bear simple          2h
□ OVERNIGHT-ENGINE  Support overnight pipeline      3h
□ RISK-1   VaR historique bootstrap                 2h
□ RISK-3   Drawdown-based deleveraging              3h
□ RISK-6   CI/CD GitHub Actions                     2h
□ ALLOC-1  Rebalancing automatique EOD              3h
□ ALLOC-2  Momentum overlay                         2h
□ ALLOC-4  Multi-bucket allocation                  3h
□ ALLOC-5  Allocation bear-specific                 2h
□ OPT-B1   Bear parameter tuning                    6h
□ OPT-B2   Stops ATR adaptatifs                     4h
□ OPT-B4   Signal confluence amplifier              3h
□ EU-2     BCE Rate Decision Drift                  4h
□ EU-4     Calendrier events EU                     2h
□ DATA-1   Calendrier d'events enrichi              3h
□ INFRA-1  CI/CD GitHub Actions                     2h
□ INFRA-2  Tests d'integration broker               4h
□ INFRA-5  IBKR reconnexion auto                    3h
□ SCALE-3  Passage live IBKR $5K                    2h
                                          TOTAL : ~60h
```

### P2 — J+60 (13 items)
```
□ RISK-4   Correlation stress monitoring            3h
□ RISK-5   Pre-market stop overnight                2h
□ RISK-7   Couverture tests 15%                     8h
□ OPT-B3   Alpha decay monitoring                   3h
□ EU-3     ASML Earnings Chain                      3h
□ OPT-1    Weekly put credit spreads                4h
□ OPT-2    Earnings IV crush                        4h
□ FUT-1    ES/NQ trend following 1H                 4h
□ FX-1     Carry AUD/JPY                            3h
□ DATA-2   Short interest FINRA                     3h
□ DATA-4   Donnees options IBKR                     4h
□ INFRA-3  Couverture tests 15%                     8h
□ INFRA-4  Monitoring memoire                       2h
□ INFRA-6  Dashboard deploye mobile                 4h
□ SCALE-2  Passage live Alpaca $25K                 1h
□ SCALE-4  Plan scaling                             2h
□ SCALE-5  Market impact model                      4h
                                          TOTAL : ~62h
```

### P3 — J+180 (3 items)
```
□ DATA-3   ML signal filter                        10h
□ SCALE-6  Tax optimization                         4h
                                          TOTAL : ~14h
```

---

## METRIQUES DE SUCCES PAR PHASE

| Metrique              | Actuel    | Phase 1   | Phase 2   | Phase 3   | Phase 4   |
|-----------------------|-----------|-----------|-----------|-----------|-----------|
| Strategies actives    | 20        | 24-26     | 28-30     | 32-35     | 35-40     |
| Ratio Long/Short      | 75/20/5   | 55/30/15  | 50/30/20  | 50/25/25  | 45/30/25  |
| Investissement        | 40%       | 55%       | 70%       | 80%       | 85%       |
| Sharpe live           | N/A       | N/A       | ~1.5      | ~1.8      | > 2.0     |
| Sharpe bear           | ~0        | > 0.3     | > 0.5     | > 0.8     | > 1.0     |
| Max DD                | N/A       | < 3%      | < 5%      | < 5%      | < 8%      |
| VaR 99% daily         | 3%        | 3%        | 2.5%      | 2.5%      | 2%        |
| Classes d'actifs      | 1         | 1         | 2         | 3-4       | 4-5       |
| Test coverage         | 5.6%      | 8%        | 15%       | 20%       | 25%       |
| Capital live          | $0        | $0        | $0        | $25K+5K   | $50K+     |

---

*TODO V3 generee le 26 mars 2026*
*52 items | 10 axes | 4 phases | ~155h total estime*
*Ancree dans la Due Diligence et les contraintes reelles du projet*
