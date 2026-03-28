# TODO XXL CRYPTO V2 — BINANCE FRANCE (MARGIN + SPOT)
## Portefeuille Crypto $15-20K | Binance Margin + Spot | Pas de Futures Perp
### Date : 27 mars 2026 | Expert Quant Analysis | Stratégies adaptées réglementation FR
### "Contrainte = avantage : moins de levier = moins de liquidations = meilleure survie"

---

## AVIS EXPERT QUANT — CADRAGE STRATÉGIQUE

```
CONTEXTE RÉGLEMENTAIRE :
Binance France = spot + margin (3-10x). Pas de futures perp.
Ça élimine le funding rate arb (l'edge structurel le plus stable en crypto).
Ça force un levier réduit (3-10x vs 125x en perp).

PARADOXE : c'est un AVANTAGE.
- 90% des traders perp se font liquider (Chainalysis 2025 : 87% des comptes perp perdent)
- Le levier réduit du margin FORCE une discipline de sizing
- Les intérêts d'emprunt margin (~0.02-0.05%/jour) sont PRÉVISIBLES vs funding rate erratique
- Le margin spot = tu détiens le sous-jacent → pas de risque de base (perp vs spot)

APPROCHE QUANT POUR CE SETUP :
1. Exploiter les edges STRUCTURELS crypto qui ne dépendent pas des perp
2. Utiliser le margin pour shorter (pas pour leverager 10x)
3. Combiner spot, margin, et Binance Earn (staking/lending) pour 3 sources de rendement
4. Cibler un Sharpe de 1.5-2.5 avec un DD max de 20% (réaliste crypto)

CAPITAL $15-20K :
- $15K en phase 1 (conservateur)
- $20K après gate M1 crypto (si tout va bien)
- Répartition : 60% spot/margin actif, 20% Binance Earn (rendement passif), 20% cash/stablecoin

LES 7 EDGES EXPLOITABLES SUR BINANCE FRANCE :
1. MOMENTUM CRYPTO : les tendances crypto sont les plus fortes cross-asset (persistance 1-6 mois)
2. MEAN REVERSION INTRA : BTC a un mean reversion puissant sur 1-4h (microstructure 24/7)
3. CARRY VIA LENDING : prêter ses crypto sur Binance Earn = rendement sans risque directionnel
4. CROSS-SECTIONNEL ALTCOIN : les altcoins sur/sous-performent BTC de façon persistante
5. VOLATILITÉ CYCLIQUE : compression → expansion est le pattern le plus fiable en crypto
6. DOMINANCE ROTATION : BTC dominance est cyclique et tradeable (spot only)
7. CALENDAR EFFECTS : lundi volatil, settlement 00/08/16 UTC, maintenance mardi AM
```

---

## ARCHITECTURE — BINANCE MARGIN + SPOT + EARN

---

### □ ARCH-001 — Setup complet Binance (Spot + Margin + Earn API)
```yaml
priorité: P0-BLOQUANT
temps: 5h
dépendances: aucune
validation: INFRA EXPERT
```

**Activation des services** :
```
ÉTAPE 1 — Compte Binance existant (KYC déjà fait) :
  □ Vérifier que le KYC est niveau "Verified Plus" (requis pour margin)
  □ Si non → compléter la vérification (pièce d'identité + selfie)

ÉTAPE 2 — Activer le Margin Trading :
  □ Aller dans [Trade] → [Margin]
  □ Passer le quiz margin obligatoire (Binance l'exige depuis 2023)
  □ Activer Cross Margin ET Isolated Margin
  □ Choisir ISOLATED par défaut (chaque position isolée = pas de contamination)
  
ÉTAPE 3 — API Keys :
  □ [Account] → [API Management] → Create API
  □ Permissions : Enable Reading ✓, Enable Spot & Margin Trading ✓
  □ PAS de Enable Futures (bloqué de toute façon)
  □ PAS de Enable Withdrawals (sécurité)
  □ IP Whitelist : ajouter UNIQUEMENT l'IP du serveur Hetzner
  □ Nommer la clé : "trading-bot-hetzner"

ÉTAPE 4 — Binance Earn :
  □ Activer Binance Simple Earn (flexible + locked)
  □ Vérifier les taux actuels : USDT (~3-8% APY), BTC (~1-3% APY), ETH (~2-5% APY)
  □ L'API Earn est accessible via /sapi/v1/simple-earn/
```

**Module broker Binance V2 (margin-aware)** :
```python
# core/broker/binance_broker.py

class BinanceBroker:
    """
    Broker adapter Binance pour la France.
    Supporte : Spot, Margin (Cross + Isolated), Earn.
    PAS de Futures Perp (bloqué réglementairement).
    """
    
    MODES = {
        "SPOT": "spot",           # Achat/vente simple
        "MARGIN_ISOLATED": "margin",  # Margin isolée (défaut)
        "MARGIN_CROSS": "margin",     # Margin croisée (usage limité)
        "EARN_FLEXIBLE": "earn",      # Lending flexible (retrait instantané)
        "EARN_LOCKED": "earn",        # Lending bloqué (meilleur rendement)
    }
    
    # Spécificités margin
    def margin_borrow(self, asset, amount, is_isolated=True, symbol=None):
        """
        Emprunter un actif pour le margin trading.
        is_isolated=True → l'emprunt est lié à UNE paire (ex: BTCUSDT)
        Binance facture des intérêts horaires (~0.02-0.05%/jour selon l'actif).
        """
        if is_isolated:
            return self.client.create_margin_loan(
                asset=asset, amount=amount, 
                isIsolated=True, symbol=symbol
            )
        return self.client.create_margin_loan(asset=asset, amount=amount)
    
    def margin_repay(self, asset, amount, is_isolated=True, symbol=None):
        """Rembourser l'emprunt + intérêts."""
        ...
    
    def margin_short(self, symbol, quantity, stop_loss_pct=None):
        """
        Shorter via margin :
        1. Emprunter l'actif (ex: emprunter 0.1 BTC)
        2. Vendre l'actif emprunté sur le marché spot
        3. Quand le prix baisse, racheter et rembourser
        
        COÛT : intérêts d'emprunt (~0.02%/jour pour BTC, ~0.05%/jour pour altcoins)
        Beaucoup plus prévisible que le funding rate des perp.
        """
        # 1. Emprunter
        self.margin_borrow(asset=symbol.replace("USDT",""), amount=quantity, 
                          is_isolated=True, symbol=symbol)
        # 2. Vendre
        order = self.client.create_margin_order(
            symbol=symbol, side="SELL", type="MARKET", quantity=quantity,
            isIsolated=True
        )
        # 3. Placer le stop loss (rachat)
        if stop_loss_pct:
            self.place_margin_stop(symbol, quantity, stop_loss_pct)
        return order
    
    def get_borrow_rate(self, asset):
        """
        Taux d'emprunt horaire actuel.
        Critique pour le calcul de rentabilité des shorts.
        BTC : ~0.0008%/h = ~0.02%/jour = ~7%/an
        ETH : ~0.001%/h = ~0.024%/jour = ~8.7%/an
        Altcoins : 0.002-0.01%/h = très variable
        """
        rates = self.client.get_margin_interest_rate_history(asset=asset)
        return rates[-1]['dailyInterestRate']
    
    # Binance Earn
    def subscribe_earn(self, asset, amount, product_type="FLEXIBLE"):
        """
        Placer des fonds dans Binance Earn.
        FLEXIBLE : retrait instantané, rendement ~3-8% sur USDT
        LOCKED : bloqué 30-120 jours, rendement +2-3% vs flexible
        """
        ...
    
    def redeem_earn(self, asset, amount, product_type="FLEXIBLE"):
        """Retirer des fonds de Binance Earn."""
        ...
    
    def get_earn_positions(self):
        """Positions actuelles dans Earn (pour le calcul de capital total)."""
        ...
```

