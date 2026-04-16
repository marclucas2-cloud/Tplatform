# Persistence inventory — état runtime critique

**Date** : 2026-04-16
**Contexte** : Phase 0 du plan TODO XXL DESK PERSO 10/10
**Source** : inspection scripts + worker.py + grep patterns écriture

## Classification

| Niveau | Signification |
|---|---|
| **CRITICAL** | Perte = perte de position/ordre live, impact $$ direct |
| **IMPORTANT** | Perte = perte tracking historique / drawdown / régime |
| **DERIVED** | Recalculable depuis broker API ou autre source |
| **CACHE** | Transient, recalculé chaque cycle |

## Inventaire

### CRITICAL (5 fichiers)

| Path | Format | Writer | Reader | Recovery |
|---|---|---|---|---|
| `data/state/futures_positions_live.json` | JSON | `worker._run_futures_cycle` | `worker.bracket_watchdog` | IBKR API positions |
| `data/state/futures_positions_paper.json` | JSON | `worker._run_futures_cycle` | `worker.bracket_watchdog` | IBKR PAPER API |
| `data/kill_switch_state.json` | JSON | `worker._run_macro_ecb_cycle` | tous cycles | redéfinir thresholds défaut |
| `data/live_risk_dd_state.json` | JSON | `worker._run_live_risk_cycle` | `live_risk_cycle` | rebuild à 1er cycle du jour |
| `data/crypto_dd_state.json` | JSON | `worker._run_crypto_cycle` | `crypto_watchdog` | rebuild depuis Binance API |

### IMPORTANT (6 fichiers)

| Path | Format | Writer | Reader | Recovery |
|---|---|---|---|---|
| `data/state/always_on_carry.json` | JSON | `worker._run_carry_daily` | dashboard | rebuild depuis IBKR positions |
| `data/state/xmomentum_state.json` | JSON | `worker._run_xmomentum_cycle` | dashboard | rebuild manuel |
| `data/state/paper_portfolio_state.json` | JSON | dynamic | `paper_portfolio` | rebuild Alpaca paper API |
| `data/fx/carry_mom_ks_state.json` | JSON | `worker._run_fx_cycle` | fx_carry_strat | redéfinir baseline |
| `logs/portfolio/YYYY-MM-DD.jsonl` | JSONL | `worker._run_portfolio_snap` | dashboard analytics | rebuild forward only |
| `data/audit/orders_YYYY-MM-DD.jsonl` | JSONL | `record_order_decision` | audit reviews | append-only, no recovery needed |

### DERIVED (cache + recalculable)

- `data/friday_close_price.json` — prix vendredi pour détection weekend gap
- `data/fx/*.parquet` — candles FX (cron yfinance)
- `data/crypto/candles/*.parquet` — candles crypto (cron Binance)
- `data/futures/*.parquet` — candles futures (cron yfinance, **buggy: NaT/duplicates**)

### CACHE (transient, peuvent être effacés)

- `data/cache/*` — caches divers
- `__pycache__/` — bytecode Python
- `.pytest_cache/` — pytest cache

## Risques identifiés

### R1 — Path dispersion
Les états critiques sont **partiellement** dans `data/state/` mais aussi dans `data/` racine (kill_switch, live_risk, crypto_dd). Doctrine 10/10 demande convention `data/state/books/<book_id>/...`.

**Action proposée** :
```
data/state/
  books/
    binance_crypto/
      dd_state.json
      positions.json
    ibkr_futures/
      positions_live.json
      positions_paper.json
    ibkr_eu/
      positions.json (ou skip car paper_only)
  global/
    kill_switch_state.json
    live_risk_dd_state.json
```

### R2 — Pas de backup automatique
Aucun backup formel des fichiers CRITICAL. En cas de corruption disk → perte définitive.

**Action proposée** : ajouter rsync/cron quotidien `data/state/` → `data/backups/state/<YYYY-MM-DD>/`.

### R3 — Aucun test de rebuild
Le plan Phase 5.5 demande "test de reconstruction de l'état depuis les journaux". Actuellement non fait.

**Action proposée** : script `scripts/recovery/rebuild_state_from_audit.py` qui replay `data/audit/orders_*.jsonl` pour reconstruire `futures_positions_*.json`.

### R4 — Convention de naming `paper`/`live` ambigüe
- `data/state/paper_portfolio_state.json` → utilisé par script `paper_portfolio.py` mais peut être `live` selon `PAPER_TRADING` env
- `data/fx/carry_mom_ks_state.json` → ambigu paper/live

**Action proposée** : suffixer explicitement `_live` ou `_paper` dans tous les noms.

## Top 3 risques runtime

1. **Pas de rebuild testé** : si corruption fichier critical, pas de procédure → perte d'état complète
2. **Convention `data/state/` non respectée** : 5 fichiers critical à la racine `data/` ou dispersés
3. **kill_switch_state.json partagé entre crypto et IBKR** : pas de scope par book → un trigger crypto peut killer IBKR

## Nombre par classe

- CRITICAL : **5**
- IMPORTANT : **6**
- DERIVED : **4 familles**
- CACHE : **N (gitignored)**

**Total fichiers state critique à gouverner** : 11 (CRITICAL + IMPORTANT)
