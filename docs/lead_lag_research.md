# STRAT-007 : Cross-Asset Lead-Lag — Recherche

> Date : 2026-03-27
> Statut : Recherche — identification des relations exploitables

---

## Relations lead-lag connues en litterature

### 1. Futures US -> Actions europeennes

**Mecanisme** : Les futures S&P 500 (ES) tradent 23h/24h. Les mouvements nocturnes (22h-9h CET) predisent l'ouverture des marches europeens.

| Signal | Lag | Amplitude | Source |
|--------|-----|-----------|--------|
| ES futures overnight return | 2-4h | 0.3-0.7 beta | Empirique, bien documente |
| ES futures close -> DAX open | 8-14h | 0.5-0.8 beta | Barclay Hedge, 2019 |
| NQ futures -> tech EU (ASML, SAP) | 2-4h | 0.4-0.6 beta | Analyse sectorielle |

**Qualite** : Relation robuste mais l'alpha est faible car largement arbitree.

### 2. VIX -> Secteurs defensifs

**Mecanisme** : Une hausse du VIX (anticipation de volatilite) declenche une rotation vers les defensifs (utilities, healthcare, consumer staples) avec un lag de 1-2 jours.

| Signal | Lag | Amplitude | Source |
|--------|-----|-----------|--------|
| VIX > 20 + hausse 2j | 1-2 jours | XLU/XLV surperform 0.5-1% | CBOE research |
| VIX spike > 30% intraday | 0-1 jour | Flight-to-quality massif | Academic (Whaley 2009) |
| VIX term structure inversion | 2-5 jours | Bear signal fiable | VIX Central |

**Qualite** : Relation forte mais non-lineaire. Le signal est surtout exploitable sur les spikes, pas en regime normal.

### 3. DXY (Dollar Index) -> Commodities

**Mecanisme** : Le dollar et les commodities sont inversement correles (commodities cotees en USD). Un renforcement du DXY pese sur l'or, le petrole, et les matieres premieres.

| Signal | Lag | Amplitude | Source |
|--------|-----|-----------|--------|
| DXY breakout > 1% weekly | 1-3 jours | GLD baisse 0.3-0.8% | Standard macro |
| DXY vs GLD | Quasi-simultane | Correlation -0.4 a -0.6 | Bloomberg, 20 ans |
| DXY vs CL (crude) | 1-2 jours | Correlation -0.3 a -0.5 | EIA data |

**Qualite** : Relation stable a long terme mais bruitee en intraday. Plus exploitable en swing trading (multi-jours).

### 4. Credit spreads -> Equities

**Mecanisme** : L'ecartement des spreads de credit (HYG-TLT, CDX) anticipe les baisses actions de 1-3 jours.

| Signal | Lag | Amplitude | Source |
|--------|-----|-----------|--------|
| HYG/TLT ratio baisse > 1% | 1-3 jours | SPY baisse 0.5-2% | Academic (Collin-Dufresne 2001) |
| CDX IG spread > +10bps | 2-5 jours | Risk-off signal fort | Goldman research |
| LQD underperform TLT | 1-2 jours | Stress signal modere | Empirique |

**Qualite** : Signal fort mais lent. Mieux adapte au regime detection qu'au trading intraday.

### 5. Crypto -> Crypto-related equities

**Mecanisme** : Bitcoin lead les actions crypto-related (COIN, MARA, MSTR) avec un lag intraday.

| Signal | Lag | Amplitude | Source |
|--------|-----|-----------|--------|
| BTC weekend move | Pre-open lundi | Beta 1.5-2.5 sur MARA | Notre backtest (Crypto-Proxy V2) |
| BTC breakout > 3% | 15-60 min | COIN suit avec beta 0.8-1.2 | Deja exploite dans le portefeuille |
| ETH/BTC ratio change | 1-2h | Rotation COIN/MARA | Observe, non teste |

**Qualite** : Exploite dans Crypto-Proxy Regime V2 (Sharpe 3.49). Potentiel d'amelioration avec le lead ETH/BTC.

---

## Relations testees dans le projet

