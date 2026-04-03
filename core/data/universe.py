"""
Univers d'actifs — catalogue organise par classe.

Deux niveaux :
  1. Univers curate (Asset objects) — backward-compatible, avec pip_value/spread
  2. Univers Alpaca etendu — S&P 500 complet, ETFs, crypto

Usage :
    from core.data.universe import UNIVERSE, get_ticker, get_all_assets
    from core.data.universe import (
        ALPACA_SP500, ALPACA_ETFS, ALPACA_CRYPTO,
        get_alpaca_full_universe, is_crypto_symbol,
    )
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


# =============================================================================
# 1. UNIVERS CURATE (backward-compatible)
# =============================================================================

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

# ─── Actions (curated) ───────────────────────────────────────────────────────

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

# ─── Crypto (curated) ────────────────────────────────────────────────────────

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

# ─── Matieres premieres ──────────────────────────────────────────────────────

COMMODITIES: list[Asset] = [
    Asset("GOLD",   "GC=F",  "Or (futures)",         "commodities", 0.1, 1.0),
    Asset("SILVER", "SI=F",  "Argent (futures)",     "commodities", 0.01, 2.0),
    Asset("OIL",    "CL=F",  "Petrole WTI (futures)","commodities", 0.01, 2.0),
    Asset("GAS",    "NG=F",  "Gaz naturel (futures)","commodities", 0.001, 3.0),
]


# ─── Index global (backward-compatible) ──────────────────────────────────────

UNIVERSE: dict[str, list[Asset]] = {
    "forex":       FOREX,
    "indices":     INDICES,
    "stocks":      STOCKS,
    "crypto":      CRYPTO,
    "commodities": COMMODITIES,
}


# =============================================================================
# 2. UNIVERS ALPACA ETENDU
# =============================================================================

# ─── S&P 500 — composants complets (alphabetique) ────────────────────────────
# ~500 tickers, tous tradables sur Alpaca (US equities)

ALPACA_SP500: list[str] = [
    # A
    "A", "AAL", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI",
    "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG",
    "AKAM", "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN",
    "AMP", "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH",
    "APTV", "ARE", "ATO", "AVGO", "AVY", "AWK", "AXP", "AZO",
    # B
    "BA", "BAC", "BAX", "BBWI", "BBY", "BDX", "BEN", "BG", "BIIB", "BIO",
    "BK", "BKNG", "BKR", "BLDR", "BMY", "BR", "BRK.B", "BRO", "BSX", "BWA",
    "BX", "BXP",
    # C
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL",
    "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI",
    "CINF", "CL", "CLX", "CMA", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC",
    "CNP", "COF", "COO", "COP", "COR", "COST", "CPAY", "CPB", "CPRT", "CPT",
    "CRL", "CRM", "CRWD", "CSCO", "CSGP", "CSX", "CTAS", "CTRA", "CTSH",
    "CTVA", "CVS", "CVX", "CZR",
    # D
    "D", "DAL", "DAY", "DD", "DE", "DECK", "DELL", "DFS", "DG", "DGX",
    "DHI", "DHR", "DIS", "DLTR", "DOV", "DOW", "DPZ", "DRI", "DT", "DTE",
    "DUK", "DVA", "DVN",
    # E
    "EA", "EBAY", "ECL", "ED", "EFX", "EIX", "EL", "EMN", "EMR", "ENPH",
    "EOG", "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS", "ETN", "ETR", "EVRG",
    "EW", "EXC", "EXPD", "EXPE", "EXR",
    # F
    "F", "FANG", "FAST", "FCNCA", "FCX", "FDS", "FDX", "FE", "FFIV", "FI",
    "FICO", "FIS", "FISV", "FITB", "FMC", "FOX", "FOXA", "FRT", "FSLR",
    "FTNT", "FTV",
    # G
    "GD", "GDDY", "GE", "GEHC", "GEN", "GEV", "GILD", "GIS", "GL", "GLW",
    "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN", "GRMN", "GS", "GWW",
    # H
    "HAL", "HAS", "HBAN", "HCA", "HD", "HOLX", "HON", "HPE", "HPQ", "HRL",
    "HSIC", "HST", "HSY", "HUBB", "HUM", "HWM",
    # I
    "IBM", "ICE", "IDXX", "IEX", "IFF", "ILMN", "INCY", "INTC", "INTU",
    "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW",
    # J
    "J", "JBHT", "JBL", "JCI", "JKHY", "JNJ", "JNPR", "JPM",
    # K
    "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KKR", "KLAC", "KMB", "KMI",
    "KMX", "KO", "KR",
    # L
    "L", "LDOS", "LEN", "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNT",
    "LOW", "LRCX", "LULU", "LUV", "LVS", "LW", "LYB", "LYV",
    # M
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST",
    "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRNA", "MRVL", "MS", "MSCI",
    "MSFT", "MSI", "MTB", "MTCH", "MTD", "MU",
    # N
    "NCLH", "NDAQ", "NDSN", "NEE", "NFLX", "NI", "NKE", "NOC", "NOW", "NRG",
    "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA", "NXPI",
    # O
    "O", "ODFL", "OGN", "OKE", "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY",
    # P
    "PANW", "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEG", "PEP", "PFE", "PFG",
    "PG", "PGR", "PH", "PHM", "PKG", "PLD", "PLTR", "PM", "PNC", "PNR",
    "PNW", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX", "PTC", "PVH", "PWR",
    # Q
    "QCOM", "QRVO",
    # R
    "RCL", "REG", "REGN", "RF", "RJF", "RL", "RMD", "ROK", "ROL", "ROP",
    "ROST", "RSG", "RTX", "RVTY",
    # S
    "SBAC", "SBUX", "SCHW", "SEE", "SHW", "SJM", "SLB", "SMCI", "SNA", "SNPS",
    "SO", "SOLV", "SPG", "SPGI", "SRE", "STE", "STLD", "STT", "STX", "STZ",
    "SWK", "SWKS", "SYF", "SYK", "SYY",
    # T
    "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", "TGT",
    "TJX", "TMO", "TMUS", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO",
    "TSLA", "TSN", "TT", "TTWO", "TXN", "TXT", "TYL",
    # U
    "UAL", "UBER", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB",
    # V
    "V", "VICI", "VLO", "VLTO", "VMC", "VRSK", "VRSN", "VRTX", "VST", "VTR",
    "VTRS", "VZ",
    # W
    "WAB", "WAT", "WBA", "WBD", "WDC", "WEC", "WELL", "WFC", "WHR", "WM",
    "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN",
    # X-Z
    "XEL", "XOM", "XYL", "YUM", "ZBH", "ZBRA", "ZS", "ZTS",
]

# ─── ETFs — marche, secteurs, thematiques, obligations, commodites ───────────

ALPACA_ETFS: list[str] = [
    # Broad US
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "VTV", "VUG", "RSP", "MDY",
    # Secteurs SPDR
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
    # Semiconducteurs
    "SMH", "SOXX", "SOXL", "SOXS",
    # Biotech / Sante
    "XBI", "IBB",
    # Energie
    "XOP", "OIH",
    # Finance
    "KRE", "KBE",
    # Thematiques / Innovation
    "ARKK", "ARKW", "ARKG", "ARKF", "ARKQ",
    "HACK", "BOTZ", "ROBO", "LIT", "TAN", "ICLN",
    # International
    "VEA", "VWO", "EFA", "EEM", "IEMG",
    "FXI", "KWEB", "EWJ", "EWZ", "EWG", "EWU", "INDA",
    # Obligations / Taux
    "TLT", "IEF", "SHY", "LQD", "HYG", "BND", "AGG", "TIPS", "MUB",
    # Commodites
    "GLD", "SLV", "IAU", "USO", "UNG", "DBC",
    # Volatilite
    "UVXY", "SVXY",
    # Leveraged / Inverse
    "TQQQ", "SQQQ", "SPXL", "SPXS",
    # Dividendes
    "VYM", "SCHD", "DVY", "HDV", "NOBL",
    # Small/Mid caps
    "IWO", "IWN", "IJR", "IJH",
]

# ─── Crypto Alpaca — paires USD (format Alpaca : "BTC/USD") ──────────────────
# Liste des cryptos disponibles sur Alpaca Markets

ALPACA_CRYPTO: dict[str, str] = {
    "BTC/USD":   "Bitcoin",
    "ETH/USD":   "Ethereum",
    "SOL/USD":   "Solana",
    "AVAX/USD":  "Avalanche",
    "LINK/USD":  "Chainlink",
    "UNI/USD":   "Uniswap",
    "AAVE/USD":  "Aave",
    "DOT/USD":   "Polkadot",
    "MATIC/USD": "Polygon",
    "DOGE/USD":  "Dogecoin",
    "SHIB/USD":  "Shiba Inu",
    "LTC/USD":   "Litecoin",
    "BCH/USD":   "Bitcoin Cash",
    "XRP/USD":   "Ripple",
    "ADA/USD":   "Cardano",
    "ATOM/USD":  "Cosmos",
    "ALGO/USD":  "Algorand",
    "FIL/USD":   "Filecoin",
    "NEAR/USD":  "NEAR Protocol",
    "APE/USD":   "ApeCoin",
    "MANA/USD":  "Decentraland",
    "SAND/USD":  "The Sandbox",
    "AXS/USD":   "Axie Infinity",
    "CRV/USD":   "Curve DAO",
    "MKR/USD":   "Maker",
    "COMP/USD":  "Compound",
    "SNX/USD":   "Synthetix",
    "SUSHI/USD": "SushiSwap",
    "BAT/USD":   "Basic Attention Token",
    "GRT/USD":   "The Graph",
    "ENJ/USD":   "Enjin Coin",
    "YFI/USD":   "Yearn Finance",
}

# Symboles crypto sans suffixe — pour auto-detection dans le loader
_CRYPTO_BASE_SYMBOLS: set[str] = {
    s.split("/")[0] for s in ALPACA_CRYPTO
}


# =============================================================================
# 3. FONCTIONS UTILITAIRES
# =============================================================================

def get_all_assets() -> list[Asset]:
    """Retourne tous les actifs de l'univers curate."""
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


