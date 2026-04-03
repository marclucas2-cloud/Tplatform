# SYNTHESE COMPLETE — TRADING PLATFORM V14.0 (PERFORMANCE + CAPITAL DEPLOYMENT)
## Portefeuille Quantitatif — 5 classes d'actifs, 47 strategies, ~24h/24h
### Date : 3 avril 2026 | 3,509 tests | ~145 fichiers test | CRO 9.5/10 APPROUVE

---

## 1. RESUME EXECUTIF

| Indicateur | V13.0 | **V14.0 (Perf+Capital)** |
|-----------|:---:|:---:|
| Classes d'actifs | 5 | **5** |
| Strategies total | 46 | **47** (+1 Cross-Asset Momentum paper) |
| Tests | 3,320 | **3,509** (+189 : 89 perf + 21 funnel + 33 capital + 46 stat arb) |
| Modules core | ~155 | **~210** (+23 perf + 15 capital + 3 funnel + 8 stat arb + 5 scripts) |
| Brokers LIVE | 2 | **2** (Binance $10K + IBKR $10K) + Alpaca paper |
| Capital deploye | ~$20K | **~$20K** (cible $45K) |
| Signal funnel | pas de diagnostic | **14-layer funnel logger + diagnostic scripts** |
| Capital utilization | non mesure | **UtilizationRate + CashDrag + DeploymentMonitor** |
| Guards | fixes | **Adaptatives** (thermostat par utilization) |
| Always-on | aucune | **FX Carry (3 paires) + Earn crypto** |
| Sizing | per-broker | **Global $45K NAV** (cap per-broker 80%) |
| Research pipeline | ad-hoc | **6 gates** (thesis→backtest→WF→cost→corr→paper) |
| New alpha | -- | **Cross-Asset Momentum** (Moskowitz 2012, paper mode) |
| CRO score | 9.5/10 | **9.5/10** |

**V13.0->V14.0 : 4 chantiers majeurs.**

**1. Performance & Alpha (23 modules)** : Signal Quality Filter v2 (5 dimensions, score 0-1). Adaptive Stops v2 (noise floor + regime). Entry Timing (LIMIT patience, intraday timing, fade-in). Exit Optimizer (trailing stop adaptatif + time stops + partial profits). Smart Router v2 (spread monitoring, maker/taker). Cost Model Audit + Commission Burn Analysis. HRP Diagnostic + Kelly Recalibration + MDC Sizing. Research Pipeline (6 gates) + Auto Backtest + Parameter Sweep. On-Chain Pipeline (MVRV, netflow, hashrate, fear&greed). Sentiment Pipeline. Calibration NEED_LIVE (slippage, win rate drift, regime effectiveness, meta-activation).

**2. Signal Funnel Unblock (7 fixes)** : Diagnostic scripts (signal_funnel + crypto). Min Position Size filter ($50-200 par asset class). Funnel Logger structure (FUNNEL|strat|layer|action). UNKNOWN regime multipliers 0.3-0.5 → 0.7-0.8. Guards adaptes $10K (position 20%, cash 5%, 12 max positions).

**3. Capital Deployment (15 modules)** : Utilization Rate Calculator (regime targets). Cash Drag ($idle × 10% / 365). Deployment Monitor (blocked strategies diagnostic). Guard Pass Rate Tracker (biggest killers). Adaptive Guards (thermostat). Always-On FX Carry (3 paires, vol scaling, PANIC=0.2 pas 0). Always-On Earn crypto (USDC/BTC/ETH flexible). Allocation Gap Tracker (ALIGNED/DRIFTING/MISALIGNED/BLOCKED). Active Rebalancer (turnover-controlled). Global Portfolio Sizer ($45K NAV). Cross-Broker Capital Optimizer. Signal Aggregator.

**4. Stat Arb Research** : MidCap stat arb teste (REJECTED, Sharpe -1.28). Cross-Asset Momentum code + backtest (Sharpe 0.81, DD -5.21%) → deploye en paper dans le worker (cycle daily 16h15 CET).

