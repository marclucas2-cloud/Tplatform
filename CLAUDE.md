# CLAUDE.md — Trading Platform

## Commandes
```bash
python worker.py                                 # Worker 24/7
python scripts/paper_portfolio.py --status       # Dashboard
python -m pytest tests/ -v --tb=short            # Tests (2312)
ssh -i ~/.ssh/id_hetzner root@178.104.125.74     # VPS Hetzner
```

## Regles critiques
- **No lookahead** : guard 9:35-15:55 ET, .shift(1), df_full.iloc[:i]
- **Couts reels** : $0.005/share + 0.02% slippage (US), 0.10% (Binance), $2 (IBKR FX)
- **Walk-forward obligatoire** : >= 50% fenetres OOS profitables
- **Paper d'abord** : PAPER_TRADING=true, guard AlpacaClient + BINANCE_LIVE_CONFIRMED
- **SL obligatoire** : tout ordre doit avoir un stop-loss (CRO audit)
- **Pipeline obligatoire** : _authorized_by sur tous les ordres
- **Shorts en qty entiere** : pas de notional pour les SELL

## Architecture (fichiers cles)
```
worker.py                          # Scheduler Railway+Hetzner 24/7
core/broker/{binance_broker,ibkr_bracket,factory}.py  # 3 brokers
core/{risk_manager_live,kill_switch_live}.py           # Risk 12 checks + kill switch
core/crypto/{risk_manager_crypto,allocator_crypto}.py  # Crypto risk + allocation
strategies/crypto/                 # 12 strats Binance (8 live + 4 new)
strategies_v2/fx/                  # 12 strats FX IBKR
strategies_v2/futures/             # 8 strats futures IBKR
scripts/{wf_fx_all,wf_crypto_all}.py  # Walk-forward scripts
config/{allocation,crypto_allocation,limits_live,crypto_limits}.yaml
```

## Etat actuel (voir SYNTHESE_COMPLETE.md pour details)
- **46 strats** : 12 crypto (Binance 20K EUR) + 15 FX/EU (IBKR $10K) + 7 US (Alpaca) + 8 futures + 4 P2/P3
- **14 LIVE lundi** : 8 crypto + 6 FX/EU
- **2,312 tests**, CRO 9/10
- **Hetzner** : IB Gateway 10.45, port 4002, VNC :5900
- **Data** : 265K candles (FX IBKR + crypto Binance)

## Variables env
`ALPACA_API_KEY` `ALPACA_SECRET_KEY` `PAPER_TRADING=true`
`IBKR_HOST=178.104.125.74` `IBKR_PORT=4002` `IBKR_PAPER=true`
`BINANCE_API_KEY` `BINANCE_API_SECRET` `BINANCE_TESTNET=false` `BINANCE_LIVE_CONFIRMED=true`
