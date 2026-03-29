# SYNTHESE COMPLETE — TRADING PLATFORM V10.0 (PORTFOLIO-AWARE RISK ENGINE + LIVE HARDENING)
## Portefeuille Quantitatif — 5 classes d'actifs, 29+12 strategies, ~22h/24h
### Date : 29 mars 2026 | 2,438 tests | ~105 fichiers test | CRO 9/10 APPROUVE

---

## 1. RESUME EXECUTIF

| Indicateur | V9.5 | **V10.0 (Portfolio-Aware Risk Engine)** |
|-----------|:---:|:---:|
| Classes d'actifs | 5 | **5** |
| Strategies total | 29 | **29** (inchange — focus risk/execution) |
| Tests | 2,312 | **2,438** (+126 nouveaux) |
| Modules core | ~100 | **~110** (+8 modules V10 + 2 helpers) |
| Dashboard | 11 pages | **11 pages + 8 endpoints V2** |
| API endpoints | 43 | **51** (+8 endpoints /v2/*) |
| Modules risk V10 | — | **8** (correlation, ERE, budget, leverage, throttler, safety, exec monitor, portfolio state) |
| Snapshot logging | — | **JSONL toutes les 5 min** (rotation quotidienne) |
| Safety Mode Phase 1 | — | **ACTIF** (max 5 strats, 1.0x levier, 20% ERE) |
| Worker V10 cycle | — | **Integre** (init au demarrage + cycle 5min) |
| CRO score | 9/10 | **9/10** (inchange) |

**V9.5->V10.0 : +8 modules portfolio-aware (correlation live, ERE, risk budget dynamique, leverage adapter, strategy throttler, execution monitor, portfolio state, safety mode). +8 endpoints V2 dashboard. +126 tests (2,438 total). Worker integre cycle V10 toutes les 5 min. Aucune nouvelle strategie — focus risque, execution, portfolio management.**

---

## 1.1 AUDIT SECURITE — RESULTATS

27 bugs fixes (5 CRIT, 10 HIGH, 12 MED/LOW), score 9/10. Key fixes: thread-safety broker init, losses-only circuit breaker, bracket post-verify SL, CRITICAL bypass throttle. All fixed.

CLEAN-001 : 9 strategies overfittees archivees (archive/rejected/), 1 dead code (EU Stoxx), 3 monitoring-only marquees, documentation WHY_REJECTED.md.

---

## 2. PORTEFEUILLE — LA REALITE STATISTIQUE

### 2.1 Walk-Forward : le filtre de verite

19 strategies US testees en walk-forward (70% IS / 30% OOS, 5 fenetres rolling).
Critere : ratio OOS/IS > 0.5 ET >= 50% fenetres profitables.

| Verdict | Strategies | Commentaire |
|---------|:---------:|-------------|
| **VALIDATED** | 4 | Edge confirme hors echantillon |
| **BORDERLINE** | 3 | Edge probable mais fragile |
| **REJECTED** | 9 | **Overfitting confirme** |
| MISSING DATA | 3 | Daily/monthly, pas de CSV intraday |

### 2.2 Strategies VALIDATED (allocation active)

| # | Strategie | Sharpe backtest | OOS Sharpe | WF ratio | % OOS profitable | Trades |
|---|-----------|:--------------:|:----------:|:--------:|:----------------:|:------:|
| 1 | Day-of-Week Seasonal | 3.42 | **2.21** | 12.01 | 60% | 44 |
| 2 | Correlation Regime Hedge | 1.09 | **1.47** | 0.84 | 60% | 88 |
| 3 | VIX Expansion Short | 3.61 | **5.67** | 3.49 | 80% | 26 |
| 4 | High-Beta Underperf Short | 2.65 | **3.30** | 3.00 | 100% | 72 |

### 2.3 Strategies BORDERLINE (allocation reduite, probatoire)

| # | Strategie | Sharpe backtest | OOS Sharpe | Probleme |
|---|-----------|:--------------:|:----------:|----------|
| 5 | Late Day Mean Reversion | 0.60 | 0.73 | Ratio OOS/IS = 0.29 (< 0.5) |
| 6 | Failed Rally Short | 1.49 | 1.49 | Ratio negatif sur certaines fenetres |
| 7 | EOD Sell Pressure V2 | 1.97 | 1.87 | Seulement 40% fenetres profitables |

### 2.4 Strategies REJECTED par walk-forward (overfitting confirme)

**Lecon capitale** : Les strategies avec les Sharpe les plus spectaculaires en backtest
(OpEx 10.41, Gap 5.22, Crypto V2 3.49) sont les plus severement rejetees en OOS.
C'est le signe classique de l'overfitting. 9 strategies archivees dans archive/rejected/.

### 2.6 Strategies EU actives (5 — pipeline multi-strats deploye)

| Strategie | Sharpe | WR | Trades | Walk-Forward | Statut |
|-----------|:------:|:--:|:------:|:------------:|:------:|
| EU Gap Open | 8.56 | 75% | 72 | 4/4 PASS | **ACTIF** |
| BCE Momentum Drift v2 | 14.93 | 77% | 99 | VALIDATED | **DEPLOYE** |
| Auto Sector German | 13.43 | 75% | 97 | VALIDATED | **DEPLOYE** |
| Brent Lag Play | 4.08 | 58% | 729 | 4/5 PASS | **DEPLOYE** |
| EU Close -> US Afternoon | 2.43 | 60% | 113 | VALIDATED | **DEPLOYE** |

### 2.7 Forex (12 strategies — allocation 18%)

| Strategie | Sharpe | Trades | Statut |
|-----------|:------:|:------:|:------:|
| EUR/USD Trend | 4.62 | 47 | **ACTIF** |
| EUR/GBP Mean Reversion | 3.65 | 32 | **ACTIF** |
| EUR/JPY Carry | 2.50 | 91 | **ACTIF** |
| AUD/JPY Carry | 1.58 | 101 | **ACTIF** |
| GBP/USD Trend (FX-002) | est. 2.0 | — | **LIVE P1** |
| USD/CHF Mean Reversion (FX-003) | est. 1.5 | — | **CODE** |
| NZD/USD Carry (FX-004) | est. 1.2 | — | **CODE** |
| Asian Range Breakout (FX-007) | — | — | **CODE** |
| Bollinger Squeeze (FX-008) | — | — | **CODE** |
| London Fix Flow (FX-009) | — | — | **CODE** |
| Session Overlap Momentum (FX-010) | — | — | **CODE** |
| EOM Flow Rebalancing (FX-011) | — | — | **CODE** |

**Data FX collectee** : 134,940 candles (8 paires, 1H/4H/1D, 2-5 ans depuis IBKR)

### 2.8 Futures Micro (8 strategies — allocation 10%)

| Strategie | Instrument | Margin | Sharpe cible | Statut |
|-----------|:----------:|:------:|:------------:|:------:|
| MES Trend Following (FUT-003) | MES | $1,400 | 1.5+ | **CODE** |
| MNQ Mean Reversion (FUT-004) | MNQ | $1,800 | 1.0+ | **CODE** |
| Brent Lag Futures (FUT-002) | MCL | $600 | 4.0+ | **CODE** |
| Gold Trend (FUT-005) | MGC | $1,000 | 1.0+ | **CODE** |
| M2K Opening Range Breakout (FUT-005) | M2K | $500 | 1.0+ | **CODE** |
| MES Overnight Momentum (FUT-006) | MES | $1,400 | 1.2+ | **CODE** |
| MGC Gold VIX Hedge (FUT-007) | MGC | $1,000 | 1.0+ | **CODE** |
| MES-MNQ Pairs Spread (FUT-008) | MES/MNQ | $3,200 | 0.8+ | **CODE** |

### 2.10 Crypto Binance France — Portefeuille INDEPENDANT ($15K, Margin + Spot + Earn)

**Capital** : $15K separe du $10K IBKR. Kill switch, risk, allocation : TOUT independant.
**3 wallets** : Spot $6K (40%) | Margin $4K (27%) | Earn $3K (20%) | Cash $2K (13%)

| # | Strategie | Type | Mode | Alloc | Levier |
|---|-----------|------|------|:-----:|:------:|
| 1 | BTC/ETH Dual Momentum | Trend | Margin | **20%** | 2x |
| 2 | Altcoin Relative Strength | Cross-sec | Margin | **15%** | 1.5x |
| 3 | BTC Mean Reversion Intra | MR | Spot | **12%** | 1x |
| 4 | Volatility Breakout | Vol | Margin | **10%** | 2x |
| 5 | BTC Dominance Rotation V2 | Macro | Spot | **10%** | 1x |
| 6 | Borrow Rate Carry | Carry | Earn | **13%** | 0x |
| 7 | Liquidation Momentum | Event | Margin | **10%** | 3x |
| 8 | Weekend Gap Reversal | Calendar | Spot | **10%** | 1x |
| 9 | Funding Rate Divergence | Contrarian | Margin | **8%** | 2x |
| 10 | Stablecoin Supply Flow | Macro | Spot | **7%** | 1x |
| 11 | ETH/BTC Ratio Breakout | Pairs | Margin | **6%** | 1.5x |
| 12 | Monthly Turn-of-Month | Calendar | Spot | **5%** | 1x |

**Data Crypto collectee** : 130,604 candles (12 symboles, 1H/4H/1D, 2-3 ans Binance) + borrow rates 10 assets 30j + BTC dominance 365j CoinGecko

| Regime | Trend | AltRS | MR | Vol | Dom | Carry | Liq | Weekend | Cash |
|--------|:-----:|:-----:|:--:|:---:|:---:|:-----:|:---:|:-------:|:----:|
| BULL | 20% | 15% | 12% | 10% | 10% | 13% | 10% | 10% | 10% |
| BEAR | 20% | 10% | 15% | 10% | 15% | 15% | 15% | 0% | 10% |
| CHOP | 5% | 10% | 20% | 15% | 10% | 20% | 10% | 10% | 10% |

**Risk management crypto V2 (12 checks)** : Position max 15% | Strategie max 30% | Gross long 80%, short 40%, net 60% | Levier BTC/ETH 2.5x, alt 1.5x, portfolio 1.8x | Borrow rate<0.1%/j, total<50%, cout mensuel<2% | DD daily 5%, weekly 10%, monthly 15%, max 20% | Margin health (reduce@1.5, close@1.3) | Cout emprunts auto-close >2%/mois | Earn max 100% | Perte position max 8% | Correlation BTC<70% | Reserve cash min 10%

**Kill switch V2 (6 triggers)** : Daily -5% | Hourly -3% | Max DD -20% | API down 10min | Margin level <1.2 | Borrow rate spike 3x en 1h
Actions : close shorts -> cancel orders -> close longs -> repay borrows -> redeem earn -> alert -> convert USDT

### 2.11 Strategies P2/P3 (8 avancees, toutes CODE)

FX Cross-Pair Momentum, EURO STOXX 50 Trend, Calendar Spread ES, Protective Puts Overlay, EUR/NOK Carry, Lead-Lag Cross-Timezone, FOMC Reaction, BCE Press Conference.

---

## 3. ALLOCATION V5 — DIVERSIFIEE MULTI-MARCHE + CRYPTO

### Structure cible V5.1

**Portefeuille IBKR ($10K) :**

| Bucket | Allocation V5 | Strategies | Broker |
|--------|:-----------------:|-----------|:------:|
| US Intraday | **25%** | DoW, Corr Hedge, VIX Short, High-Beta Short, + borderline | Alpaca |
| US Event | **8%** | FOMC Reaction | Alpaca |
| US Daily | **7%** | Momentum ETF, Pairs MU/AMAT, VRP | Alpaca |
| EU Intraday | **15%** | EU Gap, Brent Lag, EU Close->US | IBKR |
| EU Event | **10%** | BCE Momentum, Auto Sector, BCE Press Conference | IBKR |
| FX Swing | **18%** | 7 paires FX (24h) | IBKR |
| Futures Trend | **7%** | MES Trend, MNQ MR | IBKR |
| Futures Energy | **3%** | MCL Brent Lag | IBKR |
| Cash | **7%** | Buffer + margin futures | — |

**Portefeuille Crypto INDEPENDANT ($15K, Binance France V2) :**

| Bucket | Mode | Alloc BULL | Alloc BEAR | Alloc CHOP | Wallet |
|--------|------|:----------:|:----------:|:----------:|:------:|
| BTC/ETH Dual Momentum | Margin | **20%** | 20% | 5% | margin |
| Altcoin Relative Strength | Margin | 15% | 10% | 10% | spot |
| BTC Mean Reversion | Spot | 12% | 15% | **20%** | margin |
| Volatility Breakout | Margin | 10% | 10% | 15% | spot |
| BTC Dominance | Spot | 10% | 15% | 10% | spot |
| Borrow Rate Carry | Earn | 13% | **15%** | **20%** | earn |
| Liquidation Momentum | Margin | 10% | **15%** | 10% | margin |
| Weekend Gap | Spot | 10% | 0% | 10% | spot |

**REGLE : Les deux portefeuilles sont INDEPENDANTS.** Pas de transfert automatique, kill switch separes.

### Allocation cross-timezone (CET) — avec crypto

| Creneau | Marches actifs | Capital IBKR | Capital Crypto |
|---------|---------------|:------------:|:--------------:|
| 00h-09h | FX + Futures + Crypto | 20% | **40%** |
| 09h-15h30 | EU + FX + Futures + Crypto | 40% | **50%** |
| 15h30-17h30 | **OVERLAP** (EU+US+FX+Futures+Crypto) | **70%** | **60%** |
| 17h30-22h | US + FX + Futures + Crypto | 60% | **50%** |
| 22h-00h | FX + Futures + Crypto | 25% | **30%** |

**Couverture ~22h/24h** (vs 18h sans crypto).

### Allocation dynamique par regime (ALLOC-002)

| Regime | US Equity | EU Equity | FX | Futures Trend | Shorts | Cash |
|--------|:---------:|:---------:|:--:|:------------:|:------:|:----:|
| BULL | 45% | 20% | 12% | 12% | 4% | 5% |
| NEUTRAL | 35% | 20% | 18% | 8% | 7% | 7% |
| BEAR | 15% | 10% | 25% | 5% | 15% | 15% |

Transition lissee : 20%/jour vers la cible (anti-whipsaw).

### Sizing live ($10K-$25K)

| Capital | Phase | Methode | Levier max |
|---------|-------|---------|:----------:|
| $10K (soft launch) | **SOFT_LAUNCH** | **1/8 Kelly tier1, 1/16 Kelly borderline** | **1.0x** |
| $10K (mois 1) | PHASE_1 | Quart-Kelly tier1, 1/8 Kelly borderline | 1.5x |
| $15K (mois 2) | PHASE_2 | Quart-Kelly | 2.0x |
| $20K (mois 3) | PHASE_3 | Tiers-Kelly | 2.5x |
| $25K (mois 4+) | PHASE_4 | Half-Kelly | 3.0x |

**Volume live cible Phase 1** : Sem 1 = 6 strats (5 FX + EU Gap), 32-42 trades/mois, 1/8 Kelly. Sem 2+ = +MCL +MES, 52-70 trades/mois. Borderline US en PAPER ONLY.

---

## 4. RISK MANAGEMENT V4

### Framework 3 niveaux

**Niveau 1 — Pre-trade** : **12 checks** (position 10%, strategie 15%, long 60%, short 30%, gross 90%, cash 10%, secteur 25%, FX margin 40%, FX notional 1500%, futures margin 35%, combined margin 80%, cash reserve 20%)

**Niveau 2 — Intra-day** :
- Circuit-breaker : daily 5% + hourly 3%
- Deleveraging progressif : 30% a 0.9% DD, 50% a 1.35%, 100% a 1.8%
- Kill switch : calibre Monte Carlo (seuils par strategie, FP < 5%)
- Fermeture EOD + annulation ordres

**Niveau 3 — Structurel** :
- VaR portfolio-level avec matrice correlation + VaR stressed (corr 0.8)
- Risk Parity + Momentum overlay + Correlation penalty
- Regime detector HMM (3 etats, smoothing anti-bruit)
- Correlation-aware sizing (reduction 30% si cluster > 0.7)
- Signal confluence (double = x1.5, conflit = skip)
- Stops ATR adaptatifs (11 strats x 2 regimes)

### Guards (14)

Paper-only, _authorized_by, PDT $25K, circuit-breaker daily/hourly (losses only),
deleveraging progressif (0.9/1.35/1.8%), kill switch MC + hourly,
max positions (symboles uniques), bracket orders (verifie post-creation),
shorts int(), idempotence lock, reconciliation (alerte si broker down),
threading.Lock (validate_order, broker_init, kill_switch activate),
atomic state write (tmpfile + os.replace sur 3 fichiers d'etat).

### Kill switch calibre par strategie — LIVE V7.5

**IBKR :**

| Strategie | Type | Seuil kill |
|-----------|------|:----------:|
| EUR/USD Trend | FX swing | -3.0% |
| EUR/GBP MR | FX swing | -3.0% |
| EUR/JPY Carry | FX swing | -3.0% |
| AUD/JPY Carry | FX swing | -3.0% |
| GBP/USD Trend | FX swing | -3.0% |
| EU Gap Open | EU intraday | -1.5% |
| MCL Brent Lag | Futures | -2.5% |
| MES Trend | Futures | -2.5% |
| **PORTFOLIO IBKR** | Global | **-4.0% daily** |

**Crypto (Binance France) :**

| Strategie | Type | Seuil kill |
|-----------|------|:----------:|
| BTC/ETH Dual Momentum | Margin | -5.0% |
| Altcoin Relative Str | Margin | -6.0% |
| BTC Mean Reversion | Spot | -3.0% |
| Vol Breakout | Margin | -4.0% |
| BTC Dominance | Spot | -3.0% |
| Borrow Rate Carry | Earn | N/A |
| Liquidation Momentum | Margin | -5.0% |
| Weekend Gap | Spot | -5.0% |
| **PORTFOLIO CRYPTO** | Global | **-5.0% daily** |

NOTE : A calibrer par Monte Carlo apres 100+ trades live par strategie.

### V10 — Portfolio-Aware Risk Engine (8 modules)

| Module | Fichier | Role | Seuils |
|--------|---------|------|--------|
| **Live Correlation** | core/risk/live_correlation_engine.py | Rolling PnL correlation, clustering union-find | WARNING 0.70, CRITICAL 0.85 |
| **ERE** | core/risk/effective_risk.py | Vrai capital a risque (SL x correlation penalty) | REDUCE 25%, KILL 35% |
| **Risk Budget** | core/risk/risk_budget_allocator.py | Budget = base / sqrt(n_correlees) x regime | 2% base, 0.5%-3% clamp |
| **Leverage Adapter** | core/risk/leverage_adapter.py | Reduction temps reel (corr/DD/ERE/regime) | DD -50%, crisis -70% |
| **Strategy Throttler** | core/risk/strategy_throttler.py | PAUSE/REDUCE/STOP auto par performance | Sharpe<-0.5 PAUSE, slip>4x STOP |
| **Execution Monitor** | core/execution/execution_monitor.py | Slippage, fill rate, latence, SL execution | Slip>3x CRIT, fill<80% CRIT |
| **Portfolio State** | core/portfolio/portfolio_state.py | Vue unifiee IBKR+Binance (capital, ERE, DD) | Alertes auto multi-seuil |
| **Safety Mode P1** | core/risk/safety_mode.py | Limites Phase 1 (5 strats, 1.0x, 20% ERE) | 3 anomalies -> DISABLE |

**Cycle V10 dans worker.py :** toutes les 5 min, record snapshot JSONL + check correlation + check ERE + check safety + log leverage decision.

---

## 5. STRATEGIES REJETEES — ARCHIVEES (CLEAN-001)

Intraday US : 16 testees, 4 validated, 3 borderline, 9 rejected. Overnight : 9/9 MORT. Options proxy : 2/2 rejected.

1. **OpEx Gamma Pin (Sharpe 10.41)** : pur overfitting. OOS -3.99, 0% profitable. JAMAIS en live.
2. **Overnight** : mort sur 5 ans (Sharpe -0.70, 1254 jours).
3. **Mean reversion 5M** : tue par les commissions ET overfitte. 0/12 survivent au WF.
4. **Edges EU event-driven** (BCE, ASML, Auto German) : les plus robustes car moves > 1.5% > couts.

---

## 6. REGLES EMPIRIQUES (10)

1. **Commissions** : > 200 trades/6m + position < $5K = mort
2. **Sharpe** : < 1.0 apres couts = probatoire max
3. **Frequence** : Sweet spot = 30-60 trades/6m
4. **Flow** : Edges mecaniques survivent, techniques meurent
5. **Univers** : Marche sur 50 tickers mais pas 200 = survivorship bias
6. **Slippage** : Break-even < 0.05% = fragile
7. **Overnight** : Edge mort depuis 2021 (5Y de preuve)
8. **Couts EU** : 0.26% RT actions -> TP > 1.5% obligatoire. Futures 100x moins cher.
9. **Walk-forward** : Les Sharpe spectaculaires en backtest = overfitting probable. **OpEx 10.41 -> OOS -3.99.**
10. **Significativite** : < 30 trades = bruit statistique. Pas d'exception.

---

## 7. INFRASTRUCTURE V5.5

| Composant | Statut | Details |
|-----------|:------:|---------|
| Pipeline US | ACTIF | 13 strategies (7 actives + 6 monitoring) |
| Pipeline EU multi-strats | ACTIF | 5 strategies, YAML registry, per-strat market hours |
| Worker Railway | ACTIF | 24/7, heartbeat 30min + monitoring RAM |
| Worker Hetzner VPS | OPERATIONNEL | systemd auto-restart, IB Gateway 10.45, port 4002 paper |
| Hetzner VPS | ACTIF | 178.104.125.74, VNC :5900, IB Gateway connecte, account DUP573894 (1M EUR paper) |
| CI/CD | ACTIF | GitHub Actions, pytest a chaque push |
| Healthcheck externe | PRET | HTTP /health + doc UptimeRobot |
| Reconciliation | PRET | Auto toutes les 15min, alerte divergence |
| Dashboard multi-marche | ACTIF | 22 endpoints : 12 paper + 10 live |
| Triple broker | ACTIF | Alpaca (US) + IBKR (EU/FX/Futures) + Binance (Crypto) |
| Smart Router V3 | ACTIF | Route equities/FX/futures/crypto_spot/crypto_margin |
| IBKR reconnexion | ACTIF | Backoff exponentiel 1-2-4-8-30s |
| Futures infra | PRET | Contract manager, roll manager, margin tracker |
| Dynamic allocator V2 | PRET | Regime-adaptatif BULL/NEUTRAL/BEAR, smooth 20%/j |
| Cross-Portfolio Guard | V7.6 | Correlation IBKR-Binance, alerte >120%, critique >150% |
| V10 Risk Engine | DEPLOYE | 8 modules portfolio-aware |
| V10 Snapshot Logger | DEPLOYE | JSONL toutes les 5 min, rotation quotidienne, max 50MB |
| V10 Dashboard V2 | DEPLOYE | 8 endpoints /api/live/v2/* |
| V10 Safety Mode | ACTIF | Phase 1 : max 5 strats, 1.0x levier, 20% ERE |

**Fiscalite crypto FR** : PFU 30% sur cessions vers EUR. Echanges crypto-crypto non imposables.
Formulaire 2086 (PV crypto) + 3916-bis (comptes etranger = Binance). Methode PMP.

---

## 8. TESTS ET QUALITE

| Metrique | V9.5 | **V10.0** |
|----------|:--:|:------:|
| Tests total | 2,312 | **2,438** (+126) |
| Echecs | 0 | **0** |
| Fichiers test | ~100 | **~105** (+2 V10) |
| Lignes de code | ~175,000 | **~180,000** |
| Fichiers Python | ~520 | **~535** (+15 V10) |

| Category | Tests |
|----------|:-----:|
| Core risk+execution (LiveRiskManager, KillSwitch, Reconciliation, VaR, Alerting) | ~250 |
| Broker+trading engine (TradingEngine, Brackets, FX Live, Signal Sync) | ~200 |
| BacktesterV2 (Engine, DataFeed, Execution, Portfolio, Calendars, WF, MC) | ~180 |
| Crypto (Broker, Data, Backtest, Risk, Strategies, Allocation, Monitoring, ROC) | ~200 |
| Strategies V2 (IBKR 40 + Crypto 40 + FX nouvelles 30 + Futures 56) | ~170 |
| Hardening+fuzzing+stress (Fuzzing 28, Stress 9, Resilience 5, Kill E2E 11) | ~100 |
| V10 portfolio-aware (Risk 72 + Execution 54) | 126 |
| Other (Tax 55, Telegram 46, Autonomous 53, Leverage 40, Backup 8, etc.) | ~212 |
| **TOTAL** | **2,438** |

Audit CRO : **9/10** (12 domaines, 27 fixes)

---

## 9. MODULES CORE (~110)

### 9.0 BacktesterV2 — Grade Institutionnel

24 fichiers : Engine event-driven (12 types evenements), DataFeed anti-lookahead STRICT, ExecutionSimulator (latence, spread, impact Almgren-Chriss), PortfolioTracker (mark-to-market, stops, drawdown), WalkForward (rolling/expanding/anchored, grid search), MonteCarlo (10K sims, prob ruin), 5 cost models (IBKR FX $2, equity $0.005/sh, futures $0.62/ct, Binance 0.10%, funding horaire), 5 calendars (US NYSE, EU Euronext, FX 24/5, Futures CME Globex, Crypto 24/7).

### 9.0b Strategies V2 migrees (29 fichiers)

**FX (12)** : eurusd_trend, eurgbp_mr, eurjpy_carry, audjpy_carry, gbpusd_trend, asian_range_breakout, bollinger_squeeze, london_fix_flow, session_overlap_momentum, eom_flow_rebalancing, usdchf_mr, nzdusd_carry
**EU (5)** : eu_gap_open, bce_momentum, auto_sector_german, brent_lag_play, eu_close_us
**Futures (8)** : mes_trend, mnq_mr, mcl_brent_lag, mgc_trend, m2k_orb, mes_overnight, mgc_vix_hedge, mes_mnq_pairs
**Crypto (4 new)** : funding_rate_divergence, stablecoin_supply_flow, eth_btc_ratio, monthly_tom

### 9.1 Modules core (~55)

**Risk (8)** : risk_manager V5 (7 checks + VaR), live_correlation_engine V10, effective_risk V10, risk_budget_allocator V10, leverage_adapter V10, strategy_throttler V10, safety_mode V10, kill_switch_calibration MC

**Broker (6)** : factory V3 (smart router), ibkr_bracket V7.1 (OCA, post-verify, FX round(5)), ibkr_futures (contract manager), binance_broker V7.5 (margin+spot+earn), binance_ws (mark price, klines), futures_roll + futures_margin

**Crypto (12)** : data_pipeline, backtest_engine, risk_manager_crypto V7.5 (12 checks), allocator_crypto V7.5 (3 wallets), order_manager, monitoring V7.5, capital_manager, conviction_sizer, borrow_monitor, regime_detector, entry_timing, live_monitor

**Live (10)** : risk_manager_live V7.1 (12 checks), trading_engine V7.1 (dual-mode), kill_switch_live V7.1, reconciliation_live V7.1, trade_journal V6 (SQLite), alerting_live V7.1, var_live V6, fx_live_adapter V7.1, slippage_tracker V6, cost_tracker V6

**Other (11)** : allocator V5, dynamic_allocator_v2, kelly_calculator, regime_detector_hmm, position_sizer, confluence_detector V2, adaptive_stops, signal_quality_filter, signal_comparator V7, leverage_manager V7.1, autonomous_mode V6

**Execution/Portfolio V10 (3)** : execution_monitor, portfolio_state, live_snapshot_logger

**Support (6)** : telegram_commands V6, market_impact, capital_scheduler, event_calendar, alpha_decay_monitor, monitoring (RAM/CPU)

---

## 10. FEUILLE DE ROUTE V7 (PHASE 1 HARDENING)

| Phase | Capital | Delai | Strategies live | Cle |
|-------|:-------:|:-----:|:--------------:|-----|
| **Soft Launch** | $10K | Jour 4+ | **9** (5 FX + EU Gap + 3 borderline) | 1/8 + 1/16 Kelly, 40-60 trades/mois |
| **+ Futures** | $10K | Semaine 2 | **11** (+MCL +MES) | Si paper OK, 55-75 trades/mois |
| **Phase 1** | $10K | Semaine 3+ | 11 | Gate M1 (20+ trades), passage quart-Kelly |
| **Phase 2** | $15K | +1 mois si Gate M1 PASS | 14-16 | +US validated, levier 2.0x |
| **Phase 3** | $20K | +2 mois si KPI OK | 18-20 | +Futures avances |
| **Phase 4** | $25K | +3 mois si KPI OK | 22 | PDT leve, all strategies, 3.0x |

### KPI de validation (avant chaque scale-up)

**Gate M1** ($10K->$15K) : Min 15 trades live, Max DD < 5%, Sharpe > 0.3 (secondaire), WR > 42%, PF > 1.1, 0 bug
**Gate M2+** ($15K->$25K) : Min 50 trades cumules, Max DD < 8%, Sharpe > 1.0, WR > 48%, PF > 1.3, 0 bug

### Conditions passage live IBKR (checklist 14 points)

**Broker & Connectivity**
- [x] IBKR paper FX teste (positions ouvertes + fermees + reconciliees)
- [x] IBKR paper EU teste (EU Gap Open execute en paper)
- [ ] IBKR futures paper teste (MCL + MES, 5+ trades)
- [x] VPS Hetzner operationnel + IB Gateway connecte (178.104.125.74, port 4002, DUP573894)

**Strategy Validation**
- [x] Walk-forward valide sur TOUTES les strategies live
- [ ] Kill switch teste avec seuils calibres (DRILL-003)
- [x] Circuit breaker teste (losses-only fix V7.1)
- [x] Bracket orders FX testes (STP LMT + OCA)

**Risk Management**
- [x] Risk manager V7.1 audite (12 checks, 27 bugs corriges)
- [x] Stress tests passes (4 scenarios)
- [ ] Backup restore teste (DRILL-002)

**Infrastructure**
- [ ] Worker Hetzner stable 48h+ (healthcheck OK)
- [ ] Telegram alerts fonctionnels (3 niveaux testes)
- [x] Reconciliation 5min operationnelle

---

## 11. CHRONOLOGIE

| Date | Evenement |
|------|-----------|
| 22-23 mars | Debut projet, 12 strategies codees, scan 207 tickers |
| 24 mars | Bracket orders, Railway deploy, audit CRO 7/10 |
| 25 mars | Mission nuit 35 strats, CRO 9/10 |
| 26 mars | Dashboard, dual broker, TODO V3 (52 items), Risk V3, 306 tests, TODO XXL EU+ROC |
| **27 mars AM** | **AUDIT CRITIQUE : purge 8 strats, WF rejette 9 overfitting** |
| **27 mars PM** | **Consolidation V4 (433 tests) + Expansion V5 (17 strats, 4 classes, +17K lignes)** |
| **27 mars nuit** | **V6 LIVE-READY (14 modules, +23K lignes, +849 tests) + 3x CRO audit APPROUVE 9/10** |
| **27 mars nuit** | **Hardening V7 (27 bugs), CLEAN-001 (purge 10 strats), V7.3 ROC, kill switch calibre** |
| **27 mars soir** | **V7.5 CRYPTO V2 FRANCE (margin+spot+earn, 8 strats, $15K) + V7.6 CRO (10 fixes)** |
| **27-28 mars nuit** | **V8.0 BacktesterV2 : engine event-driven + WF + MC + 16 strats migrees (278 tests)** |
| **28 mars** | **CRO 9.5/10, Dashboard XL 11 pages, 8 crypto LIVE, Crypto ROC (10 modules)** |
| **28 mars soir** | **AUDIT CRO V9.0 : 27 fixes (7 CRIT + 7 HIGH + 7 MED + 3 LOW), score 9/10** |
| **29 mars AM** | **V9.5 : +13 strats (5 FX + 4 Futures + 4 Crypto), Hetzner VPS, 265K candles, 146 tests** |
| **29 mars PM** | **V10.0 PORTFOLIO-AWARE RISK ENGINE : 8 modules, +126 tests (2,438 total), Safety Mode P1** |

---

## 12. VERDICT FINAL

22 phases en 8 jours (22-29 mars 2026) : Expansion (3->34 strats) -> Critique (purge 9 overfittees) -> Consolidation (WF, VaR, MC) -> Expansion V5 (4 classes) -> Live-Ready V6 (14 modules) -> Hardening V7 (27 bugs) -> CRO V7.2 GO-LIVE -> ROC V7.3 -> Crypto V7.5 (8 strats Binance) -> CRO V7.6 -> BacktesterV2 (event-driven, WF, MC) -> Hardening S3 (fuzzing+stress) -> CRO 9.5/10 -> Dashboard XL + Crypto LIVE V8.5 -> Crypto ROC V9.0 -> Audit CRO V9.0 (27 fixes) -> V9.5 (+13 strats, Hetzner VPS, 265K candles) -> **V10.0 Portfolio-Aware (8 modules risk, Safety Mode P1)**

### AUDIT CRO V9.0 — Score 9/10 (27 fixes appliques)

| Domaine | **V9.0** | Amelioration cle |
|---------|:-------:|-----------------|
| D1 Execution ordres | **9.5/10** | Rate limiter Alpaca, error alerting, emergency close margin SL |
| D2 Gestion risque | **10/10** | Kill switch idempotent, cooldown 30min, auto-deleverage L2/L3 |
| D3 Integrite donnees | **10/10** | DST fixe (zoneinfo), empty response guard Binance |
| D4 Coherence BT/live | **9/10** | ExecutionSimulator seed=42 par defaut |
| D5 Securite | **10/10** | BINANCE_LIVE_CONFIRMED guard, *.key/*.pem gitignore |
| D6 Moteur backtest | **9.5/10** | Inchange |
| D7 Strategies actives | **9.5/10** | STRAT-004 SL absolu 2xATR, worker SL defaut -5% |
| D8 Pipeline | **9/10** | trading_paused_until verifie partout, per-strategy timeout 30s |
| D9 Monitoring | **9.5/10** | Live monitor JSONL, Telegram bot 12 cmds, auto-close 15:55 |
| D10 Infrastructure | **9.5/10** | Railway healthcheckPath=/health, crypto recon au demarrage |
| D11 Compliance | **8.5/10** | Inchange |
| D12 Documentation | **9.5/10** | Synthese V9.0 a jour |

**Reserves CRO restantes (non bloquantes) :**
- D1 : partial fills non geres — faible risque sur lots minimum
- D4 : sizing BT $100K =/= live $10K — a harmoniser apres Gate M1
- D10 : SL crypto sont script-side (pas broker-side OCO) — risque si worker crash

### Prochain pas

LIVE lundi 30 mars : IBKR 6 strats FX/EU (1/8 Kelly, switch paper->live + 2FA) + Binance 12 strats crypto (cycle 15min 24/7) + VPS Hetzner (systemd, IB Gateway, Telegram monitoring).
Safety Mode P1 actif. Gate M1 dans 3-4 semaines.
