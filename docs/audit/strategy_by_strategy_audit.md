# Strategy-by-strategy audit (Phase 3.1)

**Date** : 2026-04-16
**Contexte** : Phase 3.1 du plan TODO XXL DESK PERSO 10/10
**Auditeur** : audit ChatGPT 2026-04-15 + Claude consolidation 2026-04-16
**Méthode** : pour chaque strat, comparer thèse vs implémentation vs validation OOS vs corrélation portfolio

## Verdicts globaux

| # | Strat | Book | Status whitelist | Thèse vs code | Validation OOS | Verdict |
|---|---|---|---|---|---|---|
| 1 | cross_asset_momentum | ibkr_futures | live_core | ✅ aligné | ⚠️ 1 fenêtre OOS -20.5% | ⚠️ `keep_with_watch` |
| 2 | gold_trend_mgc | ibkr_futures | paper_only | ✅ aligné V1 | ✅ WF 5/5 + MC 0% | 🟢 `paper -> re-promote 30j` |
| 3 | gold_oil_rotation | ibkr_futures | live_core | ⚠️ pas market neutral | ✅ 5/5 OOS | ⚠️ `keep, document non-neutral` |
| 4 | mes_monday_long_oc | ibkr_futures | paper_only | ✅ aligné | ✅ WF 3/5 PASS | 🟢 `paper, candidate live 30j` |
| 5 | mes_wednesday_long_oc | ibkr_futures | paper_only | ✅ aligné | ⚠️ MC P(DD>30%) 28.3% | ⚠️ `paper, monitor close` |
| 6 | mes_pre_holiday_long | ibkr_futures | paper_only | ✅ aligné | ✅ WF 5/5 PASS | 🟢 `paper, best risk/reward T1-A` |
| 7 | btc_eth_dual_momentum | binance_crypto | live_core | ✅ aligné | ⚠️ borderline wf_results | ⚠️ `keep_with_watch` |
| 8 | volatility_breakout | binance_crypto | live_core | ✅ aligné | ✅ | 🟢 `keep` |
| 9 | btc_dominance_rotation_v2 | binance_crypto | **disabled** ✅ P0 | ❌ cassé | ❌ SKIPPED | ❌ `disabled jusqu'à fix` |
| 10 | borrow_rate_carry | binance_crypto | **paper_only** ✅ P0 | ❌ thèse trompeuse | ❌ SKIPPED | ❌ `clarifier thèse` |
| 11 | liquidation_momentum | binance_crypto | live_probation | ⚠️ partiel | ⚠️ fragmenté | ⚠️ `probation strict` |
| 12 | weekend_gap_reversal | binance_crypto | live_probation | ⚠️ partiel | ⚠️ fragmenté | ⚠️ `probation strict` |
| 13 | trend_short_btc | binance_crypto | live_probation | ⚠️ | ❌ preuves fragmentées | ❌ `demote paper` |
| 14 | mr_scalp_btc | binance_crypto | live_probation | ⚠️ | ❌ preuves fragmentées | ❌ `demote paper` |
| 15 | liquidation_spike | binance_crypto | live_probation | ⚠️ doublon STRAT-007 | ⚠️ | ⚠️ `merge ou demote` |
| 16 | vol_expansion_bear | binance_crypto | live_probation | ⚠️ | ⚠️ | ⚠️ `probation` |
| 17 | range_bb_harvest | binance_crypto | live_probation | ✅ | ⚠️ | ⚠️ `probation` |
| 18 | bb_mean_reversion_short | binance_crypto | live_probation | ✅ | ⚠️ | ⚠️ `probation` |
| 19 | fx_carry_momentum_filter | ibkr_fx | disabled | ✅ aligné | ✅ structurel solid | ⚠️ `garde, ESMA-bloqué` |
| 20 | eu_gap_open | ibkr_eu | paper_only | ✅ | ❌ rejet WF | ❌ `retire ou archive` |
| 21 | vix_mean_reversion | ibkr_eu | paper_only | ⚠️ | ❌ no WF | ❌ `research_only` |
| 22 | gold_equity_divergence | ibkr_eu | paper_only | ⚠️ | ❌ no WF | ❌ `research_only` |
| 23 | mib_estx50_spread | ibkr_eu | paper_only | ⚠️ | ❌ no WF | ❌ `research_only` |
| 24 | sector_rotation_eu | ibkr_eu | paper_only | ✅ | ⚠️ score marginal +0.15 | ⚠️ `keep_for_research` |
| 25 | us_stocks_daily | alpaca_us | paper_only | ✅ | ✅ doctrine paper | 🟢 `paper, OK` |