**Rate limiting Binance** :
```python
class BinanceRateLimiter:
    """
    Binance rate limits (mars 2026) :
    - REST API : 1200 requêtes/minute (poids total)
    - Orders : 10 ordres/seconde, 200K ordres/jour
    - WebSocket : 5 messages/seconde
    
    Chaque endpoint a un "poids" (1-50). Les ordres pèsent 1.
    GET /api/v3/klines pèse 2. GET /api/v3/depth pèse 5-50 selon la profondeur.
    
    STRATÉGIE : prioriser les ordres, throttle les requêtes data.
    """
    def __init__(self):
        self.weight_used = 0
        self.weight_limit = 1200
        self.window_start = time.time()
        self.order_count_second = 0
    
    def can_request(self, weight=1):
        self._reset_if_new_window()
        return self.weight_used + weight <= self.weight_limit * 0.8  # 80% safety margin
    
    def wait_if_needed(self, weight=1):
        while not self.can_request(weight):
            time.sleep(0.1)
        self.weight_used += weight
```

**Fichiers** :
- `core/broker/binance_broker.py` (nouveau — V2 margin-aware)
- `core/broker/binance_ws.py` (nouveau — WebSocket)
- `core/broker/binance_rate_limiter.py` (nouveau)
- `core/broker/binance_earn.py` (nouveau — interface Earn)
- `core/broker/factory.py` (modifier — ajouter BINANCE)
- `config/binance_config.yaml` (nouveau)
- `tests/test_binance_broker.py` (nouveau, 20+ tests)

---

### □ ARCH-002 — Sécurité et isolation capital
```yaml
priorité: P0-BLOQUANT
temps: 2h
dépendances: ARCH-001
validation: RISK EXPERT
```

**Architecture des wallets** :
```
Capital total crypto : $15,000

RÉPARTITION INITIALE :
┌─────────────────────┬──────────┬────────────────────────────────────┐
│ Wallet              │ Montant  │ Usage                              │
├─────────────────────┼──────────┼────────────────────────────────────┤
│ Spot Wallet         │ $6,000   │ Stratégies spot (trend, momentum)  │
│ Margin Wallet       │ $4,000   │ Collateral pour shorts + leveraged │
│ Earn (Flexible)     │ $3,000   │ USDT lending (~5-8% APY)           │
│ Cash (USDT Spot)    │ $2,000   │ Réserve + opportunités             │
├─────────────────────┼──────────┼────────────────────────────────────┤
│ TOTAL               │ $15,000  │                                    │
└─────────────────────┴──────────┴────────────────────────────────────┘

RÈGLES :
- Earn Flexible = retiré en < 1 min si besoin (pas vraiment bloqué)
- Margin ISOLATED uniquement (pas de cross margin en phase 1)
- Max emprunt margin : 3x le collateral ($12K de position max sur $4K de collateral)
- Transfer spot ↔ margin via API (instantané, gratuit)
- Transfer vers Earn via API (instantané pour Flexible)
```

**Fichiers** :
- `config/crypto_wallets.yaml` (nouveau)
- `core/crypto/capital_manager.py` (nouveau — gère les transfers entre wallets)


---

## DATA PIPELINE CRYPTO

---

### □ DATA-001 — Collecte données (candles, orderbook, margin rates)
```yaml
priorité: P0-BLOQUANT
temps: 6h
dépendances: ARCH-001
validation: QUANT EXPERT
```

**Sources de données spécifiques au setup margin** :

```
DONNÉES P0 (essentielles) :
┌──────────────────────┬──────────────┬──────────────┬──────────────────────────┐
│ Donnée               │ Source       │ Fréquence    │ Usage                    │
├──────────────────────┼──────────────┼──────────────┼──────────────────────────┤
│ Candles OHLCV        │ Binance REST │ 1m,5m,1h,4h,1d│ Signaux, backtests      │
│ Margin Borrow Rate   │ Binance REST │ 1h           │ Coût des shorts, sizing  │
│ Margin Available     │ Binance REST │ 1h           │ Liquidité d'emprunt      │
│ Order Book top 20    │ Binance WS   │ Temps réel   │ Spread, impact, entries  │
│ Volume 24h           │ Binance REST │ 1h           │ Filtre liquidité         │
│ Earn APY rates       │ Binance REST │ 4h           │ Allocation Earn vs trade │
│ BTC Dominance        │ CoinGecko    │ 1h           │ Regime, rotation         │
│ Ticker 24h stats     │ Binance REST │ 5min         │ Momentum, volatilité     │
├──────────────────────┼──────────────┼──────────────┼──────────────────────────┤
│ DONNÉES P1 (utiles) :                                                         │
├──────────────────────┼──────────────┼──────────────┼──────────────────────────┤
│ Margin Liquidations  │ Binance REST │ Event        │ Cascade detection        │
│ Long/Short Ratio     │ CoinGlass    │ 1h           │ Sentiment               │
│ Aggregate Funding    │ CoinGlass    │ 8h           │ Proxy funding (indirect) │
│ Fear & Greed Index   │ Alternative  │ 1j           │ Regime confirmation      │
│ Stablecoin Market Cap│ CoinGecko    │ 1j           │ Macro flow              │
└──────────────────────┴──────────────┴──────────────┴──────────────────────────┘

NOTE CLÉ : Même sans futures perp, on peut LIRE les données de funding rate
et d'Open Interest via l'API Binance Futures (lecture seule, pas de trading).
Ça permet d'utiliser ces données comme SIGNAUX sans trader les perp.
→ GET /fapi/v1/fundingRate = accessible en lecture même depuis la France
→ GET /fapi/v1/openInterest = idem
C'est un edge informationnel : on voit ce que font les traders perp sans prendre le risque.
```

**Univers crypto** :
```yaml
# config/crypto_universe.yaml
universe:
  tier_1:  # Toujours tradés, liquidité maximale
    - BTCUSDT    # $30B+ volume/jour
    - ETHUSDT    # $15B+ volume/jour
  
  tier_2:  # Top 5-10 market cap, margin disponible
    - SOLUSDT    # $2B+ volume
    - BNBUSDT    # $1B+ volume
    - XRPUSDT   # $1B+ volume
    - DOGEUSDT  # $1B+ volume (momentum plays)
  
  tier_3:  # Top 20, filtrés dynamiquement par volume
    - AVAXUSDT
    - LINKUSDT
    - ADAUSDT
    - DOTUSDT
    - NEARUSDT
    - SUIUSDT
    - ARBUSDT
    - OPUSDT
  
  filters:
    min_24h_volume_usd: 50_000_000    # $50M minimum
    min_market_cap_usd: 1_000_000_000  # $1B minimum
    margin_available: true              # Doit être disponible en margin
    max_borrow_rate_daily: 0.1          # Max 0.1%/jour d'intérêt emprunt
```

