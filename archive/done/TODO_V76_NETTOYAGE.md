# TODO V7.6 — NETTOYAGE INCOHÉRENCES + CORRECTIONS SYNTHÈSE
## 10 corrections identifiées dans la V7.5
### Date : 27 mars 2026 | Post-audit synthèse

---

## INSTRUCTIONS AGENT

```
CE DOCUMENT CORRIGE LES 10 INCOHÉRENCES DE LA SYNTHÈSE V7.5.
Chaque fix est chirurgical : modifier la section concernée, pas réécrire.
Le code n'est PAS impacté — c'est un nettoyage de specs/docs/configs.

SÉQUENCE : appliquer les 10 fixes dans l'ordre, vérifier la cohérence globale.
TEMPS TOTAL : ~4h
```

---

### □ FIX-001 — Nettoyer le tableau volume live IBKR (supprimer les borderline)
```yaml
priorité: P0
temps: 15min
section: 3 — Allocation V5, "Volume live cible Phase 1"
```

**Problème** : Le tableau inclut Late Day MR, Failed Rally Short, EOD Sell Pressure
(12-19 trades/mois, 1/16 Kelly). Elles ont été droppées en V7.3 (OPTIM-001)
car les commissions IBKR tuent le P&L sur des positions de $500.

**Fix** : Remplacer le tableau par :

```
Volume live cible Phase 1 — IBKR ONLY (6 stratégies semaine 1, 8 semaine 2)

| Stratégie              | Freq/mois | Sizing     | Source      |
|------------------------|:---------:|:----------:|-------------|
| EUR/USD Trend          | 4-6       | 1/8 Kelly  | FX tier 1   |
| EUR/GBP Mean Reversion | 3-4       | 1/8 Kelly  | FX tier 1   |
| EUR/JPY Carry          | 6-8       | 1/8 Kelly  | FX tier 1   |
| AUD/JPY Carry          | 6-8       | 1/8 Kelly  | FX tier 1   |
| GBP/USD Trend          | 3-4       | 1/8 Kelly  | FX-002      |
| EU Gap Open            | 10-12     | 1/4 Kelly  | OPTIM-004   |
| TOTAL SEMAINE 1        | 32-42     |            |             |
| MCL Brent Lag (jour 5) | 15-20     | 1/8 Kelly  | OPTIM-005   |
| MES Trend (jour 5)     | 5-8       | 1/8 Kelly  | OPTIM-005   |
| TOTAL SEMAINE 2+       | 52-70     |            |             |

NOTE : Les 3 borderline US (Late Day MR, Failed Rally Short, EOD Sell
Pressure) sont en PAPER ONLY sur IBKR. Réactivation possible en phase 2
si Alpaca ajouté ou si sizing augmente au-dessus de $2K/position.
```

---

### □ FIX-002 — Corriger le Smart Router V3 (crypto_margin, pas crypto_perp)
```yaml
priorité: P0
temps: 10min
section: 7 — Infrastructure V5, ligne Smart Router
```

**Problème** : "Route equities/FX/futures/crypto_spot/crypto_perp"
→ Il n'y a PAS de crypto_perp en France.

**Fix** : Remplacer par :
```
Smart Router V3 : Route equities/FX/futures/crypto_spot/crypto_margin
```

Et dans le code `core/broker/factory.py` :
```python
ASSET_ROUTES = {
    "EQUITY": "ALPACA" or "IBKR",
    "FX": "IBKR",
    "FUTURES": "IBKR",
    "CRYPTO_SPOT": "BINANCE",
    "CRYPTO_MARGIN": "BINANCE",   # Margin isolated, PAS de perp
    # "CRYPTO_PERP": supprimé — non disponible en France
}
```

---

### □ FIX-003 — Harmoniser le Sharpe KPI (0.3 gate M1, 2.0 gate M2+)
```yaml
priorité: P0
temps: 15min
section: 10 — Feuille de route, "KPI de validation"
```

**Problème** : La checklist dit "Sharpe > 2.0 avant chaque scale-up"
mais RECAL-001 (V7.3) a recalibré à Sharpe > 0.3 pour le gate M1.

**Fix** : Remplacer la section KPI par :