---

## 1.1 AUDIT SECURITE — RESULTATS

**Audit V9.0** : 27 bugs fixes. **Audit V12.5** : 40 bugs supplementaires fixes.

**Securite (P0-P2)** : secrets retires git, WebSocket JWT auth, JWT_SECRET random/restart, exec()→importlib, rate limit/IP.

**Kill chain unifiee** : /kill CONFIRM active 2 KS (IBKR+crypto) + EmergencyCloseAll. /emergency avec kill_switch_callback. Auto-kill live arme crypto KS (anti re-entree). safe_restart lit les bonnes cles.

**Paper/Live isolation** : dashboard equity = live only. V10 PortfolioStateEngine filtre paper. UnifiedPortfolio exclut positions/cash paper. Analytics source="real" exclut paper_journal.db. DD live dans fichier dedie (pas paper_portfolio_state.json).

CLEAN-001 : 9 strategies overfittees archivees (archive/rejected/).

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
| BCE Momentum Drift v2 | 14.93 | 77% | 99 | VALIDATED | **DEPLOYE** (disabled: 8 events/an) |
| Auto Sector German | 13.43 | 75% | 97 | VALIDATED | **LIVE** |
| Brent Lag Play | 4.08 | 58% | 729 | 4/5 PASS | **LIVE** |
| EU Close -> US Afternoon | 2.43 | 60% | 113 | VALIDATED | **DEPLOYE** (needs Alpaca live) |

3 strats EU LIVE sur IBKR port 4002 (clientId=11), concentrees 2 tickers/strat, 15% alloc/strat.

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

### 2.10 Crypto Binance France — Portefeuille INDEPENDANT ($10K post-realloc, Margin + Spot + Earn)

**Capital** : $10K post-realloc 31 mars (etait $23.8K, surplus retirer en EUR pour IBKR+Alpaca).
**3 wallets** : Spot $2.5K | Earn $5K (BTC+USDC) | Cash $1K | Margin $1.5K
**Paires USDT bloquees** (TRD_GRP_002) → mapping auto USDT→USDC. Fees BNB -25%.

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

**Portefeuille Crypto INDEPENDANT ($10K post-realloc, Binance France V2) :**

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

### V12 — Regime Engine + Risk of Ruin + Chaos Engineering (15 modules)

| Module | Fichier | Role | Declenchement |
|--------|---------|------|---------------|
| **Multi-Asset Regime** | core/regime/multi_asset_regime.py | 6 regimes (TREND/MR/HIGHVOL/PANIC/LOWLIQ/UNKNOWN), hysteresis 2 periodes | 15min |
| **Activation Matrix** | core/regime/activation_matrix.py | 22 strats x 6 regimes → multiplier 0.0-1.0, YAML config | A chaque signal |
| **Regime Scheduler** | core/regime/regime_scheduler.py | Orchestre detection + alerte Telegram si changement | 15min |
| **MC Portfolio** | core/risk/monte_carlo_portfolio.py | 10K sims Cholesky correle, P(DD>10%), P(ruin) | Daily 07h CET |
| **RoR Scheduler** | core/risk/ruin_scheduler.py | Auto DEFENSIVE si P(DD>10%)>15%, STOP si P(ruin)>1% | Daily 07h CET |
| **Stress Scenarios** | core/risk/stress_scenarios.py | 6 crises historiques (COVID/CHF/FTX/Volmageddon/Corr/Liq) | A la demande |
| **Double-Fill Detect** | core/execution/double_fill_detector.py | 60s window, auto-close excess, CRITICAL alert | A chaque fill |
| **Emergency Close All** | core/risk/emergency_close_all.py | 3 brokers parallele, TOTP confirm, 30s timeout | /emergency Telegram |
| **Unified Portfolio** | core/risk/unified_portfolio.py | NAV cross-broker, DD global, circuit breakers 3%/5%/8% | 4h |
| **Cross-Asset Corr** | core/risk/cross_asset_correlation.py | 5 paires (BTC/SPY/EUR/DAX/Gold), HRP penalty | 4h |
| **Shadow Logger** | core/validation/shadow_logger.py | Signal→fill slippage, alerte si >2x backtest | A chaque trade |
| **Fidelity Score** | core/validation/fidelity_score.py | Score 0-1 backtest vs live (FIDELE/DEGRADE/ECHEC) | 30+ jours data |
| **Live Tracker** | core/validation/live_tracker.py | Sharpe rolling, Z-score vs OOS, auto-KILL si z<-3 | Daily |
| **Tax Classifier** | core/tax/trade_classifier.py | FR fiscal: PFU 30%, crypto-crypto exempt, forms 2086/2074/3916-bis | A chaque cloture |
| **Backup/Restore** | scripts/backup.sh + restore.sh | Daily 03h UTC, 30j retention, 9.9MB tar.gz | Cron |

