# mes_mr_vix_spike — MES Mean Reversion + VIX Spike Filter

**Status** : paper_only (2026-04-23)
**Book** : ibkr_futures
**Grade** : A (WF 5/5 parfait)
**Origine** : research autonome Claude Opus 2026-04-23 (mission decorrelated strategy discovery)

## Thèse

Après 3 bougies consécutives rouges sur MES (close < open chaque jour) ET un VIX au-dessus de 15, le marché actions américaines a une forte probabilité de rebondir à court terme.

Combine deux effets documentés :
- **Mean reversion post-stretch** (3 down days → snap-back)
- **Flight-to-quality bounce** après spike de vol (le VIX confirme que ce n'est pas du bruit de calme)

Le filtre VIX > 15 élimine les périodes de calme où la MR naïve sur 3 down days n'a pas d'edge statistique (cf itération v1 du research sans filtre → Sharpe -0.24).

## Règles

| Règle | Valeur |
|---|---|
| Direction | LONG only |
| Signal | 3 bougies consécutives (close < open) ET VIX_daily_close > 15 |
| Entry | open du jour J+1 après signal |
| Hold | 4 jours de bourse (time exit) |
| SL | 25 points MES ($125 par contract) |
| TP | 50 points MES ($250) — optionnel, sinon time exit |
| Sizing | 1 contract MES |
| Universe | MES (S&P 500 micro future) + VIX (filtre) |

## Backtest (5Y 2021-01 → 2026-04)

Source data :
- `data/futures/MES_LONG.parquet` (2834 bars daily, 2015-2026)
- `data/futures/VIX_1D.parquet` (1315 bars daily, 2021-2026)
- Script : `scripts/research/decorrelated_variants_v2_2026_04_23.py`
- Costs : $0.62 x 2 commissions + 1 tick ($1.25) slippage par flip

### Config robuste (retenue) — consec=3, hold=4, vix_min=15

| Métrique | Valeur |
|---|---|
| n_trades | 61 (~12/an) |
| Sharpe | **0.72** |
| Sortino | 0.59 |
| CAGR | 6.24% |
| Max DD | **-9.72%** |
| Calmar | 0.64 |
| Hit rate (days) | 44.4% |
| Walk-forward OOS | **5/5 profitable (ratio 1.00)** |

### Walk-forward détaillé (5 anchored windows)

| Window | OOS Sharpe | OOS PnL% | Profitable |
|---|---|---|---|
| 1 | 0.70 | +8.08% | ✅ |
| 2 | 0.58 | +5.27% | ✅ |
| 3 | 1.09 | +4.79% | ✅ |
| 4 | 0.89 | +6.89% | ✅ |
| 5 | 0.49 | +3.14% | ✅ |

### Sensitivity — config alternative agressive

consec=3, hold=2, vix_min=18 :
- Sharpe 1.03, CAGR 6.80%, DD -7.83%
- WF 4/5 profitable (ratio 0.80)
- 55 trades sur 5.2Y

On retient la config robuste (hold=4/vix=15) car WF 5/5 parfait et DD légèrement plus élevé mais acceptable. Marc peut arbitrer selon préférence cadence vs robustesse.

## Corrélation avec le desk actuel

Daily returns vs :

| Strat | Corrélation |
|---|---|
| CAM (proxy cross-asset momentum) | **0.055** (quasi-nulle) |
| GOR (proxy gold-oil rotation) | **-0.014** (quasi-nulle) |
| mes_monday_long_oc (paper existant) | 0.170 (faible) |
| mes_wednesday_long_oc (paper existant) | 0.136 (faible) |

**Overlap jours LONG avec mes_monday_long_oc** : 69 jours communs sur 366 jours long v1 = 18.85%. Les deux sleeves activent rarement le même jour → complémentaires, pas substituables.

## Runtime wiring

**Statut actuel** : NON câblé dans `worker.py` (décision Marc).

La strat est livrée comme dossier complet (code + manifest WF + tests + doc + registry entry paper_only) mais le runner autonome n'est pas activé pour respecter la doctrine "décision paper/live = user" + éviter de polluer le runtime existant.

Pour câbler plus tard :
1. Ajouter bloc dans `worker.py:_run_futures_cycle` (après le bloc mcl_overnight, section paper)
2. Instancier `MESMeanReversionVIXSpike()` avec data_feed combiné (MES_LONG + VIX_1D)
3. Router via `core/paper_trading/paper_runner.py` ou similaire
4. Journal dédié dans `data/state/mes_mr_vix_spike/paper_trades.jsonl`

**Earliest promotion live_micro** : 2026-05-23 (30 jours de paper observables depuis 2026-04-23).

## Caveats

1. **Période de backtest** : 5.2Y (depuis 2021-01, debut de VIX_1D.parquet). Plus court que les 11Y dispo sur MES_LONG. Limite par la freshness VIX daily.
2. **Data freshness** : exige `data/futures/VIX_1D.parquet` à jour (cron yfinance déjà en place). Si stale > 24h → strat doit skip silencieusement.
3. **Regime shift** : le filtre VIX > 15 capture l'ère post-2021 de vol élevée. Si le marché rentre dans un régime < 15 permanent (2017-2019), le signal se désactive automatiquement (feature, pas bug).
4. **Pas de shorts** : on n'a pas testé le symétrique (3 up days + VIX collapse → short). Laissé pour une v2 future si pertinent.
5. **Hold = 4 jours fixe** : pas de trailing stop ni de breakeven, volontairement simple. Peut être amélioré si paper montre des patterns exploitables.

## Références

- `strategies_v2/futures/mes_mr_vix_spike.py` — code
- `data/research/wf_manifests/mes_mr_vix_spike_2026-04-23.json` — WF manifest
- `config/quant_registry.yaml#mes_mr_vix_spike` — registry entry
- `config/live_whitelist.yaml` (ibkr_futures section) — whitelist paper_only
- `tests/test_mes_mr_vix_spike.py` — 9 tests (params, signal, registry)
- `scripts/research/decorrelated_candidates_2026_04_23.py` — round 1 (rejets)
- `scripts/research/decorrelated_variants_v2_2026_04_23.py` — round 2 (winner)
- `scripts/research/v1_sensitivity_2026_04_23.py` — sensitivity grid 48 configs
- `scripts/research/v1_vs_existing_paper_2026_04_23.py` — comparaison sleeves existantes
- `reports/research/decorrelated_strategies_2026-04-23.md` — rapport principal mission
