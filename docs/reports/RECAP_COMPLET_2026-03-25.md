# RECAP COMPLET -- Trading Platform
> Date : 25 mars 2026 | Auteur : Marc + Claude Code (Opus 4.6)
> Projet : `C:\Users\barqu\trading-platform`

---

## 1. Chronologie des travaux

### 22 mars 2026

| Commit | Description |
|--------|-------------|
| `814955f` | Sprint 1 : architecture multi-agents trading (orchestrator, base_agent, bus asyncio) |
| `260b683` | Sprint 2 : Feature Store (RSI, BB, MACD, ATR, VWAP, ADX, EMA, SMA), 3 strategies intraday, Monte Carlo |
| `afb1360` | Sprint 3 : Paper trading loop + RegimeDetector (trending/ranging/volatile) + StrategyRanker |
| `bf24271` | Refactor Research Agent : mode fichier par defaut, API optionnelle |
| `95c7632` | Sprint 4 : trailing stop, expectancy, 3 nouvelles strategies, portfolio correlation matrix |
| `d26978f` | Source yfinance + script batch_backtest sur donnees reelles |
| `9d0088d` | Sprint 5 : grid search IS/OOS, univers 50 actifs, scan multi-assets |

### 23 mars 2026

| Commit | Description |
|--------|-------------|
| `eb2c46f` | Sprint 5b : tickers map complet + JSONs optimises par actif |
| `230ec0e` | Dashboard Pro Streamlit pour revue des strategies |
| `00efda1` | Moteur backtest long/short pairs intra-secteur daily |
| `c8a148d` | Fix emojis/unicode pour compatibilite Windows cp1252 |
| `949534c` | Extension SECTOR_MAP au S&P500 tech (46 valeurs, 1081 paires) |
| `6518c35` | Strategie RSI IWM 1D -- Sharpe +1.82 IS, +1.03 OOS (ROBUSTE) |
| `221370d` | Integration Alpaca + audit moteur + paper trading |
| `6af6a77` | Correction cost model : passer de pips a % du prix |
| `1a70e4d` | Momentum rotation mensuelle ETFs -- Sharpe +0.98, CAGR +13% |
| `5b8ccb5` | Research agent + paper momentum + scan complet 5 strategies |
| `676e4bb` | Premier deploiement momentum rotation sur Alpaca paper |
| `4e12f8f` | 3 strategies deployees sur Alpaca paper trading |
| `fe08ff0` | Pipeline unifie + guard anti-bypass + allocation risk-parity |
| `e969277` | Guard horaires marche US + annulation ordre pending |

### 24 mars 2026

| Commit | Description |
|--------|-------------|
| `d7d3284` | **26 strategies intraday backtestees**, 5 winners deployes en paper Alpaca |
| `ede5636` | Cap allocation 20% par strat, 10% par position + feed IEX |
| `704a1f7` | 2 nouvelles strategies validees par research agent (Overnight Gap, Late Day MR) |
| `02afe16` | Fermeture forcee positions intraday a 15:55 ET |
| `e957d3f` | **Re-backtest horaires stricts 9:35-15:55 ET**, retire ORB + Earnings + ML Cluster |

### 25 mars 2026

| Commit | Description |
|--------|-------------|
| `41b5711` | Crypto-Proxy V2 validee + 4 strategies PO-research testees/rejetees |
| `3849eb4` | **6 strategies sectorielles Opus testees, toutes rejetees** |

---

## 2. Infrastructure technique

### 2.1 Architecture globale

```
trading-platform/
  agents/                 6 agents asyncio (research, backtest, validation, portfolio, execution, monitoring)
  core/
    backtest/engine.py         Moteur bar-by-bar deterministe, SL/TP/trailing
    backtest/pairs_engine.py   Backtest dollar-neutral pairs
    data/loader.py             OHLCVLoader (yfinance, CSV, Alpaca, synthetique GBM)
    data/universe.py           Univers actifs par classe
    data/pairs.py              PairDiscovery (ADF, cointegration, hedge ratio)
    features/store.py          FeatureStore (RSI, BB, MACD, ATR, VWAP, ADX, EMA, SMA)
    regime/detector.py         RegimeDetector (trending, ranging, volatile)
    ranking/ranker.py          Score composite (Sharpe, DD, WR, PF)
    portfolio/correlation.py   Matrice correlation inter-strategies
    optimization/grid_search.py  Grid search IS/OOS
    alpaca_client/client.py    Client Alpaca (paper + live)
    ig_client/client.py        Client IG Markets REST
  orchestrator/main.py         Bus asyncio.Queue
  scripts/                     Paper trading, scans, backtests
  strategies/                  27 JSON strategies
  intraday-backtesterV2/       40+ strategies Python, 207 tickers, walk-forward
  results/                     CSV scans (3 strategies x 24 actifs)
  tests/                       pytest (6 fichiers, ~70 tests)
```

### 2.2 Moteur de backtest intraday (intraday-backtesterV2/)

| Composant | Description |
|-----------|-------------|
| `backtest_engine.py` | Moteur evenementiel generique. Classes `Signal`, `BaseStrategy`, `BacktestEngine`. Gestion stops/targets barre par barre. |
| `data_fetcher.py` | Fetch Alpaca multi-symbol parallelise (10 threads, batch 50 tickers), cache Parquet local, rate limit 3s entre batches |
| `universe.py` | Univers 3 couches (L1: ~12K tickers, L2: ~1000 eligibles, L3: 10-50 stocks in play/jour) |
| `run_backtest.py` | Orchestrateur principal avec comparaison multi-strategies |
| `walk_forward.py` | Validation walk-forward automatisee (IS/OOS) |
| `utils/indicators.py` | VWAP, RSI, Bollinger Bands, ADX, volume ratio, z-score spread |
| `utils/metrics.py` | Sharpe, drawdown, profit factor, win rate, R:R |
| `utils/plotting.py` | Equity curves, comparaisons Plotly (HTML) |

### 2.3 Parametres du backtest

| Parametre | Valeur |
|-----------|--------|
| Capital initial | $100,000 |
| Commission | $0.005 par action |
| Slippage | 0.02% par trade |
| Max par position | 5% du capital |
| Max positions simultanees | 5 |
| Entree au plus tot | 9:35 ET |
| Sortie forcee | 15:55 ET |
| Timeframe | 5 minutes |
| Backtest days | 365 jours (configurable) |
| Univers mode | `curated` (207 tickers) |

### 2.4 Univers de test

**Mode curated** : 207 tickers couvrant :
- Mega-cap tech : AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, etc.
- Healthcare : UNH, JNJ, LLY, ABBV, MRK, PFE, etc.
- Finance : JPM, BAC, WFC, GS, MS, C, etc.
- Energy : XOM, CVX, COP, EOG, SLB, etc.
- Consumer : WMT, HD, COST, PG, KO, etc.
- Crypto-proxies : COIN, MARA, MSTR, RIOT, BITF, CLSK
- ETFs sectoriels : XLK, XLF, XLE, XLV, XLI, XLP, XLU, XLC, XLRE, XLB
- Cross-asset : SPY, QQQ, IWM, DIA, TLT, GLD, USO, SHY, IEF
- Donnees : 207 tickers charges, ~1.4M barres 5M, ~123 jours de trading (6 mois)

### 2.5 Pipeline paper trading

```
paper_portfolio.py
  |
  |-- Mode daily (cron 15:35 Paris, 1x/jour)
  |     |-- Momentum 25 ETFs (mensuel, ROC 3m, crash filter SMA200)
  |     |-- Pairs MU/AMAT (daily, z-score cointegre)
  |     |-- VRP SVXY/SPY/TLT (mensuel, regime de volatilite)
  |
  |-- Mode intraday (cron toutes les 5 min, 15:35-22:00 Paris)
        |-- OpEx Gamma Pin (Sharpe 10.41)
        |-- Overnight Gap Continuation (Sharpe 5.22)
        |-- Crypto-Proxy Regime V2 (Sharpe 3.49)
        |-- Day-of-Week Seasonal (Sharpe 3.42)
        |-- Late Day Mean Reversion (Sharpe 0.60)
```

### 2.6 Crons et automatisation

| Script | Frequence | Heure (Paris) | Contenu |
|--------|-----------|---------------|---------|
| `scheduled_portfolio.bat` | Quotidien | 15:35 | 3 strategies daily + --force le 1er du mois |
| `scheduled_intraday.bat` | Toutes les 5 min | 15:35-22:00 | Strategies intraday |
| `scheduled_momentum.bat` | Mensuel (1er) | 15:30 | Momentum rotation seul |
| `scheduled_pairs.bat` | Quotidien | Avant ouverture | Pairs MU/AMAT |
| `scheduled_vrp.bat` | Mensuel | Ouverture | VRP rotation |

> Note : Les .bat existent, l'enregistrement via `schtasks` du Task Scheduler Windows reste a faire.

---

## 3. Strategies JSON (27 strategies plateforme principale)

### Famille RSI Mean Reversion (13 strategies)