**Circuit breakers GLOBAUX V12 :**
- DD global > 3% jour → reduce ALL sizing 50%
- DD global > 5% semaine → DEFENSIVE mode GLOBAL
- DD global > 8% mois → CLOSE ALL (emergency_close_all)

**Regime activation — exemples clefs :**
- FX Carry en PANIC → multiplier 0.2 (floor, pas 0.0)
- Crypto Liq Momentum en PANIC → multiplier 1.0 (concu pour)
- VIX Short en HIGH_VOL → multiplier 0.2 (floor)
- Corr Hedge en PANIC → multiplier 1.0 (hedging)
- Post-PANIC → rampe 0.2 → 0.4 → 0.6 → 0.8 → 1.0 sur 4 cycles (1h)

**Minimum exposure floor (20%)** : jamais flat sur tout. Meme en PANIC, 20% du sizing reste actif.
**Re-entry ramp** : apres sortie de PANIC, retour progressif sur 4 periodes (anti-whipsaw + anti missed-rebound).
**9 stress scenarios** : 6 historiques (COVID/CHF/FTX/Volmageddon/Corr/Liq) + 3 synthetiques (CORR=1, ZERO_LIQ, SLIP x5). 9/9 PASS.

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
| Worker Hetzner VPS | **ACTIF** | nohup 24/7, heartbeat 30min, port 4002 LIVE |
| Hetzner VPS | **ACTIF** | 178.104.125.74, VNC :5900, IB Gateway 10.45, port 4002 live + 4003 paper |
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
| V12 Regime Engine | **ACTIF** | 6 regimes, 22 strats, cycle 15min, FX+crypto metrics, Telegram alert |
| V12 Monte Carlo RoR | **ACTIF** | Daily 07h CET, 5K sims, auto DEFENSIVE/STOPPED |
| V12 Unified Portfolio | **ACTIF** | Cross-broker NAV (Binance+IBKR+Alpaca), DD global, circuit breakers |
| V12 Double-Fill Detect | **ACTIF** | 60s window, auto-close excess, wired into all fill paths |
| V12 Shadow Logger | **ACTIF** | Signal→fill tracking, slippage alerte 2x backtest |
| V12 Emergency Close | **ACTIF** | /emergency Telegram, TOTP code, 3 brokers, kill_switch_callback |
| V12 Tax Classifier | **ACTIF** | FR fiscal auto: crypto-crypto exempt, PFU 30%, forms auto |
| V12 Cross-Asset Corr | **ACTIF** | 5 paires, HRP penalty, diversification score |
| V12 Backup Quotidien | **ACTIF** | Cron 03h UTC, 30j retention, 9.9MB, restore playbook |
| V12 Live Tracker | **ACTIF** | Sharpe rolling vs OOS, alpha decay z-score, auto-KILL |
| **V13 CycleRunners** | **ACTIF** | 9 cycles wrapes, error boundaries, health HEALTHY/DEGRADED/FAILED |
| **V13 MetricsPipeline** | **ACTIF** | SQLite backend, 20+ metriques, retention 90j, flush 30s |
| **V13 AnomalyDetector** | **PRET** | 18 regles (threshold/trend/absence), cooldown, alerting |
| **V13 EventLogger** | **ACTIF** | JSONL deterministe, rotation daily, purge 30j, replay-compatible |
| **V13 WorkerState** | **ACTIF** | Etat partage thread-safe, locks granulaires, snapshot() |
| **V13 TaskQueue** | **PRET** | PriorityQueue 5 niveaux, 3 worker threads, timeout, retry |
| **V13 OrderStateMachine** | **PRET** | 9 etats, guards SL, transitions illegales bloquees (non cable) |
| **V13 PositionSM** | **PRET** | 7 etats, ORPHAN detection, invariants SL (non cable) |
| **V13 OrderTracker** | **PRET** | Registry thread-safe, lifecycle complet (non cable) |
| **V13 BrokerHealth** | **PRET** | HEALTHY/DEGRADED/DOWN/MAINTENANCE, sizing multiplier (non cable) |
| **V13 ContractRunner** | **PRET** | Validation structure API, 3 violations = CRITICAL (non cable) |
| **V13 PartialData** | **PRET** | Frozen NAV, regime UNKNOWN, DD partiel (non cable) |
| **V13 ReplayEngine** | **PRET** | CLI + API, timeline, filter by cycle/time/type |
| **V13 IncidentReport** | **PRET** | Markdown auto, resume Telegram, contexte 30min |
| **V13 ShadowMode** | **PRET** | Signal logger + comparateur divergences |
| **V13 Deploy** | **PRET** | deploy.sh (shadow→promote→rollback), pre_deploy_check.py |
| **V13 ResponseSnapshots** | **PRET** | Snapshots API broker, retention 7j |
| **V13 CyclesDashboard** | **PRET** | GET /api/cycles, health + system + queue |

