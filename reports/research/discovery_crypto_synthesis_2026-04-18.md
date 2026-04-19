# Discovery Crypto Synthesis - 2026-04-18

## Scope

This note consolidates:

- the fresh crypto discovery batch from this session
- the repo's existing crypto walk-forward results
- the specific question: which crypto strategies look robust in both bull and bear markets

This is a research and paper-trading synthesis only. It is not a production deployment instruction by itself.

## Executive view

The main conclusion is simple:

- the strongest bull/bear-robust crypto candidates are cross-sectional and market-neutral
- the best family in this session is beta-adjusted alt relative strength
- the directional or semi-directional BTC sleeves do not look as robust across both regimes

Today, only one family clearly survives the full "bull and bear" filter:

1. `alt_rel_strength_14_60_7`
2. `alt_rel_strength_14_90_7`

Everything else is either:

- useful only in one regime
- additive at the portfolio level but not truly bull/bear robust
- blocked by data quality or implementation gaps

## What survives the bull/bear filter

Primary proof file:

- [INT-D_crypto_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-D_crypto_batch.md)

Research family details:

- [T4A-02_crypto_relative_strength.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-02_crypto_relative_strength.md)

### VALIDATED

| Strategy | Status | Why it survives |
|---|---|---|
| `alt_rel_strength_14_60_7` | VALIDATED | Sharpe `+1.11`, MaxDD `-7.8%`, WF `3/5`, MC `0.5%`, bull `+$3,591`, bear `+$515` |
| `alt_rel_strength_14_90_7` | VALIDATED | Sharpe `+0.44`, MaxDD `-7.7%`, WF `3/5`, MC `4.3%`, bull `+$1,335`, bear `+$221` |

Interpretation:

- `alt_rel_strength_14_60_7` is the clear winner
- `alt_rel_strength_14_90_7` is weaker, but still passes the same robustness gate
- both are low-correlation additions versus the current baseline portfolio
- both are much closer to a real "all market" crypto sleeve than the directional BTC ideas

## Useful, but not all-market robust enough

### Paper-only / conditional

| Strategy | Current view | Why not higher |
|---|---|---|
| `range_bb_harvest_bb30` | paper-only / conditional | additive vs baseline, but bull PnL is negative and WF only `2/5` |
| `range_bb_harvest_rebuild` | keep for research | bear-friendly, but fails MC and loses money in bull |
| `crypto_ls_20_7_3` | paper-only benchmark | positive standalone and good marginal score, but loses money in bear |
| `crypto_ls_20_7_2` | keep for research | same issue as above, weaker than `20_7_3` |

Key nuance:

- the range family looks like a bear-chop sleeve, not a true all-market sleeve
- the simple `crypto_ls_*` family looks like a bull-dispersion sleeve, not a true all-market sleeve

## Existing repo crypto results that should not be ignored

Primary proof file:

- [data/crypto/wf_results.json](C:/Users/barqu/trading-platform/data/crypto/wf_results.json)

### Fresh repo re-WF negatives

| Strategy | Repo verdict | Comment |
|---|---|---|
| `btc_eth_dual_momentum` | REJECTED | only `1/5` profitable windows |
| `vol_breakout` | INSUFFICIENT_TRADES | no usable trade count in fresh re-WF |
| `bb_mr_short` | REJECTED | very weak OOS profile |
| `btc_mean_reversion` | REJECTED | unstable and not robust enough |
| `vol_expansion_bear` | REJECTED | too narrow and too unstable |

### Existing repo result that needs reconciliation

| Strategy | Repo verdict | Session view | Implication |
|---|---|---|---|
| `range_bb_harvest` | VALIDATED in repo re-WF | only conditional in this stricter rebuild | do not move straight to prod without reconciling the two backtest engines |

### Existing repo strategies still blocked for real promotion work

| Strategy | Blocker |
|---|---|
| `liquidation_momentum` | needs kwargs-aware simulator / re-WF |
| `btc_dominance_v2` | needs kwargs-aware simulator, and dominance data is broken |

## Data and infra blockers

### BTC dominance data is broken

File:

- [btc_dominance.parquet](C:/Users/barqu/trading-platform/data/crypto/dominance/btc_dominance.parquet)

Current issue:

- `366` rows
- only `1` non-null `dominance_pct`

Consequence:

- do not spend time productionizing dominance-reactive crypto sleeves until this dataset is repaired

### Borrow history is too short for a serious multi-alt production filter

Files:

- [BTC_borrow_rates.parquet](C:/Users/barqu/trading-platform/data/crypto/borrow_rates/BTC_borrow_rates.parquet)
- [ETH_borrow_rates.parquet](C:/Users/barqu/trading-platform/data/crypto/borrow_rates/ETH_borrow_rates.parquet)

Current issue:

- the saved borrow history is only from late February 2026 to late March 2026
- that is not enough to productionize a realistic multi-alt borrow filter on its own

Consequence:

- relative-strength production work should start with conservative borrow assumptions and explicit guardrails
- a proper borrow-data pipeline is still needed before aggressive live sizing

## Correlation and redundancy

The new candidate family is not fully diversified internally.

Pairwise daily PnL correlations from this session:

- `alt_rel_strength_14_60_7` vs `alt_rel_strength_14_90_7` : `+0.657`
- `crypto_ls_20_7_3` vs `alt_rel_strength_14_60_7` : `+0.651`
- `range_bb_harvest_bb30` vs the cross-sectional sleeves : around `-0.05`

Interpretation:

- the two `alt_rel_strength` variants are related, not independent
- `crypto_ls_20_7_3` is also close enough to that family that it should not receive a full parallel allocation
- `range_bb_harvest_bb30` is more decorrelated, but it is not robust enough across bull/bear yet

## Recommended crypto allocation

The honest conclusion is that the crypto sleeve should not be spread evenly across many ideas right now.

There is one strong family, one weaker backup variant, and a few research sleeves.

### Production-first allocation for the new crypto sleeve

This is the recommended starting point if the goal is "only what currently looks bull/bear robust enough":

| Sleeve | Target weight |
|---|---:|
| `alt_rel_strength_14_60_7` | 70% |
| `alt_rel_strength_14_90_7` | 15% |
| Reserve / unallocated until more robust sleeves exist | 15% |

Rationale:

- concentrate on the only family that actually passed bull/bear validation
- keep the second variant small because it is related, not independent
- keep dry powder instead of forcing weak sleeves into production

### Research-shadow allocation

This is the paper-only shadow book to keep learning without pretending these sleeves are production-ready:

| Sleeve | Target weight |
|---|---:|
| `range_bb_harvest_bb30` | 40% |
| `crypto_ls_20_7_3` | 40% |
| `crypto_ls_20_7_2` | 20% |

Interpretation:

- these are useful research sleeves
- they should remain paper / shadow until they clear a stricter all-market gate

## Recommended next step

The next clean move is not to search for ten more crypto ideas.

It is:

1. productionize `alt_rel_strength_14_60_7` first
2. keep `alt_rel_strength_14_90_7` as a lower-priority backup variant
3. reconcile the `range_bb_harvest` engine mismatch before any promotion
4. do not productionize dominance-reactive sleeves until the dataset is repaired
5. do not add fresh capital to the rejected legacy crypto sleeves until they are re-reviewed