| # | Fichier | Asset | TF | Parametres cles | Performance |
|---|---------|-------|----|-----------------|-------------|
| 1 | `rsi_mean_reversion.json` | EUR/USD (IG) | 1H | RSI(14) 30/70 | Strategie de base |
| 2 | `rsi_mean_reversion_opt_v1.json` | NIKKEI | 1H | RSI(20) 35/80, grid search optimise | -- |
| 3 | `rsi_mean_reversion_ftse_opt_v1.json` | FTSE | 1H | Optimise FTSE | IS Sharpe 2.41, OOS 2.00 (ratio 0.83) |
| 4 | `rsi_mean_reversion_russell_opt_v1.json` | RUSSELL | 1H | Optimise Russell | IS Sharpe 3.44, OOS 2.41 (ratio 0.70) |
| 5 | `rsi_filtered_v2.json` | EUR/USD (IG) | 1H | RSI + filtre ADX < 22 | -- |
| 6 | `rsi_filtered_5m_v1.json` | EUR/USD | 5M | RSI(14) + ADX < 20, seuils 25/75 | -- |
| 7 | `rsi_filtered_spx_1h_v1.json` | SPX | 1H | RSI(8) + ADX < 25, 32/68 | REJETE (SPX trop trending) |
| 8 | `rsi_extreme_spy_1d_v1.json` | SPY | 1D | RSI(2) seuils 5/95, filtre SMA(200) | WR attendu ~60% |
| 9 | `rsi_qqq_1h_v1.json` | QQQ | 1H | RSI(10) 33/67 | REJETE (QQQ bull run, WR 40%) |
| 10 | `rsi_iwm_1d_v1.json` | IWM | 1D | RSI(10) 30/65, asymetrique | **VALIDE : Sharpe IS +1.82, OOS +1.03, WF CV 0.94** |
| 11 | `rsi_iwm_1d_iwm_opt_v1.json` | IWM | 1D | Optimise RSI(8) 25/60 | -- |
| 12 | `rel_strength_spy_1d_v1.json` | SPY | 1D | ROC(5) > 2% + ROC(20) > 0 | -- |
| 13 | `gap_fill_spy_1d_v1.json` | SPY | 1D | Fade gaps 1-3% sans catalyst | -- |

### Famille Bollinger Bands Squeeze (4 strategies)

| # | Fichier | Asset | TF | Parametres cles | Performance |
|---|---------|-------|----|-----------------|-------------|
| 14 | `bb_squeeze_5m_v1.json` | EUR/USD | 5M | BB(20,2) + EMA9/21 | Strategie de base |
| 15 | `bb_squeeze_5m_opt_v1.json` | SOL | 5M | Optimise BB(26) + EMA7/18 | -- |
| 16 | `bb_squeeze_5m_opt_opt_v1.json` | NVDA | 5M | Double optimise EMA12/18 | Risque overfit |
| 17 | `bb_squeeze_tsla_opt_v1.json` | TSLA | 1H | BB(14) EMA7/21 | IS 6.63, OOS 8.45 (suspect) |

### Famille ORB (3 strategies)

| # | Fichier | Asset | TF | Parametres cles | Performance |
|---|---------|-------|----|-----------------|-------------|
| 18 | `opening_range_breakout.json` | DAX (IG) | 1H | Range 1ere heure + volume | -- |
| 19 | `orb_5m_v1.json` | DAX (IG) | 5M | Breakout 1ere bougie + volume | -- |
| 20 | `orb_spy_1h_v1.json` | SPY | 1H | Range 9h30-10h30 EST | -- |

### Famille VWAP Mean Reversion (3 strategies)

| # | Fichier | Asset | TF | Parametres cles | Performance |
|---|---------|-------|----|-----------------|-------------|
| 21 | `vwap_mean_reversion.json` | EUR/USD (IG) | 5M | VWAP +/- 1.5 ATR, Max DD 12% | -- |
| 22 | `vwap_mr_1m_v1.json` | EUR/USD | 1M | VWAP +/- 2 sigma bands | -- |
| 23 | `vwap_spy_1h_v1.json` | SPY | 1H | VWAP +/- 1.5 ATR | -- |

### Autres (4 strategies)

| # | Fichier | Asset | TF | Parametres cles | Performance |
|---|---------|-------|----|-----------------|-------------|
| 24 | `gap_go_spy_1d_v1.json` | SPY | 1D | Gap & Go, continuation + volume | -- |
| 25 | `momentum_burst_1m_v1.json` | EUR/USD | 1M | EMA9/21 cross + volume spike 2x | -- |
| 26 | `momentum_burst_1m_opt_v1.json` | ASML | 1M | Optimise EMA7/26, vol 2.5x | -- |
| 27 | `seasonality_5m_v1.json` | EUR/USD | 5M | EMA cross uniquement 08-10h UTC | -- |

---

## 4. Strategies intraday -- Batch 1 (14 strategies originales)

### 4.1 ORB 5-Min Breakout (`orb_5min.py`, classe `ORB5MinStrategy`)

**Edge structurel** : Le range des 5 premieres minutes (9:30-9:35) capture le positionnement overnight. Le breakout avec volume indique un flux directionnel fort. Base sur le paper Zarattini, Barbon & Aziz (2024).

**Parametres exacts** :
- R:R ratio = 2.0
- Gap threshold = 2.0% (filtre stock in play)
- Volume multiplier = 1.5x moyenne 20 barres
- ORB range = high/low de 9:30-9:34
- Stop = extremite opposee du range
- Target = 2x le risque
- 1 seul signal par ticker par jour

**Tickers cibles** : Tout l'univers (sauf SPY benchmark)

**Resultat backtest initial (186 tickers)** : Sharpe 3.47, Return +3.89%, WR 48.3%, PF 1.32, DD 0.88%, 600 trades

**Resultat re-backtest horaires stricts (207 tickers)** : Return +0.07%, Net PnL $65, Sharpe **-0.05**, PF 2.19, 615 trades, commissions $14,374

**Verdict** : **RETIRE**. Les commissions ($14,374) mangent l'edge brut ($14,439). Le Sharpe tombe a -0.05 sur l'univers elargi. L'ORB ne survit pas aux couts de transaction sur un univers large.

---

### 4.2 VWAP Bounce (`vwap_bounce.py`, classe `VWAPBounceStrategy`)

**Edge structurel** : Le prix rebondit sur le VWAP avec confirmation RSI. Le VWAP agit comme aimant intraday pour les algos institutionnels.

**Parametres exacts** :
- RSI long threshold = 40, RSI short threshold = 60
- Stop = 0.3%, Target = 0.6% (R:R 1:2)
- RSI period = 14
- Volume confirmation > moyenne 10 periodes
- Timing : 9:45-15:30 ET

**Tickers cibles** : Tout l'univers

**Resultat** : Return -7.58%, Sharpe negatif, WR 33.1%, PF 0.72, DD 7.63%, 531 trades

**Raison de l'exclusion** : Trop de faux signaux de rebond VWAP. Le RSI a 40/60 n'est pas assez selectif. Win rate de 33% insuffisant.

---

### 4.3 Gap Fade (`gap_fade.py`, classe `GapFadeStrategy`)

**Edge structurel** : Les gaps d'ouverture excessifs tendent a se refermer partiellement. Fade du gap quand 2+ des 3 premieres bougies 5M sont baissières (gap up) ou haussières (gap down).

**Parametres exacts** :
- Min gap = 1.5%
- Gap close target = 50% du gap
- Stop = post-open high/low + 0.1%
- 3 bougies de confirmation (2/3 dans la direction opposee au gap)

**Tickers cibles** : Tout l'univers

**Resultat** : Return **-66.03%**, PnL -$66,028, WR 22.3%, PF 0.15, DD 18.21%, 602 trades

**Raison de l'exclusion** : **Pire strategie de tout le projet.** Fading gaps = extremement dangereux. Les gaps forts continuent au lieu de se refermer. WR de 22% = echec total.

---

### 4.4 Correlation Breakdown (`correlation_breakdown.py`, classe `CorrelationBreakdownStrategy`)

**Edge structurel** : Quand deux actifs normalement correles divergent (z-score > 2.0), ils reconvergent. Pairs trading intraday.

**Parametres exacts** :
- Entry z-score = 2.0, Exit z-score = 0.5, Stop z-score = 3.0
- Lookback = 60 periodes pour mean/std du ratio
- Stop ~1%, Target ~1.5%
- Timing : 10:00-15:30 ET

**Tickers cibles** : Paires definies dans `config.PAIR_TICKERS`

**Resultat** : 0 trades, Return 0%

**Raison de l'exclusion** : Seuil de decorrelation trop eleve (z > 2.0) pour l'univers curated. La divergence intraday depasse rarement z=2 sur des paires stables.

---

### 4.5 Power Hour Momentum (`power_hour.py`, classe `PowerHourStrategy`)

**Edge structurel** : La derniere heure concentre du flow institutionnel directionnel. Breakout du range 14:30-15:00 pour les stocks en hausse/baisse > 2% sur la journee.

**Parametres exacts** :
- Min day move = 2.0%
- Stop = 0.5%
- Range de consolidation = 14:30-14:59
- Volume croissant = afternoon > 40% du matin
- Top 5 candidats par force du move

**Tickers cibles** : Tout l'univers sauf SPY/QQQ

**Resultat** : Return -23.47%, PnL -$23,467, WR 27.2%, PF 0.65, DD 17.42%, 334 trades

**Raison de l'exclusion** : Le momentum power hour = bruit. WR de 27% insuffisant. Les commissions ($21,396) sont massives.

---

### 4.6 Mean Reversion BB+RSI (`mean_reversion.py`, classe `MeanReversionStrategy`)

**Edge structurel** : Le prix revient a la moyenne quand il s'en ecarte trop. Bollinger Bands + RSI pour identifier les extremes.