**Fiscalite crypto FR (V12 automatise)** : TradeTaxClassifier classe chaque trade. PFU 30% sur cessions vers EUR. Echanges crypto-crypto non imposables. Formulaire 2086 (PV crypto) + 3916-bis (comptes etranger = Binance, IBKR, Alpaca). Methode PMP.

**V13 Note** : 22 modules robustesse crees et testes (181 tests). ACTIF = integre dans worker.py. PRET = code + tests OK, non cable dans le live path. Prochaine etape : integration OrderTracker + BrokerHealth dans les brokers live.

---

## 8. TESTS ET QUALITE

| Metrique | V12.5 | **V13.0** |
|----------|:--:|:------:|
| Tests total | 2,998 | **3,297** (+299) |
| Echecs | 0 | **0** |
| Fichiers test | ~116 | **~130** (+14) |
| Lignes de code | ~185,000 | **~195,000** |
| Fichiers Python | ~545 | **~575** (+30 modules robustesse) |

| Category | Tests |
|----------|:-----:|
| Core risk+execution (LiveRiskManager, KillSwitch, Reconciliation, VaR, Alerting) | ~280 |
| Broker+trading engine (TradingEngine, Brackets, FX Live, Signal Sync) | ~200 |
| BacktesterV2 (Engine, DataFeed, Execution, Portfolio, Calendars, WF, MC) | ~180 |
| Crypto (Broker, Data, Backtest, Risk, Strategies, Allocation, Monitoring, ROC) | ~220 |
| Strategies V2 (IBKR 40 + Crypto 40 + FX nouvelles 30 + Futures 56) | ~170 |
| Hardening+fuzzing+stress (Fuzzing 28, Stress 9, Resilience 5, Kill E2E 11) | ~100 |
| V10 portfolio-aware (Risk 72 + Execution 54) | 126 |
| Zero-bug regression (worker audit, kill switch, DD, paper/live) | 22 |
| Pipeline EU multi-strat | 100 |
| Telegram commands (V13: /health enrichi) | 46 |
| Live endpoints + dashboard | 30 |
| **V13 TaskQueue + CycleRunner** | **39** |
| **V13 WorkerState** | **14** |
| **V13 OrderStateMachine + Tracker** | **35** |
| **V13 PositionSM** | **7** |
| **V13 EventLogger** | **16** |
| **V13 MetricsPipeline** | **16** |
| **V13 AnomalyDetector + BrokerHealth + Contracts + Partial** | **54** |
| Preflight + bot_service (cleanup V12) | 118 |
| Other (Tax 55, Autonomous 53, Leverage 40, Backup 8, etc.) | ~224 |
| **TOTAL** | **3,297** |

