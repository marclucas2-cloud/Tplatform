# CLAUDE.md — Trading Platform

## Stack
Python 3.11+ · pandas/numpy (calcul quantitatif) · asyncio (orchestration)
Alpaca API (paper trading US equities) · Anthropic API (Research Agent)
Railway (worker cloud 24/7)

## Commandes
```bash
python scripts/paper_portfolio.py --status       # Dashboard consolide
python scripts/paper_portfolio.py --dry-run      # Test daily sans ordres
python scripts/paper_portfolio.py --intraday     # Execution intraday
python scripts/paper_portfolio.py --intraday --dry-run  # Test intraday
python worker.py                                 # Worker Railway (24/7)
python -m pytest tests/ -v --tb=short            # Tests
```

## Strategies actives (12)
```
Daily/Monthly :
  - Momentum 25 ETFs (mensuel, ROC 3m, crash filter SMA200)
  - Pairs MU/AMAT (daily, z-score cointegre)
  - VRP SVXY/SPY/TLT (mensuel, regime de volatilite)

Intraday (9 strategies, cron toutes les 5 min 15:35-22:00 Paris) :
  - OpEx Gamma Pin (Sharpe 10.41) — vendredis/OpEx, mean reversion round numbers
  - Overnight Gap Continuation (Sharpe 5.22) — gaps > 1.1% + volume confirmation
  - Crypto-Proxy Regime V2 (Sharpe 3.49) — decorrelation COIN vs MARA/MSTR
  - Day-of-Week Seasonal (Sharpe 3.42) — Monday effect, vendredi bullish
  - VWAP Micro-Deviation (Sharpe 3.08) — rolling VWAP 20 barres, z-score reversion
  - ORB 5-Min V2 (Sharpe 2.28) — breakout top stocks in play, gap > 3%
  - Mean Reversion V2 (Sharpe 1.44) — BB 3.0 std + RSI 12/88 extreme
  - Triple EMA Pullback (Sharpe 1.06) — EMA 8/13/21 aligned + pullback re-entry
  - Late Day Mean Reversion (Sharpe 0.60) — move > 3% + RSI extreme + volume sec
```

## Architecture
```
scripts/paper_portfolio.py    # Pipeline unifie (daily + intraday)
worker.py                     # Scheduler Railway 24/7
core/alpaca_client/client.py  # Client Alpaca (bracket orders, guard _authorized_by)
intraday-backtesterV2/        # Framework backtest (83 strategies, 35 testees mission nuit)
  strategies/                 # Toutes les strategies Python
  backtest_engine.py          # Moteur evenementiel (guard 9:35-15:55 ET)
  walk_forward.py             # Validation walk-forward automatisee
```

## Risk management
- **Cap 20%** par strategie, **10%** par position
- **Circuit-breaker** : DD > 5% journalier = stop tous les ordres
- **Bracket orders** : SL/TP envoyes a Alpaca (broker-side, survivent aux crashs)
- **Fermeture forcee 15:55 ET** + annulation ordres pendants
- **Max 10 positions** simultanees en live
- **Exposition nette** : max 40% long, 20% short
- **Guard paper/live** : abort si PAPER_TRADING != true
- **Guard _authorized_by** : tout ordre doit passer par le pipeline
- **Jours feries NYSE** : calendrier 2026 dans is_us_market_open()
- **Lock idempotence** : anti-double execution dans le worker

## Variables env critiques
`ALPACA_API_KEY` `ALPACA_SECRET_KEY` `PAPER_TRADING=true`

## Regles critiques (ne jamais violer)
- **No lookahead bias** : guard 9:35-15:55 ET dans le moteur de backtest
- **Couts reels** : $0.005/share + 0.02% slippage dans TOUS les backtests
- **Walk-forward obligatoire** : >= 50% fenetres OOS profitables (60% pour les V2)
- **Paper d'abord** : PAPER_TRADING=true obligatoire, guard dans AlpacaClient
- **Pipeline obligatoire** : scripts standalone DESACTIVES (.DISABLED)
- **Shorts en qty entiere** : pas de notional pour les SELL
