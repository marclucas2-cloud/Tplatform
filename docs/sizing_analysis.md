# Sizing Analysis — Kelly Criterion

Date: 2026-03-27
Capital live prevu: $25,000

## Methode

Le **Kelly Criterion** determine la fraction optimale du capital a risquer par trade :

```
f* = (p * b - q) / b
avec p = win_rate, q = 1-p, b = avg_win / avg_loss
```

Pour un debut de live prudent, on applique le **quart-Kelly** (fraction = 0.25).
Cela reduit le drawdown attendu de ~75% par rapport au Kelly complet,
tout en conservant ~50% du rendement optimal.

## Resultats par strategie

| Strategie | Win Rate | Avg Win | Avg Loss | b ratio | Full Kelly | 1/4 Kelly | Capital |
|-----------|----------|---------|----------|---------|-----------|-----------|---------|
| Day-of-Week Seasonal | 68.2% | $10.94 | $15.28 | 0.72 | 23.78% | 5.95% | $1,487 |
| VIX Expansion Short | 50.0% | $91.57 | $53.42 | 1.71 | 20.83% | 5.21% | $1,302 |
| High-Beta Underperf Short | 50.0% | $103.59 | $63.85 | 1.62 | 19.18% | 4.80% | $1,199 |
| Failed Rally Short | 63.9% | $10.66 | $13.64 | 0.78 | 17.71% | 4.43% | $1,107 |
| EOD Sell Pressure V2 | 50.3% | $13.46 | $9.73 | 1.38 | 14.37% | 3.59% | $898 |
| Late Day Mean Reversion | 52.3% | $74.59 | $65.25 | 1.14 | 10.57% | 2.64% | $661 |
| Correlation Regime Hedge | 51.1% | $17.01 | $15.30 | 1.11 | 7.12% | 1.78% | $445 |

## Resume allocation

| Metrique | Valeur |
|----------|--------|
| Capital total | $25,000 |
| Capital alloue (1/4 Kelly) | $7,098 (28.4%) |
| Cash reserve | $17,902 (71.6%) |
| Nombre de strategies | 7 |
| Kelly moyen | 16.2% |
| 1/4 Kelly moyen | 4.1% |

## Interpretation

### Pourquoi le quart-Kelly ?

Le Kelly complet (f*) est **trop agressif** pour le debut de live :
- Il suppose une estimation parfaite de win_rate et b ratio
- Les 3 strategies BORDERLINE ont des metriques instables
- Le walk-forward valide sur ~6 mois, pas 5 ans

Le quart-Kelly est le standard en quant trading pour :
1. **Debut de live** — monte progressivement vers le demi-Kelly
2. **Incertitude sur les parametres** — marge d'erreur
3. **Protection psychologique** — drawdowns plus faibles

### Observations cles

1. **Day-of-Week Seasonal** a le Kelly le plus eleve (23.8%) grace a un win rate tres eleve (68.2%), malgre un b ratio faible (0.72)

2. **VIX Expansion Short** et **High-Beta Underperf Short** ont un Kelly eleve (19-21%) grace a un b ratio > 1.6 (les gains sont ~60% plus gros que les pertes)

3. **Correlation Regime Hedge** a le Kelly le plus faible (7.1%) : win rate a peine > 50% et b ratio modeste (1.11)

4. **Total alloue = 28.4%** du capital, laissant 71.6% en cash. C'est tres conservateur et coherent avec un debut de live

### Plan de scaling

| Phase | Delai | Kelly fraction | Capital alloue |
|-------|-------|---------------|----------------|
| Phase 1 (lancement) | Mois 1-3 | 1/4 Kelly (25%) | ~$7,100 (28%) |
| Phase 2 (validation) | Mois 4-6 | 1/3 Kelly (33%) | ~$9,500 (38%) |
| Phase 3 (scaling) | Mois 7-12 | 1/2 Kelly (50%) | ~$14,200 (57%) |
| Phase 4 (optimal) | Mois 13+ | 2/3 Kelly (67%) | ~$18,900 (76%) |

Conditions de passage :
- Phase 2 : PnL positif sur 3 mois, max DD < 5%
- Phase 3 : Sharpe live > 1.5, toutes strats profitables
- Phase 4 : Sharpe live > 2.0, 12 mois de track record