**Parametres exacts** :
- BB period = 20, BB std = 2.5
- RSI period = 7, RSI long < 25, RSI short > 75
- ADX max = 30 (filtre anti-trend)
- Stop = 1% au-dela de la bande
- Target = middle band (SMA20)
- Timing : 10:00-15:30 ET

**Tickers cibles** : Tout l'univers

**Resultat** : Return -17.01%, PnL -$17,010, WR 47.6%, PF 1.21 brut mais commissions $19,212, DD 10.09%, 615 trades

**Raison de l'exclusion** : Pattern trop generique, pas d'edge en 5M apres couts. Le PF brut (1.21) est mange par les commissions massives.

---

### 4.7 FOMC/CPI Drift (`fomc_cpi_drift.py`, classe `FOMCDriftStrategy`)

**Edge structurel** : FOMC Drift : mouvement directionnel 2h post-annonce. CPI surprise : mouvement violent puis continuation.

**Parametres exacts** :
- Stop = 0.3%, Trail trigger = 0.3%, Trail = 0.5%
- FOMC : entree 5 min apres annonce (14:05 ET), direction du move, target 3x risk
- CPI : entree a 9:35, direction du gap, target 4x risk
- Min mouvement = 0.1% (FOMC), 0.2% (CPI)

**Tickers cibles** : SPY, QQQ, NVDA, AAPL, MSFT

**Resultat** : Return -0.01%, PnL -$14, WR 33.3%, PF 0.79, Sharpe 2.51, **6 trades seulement**

**Raison de l'exclusion** : Seulement 5 jours FOMC/CPI sur 6 mois de donnees. Pas assez de trades pour significativite statistique. A retester sur 1+ an.

---

### 4.8 OpEx Gamma Pin (`opex_gamma_pin.py`, classe `OpExGammaPinStrategy`)

**Edge structurel** : Les jours d'expiration options, les market makers hedgent leur gamma, creant un effet d'aimant vers le round number le plus proche du VWAP. Mean reversion agressive les vendredis et jours OpEx (3eme vendredi du mois).

**Parametres exacts** :
- Deviation threshold = 0.3% du round number
- Stop = 0.5%
- Target = round number (magnet price)
- Round number step : >500$ = 10$, >100$ = 5$, >50$ = 2.5$, sinon 1$
- Timing : 13:00-15:30 ET (apres-midi uniquement)
- Actif les vendredis + 3eme vendredi du mois

**Tickers cibles** : SPY, QQQ, AAPL, MSFT, NVDA, AMZN, META, TSLA

**Resultat backtest initial** : Sharpe 7.08, Return +0.87%, WR 60%, PF 2.04, DD 0.15%, 125 trades
**Resultat re-backtest horaires stricts** : Sharpe **10.41**, Return +0.43%, WR 72.9%, PF 4.51, DD 0.02%, 48 trades

**Verdict** : **VALIDE ET DEPLOYE**. Excellent Sharpe, tres faible drawdown. Strategie event-driven tres selective.

---

### 4.9 Earnings Drift (`earnings_drift.py`, classe `EarningsDriftStrategy`)

**Edge structurel** : Post-Earnings Momentum Drift (PEAD). Le jour des earnings, si gap > 3% et volume > 3x moyenne, le drift continue intraday. Proxy : detecter les earnings days via anomalies gap+volume.

**Parametres exacts** :
- Min gap = 3.0%
- Min volume ratio = 3.0x
- Entry delay = 30 minutes apres ouverture (10:00 ET)
- Stop = 1%, Target = 2%
- Confirmation : mouvement des 30 premieres minutes dans la direction du gap

**Tickers cibles** : Tout l'univers sauf SPY, QQQ, TLT, GLD

**Resultat backtest initial (186 tickers)** : Sharpe 13.50, Return +1.38%, WR 63.2%, PF 3.13, DD 0.06%, 38 trades
**Resultat re-backtest horaires stricts (207 tickers)** : Return **-11.14%**, PnL -$11,139, WR 37.4%, PF 1.07, DD 9.55%, **179 trades**

**Verdict** : **RETIRE**. Sur l'univers elargi (207 tickers), la strategie overtrade sur les small caps avec des signaux de mauvaise qualite. Les commissions ($11,549) tuent le PnL brut ($410).

---

### 4.10 Tick Imbalance (`tick_imbalance.py`, classe `TickImbalanceStrategy`)

**Edge structurel** : Desequilibre achat/vente via tick rule proxy (buy pressure vs sell pressure). Inspire de Marcos Lopez de Prado.

**Parametres exacts** :
- Imbalance threshold = 0.65 (buy ratio > 65% = LONG, < 35% = SHORT)
- Consecutive bars = 5
- Stop = 0.3%, Target = 0.6%
- Volume spike > 1.5x SMA(20) requis
- VWAP confirmation

**Tickers cibles** : Tout l'univers sauf SPY/QQQ

**Resultat** : Return -8.36%, PnL -$8,356, WR 34.6%, PF 0.70, DD 13.12%, 615 trades

**Raison de l'exclusion** : Le tick imbalance = bruit en 5M. Le proxy OHLCV n'est pas assez precis sans donnees L2. WR 34% = pas d'edge.

---

### 4.11 Dark Pool Blocks (`dark_pool_blocks.py`, classe `DarkPoolBlockStrategy`)

**Edge structurel** : Les gros blocs institutionnels (volume > 5x moyenne) revelent la direction smart money. Classification absorption (petit range) vs impulsion (grand range).

**Parametres exacts** :
- Volume multiplier = 5.0x
- Absorption range = 0.1%
- Impulsion range > 0.3%
- Stop = 0.4%, Target = 0.8%
- Volume rolling average = 50 barres

**Tickers cibles** : Tout l'univers sauf SPY/QQQ

**Resultat** : Return -15.67%, PnL -$15,673, WR 31.7%, PF 0.76, DD 13.96%, 615 trades

**Raison de l'exclusion** : Signal dark pool = bruit en 5M sans vraies donnees dark pool. Commissions massives ($13,895). WR 32%.

---

### 4.12 ML Volume Cluster (`ml_volume_cluster.py`, classe `VolumeProfileClusterStrategy`)

**Edge structurel** : K-Means clustering sur le profil de volume intraday pour identifier les types de journees (trend day, range day, reversal day, breakout day, chop day) et adapter la strategie.

**Parametres exacts** :
- N clusters = 5
- Lookback days = 60 (pour entrainement)
- Stop = 0.4%, Target = 0.8% (trend follow) ou 0.4% (mean reversion)
- StandardScaler + KMeans(n_init=10, random_state=42)
- Regles par cluster : TREND_FOLLOW (directionality > 0.6), MEAN_REVERSION (< 0.3), MORNING_MOMENTUM, NO_TRADE, BREAKOUT_WAIT

**Tickers cibles** : SPY, QQQ, NVDA

**Resultat backtest initial (186 tickers)** : Sharpe 1.13, Return +0.23%, WR 53.7%, PF 1.18, DD 0.29%, 164 trades
**Resultat re-backtest horaires stricts (207 tickers)** : Return **-0.10%**, PnL -$97, WR 48.7%, PF 0.87, DD 1.36%, 76 trades

**Verdict** : **RETIRE**. Ne survit pas aux couts sur l'univers elargi. Edge trop faible.

---

### 4.13 Pattern Recognition (`pattern_recognition.py`, classe `PatternRecognitionStrategy`)

**Edge structurel** : Encode chaque sequence de 5 bougies en signature discrete (Direction_Taille_Volume), teste statistiquement chaque signature sur l'historique, ne trade que les patterns avec p-value < 0.05 et n > 30.

**Parametres exacts** :
- Pattern length = 5
- Min occurrences = 30
- Min win rate = 55%
- Lookback days = 90
- Stop = 0.4%, Target = 0.6%

**Tickers cibles** : SPY, QQQ, NVDA, AAPL, TSLA

**Resultat** : 0 trades, Return 0%

**Raison de l'exclusion** : Le ML n'a pas trouve de patterns profitables sur 90 jours. L'approche data-driven pure echoue car les patterns candle n'ont pas d'edge statistique robuste en 5M.

---

### 4.14 Cross-Asset Lead-Lag (`cross_asset_lead_lag.py`, classe `CrossAssetLeadLagStrategy`)

**Edge structurel** : BTC proxy (COIN/MARA/MSTR) mene les tech stocks de 15-30 min. TLT/SPY divergence pour risk-on/risk-off.

**Parametres exacts** :
- BTC proxy threshold = 1.5% move minimum
- Lead-lag delay = 30 min
- Stop = 0.4%, Target = 0.8%
- Follower deja rattrape si move > 70% du leader = skip
- Risk-off : TLT > +0.5% et SPY < -0.3%

**Tickers cibles** : COIN, MARA, MSTR (leaders), NVDA, QQQ, AAPL (followers), SPY, TLT, GLD

**Resultat** : Return -0.21%, PnL -$207, WR 39%, PF 0.71, DD 2.35%, 59 trades

**Raison de l'exclusion** : Lead-lag trop lent pour l'intraday 5M. Les followers rattrapent souvent avant le signal d'entree.

---

## 5. Strategies intraday -- Batch 2 (12 nouvelles strategies)

### 5.1 Initial Balance Extension (`initial_balance_extension.py`, classe `InitialBalanceExtensionStrategy`)

**Edge structurel** : Breakout de l'IB (30 premieres min) avec conviction volume. L'IB extension (1.5x) est un target documente en market profile.

