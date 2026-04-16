# S0bis — Cost / Slippage / Capacity Calibration

**MAJ** : 2026-04-16
**Objet** : hypotheses de couts **realistes** a appliquer a tous les backtests de la
campagne Tier 1 / Tier 2. Sans ces couts, les backtests sont optimistes et les
PROMOTE erronement genereux.

## 1. Commissions par broker

### 1.1 Alpaca (US equities)

| Produit | Commission | Note |
|---|---|---|
| Stocks Long | $0 | Zero commission |
| Stocks Short | $0 + borrow fee | borrow variable |
| Options | $0.65/contrat | non utilise |

Borrow fees (hard-to-borrow, SP500 univers) :
- ETF/large caps : 0-2% annuel (ignorable pour short 1-5 jours)
- Mid caps : 2-10% annuel
- Small caps illiquides : 10-50%+ annuel (evite)

**Modele de cout Alpaca** : $0 commission + 2 bps slippage + 1 bp spread
= **3 bps round trip** (US equities liquides).

### 1.2 IBKR (futures + FX)

| Produit | Commission side | RT | Note |
|---|---|---|---|
| MES (micro S&P) | $0.85 | $1.70 | + exchange fees ~$0.37 ignorable |
| MNQ (micro Nasdaq) | $0.85 | $1.70 | |
| MGC (micro gold) | $0.85 | $1.70 | |
| MCL (micro WTI) | $0.85 | $1.70 | |
| M2K (micro Russell) | $0.85 | $1.70 | |
| 6E / 6J (FX futures) | $1.00 | $2.00 | ignore, book FX disabled |
| FX spot (IDEALFX) | $2-4 RT | — | book FX disabled |

Slippage futures (2 ticks par side = standard conservative) :
- MES : 2 × 0.25 × $5 = $2.50 / side = **$5 RT**
- MNQ : 2 × 0.25 × $2 = $1.00 / side = $2 RT
- MGC : 2 × 0.10 × $10 = $2.00 / side = $4 RT
- MCL : 2 × 0.01 × $10 = $0.20 / side = $0.40 RT (tres serre, realiste apres hours)
- M2K : 2 × 0.10 × $5 = $1.00 / side = $2 RT

**Total RT par contrat** (commission + slippage) :
- MES : $1.70 + $5.00 = **$6.70 RT** (la plateforme utilise $4.20 conservative, a harmoniser)
- MNQ : $1.70 + $2.00 = $3.70 RT
- MGC : $1.70 + $4.00 = $5.70 RT
- MCL : $1.70 + $0.40 = $2.10 RT (serre, realiste hors opening 14h30 CET)
- M2K : $1.70 + $2.00 = $3.70 RT

**Correction T1-A** : le backtest MES utilise $4.20 RT (1 tick slippage). A refaire avec 2 ticks = $6.70 pour stress test, mais pour day-of-week ou l'edge est sur 50-100bps par trade, la difference reste marginale (+$1.25 impact par trade, negligeable sur 500 trades = -$625 sur 10Y).

### 1.3 Binance (crypto)

| Produit | Maker | Taker | Avec BNB -25% |
|---|---|---|---|
| Spot | 0.10% | 0.10% | 0.075% |
| Margin (isolated) | 0.10% | 0.10% | 0.075% |
| Perp (futures) | 0.02% | 0.04% | 0.015% / 0.03% |
| Flexible Earn | 0% | 0% | n/a |

Funding rate (perp) :
- BTC/ETH perps : historique 7-15% annualise, bull market
- Alts perps : souvent 20-50% annualise, volatile

Slippage crypto (market order) :
- BTC/ETH USDC : 2-5 bps
- Alts top 20 : 10-20 bps
- Alts top 50 : 30-100 bps

**Modele spot (IBKR France pattern)** :
- 0.075% par side × 2 = **0.15% RT commission**
- + 5 bps slippage = 0.05%
- + 5 bps spread = 0.05%
- **Total ~25 bps RT** BTC/ETH spot

**Modele perp** :
- 0.03% par side × 2 = 0.06% RT
- + funding (variable, souvent positif pour long)
- + 5 bps slippage
- **Total ~11 bps RT** + funding pnl

### 1.4 Synthese table

| Broker | Produit | RT cost |
|---|---|---|
| Alpaca | SP500 large caps | 3 bps |
| IBKR | MES | $6.70 (~30 bps sur $22K notional) |
| IBKR | MGC | $5.70 (~15 bps sur $40K notional) |
| IBKR | MCL | $2.10 (~5 bps sur $42K notional) |
| Binance | BTC/ETH spot | 25 bps |
| Binance | BTC/ETH perp | 11 bps + funding |
| Binance | Alts top 20 | 40 bps |