**Fichiers** :
- `core/crypto/data_pipeline.py` (nouveau)
- `core/crypto/binance_data.py` (nouveau — wrapper collecte)
- `core/crypto/coinglass_data.py` (nouveau — funding rate + OI en lecture)
- `config/crypto_universe.yaml` (nouveau)
- `scripts/collect_crypto_history.py` (nouveau)
- `tests/test_crypto_data.py` (nouveau, 15+ tests)

---

## STRATÉGIES CRYPTO — 8 STRATÉGIES (EXPERT QUANT)

---

### Vue d'ensemble — Portefeuille optimisé Binance France

```
L'expert quant a conçu 8 stratégies exploitant TOUTES les possibilités
Binance France : spot, margin long, margin short, et Earn.

┌───┬──────────────────────────────┬─────────┬────────┬───────┬──────┬──────────────┐
│ # │ Stratégie                    │ Type    │ Mode   │ Alloc │ Freq │ Edge         │
├───┼──────────────────────────────┼─────────┼────────┼───────┼──────┼──────────────┤
│ 1 │ BTC/ETH Dual Momentum        │ Trend   │ Margin │ 20%   │ 4h   │ Persistance  │
│ 2 │ Altcoin Relative Strength    │ X-Sec   │ Margin │ 15%   │ Hebdo│ Cross-section│
│ 3 │ BTC Mean Reversion Intra     │ MR      │ Spot   │ 12%   │ 1h   │ Microstructure│
│ 4 │ Volatility Breakout          │ Vol     │ Margin │ 10%   │ 4h   │ Vol clustering│
│ 5 │ BTC Dominance Rotation       │ Macro   │ Spot   │ 10%   │ Hebdo│ Cyclique     │
│ 6 │ Borrow Rate Carry            │ Carry   │ Earn   │ 13%   │ 4h   │ Structurel   │
│ 7 │ Liquidation Momentum         │ Event   │ Margin │ 10%   │ 15m  │ Flow forcé   │
│ 8 │ Weekend Gap Exploitation     │ Calendar│ Spot   │ 10%   │ Hebdo│ Saisonnalité │
├───┼──────────────────────────────┼─────────┼────────┼───────┼──────┼──────────────┤
│   │ TOTAL                        │         │        │ 100%  │      │              │
└───┴──────────────────────────────┴─────────┴────────┴───────┴──────┴──────────────┘

Diversification par mode :
  Margin (long/short avec levier) : strats 1, 2, 4, 7 = 55%
  Spot (long only)                : strats 3, 5, 8 = 32%
  Earn (rendement passif)         : strat 6 = 13%

Diversification par fréquence :
  Intra-journalier (1h-4h)  : strats 1, 3, 4, 6 = 55%
  Hebdomadaire              : strats 2, 5, 8 = 35%
  Event-driven              : strat 7 = 10%

Corrélation cible inter-stratégies : < 0.3 (vérifier en backtest)
```

---

### □ STRAT-001 — BTC/ETH Dual Momentum (Margin Long/Short)
```yaml
priorité: P0
temps: 8h
dépendances: DATA-001
validation: QUANT EXPERT
allocation: 20% ($3,000)
mode: Margin Isolated, 2x levier max
```

**Logique détaillée** :
```
CONCEPT : Suivre les tendances BTC et ETH séparément, avec la possibilité
de shorter via margin. Le "dual" = on peut être long BTC et short ETH
simultanément si les tendances divergent (rare mais très profitable).

SIGNAL LONG :
  conditions_toutes_requises:
    - close > EMA_50(4h)
    - EMA_20(4h) > EMA_50(4h)               # Golden cross court terme
    - ADX(14, 4h) > 25                       # Trend confirmée
    - RSI(14, 4h) entre 45 et 75             # Momentum sans surchauffe
    - volume_24h > SMA(volume_24h, 7j) * 1.2 # Volume au-dessus de la moyenne
    - borrow_rate < 0.05%/jour               # Coût du margin acceptable
  
  filtre_macro:
    - BTC_dominance pas en chute libre (EMA7 > EMA21 ou stable)
    - Fear_Greed_Index > 25 (pas en panic extrême pour les longs)

SIGNAL SHORT (via margin borrow + sell) :
  conditions_toutes_requises:
    - close < EMA_50(4h)
    - EMA_20(4h) < EMA_50(4h)               # Death cross court terme
    - ADX(14, 4h) > 25                       # Trend confirmée
    - RSI(14, 4h) entre 25 et 55             # Momentum baissier sans survente
    - volume_24h > SMA(volume_24h, 7j) * 1.2
    - borrow_rate < 0.08%/jour               # Short coûte plus cher
  
  filtre_macro:
    - Fear_Greed_Index < 60 (pas en euphorie extrême pour les shorts)

EXÉCUTION MARGIN SHORT :
  1. Transférer collateral USDT vers Isolated Margin wallet (paire BTCUSDT)
  2. Emprunter BTC (quantité calculée par le sizer)
  3. Vendre BTC emprunté sur le marché (= ouverture du short)
  4. Placer un buy stop loss (= rachat BTC si le prix monte)
  5. Quand signal de sortie → racheter BTC + rembourser emprunt + intérêts

COÛT DU SHORT :
  - Intérêts emprunt BTC : ~0.02%/jour = ~0.6%/mois = ~7%/an
  - Commission spot : 0.1% aller + 0.1% retour = 0.2% RT
  - Pour un trade de 10 jours : 0.2% (commission) + 0.2% (intérêts) = 0.4% total
  - Le trade doit faire > 0.4% pour être profitable
  → Les trades trend BTC font typiquement 3-10% → largement au-dessus

GESTION DU RISQUE :
  - Levier : 2x max (= emprunter max 1x son collateral)
  - Stop loss : 2.5x ATR(14, 4h) → typiquement -3% à -5% sur BTC
  - Take profit : trailing stop 2x ATR (laisse courir les gagnants)
  - Max holding : 21 jours (force la rotation, limite les intérêts)
  - Si borrow rate spike > 0.1%/jour → fermer les shorts

SIZING :
  - Quart-Kelly basé sur le Sharpe backtest
  - Cap : 15% du capital total par position ($2,250 max)
  - Si long BTC + short ETH simultanément : chaque position à 10% max

EDGE :
  - Persistance des tendances crypto (fat tails positifs)
  - Capacité de shorter = capturer les bear markets (la plupart des spot-only perdent)
  - Le coût du short (intérêts) est fixe et prévisible (vs funding rate erratique)
  - Le filtre ADX > 25 élimine 60% des faux signaux en range

POURQUOI C'EST MIEUX QUE LA V1 (BTC/ETH Trend perp) :
  - Pas de risque de liquidation cascade (margin isolated + levier 2x max)
  - Coût prévisible (intérêts vs funding)
  - On détient le sous-jacent (pas un dérivé)
  - Les stops sont des ordres spot, pas des stop-market perp (moins de slippage)
```

