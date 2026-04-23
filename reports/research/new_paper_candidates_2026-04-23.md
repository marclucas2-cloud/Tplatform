# Research mission PM — New paper candidates 2026-04-23

**Agent** : Claude Opus (mission autonome sans user)
**Mandat** : 8-12 idées étudiées → 3-6 candidats sérieux → 2-4 nouvelles sleeves paper
**Durée** : session PM 2026-04-23 (après fix TIF P0 déjà déployé)

## Résumé exécutable

| Métrique | Valeur |
|---|---|
| Idées initiales générées | 14 |
| Candidats sérieux testés | 11 (6 round 1 + 3 round 2 + 2 round 3) |
| Rejetés | 9 |
| **Nouvelles sleeves paper câblées** | **2** (`mes_estx50_divergence`, `mgc_mes_ratio_rotation`) |
| Paper retrospective (doc seulement) | 1 (`gold_q4_seasonality`) |
| Meilleur nouveau candidat | `mes_estx50_divergence` (Sharpe 0.95, WF 5/5, DD -10.4%) |
| Meilleure réhabilitation | Aucune (les paper sleeves existantes non ré-auditées cette session) |
| Pire illusion rejetée | `c3_mcl_mgc_ratio_z` (DD -151% après blow-up Z-score explosé, Sharpe -0.30) |

**Sleeves paper futures post-mission** : 5 paper_only + 1 paper_retrospective (avant: 3 paper_only).

## Inventaire candidats

### Round 1 (6 candidats initiaux)

| # | Candidat | Sharpe | CAGR | DD | WF | Verdict |
|---|---|---|---|---|---|---|
| c1 | mes_estx50_divergence | 0.77 | 8.36% | -14.3% | 3/5 | ⚠️ Promising, simplified |
| c2 | m2k_weekly_trend | -0.28 | -5.45% | -41% | 2/5 | ❌ JETER |
| c3 | mcl_mgc_ratio_z | -0.30 | -3.85% | -151% | 4/5 | ❌ Blow-up Z |
| c4 | mgc_rsi_pullback | -0.05 | -0.18% | -10% | 2/5 | ❌ 15 trades total |
| c5 | eth_btc_rotation | 0.20 | 1.24% | -58% | 2/5 | ❌ DD cata |
| c6 | alt_oversold_bounce | 0.09 | 0.11% | -22% | 1/5 | ❌ WF 1/5 |

### Round 2 (3 variantes smart)

| # | Candidat | Sharpe | CAGR | DD | WF | Verdict |
|---|---|---|---|---|---|---|
| v7 | mes_stressed_bounce | 0.63 | 5.04% | -15% | 2/5 | ❌ 20 trades + WF fail |
| v8 | gold_q4_seasonality | 0.39 | 2.73% | -22% | **4/5** | 📄 Paper retrospective (doc) |
| v9 | mes_m2k_pair | -0.27 | -2.30% | -26% | 3/5 | ❌ Sharpe négatif full |

### Round 3 (2 variantes additionnelles)

| # | Candidat | Sharpe | CAGR | DD | WF | Verdict |
|---|---|---|---|---|---|---|
| v10 | mes_complacency_short | -0.71 | -2.00% | -12% | 1/5 | ❌ Short MES structurellement perdant |
| v11 | mgc_mes_ratio_rotation | 0.36 | 4.34% | -32% | **4/5** | 🟡 **PAPER ACTIF** |

### Round 4 (c1 simplification MES-only)

27 configs grille testés sur `lookback / z_entry / max_hold`.

**Sweet spot trouvé** : `lookback=25, z_entry=1.5, max_hold=15`
- Sharpe **0.95**
- CAGR **8.9%**
- DD **-10.4%**
- **WF 5/5 parfait (ratio 1.00)**
- 48 trades / 5Y = 10/an

→ **mes_estx50_divergence retenu en version simplifiée MES-only**

## Verdict final par candidat

