# Hypothesis Registry — WP-05 decorrelation research

**MAJ** : 2026-04-15
**Status** : Registre vivant, chaque nouvelle hypothese s'ajoute ici avant d'etre codee.

## Contexte de priorisation

Sur la base de [portfolio_baseline_2026-04-15.md](portfolio_baseline_2026-04-15.md),
[portfolio_overlap_report.md](portfolio_overlap_report.md),
[diversification_gap_map.md](diversification_gap_map.md) :

**Forces du portefeuille actuel**
- 3 strats futures alpha pur **decorrelees entre elles** (corr max 0.057)
- 11 strats crypto diversifiees (a backtest pour confirmer)
- Risk Budget Framework 5% + First-refusal CAM + V2 portfolio Sharpe 0.87

**Faiblesses criantes**
- **Capital occupancy 13.2%** — 86% idle, enorme opportunite
- **Horizon 100% swing (6-20j)** — 0% intraday, 0% position longue
- **5 familles de signal absentes** : calendar_seasonal, bear_directional, relative_value, dispersion, crisis_alpha
- **DD historique 510 jours -29%** (2022-2024) — le portefeuille a grind down pendant 2 ans

## TIER 1 — Candidats prioritaires (a backtest en semaine 2-3)

### T1-01 — Crypto basis / funding carry market neutral

**Book cible** : `binance_crypto`
**Logique economique** : Le funding rate perp/spot est souvent positif (longs paient les shorts)
en regime bull normal. Achat spot + short perp = payoff = funding recu.
**Regime cible** : bull/calm — monetise le cost of carry
**Horizon** : continuous, rebalance hebdomadaire
**Decorrelation attendue** : corr ~0 avec momentum crypto (different moteur economique)
**ROC attendu** : 5-15% annuel, faible DD
**Besoins data** : funding rates historiques Binance (API disponible)
**Couts / frictions** : fees perp + spot, spread spot/perp
**Fail mode probable** : funding negatif prolonge en bear, ou spread ecart
**Reponse a un gap** : `carry_yield` deja present (borrow_rate_carry) mais pas de basis carry
**Priority** : **high**

### T1-02 — US post-earnings drift regime-aware

**Book cible** : `alpaca_us`
**Logique economique** : Post-earnings announcement drift (PEAD) — les stocks qui surprennent
positivement (EPS beat) continuent a surperformer 20-60 jours apres. Effet documente academiquement.
**Regime cible** : tous, avec filtre regime VIX
**Horizon** : 5-20 jours
**Decorrelation attendue** : event-driven, non directionnel au marche
**ROC attendu** : 8-20% annuel sur univers filtre
**Besoins data** : earnings calendar + historique surprises (API Alpaca / Yahoo)
**Couts / frictions** : PDT rule si <$25K, slippage sur mid-caps
**Fail mode probable** : disparition de l'edge en regime high-vol (>VIX 30)
**Reponse a un gap** : `event_driven` absent en live, horizon court manquant
**Priority** : **high**

### T1-03 — Futures mean reversion intraday (MES/MGC)