```
KPI de validation (avant chaque scale-up) :

GATE M1 ($10K → $15K) — critères adaptés petit échantillon :
  - Min 15 trades live
  - Max DD < 5%
  - Sharpe > 0.3 (secondaire, pas significatif sur 15 trades)
  - Win rate > 42%
  - Profit factor > 1.1
  - 0 bug critique

GATE M2+ ($15K → $20K → $25K) — critères standard :
  - Min 50 trades live cumulés
  - Max DD < 8%
  - Sharpe > 1.0 (significatif sur 50+ trades)
  - Win rate > 48%
  - Profit factor > 1.3
  - 0 bug critique

NOTE : Le Sharpe n'est PAS un critère primaire du gate M1.
Sur 15-20 trades, un Sharpe de 0.3 n'est pas statistiquement
distinguable de 0. Les critères primaires fiables sur petit
échantillon : max_drawdown, bugs, réconciliation, exécution quality.
```

---

### □ FIX-004 — Remplacer le tableau kill switch MC par les stratégies LIVE
```yaml
priorité: P0
temps: 30min
section: 4 — Risk Management V4, "Kill switch calibré"
```

**Problème** : Le tableau montre OpEx Gamma, VWAP Micro, ORB V2, Gap Cont
→ toutes archivées. Aucune valeur opérationnelle.

**Fix** : Remplacer par les seuils des stratégies LIVE :

```
Kill switch calibré par stratégie — LIVE V7.3

IBKR :
| Stratégie              | Type        | Seuil kill  | Rationale                    |
|------------------------|-------------|:-----------:|------------------------------|
| EUR/USD Trend          | FX swing    | -3.0%       | Move 200 pips = -$250 normal |
| EUR/GBP MR             | FX swing    | -3.0%       | Idem                         |
| EUR/JPY Carry          | FX swing    | -3.0%       | Idem                         |
| AUD/JPY Carry          | FX swing    | -3.0%       | Idem                         |
| GBP/USD Trend          | FX swing    | -3.0%       | Idem                         |
| EU Gap Open            | EU intraday | -1.5%       | Intraday, DD doit être limité|
| MCL Brent Lag          | Futures     | -2.5%       | 25 ticks adverse = $250      |
| MES Trend              | Futures     | -2.5%       | 20 points adverse = $25/pt   |
| PORTFOLIO IBKR         | Global      | -4.0% daily | Aligné gate M1 max DD        |

CRYPTO (Binance France) :
| Stratégie              | Type        | Seuil kill  | Rationale                    |
|------------------------|-------------|:-----------:|------------------------------|
| BTC/ETH Dual Momentum  | Margin      | -5.0%       | Crypto = vol 3-5x equities   |
| Altcoin Relative Str   | Margin      | -6.0%       | Altcoins plus volatils       |
| BTC Mean Reversion     | Spot        | -3.0%       | Spot only, risque limité     |
| Vol Breakout           | Margin      | -4.0%       | Trades courts, stops serrés  |
| BTC Dominance          | Spot        | -3.0%       | Spot only, hebdomadaire      |
| Borrow Rate Carry      | Earn        | N/A         | Pas de risque directionnel   |
| Liquidation Momentum   | Margin      | -5.0%       | Event, levier 3x, max 24h   |
| Weekend Gap            | Spot        | -5.0%       | Spot, -3% à -8% entry       |
| PORTFOLIO CRYPTO       | Global      | -5.0% daily | Plus large que IBKR (crypto) |

NOTE : À calibrer par Monte Carlo après 100+ trades live par stratégie.
Les seuils actuels sont des estimations basées sur les backtests.
```

---

### □ FIX-005 — Corriger la checklist passage live (IBKR only, pas Alpaca)
```yaml
priorité: P0
temps: 15min
section: 10 — "Conditions passage live (checklist 17 points)"
```

**Problème** : "Alpaca paper 60j+ profitable" — on est IBKR only en phase 1.

**Fix** : Remplacer la checklist complète :

```
Conditions passage live IBKR (checklist 14 points) :

Broker & Connectivity :
  [x] IBKR paper FX testé (positions ouvertes + fermées + réconciliées)
  [x] IBKR paper EU testé (EU Gap Open exécuté en paper)
  [ ] IBKR futures paper testé (MCL + MES, 5+ trades)
  [ ] VPS Hetzner opérationnel + IB Gateway connecté

Strategy Validation :
  [x] Walk-forward validé sur TOUTES les stratégies live
  [ ] Kill switch testé avec seuils calibrés (DRILL-003)
  [x] Circuit breaker testé (losses-only fix V7.1)
  [x] Bracket orders FX testés (STP LMT + OCA)

Risk Management :
  [x] Risk manager V7.1 audité (12 checks, 27 bugs corrigés)
  [x] Stress tests passes (4 scenarios)
  [ ] Backup restore testé (DRILL-002)

Infrastructure :
  [ ] Worker Hetzner stable 48h+ (healthcheck OK)
  [ ] Telegram alerts fonctionnels (3 niveaux testés)
  [x] Réconciliation 5min opérationnelle

Operational :
  [x] Runbook opérationnel à jour
  [ ] Capital $10K transféré sur IBKR
```

