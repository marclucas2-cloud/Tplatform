"""
Universe Manager : 3 couches d'univers de tickers.

COUCHE 1 — UNIVERS COMPLET (~3000-5000 tickers)
  Tous les US equities actifs et tradables sur Alpaca.
  Filtré par : exchange principal, shortable, pas de warrants/preferred.
  Refresh : hebdomadaire, cache local JSON.

COUCHE 2 — UNIVERS ÉLIGIBLE (~500-1500 tickers)
  Filtrage daily sur les données des 20 derniers jours :
  - Volume moyen daily > 500K shares (liquidité suffisante)
  - Prix > $5 (pas de penny stocks)
  - Prix < $2000 (position sizing raisonnable avec 5% du capital)
  - ATR daily > 1% (assez de mouvement pour couvrir les coûts)
  Refresh : quotidien, basé sur les daily bars Alpaca.

COUCHE 3 — STOCKS IN PLAY (~10-50 tickers/jour)
  Scanner dynamique chaque matin avant le backtest :
  - Gap d'ouverture > 2% (news overnight)
  - Volume première heure > 2x moyenne (institutional flow)
  - ATR intraday > 1.5x moyenne (volatilité anormale)
  - Earnings day (gap + volume spike simultanés)
  Ce sont les SEULS tickers que les stratégies tradent activement.

TICKERS PERMANENTS (toujours dans le scan, jamais filtrés) :
  - Benchmarks : SPY, QQQ, IWM, DIA
  - Cross-asset : TLT, GLD, USO
  - Crypto-proxies : COIN, MARA, MSTR
  - Sector ETFs : XLK, XLF, XLE, XLV, XLI, XLP, XLU, XLC, XLRE
"""
import os
import json
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import config

# ═══════════════════════════════════════════════════════════
# PERMANENT TICKERS — never filtered, always loaded
# ═══════════════════════════════════════════════════════════
PERMANENT_TICKERS = [
    # Benchmarks
    "SPY", "QQQ", "IWM", "DIA",
    # Cross-asset signals
    "TLT", "GLD", "USO",
    # Crypto proxies
    "COIN", "MARA", "MSTR",
    # Sector ETFs (for rotation detection + ETF arb)
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE",
]

# Sector mapping for strategies
SECTOR_MAP = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ADBE", "ORCL", "INTC", "QCOM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "USB"],
    "XLE": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "PXD", "OXY"],
    "XLV": ["UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY"],
    "XLC": ["META", "GOOGL", "GOOG", "NFLX", "DIS", "CMCSA", "T", "VZ", "CHTR", "EA"],
    "XLI": ["CAT", "GE", "HON", "UNP", "RTX", "DE", "BA", "LMT", "UPS", "MMM"],
    "XLP": ["PG", "KO", "PEP", "COST", "WMT", "MDLZ", "PM", "CL", "MO", "EL"],
}

# Sympathy pairs (leader → followers)
SYMPATHY_MAP = {
    "NVDA": ["AMD", "MRVL", "AVGO", "MU", "QCOM", "TSM", "INTC", "LRCX", "AMAT", "KLAC"],
    "COIN": ["MARA", "MSTR", "RIOT", "BITF", "HUT", "CLSK"],
    "TSLA": ["RIVN", "LCID", "NIO", "LI", "XPEV", "F", "GM"],
    "JPM": ["BAC", "WFC", "GS", "MS", "C"],
    "XOM": ["CVX", "COP", "EOG", "SLB", "OXY"],
    "AAPL": ["MSFT", "GOOGL", "META", "AMZN"],
    "AMZN": ["SHOP", "MELI", "SE", "BABA", "JD"],
    "META": ["SNAP", "PINS", "TTD", "ROKU"],
    "LLY": ["NVO", "ABBV", "MRK", "PFE"],
    "UNH": ["HUM", "CI", "ELV", "CNC"],
}

# Cache paths
UNIVERSE_CACHE = os.path.join(config.CACHE_DIR, "universe_full.json")
ELIGIBLE_CACHE = os.path.join(config.CACHE_DIR, "universe_eligible.json")
DAILY_STATS_CACHE = os.path.join(config.CACHE_DIR, "daily_stats.parquet")


