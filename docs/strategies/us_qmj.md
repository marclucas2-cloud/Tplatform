# US QMJ Sector-Neutral V1

Status: `REJECTED` after Phase C walk-forward.

## Hypothese

Quality-Minus-Junk cherche a capter une prime cross-sectionnelle documentee:
les entreprises profitables, peu levees et aux earnings stables tendent a mieux
tenir que les entreprises "junk". V1 teste une version simple, sector-neutral,
sans optimisation de poids.

## Signal

Rebalance mensuel, premier jour de bourse du mois.

Score par action:

1. Profitability: `(Total Revenue - Cost Of Revenue) TTM / Total Assets`
2. Safety: `Total Debt / Total Stockholder Equity`, z-score inverse
3. Earnings stability: ecart-type EPS quarterly sur 8 trimestres, z-score inverse

Chaque sous-score est z-score intra-secteur GICS niveau 1. Le composite est:

```text
QMJ = (profitability_z + safety_z + stability_z) / 3
```

Portfolio:

- top quintile par secteur: long
- bottom quintile par secteur: short
- poids secteur = poids du secteur dans l'univers eligible
- dollar-neutral: 10k long + 10k short, gross 20k
- hold mensuel jusqu'au rebalance suivant

Pas de beta neutralization en V1.

## Point-In-Time Proxy

Les fundamentals gratuits ne sont pas un vrai PIT commercial. La strategie
utilise les dates `filed` EDGAR `companyfacts` quand elles existent. Si un
dataset futur n'a pas de filed date, le fallback applique:

- quarterly: visible seulement apres la frontiere de lag 90 jours
- annual: visible seulement apres la frontiere de lag 120 jours

Exemple teste: un fait Q4 2020 `period_end=2020-12-31` n'est pas visible avant
`2021-04-01`.

Limite importante: EDGAR companyfacts peut contenir plusieurs tags alternatifs
et restatements. Ce proxy evite le lookahead grossier, mais ne remplace pas
CRSP/Compustat PIT.

## Risk

V1 n'a pas de stop-loss intra-position. C'est une derogation de recherche:
le sleeve est un portefeuille diversifie long/short, rebalanced mensuellement,
pas une position single-name directionnelle. Pour paper/live potentiel, il
faudrait ajouter des limites:

- max single-name notional
- max sector gross
- max borrow / hard-to-borrow exclusion
- kill switch drawdown sleeve
- locate failure logging reel

## Resultats WF

Source: `data/research/wf_manifests/us_qmj_v1_2026-05-15.json`

OOS concatene 2018-2024:

| Metric | Value |
|---|---:|
| Sharpe net | -0.882 |
| Profit factor | 0.910 |
| Max DD | -17.35% |
| Trades | 3,655 |
| Hit rate | 49.66% |
| Net PnL | -$2,957.79 |
| Costs / gross PnL | 1.35% |

Windows OOS profitables: 2/7.

| OOS year | Net PnL | Sharpe | PF | DD |
|---|---:|---:|---:|---:|
| 2018 | -$287 | -0.523 | 0.944 | -3.08% |
| 2019 | -$826 | -2.030 | 0.818 | -5.21% |
| 2020 | -$719 | -1.126 | 0.886 | -4.99% |
| 2021 | +$26 | 0.069 | 1.007 | -2.57% |
| 2022 | +$420 | 1.006 | 1.089 | -1.72% |
| 2023 | -$401 | -1.209 | 0.883 | -2.46% |
| 2024 | -$612 | -1.683 | 0.788 | -5.02% |

Regimes:

| Regime | Window | Sharpe | PF | DD |
|---|---|---:|---:|---:|
| Bull | 2019 | -2.296 | 0.636 | -5.21% |
| Bull | 2021 | -0.262 | 0.955 | -2.94% |
| Bull | 2023-2024 | -1.311 | 0.786 | -6.78% |
| Bear | 2018Q4 | -2.405 | 0.607 | -1.80% |
| Bear | 2020Q1 | -0.720 | 0.888 | -1.30% |
| Bear | 2022 | 0.348 | 1.058 | -2.15% |
| Sideways | 2015-2016 | -0.071 | 0.989 | -4.95% |

Stress:

- COVID 2020-02-15 to 2020-04-30: Sharpe -2.338, DD -1.95%, PnL -$366
- 2022: Sharpe 0.348, DD -2.15%, PnL +$150
- GME squeeze 2021-01-25 to 2021-02-05: max single-name short loss -0.37% gross
- 2018Q4: Sharpe -2.405, DD -1.80%, PnL -$289

## Verdict

`REJECTED`.

Reasons:

- Sharpe OOS net is negative.
- Profit factor is below 1.2.
- Only 2/7 OOS windows are profitable.
- Rolling 60d correlation to BTC proxy slightly exceeds 0.4.
- Sideways 2015-2016 is slightly negative.

This is not a "needs small tuning" failure. V1 does not prove that free-data
single-stock QMJ deserves paper deployment.

## Recommendation

Do not paper-trade this V1. Keep the rejected manifest archived. Next decision
for Marc:

- either pause US single-stock L/S until paid PIT data is justified,
- or test a materially different V2 with beta-neutralization and borrow filters,
- or move to PEAD only after better event timestamp data is available.
