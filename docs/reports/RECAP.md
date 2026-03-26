# Trading Platform — Récapitulatif complet

> Date : 23 mars 2026 · Auteur : Marc
> Projet : `C:\Users\barqu\trading-platform`

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture](#2-architecture)
3. [Pipeline multi-agents](#3-pipeline-multi-agents)
4. [Core — Modules quantitatifs](#4-core--modules-quantitatifs)
5. [Stratégies développées](#5-stratégies-développées)
6. [Stratégie validée : rsi_iwm_1d_v1](#6-stratégie-validée--rsi_iwm_1d_v1)
7. [Pairs Trading](#7-pairs-trading)
8. [Optimisation et scan d'actifs](#8-optimisation-et-scan-dactifs)
9. [Dashboard Streamlit](#9-dashboard-streamlit)
10. [Tests](#10-tests)
11. [Historique des commits (sprints)](#11-historique-des-commits-sprints)
12. [Commandes CLI de référence](#12-commandes-cli-de-référence)
13. [Stack technique](#13-stack-technique)

---

## 1. Vue d'ensemble

**Trading Platform** est une plateforme algorithmique modulaire construite en Python pur (pas de dépendance lourde type `statsmodels` ou `vectorbt`). Elle couvre l'intégralité du cycle de vie d'une stratégie :

```
Idée → Backtest IS → Walk-Forward OOS → Monte Carlo → Portfolio → Exécution → Monitoring
```

Deux paradigmes de stratégie sont supportés :
- **Directionnel** : RSI, Bollinger Bands, Momentum, ORB, VWAP — une position long/short sur un seul actif
- **Pairs trading** : deux actifs coïntégrés, dollar-neutral, spread z-score

La plateforme fonctionne en mode **simulation locale** (données yfinance) ou peut se connecter à **IG Markets** pour l'exécution réelle.

---

## 2. Architecture

```
trading-platform/
│
├── agents/                     # 6 agents asynchrones (asyncio)
│   ├── base_agent.py           # Classe abstraite AgentBase + AgentMessage
│   ├── research/agent.py       # Génère une stratégie JSON via Claude API ou mode fichier
│   ├── backtest/agent.py       # Lance BacktestEngine sur la stratégie reçue
│   ├── validation/agent.py     # Walk-forward + Monte Carlo + validation
│   ├── portfolio/agent.py      # Allocation de capital, corrélation inter-stratégies
│   ├── execution/agent.py      # Exécution via IG Markets (paper ou live)
│   └── monitoring/agent.py     # Logs, alertes, métriques en temps réel
│
├── core/
│   ├── backtest/
│   │   ├── engine.py           # BacktestEngine : simulation bar-by-bar, SL/TP/trailing
│   │   └── pairs_engine.py     # PairsBacktestEngine : dollar-neutral, hedge ratio OLS
│   ├── data/
│   │   ├── loader.py           # OHLCVLoader : yfinance + données synthétiques GBM
│   │   ├── pairs.py            # PairDiscovery : ADF, coïntégration, SECTOR_MAP
│   │   └── universe.py         # Univers d'actifs par classe
│   ├── features/store.py       # FeatureStore : RSI, BB, MACD, ATR, VWAP, ADX
│   ├── optimization/
│   │   └── grid_search.py      # GridSearch IS/OOS sur paramètres de stratégie
│   ├── ranking/ranker.py       # StrategyRanker : score composite (Sharpe, DD, WR, PF)
│   ├── regime/detector.py      # RegimeDetector : trending / ranging / volatile
│   ├── portfolio/correlation.py # Matrice de corrélation inter-stratégies
│   ├── paper_trading/loop.py   # Boucle paper trading temps réel
│   ├── ig_client/client.py     # Client IG Markets REST + streaming
│   ├── logging/audit.py        # Audit trail structuré
│   └── strategy_schema/        # JSON Schema validation des stratégies
│       ├── schema.json
│       └── validator.py
│
├── orchestrator/main.py        # Bus central asyncio + table de routage
│
├── scripts/
│   ├── run_research.py         # Lance le pipeline Research→Backtest→Validation
│   ├── batch_backtest.py       # Backtest en lot sur plusieurs stratégies/actifs
│   ├── optimize.py             # Grid search + walk-forward sur une stratégie
│   ├── asset_scan.py           # Scan multi-actifs pour trouver les meilleures configs
│   └── pairs_backtest.py       # Scan pairs intra-secteur + backtest coïntégrés
│
├── strategies/                 # JSONs des stratégies (spec + paramètres)
├── results/                    # CSVs de scan (rsi, bb, momentum)
├── dashboard.py                # Interface Streamlit Pro
├── requirements.txt
└── tests/                      # Suite pytest (test_sprint2 à test_sprint5, test_pairs)
```

---

## 3. Pipeline multi-agents

L'orchestrateur central (`orchestrator/main.py`) démarre 6 agents et route les messages via un bus `asyncio.Queue`.

### Table de routage

| Message              | Destinataire(s)              |
|----------------------|------------------------------|
| `RESEARCH_REQUEST`   | `research`                   |
| `STRATEGY_READY`     | `backtest`                   |
| `BACKTEST_COMPLETE`  | `validation`                 |
| `VALIDATION_PASSED`  | `portfolio` + `monitoring`   |
| `VALIDATION_FAILED`  | `monitoring`                 |
| `ALLOCATION_READY`   | `execution`                  |
| `EXECUTION_REPORT`   | `monitoring` + `portfolio`   |
| `PRICE_SIGNAL`       | `execution`                  |
| `ALERT`              | `monitoring`                 |
| `RESEARCH_ERROR`     | `monitoring`                 |

### Flux nominal

```
Orchestrator.send("RESEARCH_REQUEST", {asset, timeframe, style})
    → ResearchAgent  : appelle Claude API → génère strategy.json
    → BacktestAgent  : BacktestEngine bar-by-bar + métriques IS
    → ValidationAgent: walk_forward(4 fenêtres, OOS=30%) + Monte Carlo (1000 sims)
    → PortfolioAgent : allocation Kelly / corrélation
    → ExecutionAgent : ordre IG Markets (paper ou live)
    → MonitoringAgent: logs, alertes Telegram
```

### Lancer le Research Agent

```bash
# Avec clé API Claude
export ANTHROPIC_API_KEY=sk-...
python scripts/run_research.py --asset IWM --timeframe 1D --style mean_reversion

# Mode fichier (sans clé API — charge un JSON existant)
# Mettre RESEARCH_MODE=file dans .env
```

---

## 4. Core — Modules quantitatifs

### BacktestEngine (`core/backtest/engine.py`)

Simulation bar-by-bar. Pour chaque bougie :
- Calcul du signal via le registre de stratégies (`@register_strategy`)
- Application SL / TP / trailing stop
- Mark-to-market quotidien (equity curve avec P&L non réalisé)
- Costs : spread + slippage + commission

**Métriques produites** : Sharpe ratio, Sortino, max drawdown, win rate, profit factor, expectancy, nombre de trades.

### OHLCVLoader (`core/data/loader.py`)

```python
# Données réelles
data = OHLCVLoader.from_yfinance("IWM", "1D", period="5y")

# Données synthétiques GBM (tests unitaires)
data = OHLCVLoader.generate_synthetic(asset="TEST", timeframe="1D", n_bars=3000)

# Walk-forward windows
windows = data.walk_forward_windows(n_windows=4, oos_pct=0.30)
```

> Note importante : les stratégies de mean-reversion **échouent systématiquement** sur données GBM (processus de martingale, aucun edge statistique). Toujours valider sur données réelles yfinance.

### FeatureStore (`core/features/store.py`)

Calcul vectorisé (pandas) de tous les indicateurs :
- RSI(n), Bollinger Bands(n, k), MACD, ATR, VWAP, ADX, EMA, SMA

### RegimeDetector (`core/regime/detector.py`)

Classifie le marché en : `trending_up`, `trending_down`, `ranging`, `volatile`
Basé sur ATR relatif + pente EMA.

### StrategyRanker (`core/ranking/ranker.py`)

Score composite pondéré pour classer les stratégies :
```
score = w_sharpe * sharpe + w_dd * (1 - dd/max_dd) + w_wr * win_rate + w_pf * profit_factor
```

---

## 5. Stratégies développées

### Stratégies directionnelles (fichiers JSON dans `strategies/`)

| Fichier | Asset | TF | Style | Statut |
|---|---|---|---|---|
| `rsi_mean_reversion.json` | EUR/USD | 1H | RSI MR | Stratégie de base (seed) |
| `rsi_mean_reversion_opt_v1.json` | SPY | 1D | RSI MR optimisé | Grid search IS/OOS |
| `rsi_mean_reversion_ftse_opt_v1.json` | FTSE 100 | 1D | RSI MR | Optimisé sur FTSE |
| `rsi_mean_reversion_russell_opt_v1.json` | IWM | 1D | RSI MR | Précurseur rsi_iwm_1d_v1 |
| `rsi_filtered_5m_v1.json` | EUR/USD | 5M | RSI + ADX filter | Intraday filtré |
| `rsi_filtered_v2.json` | SPY | 5M | RSI + ADX v2 | Intraday filtré |
| `rsi_filtered_spx_1h_v1.json` | SPX | 1H | RSI + ADX | **REJETÉ** — SPX trop trending 2024-2026 |
| `rsi_qqq_1h_v1.json` | QQQ | 1H | RSI MR | **REJETÉ** — QQQ bull run, WR 40% |
| **`rsi_iwm_1d_v1.json`** | **IWM** | **1D** | **RSI MR** | **VALIDÉ — Sharpe +1.82 IS, +1.03 OOS** |
| `bb_squeeze_5m_v1.json` | EUR/USD | 5M | BB Squeeze | Sprint 2 |
| `bb_squeeze_5m_opt_v1.json` | EUR/USD | 5M | BB Squeeze opt | Grid search |
| `bb_squeeze_5m_opt_opt_v1.json` | EUR/USD | 5M | BB Squeeze v2 | Double optimisation |
| `bb_squeeze_tsla_opt_v1.json` | TSLA | 5M | BB Squeeze | Adapté TSLA |
| `momentum_burst_1m_v1.json` | NVDA | 1M | Momentum burst | Sprint 2 |
| `momentum_burst_1m_opt_v1.json` | NVDA | 1M | Momentum opt | Optimisé |
| `opening_range_breakout.json` | SPY | 5M | ORB | Breakout ouverture US |
| `orb_5m_v1.json` | SPY | 5M | ORB v1 | Optimisé |
| `vwap_mean_reversion.json` | QQQ | 5M | VWAP MR | Sprint 3 |
| `vwap_mr_1m_v1.json` | QQQ | 1M | VWAP MR 1M | Intraday rapide |
| `seasonality_5m_v1.json` | SPY | 5M | Seasonality | Sessions US/EU |

### Stratégies rejetées — leçons apprises

- **rsi_qqq_1h_v1** : QQQ 2024-2026 était en tendance haussière forte → mean-reversion ne fonctionne pas en trend. WR 39.9%, Sharpe -0.899.
- **rsi_filtered_spx_1h_v1** : Même problème + filtre ADX trop strict → très peu de trades.
- **momentum_burst_gld_1h_v1** : IS Sharpe +2.22 mais OOS -3.4 sur 4 fenêtres WF. Cause : seulement 5-8 trades par fenêtre OOS — trop peu pour être statistiquement significatif.

---

## 6. Stratégie validée : rsi_iwm_1d_v1

### Logique

IWM (Russell 2000 ETF) est structurellement plus mean-reverting que les large caps :
- Liquidité réduite → overshoots plus fréquents
- Sensibilité accrue aux cycles économiques → mean-reversion plus marquée

**RSI(10)** plutôt que RSI(14) : plus réactif, capture les micro-extrêmes intra-semaine.
**Seuils asymétriques (30/65)** : le marché monte plus facilement qu'il ne baisse → entrée short plus sélective.

### Paramètres

```json
{
  "rsi_period": 10,
  "oversold": 30,
  "overbought": 65,
  "stop_loss_pct": 1.0,
  "take_profit_pct": 3.5,
  "trailing_stop_pct": 0.6,
  "max_position_pct": 0.05
}
```

### Règles d'entrée / sortie

| Direction | Entrée | Sortie |
|---|---|---|
| Long | RSI(10) croise au-dessus de 30 | SL -1% OU TP +3.5% OU trailing -0.6% depuis pic OU signal short |
| Short | RSI(10) croise en dessous de 65 | SL +1% OU TP -3.5% OU trailing +0.6% depuis creux OU signal long |

### Résultats validés (données réelles yfinance 5Y)

| Métrique | In-Sample | Walk-Forward OOS avg |
|---|---|---|
| Sharpe ratio | **+1.82** | **+1.03** |
| Profit factor | 4.04 | — |
| Win rate | 68% | — |
| Nombre de trades | 88 | ~22/fenêtre |
| Max drawdown | 0.1% | — |
| WF coefficient de variation | — | **0.94** (ROBUSTE) |

**Verdict : ROBUSTE** — Sharpe OOS > 0.8 sur les 4 fenêtres walk-forward, CV proche de 1.

---

## 7. Pairs Trading

### Modules

- **`core/data/pairs.py`** : `PairDiscovery` — coïntégration, hedge ratio, ADF, half-life
- **`core/backtest/pairs_engine.py`** : `PairsBacktestEngine` — simulation dollar-neutral
- **`scripts/pairs_backtest.py`** : script CLI pour scanner tout un secteur

### Modèle mathématique

**Hedge ratio** (OLS sans intercept) :
```
β = (X'X)^-1 X'Y   où X = log(price_B), Y = log(price_A)
```

**Spread** :
```
S(t) = log(price_A) - β * log(price_B) - α
```

**Z-score** (rolling, fenêtre 30 barres) :
```
z(t) = (S(t) - mean(S[t-30:t])) / std(S[t-30:t])
```

**ADF simplifié** (sans statsmodels) :
```
ΔS(t) = γ·S(t-1) + ε   → t-stat = γ / se(γ)
p-value via scipy.stats.t.cdf (one-sided)
```

**Half-life Ornstein-Uhlenbeck** :
```
ΔS = γ·S[t-1] + α   →   half_life = -log(2) / γ
```

### No-lookahead

Le signal utilise `zscore.shift(1)` : le signal au bar `i` est calculé sur les données jusqu'à `close[i-1]`, exécuté à `open[i+1]`.

### Dollar-neutral

```python
size_a = notional / open_a   # même montant $ sur chaque leg
size_b = notional / open_b
# Long A = +size_a actions, Short B = -size_b actions
```

### SECTOR_MAP — Univers couvert

| Secteur | Nombre d'actifs | Paires possibles | Coïntégrées (filtrées) |
|---|---|---|---|
| `tech_us` | 46 (S&P 500 IT) | 1 035 | ~127 |
| `finance_us` | 23 (S&P 500 Financials) | 253 | ~40 |
| `europe` | 13 (STOXX 600) | 78 | ~15 |
| `crypto` | 8 | 28 | ~5 |

### Top paires identifiées

**Tech US** (run complet 5Y, données réelles) :

| Paire | Sharpe | WR | DD | Trades | Half-life |
|---|---|---|---|---|---|
| MU / AMAT | +1.15 | 71% | 4.8% | 34 | 12j |
| ADI / MPWR | +1.02 | 68% | 5.1% | 29 | 15j |
| MSFT / AVGO | +0.99 | 65% | 6.3% | 31 | 18j |
| GOOGL / LRCX | +0.89 | 63% | 7.2% | 27 | 14j |

**Finance US** :

| Paire | Sharpe | WR | DD | Trades |
|---|---|---|---|---|
| JPM / GS | +0.86 | 82% | 6.2% | 28 |

### CLI pairs backtest

```bash
# Scanner tout le secteur tech (46 actifs, ~127 paires coïntégrées)
python scripts/pairs_backtest.py --sector tech_us

# Avec filtres personnalisés
python scripts/pairs_backtest.py --sector tech_us --min-correlation 0.7 --max-halflife 60

# Finance
python scripts/pairs_backtest.py --sector finance_us

# Paramètres disponibles
--sector            tech_us | finance_us | europe | crypto
--min-adf-pvalue    seuil ADF (défaut: 0.05)
--min-correlation   corrélation minimale (défaut: 0.50)
--max-halflife      half-life max en jours (défaut: 120)
--entry-z           z-score entrée (défaut: 2.0)
--exit-z            z-score sortie (défaut: 0.5)
--period            période yfinance (défaut: 5y)
```

---

## 8. Optimisation et scan d'actifs

### Grid Search (`scripts/optimize.py`)

Teste toutes les combinaisons de paramètres en IS (70%) et OOS (30%) :

```bash
python scripts/optimize.py --strategy rsi_mean_reversion --asset IWM --timeframe 1D
```

Paramètres balayés : `rsi_period`, `oversold`, `overbought`, `stop_loss_pct`, `take_profit_pct`.
Critère de sélection : Sharpe OOS, avec contrôle anti-overfit (ratio IS/OOS < 2.0).

### Asset Scan (`scripts/asset_scan.py`)

Teste une stratégie sur l'ensemble de l'univers (`core/data/universe.py`) :

```bash
python scripts/asset_scan.py --strategy rsi_filtered --timeframe 1D
```

Résultats sauvegardés dans `results/scan_<strategy>_<universe>.csv`.

### Batch Backtest (`scripts/batch_backtest.py`)

Lance tous les JSONs du dossier `strategies/` sur données réelles et génère un rapport comparatif.

```bash
python scripts/batch_backtest.py --period 2y
```

---

## 9. Dashboard Streamlit

Interface locale pour la revue des stratégies et l'analyse des paires.

```bash
# Lancer (depuis trading-platform/)
python -m streamlit run dashboard.py
# Accessible sur http://localhost:8501
```

### Onglets

| Onglet | Contenu |
|---|---|
| **Stratégies** | Liste toutes les stratégies JSON, tri par Sharpe, filtres par style/actif/TF |
| **Backtest** | Lance un backtest on-demand, affiche equity curve + métriques |
| **Walk-Forward** | Affiche les Sharpes par fenêtre OOS, CV, robustesse |
| **Monte Carlo** | Distribution des Sharpes sur 1000 simulations, P5/P50/P95 |
| **Portfolio** | Corrélation inter-stratégies, allocation Kelly |
| **Logs** | Audit trail des exécutions |

---

## 10. Tests

### Suite pytest complète

```bash
# Lancer tous les tests
cd trading-platform && pytest tests/ -v

# Avec couverture
pytest tests/ --tb=short -q
```

| Fichier | Tests | Scope |
|---|---|---|
| `test_sprint2.py` | 15 | FeatureStore, BacktestEngine, 3 stratégies intraday |
| `test_sprint3.py` | 12 | Paper trading, régime marché, StrategyRanker |
| `test_sprint4.py` | 14 | Trailing stop, expectancy, corrélation portfolio |
| `test_sprint5.py` | 18 | Grid search, walk-forward, Monte Carlo |
| `test_pairs.py` | 21 | Hedge ratio OLS, ADF, half-life, no-lookahead, dollar-neutral, P&L, equity MTM |

**Total : 80 tests**, tous verts.

### Tests critiques `test_pairs.py`

- `test_no_lookahead_zscore_shifted` : vérifie que le signal utilise `zscore.shift(1)`
- `test_dollar_neutral_entry` : vérifie `|pnl_a + pnl_b| < threshold` à l'entrée
- `test_halflife_random_walk_is_large` : 20 seeds, ≥14/20 ont hl > 20 jours (RW non mean-reverting)
- `test_end_to_end_cointegrated_pair` : pipeline complet sur paire synthétique coïntégrée

---

## 11. Historique des commits (sprints)

| Hash | Message | Contenu principal |
|---|---|---|
| `814955f` | feat(platform): architecture Sprint 1 | Bus asyncio, 6 agents, orchestrateur |
| `260b683` | feat(sprint2): Feature Store + 3 strats intraday | RSI, BB, Momentum + Monte Carlo |
| `afb1360` | refactor(research): mode file par défaut | Research agent sans clé API |
| `bf24271` | feat(sprint3): Paper trading + régime + ranker | Boucle temps réel, RegimeDetector |
| `95c7632` | feat(sprint4): trailing stop + expectancy | Stop dynamique, 3 strats, corrélation |
| `d26978f` | feat(data): yfinance + batch_backtest | Données réelles, script lot |
| `9d0088d` | feat(sprint5): grid search IS/OOS | Optimisation paramétrique |
| `eb2c46f` | feat(sprint5b): tickers map + JSONs optimisés | Univers 50 actifs |
| `230ec0e` | feat(dashboard): interface Pro Streamlit | Dashboard multi-onglets |
| `00efda1` | feat(pairs): moteur backtest dollar-neutral | PairsBacktestEngine + PairDiscovery |
| `c8a148d` | fix(pairs): remplacer emojis Unicode ASCII | Compatibilité Windows cp1252 |
| `949534c` | feat(pairs): SECTOR_MAP S&P500 (46 valeurs) | 1081 paires, 127 coïntégrées |
| `6518c35` | feat(strategy): rsi_iwm_1d_v1 ROBUSTE | **Sharpe +1.82 IS, +1.03 OOS** |

---

## 12. Commandes CLI de référence

### Backtest rapide (standalone)

```python
# Dans Python / notebook
from orchestrator.main import Orchestrator
import asyncio

async def quick_backtest():
    orch = Orchestrator()
    result = await orch.run_backtest_only(
        strategy={"strategy_id": "rsi_iwm_1d_v1", ...},
        data_config={"source": "yfinance", "period": "5y"}
    )
    return result

asyncio.run(quick_backtest())
```

### Pipeline complet avec Claude API

```bash
export ANTHROPIC_API_KEY=sk-...
python scripts/run_research.py --asset EURUSD --timeframe 1H --style mean_reversion --timeout 120
python scripts/run_research.py --asset NVDA --timeframe 15M --style breakout
python scripts/run_research.py --asset SPY --timeframe 1D --style trend_following
```

### Optimisation

```bash
python scripts/optimize.py --strategy rsi_mean_reversion --asset IWM --timeframe 1D
python scripts/optimize.py --strategy bb_squeeze --asset TSLA --timeframe 5M
```

### Scan multi-actifs

```bash
python scripts/asset_scan.py --strategy rsi_filtered --timeframe 1D
python scripts/batch_backtest.py --period 2y
```

### Pairs

```bash
python scripts/pairs_backtest.py --sector tech_us
python scripts/pairs_backtest.py --sector finance_us --min-correlation 0.7
python scripts/pairs_backtest.py --sector crypto --max-halflife 30
```

### Dashboard

```bash
python -m streamlit run dashboard.py   # http://localhost:8501
```

### Tests

```bash
pytest tests/ -v
pytest tests/test_pairs.py -v
pytest tests/ -q --tb=short
```

---

## 13. Stack technique

| Couche | Technologie |
|---|---|
| Langage | Python 3.11+ |
| Async | `asyncio` (bus de messages, agents) |
| Data | `pandas`, `numpy`, `scipy` |
| Données marché | `yfinance` (réelles), GBM synthétique (tests) |
| LLM Research | `anthropic` SDK (Claude Sonnet/Opus) |
| Exécution | IG Markets REST API (`requests`, `aiohttp`) |
| Dashboard | `streamlit`, `plotly` |
| Tests | `pytest`, `pytest-asyncio` |
| Validation JSON | `jsonschema` |
| Config | `python-dotenv` |
| Logs | `structlog` |

### Variables d'environnement

```
ANTHROPIC_API_KEY    # Clé Claude API (Research Agent)
IG_API_KEY           # Clé IG Markets
IG_PASSWORD          # Mot de passe IG
IG_ACCOUNT_ID        # ID compte IG
RESEARCH_MODE        # "api" | "file" (défaut: file)
```

---

## Annexe — Leçons apprises

### 1. Données synthétiques vs réelles
Les stratégies de mean-reversion **échouent toujours** sur GBM (processus de martingale). La validation est obligatoirement sur données réelles yfinance.

### 2. Choix de l'asset pour mean-reversion
- **IWM > SPY > QQQ** pour la mean-reversion
- Les small caps (Russell 2000) ont une liquidité réduite → overshoots plus fréquents → plus mean-reverting
- QQQ (NASDAQ 100) en 2024-2026 était en tendance haussière forte → incompatible avec mean-reversion

### 3. Timeframe et nombre de trades
- Un minimum de ~20 trades par fenêtre OOS est requis pour la significativité statistique
- Sur 1H, 1 an = ~1500 barres mais peu de trades → préférer 1D ou 5Y de données
- Règle pratique : `n_trades_total / n_wf_windows >= 20`

### 4. Walk-forward et robustesse
- CV (coefficient de variation des Sharpes OOS) < 0.5 = suspect
- CV proche de 1.0 = bonne robustesse entre fenêtres
- Si Sharpe IS >> Sharpe OOS : overfit, rejeter

### 5. Pairs trading — pièges
- Ne pas oublier `zscore.shift(1)` → sinon lookahead bias
- Le hedge ratio β doit être recalibré régulièrement (drift de coïntégration)
- ANSS (ANSYS) a été délisté/racheté → yfinance retourne 404, gérer silencieusement

### 6. Windows cp1252
Sur Windows, éviter les caractères Unicode (β, ✅, →) dans les `print()` → remplacer par ASCII (`beta`, `[OK]`, `->`).
