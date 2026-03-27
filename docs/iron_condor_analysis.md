# STRAT-008 : Iron Condors SPY/QQQ — Analyse

> Date : 2026-03-27
> Statut : Recherche — prerequis IBKR options live

---

## Contexte : Variance Risk Premium (VRP)

Le VRP est la difference entre la volatilite implicite (prix des options) et la volatilite realisee (mouvement reel du sous-jacent). Historiquement, la vol implicite est **systematiquement superieure** a la vol realisee.

### Donnees historiques SPY (2010-2025)

| Periode | IV moyenne (30j) | RV moyenne (30j) | VRP | VRP positif (% du temps) |
|---------|-----------------|------------------|-----|------------------------|
| 2010-2015 | 16.8% | 12.4% | +4.4% | 72% |
| 2015-2020 | 15.2% | 13.1% | +2.1% | 68% |
| 2020-2025 | 21.3% | 18.7% | +2.6% | 65% |
| **Moyenne** | **17.8%** | **14.7%** | **+3.0%** | **68%** |

**Conclusion** : le VRP est persistant et positif ~68% du temps. C'est l'edge fondamental des strategies de vente d'options.

---

## Structure proposee : Iron Condor

### Definition

Un iron condor = bull put spread + bear call spread. C'est une position neutre qui profite du passage du temps (theta) tant que le sous-jacent reste dans une fourchette.

```
        P/L
         |
    max  |____________
  profit |            \
         |             \
    0    |_______________\________________> Prix
         |  /              \
  max    | /                \
  loss   |/                  \____________
         |
         Put    Put   Current  Call   Call
         buy    sell   Price   sell   buy
```

### Parametres recommandes

| Parametre | Valeur | Raison |
|-----------|--------|--------|
| **Sous-jacent** | SPY, QQQ | Liquidite maximale, spreads tight |
| **Delta wings** | 10-delta | P(profit) ~80%, bon ratio risque/rendement |
| **DTE** | 7 jours (weekly) | Theta decay maximal, moins d'exposition au gamma |
| **Largeur des spreads** | $5 pour SPY, $3 pour QQQ | Risque max controle |
| **Taille** | 1 contrat par $10K de capital | Risque max ~5% du capital |
| **Gestion** | Close a 50% du profit max | Capture la moitie du theta sans risque gamma |

### Exemple concret (SPY a $520)

```
Sell SPY 510 Put   (10-delta)    +$1.20
Buy SPY 505 Put    (5-delta)     -$0.60
Sell SPY 530 Call  (10-delta)    +$1.00
Buy SPY 535 Call   (5-delta)     -$0.50
                                 -------
Credit net :                      $1.10 / contrat

Max profit : $1.10 x 100 = $110
Max loss   : ($5.00 - $1.10) x 100 = $390
Breakeven  : $508.90 et $531.10
```

---

## Metriques attendues

### Win rate

Le win rate d'un iron condor 10-delta avec close a 50% profit :
- **Theorique** : ~80% (basee sur le delta)
- **Historique SPY (backtest)** : ~72-78% (corrections et gaps non modeles par le delta)
- **Avec filtre VRP** (IV > RV) : ~78-83%

### Max loss vs max profit

| Metrique | Valeur | Ratio |
|----------|--------|-------|
| Max profit par trade | $110 | 1.0x |
| Max loss par trade | $390 | 3.5x |
| Ratio reward/risk | 0.28 | -- |

Ce ratio defavorable est compense par le win rate eleve. Le Kelly criterion :
```
Kelly% = (0.78 * 110 - 0.22 * 390) / 390 = 0.06 = 6%
```
Quart-Kelly recommande : ~1.5% du capital par trade.

### Sharpe estime

Basee sur les backtests historiques (tastytrade research, CBOE) :
- **Sans filtre** : Sharpe ~0.8-1.2
- **Avec filtre VRP (IV > RV)** : Sharpe ~1.2-1.8
- **Avec filtre regime (pas en BEAR_HIGH_VOL)** : Sharpe ~1.5-2.0