def is_crypto_symbol(symbol: str) -> bool:
    """Detecte si un symbole est crypto (pour routage Alpaca stock vs crypto)."""
    if "/" in symbol:
        return symbol.upper() in ALPACA_CRYPTO
    return symbol.upper() in _CRYPTO_BASE_SYMBOLS


def to_alpaca_crypto_symbol(symbol: str) -> str:
    """Convertit un symbole crypto en format Alpaca (BTC -> BTC/USD)."""
    symbol = symbol.upper()
    if "/" in symbol:
        return symbol
    return f"{symbol}/USD"


def get_sp500_tickers() -> list[str]:
    """Retourne tous les tickers S&P 500."""
    return list(ALPACA_SP500)


def get_etf_tickers() -> list[str]:
    """Retourne tous les tickers ETFs."""
    return list(ALPACA_ETFS)


def get_crypto_tickers() -> list[str]:
    """Retourne tous les tickers crypto Alpaca (format BTC/USD)."""
    return list(ALPACA_CRYPTO.keys())


def get_alpaca_stock_universe() -> list[str]:
    """Retourne tous les tickers actions + ETFs tradables sur Alpaca."""
    return list(set(ALPACA_SP500 + ALPACA_ETFS))


def get_alpaca_full_universe() -> dict[str, list[str]]:
    """Retourne l'univers Alpaca complet par categorie."""
    return {
        "sp500":  list(ALPACA_SP500),
        "etfs":   list(ALPACA_ETFS),
        "crypto": list(ALPACA_CRYPTO.keys()),
    }


def print_universe():
    """Affiche l'univers complet (curate + Alpaca)."""
    # Curate
    total_curated = sum(len(v) for v in UNIVERSE.values())
    print(f"\n=== Univers curate : {total_curated} actifs ===\n")
    for cls, assets in UNIVERSE.items():
        print(f"  {cls.upper()} ({len(assets)})")
        for a in assets:
            print(f"    {a.symbol:<10} {a.ticker:<15} {a.name}")
        print()

    # Alpaca etendu
    print("=== Univers Alpaca etendu ===\n")
    print(f"  S&P 500  : {len(ALPACA_SP500)} tickers")
    print(f"  ETFs     : {len(ALPACA_ETFS)} tickers")
    print(f"  Crypto   : {len(ALPACA_CRYPTO)} paires")
    total_alpaca = len(ALPACA_SP500) + len(ALPACA_ETFS) + len(ALPACA_CRYPTO)
    print(f"  TOTAL    : {total_alpaca} actifs\n")
