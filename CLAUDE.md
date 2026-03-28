# CLAUDE.md — Trading Platform

## Stack
Python 3.11+ · pandas/numpy (calcul quantitatif) · asyncio (orchestration)
Alpaca API (paper trading US equities) · IBKR (live FX/EU/Futures) · **Binance FR (crypto margin+spot+earn)**
Anthropic API (Research Agent) · Railway (worker cloud 24/7) · TradingEngine dual-mode (live + paper simultanes)

## Commandes
```bash
python scripts/paper_portfolio.py --status       # Dashboard consolide
python scripts/paper_portfolio.py --dry-run      # Test daily sans ordres
python scripts/paper_portfolio.py --intraday     # Execution intraday
python scripts/paper_portfolio.py --intraday --dry-run  # Test intraday
python worker.py                                 # Worker Railway (24/7)
python -m pytest tests/ -v --tb=short            # Tests
```

## Strategies live Phase 1 — IBKR (6 sem 1, 8 sem 2)
```
LIVE — 1/8 Kelly (FX tier 1 + EU) :
  - EUR/USD Trend (Sharpe 4.62) — FX swing, IBKR
  - EUR/GBP Mean Reversion (Sharpe 3.65) — FX swing, IBKR
  - EUR/JPY Carry (Sharpe 2.50) — FX carry, IBKR
  - AUD/JPY Carry (Sharpe 1.58) — FX carry, IBKR
  - GBP/USD Trend (Sharpe ~2.0) — FX swing, IBKR
  - EU Gap Open (Sharpe 8.56) — EU intraday, IBKR (1/4 Kelly)

PAPER — futures (live jour 5 si OK) :
  - MCL Brent Lag Futures — NYMEX, $600 margin
  - MES Trend Following — CME, $1,400 margin

Volume cible : 32-42 trades/mois (sem 1), 52-70 (sem 2+)
NOTE : 3 borderline US (Late Day MR, Failed Rally, EOD Sell) = PAPER ONLY
```

## Strategies crypto — Binance France ($15K, margin + spot + earn)
```
8 strategies, portefeuille INDEPENDANT du $10K IBKR :
  1. BTC/ETH Dual Momentum (20%) — margin long/short, 2x levier
  2. Altcoin Relative Strength (15%) — margin, BTC-adjusted alpha, hebdo
  3. BTC Mean Reversion Intra (12%) — spot only, ADX<20 range
  4. Volatility Breakout (10%) — margin, compression→breakout
  5. BTC Dominance Rotation V2 (10%) — spot only, EMA7/21 dominance
  6. Borrow Rate Carry (13%) — Earn lending USDT/BTC/ETH, 3-12% APY
  7. Liquidation Momentum (10%) — margin, OI+funding read-only signals
  8. Weekend Gap Reversal (10%) — spot only, dip -3%/-8% weekend

PAS de futures perp (Binance France). Shorts via margin borrow.
3 wallets : Spot $6K + Margin $4K + Earn $3K + Cash $2K
```

## Strategies paper US (7 WF-validated + monitoring)
```
  - Day-of-Week Seasonal (Sharpe 3.42, WF PASS)
  - Correlation Regime Hedge (Sharpe 1.09, WF PASS)
  - VIX Expansion Short (Sharpe 3.61, WF PASS)
  - High-Beta Underperf Short (Sharpe 2.65, WF PASS)
  + 3 monitoring only (Momentum ETF, Pairs, VRP)
```

## Architecture
```
core/trading_engine.py        # TradingEngine dual-mode (live + paper simultanes)
core/signal_comparator.py     # Comparaison signaux live vs paper
core/risk_manager_live.py     # LiveRiskManager (12 checks, FX/futures margin)
core/broker/ibkr_bracket.py   # Brackets OCA (FX STP LMT, futures tick-aware)
core/broker/binance_broker.py # BinanceBroker V2 (margin + spot + earn, PAS de perp)
core/broker/factory.py        # SmartRouter V3 (equity/FX/futures/crypto_spot/crypto_margin)
core/kill_switch_live.py      # Kill switch live (4 triggers + Telegram)
core/leverage_manager.py      # 5 phases SOFT_LAUNCH -> PHASE_4
core/cross_portfolio_guard.py # Correlation cross-portefeuille IBKR-Binance
core/crypto/risk_manager_crypto.py  # CryptoRiskManager V2 (12 checks, margin health)
core/crypto/allocator_crypto.py     # Allocator 3 wallets, 8 strats, regime BULL/BEAR/CHOP
core/crypto/capital_manager.py      # Wallet manager (spot/margin/earn/cash transfers)
core/crypto/monitoring.py           # Alerter margin + reconciliation V2
strategies/crypto/                  # 8 strategies V2 Binance France
scripts/paper_portfolio.py    # Pipeline unifie (daily + intraday)
worker.py                     # Scheduler Railway 24/7
config/engine.yaml            # Config pipelines live + paper
config/limits_live.yaml       # Limites risk $10K IBKR
config/crypto_limits.yaml     # Limites risk $15K crypto (margin-aware)
config/crypto_allocation.yaml # Allocation 8 strats + 3 regimes + 3 wallets
config/crypto_kill_switch.yaml# Kill switch V2 (6 triggers, actions prioritisees)
intraday-backtesterV2/        # Framework backtest (137 strategies)
```

## Risk management
- **LiveRiskManager 12 checks** : position, strategy, long/short, gross, cash, sector, FX margin, FX notional, futures margin, combined margin, cash reserve, max positions
- **FX margin vs notional** : exposure basee sur margin ($750/paire), pas notional ($25K)
- **Futures margin** : MCL $600, MES $1,400, whitelist + max 2 contrats/symbole
- **Combined limits** : total margin < 80%, cash libre > 20%
- **Bracket orders** : FX = STP LMT (anti-slippage weekend, 5 pips buffer), futures = tick-aware
- **Pre-weekend check** : vendredi 16h ET, verification brackets FX actifs
- **Circuit-breaker** : daily -1.5% + hourly -1% + weekly -3%
- **Kill switch** : 4 triggers (auto drawdown, Telegram /kill, TWS, brackets broker-side)
- **Deleveraging progressif** : 30% a -1%, 50% a -1.5%, 100% a -2%
- **Sizing SOFT_LAUNCH** : 1/8 Kelly tier1, 1/16 Kelly borderline (max $50/trade)
- **Gate M1** : 20 trades, 3 semaines, criteres primaire/secondaire/abort
- **Signal sync** : signal unique route vers live + paper, comparaison divergences
- **Guard _authorized_by** : tout ordre doit passer par le pipeline
- **Lock idempotence** : anti-double execution dans le worker

## Variables env critiques
`ALPACA_API_KEY` `ALPACA_SECRET_KEY` `PAPER_TRADING=true`
`IBKR_PORT` `IBKR_PAPER` `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID`
`BINANCE_API_KEY` `BINANCE_API_SECRET` `BINANCE_TESTNET=true`

## Regles critiques (ne jamais violer)
- **No lookahead bias** : guard 9:35-15:55 ET dans le moteur de backtest
- **Couts reels** : $0.005/share + 0.02% slippage dans TOUS les backtests
- **Walk-forward obligatoire** : >= 50% fenetres OOS profitables (60% pour les V2)
- **Paper d'abord** : PAPER_TRADING=true obligatoire, guard dans AlpacaClient
- **Pipeline obligatoire** : scripts standalone DESACTIVES (.DISABLED)
- **Shorts en qty entiere** : pas de notional pour les SELL
