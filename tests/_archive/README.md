# Test Archive

Tests for strategies moved to `strategies/_archive/` or `strategies_v2/_archive/`.
Kept here (not deleted) so they can be re-enabled if a strategy is restored.

Phase 8 XXL plan (2026-04-19):
- test_strategies_ibkr.py (was tests/test_backtester_v2/) — tested mes_trend,
  brent_lag_futures, audjpy_carry, eurgbp_mr, eurjpy_carry, eurusd_trend,
  gbpusd_trend
- test_fx_eom_strategy.py — tested fx_eom_flow
- test_fx_new_strategies.py — tested fx_asian_range_breakout (kept active),
  fx_bollinger_squeeze (archived)
- test_fx_session_strategies.py — tested fx_london_fix, fx_session_overlap
- test_futures_strategies.py — tested mes_trend, brent_lag_futures, futures_mnq_mr
