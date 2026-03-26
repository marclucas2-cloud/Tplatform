# Plan de Scaling — Trading Platform

> Derniere mise a jour : 2026-03-26
> Statut : Phase Paper — 14 strategies actives, capital simule $100K

---

## Seuils de passage

| Niveau | Capital | Prerequis | Delai min |
|--------|---------|-----------|-----------|
| **Paper** | $100K Alpaca + $1M IBKR | -- | J+0 |
| **Live L1** | $25K Alpaca + $5K IBKR | Sharpe 60j > 1.0, DD max < 5%, >= 200 trades | J+60 |
| **Live L2** | $50K Alpaca + $10K IBKR | Sharpe 90j > 1.0 at L1, DD max < 4% | J+120 |
| **Live L3** | $100K Alpaca + $25K IBKR | Sharpe 180j > 1.0 at L2, DD max < 3.5% | J+240 |
| **Live L4** | $250K total | Sharpe 360j > 0.8 at L3, audit CRO >= 9/10 | J+480 |

---

## Checklist passage Paper -> Live L1

### Performance
- [ ] Sharpe ratio 60 jours glissants > 1.0
- [ ] Max drawdown < 5% sur toute la periode paper
- [ ] Win rate > 45% sur l'ensemble des strategies
- [ ] >= 200 trades executes en paper
- [ ] Aucune strategie avec kill switch declenche dans les 30 derniers jours

### Risk Management
- [ ] Circuit-breaker teste et fonctionnel (simule au moins 1 declenchement)
- [ ] Bracket orders (SL/TP) verifies cote broker sur 100% des trades
- [ ] Fermeture forcee 15:55 ET testee sur 20+ sessions
- [ ] Exposition nette jamais > 40% long / 20% short sur la periode
- [ ] VaR 95% daily jamais > 2% du portefeuille

### Infrastructure
- [ ] Worker Railway uptime > 99.5% sur 60 jours
- [ ] Alerting Telegram fonctionnel (heartbeat + alertes critiques)
- [ ] Logs structures disponibles pour audit post-mortem
- [ ] Backup state files automatise
- [ ] Alpha decay monitor en place et sans alerte critique

### Operationnel
- [ ] Audit CRO complet score >= 8/10
- [ ] Documentation a jour (strategies, allocation, risk limits)
- [ ] Plan de rollback documente (comment revenir a paper en < 5 min)
- [ ] Compte Alpaca live configure (PAPER_TRADING=false pret, pas active)
- [ ] Tax report framework teste sur les trades paper

---

## Checklist passage Live L1 -> Live L2

