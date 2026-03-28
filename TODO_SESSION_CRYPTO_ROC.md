# TODO SESSION CRYPTO — AMÉLIORATION ROC + MONITORING + VALIDATION
## 8 strats live Binance $20K | Poche indépendante
## Claude Code exécute pendant l'absence de Marc (~2-4h de travail)
### Date : 28 mars 2026

---

## INSTRUCTIONS AGENT

```
CONTEXTE :
- 8 stratégies crypto LIVE sur Binance avec 20K EUR
- Sizing 1/8 Kelly, levier max 1.5x
- Portefeuille : BTC 0.27 Earn (~15.5K), USDC 1978 Earn (~1.7K), EUR 3359 spot
- Pas de bot Telegram encore (préparer le code, pas activer)
- Dashboard XL opérationnel (11 pages, 43 endpoints)
- Worker tourne, cycle 15 min 24/7

OBJECTIFS SESSION :
1. Valider les strats crypto par walk-forward (données historiques)
2. Améliorer le ROC de la poche crypto
3. Renforcer le monitoring et la sécurité
4. Préparer l'infrastructure Telegram (code prêt, pas activé)

RÈGLES :
- NE PAS toucher au worker live (pas de risque de casser le live)
- Travailler sur des branches séparées ou des scripts indépendants
- Tout tester avant de proposer un merge
- La crypto est une POCHE INDÉPENDANTE (pas de corrélation sizing avec IBKR)
- Tous les 2,103 tests existants doivent passer
```

---

## 1. COLLECTE DONNÉES HISTORIQUES CRYPTO

```yaml
priorité: P0-BLOQUANT (les WF en dépendent)
temps: 2h (dont 90% d'attente API)
agent: DATA-ENG
```

### □ HIST-001 — Collecte candles historiques Binance

```python
# scripts/collect_crypto_history.py

"""
Collecte l'historique complet pour le walk-forward des 8 strats.

DONNÉES À COLLECTER :
┌──────────────┬────────────┬──────────────┬──────────────────────────┐
│ Symbole      │ Timeframes │ Période      │ Usage                    │
├──────────────┼────────────┼──────────────┼──────────────────────────┤
│ BTCUSDT      │ 1h, 4h, 1d │ jan 2023 →   │ Strats 1,3,4,5,7,8       │
│ ETHUSDT      │ 1h, 4h, 1d │ jan 2023 →   │ Strat 1 (dual momentum)  │
│ SOLUSDT      │ 4h, 1d     │ jan 2024 →   │ Strat 2 (altcoin RS)     │
│ BNBUSDT      │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ XRPUSDT      │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ DOGEUSDT     │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ AVAXUSDT     │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ LINKUSDT     │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ ADAUSDT      │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ DOTUSDT      │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ NEARUSDT     │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
│ SUIUSDT      │ 4h, 1d     │ jan 2024 →   │ Strat 2                  │
└──────────────┴────────────┴──────────────┴──────────────────────────┘

DONNÉES SUPPLÉMENTAIRES :
┌──────────────────────┬────────────┬──────────────────────────────────┐
│ Donnée               │ Période    │ Usage                            │
├──────────────────────┼────────────┼──────────────────────────────────┤
│ BTC Dominance        │ jan 2023 → │ Strat 5 (dominance rotation)     │
│ Margin Borrow Rates  │ jan 2024 → │ Strats 1,2,4,7 (coût des shorts)│
│ Funding Rates (read) │ jan 2023 → │ Strat 7 (liquidation signal)     │
│ Open Interest (read) │ jan 2024 → │ Strat 7 (liquidation signal)     │
│ Fear & Greed Index   │ jan 2023 → │ Strat 1 (filtre macro)           │
└──────────────────────┴────────────┴──────────────────────────────────┘

TECHNIQUE :
- Binance REST API : GET /api/v3/klines (max 1000 candles/requête)
- Rate limit : 1200 weight/min. Klines = 2 weight. 
  → Max 600 requêtes/min = largement suffisant
- BTC 1h sur 3 ans = ~26,280 candles = 27 requêtes
- Total estimé : ~300 requêtes = 30 secondes + pagination
- Stockage : Parquet (rapide pour les backtests)
- Nettoyage : supprimer candles volume=0, détecter wicks >10%, forward-fill gaps <5min

VALIDATION POST-COLLECTE :
  □ Pas de gap > 1 candle sur BTC/ETH 1h
  □ Toutes les candles ont high >= max(open,close) et low <= min(open,close)
  □ Volume > 0 sur > 99% des candles (sauf maintenance Binance)
  □ Dates de début/fin cohérentes avec la demande
  □ Fichiers Parquet lisibles et non corrompus
"""

import asyncio
from binance.client import Client
import pandas as pd
from pathlib import Path

SYMBOLS_CONFIG = {
    "tier_1": {
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "intervals": ["1h", "4h", "1d"],
        "start": "2023-01-01",
    },
    "tier_2": {
        "symbols": ["SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", 
                     "AVAXUSDT", "LINKUSDT", "ADAUSDT", "DOTUSDT",
                     "NEARUSDT", "SUIUSDT"],
        "intervals": ["4h", "1d"],
        "start": "2024-01-01",
    },
}

OUTPUT_DIR = Path("data/crypto/candles/")

def collect_klines(client, symbol, interval, start_str):
    """Collecte avec pagination automatique."""
    all_klines = []
    start_ms = int(pd.Timestamp(start_str).timestamp() * 1000)
    
    while True:
        klines = client.get_klines(
            symbol=symbol, interval=interval,
            startTime=start_ms, limit=1000
        )
        if not klines:
            break
        all_klines.extend(klines)
        start_ms = klines[-1][0] + 1  # Next candle
        if len(klines) < 1000:
            break
    
    df = pd.DataFrame(all_klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('open_time', inplace=True)
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
        df[col] = df[col].astype(float)
    
    return df[['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades']]

def clean_candles(df, symbol):
    """Nettoyage critique crypto."""
    original_len = len(df)
    
    # 1. Supprimer volume = 0 (maintenance exchange)
    df = df[df['volume'] > 0]
    
    # 2. Vérifier OHLC cohérence
    assert (df['high'] >= df[['open', 'close']].max(axis=1)).all(), f"{symbol}: high < max(open,close)"
    assert (df['low'] <= df[['open', 'close']].min(axis=1)).all(), f"{symbol}: low > min(open,close)"
    
    # 3. Marquer les wicks > 10% (flash crash potentiel)
    df['wick_pct'] = (df['high'] - df['low']) / df['close'] * 100
    flash_crashes = df[df['wick_pct'] > 10]
    if len(flash_crashes) > 0:
        print(f"  ⚠ {symbol}: {len(flash_crashes)} candles avec wick > 10%")
    
    # 4. Vérifier la continuité temporelle
    time_diffs = df.index.to_series().diff()
    # (on ne force pas — juste log les gaps)
    
    cleaned = original_len - len(df)
    if cleaned > 0:
        print(f"  {symbol}: {cleaned} candles supprimées ({cleaned/original_len*100:.1f}%)")
    
    return df

def main():
    client = Client()  # Pas besoin d'API key pour les données publiques
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    for tier_name, config in SYMBOLS_CONFIG.items():
        for symbol in config["symbols"]:
            for interval in config["intervals"]:
                print(f"Collecting {symbol} {interval} from {config['start']}...")
                df = collect_klines(client, symbol, interval, config["start"])
                df = clean_candles(df, symbol)
                
                output_path = OUTPUT_DIR / f"{symbol}_{interval}.parquet"
                df.to_parquet(output_path)
                print(f"  → {len(df)} candles saved to {output_path}")
    
    print("\n✅ Collection terminée")
    print(f"Fichiers dans {OUTPUT_DIR}:")
    for f in sorted(OUTPUT_DIR.glob("*.parquet")):
        df = pd.read_parquet(f)
        print(f"  {f.name}: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

if __name__ == "__main__":
    main()
```

