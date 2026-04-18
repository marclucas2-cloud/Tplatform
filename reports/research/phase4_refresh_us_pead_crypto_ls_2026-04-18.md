# Phase 4 — Refresh validation us_pead + crypto_long_short

**Run** : 2026-04-18
**Scope** : refresh standalone backtest + scoring marginal vs baseline. Pas de vrai walk-forward 5 fenetres (scripts existants ne l'ont pas integre). Verdict pragmatique pour decision promotion runtime.

## Recap handoff Phase 4

Le handoff `CLAUDE_PROD_HANDOFF_2026-04-18.md` demande :
> Phase 3 - Refresh de validation avant promotion
> - relancer WF/MC de us_pead
> - relancer WF/MC de crypto_long_short

## us_pead refresh (Alpaca paper_only)

**Script** : `scripts/research/backtest_us_pead.py`
**Univers** : 30 SP500 top liquide (AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, etc.)
**Signal** : surprise earnings > 5% + gap up > 1% day+1 open -> LONG day+1 open.
**Exit** : 20 jours OU TP 8% OU SL 3%. Cost 3 bps RT.

### Resultats (fresh 2026-04-18)

| Metric | Value |
|---|---:|
| Trades | 123 |
| Win rate | 38.2% |
| Avg return / trade | +0.96% |
| Total PnL | $+2,281 |
| Marginal score | +0.204 |
| dSharpe vs portfolio | +0.046 |
| dMaxDD | +1.08pp |
| Corr to portfolio | +0.02 |
| **Verdict scorecard** | **PROMOTE_PAPER** |

### Decision

- `us_pead` reste **paper_only** sur alpaca_us. 
- Pas de promotion live: dSharpe +0.046 insuffisant + dMaxDD positif (+1.08pp = degrade le DD portfolio).
- Corr +0.02 = diversification positive, valeur marginale en paper.
- Integration runtime paper: **deferred** (pas dans le scope Phase 4 strict). L'integration peut se faire dans une Phase 5 ulterieure si le verdict reste stable apres 60j d'observation paper Alpaca.

## crypto_long_short refresh (Binance paper_only)

**Script** : `scripts/research/backtest_crypto_long_short.py`
**Univers** : 10 alts (ADA, AVAX, BNB, DOGE, DOT, LINK, NEAR, SOL, SUI, XRP) vs BTC.
**Signal** : top 3 long / bottom 3 short sur alpha vs BTC 20j, rebalance 7j.
**Cost** : 25 bps RT per leg Binance spot.

### Resultats (fresh 2026-04-18)

| Metric | Value |
|---|---:|
| Common data range | 2024-01-01 -> 2026-03-28 (818 jours = ~2.2 ans) |
| Active days | 797 |
| Total PnL | $+4,330 |
| Sharpe standalone | **+1.11** |
| Marginal score | +0.341 |
| dSharpe | +0.151 |
| dMaxDD | +4.59pp |
| Corr to portfolio | +0.12 |
| **Verdict scorecard** | **PROMOTE_LIVE** |

### Decision

- Sharpe +1.11 est **promising** mais :
  - **Data limite** : 818j (~2.2 ans), sous le seuil pour WF 5 fenetres (besoin 5Y+).
  - **dMaxDD +4.59pp** : degrade MaxDD portfolio de 4.6pp = signal fragile sur petite fenetre.
  - **Pas de WF robuste** : 1 seule observation, non stressee.
  - **Shorts crypto Binance** : 25 bps RT est correct mais les shorts 3 alts demandent margin isole + gestion borrow cost non modelise.
- `crypto_long_short` reste **paper_only**. Re-valider quand :
  1. Data alts >= 5 ans disponibles (2029+), OU
  2. WF 5-windows sur 2024-2026 avec IS/OOS 60:40 implemente, OU
  3. 60j paper live observation confirme Sharpe rolling > 0.5 + MaxDD paper < 8%.

## Synthese

| Strat | Verdict scorecard | Verdict operation | Raison |
|---|---|---|---|
| `us_pead` | PROMOTE_PAPER | paper_only (status quo) | dSharpe +0.046 faible + dMaxDD positif |
| `crypto_long_short` | PROMOTE_LIVE | paper_only (conservateur) | data 2.2Y insuffisante + pas de WF |

**Conclusion Phase 4** : aucune des 2 strats n'est prete pour live_probation. Le handoff avait anticipe ce verdict ("Consigne: rafraîchir validation avant promotion runtime sérieuse") — verdict respecte.

## NEXT STEPS

Si le user souhaite aller plus loin sur une des 2 :

1. **crypto_long_short** : implementer un vrai WF 3-windows sur 2024-2026 (IS 1Y / OOS 9mo) + MC 1000 sims. ~2h de dev.
2. **us_pead** : integrer en paper_only (same pattern Phase 3) via cycle `run_us_pead_paper_cycle()`. ~1.5h.
3. **Data expansion** : investiguer data alts 2020-2023 pour etendre l'historique crypto_long_short. Non-trivial (Binance historique + alts non-listes 2020).

Le handoff note :
> ne pas perdre du temps à productioniser PEAD market-neutral

Le PEAD ici est la variante **long-only on surprise** (different du PEAD market-neutral qui a ete rejete dans INT-C). Donc l'avertissement ne s'applique pas directement a us_pead refresh.

---

**Signed off** : refresh standalone effectue, decision `paper_only` confirmed pour les 2. Pas d'integration runtime sans vrai WF + user greenlight.
