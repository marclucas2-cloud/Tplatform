# TODO XXL — STRATÉGIES EUROPE + OPTIMISATION DU CAPITAL (ROC)
# Date : 27 mars 2026 | Pour Claude Code — Exécution autonome
# 2 axes : Marchés européens (6 classes d'actifs) + ROC (capital efficiency)
# Ancré dans la Due Diligence V3 (données réelles)

---

## CONTEXTE CRITIQUE (à lire AVANT de commencer)

```
CE QU'ON SAIT DES MARCHÉS EU (Due Diligence V3) :

✅ EU Gap Open : Sharpe 8.56, WR 75%, 72 trades/an → SOLIDE, DÉPLOYÉ
✅ EU Stoxx/SPY Reversion : Sharpe 33.44 → SUSPECT (18 trades, overfit probable)
✅ AUD/JPY Carry Trade : Sharpe 1.58, validé → P1 à déployer

❌ EU OpEx : REJETÉ (gamma pinning EU plus faible)
❌ EU Day-of-Week : REJETÉ (TP 0.3% - coûts 0.26% = 0.04% net = MORT)
❌ EU VWAP Micro : REJETÉ (coûts EU tuent le mean reversion)
❌ EU Pairs : REJETÉ (spread insuffisant + coûts)

RÈGLE EMPIRIQUE #8 : Coûts EU 0.26% round-trip → seules les stratégies 
avec TP > 1.5% survivent.

OVERNIGHT : DÉFINITIVEMENT ENTERRÉ (Sharpe -0.70 sur 5Y, 1254 jours).
L'edge n'existe plus depuis 2021. Ne PAS re-tester.

CASH DORMANT : 60% du capital ne travaille pas. Le problème #1 du ROC.

INSTRUMENTS DISPONIBLES VIA IBKR :
  - Actions EU réelles : Euronext (Paris), Xetra (Frankfurt), LSE (London)
  - Futures : EUREX (Eurostoxx 50, DAX, Bund), ICE (FTSE, Brent)
  - Forex : 100+ paires via IBKR
  - Options EU : Eurostoxx, DAX (options européennes, pas américaines)
  - Volatilité : VSTOXX (tradeable via futures/options)
  - ETFs EU : iShares, Lyxor, Amundi sur Xetra/Euronext

COMMISSIONS IBKR EU :
  Actions EU : ~0.10% par trade (min €4) → round-trip 0.20%
  Futures EUREX : ~€1-2 par contrat → TRÈS BON
  Forex : ~$2/100K → TRÈS BON
  Options EU : ~€1-3 par contrat → BON
  
  → Les FUTURES et le FOREX EU sont beaucoup moins chers que les actions EU.
  → Privilégier les futures/forex pour les stratégies à faible edge.
  → Réserver les actions EU pour les stratégies à fort TP (> 1.5%).
```

---

# ═══════════════════════════════════════════════════════════
# PARTIE 1 : STRATÉGIES MARCHÉS EUROPÉENS
# ═══════════════════════════════════════════════════════════

## 1.1 DONNÉES EU — SETUP COMPLET

```
AVANT de backtester quoi que ce soit, récupérer les données.

DONNÉES-1 : Actions EU individuelles (yfinance + IBKR)

  FRANCE (Euronext Paris, suffixe .PA) :
    MC.PA (LVMH), TTE.PA (TotalEnergies), SAN.PA (Sanofi),
    OR.PA (L'Oréal), AI.PA (Air Liquide), SU.PA (Schneider),
    BNP.PA (BNP Paribas), GLE.PA (Société Générale),
    RMS.PA (Hermès), DSY.PA (Dassault Systèmes),
    KER.PA (Kering), CAP.PA (Capgemini), SGO.PA (Saint-Gobain),
    VIV.PA (Vivendi), STM.PA (STMicroelectronics)

  ALLEMAGNE (Xetra, suffixe .DE) :
    SAP.DE, SIE.DE (Siemens), ALV.DE (Allianz),
    DTE.DE (Deutsche Telekom), BAS.DE (BASF), BMW.DE,
    MBG.DE (Mercedes), DBK.DE (Deutsche Bank), IFX.DE (Infineon),
    ADS.DE (Adidas), VOW3.DE (VW), MUV2.DE (Munich Re),
    DPW.DE (Deutsche Post), HEN3.DE (Henkel), RWE.DE

  PAYS-BAS (Euronext Amsterdam, suffixe .AS) :
    ASML.AS, SHEL.AS (Shell), PRX.AS (Prosus),
    ADYEN.AS, PHIA.AS (Philips), UNA.AS (Unilever NV)

  UK (LSE, suffixe .L — en pence, diviser par 100 !) :
    AZN.L (AstraZeneca), SHEL.L (Shell UK), HSBA.L (HSBC),
    ULVR.L (Unilever), BP.L, RIO.L (Rio Tinto),
    GLEN.L (Glencore), LSEG.L (London Stock Exchange),
    BARC.L (Barclays), DGE.L (Diageo)

  ESPAGNE (BME, suffixe .MC) :
    SAN.MC (Santander), BBVA.MC, IBE.MC (Iberdrola),
    ITX.MC (Inditex), TEF.MC (Telefónica)

  ITALIE (Borsa Italiana, suffixe .MI) :
    UCG.MI (UniCredit), ISP.MI (Intesa), ENEL.MI,
    ENI.MI, STLAM.MI (Stellantis)

  TOTAL : ~60 tickers EU individuels

DONNÉES-2 : Indices et ETFs EU

  INDICES (pour le signal, pas le trading) :
    ^GDAXI (DAX 40), ^FCHI (CAC 40), ^FTSE (FTSE 100),
    ^STOXX50E (Eurostoxx 50), ^STOXX (Stoxx Europe 600)

  ETFs TRADABLES (pour le trading réel via IBKR) :
    EXS1.DE (iShares Core DAX), SX5S.DE (iShares Euro Stoxx 50),
    EXSA.DE (iShares Stoxx Europe 600), ISF.L (iShares Core FTSE 100),
    CSPX.L (iShares Core S&P 500 en EUR)

  ETFs SECTORIELS EU :
    EXV1.DE (iShares STOXX 600 Banks)
    EXV3.DE (iShares STOXX 600 Technology)
    EXH1.DE (iShares STOXX 600 Oil & Gas)
    EXV4.DE (iShares STOXX 600 Health Care)
    EXV5.DE (iShares STOXX 600 Automobiles)
    EXH4.DE (iShares STOXX 600 Industrial Goods)

DONNÉES-3 : Futures EU (yfinance pour proxy, IBKR pour réel)
    FESX (Eurostoxx 50 futures) → proxy : ^STOXX50E
    FDAX (DAX futures) → proxy : ^GDAXI
    FGBL (Bund futures) → proxy : via taux allemands
    BZ=F (Brent Crude) → yfinance direct

DONNÉES-4 : Forex
    EURUSD=X, GBPUSD=X, EURGBP=X, EURJPY=X,
    EURCHF=X, AUDJPY=X (déjà validé)

DONNÉES-5 : Volatilité EU
    ^V2TX (VSTOXX — volatilité Eurostoxx 50)
    ^VIX (VIX US — pour comparaison)

FETCH :
  1. yfinance daily 5 ans pour TOUT (le plus long possible)
  2. yfinance intraday 15M (60 jours max) pour les 20 plus liquides
  3. IBKR historical pour compléter si disponible
  4. Sauvegarder en Parquet dans data_cache/eu/

ANALYSE EXPLORATOIRE :
  Pour chaque ticker, calculer :
  - Volume moyen daily (en EUR)
  - ATR daily en %
  - Corrélation avec SPY
  - Corrélation avec DAX
  - Spread bid-ask estimé (proxy : (high-low)/close en daily)
  - Nombre de gaps > 1% par mois
  - Saisonnalité par jour de semaine
  
  OUTPUT : eu_universe_analysis.csv + eu_eligible_tickers.json
  
  FILTRE ÉLIGIBILITÉ :
  - Volume > €5M daily (assez liquide pour IBKR)
  - ATR > 1% (assez de mouvement pour couvrir 0.26% de coûts)
  - Données > 2 ans disponibles
```