### □ HIST-002 — Collecte borrow rates et données annexes

```python
# scripts/collect_crypto_borrow_rates.py

"""
Collecte les taux d'emprunt margin historiques.
CRITIQUE pour le backtest des strats qui shortent via margin.

Binance API : GET /sapi/v1/margin/interestRateHistory
Nécessite une API key (lecture seule suffit).
"""

def collect_borrow_rates(client, asset, days=730):
    """
    Collecte les taux d'emprunt sur N jours.
    Binance renvoie les taux par jour (pas par heure).
    Pour le backtest, on interpole linéairement entre les jours.
    """
    rates = []
    # Binance API paginée par 100 jours max
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = end_time - (days * 86400 * 1000)
    
    while start_time < end_time:
        chunk_end = min(start_time + (100 * 86400 * 1000), end_time)
        data = client.get_margin_interest_rate_history(
            asset=asset,
            startTime=start_time,
            endTime=chunk_end,
            recvWindow=60000
        )
        rates.extend(data)
        start_time = chunk_end
    
    df = pd.DataFrame(rates)
    if len(df) > 0:
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['dailyInterestRate'] = df['dailyInterestRate'].astype(float)
    
    return df

BORROW_ASSETS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", 
                  "AVAX", "LINK", "ADA", "DOT", "USDT"]

def main():
    client = Client(api_key=os.environ["BINANCE_API_KEY"],
                    api_secret=os.environ["BINANCE_API_SECRET"])
    
    output_dir = Path("data/crypto/borrow_rates/")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for asset in BORROW_ASSETS:
        print(f"Collecting borrow rates for {asset}...")
        df = collect_borrow_rates(client, asset)
        output_path = output_dir / f"borrow_rate_{asset}.parquet"
        df.to_parquet(output_path)
        print(f"  → {len(df)} records, avg rate: {df['dailyInterestRate'].mean():.6f}/day")


# scripts/collect_btc_dominance.py

"""
Collecte la BTC dominance historique via CoinGecko API (gratuit).
"""

import requests

def collect_btc_dominance(days=1095):  # 3 ans
    """CoinGecko /global/charts endpoint."""
    url = "https://api.coingecko.com/api/v3/global"
    # CoinGecko n'a pas de données historiques gratuites pour la dominance
    # Alternative : calculer depuis les market caps
    
    # Méthode : récupérer BTC market cap + total market cap
    btc_data = requests.get(
        f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": days}
    ).json()
    
    total_data = requests.get(
        "https://api.coingecko.com/api/v3/global"
    ).json()
    
    # Pour l'historique, utiliser les données de market cap
    btc_mcap = pd.DataFrame(btc_data['market_caps'], columns=['timestamp', 'btc_mcap'])
    btc_mcap['timestamp'] = pd.to_datetime(btc_mcap['timestamp'], unit='ms')
    btc_mcap.set_index('timestamp', inplace=True)
    
    # Note : la dominance exacte nécessite le total market cap historique
    # CoinGecko gratuit ne le fournit pas → utiliser un proxy
    # Proxy : BTC dominance ≈ BTC market cap / (BTC market cap + ETH market cap * 3)
    # C'est une approximation, mais suffisante pour le signal EMA7/EMA21
    
    return btc_mcap
```

**Fichiers produits** :
```
data/crypto/
├── candles/
│   ├── BTCUSDT_1h.parquet      # ~26K candles (3 ans)
│   ├── BTCUSDT_4h.parquet      # ~6.5K candles
│   ├── BTCUSDT_1d.parquet      # ~1.1K candles
│   ├── ETHUSDT_1h.parquet
│   ├── ETHUSDT_4h.parquet
│   ├── ETHUSDT_1d.parquet
│   ├── SOLUSDT_4h.parquet      # ~4.4K candles (2 ans)
│   ├── SOLUSDT_1d.parquet
│   └── ... (10 altcoins x 2 timeframes)
├── borrow_rates/
│   ├── borrow_rate_BTC.parquet
│   ├── borrow_rate_ETH.parquet
│   └── ... (11 assets)
├── dominance/
│   └── btc_dominance.parquet
└── metadata/
    └── collection_log.json      # Log de la collecte (dates, counts)
```

---

## 2. WALK-FORWARD DES 8 STRATÉGIES CRYPTO

```yaml
priorité: P0
temps: 3h (code + exécution)
agent: QR + BT-ARCH
dépendances: HIST-001
```

### □ WF-001 — Walk-forward avec le BacktesterV2

