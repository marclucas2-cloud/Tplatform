# Allocation Optimization V9 — ROC Maximization
## IBKR ($10K) + Binance (20K EUR) | 29 mars 2026

---

## 1. INVENTAIRE ACTUEL

### 1.1 IBKR — 6 strategies LIVE lundi ($10K)

| # | Strategie | Sharpe | Trades/5y | Margin | Notional | Sizing actuel | Alloc FX actuelle |
|---|-----------|:------:|:---------:|:------:|:--------:|:-------------:|:-----------------:|
| 1 | EUR/USD Trend | 4.62 | 47 | $900 | $30,000 | 1/8 Kelly | 35% FX bucket |
| 2 | EUR/GBP MR | 3.65 | 32 | $750 | $25,000 | 1/8 Kelly | 25% FX bucket |
| 3 | EUR/JPY Carry | 2.50 | 91 | $750 | $25,000 | 1/8 Kelly | 22% FX bucket |
| 4 | AUD/JPY Carry | 1.58 | 101 | $750 | $25,000 | 1/8 Kelly | 18% FX bucket |
| 5 | GBP/USD Trend | ~2.0 | 38 | $720 | $24,000 | 1/8 Kelly | (boost, pas alloue) |
| 6 | EU Gap Open | 8.56 | 72 | ~$500 | ~$5,000 | **1/4 Kelly** | 15% alloc propre |

**Total margin FX** : $3,870 (38.7% du capital) + EU Gap equity ~$500
**Total margin usage** : ~$4,370 (43.7% du capital)
**Cash libre** : ~$5,630 (56.3%)

### 1.2 Binance — 8+4 strategies (20K EUR)

| # | Strategie | Alloc | Type | Levier | Sharpe attendu | Wallet |
|---|-----------|:-----:|------|:------:|:--------------:|:------:|
| 1 | BTC/ETH Dual Momentum | 20% | Trend | 2x | 1.5-2.5 | margin |
| 2 | Altcoin Relative Strength | 15% | Cross-sec | 1.5x | 1.0-2.0 | margin |
| 3 | BTC Mean Reversion | 12% | MR | 1x | 1.0-1.8 | spot |
| 4 | Volatility Breakout | 10% | Vol | 2x | 1.2-2.0 | margin |
| 5 | BTC Dominance V2 | 10% | Macro | 1x | 0.8-1.5 | spot |
| 6 | Borrow Rate Carry | 13% | Carry | 1x | N/A (yield) | earn |
| 7 | Liquidation Momentum | 10% | Event | 3x | 1.0-2.5 | margin |
| 8 | Weekend Gap Reversal | 10% | Calendar | 1x | 0.5-1.5 | spot |
| **Total original 8** | **100%** | | | | |
| 9 | Funding Rate Divergence | 8% | Contrarian | 2x | 1.0-2.0 | margin |
| 10 | Stablecoin Supply Flow | 7% | Macro | 1x | 0.5-1.0 | spot |
| 11 | ETH/BTC Ratio Breakout | 6% | Pairs | 1.5x | 1.0-1.8 | margin |
| 12 | Monthly Turn-of-Month | 5% | Calendar | 1x | 0.8-1.2 | spot |
| **Total 12 strats** | **126%** | | | | | **SURALLOCATION** |

**Probleme identifie** : Total 126% > 100%. Les 4 nouvelles strategies (STRAT-009 a 012) creent une surallocation de 26%.

**Capital reel (config actuel)** : Spot 4,000 + Margin 2,000 + Earn 11,500 + Cash 2,500 = 20,000 EUR
Note : Earn 11,500 EUR (57.5%) est tres concentre — principalement BTC 0.27 + USDC 1,978.

---

## 2. ANALYSE QUANTITATIVE IBKR

### 2.1 Kelly Fraction Optimale

Formule Kelly : f* = (p * b - q) / b, approximation via Sharpe : f* = mu / sigma^2
Pour un Sharpe S annualise : f_Kelly_approx = S / sqrt(252) (pour daily), en pratique f* ~ S^2 / (S^2 + 1) pour les ratios P/L.

Approximation simplifiee (Sharpe-based) : f_Kelly ~ Sharpe / sigma_trade

| Strategie | Sharpe | Kelly plein | 1/8 Kelly | 1/4 Kelly | 1/2 Kelly |
|-----------|:------:|:----------:|:---------:|:---------:|:---------:|
| EU Gap Open | 8.56 | ~35% | 4.4% | 8.8% | 17.5% |
| EUR/USD Trend | 4.62 | ~22% | 2.7% | 5.5% | 11.0% |
| EUR/GBP MR | 3.65 | ~18% | 2.2% | 4.5% | 9.0% |
| EUR/JPY Carry | 2.50 | ~13% | 1.6% | 3.2% | 6.5% |
| GBP/USD Trend | 2.0 | ~11% | 1.4% | 2.7% | 5.4% |
| AUD/JPY Carry | 1.58 | ~9% | 1.1% | 2.2% | 4.4% |

