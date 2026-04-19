# Cycle Map — worker.py + extracted modules

Source de verite des cycles APScheduler du worker. Mise a jour 2026-04-19
apres Phase 2 XXL (extraction paper_cycles + macro_ecb_runner).

## Structure actuelle

worker.py (6390 lignes) contient encore :
- `main()` + `_handle_sigterm()` — bootstrap + arret propre
- Scheduler APScheduler (registration des jobs)
- Cycles "core" tres dependents du module worker (locks, helpers internes) :
  - `_run_futures_cycle()` (1200 lignes — futures live + paper)
  - `run_crypto_cycle()` (~900 lignes — Binance crypto)
  - `run_live_risk_cycle()` (~400 lignes — IBKR risk live)
  - `run_bracket_watchdog_cycle()` (~316 lignes)
  - `run_fx_carry_cycle()` (~282 lignes)
  - autres cycles FX, US, EU, daily/intraday...

## Modules extraits (Phase 2)

### `core/worker/cycles/paper_cycles.py`
Paper-only runners log-only (pas d'ordre reel) :
- `run_mib_estx50_spread_paper_cycle` — 17h45 Paris weekday, MIB/ESTX50 spread
- `run_alt_rel_strength_paper_cycle` — 03h00 Paris daily, T4-A2 alts
- `run_btc_asia_mes_leadlag_paper_cycle` — 10h30 Paris weekday, T3-A2
- `run_us_sector_ls_paper_cycle` — 22h30 Paris weekday, T3-B1
- `run_eu_relmom_paper_cycle` — 18h00 Paris weekday, T3-A3
- `_run_relmom_paper_tick` — helper partage US/EU

### `core/worker/cycles/macro_ecb_runner.py`
- `make_macro_ecb_executor(mode, ibkr_lock)` — factory placement bracket OCA
- `run_macro_ecb_live_cycle(ibkr_lock)` — cycle event-driven jours BCE

### `core/worker/cycles/macro_ecb_cycle.py` (deja extrait avant Phase 2)
- `run_macro_ecb_cycle` — fetch DAX/CAC40/ESTX50 + signal generation

## Scheduler entry points (worker.py main)

Chaque cycle est enregistre via APScheduler avec son trigger (cron / interval).
Voir `worker.py:main()` pour la liste complete des `scheduler.add_job()` calls.

## Roadmap decomposition (futur)

Reduction worker.py >5000 lignes -> <1500 lignes ne peut pas etre fait en 1 phase
sans risquer la prod live (CAM + gold_oil_rotation). Etapes restantes :

1. Extract `_run_futures_cycle` -> `cycles/futures_cycle.py` (~1200 lignes,
   forte interaction avec _ibkr_lock + helpers _strat_is_paused etc).
   Necessite refactor des helpers en module `core/worker/strategy_state.py`.

2. Extract `run_crypto_cycle` -> `cycles/crypto_cycle.py` (~900 lignes).
   Forte interaction avec _enrich_crypto_kwargs + _execute_earn_signal +
   _log_strategy_debug. Refactor helpers d'abord.

3. Extract `run_live_risk_cycle` -> `cycles/live_risk_cycle.py` (~400 lignes).

4. Extract bracket watchdog + trailing stop -> `cycles/bracket_watchdog.py`.

5. Refactor `_strat_is_paused` / `_strat_record_failure` / `_strat_record_success`
   -> `core/worker/strategy_state.py` (already partial in `worker_state.py`).

Apres ces 5 etapes : worker.py = scheduler + main + bootstrap (<1500 lignes).