```python
# scripts/wf_crypto_all.py

"""
Walk-forward des 8 stratégies crypto avec le BacktesterV2.
Utilise les données historiques collectées par HIST-001.

POUR CHAQUE STRATÉGIE :
1. Charger les données historiques
2. Configurer le backtest avec les coûts Binance France
   (commissions 0.10%, intérêts emprunt horaires, pas de funding)
3. Exécuter le walk-forward (train 6m / test 2m, min 4 fenêtres)
4. Exécuter le Monte Carlo (10K permutations)
5. Verdict : VALIDATED / BORDERLINE / REJECTED

COÛTS INTÉGRÉS :
- Commission spot/margin : 0.10% par trade (sans BNB discount)
- Intérêts emprunt : taux historiques réels par asset par jour
- Slippage : BTC 2bps, ETH 3bps, altcoins 5-8bps
- Pas de funding rate (pas de perp en France)
"""

from core.backtester_v2.engine import BacktesterV2
from core.backtester_v2.walk_forward import WalkForwardEngine
from core.backtester_v2.monte_carlo import MonteCarloEngine
from core.backtester_v2.config import BacktestConfig

# Import des 8 stratégies migrées
from strategies_v2.crypto.btc_eth_momentum import BTCETHDualMomentum
from strategies_v2.crypto.altcoin_rs import AltcoinRelativeStrength
from strategies_v2.crypto.btc_mr import BTCMeanReversion
from strategies_v2.crypto.vol_breakout import VolBreakout
from strategies_v2.crypto.btc_dominance import BTCDominanceV2
from strategies_v2.crypto.borrow_carry import BorrowRateCarry
from strategies_v2.crypto.liquidation_momentum import LiquidationMomentum
from strategies_v2.crypto.weekend_gap import WeekendGap

CRYPTO_BACKTEST_CONFIG = BacktestConfig(
    initial_capital=20000,
    brokers=["BINANCE"],
    cost_model="binance_margin",  # 0.10% + borrow interest
    slippage_model="crypto_tiered",  # BTC 2bps, alt 5-8bps
    risk_limits={
        "max_position_pct": 15,
        "max_gross_pct": 150,
        "max_drawdown_pct": 20,
        "max_leverage": 2.5,
    }
)

WF_CONFIG_TIER1 = {  # BTC/ETH (3 ans de données)
    "train_months": 6,
    "test_months": 2,
    "min_windows": 4,
    "min_oos_is_ratio": 0.4,
    "min_profitable_windows_pct": 0.5,
}

WF_CONFIG_TIER2 = {  # Altcoins (2 ans de données)
    "train_months": 4,
    "test_months": 1.5,
    "min_windows": 4,
    "min_oos_is_ratio": 0.45,
    "min_profitable_windows_pct": 0.5,
}

BOOTSTRAP_CONFIG = {  # Strats avec < 50 trades/an
    "method": "bootstrap",
    "n_samples": 1000,
    "min_sharpe_95ci_lower": 0.3,
}

STRATEGIES = [
    {"class": BTCETHDualMomentum, "wf_config": WF_CONFIG_TIER1, 
     "name": "BTC/ETH Dual Momentum", "expected_sharpe": "1.5-2.5"},
    {"class": AltcoinRelativeStrength, "wf_config": WF_CONFIG_TIER2, 
     "name": "Altcoin Relative Strength", "expected_sharpe": "1.0-2.0"},
    {"class": BTCMeanReversion, "wf_config": WF_CONFIG_TIER1, 
     "name": "BTC Mean Reversion", "expected_sharpe": "1.0-1.8"},
    {"class": VolBreakout, "wf_config": WF_CONFIG_TIER1, 
     "name": "Vol Breakout", "expected_sharpe": "1.2-2.0"},
    {"class": BTCDominanceV2, "wf_config": WF_CONFIG_TIER1, 
     "name": "BTC Dominance V2", "expected_sharpe": "0.8-1.5"},
    {"class": BorrowRateCarry, "wf_config": None,  # Pas de WF (pas de signal)
     "name": "Borrow Rate Carry", "expected_sharpe": "N/A"},
    {"class": LiquidationMomentum, "wf_config": BOOTSTRAP_CONFIG, 
     "name": "Liquidation Momentum", "expected_sharpe": "1.0-2.5"},
    {"class": WeekendGap, "wf_config": BOOTSTRAP_CONFIG, 
     "name": "Weekend Gap", "expected_sharpe": "0.5-1.5"},
]

def run_all_wf():
    results = {}
    
    for strat_config in STRATEGIES:
        name = strat_config["name"]
        print(f"\n{'='*60}")
        print(f"WALK-FORWARD: {name}")
        print(f"{'='*60}")
        
        if strat_config["wf_config"] is None:
            print(f"  → SKIP (pas de WF pour {name} — rendement passif)")
            results[name] = {"verdict": "N/A", "reason": "Passive yield strategy"}
            continue
        
        strat_class = strat_config["class"]
        wf_config = strat_config["wf_config"]
        
        # Walk-Forward
        wf = WalkForwardEngine()
        wf_result = wf.run(strat_class, data, wf_config)
        
        # Monte Carlo
        if wf_result.verdict != "REJECTED":
            mc = MonteCarloEngine()
            mc_result = mc.run(wf_result.combined_trade_log, n_simulations=10000)
        else:
            mc_result = None
        
        results[name] = {
            "verdict": wf_result.verdict,
            "avg_oos_sharpe": wf_result.avg_oos_sharpe,
            "avg_is_sharpe": wf_result.avg_is_sharpe,
            "oos_is_ratio": wf_result.oos_is_ratio,
            "pct_profitable": wf_result.pct_profitable,
            "total_trades_oos": wf_result.total_trades_oos,
            "mc_p5_sharpe": mc_result.p5_sharpe if mc_result else None,
            "mc_prob_ruin": mc_result.prob_ruin if mc_result else None,
        }
        
        print(f"  Verdict: {wf_result.verdict}")
        print(f"  OOS Sharpe: {wf_result.avg_oos_sharpe:.2f}")
        print(f"  OOS/IS ratio: {wf_result.oos_is_ratio:.2f}")
        print(f"  % fenêtres profitables: {wf_result.pct_profitable:.0%}")
        print(f"  Trades OOS: {wf_result.total_trades_oos}")
        if mc_result:
            print(f"  MC P5 Sharpe: {mc_result.p5_sharpe:.2f}")
            print(f"  MC prob ruin: {mc_result.prob_ruin:.2%}")
    
    # Résumé final
    print(f"\n{'='*60}")
    print("RÉSUMÉ WALK-FORWARD CRYPTO")
    print(f"{'='*60}")
    validated = sum(1 for r in results.values() if r["verdict"] == "VALIDATED")
    borderline = sum(1 for r in results.values() if r["verdict"] == "BORDERLINE")
    rejected = sum(1 for r in results.values() if r["verdict"] == "REJECTED")
    na = sum(1 for r in results.values() if r["verdict"] == "N/A")
    print(f"VALIDATED: {validated} | BORDERLINE: {borderline} | REJECTED: {rejected} | N/A: {na}")
    print(f"Minimum requis : 4/8 VALIDATED pour maintenir le portefeuille crypto")
    
    if validated + borderline >= 4:
        print("✅ PORTEFEUILLE CRYPTO VALIDÉ")
    else:
        print("⚠️ PORTEFEUILLE CRYPTO À RISQUE — moins de 4 strats validées")
    
    # Sauvegarder
    with open("data/crypto/wf_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    return results
```

---

## 3. OPTIMISATION ROC CRYPTO

```yaml
priorité: P0
temps: 4h
agent: QR + EXEC-ENG
```

### □ ROC-C01 — Cash Sweep Earn automatique

```python
# core/crypto/cash_sweep.py

"""
Le cash idle sur Binance → Earn Flexible USDT automatiquement.
Retrait en < 1 min quand un signal arrive.
Rendement : 3-8% APY sur le cash mort.
"""

class CashSweepManager:
    MIN_CASH_BUFFER = 500       # $500 toujours en spot (ordres urgents)
    MIN_SWEEP_AMOUNT = 100      # Ne pas sweeper < $100 (frais inutiles)
    CHECK_INTERVAL = 3600       # Vérifier toutes les heures
    SWEEP_ASSET = "USDT"
    
    def __init__(self, broker):
        self.broker = broker
        self.last_sweep = None
        self.total_swept = 0
        self.total_yield = 0
    
    def check_and_sweep(self):
        """Appelé toutes les heures par le worker."""
        spot_balance = self.broker.get_spot_balance(self.SWEEP_ASSET)
        sweepable = spot_balance - self.MIN_CASH_BUFFER
        
        if sweepable >= self.MIN_SWEEP_AMOUNT:
            result = self.broker.subscribe_earn(
                self.SWEEP_ASSET, sweepable, "FLEXIBLE"
            )
            if result:
                self.total_swept += sweepable
                self.last_sweep = datetime.utcnow()
                logger.info(f"Cash sweep: ${sweepable:.0f} → Earn Flexible USDT")
                return sweepable
        return 0
    
    def ensure_cash_for_order(self, required_amount):
        """
        Avant un ordre, s'assurer qu'on a assez de cash.
        Si non → retirer de Earn Flexible (instantané).
        """
        spot_balance = self.broker.get_spot_balance(self.SWEEP_ASSET)
        
        if spot_balance >= required_amount:
            return True
        
        to_redeem = required_amount - spot_balance + 50  # $50 buffer
        earn_balance = self.broker.get_earn_balance(self.SWEEP_ASSET, "FLEXIBLE")
        
        if earn_balance < to_redeem:
            logger.warning(f"Cash insuffisant: besoin ${required_amount}, "
                          f"spot ${spot_balance}, earn ${earn_balance}")
            return False
        
        self.broker.redeem_earn(self.SWEEP_ASSET, to_redeem, "FLEXIBLE")
        import time
        time.sleep(3)  # Attendre que le retrait soit effectif
        
        new_balance = self.broker.get_spot_balance(self.SWEEP_ASSET)
        logger.info(f"Redeemed ${to_redeem:.0f} from Earn. New spot: ${new_balance:.0f}")
        return new_balance >= required_amount
    
    def get_stats(self):
        """Statistiques du cash sweep."""
        earn_balance = self.broker.get_earn_balance(self.SWEEP_ASSET, "FLEXIBLE")
        earn_apy = self.broker.get_earn_apy(self.SWEEP_ASSET, "FLEXIBLE")
        return {
            "earn_balance": earn_balance,
            "earn_apy": earn_apy,
            "estimated_daily_yield": earn_balance * earn_apy / 365,
            "estimated_annual_yield": earn_balance * earn_apy,
            "total_swept": self.total_swept,
            "total_yield": self.total_yield,
        }
```