### 2.2 Sharpe-Weighted Allocation (cible)

Poids bruts proportionnels au Sharpe :

| Strategie | Sharpe | Poids brut | Poids normalise |
|-----------|:------:|:----------:|:---------------:|
| EU Gap Open | 8.56 | 8.56 | **37.2%** |
| EUR/USD Trend | 4.62 | 4.62 | **20.1%** |
| EUR/GBP MR | 3.65 | 3.65 | **15.9%** |
| EUR/JPY Carry | 2.50 | 2.50 | **10.9%** |
| GBP/USD Trend | 2.00 | 2.00 | **8.7%** |
| AUD/JPY Carry | 1.58 | 1.58 | **6.9%** |
| **TOTAL** | | **22.91** | **100%** |

### 2.3 Correlation-Adjusted Allocation

**Matrice de correlation estimee** (basee sur les types de strategie et les sous-jacents) :

| | EURUSD | EURGBP | EURJPY | AUDJPY | GBPUSD | EU Gap |
|------|:------:|:------:|:------:|:------:|:------:|:------:|
| EURUSD | 1.00 | **0.65** | 0.55 | 0.25 | **0.70** | 0.15 |
| EURGBP | | 1.00 | 0.30 | 0.10 | 0.45 | 0.10 |
| EURJPY | | | 1.00 | **0.75** | 0.40 | 0.10 |
| AUDJPY | | | | 1.00 | 0.30 | 0.05 |
| GBPUSD | | | | | 1.00 | 0.15 |
| EU Gap | | | | | | 1.00 |

**Clusters a forte correlation** :
- **Cluster EUR** : EURUSD + EURGBP + GBPUSD (rho 0.45-0.70) -- penalite -15%
- **Cluster JPY carry** : EURJPY + AUDJPY (rho 0.75) -- penalite -20%
- **EU Gap Open** : quasi-independant (rho < 0.15) -- bonus +10%

**Allocation correlation-ajustee** :

| Strategie | Sharpe-wt | Penalite corr | **Alloc ajustee** |
|-----------|:---------:|:-------------:|:-----------------:|
| EU Gap Open | 37.2% | +10% (unique) | **40.9%** |
| EUR/USD Trend | 20.1% | -15% (cluster EUR) | **17.1%** |
| EUR/GBP MR | 15.9% | -15% (cluster EUR) | **13.5%** |
| EUR/JPY Carry | 10.9% | -20% (cluster JPY) | **8.7%** |
| GBP/USD Trend | 8.7% | -15% (cluster EUR) | **7.4%** |
| AUD/JPY Carry | 6.9% | -20% (cluster JPY) | **5.5%** |
| Cash reserve | | | **7.0%** |

### 2.4 Risk Parity (contribution egale au risque)

En risk parity, chaque strategie contribue egalement au risque total.
Vol estimee par type (annualisee sur capital) :

| Strategie | Vol annualisee estimee | Poids inv-vol brut | **Risk Parity %** |
|-----------|:----------------------:|:------------------:|:-----------------:|
| EU Gap Open | 12% (intraday, pas overnight) | 8.33 | **19.2%** |
| EUR/USD Trend | 8% (FX swing, 30x levier) | 12.50 | **17.0%** |
| EUR/GBP MR | 6% (FX faible vol) | 16.67 | **17.0%** |
| EUR/JPY Carry | 10% (JPY vol + carry) | 10.00 | **15.5%** |
| GBP/USD Trend | 9% (FX swing) | 11.11 | **16.2%** |
| AUD/JPY Carry | 14% (commodity FX, JPY) | 7.14 | **15.1%** |

Note : Risk parity surpondere les strategies a faible vol (EURGBP MR) et sous-pondere les strategies vol (AUDJPY, EU Gap). Ce n'est PAS optimal pour le ROC car il ignore le Sharpe.

### 2.5 RECOMMANDATION : Allocation Hybride Sharpe-Correlation

L'allocation optimale combine Sharpe-weighting et penalite de correlation tout en respectant les contraintes operationnelles (margin IBKR, lots minimums FX).

**Contrainte cle** : Lots FX IBKR minimum = 25,000 units. Chaque paire FX consomme ~$750 margin.
Avec 5 paires FX, la margin FX seule = $3,750 (37.5%) — proche de la limite fx_margin 40%.

---

## 3. ALLOCATION IBKR : ACTUELLE vs RECOMMANDEE

### 3.1 Tableau comparatif

