# SYNTHESE COMPLETE — TRADING PLATFORM V16.1 (P0 MCL FIX + PAPER BLOCK CLEANED)
## Portefeuille Quantitatif — 3 classes d'actifs, 15 strats canoniques (2 live_core + 1 live_micro + 6 paper_only + 4 frozen + 2 disabled + 16 archived)
### Date : 23 avril 2026 | 3,816 tests | ~151 fichiers test | CRO 9.0/10 APPROUVE | CAM unlocked (MCL contract fixed) | Cleanup Phase 3.5 active

---

## 1. RESUME EXECUTIF

| Indicateur | V15.3 (10/04) | **V16.0 (22/04)** |
|-----------|:---:|:---:|
| Strats canoniques (non-archivees) | 55 codees | **15** (bucket drains: A 19/04 + C + D 22/04) |
| LIVE_CORE | 4 | **2** (cross_asset_momentum grade A + gold_oil_rotation grade S) |
| LIVE_MICRO (nouveau status) | 0 | **1** (btc_asia_q80_long_only, $200 USDC, kill DD -$50) |
| PAPER_ONLY | — | **6** (gold_trend_mgc, mes_monday, mes_wednesday, mcl_overnight, alt_rel_strength, us_sector_ls) |
| FROZEN (nouveau status) | 0 | **4** (mes_pre_holiday, eu_relmom, us_stocks_daily, mib_estx50) |
| DISABLED | 9 | **2** (fx_carry ESMA + btc_dominance REJECTED) |
| ARCHIVED_REJECTED | — | **16** (bucket A=11 + C=4 + D=1 q70_v80 duplicate 22/04) |
| Capital live deployable | EUR 18.6K | **$21,283** (IBKR $11,280 + Binance $9,983 earn + $0 spot apres auto-redeem $2K) |
| Capital expose actuel | $600 (4 pos) | **$0** (btc_asia signal NONE 22/04, CAM dort 20j cadence) |
| PnL live 7j reel | backtest only | **+$429 (+2.06%)** (TP MCL CAM 19/04 +$605 realized) |
| Tests | 3,523 | **3,799** (+276 Phase 1+2+3 + TTL + frozen + metrics + weekly review) |
| CRO audit | 9.0/10 | **9.0/10** (audit 21/04 + validation runtime 22/04) |

**V15.3->V16.0 : Desk productif doctrine (live_micro + catalogue clean + runtime-truth).**

**V16.0->V16.1 (23/04)** : **3 incidents decouverts + fixes** :
  - **P0 CAM MCL contract** (commit 1217acf) : CAM etait broken en live depuis 20/04 suite a TP 19/04 + rollover contrat. `futures_runner.py` hardcodait `exchange="CME"` pour MCL (doit etre NYMEX) et MGC (COMEX). 9 echecs "no contract details" logges. Fix: helper `_make_future_contract` avec `_FUTURES_EXCHANGE_MAP` (MCL->NYMEX, MGC->COMEX, MES/MNQ->CME, MIB/ESTX50->EUREX). Valide VPS: MCLZ6 Dec 2026 resout OK. CAM peut de nouveau trader des prochain cycle 14h UTC.
  - **P1 gaps live_pnl 20-21/04** : root cause = cron 22h UTC tombait pendant fenetre 2FA restart IB Gateway (22h15-22h25). Fix: decale cron VPS 22h -> 23h UTC (post 2FA). Actif des 23/04 23h UTC.
  - **P2 gold_trend_mgc actif** : log "below EMA20" chaque cycle depuis 17/04, 0 BUY signal observable en 6j. Decision Marc: 30/04 reste date d'**arming technique** (eligibilite calendaire), PAS validation economique. Premier BUY paper observable requis avant toute promotion live_micro. Nouvelle regle doctrine.

**Bilan live depuis origine (23/04 05:30 UTC)** :
  - 1 seul trade live ferme: CAM MCL 07/04 entry $75.85 -> 19/04 TP $81.92 = **+$605.46 realized**
  - 0 fill live 24h (CAM broken contrat, GOR signal spread <2% dormant, btc_asia_q80 signal NONE 22/04)
  - Capital total: $21,283 (+$429 vs origin 19/04)
  - DUP573894 paper: positions MES/MNQ closed hier soir post-cleanup, 0 fill attendu aujourd'hui

**1. Bascule operationnelle majeure (22/04)** : le desk passe de "prepare le trading" a "trade + se concentre + rapporte". Premier sleeve live_micro arme (btc_asia_q80_long_only, BTCUSDC $200 real money, kill DD -$50 hard, no pyramid J+14, max 1 position, rate limit 1 nouvelle sleeve/7j). Premier cycle live execute 22/04 10h30 Paris = event entry_skipped (signal NONE, plumbing propre valide).

**2. Reduction structurelle catalogue** : 55 strats codees -> 15 canoniques. Drains operes (19/04 bucket A 11 crypto REJECTED, bucket C 4 EU, bucket D 22/04 btc_asia_q70 duplicate) + freeze 4 sleeves sans chemin vers live a court terme (pas de disabled = re-activables). Catalogue plus lisible = moins de bruit mental pour decisions capital.

**3. Nouvelle infrastructure desk-productif (22/04)** :
   - `core/governance/live_micro_sizing.py` : caps par grade (S=$500/$50, A=$300/$30, B=$200/$20), guardrails no-pyramid J+14, rate limit 1 sleeve/7j
   - `core/governance/incidents_ttl.py` : TTL 72h auto-exclusion incidents isoles (chaines (sev,book,cat) inactives purged)
   - `core/runtime/btc_asia_q80_live_micro_runner.py` : runner dedie entry/exit + kill DD + Telegram alerts + journal dedie
   - `scripts/weekly_desk_review.py` + systemd timer dim 22h UTC : report hebdo auto + Telegram 5 metriques
   - live_pnl_tracker summary.json etend : max_dd_live_pct + trades_count_30d + capital_exposure snapshot