---

### □ FIX-006 — Ajouter tableau allocation soft launch crypto (semaine 1 vs steady-state)
```yaml
priorité: P0
temps: 20min
section: 2.10 — Crypto Binance France
```

**Problème** : Le soft launch dit "PAS de margin" mais le tableau d'allocation
montre 55% en margin. Il faut un tableau spécifique par phase.

**Fix** : Ajouter après le tableau d'allocation par régime :

```
Allocation par phase de déploiement crypto :

SEMAINE 1 — Soft launch ($10K, spot + earn UNIQUEMENT, PAS de margin) :
| Stratégie              | Mode | Alloc  | Capital |
|------------------------|------|:------:|:-------:|
| BTC Mean Reversion     | Spot | 25%    | $2,500  |
| BTC Dominance V2       | Spot | 15%    | $1,500  |
| Weekend Gap            | Spot | 10%    | $1,000  |
| Borrow Rate Carry      | Earn | 25%    | $2,500  |
| Cash USDT              | —    | 25%    | $2,500  |
| TOTAL                  |      | 100%   | $10,000 |
→ 0% margin, 0% short, 0% levier. Validation technique pure.

SEMAINE 2 ($12.5K, ajout margin 1.5x max) :
| Stratégie              | Mode   | Alloc  | Capital  |
|------------------------|--------|:------:|:--------:|
| BTC/ETH Dual Momentum  | Margin | 15%    | $1,875   |
| Altcoin Relative Str   | Margin | 10%    | $1,250   |
| BTC Mean Reversion     | Spot   | 15%    | $1,875   |
| BTC Dominance V2       | Spot   | 10%    | $1,250   |
| Weekend Gap            | Spot   | 10%    | $1,250   |
| Borrow Rate Carry      | Earn   | 20%    | $2,500   |
| Cash USDT              | —      | 20%    | $2,500   |
| TOTAL                  |        | 100%   | $12,500  |

SEMAINE 3+ ($15K, steady-state, toutes stratégies WF-validées) :
→ Tableau d'allocation par régime (BULL/BEAR/CHOP) = le tableau existant
```

---

### □ FIX-007 — Ajouter backtests attendus pour les 8 stratégies crypto
```yaml
priorité: P0
temps: 30min
section: 2.10 — Crypto Binance France
```

**Problème** : Pas de Sharpe cible, trades estimés, ni critères WF pour les crypto.

**Fix** : Ajouter après le tableau des 8 stratégies :

```
Backtests attendus — Stratégies crypto :

| # | Stratégie              | Période BT    | Trades/an | Sharpe cible | Max DD | WR cible | WF         |
|---|------------------------|---------------|:---------:|:------------:|:------:|:--------:|:----------:|
| 1 | BTC/ETH Dual Momentum  | 2023-2026 (3a)| 50-80     | 1.5-2.5      | < 18%  | 38-45%   | 4 fenêtres |
| 2 | Altcoin Relative Str   | 2024-2026 (2a)| ~312      | 1.0-2.0      | < 25%  | 50-55%   | 4 fenêtres |
| 3 | BTC Mean Reversion     | 2023-2026 (3a)| 150-250   | 1.0-1.8      | < 12%  | 55-65%   | 4 fenêtres |
| 4 | Vol Breakout           | 2023-2026 (3a)| 30-50     | 1.2-2.0      | < 20%  | 40-50%   | 4 fenêtres |
| 5 | BTC Dominance V2       | 2023-2026 (3a)| 50-100    | 0.8-1.5      | < 15%  | 50-55%   | 4 fenêtres |
| 6 | Borrow Rate Carry      | 2023-2026 (3a)| N/A       | N/A          | ~0%    | N/A      | N/A        |
| 7 | Liquidation Momentum   | 2024-2026 (2a)| 36-60     | 1.0-2.5      | < 15%  | 45-55%   | Bootstrap  |
| 8 | Weekend Gap            | 2023-2026 (3a)| 25-40     | 0.5-1.5      | < 10%  | 55-65%   | Bootstrap  |

NOTES :
- Strat 6 (Carry) : pas de backtest directionnel, juste simulation de rendement Earn
- Strat 7 et 8 : < 50 trades/an → Bootstrap 1000 samples au lieu de WF classique
- Le walk-forward crypto utilise train 6m / test 2m (tier 1) ou train 4m / test 1.5m (tier 2)
- TOUS les backtests incluent : intérêts emprunt horaires, commissions 0.10% RT, slippage tier-based
- Si une stratégie ne passe pas le WF → archivée, pas de live
- Minimum 4/8 stratégies doivent passer pour lancer le portefeuille crypto
```