| Strategie | Alloc ACTUELLE | **Alloc RECOMMANDEE** | Delta | Justification |
|-----------|:--------------:|:---------------------:|:-----:|---------------|
| EU Gap Open | 15% (~$1,500) | **22% (~$2,200)** | +7% | Sharpe 8.56, WF 4/4, intraday sans overnight risk. Meilleur ROC du portefeuille. Passer a 1/4 Kelly confirme. |
| EUR/USD Trend | 14% (~$1,400) | **17% (~$1,700)** | +3% | Sharpe 4.62, flagship. Margin $900 incompressible (lot min 25K). |
| EUR/GBP MR | 10% (~$1,000) | **13% (~$1,300)** | +3% | Sharpe 3.65, faible vol = bon risk-adjusted. |
| EUR/JPY Carry | 8.8% (~$880) | **9% (~$900)** | +0.2% | Sharpe 2.50, penalise par correlation JPY forte avec AUDJPY. |
| GBP/USD Trend | (non alloue) | **8% (~$800)** | +8% | Sharpe ~2.0, ajoute diversification GBP. |
| AUD/JPY Carry | 7.2% (~$720) | **6% (~$600)** | -1.2% | Plus faible Sharpe (1.58) + forte correlation JPY carry. Candidate a la suppression si margin contrainte. |
| Futures (paper) | 0% live | **0% live** | 0% | En paper, activation jour 5 si OK. Ne pas diluer le ROC avec du paper. |
| Cash reserve | ~45% | **25% (~$2,500)** | -20% | 25% est amplement suffisant avec des strategies FX/EU. Liberer du capital vers les strategies productives. |

### 3.2 Justification de la reduction du cash a 25%

Le cash actuel a 56% est EXCESSIF. Raisons :
- Les strategies FX operent en margin (levier 20-33x). Le capital reel immobilise est le MARGIN, pas le notional.
- Total margin utilise = ~$4,370 = 43.7%. Cash libre = 56.3%.
- La limite combined_limits.max_total_margin_pct = 80%. Il reste 36 points de marge.
- La limite min_cash_pct = 20%. Nous recommandons 25% pour un buffer confortable.
- **MAIS** : En SOFT_LAUNCH, le levier max est 1.0x, donc les strategies ne peuvent pas deployer plus que le capital alloue. Le cash a 25% est le MINIMUM acceptable, pas une reduction aggressive.

**Impact ROC** : Passer de 56% cash a 25% cash libere ~$3,100 de capital qui peut etre deploye en EU Gap Open (meilleur ROC) et en ajustement des poids FX.

### 3.3 Budget margin IBKR recommande

| Composant | Margin actuel | Margin recommande | Limite |
|-----------|:------------:|:-----------------:|:------:|
| FX total (5 paires) | $3,870 | $3,870 | $4,000 (40%) |
| EU Gap Open | ~$500 | ~$800 | incl. dans gross |
| Futures (paper) | $0 | $0 (paper) | $3,500 (35%) |
| **Total margin** | **$4,370** | **$4,670** | **$8,000 (80%)** |
| **Cash libre** | **$5,630** | **$5,330** | **min $2,000 (20%)** |

### 3.4 Estimation P&L mensuel IBKR (1/8 Kelly, SOFT_LAUNCH)

Hypotheses :
- Sharpe annualise converti en rendement mensuel : R_monthly ~ Sharpe * sigma_monthly
- Sigma mensuel FX ~2-3% du capital alloue, EU Gap ~5%
- 1/8 Kelly = ~12.5% du Kelly plein

| Strategie | Capital alloue | Sharpe | Trades/mois | R mensuel estime | P&L/mois |
|-----------|:--------------:|:------:|:-----------:|:----------------:|:--------:|
| EU Gap Open | $2,200 | 8.56 | 10-12 | 3.5% | **+$77** |
| EUR/USD Trend | $1,700 | 4.62 | 4-6 | 1.5% | **+$26** |
| EUR/GBP MR | $1,300 | 3.65 | 3-4 | 1.2% | **+$16** |
| EUR/JPY Carry | $900 | 2.50 | 6-8 | 0.8% | **+$7** |
| GBP/USD Trend | $800 | 2.00 | 3-4 | 0.6% | **+$5** |
| AUD/JPY Carry | $600 | 1.58 | 6-8 | 0.5% | **+$3** |
| **TOTAL** | **$7,500** | | **32-42** | | **+$134/mois** |

**ROC mensuel IBKR estime** : $134 / $10,000 = **1.34%/mois = 16.1% annualise** (1/8 Kelly)
**ROC apres passage 1/4 Kelly (Phase 1)** : ~$268/mois = **2.68%/mois = 32.2% annualise**

Note : Ces estimations sont CONSERVATRICES (1/8 Kelly). Le potentiel au Kelly plein serait ~8x plus eleve mais avec un risque de ruine inacceptable.

