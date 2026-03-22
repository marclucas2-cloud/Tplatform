"""
Univers d'actifs — catalogue organise par classe.

Chaque entree contient :
  ticker    : symbole Yahoo Finance
  name      : nom lisible
  pip_value : valeur d'un pip (0.0001 pour FX, 1.0 pour indices/actions)
  spread    : spread typique en pips (pour le cost model)

Usage :
    from core.data.universe import UNIVERSE, get_ticker, asset_classes
    tickers = UNIVERSE["forex"]          # tous les FX
    all_assets = get_all_assets()        # tout l'univers
    ticker = get_ticker("EURUSD")        # "EURUSD=X"
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Asset:
    symbol: str       # Identifiant interne (ex: "EURUSD")
    ticker: str       # Ticker Yahoo Finance (ex: "EURUSD=X")
    name: str         # Nom lisible
    asset_class: str  # "forex", "indices", "stocks", "crypto", "commodities"
    pip_value: float  # Valeur d'un pip (0.0001 pour FX, 1 pour actions/indices)
    spread_pips: float  # Spread typique en pips pour le cost model


# ─── Forex ────────────────────────────────────────────────────────────────────

FOREX: list[Asset] = [
    Asset("EURUSD", "EURUSD=X",  "Euro / Dollar",          "forex", 0.0001, 0.8),
    Asset("GBPUSD", "GBPUSD=X",  "Livre / Dollar",         "forex", 0.0001, 1.0),
    Asset("USDJPY", "USDJPY=X",  "Dollar / Yen",           "forex", 0.01,   0.5),
    Asset("AUDUSD", "AUDUSD=X",  "Australien / Dollar",    "forex", 0.0001, 0.9),
    Asset("USDCHF", "USDCHF=X",  "Dollar / Franc suisse",  "forex", 0.0001, 0.9),
    Asset("EURGBP", "EURGBP=X",  "Euro / Livre",           "forex", 0.0001, 1.2),
    Asset("NZDUSD", "NZDUSD=X",  "NZ Dollar / Dollar",     "forex", 0.0001, 1.3),
    Asset("USDCAD", "USDCAD=X",  "Dollar / CAD",           "forex", 0.0001, 1.2),
    Asset("EURJPY", "EURJPY=X",  "Euro / Yen",             "forex", 0.01,   1.0),
    Asset("GBPJPY", "GBPJPY=X",  "Livre / Yen",            "forex", 0.01,   1.5),
]

# ─── Indices ──────────────────────────────────────────────────────────────────

INDICES: list[Asset] = [
    Asset("DAX",     "^GDAXI", "DAX 40 (Allemagne)",       "indices", 1.0, 1.5),
    Asset("SP500",   "^GSPC",  "S&P 500 (USA)",            "indices", 1.0, 0.5),
    Asset("NASDAQ",  "^IXIC",  "NASDAQ Composite (USA)",   "indices", 1.0, 1.0),
    Asset("FTSE",    "^FTSE",  "FTSE 100 (UK)",            "indices", 1.0, 1.5),
    Asset("CAC40",   "^FCHI",  "CAC 40 (France)",          "indices", 1.0, 1.5),
    Asset("DOW",     "^DJI",   "Dow Jones (USA)",          "indices", 1.0, 2.0),
    Asset("NIKKEI",  "^N225",  "Nikkei 225 (Japon)",       "indices", 1.0, 5.0),
    Asset("RUSSELL", "^RUT",   "Russell 2000 (USA small)", "indices", 1.0, 2.0),
    Asset("VIX",     "^VIX",   "VIX Volatilite (USA)",    "indices", 1.0, 0.5),
    Asset("EUROSTOXX","^STOXX50E","EuroStoxx 50",          "indices", 1.0, 2.0),
]

# ─── Actions (Large Cap) ──────────────────────────────────────────────────────

STOCKS: list[Asset] = [
    # Tech US
    Asset("AAPL",  "AAPL",  "Apple",             "stocks", 1.0, 0.5),
    Asset("MSFT",  "MSFT",  "Microsoft",         "stocks", 1.0, 0.5),
    Asset("GOOGL", "GOOGL", "Alphabet (Google)", "stocks", 1.0, 0.5),
    Asset("AMZN",  "AMZN",  "Amazon",            "stocks", 1.0, 0.5),
    Asset("NVDA",  "NVDA",  "NVIDIA",            "stocks", 1.0, 0.5),
    Asset("TSLA",  "TSLA",  "Tesla",             "stocks", 1.0, 1.0),
    Asset("META",  "META",  "Meta (Facebook)",   "stocks", 1.0, 0.5),
    # Finance US
    Asset("JPM",   "JPM",   "JP Morgan",         "stocks", 1.0, 0.5),
    Asset("GS",    "GS",    "Goldman Sachs",     "stocks", 1.0, 1.0),
    Asset("BAC",   "BAC",   "Bank of America",   "stocks", 1.0, 0.5),
    # Europe
    Asset("ASML",  "ASML",  "ASML (NL)",         "stocks", 1.0, 1.0),
    Asset("LVMH",  "MC.PA", "LVMH (FR)",         "stocks", 1.0, 2.0),
    Asset("SAP",   "SAP",   "SAP (DE)",          "stocks", 1.0, 1.0),
    # Diversification sectorielle
    Asset("NFLX",  "NFLX",  "Netflix",           "stocks", 1.0, 1.0),
    Asset("BRKB",  "BRK-B", "Berkshire Hathaway","stocks", 1.0, 0.5),
]

# ─── Crypto ───────────────────────────────────────────────────────────────────

CRYPTO: list[Asset] = [
    Asset("BTC",  "BTC-USD",  "Bitcoin",          "crypto", 1.0, 5.0),
    Asset("ETH",  "ETH-USD",  "Ethereum",         "crypto", 1.0, 3.0),
    Asset("SOL",  "SOL-USD",  "Solana",           "crypto", 1.0, 5.0),
    Asset("BNB",  "BNB-USD",  "Binance Coin",     "crypto", 1.0, 5.0),
    Asset("XRP",  "XRP-USD",  "Ripple",           "crypto", 0.0001, 5.0),
    Asset("ADA",  "ADA-USD",  "Cardano",          "crypto", 0.0001, 5.0),
    Asset("AVAX", "AVAX-USD", "Avalanche",        "crypto", 1.0, 8.0),
    Asset("LINK", "LINK-USD", "Chainlink",        "crypto", 1.0, 8.0),
]

# ─── Matieres premieres ───────────────────────────────────────────────────────

COMMODITIES: list[Asset] = [
    Asset("GOLD",   "GC=F",  "Or (futures)",         "commodities", 0.1, 1.0),
    Asset("SILVER", "SI=F",  "Argent (futures)",     "commodities", 0.01, 2.0),
    Asset("OIL",    "CL=F",  "Petrole WTI (futures)","commodities", 0.01, 2.0),
    Asset("GAS",    "NG=F",  "Gaz naturel (futures)","commodities", 0.001, 3.0),
]


# ─── Index global ─────────────────────────────────────────────────────────────

UNIVERSE: dict[str, list[Asset]] = {
    "forex":       FOREX,
    "indices":     INDICES,
    "stocks":      STOCKS,
    "crypto":      CRYPTO,
    "commodities": COMMODITIES,
}


def get_all_assets() -> list[Asset]:
    """Retourne tous les actifs de l'univers."""
    return [a for assets in UNIVERSE.values() for a in assets]


def get_asset(symbol: str) -> Asset | None:
    """Trouve un Asset par son symbole interne."""
    for a in get_all_assets():
        if a.symbol == symbol:
            return a
    return None


def get_ticker(symbol: str) -> str:
    """Retourne le ticker Yahoo Finance pour un symbole."""
    asset = get_asset(symbol)
    return asset.ticker if asset else symbol


def assets_by_class(asset_class: str) -> list[Asset]:
    """Retourne les actifs d'une classe donnee."""
    return UNIVERSE.get(asset_class, [])


def print_universe():
    """Affiche l'univers complet."""
    total = sum(len(v) for v in UNIVERSE.values())
    print(f"\nUnivers de trading : {total} actifs\n")
    for cls, assets in UNIVERSE.items():
        print(f"  {cls.upper()} ({len(assets)})")
        for a in assets:
            print(f"    {a.symbol:<10} {a.ticker:<15} {a.name}")
        print()
