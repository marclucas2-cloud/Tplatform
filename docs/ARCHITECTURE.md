# ARCHITECTURE — Trading Platform (snapshot 2026-04-19)

## Vue d'ensemble

Operateur unique (Marc) operant 3 brokers depuis VPS Hetzner Frankfurt :
- **IBKR Live** (port 4002, EUR 9.9K) - 2 strats live_core futures
- **IBKR Paper** (port 4003) - data + paper retrospective
- **Binance Live** ($8.7K) - 0 strat live actuellement (post-demotes)
- **Alpaca Paper** ($100K simule) - 0 strat live actuellement

## Composants principaux

```
worker.py (6390 lignes apres Phase 2)
  |
  +-- core/worker/cycles/
  |     +-- paper_cycles.py (Phase 2 extract)
  |     +-- macro_ecb_runner.py (Phase 2 extract)
  |     +-- macro_ecb_cycle.py
  |     +-- CYCLES.md (cartographie + roadmap)
  |
  +-- core/crypto/
  |     +-- risk_manager_crypto.py (12 checks + kill switch + dd_state_path)
  |     +-- dd_baseline_state.py (Phase 1: BootState 4 etats + atomic persist)
  |     +-- allocator_crypto.py + monitoring.py
  |
  +-- core/execution/
  |     +-- order_state_machine.py (DRAFT->FILLED/...)
  |     +-- order_tracker.py (Phase 3: persist + crash recovery)
  |     +-- position_state_machine.py (defini, non-wire prod)
  |     +-- bracket / orphan / partial_fill / slippage
  |
  +-- core/broker/
  |     +-- {binance_broker, ibkr_adapter, alpaca_adapter}.py
  |     +-- broker_health.py
  |     +-- contracts/ (validators + ContractRunner + validation_cycle Phase 4)
  |
  +-- core/governance/
  |     +-- pre_order_guard.py (7 checks: book, mode, whitelist, safety,
  |     |   kill, health, freshness)
  |     +-- audit_trail.py (Phase 5 tests)
  |     +-- live_whitelist.py + safety_mode_flag.py + kill_switches_scoped.py
  |     +-- book_health.py + data_freshness.py + auto_demote.py
  |     +-- reconciliation.py + reconciliation_cycle.py (Phase 6)
  |     +-- promotion_gate.py (Phase 7) + scripts/promotion_check.py CLI
  |
  +-- core/monitoring/
  |     +-- metrics_pipeline.py + anomaly_detector.py (Phase 12 tests)
  |     +-- incident_report.py (Phase 13 tests)
  |
  +-- core/research/
  |     +-- wf_canonical.py (Phase 9: schema v1 + reproducible manifest)
  |     +-- auto_backtest.py + research_pipeline.py
  |
  +-- strategies/{crypto,futures,fx,us,eu} + strategies_v2/...
       (76 actives apres Phase 8 archive de 21 unused)
```

## Flux d'un signal -> ordre

```
[Strategy.generate_signal]
  -> Signal dataclass (symbol, side, qty, sl, tp)
  -> [pre_order_guard] : 7 checks bloquants
  -> [risk_manager.check_all] : 12 checks crypto / 6 futures
  -> [order_tracker.create_order] : DRAFT, persist
  -> [order_tracker.validate] : VALIDATED, persist
  -> [broker.place_order] : POST API
  -> [order_tracker.submit] : SUBMITTED + broker_order_id, persist
  -> [audit_trail.record_order_decision] : JSONL data/audit/
  -> [callback fill] : tracker.fill() FILLED + has_sl, persist
  -> [position update] : state JSON file
  -> [Telegram alert] : critical/warning/info
```

## Persistance critique (state files)

Tous atomic write (tempfile + os.replace + fsync apres Phases 1, 3) :
- `data/crypto_dd_state.json` (Phase 1: schema v1, BootState classification)
- `data/state/order_tracker.json` (Phase 3: tous orders + transitions)
- `data/state/{book}/equity_state.json`
- `data/state/{book}/positions_*.json`
- `data/kill_switch_state.json` + `data/crypto_kill_switch_state.json`
- `data/audit/orders_YYYY-MM-DD.jsonl` (Phase 5)
- `data/governance/greenlights/*_*.json` (Phase 7)
- `data/reconciliation/{book}_YYYY-MM-DD.json` (Phase 6)

## Cycles APScheduler (worker.py main)

| Cycle                      | Schedule                | Source                                  |
|----------------------------|-------------------------|-----------------------------------------|
| run_crypto_cycle           | 5 min                   | worker.py                               |
| _run_futures_cycle live    | 16h Paris weekday       | worker.py                               |
| run_fx_carry_cycle         | 10h CET                 | worker.py                               |
| run_live_risk_cycle        | 5 min                   | worker.py                               |
| run_bracket_watchdog       | 5 min                   | worker.py                               |
| run_trailing_stop          | 15 min                  | worker.py                               |
| run_macro_ecb_live_cycle   | event-driven (ECB days) | core/worker/cycles/macro_ecb_runner.py  |
| run_mib_estx50_paper       | 17h45 Paris             | core/worker/cycles/paper_cycles.py      |
| run_alt_rel_strength_paper | 03h Paris               | core/worker/cycles/paper_cycles.py      |
| run_btc_asia_mes_leadlag   | 10h30 Paris             | core/worker/cycles/paper_cycles.py      |
| run_us_sector_ls_paper     | 22h30 Paris             | core/worker/cycles/paper_cycles.py      |
| run_eu_relmom_paper        | 18h Paris               | core/worker/cycles/paper_cycles.py      |
| run_v11/v12 regime         | various                 | worker.py                               |
| run_v10_portfolio          | hourly                  | worker.py                               |

## Doctrine

- **No lookahead**: guard 9:35-15:55 ET, .shift(1), df_full.iloc[:i] (audit Phase 14)
- **Couts reels**: $0 commission + 0.02% slippage (Alpaca), 0.10% (Binance), $2 (IBKR FX)
- **Walk-forward obligatoire**: >= 50% windows OOS profitable (Phase 9 wf_canonical)
- **Paper d'abord**: PAPER_TRADING=true, BINANCE_LIVE_CONFIRMED gate
- **SL obligatoire**: tout ordre doit avoir un stop-loss (CRO audit)
- **Pipeline obligatoire**: _authorized_by sur tous ordres (Alpaca enforce fail-closed)
- **Shorts en qty entiere**: pas de notional pour SELL

## Variables env critiques

```
ALPACA_API_KEY / ALPACA_SECRET_KEY / PAPER_TRADING=true
IBKR_HOST=178.104.125.74 / IBKR_PORT=4002 / IBKR_PAPER=true
BINANCE_API_KEY / BINANCE_API_SECRET / BINANCE_TESTNET=false / BINANCE_LIVE_CONFIRMED=true
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
DATA_FRESHNESS_GATE=true (opt-in)
MACRO_ECB_LIVE_ENABLED=false (gate avant live BCE)
```