---

## 1.2 ACTIONS EU — STRATÉGIES (TP > 1.5% obligatoire)

### EU-ACT-1 : ASML Earnings Chain Reaction

```
EDGE : ASML est le MONOPOLE mondial de la lithographie EUV.
Quand ASML reporte (4x/an), ça révèle la demande de toute la chaîne 
semi mondiale. Les followers EU (Infineon, STMicro, BE Semi) et US 
(NVDA, AMD) rattrapent avec un lag de 2-4 heures.

C'est le MEILLEUR edge EU car :
  - Le move ASML est toujours > 3% sur earnings (assez pour couvrir les coûts)
  - Le lag followers est documenté et mécanique
  - C'est event-driven (rare mais rentable)

TICKERS :
  Leader : ASML.AS (signal)
  Followers EU : IFX.DE (Infineon), STMPA.PA (STMicro), BESI.AS (BE Semi)
  Followers US (via Alpaca, pas IBKR) : NVDA, AMD, LRCX, AMAT
  
TIMING : Jour des earnings ASML, 9:05-17:00 CET

ENTRÉE LONG (followers) :
  - ASML gap > 3% sur earnings beat (volume > 3x confirme)
  - Follower EU gap < 1% (n'a PAS encore réagi pleinement)
  - Première barre 15M du follower est dans la direction du gap ASML
  - Volume follower > 1.5x moyenne
  - Entrée au close de la barre de confirmation

ENTRÉE SHORT : inverse si ASML miss (gap < -3%)

STOP LOSS : 1.5% (earnings = vol élevée)
TAKE PROFIT : 3.0% ou EOD 17:00
  → TP 3.0% >> coûts 0.26% = VIABLE

FILTRES :
  - ASML gap < 3% → skip (pas assez de move pour couvrir les coûts)
  - ASML fade > 50% dans la première heure → skip
  - Le follower a AUSSI des earnings cette semaine → skip
  - Max 2 positions (les 2 followers les plus en retard)

DONNÉES BACKTEST : yfinance ASML.AS, IFX.DE, STMPA.PA en daily 5Y
  Identifier les jours d'earnings ASML (gaps > 3% avec volume > 3x)
  Mesurer le move des followers J+0 et J+1
  
FRÉQUENCE : 4-8 trades/an (ASML reporte 4x/an, 1-2 followers par event)

CALENDRIER EARNINGS ASML 2026 (à vérifier) :
  ~Janvier (Q4), ~Avril (Q1), ~Juillet (Q2), ~Octobre (Q3)
```

### EU-ACT-2 : Luxury Momentum China Signal

```
EDGE : Le secteur luxe mondial est dominé par les entreprises françaises
(LVMH, Hermès, Kering). Leur chiffre d'affaires dépend de la Chine à 
30-40%. Quand les données de consommation chinoise sortent (PMI, retail 
sales, GDP), le luxe EU réagit avec un lag de 2-4h le matin car les 
traders EU arrivent en retard sur les news asiatiques publiées la nuit.

TICKERS :
  Luxe : MC.PA (LVMH), RMS.PA (Hermès), KER.PA (Kering)
  Signal Chine : proxy via Hang Seng Index (^HSI) overnight performance

TIMING : 9:05-12:00 CET

ENTRÉE LONG :
  - Hang Seng a clôturé en hausse > 1% (données Chine positives)
  - Le stock luxe EU gap > 0.5% à l'ouverture
  - Volume > 1.3x moyenne première heure
  - Le stock est au-dessus de son VWAP à 9:30
  - Entrée au premier pullback vers VWAP

ENTRÉE SHORT :
  - Hang Seng en baisse > 1%
  - Luxe EU gap négatif
  - SHORT le stock luxe le plus faible

STOP LOSS : 1.5% 
TAKE PROFIT : 2.5% ou 12:00 CET (avant overlap US)
  → TP 2.5% >> coûts 0.26% = VIABLE

FILTRES :
  - Hang Seng < ±1% → skip (pas de signal Chine)
  - Earnings LVMH/Hermès dans 5 jours → skip
  - Max 1 position

DONNÉES BACKTEST : yfinance MC.PA, RMS.PA, KER.PA + ^HSI en daily 5Y
  Identifier les jours Hang Seng > ±1% et mesurer le move luxe EU J+0

FRÉQUENCE : 15-25 trades/6 mois
```

### EU-ACT-3 : BCE Rate Decision Drift

```
EDGE : La BCE annonce ses décisions de taux 8x/an (14:15 CET, conférence 
14:45). Le drift post-BCE est documenté. Les banques EU réagissent 
MÉCANIQUEMENT aux décisions de taux car ça impacte directement leur NIM.

TICKERS :
  Signal : décision BCE
  Trades banques : BNP.PA, GLE.PA, DBK.DE, CBK.DE, UCG.MI, ISP.MI
  Trades broad : EXS1.DE (DAX ETF), SX5S.DE (Eurostoxx ETF)

TIMING : Jour BCE, 14:15-17:00 CET

ENTRÉE LONG (banques) :
  - BCE hold hawkish OU hausse de taux
  - Banques réagissent positivement dans les 15 min post-annonce
  - Volume > 2x moyenne
  - Entrée au close de la barre 14:30 (après digestion initiale)

ENTRÉE SHORT (banques) :
  - BCE coupe les taux OU dovish surprise → banques en baisse

STOP LOSS : 1.5%
TAKE PROFIT : 3.0% ou 17:00 CET
  → Event-driven = TP large = VIABLE malgré coûts EU

FILTRES :
  - Décision inline (pas de surprise) → skip (pas de drift)
  - Max 2 positions (les 2 banques les plus réactives)

DONNÉES BACKTEST : yfinance BNP.PA, GLE.PA, DBK.DE en daily 5Y
  Identifier les 8 jours BCE/an, mesurer le move banques J+0

CALENDRIER BCE 2026 :
  23 jan, 6 mars, 17 avril, 5 juin, 17 juillet, 11 sept, 23 oct, 18 déc

FRÉQUENCE : 8-16 trades/an
```

### EU-ACT-4 : Auto Sector German Sympathy