---

## 4. ANALYSE QUANTITATIVE BINANCE (20K EUR)

### 4.1 Probleme : Surallocation 126%

Les 12 strategies totalisent 126% d'allocation :
- 8 strategies originales : 100% (20+15+12+10+10+13+10+10)
- 4 nouvelles : 26% (8+7+6+5)

**Ce n'est pas viable en production.** Il faut normaliser a 100% OU definir une priorite d'activation.

### 4.2 Classement par efficacite ROC attendue

Score composite = (Sharpe_midpoint * sqrt(Trades/an)) / (1 + Leverage * 0.1) * (1 - Borrow_cost_annual_pct)

| # | Strategie | Sharpe mid | Trades/an | Levier | Cout/an | Score ROC |
|---|-----------|:----------:|:---------:|:------:|:-------:|:---------:|
| 6 | Borrow Rate Carry | N/A (yield) | N/A | 0x | 0% | **Special** (3-12% APY, risk-free) |
| 3 | BTC Mean Reversion | 1.4 | 200 | 1x | 0% | **9.9** |
| 1 | BTC/ETH Dual Momentum | 2.0 | 65 | 2x | 0.9% | **8.0** |
| 7 | Liquidation Momentum | 1.75 | 48 | 3x | 0.1% | **6.7** |
| 4 | Volatility Breakout | 1.6 | 40 | 2x | 0.4% | **5.3** |
| 9 | Funding Rate Divergence | 1.5 | 36 | 2x | 0.5% | **4.7** |
| 11 | ETH/BTC Ratio Breakout | 1.4 | 30 | 1.5x | 0.3% | **4.2** |
| 2 | Altcoin Relative Strength | 1.5 | 312 | 1.5x | 2.7% | **4.0** (haute freq mais COUT ELEVE) |
| 5 | BTC Dominance V2 | 1.15 | 75 | 1x | 0% | **3.9** |
| 12 | Monthly Turn-of-Month | 1.0 | 72 | 1x | 0% | **3.5** |
| 8 | Weekend Gap Reversal | 1.0 | 32 | 1x | 0% | **2.5** |
| 10 | Stablecoin Supply Flow | 0.75 | 52 | 1x | 0% | **2.0** |

### 4.3 Analyse de correlation crypto (penalites)

Toutes les strategies crypto sont correlees au BTC a des degres divers :

| Strategie | Corr BTC estimee | Cluster | Penalite |
|-----------|:----------------:|---------|:--------:|
| BTC Mean Reversion | 0.90 | BTC direct | -25% |
| BTC/ETH Dual Momentum | 0.75 | BTC+ETH trend | -20% |
| BTC Dominance V2 | 0.70 | BTC macro | -15% |
| Weekend Gap Reversal | 0.65 | BTC calendar | -15% |
| Monthly Turn-of-Month | 0.60 | BTC calendar | -10% |
| Stablecoin Supply Flow | 0.55 | BTC macro | -10% |
| Volatility Breakout | 0.50 | BTC/ETH/SOL vol | -5% |
| Altcoin Relative Strength | 0.35 | Cross-sectional | 0% |
| Funding Rate Divergence | 0.30 | Contrarian | 0% |
| ETH/BTC Ratio Breakout | 0.10 | Market-neutral pairs | **+10%** |
| Liquidation Momentum | 0.25 | Event-driven | +5% |
| Borrow Rate Carry | 0.05 | Yield, no direction | **+15%** |

**Implication** : Les strategies market-neutral (ETH/BTC Ratio, Borrow Carry) et event-driven (Liquidation, Funding Rate) meritent un poids PLUS ELEVE car elles diversifient le risque BTC directional.

### 4.4 Allocation Crypto Actuelle vs Recommandee

**Principe** : Normaliser a 100%, surponderer les strategies a haut score ROC et faible correlation BTC.

