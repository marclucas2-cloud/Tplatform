# Claude Crypto Prod Handoff - 2026-04-18

## Mission

This document is an action-oriented handoff for moving the best crypto research sleeves toward a clean `paper -> prod` path in this repo.

The focus is narrow:

- crypto only
- sleeves that can survive both bull and bear markets
- no blind promotion of every promising backtest

Claude's job is not to restart idea generation. Claude's job is to productionize only the sleeves that already look strong enough, while respecting the repo's governance and broker constraints.

## Non-negotiable rules

Claude must respect first:

- `no lookahead`
- real costs
- walk-forward mandatory
- paper first
- stop-loss mandatory on live orders
- `_authorized_by` mandatory on all orders
- integer qty on shorts, no notional shorts

Crypto-specific reminder:

- live-allowed book today: `binance_crypto`
- any live path still has to respect the Binance runtime, risk and governance stack already in place

## Mandatory reading before editing

Claude should read these first:

- [discovery_crypto_synthesis_2026-04-18.md](C:/Users/barqu/trading-platform/reports/research/discovery_crypto_synthesis_2026-04-18.md)
- [T4A-02_crypto_relative_strength.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-02_crypto_relative_strength.md)
- [INT-D_crypto_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-D_crypto_batch.md)
- [T4A-01_crypto_range_harvest.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-01_crypto_range_harvest.md)
- [wf_results.json](C:/Users/barqu/trading-platform/data/crypto/wf_results.json)
- [altcoin_relative_strength.py](C:/Users/barqu/trading-platform/strategies/crypto/altcoin_relative_strength.py)
- [range_bb_harvest.py](C:/Users/barqu/trading-platform/strategies/crypto/range_bb_harvest.py)
- [config/books_registry.yaml](C:/Users/barqu/trading-platform/config/books_registry.yaml)
- [config/live_whitelist.yaml](C:/Users/barqu/trading-platform/config/live_whitelist.yaml)
- [core/governance/pre_order_guard.py](C:/Users/barqu/trading-platform/core/governance/pre_order_guard.py)
- [worker.py](C:/Users/barqu/trading-platform/worker.py)

Claude should also inspect the existing crypto integration patterns in:

- [strategies/crypto](C:/Users/barqu/trading-platform/strategies/crypto)
- [core/crypto](C:/Users/barqu/trading-platform/core/crypto)
- [core/broker](C:/Users/barqu/trading-platform/core/broker)
- [tests](C:/Users/barqu/trading-platform/tests)

## Priority order

### Priority A - the only real bull/bear production candidate

#### 1. `alt_rel_strength_14_60_7`

- Book target: `binance_crypto`
- Status: **VALIDATED**
- Proof:
  - [T4A-02_crypto_relative_strength.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-02_crypto_relative_strength.md)
  - [INT-D_crypto_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-D_crypto_batch.md)
- Summary:
  - standalone Sharpe `+1.11`
  - MaxDD `-7.8%`
  - WF `3/5`
  - MC `P(DD>30%) = 0.5%`
  - bull regime `+$3,591`
  - bear regime `+$515`
- Interpretation:
  - this is the strongest crypto candidate from the session
  - it is the closest thing to a true bull/bear-robust crypto sleeve
  - it should be the first crypto research candidate to receive real productionization work
- Path:
  - runtime implementation
  - config
  - tests
  - `paper`
  - then `live_probation` if execution and governance checks are clean

### Priority B - backup variant, not an equal-sized twin

#### 2. `alt_rel_strength_14_90_7`

- Book target: `binance_crypto`
- Status: **VALIDATED**
- Proof:
  - [T4A-02_crypto_relative_strength.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-02_crypto_relative_strength.md)
  - [INT-D_crypto_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-D_crypto_batch.md)
- Summary:
  - standalone Sharpe `+0.44`
  - MaxDD `-7.7%`
  - WF `3/5`
  - MC `P(DD>30%) = 4.3%`
  - bull regime `+$1,335`
  - bear regime `+$221`
- Important nuance:
  - this is a backup variant, not a separate independent family
  - daily PnL correlation vs `alt_rel_strength_14_60_7` is about `+0.657`
- Path:
  - implement only if Claude wants a lower-turnover backup parameterization
  - do not allocate it as a full parallel sleeve without a fresh redundancy check

### Priority C - paper-only, not "all market" robust enough yet

#### 3. `range_bb_harvest_bb30`

- Book target: `binance_crypto`
- Status: **paper-only / conditional**
- Proof:
  - [T4A-01_crypto_range_harvest.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-01_crypto_range_harvest.md)
  - [INT-D_crypto_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-D_crypto_batch.md)
- Why not higher:
  - bull regime PnL is negative
  - WF only `2/5`
  - the repo's existing `range_bb_harvest` re-WF says `VALIDATED`, but this session's stricter rebuild does not clear the full bull/bear gate
- Path:
  - reconcile the engine mismatch first
  - keep in `paper_only` until the discrepancy is explained

#### 4. `crypto_ls_20_7_3`