**Backtest attendu** :
```
Période : jan 2023 — mars 2026 (3+ ans, couvre bull 2023 + bear 2024 + bull 2025)
Trades estimés : 50-80/an (BTC + ETH combinés)
Sharpe cible : 1.5-2.5
Max DD cible : < 18%
Win rate : 38-45% (trend = win rate bas, payoff élevé)
Profit factor : > 1.8
Ratio gains/pertes moyen : > 2.5
Coût moyen par trade (commission + intérêts) : ~0.3-0.5%
```

**Fichiers** :
- `strategies/crypto/btc_eth_dual_momentum.py` (nouveau)
- `tests/test_btc_eth_dual_momentum.py` (nouveau, 12+ tests)

---

### □ STRAT-002 — Altcoin Relative Strength (Margin Long/Short)
```yaml
priorité: P0
temps: 8h
dépendances: DATA-001
validation: QUANT EXPERT
allocation: 15% ($2,250)
mode: Margin Isolated, 2x levier max
```

**Logique détaillée** :
```
CONCEPT : Chaque semaine, classer les top 15 altcoins par performance RELATIVE
à BTC sur 14 jours. Long les 3 plus forts (vs BTC), short les 3 plus faibles.
C'est du momentum cross-sectionnel beta-ajusté.

POURQUOI BETA-AJUSTER ?
  - 80% du mouvement d'un altcoin = mouvement de BTC
  - Le 20% restant = alpha de l'altcoin (sur/sous-performance propre)
  - En tradant le résiduel (performance - beta * BTC), on isole l'alpha
  - Long/short = partiellement hedgé contre BTC

SIGNAL HEBDOMADAIRE (dimanche 00:00 UTC) :
  1. Pour chaque altcoin dans l'univers :
     rendement_14j = (close_t / close_t-14) - 1
     rendement_btc_14j = (btc_close_t / btc_close_t-14) - 1
     beta = correlation_90j(altcoin, btc) * (vol_altcoin / vol_btc)
     alpha_14j = rendement_14j - beta * rendement_btc_14j
  
  2. Classer par alpha_14j
  3. LONG top 3 (plus forte surperformance vs BTC)
  4. SHORT bottom 3 (plus forte sous-performance vs BTC)
  5. Rebalancer chaque dimanche

FILTRES :
  - Volume 24h > $50M
  - Margin borrow disponible (certains altcoins ne sont pas empruntables)
  - Borrow rate < 0.1%/jour (sinon le short coûte trop cher)
  - Market cap > $2B
  - Pas de token en unlock massif cette semaine (> 3% supply)
  - Exclure les meme coins purs (DOGE, SHIB) — trop de bruit

UNIVERS FILTRÉ :
  SOL, BNB, XRP, AVAX, LINK, ADA, DOT, NEAR, SUI, ARB, OP, APT, INJ, TIA, SEI
  (filtré dynamiquement chaque semaine)

EXÉCUTION MARGIN SHORT ALTCOIN :
  Même mécanique que STRAT-001 mais attention :
  - Les altcoins ont des borrow rates plus élevés (~0.05-0.2%/jour)
  - La liquidité d'emprunt est parfois limitée
  - Vérifier la disponibilité AVANT de placer le trade
  → Si un altcoin du bottom 3 n'est pas empruntable → passer au 4ème

SIZING :
  - 4% du capital par position (6 positions = 24% déployé, avec buffer)
  - Levier : 1.5x max pour les altcoins (plus volatils que BTC)
  - Equal-weight long, equal-weight short

STOPS :
  - Stop par position : -8% (altcoins bougent fort)
  - Stop portefeuille (toutes les positions momentum) : -5%
  - Si 2 positions sur 6 touchent le stop → fermer tout, rebalancer dimanche suivant

COÛT :
  - 6 positions * 2 trades (entrée + sortie) = 12 trades/semaine
  - Commission : 12 * 0.1% * ~$600 = ~$7.2/semaine
  - Intérêts shorts (3 positions * 7 jours * 0.05%/jour * $600) = ~$6.3/semaine
  - Coût total : ~$13.5/semaine = ~$700/an = ~4.7% du capital alloué
  - Le portefeuille doit faire > 5% de rendement annualisé pour être profitable
  → Le momentum cross-sectionnel crypto fait typiquement 30-60%/an en backtest

EDGE :
  - Momentum cross-sectionnel documenté académiquement (Jegadeesh-Titman adapté crypto)
  - Beta-adjusted = isole le vrai alpha altcoin
  - Long/short = hedgé partiellement
  - Hebdomadaire = faible turnover
  - Le coût est fixe et calculable (intérêts + commissions)
```

**Fichiers** :
- `strategies/crypto/altcoin_relative_strength.py` (nouveau)
- `tests/test_altcoin_relative_strength.py` (nouveau, 12+ tests)

---

### □ STRAT-003 — BTC Mean Reversion Intra (Spot Only)
```yaml
priorité: P0
temps: 6h
dépendances: DATA-001
validation: QUANT EXPERT
allocation: 12% ($1,800)
mode: Spot only (long only), pas de levier
```

**Logique détaillée** :
```
CONCEPT : BTC a une propriété de mean reversion forte sur les timeframes
courts (1-4h) quand il n'est PAS en tendance. Acheter les dips intra-journaliers
et vendre les rebounds. Spot only = pas de short, pas de levier, risque minimal.

C'est la stratégie "buy the dip" systématisée avec des règles strictes.

PRÉ-CONDITION (filtre de régime) :
  - ADX(14, 4h) < 20 → marché en RANGE (pas de trend)
  - Si ADX > 20 → cette stratégie ne trade PAS (laisser STRAT-001 prendre le relai)
  → Complémentarité parfaite : MR quand pas de trend, Trend quand trend

SIGNAL ENTRY (achat spot) :
  1. RSI(14, 1h) < 30                           # Survendu court terme
  2. Prix < Bollinger_Lower(20, 2σ, 1h)         # Sous la bande inférieure
  3. Volume 1h > SMA(volume_1h, 24h) * 0.8      # Pas de volume mort
  4. Spread bid-ask < 5 bps                      # Liquidité OK
  5. Prix > EMA_200(1h)                          # Pas en bear market profond
     → Ce dernier filtre évite les "falling knives"

SIGNAL EXIT (vente spot) :
  1. RSI(14, 1h) > 60                            # Retour à la normale
  2. OU prix > Bollinger_Mid(20, 1h)             # Retour au milieu de la bande
  3. OU holding > 48h                            # Max 2 jours (c'est du MR intra)
  4. Stop loss : -3% du prix d'entrée

FRÉQUENCE :
  - Évaluation toutes les heures
  - ~3-5 trades/semaine quand BTC est en range
  - ~0 trades/semaine quand BTC est en trend (filtre ADX)

SIZING :
  - Position fixe : 8% du capital ($1,200)
  - Pas de pyramiding (1 seule position MR à la fois)
  - Spot only → pas de risque de liquidation

COÛT :
  - Commission : 0.1% * 2 = 0.2% par trade
  - Pas d'intérêts (spot, pas de margin)
  - Gain moyen ciblé : 1-2% par trade → rapport coût/gain = 1:5 à 1:10

EDGE :
  - Mean reversion BTC sur 1-4h est documenté (Katsiampa 2017, mean reversion en range)
  - Le filtre ADX élimine le pire ennemi du MR : le trend
  - Spot only = sleep well, pas de liquidation, pas d'intérêts
  - Complémentaire avec STRAT-001 : quand l'un ne trade pas, l'autre si
  - Volume 24/7 crypto = les opportunities de MR sont fréquentes (pas d'heures fermées)
```