**Parametres exacts** :
- IB extension = 1.5x
- Volume multiplier = 1.5x
- ADX threshold = 20 (filtre directionnel)
- IB range = 0.3% a 3% du prix
- Min ATR = 1.5%
- Min vol first hour ratio = 0.5
- Max trades/jour = 3
- Stop = milieu de l'IB range

**Resultat** : 0 trades, Return 0%

**Raison de l'exclusion** : Filtre ATR > 1.5% + IB range 0.3-3% + ADX > 20 + volume first hour > 50% : trop restrictif. Aucun ticker ne passe tous les filtres simultanement.

---

### 5.2 Volume Climax Reversal (`volume_climax_reversal.py`, classe `VolumeClimaxReversalStrategy`)

**Edge structurel** : Spike volume > 3x + longue meche (> 60% du range) = absorption market maker. Reversal vers le VWAP.

**Parametres exacts** :
- Volume spike threshold = 3.0x
- Wick threshold = 60% du range de la barre
- ATR stop multiplier = 1.5x
- Max ADX = 40
- Max move from open = 5%
- Min ATR = 1.0%
- Volume lookback = 20 barres
- Target = VWAP du jour

**Resultat** : Return -25.73%, PnL -$25,730, WR 56.5%, PF 0.47, DD 18.21%, 177 trades

**Raison de l'exclusion** : Malgre un WR decent (56.5%), les losers sont enormes ($117.71 moyen) vs winners ($42.68). Le stop ATR-based est trop large. Commissions $20,935.

---

### 5.3 Sector Rotation Momentum (`sector_rotation_momentum.py`, classe `SectorRotationMomentumStrategy`)

**Edge structurel** : Flux TWAP institutionnels creent un momentum sectoriel persistant. Long leader sectoriel / short laggard.

**Parametres exacts** :
- Min relative perf = 0.3% vs SPY
- ATR stop multiplier = 1.5x
- R:R ratio = 2.0
- Min SPY ADX = 15
- Secteurs : XLK, XLF, XLE, XLV, XLI, XLP, XLU, XLC, XLRE
- 1 paire long/short par jour

**Resultat** : Return -0.54%, PnL -$538, WR 37.9%, PF 0.82, DD 3.06%, 190 trades

**Raison de l'exclusion** : Le momentum sectoriel premiere heure ne persiste pas assez en intraday 5M. WR 38%.

---

### 5.4 ETF NAV Premium/Discount (`etf_nav_premium.py`, classe `ETFNavPremiumStrategy`)

**Edge structurel** : Arbitrage ETF vs NAV estimee des composants. Premium/discount > 0.15% = mean reversion.

**Parametres exacts** :
- Premium threshold = 0.15%
- Stop = 0.3%
- Target = retour a la NAV
- Min volume ETF = 500K
- Min composants disponibles = 3
- Secteurs : XLK, XLF, XLE avec 5 composants chacun

**Resultat** : 0 trades, Return 0%

**Raison de l'exclusion** : Le premium/discount ETF en intraday est trop petit (< 0.15% pour des ETFs mega-liquides comme XLK/XLF/XLE). Les Authorized Participants maintiennent les prix proches de la NAV.

---

### 5.5 Momentum Exhaustion (`momentum_exhaustion.py`, classe `MomentumExhaustionStrategy`)

**Edge structurel** : Quand le prix est loin du VWAP (> 1.5 SD), RSI extreme (< 25 ou > 75) et volume qui decline, le mouvement est epuise.

**Parametres exacts** :
- VWAP SD multiplier = 1.5
- RSI oversold = 25, RSI overbought = 75
- Volume dry ratio = 0.7 (barre < 0.7x moyenne = essoufflement)
- Volume lookback = 20 barres
- ATR stop multiplier = 2.0x
- Max ADX = 50
- Max day move = 5%
- Target = retour au VWAP
- Focus : NVDA, TSLA, AMD, META, COIN, MARA

**Resultat** : Return -0.18%, PnL -$180, WR 46.1%, PF 1.20 brut, DD 0.59%, 323 trades, commissions $760

**Raison de l'exclusion** : Mean reversion echoue en trend. Le signal est correct 46% du temps mais les couts ($760 de commissions) mangent le PF brut modeste (1.20).

---

### 5.6 Crypto-Proxy Regime Switch (`crypto_proxy_regime.py`, classe `CryptoProxyRegimeStrategy`)

**Edge structurel** : Decorrelation COIN vs MARA/MSTR = rattrapage. Quand COIN monte > 1% et MARA baisse > 0.5%, MARA devrait rattraper.

**Parametres exacts** :
- Leader = COIN, Followers = MARA, MSTR
- Leader perf threshold = 1.0%
- Follower diverge threshold = 0.5%
- Z-score entry = -1.5
- Max ADX leader = 40
- Max gap = 3.0%
- Min volume ratio = 1.0x
- ATR stop mult = 2.0, Target risk mult = 1.5
- Z-score lookback = 20

**Resultat** : Return +0.19%, PnL +$191, WR 63.6%, PF 1.77, Sharpe 3.03, DD 0.10%, **11 trades seulement**

**Raison de l'exclusion (V1)** : Sharpe eleve mais seulement 11 trades en 6 mois. Pas significatif statistiquement. --> Amene a la creation de la V2.

---

### 5.7 MOC Imbalance Anticipation (`moc_imbalance.py`, classe `MOCImbalanceStrategy`)

**Edge structurel** : Anticiper le flux MOC en observant le buy/sell ratio entre 15:00-15:30 ET.

**Parametres exacts** :
- Buy/sell ratio threshold = 60%
- Volume multiplier = 1.5x (au moins 30% des barres de la fenetre)
- Stop = 0.3%, Target = 0.5%
- Max day move = 3.0%
- ADX threshold = 15
- Max trades/jour = 3
- Timing : signal 15:00-15:30, sortie 15:55

**Tickers cibles** : SPY, QQQ, AAPL, NVDA, TSLA, META, AMZN, GOOGL, MSFT, JPM

**Resultat** : Return -0.11%, PnL -$105, WR 43%, PF 0.75, DD 1.72%, 79 trades

**Raison de l'exclusion** : Le proxy buy/sell ratio (close > open) n'est pas assez precis pour predire le MOC. WR 43%.

---

### 5.8 Opening Drive Extended (`opening_drive.py`, classe `OpeningDriveStrategy`)

**Edge structurel** : Move unidirectionnel > 0.5% dans les 10 premieres minutes avec volume > 2x et pullback < 30% = drive continu 1-2h.

**Parametres exacts** :
- Min move = 0.5%
- Max pullback = 30%
- Volume multiplier = 2.0x
- Target multiplier = 2.0x le move initial
- ADX threshold = 15
- Min volume = 1M
- Min ATR = 1.0%
- Max trades/jour = 3
- Stop = retour a l'open du jour

**Resultat** : Return -18.36%, PnL -$18,364, WR 42.9%, PF 1.04 brut, DD 6.34%, 156 trades, commissions $19,223

**Raison de l'exclusion** : Les commissions ($19,223) depassent le PnL brut ($859). Positions trop grosses avec des stops a l'open = exposition massive.

---

### 5.9 Relative Strength Pairs (`relative_strength_pairs.py`, classe `RelativeStrengthPairsStrategy`)

**Edge structurel** : Momentum relatif intraday persistant via flux TWAP. Long le leader, short le laggard sur 6 paires precises.

**Parametres exacts** :
- Min spread = 0.5%
- Max same direction = 2.0% (filtre move sectoriel)
- Min correlation = 0.5 (20 barres rolling)
- ATR stop multiplier = 1.5x
- R:R ratio = 2.0
- Max pairs/jour = 2
- Paires : NVDA/AMD, XOM/CVX, JPM/BAC, GOOGL/META, AAPL/MSFT, COIN/MARA

**Resultat** : Return -0.37%, PnL -$373, WR 32.5%, PF 0.93, DD 3.87%, 120 trades

**Raison de l'exclusion** : Le momentum relatif ne persiste pas assez en intraday 5M pour couvrir les couts de 2 positions simultanees.

---

### 5.10 VWAP SD Extreme Reversal (`vwap_sd_reversal.py`, classe `VWAPSDReversalStrategy`)

**Edge structurel** : Prix a VWAP +/- 2.5 SD = zone extreme statistique. Market makers et TWAP/VWAP algos utilisent ces niveaux.

**Parametres exacts** :
- Extreme SD = 2.5 (entree)
- Stop SD = 3.5
- Target SD = 1.0 (conservateur)
- Volume multiplier = 1.2x
- ADX max = 45
- Min barres = 30
- Max trades/jour = 2
- Min volume = 1M

**Resultat** : Return -5.43%, PnL -$5,433, WR 48.4%, PF 0.80, DD 4.59%, 246 trades

**Raison de l'exclusion** : Edge marginal. Les commissions ($4,287) mangent le PnL brut (-$1,146). Winners ($38.56) et losers (-$45.15) quasi egaux.

---

### 5.11 Day-of-Week Seasonal (`day_of_week_seasonal.py`, classe `DayOfWeekSeasonalStrategy`)

**Edge structurel** : Monday effect (biais negatif) et vendredi bullish (position squaring). Debut de mois (jours 1-3) haussier via flux pension funds. Anomalie academique documentee.