### □ ROC-C02 — Conviction Sizer crypto

```python
# core/crypto/conviction_sizer.py

"""
Sizing dynamique par conviction pour les strats crypto.
Signal fort = 1.5x le sizing de base.
Signal faible = 0.7x ou skip.
"""

class CryptoConvictionSizer:
    MULTIPLIERS = {
        "STRONG": {"min_score": 0.8, "multiplier": 1.5, "max_fraction": 0.1875},
        "NORMAL": {"min_score": 0.5, "multiplier": 1.0, "max_fraction": 0.125},
        "WEAK":   {"min_score": 0.3, "multiplier": 0.7, "max_fraction": 0.09},
        "SKIP":   {"min_score": 0.0, "multiplier": 0.0, "max_fraction": 0.0},
    }
    
    def calculate_conviction(self, signal, market_state):
        """
        Conviction score crypto (0-1).
        Agrège 5 signaux pondérés.
        """
        scores = {
            "trend_strength": self._adx_score(signal, market_state),
            "volume_confirm": self._volume_score(signal, market_state),
            "regime_align": self._regime_score(signal, market_state),
            "borrow_cost": self._borrow_cost_score(signal, market_state),
            "correlation": self._correlation_score(signal, market_state),
        }
        
        weights = {
            "trend_strength": 0.25,
            "volume_confirm": 0.20,
            "regime_align": 0.25,
            "borrow_cost": 0.15,  # Pénaliser si le borrow rate est élevé
            "correlation": 0.15,  # Pénaliser si trop corrélé au BTC
        }
        
        conviction = sum(scores[k] * weights[k] for k in scores)
        return conviction, scores
    
    def _adx_score(self, signal, market):
        """ADX > 30 = forte tendance = conviction haute."""
        adx = market.get("adx_14", 0)
        if adx > 40: return 1.0
        if adx > 30: return 0.8
        if adx > 25: return 0.6
        if adx > 20: return 0.4
        return 0.2
    
    def _volume_score(self, signal, market):
        """Volume > 2x moyenne = confirmation forte."""
        vol_ratio = market.get("volume_ratio_24h", 1.0)
        if vol_ratio > 2.0: return 1.0
        if vol_ratio > 1.5: return 0.8
        if vol_ratio > 1.0: return 0.6
        if vol_ratio > 0.8: return 0.4
        return 0.2
    
    def _regime_score(self, signal, market):
        """Signal aligné avec le régime crypto = conviction haute."""
        regime = market.get("crypto_regime", "CHOP")
        if signal.side == "BUY" and regime == "BULL": return 1.0
        if signal.side == "SELL" and regime == "BEAR": return 1.0
        if regime == "CHOP": return 0.5
        return 0.3  # Contre-tendance = faible conviction
    
    def _borrow_cost_score(self, signal, market):
        """Si le borrow rate est élevé, réduire la conviction pour les shorts."""
        if signal.side != "SELL":
            return 0.8  # Pas de pénalité pour les longs
        rate = market.get("borrow_rate_daily", 0)
        if rate < 0.02: return 1.0      # < 2%/an = pas cher
        if rate < 0.05: return 0.7      # < 18%/an = acceptable
        if rate < 0.10: return 0.4      # < 36%/an = cher
        return 0.1                       # > 36%/an = très cher
    
    def _correlation_score(self, signal, market):
        """Si la position est trop corrélée au BTC existant, réduire."""
        btc_correlation = market.get("btc_correlation_7d", 0)
        if btc_correlation < 0.3: return 1.0  # Décorrélé = bon
        if btc_correlation < 0.5: return 0.8
        if btc_correlation < 0.7: return 0.6
        return 0.3                             # Très corrélé = pas de diversification
    
    def get_adjusted_size(self, signal, market_state, base_kelly, capital):
        conviction, scores = self.calculate_conviction(signal, market_state)
        
        for level_name, params in sorted(
            self.MULTIPLIERS.items(), 
            key=lambda x: -x[1]["min_score"]
        ):
            if conviction >= params["min_score"]:
                adjusted = base_kelly * params["multiplier"]
                adjusted = min(adjusted, params["max_fraction"])
                size = capital * adjusted
                
                logger.info(
                    f"Conviction {signal.symbol}: {conviction:.2f} ({level_name}) "
                    f"→ {adjusted:.4f} Kelly (base {base_kelly:.4f})"
                )
                return size, conviction, level_name
        
        return 0, conviction, "SKIP"
```

### □ ROC-C03 — Borrow Rate Monitor + Auto-Close shorts coûteux

```python
# core/crypto/borrow_monitor.py

"""
Surveille les coûts d'emprunt margin en temps réel.
Ferme automatiquement les shorts si les intérêts deviennent trop chers.
"""

class BorrowRateMonitor:
    MAX_DAILY_RATE = 0.001    # 0.1%/jour = 36%/an → alerte
    MAX_MONTHLY_COST_PCT = 2  # 2% du capital/mois en intérêts → fermer
    CHECK_INTERVAL = 900      # Toutes les 15 min
    
    def __init__(self, broker, risk_manager):
        self.broker = broker
        self.risk_manager = risk_manager
        self.rate_history = {}
        self.cost_accumulator = {}
    
    def check_rates(self, positions):
        """Appelé toutes les 15 min par le worker."""
        alerts = []
        
        for pos in positions:
            if not pos.is_margin_short:
                continue
            
            asset = pos.borrowed_asset
            current_rate = self.broker.get_borrow_rate(asset)
            
            # Log le taux
            if asset not in self.rate_history:
                self.rate_history[asset] = []
            self.rate_history[asset].append({
                "timestamp": datetime.utcnow(),
                "rate": current_rate,
            })
            
            # Alerte si rate spike
            if current_rate > self.MAX_DAILY_RATE:
                alerts.append({
                    "level": "WARNING",
                    "message": f"Borrow rate {asset}: {current_rate*100:.3f}%/jour "
                              f"(seuil: {self.MAX_DAILY_RATE*100:.3f}%)",
                    "action": "consider_closing",
                    "position": pos,
                })
            
            # Rate spike 3x en 1h → kill switch trigger
            rates_1h = [r["rate"] for r in self.rate_history.get(asset, [])
                       if r["timestamp"] > datetime.utcnow() - timedelta(hours=1)]
            if len(rates_1h) >= 2 and rates_1h[-1] > rates_1h[0] * 3:
                alerts.append({
                    "level": "CRITICAL",
                    "message": f"Borrow rate SPIKE {asset}: {rates_1h[0]*100:.4f}% → "
                              f"{rates_1h[-1]*100:.4f}% en 1h (3x)",
                    "action": "close_immediately",
                    "position": pos,
                })
            
            # Coût mensuel cumulé
            monthly_cost = self._calc_monthly_cost(pos)
            cost_pct = monthly_cost / self.risk_manager.capital * 100
            if cost_pct > self.MAX_MONTHLY_COST_PCT:
                alerts.append({
                    "level": "WARNING",
                    "message": f"Monthly borrow cost {asset}: "
                              f"${monthly_cost:.0f} ({cost_pct:.1f}% du capital)",
                    "action": "close_most_expensive",
                    "position": pos,
                })
        
        return alerts
    
    def auto_close_expensive_shorts(self, positions, max_cost_pct=2.0):
        """
        Fermer les shorts les plus coûteux en intérêts.
        Commence par le plus cher et ferme jusqu'à ce que le coût soit < seuil.
        """
        shorts = [p for p in positions if p.is_margin_short]
        shorts_with_cost = [(p, self._calc_monthly_cost(p)) for p in shorts]
        shorts_with_cost.sort(key=lambda x: -x[1])  # Plus cher d'abord
        
        total_cost = sum(c for _, c in shorts_with_cost)
        cost_pct = total_cost / self.risk_manager.capital * 100
        
        closed = []
        while cost_pct > max_cost_pct and shorts_with_cost:
            pos, cost = shorts_with_cost.pop(0)
            self.broker.close_margin_position(pos)
            total_cost -= cost
            cost_pct = total_cost / self.risk_manager.capital * 100
            closed.append(pos.symbol)
            logger.warning(f"Auto-closed expensive short: {pos.symbol} (${cost:.0f}/mois)")
        
        return closed
    
    def get_report(self):
        """Rapport des coûts d'emprunt."""
        report = {}
        for asset, rates in self.rate_history.items():
            if not rates:
                continue
            recent = [r["rate"] for r in rates[-24:]]  # Dernières 24h
            report[asset] = {
                "current_rate": rates[-1]["rate"],
                "avg_24h": sum(recent) / len(recent),
                "max_24h": max(recent),
                "annualized": rates[-1]["rate"] * 365 * 100,
            }
        return report
```

