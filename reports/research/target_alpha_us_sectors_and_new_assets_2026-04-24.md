# Target Alpha Research - US Sectors And New Assets - 2026-04-24

## Scope

This mission targeted the **future desk**, not today's runtime.

Touched only non-prod surfaces:
- `scripts/research/*`
- `reports/research/*`
- `data/research/*`
- `docs/research/*`
- research tests

Not touched:
- `worker.py`
- `core/worker/*`
- broker adapters
- registries / prod whitelist
- runtime scripts

## Executive Verdict

### Best US sectors idea
- **`stock_sector_ls_40_5`**
- Equal-weight long strongest sector basket / short weakest sector basket, 40-day lookback, 5-day hold.
- Score against current desk baseline: **0.190**
- Standalone: Sharpe **0.392**, max DD **-2.15%**, WF **5/5 profitable**
- Strategic reading: **the sector relative-value idea is real**, but the edge is moderate, not explosive.

### Best new-assets idea
- **`macro_top1_rotation`**
- Long-only monthly rotation among `SPY`, `TLT`, `GLD`, `DBC`, `UUP`, `IEF`, `HYG`, `QQQ`.
- Score against current desk baseline: **0.199**
- Standalone: Sharpe **0.676**, max DD **-5.94%**, WF **4/5 profitable**
- Strategic reading: **this is the cleanest target-alpha result of the whole batch**.

### Best tradable implementation candidate
- **`pair_xle_xlk_ratio`**
- Pair ETF sector spread on `XLE` vs `XLK`
- Score: **0.175**
- Standalone: Sharpe **0.532**, max DD **-3.53%**, WF **5/5 profitable**
- Strategic reading: lower alpha than the best macro result, but more interpretable than broad stock L/S and easier to operationalize than synthetic stock-sector baskets.

### Pire illusion rejetée
- **US sector ETF long/short momentum naïf**
- After anti-lookahead correction:
  - `etf_sector_ls_mom_20_5`: Sharpe **-0.467**
  - `etf_sector_ls_mom_40_5`: Sharpe **-0.386**
- Conclusion: the obvious “long strongest ETF sector / short weakest ETF sector” story does **not** survive realistic timing.

### Alpaca live becomes more defensible?
- **No, not yet.**
- There is enough evidence to justify **continued paper research** on US sectors and macro targets.
- There is **not** enough evidence yet to justify an Alpaca live push on ROC alone.

## Minimal Truth Snapshot

- The desk today is still primarily a futures / crypto desk.
- Alpaca is still not the next live priority because the recoverable ROC from US ideas is **not yet overwhelming**.
- This mission therefore asked a different question:
  - is there enough future alpha in US sectors or adjacent assets to justify a future build track?

Answer:
- **Yes for research and paper targeting**
- **No for immediate live commitment**

## Data Used

### Existing local data
- US single-name parquet universe from [data/us_stocks](C:\Users\barqu\trading-platform\data\us_stocks)
- Sector metadata from [data/us_stocks/_metadata.csv](C:\Users\barqu\trading-platform\data\us_stocks\_metadata.csv)
- Existing desk baseline from [data/research/portfolio_baseline_timeseries.parquet](C:\Users\barqu\trading-platform\data\research\portfolio_baseline_timeseries.parquet)

### Downloaded for this mission
- Non-prod ETF/macros cache created at:
  - [target_alpha_us_sectors_2026_04_24_prices.parquet](C:\Users\barqu\trading-platform\data\research\target_alpha_us_sectors_2026_04_24_prices.parquet)

