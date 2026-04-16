# T1-A — Futures calendar / session effects

**Run date** : 2026-04-16 06:03 UTC
**Instrument** : MES (S&P 500 micro futures)
**Historique** : 2015-01-02 -> 2026-04-09 (2834 trading days)
**Baseline portfolio** : ['cross_asset_momentum', 'gold_oil_rotation', 'gold_trend_mgc']
**RT cost par contrat** : $4.20 (IBKR $0.85/side + 2 ticks slippage)

## Standalone stats

| Variant | Trades | Total PnL $ | Win rate |
|---|---:|---:|---:|
| `long_mon_oc` | 528 | +10,831 | 57.2% |
| `long_tue_oc` | 584 | -144 | 48.5% |
| `long_wed_oc` | 581 | +7,507 | 54.7% |
| `long_thu_oc` | 573 | -5,922 | 51.1% |
| `long_fri_oc` | 568 | +1,373 | 51.8% |
| `monday_reversal` | 528 | -366 | 50.6% |
| `short_fri_oc` | 568 | -6,144 | 44.5% |
| `turn_of_month` | 816 | +4,782 | 52.5% |
| `fomc_day_long` | 90 | +576 | 46.7% |
| `fomc_overnight_drift` | 90 | -650 | 37.8% |
| `pre_holiday_drift` | 106 | +2,653 | 61.3% |

## Scorecards (marginal vs baseline)

| Variant | Verdict | Score | dSharpe | dCAGR | dMaxDD | Corr | Tail | Penalties |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `long_mon_oc` | **PROMOTE_LIVE** | +1.212 | +0.216 | +2.64% | -1.25pp | +0.02 | 0% | - |
| `long_wed_oc` | **PROMOTE_LIVE** | +0.596 | +0.083 | +1.92% | +3.20pp | +0.07 | 3% | - |
| `turn_of_month` | **PROMOTE_LIVE** | +0.463 | +0.007 | +1.32% | +5.49pp | +0.02 | 7% | - |
| `pre_holiday_drift` | **PROMOTE_LIVE** | +0.315 | +0.070 | +0.66% | +1.86pp | +0.01 | 3% | - |
| `fomc_day_long` | **KEEP_FOR_RESEARCH** | +0.189 | -0.002 | +0.22% | +5.04pp | +0.03 | 3% | - |
| `fomc_overnight_drift` | **KEEP_FOR_RESEARCH** | +0.102 | -0.023 | -0.19% | -0.01pp | -0.00 | 3% | - |
| `long_tue_oc` | **KEEP_FOR_RESEARCH** | -0.005 | -0.075 | +0.00% | -1.79pp | -0.03 | 0% | - |
| `monday_reversal` | **DROP** | -0.129 | -0.108 | -0.10% | -12.74pp | +0.02 | 3% | sharpe_worsens=-0.11, maxdd_worsens=-12.7pp, HARD_DROP: sharpe_degrades=-0.11, maxdd_degrades=-12.7pp |
| `long_fri_oc` | **DROP** | -0.249 | -0.049 | +0.43% | -6.96pp | -0.03 | 7% | maxdd_worsens=-7.0pp, HARD_DROP: maxdd_degrades=-7.0pp |
| `short_fri_oc` | **DROP** | -0.698 | -0.290 | -1.90% | -15.92pp | +0.03 | 3% | sharpe_worsens=-0.29, maxdd_worsens=-15.9pp, HARD_DROP: sharpe_degrades=-0.29, maxdd_degrades=-15.9pp |
| `long_thu_oc` | **DROP** | -1.445 | -0.284 | -1.88% | -7.23pp | +0.01 | 7% | sharpe_worsens=-0.28, maxdd_worsens=-7.2pp, HARD_DROP: sharpe_degrades=-0.28, maxdd_degrades=-7.2pp |

## Details par variante

### `long_mon_oc` — PROMOTE_LIVE

- Marginal score : **+1.212**
- Delta Sharpe : +0.216
- Delta CAGR : +2.64%
- Delta MaxDD : -1.25pp
- Delta Calmar : +0.070
- Corr to portfolio : +0.022
- Max corr to individual strat : +0.023
- Tail overlap (worst 30 days) : 0% (0/30)
- Diversification benefit : +0.978
- Capital utilization benefit : +0.161
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=1.08, CAGR=14.95%, MaxDD=-30.23%

### `long_wed_oc` — PROMOTE_LIVE

- Marginal score : **+0.596**
- Delta Sharpe : +0.083
- Delta CAGR : +1.92%
- Delta MaxDD : +3.20pp
- Delta Calmar : +0.127
- Corr to portfolio : +0.065
- Max corr to individual strat : +0.150
- Tail overlap (worst 30 days) : 3% (1/30)
- Diversification benefit : +0.935
- Capital utilization benefit : +0.180
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.947, CAGR=14.23%, MaxDD=-25.78%

### `turn_of_month` — PROMOTE_LIVE

- Marginal score : **+0.463**
- Delta Sharpe : +0.007
- Delta CAGR : +1.32%
- Delta MaxDD : +5.49pp
- Delta Calmar : +0.155
- Corr to portfolio : +0.024
- Max corr to individual strat : +0.029
- Tail overlap (worst 30 days) : 7% (2/30)
- Diversification benefit : +0.976
- Capital utilization benefit : +0.248
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.871, CAGR=13.63%, MaxDD=-23.49%

