# Live State Inventory — P2.1 live hardening

Ce document liste tous les fichiers de state qui gouvernent un comportement
live de la plateforme. Chaque ligne indique owner, book, usage, criticité,
et mode de reconstruction en cas de perte.

**MAJ** : 2026-04-15
**Owner** : Marc (solo dev) — pas d'équipe, toutes les responsabilités centralisées.

## Légende criticité

- 🔴 **CRITIQUE** — la perte bloque immédiatement le live / peut causer des doubles positions
- 🟠 **HAUTE** — la perte dégrade une fonction sans bloquer le live
- 🟡 **MOYENNE** — la perte casse une feature non-critique (reporting, display)
- 🟢 **BASSE** — reconstructible trivialement

## Inventaire

### Futures (IBKR)

| Fichier | Book | Usage | Criticité | Reconstruction |
|---|---|---|---|---|
| `data/state/futures_positions_live.json` | ibkr_futures | Positions futures LIVE, SL/TP OCA groups | 🔴 CRITIQUE | Reconciliation au boot via `ibkr._ib.positions()` + `reqAllOpenOrders()` |
| `data/state/futures_positions_paper.json` | ibkr_futures (paper) | Positions paper port 4003 | 🟠 HAUTE | Reconciliation au boot, acceptable de perdre |
| `data/futures/*_1D.parquet` | ibkr_futures | Données daily pour signaux (MES, MNQ, M2K, MGC, MCL, DAX, etc.) | 🔴 CRITIQUE | Cron refresh quotidien 23h30 Paris via `refresh_futures_parquet.py` (yfinance) |
| `data/futures/*_LONG.parquet` | backtest | Historique 10Y pour backtests hors-ligne | 🟡 MOYENNE | Backtests nécessaires uniquement, pas le live |

### Crypto (Binance)

| Fichier | Book | Usage | Criticité | Reconstruction |
|---|---|---|---|---|
| `data/crypto_equity_state.json` | binance_crypto | Equity totale = spot + earn + margin | 🔴 CRITIQUE | Reconstruit par `CryptoRiskManager` au cycle (rafraîchi toutes les 5 min) |
| `data/crypto_kill_switch_state.json` | binance_crypto | État du kill switch crypto | 🔴 CRITIQUE | Backup avant modif, pas de recalcul automatique — manuel si perte |
| `data/crypto_kill_switch_state.json.bak` | binance_crypto | Backup kill switch | 🟠 HAUTE | Généré automatiquement avant écriture |
| `data/orchestrator/state.json` | binance_crypto | État de l'orchestrator crypto (strategy pause, failure counter) | 🟠 HAUTE | Reconstructible par replay des events JSONL |
| `data/orchestrator/failures.json` | binance_crypto | Failure tracker crypto strats | 🟠 HAUTE | Reset OK, perte = tout est à 0 |
| `data/tickets/*.json` | binance_crypto | Tickets d'alertes crypto (anomalies) | 🟡 MOYENNE | Append-only, perte acceptable |

### IBKR (partagé FX/EU/Futures)

| Fichier | Book | Usage | Criticité | Reconstruction |
|---|---|---|---|---|
| `data/state/ibkr_equity.json` | ibkr_* | Dernière equity IBKR connue (cache NAV) | 🟠 HAUTE | Refresh au cycle suivant via `ibkr._ib.accountValues()` |
| `data/state/kill_switch_state.json` | ibkr_* | État kill switch IBKR global | 🔴 CRITIQUE | Backup manuel |
| `data/risk/last_known_broker_state.json` | all IBKR | Snapshot risk last known | 🟠 HAUTE | Reconstructible par reconcile au prochain cycle |

### Legacy / Paper (non-critique live)

| Fichier | Book | Usage | Criticité |
|---|---|---|---|
| `data/state/paper_momentum_state.json` | paper | State paper momentum | 🟡 MOYENNE |
| `data/state/paper_pairs_state.json` | paper | State paper pairs | 🟡 MOYENNE |
| `data/state/paper_portfolio_state.json` | paper | State paper portfolio | 🟡 MOYENNE |
| `data/state/paper_trading_state.json` | paper | State paper trading générique | 🟡 MOYENNE |
| `data/state/paper_vrp_state.json` | paper | State paper VRP | 🟡 MOYENNE |
| `paper_portfolio_eu_state.json` (ROOT) | ibkr_eu (paper) | State pipeline EU live (ancien nom `paper_portfolio_eu`) | 🟠 HAUTE |

### Config canonique (pas "state" mais source de vérité)

| Fichier | Usage | Criticité |
|---|---|---|
| `config/live_whitelist.yaml` | Whitelist canonique (P1.1) | 🔴 CRITIQUE |
| `config/limits_live.yaml` | Limites de risque live | 🔴 CRITIQUE |
| `config/crypto_limits.yaml` | Limites crypto | 🔴 CRITIQUE |
| `config/allocation.yaml` | Allocations global | 🟠 HAUTE |
| `config/crypto_allocation.yaml` | Allocations crypto | 🟠 HAUTE |
| `config/regime.yaml` | Activation matrix par régime | 🟠 HAUTE |
| `config/strategies_eu.yaml` | Registry EU strats (paper) | 🟡 MOYENNE |

### Audit trail (P1.4)

| Fichier | Usage | Criticité |
|---|---|---|
| `data/audit/orders_{date}.jsonl` | Audit trail append-only par ordre live | 🟠 HAUTE |

### Logs & métriques (pas du state mais critique en cas d'incident)

| Fichier | Usage |
|---|---|
| `logs/worker/worker.log` | Log principal worker (rotation jour) |
| `logs/worker/worker_stdout.log` | Stdout worker |
| `logs/events/*.jsonl` | Events JSONL append-only |
| `data/metrics.db` | SQLite métriques (90j retention) |

## Candidats pour migration SQLite (P1.5 — pas encore fait)

Les fichiers CRITIQUES en JSON flat fichier sont fragiles (corruption possible
en cas de kill -9 pendant l'écriture). Migrer vers SQLite avec transactions :

1. `data/crypto_kill_switch_state.json` — kill switch crypto
2. `data/state/kill_switch_state.json` — kill switch IBKR
3. `data/state/futures_positions_live.json` — positions futures live
4. `data/orchestrator/state.json` — orchestrator state

## Backup

Cron `backup.sh` tourne à 03h00 UTC chaque jour et sauvegarde `/opt/trading-platform/data/`
+ `/opt/trading-platform/config/`. Logs dans `logs/backup/cron.log`.
**Ne pas toucher.**

## Règles d'usage

1. **Ne jamais écrire directement un state critique sans backup** — utiliser les
   helpers `save_atomic()` qui font `tmp + rename`.
2. **Les state files ne doivent jamais être source de vérité pour le sizing live** —
   toujours re-query le broker pour equity/positions avant un ordre.
3. **La whitelist (`config/live_whitelist.yaml`) est la seule source de vérité**
   pour "qu'est-ce qui tourne en live". Tout le reste (dashboard, API, reporting)
   lit ce fichier via `core.governance.load_live_whitelist()`.
4. **Les fichiers JSONL audit sont append-only** — jamais modifier / tronquer
   sauf via script de rotation explicite.
