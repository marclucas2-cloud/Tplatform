# Tier 1 / Tier 2 Execution Plan — Research Campaign

**MAJ** : 2026-04-16
**Contexte** : plan de sessions successives pour transformer le backlog [hypothesis_registry.md](hypothesis_registry.md) en decisions `DROP` / `KEEP_FOR_RESEARCH` / `PROMOTE_PAPER` / `PROMOTE_LIVE`.

Chaque session est une **unite atomique** (2-4h) avec:
- objectifs precis
- prerequis data/code
- artefacts de sortie
- gate de decision explicite

## 0. Dependances cross-sessions

Certaines sessions BLOQUENT d'autres. Resoudre d'abord les prerequis.

### 0.1 PRE-REQUIS absolu : returns crypto harmonisees

**Pourquoi** : 3 candidates sur 5 Tier 1 sont crypto (T1-01 basis carry, T1-05 long/short, T2-02 liquidation). Sans returns daily harmonisees, le moteur `portfolio_marginal_score.py` ne peut PAS scorer.

**Action** : session dediee `S0 — rebuild crypto returns`
**Duree** : 2-3h
**Deliverable** : `data/research/portfolio_baseline_timeseries.parquet` enrichi avec colonnes crypto
**Sources envisageables** :
- `logs/worker/worker.log` parse des signaux "fill" + computing P&L a posteriori
- Backtest dedie des 11 strats crypto sur historique BTCUSDC/ETHUSDC disponible
- Shadow trade logger si active

**Gate pour la suite** : si cette session echoue, les candidates crypto restent en "missing_data" et on priorise les candidates futures/US.

### 0.2 Cost / slippage / capacity calibration (WP-14)

**Pourquoi** : sans couts realistes, les backtests sont optimistes. L'audit `reality check` est indispensable avant de scorer.

**Action** : session dediee `S0bis — cost calibration`
**Duree** : 1-2h (peut etre parallele a S0)
**Deliverable** : `docs/research/cost_capacity_assumptions.md`
**Contenu** :
- Commissions par broker (Alpaca $0, IBKR $0.85/RT futures, Binance 0.1% spot / 0.04% futures / -25% BNB)
- Slippage par produit (2 ticks deja modelise futures, a ajouter US + crypto)
- Funding rates Binance (historique dispo via API)
- Borrow rates Alpaca (pour short)
- Capacity estimee (notionnel max executable en 1 bar sans market impact)

### 0.3 Earnings calendar pour US PEAD

**Pourquoi** : T1-02 US post-earnings drift necessite un calendrier d'earnings et idealement les surprises historiques.

**Action** : dans la session T1-02, 30 min de data acquisition via `yfinance` ou API alternative
**Note** : les earnings dates + surprises sont accessibles via `yfinance Ticker.earnings_history` mais sur univers limite (SP500).

## 1. Tier 1 — 5 candidates prioritaires

Ordre d'execution **pragmatique** (facile a difficile, data-readiness en priorite) :

### SESSION T1-A : Futures calendar / session effects (T1-04)

**Pourquoi commencer ici** : data 100% disponible (MES/MGC/MCL daily 10Y via LONG parquets), logique simple, aucune dependance crypto.

**Duree** : 2h
**Book** : `ibkr_futures`
**Objectifs** :
- Tester day-of-week effect (Monday reversal, Thursday drift, Friday weakness)
- Tester turn-of-month effect (last 3 + first 3 days)
- Tester FOMC day / NFP day effects (calendrier economique)
- Chaque variante scoree via `portfolio_marginal_score.py`

**Prerequis** : aucun (data deja la)
**Outputs** :
- `scripts/research/backtest_futures_calendar.py`
- `output/research/wf_reports/T1-04_futures_calendar.md`
- Scorecard via marginal score engine

**Gate de decision** :
- Si `PROMOTE_LIVE` ou `PROMOTE_PAPER` → ajouter a `config/live_whitelist.yaml` avec status `live_probation`
- Si `KEEP_FOR_RESEARCH` → doc les fail modes + retente avec params differents en Semaine 4
- Si `DROP` → noter dans `docs/research/dropped_hypotheses.md` avec raison

---

### SESSION T1-B : Futures intraday mean reversion MES/MGC (T1-03)

**Pourquoi ensuite** : data 5M disponible (`data/futures/MES_5M.parquet`, etc.), logique claire, capture le **86% capital idle** identifie dans gap map.