# ═══════════════════════════════════════════════════════════
# COUCHE 1 : UNIVERS COMPLET (Alpaca listing)
# ═══════════════════════════════════════════════════════════

def fetch_full_universe(force_refresh: bool = False) -> list[str]:
    """
    Récupère TOUS les tickers tradables sur Alpaca.
    Cache 7 jours. Retourne ~3000-5000 tickers.
    """
    os.makedirs(config.CACHE_DIR, exist_ok=True)

    if not force_refresh and os.path.exists(UNIVERSE_CACHE):
        with open(UNIVERSE_CACHE) as f:
            cached = json.load(f)
        cache_date = datetime.fromisoformat(cached["date"])
        if (datetime.now() - cache_date).days < 7:
            tickers = cached["tickers"]
            print(f"  [UNIVERSE L1] Cache: {len(tickers)} tickers ({cache_date.date()})")
            return tickers

    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass, AssetStatus

        client = TradingClient(
            api_key=config.ALPACA_API_KEY or None,
            secret_key=config.ALPACA_SECRET_KEY or None,
            paper=True,
        )

        request = GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
        )
        assets = client.get_all_assets(request)

        tickers = []
        for asset in assets:
            if not asset.tradable:
                continue
            # Exchanges principaux uniquement
            if asset.exchange not in ["NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "NYSEARCA"]:
                continue
            sym = asset.symbol
            # Exclure warrants, preferred, units, droits
            if any(c in sym for c in ["/", ".", "-"]):
                continue
            if len(sym) > 5:
                continue
            tickers.append(sym)

        # Ajouter les permanents au cas où
        tickers = list(set(tickers + PERMANENT_TICKERS))
        tickers.sort()

        with open(UNIVERSE_CACHE, "w") as f:
            json.dump({"date": datetime.now().isoformat(), "tickers": tickers}, f)

        print(f"  [UNIVERSE L1] Fetched {len(tickers)} tradable US equities from Alpaca")
        return tickers

    except Exception as e:
        print(f"  [UNIVERSE L1] Alpaca error: {e}")
        print(f"  [UNIVERSE L1] Using fallback (~250 top tickers)")
        return _fallback_universe()


# ═══════════════════════════════════════════════════════════
# COUCHE 2 : UNIVERS ÉLIGIBLE (filtrage daily)
# ═══════════════════════════════════════════════════════════

def compute_daily_stats(
    tickers: list[str],
    lookback_days: int = 30,
    max_workers: int = 10,
) -> pd.DataFrame:
    """
    Calcule les stats daily (volume moyen, prix, ATR) pour filtrer l'univers.
    Utilise le threading pour paralléliser les appels Alpaca.
    Cache le résultat.
    """
    from data_fetcher import get_client
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    # Check cache (valid 24h)
    if os.path.exists(DAILY_STATS_CACHE):
        stats = pd.read_parquet(DAILY_STATS_CACHE)
        cache_time = datetime.fromtimestamp(os.path.getmtime(DAILY_STATS_CACHE))
        if (datetime.now() - cache_time).total_seconds() < 86400:
            print(f"  [UNIVERSE L2] Cache: {len(stats)} tickers daily stats")
            return stats

    client = get_client()
    end = datetime.now()
    start = end - timedelta(days=lookback_days + 10)

    def fetch_daily_one(ticker: str) -> Optional[dict]:
        """Fetch daily bars for a single ticker."""
        try:
            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
            )
            bars = client.get_stock_bars(request)
            if not bars or ticker not in bars.data or len(bars.data[ticker]) < 10:
                return None

            closes = [float(b.close) for b in bars.data[ticker]]
            highs = [float(b.high) for b in bars.data[ticker]]
            lows = [float(b.low) for b in bars.data[ticker]]
            volumes = [int(b.volume) for b in bars.data[ticker]]

            # ATR proxy
            trs = [h - l for h, l in zip(highs, lows)]
            atr = np.mean(trs[-14:]) if len(trs) >= 14 else np.mean(trs)
            last_price = closes[-1]

            return {
                "ticker": ticker,
                "last_price": last_price,
                "avg_volume_20d": int(np.mean(volumes[-20:])),
                "avg_volume_5d": int(np.mean(volumes[-5:])),
                "atr_14d": round(atr, 4),
                "atr_pct": round((atr / last_price) * 100, 2) if last_price > 0 else 0,
                "avg_range_pct": round(np.mean([(h-l)/c*100 for h,l,c in zip(highs[-20:], lows[-20:], closes[-20:])]), 2),
                "n_bars": len(closes),
            }
        except Exception:
            return None

    # Paralléliser — Alpaca rate limits ~200 req/min
    print(f"  [UNIVERSE L2] Computing daily stats for {len(tickers)} tickers...")
    results = []
    batch_size = 50

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_daily_one, t): t for t in batch}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        # Rate limit respect
        if i + batch_size < len(tickers):
            time.sleep(1)

        # Progress
        done = min(i + batch_size, len(tickers))
        if done % 500 == 0 or done == len(tickers):
            print(f"    ... {done}/{len(tickers)} tickers processed, {len(results)} valid")

    stats = pd.DataFrame(results)
    if not stats.empty:
        stats.to_parquet(DAILY_STATS_CACHE)

    print(f"  [UNIVERSE L2] Computed stats for {len(stats)} tickers")
    return stats