### □ ROC-C04 — Régime Detector crypto amélioré

```python
# core/crypto/regime_detector.py

"""
Détection de régime crypto : BULL / BEAR / CHOP.
Utilisé par l'allocateur pour ajuster les poids des stratégies.
"""

class CryptoRegimeDetector:
    """
    Détecte le régime crypto en combinant 4 signaux :
    1. Trend BTC (EMA50 daily)
    2. Momentum BTC (rendement 30j)
    3. Volatilité (vol 7j vs vol 30j)
    4. Market breadth (% d'altcoins > EMA50)
    
    Chaque signal vote BULL, BEAR, ou CHOP.
    Le régime final = vote majoritaire.
    Transition lissée : max 10%/jour vers la cible.
    """
    
    def detect(self, market_data):
        votes = {
            "trend": self._trend_vote(market_data),
            "momentum": self._momentum_vote(market_data),
            "volatility": self._volatility_vote(market_data),
            "breadth": self._breadth_vote(market_data),
        }
        
        regime_scores = {"BULL": 0, "BEAR": 0, "CHOP": 0}
        weights = {"trend": 0.35, "momentum": 0.25, "volatility": 0.20, "breadth": 0.20}
        
        for signal, vote in votes.items():
            regime_scores[vote] += weights[signal]
        
        # Le régime avec le score le plus élevé gagne
        regime = max(regime_scores, key=regime_scores.get)
        confidence = regime_scores[regime]
        
        return CryptoRegime(
            regime=regime,
            confidence=confidence,
            votes=votes,
            scores=regime_scores,
            timestamp=datetime.utcnow(),
        )
    
    def _trend_vote(self, data):
        btc_close = data["BTCUSDT"]["close"]
        ema50 = data["BTCUSDT"]["ema_50_daily"]
        ema200 = data["BTCUSDT"]["ema_200_daily"]
        
        if btc_close > ema50 and ema50 > ema200:
            return "BULL"
        elif btc_close < ema50 and ema50 < ema200:
            return "BEAR"
        return "CHOP"
    
    def _momentum_vote(self, data):
        ret_30d = data["BTCUSDT"]["return_30d"]
        if ret_30d > 0.10:    return "BULL"   # +10% en 30j
        elif ret_30d < -0.10: return "BEAR"   # -10% en 30j
        return "CHOP"
    
    def _volatility_vote(self, data):
        vol_7d = data["BTCUSDT"]["vol_7d"]
        vol_30d = data["BTCUSDT"]["vol_30d"]
        ratio = vol_7d / vol_30d if vol_30d > 0 else 1
        
        if ratio < 0.5:   return "CHOP"   # Compression = range
        elif ratio > 1.5: return "BEAR" if data["BTCUSDT"]["return_7d"] < 0 else "BULL"
        return "CHOP"
    
    def _breadth_vote(self, data):
        """% d'altcoins au-dessus de leur EMA50."""
        above_ema50 = sum(1 for sym in data.get("altcoins", []) 
                         if data[sym]["close"] > data[sym]["ema_50_daily"])
        total = len(data.get("altcoins", []))
        if total == 0:
            return "CHOP"
        
        pct = above_ema50 / total
        if pct > 0.7:  return "BULL"
        elif pct < 0.3: return "BEAR"
        return "CHOP"
```

### □ ROC-C05 — Timing d'entrée optimisé crypto

```python
# core/crypto/entry_timing.py

"""
Optimiser le timing des entrées crypto par session.

DONNÉES EMPIRIQUES :
- Session Asie (0-8h UTC) : spread BTC ~2-3 bps, volume bas
- Session Europe (8-16h UTC) : spread BTC ~1.5-2 bps, volume moyen
- Session US (14-22h UTC) : spread BTC ~1-1.5 bps, volume max
- Overlap EU/US (14-16h UTC) : spread minimum, meilleur pour les entrées

OPTIMISATION : retarder les entrées non-urgentes vers les sessions à haut volume.
"""

class CryptoEntryTiming:
    # Spread multiplier par heure UTC (1.0 = baseline)
    SPREAD_CURVE = {
        0: 1.8, 1: 1.9, 2: 2.0, 3: 2.0, 4: 1.8, 5: 1.6,
        6: 1.4, 7: 1.3, 8: 1.2, 9: 1.1, 10: 1.0, 11: 1.0,
        12: 0.9, 13: 0.9, 14: 0.8, 15: 0.8,  # Overlap EU/US = best
        16: 0.9, 17: 0.9, 18: 1.0, 19: 1.0, 20: 1.1, 21: 1.2,
        22: 1.4, 23: 1.6,
    }
    
    # Heures optimales par type de signal
    OPTIMAL_WINDOWS = {
        "trend": {"preferred": (14, 20), "avoid": (2, 7)},       # Trend = volume
        "mean_reversion": {"preferred": (8, 16), "avoid": None},  # MR = any time
        "momentum": {"preferred": (14, 18), "avoid": (0, 6)},    # Momentum = overlap
        "event": {"preferred": None, "avoid": None},              # Event = immédiat
    }
    
    def should_delay_entry(self, signal, current_hour_utc):
        """
        Retourne True si l'entrée devrait être retardée pour un meilleur prix.
        Ne retarde JAMAIS :
        - Les signaux EVENT (urgents)
        - Les signaux avec conviction > 0.9 (trop fort pour attendre)
        - Les signaux pendant les heures optimales
        """
        if signal.strategy_type == "event":
            return False, 0  # Jamais retarder les events
        
        if signal.conviction_score > 0.9:
            return False, 0  # Signal trop fort, entrer immédiatement
        
        window = self.OPTIMAL_WINDOWS.get(signal.strategy_type, {})
        preferred = window.get("preferred")
        avoid = window.get("avoid")
        
        # Si on est dans les heures à éviter → retarder
        if avoid and avoid[0] <= current_hour_utc < avoid[1]:
            # Calculer le délai jusqu'à la prochaine heure préférée
            if preferred:
                delay_hours = (preferred[0] - current_hour_utc) % 24
                if delay_hours > 6:
                    return False, 0  # Trop long, ne pas retarder > 6h
                return True, delay_hours
        
        return False, 0
    
    def get_spread_estimate(self, symbol, hour_utc):
        """Estimation du spread actuel basé sur l'heure."""
        base_spread = {"BTCUSDT": 2, "ETHUSDT": 3}.get(symbol, 5)  # bps
        return base_spread * self.SPREAD_CURVE.get(hour_utc, 1.0)
```