**Fichiers** :
- `strategies/crypto/btc_mean_reversion.py` (nouveau)
- `tests/test_btc_mean_reversion.py` (nouveau, 10+ tests)

---

### □ STRAT-004 — Volatility Breakout (Margin Long/Short)
```yaml
priorité: P0
temps: 6h
dépendances: DATA-001
validation: QUANT EXPERT
allocation: 10% ($1,500)
mode: Margin Isolated, 2x levier max
```

**Logique détaillée** :
```
CONCEPT : La volatilité crypto est cyclique : compression → explosion → compression.
Détecter les phases de compression et trader le breakout dans la direction confirmée.

DÉTECTION COMPRESSION :
  vol_7d = std(log_returns, 7j)
  vol_30d = std(log_returns, 30j)
  compression_ratio = vol_7d / vol_30d
  
  SI compression_ratio < 0.5 → COMPRESSION DÉTECTÉE
  (La vol récente est < 50% de la vol historique = le marché se contracte)

SIGNAL BREAKOUT :
  Quand compression détectée :
  1. Calculer le range 7j : high_7d - low_7d
  2. LONG si close > high_7d + 0.3 * ATR(14, 4h)   # Breakout haussier
  3. SHORT si close < low_7d - 0.3 * ATR(14, 4h)    # Breakout baissier
  4. Le premier breakout qui trigger = la direction
  
  CONFIRMATION (éviter les faux breakouts) :
  - Volume du breakout > 2x volume moyen 7j
  - Le breakout tient pendant 2 candles 4h consécutives
  - ADX passe au-dessus de 20 (la trend démarre)

SORTIE :
  - Trailing stop : 2x ATR(14, 4h)
  - OU vol_7d/vol_30d revient > 1.2 (l'expansion est terminée)
  - Max holding : 14 jours
  - Stop loss initial : 1.5x ATR sous l'entrée

SIZING :
  - 10% du capital ($1,500)
  - Levier 2x max (position max $3,000)
  - 1 position à la fois

EDGE :
  - Vol clustering est le phénomène le plus robuste en finance (Mandelbrot, 1963)
  - En crypto, les compressions sont plus extrêmes et les breakouts plus violents
  - Le filtre de confirmation (volume + 2 candles) élimine ~70% des faux breakouts
```

**Fichiers** :
- `strategies/crypto/vol_breakout.py` (nouveau)
- `tests/test_vol_breakout.py` (nouveau, 8+ tests)

---

### □ STRAT-005 — BTC Dominance Rotation (Spot Only)
```yaml
priorité: P1
temps: 4h
dépendances: DATA-001
validation: QUANT EXPERT
allocation: 10% ($1,500)
mode: Spot only
```

**Logique détaillée** :
```
CONCEPT : Rotation entre BTC et un panier ALT selon la tendance de dominance.
Identique à la V1 mais amélioré avec des seuils dynamiques et une dead zone.

SIGNAL :
  btc_dom = BTC market cap / total crypto market cap (CoinGecko API)
  dom_ema7 = EMA(btc_dom, 7j)
  dom_ema21 = EMA(btc_dom, 21j)
  
  SI dom_ema7 > dom_ema21 + 0.5% → BTC SEASON
    → 80% BTC spot + 20% USDT (cash)
  
  SI dom_ema7 < dom_ema21 - 0.5% → ALT SEASON
    → 30% ETH + 30% SOL + 20% top performer 30j + 20% USDT
  
  SI |dom_ema7 - dom_ema21| < 0.5% → DEAD ZONE
    → 50% BTC + 30% ETH + 20% USDT (neutre)
  
  Rebalancement : hebdomadaire (dimanche 00:00 UTC)

AMÉLIORATIONS V2 :
  - Dead zone dynamique (0.5% au lieu de 2% fixe → plus de trades)
  - Allocation ALT diversifiée (pas juste ETH)
  - Cash buffer 20% en permanence (opportunités)

SIZING : 10% du capital, spot only, rebalancement hebdo
```

**Fichiers** :
- `strategies/crypto/btc_dominance_v2.py` (nouveau)
- `tests/test_btc_dominance_v2.py` (nouveau, 6+ tests)

---

### □ STRAT-006 — Borrow Rate Carry (Earn + Margin Lending)
```yaml
priorité: P0
temps: 6h
dépendances: ARCH-001
validation: QUANT EXPERT + RISK EXPERT
allocation: 13% ($1,950)
mode: Binance Earn + Margin Lending
```

**Logique détaillée** :
```
CONCEPT : C'est la stratégie de REMPLACEMENT du funding rate arb.
Au lieu de collecter le funding rate (indisponible en France), on 
PRÊTE nos actifs via Binance Earn et on optimise le rendement.

3 SOURCES DE CARRY :
1. USDT Flexible Earn : ~5-12% APY (varie avec la demande de margin)
2. BTC Flexible Earn : ~1-3% APY
3. ETH Flexible Earn : ~2-5% APY (staking ETH inclus parfois)

STRATÉGIE DYNAMIQUE :
  Toutes les 4h, vérifier les taux APY :
  
  SI usdt_apy > 8% :
    → Allouer 80% en USDT Earn, 20% en BTC/ETH Earn
    → Les taux USDT élevés = forte demande de margin = marché spéculatif
    
  SI usdt_apy < 5% ET btc_apy > 2% :
    → Allouer 40% BTC Earn, 40% ETH Earn, 20% USDT Earn
    → Les taux bas sur USDT = marché calme, autant avoir du BTC/ETH
    
  SI usdt_apy < 3% ET btc_apy < 1% :
    → Réduire l'allocation Earn → transférer vers les stratégies actives
    → Le carry ne vaut pas le coût d'opportunité

SIGNAL DE SORTIE EARN :
  - Si une stratégie active a besoin de capital → retrait flexible instantané
  - Si les taux APY chutent sous 3% partout → réduire l'allocation Earn
  - Les positions Earn Flexible sont retirables EN TEMPS RÉEL

EDGE :
  - Rendement SANS risque directionnel (tu prêtes, tu reçois des intérêts)
  - Les taux sont corrélés positivement avec la volatilité du marché
    → Quand le marché est volatil, les taux montent (les margin traders empruntent plus)
    → C'est CONTRE-CYCLIQUE aux stratégies directionnelles : quand le trend perd, le carry gagne
  - C'est le "risk-free rate" de la crypto (comparable aux T-Bills en TradFi)

RISQUE :
  - Risque de plateforme (Binance fait faillite) → mitigé par la taille de Binance
  - Les taux peuvent baisser à ~1% en bear market profond
  - Earn Flexible = pas garanti, Binance peut changer les taux
  → Pas de Earn Locked en phase 1 (on veut la liquidité)

RENDEMENT ESTIMÉ :
  - Année bull (2023, 2025) : 8-15% APY moyen
  - Année bear (2024) : 3-6% APY moyen
  - Moyenne pondérée : ~6-10% APY sur le capital alloué
  - Sur $1,950 : ~$120-195/an (modeste mais sans risque directionnel)
```