| Relation | Strategie | Backtestee | Resultat |
|----------|-----------|-----------|---------|
| SPY/TLT correlation anomaly | Correlation Regime Hedge | Oui | Sharpe 1.09 — deployee |
| GLD up + SPY down | Gold Fear Gauge | Oui | Sharpe 5.01 — deployee |
| BTC -> COIN/MARA/MSTR | Crypto-Proxy Regime V2 | Oui | Sharpe 3.49 — deployee |
| VIX -> regime allocation | Regime Detector HMM | Oui | Utilise pour allocation |
| ES futures -> EU open | EU strategies (Phase 2) | Oui | Sharpe < 1.0 — non deployee |
| DXY -> commodities | Non testee | Non | A tester |
| Credit spreads -> SPY | Non testee | Non | A tester |
| VIX term structure | Non testee | Non | A tester |

---

## Relations prometteuses pour la suite

### Top 3 candidates

#### 1. VIX Term Structure Inversion -> SPY short (Priorite haute)

**Hypothese** : Quand la structure par terme du VIX s'inverse (VIX > VIX3M), c'est un signal bear fiable. Shorter SPY/QQQ avec un lag de 1-2 jours.

**Avantages** :
- Signal rare (3-5x/an) mais a fort alpha
- Complementaire de nos strategies short existantes
- Donnees disponibles (CBOE VIX futures, gratuit)
- Win rate historique ~65-70% (Szado & Schneeweis, 2012)

**Plan de backtest** :
1. Telecharger VIX et VIX3M historiques (CBOE, 10 ans)
2. Identifier les inversions (VIX/VIX3M ratio > 1.0)
3. Simuler un short SPY a J+1 de l'inversion, exit a J+5 ou VIX/VIX3M < 0.95
4. Walk-forward validation (70/30 split)
5. Critere de validation : Sharpe > 1.0, win rate > 55%

#### 2. Credit Spreads (HYG-TLT) -> Risk-off rotation (Priorite moyenne)

**Hypothese** : L'ecartement des spreads de credit precede les baisses actions de 1-3 jours. Utiliser comme filtre pour reduire l'exposition long et augmenter les shorts.

**Avantages** :
- Signal lead confirme en litterature academique
- HYG et TLT sont tres liquides (>$1B ADV)
- Integrable comme filtre dans le regime detector (pas de trading direct)

**Plan de backtest** :
1. Calculer le spread HYG-TLT (rendement relatif 5j)
2. Quand spread s'ecarte > 1 std sur 20j : reduire exposition long de 30%
3. Backtester l'impact sur le portefeuille global (2015-2025)
4. Comparer la performance avec et sans le filtre
5. Critere : reduction du max drawdown > 20% sans sacrifier > 5% de rendement

#### 3. DXY -> GLD Lead-lag intraday (Priorite basse)

**Hypothese** : Le DXY (via UUP ETF) lead GLD de 15-30 minutes en intraday. Un DXY breakout predicit un mouvement inverse de GLD.

**Avantages** :
- GLD est deja dans notre univers (Gold Fear Gauge)
- Le lead-lag intraday est potentiellement non-arbitre (moins de participants intraday)
- Complementaire avec la strategie Gold Fear Gauge existante

**Plan de backtest** :
1. Telecharger les donnees 5-min UUP et GLD (6 mois)
2. Cross-correlation analysis pour confirmer le lag optimal
3. Signal : UUP move > 0.3% en 30 min -> GLD position inverse
4. Walk-forward validation
5. Critere : Sharpe > 1.5 (intraday, apres couts)

---

## Prochaines etapes

| Priorite | Action | Timeline | Prerequis |
|----------|--------|----------|-----------|
| P1 | Backtester VIX term structure strategy | Semaine prochaine | Donnees CBOE VIX3M |
| P2 | Integrer HYG-TLT spread dans le regime detector | Sprint suivant | Donnees HYG/TLT daily |
| P3 | Explorer DXY -> GLD intraday lead-lag | Mois prochain | Donnees 5-min UUP |
| P4 | ETH/BTC ratio -> rotation crypto | Plus tard | Donnees crypto minute |

---

## References

- Whaley, R. (2009). "Understanding the VIX". Journal of Portfolio Management.
- Collin-Dufresne et al. (2001). "The Determinants of Credit Spread Changes". Journal of Finance.
- Szado & Schneeweis (2012). "Loosening Your Collar: Alternative Implementations of QQQ Collars". Isenberg School of Management.
- Almgren & Chriss (2005). "Optimal Execution of Portfolio Transactions". Journal of Risk.
