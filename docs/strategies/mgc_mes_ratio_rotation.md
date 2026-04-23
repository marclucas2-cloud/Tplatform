# mgc_mes_ratio_rotation — Gold/Equity Z-score MR Rotation

**Status** : paper_only (2026-04-23)
**Book** : ibkr_futures
**Grade** : B (WF 4/5, Sharpe 0.36 modeste, décor parfaite)
**Origine** : research mission autonome Claude Opus 2026-04-23 PM

## Thèse

Le ratio log(MGC/MES) oscille autour d'un niveau macro d'équilibre (gold-to-equity ratio). Quand le Z-score 30d est extrême (≥1.5 ou ≤-1.5), mean reversion vers la moyenne.

Différent de **gold_oil_rotation (GOR)** qui est momentum entre MGC/MCL — ici c'est MR Z-score sur ratio gold-equity. Malgré l'instrument MGC en commun, **corrélation desk quasi-nulle** (-0.029 avec GOR).

Alternate LONG MGC / LONG MES selon direction de la MR (pas de SHORT).

## Règles

| Règle | Valeur |
|---|---|
| Z <= -1.5 | LONG MGC (gold catch-up attendu) |
| Z >= +1.5 | LONG MES (equity catch-up attendu) |
| Exit | abs(Z) < 0.3 OU abs(Z) > 3.0 (stop-divergence) OU 20 jours |
| SL | 3% du prix |
| Sizing | 1 contract MGC ou 1 contract MES selon sens |

## Backtest (11Y 2015-01 → 2026-04)

| Métrique | Valeur |
|---|---|
| n_trades | 143 (~13/an) |
| Sharpe | 0.36 |
| Sortino | 0.35 |
| CAGR | 4.34% |
| Total return | +61.27% |
| Max DD | **-32.18%** (élevé, à surveiller) |
| Calmar | 0.13 |
| Hit rate | 49.3% |
| Walk-forward OOS | **4/5 profitable (ratio 0.80)** |

Data : `data/futures/MGC_LONG.parquet` + `data/futures/MES_LONG.parquet` (11Y).

## Corrélation desk

| Strat | Corrélation |
|---|---|
| CAM proxy | -0.064 |
| GOR proxy | -0.029 (malgré MGC en commun → mécanisme différent) |
| btc_asia proxy | 0.120 |

**Orthogonal au desk.** Utile pour diversification > alpha isolé.

## Runtime wiring

**NON câblé** (décision Marc). DD -32% sur 11Y = nécessite stop plus strict en live probablement. À surveiller pendant 30j paper.

## Caveats

1. **DD -32%** élevé — non-trivial pour un desk de $20K. Si live_micro, forcer stop rolling 90d à -12% ou kill sleeve.
2. **Sharpe 0.36 modeste** — cette sleeve n'est pas un moteur alpha seul; elle est utile en complément de CAM/GOR pour diversifier.
3. **Routing 2 instruments** (MGC ou MES selon signal) — bracket à construire avec le bon contract par trade. Pas un pair trade (pas de short simultané).
4. **Window 2 OOS négatif** (-2.58%) mais les autres 4/5 positives → robustesse validée sur 11Y.

## Références

- `strategies_v2/futures/mgc_mes_ratio_rotation.py`
- `data/research/wf_manifests/mgc_mes_ratio_rotation_2026-04-23.json`
- `scripts/research/new_paper_v3_2026_04_23.py`
- `tests/test_new_paper_sleeves_2026_04_23.py`
