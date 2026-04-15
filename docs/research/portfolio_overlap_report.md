# Portfolio Correlation Report — 2026-04-15

**WP-02 decorrelation research** — analyse redondance portefeuille.

Scope: 3 strategies sur 2917 jours
Source: `data/research/portfolio_baseline_timeseries.parquet`
Genere le: 2026-04-15T21:16:17.788562Z

## Matrice de correlation Pearson

```
strategy_id           cross_asset_momentum  gold_oil_rotation  gold_trend_mgc
strategy_id                                                                  
cross_asset_momentum                 1.000             -0.027           0.057
gold_oil_rotation                   -0.027              1.000           0.001
gold_trend_mgc                       0.057              0.001           1.000
```

## Interpretation

- `cross_asset_momentum` vs `gold_trend_mgc`: +0.057 (FAIBLE)
- `cross_asset_momentum` vs `gold_oil_rotation`: -0.027 (FAIBLE)
- `gold_trend_mgc` vs `gold_oil_rotation`: +0.001 (FAIBLE)

## Correlation descendante (both in loss)

Capture si les strats perdent ensemble pendant les mauvaises periodes.
Une correlation downside elevee = pas de diversification en cas de stress.

```
                      cross_asset_momentum  gold_oil_rotation  gold_trend_mgc
cross_asset_momentum                   1.0                NaN             NaN
gold_oil_rotation                      NaN                1.0             NaN
gold_trend_mgc                         NaN                NaN             1.0
```

## Overlap des 30 pires jours

Combien des 30 pires jours de chaque strat sont communs avec les autres.

| Strategy | Overlap avec autres strats |
|---|---|
| `cross_asset_momentum` | gold_oil_rotation=0, gold_trend_mgc=1 |
| `gold_oil_rotation` | cross_asset_momentum=0, gold_trend_mgc=0 |
| `gold_trend_mgc` | cross_asset_momentum=1, gold_oil_rotation=0 |

## Clusters hierarchiques

Distance = 1 - |correlation|. Seuil de coupe: 0.5.
Des strategies dans le meme cluster ont >=50% de corr absolue.

- **Cluster 1**: cross_asset_momentum
- **Cluster 2**: gold_oil_rotation
- **Cluster 3**: gold_trend_mgc

## Rolling 60d correlation stats

| Pair | Mean | Min | Max | Std |
|---|---|---|---|---|
| `cross_asset_momentum_vs_gold_oil_rotation` | -0.014 | -0.988 | +0.233 | 0.119 |
| `cross_asset_momentum_vs_gold_trend_mgc` | +0.006 | -0.728 | +1.000 | 0.181 |
| `gold_oil_rotation_vs_gold_trend_mgc` | -0.002 | -0.563 | +0.326 | 0.072 |

## Verdict de redondance

Correlation max observee: **0.057** entre ('cross_asset_momentum', 'gold_trend_mgc')

**EXCELLENT — tous les moteurs futures sont decorreles (<0.3)**

## Data gaps

- Les strats `binance_crypto` ne sont pas dans cette matrice (pas de timeseries
  harmonisees reconstruites depuis les logs worker).
- Les strats paper `alpaca_us` et `ibkr_eu` ne sont pas incluses non plus.
- Next step: reconstruire les returns crypto depuis `data/crypto/wf_results.json`
  ou un backtest dedie pour enrichir la matrice.
