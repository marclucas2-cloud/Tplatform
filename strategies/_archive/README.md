# Strategy Archive

Strategies declared dead-code (no production import path, no test references)
and moved here to reduce cognitive load on operators while preserving git history.

## Convention
- Files moved via `git mv` (history preserved).
- Subdirectories mirror original location:
  - `crypto/`     : moved from `strategies/crypto/`
  - `legacy/`     : moved from `strategies/` top-level
  - `_archive/fx` lives in `strategies_v2/_archive/fx/` (mirrors original v2 layout)

## Restoration
```bash
git mv strategies/_archive/crypto/<file>.py strategies/crypto/<file>.py
```

## Why archived (Phase 8 XXL plan, 2026-04-19)

| Strategy                              | Reason                              |
|---------------------------------------|-------------------------------------|
| crypto/altcoin_relative_strength      | Replaced by strategies_v2/crypto/altcoin_rs |
| crypto/btc_dominance_flight           | WF_PENDING never validated, no production callers |
| crypto/btc_mean_reversion             | Demoted, no callers |
| crypto/dead_cat_bounce                | Demoted, no callers |
| crypto/eth_btc_ratio_breakout         | Demoted, no callers |
| crypto/funding_rate_divergence        | WF_PENDING, no perp data integration |
| crypto/funding_rate_squeeze           | Same as above |
| crypto/monthly_turn_of_month          | Demoted, no callers |
| crypto/stablecoin_supply_flow         | Demoted, no callers |
| brent_lag_futures                     | Replaced by strategies_v2/futures/mcl_brent_lag |
| futures_mes_trend                     | Replaced by strategies_v2/futures/mes_trend |
| futures_mnq_mr                        | Replaced by strategies_v2/futures/mes_trend_mr |