def filter_eligible_universe(
    stats: pd.DataFrame,
    min_volume: int = 500_000,
    min_price: float = 5.0,
    max_price: float = 2000.0,
    min_atr_pct: float = 1.0,
) -> list[str]:
    """
    Filtre l'univers complet pour ne garder que les tickers éligibles.
    Retourne ~500-1500 tickers.
    """
    if stats.empty:
        return PERMANENT_TICKERS

    eligible = stats[
        (stats["avg_volume_20d"] >= min_volume)
        & (stats["last_price"] >= min_price)
        & (stats["last_price"] <= max_price)
        & (stats["atr_pct"] >= min_atr_pct)
    ]

    tickers = list(set(eligible["ticker"].tolist() + PERMANENT_TICKERS))
    tickers.sort()

    print(f"  [UNIVERSE L2] Eligible: {len(tickers)} tickers "
          f"(vol>{min_volume/1000:.0f}K, ${min_price}-${max_price}, ATR>{min_atr_pct}%)")

    # Sauvegarder
    with open(ELIGIBLE_CACHE, "w") as f:
        json.dump({
            "date": datetime.now().isoformat(),
            "tickers": tickers,
            "filters": {
                "min_volume": min_volume,
                "min_price": min_price,
                "max_price": max_price,
                "min_atr_pct": min_atr_pct,
            },
        }, f)

    return tickers


# ═══════════════════════════════════════════════════════════
# COUCHE 3 : STOCKS IN PLAY (scanner dynamique quotidien)
# ═══════════════════════════════════════════════════════════

