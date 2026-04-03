# CLAUDE.md — Trading Platform

## Commandes
```bash
python worker.py                                 # Worker 24/7
python scripts/paper_portfolio.py --status       # Dashboard
python -m pytest tests/ -v --tb=short            # Tests (3509)
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

## Etat actuel (voir SYNTHESE_COMPLETE.md pour details)
- **47 strats** : 11 crypto + 15 FX/EU + 7 US + 8 futures + 5 P2/P3 + 1 Cross-Asset Momentum (paper)
- **14 LIVE** : 11 crypto + 1 FX carry + Cross-Asset Momentum en paper
- **3,509 tests**, CRO 9.5/10 post V14.0
- **Hetzner** : IB Gateway 10.45, port 4002, VNC :5900
- **Data** : 265K candles + 175 midcap tickers (3 ans daily via Alpaca)
- **Capital** : $45K cross-broker (Binance 10K + IBKR 10K + Alpaca 30K paper)

## Variables env
`ALPACA_API_KEY` `ALPACA_SECRET_KEY` `PAPER_TRADING=true`
`IBKR_HOST=178.104.125.74` `IBKR_PORT=4002` `IBKR_PAPER=true`
`BINANCE_API_KEY` `BINANCE_API_SECRET` `BINANCE_TESTNET=false` `BINANCE_LIVE_CONFIRMED=true`