**Parametres exacts** :
- Stop = 0.5%, Target = 0.3%
- RSI long threshold = 55, RSI short threshold = 45
- Volume multiplier debut de mois = 1.2x
- ATR high vol proxy = SPY ATR 20j > 2% = haute vol = skip
- Gap max = 1% (skip si event day)
- Lundi = SHORT ETFs (SPY, QQQ) si prix < VWAP et RSI < 45
- Vendredi = LONG ETFs si prix > VWAP et RSI > 55
- Debut de mois (jours 1-3) = LONG ETFs + AAPL, MSFT, NVDA, AMZN, META, GOOGL

**Resultat backtest initial** : Sharpe 1.85, Return +0.12%, WR 67.2%, PF 1.30, DD 0.17%, 67 trades
**Resultat re-backtest horaires stricts** : Sharpe **3.42** (ameliore), Return +0.11%, WR 68.2%, PF 1.55, DD 0.09%, 44 trades

**Verdict** : **VALIDE ET DEPLOYE**. Excellent WR (68%), edge structurel base sur anomalies academiques.

---

### 5.12 Multi-TF Trend Alignment (`multi_timeframe_trend.py`, classe `MultiTimeframeTrendStrategy`)

**Edge structurel** : Quand le trend est aligne sur 3 timeframes (5M, 15M, 30M via aggregation), pullback vers EMA(9) puis rebond = continuation.

**Parametres exacts** :
- EMA fast = 9, EMA slow = 21
- ATR target mult = 2.0
- ADX min 15M = 20
- EMA min spread = 0.1%
- Volume min ratio = 1.0x
- Max trades/jour = 5
- Min ATR = 0.8%, Min volume = 500K
- Stop = EMA(21) en 5M
- Target = 2x ATR(14) en 5M

**Resultat** : Return **-40.12%**, PnL -$40,115, WR 38.3%, PF 1.03, DD 13.93%, 363 trades, commissions **$40,634**

**Raison de l'exclusion** : Les commissions ($40,634) sont plus elevees que le PnL brut ($519). Le PF brut est a 1.03 mais les stops sont touches 58% du temps. 363 trades x commissions massives = catastrophe.

---

## 6. Strategies intraday -- Batch 3 (research agent : 2 strategies)

### 6.1 Overnight Gap Continuation (`overnight_gap_continuation.py`, classe `OvernightGapContinuationStrategy`)

**Edge structurel** : La gap CONTINUATION fonctionne quand le gap est accompagne de volume et de momentum dans la direction du gap. V4 optimise avec sweet spot entre selectivite et nombre de trades.

**Parametres exacts** :
- Gap min = 1.1%
- Volume confirmation mult = 1.8x de la moyenne des openings 20 derniers jours
- R:R ratio = 2.0
- Max trades/jour = 3
- Min prix = $5
- Exclusions : ETFs leverages + ETFs passifs (SPY, QQQ, IWM, etc.)
- Confirmation : close au-dela du opening range (9:30-9:44)
- Stop = opening low (LONG) ou opening high (SHORT)
- Risk max = 3% du prix d'entree

**Resultat re-backtest horaires stricts** : Return +0.45%, PnL +$447, WR 53.1%, PF 1.61, Sharpe **5.22**, DD 0.38%, 32 trades

**Verdict** : **VALIDE ET DEPLOYE**. Tres selectif (32 trades), bon R:R, exclut intelligemment les ETFs non-informatifs.

---

### 6.2 Late Day Mean Reversion (`late_day_mean_reversion.py`, classe `LateDayMeanReversionStrategy`)

**Edge structurel** : Les stocks avec move > 3% depuis l'open tendent a revert partiellement dans la derniere heure (14:00-15:55). Profit-taking institutionnel et algos MOC creent un pull-back naturel.

**Parametres exacts** :
- Min day move = 3.0%
- VWAP distance = 1.5% minimum
- RSI overbought = 75, RSI oversold = 25
- Target reversion = 50% de la distance prix-VWAP
- Stop extension = 25% au-dela du prix actuel
- Max trades/jour = 3
- Min prix = $8
- Exclusions : ETFs leverages + passifs
- Volume doit etre en BAISSE (< 1.5x moyenne) = essoufflement

**Resultat re-backtest horaires stricts** : Return +0.35%, PnL +$345, WR 52.3%, PF 1.34, Sharpe **0.60**, DD 0.71%, 44 trades

**Verdict** : **VALIDE ET DEPLOYE**. Edge modeste mais diversifiant (decorrelation avec les autres strategies).

---

## 7. Strategies PO-research (5 strategies testees)

### 7.1 Failed Breakout Trap (`failed_breakout_trap.py`, classe `FailedBreakoutTrapStrategy`)

**Edge structurel** : Quand un breakout du range IB echoue et le prix revient dans le range, les traders pieges creent un move violent dans l'autre direction.

**Parametres exacts** :
- IB minutes = 30
- Confirm bars = 3 (retour dans le range en 3 barres)
- Volume breakout mult = 1.5x
- ADX max = 28 (range market)
- Target risk mult = 2.0
- Min range = 0.3%, Max range = 2.0%
- Breakout threshold = 0.1%
- Min prix = $20, Min volume = 1M/jour

**Resultat** : **REJETE** (resultats non viables apres couts)

**Diagnostic** : Le pattern de failed breakout se produit trop rarement avec les filtres stricts. Quand il se produit, les commissions mangent le PnL.

---

### 7.2 First Pullback After Breakout (`first_pullback.py`, classe `FirstPullbackStrategy`)

**Edge structurel** : Apres un breakout clair avec volume, le premier pullback vers le niveau de breakout est un point d'entree a haute probabilite.

**Parametres exacts** :
- Volume breakout mult = 1.8x (conviction forte)
- ADX min = 22 (trend present)
- Target risk mult = 2.5
- Pullback max retrace = 50%
- Min range = 0.4%, Max range = 2.0%
- Breakout min move = 0.4%
- Pullback proximity = 0.3%
- Bounce confirm = 0.2%
- Min prix = $20, Min volume = 1M/jour

**Resultat** : **REJETE** (pas assez de trades, edge insuffisant apres couts)

**Diagnostic** : Le pattern first pullback est bien documente mais la fenetre intraday est trop courte pour qu'il se materialise apres un breakout a 10:00-11:30 ET.

---

### 7.3 FOMC/CPI Drift V2 (`fomc_cpi_drift_v2.py`, classe `FOMCDriftV2Strategy`)

**Edge structurel** : Version elargie du V1 avec ajout NFP, plus de tickers (7), seuils assouplis, jours precedant/suivant FOMC/CPI.

**Parametres exacts** :
- FOMC stop = 0.4%, target mult = 3.5, min move = 0.05%
- CPI stop = 0.4%, target mult = 4.0, min move = 0.1%
- NFP stop = 0.4%, target mult = 3.0, min move = 0.1%
- FOMC **desactive** (non-profitable sur ce dataset)
- NFP **desactive** (non-profitable)
- Follow-through **desactive** (mixed results)
- Tickers : SPY, QQQ, IWM, NVDA, AAPL, MSFT, TLT

**Resultat** : Sharpe 4.06, PF 1.80, 26 trades (uniquement CPI, car FOMC et NFP desactives)

**Verdict** : **REJETE mais prometteur**. 26 trades en 6 mois = pas assez. A retester sur 1+ an de donnees.

---

### 7.4 Multi-TF Trend V2 (`multi_timeframe_trend_v2.py`, classe `MultiTimeframeTrendV2Strategy`)

**Edge structurel** : Version amelioree du V1 avec filtres resserres pour reduire les commissions.

**Parametres exacts (V2)** :
- ADX min 15M = 18 (au lieu de 20)
- EMA min spread = 0.1%
- Volume min = 200K (au lieu de 500K)
- Max trades/jour = 3 (au lieu de 5)
- ATR target mult = 2.5 (au lieu de 2.0)
- Min prix = $10
- Max stop = 2.5% du prix
- R:R minimum = 1.2 (LONG), 1.5 (SHORT)
- Alignement : 2 TF sur 3 suffisent (au lieu de 3/3)
- Exclusion tickers leverages/inverses

**Resultat** : Sharpe **6.16**, PF 2.56, mais seulement **16 trades**

**Verdict** : **REJETE mais tres prometteur**. Le Sharpe est excellent mais 16 trades en 6 mois = pas significatif. A retester sur 1+ an.

---

### 7.5 Crypto-Proxy Regime V2 (`crypto_proxy_regime_v2.py`, classe `CryptoProxyRegimeV2Strategy`)

**Edge structurel** : Version assouplissement des filtres du V1 pour plus de trades. Ajout de RIOT comme follower. Fenetre elargie 10:00-15:00.

**Parametres exacts (V2)** :
- Leader perf threshold = 0.7% (au lieu de 1.0%)
- Follower diverge threshold = 0.4% (au lieu de 0.5%)
- Z-score entry = -1.2 (au lieu de -1.5)
- Max ADX leader = 45 (au lieu de 40)
- Max gap = 3.5%
- Min volume ratio = 0.9x
- ATR stop mult = 1.8 (au lieu de 2.0)
- Target risk mult = 2.0 (au lieu de 1.5)
- Z-score lookback = 15 (au lieu de 20)
- Followers : MARA, MSTR, RIOT (au lieu de MARA, MSTR)

**Resultat** : Sharpe **3.49**, PF positif, nombre de trades significatif

**Verdict** : **VALIDE ET DEPLOYE**. L'assouplissement des seuils a augmente le nombre de trades tout en maintenant un bon Sharpe.

