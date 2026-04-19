# Strategy V2 Archive

## FX archive (Phase 8 XXL plan, 2026-04-19)

ESMA EU leverage limits + no competitive edge on majors FX in daily ->
ibkr_fx book is `disabled` in `config/live_whitelist.yaml`. Old FX strategies
with no production callers are archived here.

| Strategy                  | Reason |
|---------------------------|--------|
| fx/audjpy_carry           | FX disabled (ESMA), no callers |
| fx/eurgbp_mr              | FX disabled (ESMA), legacy WF rejected |
| fx/eurjpy_carry           | FX disabled (ESMA), no callers |
| fx/eurusd_trend           | FX disabled (ESMA), no callers |
| fx/fx_bollinger_squeeze   | FX disabled (ESMA), no callers |
| fx/fx_eom_flow            | FX disabled (ESMA), no callers |
| fx/fx_london_fix          | FX disabled (ESMA), no callers |
| fx/fx_session_overlap     | FX disabled (ESMA), no callers |
| fx/gbpusd_trend           | FX disabled (ESMA), no callers |

Production FX strategies remain in `strategies_v2/fx/` (carry validated):
- fx_carry_g10_diversified
- fx_carry_momentum_filter
- fx_carry_vol_scaled
- fx_asian_range_breakout
- fx_mean_reversion_hourly
- fx_momentum_breakout