```
EDGE : BMW, Mercedes (MBG), VW sont très corrélés en intraday.
Quand un des 3 fait un move > 2% sur une news (ventes Chine, régulation 
émissions, EV news), les 2 autres suivent avec un lag de 30-60 min.

TICKERS : BMW.DE, MBG.DE (Mercedes), VOW3.DE (VW)

TIMING : 9:30-16:00 CET

ENTRÉE LONG (sympathy) :
  - Un des 3 gap > 2% sur news (volume > 2x)
  - Les 2 autres ont gappé < 0.8% (lag)
  - Le retardataire commence à bouger dans la direction du leader
  - Volume retardataire > 1.3x

STOP LOSS : 1.5%
TAKE PROFIT : 50% du move du leader ou 2.5% (le plus petit)

FILTRES :
  - Le leader fade > 50% dans la première heure → skip
  - Le retardataire a sa propre news → skip
  - Max 1 trade (1 seul retardataire)

DONNÉES BACKTEST : yfinance BMW.DE, MBG.DE, VOW3.DE en daily 5Y
  Identifier les jours où un des 3 a bougé > 2% et mesurer le lag

FRÉQUENCE : 10-20 trades/6 mois
```

### EU-ACT-5 : Banking Stress Contagion Fade

```
EDGE : Les banques EU sont plus fragiles que les US (CET1 plus bas, NPL 
plus élevés). La contagion lors d'un stress est PLUS FORTE et PLUS DURABLE 
qu'aux US. Mais elle est aussi plus souvent EXCESSIVE — les banques 
saines baissent en sympathie par panique, pas par fondamentaux.

TICKERS : BNP.PA, GLE.PA, DBK.DE, CBK.DE, UCG.MI, ISP.MI, 
  HSBA.L, BARC.L, SAN.MC, BBVA.MC

TIMING : 10:00-16:00 CET

ENTRÉE LONG (fade contagion) :
  - Une banque EU ("patient zéro") chute > 5% intraday
  - Les autres banques baissent > 2% en sympathie
  - Identifier les banques qui baissent MAIS qui :
    a) Sont dans un PAYS DIFFÉRENT du patient zéro
    b) Ont un CET1 > 12% (bien capitalisées)
    c) Volume en baisse (panique qui s'estompe)
  - Acheter au premier reversal (barre 15M verte après série de rouges)

STOP LOSS : Low du jour - 1.0% (large car situationnel)
TAKE PROFIT : Récupération de 50% du drop de contagion
  → Les moves de panique bancaire sont > 3% = TP > 1.5% = VIABLE

FILTRES :
  - Intervention BCE/régulateur annoncée → skip (problème systémique réel)
  - Plus de 3 banques > -5% → skip (contagion réelle, pas de la panique)
  - Max 1 position

DONNÉES BACKTEST : yfinance toutes les banques EU en daily 5Y
  Identifier les jours de stress bancaire (1 banque > -5%)
  Mesurer le fade des banques saines J+0 à J+3

FRÉQUENCE : 2-8 trades/an (rare mais très rentable quand ça arrive)
```

### EU-ACT-6 : Energy EU — Brent Lag Play

```
EDGE : TotalEnergies, Shell, BP réagissent au Brent (pas au WTI) et 
au gaz TTF. Le Brent trade à Londres (ICE), le TTF à Amsterdam. 
Les energy stocks EU rattrapent le Brent avec un lag similaire au 
crude-equity lag US (qui a fait Sharpe 1.85 mais seulement 5 trades).

TICKERS :
  Signal : BZ=F (Brent futures via yfinance)
  Trades : TTE.PA (Total), SHEL.AS (Shell), BP.L, ENI.MI (Eni)

TIMING : 10:00-16:00 CET

ENTRÉE LONG :
  - Brent en hausse > 1.0% à 10:00 (seuil plus élevé que US car coûts EU)
  - Le stock energy EU en hausse < 0.5% (lag)
  - Volume stock > moyenne
  - Au-dessus du VWAP

STOP LOSS : 1.2%
TAKE PROFIT : 2.0%
  → TP 2.0% >> coûts 0.26% = VIABLE (si le Brent move est fort)

FILTRES :
  - Brent < 1.0% → skip (pas assez de move pour les coûts EU)
  - OPEC meeting today → skip
  - Earnings du stock → skip
  - Max 2 trades/jour

DONNÉES BACKTEST : yfinance BZ=F, TTE.PA, SHEL.AS, BP.L en daily 5Y

FRÉQUENCE : 10-20 trades/6 mois
```

---

## 1.3 FUTURES EU — STRATÉGIES (coûts bas = plus de flexibilité)

Les futures EUREX coûtent ~€1-2/contrat (pas 0.10% comme les actions EU).
C'est 10-50x moins cher. On peut donc tester des stratégies à FAIBLE edge.

### EU-FUT-1 : Eurostoxx 50 Trend Following

```
EDGE : Le trend following sur indices fonctionne mieux que sur actions 
individuelles (moins de bruit idiosyncratique). L'Eurostoxx 50 futures 
(FESX) est l'instrument le plus liquide d'EUREX.

INSTRUMENT : FESX (Eurostoxx 50 futures, EUREX)
  1 point = €10, tick size = 1 point
  Margin initiale ~€3,000-4,000 par contrat
  Commission ~€1.50/contrat round-trip
  
PROXY BACKTEST : ^STOXX50E en 1H (yfinance)

TIMING : 9:00-17:30 CET

ENTRÉE LONG :
  - Prix > EMA(20) 1H ET EMA(20) > EMA(50) 1H
  - Volume barre courante > moyenne 20 barres
  - RSI(14) entre 40 et 70 (pas surachet)

ENTRÉE SHORT :
  - Prix < EMA(20) ET EMA(20) < EMA(50)
  - RSI entre 30 et 60

STOP LOSS : 2.0 × ATR(14) en 1H (~30-50 points Eurostoxx)
TAKE PROFIT : 3.0 × ATR(14) OU trailing stop 1.5 × ATR

FILTRES :
  - BCE meeting day → skip (macro domine)
  - ADX(14) < 15 → skip (pas de trend)
  - Max 1 position

SIZING : 1 contrat = ~€50K notionnel → max 2 contrats sur $30K capital 
  (levier 3:1, conservateur)

FRÉQUENCE : 20-40 trades/6 mois
HOLDING : 1-8 heures

NOTE : Les commissions futures (€1.50 RT) sont NÉGLIGEABLES vs le 
notionnel (€50K). Le break-even slippage est beaucoup plus favorable 
que les actions EU.
```

### EU-FUT-2 : DAX Breakout Post-BCE

```
EDGE : Le jour de la BCE (8x/an), le DAX futures fait un move de 
1-3% dans les 2 heures post-annonce. Le FDAX est très liquide sur 
EUREX et les commissions sont faibles.

INSTRUMENT : FDAX (DAX futures, EUREX)
  1 point = €25, tick size = 0.5 points
  Margin ~€15,000 par contrat (mais mini-DAX = €5 par point existe)
  
PROXY BACKTEST : ^GDAXI en 15M/1H

TIMING : Jour BCE, 14:15-17:00 CET

ENTRÉE LONG :
  - BCE annonce hawkish (hold ou hausse)
  - DAX monte > 0.3% dans les 15 min post-annonce
  - Volume > 2x
  - Entrée au close de la barre 14:30

ENTRÉE SHORT :
  - BCE dovish (coupe ou surprise baissière)
  - DAX baisse > 0.3%

STOP LOSS : 1.0% (~150-200 points DAX)
TAKE PROFIT : 2.0% ou 17:00 CET

FILTRES :
  - Move initial < 0.3% → skip (pas de conviction)
  - Max 1 contrat mini-DAX (€5/point = ~€100K notionnel / 5 = €20K eq)

FRÉQUENCE : 8 trades/an (1 par meeting BCE)
```

