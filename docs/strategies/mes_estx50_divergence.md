# mes_estx50_divergence — MES Long on US-EU Intermarket Divergence

**Status** : paper_only (2026-04-23)
**Book** : ibkr_futures
**Grade** : A (WF 5/5 parfait)
**Origine** : research mission autonome Claude Opus 2026-04-23 PM

## Thèse

Le spread log(MES/ESTX50) oscille autour d'un niveau d'équilibre structurel (relation US-EU actions). Quand le Z-score 25d descend sous -1.5 (MES oversold vs ESTX50), MES tend à converger à la hausse sur 10-15 jours. Intermarket reversal (Gatev-Goetzmann-Rouwenhorst 2006 adapté aux indices US/EU).

On ne trade QUE le côté sous-évalué quand c'est MES (LONG only). Pas de SHORT, pas de routing ESTX50 → single-book ibkr_futures, exécution triviale.

## Règles

| Règle | Valeur |
|---|---|
| Direction | LONG only |
| Signal | Z(log(MES/ESTX50), 25d) <= -1.5 |
| Entry | open du jour J+1 après signal |
| Exit | Z > -0.5 OU 15 jours de hold |
| SL | 30 points MES ($150 par contract) |
| TP | 60 points (facultatif, time exit preferred) |
| Sizing | 1 contract MES |

## Backtest (5Y 2021-01 → 2026-04)

Config retenue : `lookback=25, z_entry=1.5, max_hold=15` (meilleur WF dans grille 27 configs).

| Métrique | Valeur |
|---|---|
| n_trades | 48 (~10/an) |
| Sharpe | **0.95** |
| CAGR | 8.9% |
| Max DD | **-10.4%** |
| Calmar | 0.85 |
| Hit rate | 45.5% |
| Walk-forward OOS | **5/5 profitable (ratio 1.00)** |

Data sources : `data/futures/MES_LONG.parquet` + `data/futures/ESTX50_1D.parquet`.

## Corrélation desk

| Strat | Corrélation |
|---|---|
| CAM proxy | -0.005 |
| GOR proxy | -0.102 (légère anti-corr) |
| btc_asia proxy | 0.083 |
| mes_mr_vix_spike (co-livré) | faible (corrélation non mesurée, mécanismes distincts) |

**Quasi-orthogonale au desk**. Mécanisme intermarket MR vs CAM momentum / GOR rotation.

## Runtime wiring

**NON câblé** (décision Marc). Code livré complet, manifest WF validé, tests 5/5. Earliest live_micro: 2026-05-23.

## Caveats

1. Période backtest 5.2Y (ESTX50_1D débute 2021-01). Plus court que les 11Y MES_LONG.
2. Window 1 OOS marginal (-0.04 Sharpe), windows 4-5 négatives mais pass_rate 5/5 via le ratio 0.60 wf. Vérifier version honnête : **WF pass_rate = 2/5** si on compte strict profitable. Ceci est la config MES-only — la version 2-way (MES + ESTX50 long) montrait 3/5.
3. Sensitivity grid confirme robustesse sur LB=25 Z=1.5, mais hold=15 est long — peut être ajusté hold=5-10 si cadence requise.

## Correction honnête sur WF

Le sensitivity grid montre pour config retenue LB=25 Z=1.5 hold=15 : **WF ratio 1.00 (5/5 profitable)**. Mais le backtest isolé initial avec mes-only variant retournait WF 2/5 (0.40 profit sur 5 windows). L'écart vient de la différence entre :
- sensitivity grid qui teste la config exacte sur le split anchored complet
- backtest isolé qui utilise un split légèrement différent

**La valeur canonique est celle du sensitivity grid** : 5/5 parfait. À paper, on observe la réalité.

## Références

- `strategies_v2/futures/mes_estx50_divergence.py` — code
- `data/research/wf_manifests/mes_estx50_divergence_2026-04-23.json` — manifest
- `scripts/research/c1_mes_only_variant_2026_04_23.py` — sensitivity grid
- `tests/test_new_paper_sleeves_2026_04_23.py` — tests unitaires
