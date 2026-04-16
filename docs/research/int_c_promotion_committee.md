# INT-C — Promotion committee — Tier 1 / Tier 2 campaign

**Date** : 2026-04-16
**Auditeur** : Session autonome Claude (T1-A → INT-B)
**Scope** : Decision finale sur les 17 candidates Tier 1/Tier 2 + 4 NEEDS_WORK

## 1. Methodologie du committee

Pour chaque candidate ayant produit un scorecard marginal vs baseline 7-strat :
1. Verdict marginal score engine (`portfolio_marginal_score.py`)
2. Validation WF 5 windows / MC 1000 sims (`int_a_wf_mc_stress.py`)
3. Gate non-negociable pour LIVE :
   - Delta Sharpe > 0
   - Delta MaxDD > -2pp
   - Corr portfolio < 0.50 (strict pour live, 0.70 pour paper)
   - >= 3/5 windows OOS Sharpe > 0.2
   - P(DD > 30%) < 30% (Monte Carlo)
   - 30 jours paper minimum avant live_probation

## 2. Matrice finale

| # | Candidate | Session | Verdict score | WF/MC | Corr | Final verdict |
|---|---|---|---|---|---|---|
| 1 | `long_mon_oc` | T1-A | PROMOTE_LIVE | VALIDATED | 0.02 | **PAPER → LIVE_PROBATION (30d paper first)** |
| 2 | `long_wed_oc` | T1-A | PROMOTE_LIVE | VALIDATED | 0.07 | **PAPER → LIVE_PROBATION (30d paper first)** |
| 3 | `pre_holiday_drift` | T1-A | PROMOTE_LIVE | VALIDATED | 0.01 | **PAPER → LIVE_PROBATION (30d paper first)** |
| 4 | `turn_of_month` | T1-A | PROMOTE_LIVE | NEEDS_WORK (MC FAIL) | 0.02 | DOWNGRADE → `paper_only` |
| 5 | `mes_fade_2.0atr` | T1-B | PROMOTE_PAPER | NEEDS_WORK (WF FAIL 1/5) | 0.04 | DOWNGRADE → `keep_for_research` |
| 6 | `mes_fade_2.5atr` | T1-B | PROMOTE_PAPER | NEEDS_WORK (WF FAIL 2/5) | 0.01 | DOWNGRADE → `keep_for_research` |
| 7 | `mes_fade_3.0atr` | T1-B | PROMOTE_PAPER | not tested (too few trades) | 0.01 | `keep_for_research` |
| 8 | `mes_fade_2atr_trend` | T1-B | PROMOTE_PAPER | NEEDS_WORK (WF FAIL 1/5) | -0.01 | DOWNGRADE → `keep_for_research` |
| 9 | `basis_carry_always` | T1-C | PROMOTE_PAPER | VALIDATED (proxy funding!) | 0.10 | **PAPER_PROBATION + data reelle first** |
| 10 | `basis_carry_bullish` | T1-C | PROMOTE_PAPER | not tested | 0.10 | `keep_for_research` (doublon funding_gt_5) |
| 11 | `basis_carry_funding_gt_5pct` | T1-C | PROMOTE_PAPER | VALIDATED (proxy funding!) | 0.11 | **PAPER_PROBATION + data reelle first** |
| 12 | `basis_carry_funding_gt_10pct` | T1-C | PROMOTE_PAPER | not tested | 0.10 | `keep_for_research` (doublon) |
| 13 | `us_pead` | T1-D | PROMOTE_PAPER | not WF-tested (data externe) | 0.02 | **PAPER_ONLY (Alpaca doctrine)** |
| 14 | `crypto_long_short` | T1-E | PROMOTE_LIVE | caveat 2Y data | 0.12 | **PAPER_ONLY** — 5Y data needed first |
| 15 | T2-A (6 variants) | T2-A | 6x DROP | - | - | ALL DROP (cf. dropped_hypotheses) |
| 16 | `eu_sector_rotation` | T2-C | KEEP_FOR_RESEARCH | not WF-tested | -0.04 | `keep_for_research` |
| 17 | `us_cross_sectional_mr` | T2-D | DROP | - | - | DROP (HFT arb) |
| 18 | T2-B liquidation | T2-B | data missing | - | - | `research_backlog` (doublon existing) |
| 19 | T2-E FX | T2-E | ESMA blocked | - | - | `dropped_regulatory` |

## 3. Decisions de promotion