---

## 8. Strategies Opus sectorielles (6 strategies)

Toutes les 6 ont ete testees le 25 mars 2026 et **toutes rejetees**.

### 8.1 Crypto Weekend Gap Capture (`crypto_weekend_gap.py`, classe `CryptoWeekendGapStrategy`)

**Edge propose** : Le gap du lundi matin sur les crypto-proxies est souvent excessif. Continuation quand la premiere barre confirme.

**Parametres** : Min gap 2.0%, max gap 12%, stop 2.0%, target 2.5%, max 3 positions. LUNDI UNIQUEMENT. Tickers : COIN, MARA, MSTR, RIOT, BITF, CLSK, HUT.

**Resultat** : **REJETE**. Trop peu de lundis avec des gaps > 2% sur les crypto-proxies en 6 mois. Les gaps sont souvent deja fermes a l'ouverture.

---

### 8.2 Crude-Equity Lag Play (`crude_equity_lag.py`, classe `CrudeEquityLagStrategy`)

**Edge propose** : Le crude (USO) bouge avant les energy stocks. Retard de 15-45 min detectable a 10:00 ET.

**Parametres** : USO min move 0.5%, energy max at check 0.2%, SPY max move 1.5%, stop 0.7%, target 1.2%. Signal : USO. Trades : XOM, CVX, COP, EOG, SLB, MPC, PSX, VLO, OXY, DVN.

**Resultat** : Sharpe 1.85, PF 2.48, mais seulement **5 trades**

**Diagnostic** : L'edge est reel mais le filtre "energy stock n'a pas encore bouge" est trop restrictif. A retester sur 1+ an.

---

### 8.3 Semi Earnings Chain Reaction (`semi_earnings_chain.py`, classe `SemiEarningsChainStrategy`)

**Edge propose** : La supply chain semi est sequentielle. Quand un leader (NVDA, AMD) gap > 1%, les followers (SMCI, AMAT, MRVL) rattrapent.

**Parametres** : Leader min gap 1.0%, follower max gap 1.5%, leader fade threshold 60%, stop 0.8%, target 1.5%. Leaders : NVDA, AMD, AVGO, TSM, ASML. Followers : SMCI, AMAT, LRCX, KLAC, MRVL, MU, DELL.

**Resultat** : Sharpe 0.91, mais seulement **6 trades**

**Diagnostic** : Concept correct mais les earnings des leaders semi arrivent peu souvent (~1 gap > 1% par semaine). A retester sur 1+ an.

---

### 8.4 FDA Approval Drift (`fda_approval_drift.py`, classe `FDAApprovalDriftStrategy`)

**Edge propose** : Le drift post-evenement (gap > 5%) persiste en intraday car les analystes mettent des heures a updater.

**Parametres** : Min gap 5.0%, min vol ratio 2.5x, stop 1.2%, target 3.5%. Tickers pharma/biotech : 22 tickers + scan tout l'univers. Confirmation : premiere barre bullish/bearish.

**Resultat** : **REJETE**. Les gaps > 5% sont tres rares sur l'univers (quelques-uns en 6 mois). Pas assez de trades.

---

### 8.5 Yield Curve Banks (`yield_curve_banks.py`, classe `YieldCurveBankStrategy`)

**Edge propose** : Quand TLT baisse (steepening), les banques montent avec retard 30-60 min.

**Parametres** : TLT min drop -0.12%, SHY max move 0.15%, XLF max follow 0.35%, stop 0.6%, target 1.2%. Signal : TLT, SHY, IEF. Trades : 12 banques (JPM, BAC, WFC, C, GS, MS, USB, PNC, TFC, FITB, KEY, SCHW).

**Resultat** : **REJETE**. Le lag TLT -> banques n'est pas assez exploitable en intraday 5M. Les banques reagissent souvent en meme temps que TLT.

---

### 8.6 Multi-Sector Rotation (`multi_sector_rotation.py`, classe `MultiSectorRotationStrategy`)

**Edge propose** : Les flux institutionnels sectoriels prennent 2-4h. Long le meilleur stock du top secteur, short le pire du bottom secteur.

**Parametres** : Min sector return 0.3%, min spread 0.7%, stop 0.7%, target 1.2%. 10 secteurs (XLK, XLF, XLE, XLV, XLC, XLI, XLP, XLU, XLRE, XLB) avec 7-10 composants chacun.

**Resultat** : **REJETE**. La rotation sectorielle intraday n'a pas d'edge apres couts. Le spread leader/laggard se ferme trop vite.

**Analyse globale des 6 strategies Opus** : Les strategies sectorielles et thematiques echouent systematiquement en intraday 5M. Les edges sont trop petits pour couvrir les couts, ou les evenements sont trop rares pour generer assez de trades en 6 mois.

---

## 9. Walk-forward validation

### 9.1 Methodologie

- **In-Sample (IS)** : 60 jours de trading
- **Out-of-Sample (OOS)** : 30 jours de trading
- **Step** : 30 jours (2 fenetres sur 121 jours)
- **Critere de validation** : profitable sur >= 50% des fenetres OOS

### 9.2 Resultats (5 strategies walk-forward validees sur le backtest initial 186 tickers)

| Strategie | Hit Rate | Avg OOS Return | Avg OOS Sharpe | Avg OOS PF | Trades WF | Verdict |
|-----------|----------|----------------|----------------|------------|-----------|---------|
| ORB 5-Min Breakout | 100% (2/2) | +1.315% | 4.09 | 1.46 | 300 | VALIDATED |
| OpEx Gamma Pin | 100% (2/2) | +0.285% | 10.50 | 2.61 | 65 | VALIDATED |
| Earnings Drift | 100% (2/2) | +0.175% | 4.90 | 2.57 | 16 | VALIDATED |
| Day-of-Week Seasonal | 50% (1/2) | +0.030% | 2.83 | 3.41 | 30 | VALIDATED |
| ML Volume Cluster | 50% (1/2) | +0.030% | 0.47 | 1.33 | 34 | VALIDATED |

### 9.3 Detail par fenetre

**ORB 5-Min Breakout :**
- W1 (19 dec - 3 fev) : +1.51%, Sharpe 5.24, WR 53.3%, 150 trades
- W2 (4 fev - 18 mar) : +1.12%, Sharpe 2.94, WR 48.0%, 150 trades

**OpEx Gamma Pin :**
- W1 : +0.26%, Sharpe 8.26, WR 65.7%, 35 trades
- W2 : +0.31%, Sharpe 12.74, WR 73.3%, 30 trades

**Earnings Drift :**
- W1 : +0.08%, Sharpe 5.14, WR 44.4%, 9 trades
- W2 : +0.27%, Sharpe 4.66, WR 71.4%, 7 trades

**Day-of-Week Seasonal :**
- W1 : -0.07%, Sharpe -4.65, WR 43.8%, 16 trades (fenetre defavorable)
- W2 : +0.13%, Sharpe 10.30, WR 92.9%, 14 trades

**ML Volume Cluster :**
- W1 : +0.06%, Sharpe 1.49, WR 61.5%, 13 trades
- W2 : +0.00%, Sharpe -0.56, WR 47.6%, 21 trades

---

## 10. Re-backtest horaires stricts (9:35-15:55 ET)

Le 24 mars 2026, un re-backtest complet a ete effectue sur l'univers elargi (207 tickers) avec les horaires stricts du moteur (guard 9:35-15:55 ET). Ce re-backtest a **elimine 3 strategies** qui avaient passe la walk-forward initiale sur 186 tickers.

### Comparaison avant/apres

| Strategie | Avant (186 tickers) | Apres (207 tickers, horaires stricts) | Verdict |
|-----------|---------------------|--------------------------------------|---------|
| ORB 5-Min Breakout | Sharpe +3.47, +3.89% | Sharpe **-0.05**, +0.07%, commissions $14K | **RETIRE** |
| Earnings Drift | Sharpe +13.50, +1.38% | Sharpe negatif, **-11.14%**, overtrade small caps | **RETIRE** |
| ML Volume Cluster | Sharpe +1.13, +0.23% | Sharpe negatif, **-0.10%**, PF 0.87 | **RETIRE** |
| OpEx Gamma Pin | Sharpe +7.08, +0.87% | Sharpe **+10.41**, +0.43%, PF 4.51 | **Conserve et ameliore** |
| Day-of-Week Seasonal | Sharpe +1.85, +0.12% | Sharpe **+3.42** (ameliore), +0.11% | **Conserve et ameliore** |
| Overnight Gap Continuation | -- | Sharpe **+5.22**, +0.45% | **Nouveau, valide** |
| Late Day Mean Reversion | -- | Sharpe **+0.60**, +0.35% | **Nouveau, valide** |
| Crypto-Proxy Regime V2 | -- | Sharpe **+3.49** | **Nouveau, valide** |

**Lecon cle** : Les strategies qui scannent l'univers entier (ORB, Earnings) sont penalisees par les commissions sur les small caps. Les strategies selectives (OpEx sur 8 tickers, DoW sur ETFs) beneficient des horaires stricts.

---

## 11. Strategies actives en paper trading

### 11.1 Configuration actuelle (8 strategies)

**3 strategies daily/monthly :**