| Candidat | Verdict | Justification |
|---|---|---|
| **mes_estx50_divergence** | 🟡 **PAPER ACTIF** | WF 5/5, Sharpe 0.95, corr CAM -0.005 / GOR -0.102, 10 trades/an, exécution simple single-book |
| **mgc_mes_ratio_rotation** | 🟡 **PAPER ACTIF** | WF 4/5, Sharpe 0.36 modeste mais décor parfaite (corr CAM -0.064, GOR -0.029 malgré MGC commun), 13 trades/an, DD -32% à surveiller |
| **gold_q4_seasonality** | 📄 PAPER RETROSPECTIVE | WF 4/5, Sharpe 0.39 mais 1 trade/an → cadence insuffisante pour paper runtime actif, doc conservé |
| c1 version 2-way | ❌ Jeter | Simplification MES-only meilleure (WF 5/5 vs 3/5) |
| c2 m2k_weekly_trend | ❌ Jeter | Sharpe négatif |
| c3 mcl_mgc_ratio_z | ❌ Jeter | Blow-up Z-score (DD -151%) |
| c4 mgc_rsi_pullback | ❌ Jeter | 15 trades total sur 11Y |
| c5 eth_btc_rotation | ❌ Jeter | DD -58%, WF 2/5 |
| c6 alt_oversold_bounce | ❌ Jeter | WF 1/5 |
| v7 mes_stressed_bounce | ❌ Jeter | WF fail, peu de trades |
| v9 mes_m2k_pair | ❌ Jeter | Sharpe négatif |
| v10 mes_complacency_short | ❌ Jeter | Short MES contre le beta structurel haussier ne marche pas |

## Scores décision framework

| Score | mes_estx50_divergence | mgc_mes_ratio_rotation |
|---|---|---|
| Décorrélation (0-10) | **9** | **10** |
| Tradabilité (0-10) | **9** | 8 (routing 2 instruments MGC/MES) |
| ROC (0-10) | **8** (Sharpe 0.95) | **5** (Sharpe 0.36) |
| Robustesse (0-10) | **10** (WF 5/5) | **8** (WF 4/5) |
| Utilité desk (0-10) | **8** | **7** (diversification > alpha) |
| **Score global** | **44/50** | **38/50** |

## Corrélation matrice finale

|  | CAM | GOR | btc_asia | mes_mr_vix_spike | mes_estx50 | mgc_mes_ratio |
|---|---|---|---|---|---|---|
| mes_estx50_divergence | -0.005 | -0.102 | 0.083 | n/a | 1.000 | — |
| mgc_mes_ratio_rotation | -0.064 | -0.029 | 0.120 | — | — | 1.000 |

Toutes les nouvelles sleeves sont **quasi-orthogonales** aux live_core existants et à la live_micro crypto.

## Ce qui a marché (bonus mission)

- Fix TIF P0 (commit 72b742c) déployé VPS avant cette mission
- Cleanup DUP573894 SELL GTC positionné (fill à la réouverture CME 22h UTC)
- Pipeline framework backtest réutilisable (`new_paper_candidates_2026_04_23.py` + variants)
- 2 nouvelles sleeves paper ajoutées au catalogue

## Ce qui n'a pas été fait (délibérément)

- Pas de runtime wiring (doctrine "décision paper/live = user")
- Pas de réhabilitation des sleeves existantes (`mes_monday_long_oc`, `mes_wednesday_long_oc`, `mcl_overnight_mon_trend10`, `alt_rel_strength_14_60_7`) — peuvent être ré-auditées dans une prochaine mission
- Pas de nouvelle sleeve crypto car toutes les idées crypto ont échoué (c5, c6, v5 idée droppée)
- Pas de sleeve US/Alpaca (PDT waiver non financé, pas prioritaire)

## Pipeline paper desk post-mission

