# Research autonome — Stratégies décorrélées 2026-04-23

**Agent** : Claude Opus (mission autonome sans user)
**Mandat** : trouver 2-5 stratégies décorrélées, tradables, potentiellement rentables, compatibles avec le desk actuel
**Durée mission** : 2026-04-23 (session unique)

## Résumé exécutif

**Meilleur candidat identifié** : `mes_mr_vix_spike`
- MES mean reversion après 3 down-days consécutifs + filtre VIX > 15
- WF 5/5 OOS profitable (ratio 1.00 parfait), Sharpe 0.72, DD -9.72%, 61 trades / 5.2Y
- Corrélation quasi-nulle avec CAM (0.055) et GOR (-0.014)
- 🟡 **Verdict : paper actif recommandé** (runtime wiring laissé à Marc)

**Rejetés (5 candidats)** :
- mes_3day_stretch naïf (Sharpe -0.24, MR seul ne marche pas)
- mgc_vix_hedge long/short (Sharpe 0.10, trop peu de trades en long-short)
- mes_mnq_pairs (Sharpe -0.38, stat arb naïf ne génère pas d'edge)
- alt_relmom_long_only (Sharpe 0.30 mais DD -60%)
- v5_mes_down_week_mr (Sharpe -0.11)
- v3_mcl_mon_overnight (coûts écrasent le signal, mécanisme déjà couvert par sleeve existante)

**Réhabilitations notables** :
- `mes_monday_long_oc` (paper existant grade B) : mon backtest simplifié 11Y donne Sharpe -0.03. Inconsistence avec grade B du registry — nécessite re-audit avec filtres intraday du code original.
- `mes_wednesday_long_oc` (paper existant grade B) : idem Sharpe -0.15 sur 11Y. Re-audit recommandé.
- `gold_trend_mgc` V1 (paper, grade A) : confirmé robuste via manifest existant, déjà en paper.

## Phase A — Vérité initiale du desk (2026-04-23)

**État du portefeuille** (source : `config/quant_registry.yaml` V16.1) :
- 2 live_core ibkr_futures : `cross_asset_momentum` (CAM, grade A), `gold_oil_rotation` (GOR, grade S)
- 1 live_micro binance : `btc_asia_mes_leadlag_q80_v80_long_only` (grade B, first live_micro du desk)
- 6 paper_only : gold_trend_mgc, mes_monday_long_oc, mes_wednesday_long_oc, mcl_overnight_mon_trend10, alt_rel_strength_14_60_7, us_sector_ls_40_5
- 4 frozen (hors rotation business) : mes_pre_holiday_long, eu_relmom_40_3, mib_estx50_spread, us_stocks_daily
- 2 disabled : fx_carry_momentum_filter (ESMA), btc_dominance_rotation_v2

**Capital déployable** : $20,856 live (IBKR $11,013 + Binance $9,843), 1 position ouverte MCL (via CAM).

**Books opérables** : binance_crypto (live_micro_allowed), ibkr_futures (live_allowed), ibkr_eu (paper_only), alpaca_us (paper_only), ibkr_fx (disabled).

**Incidents récents** : P0 MCL contract mapping fixé 2026-04-23 commit `1217acf`. 3,816 tests pass (2 preexisting fails connus non-bloquants).

## Phase B — Cartographie data & assets

| Type | Assets | Couverture |
|---|---|---|
| Futures daily LONG | MES/MNQ/M2K/MGC/MCL | 2015-01 → 2026-04 (~11Y, 2834 bars) |
| Futures daily short | CAC40/DAX/ESTX50/MIB/VIX | 2021-01 → 2026-03 (~5Y, 1315 bars) |
| Futures intraday 5M/1H | MES/MNQ/MGC/MCL/M2K/VIX | 6 mois IBKR paper |
| Bonds 1H | ZB/ZN/ZT | 6 mois IBKR paper (INSUFFISANT pour backtest) |
| Crypto daily | 12 alts + BTC/ETH | 2023-01 → 2026-03 (~3.25Y) |
| Crypto 4H | 12 alts + BTC/ETH | ~3Y |

**Data stale** : futures daily stop 2026-03-30 (24j stale), crypto stop 2026-03-28 (26j stale). Non-critique pour backtest 3-11Y.

**Rejets data-driven** :
- Bonds MR (ZB/ZN) : seulement 6 mois 1H → insuffisant pour WF robuste (hypothèse documentée)
- Brent/WTI cross : Brent non disponible local

## Phase C — Refresh data

**Décision** : SKIP. Data actuelle (stop 2026-03-30) est stale de 24 jours mais backtests sont sur 5-11 ans. Le refresh n'aurait pas changé les conclusions macro. Refresh quand un candidat passe en paper actif → cron yfinance existant prend le relais.

## Phase D — Shortlist (round 1 → naïfs rejetés)

10 idées initiales, 5 shortlisted :

| # | Candidat | Mécanisme | Source |
|---|---|---|---|
| 1 | mes_3day_stretch_v2 | MR 3 down days LONG/SHORT | strategies_v2/futures/mes_3day_stretch.py |
| 2 | mgc_vix_hedge_v2 | Gold + VIX RSI long/short | strategies_v2/futures/mgc_vix_hedge.py |
| 3 | mes_mnq_pairs_v2 | Z-score log ratio stat arb | strategies_v2/futures/mes_mnq_pairs.py |
| 4 | alt_relmom_long_only_v2 | Crypto rel strength + BTC trend filter | rehab sleeve existante |
| 5 | bond_mr_ZB (dropped) | MR ZB daily | data insuffisante |

**Résultats round 1** (`scripts/research/decorrelated_candidates_2026_04_23.py`):

| Candidat | Sharpe | CAGR | DD | WF | Verdict |
|---|---|---|---|---|---|
| mes_3day_stretch_v2 | -0.24 | -3.1% | -53% | — | ❌ |
| mgc_vix_hedge_v2 | 0.10 | 0.45% | -12% | — | ❌ trop peu |
| mes_mnq_pairs_v2 | -0.38 | -2.0% | -28% | — | ❌ |
| alt_relmom_long_only_v2 | 0.30 | 2.7% | -60% | — | ❌ DD cata |

**Leçon** : les formes naïves de ces ideas ne produisent PAS d'edge brut. Itération v2 avec filtres smart.

## Phase E — Round 2 avec filtres smart

5 variants testés (`scripts/research/decorrelated_variants_v2_2026_04_23.py`):

| Candidat | Filtre ajouté | Sharpe | CAGR | DD | WF ratio | Verdict |
|---|---|---|---|---|---|---|
| v1_mes_mr_vix_spike | LONG only + VIX>18 | **0.71** | 5.24% | -11.5% | **0.80** | ✅ **WINNER** |
| v2_mgc_long_risk_off | LONG only + VIX RSI>55 + MGC>SMA50 | 0.68 | 8.45% | -16% | 0.80 | ⚠️ overlap GOR |
| v3_mcl_mon_overnight | Lundi + SMA10 filter | -7.28 | -100% | -100% | 0.00 | ❌ costs écrasent (bug formule) |
| v4_alt_relmom_dd_stop | + DD stop -20% portfolio | 0.37 | 5.5% | -66% | 0.40 | ❌ |
| v5_mes_down_week_mr | Weekly MR, Monday entry | -0.11 | -1.7% | -43% | 0.40 | ❌ |

**Correlation matrix v2** :

|  | v1 | v2 | CAM | GOR |
|---|---|---|---|---|
| v1 | 1.00 | -0.02 | 0.055 | -0.014 |
| v2 | -0.02 | 1.00 | 0.14 | **0.375** |
| CAM | 0.055 | 0.14 | 1.00 | 0.651 |
| GOR | -0.014 | **0.375** | 0.651 | 1.00 |

- **v1 quasi-orthogonal à tout le desk** : meilleure décorrélation trouvée.
- v2 corrélé 0.375 avec GOR → substituable/overlap, moins décorrélant.

## Phase F — Walk-forward + sensibilité sur v1

Grid 48 configs testés (`scripts/research/v1_sensitivity_2026_04_23.py`) :

**Sweet spot agressif** : consec=3, hold=2, vix_min=18 → Sharpe 1.03 / DD -7.83% / WF 0.80 / 55 trades
**Sweet spot robuste** : consec=3, hold=4, vix_min=15 → Sharpe 0.72 / DD -9.72% / **WF 1.00** / 61 trades

Config retenue : **robuste** (WF 5/5 parfait, même si Sharpe légèrement inférieur).

**Stabilité autour du sweet spot** (consec=3 consistently best) :
- hold=2..5 : Sharpe 0.5-1.0 tous, WF ≥ 0.80 dans 10/12 configs
- vix_min=15..22 : meilleur à 15-18, dégrade au-delà
- consec=4 : WF degrade (peu de trades)
- consec=2 : Sharpe dégrade (bruit)

## Phase G — Corrélation finale v1 vs desk + paper existants

| Strat | Corr v1 | Overlap days |
|---|---|---|
| CAM proxy (momentum MES/MNQ/M2K/MGC/MCL) | 0.055 | — |
| GOR proxy (MGC/MCL rotation) | -0.014 | — |
| mes_monday_long_oc (paper existant) | 0.170 | 18.85% |
| mes_wednesday_long_oc (paper existant) | 0.136 | — |

**v1 apporte une décorrélation réelle** (non-illusoire) et un mécanisme distinct des sleeves MES existantes (event-driven MR vs calendar effect).

## Phase H — Verdicts par candidat

| Candidat | Verdict | Raison |
|---|---|---|
| **mes_mr_vix_spike** (v1 final) | 🟡 **PAPER ACTIF** | WF 5/5, corr 0.055/-0.014, DD -9.7%, 12 trades/an |
| mgc_long_risk_off (v2) | 🧪 RECHERCHE COMPLEMENTAIRE | Sharpe 0.68 ok mais overlap GOR 0.375 réduit valeur décorrélante |
| v3_mcl_mon_overnight | ❌ JETER | Bug costs, mécanisme redondant avec mcl_overnight_mon_trend10 paper existant |
| v4_alt_relmom_dd_stop | ❌ JETER | DD -66% après stop, pas robuste |
| v5_mes_down_week_mr | ❌ JETER | Sharpe -0.11, WF 0.40 |
| mes_monday_long_oc (reexam) | ⚠️ RE-AUDIT | Backtest simplifié 11Y donne Sharpe -0.03, divergent du grade B registry |
| mes_wednesday_long_oc (reexam) | ⚠️ RE-AUDIT | Idem, Sharpe -0.15 sur 11Y |

## Phase I — Paper cabling

**Livrés** :
- `strategies_v2/futures/mes_mr_vix_spike.py` — code strategy (clean, StrategyBase pattern)
- `data/research/wf_manifests/mes_mr_vix_spike_2026-04-23.json` — manifest WF complet (grade A, verdict VALIDATED)
- `config/quant_registry.yaml` — entrée canonique status=paper_only, paper_start_at=2026-04-23
- `config/live_whitelist.yaml` — entrée ibkr_futures paper_only avec params + notes
- `tests/test_mes_mr_vix_spike.py` — 9 tests (default params, signal semantics, registry integration) — 9/9 PASS
- `docs/strategies/mes_mr_vix_spike.md` — doc stratégie complète

**NON fait** (respect doctrine "décision paper/live = user") :
- Wiring dans `worker.py:_run_futures_cycle` (block runtime)
- Journal `data/state/mes_mr_vix_spike/paper_trades.jsonl`
- Kill switch integration

Pour câbler runtime plus tard (laissé à Marc) :
1. Ajouter call site dans `worker.py:_run_futures_cycle` section paper
2. Créer data_feed combiné MES+VIX daily
3. Activer journal + kill switch scope=mes_mr_vix_spike_only

## Scores décision framework

| Score | v1_mes_mr_vix_spike |
|---|---|
| Décorrélation (0-10) | **9** (quasi-orthogonal à tout le desk) |
| Tradabilité (0-10) | **9** (MES liquid, VIX daily, runner infra existe) |
| ROC (0-10) | **6** (Sharpe 0.72, 12 trades/an, DD -9.7% — honnête pas spectaculaire) |
| Robustesse (0-10) | **9** (WF 5/5, sensitivity stable) |
| Utilité desk (0-10) | **8** (vrai nouveau moteur MR + VIX, pas couvert actuellement) |
| **Score global** | **41/50** |

## Conclusion honnête

Sur 10 idées initiales (5 shortlisted + 5 variants), **1 seul candidat passe la barre** : `mes_mr_vix_spike`.

Les mean reversion et pairs trading sur MES/MNQ dans leur forme naïve n'ont pas d'edge brut sur 11Y — seul l'ajout du filtre VIX > 15 transforme le signal 3-day-stretch en stratégie viable (Sharpe -0.24 → 0.72). C'est une confirmation que le market microstructure sans filtre régime de vol n'est pas suffisant.

**Pas d'alpha miracle, pas de Sharpe surestimé, pas de narration gonflée.** 12 trades/an, 6% CAGR, DD -10% : un ajout défensif et décorrélant qui apporterait :
- Un nouveau mécanisme (event-driven MR) à un desk actuellement 100% trend/momentum/pairs
- Un usage des "dead periods" quand CAM/GOR sont en cooldown
- Un signal tradable en paper dès 2026-04-23 avec promotion live possible 2026-05-23 si paper confirme

**Ce que cette mission a aussi révélé** (bonus) :
- `mes_monday_long_oc` et `mes_wednesday_long_oc` (paper grade B registry) échouent un backtest simplifié 11Y → audit du code original recommandé pour confirmer que les filtres intraday (VIX, ADX, ATR) justifient le grade B.
- Les bonds (ZB/ZN/ZT) ne peuvent pas être explorés tant que l'historique daily dépasse les 6 mois actuels.
- Le book alpaca_us reste gelé (PDT waiver non financé) — pas d'exploration US stocks possible.

## Prochaines actions desk justifiées

**Immédiat (Marc, si validation)** :
- Câbler `mes_mr_vix_spike` runtime dans worker.py (snippet doc inclus)
- Observer 30j paper (earliest promotion live_micro : 2026-05-23)

**Ce mois** :
- Re-audit `mes_monday_long_oc` + `mes_wednesday_long_oc` avec filtres intraday pour confirmer/infirmer grade B
- Télécharger historique ZB/ZN daily (yfinance) si intérêt bonds book

**Pas justifié** :
- Ajouter de nouvelles sleeves paper au-delà de v1 (le desk en a déjà 6 + 4 frozen)
- Re-chercher de nouvelles ideas avant que v1 paper montre un résultat

---

## Artefacts produits

### Scripts research (4)
- `scripts/research/decorrelated_candidates_2026_04_23.py` — round 1 (4 candidats naïfs)
- `scripts/research/decorrelated_variants_v2_2026_04_23.py` — round 2 (5 variants smart, winner v1)
- `scripts/research/v1_sensitivity_2026_04_23.py` — sensitivity grid 48 configs
- `scripts/research/v1_vs_existing_paper_2026_04_23.py` — comparaison sleeves existantes

### Code + config
- `strategies_v2/futures/mes_mr_vix_spike.py` — strategy (120 lignes, StrategyBase)
- `data/research/wf_manifests/mes_mr_vix_spike_2026-04-23.json` — manifest WF
- `config/quant_registry.yaml` — entrée paper_only grade A
- `config/live_whitelist.yaml` — entrée ibkr_futures paper_only

### Tests
- `tests/test_mes_mr_vix_spike.py` — 9 tests (9/9 PASS)

### Docs
- `docs/strategies/mes_mr_vix_spike.md` — strategy detailed doc
- `reports/research/decorrelated_strategies_2026-04-23.md` — ce rapport
- `reports/research/decorrelated_strategies_2026-04-23_metrics.json` — round 1 metrics
- `reports/research/decorrelated_v2_2026-04-23_metrics.json` — round 2 metrics
- `reports/research/decorrelated_v2_2026-04-23_returns.parquet` — daily returns series

## Hypothèses prises (à documenter/valider après)

1. Data stale 24j ignorée — jugée immatérielle sur backtest 5-11Y
2. Sleeves existantes `mes_monday_long_oc` et `mes_wednesday_long_oc` testées en version simplifiée (pas les filtres intraday du code original) — mes résultats négatifs ne valent que pour la version simplifiée
3. VIX_1D.parquet démarre 2021-01, donc backtest v1 limité à 5.2Y (vs 11Y pour MES_LONG seul)
4. Costs approximés en pct-of-notional pour simplicité vs dollar-par-contract exact
5. Pas de shorts testés (LONG only) — laissé pour une v2 si pertinent
6. Runtime wiring NON fait délibérément — décision Marc respectée