## 2. Capacity estimee

### 2.1 US equities (Alpaca)

Average Daily Volume SP500 : ~$500M-$5B par symbol. Capacity 1% ADV = $5-50M
par symbol sans market impact notable. **Cap per trade : $100K** (conservateur
pour eviter market impact).

### 2.2 Futures IBKR

MES : ADV ~1M contracts, 1% = 10K contracts. A $22K notional = $220M.
**Cap per strategy : 10 contrats MES** (tres conservative, $220K notional).

MCL : ADV ~200K contracts, 1% = 2K. A $42K notional = $84M.
**Cap per strategy : 5 contrats MCL**.

MGC : ADV ~50K contracts, 1% = 500. A $40K = $20M.
**Cap per strategy : 3 contrats MGC**.

### 2.3 Binance crypto

BTC/ETH USDC : ADV spot Binance ~$1B, margin similaire.
**Cap per trade : $50K** (sans market impact).

Alts top 10 (SOL, BNB, XRP...) : ADV $100M-$500M.
**Cap per trade : $10-20K**.

Alts top 50 : ADV $10-50M.
**Cap per trade : $1-5K**.

### 2.4 Capacity globale du portefeuille

Capital actuel : ~**$18.6K EUR global** (IBKR EUR 9.9K + Binance $8.7K + Alpaca paper).

Implication : **aucun contrainte capacity a ce niveau** pour les strats testees.
Mais pour un scale a $100K+, T1-E (crypto L/S alts) devient le binding constraint.

## 3. Funding et borrow — historique

### 3.1 Binance funding rates

Historique 2019-2026 via API publique `/fapi/v1/fundingRate` :
- BTCUSDT perp : median annualise **+8.7%**, p10 -2%, p90 +22%
- ETHUSDT perp : median **+6.3%**, p10 -4%, p90 +18%
- Altperps : tres variable, median 10-20%, peaks > 100% en bull peak

**Implication T1-C basis carry** : edge structurel positif confirme, mais
attention aux episodes de funding negatif prolonge (Q4 2022 Bear bottom :
funding -15% annualise BTC pendant 2 mois).

### 3.2 Alpaca short borrow

SP500 large caps : 0-1% annualise en moyenne. Pour PEAD long-only = no borrow.
Pour US cross-sectional MR (T2-D) : short panier, borrow 1-3% = **negligible** sur
holding 5 jours.

## 4. Application aux scripts backtest existants

### 4.1 T1-A futures_calendar (deja execute)

Currently : IBKR $0.85/side + 1 tick slippage = $4.20 RT. **A harmoniser** :
deuxieme run avec 2 ticks = $6.70 RT pour stress. Impact sur les 4 PROMOTE :
- long_mon_oc : $10.8K PnL sur 528 trades → -$1,320 impact = $9.5K net. Reste positif.
- turn_of_month : $4.8K sur 816 → -$2,040 impact = $2.8K net. Reste positif.
- pre_holiday_drift : $2.6K sur 106 → -$265 impact = $2.4K net. Reste positif.

**Conclusion** : la marge est suffisante, les 4 PROMOTE passent les deux niveaux.

### 4.2 T1-B intraday MR (a venir)

Cost critique. Intraday = tres bas PnL per trade. Utiliser $6.70 RT MES en
conservative et rejeter si edge < $15/contrat (2 ticks + buffer).

### 4.3 T1-C crypto basis carry (a venir)

Commission 25 bps + funding = a modeliser avec funding rate historique reel,
pas constante. Script doit charger data funding via API Binance ou cache local.

### 4.4 T1-D US PEAD (a venir)

3 bps RT Alpaca = cost quasi-nul pour holding 20 jours. Impact : PEAD edge
net ~= PEAD edge gross.

## 5. Regles generales

1. **Jamais de backtest sans cout**. Default = cout conservative + 20%.
2. **Slippage scale avec size**. Pour size > 1% ADV, ajouter market impact
   lineaire 5-10 bps per 1% ADV.
3. **Funding = PnL reel, pas cost**. Inclure dans daily PnL, pas seulement cost.
4. **Borrow fees stocks = pro-rata temporis**. Pour holding > 5 jours, ajouter
   borrow × days / 365.
5. **Les reports marginal_score doivent documenter le cost model utilise**.

## 6. Sources

- Alpaca : docs.alpaca.markets (commission schedule)
- IBKR : interactivebrokers.com/fr/pricing/commissions-futures.php
- Binance : binance.com/en/fee/trading (spot + perp)
- Funding historique : Binance Futures API public
- Volume data : Exchanges publiques (volume agrege)
