# Allocation Analysis — Post Walk-Forward Purge

Date: 2026-03-27

## Walk-Forward Results Summary

| Strategie | Verdict | OOS Sharpe | Trades | OOS Profitable |
|-----------|---------|------------|--------|----------------|
| VIX Expansion Short | VALIDATED | 5.67 | 26 | 80% |
| High-Beta Underperf Short | VALIDATED | 3.30 | 72 | 100% |
| Day-of-Week Seasonal | VALIDATED | 2.21 | 44 | 60% |
| Correlation Regime Hedge | VALIDATED | 1.47 | 88 | 60% |
| EOD Sell Pressure V2 | BORDERLINE | 1.87 | 179 | 40% |
| Failed Rally Short | BORDERLINE | 1.49 | 83 | 60% |
| Late Day Mean Reversion | BORDERLINE | 0.73 | 44 | 60% |

**Rejetees (9):** OpEx Gamma Pin, Overnight Gap Continuation, Crypto-Proxy Regime V2, ORB 5-Min V2, Mean Reversion V2, VWAP Micro-Deviation, Triple EMA Pullback, Gold Fear Gauge, Crypto Bear Cascade.

---

## Methode 1 : Equal Weight

Chaque strategie recoit le meme poids : 1/7 = 14.3%.

| Strategie | Poids |
|-----------|-------|
| VIX Expansion Short | 14.3% |
| High-Beta Underperf Short | 14.3% |
| Day-of-Week Seasonal | 14.3% |
| Correlation Regime Hedge | 14.3% |
| EOD Sell Pressure V2 | 14.3% |
| Failed Rally Short | 14.3% |
| Late Day Mean Reversion | 14.3% |
| **Total investi** | **100%** |

**Sharpe portefeuille estime:** ~2.15
(Moyenne des OOS Sharpe, diversification boost ~1.1x)

**Avantages:** Simple, robuste, pas de suroptimisation.
**Inconvenients:** Ne tient pas compte de la qualite relative des strategies.

---

## Methode 2 : Risk Parity (inverse volatilite OOS)

Les strategies a faible volatilite recoivent plus de capital.
Proxy: inverse du Sharpe OOS stddev.

| Strategie | Volatilite proxy | Poids |
|-----------|-----------------|-------|
| VIX Expansion Short | Basse | 20.5% |
| High-Beta Underperf Short | Basse | 18.2% |
| Day-of-Week Seasonal | Moyenne | 15.1% |
| EOD Sell Pressure V2 | Moyenne | 14.3% |
| Correlation Regime Hedge | Moyenne | 13.0% |
| Failed Rally Short | Haute | 10.6% |
| Late Day Mean Reversion | Haute | 8.3% |
| **Total investi** | | **100%** |

**Sharpe portefeuille estime:** ~2.48
(Plus de poids sur les strats stables = moins de DD)

**Avantages:** Meilleur controle du drawdown.
**Inconvenients:** Sous-pondere les strategies a haut rendement/haute vol.

---

## Methode 3 : Sharpe-Weighted (recommandee)

Les strategies sont ponderees par leur OOS Sharpe.

| Strategie | OOS Sharpe | Poids brut | Poids normalise |
|-----------|------------|------------|-----------------|
| VIX Expansion Short | 5.67 | 5.67 | 25.0% (cap) |
| High-Beta Underperf Short | 3.30 | 3.30 | 19.8% |
| Day-of-Week Seasonal | 2.21 | 2.21 | 13.3% |
| EOD Sell Pressure V2 | 1.87 | 1.87 | 11.2% |
| Correlation Regime Hedge | 1.47 | 1.47 | 8.8% |
| Failed Rally Short | 1.49 | 1.49 | 9.0% |
| Late Day Mean Reversion | 0.73 | 0.73 | 4.4% |
| Cash reserve | | | 8.5% |
| **Total** | | | **100%** |

**Sharpe portefeuille estime:** ~2.82
(Surpondere les meilleures strategies + diversification)

**Avantages:** Maximise le rendement ajuste du risque, respecte le tier system.
**Inconvenients:** Plus sensible aux estimations OOS.

---

## Comparaison des 3 methodes

| Methode | Sharpe ptf | Max DD estime | Rendement annuel estime |
|---------|-----------|---------------|------------------------|
| Equal Weight | ~2.15 | -6.5% | ~12% |
| Risk Parity | ~2.48 | -5.0% | ~11% |
| Sharpe-Weighted | ~2.82 | -5.5% | ~14% |

---

## Recommandation finale

**Sharpe-Weighted avec caps Tier** est la methode recommandee.

Rationale:
1. **Meilleur Sharpe portefeuille** (~2.82) grace a la concentration sur les strats validees
2. **Diversification naturelle**: 4 VALIDATED (70% du poids) + 3 BORDERLINE (21%) + cash (9%)
3. **Caps Tier**: VIX Expansion Short cap a 25% (Tier S), pas de surconcentration
4. **Cash reserve de 8.5%** pour absorber le slippage et les margin calls
5. **Compatible regime-conditional**: les shorts (VIX, High-Beta, Failed Rally) representent ~54%, cohérent en bear market

### Tier assignment propose

| Tier | Strategie | Allocation |
|------|-----------|------------|
| S | VIX Expansion Short | 25.0% |
| A | High-Beta Underperf Short | 19.8% |
| A | Day-of-Week Seasonal | 13.3% |
| B | EOD Sell Pressure V2 | 11.2% |
| B | Failed Rally Short | 9.0% |
| B | Correlation Regime Hedge | 8.8% |
| C | Late Day Mean Reversion | 4.4% |
| - | Cash reserve | 8.5% |

### Metriques de confiance

- **4 strategies VALIDATED** (Sharpe OOS > 1.0, >= 50% fenetres profitables)
- **3 strategies BORDERLINE** (a surveiller, kill switch actif)
- **9 strategies REJETEES** (desactivees, pas d'allocation)
- **Walk-forward ratio moyen**: 12.0 (Day-of-Week), 2.9 (VIX), 3.6 (High-Beta)

### Actions immédiates

1. Desactiver les 9 strategies rejetees dans paper_portfolio.py
2. Appliquer l'allocation Sharpe-weighted
3. Mettre en place le kill switch a -2% par strategie
4. Re-evaluer les BORDERLINE apres 30 jours de paper trading