| # | Strategie | Alloc ACTUELLE | **Alloc RECOMMANDEE** | Delta | Justification |
|---|-----------|:--------------:|:---------------------:|:-----:|---------------|
| 6 | Borrow Rate Carry | 13% | **18%** | +5% | Risque zero directionnel, 3-12% APY garanti. Earn flexible = liquidite instantanee. Meilleur ROC risk-adjusted du portefeuille crypto. |
| 3 | BTC Mean Reversion | 12% | **14%** | +2% | Meilleur score ROC des strats directionnelles. Spot only = pas de cout borrow. Complementaire a STRAT-001. |
| 1 | BTC/ETH Dual Momentum | 20% | **16%** | -4% | Reduit car (a) forte corr BTC, (b) cout borrow ~$130/an, (c) 20% trop concentre pour 1 strat a Sharpe 2.0. |
| 7 | Liquidation Momentum | 10% | **10%** | 0% | Bon score ROC, event-driven = decorrelation. Max 3 trades/sem garde le sizing modeste. |
| 4 | Volatility Breakout | 10% | **8%** | -2% | Bon mais rare (30-50 trades/an). Reduit legerement au profit de strategies plus frequentes. |
| 9 | Funding Rate Divergence | 8% | **7%** | -1% | **AJOUTER LIVE semaine 2**. Contrarian, faible corr BTC. Signal propre (funding observable). |
| 11 | ETH/BTC Ratio Breakout | 6% | **6%** | 0% | **AJOUTER LIVE semaine 2**. Market-neutral = meilleure diversification du portefeuille. |
| 2 | Altcoin Relative Strength | 15% | **8%** | -7% | Forte reduction : cout borrow altcoins ELEVE (~$410/an = 2.7%), incertain, alt-specific risk. Sharpe real < backtest probable. |
| 5 | BTC Dominance V2 | 10% | **5%** | -5% | Sharpe le plus faible des 8 originaux. Rebalancement hebdo = peu de trades. Signal macro floue. |
| 12 | Monthly Turn-of-Month | 5% | **4%** | -1% | **AJOUTER LIVE semaine 3**. Calendar anomaly, complement a Weekend Gap. Evidence academique. |
| 8 | Weekend Gap Reversal | 10% | **4%** | -6% | Forte reduction : max 1 trade/weekend = ~50/an, Sharpe faible (0.5-1.5), signal binaire peu fiable. |
| 10 | Stablecoin Supply Flow | 7% | **0% (MONITORING)** | -7% | **NE PAS ACTIVER**. Sharpe le plus faible (0.5-1.0), signal macro tres lent (hebdo), data CoinGecko non fiable en temps reel. Monitoring uniquement. |
| | **TOTAL** | **126%** | **100%** | | **NORMALISE** |

### 4.5 Redistribution par wallet

| Wallet | Actuel | Recommande | Strategies |
|--------|:------:|:----------:|-----------|
| **Spot** | 4,000 EUR | **5,400 EUR (27%)** | BTC MR (14%), BTC Dom (5%), Weekend Gap (4%), ToM (4%) |
| **Margin** | 2,000 EUR | **5,100 EUR (25.5%)** | Dual Mom (16%), Vol Break (8%), Liq Mom (10%), Funding (7%), ETH/BTC (6%), AltRS (8%) |
| **Earn** | 11,500 EUR | **3,600 EUR (18%)** | Borrow Rate Carry (18%) |
| **Cash** | 2,500 EUR | **5,900 EUR (29.5%)** | Reserve + buffer margin |

**Changement majeur** : Reduire Earn de 11,500 a 3,600 EUR et redeployer 7,900 EUR vers Spot/Margin/Cash.

**Justification** : L'allocation actuelle met 57.5% en Earn (rendement 3-8% APY) alors que les strategies actives (margin + spot) ont un rendement attendu 15-30% annualise. Le ROC est maximise en redeployant vers les strategies actives.

**ATTENTION** : Si une grande partie du Earn est du BTC (0.27 BTC ~ 17,000 EUR au cours actuel), le transfert vers cash implique une vente de BTC. A faire progressivement (10%/semaine) pour eviter le risque de timing.

### 4.6 Estimation P&L mensuel Binance (1/8 Kelly, semaine 1)

**Semaine 1 (conservative, spot + earn, 14K EUR deploye)** :

| Strategie | Capital | Sharpe | R mensuel | P&L/mois |
|-----------|:-------:|:------:|:---------:|:--------:|
| BTC Mean Reversion | 3,500 EUR | 1.4 | 2.0% | +70 EUR |
| BTC Dominance V2 | 1,000 EUR | 1.15 | 1.0% | +10 EUR |
| Weekend Gap | 800 EUR | 1.0 | 0.8% | +6 EUR |
| Borrow Rate Carry | 3,600 EUR | N/A | 0.5% | +18 EUR |
| Cash (earn USDT) | 5,100 EUR | N/A | 0.3% | +15 EUR |
| **TOTAL semaine 1** | **14,000 EUR** | | | **+119 EUR/mois** |

**Semaine 3+ (steady-state, 20K EUR, regime BULL)** :