**Fichiers** :
- `strategies/crypto/borrow_rate_carry.py` (nouveau)
- `tests/test_borrow_rate_carry.py` (nouveau, 8+ tests)

---

### □ STRAT-007 — Liquidation Momentum (Margin, Event-Driven)
```yaml
priorité: P1
temps: 6h
dépendances: DATA-001
validation: QUANT EXPERT + RISK EXPERT
allocation: 10% ($1,500)
mode: Margin Isolated, 3x levier max (trades courts)
```

**Logique détaillée** :
```
CONCEPT : Même idée que la V1 (liquidation cascade) mais adaptée au margin.
On DÉTECTE les cascades de liquidation perp via les données en lecture seule,
et on trade dans le sens de la cascade via le margin spot.

EDGE INFORMATIONNEL : on voit les liquidations perp (API lecture gratuite)
sans prendre le risque des perp. On trade sur spot/margin avec moins de levier.

DONNÉES (en lecture seule, pas de trading perp) :
  - GET /fapi/v1/openInterest → chute OI = liquidations
  - GET /fapi/v1/fundingRate → funding extrême = signal
  - CoinGlass API → volume de liquidations agrégées

SIGNAL :
  1. Chute OI > 8% en 4h sur BTC/ETH (liquidations massives)
  2. Volume spot > 3x moyenne 7j (activité de panique)
  3. Mouvement prix > 4% en 4h (le cascade est en cours)
  4. Attendre 30-60 min après le pic de liquidations (laisser le dust settle)
  5. Entrer dans le sens du mouvement (momentum post-cascade)
  
  DIRECTION :
  - Prix baisse + OI chute = liquidation de longs → SHORT (margin)
  - Prix monte + OI chute = liquidation de shorts → LONG (spot ou margin)

SORTIE :
  - Stop serré : 1.5% (c'est un trade court)
  - Take profit : 3% (ratio 2:1)
  - Max holding : 24h
  - Si OI se stabilise → sortir (la cascade est terminée)

SIZING :
  - 5% du capital par trade ($750)
  - Levier 2-3x max (trade court, stop serré)
  - Max 1 trade cascade actif
  - Max 3 trades/semaine (c'est un événement rare)

COÛT :
  - Commission : 0.2% RT
  - Intérêts (si short, max 24h) : ~0.02%
  - Total : ~0.22% par trade
  - Gain ciblé : 3% → rapport coût/gain = 1:14

EDGE :
  - Les liquidations perp créent un FLUX FORCÉ observable
  - On utilise l'information sans prendre le risque des perp
  - Le momentum post-cascade dure typiquement 4-24h
  - Low frequency (3-5/mois) = faible coût total
```

**Fichiers** :
- `strategies/crypto/liquidation_momentum.py` (nouveau)
- `tests/test_liquidation_momentum.py` (nouveau, 10+ tests)

---

### □ STRAT-008 — Weekend Gap Exploitation (Spot)
```yaml
priorité: P2
temps: 4h
dépendances: DATA-001
validation: QUANT EXPERT
allocation: 10% ($1,500)
mode: Spot only
```

**Logique détaillée** :
```
CONCEPT : Le week-end, la liquidité crypto baisse (~40% vs semaine).
Les mouvements du week-end sont souvent reversés le lundi.
Acheter les dips du week-end, vendre les rallies du lundi.

C'est un CALENDAR EFFECT spécifique à la crypto.

DONNÉES :
  - Rendement BTC vendredi 22h UTC → dimanche 22h UTC (le "week-end return")
  - Historique des reversals lundi

SIGNAL :
  SI btc_weekend_return < -3% :
    → Acheter BTC spot dimanche soir (22:00 UTC)
    → Le lundi, les traders institutionnels reviennent et stabilisent
    → Vendre quand BTC revient au prix de vendredi soir OU après 48h
  
  SI btc_weekend_return > +3% :
    → NE PAS acheter (les rallies week-end sont moins fiables)
    → Attendre un pullback lundi pour entrer
  
  SI -3% < btc_weekend_return < +3% :
    → Pas de trade (mouvement insuffisant)

SIZING :
  - 8% du capital ($1,200)
  - Spot only, pas de levier
  - 1 trade par week-end max

EDGE :
  - Effet week-end documenté en crypto (Caporale & Plastun, 2019)
  - Faible liquidité week-end = overreaction → reversion lundi
  - Spot only = risque limité
  - Hebdomadaire = très peu de trades

RISQUE :
  - Le "dip" du week-end peut être le début d'un vrai crash
  → Le filtre > -3% et < -8% protège (si > -8% → possible vrai crash, pas de trade)
  → Stop à -5% du prix d'entrée
```

**Fichiers** :
- `strategies/crypto/weekend_gap.py` (nouveau)
- `tests/test_weekend_gap.py` (nouveau, 6+ tests)


---

## BACKTEST ENGINE CRYPTO V2

---

### □ BT-001 — Moteur de backtest margin-aware
```yaml
priorité: P0-BLOQUANT
temps: 10h
dépendances: DATA-001
validation: QUANT EXPERT
```