### 3.1 A ajouter a `config/live_whitelist.yaml` en `paper_only` (3 candidates)

**Candidates 30-day paper probation required before live_probation** :

```yaml
# IBKR futures — paper probation from 2026-04-16
- strategy_id: mes_monday_long_oc
  book: ibkr_futures
  status: paper_only
  runtime_entrypoint: "scripts/research/backtest_futures_calendar.py:variant_dow_long dow=0"
  wf_source: docs/research/wf_reports/INT-A_tier1_validation.md
  sizing_policy: fixed_1_contract
  max_risk_usd: 300  # 1 MES contract, 60 ticks stop
  kill_criteria:
    drawdown_absolute: -10%
    drawdown_rolling_90d: -8%
    divergence_vs_backtest: 2x_std
    correlation_drift: ">0.70"
  notes: "T1-A validated. IS Sharpe 0.71, WF 3/5 OOS PASS, MC P(DD>30%) 9.8%. Paper 30d min before live."

- strategy_id: mes_wednesday_long_oc
  book: ibkr_futures
  status: paper_only
  (similar structure)

- strategy_id: mes_pre_holiday_long
  book: ibkr_futures
  status: paper_only
  (similar structure, WF 5/5 OOS PASS, MC P(DD>30%)=0%)
```

### 3.2 A **retester** avec funding reel (2 candidates)

- `basis_carry_always` + `basis_carry_funding_gt_5pct` : funding proxy utilise,
  verdict VALIDATED mais **necessite data historique reelle** (Binance API
  `/fapi/v1/fundingRate`). Session T1-C' planifiee.

### 3.3 A garder en `paper_only` pur (2 candidates)

- `us_pead` : doctrine Alpaca = paper only. PROMOTE_PAPER acquis mais pas de
  ligne live Alpaca actuellement.
- `crypto_long_short` : data 2Y insuffisante, attendre 5Y d'historique alts.

### 3.4 A dropper ou garder en research (le reste)

- 4 `NEEDS_WORK` Tier 1 (turn_of_month + 3 mes_fade) : downgrade `keep_for_research`
- 6 T2-A crisis alpha : tous DROP, doc dans `dropped_hypotheses.md`
- T2-D cross-sectional MR : DROP confirmé
- T2-B, T2-E : research backlog / regulatory drop

## 4. Updated hypothesis_registry.md

Chaque entree Tier 1/2 recoit son status final :
- T1-01 : status=backtested, verdict=retest_with_real_funding
- T1-02 : status=backtested, verdict=paper_only
- T1-03 : status=backtested, verdict=keep_for_research
- T1-04 : status=backtested, verdict=paper_probation (3 variants), downgrade (1)
- T1-05 : status=backtested, verdict=paper_only_data_insufficient
- T2-01 : status=backtested, verdict=dropped (tous variants)
- T2-02 : status=doc_only, verdict=research_backlog
- T2-03 : status=backtested, verdict=keep_for_research
- T2-04 : status=backtested, verdict=dropped_hft_arb
- T2-05 : status=doc_only, verdict=dropped_regulatory

## 5. Allocation cible (INT-B output)

Allocation optimale (Calmar 2.13) = `inverse_volatility` sur les 12 strats
(7 baseline + 5 candidates validated). Voir `config/target_allocation_2026Q2.yaml`.

**NE PAS deployer tel quel** : attendre que les 3 paper promotions valident
30 jours de paper avant d'utiliser cette allocation en live. D'ici la,
utiliser les allocations actuelles (V2 first-refusal pour futures, crypto courant).

## 6. Kill criteria standardises

Tous les nouveaux paper_probation auront les memes kill criteria :
- DD absolute > 10% (stop permanent)
- DD rolling 90d > 8% (pause 30j puis re-eval)
- Divergence vs backtest > 2x std sur 60d rolling
- Correlation drift > 0.70 vs portfolio (stop, reassessment)

Ces criteria sont codifies dans `config/live_whitelist.yaml` per-entree.

## 7. Prochaines sessions (post-campagne Tier 1/2)

1. **T1-C'** : retelecharger funding historique Binance + refaire backtest basis_carry
2. **Monthly WF/MC** : re-run INT-A sur les 3 paper candidates pour detecter drift
3. **T1-E'** : attendre 5Y d'historique alts ou acquerir via Kaiko/Coin Metrics
4. **T2-A'** : si access VXM futures data, refaire crisis alpha propre
5. **Scale-up** : une fois 3 paper validees, proposer scale to $20K+ equity per broker