---

## 4. MONITORING CRYPTO (sans Telegram)

```yaml
priorité: P0
temps: 2h
agent: INFRA
```

### □ MON-001 — Crypto Monitor avec log fichier

```python
# core/crypto/live_monitor.py

"""
Monitoring crypto en temps réel.
Log en fichier (pas de Telegram encore).
Le dashboard XL lit ces logs pour afficher les données.
"""

class CryptoLiveMonitor:
    LOG_FILE = "logs/crypto_monitor.jsonl"
    CHECK_INTERVAL = 300  # 5 minutes
    
    def __init__(self, broker, risk_manager):
        self.broker = broker
        self.risk = risk_manager
        self.start_capital = None
        self.snapshots = []
    
    def run_check(self):
        """Appelé toutes les 5 minutes par le worker."""
        snapshot = self._collect_snapshot()
        self._check_alerts(snapshot)
        self._log(snapshot)
        self.snapshots.append(snapshot)
        return snapshot
    
    def _collect_snapshot(self):
        """Collecter toutes les données crypto."""
        positions = self.broker.get_positions()
        balances = self.broker.get_account_balance()
        
        # Calculer les métriques
        total_equity = balances.total_equity_usd
        if self.start_capital is None:
            self.start_capital = total_equity
        
        pnl_total = total_equity - self.start_capital
        pnl_pct = pnl_total / self.start_capital * 100
        
        # Positions détaillées
        position_details = []
        for pos in positions:
            detail = {
                "symbol": pos.symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "pnl": pos.unrealized_pnl,
                "pnl_pct": pos.unrealized_pnl_pct,
                "strategy": pos.strategy_name,
                "mode": pos.mode,  # SPOT, MARGIN, EARN
            }
            if pos.is_margin_short:
                detail["borrow_rate"] = self.broker.get_borrow_rate(pos.borrowed_asset)
                detail["margin_level"] = pos.margin_level
                detail["borrow_cost_cumul"] = pos.cumulative_interest
            position_details.append(detail)
        
        # Earn positions
        earn_positions = self.broker.get_earn_positions()
        earn_details = []
        for ep in earn_positions:
            earn_details.append({
                "asset": ep.asset,
                "amount": ep.amount,
                "apy": ep.current_apy,
                "daily_yield": ep.amount * ep.current_apy / 365,
            })
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "equity": total_equity,
            "pnl_total": pnl_total,
            "pnl_pct": pnl_pct,
            "positions_count": len(positions),
            "positions": position_details,
            "earn": earn_details,
            "balances": {
                "spot_usdt": balances.spot.get("USDT", 0),
                "spot_eur": balances.spot.get("EUR", 0),
                "spot_btc": balances.spot.get("BTC", 0),
                "margin_used": balances.margin_used,
                "margin_level": balances.margin_level,
                "earn_total": sum(e["amount"] * self._get_usd_price(e["asset"]) 
                                for e in earn_details),
            },
            "risk": {
                "drawdown_pct": self._calc_drawdown(),
                "gross_exposure_pct": self._calc_gross_exposure(positions, total_equity),
                "net_exposure_pct": self._calc_net_exposure(positions, total_equity),
                "kill_switch_active": self.risk.is_killed,
            },
            "regime": self._detect_regime(),
        }
    
    def _check_alerts(self, snapshot):
        """Vérifier les conditions d'alerte."""
        alerts = []
        
        # Drawdown
        if snapshot["risk"]["drawdown_pct"] > 5:
            alerts.append(("CRITICAL", f"Drawdown crypto: {snapshot['risk']['drawdown_pct']:.1f}%"))
        elif snapshot["risk"]["drawdown_pct"] > 3:
            alerts.append(("WARNING", f"Drawdown crypto: {snapshot['risk']['drawdown_pct']:.1f}%"))
        
        # Margin level
        ml = snapshot["balances"]["margin_level"]
        if ml and ml < 1.3:
            alerts.append(("CRITICAL", f"Margin level: {ml:.2f} (Binance liquide à 1.1)"))
        elif ml and ml < 1.5:
            alerts.append(("WARNING", f"Margin level: {ml:.2f}"))
        
        # Borrow rates élevés
        for pos in snapshot["positions"]:
            if pos.get("borrow_rate", 0) > 0.001:
                alerts.append(("WARNING", f"Borrow rate {pos['symbol']}: "
                              f"{pos['borrow_rate']*100:.3f}%/jour"))
        
        # PnL journalier
        daily_pnl = self._calc_daily_pnl()
        if daily_pnl < -0.05 * self.start_capital:
            alerts.append(("CRITICAL", f"Daily loss: ${daily_pnl:.0f} (> 5%)"))
        
        for level, message in alerts:
            logger.log(
                logging.CRITICAL if level == "CRITICAL" else logging.WARNING,
                f"[CRYPTO ALERT] {message}"
            )
        
        return alerts
    
    def _log(self, snapshot):
        """Log le snapshot en JSONL."""
        with open(self.LOG_FILE, "a") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")
    
    def get_summary(self, period_hours=24):
        """Résumé pour le dashboard."""
        recent = [s for s in self.snapshots 
                 if datetime.fromisoformat(s["timestamp"]) > 
                    datetime.utcnow() - timedelta(hours=period_hours)]
        
        if not recent:
            return None
        
        return {
            "period_hours": period_hours,
            "snapshots_count": len(recent),
            "equity_start": recent[0]["equity"],
            "equity_end": recent[-1]["equity"],
            "pnl_period": recent[-1]["equity"] - recent[0]["equity"],
            "max_drawdown": max(s["risk"]["drawdown_pct"] for s in recent),
            "avg_positions": sum(s["positions_count"] for s in recent) / len(recent),
            "alerts_count": sum(len(self._check_alerts(s)) for s in recent),
            "avg_margin_level": sum(s["balances"]["margin_level"] or 999 
                                   for s in recent) / len(recent),
        }
```

### □ MON-002 — Réconciliation crypto toutes les 5 min