**Le backtest crypto DOIT intégrer** :
```python
class CryptoBacktesterV2:
    """
    Backtest engine spécifique Binance France (margin + spot + earn).
    CHAQUE coût doit être modélisé sinon le P&L est fictif.
    """
    
    # 1. INTÉRÊTS D'EMPRUNT MARGIN (remplace le funding rate)
    def apply_borrow_interest(self, position, timestamp):
        """
        Les intérêts margin sont facturés TOUTES LES HEURES par Binance.
        Le taux varie selon l'offre/demande d'emprunt.
        
        DONNÉES HISTORIQUES nécessaires :
        - Binance publie les taux historiques via GET /sapi/v1/margin/interestRateHistory
        - Stocker les taux horaires pour chaque actif
        - En backtest, utiliser le taux RÉEL à chaque heure
        
        TAUX TYPIQUES (mars 2026) :
          BTC : 0.0008-0.003%/heure = 0.02-0.07%/jour = 7-25%/an
          ETH : 0.001-0.004%/heure = 0.024-0.1%/jour = 8-36%/an
          SOL : 0.002-0.01%/heure = 0.05-0.24%/jour = 18-88%/an
          USDT : 0.001-0.005%/heure = 0.024-0.12%/jour = 8-44%/an
        
        ATTENTION : les taux altcoin sont BEAUCOUP plus élevés que BTC/ETH.
        Un short altcoin pendant 30 jours peut coûter 5-10% en intérêts seuls.
        Le backtest DOIT capturer cette asymétrie.
        """
        if position.is_margin_borrow:
            hourly_rate = self.get_historical_borrow_rate(
                position.borrowed_asset, timestamp
            )
            hours_elapsed = (timestamp - position.last_interest_ts).total_seconds() / 3600
            interest = position.borrowed_amount * hourly_rate * hours_elapsed
            position.realized_pnl -= interest
            position.total_borrow_cost += interest
            position.last_interest_ts = timestamp
    
    # 2. COMMISSIONS BINANCE (spot + margin)
    COMMISSION = {
        "spot_maker": 0.001,      # 0.10%
        "spot_taker": 0.001,      # 0.10%
        "margin_maker": 0.001,    # 0.10% (identique au spot)
        "margin_taker": 0.001,    # 0.10%
        # Avec BNB discount (25% off) : 0.075% chacun
    }
    # NOTE : pas de commission maker 0.02% comme en futures
    # Le spot/margin est 5x plus cher que les futures → impact significatif
    
    # 3. SLIPPAGE RÉALISTE (adapté spot, pas order book perp)
    SLIPPAGE_MODEL = {
        "BTCUSDT": {"base_bps": 2, "impact_per_100k": 0.5},   # Spot très liquide
        "ETHUSDT": {"base_bps": 3, "impact_per_100k": 1.0},
        "tier_2":  {"base_bps": 5, "impact_per_100k": 3.0},   # SOL, BNB, XRP
        "tier_3":  {"base_bps": 8, "impact_per_100k": 8.0},   # AVAX, LINK...
    }
    # NOTE : le slippage spot est légèrement PLUS élevé que le slippage perp
    # car les order books perp sont plus profonds (plus de market makers)
    
    # 4. EARN YIELD SIMULATION
    def apply_earn_yield(self, earn_positions, timestamp):
        """
        Simuler le rendement Earn avec les taux historiques.
        Les taux Earn Flexible changent quotidiennement.
        Utiliser les données CoinGlass/DefiLlama pour l'historique.
        """
        for pos in earn_positions:
            daily_rate = self.get_historical_earn_rate(pos.asset, timestamp)
            days_elapsed = (timestamp - pos.last_yield_ts).total_seconds() / 86400
            yield_earned = pos.amount * daily_rate * days_elapsed
            pos.total_yield += yield_earned
            pos.last_yield_ts = timestamp
    
    # 5. MARGIN AVAILABILITY
    def check_borrow_availability(self, asset, amount, timestamp):
        """
        CRITIQUE : en backtest, vérifier que l'actif était EMPRUNTABLE.
        Certains altcoins ont une liquidité d'emprunt limitée.
        Si la pool de prêt est épuisée → le short est impossible.
        
        PROXY : si le borrow rate > 0.5%/jour, considérer que la liquidité
        est tendue et réduire la taille du short de 50%.
        """
        rate = self.get_historical_borrow_rate(asset, timestamp)
        if rate > 0.005:  # 0.5%/jour
            return amount * 0.5  # Réduire la taille
        return amount
    
    # 6. LIQUIDATION MARGIN
    def check_margin_liquidation(self, position, current_price):
        """
        Binance liquidation margin :
        Margin Level = Total Asset / Total Debt
        Si Margin Level < 1.1 → liquidation automatique
        
        Pour un short avec 2x levier :
        - Collateral : $1000 USDT
        - Emprunté : 0.01 BTC à $100,000 = $1000
        - Si BTC monte à $110,000 : dette = $1100, collateral = $1000
        - Margin Level = $1000 / $1100 = 0.91 → LIQUIDÉ
        
        → Le backtest doit simuler cette liquidation, pas juste le stop loss
        """
        ...
```

**Walk-forward crypto V2** :
```yaml
wf_config:
  # BTC/ETH (3+ ans d'historique)
  tier_1:
    train_months: 6
    test_months: 2
    min_windows: 4
    min_oos_is_ratio: 0.4
    min_profitable_windows: 50%
  
  # Altcoins (2 ans d'historique)
  tier_2:
    train_months: 4
    test_months: 1.5
    min_windows: 4
    min_oos_is_ratio: 0.45
    min_profitable_windows: 50%
  
  # Stratégies avec < 50 trades en backtest
  low_frequency:
    # Bootstrap au lieu de WF classique (pas assez de données)
    method: "bootstrap_1000_samples"
    min_sharpe_95pct_ci_lower: 0.3  # Le bound inférieur du CI doit être > 0.3
```

**Fichiers** :
- `core/crypto/backtest_engine_v2.py` (nouveau)
- `core/crypto/margin_simulator.py` (nouveau)
- `core/crypto/earn_simulator.py` (nouveau)
- `tests/test_crypto_backtest_v2.py` (nouveau, 20+ tests)

---

## RISK MANAGEMENT CRYPTO V2

---

### □ RISK-001 — Risk manager crypto margin-aware
```yaml
priorité: P0-BLOQUANT
temps: 8h
dépendances: ARCH-001
validation: RISK EXPERT
```

```python
class CryptoRiskManagerV2:
    """
    Risk manager adapté au margin Binance France.
    12 checks (vs 10 en V1).
    """
    
    LIMITS = {
        # Position limits
        "max_position_pct": 15,          # 15% du capital par position (plus conservateur)
        "max_strategy_pct": 25,          # 25% par stratégie
        "max_gross_long_pct": 80,        # Gross long max 80%
        "max_gross_short_pct": 40,       # Gross short max 40% (shorts plus risqués)
        "max_net_pct": 60,               # Net exposure max 60%
        
        # Leverage limits (margin)
        "max_leverage_btc_eth": 2.5,     # BTC/ETH : 2.5x max
        "max_leverage_altcoin": 1.5,     # Altcoins : 1.5x max (plus volatils)
        "max_leverage_portfolio": 1.8,   # Moyenne pondérée < 1.8x
        
        # Borrow limits
        "max_borrow_rate_daily": 0.1,    # Max 0.1%/jour d'intérêt → sinon pas de trade
        "max_total_borrow_pct": 50,      # Max 50% du capital emprunté
        "max_borrow_cost_monthly_pct": 2, # Si intérêts > 2%/mois → réduire les shorts
        
        # Drawdown (crypto-specific, plus large que equities)
        "daily_max_loss_pct": 5,
        "weekly_max_loss_pct": 10,
        "monthly_max_loss_pct": 15,
        "max_drawdown_pct": 20,          # 20% au lieu de 25% (margin = plus risqué que perp)
        
        # Margin-specific
        "min_margin_level": 1.5,         # Binance liquide à 1.1, on veut > 1.5
        "margin_warning_level": 1.8,     # Alerte si margin level < 1.8
        "min_free_collateral_pct": 30,   # 30% de collateral libre
    }
    
    def check_margin_health(self, account):
        """
        CHECK CRITIQUE : le margin level de chaque position isolée.
        Si margin level < 1.5 → alerte WARNING + réduire position 30%
        Si margin level < 1.3 → alerte CRITICAL + fermer position
        Si margin level < 1.15 → Binance va liquider → on a échoué
        """
        for position in account.margin_positions:
            ml = position.total_asset_value / position.total_debt
            if ml < 1.15:
                self.alert_critical(f"LIQUIDATION IMMINENTE {position.symbol}")
                self.emergency_close(position)
            elif ml < 1.3:
                self.alert_critical(f"Margin level {ml:.2f} < 1.3 : {position.symbol}")
                self.reduce_position(position, 0.5)  # Réduire de 50%
            elif ml < 1.5:
                self.alert_warning(f"Margin level {ml:.2f} < 1.5 : {position.symbol}")
                self.reduce_position(position, 0.3)  # Réduire de 30%
    
    def check_borrow_costs(self, portfolio):
        """
        Surveiller le coût cumulé des emprunts margin.
        Si les intérêts deviennent trop chers → fermer les shorts non rentables.
        """
        total_borrow_cost_30d = sum(p.borrow_cost_30d for p in portfolio.margin_positions)
        cost_pct = total_borrow_cost_30d / portfolio.capital
        if cost_pct > self.LIMITS["max_borrow_cost_monthly_pct"] / 100:
            worst_positions = sorted(portfolio.shorts, key=lambda p: p.borrow_cost_30d, reverse=True)
            for p in worst_positions[:2]:
                self.alert_warning(f"Short coûteux fermé : {p.symbol} ({p.borrow_cost_30d:.2f})")
                self.close_position(p)
    
    def check_earn_exposure(self, portfolio):
        """
        Les positions Earn ne sont PAS sans risque :
        - Si l'actif dans Earn est BTC/ETH → risque directionnel
        - Si c'est du USDT → risque de depeg (faible mais réel)
        Compter les positions Earn dans l'exposure totale.
        """
        ...
```

