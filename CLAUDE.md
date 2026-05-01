# CLAUDE.md — Trading Platform

## Commandes
```bash
python worker.py                                 # Worker 24/7
python scripts/paper_portfolio.py --status       # Dashboard
python -m pytest -q -o cache_dir=.pytest_cache --basetemp .pytest_tmp  # Tests (3669 pass)
python scripts/runtime_audit.py --strict         # Verite runtime (VPS exit 0, local FAIL attendu dev)
python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5  # Gate Alpaca PDT waiver
ssh -i ~/.ssh/id_hetzner root@178.104.125.74     # VPS Hetzner
```

## Regles critiques
- **No lookahead** : guard 9:35-15:55 ET, .shift(1), df_full.iloc[:i]
- **Couts reels** : $0 commission + 0.02% slippage (Alpaca US), 0.10% (Binance), $2 (IBKR FX)
- **Walk-forward obligatoire** : >= 50% fenetres OOS profitables
- **Paper d'abord** : PAPER_TRADING=true, guard AlpacaClient + BINANCE_LIVE_CONFIRMED
- **SL obligatoire** : tout ordre doit avoir un stop-loss (CRO audit)
- **Pipeline obligatoire** : _authorized_by sur tous les ordres
- **Shorts en qty entiere** : pas de notional pour les SELL

## Skills projet
- **Par defaut**, utiliser les skills projet situes dans `C:\Users\barqu\.claude\skills` quand ils sont pertinents pour la mission.
- Priorite workflow desk/research: `discover` -> `crypto` -> `bt` -> `qr` -> `risk` -> `review` -> `exec`.
- Ne pas utiliser ces skills comme pretexte pour contourner les garde-fous runtime/prod du projet.

## Architecture (fichiers cles)
```
worker.py                          # Scheduler 24/7 + CycleRunners (9 cycles)
core/worker/{task_queue,cycle_runner,worker_state,event_logger}.py  # Robustesse
core/broker/{binance_broker,ibkr_bracket,factory,broker_health}.py  # 3 brokers + health
core/broker/contracts/{binance,ibkr,alpaca}_contracts.py            # Contract testing
core/{risk_manager_live,kill_switch_live}.py           # Risk 12 checks + kill switch
core/crypto/{risk_manager_crypto,allocator_crypto}.py  # Crypto risk + allocation
core/execution/{order_state_machine,position_state_machine,order_tracker}.py  # SM formelles
core/monitoring/{metrics_pipeline,anomaly_detector,incident_report}.py  # Observabilite
strategies/crypto/                 # 12 strats Binance (8 live + 4 new)
strategies_v2/fx/                  # 12 strats FX IBKR
strategies_v2/futures/             # 8 strats futures IBKR
scripts/{wf_fx_all,wf_crypto_all}.py  # Walk-forward scripts
scripts/{deploy.sh,pre_deploy_check.py}  # Canary deploy + checklist
config/{allocation,crypto_allocation,limits_live,crypto_limits}.yaml
```

## Etat actuel (as of 2026-04-19T15:01Z — voir docs/audit/hygiene_baseline.md)
- **16 strategies canoniques** : 2 ACTIVE + 11 READY + 1 AUTHORIZED + 2 DISABLED (+15 archived REJECTED)
- **2 LIVE** (ibkr_futures uniquement) :
  - `cross_asset_momentum` (grade A, live depuis 2026-04-07, position MCL +$295 unrealized)
  - `gold_oil_rotation` (grade S, live depuis 2026-04-08, signal dormant)
- **0 crypto LIVE** post bucket A drain 2026-04-19 (11 strats archivees REJECTED)
- **Paper candidates probation** : gold_trend_mgc V1 (grade A, earliest 2026-05-16), mes_monday (B), alt_rel_strength (B, 2026-05-18), btc_asia q80_long_only (B, 2026-05-20)
- **3669 tests pass** 0 fail (pytest suite complete)
- **Hetzner** : IB Gateway 10.45, port 4002 live / 4003 paper. Runtime audit VPS exit 0.
- **Capital** : $20,856 live deployable (IBKR $11,013 + Binance $9,843), 1.09% at-risk (1 position MCL). Alpaca $99K paper (PDT waiver requiert $25K depot).
- **Risk limits** (`config/limits_live.yaml`) : daily -5%, hourly -3%, weekly -8%, trailing_5d -8%, monthly -12%, level_1 DD -2.5%, level_3 DD -6%, max 4 contrats futures simultanes.
- **Scores** (post iter3-fix2, honnete) : plateforme 8.5/10, live readiness 5.5/10, ROC/capital usage 4.0/10, qualite livrables docs 7.5/10.

## Variables env
`ALPACA_API_KEY` `ALPACA_SECRET_KEY` `PAPER_TRADING=true`
`IBKR_HOST=178.104.125.74` `IBKR_PORT=4002` `IBKR_PAPER=true`
`BINANCE_API_KEY` `BINANCE_API_SECRET` `BINANCE_TESTNET=false` `BINANCE_LIVE_CONFIRMED=true`