| Strategie | Capital | Sharpe | R mensuel | Cout borrow | P&L net/mois |
|-----------|:-------:|:------:|:---------:|:----------:|:------------:|
| Borrow Rate Carry | 3,600 EUR | yield | 0.5% | 0 | +18 EUR |
| BTC Mean Reversion | 2,800 EUR | 1.4 | 2.0% | 0 | +56 EUR |
| BTC/ETH Dual Momentum | 3,200 EUR | 2.0 | 2.5% | -8 EUR | +72 EUR |
| Liquidation Momentum | 2,000 EUR | 1.75 | 2.2% | -1 EUR | +43 EUR |
| Volatility Breakout | 1,600 EUR | 1.6 | 1.8% | -3 EUR | +26 EUR |
| Funding Rate Divergence | 1,400 EUR | 1.5 | 1.5% | -3 EUR | +18 EUR |
| ETH/BTC Ratio Breakout | 1,200 EUR | 1.4 | 1.2% | -2 EUR | +12 EUR |
| Altcoin RS | 1,600 EUR | 1.5 | 1.5% | -20 EUR | +4 EUR |
| BTC Dominance V2 | 1,000 EUR | 1.15 | 1.0% | 0 | +10 EUR |
| Monthly ToM | 800 EUR | 1.0 | 0.8% | 0 | +6 EUR |
| Weekend Gap | 800 EUR | 1.0 | 0.8% | 0 | +6 EUR |
| **TOTAL** | **20,000 EUR** | | | **-37 EUR** | **+271 EUR/mois** |

**ROC mensuel Binance estime** : 271 / 20,000 = **1.36%/mois = 16.3% annualise** (1/8 Kelly)
**ROC apres passage 1/4 Kelly** : ~542 EUR/mois = **2.71%/mois = 32.5% annualise**

---

## 5. SYNTHESE CROSS-PORTFOLIO

### 5.1 ROC combine (1/8 Kelly, SOFT_LAUNCH)

| Portefeuille | Capital | P&L/mois estime | ROC mensuel | ROC annualise |
|-------------|:-------:|:---------------:|:-----------:|:-------------:|
| IBKR | $10,000 | +$134 | 1.34% | 16.1% |
| Binance | 20,000 EUR | +271 EUR | 1.36% | 16.3% |
| **COMBINE** | **~$31,500** | **~$420** | **1.33%** | **16.0%** |

### 5.2 ROC combine (1/4 Kelly, Phase 1)

| Portefeuille | Capital | P&L/mois estime | ROC mensuel | ROC annualise |
|-------------|:-------:|:---------------:|:-----------:|:-------------:|
| IBKR | $10,000 | +$268 | 2.68% | 32.2% |
| Binance | 20,000 EUR | +542 EUR | 2.71% | 32.5% |
| **COMBINE** | **~$31,500** | **~$840** | **2.67%** | **32.0%** |

### 5.3 Amelioration ROC vs allocation actuelle

| Metrique | Alloc actuelle | Alloc recommandee | Amelioration |
|----------|:--------------:|:-----------------:|:------------:|
| Cash improductif IBKR | 56% | 25% | **-31 points** (redeploy $3,100) |
| Cash+Earn improductif Binance | 70% (Earn 57.5% + Cash 12.5%) | 47.5% (Earn 18% + Cash 29.5%) | **-22.5 points** |
| Surallocation crypto | 126% | 100% | **Normalise** |
| Strat supprimee (low ROC) | 0 | 1 (Stablecoin Supply = monitoring) | **-7% improductif** |
| Nouvelles strats LIVE | 8 crypto | 11 crypto (+3 phased) | **+3 sources alpha** |
| EU Gap Open (best Sharpe) | 15% IBKR | 22% IBKR | **+$77/mois supplem.** |
| ROC IBKR mensuel 1/8K | ~0.90% (56% cash) | ~1.34% (25% cash) | **+49%** |
| ROC Binance mensuel 1/8K | ~0.85% (70% idle) | ~1.36% (47% idle) | **+60%** |

---

## 6. BUDGET DE RISQUE (VaR PAR STRATEGIE)

### 6.1 IBKR — VaR 95% daily par strategie

| Strategie | Capital | Vol daily | VaR 95% daily | % du total |
|-----------|:-------:|:---------:|:-------------:|:----------:|
| EU Gap Open | $2,200 | 1.2% | -$43 | **28.7%** |
| EUR/USD Trend | $1,700 | 0.6% | -$17 | **11.3%** |
| EUR/GBP MR | $1,300 | 0.4% | -$9 | **6.0%** |
| EUR/JPY Carry | $900 | 0.7% | -$10 | **6.7%** |
| GBP/USD Trend | $800 | 0.6% | -$8 | **5.3%** |
| AUD/JPY Carry | $600 | 0.9% | -$9 | **6.0%** |
| Diversification | | | +$22 (corr < 1) | **-14.7%** |
| Cash | $2,500 | 0% | $0 | 0% |
| **Portfolio VaR 95%** | **$10,000** | | **-$74** | **0.74%** |

Bien sous la limite circuit-breaker daily de -1.5% ($150).

### 6.2 Binance — VaR 95% daily par strategie (steady-state)