Universe downloaded:
- Sector ETFs: `XLB`, `XLC`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`, `XLU`, `XLV`, `XLY`, `SPY`
- Macro / cross-asset ETFs: `TLT`, `GLD`, `DBC`, `UUP`, `IEF`, `HYG`, `QQQ`

Data range:
- **2018-01-02 -> 2026-04-23**
- **2088 daily rows**

## Initial Idea Funnel

### 14 initial ideas
1. Stock-sector basket long/short momentum fast
2. Stock-sector basket long/short momentum slow
3. ETF sector long/short momentum fast
4. ETF sector long/short momentum slow
5. ETF sector long/short short-term mean reversion
6. ETF sector top-1 long-only rotation
7. Defensive vs cyclical regime spread
8. `XLE` vs `XLK` pair spread
9. `XLF` vs `XLU` pair spread
10. Macro top-1 rotation
11. Macro risk-on / risk-off switch
12. Broad US cross-sectional mean reversion
13. Sector dispersion overlay
14. Rates/commodities proxy rotation

### 11 serious candidates actually tested
- `stock_sector_ls_20_5`
- `stock_sector_ls_40_5`
- `etf_sector_ls_mom_20_5`
- `etf_sector_ls_mom_40_5`
- `etf_sector_ls_rev_5_3`
- `etf_sector_top1_long_40_5`
- `defensive_vs_cyclical_regime`
- `pair_xle_xlk_ratio`
- `pair_xlf_xlu_ratio`
- `macro_top1_rotation`
- `macro_risk_switch`

### 3 dropped before full scoring
- sector dispersion overlay
  - too close to sector momentum family
- rates/commodities proxy rotation
  - absorbed into macro rotation universe
- broad stock cross-sectional L/S expansion
  - existing local MR evidence already strongly negative

## Results Table

| Candidate | Family | Sharpe | Max DD | WF | Score | Verdict |
|---|---|---:|---:|---:|---:|---|
| `macro_top1_rotation` | new assets | 0.676 | -5.94% | 0.80 | 0.199 | PROMOTE_PAPER |
| `stock_sector_ls_40_5` | US sectors | 0.392 | -2.15% | 1.00 | 0.190 | PROMOTE_PAPER |
| `stock_sector_ls_20_5` | US sectors | 0.496 | -4.42% | 0.80 | 0.188 | PROMOTE_PAPER |
| `pair_xle_xlk_ratio` | sector pair ETF | 0.532 | -3.53% | 1.00 | 0.175 | PROMOTE_PAPER |
| `macro_risk_switch` | new assets | 0.672 | -7.21% | 0.80 | 0.172 | PROMOTE_PAPER |
| `etf_sector_top1_long_40_5` | sector ETF long-only | 0.364 | -8.38% | 1.00 | 0.167 | PROMOTE_PAPER |
| `pair_xlf_xlu_ratio` | sector pair ETF | -0.402 | - | 0.40 | 0.165 | KEEP_FOR_RESEARCH |
| `defensive_vs_cyclical_regime` | sector regime spread | -0.171 | - | 0.40 | 0.164 | KEEP_FOR_RESEARCH |
| `etf_sector_ls_mom_40_5` | sector ETF L/S | -0.386 | -12.48% | 0.20 | 0.153 | KEEP_FOR_RESEARCH |
| `etf_sector_ls_mom_20_5` | sector ETF L/S | -0.467 | -12.94% | 0.20 | 0.151 | KEEP_FOR_RESEARCH |
| `etf_sector_ls_rev_5_3` | sector ETF L/S MR | -0.968 | -22.71% | 0.20 | 0.130 | KEEP_FOR_RESEARCH |

## What This Actually Means

### 1. US sectors are worth a research track
Yes.

The evidence is not huge-alpha, but it is good enough to justify ongoing paper targeting:
- sector relative value is not dead
- sector selection and sector spreads can help the desk
- stock-sector baskets are more promising than naïve ETF long/short momentum

### 2. Broad US stock long/short is not the answer right now
No.

Existing broad cross-sectional MR on US stocks was already ugly:
- standalone total PnL about **-$17,332**
- Sharpe about **-2.97**
- verdict **DROP**

So the right interpretation is:
- **do not open a broad US stock L/S build track**
- **do keep working on sectors / pairs / simpler constructions**

### 3. Pair sector ETFs are more interesting than broad stock L/S
Yes.

Especially:
- `pair_xle_xlk_ratio`

Why:
- easier to explain
- easier to execute
- lower operational complexity
- lower borrow fiction risk than large single-name L/S
- survives anti-lookahead with respectable though not spectacular metrics

### 4. New-assets research is actually more compelling than Alpaca US right now
Yes.

The strongest result in this whole pass is not a US stock strategy.
It is:
- `macro_top1_rotation`

That matters because it says:
- the next real target-alpha opportunity may come from **cross-asset ETF rotation**
- not from rushing into Alpaca live

### 5. The naive US sectors story is weaker than it looks
This is the most important falsification from the batch.

Before anti-lookahead correction, the sector ETF long/short momentum family looked much better.
After fixing timing:
- both fast and slow versions turned negative
- mean-reversion sector ETF L/S was worse

So the honest conclusion is:
- the obvious “long best sector, short worst sector ETF” narrative is **not strong enough**

## Final Ranking

### Top target candidates
1. **`macro_top1_rotation`**
2. **`stock_sector_ls_40_5`**
3. **`pair_xle_xlk_ratio`**
4. **`macro_risk_switch`**
5. **`etf_sector_top1_long_40_5`**

### Best US-sectors-only target
1. `stock_sector_ls_40_5`
2. `pair_xle_xlk_ratio`
3. `etf_sector_top1_long_40_5`

### Keep-for-research only
- `defensive_vs_cyclical_regime`
- `pair_xlf_xlu_ratio`

### Reject as future build priorities
- `etf_sector_ls_mom_20_5`
- `etf_sector_ls_mom_40_5`
- `etf_sector_ls_rev_5_3`
- broad US stock cross-sectional L/S expansion

## Strategic Answer To The Original Question

### Should we search new assets?
**Yes.**

This mission says new-assets research is not a distraction.
It may be the strongest future alpha track available right now.

### Should we search US stocks / sectors / long-short?
**Yes, but narrowly.**

The right lane is:
- sector baskets
- sector pairs
- long-only sector rotation

The wrong lane is:
- broad stock L/S complexity
- borrow-heavy single-name constructions

### Does this justify Alpaca live now?
**No.**

What it justifies is:
- a focused **paper research / paper runtime** preparation track
- not an immediate capital / live decision

## Recommended Next Step

If a future paper wiring window opens, the cleanest order is:

1. `macro_top1_rotation`
2. `stock_sector_ls_40_5`
3. `pair_xle_xlk_ratio`

If the target must stay US-only:

1. `stock_sector_ls_40_5`
2. `pair_xle_xlk_ratio`
3. `etf_sector_top1_long_40_5`

And if the question is “what should not get more attention right now?”:

- broad stock L/S
- naive sector ETF momentum L/S
- more idea generation before one of the top 3 is pushed further in paper

## Files Produced

### New non-prod artifacts
- [target_alpha_us_sectors_and_new_assets_2026_04_24.py](C:\Users\barqu\trading-platform\scripts\research\target_alpha_us_sectors_and_new_assets_2026_04_24.py)
- [target_alpha_us_sectors_2026_04_24_prices.parquet](C:\Users\barqu\trading-platform\data\research\target_alpha_us_sectors_2026_04_24_prices.parquet)
- [target_alpha_us_sectors_and_new_assets_2026-04-24_metrics.json](C:\Users\barqu\trading-platform\reports\research\target_alpha_us_sectors_and_new_assets_2026-04-24_metrics.json)
- [target_alpha_us_sectors_and_new_assets_2026-04-24_returns.parquet](C:\Users\barqu\trading-platform\reports\research\target_alpha_us_sectors_and_new_assets_2026-04-24_returns.parquet)
- [test_research_target_alpha_2026_04_24.py](C:\Users\barqu\trading-platform\tests\test_research_target_alpha_2026_04_24.py)

### Refreshed existing non-prod reports
- [T3B-01_us_sector_ls.md](C:\Users\barqu\trading-platform\docs\research\wf_reports\T3B-01_us_sector_ls.md)
- [T2-04_us_cross_sectional_mr.md](C:\Users\barqu\trading-platform\docs\research\wf_reports\T2-04_us_cross_sectional_mr.md)

## Bottom Line

The target desk should keep hunting beyond the current futures/crypto runtime.

But this batch does **not** say:
- "go Alpaca live"
- "build broad US stock L/S"

It says:
- **new-assets research is worth pursuing**
- **US sector relative value is worth pursuing**
- **broad stock L/S is not worth the operational pain right now**