### EU-FUT-3 : Bund Futures Rate Play

```
EDGE : Les Bund futures (FGBL, EUREX) sont l'instrument le plus 
liquide pour trader les taux européens. Quand la BCE change sa guidance, 
les Bunds réagissent immédiatement mais le drift continue 2-4 heures.
C'est le TLT européen mais en futures (levier natif, pas de PDT).

INSTRUMENT : FGBL (Euro-Bund futures, EUREX)
  1 point = €1,000, tick size = 0.01 point
  Margin ~€2,000-3,000 par contrat
  Commission ~€1.50/contrat
  
PROXY BACKTEST : taux Bund 10Y via yfinance ou proxy via TLT inversé

TIMING : Jour BCE + jours de données macro EU (CPI, PMI)

ENTRÉE SHORT BUND (taux montent) :
  - BCE hawkish OU CPI EU > consensus
  - Bund baisse > 0.2% dans les 30 min post-annonce
  - Drift continuation attendu

ENTRÉE LONG BUND (taux baissent) :
  - BCE dovish OU CPI EU < consensus
  - Flight to quality

STOP LOSS : 0.5% (~50 ticks Bund)
TAKE PROFIT : 1.0% ou EOD

FILTRES :
  - Données inline → skip
  - Max 1 contrat

FRÉQUENCE : 10-15 trades/6 mois (BCE + CPI EU + PMI EU)
```

### EU-FUT-4 : Brent Crude Momentum

```
EDGE : Le Brent (ICE Futures Europe) trade à Londres et réagit 
aux news géopolitiques (Moyen-Orient, OPEC) avant les US markets.
Le trend following sur le Brent est bien documenté et les commissions 
futures sont faibles.

INSTRUMENT : BZ (Brent Crude futures, ICE)
  1 point = $1,000 par contrat
  Margin ~$5,000-8,000
  
PROXY BACKTEST : BZ=F yfinance en daily/1H

TIMING : 8:00-20:00 CET (le Brent trade presque 24h)

ENTRÉE LONG :
  - Prix > EMA(20) daily ET EMA(20) > EMA(50) daily
  - Prix au-dessus du VWAP intraday
  - Volume > moyenne

ENTRÉE SHORT : inverse

STOP LOSS : 2.5 × ATR(14) daily (~$2-4 par baril)
TAKE PROFIT : 4.0 × ATR(14) OU trailing stop

FILTRES :
  - OPEC meeting dans 3 jours → skip (event risk)
  - Contango > 3% (le roll cost mange le profit)

SIZING : 1 mini contrat (si disponible) ou position sizing adapté

FRÉQUENCE : 5-10 trades/6 mois (swing, holding 2-10 jours)
```

---

## 1.4 ETFs SECTORIELS EU — STRATÉGIES

### EU-ETF-1 : Sector Rotation EU Weekly

```
EDGE : La rotation sectorielle en EU est drivée par les flux 
institutionnels. En 2026, la rotation est de tech → value/consumer.
En weekly, le momentum sectoriel persiste (l'intraday a échoué, 
le weekly devrait marcher car les flux TWAP s'étalent sur plusieurs jours).

TICKERS : EXV1.DE (Banks), EXV3.DE (Tech), EXH1.DE (Energy),
  EXV4.DE (Healthcare), EXV5.DE (Auto), EXH4.DE (Industrial)

TIMING : Rebalance chaque lundi matin

ENTRÉE LONG : Top 2 ETFs sectoriels EU par performance 1 semaine
ENTRÉE SHORT : Bottom 2 ETFs sectoriels EU par performance 1 semaine
  (dollar-neutral)

STOP LOSS : -2% par position
TAKE PROFIT : Vendredi close (rebalance lundi suivant)

FILTRES :
  - Spread top2 vs bottom2 < 1% → skip (pas de rotation)
  - BCE cette semaine → skip
  - Max 4 positions (2L + 2S)

COÛTS : ~0.26% × 4 positions × 26 semaines = ~$2,700/an
  → Viable seulement si le return annuel > 5%

DONNÉES BACKTEST : yfinance ETFs sectoriels EU daily 3Y

FRÉQUENCE : ~26 rebalances/an
HOLDING : 5 jours
```

### EU-ETF-2 : Banks EU vs US Relative Value

```
EDGE : Quand les banques US (XLF) surperforment les banques EU (EXV1.DE) 
de > 3% sur 2 semaines, les banques EU tendent à rattraper (mean 
reversion cross-géographique). Les banques sont globales — les NIM 
sont corrélés aux cycles de taux mondiaux.

TICKERS : EXV1.DE (Banks EU) vs XLF (Banks US via Alpaca)

TIMING : Entry quand spread > 2 sigma, hold 5-15 jours

ENTRÉE LONG EXV1.DE / SHORT XLF :
  - XLF a surperformé EXV1.DE de > 3% sur 10 jours rolling
  - Le z-score du spread > 2.0
  - Les taux EU et US sont dans la même direction (pas de divergence BCE/Fed)

STOP LOSS : Z-score atteint 3.0
TAKE PROFIT : Z-score revient à 0.5

FILTRES :
  - BCE OU FOMC dans 5 jours → skip
  - Max 1 position

NOTE : Cette stratégie nécessite un trade CROSS-BROKER 
  (long IBKR + short Alpaca). Le multi-broker manager doit supporter ça.

FRÉQUENCE : 5-10 trades/6 mois
HOLDING : 5-15 jours
```

---

## 1.5 FOREX — STRATÉGIES

### EU-FX-1 : EUR/USD Trend Following

```
EDGE : L'EUR/USD est la paire la plus liquide au monde (spread < 0.5 pip).
Le trend following sur EUR/USD en 4H a un edge documenté car les flux 
de change sont drivés par les différentiels de taux (BCE vs Fed) qui 
évoluent lentement → trends durables.

INSTRUMENT : EUR/USD via IBKR
  Commission : ~$2 par $100K (0.002%)
  Spread : < 0.5 pip = 0.005%
  
PROXY BACKTEST : EURUSD=X yfinance 4H (si dispo) ou daily

TIMING : 24/5 (forex trade en continu)

ENTRÉE LONG EUR :
  - Prix > EMA(20) 4H ET EMA(20) > EMA(50) 4H
  - ADX(14) > 20 (trend confirmé)
  - Entry au croisement confirmé

ENTRÉE SHORT EUR : inverse

STOP LOSS : 2.0 × ATR(14) 4H (~50-80 pips)
TAKE PROFIT : Trailing stop 1.5 × ATR

SIZING : Levier 2:1 max ($5K capital → $10K position → 0.1 lot)
  Risque par trade : 1% du capital ($300)

FILTRES :
  - BCE OU FOMC dans 24h → close position
  - NFP day → close position
  - ADX < 15 → skip

FRÉQUENCE : 10-20 trades/6 mois
HOLDING : 1-10 jours (swing)
```

