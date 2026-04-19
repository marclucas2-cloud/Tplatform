# Strategy Discovery Engine - Session 2026-04-18

## Scope

This session executed the `PROMPT_STRATEGY_DISCOVERY_ENGINE` in a strict read-mostly mode:

- no existing file was modified
- no research script with default versioned outputs was run
- only local repo evidence and read-only ad hoc scans were used

The goal here was not to force full backtest execution, because most native research scripts write into existing `docs/research/*` or `output/research/*` paths. Instead, this run completed the `SCOUT` and `QUANT selection` layers with real repo evidence and produced a fresh session artifact.

## What already exists in the repo

The discovery engine is not starting from zero. The repo already contains:

- a live hypothesis register in `docs/research/hypothesis_registry.md`
- a graveyard of rejected ideas in `docs/research/dropped_hypotheses.md`
- scorecards in `output/research/wf_reports/*.json`
- tier-1 validation in `docs/research/wf_reports/INT-A_tier1_validation.md`
- a funnel skeleton in `scripts/research_funnel.py`

In practice, the project has already completed large parts of `TESTER` and `COMBINER` for several candidates.

## Current engine state

### Existing validated or near-validated candidates

From local research outputs already present in the repo:

| Candidate | Current state | Why it matters |
|---|---|---|
| `pre_holiday_drift` | VALIDATED | 5/5 WF, MC P(DD>30%) = 0.0%, low corr |
| `long_wed_oc` | VALIDATED | 4/5 WF, positive marginal contribution |
| `long_mon_oc` | VALIDATED reserve | strong score, but less robust than the two above |
| `basis_carry_funding_gt_5pct` | VALIDATED but blocked | excellent research result, but current broker setup does not support direct live deployment |
| `us_pead` | PROMOTE_PAPER pending WF/MC | low correlation and existing trade history |
| `crypto_long_short` | PROMOTE_PAPER pending WF/MC | fills dispersion / relative-value gap |

### Existing hard drops or blockers

These should not be re-tested in the same form:

- futures crisis alpha via naive short MES proxy
- US cross-sectional mean reversion with naive RSI14 construction
- FX cross-sectional carry under current EU broker constraints
- a third liquidation strategy variant without better data

### Data blocker discovered in this session

- `data/crypto/dominance/btc_dominance.parquet` has 366 rows but only 1 non-null `dominance_pct`
- conclusion: the "crypto regime-reactive via BTC dominance" axis is conceptually valid, but currently blocked by data quality

## New local scouting signals produced in this session

These were read-only checks on existing datasets, not formal backtests.

### Commodity overnight asymmetry

On `data/futures/MCL_1D.parquet`:

- mean overnight return is about `+0.081%`
- mean intraday return is about `+0.006%`
- overnight Sharpe is about `1.56`
- intraday Sharpe is about `0.04`

Interpretation:

- crude oil looks materially more interesting overnight than intraday
- this makes `MCL` a better analogue to an overnight commodity sleeve than a daytime mean-reversion sleeve

### Cross-timezone lead-lag

On `MES_1H_YF2Y.parquet` and `BTCUSDT_1h.parquet`:

- corr(previous US late-session MES, next Asia-session BTC) is about `+0.114`
- BTC Asia-session mean return after weak US late MES is about `-0.296%`
- BTC Asia-session mean return after strong US late MES is about `+0.298%`

Interpretation:

- the effect is not large enough to call validated
- it is large enough to justify a coded candidate with regime filters

### PEAD concentration risk

From `data/us_research/pead_trades.parquet` plus `data/us_stocks/_metadata.csv`:

- the current PEAD trade sample is concentrated in Information Technology and Communication Services
- this supports a second-generation `sector_neutral_pead` design instead of simply pushing raw PEAD into production

## QUANT selection - active queue

The prompt asks QUANT to pick the top 8-10 ideas for the next stage. Based on current repo reality, the active queue is:

| Rank | Candidate | Status | Next gate |
|---|---|---|---|
| 1 | `pre_holiday_drift_mes` | validated existing | combiner + paper promotion path |
| 2 | `long_wed_oc_mes` | validated existing | combiner + paper promotion path |
| 3 | `us_pead_v1` | promote_paper pending WF/MC | tester WF/MC |
| 4 | `crypto_long_short_v1` | promote_paper pending WF/MC | tester WF/MC |
| 5 | `sector_neutral_pead` | new scouted | coder + backtest |
| 6 | `mcl_overnight_drift` | new scouted | coder + backtest |
| 7 | `mes_to_btc_asia_leadlag` | new scouted | coder + backtest |
| 8 | `eu_indices_relative_momentum_ls` | new scouted, paper-only target | coder + paper-only backtest |

## Reserve queue

These are worth keeping close, but not in the first active batch:

| Candidate | Reason |
|---|---|
| `long_mon_oc_mes` | valid, but less robust and potentially redundant versus higher-ranked MES calendar sleeves |
| `basis_carry_funding_gt_5pct` | excellent research result, but blocked by current broker/product constraints |
| `crypto_regime_reactive_dominance_filter` | conceptually attractive, but currently blocked by broken dominance data |

## Why this selection is coherent with the prompt

The prompt emphasized:

- commodities
- US stocks market neutral
- cross-timezone lead-lag
- crypto regime-reactive
- EU country indices long/short

This session maps those axes into repo-reality as follows:

| Prompt axis | Repo-consistent translation |
|---|---|
| commodities | `mcl_overnight_drift` |
| US stocks market neutral | `sector_neutral_pead` |
| cross-timezone lead-lag | `mes_to_btc_asia_leadlag` |
| crypto regime-reactive | blocked for now by broken dominance data |
| EU indices long/short | `eu_indices_relative_momentum_ls` |

## Practical conclusion

The engine does not actually need more brainstorming first. It already has:

- 2+ validated candidates
- 2 near-promotion candidates
- 4 additional scouted candidates worth coding next

So the bottleneck is no longer idea generation. The bottleneck is:

1. coding clean standalone candidates for the new queue
2. running WF/MC on `us_pead` and `crypto_long_short`
3. deciding whether broker constraints permanently disqualify basis carry
4. repairing crypto dominance data before any regime meta-filter work

## Artifacts produced in this session

- `data/research/strategy_discovery_engine_2026-04-18_scout_cards.json`
- `reports/research/strategy_discovery_engine_2026-04-18.md`

No existing project file was modified.