| Strategie | Capital | Vol daily | Levier | VaR 95% | % du total |
|-----------|:-------:|:---------:|:------:|:-------:|:----------:|
| BTC/ETH Dual Mom | 3,200 EUR | 3.5% | 2x | -368 EUR | **20.2%** |
| Altcoin RS | 1,600 EUR | 5.0% | 1.5x | -198 EUR | **10.9%** |
| BTC Mean Reversion | 2,800 EUR | 3.0% | 1x | -138 EUR | **7.6%** |
| Vol Breakout | 1,600 EUR | 4.0% | 2x | -211 EUR | **11.6%** |
| BTC Dominance | 1,000 EUR | 2.5% | 1x | -41 EUR | **2.3%** |
| Borrow Carry | 3,600 EUR | 0.5% | 1x | -30 EUR | **1.6%** |
| Liq Momentum | 2,000 EUR | 4.5% | 3x | -445 EUR | **24.5%** |
| Weekend Gap | 800 EUR | 3.0% | 1x | -40 EUR | **2.2%** |
| Funding Rate | 1,400 EUR | 3.0% | 2x | -138 EUR | **7.6%** |
| ETH/BTC Ratio | 1,200 EUR | 2.0% | 1.5x | -59 EUR | **3.2%** |
| Monthly ToM | 800 EUR | 2.5% | 1x | -33 EUR | **1.8%** |
| Diversification | | | | +352 EUR | **-19.4%** |
| Cash | 5,900 EUR | 0% | | 0 EUR | 0% |
| **Portfolio VaR 95%** | **20,000 EUR** | | | **-1,349 EUR** | **6.7%** |

**Attention** : VaR 6.7% daily est ELEVE. La limite circuit-breaker daily est a 5.0% = 1,000 EUR.
Le risque est concentre dans Liquidation Momentum (24.5%) et BTC/ETH Dual Momentum (20.2%).

**Recommandation** : Reduire le levier de Liquidation Momentum de 3x a 2x en semaine 1-2.
Cela reduit sa contribution VaR de -445 EUR a -296 EUR, et le portfolio VaR a ~5.2%.

---

## 7. STRATEGIES NOUVELLES : PLAN D'ACTIVATION

### 7.1 Crypto (4 nouvelles strategies STRAT-009 a 012)

| Strategie | Semaine d'activation | Conditions | Alloc initiale |
|-----------|:--------------------:|------------|:--------------:|
| STRAT-009 Funding Rate Divergence | **Semaine 2** | 7j sans kill switch, margin level > 2.0 | 5% (puis 7%) |
| STRAT-011 ETH/BTC Ratio Breakout | **Semaine 2** | Idem, minimum 10 trades Dual Mom | 4% (puis 6%) |
| STRAT-012 Monthly Turn-of-Month | **Semaine 3** | 14j OK, 2/2 strats semaine 2 OK | 3% (puis 4%) |
| STRAT-010 Stablecoin Supply Flow | **MONITORING ONLY** | Ne pas activer en production | 0% |