```python
# core/crypto/reconciliation.py

"""
Vérifie que les positions locales matchent Binance toutes les 5 min.
9 checks de réconciliation.
"""

class CryptoReconciliation:
    CHECKS = [
        "positions_match",       # Positions locales = positions Binance
        "balances_match",        # Balances locales = balances Binance
        "margin_level_ok",       # Margin level > 1.5
        "all_stops_active",      # Chaque position a un stop loss
        "leverage_correct",      # Levier de chaque position <= max autorisé
        "margin_mode_isolated",  # Toutes les positions margin sont ISOLATED
        "earn_positions_tracked",# Positions Earn dans le modèle local
        "no_orphan_orders",      # Pas d'ordres ouverts non trackés
        "borrow_repaid",         # Pas d'emprunt sur des positions fermées
    ]
    
    def run(self, local_state, broker):
        """Exécuter les 9 checks."""
        results = {}
        
        # 1. Positions match
        broker_positions = broker.get_positions()
        local_positions = local_state.positions
        results["positions_match"] = self._check_positions(local_positions, broker_positions)
        
        # 2. Balances match
        broker_balances = broker.get_account_balance()
        local_balances = local_state.balances
        results["balances_match"] = self._check_balances(local_balances, broker_balances)
        
        # 3. Margin level
        ml = broker_balances.margin_level
        results["margin_level_ok"] = ml is None or ml > 1.5
        
        # 4. Stops actifs
        results["all_stops_active"] = all(
            self._has_stop(pos, broker) for pos in broker_positions
            if pos.mode != "EARN"
        )
        
        # 5. Leverage correct
        results["leverage_correct"] = all(
            pos.leverage <= self._max_leverage(pos.symbol)
            for pos in broker_positions if pos.is_margin
        )
        
        # 6. Margin mode
        results["margin_mode_isolated"] = all(
            pos.margin_type == "ISOLATED"
            for pos in broker_positions if pos.is_margin
        )
        
        # 7. Earn tracked
        broker_earn = broker.get_earn_positions()
        results["earn_positions_tracked"] = len(broker_earn) == len(local_state.earn_positions)
        
        # 8. Orphan orders
        open_orders = broker.get_open_orders()
        tracked_orders = local_state.open_orders
        orphans = [o for o in open_orders if o.id not in tracked_orders]
        results["no_orphan_orders"] = len(orphans) == 0
        
        # 9. Borrow repaid
        borrows = broker.get_margin_borrows()
        active_shorts = [p for p in broker_positions if p.is_margin_short]
        results["borrow_repaid"] = len(borrows) <= len(active_shorts)
        
        # Divergences
        divergences = [k for k, v in results.items() if not v]
        if divergences:
            logger.warning(f"Crypto reconciliation: {len(divergences)} divergences: {divergences}")
        else:
            logger.info("Crypto reconciliation: OK (9/9 checks pass)")
        
        return results, divergences
```

---

## 5. PRÉPARATION TELEGRAM (code prêt, pas activé)

```yaml
priorité: P1
temps: 3h
agent: INFRA
```

### □ TG-001 — Bot Telegram complet (non activé)

```python
# core/telegram/crypto_bot.py

"""
Bot Telegram pour le monitoring crypto.
PRÊT À BRANCHER quand Marc créera le bot via @BotFather.

COMMANDES :
  /status      — Résumé portefeuille crypto
  /positions   — Positions ouvertes détaillées
  /pnl         — P&L today/week/month
  /risk        — Indicateurs de risque (DD, margin, exposure)
  /earn        — Positions Earn et rendement
  /regime      — Régime crypto actuel (BULL/BEAR/CHOP)
  /borrow      — Taux d'emprunt actuels
  /kill        — Kill switch (avec confirmation)
  /alerts      — Dernières alertes
  /strats      — Performance par stratégie
  /sweep       — Status du cash sweep Earn
  /help        — Liste des commandes

ALERTES AUTOMATIQUES (3 niveaux) :
  INFO : trade exécuté, rebalancement, earn yield
  WARNING : drawdown > 3%, borrow rate élevé, latence, margin level < 1.8
  CRITICAL : drawdown > 5%, margin level < 1.3, kill switch, API down

SÉCURITÉ :
  - Auth par chat_id (seul Marc peut utiliser le bot)
  - Rate limit : max 5 commandes/minute
  - Commandes destructives (/kill) : double confirmation
  - Pas de données sensibles dans les messages (pas d'API keys, pas de balances exactes si partagé)
"""

import asyncio
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler

class CryptoTelegramBot:
    def __init__(self, token, authorized_chat_id, monitor, risk_manager):
        self.token = token
        self.authorized_chat_id = str(authorized_chat_id)
        self.monitor = monitor
        self.risk = risk_manager
        self.app = None
        self.rate_limiter = {}
    
    def _auth(self, update):
        """Vérifier que le message vient de Marc."""
        return str(update.effective_chat.id) == self.authorized_chat_id
    
    async def cmd_status(self, update, context):
        if not self._auth(update):
            return
        
        snapshot = self.monitor.run_check()
        msg = (
            f"📊 *CRYPTO STATUS*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Capital: ${snapshot['equity']:,.0f}\n"
            f"📈 P&L total: ${snapshot['pnl_total']:+,.0f} ({snapshot['pnl_pct']:+.2f}%)\n"
            f"📉 Drawdown: {snapshot['risk']['drawdown_pct']:.1f}%\n"
            f"📊 Positions: {snapshot['positions_count']}\n"
            f"🏛 Margin level: {snapshot['balances']['margin_level'] or 'N/A'}\n"
            f"🎯 Régime: {snapshot['regime']}\n"
            f"🛡 Kill switch: {'🔴 ACTIF' if snapshot['risk']['kill_switch_active'] else '🟢 OFF'}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    
    async def cmd_positions(self, update, context):
        if not self._auth(update):
            return
        
        snapshot = self.monitor.run_check()
        if not snapshot["positions"]:
            await update.message.reply_text("Aucune position ouverte.")
            return
        
        msg = "📋 *POSITIONS OUVERTES*\n━━━━━━━━━━━━━━━━\n"
        for pos in snapshot["positions"]:
            emoji = "🟢" if pos["pnl"] >= 0 else "🔴"
            mode_emoji = {"SPOT": "💵", "MARGIN": "📊", "EARN": "🏦"}.get(pos["mode"], "❓")
            msg += (
                f"{emoji} *{pos['symbol']}* {pos['side']} {mode_emoji}\n"
                f"  Entry: ${pos['entry_price']:,.2f} → ${pos['current_price']:,.2f}\n"
                f"  P&L: ${pos['pnl']:+,.2f} ({pos['pnl_pct']:+.2f}%)\n"
                f"  Strat: {pos['strategy']}\n"
            )
            if pos.get("borrow_rate"):
                msg += f"  Borrow: {pos['borrow_rate']*100:.3f}%/jour\n"
            msg += "\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    
    async def cmd_pnl(self, update, context):
        if not self._auth(update):
            return
        
        summary_24h = self.monitor.get_summary(24)
        summary_7d = self.monitor.get_summary(168)
        summary_30d = self.monitor.get_summary(720)
        
        msg = (
            f"💹 *P&L CRYPTO*\n"
            f"━━━━━━━━━━━━━━━━\n"
        )
        if summary_24h:
            msg += f"📅 24h: ${summary_24h['pnl_period']:+,.0f}\n"
        if summary_7d:
            msg += f"📅 7j:  ${summary_7d['pnl_period']:+,.0f}\n"
        if summary_30d:
            msg += f"📅 30j: ${summary_30d['pnl_period']:+,.0f}\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    
    async def cmd_kill(self, update, context):
        if not self._auth(update):
            return
        
        if not context.args or context.args[0] != "CONFIRM":
            await update.message.reply_text(
                "⚠️ *KILL SWITCH CRYPTO*\n\n"
                "Ceci va fermer TOUTES les positions crypto.\n"
                "Tapez `/kill CONFIRM` pour confirmer.",
                parse_mode="Markdown"
            )
            return
        
        # Exécuter le kill switch
        result = self.risk.activate_kill_switch("TELEGRAM_MANUAL")
        await update.message.reply_text(
            f"🔴 *KILL SWITCH ACTIVÉ*\n"
            f"Positions fermées: {result['positions_closed']}\n"
            f"Ordres annulés: {result['orders_cancelled']}\n"
            f"Emprunts remboursés: {result['borrows_repaid']}\n"
            f"Earn retiré: ${result['earn_redeemed']:,.0f}",
            parse_mode="Markdown"
        )
    
    async def cmd_earn(self, update, context):
        if not self._auth(update):
            return
        
        snapshot = self.monitor.run_check()
        msg = "🏦 *BINANCE EARN*\n━━━━━━━━━━━━━━━━\n"
        
        total_yield_daily = 0
        for ep in snapshot["earn"]:
            daily = ep["daily_yield"]
            total_yield_daily += daily
            msg += (
                f"  {ep['asset']}: ${ep['amount']:,.0f} @ {ep['apy']*100:.1f}% APY\n"
                f"  Yield/jour: ${daily:.2f}\n\n"
            )
        
        msg += f"💰 Yield total/jour: ${total_yield_daily:.2f}\n"
        msg += f"📅 Yield estimé/an: ${total_yield_daily*365:.0f}\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    
    async def send_alert(self, level, message):
        """Envoyer une alerte automatique."""
        emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "CRITICAL": "🚨"}.get(level, "❓")
        text = f"{emoji} *{level}*\n{message}"
        
        bot = Bot(self.token)
        await bot.send_message(
            chat_id=self.authorized_chat_id,
            text=text,
            parse_mode="Markdown"
        )
    
    def setup(self):
        """Configurer le bot (appeler au démarrage)."""
        self.app = Application.builder().token(self.token).build()
        
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("pnl", self.cmd_pnl))
        self.app.add_handler(CommandHandler("kill", self.cmd_kill))
        self.app.add_handler(CommandHandler("earn", self.cmd_earn))
        self.app.add_handler(CommandHandler("risk", self.cmd_risk))
        self.app.add_handler(CommandHandler("regime", self.cmd_regime))
        self.app.add_handler(CommandHandler("borrow", self.cmd_borrow))
        self.app.add_handler(CommandHandler("strats", self.cmd_strats))
        self.app.add_handler(CommandHandler("sweep", self.cmd_sweep))
        self.app.add_handler(CommandHandler("alerts", self.cmd_alerts))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        
        return self.app


# config/telegram_config.yaml (TEMPLATE — remplacer par les vraies valeurs)
# telegram:
#   token: "YOUR_BOT_TOKEN_HERE"  # Obtenu via @BotFather
#   chat_id: "YOUR_CHAT_ID_HERE"  # Obtenu via @userinfobot
#   enabled: false                 # Mettre à true quand prêt
#   rate_limit: 5                  # Max 5 commandes/minute
#   alert_levels: ["WARNING", "CRITICAL"]  # Quels niveaux envoyer
```