---

### □ FIX-008 — Ajouter la fiscalité crypto FR (formulaire 2086)
```yaml
priorité: P1
temps: 30min
section: nouvelle sous-section dans section 7 ou 9
```

**Problème** : Le tax report PFU V6 ne gère que equities/FX/futures.
La crypto a des spécificités fiscales FR.

**Fix** : Ajouter dans la section Infrastructure :

```
Tax Report Crypto FR (PFU 30% + formulaire 2086) :

RÈGLES FISCALES CRYPTO FRANCE (2026) :
- Flat tax 30% (PFU) sur les plus-values de cession vers EUR/fiat
- Les échanges crypto-crypto ne sont PAS imposables
  → Swap BTC → ETH = pas d'impôt
  → Vente BTC → EUR = imposable
  → Vente BTC → USDT = zone grise (stablecoin = crypto, pas fiat)
    → Position AMF 2024 : USDT→EUR = fait générateur, BTC→USDT = non imposable
- Formulaire 2086 : déclaration des plus-values crypto
- Formulaire 3916-bis : déclaration des comptes crypto à l'étranger (Binance = étranger)
- Méthode de calcul : prix moyen pondéré d'acquisition (PMP)

IMPACT POUR LE TRADING :
- Les trades margin (emprunt + vente + rachat) sont des cessions → imposables si vers fiat
- Les intérêts Earn reçus en crypto = pas imposables tant que non convertis en EUR
- Les liquidations margin = cessions forcées → imposables
- Le cost basis doit tracker le PMP de chaque crypto détenue

IMPLÉMENTATION :
- Adapter le module core/tax_report_pfu.py pour les crypto
- Tracker chaque cession vers fiat/stablecoin
- Calculer le PMP par actif
- Générer le formulaire 2086 + 3916-bis
- Exclure les échanges crypto-crypto du calcul fiscal

FICHIERS :
- core/crypto/tax_report_crypto.py (nouveau)
- tests/test_crypto_tax.py (nouveau, 10+ tests)

PRIORITÉ : P1 — pas bloquant pour le live, nécessaire avant la déclaration 2027
```

---

### □ FIX-009 — Estimer le budget annuel d'intérêts margin crypto
```yaml
priorité: P0
temps: 20min
section: 2.10 — Crypto Binance France
```

**Problème** : 55% du portefeuille crypto utilise le margin mais le coût
total des intérêts d'emprunt n'est pas estimé.

**Fix** : Ajouter après la section risk management crypto :

```
Budget annuel intérêts margin (estimation) :

| Stratégie              | Capital margin | Durée moy short | Borrow rate/j | Coût/an estimé |
|------------------------|:--------------:|:----------------:|:-------------:|:--------------:|
| BTC/ETH Dual Momentum  | $3,000         | 12j/trade        | 0.03%         | ~$130          |
| Altcoin Relative Str   | $2,250         | 7j/trade (hebdo) | 0.07%         | ~$410          |
| Vol Breakout           | $1,500         | 8j/trade         | 0.03%         | ~$55           |
| Liquidation Momentum   | $1,500         | 1j/trade         | 0.03%         | ~$16           |
| TOTAL ESTIMÉ           |                |                  |               | **~$610/an**   |

→ $610 / $15,000 = ~4.1% du capital crypto en intérêts d'emprunt
→ Le portefeuille crypto doit faire > 4.1% net pour être rentable après intérêts
→ Les backtests DOIVENT inclure ces coûts (BT-001 V2 le fait)

ATTENTION : Les borrow rates altcoin sont très variables.
En période de forte demande (bull market), les taux peuvent monter à 0.2%/jour
sur certains altcoins. Le risk manager V2 ferme automatiquement les shorts
si le coût mensuel dépasse 2% du capital (check #8).

OPTIMISATION :
- Privilégier les shorts BTC/ETH (borrow rate 3-5x moins cher que les altcoins)
- Limiter la durée des shorts altcoin à 7 jours max (rebalancement hebdo)
- Utiliser des ordres limit (maker) pour réduire les commissions
- Surveiller les borrow rates en temps réel et adapter le sizing
```