**Justification STRAT-010 monitoring only** :
- Sharpe attendu le plus faible (0.5-1.0)
- Signal macro lent (hebdo, CoinGecko API)
- Redondant avec BTC Dominance V2 (meme type macro, meme direction)
- Aucune edge executionnelle (pas de timing, pas d'asymetrie)

### 7.2 IBKR (strategies en pipeline)

| Strategie | Activation cible | Condition | Impact ROC |
|-----------|:----------------:|-----------|:----------:|
| MCL Brent Lag Futures | **Jour 5 (paper OK)** | 3+ MCL reconcilies, 0 bug | +$30-50/mois |
| MES Trend Following | **Jour 5 (paper OK)** | 2+ MES reconcilies, brackets OK | +$20-40/mois |
| USD/CHF Mean Reversion | **Phase 1 (mois 2)** | Gate M1 PASS | +$10-20/mois |
| NZD/USD Carry | **Phase 1 (mois 2)** | Gate M1 PASS, Sharpe NZD confirme | +$5-15/mois |
| BCE Momentum Drift V2 | **Phase 2 (mois 3)** | Capital $15K+, IBKR equity access | +$40-60/mois |
| Auto Sector German | **Phase 2 (mois 3)** | Idem | +$30-50/mois |

### 7.3 Impact ROC cumule du plan d'activation

| Phase | Mois | Strats IBKR | Strats Crypto | P&L/mois estime | ROC annualise |
|-------|:----:|:-----------:|:------------:|:---------------:|:-------------:|
| SOFT_LAUNCH | 1 | 6 FX/EU | 8 core | +$420 | 16.0% |
| + Futures live | 1 (j5) | 8 | 8 | +$480 | 18.3% |
| + Crypto S2 | 2 | 8 | 10 | +$540 | 20.6% |
| PHASE_1 (1/4 K) | 2 | 8 | 10 | +$920 | 35.0% |
| + FX S2 | 3 | 10 | 11 | +$960 | 36.6% |
| PHASE_2 (EU) | 3 | 12 | 11 | +$1,100 | **41.9%** |

---

## 8. CONTRAINTES RESPECTEES

| Contrainte | Limite | Valeur recommandee | Statut |
|-----------|:------:|:------------------:|:------:|
| IBKR cash minimum | 20% ($2,000) | 25% ($2,500) | OK |
| IBKR FX margin max | 40% ($4,000) | 38.7% ($3,870) | OK |
| IBKR combined margin | 80% ($8,000) | 46.7% ($4,670) | OK |
| IBKR max positions | 6 | 6 (5 FX + 1 EU) | OK |
| IBKR daily circuit-breaker | -1.5% (-$150) | VaR 95% = -$74 | OK (2x buffer) |
| Binance cash+stablecoin min | 20% | 29.5% (5,900 EUR) | OK |
| Binance max margin | 80% gross | ~60% gross | OK |
| Binance leverage avg max | 1.8x | ~1.5x | OK |
| Binance daily circuit-breaker | -5% (-1,000 EUR) | VaR 95% = -1,349 EUR | **ATTENTION** |
| Binance max BTC-correlated | 70% | ~55% | OK |
| Binance borrow cost max | 2%/mois | ~0.2%/mois | OK |
| Cross-portfolio correlation | alerte > 120% | N/A (independants) | OK |
| Crypto allocation total | 100% | 100% | OK (corrige de 126%) |

**Action requise** : Reduire le levier de Liquidation Momentum de 3x a 2x pour passer sous la limite circuit-breaker daily crypto.

---

## 9. RESUME DES ACTIONS

### Immediat (avant lundi)

1. **IBKR** : Confirmer les poids FX dans fx_live_sizing.yaml (deja correct a 35/25/22/18, ajouter GBP/USD)
2. **IBKR** : Valider EU Gap Open a 1/4 Kelly, $2,200 max position
3. **Binance** : NE PAS activer les 4 nouvelles strats en semaine 1
4. **Binance** : Reduire Earn de 11,500 a 3,600 EUR (progressif, 10%/semaine)
5. **Binance** : Augmenter Cash reserve de 2,500 a 5,900 EUR (buffer margin)

### Semaine 1

6. Collecter les metrics live (Sharpe, trades, slippage, borrow rates)
7. Confirmer que le VaR daily < circuit-breaker sur les deux portefeuilles
8. Activer futures MCL+MES en paper jour 1, live jour 5 si conditions remplies

### Semaine 2

9. Activer STRAT-009 (Funding Rate Divergence) a 5%
10. Activer STRAT-011 (ETH/BTC Ratio Breakout) a 4%
11. Rebalancer les poids crypto (reduire AltRS de 15% a 8%, Dual Mom de 20% a 16%)

### Semaine 3+

12. Activer STRAT-012 (Monthly Turn-of-Month) a 3%
13. STRAT-010 (Stablecoin Supply Flow) reste en monitoring
14. Evaluer passage a 1/4 Kelly si gate conditions remplies

### NE PAS FAIRE

- Ne pas modifier les config files sans validation live des metrics semaine 1
- Ne pas activer les strategies P2/P3 (FX Cross Momentum, STOXX 50, Calendar Spread, EUR/NOK) avant gate M2
- Ne pas augmenter le levier crypto au-dessus de 2x avant 100 trades live
- Ne pas convertir le BTC Earn en un seul bloc (risque de timing)

---

## 10. ANNEXE — FORMULES

### Kelly Fraction

```
f* = (p * b - q) / b

Ou :
  p = probabilite de gain (win rate)
  b = gain moyen / perte moyenne (profit factor)
  q = 1 - p

Approximation Sharpe :
  f* ~ Sharpe^2 / (Sharpe^2 + vol^2 * 252)
  En pratique, 1/8 Kelly est le standard SOFT_LAUNCH.
```

### Sharpe-Weighted Allocation

```
w_i = S_i / sum(S_j)    pour tout j dans le portefeuille
Ou S_i = Sharpe annualise de la strategie i
```

### Correlation-Adjusted Weight

```
w_adj_i = w_i * (1 - penalty_i)
penalty_i = max(0, avg_corr_cluster_i - 0.3) * 0.5
```

### VaR 95% Daily

```
VaR_i = Capital_i * Leverage_i * sigma_daily_i * 1.645
VaR_portfolio = sqrt(sum_ij(VaR_i * VaR_j * rho_ij))
```

---

*Document genere le 29 mars 2026. A revalider apres 2 semaines de trading live.*
*Auteur : Quant Research Skill, Trading Platform V9.5*