### EU-FX-2 : EUR/GBP Mean Reversion

```
EDGE : L'EUR/GBP est une paire range-bound (économies très liées).
Le mean reversion fonctionne mieux que le trend following sur cette paire.
Quand le prix s'écarte de > 2 sigma de la moyenne 60 jours → retour.

INSTRUMENT : EUR/GBP via IBKR

PROXY BACKTEST : EURGBP=X yfinance daily 5Y

ENTRÉE LONG EUR/GBP :
  - Z-score (prix vs SMA60) < -2.0
  - Le prix a stoppé de baisser (barre daily verte après série de rouges)

ENTRÉE SHORT EUR/GBP :
  - Z-score > 2.0

STOP LOSS : Z-score atteint 3.0 (le spread continue)
TAKE PROFIT : Z-score revient à 0.5

SIZING : Levier 2:1 max

FILTRES :
  - BCE OU BOE dans 3 jours → skip (politique monétaire divergente)
  - Brexit/événement politique UK → skip
  - Max 1 position

FRÉQUENCE : 5-10 trades/6 mois
HOLDING : 5-20 jours
```

### EU-FX-3 : EUR/JPY Carry + Momentum

```
EDGE : Similaire à AUD/JPY (déjà validé Sharpe 1.58) mais sur EUR/JPY.
Le différentiel de taux BCE/BOJ crée un carry positif. Le JPY est 
structurellement faible depuis 2022 (BOJ ultra-dovish).

INSTRUMENT : EUR/JPY via IBKR

PROXY BACKTEST : EURJPY=X yfinance daily 5Y

ENTRÉE LONG EUR/JPY :
  - Carry positif (taux BCE > taux BOJ → toujours vrai en 2026)
  - Momentum confirmé : prix > EMA(20) daily
  - VIX < 25 (pas de risk-off → JPY = safe haven)

STOP LOSS : 2% du capital alloué
TAKE PROFIT : Trailing stop 1.5 × ATR daily

FILTRES :
  - VIX > 25 → close position (risk-off = JPY monte)
  - BOJ intervention verbale → close
  - Max 1 position, levier 2:1

FRÉQUENCE : 4-8 trades/6 mois
HOLDING : 10-30 jours
```

---

## 1.6 VOLATILITÉ EU — STRATÉGIES

### EU-VOL-1 : VSTOXX / VIX Spread

```
EDGE : Le VSTOXX (volatilité Eurostoxx) et le VIX (volatilité S&P) sont 
corrélés à ~0.85 mais le VSTOXX trade avec un premium de 2-5 points 
vs le VIX. Quand ce premium s'élargit > 2 sigma, il mean-reverte.

INSTRUMENTS : 
  VSTOXX futures (EUREX) vs VIX futures (CBOE/IBKR)
  
PROXY BACKTEST : ^V2TX vs ^VIX yfinance daily 5Y

ENTRÉE :
  - Calculer le spread VSTOXX - VIX quotidien
  - Z-score du spread (60 jours lookback)
  - Si z-score > 2.0 : LONG VIX / SHORT VSTOXX (le spread va se comprimer)
  - Si z-score < -2.0 : SHORT VIX / LONG VSTOXX

STOP LOSS : Z-score atteint 3.0
TAKE PROFIT : Z-score revient à 0.5

FILTRES :
  - BCE ET FOMC dans la même semaine → skip (les deux bougent)
  - VIX > 35 → skip (crise, corrélations instables)
  - Max 1 position

FRÉQUENCE : 4-8 trades/6 mois
HOLDING : 5-20 jours

NOTE : Nécessite IBKR avec accès aux futures vol sur 2 exchanges.
Vérifier la disponibilité et le margin requirement.
```

---

## 1.7 CROSS-TIMEZONE — STRATÉGIES

### EU-CROSS-1 : US Close → EU Open Gap

```
DÉJÀ VALIDÉ (Sharpe 8.56, WR 75%). Documenter les paramètres exacts.

EDGE : Le close US (21:55 Paris) contient de l'information non encore 
absorbée par le marché EU. Le gap EU du lendemain matin reflète cette 
information avec un move de continuation de 2-4 heures.

TICKERS :
  Signal : SPY, QQQ (close de la veille US)
  Trades : EXS1.DE (DAX ETF), SX5S.DE (Eurostoxx ETF),
           ASML.AS, SAP.DE, MC.PA (top liquides EU)

TIMING : 9:05-12:00 CET

PARAMÈTRES EXACTS (du backtest Sharpe 8.56) :
  [Récupérer les paramètres exacts du fichier de stratégie existant]
```

### EU-CROSS-2 : Asie Close → EU Open Catch-Up

```
EDGE : Quand la session asiatique (Nikkei, Hang Seng) fait un move 
fort, l'Europe rattrape partiellement à l'ouverture mais le rattrapage 
CONTINUE pendant 2-3 heures.

TICKERS :
  Signal : ^N225 (Nikkei), ^HSI (Hang Seng) — clôture 8:00-9:00 CET
  Trades : EXS1.DE (DAX), SX5S.DE (Eurostoxx), ASML.AS

TIMING : 9:05-12:00 CET

ENTRÉE LONG :
  - Nikkei ET/OU Hang Seng en hausse > 1% à la clôture
  - DAX/Eurostoxx gap > 0.3% à 9:00 (rattrapage partiel)
  - Volume première barre > 1.3x
  - Continuation confirmée (barre verte)

ENTRÉE SHORT : inverse

STOP LOSS : 0.8%
TAKE PROFIT : 1.5% ou 12:00

FILTRES :
  - Gap EU > 1.5% → skip (tout est déjà pricé)
  - Données macro EU à 10:00 → skip
  - Max 1 trade

DONNÉES BACKTEST : yfinance ^N225, ^HSI, ^GDAXI daily 5Y
  Corréler les moves Asie → EU sur 5 ans

FRÉQUENCE : 20-30 trades/6 mois
```

### EU-CROSS-3 : EU Close → US Afternoon Signal

```
EDGE : La clôture EU (17:30 CET = 11:30 ET) contient de l'information 
non pricée aux US. Si le DAX clôture en hausse forte (> 1%) et que 
SPY est flat à 11:30 ET, SPY tend à rallier l'après-midi.

TICKERS :
  Signal : ^GDAXI (DAX close à 17:30 CET)
  Trade : SPY (via Alpaca, pas IBKR)

TIMING : Signal 17:30 CET (11:30 ET), Trade 12:00-15:55 ET

ENTRÉE LONG SPY :
  - DAX clôture en hausse > 1% à 17:30 CET
  - SPY flat (< 0.3%) à 11:30 ET (divergence EU/US)
  - Acheter SPY à 12:00 ET

ENTRÉE SHORT : inverse

STOP LOSS : 0.4%
TAKE PROFIT : 0.6% ou 15:55 ET

FILTRES :
  - DAX et SPY dans la même direction > 0.5% → skip (pas de divergence)
  - FOMC/CPI US aujourd'hui → skip

COÛTS : Alpaca ($0.005/share) = pas le problème EU

DONNÉES BACKTEST : yfinance ^GDAXI daily + SPY intraday
  Mesurer la corrélation DAX close → SPY afternoon sur 3Y

FRÉQUENCE : 15-25 trades/6 mois
```