- [ ] Sharpe 90j > 1.0 en conditions live L1
- [ ] Max drawdown < 4% en live
- [ ] Market impact model valide (slippage reel vs estime < 50% d'ecart)
- [ ] >= 500 trades live executes
- [ ] Pas de probleme d'execution (fills, latence, rejects) sur > 99% des ordres
- [ ] Short interest data integre pour les strategies short

---

## Strategies qui ne scalent pas

### Problemes de liquidite identifies

| Strategie | Tickers a risque | ADV estime | Seuil max ordre | Raison |
|-----------|------------------|------------|-----------------|--------|
| Crypto-Proxy Regime V2 | MARA | ~$200M | $5K | 10% du volume 5-min a $5K |
| Crypto-Proxy Regime V2 | RIOT | ~$100M | $3K | Tres faible liquidite |
| Crypto-Proxy Regime V2 | MSTR | ~$300M | $8K | Spread eleve en volatilite |
| Crypto-Proxy Regime V2 | COIN | ~$500M | $50K | Scalable, ADV suffisant |
| Gold Fear Gauge | GLD | ~$2B | $200K | Tres liquide, pas de souci |
| Gold Fear Gauge | High-beta shorts | Variable | $10K | Depends du ticker selectionne |
| ORB 5-Min V2 | Small caps in play | < $50M | $2K | Stocks en jeu = faible float |

### Strategies parfaitement scalables (ADV > $1B)

| Strategie | Tickers | ADV estime | Scalable jusqu'a |
|-----------|---------|------------|-----------------|
| OpEx Gamma Pin | SPY, QQQ | > $20B | $500K+ |
| Overnight Gap Continuation | SPY, QQQ, AAPL | > $10B | $500K+ |
| VWAP Micro-Deviation | Large caps | > $5B | $200K+ |
| Day-of-Week Seasonal | SPY, QQQ | > $20B | $500K+ |
| Correlation Regime Hedge | SPY, TLT, GLD | > $2B | $200K+ |
| Momentum 25 ETFs | ETFs large cap | > $1B | $100K+ |
| VRP Rotation | SVXY, SPY, TLT | > $500M | $50K+ |

---

## Market impact par strategie

### Modele utilise : Almgren-Chriss simplifie

```
impact = temporary_impact + permanent_impact
temporary = 0.005 * participation_rate^0.5
permanent = 0.002 * participation_rate
participation_rate = order_notional / (ADV / 78)  # 78 barres 5-min/jour
```

Coefficients calibres sur empirical US equities mid/small cap (Almgren et al. 2005).

### Simulation a differents niveaux de capital

| Strategie | $25K | $50K | $100K | $250K |
|-----------|------|------|-------|-------|
| OpEx Gamma Pin | 0.02% | 0.02% | 0.02% | 0.03% |
| Gap Continuation | 0.02% | 0.02% | 0.03% | 0.04% |
| Gold Fear Gauge | 0.03% | 0.04% | 0.06% | 0.10% |
| Crypto-Proxy V2 (MARA) | 0.04% | 0.07% | 0.11% | **0.25%** |
| Crypto-Proxy V2 (RIOT) | 0.05% | 0.10% | 0.16% | **0.39%** |
| Crypto-Proxy V2 (COIN) | 0.02% | 0.03% | 0.04% | 0.07% |
| VWAP Micro | 0.02% | 0.03% | 0.04% | 0.06% |
| ORB 5-Min V2 | 0.05% | 0.08% | 0.14% | **0.30%** |
| Mean Reversion V2 | 0.03% | 0.04% | 0.06% | 0.10% |
| Triple EMA | 0.03% | 0.04% | 0.05% | 0.08% |
| Pairs MU/AMAT | 0.03% | 0.04% | 0.06% | 0.10% |

**Seuil d'alerte** : impact > 0.20% = strategie non scalable a ce niveau de capital.

### Recommandations de scaling par niveau

**Live L1 ($25K)** : Toutes les 14 strategies deployables. Impact negligeable.

**Live L2 ($50K)** : Surveiller Crypto-Proxy sur MARA/RIOT. Envisager de remplacer
MARA par COIN pour les positions > $5K.

**Live L3 ($100K)** : Plafonner ORB 5-Min a 3% du capital (small caps).
Crypto-Proxy : basculer integralement sur COIN et MSTR, exclure MARA/RIOT.

**Live L4 ($250K)** : Exclure ORB 5-Min ou le limiter aux large caps uniquement.
Crypto-Proxy uniquement COIN. Pairs MU/AMAT : surveiller le spread.

---

## Plan de migration technique

### Phase 1 : Paper -> Live L1 (J+60)
1. Dupliquer `paper_portfolio_state.json` en `live_portfolio_state.json`
2. Creer `.env.live` avec `PAPER_TRADING=false`
3. Deployer un worker Railway dedie live (separe du paper)
4. Activer les alertes Telegram specifiques live (seuils plus bas)
5. Capital initial : $25K Alpaca

### Phase 2 : Live L1 -> L2 (J+120)
1. Augmenter le capital a $50K
2. Activer le market impact model dans le pipeline
3. Ajuster les seuils de liquidite par strategie
4. Integrer les donnees short interest (FINRA bi-mensuel)

### Phase 3 : Live L2 -> L3 (J+240)
1. Capital $100K
2. Deployer le ML signal filter (si >= 200 trades/strategie)
3. Ajouter broker IBKR pour les strategies daily/monthly
4. Dupliquer le worker sur un second provider (redundance)

### Phase 4 : Live L3 -> L4 (J+480)
1. Capital $250K
2. Multi-broker actif (Alpaca intraday + IBKR daily)
3. Alpha decay monitor avec auto-desactivation
4. Optimisation fiscale active (tax loss harvesting)

---

## Metriques de suivi

| Metrique | Frequence | Seuil alerte | Action |
|----------|-----------|--------------|--------|
| Sharpe rolling 30j | Quotidien | < 0.8 | Review allocation |
| Max drawdown running | Temps reel | > 3% | Circuit-breaker |
| Slippage reel vs estime | Hebdomadaire | > 50% ecart | Revoir market impact |
| Fill rate | Quotidien | < 98% | Debug execution |
| Worker uptime | Quotidien | < 99% | Escalade infra |
| Alpha decay slope | Hebdomadaire | p < 0.05, slope < 0 | Review strategie |
| Kill switch count | Quotidien | > 2 strategies | Review global |