**Duree** : 3h
**Book** : `ibkr_futures`
**Objectifs** :
- Detect excess intraday moves (>2x ATR) sur MES/MGC
- Fade the move au close de la session suivante (retracement 50-70% historique)
- Filtre regime : desactiver en trend fort (EMA50 slope > seuil)
- Scorer vs portfolio existant

**Prerequis** : aucun
**Outputs** :
- `scripts/research/backtest_futures_intraday_mr.py`
- `output/research/wf_reports/T1-03_futures_intraday_mr.md`
- Walk-forward 5 windows + Monte Carlo 10K
- Scorecard

**Gate de decision** : meme que T1-04

---

### SESSION T1-C : Crypto basis / funding carry (T1-01)

**Pourquoi ensuite** : apres S0 (returns crypto harmonisees). Moteur economique clair, documente.

**Duree** : 3h (+1h si S0 non fait)
**Book** : `binance_crypto`
**Objectifs** :
- Long spot BTCUSDC + short perp BTCUSDT_PERP = receive funding
- Variante conditionnelle : entrer seulement si funding 7d rolling > seuil
- Mesurer le cost of carry vs spread spot/perp
- Market-neutral donc delta 0

**Prerequis** :
- S0 returns crypto harmonisees
- Historique funding rates Binance (API publique, disponible)
**Outputs** :
- `scripts/research/backtest_crypto_basis_carry.py`
- Scorecard

**Gate** : meme que T1-04. Attention : si deja couvert par `borrow_rate_carry` (STRAT-006), risque de redondance -> verifier corr to STRAT-006 > 0.7 = DROP.

---

### SESSION T1-D : US post-earnings drift (T1-02)

**Pourquoi ensuite** : necessite earnings calendar data (pas immediat).

**Duree** : 3-4h
**Book** : `alpaca_us`
**Objectifs** :
- Telecharger earnings dates + surprises historiques SP500 via yfinance
- Long stocks avec EPS beat > 5% + gap up > 1% le day+1
- Hold 20 jours ou TP 8% / SL 3%
- Filtre regime : eviter si VIX > 30
- Scorer vs portfolio

**Prerequis** :
- Data earnings acquise en debut de session
- Capital Alpaca paper $100K disponible (verified)
**Contraintes** :
- PDT rule : utiliser qty entieres, eviter day-trading si capital < $25K reel
- Paper-first per doctrine

**Outputs** :
- `scripts/research/backtest_us_pead.py`
- `data/us_research/earnings_history.parquet`
- Scorecard

**Gate** : promote en paper uniquement (doctrine Alpaca US). Si PAPER > 3 mois sans divergence, re-evaluer pour live_small.

---

### SESSION T1-E : Crypto long/short cross-sectional (T1-05)

**Pourquoi en dernier Tier 1** : plus complexe (univers 20 alts), dependance data crypto forte.

**Duree** : 4h
**Book** : `binance_crypto`
**Objectifs** :
- Long top 5 alts performant vs BTC sur 20j, short bottom 5
- Rebalance hebdomadaire
- Market neutral sur beta BTC
- Filtre liquidite : volume > $10M/jour

**Prerequis** :
- S0 returns crypto harmonisees
- Historique daily 20 alts (BNB, SOL, ADA, DOT, AVAX, LINK, MATIC, DOGE, XRP, LTC, ATOM, NEAR, FTM, ALGO, APT, ARB, OP, STX, SUI, INJ)
**Outputs** :
- `scripts/research/backtest_crypto_long_short.py`
- Scorecard

**Gate** : capacity check crucial (alts illiquides). Si size viable < $500 -> DROP pour capital actuel $10K, garder en backlog pour scale-up futur.

## 2. Tier 2 — 5 candidates secondaires

A attaquer **apres validation complete Tier 1** (= 3+ candidates scorees, au moins 1 PROMOTE).

### SESSION T2-A : Futures crisis alpha / vol long overlay (T2-01)