### EU-CROSS-4 : US Earnings After-Hours → EU Morning Sympathy

```
EDGE : Les FAANG reportent after-hours US (22:00+ CET). Le gap du 
lendemain matin en EU affecte les stocks corrélés.

MAPPING :
  NVDA/AMD earnings → ASML.AS, IFX.DE (semi EU)
  META earnings → ADYEN.AS (digital advertising EU)
  MSFT earnings → SAP.DE (enterprise software EU)
  AMZN earnings → Zalando, ADYEN (e-commerce EU)
  AAPL earnings → STM.PA (supply chain EU)

TIMING : J+1 matin, 9:05-12:00 CET

ENTRÉE LONG (follower EU) :
  - FAANG a beat (gap > 3% pre-market US)
  - Le follower EU gap > 0.5% à l'ouverture
  - Volume > 1.5x
  - Première barre 15M dans la direction du gap

STOP LOSS : 1.5%
TAKE PROFIT : 3.0% ou 12:00

FILTRES :
  - Le follower EU a AUSSI des earnings cette semaine → skip
  - Gap EU > 3% → skip (déjà pricé)
  - Max 1 trade

FRÉQUENCE : 8-15 trades/6 mois (saisons earnings)
```

---

# ═══════════════════════════════════════════════════════════
# PARTIE 2 : OPTIMISATION DU CAPITAL (ROC)
# ═══════════════════════════════════════════════════════════

## 2.1 DIAGNOSTIC DU PROBLÈME

```
CAPITAL : $100K (paper) → $30K (live cible)
INVESTI : ~40% ($40K travaille, $60K dort)
RENDEMENT SUR CAPITAL INVESTI : ~+6.5%/6m sur $40K = ~13%/6m
RENDEMENT SUR CAPITAL TOTAL : ~+6.5%/6m sur $100K = ~6.5%/6m

LE PROBLÈME :
  Si on investissait 80%, le rendement serait ~13% (2x)
  Si on investissait 85% + levier intelligent, ~18-22% (3x)

POURQUOI LE CAPITAL DORT :
  1. Trop peu de stratégies (20) pour utiliser 80% du capital
  2. Les stratégies tradent aux mêmes heures (15:30-22:00 Paris)
  3. Pas de positions swing/overnight (le capital dort 18h/jour)
  4. Cash reserve trop conservatrice (15% requis par les limites)
  5. Pas de levier (le cash ne multiplie rien)
  6. Les stratégies short consomment du capital sans le multiplier
```

## 2.2 LEVIER 1 — AUGMENTER LE NOMBRE DE STRATÉGIES ACTIVES

```
ROC-1 : Passer de 20 à 30+ stratégies

Stratégies EU (cette TODO) : 15 nouvelles potentielles
  Si 4-5 passent le backtest = +5 stratégies = +10% d'investissement

Stratégies short P1 validées : Cross-Asset Risk-Off, OpEx Short Ext
  = +2 stratégies = +5% d'investissement

AUD/JPY Carry validé : +1 stratégie = +3%

Impact estimé : 40% investi → 55-60% investi
```

## 2.3 LEVIER 2 — ÉTENDRE LES HEURES DE TRADING

```
ROC-2 : Couverture temporelle du capital

ACTUEL :
  9:00-12:00 CET  : 1 strat EU (EU Gap Open)        → 5% du capital
  15:30-22:00 CET : 19 strats US                      → 35% du capital
  22:00-9:00 CET  : 0 strat (overnight mort)          → 0%
  Lundi matin     : 1 strat EU (Stoxx Reversion)      → 3%
  24/7            : 1 strat FX (AUD/JPY carry)         → 3%

CIBLE (avec les stratégies EU de cette TODO) :
  8:00-9:00 CET   : Analyse Asie → préparer EU         → 0% (signal)
  9:00-15:30 CET  : 8-10 strats EU (actions + futures)  → 20-25%
  15:30-17:30 CET : OVERLAP EU/US (le plus riche)       → 30%
  17:30-22:00 CET : 19 strats US                        → 35%
  22:00-9:00 CET  : FX carry/swing (pas d'overnight)    → 5-8%
  
  TOTAL INVESTI : 75-85% du capital (vs 40%)
  HEURES DE TRADING : ~14h/jour (vs ~6.5h)

IMPACT SUR LE ROC :
  Si le rendement par heure de trading est constant :
  14h/6.5h = 2.15x multiplicateur
  6.5% rendement 6m × 2.15 = ~14% rendement 6m = ~28% annualisé
```

## 2.4 LEVIER 3 — LEVIER STRUCTUREL (pas du levier brut)

```
ROC-3 : Utiliser le levier natif des instruments pour amplifier le ROC

FUTURES EU :
  1 contrat Eurostoxx (FESX) = ~€50K notionnel pour ~€3K de margin
  Levier natif : ~16:1
  On utilise à 3:1 max (conservateur) :
    $5K de capital → 1 contrat FESX = $50K notionnel
    Le ROC est sur le NOTIONNEL, pas le capital
    
  Résultat : 2% de rendement sur le notionnel = 
    2% × $50K = $1,000 → sur $5K de capital = 20% ROC

FOREX :
  Position EUR/USD $100K pour ~$5K de margin
  Levier natif 20:1, on utilise 2:1
    $5K de capital → $10K de position
    Le carry de 3-5%/an s'applique sur $10K
    = $300-500/an de carry pour $5K de capital = 6-10% ROC

OPTIONS (futur) :
  Un put spread SPY $5 de largeur = ~$500 de margin
  Premium reçu ~$100-200
  ROC = 20-40% par trade
  4 trades/mois = ~100% ROC annualisé (sur le capital alloué)

IMPACT TOTAL :
  Sans levier : $30K × 80% investi × 13%/6m = $3,120/6m
  Avec levier structurel : $30K × 85% investi × 18%/6m = $4,590/6m
  Gain : +47% de rendement absolu
```

## 2.5 LEVIER 4 — MULTI-HORIZON (le capital travaille plus longtemps)

```
ROC-4 : Le même dollar travaille sur plusieurs horizons

PROBLÈME :
  Une position intraday occupe le capital pendant 6h, pas 24h.
  Rendement = edge × temps d'exposition.
  Si le capital dort 18h/jour, on perd 75% du temps productif.

SOLUTION : Empiler les horizons sur le même capital

  $10K de capital peut SIMULTANÉMENT :
  - Être alloué à des stratégies intraday US (15:30-22:00)
  - Servir de margin pour une position swing FX (tenue 5 jours)
  - Être le cash reserve qui protège les positions overnight EU
  
  Le cash n'est PAS monolithique — il a 3 usages :
  1. Capital engagé en positions intraday (active 6h/jour)
  2. Margin pour les positions swing (engagé mais pas cash-out)
  3. Buffer de sécurité (toujours disponible)

IMPLÉMENTATION :
  L'allocateur doit calculer le "capital disponible par créneau" :
  
  def available_capital_for_slot(self, slot: str) -> float:
      """
      Le capital disponible change selon l'heure :
      - 9:00-15:30 CET : capital US est libre (pas de trades US)
        → utiliser pour les stratégies EU
      - 15:30-17:30 CET : overlap → les deux travaillent
        → attention aux limites gross exposure
      - 17:30-22:00 CET : capital EU est libre
        → utiliser pour les stratégies US
      - 22:00-9:00 CET : tout est libre sauf les swings
        → utiliser pour le carry FX
      """
      
  Résultat : le même $10K peut générer du rendement en EU le matin, 
  en US l'après-midi, et en FX la nuit. Triple utilisation.
```