def scan_stocks_in_play(
    data: dict[str, pd.DataFrame],
    daily_stats: pd.DataFrame,
    date,
    min_gap_pct: float = 2.0,
    min_vol_ratio: float = 2.0,
    min_atr_ratio: float = 1.5,
    max_stocks: int = 50,
) -> list[dict]:
    """
    Scanner quotidien : identifie les 10-50 "Stocks in Play" du jour.
    
    Critères (OR — un seul suffit) :
    1. Gap d'ouverture > min_gap_pct (news overnight)
    2. Volume première heure > min_vol_ratio × moyenne 20j (institutional flow)
    3. Range première heure > min_atr_ratio × ATR moyen (vol expansion)
    4. Earnings proxy : gap > 3% ET volume > 3x (très probable earnings day)
    
    Returns:
        Liste de dicts triés par "score" décroissant :
        [{"ticker": "NVDA", "score": 8.5, "reasons": ["gap_up_4.2%", "vol_3.1x"], ...}]
    """
    candidates = []

    # Pré-indexer les stats daily
    stats_dict = {}
    if not daily_stats.empty:
        stats_dict = daily_stats.set_index("ticker").to_dict("index")

    for ticker, df in data.items():
        if len(df) < 5:
            continue

        reasons = []
        score = 0

        today_open = df.iloc[0]["open"]

        # Stats daily de référence
        ref = stats_dict.get(ticker, {})
        avg_vol = ref.get("avg_volume_20d", 0)
        avg_atr_pct = ref.get("atr_pct", 1.0)

        # ── 1. Gap d'ouverture ──
        # Utilise la première barre du jour vs la dernière barre de la veille
        prev_close = ref.get("last_price", today_open)
        # Meilleur proxy : chercher dans les données intraday
        all_dates = sorted(set(df.index.date))
        current_date_idx = None
        for i, d in enumerate(all_dates):
            if d == date:
                current_date_idx = i
                break

        if current_date_idx and current_date_idx > 0:
            prev_date = all_dates[current_date_idx - 1]
            prev_day_data = df[df.index.date == prev_date]
            if not prev_day_data.empty:
                prev_close = prev_day_data.iloc[-1]["close"]

        gap_pct = ((today_open - prev_close) / prev_close) * 100 if prev_close > 0 else 0

        if abs(gap_pct) >= min_gap_pct:
            direction = "up" if gap_pct > 0 else "down"
            reasons.append(f"gap_{direction}_{abs(gap_pct):.1f}%")
            score += min(abs(gap_pct), 10)  # Cap le score à 10 pour le gap

        # ── 2. Volume anormal ──
        today_data = df[df.index.date == date]
        first_hour = today_data.between_time("09:30", "10:30")
        if not first_hour.empty and avg_vol > 0:
            first_hour_vol = first_hour["volume"].sum()
            # Le volume première heure est typiquement ~30-40% du volume daily
            expected_first_hour = avg_vol * 0.35
            vol_ratio = first_hour_vol / expected_first_hour if expected_first_hour > 0 else 0

            if vol_ratio >= min_vol_ratio:
                reasons.append(f"vol_{vol_ratio:.1f}x")
                score += min(vol_ratio, 5)

        # ── 3. Range anormal (volatilité) ──
        if not first_hour.empty:
            first_hour_range = (first_hour["high"].max() - first_hour["low"].min())
            range_pct = (first_hour_range / today_open) * 100 if today_open > 0 else 0

            if avg_atr_pct > 0 and range_pct > avg_atr_pct * min_atr_ratio:
                atr_ratio = range_pct / avg_atr_pct
                reasons.append(f"atr_{atr_ratio:.1f}x")
                score += min(atr_ratio, 5)

        # ── 4. Earnings proxy ──
        if abs(gap_pct) > 3 and len(reasons) >= 2:
            reasons.append("probable_earnings")
            score += 3

        # ── Résultat ──
        if reasons:
            candidates.append({
                "ticker": ticker,
                "score": round(score, 2),
                "reasons": reasons,
                "gap_pct": round(gap_pct, 2),
                "is_permanent": ticker in PERMANENT_TICKERS,
            })

    # Trier par score et limiter
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Toujours inclure les permanents qui ont scoré
    permanent_in_play = [c for c in candidates if c["is_permanent"]]
    non_permanent = [c for c in candidates if not c["is_permanent"]]
    result = permanent_in_play + non_permanent[:max_stocks]

    return result


# ═══════════════════════════════════════════════════════════
# ORCHESTRATION : prépare l'univers complet pour un run
# ═══════════════════════════════════════════════════════════

def prepare_universe(
    mode: str = "eligible",
    force_refresh: bool = False,
) -> list[str]:
    """
    Point d'entrée principal. Retourne la liste de tickers à fetcher.
    
    Modes :
    - "full"     : tous les tickers Alpaca (~3000-5000)
    - "eligible" : filtrés par volume/prix/ATR (~500-1500)
    - "curated"  : top 200 par volume + permanents
    - "minimal"  : permanents + sectors leaders seulement (~50)
    """
    if mode == "minimal":
        tickers = list(set(
            PERMANENT_TICKERS
            + [t for leaders in SECTOR_MAP.values() for t in leaders[:3]]
            + [t for followers in SYMPATHY_MAP.values() for t in followers[:2]]
        ))
        print(f"  [UNIVERSE] Minimal mode: {len(tickers)} tickers")
        return sorted(tickers)

    # Fetch full universe from Alpaca
    full = fetch_full_universe(force_refresh=force_refresh)

    if mode == "full":
        return full

    if mode == "curated":
        # Utilise directement la liste curated (~250 tickers top US).
        # Skip compute_daily_stats qui est trop lent sur Alpaca free plan.
        # Si le cache daily stats existe, on peut l'utiliser pour un top 200.
        if os.path.exists(DAILY_STATS_CACHE):
            try:
                stats = pd.read_parquet(DAILY_STATS_CACHE)
                if not stats.empty:
                    top200 = stats.nlargest(200, "avg_volume_20d")["ticker"].tolist()
                    tickers = sorted(list(set(top200 + PERMANENT_TICKERS)))
                    print(f"  [UNIVERSE] Curated from cache: {len(tickers)} tickers")
                    return tickers
            except Exception:
                pass
        tickers = _fallback_universe()
        print(f"  [UNIVERSE] Curated fallback: {len(tickers)} top US tickers")
        return tickers

    # Compute daily stats for filtering (eligible mode)
    stats = compute_daily_stats(full)

    if mode == "eligible":
        return filter_eligible_universe(stats)

    return full