### `pre_holiday_drift` — PROMOTE_LIVE

- Marginal score : **+0.315**
- Delta Sharpe : +0.070
- Delta CAGR : +0.66%
- Delta MaxDD : +1.86pp
- Delta Calmar : +0.053
- Corr to portfolio : +0.007
- Max corr to individual strat : +0.019
- Tail overlap (worst 30 days) : 3% (1/30)
- Diversification benefit : +0.993
- Capital utilization benefit : +0.031
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.934, CAGR=12.97%, MaxDD=-27.12%

### `fomc_day_long` — KEEP_FOR_RESEARCH

- Marginal score : **+0.189**
- Delta Sharpe : -0.002
- Delta CAGR : +0.22%
- Delta MaxDD : +5.04pp
- Delta Calmar : +0.098
- Corr to portfolio : +0.026
- Max corr to individual strat : +0.058
- Tail overlap (worst 30 days) : 3% (1/30)
- Diversification benefit : +0.974
- Capital utilization benefit : +0.028
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.862, CAGR=12.53%, MaxDD=-23.94%

### `fomc_overnight_drift` — KEEP_FOR_RESEARCH

- Marginal score : **+0.102**
- Delta Sharpe : -0.023
- Delta CAGR : -0.19%
- Delta MaxDD : -0.01pp
- Delta Calmar : -0.007
- Corr to portfolio : -0.003
- Max corr to individual strat : +0.007
- Tail overlap (worst 30 days) : 3% (1/30)
- Diversification benefit : +0.997
- Capital utilization benefit : +0.028
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.841, CAGR=12.12%, MaxDD=-28.99%

### `long_tue_oc` — KEEP_FOR_RESEARCH

- Marginal score : **-0.005**
- Delta Sharpe : -0.075
- Delta CAGR : +0.00%
- Delta MaxDD : -1.79pp
- Delta Calmar : -0.025
- Corr to portfolio : -0.028
- Max corr to individual strat : +0.029
- Tail overlap (worst 30 days) : 0% (0/30)
- Diversification benefit : +0.972
- Capital utilization benefit : +0.177
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.789, CAGR=12.31%, MaxDD=-30.77%

### `monday_reversal` — DROP

- Marginal score : **-0.129**
- Delta Sharpe : -0.108
- Delta CAGR : -0.10%
- Delta MaxDD : -12.74pp
- Delta Calmar : -0.132
- Corr to portfolio : +0.020
- Max corr to individual strat : +0.026
- Tail overlap (worst 30 days) : 3% (1/30)
- Diversification benefit : +0.980
- Capital utilization benefit : +0.161
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.756, CAGR=12.21%, MaxDD=-41.72%
- Penalties : sharpe_worsens=-0.11, maxdd_worsens=-12.7pp, HARD_DROP: sharpe_degrades=-0.11, maxdd_degrades=-12.7pp

### `long_fri_oc` — DROP

- Marginal score : **-0.249**
- Delta Sharpe : -0.049
- Delta CAGR : +0.43%
- Delta MaxDD : -6.96pp
- Delta Calmar : -0.071
- Corr to portfolio : -0.025
- Max corr to individual strat : +0.029
- Tail overlap (worst 30 days) : 7% (2/30)
- Diversification benefit : +0.975
- Capital utilization benefit : +0.172
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.815, CAGR=12.74%, MaxDD=-35.94%
- Penalties : maxdd_worsens=-7.0pp, HARD_DROP: maxdd_degrades=-7.0pp

### `short_fri_oc` — DROP

- Marginal score : **-0.698**
- Delta Sharpe : -0.290
- Delta CAGR : -1.90%
- Delta MaxDD : -15.92pp
- Delta Calmar : -0.193
- Corr to portfolio : +0.025
- Max corr to individual strat : +0.030
- Tail overlap (worst 30 days) : 3% (1/30)
- Diversification benefit : +0.975
- Capital utilization benefit : +0.172
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.574, CAGR=10.41%, MaxDD=-44.9%
- Penalties : sharpe_worsens=-0.29, maxdd_worsens=-15.9pp, HARD_DROP: sharpe_degrades=-0.29, maxdd_degrades=-15.9pp

### `long_thu_oc` — DROP

- Marginal score : **-1.445**
- Delta Sharpe : -0.284
- Delta CAGR : -1.88%
- Delta MaxDD : -7.23pp
- Delta Calmar : -0.137
- Corr to portfolio : +0.013
- Max corr to individual strat : +0.039
- Tail overlap (worst 30 days) : 7% (2/30)
- Diversification benefit : +0.987
- Capital utilization benefit : +0.174
- Days aligned with baseline : 2812
- Baseline metrics : Sharpe=0.864, CAGR=12.31%, MaxDD=-28.98%
- Combined metrics : Sharpe=0.58, CAGR=10.43%, MaxDD=-36.21%
- Penalties : sharpe_worsens=-0.28, maxdd_worsens=-7.2pp, HARD_DROP: sharpe_degrades=-0.28, maxdd_degrades=-7.2pp

## Verdict summary

- **PROMOTE_LIVE** : `long_mon_oc`, `long_wed_oc`, `turn_of_month`, `pre_holiday_drift`
- **KEEP_FOR_RESEARCH** : `fomc_day_long`, `fomc_overnight_drift`, `long_tue_oc`
- **DROP** : `monday_reversal`, `long_fri_oc`, `short_fri_oc`, `long_thu_oc`