- Book target: `binance_crypto`
- Status: **paper-only benchmark**
- Proof:
  - [T4A-02_crypto_relative_strength.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/T4A-02_crypto_relative_strength.md)
  - [INT-D_crypto_batch.md](C:/Users/barqu/trading-platform/docs/research/wf_reports/INT-D_crypto_batch.md)
- Why not higher:
  - positive in bull, negative in bear
  - WF only `2/5`
  - correlated enough with the `alt_rel_strength` family that it is not worth force-promoting
- Path:
  - keep as paper benchmark
  - do not prioritize productionization ahead of `alt_rel_strength_14_60_7`

## Do not promote in prod right now

Claude should not push these into production in their current form:

- `btc_eth_dual_momentum`
  - fresh repo re-WF: `REJECTED`
- `vol_breakout`
  - fresh repo re-WF: `INSUFFICIENT_TRADES`
- `bb_mr_short`
  - fresh repo re-WF: `REJECTED`
- `btc_mean_reversion`
  - fresh repo re-WF: `REJECTED`
- `vol_expansion_bear`
  - fresh repo re-WF: `REJECTED`

Claude should also not spend real production effort yet on:

- `btc_dominance_v2`
  - dominance dataset is broken
- `liquidation_momentum`
  - still needs kwargs-aware re-WF tooling

## Data blockers Claude must respect

### Dominance-reactive sleeves are blocked

File:

- [btc_dominance.parquet](C:/Users/barqu/trading-platform/data/crypto/dominance/btc_dominance.parquet)

Current state:

- `366` rows
- only `1` non-null `dominance_pct`

Implication:

- do not attempt to productionize dominance-based crypto logic until the dataset is repaired first

### Borrow data is not mature enough for aggressive live sizing

Files:

- [BTC_borrow_rates.parquet](C:/Users/barqu/trading-platform/data/crypto/borrow_rates/BTC_borrow_rates.parquet)
- [ETH_borrow_rates.parquet](C:/Users/barqu/trading-platform/data/crypto/borrow_rates/ETH_borrow_rates.parquet)

Current state:

- saved history only covers late February 2026 to late March 2026

Implication:

- production logic must use conservative borrow assumptions and hard guardrails
- aggressive multi-alt short sizing is not justified by the current borrow-data depth

## Recommended work order for Claude

### Phase 1 - productionize the best family

1. implement `alt_rel_strength_14_60_7` cleanly in the crypto runtime
2. add tests for ranking, rebalance timing, borrow-safe shorts and stop-loss behavior
3. wire paper mode first
4. only then consider `live_probation`

### Phase 2 - optional backup variant

5. decide whether `alt_rel_strength_14_90_7` should exist as:
   - an alternate parameter set, or
   - a shadow benchmark only
6. do not size it as if it were independent from `14_60_7`

### Phase 3 - paper-only research cleanup

7. reconcile the existing `range_bb_harvest` repo validation with the stricter rebuild from this session
8. keep `crypto_ls_20_7_3` as a paper benchmark, not a prod priority
9. defer dominance and liquidation sleeves until data / simulator blockers are removed

## Files Claude will probably need to touch

Depending on the repo's actual integration pattern, Claude will likely need to work in:

- `strategies/crypto/`
- `config/strategies/`
- `core/crypto/`
- `worker.py`
- `tests/`
- `config/live_whitelist.yaml` only if Claude explicitly decides a sleeve is truly ready for live probation

Claude should avoid ad hoc special cases in `worker.py` if a clean declarative strategy pattern already exists.

## Required implementation outputs

For any sleeve Claude promotes, Claude should produce:

1. a runtime implementation
2. dedicated config
3. tests
4. a cost model consistent with Binance reality
5. paper activation before any live-like activation
6. sizing that respects the allocation guidance below

And for any live activation:

1. `_authorized_by` on every order
2. stop-loss attached or enforced by the execution path
3. full compatibility with `pre_order_guard`
4. no bypass around book status or live whitelist governance

## Suggested crypto allocation

This should be treated as a starting point, not a law.

### Production-first crypto discovery allocation

| Sleeve | Target weight |
|---|---:|
| `alt_rel_strength_14_60_7` | 70% |
| `alt_rel_strength_14_90_7` | 15% |
| Reserve / unallocated until more robust sleeves exist | 15% |

Interpretation:

- the production path should concentrate on the only family that clearly passed the bull/bear gate
- the second variant stays small because it is related, not independent
- keeping reserve capital is better than forcing weak sleeves into production

### Research-shadow allocation

| Sleeve | Target weight |
|---|---:|
| `range_bb_harvest_bb30` | 40% |
| `crypto_ls_20_7_3` | 40% |
| `crypto_ls_20_7_2` | 20% |

Interpretation:

- this is for paper tracking and learning
- not for immediate production rollout

## Minimal executive summary

If Claude wants the shortest possible read:

1. productionize `alt_rel_strength_14_60_7` first
2. keep `alt_rel_strength_14_90_7` as a smaller backup or shadow variant
3. leave `range_bb_harvest_bb30` and `crypto_ls_20_7_3` in paper-only
4. do not waste time on dominance-reactive or already rejected crypto sleeves until the blockers are fixed