**Kill switch crypto V2** :
```yaml
crypto_kill_switch_v2:
  triggers:
    daily_loss_pct: 5.0
    hourly_loss_pct: 3.0
    max_drawdown_pct: 20.0
    api_down_minutes: 10
    margin_level_critical: 1.2     # Nouveau : margin level global < 1.2
    borrow_cost_spike: true        # Nouveau : si borrow rate spike > 3x en 1h
    
  actions:
    priority_1: "close_all_margin_shorts"   # Shorts d'abord (coûtent des intérêts)
    priority_2: "cancel_all_open_orders"
    priority_3: "close_margin_longs"
    priority_4: "redeem_all_earn"           # Rapatrier le cash Earn
    priority_5: "alert_telegram_critical"
    priority_6: "convert_all_to_usdt"       # Tout en USDT (safe)
```

**Fichiers** :
- `core/crypto/risk_manager_v2.py` (nouveau)
- `config/crypto_limits_v2.yaml` (nouveau)
- `config/crypto_kill_switch_v2.yaml` (nouveau)
- `tests/test_crypto_risk_v2.py` (nouveau, 20+ tests)

---

## ALLOCATION, PAPER, LIVE, MONITORING

---

### □ ALLOC-001 — Allocator crypto V2 (3 régimes + Earn dynamique)
```yaml
priorité: P0
temps: 4h
```

```yaml
crypto_allocation_v2:
  capital: 15000
  
  regime_allocations:
    BULL:  # BTC > EMA50 daily
      trend: 25%
      altcoin_rs: 15%
      mean_reversion: 5%     # MR ne marche pas en trend → réduit
      vol_breakout: 10%
      dominance: 10%
      carry: 10%
      liquidation: 10%
      weekend: 5%
      cash: 10%
    
    BEAR:  # BTC < EMA50 daily
      trend: 20%              # Short BTC via margin
      altcoin_rs: 15%         # Short les plus faibles
      mean_reversion: 10%
      vol_breakout: 10%
      dominance: 10%
      carry: 15%              # Plus de carry en bear (safe haven)
      liquidation: 10%
      weekend: 0%             # Pas de buy-the-dip en bear
      cash: 10%
    
    CHOP:  # Range
      trend: 5%               # Trend mort en range
      altcoin_rs: 10%
      mean_reversion: 20%    # MR brille en range
      vol_breakout: 15%       # Compression → breakout
      dominance: 5%
      carry: 20%              # Carry = rendement stable en chop
      liquidation: 5%
      weekend: 10%
      cash: 10%
  
  transition: 10_pct_per_day
```

### □ PAPER-001 + LIVE-001 — Paper 7j puis soft launch
```yaml
paper:
  durée: 7 jours
  plateforme: Binance Testnet (spot) + simulation margin locale
  KPI_pass:
    min_trades: 15
    max_dd: 12%
    zero_api_errors: true
    reconciliation_ok: true
    
live_soft_launch:
  semaine_1:
    capital: $10K (66% du total)
    stratégies: STRAT-001 + STRAT-003 + STRAT-006 (carry)
    sizing: 1/8 Kelly
    levier: 1x
  semaine_2:
    capital: $12.5K (83%)
    ajouter: STRAT-002 + STRAT-004
    sizing: 1/4 Kelly
    levier: 2x max
  semaine_3:
    capital: $15K (100%)
    toutes stratégies validées WF
    gate_M1_crypto: 20 trades, DD < 12%, Sharpe > 0.3
```

---

## ROADMAP 14 JOURS

```
J1     ARCH-001 (API + broker margin) + ARCH-002 (sécurité)           8h
J2     DATA-001 (pipeline) + DATA-002 (historique 3 ans)              10h
J3     BT-001 (backtest engine margin-aware)                           10h
J4     STRAT-001 (BTC/ETH Dual Momentum) + STRAT-003 (BTC MR)        12h
J5     STRAT-002 (Altcoin RS) + STRAT-006 (Carry)                    12h
J6     STRAT-004 (Vol Breakout) + STRAT-005 (Dominance)              8h
       Walk-forward sur toutes les stratégies
J7     STRAT-007 (Liquidation) + STRAT-008 (Weekend)                  8h
       RISK-001 + ALLOC-001 + MON-001                                 12h
J8-10  PAPER-001 (paper trading 3 jours minimum)                      12h
J11    Analyse paper + Go/No-Go                                        4h
J12    LIVE soft launch ($10K, 3 strats, 1/8 Kelly)                   4h
J13-14 Monitoring + ajout strats si clean                              8h

TOTAL : ~108h code + 7j paper/live
```

---

## CHECKLIST COMPLÈTE

```
ARCHITECTURE :
□ ARCH-001  Binance API (spot + margin + earn)                    (5h)
□ ARCH-002  Sécurité + isolation wallets                          (2h)

DATA :
□ DATA-001  Pipeline (candles, borrow rates, earn APY, OI)        (6h)
□ DATA-002  Historique 3 ans BTC/ETH, 2 ans altcoins              (4h)

BACKTEST :
□ BT-001    Engine margin-aware (intérêts, commission, slippage)  (10h)

STRATÉGIES :
□ STRAT-001 BTC/ETH Dual Momentum (margin long/short)            (8h)
□ STRAT-002 Altcoin Relative Strength (margin long/short)         (8h)
□ STRAT-003 BTC Mean Reversion Intra (spot only)                  (6h)
□ STRAT-004 Volatility Breakout (margin)                          (6h)
□ STRAT-005 BTC Dominance Rotation (spot)                         (4h)
□ STRAT-006 Borrow Rate Carry (Earn)                              (6h)
□ STRAT-007 Liquidation Momentum (margin, event)                  (6h)
□ STRAT-008 Weekend Gap (spot)                                     (4h)

RISK :
□ RISK-001  Risk manager V2 (12 checks, margin health)            (8h)

ALLOCATION :
□ ALLOC-001 Allocator 3 régimes + Earn dynamique                  (4h)

PAPER + LIVE :
□ PAPER-001 Paper trading 7 jours                                  (4h+7j)
□ LIVE-001  Soft launch progressif                                 (4h)

MONITORING :
□ MON-001   Dashboard + alertes + réconciliation margin            (4h)

TOTAL : 19 tâches | ~108h code + 7j paper
```

---

*TODO XXL CRYPTO V2 — Binance France (Margin + Spot + Earn)*
*8 stratégies | $15-20K | Pas de futures perp*
*"La contrainte réglementaire force la discipline : moins de levier, meilleure survie."*