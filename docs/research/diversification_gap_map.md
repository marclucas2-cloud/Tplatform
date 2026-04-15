# Diversification Gap Map — 2026-04-15

**WP-04 decorrelation research** — cartographie des trous du portefeuille.

## Executive summary

- Horizon dominant: **swing (6-20d)** (100% des strats live)
- Familles de signal **ABSENTES du live**: calendar_seasonal, bear_directional, relative_value, dispersion, crisis_alpha
- Capital occupancy: 13.2% (2533/2917 jours idle)
- Nombre de drawdowns >5% historiques: **23**
- Longest DD: **710** jours

## Trous de regime — drawdowns historiques

Les 5 pires periodes historiques ou le portefeuille global perd. Chercher
des strategies qui auraient genere du PnL pendant ces periodes specifiques.

| # | Start | End | Days | Depth |
|---|---|---|---|---|
| 1 | 2022-03-25 | 2024-03-07 | 510 | -29.0% |
| 2 | 2015-07-07 | 2018-03-26 | 710 | -25.3% |
| 3 | 2020-08-12 | 2021-05-20 | 202 | -18.5% |
| 4 | 2021-07-07 | 2021-11-05 | 88 | -13.7% |
| 5 | 2021-11-22 | 2022-01-25 | 47 | -10.4% |

### Interpretation regime

Ces periodes DD correspondent typiquement a des regimes ou les 3 strats futures
actuelles (momentum/trend et rotation commodity) perdent ensemble. Candidats
prioritaires pour couvrir ces trous:
- **Mean reversion short-horizon**: capture les rebounds post-fort drawdown
- **Carry / basis / funding**: source de rendement independante du trend
- **Crisis alpha / vol long**: convexite positive en stress equity
- **Event-driven**: returns non directionnels

## Trous d'horizon de detention

| Bucket | Count | Pct |
|---|---|---|
| intraday (<=1d) | 0 | 0% |
| short (2-5d) | 0 | 0% |
| swing (6-20d) | 3 | 100% |
| position (>20d) | 0 | 0% |

### Interpretation horizon

Le portefeuille futures actuel est concentre sur **swing (6-20j)**. Manque:
- **Intraday / end-of-day** (<1j) pour diversifier par timing
- **Position longue (>20j)** pour capturer les grandes tendances

Un moteur intraday sur MES/MGC aux heures d'ouverture US apporterait de la
diversification sans chevaucher les strats swing existantes.

## Trous de famille de signal

### Presents

- `carry_yield` (1): carry / yield (basis, funding, borrow)
- `cross_asset_rotation` (2): cross-asset rotation
- `event_driven` (1): event-driven (earnings, liquidations, news)
- `mean_reversion` (3): mean reversion
- `momentum_trend` (5): trend following
- `unknown` (1): 
- `volatility_breakout` (2): volatility / breakout

### ABSENTS (candidats prioritaires)

- `calendar_seasonal`: calendar / seasonal / day-of-week
- `bear_directional`: bear / short bias
- `relative_value`: relative value / pairs / spreads
- `dispersion`: dispersion / cross-sectional
- `crisis_alpha`: crisis alpha / convexity / vol long

## Capital occupancy

- Jours actifs (PnL non nul): **384** / 2917 = 13.2%
- Jours idle: 2533

Si occupancy < 60%, il y a de la place pour un moteur haute-frequence qui
travaille les jours ou les strats swing sont en attente.

## Priorisation candidats (preliminaire)

Sur la base des gaps identifies, les candidats Tier 1 a explorer en priorite:

1. **Crypto basis / funding carry** — market-neutral, source de rendement
   independante du momentum futures (carry_yield absent)
2. **US post-earnings drift** — event-driven, horizon court (<5j),
   travaille sur les heures US ou le book futures est calme (event_driven absent)
3. **Futures mean reversion intraday (MES/MGC)** — monetise les excess moves
   apres grandes journees (mean_reversion sous-represente)
4. **FX cross-sectional carry** (si contournement ESMA possible) — carry sur
   bloc devises, decoupage par regime de vol
5. **Crypto long/short cross-sectional** — alts vs BTC dominance, market neutral

## Prochaine etape

WP-09 a WP-13 : batches de backtests par famille, chaque candidate passee par
le scoring marginal `scripts/research/portfolio_marginal_score.py`.
