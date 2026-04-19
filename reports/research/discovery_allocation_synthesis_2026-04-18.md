# Discovery Allocation Synthesis - 2026-04-18

## Scope

This note consolidates:

- existing research results already present in the repo
- new T3-A discovery batch results
- new T3-B US batch results

It is a research and paper-trading synthesis only. It is not a production deployment instruction.

## Strategy matrix

### VALIDATED or close enough to include in the allocation draft

| Strategy | Book | Status | Why it survives |
|---|---|---|---|
| `pre_holiday_drift` | IBKR futures | validated existing | 5/5 WF, tiny tail risk, strong calendar diversification |
| `long_wed_oc` | IBKR futures | validated existing | 4/5 WF, low correlation to current futures core |
| `mcl_overnight_mon_trend10` | IBKR futures | validated new | 4/5 WF, MC clean, commodity overnight sleeve |
| `btc_asia_mes_leadlag_q70_v80` | Binance | validated new | 4/5 WF, strong marginal score, cross-timezone sleeve |
| `eu_relmom_40_3` | IBKR EU paper | validated new | 4/5 WF, low DD, clean regional relative-value sleeve |
| `us_sector_ls_40_5` | Alpaca paper | validated new | 3/5 WF, MC clean, best US market-neutral style candidate |
| `us_pead` | Alpaca paper | near-promotion existing | good marginal score, but not yet re-validated in the new batch |
| `crypto_long_short` | Binance | near-promotion existing | good marginal score, but no fresh WF in this session |

### Keep as reserve / conditional

| Strategy | Reason |
|---|---|
| `long_mon_oc` | valid, but slightly weaker and more redundant than `pre_holiday_drift` and `long_wed_oc` |
| `basis_carry_funding_gt_5pct` | excellent research result, but blocked by product/broker constraints |
| `eu_relmom_80_10_2v2` | decent score, but less compelling than `eu_relmom_40_3` |

### Rejected or blocked for now

| Strategy | Reason |
|---|---|
| `PEAD market-neutral` variants | repeated drawdown degradation under neutralized designs |
| `US cross-sectional MR` | already dropped historically and still not attractive |
| `crypto regime-reactive dominance filter` | blocked by broken BTC dominance dataset |
| `FX cross-sectional carry` | blocked by current broker/regulatory setup |

## Allocation philosophy

The allocation should use risk buckets, not raw conviction alone.

Three rules matter most:

1. avoid over-clustering inside IBKR futures calendar sleeves
2. do not overweight strategies that are validated only on short samples
3. reserve capital for paper-only books until execution reality is confirmed

## Proposed global research / paper allocation

### Total sleeve weights

| Sleeve | Target weight |
|---|---:|
| IBKR futures existing core | 30% |
| New IBKR futures discovery sleeves | 20% |
| Binance discovery sleeves | 20% |
| Alpaca US paper sleeves | 20% |
| IBKR EU paper sleeve | 10% |

### Detailed split

| Strategy | Target weight | Comment |
|---|---:|---|
| Existing futures core (`cross_asset_momentum`, `gold_oil_rotation`, `gold_trend_mgc`) | 30% | preserve the proven base without letting it dominate everything |
| `mcl_overnight_mon_trend10` | 10% | strongest new futures discovery sleeve |
| `pre_holiday_drift` | 5% | small but robust calendar edge |
| `long_wed_oc` | 5% | additive calendar sleeve, but keep size modest to avoid MES clustering |
| `btc_asia_mes_leadlag_q70_v80` | 10% | new cross-asset crypto sleeve with strong marginal contribution |
| `crypto_long_short` | 10% | good dispersion candidate, but still needs refreshed WF before live-style sizing |
| `us_sector_ls_40_5` | 12% | best new US neutral-style candidate |
| `us_pead` | 8% | keep in paper allocation, not yet upgraded in this session |
| `eu_relmom_40_3` | 10% | paper-only regional spread sleeve |

Total = 100%

## Interpretation

This proposal is intentionally not aggressive on any single theme.

- Futures remain the core because they are already operationally proven.
- New futures sleeves are capped at 20% total to avoid hidden correlation creep.
- Binance gets 20%, but split between cross-timezone and dispersion rather than pure beta.
- Alpaca gets 20% because the best new US ideas are still paper-stage.
- EU stays at 10% because the signal looks clean, but the book is not live-ready.

## What looks strongest right now

If we rank by practical usefulness rather than raw backtest excitement:

1. `mcl_overnight_mon_trend10`
2. `pre_holiday_drift`
3. `btc_asia_mes_leadlag_q70_v80`
4. `us_sector_ls_40_5`
5. `long_wed_oc`
6. `eu_relmom_40_3`

## Next execution recommendation

The cleanest next step is not to add more idea generation.

It is:

1. paper-track `mcl_overnight_mon_trend10`, `btc_asia_mes_leadlag_q70_v80`, `us_sector_ls_40_5`, `eu_relmom_40_3`
2. refresh WF for `us_pead` and `crypto_long_short`
3. keep `pre_holiday_drift` and `long_wed_oc` as small, explicit add-on sleeves rather than full-size strategies
4. do not allocate anything to PEAD market-neutral until the design changes materially
