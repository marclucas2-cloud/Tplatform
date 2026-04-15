# Portfolio Baseline — 2026-04-15

**WP-01 decorrelation research** — snapshot canonique du portefeuille actuel.

Source de verite: `config/live_whitelist.yaml` v1
Genere le: 2026-04-15T21:15:13.432050Z

## Inventaire par book

| Book | Live | Paper/Disabled | Total |
|---|---|---|---|
| alpaca_us | 0 | 1 | 1 |
| binance_crypto | 12 | 0 | 12 |
| ibkr_eu | 0 | 5 | 5 |
| ibkr_futures | 3 | 0 | 3 |
| ibkr_fx | 0 | 1 | 1 |

## Inventaire par famille de signal

| Signal family | Count |
|---|---|
| momentum_trend | 6 |
| mean_reversion | 5 |
| cross_asset_rotation | 4 |
| unknown | 3 |
| volatility_breakout | 2 |
| event_driven | 1 |
| carry_yield | 1 |

## Detail strategies live

| strategy_id | book | status | signal_family | capital_model | horizon_days |
|---|---|---|---|---|---|
| cross_asset_momentum | ibkr_futures | live_core | momentum_trend | margin_leveraged | 20 |
| gold_trend_mgc | ibkr_futures | live_core | momentum_trend | margin_leveraged | 10 |
| gold_oil_rotation | ibkr_futures | live_core | cross_asset_rotation | margin_leveraged | 10 |
| btc_eth_dual_momentum | binance_crypto | live_core | momentum_trend | spot_or_margin_isolated | 0 |
| volatility_breakout | binance_crypto | live_core | volatility_breakout | spot_or_margin_isolated | 0 |
| btc_dominance_rotation_v2 | binance_crypto | live_core | cross_asset_rotation | spot_or_margin_isolated | 0 |
| borrow_rate_carry | binance_crypto | live_core | carry_yield | yield_passive | 0 |
| liquidation_momentum | binance_crypto | live_probation | momentum_trend | spot_or_margin_isolated | 0 |
| weekend_gap_reversal | binance_crypto | live_probation | mean_reversion | spot_or_margin_isolated | 0 |
| trend_short_btc | binance_crypto | live_probation | momentum_trend | spot_or_margin_isolated | 0 |
| mr_scalp_btc | binance_crypto | live_probation | mean_reversion | spot_or_margin_isolated | 0 |
| liquidation_spike | binance_crypto | live_probation | event_driven | spot_or_margin_isolated | 0 |
| vol_expansion_bear | binance_crypto | live_probation | volatility_breakout | spot_or_margin_isolated | 0 |
| range_bb_harvest | binance_crypto | live_probation | unknown | spot_or_margin_isolated | 0 |
| bb_mean_reversion_short | binance_crypto | live_probation | mean_reversion | spot_or_margin_isolated | 0 |

## Returns futures par strategie (10Y baseline)

| strategy_id | total_pnl | active_days | pnl_per_day |
|---|---|---|---|
| cross_asset_momentum | $+2,464 | 92 | $+26.8 |
| gold_oil_rotation | $+5,925 | 101 | $+58.7 |
| gold_trend_mgc | $+18,127 | 202 | $+89.7 |

**Dominance**: quelle part du PnL total vient de chaque strategie

- `cross_asset_momentum`: 9% du PnL futures
- `gold_oil_rotation`: 22% du PnL futures
- `gold_trend_mgc`: 68% du PnL futures

## Data gaps identifies

- `binance_crypto`: returns daily harmonisees absentes — les strats tournent en live
  mais il n'y a pas encore de timeseries reconstituee depuis les logs du worker.
  Action: reconstruire depuis `logs/worker/worker.log` ou depuis un backtest dedie.
- `ibkr_fx`: book disabled, pas de returns (normal).
- `ibkr_eu`: book paper_only, returns disponibles via `paper_portfolio_eu_state.json`
  mais hors scope live.
- `alpaca_us`: book paper_only, returns via state Alpaca paper.

## Moteurs dominants identifies

Sur le book `ibkr_futures`, 86% du PnL vient historiquement de `gold_trend_mgc` 
mais apres first-refusal CAM le poids est redistribue.
- `gold_trend_mgc`: $+18,127
- `gold_oil_rotation`: $+5,925
- `cross_asset_momentum`: $+2,464

## Prochaines etapes (WP-02 / WP-03)

1. Construire la matrice de correlation pour les 3 strats futures (done in WP-02)
2. Clustering hierarchique -> detection des redondances
3. Score marginal engine -> comment chaque candidate ameliore le portefeuille
4. Gap map -> quels regimes sont mal monetises
