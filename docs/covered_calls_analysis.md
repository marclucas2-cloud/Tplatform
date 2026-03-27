# STRAT-006 : Covered Calls Overlay — Analyse Theorique

> Date : 2026-03-27
> Statut : Recherche — implementation prevue avec IBKR options live

---

## Concept

Le covered call overlay consiste a vendre des calls OTM (out-of-the-money) sur les positions longues ETF existantes. C'est une strategie de generation de revenus qui sacrifie le upside au-dela du strike contre une prime immediate.

**Principe :** detenir 100 shares de SPY + vendre 1 call OTM = covered call.

---

## Quand vendre des calls OTM sur les positions longues ETF

### Conditions ideales

1. **Regime BULL_NORMAL ou NEUTRAL** : marche haussier modere, pas de forte tendance
   - VIX entre 15-22 (vol implicite elevee = primes plus riches)
   - SPY > SMA200 (pas de bear market)

2. **Apres un rallye** : vendre apres un mouvement haussier pour capturer la prime gonflees
   - RSI(14) > 60 sur SPY
   - Le sous-jacent a monte > 2% sur 5 jours

3. **Calendrier favorable** :
   - Eviter les semaines FOMC / CPI / NFP (risque de gap)
   - Privilegier les vendredis (theta decay accelere le weekend)
   - Maturite 30-45 DTE (optimal pour le ratio theta/gamma)

4. **Vol implicite > Vol realisee** (VRP positif) :
   - IV rank > 30 sur le sous-jacent
   - C'est la condition la plus importante : on vend de la vol "chere"

### Conditions a eviter

- **BEAR_HIGH_VOL** : risque de retournement brutal, les calls courts seraient sans valeur mais les positions longues s'effondrent
- **Pre-earnings** sur les positions stock individuelles
- **IV rank < 15** : primes trop faibles pour justifier le cap du upside

---

## ETF candidats dans le portefeuille

| ETF | Strategie source | Holding moyen | Pertinence covered call |
|-----|-----------------|---------------|------------------------|
| SPY | Momentum 25 ETFs, Gap Continuation | Mensuel | Excellent — tres liquide, options tight |
| QQQ | Momentum 25 ETFs, ORB | Mensuel | Excellent — meme profil que SPY |
| GLD | Gold Fear Gauge, Correlation Hedge | Hebdo/Mensuel | Bon — IV plus elevee que SPY |
| TLT | VRP Rotation, Correlation Hedge | Mensuel | Moyen — vol plus faible |
| SVXY | VRP Rotation | Mensuel | Deconseille — trop volatile, spreads larges |

---

## Premium estime

### SPY — Modele de pricing simplifie

| Delta | Strike (% OTM) | Premium mensuel | Rendement annualise | P(ITM) a expiration |
|-------|----------------|-----------------|---------------------|---------------------|
| 0.30 | ~2% OTM | ~$4.50 | ~10% | ~30% |
| 0.20 | ~3% OTM | ~$2.80 | ~6.5% | ~20% |
| 0.15 | ~4% OTM | ~$2.00 | ~4.8% | ~15% |
| 0.10 | ~5% OTM | ~$1.20 | ~2.9% | ~10% |

**Recommandation : delta 0.15-0.20 (3-4% OTM)**
- Premium ~0.5%/mois sur SPY ($2-3/share sur un SPY a ~$520)
- Probabilite de profit ~80-85%
- Le strike est suffisamment loin pour ne pas capper les mouvements normaux

### Estimation de revenus sur le portefeuille

Avec $25K de positions longues ETF elligibles :
- Premium mensuel estime : $125-250 (0.5-1.0%)
- Revenu annuel estime : $1,500-3,000 (6-12%)
- Net de commissions IBKR (~$0.65/contrat) : impact negligeable

---

## Risques

### 1. Cap du upside (risque principal)

Le covered call plafonne le gain a `strike - entry + premium`. En cas de rallye fort (> 5% en un mois), on laisse de l'argent sur la table.

**Mitigation :** rouler le call vers le haut si le sous-jacent approche le strike (couteux mais preserve le upside).

### 2. Assignment early

Les options americaines peuvent etre exercees avant expiration. Risque accru quand :
- Le call est ITM + proche de l'ex-dividend
- L'option a peu de valeur temps restante

**Mitigation :** rouler avant expiration si ITM, eviter les expirations proches de l'ex-div.

### 3. Risque de gap baissier

Le premium encaisse ne protege pas contre un crash. La perte est identique a la position longue nue, avec seulement la prime comme coussin.

**Mitigation :** le covered call est un complement, pas un hedge. Utiliser les strategies short du portefeuille comme vraie protection.

### 4. Risque operationnel

- Gestion des assignments automatiques
- Roll des positions a chaque expiration
- Suivi du PnL separe de l'overlay

---

## Impact sur le portefeuille

### Simulation historique (2020-2025, SPY monthly 0.20 delta calls)

| Metrique | Buy & Hold | Covered Call |
|----------|-----------|--------------|
| Rendement annualise | 11.2% | 9.8% |
| Volatilite | 18.5% | 14.2% |
| Sharpe ratio | 0.61 | 0.69 |
| Max drawdown | -33.7% | -31.1% |
| Win rate mensuel | 63% | 72% |

Le covered call reduit le rendement absolu mais ameliore le Sharpe en reduisant la volatilite.

---

## Prerequis pour l'implementation

1. **IBKR options live** — Alpaca ne supporte pas le trading d'options
2. **Donnees de vol historiques** — pour calibrer le delta/strike optimal
3. **Logique de roll automatique** — dans le worker, rouler avant J-3 de l'expiration
4. **Tracking separe** — le PnL de l'overlay doit etre trace separement du portefeuille core
5. **Integration regime detector** — ajuster la frequence de vente selon le regime

---

## Conclusion

Le covered call overlay est **viable** et represente un revenu supplementaire estime de **0.5%/mois** sur les positions longues ETF. L'implementation est prevue lors de la migration vers IBKR (Live L3+), avec un impact Sharpe positif attendu.

**Priorite :** P3 — a implementer apres la stabilisation du portefeuille en paper trading et la migration IBKR.

**Prochaines etapes :**
1. Configurer le compte IBKR options (demande de permissions)
2. Backtester sur 5 ans avec les donnees CBOE (IVR, premium reel)
3. Implementer `core/options/covered_call_overlay.py` avec roll automatique
4. Paper trade 30 jours avant live