Audit CRO : **9.5/10** (12/12 domaines PASS, 67 fixes cumules)

---

## 9. MODULES CORE (~125)

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

**V12 Regime (4)** : multi_asset_regime (6 regimes, hysteresis), activation_matrix (22x6 YAML), regime_scheduler (worker integration), config/regime.yaml

**V12 Risk (6)** : monte_carlo_portfolio (Cholesky 10K sims), ruin_scheduler (daily auto-action), stress_scenarios (6 crises), emergency_close_all (multi-broker TOTP), unified_portfolio (cross-broker NAV), cross_asset_correlation (5 paires, HRP penalty)

**V12 Execution (1)** : double_fill_detector (60s window, auto-close)

**V12 Validation (3)** : shadow_logger (signal→fill slippage), fidelity_score (backtest vs live), live_tracker (Sharpe rolling, alpha decay KILL)

**V12 Tax (1)** : trade_classifier (FR PFU 30%, crypto-crypto exempt, forms 2086/2074/3916-bis)

**Support (6)** : telegram_commands V6, market_impact, capital_scheduler, event_calendar, alpha_decay_monitor, monitoring (RAM/CPU)

---

## 10. FEUILLE DE ROUTE V12 (POST-REALLOC)

| Phase | Capital | Delai | Strategies live | Cle |
|-------|:-------:|:-----:|:--------------:|-----|
| **ACTUEL** | $20K (10K BNB + 10K IBKR) | Maintenant | **14** (12 crypto + 2 FX carry) | V12 regime engine actif |
| **+Alpaca** | $45K (+$25K Alpaca) | ASAP (capital arrive) | **19** (+5 US paper→live) | Pre-live validation script |
| **Phase 2** | $50K (+$5K IBKR) | +1 mois | 22+ | +futures paper→live, +EU |
| **Phase 3** | $50K | +3 mois | 25+ | Meta-strategy scorer, fidelity gate |
| **Phase 4** | $75K+ | +6 mois si KPI OK | 30+ | Full Kelly, PostgreSQL, tax reports |

### KPI de validation (avant chaque scale-up)

**Gate M1** ($10K->$15K) : Min 15 trades live, Max DD < 5%, Sharpe > 0.3 (secondaire), WR > 42%, PF > 1.1, 0 bug
**Gate M2+** ($15K->$25K) : Min 50 trades cumules, Max DD < 8%, Sharpe > 1.0, WR > 48%, PF > 1.3, 0 bug

### Conditions passage live (checklist V12)

**Broker & Connectivity**
- [x] IBKR live FX (port 4002, clientId=10, premiers trades 31 mars)
- [x] Binance live (12 strats, paires USDC, cycle 15min 24/7)
- [x] VPS Hetzner 5 services systemd (worker, watchdog, dashboard, gateway live+paper)
- [ ] Alpaca live (en attente capital $25K)