---

### □ FIX-010 — Ajouter le check corrélation cross-portefeuille IBKR-Binance
```yaml
priorité: P0
temps: 20min
sections: 4 (Risk Management) + 2.10 (Crypto Risk)
```

**Problème** : Les deux portefeuilles sont "indépendants" mais BTC corrèle
avec le S&P500 (~0.3-0.6). Un crash simultané impacte les deux.
L'alerte "exposition combinée > 150%" est mentionnée une fois mais
n'est dans aucune checklist de risk.

**Fix** : Ajouter dans la section Risk Management V4 :

```
Guard #15 — Corrélation cross-portefeuille (IBKR + Binance) :

RÈGLE :
  Toutes les 4h, calculer l'exposition directionnelle combinée :
  
  ibkr_net_long_pct = (positions_long - positions_short) / capital_ibkr
  crypto_net_long_pct = (positions_long - positions_short) / capital_crypto
  combined_net_long_usd = ibkr_net_long_pct * capital_ibkr + crypto_net_long_pct * capital_crypto
  combined_net_pct = combined_net_long_usd / (capital_ibkr + capital_crypto)
  
  SI combined_net_pct > 120% → WARNING
    "Les deux portefeuilles sont fortement net long. Risque de corrélation en crash."
  
  SI combined_net_pct > 150% → ALERTE CRITIQUE
    "Exposition combinée > 150%. Réduire l'un des deux portefeuilles."
    Action : réduire les positions les plus corrélées à BTC/SPY

  CORRÉLATION BTC-SPY (historique) :
  - Bull normal : ~0.3 (faible)
  - Correction : ~0.5
  - Crash (mars 2020, nov 2022) : ~0.8+ (tout corrèle en panique)
  
  EN PRATIQUE : si IBKR est 80% net long equities ET crypto est 60% net long BTC,
  en cas de crash simultané type mars 2020 :
  - IBKR perd ~15-20% ($1,500-2,000)
  - Crypto perd ~25-35% ($3,750-5,250)
  - Perte combinée : ~$5,250-7,250 = 21-29% du capital total
  → Le guard #15 doit alerter AVANT que cette situation se produise

IMPLÉMENTATION :
  - Ajouter dans le worker principal (pas dans un worker séparé)
  - Log le combined_net_pct dans le dashboard
  - Alerte Telegram si > 120%

FICHIER :
  - core/cross_portfolio_guard.py (nouveau, ~50 lignes)
  - config/cross_portfolio_limits.yaml (nouveau)
```

Et ajouter le check #13 dans la section Risk Management Crypto V2 :
```
13. Corrélation cross-portefeuille < 120% net long combiné
```

---

## CHECKLIST V7.6

```
□ FIX-001  Nettoyer volume live IBKR (supprimer borderline)     (15min)
□ FIX-002  Smart Router V3 → crypto_margin (pas crypto_perp)     (10min)
□ FIX-003  Harmoniser Sharpe KPI (M1=0.3, M2+=1.0)              (15min)
□ FIX-004  Kill switch MC → stratégies LIVE (pas archivées)      (30min)
□ FIX-005  Checklist passage live → IBKR only (pas Alpaca)       (15min)
□ FIX-006  Tableau allocation soft launch crypto par phase        (20min)
□ FIX-007  Backtests attendus pour les 8 strats crypto            (30min)
□ FIX-008  Fiscalité crypto FR (2086 + 3916-bis)                  (30min)
□ FIX-009  Budget annuel intérêts margin estimé                   (20min)
□ FIX-010  Guard corrélation cross-portefeuille IBKR-Binance      (20min)

TOTAL : 10 fixes | ~3.5h | 0 impact code existant (specs/docs/configs)
```

---

*TODO V7.6 — Nettoyage incohérences — 27 mars 2026*
*10 corrections chirurgicales | ~3.5h | Synthèse propre et cohérente*
