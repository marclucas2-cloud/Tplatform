# Alpha Ideas — Research Pipeline

## Haute Conviction (edges structurels documentes)

### 1. Cross-Asset Momentum (Moskowitz et al. 2012)
- **Edge** : momentum time-series sur 5 classes d'actifs simultanement
- **Avantage** : donnees et infra multi-asset deja en place
- **Correlation attendue** : faible (diversification cross-asset)
- **Instruments** : SPY, TLT, GLD, EURUSD, BTC (1 par classe)
- **Frequence** : weekly rebalance
- **Litterature** : AQR, Man Group, Winton
- **Status** : TODO — Gate 1

### 2. FX Carry Crash Protection (Brunnermeier et al. 2008)
- **Edge** : les carry crashes sont previsibles (funding liquidity)
- **Avantage** : hedge naturel du FX carry existant
- **Mecanisme** : short carry quand vol des devises de financement explose
- **Correlation** : negative avec FX Carry VS (c'est le but)
- **Instruments** : memes paires FX, direction opposee conditionnelle
- **Status** : TODO — Gate 1

### 3. Crypto On-Chain Metrics
- **Edge** : metriques on-chain (exchange flows, MVRV) precedent les prix de 1-3 jours
- **Avantage** : edge accessible aux retails, donnees gratuites (Glassnode, CryptoQuant)
- **Instruments** : BTC, ETH via Binance
- **Frequence** : daily
- **Status** : TODO — Gate 1

### 4. Volatility Risk Premium Harvest (Carr & Wu 2009)
- **Edge** : vol implicite surestime systematiquement vol realisee
- **Avantage** : VIX Expansion Short deja valide (Sharpe 5.67 OOS)
- **Extension** : VRP sur FX vol, crypto vol
- **Instruments** : VIX via Alpaca, FX vol via IBKR
- **Status** : TODO — Gate 1

### 5. Intraday Seasonality Refinement
- **Edge** : effets intraday (open, close, lunch) plus stables que daily
- **Avantage** : DoW Seasonal deja valide (Sharpe 2.21 OOS)
- **Extension** : hour-of-day effects, overlap EU/US
- **Data** : candles 1H/4H disponibles
- **Status** : TODO — Gate 1

## Moyenne Conviction (a tester)

### 6. Earnings Sentiment (NLP)
- **Edge** : ton des earnings calls predit le drift post-annonce
- **Complexite** : necessite NLP pipeline
- **Instruments** : US equities via Alpaca
- **Status** : TODO — necesssite budget API

### 7. ETF Flow Momentum
- **Edge** : flux ETF (creation/redemption) predisent les prix
- **Data** : gratuite (ETF.com, ICI)
- **Instruments** : SPY, QQQ, TLT, GLD
- **Status** : TODO — Gate 1

### 8. Crypto Funding-Spot Divergence
- **Edge** : funding rate deja lu pour Liquidation Momentum
- **Extension** : divergence funding/spot momentum = signal contrarian
- **Instruments** : BTC, ETH via Binance
- **Status** : TODO — Gate 1

## Tracking

Voir `data/research/pipeline_status.json` pour le suivi gate par gate.

## Regles

1. Chaque idee passe par le Research Pipeline (6 gates)
2. Quick backtest Sharpe < 0.5 = KILL immediat
3. WF : OOS/IS > 0.5, >= 50% fenetres profitables, >= 30 trades
4. Correlation > 0.6 avec strat existante = apport marginal faible
5. Break-even slippage < 3x = fragile