| # | Strategie | Sharpe | Allocation | Capital | Frequence |
|---|-----------|--------|-----------|---------|-----------|
| 1 | Momentum 25 ETFs | 0.88 | 5.5% | $5,500 | Mensuel |
| 2 | Pairs MU/AMAT | 0.94 | 5.9% | $5,900 | Daily |
| 3 | VRP SVXY/SPY/TLT | 0.75 | 4.7% | $4,700 | Mensuel |

**5 strategies intraday :**

| # | Strategie | Sharpe | Allocation | Capital | Frequence estimee |
|---|-----------|--------|-----------|---------|-------------------|
| 4 | OpEx Gamma Pin | 10.41 | **20.0%** (cap) | $20,000 | ~48 trades/6 mois (vendredis) |
| 5 | Overnight Gap Continuation | 5.22 | **20.0%** (cap) | $20,000 | ~32 trades/6 mois |
| 6 | Crypto-Proxy Regime V2 | 3.49 | **20.0%** (cap) | $20,000 | ~20-30 trades/6 mois |
| 7 | Day-of-Week Seasonal | 3.42 | **20.0%** (cap) | $20,000 | ~44 trades/6 mois |
| 8 | Late Day Mean Reversion | 0.60 | 3.8% | $3,800 | ~44 trades/6 mois |

**TOTAL** : 100% = $100,000

### 11.2 Methode d'allocation

**Risk-parity Sharpe-weighted** avec cap 20% par strategie. Redistribution iterative du surplus vers les strategies non-cappees.

---

## 12. Risk management

### 12.1 Guards et protections

| Protection | Parametre | Description |
|------------|-----------|-------------|
| Cap par strategie | 20% du capital total | Aucune strategie ne peut depasser $20,000 |
| Cap par position | 10% du capital total ($10,000) | Un seul trade ne peut depasser 10% |
| Max positions simultanees | 5 | Gere par le moteur BacktestEngine |
| Circuit-breaker DD | 5% journalier | Si le drawdown journalier depasse 5%, tous les ordres sont stoppes |
| Horaires de marche | `is_us_market_open()` | Verifie avant chaque ordre |
| Sortie forcee intraday | 15:55 ET | Toutes les positions intraday fermees |
| Guard anti-bypass | `_authorized_by` requis | AlpacaClient refuse les ordres sans autorisation pipeline |
| Feed IEX | Alpaca data source | Feed gratuit pour eviter les couts de donnees |

### 12.2 Protections dans le moteur de backtest

- **No-lookahead** : Signaux generes sur close[t], execution a open[t+1] via `.shift(1)` sur tous les indicateurs
- **Slippage** : 0.02% applique a l'entree et a la sortie
- **Commissions** : $0.005/share applique a chaque trade
- **Guard horaires** : Tout signal hors 9:35-15:55 ET est rejete par le moteur

---

## 13. Etat du compte Alpaca

### 13.1 Compte

| Metrique | Valeur |
|----------|--------|
| Type | PAPER |
| Equity | $100,000 |
| Cash | $100,000 |
| Positions ouvertes | 0 (au 25 mars 2026) |
| P&L total | $0.00 |

### 13.2 Etat par strategie

| Strategie | Position | Commentaire |
|-----------|----------|-------------|
| Momentum 25 ETFs | CASH | Crash filter actif (SPY < SMA 200) |
| Pairs MU/AMAT | FLAT | Pas de signal (|z-score| < 2.0) |
| VRP Rotation | LONG SPY | Regime normal (vol 20j = 13.7%, trend rising) |
| OpEx Gamma Pin | Pas de signal | Actif seulement dates OpEx/vendredis |
| Gap Continuation | Pas de signal | -- |
| Crypto-Proxy V2 | Pas de signal | -- |
| Day-of-Week Seasonal | Pas de signal | -- |
| Late Day MR | Pas de signal | -- |

### 13.3 Historique des runs

- **23 mars (3 runs)** : momentum=sell_all, pairs=hold, vrp=rebalance(SPY), 1 ordre execute
- **24 mars (intraday)** : 7 positions ORB ouvertes (LONG XLF, CVX, UNH, AAPL, PG, XLU, HON), erreurs short fractionnel sur USO/GOOG/META. Ces positions ORB ont ete ouvertes AVANT le re-backtest qui a retire l'ORB.

### 13.4 State file (`paper_trading_state.json`)

```json
{
  "position": null,
  "trade_log": [],
  "total_pnl": 0.0,
  "last_run": "2026-03-23T21:01:41.230810+00:00"
}
```

---

## 14. Statistiques globales

| Metrique | Valeur |
|----------|--------|
| Strategies JSON concues (plateforme principale) | 27 |
| Strategies intraday Batch 1 (originales) | 14 |
| Strategies intraday Batch 2 (nouvelles) | 12 |
| Strategies research agent (Batch 3) | 2 |
| Strategies PO-research | 5 |
| Strategies Opus sectorielles | 6 |
| **Total strategies testees** | **39 intraday + 27 JSON = 66** |
| Strategies walk-forward validees (initial) | 5 (ORB, OpEx, Earnings, DoW, ML Cluster) |
| Strategies survivantes apres re-backtest strict | 5 intraday + 3 daily = **8 actives** |
| Taux de survie intraday | 5/39 = **12.8%** |
| Taux de survie global | 8/66 = **12.1%** |
| Strategies avec 0 trades | 5 (Correlation Breakdown, Pattern Recognition, Initial Balance, Volume Climax, ETF NAV) |
| Strategies avec < 30 trades | 7 (FOMC/CPI, Multi-TF, Crypto-Proxy V1, Opening Drive...) |
| Pire strategie | Gap Fade (-66.03%, -$66,028) |
| Meilleur Sharpe | OpEx Gamma Pin (10.41 re-backtest strict) |
| Capital paper | $100,000 |
| Tests unitaires | ~70 (6 fichiers pytest) |
| Paires cointegrees identifiees | ~187 (tech + finance + europe + crypto) |
| Brokers integres | 2 (Alpaca + IG Markets) |
| Sprints de developpement | 5 + pairs + intraday V1/V2 |
| Lignes de code ajoutees | ~10,000+ |

---

## 15. Lecons apprises

### 15.1 Donnees et infrastructure

1. **Synthetique vs reel** : Mean-reversion echoue TOUJOURS sur GBM (martingale). Valider uniquement sur yfinance ou Alpaca.
2. **Nombre de trades minimum** : >= 20 trades par fenetre OOS, >= 30 trades au total pour significativite statistique.
3. **Crypto vs stocks** : Crypto genere 5-6x plus de trades (24/7), plus facile a valider statistiquement.
4. **Univers elargi invalide certaines strategies** : ORB et Earnings passent sur 186 tickers mais echouent sur 207 tickers car les small caps ajoutent du bruit.

### 15.2 Choix de strategies

5. **Les strategies simples (RSI, EMA, BB) ne fonctionnent pas en 5M** : l'edge est plus petit que les couts de transaction.
6. **Les edges structurels gagnent** : OpEx (gamma hedging), gap continuation (flux overnight), crypto decorrelation, anomalies calendaires.
7. **Moins de trades = mieux** : les strategies selectives (OpEx: 48 trades, DoW: 44) battent les strategies hyperactives (ORB: 615, BB+RSI: 615).
8. **Le mean reversion intraday est dangereux** : Gap Fade (-66%), VWAP Bounce (-7.6%), Power Hour (-23%), Momentum Exhaustion.
9. **Les pairs/arbitrage intraday echouent** : les couts mangent l'edge (ETF NAV, Relative Strength Pairs, Correlation Breakdown, Sector Rotation).
10. **Les strategies event-driven (FOMC, earnings, FDA) necessitent 1+ an de donnees** pour significativite statistique.

### 15.3 Execution et pipeline

11. **Ne JAMAIS bypasser le pipeline** : 3 scripts standalone ont failli investir 96% sur un seul trade. Le guard `_authorized_by` sur AlpacaClient empeche les bypass.
12. **Horaires** : Crons en heure Paris (15:35), pas UTC. Verifier `is_us_market_open()` avant tout ordre.
13. **Alpaca shorts fractionnels** : Rejetes. Utiliser des ordres entiers pour les shorts.
14. **Les commissions tuent les strategies actives** : ORB (-$14K commissions), Multi-TF V1 (-$41K commissions), Earnings (-$11K commissions).

### 15.4 Validation et robustesse

15. **Walk-forward obligatoire** : les Sharpe In-Sample sont trompeurs. Le ratio IS/OOS doit etre > 0.7.
16. **BB Squeeze TSLA suspect** : OOS Sharpe (8.45) > IS (6.63) = regime-dependant, pas robuste.
17. **Le re-backtest sur univers elargi est essentiel** : 3 strategies validees sur 186 tickers echouent sur 207 tickers.
18. **Taux de survie de ~13% est normal** : en recherche quantitative, la majorite des hypotheses echouent.

### 15.5 Architecture et decisions

19. **Diversification > concentration** : Marc veut 20 strategies a 3. 8 actives est un bon debut.
20. **Le PO drive le projet** : Toute decision strategique doit etre validee par le PO avant implementation.
21. **Circuit-breaker** : DD journalier > 5% = arret total. Jamais declenche pour l'instant.
22. **Risk-parity Sharpe-weighted** : les strategies avec meilleur Sharpe obtiennent plus de capital, cappee a 20%.

---

## 16. Prochaines etapes recommandees

### 16.1 Court terme (cette semaine)

