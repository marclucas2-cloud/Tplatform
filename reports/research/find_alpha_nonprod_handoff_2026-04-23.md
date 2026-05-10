# Find Alpha Non-Prod Handoff - 2026-04-23

## Scope

This handoff stays strictly outside prod-used files.

Allowed perimeter used:
- `scripts/research/*`
- `reports/research/*`
- read-only inspection of registries, runtime scripts, and docs

Not touched here:
- `worker.py`
- runtime cycles
- broker adapters
- registries / whitelist
- any prod scheduling or VPS runtime file

## Executive Truth

- Local `runtime_audit --strict`: FAIL expected on dev workstation because `data/state/ibkr_futures/equity_state.json` is absent locally and futures parquets are stale.
- Local `live_pnl_tracker --summary`: works with `PYTHONIOENCODING=utf-8`, but still reports only 1 day of history.
- Local `pytest`: `3831 passed, 2 failed, 1 skipped`.
- The 2 failing tests are pre-existing and both hit `tests/test_mcl_overnight_mon_trend.py`.
- Research surface is already rich: multiple scripts and reports dated `2026-04-23` exist under [scripts/research](C:\Users\barqu\trading-platform\scripts\research) and [reports/research](C:\Users\barqu\trading-platform\reports\research).

## What Already Exists

The repo already contains a serious first-wave discovery effort for new paper candidates:

1. [decorrelated_strategies_2026-04-23.md](C:\Users\barqu\trading-platform\reports\research\decorrelated_strategies_2026-04-23.md)
2. [decorrelated_strategies_2026-04-23_EXECUTIVE.md](C:\Users\barqu\trading-platform\reports\research\decorrelated_strategies_2026-04-23_EXECUTIVE.md)
3. [new_paper_candidates_2026-04-23_metrics.json](C:\Users\barqu\trading-platform\reports\research\new_paper_candidates_2026-04-23_metrics.json)
4. [new_paper_v3_2026-04-23_metrics.json](C:\Users\barqu\trading-platform\reports\research\new_paper_v3_2026-04-23_metrics.json)
5. [scripts/research/new_paper_candidates_2026_04_23.py](C:\Users\barqu\trading-platform\scripts\research\new_paper_candidates_2026_04_23.py)
6. [scripts/research/decorrelated_candidates_2026_04_23.py](C:\Users\barqu\trading-platform\scripts\research\decorrelated_candidates_2026_04_23.py)
7. [scripts/research/v1_vs_existing_paper_2026_04_23.py](C:\Users\barqu\trading-platform\scripts\research\v1_vs_existing_paper_2026_04_23.py)

This means the correct next move is not "start from scratch". It is "triage the existing research and decide what deserves paper when prod-file edits are allowed".

## Best Current Candidates

### 1. `mes_mr_vix_spike`

Source:
- [decorrelated_strategies_2026-04-23_EXECUTIVE.md](C:\Users\barqu\trading-platform\reports\research\decorrelated_strategies_2026-04-23_EXECUTIVE.md)
- [decorrelated_strategies_2026-04-23.md](C:\Users\barqu\trading-platform\reports\research\decorrelated_strategies_2026-04-23.md)

Why it stands out:
- Sharpe about `0.72`
- WF `5/5` profitable windows
- max DD about `-9.7%`
- roughly `12 trades/year`
- correlation near zero to CAM and GOR
- compares favorably against existing MES paper sleeves

Desk verdict:
- Strongest paper-ready candidate found so far.
- Best fit for "new mechanism + low overlap".

Constraint under current mission:
- Do not wire it to runtime here because that would touch prod-used files.

### 2. `mes_estx50_divergence`

Source:
- [new_paper_candidates_2026-04-23_metrics.json](C:\Users\barqu\trading-platform\reports\research\new_paper_candidates_2026-04-23_metrics.json)
- [scripts/research/new_paper_candidates_2026_04_23.py](C:\Users\barqu\trading-platform\scripts\research\new_paper_candidates_2026_04_23.py)

Observed metrics:
- Sharpe `0.773`
- CAGR about `8.36%`
- DD about `-14.3%`
- WF validated `3/5 profitable`, ratio `0.6`
- Low reported correlation to CAM/GOR proxies

Desk verdict:
- Promising second-tier candidate.
- Not as clean as `mes_mr_vix_spike`, but good enough to stay on shortlist.
- Needs extra scrutiny because the thesis mixes US/EU market structure and may be more sensitive to data alignment and book routing assumptions.

### 3. `v11_mgc_mes_ratio`

Source:
- [new_paper_v3_2026-04-23_metrics.json](C:\Users\barqu\trading-platform\reports\research\new_paper_v3_2026-04-23_metrics.json)

Observed metrics:
- Sharpe `0.362`
- CAGR about `4.34%`
- DD about `-32.18%`
- WF validated `4/5 profitable`
- low correlation to CAM/GOR proxies

Desk verdict:
- Interesting as a decorrelation research path.
- Not paper-worthy yet because DD is too large for the edge quality shown.
- Keep only as `research_complementary`.

## Rejected Or Weak Candidates

### Reject now
- `m2k_weekly_trend`: negative Sharpe, failed WF.
- `mcl_mgc_ratio_z`: invalid risk profile, one OOS window catastrophically bad.
- `mgc_rsi_pullback`: weak edge, low trade count.
- `eth_btc_rotation`: low edge, very large DD.
- `alt_oversold_bounce`: weak and unstable.
- naive `mes_3day_stretch`: negative Sharpe without regime filter.
- naive `mes_mnq_pairs`: no edge.

### Existing paper sleeves that do not look good in simplified re-audit
- `mes_monday_long_oc`
- `mes_wednesday_long_oc`

Important nuance:
- The simplified re-audits do not necessarily replicate the original runtime filters.
- They should not be deleted on this basis alone.
- They do deserve a targeted re-audit before anyone treats them as strong grade-B paper sleeves.

## Non-Prod Conclusions

Under the current "no prod-used files" rule, the honest output is:

- There is already enough evidence to justify **1 strong next paper candidate**: `mes_mr_vix_spike`.
- There is **1 plausible second candidate** worth keeping warm: `mes_estx50_divergence`.
- There is **not yet evidence for 2-4 clean paper promotions** without touching prod-facing files or doing more research.

So the disciplined answer is:
- do not flood the catalog
- keep `mes_mr_vix_spike` as priority one
- keep `mes_estx50_divergence` as priority two
- treat the rest as research-only until they survive tougher review

## Safe Next Steps Without Touching Prod Files

1. Add a second non-prod report comparing `mes_estx50_divergence` against current EU/futures sleeves with cleaner portfolio-level overlap stats.
2. Re-audit `mes_monday_long_oc` and `mes_wednesday_long_oc` against their original logic, not simplified proxies.
3. Download longer non-prod research data for bonds / alternative futures only if a concrete candidate needs it.
4. Consolidate one machine-readable shortlist file so future sessions stop rediscovering the same candidates.

## If Prod-File Edits Become Allowed Later

Priority order would be:

1. Wire `mes_mr_vix_spike` into paper.
2. If paper remains clean, wire `mes_estx50_divergence` next.
3. Do not add any third candidate until one of those two starts producing useful paper history.

## Bottom Line

The research pipeline is not empty. It already found one real candidate and one credible backup.

What is still missing is not "more ideas".
What is still missing is the permission to turn the best of those findings into actual paper runtime wiring.
