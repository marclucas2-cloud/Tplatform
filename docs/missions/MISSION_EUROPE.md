# MISSION AGENT EUROPE — STRATÉGIES MARCHÉS EUROPÉENS
# Date : 27 mars 2026
# Broker : Interactive Brokers (actions réelles, pas CFD)
# Marchés : DAX 40, CAC 40, FTSE 100, Eurostoxx 50, Actions individuelles EU

---

## INSTRUCTIONS

Tu es un quant researcher spécialisé sur les marchés européens.
Tu travailles sur le projet `trading-platform` de Marc.

Le projet a 17 stratégies actives, TOUTES sur le marché US (Alpaca).
Les marchés européens sont un angle mort complet : 0 stratégie, 0 backtest, 0 donnée.

Ta mission : explorer, backtester et valider des stratégies sur les marchés EU
pour diversifier le portefeuille temporellement (6h30 de trading avant l'ouverture US)
et structurellement (drivers macro différents : BCE, énergie EU, politique UE).

### RÈGLES

1. **Broker : IBKR** — actions réelles sur Euronext, Xetra, LSE. PAS de CFD.
2. **Données** : yfinance pour le backtest (tickers avec suffixe : ^GDAXI, ^FCHI, ^FTSE, ^STOXX50E, MC.PA, ASML.AS, SAP.DE, etc.)
3. **Horaires** : marché EU = 9:00-17:30 CET (Paris). Entrée au plus tôt 9:05, sortie au plus tard 17:25.
4. **Coûts IBKR Europe** : commission ~0.10% par trade (min €4), slippage 0.03% (marchés moins liquides que US)
5. **Devise** : EUR (sauf FTSE en GBP). Ignorer le risque de change pour le paper trading.
6. **Validation** : Sharpe > 0.5, PF > 1.2, trades ≥ 15, DD < 10%
7. **Période de backtest** : maximum disponible sur yfinance (typiquement 2-5 ans en daily, 60 jours en intraday)
8. **Ne t'arrête pas** avant le rapport final.

### CONTEXTE PORTEFEUILLE EXISTANT

```
17 stratégies US actives :
  11 intraday (9:35-15:55 ET = 15:35-21:55 Paris)
  3 daily (rebalance mensuel/quotidien)
  3 short/bear

Le capital dort de 9:00 à 15:30 Paris (ouverture EU → ouverture US).
C'est 6h30 de marché inexploitées chaque jour.

Les edges validés sur US :
  - OpEx Gamma Pin (Sharpe 10.41) — flux mécanique expiration options
  - Gap Continuation (Sharpe 5.22) — flux overnight
  - Day-of-Week (Sharpe 3.42) — anomalie calendaire
  - Gold Fear Gauge (Sharpe 5.01) — cross-asset risk-off
  - Crypto-Proxy V2 (Sharpe 3.49) — décorrélation intra-cluster

Les edges qui échouent sur US :
  - Mean reversion RSI/BB intraday → commissions tuent l'edge
  - Pairs intraday → pas de spread exploitable en 5M
  - Sector rotation intraday → trop bruité
  - ML/Pattern Recognition → pas assez de données
```

### ORDRE D'EXÉCUTION

```
PHASE 1 : Setup données EU (yfinance + exploration)
PHASE 2 : Transfert des winners US → EU (5 stratégies)
PHASE 3 : Stratégies spécifiques EU (8 stratégies)
PHASE 4 : Stratégies cross-timezone (4 stratégies)
PHASE 5 : Portefeuille EU simulé + intégration
PHASE 6 : Rapport final
```

---

## PHASE 1 : SETUP DONNÉES EUROPÉENNES

### 1.1 Univers de tickers

```python
# INDICES (yfinance tickers)
INDICES = {
    'DAX': '^GDAXI',
    'CAC40': '^FCHI',
    'FTSE100': '^FTSE',
    'EUROSTOXX50': '^STOXX50E',
    'IBEX35': '^IBEX',      # bonus : Espagne
}

# ETFs INDICES (plus pratiques pour le trading que les indices)
INDEX_ETFS = {
    'DAX_ETF': 'EXS1.DE',       # iShares Core DAX (Xetra)
    'CAC_ETF': 'CAC.PA',        # Amundi CAC 40 (Euronext Paris)
    'FTSE_ETF': 'ISF.L',        # iShares Core FTSE 100 (LSE)
    'STOXX50_ETF': 'SX5E.DE',   # iShares Euro Stoxx 50
    'STOXX600_ETF': 'EXSA.DE',  # iShares Stoxx Europe 600
}

# ACTIONS INDIVIDUELLES — TOP 30 EU PAR LIQUIDITÉ
EU_STOCKS = {
    # France (Euronext Paris, suffixe .PA)
    'LVMH': 'MC.PA',
    'TotalEnergies': 'TTE.PA',
    'Sanofi': 'SAN.PA',
    'L\'Oreal': 'OR.PA',
    'AirLiquide': 'AI.PA',
    'Schneider': 'SU.PA',
    'BNP_Paribas': 'BNP.PA',
    'SocieteGenerale': 'GLE.PA',
    'Hermes': 'RMS.PA',
    'Dassault': 'DSY.PA',
    
    # Allemagne (Xetra, suffixe .DE)
    'SAP': 'SAP.DE',
    'Siemens': 'SIE.DE',
    'Allianz': 'ALV.DE',
    'Deutsche_Telekom': 'DTE.DE',
    'BASF': 'BAS.DE',
    'BMW': 'BMW.DE',
    'Mercedes': 'MBG.DE',
    'Deutsche_Bank': 'DBK.DE',
    'Infineon': 'IFX.DE',
    'Adidas': 'ADS.DE',
    
    # Pays-Bas (Euronext Amsterdam, suffixe .AS)
    'ASML': 'ASML.AS',
    'Shell': 'SHEL.AS',
    'Prosus': 'PRX.AS',
    'RELX': 'REN.AS',
    'Adyen': 'ADYEN.AS',
    
    # UK (LSE, suffixe .L)
    'AstraZeneca': 'AZN.L',
    'Shell_UK': 'SHEL.L',
    'HSBC': 'HSBA.L',
    'Unilever': 'ULVR.L',
    'BP': 'BP.L',
    'Rio_Tinto': 'RIO.L',
}

# SECTEURS EU (ETFs sectoriels)
SECTOR_ETFS_EU = {
    'Tech_EU': 'EXV3.DE',       # iShares STOXX Europe 600 Technology
    'Banks_EU': 'EXV1.DE',      # iShares STOXX Europe 600 Banks
    'Energy_EU': 'EXH1.DE',     # iShares STOXX Europe 600 Oil & Gas
    'Healthcare_EU': 'EXV4.DE', # iShares STOXX Europe 600 Health Care
    'Luxury_EU': 'LUXU.PA',     # Amundi S&P Global Luxury (si dispo)
    'Auto_EU': 'EXV5.DE',       # iShares STOXX Europe 600 Automobiles
    'Industrial_EU': 'EXH4.DE', # iShares STOXX Europe 600 Industrial
}
```

### 1.2 Fetch et exploration des données

```
ÉTAPE 1 : Télécharger les données daily (5 ans) pour TOUT l'univers EU
  → yfinance, sauvegarder en Parquet dans data_cache/eu/

ÉTAPE 2 : Télécharger les données intraday (max disponible, souvent 60 jours)
  → yfinance en 5M ou 15M, sauvegarder en Parquet

ÉTAPE 3 : Analyser les propriétés statistiques :
  - Volatilité moyenne par ticker (ATR daily)
  - Volume moyen daily
  - Corrélation avec SPY / DAX / CAC
  - Saisonnalité (jour de semaine, mois)
  - Gaps d'ouverture (fréquence et taille)

ÉTAPE 4 : Identifier les tickers éligibles :
  - Volume daily > 1M EUR
  - Spread bid-ask estimé < 0.10%
  - Données disponibles > 2 ans en daily
  - Pas de problèmes de données (gaps, splits non ajustés)

OUTPUT : 
  eu_universe_stats.csv (toutes les stats par ticker)
  eu_eligible_tickers.json (liste filtrée)
```

---

## PHASE 2 : TRANSFERT DES WINNERS US → EU

L'hypothèse : les edges structurels qui marchent sur SPY/QQQ pourraient
marcher sur DAX/CAC/FTSE car les mécanismes sous-jacents sont universels.

### STRAT-EU1 : OpEx Gamma Pin — Version Eurostoxx

```python
"""
HYPOTHÈSE : Le gamma pinning existe aussi sur les marchés EU.
Les options Eurostoxx 50 expirent le 3ème vendredi du mois.
Les options DAX expirent aussi le 3ème vendredi.

TICKERS : EXS1.DE (DAX ETF), SX5E.DE (Eurostoxx ETF)
TIMING : 14:00-17:25 CET (l'après-midi, comme le US)

ENTRÉE : Mêmes règles que OpEx US mais adaptées :
  - Jour = 3ème vendredi du mois (expiration mensuelle EU)
  - AUSSI tous les vendredis (weeklies existent sur Eurostoxx)
  - Prix < round_number(VWAP) - 0.3%
  - Round number step : DAX ~50-100 pts, Eurostoxx ~25-50 pts
  - Higher low sur barre précédente
  - Volume > moyenne 10 barres

STOP LOSS : 0.5% (comme US)
TAKE PROFIT : Round number

FILTRES :
  - Range 9:00-14:00 > 1.5% = skip
  - BCE meeting day = skip (macro domine)
  - Max 2 trades/jour

NOTE IMPORTANTE : 
  Le round number pour le DAX (~18000-19000) est en POINTS, pas en $.
  Step = 50 points (ex: 18050, 18100, 18150, 18200...)
  Pour Eurostoxx (~4800-5200) : step = 25 points.

FRÉQUENCE ESTIMÉE : 30-50 trades/6 mois
DONNÉES BACKTEST : yfinance ^GDAXI et ^STOXX50E en 15M (60 jours)
  + daily (5 ans) pour les vendredis historiques
"""
```

### STRAT-EU2 : Gap d'Ouverture EU (US Close → EU Open)

```python
"""
HYPOTHÈSE : Le DAX/CAC ouvrent à 9:00 CET avec un gap basé sur :
  a) La clôture US de la veille (21:55 Paris)
  b) La session asiatique (2:00-8:00 Paris)
Le gap reflète des informations non encore pricées en Europe.
Continuation si le gap est confirmé par le volume.

TICKERS : EXS1.DE (DAX), CAC.PA (CAC), ISF.L (FTSE)
TIMING : 9:05-12:00 CET

ENTRÉE LONG :
  - Gap d'ouverture > 0.5% (plus bas que US car les indices EU bougent moins)
  - Le SPY a clôturé en hausse > 0.3% la veille (confirmation US)
  - Volume de la première barre 15M > 1.5x moyenne 
  - La première barre 15M est dans la direction du gap (continuation)

ENTRÉE SHORT :
  - Gap < -0.5%, SPY a clôturé en baisse > 0.3%, confirmation

STOP LOSS : Low/High de la première barre 15M
TAKE PROFIT : 2x le risque

FILTRES :
  - Gap > 2% = skip (probable event, trop de bruit)
  - Jour de données macro EU (CPI EU, PMI, BCE) à 10:00 = skip
  - Lundi = skip (gap weekend = bruit)
  - Max 2 trades/jour

FRÉQUENCE ESTIMÉE : 40-60 trades/6 mois
DONNÉES : yfinance ^GDAXI, ^FCHI, ^FTSE en 15M
"""
```

### STRAT-EU3 : Day-of-Week EU

```python
"""
HYPOTHÈSE : L'anomalie Monday Effect existe aussi en Europe.
Études académiques confirment : le lundi est négatif sur les marchés
EU, le vendredi est positif (effet de short covering avant weekend).
Début de mois haussier (flux pension funds EU similaires aux US).

TICKERS : EXS1.DE (DAX), CAC.PA (CAC), ISF.L (FTSE)
TIMING : 9:30-17:00 CET

RÈGLES :
  Lundi : SHORT si prix < VWAP à 10:00 et RSI(14) < 45
  Vendredi : LONG si prix > VWAP à 10:00 et RSI(14) > 55
  Début de mois (jours 1-3) : LONG si prix > VWAP

STOP LOSS : 0.5%
TAKE PROFIT : 0.3% (conservateur, anomalie faible)

FILTRES :
  - BCE meeting cette semaine = skip (macro domine)
  - ATR 20j DAX > 2% = skip (haute vol)
  - Gap > 1% = skip (event day)
  - Max 1 trade/jour

FRÉQUENCE ESTIMÉE : 30-50 trades/6 mois
DONNÉES : yfinance ^GDAXI daily (5 ans) pour le backtest statistique
"""
```

### STRAT-EU4 : Gold Fear Gauge EU

```python
"""
HYPOTHÈSE : Le signal GLD up + marché down fonctionne aussi en Europe.
L'or est un actif global, pas régional. Si GLD (ou GOLD ETF EU) monte
pendant que le DAX/CAC baisse, c'est du risk-off.

TICKERS : 
  Signal : GLD (US, via yfinance) ou IGLN.L (iShares Gold ETF, LSE)
  Trade : high-beta EU stocks à shorter
    ASML.AS, IFX.DE (semi EU), DBK.DE (Deutsche Bank), BMW.DE, ADS.DE
TIMING : 10:00-16:00 CET

ENTRÉE SHORT :
  - Gold en hausse > 0.3% à 10:00 CET
  - DAX en baisse > 0.2% à 10:00 CET
  - Le stock high-beta EU est en baisse > 0.3%
  - Volume > 1.2x moyenne
  - Shorter le stock EU high-beta avec le plus gros move

ENTRÉE LONG : PAS DE LONG (short-only risk-off)

STOP LOSS : 1.0%
TAKE PROFIT : 2.0% ou 16:00 CET

FILTRES :
  - Gold ET DAX dans la même direction = skip
  - BCE meeting today = skip
  - Max 1 position

FRÉQUENCE ESTIMÉE : 10-20 trades/6 mois
"""
```

### STRAT-EU5 : VWAP Micro EU

```python
"""
HYPOTHÈSE : Le VWAP Micro (z-score > 2.5 = reversion) fonctionne 
aussi sur les stocks EU liquides. Les algos TWAP/VWAP institutionnels
opèrent de la même façon en Europe qu'aux US.

TICKERS : ASML.AS, MC.PA, SAP.DE, TTE.PA, SAN.PA, SIE.DE, ALV.DE
  (les plus liquides d'Europe, spread serré)
TIMING : 10:00-16:30 CET (éviter première et dernière demi-heure)

ENTRÉE : Mêmes règles que VWAP Micro US :
  - Rolling VWAP 20 barres 15M
  - Z-score > 2.5 = SHORT (reversion vers VWAP)
  - Z-score < -2.5 = LONG
  - Volume confirmation > 1.2x

STOP LOSS : 0.4% (un peu plus large que US car liquidité moindre)
TAKE PROFIT : Retour à VWAP (z-score < 0.5)

FILTRES :
  - Earnings du stock dans 2 jours = skip
  - Volume < 0.5x moyenne = skip (pas assez liquide)
  - Max 3 trades/jour

FRÉQUENCE ESTIMÉE : 30-50 trades/6 mois
DONNÉES : yfinance en 15M (60 jours)

NOTE : Utiliser 15M au lieu de 5M car les marchés EU sont moins
liquides que les US. Le 5M pourrait être trop bruité.
"""
```

---

## PHASE 3 : STRATÉGIES SPÉCIFIQUES EU

Stratégies qui exploitent des edges propres à l'Europe et qui n'existent
pas sur le marché US.

### STRAT-EU6 : Luxury Sector Momentum EU

```python
"""
EDGE SPÉCIFIQUE EU : Le secteur luxe est dominé par les entreprises
européennes (LVMH, Hermès, Kering, Richemont). Quand les données 
de consommation chinoise sortent (PMI Chine, retail sales), 
le luxe EU réagit avec un lag de 2-4 heures car les traders EU 
arrivent en retard sur les news asiatiques.

TICKERS :
  Luxe : MC.PA (LVMH), RMS.PA (Hermès), KER.PA (Kering), 
         CFR.SW (Richemont si dispo via IBKR)
TIMING : 9:05-12:00 CET (matin EU, après news Chine nuit)

ENTRÉE LONG :
  - Les futures US (ES/NQ) sont en hausse overnight (> 0.3%)
  - Les données macro Chine de la nuit sont positives 
    (proxy : Hang Seng en hausse > 0.5%)
  - LVMH gap > 0.3% à l'ouverture
  - Volume > 1.3x moyenne première heure
  - Entrée au premier pullback vers VWAP

ENTRÉE SHORT :
  - Hang Seng en baisse > 0.5% + LVMH gap négatif

STOP LOSS : 0.8% (le luxe a des moves plus lents)
TAKE PROFIT : 1.5% ou 12:00 CET

FILTRES :
  - Earnings LVMH/Hermès dans 5 jours = skip
  - Pas de données macro Chine cette nuit = skip
  - Max 1 position

FRÉQUENCE ESTIMÉE : 15-25 trades/6 mois
"""
```

### STRAT-EU7 : BCE Rate Decision Drift

```python
"""
EDGE SPÉCIFIQUE EU : La BCE annonce ses décisions de taux 8x par an 
(14:15 CET suivi d'une conférence de presse à 14:45). Le drift
post-BCE est bien documenté. Les banques EU (BNP, SocGen, Deutsche Bank)
réagissent mécaniquement aux décisions de taux.

TICKERS :
  Signal : décision BCE (calendrier hardcodé)
  Trades : BNP.PA, GLE.PA, DBK.DE, ISP.MI, UCG.MI, HSBA.L
TIMING : Jour BCE, 14:15-17:00 CET

ENTRÉE LONG (banques EU) :
  - BCE a annoncé une hausse de taux OU un hold hawkish
  - Les banques EU réagissent positivement dans les 15 min
  - Volume > 2x moyenne
  - Entrée au close de la barre 14:30 (après digestion initiale)

ENTRÉE SHORT (banques EU) :
  - BCE coupe les taux OU dovish surprise
  - Banques en baisse, volume élevé

STOP LOSS : 1.0% (événement = vol élevée)
TAKE PROFIT : 2.0% ou 17:00 CET

FILTRES :
  - Décision inline (pas de surprise) = skip
  - Max 2 positions (les 2 banques les plus réactives)

FRÉQUENCE ESTIMÉE : 8-16 trades/an (8 meetings BCE)

CALENDRIER BCE 2026 (à intégrer) :
  23 janvier, 6 mars, 17 avril, 5 juin, 
  17 juillet, 11 septembre, 23 octobre, 18 décembre
"""
```

### STRAT-EU8 : ASML/Semi EU Earnings Chain

```python
"""
EDGE SPÉCIFIQUE EU : ASML est LE monopole de la lithographie EUV.
Quand ASML reporte, ça révèle la demande de TOUTE la chaîne semi 
mondiale. ASML reporte avant les US semis → c'est un LEADING INDICATOR
pour NVDA, AMD, INTC. Mais les semi EU (Infineon, STMicro, BE Semi)
réagissent aussi avec un lag intraday.

TICKERS :
  Leader : ASML.AS
  Followers EU : IFX.DE (Infineon), STMPA.PA (STMicro), BESI.AS (BE Semi)
  Followers US (pour le cross-timezone) : NVDA, AMD, LRCX, AMAT

TIMING : Jour des earnings ASML, 9:05-17:00 CET

ENTRÉE LONG (followers EU) :
  - ASML gap > 2% sur earnings beat
  - Volume > 3x (confirme le catalyseur)
  - Le follower EU gap < 1% (n'a pas encore réagi)
  - La première barre 15M du follower est dans la direction du gap ASML
  - Entrée au close de cette barre

ENTRÉE SHORT :
  - ASML gap < -2% sur miss, followers n'ont pas encore réagi

STOP LOSS : 1.2% (earnings = vol élevée)
TAKE PROFIT : 2.5% ou EOD

FILTRES :
  - ASML fade > 50% dans la première heure = skip
  - Le follower a AUSSI des earnings cette semaine = skip
  - Max 2 positions

FRÉQUENCE ESTIMÉE : 4-8 trades/an (ASML reporte 4x/an)
"""
```

### STRAT-EU9 : Auto Sector EU (BMW/Mercedes/VW)

```python
"""
EDGE SPÉCIFIQUE EU : Le secteur auto allemand est très corrélé en
intraday. Quand un des 3 grands (BMW, Mercedes, VW) fait un move > 2% 
sur une news spécifique (ventes Chine, régulation émissions, EV news), 
les 2 autres suivent avec un lag de 30-60 min.

TICKERS :
  BMW.DE, MBG.DE (Mercedes), VOW3.DE (VW), P911.DE (Porsche si dispo)
TIMING : 9:30-16:00 CET

ENTRÉE LONG (sympathie) :
  - Un des 3 autos gap > 1.5% sur une news (volume > 2x)
  - Les 2 autres ont gappé < 0.5% (lag)
  - Le retardataire commence à bouger dans la direction du leader
  - Volume du retardataire > 1.3x

ENTRÉE SHORT : inverse

STOP LOSS : 1.0%
TAKE PROFIT : 50% du move du leader ou EOD

FILTRES :
  - Le leader fade > 50% dans la première heure = skip
  - Le retardataire a sa propre news = skip
  - Max 1 trade (1 seul retardataire)

FRÉQUENCE ESTIMÉE : 10-20 trades/6 mois
"""
```

### STRAT-EU10 : Energy EU — Brent/TTF Lag

```python
"""
EDGE SPÉCIFIQUE EU : TotalEnergies, Shell, BP réagissent au Brent 
(pas au WTI) et au gaz TTF (pas au Henry Hub). Le Brent trade à 
Londres (ICE), le TTF à Amsterdam. Les energy stocks EU rattrapent
le Brent avec un lag similaire au crude-equity lag US.

TICKERS :
  Signal : BZ=F (Brent futures yfinance), ou proxy via shell/BP spread
  Trades : TTE.PA (Total), SHEL.AS (Shell), BP.L (BP), ENI.MI (Eni)
TIMING : 10:00-16:00 CET

ENTRÉE LONG :
  - Brent en hausse > 0.5% à 10:00
  - Le stock energy EU est en hausse < 0.3% (lag)
  - Volume normal (pas de news stock-specific)
  - Au-dessus du VWAP

ENTRÉE SHORT :
  - Brent en baisse > 0.5%, stock energy résiste

STOP LOSS : 0.8%
TAKE PROFIT : 1.2%

FILTRES :
  - OPEC meeting today = skip (le move Brent est event-driven, pas normal)
  - Earnings du stock = skip
  - Max 2 trades/jour

FRÉQUENCE ESTIMÉE : 20-35 trades/6 mois
"""
```

### STRAT-EU11 : Banking Stress Contagion EU

```python
"""
EDGE SPÉCIFIQUE EU : Les banques EU sont beaucoup plus fragiles que 
les US (capital ratio plus bas, NPL plus élevés, souverain risk).
Quand une banque EU chute > 5% intraday, la contagion est PLUS FORTE 
et PLUS DURABLE qu'aux US. Mais elle est aussi plus souvent EXCESSIVE.
Fader la contagion sur les banques saines.

TICKERS :
  Toutes les banques EU : BNP.PA, GLE.PA, DBK.DE, CBK.DE, 
  UCG.MI, ISP.MI, HSBA.L, BARC.L, SAN.MC, BBVA.MC
TIMING : 10:00-16:00 CET

ENTRÉE LONG (fade contagion) :
  - Une banque EU chute > 5% intraday (patient zéro)
  - Les autres banques baissent > 2% en sympathie
  - Identifier les banques qui baissent MAIS qui :
    a) Ont un CET1 > 12% (bien capitalisées)
    b) Sont dans un pays DIFFÉRENT (pas le même risque souverain)
    c) Ont un volume qui se calme (panique s'estompe)
  - Acheter au premier reversal

ENTRÉE SHORT : PAS DE SHORT (on fade l'excès uniquement)

STOP LOSS : Low du jour - 0.5%
TAKE PROFIT : Récupération de 50% du drop de contagion

FILTRES :
  - Vrai problème systémique (intervention BCE, FDIC européen) = skip
  - Plus de 3 banques > -5% = skip (contagion réelle)
  - Max 1 position

FRÉQUENCE ESTIMÉE : 2-8 trades/an (rare mais très rentable)
"""
```

### STRAT-EU12 : Eurostoxx Mean Reversion Weekly

```python
"""
EDGE : Le mean reversion WEEKLY fonctionne mieux que le intraday.
Quand l'Eurostoxx 50 sous-performe le S&P 500 de > 2% sur 1 semaine,
il tend à rattraper la semaine suivante (arbitrage de valorisation).

TICKERS : SX5E.DE (Eurostoxx 50 ETF) vs SPY
TIMING : Achat lundi open, vente vendredi close

ENTRÉE LONG :
  - Eurostoxx a sous-performé SPY de > 2% la semaine précédente
  - Eurostoxx au-dessus de sa SMA(50) daily
  - Achat lundi open

ENTRÉE SHORT :
  - Eurostoxx a surperformé SPY de > 2% = short (mean reversion)

STOP LOSS : -2%
TAKE PROFIT : Vendredi close

FILTRES :
  - BCE meeting cette semaine = skip
  - FOMC meeting cette semaine = skip
  - Max 1 position

FRÉQUENCE ESTIMÉE : 10-15 trades/6 mois
HOLDING : 5 jours

DONNÉES : yfinance ^STOXX50E vs SPY daily 5 ans
"""
```

### STRAT-EU13 : EU Close → US Open Signal

```python
"""
EDGE CROSS-TIMEZONE : La clôture européenne (17:30 CET = 11:30 ET)
contient de l'information sur le sentiment qui n'est pas encore 
complètement pricée aux US. Si le DAX clôture en hausse forte 
(> 1%) alors que les US sont flat à 11:30, les US tendent à 
rallier l'après-midi (12:00-16:00 ET).

TICKERS :
  Signal : ^GDAXI (DAX), ^STOXX50E (Eurostoxx)
  Trade : SPY, QQQ (via Alpaca, pas IBKR — on est déjà sur Alpaca)
TIMING : Signal à 17:30 CET (11:30 ET), Trade 12:00-15:55 ET

ENTRÉE LONG SPY :
  - DAX a clôturé en hausse > 1% à 17:30 CET
  - SPY est flat (< 0.3%) à 11:30 ET
  - Le signal = la divergence (EU très positif, US pas encore)
  - Acheter SPY à 12:00 ET

ENTRÉE SHORT :
  - DAX a clôturé en baisse > 1%, SPY flat → SHORT SPY

STOP LOSS : 0.4%
TAKE PROFIT : 0.6% ou 15:55 ET

FILTRES :
  - DAX et SPY dans la même direction > 0.5% = skip (pas de divergence)
  - FOMC/CPI aujourd'hui = skip
  - Max 1 trade

FRÉQUENCE ESTIMÉE : 15-25 trades/6 mois
NOTE : Cette stratégie trade aux US (Alpaca) mais le SIGNAL vient d'EU.
"""
```

---

## PHASE 4 : STRATÉGIES CROSS-TIMEZONE

### STRAT-EU14 : US After-Hours → EU Pre-Market Drift

```python
"""
EDGE : Les earnings US after-hours (AAPL, MSFT, META reportent à 22:00 CET)
créent un gap le lendemain matin en Europe sur les stocks corrélés.
Ex : AAPL beat → ASML gap up, MSFT miss → SAP gap down.

TICKERS :
  Signal : AAPL, MSFT, NVDA, META, AMZN (earnings after-hours US)
  Trade : ASML.AS, SAP.DE, IFX.DE (semi EU si NVDA signal), 
          MC.PA (luxe si consumer signal)
TIMING : Lendemain matin 9:05-11:00 CET

ENTRÉE LONG :
  - Un des FAANG a reporté un beat after-hours (gap > 3% pre-market US)
  - Le stock EU corrélé gap > 0.5% à l'ouverture
  - Volume > 1.5x moyenne
  - Première barre 15M dans la direction du gap

ENTRÉE SHORT : inverse pour les miss

STOP LOSS : 1.0%
TAKE PROFIT : 2.0% ou 11:00

FILTRES :
  - Le stock EU a AUSSI des earnings cette semaine = skip
  - Le gap EU > 3% (déjà pricé) = skip
  - Max 1 trade

MAPPING SIGNAL → TRADE :
  NVDA/AMD earnings → ASML.AS, IFX.DE
  AAPL earnings → aucun EU direct (skip)
  META earnings → Adyen.AS (digital advertising)
  MSFT earnings → SAP.DE (enterprise software)
  AMZN earnings → Zalando, Adyen (e-commerce EU)

FRÉQUENCE ESTIMÉE : 8-15 trades/6 mois (saisons earnings)
"""
```

### STRAT-EU15 : Overnight EU (Hold EU stocks pendant la nuit)

```python
"""
EDGE : Le POC overnight sur SPY est validé (le return overnight est 
positif en bull). Tester la même chose sur les indices/stocks EU.
L'overnight EU inclut la session US entière (15:30-22:00 CET) 
qui contient beaucoup d'information.

TICKERS : EXS1.DE (DAX ETF), MC.PA (LVMH), ASML.AS
TIMING : Achat 17:20 CET, vente 9:10 CET lendemain

ENTRÉE LONG :
  - Le stock/indice EU est en hausse sur la journée
  - Les futures US (ES) sont stables ou positifs à 17:20
  - Achat à 17:20 CET

STOP LOSS : Vente à 9:10 quoi qu'il arrive
TAKE PROFIT : Vente à 9:10

FILTRES :
  - Vendredi soir = skip (weekend risk)
  - Earnings FAANG ce soir = skip (gap imprévisible)
  - BCE/FOMC demain = skip
  - Sizing : 3% max (overnight = plus de risque)

FRÉQUENCE ESTIMÉE : 50-70 trades/6 mois
DONNÉES : yfinance daily (close vs next open)
"""
```

### STRAT-EU16 : Morning Catch-Up (EU rattrape l'Asie)

```python
"""
EDGE : Quand la session asiatique (Nikkei, Hang Seng) fait un move
fort dans une direction, l'Europe ouvre avec un gap dans la même
direction mais ne rattrape que partiellement dans la première heure.
Le rattrapage continue 2-3 heures.

TICKERS : EXS1.DE (DAX), CAC.PA (CAC)
TIMING : 9:05-12:00 CET

ENTRÉE LONG :
  - Nikkei (^N225) en hausse > 1% à la clôture (8:00 CET)
  - DAX/CAC gap > 0.3% à 9:00 (rattrapage partiel)
  - Volume première barre > 1.3x
  - Continuation confirmée (première barre verte)

ENTRÉE SHORT :
  - Nikkei en baisse > 1%, DAX gap négatif

STOP LOSS : 0.5%
TAKE PROFIT : 1.0% ou 12:00

FILTRES :
  - Gap EU > 1.5% = skip (tout est déjà pricé)
  - Données macro EU à 10:00 = skip
  - Max 1 trade

FRÉQUENCE ESTIMÉE : 20-30 trades/6 mois
"""
```

### STRAT-EU17 : Sector Rotation EU/US (cross-timezone)

```python
"""
EDGE : Quand un secteur surperforme massivement aux US la veille,
le même secteur en EU tend à surperformer le lendemain matin.
Les flux de rotation sectorielle sont GLOBAUX mais avec un décalage 
de timezone.

TICKERS :
  Signal US (veille) : XLK, XLF, XLE, XLV
  Trade EU (lendemain) :
    XLK up → acheter EXV3.DE (Tech EU), ASML, SAP
    XLF up → acheter EXV1.DE (Banks EU), BNP, DBK
    XLE up → acheter EXH1.DE (Energy EU), TTE, SHEL
    XLV up → acheter EXV4.DE (Healthcare EU), SAN, AZN

TIMING : 9:05-14:00 CET (avant l'overlap US)

ENTRÉE LONG :
  - Le sector ETF US a surperformé SPY de > 0.8% la veille
  - Le sector ETF EU correspondant gap > 0.2% à l'ouverture
  - Volume > 1.2x
  - Acheter le sector ETF EU OU le meilleur composant

STOP LOSS : 0.8%
TAKE PROFIT : 1.2% ou 14:00 (avant l'ouverture US qui apporte du bruit)

FILTRES :
  - Tous les secteurs US en hausse uniforme = skip (pas de rotation)
  - BCE/FOMC cette semaine = skip
  - Max 1 position

FRÉQUENCE ESTIMÉE : 15-25 trades/6 mois
"""
```

---

## PHASE 5 : PORTEFEUILLE EU SIMULÉ

```
OBJECTIF : Simuler un portefeuille EU autonome et mesurer l'impact 
de l'ajout au portefeuille US existant.

SIMULATION :
1. Backtester chaque stratégie EU individuellement
2. Combiner les winners EU en un mini-portefeuille
3. Combiner le mini-portefeuille EU avec le portefeuille US existant
4. Calculer :
   - Sharpe EU seul vs US seul vs combiné
   - Corrélation EU vs US (devrait être < 0.5)
   - Max DD combiné (devrait être < max DD US seul)
   - Heures de trading couvertes : devrait passer de 6.5h à 13h

ALLOCATION EU DANS LE PORTEFEUILLE GLOBAL :
  Si Sharpe EU > 1.0 → allouer 15-20% du capital aux stratégies EU
  Si Sharpe EU 0.5-1.0 → allouer 10% max
  Si Sharpe EU < 0.5 → ne pas déployer, continuer la recherche

SIZING :
  Les stratégies EU sont en EUR/GBP → le sizing doit tenir compte 
  du taux de change. Pour le paper trading, ignorer le FX risk.
  Pour le live, hedger le FX via un short EUR/USD proportionnel.
```

---

## PHASE 6 : RAPPORT FINAL

```markdown
# RAPPORT STRATÉGIES EUROPÉENNES

## Setup données
- Tickers testés : X
- Données disponibles : X ans daily, X jours intraday
- Univers éligible : X tickers

## Transferts US → EU (5 stratégies)
| Stratégie | Sharpe US | Sharpe EU | Transfère ? | Ajustements |
| OpEx EU | 10.41 | X | OUI/NON | ... |
| Gap EU | 5.22 | X | OUI/NON | ... |
| DoW EU | 3.42 | X | OUI/NON | ... |
| Gold Fear EU | 5.01 | X | OUI/NON | ... |
| VWAP Micro EU | 3.08 | X | OUI/NON | ... |

## Stratégies spécifiques EU (8)
| Stratégie | Sharpe | WR | PF | DD | Trades | Verdict |
| Luxury Momentum | X | X | X | X | X | VALIDÉ/REJETÉ |
| BCE Drift | X | X | X | X | X | VALIDÉ/REJETÉ |
| ... |

## Stratégies cross-timezone (4)
| ... |

## Corrélation EU vs US
  Corrélation moyenne : X (cible < 0.5)
  Diversification bénéfice : Sharpe combiné vs US seul

## Allocation recommandée
  % du capital total à allouer aux stratégies EU : X%

## Prochaines étapes
  1. Connecter IBKR aux marchés EU
  2. Paper trading EU pendant 1 mois
  3. Intégrer au pipeline unifié
```

---

## NOTES TECHNIQUES

### Différences EU vs US à prendre en compte

```
1. LIQUIDITÉ : Les marchés EU sont 3-5x moins liquides que les US.
   Le slippage doit être plus élevé dans le backtest (0.03% vs 0.02%).

2. HORAIRES : EU = 9:00-17:30 CET. PAS de pre/after hours significatifs.
   Les futures Eurostoxx tradent plus longtemps mais avec peu de volume.

3. COMMISSIONS IBKR EU : ~0.10% par trade (min €4).
   C'est 20x plus cher que Alpaca US ($0 commissions).
   Les stratégies EU doivent avoir un edge plus large pour compenser.

4. SPREAD : Les actions EU mid-cap ont des spreads de 0.05-0.15%.
   Rester sur les mega-caps EU (LVMH, ASML, SAP, Total, Shell).

5. DEVISES : DAX en EUR, FTSE en GBP. Le FX risk est négligeable 
   intraday mais important en swing/weekly.

6. DONNÉES : yfinance a moins de données intraday pour l'EU (~60 jours).
   Le backtest sera principalement sur daily (5 ans).
   Les stratégies intraday seront validées sur un échantillon plus petit.

7. JOURS FÉRIÉS : Différents des US. Chaque pays a ses propres jours 
   fériés. Le DAX ferme certains jours où le CAC est ouvert et vice versa.
   Intégrer un calendrier par pays.

8. EARNINGS : Les entreprises EU reportent souvent AVANT l'ouverture 
   (7:00-8:00 CET), pas after-hours comme aux US. Le gap d'ouverture 
   EU contient l'information earnings directement.

9. TICKS : Les actions EU sont cotées en EUR/GBP avec 2 décimales 
   (pas 4 comme aux US). Les round numbers sont différents.

10. OPTIONS EU : Les options Eurostoxx 50 et DAX sont européennes 
    (exercice à expiration uniquement), pas américaines. Le gamma 
    pinning est DIFFÉRENT — il n'y a pas d'exercice anticipé, donc 
    l'effet de pinning pourrait être plus faible ou plus fort.
```

### Calendrier BCE 2026

```python
BCE_DATES_2026 = [
    "2026-01-23",
    "2026-03-06",
    "2026-04-17",
    "2026-06-05",
    "2026-07-17",
    "2026-09-11",
    "2026-10-23",
    "2026-12-18",
]
```

---

*Document préparé par Claude Opus 4.6 — 27 mars 2026*
*17 stratégies EU : 5 transferts US→EU + 8 spécifiques EU + 4 cross-timezone*
*Marchés : DAX, CAC, FTSE, Eurostoxx, 30+ actions individuelles*
*Broker : IBKR (actions réelles)*