**Duree** : 3h
**Book** : `ibkr_futures`
**Logique** : Long vol (via proxy short MES deep OTM puts ou VXM si data) quand VIX < 15.
Hedge convexe contre crashs.
**Prerequis** : data VIX historique (deja dispo dans `data/futures/VIX_1D.parquet`)
**Gate special** : standalone negative expected (cout de l'assurance), mais **delta portfolio MaxDD doit etre > +2pp**. Si oui, PROMOTE_LIVE_SMALL meme avec CAGR negatif standalone.

### SESSION T2-B : Crypto liquidation event-driven (T2-02)

**Duree** : 3-4h
**Book** : `binance_crypto`
**Logique** : Detect liquidation cascades (volume spike + break du range) et entree contrarian sur le rebond 5-30min apres.
**Prerequis** :
- S0 returns crypto harmonisees
- Historique liquidations (API Binance futures, plus complexe)
**Note** : deja une strat `liquidation_momentum` (STRAT-007) et `liquidation_spike` (STRAT-011) en live probation. Verifier que cette variante event-driven est differente, sinon merge.

### SESSION T2-C : EU sector rotation paneuropeen (T2-03)

**Duree** : 3h
**Book** : `ibkr_eu` (potentiellement a re-activer)
**Logique** : Long top 3 secteurs EU sur 20j momentum vs short bottom 3. Utilise ETFs sectoriels.
**Prerequis** : data sectorielle EU via yfinance (XLK, XLF equivalents EU : EXS1.DE, etc.)
**Gate** : doit **apporter diversification** vs crypto+futures sinon book EU reste paper_only per doctrine.

### SESSION T2-D : US cross-sectional mean reversion (T2-04)

**Duree** : 3-4h
**Book** : `alpaca_us`
**Logique** : Long paniers SP500 oversold sur 5j (RSI < 25 panier), short paniers overbought. Beta neutral.
**Prerequis** : data panier SP500 via Alpaca / yfinance
**Gate** : tres data-heavy, verifier capacity.

### SESSION T2-E : FX cross-sectional carry (T2-05)

**Duree** : 2h (quick verdict)
**Book** : `ibkr_fx`
**Status** : **BLOQUE par ESMA**. Session limitee a documenter le backtest et le ranger en backlog `speculative` jusqu'a changement reglementaire.
**Action** : ne PAS coder, juste documenter `docs/research/dropped_hypotheses.md`

## 3. Integration portfolio et promotion

### SESSION INT-A : Walk-forward + stress + Monte Carlo (WP-15)

**Duree** : 2h apres chaque PROMOTE_PAPER ou PROMOTE_LIVE
**Action** : pour chaque candidate retenue, lancer :
- Walk-forward 5 windows 70/30
- Monte Carlo 10K sims block-bootstrap
- Stress tests regimes (2020 COVID, 2022 bear, 2024 rally)
**Output** : `output/research/wf_reports/{candidate_id}/` + `output/research/stress_reports/{candidate_id}/`
**Gate** : si WF < 3/5 OOS profitable OU Monte Carlo DD p10 > -45% -> downgrade de 1 tier.

### SESSION INT-B : Portfolio allocation optimizer (WP-16)

**Duree** : 3h
**Prerequis** : minimum 2 candidates PROMOTE_PAPER + baseline complete
**Action** :
- Combiner portfolio baseline + candidates en un meta-portfolio
- Tester allocations : equal weight / risk parity / HRP / ROC-weighted / marginal-score-weighted
- Imposer contraintes : budget margin par book, budget DD total 5%, budget correlation
**Output** :
- `scripts/research/portfolio_allocation_optimizer.py`
- `docs/research/portfolio_optimizer_results.md`
- Allocation cible definie par `config/target_allocation_2026Q2.yaml`

### SESSION INT-C : Comite de promotion / rejection (WP-17)

**Duree** : 1h (decision + commit)
**Action** :
- Relire chaque scorecard candidate + resultats WF/MC/stress
- Categoriser : `REJECT` / `RESEARCH_MORE` / `PAPER_ONLY` / `LIVE_SMALL` / `LIVE_NORMAL`
- Mise a jour `config/live_whitelist.yaml` avec nouvelles promotions en `live_probation`
- Alerting : Telegram message avec la liste des changements
**Gate non-negociable pour LIVE** :
- Delta Portfolio Sharpe > 0
- Delta Portfolio MaxDD > -2pp
- Corr to portfolio < 0.50 (pas 0.70, plus strict pour LIVE)
- Au moins 30 jours de paper sans divergence backtest/live

## 4. Ordre de bataille concret

### Sequence recommandee

```
Session 1 : S0 — Rebuild crypto returns harmonisees               (2-3h) [BLOCKER]
Session 2 : S0bis — Cost/slippage calibration                     (1-2h)
Session 3 : T1-A — Futures calendar/session effects               (2h)
Session 4 : T1-B — Futures intraday MR MES/MGC                    (3h)
Session 5 : T1-C — Crypto basis / funding carry                   (3h)
Session 6 : T1-D — US post-earnings drift                         (3-4h)
Session 7 : T1-E — Crypto long/short cross-sectional              (4h)
Session 8 : INT-A — WF/MC/stress sur les Tier 1 qui passent        (2-3h)
Session 9 : T2-A — Futures crisis alpha                           (3h)
Session 10: T2-B — Crypto liquidation event                       (3-4h)
Session 11: T2-C — EU sector rotation                             (3h)
Session 12: T2-D — US cross-sectional MR                          (3-4h)
Session 13: T2-E — FX (doc only, blocked)                         (30min)
Session 14: INT-A — WF/MC/stress sur les Tier 2 qui passent        (2-3h)
Session 15: INT-B — Portfolio allocation optimizer                (3h)
Session 16: INT-C — Promotion committee + commit whitelist        (1h)
```

### Estimation totale

- **Tier 1 complete** : ~18h (sessions 1-7 + 8) sur 4-5 sessions reelles
- **Tier 2 complete** : ~15h (sessions 9-14) sur 4 sessions reelles
- **Integration** : ~7h (sessions 15-16) sur 2 sessions reelles
- **Grand total** : 10-12 sessions de travail pour boucler tout le backlog

### Checkpoints obligatoires

- **Apres chaque session candidate** : commit + scorecard + mise a jour `hypothesis_registry.md` avec verdict
- **Apres chaque PROMOTE** : rollout controlle (ajout whitelist + monitor 30 jours paper avant live)
- **Apres session 8** : revue intermediaire — si >= 2 Tier 1 retenus, continuer Tier 2. Sinon, reprise de Tier 1 avec params differents.

## 5. Kill criteria (rappel pour chaque promotion)

Chaque strategie promue doit avoir ses kill criteria explicites dans `config/live_whitelist.yaml` :

```yaml
kill_criteria:
  - drawdown_absolute: -10%    # stop si cum loss > 10% depuis start
  - drawdown_rolling_90d: -8%  # stop si rolling 90d DD > 8%
  - sharpe_rolling_60d: -0.5   # stop si Sharpe 60d < -0.5
  - divergence_vs_backtest: 2x_std  # stop si live diverge du backtest > 2 sigma
  - correlation_drift: >0.70   # stop si corr to portfolio monte au-dessus
```

## 6. Risques identifies sur le plan

1. **Data crypto harmonisees** : S0 bloque 3/5 Tier 1. Si echec, dropper temporairement les candidates crypto et se concentrer sur futures + US.
2. **Capacity alts** : T1-E crypto long/short peut etre non viable a $10K. Documenter la capacity plancher pour decider.
3. **Earnings calendar API** : yfinance peut rate-limiter sur SP500 complet. Prevoir chunking + cache local.
4. **Broad except migration** (P1.3 non fait) : tout nouveau code dans les backtests doit respecter la politique `docs/error_handling_policy.md`, pas de regression.
5. **Overfitting** : chaque candidate DOIT avoir un WF PASS obligatoire avant de toucher la whitelist. Pas de shortcut.

## 7. Ce qui n'est PAS dans ce plan (out of scope)

- Refacto per-book orchestrators (P2.2 live hardening) — traite dans une campagne separee
- Migration des 233 broad except (P1.3) — vague par vague en background
- Nouveaux books (pas de Deribit, pas de dYdX pour l'instant)
- Nouveaux brokers (pas de TradingView, pas d'Interactive Brokers alt)

## Quick reference

| # | Session | Book | Difficulty | Data ready | Expected verdict |
|---|---|---|---|---|---|
| S0 | Rebuild crypto returns | — | Med | partial | prerequis |
| S0bis | Cost calibration | — | Low | yes | prerequis |
| T1-A | Futures calendar | futures | Low | yes | PROMOTE_PAPER likely |
| T1-B | Futures intraday MR | futures | Med | yes | PROMOTE_PAPER likely |
| T1-C | Crypto basis carry | crypto | Med | after S0 | PROMOTE_PAPER likely |
| T1-D | US PEAD | alpaca | Med-High | partial | PROMOTE_PAPER only |
| T1-E | Crypto L/S | crypto | High | after S0 | KEEP_FOR_RESEARCH likely |
| T2-A | Crisis alpha | futures | Med | yes | PROMOTE_LIVE_SMALL maybe |
| T2-B | Liquidation event | crypto | High | after S0 | merge with existing |
| T2-C | EU sector rotation | EU | Med | yes | depends |
| T2-D | US cross-sectional MR | alpaca | High | partial | paper only |
| T2-E | FX | FX | Blocked | n/a | DROP (ESMA) |