**Book cible** : `ibkr_futures`
**Logique economique** : Excess intraday moves (>2x ATR) tendent a retracer 50-70% sur la
session suivante. Capture en opening range / lunch hour / close.
**Regime cible** : toutes volatilities sauf crisis
**Horizon** : <=1 jour (intraday)
**Decorrelation attendue** : ~0 corr avec strats swing actuelles (horizon different)
**ROC attendu** : tres dependant du capital deploye, petit mais stable
**Besoins data** : MES/MGC intraday 5m/1h (deja disponible dans data/futures/*_5M.parquet)
**Couts / frictions** : slippage ticks, IBKR commissions $0.85/RT
**Fail mode probable** : faux signal quand le trend est fort (gap + continuation)
**Reponse a un gap** : horizon intraday absent, capital occupancy 13% -> enorme marge
**Priority** : **high**

### T1-04 — Futures calendar / session effects (MES day-of-week)

**Book cible** : `ibkr_futures`
**Logique economique** : Effets calendaires documentes : Thursday rally, Monday effect,
turn of month, FOMC/NFP days. Independants de la tendance.
**Regime cible** : tous
**Horizon** : 1-3 jours
**Decorrelation attendue** : corr ~0 avec momentum futures (moteur calendrier pur)
**ROC attendu** : 3-8% annuel standalone, bon complement portfolio
**Besoins data** : MES daily + economic calendar
**Couts / frictions** : faibles, commission standard
**Fail mode probable** : les effets calendaires s'erodent sur le long terme
**Reponse a un gap** : `calendar_seasonal` totalement absent
**Priority** : **medium-high**

**STATUS 2026-04-16** : **backtested (session T1-A)**
- Script : `scripts/research/backtest_futures_calendar.py`
- Rapport : `output/research/wf_reports/T1-04_futures_calendar.md`
- 11 variantes testees sur MES 10Y daily (2015-2026)
- **4 PROMOTE_LIVE en in-sample** (necessite WF/MC avant live reel):
  - `long_mon_oc` : score +1.212, dSharpe +0.216, $10.8K PnL, WR 57.2%, corr 0.02
  - `long_wed_oc` : score +0.596, dSharpe +0.083
  - `turn_of_month` : score +0.463, dSharpe +0.007 (faible, surtout cap util)
  - `pre_holiday_drift` : score +0.315, dSharpe +0.070, WR 61.3% sur 106 trades
- **4 DROP confirmes** : `long_thu_oc` (dMaxDD -7.23pp), `short_fri_oc` (dMaxDD -15.92pp), `long_fri_oc`, `monday_reversal`
- **3 KEEP_FOR_RESEARCH** : `long_tue_oc`, `fomc_day_long`, `fomc_overnight_drift`
- **Next gate** : session INT-A WF/MC 5 windows obligatoire avant ajout whitelist en `live_probation`
- **Recommandation** : demarrage en `paper_only` sur Alpaca paper ou log-only sur IBKR, pas LIVE direct

### T1-05 — Crypto long/short cross-sectional (alts vs majors)

**Book cible** : `binance_crypto`
**Logique economique** : Long les alts qui surperforment BTC sur 20j, short les alts qui
sous-performent. Market-neutral sur le beta BTC.
**Regime cible** : bull normal et sideways — failles en crash
**Horizon** : 10-20 jours
**Decorrelation attendue** : market neutral donc corr ~0 avec BTC/ETH momentum
**ROC attendu** : 15-25% annuel si bien execute
**Besoins data** : prix daily top 20 alts Binance (dispo)
**Couts / frictions** : funding short perp, spread alts illiquides
**Fail mode probable** : crash universel (2022 alts -90%+) ou ban/delist
**Reponse a un gap** : `dispersion` + `relative_value` absents
**Priority** : **medium-high**

## TIER 2 — Candidats secondaires (apres Tier 1 valide)

### T2-01 — Futures crisis alpha / vol expansion overlay

**Logique** : Long vol proxy (VXM ou MES puts ITM virtuelle via backtest) quand VIX < 15.
Hedge convexe contre les crashs.
**Decorrelation attendue** : **fortement negative** en crisis, flat en calm
**Reponse a un gap** : `crisis_alpha` absent, ameliorerait les DD portfolios

### T2-02 — Crypto liquidation dislocation event-driven

**Logique** : Quand une cascade de liquidations short rompt un niveau, entree contrarian
sur le rebond (5-30 min post-spike).
**Decorrelation attendue** : event-driven, ~0 corr avec les strats lent
**Reponse a un gap** : horizon tres court absent

### T2-03 — EU sector rotation paneuropeen

**Logique** : Long les secteurs EU qui surperforment sur 20j, short ceux qui sous-performent.
**Decorrelation attendue** : different broker, different univers
**Reponse a un gap** : book EU est actuellement paper_only, pourrait redevenir utile

### T2-04 — US cross-sectional mean reversion panier liquide

**Logique** : Long paniers SP500 sold-off sur 5j, short paniers over-bought. Filtre beta neutral.
**Decorrelation attendue** : different horizon vs CAM, market neutral
**Reponse a un gap** : `dispersion` absent

### T2-05 — FX cross-sectional regime-aware (si ESMA contournable)

**Logique** : Carry sur bloc devise, long high-yield vs short low-yield, filtre regime vol.
**Note** : BLOQUE ESMA EU leverage limits (IBKR_FX_ENABLED=false actuellement)
**Decorrelation attendue** : moteur macro different
**Priority** : low (bloque par reglementation)

## TIER 3 — Speculatif (pas de these forte yet)

### T3-01 — Weekend anomalie crypto enrichie par regime

**Logique** : Le gap weekend crypto a historiquement un pattern. Filtrer par regime VIX/DXY.
**Status** : deja en live probation sur crypto mais sans filtre regime

### T3-02 — Stat arb crypto sur triangles (BTC-ETH-USDT)

**Logique** : Triangular arb between spot pairs. Microstructure-dependant.
**Note** : complexite executionnelle forte, exige data haute frequence

### T3-03 — Alpha ML defensif (deja un skill ML present)

**Logique** : ML comme filtre de signaux existants, pas comme source d'alpha
**Note** : respecter la regle "ML defensif, pas alpha ML"

## Regles de promotion (voir WP-17)

Une hypothese passe de Tier X a "backtested" quand :
1. Data disponible audited (cout, frictions, capacite modelises)
2. Backtest net avec slippage et commissions realistes
3. Walk-forward >= 50% fenetres OOS profitables
4. Scorecard marginal via `portfolio_marginal_score.py`
5. Verdict != DROP (hard gates passes)

Une hypothese passe "backtested" a "paper_live" quand :
1. Verdict >= PROMOTE_PAPER dans le marginal score
2. Delta Portfolio Sharpe > 0
3. Aucune regression sur MaxDD (delta > -2pp)
4. Dossier de preuve OOS defendable

Une hypothese passe "paper_live" a "live_probation" quand :
1. 30 jours en paper sans divergence backtest/live
2. Execution reality confirmee (fills, slippage, latence)
3. Ajoutee a `config/live_whitelist.yaml` avec status=live_probation

Une hypothese passe "live_probation" a "live_core" quand :
1. 90 jours en live_probation avec performance coherente avec backtest
2. Pas de correlation drift (monitor rolling corr vs portfolio)
3. Kill criteria documentes et respectes
