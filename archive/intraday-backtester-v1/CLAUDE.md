# Intraday Backtester — CLAUDE.md

## Projet
Framework de backtest de stratégies intraday sur actions US, connecté à Alpaca (paper trading).

## Setup requis
```bash
pip install alpaca-py pandas numpy matplotlib plotly ta scipy
```

## Variables d'environnement
```bash
export ALPACA_API_KEY="your_key"
export ALPACA_SECRET_KEY="your_secret"
# Paper trading endpoint (pas live)
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"
```

## Architecture
```
intraday-backtester/
├── CLAUDE.md
├── config.py              # Config globale (capital, frais, tickers)
├── data_fetcher.py         # Connexion Alpaca + cache local
├── backtest_engine.py      # Moteur de backtest générique
├── strategies/
│   ├── __init__.py
│   ├── orb_5min.py         # Opening Range Breakout 5-min
│   ├── vwap_bounce.py      # VWAP Bounce + RSI
│   ├── gap_fade.py         # Gap Fade contrarian
│   ├── correlation_breakdown.py  # Pairs trading décorrélation
│   ├── power_hour.py       # Power Hour Momentum 15h-16h
│   └── mean_reversion.py   # Bollinger + RSI mean reversion
├── utils/
│   ├── __init__.py
│   ├── indicators.py       # Calculs techniques (VWAP, ORB range, etc.)
│   ├── metrics.py          # Win rate, Sharpe, drawdown, profit factor
│   └── plotting.py         # Visualisations plotly
├── run_backtest.py          # Point d'entrée principal
├── run_meta_analysis.py     # Comparaison multi-stratégies + régimes
└── output/                  # Résultats CSV + HTML charts
```

## Conventions
- Chaque stratégie hérite de `BaseStrategy` dans `backtest_engine.py`
- Les signaux retournent un dict : `{"action": "LONG"/"SHORT"/None, "entry": price, "stop": price, "target": price}`
- Les frais sont inclus : $0.005/share + 0.02% slippage
- Capital : 100 000 $ — max 5% par position
- Toutes les positions fermées avant 15:59 ET
- Timezone : US/Eastern pour tous les calculs

## Tickers par défaut
Mega-cap momentum : NVDA, AAPL, AMZN, META, TSLA, AMD, MSFT, GOOGL
Paires corrélées : NVDA/AMD, AAPL/MSFT, JPM/BAC, XOM/CVX, GOOGL/META
ETFs benchmark : SPY, QQQ

## Métriques obligatoires par stratégie
- Total return (%) et annualisé
- Win rate (%)
- Profit factor
- Sharpe ratio (annualisé)
- Max drawdown (%)
- Nombre de trades
- Avg winner vs avg loser
- Meilleur/pire jour
- Performance par jour de semaine
- Equity curve vs SPY buy&hold