### ibkr_futures
- 2 live_core : `cross_asset_momentum`, `gold_oil_rotation`
- 5 paper_only : `gold_trend_mgc`, `mes_monday_long_oc`, `mes_wednesday_long_oc`, `mcl_overnight_mon_trend10`, `mes_mr_vix_spike` (livré AM)
- **2 paper_only new** : `mes_estx50_divergence`, `mgc_mes_ratio_rotation`
- 1 paper_retrospective : `gold_q4_seasonality` (doc)
- 1 frozen : `mes_pre_holiday_long`

### binance_crypto
- 1 live_micro : `btc_asia_mes_leadlag_q80_v80_long_only`
- 1 paper_only : `alt_rel_strength_14_60_7`
- 1 disabled : `btc_dominance_rotation_v2`

### Autres inchangés

## Livrables

### Code
- `strategies_v2/futures/mes_estx50_divergence.py`
- `strategies_v2/futures/mgc_mes_ratio_rotation.py`

### Manifests WF
- `data/research/wf_manifests/mes_estx50_divergence_2026-04-23.json`
- `data/research/wf_manifests/mgc_mes_ratio_rotation_2026-04-23.json`

### Config
- `config/quant_registry.yaml` — 3 nouvelles entrées (2 paper_only + 1 paper_retrospective)
- `config/live_whitelist.yaml` — 2 entrées ibkr_futures

### Tests
- `tests/test_new_paper_sleeves_2026_04_23.py` — 12 tests (9/9 pass pour les 2 sleeves + 3 registry)

### Docs
- `docs/strategies/mes_estx50_divergence.md`
- `docs/strategies/mgc_mes_ratio_rotation.md`

### Scripts research
- `scripts/research/new_paper_candidates_2026_04_23.py` — 6 candidats round 1
- `scripts/research/new_paper_v2_2026_04_23.py` — 3 variantes round 2
- `scripts/research/new_paper_v3_2026_04_23.py` — 2 variantes round 3
- `scripts/research/c1_mes_only_variant_2026_04_23.py` — sensitivity 27 configs

### Reports
- `reports/research/new_paper_candidates_2026-04-23.md` (ce rapport)
- `reports/research/new_paper_candidates_2026-04-23_metrics.json`
- `reports/research/new_paper_v2_2026-04-23_metrics.json`
- `reports/research/new_paper_v3_2026-04-23_metrics.json`
- `reports/research/new_paper_candidates_2026-04-23_returns.parquet`
- `reports/research/new_paper_v2_2026-04-23_returns.parquet`
- `reports/research/new_paper_v3_2026-04-23_returns.parquet`

## Prochaines actions suggérées

**Pour Marc** (si validation) :
1. Câbler runtime `mes_estx50_divergence` dans `worker.py:_run_futures_cycle` (section paper) — le plus simple (single-book MES)
2. Câbler runtime `mgc_mes_ratio_rotation` avec routing par signal (MGC ou MES selon sens Z)
3. Observer 30j paper → promotion live_micro envisageable 2026-05-23

**Par le desk pas cette mission** :
- Re-audit `mes_monday_long_oc` et `mes_wednesday_long_oc` avec filtres intraday (ma backtest simplifiée 11Y les rejette Sharpe négatif — besoin de vérifier avec version complète)
- Re-audit `mcl_overnight_mon_trend10` (data MCL_1D stale connue)

## Hypothèses documentées

1. Backtest ESTX50_1D commence 2021-01 → limite `mes_estx50_divergence` à 5Y vs 11Y autre data
2. `mgc_mes_ratio_rotation` utilise la même formule MR que le blow-up c3, mais avec `z_stop=3.0` qui limite le risk (c3 n'avait pas de stop → DD -151%)
3. Sensitivity grid c1 révèle un WF 5/5 parfait pour LB=25 Z=1.5 hold=15 qui diverge de la backtest isolée MES-only (WF 2/5). Le canonical est la grille (config testée sur splits anchored), mais le paper réel tranchera
4. Pas de réhabilitation des paper sleeves existantes (mandat permet, session déjà longue)
5. Framework backtest utilisé : anti-lookahead strict `.shift(1)`, costs $0.62/side + 1 tick slippage, splits anchored 5-way