- [ ] Monitorer les premieres executions automatiques du cron intraday
- [ ] Verifier que les positions se ferment bien a 15:55 ET
- [ ] Creer les taches planifiees Windows via `schtasks` (les .bat existent mais ne sont pas enregistres)
- [ ] Verifier le state file paper_portfolio_state.json vs paper_trading_state.json (incohérence possible)
- [ ] Nettoyer les positions ORB ouvertes le 24 mars (avant le retrait de la strategie)

### 16.2 Moyen terme (ce mois)

- [ ] Retester FOMC/CPI Drift V2, Multi-TF Trend V2, Crude-Equity Lag, Semi Earnings Chain sur 1+ an de donnees
- [ ] Grid search sur les 5 strategies actives (seuils ATR, volumes, timing)
- [ ] Walk-forward sur 4+ fenetres (quand on aura 1 an de donnees)
- [ ] Ajouter un dashboard web temps reel (equity curve, positions, P&L)
- [ ] Migrer le cron vers un VPS/cloud pour ne pas dependre du PC

### 16.3 Long terme

- [ ] Elargir l'univers (resoudre le rate limit Alpaca pour les daily stats)
- [ ] Objectif : 15-20 strategies actives diversifiees
- [ ] Passage en live quand les strategies sont validees sur 3+ mois de paper trading
- [ ] Ajouter le suivi alpha vs SPY buy & hold dans le dashboard
- [ ] Refactorer les helpers ATR dupliques dans utils/indicators.py
- [ ] Explorer les strategies overnight et multi-jour (holding > 1 jour)

---

## Annexe A -- Scan multi-actifs (plateforme principale)

### BB Squeeze 5M -- 22/24 actifs valides (top 10)

| Asset | Classe | Sharpe | Return % | Max DD % | WR % | PF | Trades |
|-------|--------|--------|----------|----------|------|----|--------|
| TSLA | stocks | 5.92 | 1.30 | 5.42 | 66.1 | 4.78 | 112 |
| SOL | crypto | 5.38 | 4.66 | 5.22 | 59.5 | 3.42 | 603 |
| NVDA | stocks | 5.24 | 0.88 | 5.59 | 64.1 | 4.15 | 103 |
| AVAX | crypto | 4.83 | 4.52 | 15.0 | 54.1 | 3.13 | 580 |
| LINK | crypto | 4.37 | 3.98 | 7.0 | 54.9 | 2.79 | 594 |
| META | stocks | 4.00 | 0.46 | 3.46 | 54.6 | 2.21 | 121 |
| ETH | crypto | 3.80 | 3.04 | 6.91 | 50.5 | 2.54 | 568 |
| AAPL | stocks | 3.62 | 0.40 | 4.10 | 57.1 | 2.56 | 105 |
| ASML | stocks | 3.61 | 0.39 | 6.84 | 51.4 | 2.10 | 111 |
| XRP | crypto | 3.58 | 2.94 | 17.5 | 48.9 | 2.28 | 558 |

### Momentum Burst 1M -- 8/24 valides (tous crypto)

| Asset | Sharpe | Return % | WR % | PF | Trades |
|-------|--------|----------|------|----|--------|
| AVAX | 4.03 | 2.06 | 58.3 | 6.38 | 206 |
| ETH | 3.79 | 1.86 | 62.3 | 6.39 | 215 |
| SOL | 3.58 | 2.06 | 57.9 | 6.03 | 216 |
| LINK | 3.58 | 1.77 | 59.7 | 5.73 | 196 |
| BTC | 3.22 | 0.92 | 55.6 | 3.85 | 196 |

### Pairs trading -- top paires cointegrees (5Y donnees reelles)

| Paire | Sharpe | WR % | Max DD % | Trades | Half-life |
|-------|--------|------|----------|--------|-----------|
| MU / AMAT | +1.15 | 71 | 4.8 | 34 | 12j |
| ADI / MPWR | +1.02 | 68 | 5.1 | 29 | 15j |
| MSFT / AVGO | +0.99 | 65 | 6.3 | 31 | 18j |
| GOOGL / LRCX | +0.89 | 63 | 7.2 | 27 | 14j |
| JPM / GS | +0.86 | 82 | 6.2 | 28 | -- |

---

## Annexe B -- Tests unitaires

### Suite pytest -- ~70 tests, 6 fichiers

| Fichier | Tests | Scope |
|---------|-------|-------|
| `test_backtest.py` | 13 | Moteur backtest, no-lookahead, reproductibilite, schema JSON |
| `test_sprint2.py` | 12 | FeatureStore (RSI, ADX, VWAP, BB), 3 strategies, caching |
| `test_sprint3.py` | 12 | Paper trading loop, RegimeDetector, StrategyRanker |
| `test_sprint4.py` | 17 | Trailing stop, expectancy, rolling Sharpe, 6 nouvelles strats, portfolio correlation |
| `test_sprint5.py` | 15 | Grid search IS/OOS, univers actifs, yfinance loader |
| `test_pairs.py` | 16 | Hedge ratio OLS, ADF, half-life, no-lookahead, dollar-neutral, PnL, PairDiscovery E2E |

### Tests critiques

- **No-lookahead bias** : modifie donnees futures, verifie signaux passes inchanges
- **Dollar-neutral** : notional A ~= notional B (tolerance 1%)
- **Reproductibilite** : meme seed = meme resultat (4 strategies parametrisees)
- **Walk-forward** : fenetres non-chevauchantes
- **Schema JSON** : toute strategie doit valider `strategy_schema/schema.json`

---

## Annexe C -- Resultats bruts du backtest final (strategy_summary.csv)

Univers : 207 tickers, mode curated, horaires stricts 9:35-15:55 ET.

| Strategie | Return % | Net PnL ($) | Trades | WR % | PF | Sharpe | Max DD % | Commission ($) |
|-----------|----------|-------------|--------|------|----|--------|----------|----------------|
| Overnight Gap Continuation | +0.45 | +447 | 32 | 53.1 | 1.61 | 2.82 | 0.38 | 95 |
| OpEx Gamma Pin | +0.43 | +430 | 48 | 72.9 | 4.51 | 10.41 | 0.02 | 4 |
| Late Day Mean Reversion | +0.35 | +345 | 44 | 52.3 | 1.34 | 0.60 | 0.71 | 107 |
| Crypto-Proxy Regime Switch | +0.19 | +191 | 11 | 63.6 | 1.77 | 3.03 | 0.10 | 31 |
| Day-of-Week Seasonal | +0.11 | +115 | 44 | 68.2 | 1.55 | 2.87 | 0.09 | 3 |
| ORB 5-Min Breakout | +0.07 | +65 | 615 | 55.1 | 2.19 | -0.05 | 3.20 | 14,374 |
| Correlation Breakdown | 0.00 | 0 | 0 | -- | -- | -- | 0.00 | 0 |
| Pattern Recognition | 0.00 | 0 | 0 | -- | -- | -- | 0.00 | 0 |
| Initial Balance Extension | 0.00 | 0 | 0 | -- | -- | -- | 0.00 | 0 |
| ETF NAV Premium/Discount | 0.00 | 0 | 0 | -- | -- | -- | 0.00 | 0 |
| ML Volume Cluster | -0.10 | -97 | 76 | 48.7 | 0.87 | -- | 1.36 | 6 |
| MOC Imbalance Anticipation | -0.11 | -105 | 79 | 43.0 | 0.75 | -- | 1.72 | 7 |
| Momentum Exhaustion | -0.18 | -180 | 323 | 46.1 | 1.20 | -- | 0.59 | 760 |
| Cross-Asset Lead-Lag | -0.21 | -207 | 59 | 39.0 | 0.71 | -- | 2.35 | 5 |
| Relative Strength Pairs | -0.37 | -373 | 120 | 32.5 | 0.93 | -- | 3.87 | 287 |
| Sector Rotation Momentum | -0.54 | -538 | 190 | 37.9 | 0.82 | -- | 3.06 | 114 |
| VWAP SD Extreme Reversal | -5.43 | -5,433 | 246 | 48.4 | 0.80 | -- | 4.59 | 4,287 |
| VWAP Bounce | -7.58 | -7,584 | 531 | 33.1 | 0.72 | -- | 7.63 | 6,079 |
| Tick Imbalance | -8.36 | -8,356 | 615 | 34.6 | 0.70 | -- | 13.12 | 6,686 |
| Earnings Drift | -11.14 | -11,139 | 179 | 37.4 | 1.07 | -- | 9.55 | 11,549 |
| Dark Pool Blocks | -15.67 | -15,673 | 615 | 31.7 | 0.76 | -- | 13.96 | 13,895 |
| Mean Reversion BB+RSI | -17.01 | -17,010 | 615 | 47.6 | 1.21 | -- | 10.09 | 19,212 |
| Opening Drive Extended | -18.36 | -18,364 | 156 | 42.9 | 1.04 | -- | 6.34 | 19,223 |
| Power Hour Momentum | -23.47 | -23,467 | 334 | 27.2 | 0.65 | -- | 17.42 | 21,396 |
| Volume Climax Reversal | -25.73 | -25,730 | 177 | 56.5 | 0.47 | -- | 18.21 | 20,935 |
| Multi-TF Trend Alignment | -40.12 | -40,115 | 363 | 38.3 | 1.03 | -- | 13.93 | 40,634 |
| Gap Fade | -66.03 | -66,028 | 602 | 22.3 | 0.15 | -- | 18.21 | 20,201 |

---

*Fin du rapport. 66 strategies testees, 8 actives, $100K en paper trading sur Alpaca.*