**V12 Protection Capital**
- [x] Regime engine actif (6 regimes, 22 strats, PANIC bloque FX carry)
- [x] Monte Carlo portfolio (RoR daily 07h CET, auto DEFENSIVE)
- [x] Stress scenarios (6 crises historiques)
- [x] Double-fill detector branche sur tous les fill paths
- [x] Emergency close all-broker (/emergency Telegram + TOTP)
- [x] Unified portfolio cross-broker (3 brokers, DD global, circuit breakers)
- [x] Backup quotidien (cron 03h UTC, 30j retention, restore playbook)

**Monitoring**
- [x] 15 commandes Telegram (/regime, /portfolio, /emergency)
- [x] Shadow trade logger (slippage signal→fill)
- [x] Live performance tracker (alpha decay z-score)
- [x] Tax classifier automatique (FR PFU 30%)

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
| **29 mars soir** | **LIVE DEPLOY : IBKR live (port 4002, IB Key push 2FA), Binance live (12 strats, $23K), worker Hetzner systemd, sizing sur equity live, fix kwargs 12 strats crypto** |
| **30 mars** | **V11 HRP+Kelly deploye, dashboard instit, 13 bugs fixes, ROC optim, BEAR strats, premiers trades live IBKR** |
| **31 mars AM** | **Realloc Binance executee : $23.8K→$10K, sell 0.123 BTC @ $67,950, configs 6 fichiers MAJ** |
| **31 mars PM** | **Watchdog IB Gateway auto-restart 2FA, pre-live verdict par broker, Binance last_price fix** |
| **1 avril AM** | **V12.0 : +15 modules deployes Hetzner, 8/8 init OK. Regime branche FX+crypto. RoR daily 07h. Backup cron 03h. 15 cmds Telegram.** |
| **1 avril PM** | **V12.1 : 3 fixes post-audit GPT — min exposure floor 20%, re-entry ramp 4 periodes, +3 stress synthetiques (corr=1, liq=0, slip x5). 9/9 stress PASS.** |
| **2 avril** | **V12.5 ZERO-BUG AUDIT : 40 bugs fixes (12 worker, 8 V12, 5 secu, 3 kill chain, 12 paper/live). Kill chain unifiee. Paper/live isoles. DD crypto excl earn passif. Warmup 3 cycles. 22 regression tests. CRO 9.5/10.** |
| **3 avril AM** | **Fix V10 safety mode DD 90.9% (paper default=False). Vendredi Saint = marches EU/US fermes.** |
| **3 avril** | **Cleanup V12 : refactor worker.py (3800→3292 lignes, 6 modules extraits core/worker/), ruff lint 600+ fichiers, archive intraday-backtesterV2, 118 tests (bot_service+preflight). 3,116 tests.** |
| **3 avril** | **V13.0 ROBUSTESSE STRUCTURELLE XXXL : 22 taches, 7 chantiers (R1-R7). TaskQueue PriorityQueue, CycleRunners (9 cycles), WorkerState thread-safe, OrderStateMachine (9 etats), PositionSM (7 etats), EventLogger JSONL, MetricsPipeline SQLite, AnomalyDetector 18 regles, BrokerHealth 4 etats, ContractTesting 3 brokers, PartialDataHandler, ReplayEngine, ShadowMode, deploy.sh+rollback, pre_deploy_check.py. 181 tests robustesse. 3,297 tests total. CRO 8.5/10 (modules PRETS, non cables dans live path).** |

---

## 12. VERDICT FINAL

26 phases en 11 jours (22 mars - 1 avril 2026) : Expansion (3->34 strats) -> Critique (purge 9 overfittees) -> Consolidation (WF, VaR, MC) -> Expansion V5 (4 classes) -> Live-Ready V6 (14 modules) -> Hardening V7 (27 bugs) -> CRO V7.2 GO-LIVE -> ROC V7.3 -> Crypto V7.5 (8 strats Binance) -> CRO V7.6 -> BacktesterV2 (event-driven, WF, MC) -> Hardening S3 (fuzzing+stress) -> CRO 9.5/10 -> Dashboard XL + Crypto LIVE V8.5 -> Crypto ROC V9.0 -> Audit CRO V9.0 (27 fixes) -> V9.5 (+13 strats, Hetzner VPS, 265K candles) -> V10.0 Portfolio-Aware (8 modules risk) -> V11 HRP+Kelly deploye -> **Realloc Binance $23K->$10K** -> **V12.0 Regime Engine + RoR + Chaos (15 modules, 15 cmds Telegram, backup daily)**