## 2.6 LEVIER 5 — RÉDUCTION DES COÛTS

```
ROC-5 : Chaque dollar économisé en coûts = 1 dollar de rendement en plus

COÛTS ACTUELS (estimés sur 6 mois) :
  Commissions US Alpaca : ~$15,000 (1,800 trades)
  Commissions EU IBKR  : ~$600 (90 trades)
  Slippage US          : ~$3,600 (0.02% × $180K notionnel)
  Slippage EU          : ~$270 (0.03% × $90K notionnel)
  Données              : ~$30 (IBKR data)
  Infra                : ~$30 (Railway)
  TOTAL                : ~$19,530/6 mois = ~$39K/an

OPTIMISATIONS :
  
  a) Migrer les stratégies US haute fréquence vers IBKR
     IBKR US : $0.0005-0.0035/share (vs Alpaca $0.005)
     Économie sur ORB V2 (220 trades) : ~$500/6m
     Économie sur VWAP Micro (363 trades) : ~$800/6m
     Économie sur Triple EMA (360 trades) : ~$800/6m
     TOTAL : ~$2,100/6m = ~$4,200/an
     
  b) Utiliser des limit orders au lieu de market orders
     Le slippage est dû aux market orders.
     Limit orders avec offset de 1 tick réduisent le slippage de 50%.
     Économie estimée : $1,800/6m = $3,600/an
     RISQUE : certains ordres ne seront pas fill → missed trades
     
  c) Favoriser les instruments à faible coût
     Futures EU (~€1.50/contrat) vs Actions EU (0.10%)
     Sur un notionnel de €50K :
       Futures : €1.50 → 0.003% du notionnel
       Actions : €50 → 0.100% du notionnel
     → 33x moins cher en futures
     
  d) Réduire le nombre de trades des stratégies à faible edge
     Triple EMA (360 trades, Sharpe 1.06) → si filtres plus stricts 
     réduisent à 180 trades avec le même Sharpe → économie ~$400/6m

IMPACT TOTAL : ~$4,000-8,000/an d'économie = +1-2% de rendement net
```

## 2.7 LEVIER 6 — ALLOCATION DYNAMIQUE CROSS-TIMEZONE

```
ROC-6 : L'allocation n'est pas fixe — elle change selon l'heure du jour

CONCEPT :
  Le portefeuille a un "budget de risque" total de 80% du capital.
  Ce budget est REDISTRIBUÉ dynamiquement selon les marchés ouverts :

  9:00-15:30 CET (EU only) :
    EU Core   : 25% du capital (maximum car seul marché ouvert)
    FX Carry  : 5% (tourne 24/7)
    US Reserve: 50% (prêt pour l'ouverture US)
    
  15:30-17:30 CET (OVERLAP) :
    EU Core   : 15% (réduit car US prend le relais)
    US Core   : 40%
    FX Carry  : 5%
    Shorts    : 15%
    Cash      : 25%
    
  17:30-22:00 CET (US only) :
    US Core   : 45%
    Shorts    : 20%
    FX Carry  : 5%
    EU freed  : 10% (libéré par la fermeture EU → réutilisé en US)
    Cash      : 20%
    
  22:00-9:00 CET (OFF-HOURS) :
    FX Carry/Swing : 10%
    Cash           : 90% (protège contre les gaps overnight)

IMPLÉMENTATION :
  def get_allocation_multiplier(self, bucket: str, hour_cet: int) -> float:
      """Retourne le multiplicateur d'allocation selon l'heure."""
      if 9 <= hour_cet < 15:  # EU only
          return {'eu_core': 1.5, 'us_core': 0.0, 'fx': 1.0, 'shorts': 0.5}
      elif 15 <= hour_cet < 17:  # Overlap
          return {'eu_core': 0.8, 'us_core': 1.0, 'fx': 1.0, 'shorts': 1.0}
      elif 17 <= hour_cet < 22:  # US only
          return {'eu_core': 0.0, 'us_core': 1.2, 'fx': 1.0, 'shorts': 1.2}
      else:  # Off-hours
          return {'eu_core': 0.0, 'us_core': 0.0, 'fx': 1.0, 'shorts': 0.0}
```

## 2.8 LEVIER 7 — COMPOUNDING INTRA-PÉRIODE

```
ROC-7 : Réinvestir les gains immédiatement, pas à la fin du mois

ACTUEL :
  L'allocation est calculée sur le capital INITIAL ($100K).
  Si le portefeuille gagne $5K, les $5K restent en cash non-alloué.

CIBLE :
  L'allocation est calculée sur le capital ACTUEL (equity Alpaca + IBKR).
  Si le portefeuille est à $105K, les stratégies tradent avec $105K de base.
  Les positions sont proportionnellement plus grandes.

IMPACT :
  Avec compounding mensuel, 1% par mois = 12.68% par an.
  Avec compounding continu, 1% par mois = 12.75% par an.
  Différence faible mais sur 5 ans :
    Sans compounding : $100K × (1 + 0.12 × 5) = $160K
    Avec compounding : $100K × (1.01)^60 = $181.7K
    Gain : +$21.7K (+13.6%)

IMPLÉMENTATION :
  Dans paper_portfolio.py, le capital de référence pour le sizing 
  doit être equity_actuelle (déjà le cas dans le code récent).
  Vérifier que c'est bien equity Alpaca + IBKR combiné.
```

---

# ═══════════════════════════════════════════════════════════
# PARTIE 3 : PLAN D'EXÉCUTION
# ═══════════════════════════════════════════════════════════

## PHASE 1 : DATA + QUICK WINS (Semaine 1)

```
□ DONNÉES-1 : Fetch toutes les données EU (60 tickers + indices + ETFs)
  yfinance daily 5Y + intraday 60j
  Sauvegarder dans data_cache/eu/
  Temps : 2-3h

□ DONNÉES-2 : Analyse exploratoire EU
  Volume, ATR, corrélations, saisonnalité
  Output : eu_universe_analysis.csv
  Temps : 1h

□ ROC-7 : Vérifier le compounding (equity actuelle vs initiale)
  Vérifier paper_portfolio.py et paper_portfolio_eu.py
  Temps : 30min

□ ROC-5a : Comparer les commissions IBKR US vs Alpaca
  Pour les stratégies haute fréquence (ORB, VWAP, Triple EMA)
  Si IBKR moins cher → planifier la migration
  Temps : 1h
```

## PHASE 2 : STRATÉGIES EU PRIORITAIRES (Semaine 2-3)

