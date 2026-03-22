# CLAUDE.md — Trading Platform

## Stack
Python 3.11+ · pandas/numpy (calcul quantitatif) · asyncio (orchestration)
IG Markets API REST + Lightstreamer · Anthropic API (Research Agent)
SQLite (dev) / PostgreSQL (prod)

## Commandes
```bash
python run.py                          # Lancer l'orchestrator
python run.py --mode backtest          # Backtest uniquement
python run.py --mode paper             # Paper trading IG démo
python -m pytest tests/               # Tests
python -m pytest tests/ -v --tb=short # Tests verbose
```

## Architecture
```
orchestrator/main.py      # Orchestrator central — bus événements asyncio.Queue
agents/
  base_agent.py           # Classe de base — interface commune
  research/               # LLM → génère JSON stratégie
  backtest/               # Déclenche le moteur de backtest
  validation/             # Filtres statistiques stricts (walk-forward)
  portfolio/              # Allocation de capital (Kelly / risk parity)
  execution/              # Ordres IG Markets (paper + live)
  monitoring/             # Métriques, alertes, circuit-breakers
core/
  strategy_schema/        # JSON Schema source de vérité
  data/                   # Loader OHLCV + no-lookahead guard
  backtest/engine.py      # Moteur pur — ZÉRO LLM, déterministe
  ig_client/              # Client API IG (auth, prix, ordres)
  logging/                # Audit trail structuré + reproductibilité
strategies/               # JSON des stratégies (versionnées en git)
```

## Règles critiques (ne jamais violer)
- **No lookahead bias** : indicateurs calculés avec `.shift(1)` — signal sur close[t], ordre à open[t+1]
- **Séparation IA / calcul** : le LLM génère UNIQUEMENT du JSON de paramètres, jamais de calculs
- **Coûts réels** : chaque backtest inclut spread + slippage du `cost_model` dans le JSON stratégie
- **Validation obligatoire** : toute stratégie passe walk-forward avant d'accéder à l'Execution Agent
- **Paper trading d'abord** : `PAPER_TRADING=true` dans .env jusqu'à validation explicite

## Format JSON stratégie
Voir `core/strategy_schema/schema.json` — toute stratégie doit valider ce schéma.
Chemin : `strategies/<nom>.json`

## Variables env critiques
`IG_API_KEY` `IG_USERNAME` `IG_PASSWORD` `IG_ACC_TYPE` `IG_BASE_URL`
`ANTHROPIC_API_KEY` `PAPER_TRADING` `MAX_RISK_PER_TRADE` `MAX_DAILY_DRAWDOWN`

## Pièges connus
- **IG auth** : X-SECURITY-TOKEN + CST headers valables 6h — à renouveler
- **IG Streaming** : prix temps réel via Lightstreamer (pas REST) — client séparé requis
- **Lookahead** : NE PAS utiliser `df['rsi']` pour générer signal sur la même bougie
- **Paramètres PG** : `$1, $2...` si migration vers PostgreSQL