### AUDIT CRO V13.0 — Score 9.5/10 (toutes reserves corrigees)

| Domaine | **V13.0** | Amelioration V13 |
|---------|:-------:|-----------------|
| D1 Execution ordres | **PASS** | SL MANDATORY (validate_order + CryptoOrderManager), OrderTracker cable, bracket IBKR |
| D2 Gestion risque | **PASS** | Kill chain unifiee, CycleRunners error boundaries, BrokerHealth init |
| D3 Integrite donnees | **PASS** | .shift(1), guard ET, timezone zoneinfo |
| D4 Coherence BT/live | **PASS** | Commissions coherentes ($0 Alpaca, 0.10% Binance, $2 IBKR FX) |
| D5 Securite | **PASS** | env vars, guards, pre-deploy checklist 6 checks |
| D6 Moteur backtest | **PASS** | Determinisme, pas de double position |
| D7 Strategies actives | **PASS** | 11 crypto + 1 FX + 3 EU live, WF validated |
| D8 Pipeline | **PASS** | CycleRunners (9 cycles), MetricsPipeline, EventLogger actifs |
| D9 Monitoring | **PASS+** | AnomalyDetector 18 regles, /health enrichi, dead man's switch, ReplayEngine |
| D10 Infrastructure | **PASS** | deploy.sh canary, SIGTERM ferme positions + cancel ordres + flush metrics |
| D11 Compliance | **PASS** | PDT rule, tax classifier FR, wash sale detect |
| D12 Documentation | **PASS** | Synthese V13.0, CLAUDE.md a jour, 3320 tests |

**Reserves corrigees (session 3 avril) :**
- [H-1] CORRIGE : OrderTracker cable dans CryptoOrderManager (DRAFT→VALIDATED→SUBMITTED→FILLED tracke)
- [H-2] CORRIGE : validate_order() rejette tout ordre sans stop_loss (reduce_only exempte)
- [H-3] CORRIGE : SIGTERM ferme Binance + cancel Alpaca + flush metrics/events
- [M-1] CORRIGE : /health enrichi (pid, memory_mb, cpu, uptime, cycles health)
- [M-2] CORRIGE : CLAUDE.md mis a jour (3320 tests)
- [M-3] CORRIGE : Dead man's switch dans worker loop (alerte si heartbeat >35min)
- [M-4] CORRIGE : Couts US alignes sur Alpaca $0 commission
- [L-1] CORRIGE : gc.collect() a 300MB dans worker loop
- [L-2] CORRIGE : 23 tests CRO reserves (SL mandatory, OrderTracker, SIGTERM, health, dead man's switch)

### Prochain pas

**ACTIF depuis 2 avril :**
- IBKR $10K live : 1 FX carry momentum (4 paires), 3 EU intraday (Gap, Sector, Brent), clientId=10/11
- Binance $10K live : 11 strats crypto, cycle 15min 24/7, paires USDC, dd_equity excl earn
- Alpaca paper : RSI2 Mean Reversion + daily strategies
- VPS Hetzner : worker nohup, IB Gateway watchdog, backup quotidien

**Allocation cible :** Binance $10K + IBKR $10K (fait) + Alpaca $25K (en attente capital) = $45K

**V13.0 operationnel :** CycleRunners actifs (9 cycles, error boundaries), MetricsPipeline SQLite, EventLogger JSONL, AnomalyDetector configure, /health Telegram enrichi. 22 modules robustesse PRETS pour integration Phase 2.