```
Priorité basée sur : probabilité de succès × impact ROC × facilité

□ P0 : EU-CROSS-2 Asie → EU Catch-Up
  Données daily 5Y disponibles. Backtest rapide.
  Similaire à EU Gap Open (Sharpe 8.56). Haute probabilité.
  Temps : 3h

□ P0 : EU-ACT-3 BCE Rate Drift
  8 events/an, moves > 1-2%. Event-driven = pattern validé.
  Temps : 3h

□ P0 : EU-FUT-1 Eurostoxx Trend Following
  Futures = coûts bas. Proxy sur ^STOXX50E 1H.
  Diversifie les instruments (pas que des actions).
  Temps : 4h

□ P1 : EU-ACT-1 ASML Earnings Chain
  4 events/an mais moves > 3%. Earnings chain = pattern validé (Semi US a fait Sharpe 0.91).
  Temps : 3h

□ P1 : EU-ACT-2 Luxury Momentum China
  Spécifique EU, décorrélé de tout le reste.
  Temps : 3h

□ P1 : EU-FX-1 EUR/USD Trend Following
  Coûts ultra-bas, 24/5, décorrélé des equities.
  Complète AUD/JPY déjà validé.
  Temps : 3h

□ P1 : EU-CROSS-3 EU Close → US Afternoon
  Trade via Alpaca (pas IBKR) → pas de coûts EU.
  Temps : 2h

□ P2 : EU-ACT-4 Auto Sector German
  Spécifique EU, sympathy play.
  Temps : 3h

□ P2 : EU-ACT-6 Brent Lag Play
  Similaire au crude-equity US (Sharpe 1.85 mais 5 trades).
  Temps : 3h

□ P2 : EU-ETF-1 Sector Rotation Weekly
  Weekly = holding 5 jours, coûts amortis.
  Temps : 3h

□ P2 : EU-FX-2 EUR/GBP Mean Reversion
  Mean reversion sur paire range-bound.
  Temps : 2h

□ P2 : EU-FX-3 EUR/JPY Carry
  Similaire à AUD/JPY validé.
  Temps : 2h

□ P3 : EU-FUT-2 DAX Post-BCE
  Sous-ensemble de BCE Drift mais en futures.
  Temps : 2h

□ P3 : EU-FUT-3 Bund Rate Play
  Event-driven bonds.
  Temps : 3h

□ P3 : EU-FUT-4 Brent Momentum
  Commodities, swing.
  Temps : 3h

□ P3 : EU-VOL-1 VSTOXX/VIX Spread
  Avancé, besoin de données vol.
  Temps : 4h

□ P3 : EU-ACT-5 Banking Stress Fade
  Rare (2-8/an) mais très rentable.
  Temps : 3h

□ P3 : EU-ETF-2 Banks EU vs US
  Cross-broker trade, complexe.
  Temps : 4h

□ P3 : EU-CROSS-4 US Earnings → EU Sympathy
  Saisons earnings uniquement.
  Temps : 3h
```

## PHASE 3 : OPTIMISATION ROC (Semaine 3-4)

```
□ ROC-2 : Implémenter l'allocation cross-timezone
  Le capital se redistribue selon les marchés ouverts.
  Modifier allocator.py
  Temps : 4h

□ ROC-3 : Sizing futures avec levier structurel
  Calculer le notionnel vs capital pour chaque instrument futures.
  Limiter à 3:1 levier sur futures, 2:1 sur forex.
  Temps : 2h

□ ROC-4 : Multi-horizon stacking
  Vérifier que le même capital peut servir de margin pour :
  - Intraday EU le matin
  - Intraday US l'après-midi
  - Swing FX la nuit
  Sans dépasser les limites de gross exposure.
  Temps : 3h

□ ROC-5 : Migration commissions haute fréquence → IBKR
  Si les tests montrent que IBKR est moins cher pour les strats 
  à 200+ trades, migrer ORB V2, VWAP Micro, Triple EMA.
  Temps : 4h

□ ROC-6 : Dashboard ROC
  Ajouter une page "Capital Efficiency" au dashboard :
  - Capital investi vs dormant (pie chart par heure)
  - ROC par stratégie (rendement / capital alloué)
  - Heures de trading couvertes (timeline)
  - Cost breakdown (commissions par broker, par stratégie)
  Temps : 4h
```

## PHASE 4 : BACKTEST PORTEFEUILLE COMBINÉ (Semaine 4)

```
□ Simuler le portefeuille COMPLET (US + EU + FX + Futures) :
  - 20 stratégies US existantes
  - X stratégies EU validées (Phase 2)
  - AUD/JPY carry
  - X stratégies forex validées
  - Allocation cross-timezone dynamique
  
  Calculer :
  - Sharpe portefeuille combiné (cible > 2.5)
  - Max DD combiné (cible < 5%)
  - ROC (rendement / capital total, cible > 20%/an)
  - Corrélation EU/US (cible < 0.4)
  - Heures de trading couvertes (cible 14h/jour)
  - Capital utilisation moyenne (cible > 70%)

□ Comparer 4 scénarios :
  A. US only (actuel)
  B. US + EU actions
  C. US + EU actions + futures + forex
  D. US + EU + futures + forex + levier structurel

□ Output : rapport de simulation avec equity curves comparées
```

---

## MÉTRIQUES DE SUCCÈS

```
| Métrique                | Actuel    | Phase 2   | Phase 3   | Phase 4   |
|-------------------------|-----------|-----------|-----------|-----------|
| Stratégies actives      | 20        | 25-28     | 28-30     | 30-35     |
| Classes d'actifs        | 3         | 5         | 6         | 6         |
| Capital investi moyen   | 40%       | 55%       | 70%       | 80%       |
| Heures trading/jour     | 6.5h      | 10h       | 14h       | 14h       |
| ROC annualisé           | ~13%      | ~18%      | ~25%      | ~30%      |
| Sharpe portefeuille     | ~2.5      | ~2.8      | ~3.0      | ~3.5      |
| Max DD                  | 1.8%      | 3%        | 4%        | 5%        |
| Corrélation EU/US       | N/A       | < 0.5     | < 0.4     | < 0.3     |
| Coûts annuels           | ~$39K     | ~$42K     | ~$38K     | ~$35K     |
| Coût / rendement        | ~300%     | ~150%     | ~100%     | ~70%      |
```

---

## CONTRAINTES À RESPECTER

```
1. COÛTS EU : 0.26% round-trip actions → TP > 1.5% OBLIGATOIRE
2. FUTURES : Levier 3:1 max (pas 16:1 natif)
3. FOREX : Levier 2:1 max (pas 20:1)
4. OVERNIGHT MORT : Ne PAS re-tester (Sharpe -0.70 sur 5Y)
5. PAPER 60 JOURS : Pas de live avant validation
6. DONNÉES EU : 60 jours intraday max (yfinance) → backtest daily principalement
7. IBKR déconnecte 24h : Gestion reconnexion pour les stratégies swing
8. UK en pence (LSE) : Diviser par 100 pour avoir les prix en £
9. HORAIRES EU : 9:00-17:30 CET (pas 9:30-16:00 comme US)
10. OPTIONS EU = EUROPÉENNES : Pas d'exercice anticipé → gamma pinning différent
```

---

*TODO XXL préparé par Claude Opus 4.6 — 27 mars 2026*
*20 stratégies EU + 7 leviers ROC + plan d'exécution 4 phases*
*Ancré dans la Due Diligence V3 (pas théorique)*
