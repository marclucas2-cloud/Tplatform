# Intraday Backtester — CLAUDE.md

## Projet
Framework de backtest de stratégies intraday sur l'UNIVERS COMPLET des actions US
disponibles sur Alpaca (paper trading). Pas 19 tickers — l'univers entier.

## Setup
```bash
pip install alpaca-py pandas numpy matplotlib plotly ta scipy scikit-learn pyarrow
export ALPACA_API_KEY="your_key"
export ALPACA_SECRET_KEY="your_secret"
```

## Architecture univers (3 couches)
```
COUCHE 1 — UNIVERS COMPLET (~3000-5000 tickers)
  Source : Alpaca asset listing
  Filtre : US equity, actif, tradable, exchanges principaux
  Cache : universe_full.json (refresh hebdomadaire)

COUCHE 2 — UNIVERS ÉLIGIBLE (~500-1500 tickers)
  Filtres daily : volume > 500K, prix $5-$2000, ATR > 1%
  Cache : universe_eligible.json (refresh quotidien)

COUCHE 3 — STOCKS IN PLAY (~10-50 tickers/jour)
  Scanner dynamique : gap > 2%, volume > 2x, ATR > 1.5x
  Recalculé chaque jour du backtest
```

Tickers PERMANENTS (jamais filtrés) :
- Benchmarks : SPY, QQQ, IWM, DIA
- Cross-asset : TLT, GLD, USO
- Crypto-proxies : COIN, MARA, MSTR
- Sector ETFs : XLK, XLF, XLE, XLV, XLI, XLP, XLU, XLC, XLRE

## Usage
```bash
# Univers complet éligible (recommandé)
python run_backtest.py --universe eligible --strategy all --days 180

# Quick test
python run_backtest.py --universe minimal --strategy orb --days 30

# Top 200 par volume
python run_backtest.py --universe curated --strategy all --days 365

# Scanner seulement (pas de backtest)
python run_backtest.py --universe eligible --scan-only

# Variable d'environnement alternative
UNIVERSE_MODE=eligible python run_backtest.py --strategy all
```

## Structure
```
intraday-backtester/
├── CLAUDE.md
├── config.py              # Config globale (capital, frais, univers)
├── universe.py            # Gestionnaire d'univers 3 couches + scanner
├── data_fetcher.py        # Fetch Alpaca multi-symbol parallélisé + cache
├── backtest_engine.py     # Moteur événementiel générique
├── strategies/
│   ├── orb_5min.py         # ORB 5-min breakout ★ Sharpe +2.16
│   ├── vwap_bounce.py
│   ├── gap_fade.py
│   ├── correlation_breakdown.py
│   ├── power_hour.py
│   ├── mean_reversion.py
│   ├── fomc_cpi_drift.py
│   ├── opex_gamma_pin.py   # OpEx Gamma Pin ★ Sharpe +7.03
│   ├── tick_imbalance.py
│   ├── dark_pool_blocks.py
│   ├── ml_volume_cluster.py # ML Cluster ★ Sharpe +1.39
│   ├── cross_asset_lead_lag.py
│   ├── pattern_recognition.py
│   └── earnings_drift.py
├── utils/
│   ├── indicators.py       # VWAP, RSI, BB, ADX, ORB range, z-score
│   ├── metrics.py          # Win rate, Sharpe, drawdown, profit factor
│   └── plotting.py         # Plotly equity curves, comparisons
├── run_backtest.py          # Orchestrateur principal
├── run_meta_analysis.py     # Régime detection + allocation dynamique
├── data_cache/              # Cache Parquet par ticker (~500MB-5GB)
└── output/                  # CSV trades + HTML charts
```

## Conventions
- Chaque stratégie hérite de BaseStrategy
- Signaux : {"action": "LONG"/"SHORT", "entry": price, "stop": price, "target": price}
- Coûts : $0.005/share + 0.02% slippage (TOUJOURS inclus)
- Capital : $100K — max 5% par position, max 5 simultanées
- Timing : entrée au plus tôt 9:35 ET, sortie forcée 15:55 ET
- Timezone : US/Eastern pour TOUS les calculs
- Les stratégies reçoivent data: dict[str, DataFrame] — elles scannent
  l'univers ENTIER et tradent les meilleures opportunités

## Sector & Sympathy Maps (dans universe.py)
- SECTOR_MAP : ETF → top 10 composants (XLK, XLF, XLE, XLV, XLC, XLI, XLP)
- SYMPATHY_MAP : leader → followers (NVDA→AMD/MRVL, COIN→MARA/MSTR, TSLA→RIVN/LCID...)

## Résultats backtest (6 mois, 19 tickers, 5M)
WINNERS :
- OpEx Gamma Pin : Sharpe +7.03, WR 59%, PF 2.08, 110 trades
- ORB 5-Min : Sharpe +2.16, Return +3.44%, 510 trades, WR 49%
- ML Volume Cluster : Sharpe +1.39, WR 56%, PF 1.22, 129 trades

TODO : re-run sur univers complet pour voir l'impact du scanning sur plus de tickers.
