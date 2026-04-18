# T1-E — Crypto long/short cross-sectional (alts vs BTC)

**Run** : 2026-04-18 16:29 UTC
**Univers** : 10 alts (ADA, AVAX, BNB, DOGE, DOT, LINK, NEAR, SOL, SUI, XRP)
**Base** : BTC (benchmark)
**Methodologie** : top 3 long, bottom 3 short sur alpha vs BTC 20d, rebalance 7d
**Data range** : 2024-01-01 -> 2026-03-28 (818 days)
**Sizing** : $1000.0/leg, cost 0.25 bps RT

**Caveat** : data alts disponible depuis 2024 seulement, 2Y pas suffisants pour
WF 5 windows classique. Resultat preliminaire, PROMOTE = necessite data 5Y complete
avant live (plan Tier 1-E note 'KEEP_FOR_RESEARCH likely').

## Results

- Active days : 797
- Total PnL : $+4,330
- Sharpe standalone : +1.11

## Scorecard

- Verdict : **PROMOTE_LIVE**
- Marginal score : +0.341
- dSharpe : +0.151
- dCAGR : +5.03%
- dMaxDD : +4.59pp
- Corr to portfolio : +0.12
- Max corr to strat : +0.16
- Tail overlap : 10%
- Penalties : -