---

## 6. KILL SWITCH CRYPTO — TEST COMPLET

```yaml
priorité: P0
temps: 2h
agent: SEC-AUD + RISK-ENG
```

### □ KS-001 — Test des 6 triggers

```python
# tests/test_crypto_kill_switch_e2e.py

"""
Test end-to-end des 6 triggers du kill switch crypto.
Chaque test simule le trigger et vérifie le séquencement correct :
  1. Close shorts (arrêter les intérêts)
  2. Cancel all orders
  3. Close longs
  4. Repay borrows
  5. Redeem Earn
  6. Alert (log, pas Telegram encore)
  7. Convert all to USDT
"""

class TestCryptoKillSwitch:
    
    def test_daily_loss_5pct(self):
        """Trigger 1 : perte daily > 5%."""
        # Simuler un P&L de -$1,100 sur $20K = -5.5%
        ks = CryptoKillSwitch(capital=20000)
        ks.update_pnl(daily_pnl=-1100)
        assert ks.is_triggered
        assert ks.trigger_reason == "DAILY_LOSS_5PCT"
        # Vérifier le séquencement
        assert ks.actions_executed == [
            "close_shorts", "cancel_orders", "close_longs",
            "repay_borrows", "redeem_earn", "alert", "convert_usdt"
        ]
    
    def test_hourly_loss_3pct(self):
        """Trigger 2 : perte horaire > 3%."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_pnl(hourly_pnl=-700)  # -3.5%
        assert ks.is_triggered
        assert ks.trigger_reason == "HOURLY_LOSS_3PCT"
    
    def test_max_drawdown_20pct(self):
        """Trigger 3 : drawdown > 20%."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_drawdown(drawdown_pct=21)
        assert ks.is_triggered
        assert ks.trigger_reason == "MAX_DD_20PCT"
    
    def test_api_down_10min(self):
        """Trigger 4 : API Binance down > 10 minutes."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_api_status(down_since=datetime.utcnow() - timedelta(minutes=11))
        assert ks.is_triggered
        assert ks.trigger_reason == "API_DOWN_10MIN"
    
    def test_margin_level_critical(self):
        """Trigger 5 : margin level < 1.2."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_margin_level(1.15)
        assert ks.is_triggered
        assert ks.trigger_reason == "MARGIN_LEVEL_CRITICAL"
    
    def test_borrow_rate_spike(self):
        """Trigger 6 : borrow rate spike 3x en 1h."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_borrow_rate("BTC", [0.0001, 0.0001, 0.0003, 0.0005])  # Spike 5x
        assert ks.is_triggered
        assert ks.trigger_reason == "BORROW_RATE_SPIKE"
    
    def test_no_false_positive(self):
        """Vérifier que le kill switch ne se déclenche PAS dans des conditions normales."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_pnl(daily_pnl=-500)    # -2.5% = sous le seuil
        ks.update_margin_level(2.3)       # Healthy
        ks.update_drawdown(drawdown_pct=3) # Sous le seuil
        assert not ks.is_triggered
    
    def test_kill_switch_is_idempotent(self):
        """Déclencher 2 fois ne ferme pas les positions 2 fois."""
        ks = CryptoKillSwitch(capital=20000)
        ks.update_pnl(daily_pnl=-1100)
        assert ks.is_triggered
        
        # Déclencher à nouveau
        ks.update_pnl(daily_pnl=-1200)
        # Les actions ne doivent PAS être re-exécutées
        assert len(ks.actions_log) == 7  # Pas 14
```

---

## CHECKLIST COMPLÈTE

```
DONNÉES (2h) :
□ HIST-001  Collecte candles 3 ans BTC/ETH + 2 ans altcoins    (1.5h)
□ HIST-002  Collecte borrow rates + dominance + fear/greed       (0.5h)

VALIDATION (3h) :
□ WF-001    Walk-forward des 8 strats crypto                     (3h)

ROC CRYPTO (4h) :
□ ROC-C01   Cash Sweep Earn automatique                          (1h)
□ ROC-C02   Conviction Sizer crypto                              (1h)
□ ROC-C03   Borrow Rate Monitor + auto-close shorts chers        (1h)
□ ROC-C04   Régime Detector crypto amélioré                      (0.5h)
□ ROC-C05   Timing d'entrée optimisé par session                  (0.5h)

MONITORING (2h) :
□ MON-001   Crypto Monitor avec log fichier                       (1h)
□ MON-002   Réconciliation crypto 9 checks                        (1h)

TELEGRAM PREP (3h) :
□ TG-001    Bot Telegram complet (12 commandes, alertes, auth)    (3h)

KILL SWITCH (2h) :
□ KS-001    Test des 6 triggers end-to-end                        (2h)

TOTAL : 13 tâches | ~16h de travail | ~80 tests additionnels
```

---

*TODO Session Crypto — ROC + Monitoring + Validation*
*13 tâches | ~16h | 8 strats live $20K Binance*
*"Le monitoring est le deuxième pilote. Le kill switch est le siège éjectable."*