def get_sector_for_ticker(ticker: str) -> Optional[str]:
    """Retourne le secteur ETF d'un ticker, ou None."""
    for etf, components in SECTOR_MAP.items():
        if ticker in components:
            return etf
    return None


def get_sympathy_leader(ticker: str) -> Optional[str]:
    """Si un ticker est un follower, retourne son leader."""
    for leader, followers in SYMPATHY_MAP.items():
        if ticker in followers:
            return leader
    return None


def get_sympathy_followers(ticker: str) -> list[str]:
    """Retourne les sympathy followers d'un ticker leader."""
    return SYMPATHY_MAP.get(ticker, [])


# ═══════════════════════════════════════════════════════════
# FALLBACK (si Alpaca API indisponible)
# ═══════════════════════════════════════════════════════════

def _fallback_universe() -> list[str]:
    """Top ~250 tickers US par volume — fallback hardcodé."""
    return sorted(list(set(PERMANENT_TICKERS + [
        # Mega-cap tech
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ORCL",
        "CRM", "ADBE", "AMD", "INTC", "QCOM", "TXN", "AMAT", "LRCX", "MU", "MRVL",
        "KLAC", "SNPS", "CDNS", "NOW", "PANW", "CRWD", "FTNT", "NET", "DDOG", "ZS",
        "PLTR", "SNOW", "UBER", "ABNB", "SHOP", "SQ", "PYPL", "INTU", "ADP", "NFLX",
        # Healthcare
        "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
        "AMGN", "GILD", "VRTX", "REGN", "ISRG", "MDT", "SYK", "BDX", "ZTS", "MRNA",
        # Finance
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "USB",
        "PNC", "TFC", "COF", "BK", "STT", "FITB", "HBAN", "KEY", "RF", "CFG",
        # Consumer
        "WMT", "HD", "COST", "PG", "KO", "PEP", "MCD", "NKE", "SBUX", "TGT",
        "LOW", "TJX", "ROST", "DG", "DLTR", "YUM", "CMG", "DPZ", "LULU", "DECK",
        # Energy
        "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "DVN",
        "HES", "FANG", "HAL", "BKR", "CTRA",
        # Industrials
        "CAT", "GE", "HON", "UNP", "RTX", "DE", "BA", "LMT", "UPS", "FDX",
        "GD", "NOC", "WM", "RSG", "EMR", "ETN", "ITW", "CSX", "NSC",
        # Crypto & high-beta
        "COIN", "MARA", "MSTR", "RIOT", "BITF", "HUT", "CLSK",
        # EV & clean energy
        "RIVN", "LCID", "NIO", "LI", "XPEV", "FSLR", "ENPH", "SEDG",
        # High-vol mid-caps
        "SMCI", "SOFI", "HOOD", "RBLX", "CRSP", "DKNG", "PENN", "CHWY", "W",
        "ROKU", "SNAP", "PINS", "TTD", "U", "BILL", "HUBS", "TWLO", "OKTA",
        # ETFs sectoriels + cross-asset
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE",
        "TLT", "GLD", "SLV", "USO", "UNG",
        "IWM", "DIA", "VXX", "SQQQ", "TQQQ",
    ])))


# ═══════════════════════════════════════════════════════════
# CLI test
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "minimal"
    print(f"\n{'='*60}")
    print(f"  UNIVERSE MANAGER — mode: {mode}")
    print(f"{'='*60}")
    tickers = prepare_universe(mode=mode)
    print(f"\n  Total: {len(tickers)} tickers")
    print(f"  Permanent: {len([t for t in tickers if t in PERMANENT_TICKERS])}")
    print(f"  Sample: {tickers[:20]}...")