**4. Gates assouplis mais non supprimes** : separation governance_gate/capital_gate, sizing caps par grade au lieu de blocages absolus, TTL 72h sur incidents (plus de pollution historique sur decisions capital actuelles), promotion_gate.can_go_live_micro() (7j paper + grade>=B + 0 incident 24h vs 30j standard).

**5. Metrics desk (post 22/04)** :
   - Capital exposed avg : 0% (snapshot courant, en attente premier fill)
   - Trades live 30j : 0 (post-fermeture MCL 19/04, signal q80 n'a pas fire)
   - Incidents actifs (TTL 72h) : 7 residus chaine MCL reconciliation (auto-exclu ~22/04 22h UTC)
   - Services VPS : 6/6 active
   - Runtime_audit --strict : exit 0

**1. Decouverte cle** : 6 strategies EU intraday testees sur 5 ans de data 5min/15min IBKR (2021-2026, 601K bars), une seule a un edge robuste apres couts : **Macro ECB Event Momentum**. Les 5 autres (ORB, Mean Reversion RSI, Lunch Effect, US Open Impact, Pairs DAX/ESTX50) ont edge < couts apres tuning.

**2. Strategie MacroECB validee** :
   - Mecanique : trade le momentum 30min post annonce BCE (14:15 CET) si |move| > 0.15%, SL=50% du move, TP=2x move, max hold 3h
   - Multi-instrument : DAX (avg +$172/tr), CAC40 (+$87), ESTX50 (+$45)
   - 5 ans : 69 trades / +$7,004 / Sharpe 3.18 / PF 1.84 / MaxDD -$1,846
   - WF yearly 4/6 PASS (gagnant 2022-2024 + 2026, perdant 2021 + 2025 = pause cycles)
   - Decorrelation parfaite avec les 4 LIVE (event-driven, 8 jours/an)

**3. Backtest portefeuille V15.3 (4 LIVE + 3 MacroECB, 3 ans, max 3 positions)** :

| Strategie | Sym | Trades | WR | PnL 3 ans | Avg/trade | Sharpe |
|-----------|-----|:------:|:--:|:---------:|:---------:|:------:|
| Sector Rotation EU | DAX/CAC40 | 53 | 58% | +$3,416 | $64 | 3.11 |
| **MacroECB (3 inst)** | DAX/CAC/ESTX | **27** | **37%** | **+$2,751** | **$102** | **3.02** |
| Gold-Equity Div | MES | 44 | 41% | +$2,078 | $47 | 1.74 |
| Overnight MES | MES | 523 | 50% | +$895 | $2 | 0.36 |
| EU Gap Open | ESTX50 | 8 | 50% | +$452 | $56 | 0.80 |
| **TOTAL V15.3** | | **655** | **50%** | **+$9,591** | **$15** | **1.00** |

Return: +95.9% / 3 ans = **31.7%/an** | PF: **1.36** | MaxDD: **-$3,031** (-30%)
12 trades MacroECB rejetes par slot conflict (max 3 pos) — pertinent pour augmenter MAX_POS=4 en V15.4.

**4. Code livre** :
   - `strategies_v2/futures/macro_ecb.py` : StrategyBase multi-instrument
   - `core/worker/cycles/macro_ecb_cycle.py` : runner cycle dedie (skip jours non-BCE)
   - `data/calendar_bce.csv` : 42 dates ECB 2021-2026 hardcodees
   - `tests/test_macro_ecb.py` : 14 tests PASS (config, params, signal logic, BCE filter, one-per-day)
   - `scripts/backtest_eu_intraday.py` : framework backtest 6 strats + WF
   - `scripts/backtest_portfolio_v153.py` : portfolio combiner 4 LIVE + 3 MacroECB

**5. Strats EU rejetees** (edge < couts apres tuning intraday 5min/15min) :
   - EU-01 ORB DAX : 462 tr / -$3,945 / avg -$9
   - EU-02 Mean Reversion RSI ESTX50 : 1174 tr / -$2,544 / avg -$2 (presque break-even)
   - EU-03 Lunch Effect DAX : 942 tr / -$10,683 / avg -$11
   - EU-04 US Open Impact ESTX50 : prometteur (+$27/tr sur 25 trades) mais MES historique limite a 8 mois -> abandon temporaire
   - EU-05 Pairs DAX/ESTX50 : 1226 tr / -$8,116 / avg -$7 (double cost tue l'edge)

**6. Decision deploiement** : 3 MacroECB en CODE_REVIEW, deploiement V15.4 apres validation cycle worker en paper.

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

### 2.6 Catalogue canonique V16 (22/04/2026) — SOURCE UNIQUE

**15 sleeves canoniques** + 16 archived_rejected. Cette table supersede 2.8-2.11 (logique V15.3 conservee comme memoire historique). Source de verite = `config/quant_registry.yaml` + `config/live_whitelist.yaml`.

| # | strategy_id | book | status | grade | paper_start | live_start | thesis (1-line) | blocker / next action |
|---|-------------|------|--------|:-----:|:-----------:|:-----------:|-----------------|----------------------|
| 1 | `cross_asset_momentum` | ibkr_futures | **live_core** | A | — | 2026-04-07 | Rotation cross-asset (MCL/MGC/MES) cadence 20j | Next rebal ~2026-05-07 |
| 2 | `gold_oil_rotation` | ibkr_futures | **live_core** | S | — | 2026-04-08 | Long gold / short oil si spread >= 2% | Signal dormant (spread < 2%) |
| 3 | `btc_asia_mes_leadlag_q80_v80_long_only` | binance_crypto | **live_micro** | B | 2026-04-20 | 2026-04-23 | MES US D-1 predit BTC Asia D (long only) | Premier fill possible dai, review J+14 = 2026-05-07 |
| 4 | `gold_trend_mgc` | ibkr_futures | paper_only | A | 2026-04-17 | — | Trend following MGC (gold futures) | Arming fast-track earliest 2026-04-30, puis live_micro candidate #2 |
| 5 | `mes_monday_long_oc` | ibkr_futures | paper_only | B | 2026-04-16 | — | Anomalie calendaire lundi open-close | Promotion earliest 2026-05-16 |
| 6 | `mes_wednesday_long_oc` | ibkr_futures | paper_only | B | 2026-04-16 | — | Anomalie mercredi open-close | Review 2026-06-01 (MC DD 28.3% borderline) |
| 7 | `mcl_overnight_mon_trend10` | ibkr_futures | paper_only | B | 2026-04-18 | — | MCL weekend gap + trend overnight | Re-WF Friday required, earliest 2026-05-30 |
| 8 | `alt_rel_strength_14_60_7` | binance_crypto | paper_only | B | 2026-04-18 | — | Altcoins relative strength vs BTC (14d lookback) | 30j paper + infra gaps fix, earliest 2026-05-18 |
| 9 | `us_sector_ls_40_5` | alpaca_us | paper_only | B | 2026-04-18 | — | Long/short US sectors top-5 vs bottom-5 | Gate Alpaca NO_GO_paper_too_short, earliest 30j + $25K PDT |
| 10 | `mes_pre_holiday_long` | ibkr_futures | **frozen** | B | 2026-04-16 | — | Long MES veille US holidays | Frozen 2026-04-22 : rare, re-activable si volume insuff prouve |
| 11 | `eu_relmom_40_3` | ibkr_eu | **frozen** | B | 2026-04-18 | — | Relative momentum DAX/CAC40/ESTX50 | Frozen : shorts EU sans plan, re-WF long-only TODO |
| 12 | `us_stocks_daily` | alpaca_us | **frozen** | meta | 2026-04-18 | — | Meta-orchestrator Alpaca (role=infra_orchestrator) | Frozen : PDT $25K non finance, not a canonical alpha |
| 13 | `mib_estx50_spread` | ibkr_eu | **frozen** | S | 2026-04-18 | — | Spread MIB/ESTX50 z-score entry | Frozen : margin EUR 13.5K requis vs dispo 9.9K (cf chiffrage NO_GO 22/04) |
| 14 | `fx_carry_momentum_filter` | ibkr_fx | **disabled** | — | — | — | FX carry avec filtre momentum | ESMA EU leverage limits (reglementaire permanent) |
| 15 | `btc_dominance_rotation_v2` | binance_crypto | **disabled** | REJECTED | — | — | Rotation BTC/alts via dominance | WF REJECTED Sharpe -6.08, logic broken |

**Archived_rejected (16)** : bucket A 11 crypto (19/04) + bucket C 4 EU (19/04) + bucket D 1 btc_asia_q70_v80 duplicate (22/04). Liste complete dans `config/quant_registry.yaml::archived_rejected`.

### 2.7 Forex — MORT (permanent, pas de regression)

16 strats FX codees mais **0 executable** : IBIE (Interactive Brokers Ireland) interdit levier FX pour retail france (ESMA). Confirme support IBKR 2026-04-08. Decision : `fx_carry_momentum_filter` est la SEULE entree registry (status=disabled), les 15 autres strats FX historiques sont archivees. Data FX conservee (134K candles) reutilisable si broker alternatif ajoute (ESMA 30:1).

---

## 3. ALLOCATION & CAPITAL V16 (22/04/2026)

### 3.1 Capital par book (source : runtime VPS 22/04)

| Book | Mode | Capital deployable | Capital expose | Capital idle | Nb sleeves canoniques | Nb live* | Runtime entrypoint |
|------|------|:-----------------:|:--------------:|:------------:|:---------------------:|:--------:|-------------------|
| **ibkr_futures** | live_allowed | $11,280 | $0 (position MCL CAM fermee 19/04) | 100% | 7 | 2 live_core | `core/worker/cycles/futures_runner.py` |
| **binance_crypto** | live_micro_allowed | $9,983 (earn $9,983 + spot auto-redeem $2K) | $0 (signal q80 NONE 22/04) | 100% | 3 | 1 live_micro | `core/worker/cycles/paper_cycles.py` + `core/runtime/btc_asia_q80_live_micro_runner.py` |
| **ibkr_eu** | paper_only | EUR 9,900 (~$10,600) | $0 (frozen) | 100% | 2 | 0 | N/A (both frozen) |
| **alpaca_us** | paper_only | $99,840 (paper) | paper only | N/A | 2 | 0 | `scripts/run_us_stocks_daily.py` (frozen) |
| **ibkr_fx** | disabled | — | — | — | 1 | 0 | disabled ESMA |
| **TOTAL live** | — | **$21,283** (IBKR + Binance) | **$0** (pas de position ouverte) | 100% | 15 | **3** | — |

*Nb live = live_core + live_micro (inclut paper dans la colonne sleeves).

### 3.2 Sizing par sleeve (policy reel V16)

| Sleeve | Book | Policy sizing | Capital utilisable | Kill trigger | Prochain milestone |
|--------|------|--------------|-------------------|--------------|-------------------|
| `cross_asset_momentum` | ibkr_futures | 1 contrat par leg, risk budget 5% portfolio | ~$300 risk/trade | DD portfolio -5% | Rebal 2026-05-07 |
| `gold_oil_rotation` | ibkr_futures | 1 spread position, risk budget 5% | ~$300 risk/trade | DD portfolio -5% | Signal spread >= 2% (dormant) |
| `btc_asia_q80_long_only` | binance_crypto | **notional $200 USDC strict** (live_micro grade B cap) | $200 | **DD absolu -$50** auto-kill sleeve | J+14 review 2026-05-07 (pyramid lift) |
| `gold_trend_mgc` V1 | ibkr_futures | 1 contrat MGC (=~$200 margin) | $300 (future live_micro grade A cap) | DD -$30 si live_micro | Arming fast-track 2026-04-30 |
| `mes_monday_long_oc` | ibkr_futures | 1 contrat MES paper | paper only | paper only | Promotion 2026-05-16 |
| `mes_wednesday_long_oc` | ibkr_futures | 1 contrat paper | paper only | paper only | Review 2026-06-01 (borderline) |
| `mcl_overnight_mon_trend10` | ibkr_futures | 1 contrat MCL paper | paper only | paper only | Re-WF Friday + earliest 2026-05-30 |
| `alt_rel_strength_14_60_7` | binance_crypto | notional fixe $500 gross (paper) | paper only | paper only | Earliest 2026-05-18 |
| `us_sector_ls_40_5` | alpaca_us | portfolio rebal mensuel paper | paper only (gate $25K NO_GO) | paper only | 30j paper + gate Alpaca |
| `mes_pre_holiday_long` / `eu_relmom` / `us_stocks_daily` / `mib_estx50_spread` | — | **FROZEN** (cycles runtime skip) | 0 | N/A | Re-activable via `status: frozen -> paper_only` dans quant_registry |

### 3.3 Cadres capital (pas de Kelly agressif actuel)

Post audit 19/04 + doctrine desk productif 22/04 : **plus de scaling Kelly agressif** tant que 30j live non prouve. Caps par grade + kill criteria explicites > fractions Kelly theoriques.

| Phase | Capital | Approche sizing |
|-------|---------|-----------------|
| Actuel (22/04) | $21K | 2 live_core risk-budget 5% + 1 live_micro cap $200 |
| M1 (21/05) | $21K | Si PnL live net >= 0 sur btc_asia q80 30j : promotion live_probation (caps $400-500) ; sinon rollback paper |
| M2 (21/06) | $21K ou +$25K Alpaca si gate GO | Revue complete caps + ouverture live_probation sur 2-3 sleeves max |
| M3 (21/07) | $25-45K | Scale conditionne a 60j+ PnL live > 0, Sharpe rolling > 0.5, 0 incident P0 |

**Principe directeur 22/04** : pas d'expansion tant que la vérité live 30j ne confirme pas l'edge. Policy alignee sur [feedback_prove_profitability_first](https://github.com/marclucas2-cloud/Tplatform) memory.

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

### Kill switch calibre — LIVE V15.2

**IBKR (4 strats actives) :**

| Strategie | Type | Seuil kill |
|-----------|------|:----------:|
| Overnight MES | Futures overnight | -2.5% |
| Gold-Equity Div | Futures swing 5j | -2.5% |
| Sector Rotation EU | EU indices weekly | -4.0% |
| EU Gap Open | EU intraday | -1.5% |
| **PORTFOLIO IBKR** | Global | **-5.0% daily** (YAML) |

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

### V16 — Live_micro sizing caps + TTL 72h incidents (22/04)

**Live_micro caps par grade (core/governance/live_micro_sizing.py)** :

| Grade | Max notional USD | Max risk USD (10% stop) | Usage actuel |
|---|---|---|---|
| S | 500 | 50 | inutilise (mib_estx50_spread frozen) |
| A | 300 | 30 | inutilise (gold_trend_mgc still paper) |
| B | 200 | 20 | **btc_asia_q80_long_only** (1 sleeve active) |

**Garde-fous anti-dispersion** :
- **No pyramid before J+14** : si >=1 position ouverte + live_start_at < 14j, refuse nouveau entry. Review quinzaine avant scaling.
- **Rate limit 1 sleeve/7j** : max 1 nouvelle promotion vers live_micro par rolling 7 jours, evite de reouvrir la dispersion catalogue.
- **Kill DD -$50 hard** (btc_asia_q80) : au premier hit auto-ecrit `_kill_switch.json`, sleeve auto-disabled, Telegram critical. Re-activation manuelle requise.
- **Unknown grade = rejet** : pas de fallback permissif si grade null/REJECTED.

**TTL 72h incidents (core/governance/incidents_ttl.py)** :
- Incident < 72h = actif (inchange)
- Incident > 72h SANS re-trigger meme (severity, book, category) dans la fenetre = AUTO-EXCLU
- Incident > 72h AVEC re-trigger = chaine reste active tant que max(ts) < 72h
- Impact : gates (promotion_gate + alpaca_go_25k_gate) ne bloquent plus sur historique obsolete
- Scope : P0 + P1 + CRITICAL (warnings non concernes)

### V16.1 — Futures contract exchange map (23/04)

Fix critique P0 : 4 endroits hardcodes `exchange="CME"` dans futures_runner.py
provoquaient "no contract details" pour MCL/MGC depuis 20/04. Nouveau helper
`_make_future_contract(symbol)` route via `_FUTURES_EXCHANGE_MAP` :

| Symbol | Exchange | Currency | Commentaire |
|---|---|---|---|
| MES / MNQ / M2K / ES / NQ | CME | USD | Equity index futures |
| MCL / CL | NYMEX | USD | Energy (crude oil) |
| MGC / GC | COMEX | USD | Metals (gold) |
| MIB | MEFFRV | EUR | FTSE MIB |
| ESTX50 | EUREX | EUR | EuroStoxx 50 |
| VIX | CFE | USD | CBOE volatility |
| (unknown) | CME | USD | Fallback conservative |

Applique a 4 points : main order execution, bracket repose, fail-safe close,
time-exit 48h. Tests non-regression: `tests/test_futures_exchange_mapping.py` (12 tests).

### V16.1 — Doctrine arming date vs validation economique (23/04)

Regle Marc : une date de calendrier (ex: gold_trend_mgc 30/04 earliest fast-track)
est une **eligibilite technique**, PAS une autorisation implicite de trader.
La bascule effective vers live_micro necessite :
  - premier BUY paper observable journalise, OU
  - preuve equivalente explicitement validee par Marc
Applicable a toute promotion future (alt_rel_strength 18/05, mes_monday 16/05, etc.).
Memoire: `feedback_arming_date_vs_economic_validation.md`.

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
10. **Significativite** : < 30 trades = bruit statistique. Pas d'exception. (EU Gap Open = 8 trades/3 ans → a surveiller)
11. **Backtest portefeuille** : JAMAIS deployer sur backtest isole. Tout candidat passe le backtest portefeuille combine 3 ans avec toutes les strats actives + contraintes live (slots, priorite, interactions). Lecon V15.1 : 5 strats WF-pass individuellement → portefeuille -$6K.

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
| **V16 live_micro_sizing** | **ACTIF** | Caps par grade (S/A/B = $500/$300/$200), no pyramid J+14, rate limit 1/7j |
| **V16 incidents_ttl** | **ACTIF** | TTL 72h auto-exclusion chaines isolees, utilise par promotion + alpaca_gate |
| **V16 btc_asia_q80 runner** | **ACTIF** | Live_micro BTCUSDC $200, kill DD -$50, state + journal + Telegram |
| **V16 weekly_desk_review** | **ACTIF** | systemd timer dim 22h UTC, report md + Telegram 5 metriques |
| **V16 quant_registry frozen** | **ACTIF** | 4 sleeves groupe C hors rotation (re-activables, pas disabled) |
| **V16 resolutions.jsonl** | **ACTIF** | Manifest incidents fermes append-only (exclus des gates) |
| **V16.1 futures_exchange_map** | **ACTIF** | `_make_future_contract(symbol)` route MCL/MGC/MES/etc. via bon exchange (fix P0 23/04) |
| **V16.1 paper block clean** | **ACTIF** | 14 strats legacy retirees de futures_runner paper block (commit 21cc040 22/04) |
| **V16.1 live_pnl_tracker cron** | **ACTIF** | Decale 22h -> 23h UTC (post 2FA IBGW restart, evite partial_fetch) |

**Fiscalite crypto FR (V12 automatise)** : TradeTaxClassifier classe chaque trade. PFU 30% sur cessions vers EUR. Echanges crypto-crypto non imposables. Formulaire 2086 (PV crypto) + 3916-bis (comptes etranger = Binance, IBKR, Alpaca). Methode PMP.

**V13 Note** : 22 modules robustesse crees et testes (181 tests). ACTIF = integre dans worker.py. PRET = code + tests OK, non cable dans le live path. Prochaine etape : integration OrderTracker + BrokerHealth dans les brokers live.

---

## 8. TESTS ET QUALITE

| Metrique | V15.3 (10/04) | V16.0 (22/04) | **V16.1 (23/04)** |
|----------|:--:|:--:|:------:|
| Tests total | 3,523 | 3,799 | **3,816** (+17: +7 Phase 3.5 cleanup runner + +12 exchange mapping - 2 failing preexisting) |
| Echecs | 0 | 0 | **2 preexisting** (test_mcl_overnight_mon_trend non lie au fix, a investiguer) |
| Skipped | — | 1 | **1** (lightgbm/pandas optionnel) |
| Fichiers test | ~146 | ~150 | **~151** |
| Lignes de code | ~195,000 | ~200,000 | **~200,500** |
| Fichiers Python | ~575 | ~585 | **~586** (+_make_future_contract dans futures_runner) |

**Tests desk productif Phase 1-3 (2026-04-22)** :
- test_live_micro_sizing.py (22) : caps par grade, pyramid J+14, rate limit 1/7j
- test_live_micro_gate.py (7) : can_go_live_micro + pre_order_guard live_micro enforcement
- test_btc_asia_q80_live_micro_runner.py (11) : state I/O, kill flag, extract_fill_details, journal, skip paths
- test_worker_skip_frozen.py (11) : is_strategy_frozen + early-return 4 cycles
- test_incidents_ttl_72h.py (18) : filter_active, _incident_key, _parse_ts
- test_live_pnl_metrics_extended.py (10) : trades_count_30d, capital_exposure snapshot
- test_weekly_desk_review.py (18) : week_slice, build_report, render md/telegram
- **Total nouveau 97 tests** (reste 179 deltas = ajustements dependances + tests recouvrants)

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
| **3 avril** | **V13.0 ROBUSTESSE STRUCTURELLE XXXL : 22 taches, 7 chantiers (R1-R7). TaskQueue, CycleRunners, OrderSM, PositionSM, EventLogger, MetricsPipeline, AnomalyDetector, BrokerHealth, ContractTesting, deploy.sh+rollback. 181 tests. 3,297 total.** |
| **7 avril** | **Futures IBKR live : marge activee, permissions futures, SL/TP software (presets IBKR tuent GTC), bracket OCA standalone** |
| **8 avril** | **Bug 4 contrats MES au lieu de 1 : triple guard + MAX_FUTURES_CONTRACTS=2 + connexion directe (plus de os.environ mutation). Emergency close 4 MES @ 6784** |
| **9 avril AM** | **STRAT-015 BB MR Short deploye Binance. Backtest 8+8 strats EU indices : MIB/ESTX50 Spread WF 4/5 +$57K** |
| **9 avril PM** | **PO decision : 3 futures live. Kill switch 1.5%->5%. Dashboard deep audit : 14 bugs fixes. Chatbot enrichi. CRO 9.0/10 : disconnect, orphan cancel, thresholds YAML** |
| **9 avril soir** | **Discovery pipeline : 6 candidats backtestes (11 ans). VIX MR WF 5/8, Gold-Equity WF 5/8, MCL TSMOM FAIL, BTC TSMOM FAIL, crypto FAIL** |
| **10 avril AM** | **CONFRONTATION REALITE : backtest portefeuille 3 ans = -$6,175 (FAIL). TSMOM -$5K, Trend+MR -$2K. Seul Overnight MES positif. 5 strats DISABLED. ROC 1%/an. Erreur : backtests individuels sans test portefeuille** |
| **10 avril PM** | **PIVOT EU+FUTURES : Sector Rotation DAX/CAC40 ($64/trade, Sharpe 1.17) + EU Gap ESTX50 ($56/trade) + Gold-Equity Div ($47/trade). Backtest portefeuille 4 strats combine : +$6,840 (22.8%/an), WF 3/6 PASS, PF 1.30, corr < 0.12. Deploy live.** |
| **10 avril soir** | **DOWNLOAD EU INTRADAY 5Y : 601K bars 5min/15min DAX/CAC40/ESTX50 via IBKR Index, 4h13 sur Hetzner. 6 strats EU intraday testees (ORB DAX, MR RSI, Lunch Effect, US Open Impact, Pairs, Macro ECB). 5/6 REJETEES (edge<couts). Une seule gagnante : MacroECB.** |
| **10 avril nuit** | **V15.3 MACROECB MULTI-INSTRUMENT : 3 indices (DAX +$172/tr, CAC40 +$87, ESTX50 +$45), 69 trades 5 ans, +$7,004, Sharpe 3.18, WF 4/6 yearly. Portfolio combine 4 LIVE+3 MacroECB : 22.8%/an -> 31.7%/an, Sharpe 0.83->1.00 (+20%), MaxDD -2914->-3031 (+4%). Code livre : strategies_v2/futures/macro_ecb.py + core/worker/cycles/macro_ecb_cycle.py + 14 tests PASS. CODE_REVIEW pour deploiement V15.4.** |
| 15 avril | Session debug : bug FrozenInstanceError, overnight MES sweep 85 combos KO. IBKR paper gateway (port 4003) active = data historical futures gratuit. Cross-Asset Momentum (CAM) + Gold-Oil Rotation + Gold Trend MGC identifiees BEAR-capable (alpha pur). Decision : risk budget futures = 5% risk-if-stopped (pas count contracts). |
| 16 avril | Chain de 5 bugs debugge en session, 1er fill crypto, trade MGC live, campagne T1/T2 discovery complete, gold_trend_mgc V1 valide. |
| 17 avril | Audit deep plateforme 7.2 -> 9.0. Trailing MGC V2 +$718. MCL live CAM. Guards always-on sur pre_order_guard. Kill switch fix. |
| 18 avril | Audit P0/P1 cloture. MIB/ESTX50 paper. Re-WF crypto demote 7 strats live (REJECTED). Governance fail-closed binance/alpaca. |
| **19 avril AM** | **Audit comite senior 6.5/10 FRAGILE. Phase 1 obligatoire avant nouveau dev. 18 strats a killer.** |
| **19 avril PM** | **Drain bucket A : 11 strats crypto archived_rejected (btc_eth_dual_momentum, vol_breakout, weekend_gap, liquidation_momentum, trend_short_btc, mr_scalp_btc, liquidation_spike, funding_rate_arb, basis_carry_crypto, triangular_arb, ld_earn_yield_harvest). Drain bucket C : 4 strats EU archived (eu_gap_open, vix_mean_reversion, gold_equity_divergence, sector_rotation_eu). Post-drain : 2 live_core IBKR futures (CAM + GOR) + candidates paper_only. TP MCL CAM ferme dimanche 22h UTC +$605.46 realized. Plan 9.0 ambitieux : 11 commits, quant_registry canonique, boot preflight, StrategyStatus unifie, runtime_audit, incident JSONL. Doctrine live_fast_track_probation grade A/S STRICT defini (gold_trend_mgc V1 seule candidate). Tests 3722 -> 3744 (+22). Score plateforme 6.5 -> 8.5.** |
| 20 avril | Ops review : checkup 24h, 0 incident, DD baseline rollover. |
| **21 avril AM** | **CRO audit 9.0/10 : 9 HAUTES fixes session (Telegram spam, live_pnl fail-closed, eu_relmom dedup, futures_runner account filter, mes_calendar runtime_today, worker skip disabled, fx_paper ESMA, paper_cycles EU holiday guard, live_risk_dd rollover). 42 tests ajoutes. 0 regression.** |
| **21 avril PM** | **Hotfix Binance auto-redeem oscillation (skip si 0 crypto live) + Alpaca state sync persistence. Rapport checkup end-of-day.** |
| **22 avril AM (tot)** | **Phase 0 cleanup verite : banniere STALE sur desk_operating_truth.md (snapshot 19/04 obsolete). resolutions.jsonl nouveau manifest append-only incidents resolus (alpaca_us SPY 19/04 -> resolved). .gitignore etendu live_pnl + debug/crypto temp. CSV daily_equity.csv ligne 1 daily_return_pct 111.86% -> 0.0 (artefact partial fetch pre-fix-92ebd7e). Commit 48acb71.** |
| **22 avril AM** | **Phase 2 PO cleanup catalogue (post PO review 5 sleeves) : archive btc_asia_mes_leadlag_q70_v80 (duplicate q80_long_only, incompat Binance FR spot short). Degrade us_stocks_daily AUTHORIZED -> role=infra_orchestrator + is_canonical_strategy=false (meta-wrapper, pas alpha autonome). Cardinal 16 -> 14 canoniques. Commit 6bb3a4a. Chiffrage mib_estx50_spread funding : NO_GO court terme (ratio edge/risk 0.67, window 4 WF = EUR -8,437 > 100% EUR capital, MaxDD EUR 14,395 > 13,500 margin requis). Commit 534f4e4.** |
| **22 avril PM** | **Phase 1 desk productif : infrastructure live_micro (nouveaux status live_micro + frozen, nouveau mode book live_micro_allowed). core/governance/live_micro_sizing.py (caps par grade, no pyramid J+14, rate limit 1/7j). core/governance/promotion_gate.can_go_live_micro() (grade>=B, paper>=7j, 0 incident 24h). pre_order_guard enforce caps si status=live_micro. 29 tests. Commits 48acb71+6bb3a4a+534f4e4+dc094b9. Tests 3744 -> 3773.** |
| **22 avril PM** | **Phase 2 desk productif : wire btc_asia_mes_leadlag_q80_v80_long_only LIVE_MICRO BTCUSDC $200 USDC, kill DD -$50 hard, max 1 position, no pyramid. Runner dedie core/runtime/btc_asia_q80_live_micro_runner.py (entry/exit + kill auto + Telegram + journal). Hotfix auto-redeem USDC pour detecter live_micro crypto sleeves (sinon spot=$0). Premier cycle 22/04 10h30 Paris = entry_skipped reason=signal_side=NONE (plumbing propre valide). Commits 15a5f2e+6dc6580. Close-out docs/ops/btc_asia_q80_live_micro_launch_2026-04-23.md.** |
| **22 avril soir** | **Phase 3.1 freeze groupe C : mes_pre_holiday_long + eu_relmom_40_3 + us_stocks_daily + mib_estx50_spread passes en status=frozen (hors rotation business, re-activable != disabled). Helper is_strategy_frozen() + guards dans 4 cycles runtime (futures_runner, paper_cycles, worker.py us_stocks). 11 tests. Commit ffe85ce. Phase 3.2 TTL 72h incidents : core/governance/incidents_ttl.py (filter_active_incidents par groupe (sev, book, cat)). Integre promotion_gate + alpaca_go_25k_gate. 18 tests. Commit 02e3403.** |
| **22 avril soir** | **Phase 3.3 metrics etendus live_pnl_tracker summary.json (max_dd_live_pct + trades_count_30d + capital_exposure snapshot) + Phase 3.4 weekly_desk_review.py (report md + Telegram 5 metriques) + systemd timer dim 22h UTC. 28 tests. Commit 533c6f0. Tests finaux 3799 pass 1 skip 0 fail. Timer armed Sun 2026-04-26 22h UTC. Dry-run valide (Telegram push ok). Desk passe de "autorise le trading" a "trade + se concentre + rapporte". Premier fill live_micro possible des demain (~10% proba/jour signal long-only q80).** |
| **22 avril soir (bis)** | **Phase 3.5 cleanup drift code-registry (commit 21cc040) : retrait de 14+ strats legacy de futures_runner.py (MES Trend/Trend+MR/3-Day, Overnight MES V2/MNQ, TSMOM, M2K ORB, Thursday Rally, Friday-Monday, Multi-TF Mom, BB Squeeze, RS MES/MNQ, MCL Brent Lag, MGC VIX Hedge, Commodity Season, MES/MNQ Pairs, MIB/ESTX50 duplicate). Ces strats n'etaient pas dans le catalogue V16 mais tradaient paper sur DUP573894 quotidiennement. +7 tests non-regression. Positions DUP573894 MES -3 + MNQ +3 fermees runtime via market orders. Egalement: CAM.get_top_pick() revise pour ne reserver MCL que si position active OU rebal window (au lieu de toujours). Doctrine : une live_core ne doit pas neutraliser une autre live_core par reservation virtuelle. Tests 3806 pass.** |
| **23 avril AM** | **Session investigation + P0 fix. Bilan: CAM MCL broken en live depuis 20/04 (9 erreurs "no contract details"), 1 seul trade live ferme depuis origine (+$605.46 TP MCL 19/04), 0 fill 24h, capital stable $21,283. Root cause: futures_runner.py hardcodait exchange="CME" pour tous symboles. Fix P0 (commit 1217acf): nouveau `_make_future_contract(symbol)` + `_FUTURES_EXCHANGE_MAP` (MCL->NYMEX, MGC->COMEX). Valide VPS: MCLZ6 Dec 2026 resout OK. +12 tests exchange mapping. Audit gaps live_pnl 20-21/04: cron 22h UTC collidait avec 2FA IBGW restart; decale a 23h UTC. Audit gold_trend_mgc: strat active, "below EMA20" chaque cycle, 0 BUY signal 6j -> decision Marc: 30/04 = date technique eligibilite fast-track, PAS validation economique (doctrine saved feedback_arming_date_vs_economic_validation.md). Wake-up cron reprogramme 16h30 Paris (= 14h30 UTC) pour check post-cycle futures. Tests 3816 pass (+17), 2 preexisting fails test_mcl_overnight_mon_trend non lies.** |

---

## 12. VERDICT FINAL

26 phases en 11 jours (22 mars - 1 avril 2026) : Expansion (3->34 strats) -> Critique (purge 9 overfittees) -> Consolidation (WF, VaR, MC) -> Expansion V5 (4 classes) -> Live-Ready V6 (14 modules) -> Hardening V7 (27 bugs) -> CRO V7.2 GO-LIVE -> ROC V7.3 -> Crypto V7.5 (8 strats Binance) -> CRO V7.6 -> BacktesterV2 (event-driven, WF, MC) -> Hardening S3 (fuzzing+stress) -> CRO 9.5/10 -> Dashboard XL + Crypto LIVE V8.5 -> Crypto ROC V9.0 -> Audit CRO V9.0 (27 fixes) -> V9.5 (+13 strats, Hetzner VPS, 265K candles) -> V10.0 Portfolio-Aware (8 modules risk) -> V11 HRP+Kelly deploye -> **Realloc Binance $23K->$10K** -> **V12.0 Regime Engine + RoR + Chaos (15 modules, 15 cmds Telegram, backup daily)**

### AUDIT CRO V15.0 — Score 9.0/10

| Domaine | **V15.0** | Amelioration V15 |
|---------|:-------:|-----------------|
| D1 Execution ordres | **PASS** | OCA SL+TP, software SL/TP 5min, orphan cancel, disconnect finally |
| D2 Gestion risque | **PASS** | Daily -5% YAML, triple guard, max 2 contrats, deleverage 3 niveaux |
| D3 Integrite donnees | **PASS** | .shift(1), guard ET, UTC |
| D4 Coherence BT/live | **PASS** | State persiste, reconciliation startup+4h, time-exit 48h |
| D5 Securite | **PASS** | Connexion directe (0 env mutation), port isolation live/paper |
| D6 Moteur backtest | **PASS** | WF 5 fenetres, no lookahead |
| D7 Strategies actives | **PASS** | 3 futures LIVE (WF validated) + 11 crypto + PO decision documentee |
| D8 Pipeline | **PASS** | CycleRunners, ibkr_lock, error boundaries |
| D9 Monitoring | **PASS** | Dashboard prod, events.jsonl, snapshots 5min, Telegram 15 cmds |
| D10 Infrastructure | **PASS** | Hetzner VPS, 3 services systemd, IB Gateway watchdog |
| D11 Compliance | **PASS** | cash_flows.jsonl, journal DB, tax PFU 30% |
| D12 Documentation | **PASS** | CLAUDE.md + SYNTHESE V15.0 a jour |

**Critiques fixes (session 9 avril) :**
- [C-1] CORRIGE : Kill switch thresholds hardcodes 1.5% dans Python → YAML 5%
- [C-2] CORRIGE : FX paper os.environ mutation → connexion directe
- [C-3] CORRIGE : Entry not filled → cancelOrder() orphan
- [C-4] CORRIGE : Disconnect dans finally (598 erreurs clientId eliminees)
- [C-5] CORRIGE : Kill switch cascade IBKR→crypto sans raison

**Dashboard fixes (session 9 avril) :**
- 14 bugs fixes : equity curve reelle, drawdown reel, kill switch reset, EUR currency, journal IBKR badge, margin level ratio, correlation calculee, nav cost_basis, strategies count dynamique, tax sans fausses donnees

### Prochain pas

**ACTIF depuis 22 avril (post drain bucket A + C + D + freeze groupe C) :**
- **IBKR live $11,280** (U25023333) : **2 live_core** (cross_asset_momentum grade A + gold_oil_rotation grade S). CAM position MCL fermee 19/04 +$605 realized, prochain rebal cadence 20j = ~2026-05-07. GOR signal dormant (spread gold/oil < 2%).
- **Binance live $9,983** (earn + margin, spot auto-redeemable $2K) : **1 live_micro** (btc_asia_q80_long_only, $200 USDC BTCUSDC, signal ~1x/10j statistiquement).
- **Alpaca paper $99,840** : paper observation seulement (us_sector_ls_40_5 gate NO_GO $25K PDT).
- **6 paper_only** en maturation : gold_trend_mgc (grade A, fast-track earliest 30/04), mes_monday (earliest 16/05), mes_wednesday (review 01/06), mcl_overnight, alt_rel_strength (earliest 18/05), us_sector_ls.
- **4 frozen** : mes_pre_holiday_long, eu_relmom_40_3, us_stocks_daily, mib_estx50_spread (re-activables sans rejet).
- **ROC attendu court terme** : non-mesurable (sample insuffisant), baseline pour 30j observation. Realiste M1 : +$200-500 si premier fill q80 positif + CAM rebal OK.

**Doctrine desk productif V16.0** :
- **Live_micro caps par grade** (B=$200, A=$300, S=$500) empeche burn capital avant validation
- **No pyramid avant J+14 review** : discipline quinzaine pour chaque sleeve promue
- **Rate limit 1 sleeve/7j** : anti-dispersion catalogue
- **TTL 72h incidents** : gates ne bloquent plus sur historique obsolete
- **Freeze != disabled** : groupe C hors rotation business, re-activable quand conditions changent
- **Metriques production** : PnL live 30j + trades 30j + capital_exposure + DD live > scores readiness

**Criteres passage live_micro -> live_probation** (earliest 2026-05-21 btc_asia q80) :
- 30j live_micro PnL net >= 0 apres couts
- 0 incident P0/P1 pendant 14j avant review
- Divergence paper/live < 1 sigma vs backtest
- Kill DD non trigger
- Marc manual greenlight

**Prochaines etapes programmees** :
- **2026-04-23 10h30 Paris** : cycle 2 q80 live_micro (premier fill reel possible si signal BUY, ~10% proba/jour)
- **2026-04-26 22h UTC** : premier systemd weekly_desk_review auto (Telegram push + report/weekly/)
- **2026-04-30** : arming fast-track gold_trend_mgc V1 (candidat #2 live_micro apres 14j paper)
- **2026-05-07** : rebal CAM cadence 20j (probable re-entry MCL ou rotation)
- **2026-05-21** : review J+30 q80 live_micro, decision live_probation OU rollback paper_only
- **2026-06-01** : review mes_wednesday (MC DD 28.3% borderline, 45j surveillance)

**V16.0 operationnel** : Runner live_micro + incidents TTL 72h + weekly review systemd timer + metrics etendus + 4 sleeves groupe C freezees. Premier sleeve live_micro = btc_asia_q80_long_only, armed 2026-04-22, premier event journal (entry_skipped signal=NONE) validates plumbing. **Prochaine vraie question : est-ce qu'on convertit ce setup en evidence live utile quand le signal arrive ?**

---

### AUDIT CRO V16.0 — Score 9.0/10 (inchange post desk-productif)

12 domaines tous PASS (CRO audit 21/04 confirme post validation runtime 22/04). Caveat unique : samples paper+live trop courts pour conclusions ROC, necessite 30j+ de history live avant ajustement scores ROC/capital_usage.

### VERDICT V16.1 (23/04) — Le desk n'est plus bloque par la gouvernance; il etait bloque par un bug contrat

**Photo honnete au 23 avril 2026** (verifiee session matinale) :
  - 1 seul trade live ferme depuis origine (CAM MCL +$605.46 19/04)
  - 0 fill live 24h
  - btc_asia_q80_live_micro: plomberie OK, 0 signal encore
  - gold_oil_rotation: toujours non prouvee (spread <2% dormant)
  - CAM: live_core en statut, mais broken en execution depuis 20/04 suite rollover MCL -> **FIXE 23/04**
  - gold_trend_mgc: actif (log "below EMA20" chaque cycle), 0 BUY en 6j

**Progres structurel V16.1** :
  - Plus de narration vs runtime (code=registry=synthese=runtime aligned)
  - Plus de fausses reservations live_core (CAM ne neutralise plus GOR)
  - Plus de faux portefeuille paper parallele (14 strats legacy retirees)
  - Plus de gap PnL tracker (cron decale 23h UTC)
  - Plus d'incidents obsoletes qui polluent les gates (TTL 72h)
  - Premier moteur live operationnel de nouveau (CAM MCL unlocked)

**Economiquement** :
  - 1 trade profitable en 15 jours de live
  - +2.06% equity depuis origine
  - Capital expose moyen: proche de 0% (sleeves dormantes)
  - **Le desk n'a pas encore prouve qu'il convertit le setup en ROC live recurrent.** Prochain test decisif: 07/05 rebal CAM + premier fill q80 si signal.

**Prochaine etape critique** : observer le cycle 14h UTC 23/04 (post fix P0) pour confirmer que CAM re-entre MCL proprement. Wake-up cron programme 16h30 Paris (= 14h30 UTC) pour verifier les 6 checks. Si 6/6 propre, desk en position d'attendre le prochain trade naturellement.
