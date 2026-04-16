# Dropped Hypotheses — Research Graveyard

Traces les hypotheses testees et rejetees avec leur raison. Utile pour
eviter de re-tester les memes idees et pour documenter l'edge erode.

## Format par entree

```
### H-ID — Titre
- **Session** : T1-X / T2-X
- **Date** : YYYY-MM-DD
- **Hard drop reason(s)** : critere de rejet tranchant
- **Metrics** : score, dSharpe, dMaxDD, corr, tail
- **Notes** : interpretation et pistes eventuelles de re-design
```

## Drops — Session T1-A (Futures calendar, 2026-04-16)

Script : `scripts/research/backtest_futures_calendar.py`
Marginal scoring engine : `scripts/research/portfolio_marginal_score.py`

### H-T1A-01 — `long_thu_oc` (Long MES Thursday open-to-close)

- **Session** : T1-A
- **Date** : 2026-04-16
- **Hard drop reason(s)** : `sharpe_degrades=-0.28`, `maxdd_degrades=-7.23pp`
- **Metrics** : score -1.445, dSharpe -0.284, dMaxDD -7.23pp, corr +0.01
- **Notes** : Hypothese initiale "Thursday rally" NON confirmee sur MES 10Y.
  Au contraire, `long_thu` degrade la baseline. Possible erosion post-2018
  ou bias specifique US equity futures. **Ne pas re-tester sans nouvelle these**.

### H-T1A-02 — `short_fri_oc` (Short MES Friday open-to-close)

- **Session** : T1-A
- **Date** : 2026-04-16
- **Hard drop reason(s)** : `sharpe_degrades=-0.29`, `maxdd_degrades=-15.92pp`
- **Metrics** : score -0.698, dSharpe -0.290, dMaxDD -15.92pp, WR 44.5%
- **Notes** : "Friday weakness" hypothese NON confirmee. Short systematique
  Friday fait saigner le portfolio sur les rallies (2020-2021, 2023-2024).
  **Dropped definitif**.

### H-T1A-03 — `monday_reversal` (Short si vendredi up, long sinon)

- **Session** : T1-A
- **Date** : 2026-04-16
- **Hard drop reason(s)** : `maxdd_degrades=-12.74pp`
- **Metrics** : score -0.129, dSharpe -0.108, dMaxDD -12.74pp
- **Notes** : La logique mean-reverting Friday->Monday ne fonctionne plus depuis
  les annees 2010s (weekend effect "reverse reversed" documente academiquement).
  Abandon.

### H-T1A-04 — `long_fri_oc` (Long MES Friday open-to-close)

- **Session** : T1-A
- **Date** : 2026-04-16
- **Hard drop reason(s)** : `maxdd_degrades=-6.96pp`
- **Metrics** : score -0.249, dSharpe -0.049, dMaxDD -6.96pp, WR 51.8%
- **Notes** : Symetrique du short_fri mais moins catastrophique. Edge absent,
  bruit pur avec cout RT. Dropped.

## Kept for research (Session T1-A)

Les variantes suivantes ont un score proche de zero, pas assez convaincantes
pour `PROMOTE_PAPER` mais a ne pas jeter non plus :

- `long_tue_oc` : score -0.005, dSharpe -0.075 — bruit
- `fomc_day_long` : score +0.189, petit edge mais 90 trades seulement
- `fomc_overnight_drift` : score +0.102, pre-FOMC drift Lucca/Moench
  pas aussi fort sur MES 2015-2026 que documente sur SPX 1994-2011.
  **Re-tester avec filtre VIX / filtre direction fed (hawkish vs dovish)**.

## Drops — Session T2-A (Futures crisis alpha, 2026-04-16)

### H-T2A-01 a H-T2A-06 — Short MES VIX-contrarian variants

- **Session** : T2-A
- **Date** : 2026-04-16
- **Hard drop reason(s)** : `maxdd_degrades` de -8pp a -68pp sur les 6 variantes
- **Variantes testees** :
  - `short_mes_vix_lt_13`, `_lt_15`, `_lt_18` (VIX calm trigger)
  - `short_mes_vix_breakout_120`, `_130`, `_150` (VIX expansion trigger)
- **Metrics** : scores -0.26 a -0.81, dSharpe jusqu'a -0.26, **aucun** gate dMaxDD >= +2pp
- **Notes** : le short MES systematique sur 10Y bull market est structurellement
  penalisant. Un vrai "crisis alpha" necessite des **options** (deep OTM puts,
  long VXM futures, tail risk via calendrier). Les proxies "short MES" ne
  capturent pas la convexite convexe. **Re-tester avec VXM futures data** ou
  **options via VIX chain** si data accessible.

## Drops — Session T2-D (US cross-sectional MR, 2026-04-16)

### H-T2D-01 — RSI14 long oversold / short overbought SP500

- **Session** : T2-D
- **Date** : 2026-04-16
- **Hard drop reason(s)** : `sharpe_degrades=-0.30`, `maxdd_degrades=-13.15pp`
- **Metrics** : score -0.894, dSharpe -0.298, standalone Sharpe -2.97
- **Notes** : cross-sectional MR naive sur SP500 30 tickers ne fonctionne plus.
  Arbitrage par HFT depuis les annees 2010s. Pour re-tester, besoin de :
  - Univers plus large (500 tickers complet)
  - Filtres qualitatifs (volatilite, earnings proche)
  - Signal plus sophistique que RSI14 brut
  **Drop confirme pour la doctrine $18K capital**.

## Drops — Session T2-B (Crypto liquidation, documentation)

### H-T2B-01 — Crypto liquidation event-driven contrarian

- **Session** : T2-B (documentation uniquement, data manquante)
- **Date** : 2026-04-16
- **Raison** : **data manquante** — pas d'historique liquidation cascades
  dans `data/crypto/` (API Binance futures liquidations non telechargee).
- **Alternative** : deja deux strats en live probation (`liquidation_momentum`
  STRAT-007, `liquidation_spike` STRAT-011) qui couvrent partiellement ce signal.
- **Status** : NE PAS coder une 3e variante pour l'instant, risque de doublon.
  Si data reelle telechargee, tester une variante plus rapide (5-30min hold)
  orthogonale aux deux existantes. Corr < 0.5 vs STRAT-007 et STRAT-011 obligatoire.

## Drops — Session T2-E (FX cross-sectional, 2026-04-16)

### H-T2E-01 — FX cross-sectional carry

- **Session** : T2-E (documentation uniquement)
- **Date** : 2026-04-16
- **Hard drop reason(s)** : **regulatory** — ESMA EU leverage limits empechent
  le carry FX retail efficace. Book `ibkr_fx` est `disabled` en whitelist.
- **Status** : NE PAS coder, remettre en backlog si regulation change.

## Regles pour ce document

1. Tout drop **doit** documenter les metrics exacts (score, dSharpe, dMaxDD).
2. Les hypotheses `KEEP_FOR_RESEARCH` restent dans le registry, pas ici.
3. Un drop peut etre "re-tested with new design" mais il faut une nouvelle
   hypothese avec un H-ID different et une these mise a jour.
4. Si une strat en `live_probation` est killee, son H-ID d'origine doit etre
   ajoute ici avec la raison de kill.