## Catégorisation actions

### KEEP (live_core ou paper validé) — 6 strats
- `cross_asset_momentum` : monitor 1 OOS window négative
- `gold_trend_mgc` (V1) : re-promote après 30j paper
- `gold_oil_rotation` : keep, documenter "long winner" pas neutral
- `volatility_breakout` : keep
- `mes_monday_long_oc`, `mes_pre_holiday_long` : paper validés WF

### KEEP_WITH_WATCH (probation, monitoring strict) — 6 strats
- `btc_eth_dual_momentum` : borderline OOS
- `mes_wednesday_long_oc` : MC borderline
- `liquidation_momentum`, `weekend_gap_reversal` : probation
- `range_bb_harvest`, `bb_mean_reversion_short`, `vol_expansion_bear` : probation
- Action : kill_criteria stricte (4 consec losses au lieu de 5)

### FIX REQUIS — 2 strats
- `btc_dominance_rotation_v2` : déjà `disabled` ✅. Fix `dominance_series` puis re-WF.
- `borrow_rate_carry` : déjà `paper_only` ✅. Clarifier thèse (rename ou supprimer beta directionnel BTC/ETH).

### DEMOTE (paper_only ou retire) — 4 strats
- `trend_short_btc` (STRAT-009) : demote `live_probation` → `paper_only` (preuves fragmentées)
- `mr_scalp_btc` (STRAT-010) : demote `paper_only`
- `liquidation_spike` (STRAT-011) : merge avec `liquidation_momentum` ou demote
- `eu_gap_open` : `retired` (WF rejet)

### RESEARCH_ONLY (pas de capital) — 3 strats
- `vix_mean_reversion`, `gold_equity_divergence`, `mib_estx50_spread` : keep en code mais `research`

### REGULATORY_BLOCKED — 1 strat
- `fx_carry_momentum_filter` : `disabled` ESMA, garde le code

### N/A doctrine — 1 strat
- `us_stocks_daily` : `paper_only` doctrine, OK

## Actions immédiates (P1 next)

1. **Demote `trend_short_btc`, `mr_scalp_btc`, `liquidation_spike`** dans whitelist
2. **Promotion committee formel** (Phase 3.3) à instaurer pour toute promotion live future
3. **Fix `btc_dominance_v2.py`** : dominance_series doit être un vrai historique de BTC.D
4. **Clarify thèse `borrow_rate_carry`** : decision rename "delta-1 carry" si on garde 40/40, ou supprimer 40/40 si on garde le nom
5. **Retire `eu_gap_open`** : déplacer dans `archive/strategies_eu/` + status `retired`

## Métriques portfolio post-actions

Si on applique tout :
- **live_core** : 4 → 4 (stable, gold_trend_mgc re-promote prévu)
- **live_probation** : 8 → 5 (3 demotes)
- **paper_only** : 5 → 8 (3 demotes + 3 EU research)
- **disabled** : 2 → 2 (BTC dominance, FX ESMA)
- **retired** : 0 → 1 (eu_gap_open)

→ **Portfolio live plus petit (de 12 à 9 strats live/probation) mais beaucoup plus défendable**.

## Format de fiche pour Phase 3.3 promotion committee

Toute future promotion live doit fournir :

```markdown
## Promotion request : <strategy_id>

- Backtest reproductible : <path>
- Walk-forward : 5 windows, ≥3/5 OOS profitable
- Stress tests : 2018-19, 2020-21, 2022, 2023-24, 2025-26
- Coûts/slippage : modèle explicite + sensibilité
- Fiche corrélation portfolio : dSharpe, dMaxDD, dCAGR vs baseline
- Budget capital + drawdown approuvé
- Checklist readiness signée
- Promotion committee approval : Marc + (Claude / PO subagent)
```