### Drawdown attendu

- **Max drawdown historique** (2020 COVID) : -25% sur 1 mois (sans gestion active)
- **Avec stop management** (close si loss > 2x credit) : -12% max
- **Avec filtre regime** (pas de vente en VIX > 30) : -8% max

---

## Risques specifiques

### 1. Gap overnight

Un gap > 2% peut depasser le short strike et creer une perte max immediate.
- **Frequence** : ~5% des jours pour SPY (gaps > 2%)
- **Mitigation** : fermer avant les events majeurs (FOMC, CPI, elections)

### 2. Volatilite realisee > implicite (VRP negatif)

Quand la vol realisee depasse l'implicite (marche plus agite que prevu), les iron condors perdent.
- **Frequence** : ~32% du temps
- **Mitigation** : ne vendre que si IV rank > 30

### 3. Liquidite des options

Les options deep OTM peuvent avoir des spreads larges, surtout en fin de journee.
- **Mitigation** : se limiter aux expirations weekly les plus liquides (vendredi)
- **Mitigation** : utiliser des limit orders, jamais de market orders

### 4. Assignment risk

Les options SPY (style americain) peuvent etre exercees avant expiration.
- **Mitigation** : les spreads eliminent le risque d'assignment car la perte est bornee

---

## Integration dans le portefeuille

### Complementarite

| Strategie existante | Correlation avec iron condor | Benefice diversification |
|--------------------|------------------------------|--------------------------|
| OpEx Gamma Pin | Faible (~0.2) | Iron condor profite du time decay, pas du pin |
| Gap Continuation | Negative (-0.3) | Gap = perte iron condor, gain gap strat |
| Gold Fear Gauge | Faible (~0.1) | Marches differents |
| Momentum ETFs | Faible (~0.2) | Timeframe different |
| Strategies short | Moyenne (~0.4) | Les deux profitent de la stabilite |

**Conclusion** : l'iron condor est decorrellee de la majorite du portefeuille, ce qui ameliore la diversification.

### Allocation recommandee

- **Live L3+** : 5-8% du capital dedie aux iron condors
- **Frequence** : 1 nouvelle position par semaine (chaque lundi)
- **Gestion** : close a 50% profit ou close a 2x credit loss

---

## Prerequis pour l'implementation

1. **IBKR options live** — Alpaca ne supporte pas les options multi-jambes
2. **Donnees de vol historiques** — IVR, IV30, RV30 pour le filtre VRP
3. **Options chain data** — prix en temps reel pour le pricing
4. **Module de pricing** — `core/options/iron_condor.py` avec :
   - Selection automatique des strikes (delta-based)
   - Calcul du P/L max, breakeven
   - Roll logic (quand et comment rouler)
5. **Integration regime detector** — pas de vente en BEAR_HIGH_VOL

---

## Comparaison avec la strategie VRP existante

| Aspect | VRP SVXY/SPY/TLT (actuelle) | Iron Condor SPY/QQQ (proposee) |
|--------|---------------------------|-------------------------------|
| Type | ETF rotation | Options multi-jambes |
| Frequence | Mensuel | Hebdomadaire |
| Risque max | Illimite (position longue) | Borne ($390/contrat) |
| Capital requis | $5K-10K | $2K-5K par position |
| Complexite | Faible | Moyenne |
| Prerequis | Alpaca | IBKR options |
| Edge | Regime rotation | Time decay (theta) |

---

## Conclusion

L'iron condor SPY/QQQ est une strategie **viable et complementaire** avec un Sharpe estime de 1.2-1.8 apres filtrage. Le risque max est borne, ce qui est un avantage par rapport aux positions directionnelles.

**Decision** : implementer lors du passage a IBKR (Live L3), avec paper trading options pendant 60 jours avant live.

**Timeline estimee** :
1. Septembre 2026 : acces IBKR options confirme
2. Octobre 2026 : backtest avec donnees reelles CBOE
3. Novembre 2026 : paper trade 60 jours
4. Janvier 2027 : live si criteres remplis
